# Dynamic Crew Reliability Gates Design

日期：2026-05-01
状态：已确认设计，等待实现计划
范围：仅针对 dynamic crew path 的可靠控制平面；不覆盖旧版 `session`、`bridge`、单 worker `dispatch` 流程。

## 1. 背景与目标

当前 dynamic crew 已经具备 `WorkerContract`、worktree worker、patch auditor、verification、blackboard、artifact 等核心骨架，但仍有几个可靠性缺口：

- patch auditor 完成后，supervisor 目前会把 review 状态视为 `ok`，没有严格解析 `OK/WARN/BLOCK`。
- `write_scope` 已写入 contract 和 prompt，但还没有在 diff 后作为 runtime gate 强制检查。
- `ready_for_codex_accept` 的含义不够硬，只表示当前流程跑到了验证通过附近，而不是所有必要证据都被检查过。
- worker completion 依赖 tmux pane 中的精确 marker，失败时缺少可解释原因。

本设计目标是把 dynamic crew 的主线补成可靠的证据门禁流水线：

```text
source turn completed
  -> record changes
  -> write_scope gate
  -> review gate
  -> optional browser gate
  -> verification gate
  -> readiness gate
```

只有 scope 已检查、review verdict 已解析、verification 已通过、readiness report 已落盘时，supervisor 才能返回 `ready_for_codex_accept`。

## 2. 非目标

本次不做：

- 不改旧版 `session`、`bridge`、单 worker `dispatch` 流程。
- 不实现真实文件系统 sandbox；`write_scope` 先作为 diff 后门禁。
- 不实现 worker 间 message routing 闭环。
- 不实现后台 supervisor daemon。
- 不实现自动 merge/apply 到主工作区。
- 不把 browser tester 绑定到真实浏览器运行时。

这些可以在后续 B/C 阶段继续设计。

## 3. 目标流程

dynamic crew 每一轮 source worker 完成后，supervisor 固定经过以下阶段：

```text
send source worker
observe source marker
record changes

scope_result = WriteScopeGate.evaluate(changed_files, write_scope)
if scope_result.block:
  write readiness(blocked)
  return needs_human
if scope_result.challenge:
  challenge source worker
  continue next round

if changed_files:
  spawn reviewer
  observe reviewer marker
  review_verdict = ReviewVerdictParser.parse(reviewer_output)
  if review_verdict.unknown:
    write readiness(blocked)
    return needs_human
  if review_verdict.block:
    challenge source worker with findings
    continue next round
  if review_verdict.warn:
    record warning and continue

optional browser tester

verification_results = run commands
if verification failed:
  challenge source worker
  maybe spawn failure analyst / guardrail maintainer
  continue next round

readiness = CrewReadinessEvaluator.evaluate(round_evidence)
if readiness.ready:
  return ready_for_codex_accept
return needs_human
```

每个 gate 输出三类结果：

```text
pass       可以进入下一阶段
challenge 需要把明确修复指令发回 source worker
block      需要 Codex/人介入，不能自动继续
```

## 4. 新组件设计

### 4.1 `ReviewVerdictParser`

建议路径：

```text
src/codex_claude_orchestrator/crew/review_verdict.py
```

职责：从 reviewer 输出中解析 verdict。

优先支持结构化 block：

```text
<<<CODEX_REVIEW
verdict: OK | WARN | BLOCK
summary: Reviewer summary text.
findings:
- Reviewer finding text.
>>>
```

同时兼容普通文本：

```text
Verdict: BLOCK
Findings:
- Reviewer finding text.
```

输出模型：

```python
ReviewVerdict(
    status="ok" | "warn" | "block" | "unknown",
    summary=str,
    findings=list[str],
    evidence_refs=list[str],
    raw_artifact=str,
)
```

判定规则：

```text
OK      -> pass
WARN    -> pass_with_warning
BLOCK   -> challenge source worker
unknown -> block / needs_human
```

关键约束：`unknown` 不能默认为 `OK`。

### 4.2 `WriteScopeGate`

建议路径：

```text
src/codex_claude_orchestrator/crew/gates.py
```

职责：把 `contract.write_scope` 从 prompt 约束升级为 diff 后门禁。

输入：

```python
contract.write_scope
changed_files
protected_paths
```

输出：

```python
GateResult(
    status="pass" | "challenge" | "block",
    reason=str,
    evidence_refs=list[str],
    details=dict,
)
```

基础规则：

- `changed_files` 为空：`pass`。
- `write_scope` 为空且存在 changed files：`block`。
- 文件位于任一 scope prefix 下：允许。
- 低风险越界：`challenge`，要求 worker 回滚或申请扩大 scope。
- 高风险越界：`block`，要求 Codex/人介入。

高风险路径第一版包括：

```text
.git/
.env
secrets/
*.pem
*.key
pyproject.toml
package-lock.json
pnpm-lock.yaml
uv.lock
.github/workflows/
```

锁文件、CI 文件不是永久禁止修改，只是在当前 MVP 中默认 block，防止 worker 擅自扩大影响面。

### 4.3 `CrewReadinessEvaluator`

建议路径：

```text
src/codex_claude_orchestrator/crew/readiness.py
```

职责：把一轮中的 scope、review、browser、verification 证据合成 readiness report。

输入：

```python
scope_result
review_verdict
browser_result optional
verification_results
changed_files
worker_id
contract_id
```

输出 artifact：

```text
artifacts/readiness/<round_id>.json
```

核心字段：

```json
{
  "status": "ready | challenge | blocked",
  "scope_status": "pass | challenge | block",
  "review_status": "ok | warn | block | unknown | skipped",
  "verification_status": "pass | fail | skipped",
  "warnings": [],
  "blockers": [],
  "evidence_refs": []
}
```

只有 `status=ready` 时，supervisor 才能返回 `ready_for_codex_accept`。

### 4.4 `MarkerObservationPolicy`

建议路径：

```text
src/codex_claude_orchestrator/runtime/marker_policy.py
```

职责：增强 worker completion 判断和错误解释。

第一版只做轻量增强：

```text
observe pane last N lines
if exact marker found -> completed
else scan transcript artifact for exact marker
else if old contract marker found but per-turn marker missing -> mismatch
else -> waiting
```

输出模型：

```python
MarkerObservation(
    status="completed" | "waiting" | "mismatch",
    marker_seen=bool,
    reason=str,
    evidence_refs=list[str],
)
```

集成点优先放在 supervisor 侧的 `_wait_for_marker()`，少动底层 tmux 封装。

## 5. Supervisor 集成

集成原则：

- 不重写整个 `supervise_dynamic()`。
- 在现有关键点插入小 gate。
- 新组件独立测试，supervisor 只负责串联。

关键变更：

1. `record_changes` 后立刻执行 `WriteScopeGate`。
2. scope `challenge` 时，向 source worker 发送明确修复指令并进入下一轮。
3. scope `block` 时，写 readiness artifact，返回 `needs_human`。
4. reviewer 完成后，通过 `ReviewVerdictParser` 解析 verdict。
5. review `BLOCK` 时 challenge source worker，不运行 verification。
6. review `unknown` 时写 readiness artifact，返回 `needs_human`。
7. review `WARN` 时记录 warning，允许继续 verification。
8. verification 通过后，由 `CrewReadinessEvaluator` 决定是否返回 `ready_for_codex_accept`。
9. `ready_for_codex_accept` payload 增加 `readiness_artifact`。

示例返回：

```json
{
  "crew_id": "crew-example",
  "status": "ready_for_codex_accept",
  "rounds": 1,
  "events": [],
  "readiness_artifact": "readiness/round-1.json"
}
```

blocked 示例：

```json
{
  "crew_id": "crew-example",
  "status": "needs_human",
  "reason": "write_scope_blocked",
  "readiness_artifact": "readiness/round-1.json"
}
```

## 6. Gate 判定规则

### 6.1 Scope Gate

```text
pass:
  changed_files 为空
  或所有 changed_files 都在 write_scope 内

challenge:
  存在低风险越界文件
  例如 docs/、README、非关键配置旁路说明文件

block:
  write_scope 为空但存在 changed_files
  或存在高风险越界路径
```

scope challenge prompt 应包含：

```text
Your patch changed files outside write_scope:
- docs/example.md

Allowed write_scope:
- src/
- tests/

Revert the out-of-scope edits or request an expanded scope using CODEX_MESSAGE.
```

### 6.2 Review Gate

```text
OK:
  继续 verification

WARN:
  继续 verification
  readiness report 中记录 warning

BLOCK:
  challenge source worker
  不运行 verification

unknown:
  needs_human
  不运行 verification
```

review BLOCK challenge prompt 应包含 reviewer findings 和 evidence refs。

### 6.3 Verification Gate

verification 失败沿用现有策略：

```text
失败 -> challenge
失败 >= 2 -> failure analyst
失败 >= 3 -> guardrail maintainer
```

新增要求：失败时也写 readiness artifact，记录 failed verification evidence。

### 6.4 Readiness Gate

`ready` 必须满足：

- scope gate 为 `pass`。
- review verdict 为 `ok` 或 `warn`；如果没有 changed files，可为 `skipped`。
- 所有 verification command 通过。
- readiness artifact 写入成功。

任一必要证据缺失时，不得返回 `ready_for_codex_accept`。

## 7. 状态与 Artifacts

新增 artifacts：

```text
gates/<round_id>/write_scope.json
workers/<worker_id>/review_verdict.json
readiness/<round_id>.json
```

blackboard 记录：

```text
RISK     scope violation / review block
REVIEW   parsed review verdict
DECISION readiness decision
```

events 记录建议：

```text
write_scope_gate
review_verdict_parsed
readiness_evaluated
marker_mismatch
```

第一版可以只写 artifacts 和 blackboard，event envelope 可逐步补齐。

## 8. CLI 行为

第一版不新增命令，避免 CLI 面膨胀。

已有命令继续可用：

```text
crew run
crew status
crew blackboard
crew decisions
crew worker tail
```

`crew run` 返回值增加：

- `readiness_artifact`
- `reason`，仅在 `needs_human` / blocked / waiting 时出现。

后续如有需要，再增加：

```text
crew readiness --repo . --crew <crew_id>
```

## 9. 测试计划

新增或调整测试集中在 `tests/crew/` 和 `tests/runtime/`。

### 9.1 ReviewVerdictParser

- 解析 `CODEX_REVIEW` 中的 `OK`。
- 解析 `CODEX_REVIEW` 中的 `WARN`。
- 解析 `CODEX_REVIEW` 中的 `BLOCK`。
- 解析普通文本 `Verdict: BLOCK`。
- 无 verdict 时返回 `unknown`。

### 9.2 WriteScopeGate

- changed files 全在 scope 内：`pass`。
- changed files 为空：`pass`。
- 低风险越界：`challenge`。
- 高风险路径越界：`block`。
- write_scope 为空但存在 changed files：`block`。

### 9.3 CrewReadinessEvaluator

- scope pass + review OK + verification pass：`ready`。
- scope pass + review WARN + verification pass：`ready` 且有 warning。
- review BLOCK：`challenge` 或 `blocked`。
- verification fail：`challenge`。
- 缺少 verification evidence：`blocked`。

### 9.4 CrewSupervisorLoop

- scope violation challenge 时跳过 reviewer 和 verification。
- protected path violation 返回 `needs_human`。
- review BLOCK challenge source worker，并跳过 verification。
- review unknown 返回 `needs_human`。
- review WARN 继续 verification，并返回 ready with warning。
- verification 通过前必须写 readiness artifact。

### 9.5 MarkerObservationPolicy

- pane 中有 exact marker：completed。
- pane 无 marker 但 transcript 有 exact marker：completed。
- 只看到 contract marker：mismatch / waiting。
- 完全无 marker：waiting。

## 10. 分阶段实施顺序

### Phase 1：ReviewVerdictParser

交付：

```text
crew/review_verdict.py
tests/crew/test_review_verdict.py
```

完成标准：

```text
OK/WARN/BLOCK 能解析
unknown 不放行
解析结果可序列化为 artifact
```

### Phase 2：WriteScopeGate

交付：

```text
crew/gates.py
tests/crew/test_gates.py
```

完成标准：

```text
scope 内文件 pass
低风险越界 challenge
高风险路径 block
empty scope + changed files block
```

### Phase 3：CrewReadinessEvaluator

交付：

```text
crew/readiness.py
tests/crew/test_readiness.py
```

完成标准：

```text
scope pass + review OK/WARN + verification pass -> ready
scope block / review BLOCK / verification fail -> blocked/challenge
readiness JSON 包含 evidence_refs、warnings、blockers
```

### Phase 4：Supervisor integration

交付：

```text
crew/supervisor_loop.py
tests/crew/test_supervisor_loop.py
```

完成标准：

```text
record_changes 后立刻检查 scope
review worker 完成后 parse verdict
BLOCK/unknown 不运行 verification
verification 通过后由 readiness evaluator 决定 ready
返回 payload 增加 readiness_artifact
```

### Phase 5：MarkerObservationPolicy

交付：

```text
runtime/marker_policy.py
tests/runtime/test_marker_policy.py
```

完成标准：

```text
transcript fallback 可识别 marker
marker mismatch 有明确 reason
waiting_for_worker 返回更可解释
```

### Phase 6：CLI/status exposure

交付：

```text
crew run payload 增加 readiness_artifact / reason
crew status 通过 artifacts 可查看 readiness report
```

完成标准：

```text
不新增命令也能定位 readiness artifact
blocked / waiting 返回原因清楚
```

## 11. 风险与后续扩展

### 风险

- reviewer 可能不按格式输出，第一版会返回 `needs_human`，这会提高安全性但可能降低自动化率。
- scope gate 的高风险路径列表可能过严，部分合法修改会被 block。
- readiness artifact 成为新的必要证据，如果写入失败，流程应保守地不 accept。
- marker transcript fallback 需要读取 transcript artifact，路径必须和 worker record 保持一致。

### 后续扩展

- 将 review verdict 要求写入 patch auditor contract 和 protocol fragment。
- 将 scope expansion 变成正式 `ProtocolRequest`。
- 将 gate 事件统一写入 `CrewEvent`。
- 增加 `crew readiness` CLI。
- 在 merge/apply 到主工作区前复用 readiness report 作为 merge gate 输入。
- 后续 B 阶段实现 message routing 和 inbox digest 注入。
- 后续 C 阶段实现 background supervisor 和更完整的 resume。
