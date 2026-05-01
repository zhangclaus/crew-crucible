# Codex Managed Claude Crew V4 Design

日期：2026-05-01
状态：已确认方向，等待实现计划
范围：V4 版本总体设计。目标是把当前 V3 dynamic crew 从 `tmux capture-pane + marker` 驱动的 supervisor，升级为 durable event-sourced agent runtime/control plane。

## 1. 背景

当前 dynamic crew 已经有 worker contract、write scope gate、review verdict、readiness report、marker policy 等可靠性补强。但主控制链路仍然是：

```text
send worker turn
  -> ask Claude Code to print turn marker
  -> capture tmux pane / transcript
  -> parse marker
  -> continue supervisor flow
```

这个模型适合 MVP，但不适合作为长期稳定系统的核心。它把终端输出当成控制面事实源，把模型打印 marker 当成完成事实。终端输出适合人看，不适合作为 durable runtime 的状态机输入。

V4 的目标是把系统演进为一个 event-sourced durable agent operating system：终端、Claude Code、浏览器、shell、worker 进程都只是 runtime adapter；系统真正的事实源是 append-only event log、typed artifacts、durable workflow state。

## 2. 设计目标

- 所有 worker turn 都有稳定 `turn_id`、`worker_id`、`contract_id`、`attempt_id`、deadline 和 idempotency key。
- 所有副作用先记录 intent，再执行，再记录 result，支持崩溃恢复和重复执行检测。
- terminal pane 和 marker 只作为 observation evidence，不作为唯一完成事实。
- supervisor 不再长期阻塞在 `sleep + capture-pane` 循环中，而是等待 durable event 或 workflow condition。
- worker runtime 可以替换：Claude Code/tmux、PTY shell、container sandbox、browser runner、未来的 MCP/tool runtime 都通过统一 adapter 接入。
- review、scope、verification、readiness、human escalation 都消费结构化事件和 artifact，而不是读 terminal text。
- 系统可恢复：进程中断后能从 event log 和 workflow checkpoint 重建 crew/worker/turn 状态，判断 resume、retry、cancel、reconcile 或 human escalation。
- 系统可观测：UI 和 CLI 能解释每个 worker 当前在等什么、已经产出什么、为什么不能继续。

## 3. 非目标

本设计不要求立刻放弃 Claude Code 或 tmux。它们可以继续作为第一版 runtime adapter。

本设计不把某个外部框架绑定为必须依赖。OpenHands、LangGraph、AutoGen、SWE-ReX、Temporal/DBOS/Pydantic AI Durable Execution 都只作为设计参考。实现时可以先构建本项目自己的小内核，再决定是否接入外部 durable workflow 引擎。

本设计不把模型输出视为可信安全边界。模型可以报告事实，但 gate 必须由 runtime 或 supervisor 基于文件系统、artifact、verification result 独立判断。

## 4. 核心原则

### 4.1 Event Log 是事实源

每个 crew 有一个 append-only event stream。事件不可原地修改，只能追加补偿事件。当前状态由事件投影得到。

```text
crew.started
worker.spawn.requested
worker.spawned
turn.requested
turn.delivered
output.chunk
marker.detected
turn.completed
turn.failed
changes.recorded
scope.evaluated
review.verdict
verification.started
verification.finished
readiness.evaluated
human.required
crew.accepted
```

### 4.2 Workflow State 是控制面

supervisor 的核心不是 while loop，而是 durable workflow：

```text
RoundStarted
  -> SourceTurnRequested
  -> SourceTurnCompleted
  -> ChangesRecorded
  -> ScopeEvaluated
  -> ReviewEvaluated
  -> VerificationEvaluated
  -> ReadinessEvaluated
  -> Accepted | ChallengeIssued | HumanRequired | NextRound
```

每个状态迁移都由事件驱动，并写入 checkpoint。进程重启后，从最后一个 checkpoint 和 event log 继续，而不是重新猜测终端状态。

### 4.3 Runtime Adapter 只负责执行与观测

adapter 不能决定业务接受条件。它只把外部世界转成事件：

```text
send(turn) -> turn.delivered | turn.delivery_failed
watch(turn) -> output.chunk* -> marker.detected | process.exited | timeout
collect_artifacts(turn) -> transcript, stdout, stderr, file snapshot
cancel(turn) -> turn.cancelled
```

Claude Code/tmux adapter 的 marker 检测只是 completion evidence。最终是否完成，由 `CompletionDetector` 和 workflow policy 根据多个 evidence 判断。

## 5. 总体架构

```text
Codex Orchestrator
  |
  +-- Workflow Engine
  |     +-- crew state machine
  |     +-- turn state machine
  |     +-- retry / timeout / resume / cancellation
  |     +-- idempotency and dedupe
  |
  +-- Event Store
  |     +-- append-only JSONL / SQLite / Postgres backend
  |     +-- event projections
  |     +-- cursor / subscription API
  |
  +-- Artifact Store
  |     +-- transcripts
  |     +-- diffs and file snapshots
  |     +-- verification output
  |     +-- review verdicts
  |     +-- readiness reports
  |
  +-- Runtime Kernel
  |     +-- worker registry
  |     +-- runtime adapters
  |     +-- output ingestors
  |     +-- completion detectors
  |     +-- watchdogs
  |
  +-- Policy Engine
  |     +-- worker selection
  |     +-- write scope gate
  |     +-- review gate
  |     +-- browser/user-flow gate
  |     +-- verification gate
  |     +-- readiness gate
  |     +-- human escalation
  |
  +-- Operator Interface
        +-- CLI
        +-- UI
        +-- event tail
        +-- recovery controls
```

## 6. 主要组件

### 6.1 Event Store

职责：

- 追加事件，保证同一个 stream 内顺序一致。
- 基于 `event_id` 和 `idempotency_key` 去重。
- 支持按 `crew_id`、`worker_id`、`turn_id` 查询。
- 支持消费者 cursor，用于 UI、watcher、workflow resume。

建议初期使用 SQLite，保留 JSONL export。SQLite 提供事务、索引、并发读取和恢复能力；JSONL 适合调试和迁移。

事件基础字段：

```json
{
  "event_id": "evt_...",
  "stream_id": "crew_...",
  "crew_id": "crew_...",
  "worker_id": "worker_...",
  "turn_id": "turn_...",
  "type": "turn.completed",
  "sequence": 42,
  "created_at": "2026-05-01T12:00:00Z",
  "idempotency_key": "crew/worker/turn/action",
  "payload": {},
  "artifact_refs": []
}
```

### 6.2 Workflow Engine

职责：

- 管理 crew/round/turn 状态机。
- 在执行副作用前记录 intent，例如 `turn.requested`。
- 从事件恢复状态，避免重复发送同一 turn。
- 把 timeout、worker death、missing artifact、policy block 转成显式状态。
- 对外暴露 `run_until_blocked_or_ready(crew_id)`，而不是永久阻塞。

工作流 step 必须满足：

- 可重入：重复调用不会重复执行不可重复副作用。
- 可恢复：进程重启后能从事件判断下一步。
- 可解释：每个 blocked/waiting 状态都有 reason 和 evidence refs。

### 6.3 Runtime Kernel

职责：

- 管理 worker 生命周期：spawn、attach、send、observe、cancel、stop。
- 把 runtime adapter 的原始输出转成标准事件。
- 管理 watchdog：deadline、idle timeout、heartbeat、process death。
- 维护 worker health：running、idle、busy、blocked、failed、stopped。

Runtime Kernel 不知道业务流程，只知道 turn lifecycle。

### 6.4 Runtime Adapter

统一接口：

```python
class RuntimeAdapter:
    def spawn_worker(spec: WorkerSpec) -> WorkerHandle: ...
    def deliver_turn(turn: TurnEnvelope) -> DeliveryResult: ...
    def watch_turn(turn_id: str) -> Iterator[RuntimeEvent]: ...
    def collect_artifacts(turn_id: str) -> list[ArtifactRef]: ...
    def cancel_turn(turn_id: str) -> CancellationResult: ...
    def stop_worker(worker_id: str) -> StopResult: ...
```

第一批 adapter：

- `ClaudeCodeTmuxAdapter`：继续使用当前 tmux/Claude Code。
- `PtyShellAdapter`：用于普通 shell/tool worker。
- `VerificationAdapter`：运行测试命令，产出 exit code/stdout/stderr artifact。
- `BrowserAdapter`：未来接 Playwright/browser-use，产出截图、trace、console logs。

### 6.5 Output Ingestor

职责：

- 从 transcript、PTY、tmux pipe-pane、process stdout 读取流式输出。
- 生成 `output.chunk` 事件。
- 按 turn 切分输出，避免旧内容污染新 turn。
- 保留原始 transcript artifact。

强约束：

- terminal snapshot 只能做 fallback。
- transcript/event stream 是 primary observation source。
- 每个 output chunk 必须绑定 `turn_id` 或标记为 `unattributed`，再由 reconciler 处理。

### 6.6 Completion Detector

职责：

- 综合多个 evidence 判断 turn 是否结束。
- 支持 marker、structured block、process exit、tool result、explicit failure、timeout。
- 输出 `turn.completed`、`turn.failed`、`turn.timeout`、`turn.inconclusive`。

完成判断不等于接受判断。`turn.completed` 只表示 worker 这一轮结束；是否可接受由 policy gates 决定。

### 6.7 Artifact Store

职责：

- 存储不可变 artifact。
- artifact 通过 content hash 或 stable path 引用。
- 所有 gate 的输入输出都要写 artifact。

核心 artifact：

- `turns/{turn_id}/transcript.txt`
- `turns/{turn_id}/output.jsonl`
- `changes/{turn_id}.json`
- `diffs/{turn_id}.patch`
- `reviews/{turn_id}.json`
- `verification/{verification_id}/stdout.txt`
- `readiness/{round_id}.json`

### 6.8 Policy Engine

职责：

- 只消费结构化 state 和 artifact。
- 不直接读取 terminal pane。
- 所有决策写 `decision.recorded` 事件。

关键 gate：

- write scope gate：基于 changed files 和 worker contract。
- review gate：基于 `review.verdict` artifact。
- verification gate：基于 exit code/stdout/stderr artifact。
- readiness gate：基于 round evidence。
- human escalation：基于 unknown、repeated failure、timeout、policy conflict。

### 6.9 Reconciler

职责：

- 进程重启后扫描 event store 和 runtime 实际状态。
- 识别 dangling turn：已 requested 但未 delivered、已 delivered 但无 completion、marker 已出现但未投影等。
- 生成补偿事件或 human escalation。

Reconciler 不直接“猜成功”。没有足够证据时，状态必须是 `inconclusive` 或 `needs_human`。

## 7. 数据模型

### 7.1 Crew

```text
crew_id
goal
status: running | waiting | ready | needs_human | failed | accepted | cancelled
current_round_id
active_worker_ids
created_at
updated_at
```

### 7.2 Worker

```text
worker_id
crew_id
contract_id
runtime_type
workspace_id
status: spawning | idle | busy | blocked | failed | stopped
capabilities
authority_level
write_scope
health
```

### 7.3 Turn

```text
turn_id
crew_id
worker_id
round_id
phase
status: requested | delivered | running | completed | failed | timeout | cancelled | inconclusive
message_artifact
deadline_at
attempt
idempotency_key
expected_outputs
completion_evidence_refs
```

### 7.4 Workflow Step

```text
step_id
crew_id
round_id
type
status: pending | running | succeeded | failed | skipped | waiting
input_refs
output_refs
decision_event_id
```

## 8. Turn 生命周期

```text
turn.requested
  -> turn.delivery_started
  -> turn.delivered
  -> output.chunk*
  -> marker.detected | structured_output.detected | process.exited | timeout.detected
  -> turn.completed | turn.failed | turn.timeout | turn.inconclusive
  -> artifacts.collected
```

每个状态迁移都写 event。任何时候 supervisor 崩溃，恢复后都可以问：

- 这个 turn 是否已经 delivered？
- 是否有 completion evidence？
- 是否已经收集 changes？
- 是否已经运行 review/verification/readiness？
- 下一步是否可重入？

## 9. Crew Round 生命周期

```text
round.started
source_turn.completed
changes.recorded
scope.evaluated

if scope challenge:
  challenge_turn.requested
  next round

review_turn.completed
review.verdict

if review block:
  challenge_turn.requested
  next round

verification.finished
readiness.evaluated

if readiness ready:
  crew.ready_for_accept
else:
  next round | needs_human
```

## 10. 错误处理策略

### 10.1 Timeout

每个 turn 有 deadline。deadline 到达后 runtime 写 `turn.timeout`，workflow 根据 phase 决策：

- source timeout：challenge 或 spawn replacement。
- review timeout：重新发送 review turn 或 needs human。
- verification timeout：记录 failed verification。
- repeated timeout：human escalation。

### 10.2 Worker Death

adapter 发现 tmux session/process 消失，写 `worker.failed` 和 `turn.failed`。workflow 不能直接重发旧消息，必须先检查是否已有 artifacts/changes。

### 10.3 Duplicate Delivery

`deliver_turn` 使用 idempotency key。如果 `turn.delivered` 已存在，则恢复时不能再次发送同一 message，除非创建新的 `attempt_id` 和补偿事件。

### 10.4 Stale Output

output ingestor 必须按 turn boundary 切分。无法归属的输出写 `output.unattributed`，不能驱动 completion。

### 10.5 Prompt Injection / False Marker

marker.detected 只是 evidence，不直接等于 policy pass。review verdict、readiness、verification 必须由独立 parser/gate 处理。

### 10.6 Partial File Writes

completion 后先记录 file snapshot/diff，再运行 gate。changes artifact 是后续 review/verification 的输入，不允许 review 直接读不稳定工作区状态作为唯一证据。

## 11. Human-in-the-loop

human escalation 不是异常，而是一等状态：

```text
human.required
human.question.created
human.response.recorded
workflow.resumed
```

触发条件：

- completion inconclusive
- review verdict unknown
- write scope block
- repeated timeout
- repeated verification failure
- worker contract conflict
- adapter cannot reconcile state

UI 应展示：

- 当前 crew/round/turn 状态
- blocked reason
- evidence refs
- recommended operator action

## 12. 外部方案借鉴

- OpenHands：typed event stream、Action/Observation 模式、事件作为 memory 和外部观察接口。
  - https://docs.openhands.dev/sdk/arch/events
  - https://docs.openhands.dev/sdk/arch/tool-system
- LangGraph：durable execution、checkpoint、thread persistence、人机中断恢复。
  - https://docs.langchain.com/oss/python/langgraph/durable-execution
  - https://docs.langchain.com/oss/python/langgraph/persistence
- AutoGen Core：runtime 管理 agent lifecycle、message delivery、agent 间通信。
  - https://microsoft.github.io/autogen/stable/user-guide/core-user-guide/index.html
- SWE-ReX / SWE-agent：shell session、命令完成识别、agent-computer interface。
  - https://github.com/SWE-agent/swe-rex
  - https://swe-agent.com/0.7/config/env/
- Temporal / durable workflow 系统：长流程、retry、activity、workflow replay、idempotency。
  - https://docs.temporal.io/

## 13. 推荐技术路线

### Phase A：Runtime Kernel 与 Event Store

- 引入 SQLite-backed event store。
- 定义 typed event schema 和 projections。
- 为当前 tmux/Claude Code worker 包一层 `RuntimeAdapter`。
- 把 `send_worker`、`observe_worker` 改成写事件。
- 保留现有 supervisor 行为，但让它从 event projection 读取 turn 状态。

### Phase B：Workflow State Machine

- 把 `supervise_dynamic()` 拆成 durable workflow steps。
- 每个 gate 成为可重入 step。
- 引入 `run_until_blocked_or_ready()`。
- 实现 crash recovery 和 duplicate-delivery guard。

### Phase C：Streaming Observation

- 用 transcript tail/watch 或 tmux pipe-pane 作为 primary output source。
- `capture-pane` 只保留为 fallback/debug。
- 输出按 turn_id 切分并写 `output.chunk`。
- CompletionDetector 产生 completion events。

### Phase D：Runtime Adapter 扩展

- 引入 shell/container adapter。
- 引入 browser adapter。
- 引入 verification adapter。
- 允许不同 worker contract 指定不同 runtime。

### Phase E：Operator UI 与 Recovery

- UI 订阅 event stream。
- 展示 crew/worker/turn DAG。
- 支持 cancel/retry/resume/human response。
- 支持 artifact drill-down。

## 14. 成功标准

系统达到以下标准，才算 V4 从 polling supervisor 升级为稳定 runtime：

- supervisor 进程被杀后，可以恢复同一 crew，不重复发送已 delivered 的 turn。
- terminal pane 滚屏不会导致 valid turn completion 丢失。
- worker 未打印 marker 时，系统能区分 still running、timeout、contract marker mismatch、process death、inconclusive。
- review/verification/readiness 都由结构化 artifact 驱动。
- UI 能解释每个 waiting/blocked 状态的原因和证据。
- 多 worker 并发时，事件不会串线，旧 turn 输出不能完成新 turn。
- 任何 ready/accept 状态都能追溯到完整 evidence chain。

## 15. 关键取舍

本设计选择自建小型 runtime kernel，而不是马上迁移到完整外部框架。原因是本项目已经有 crew、worker contract、artifact、gate、Claude Code/tmux 集成；最关键的缺口是把这些能力统一到 durable event model 中。

后续如果发现 workflow retry/replay 复杂度快速上升，可以把 Workflow Engine 替换或托管到 LangGraph/Temporal/DBOS；如果 shell runtime 复杂度上升，可以吸收 SWE-ReX 风格的 shell abstraction；如果多 agent message routing 复杂度上升，可以借鉴 AutoGen Core。

## 16. 待实现计划拆分

V4 implementation plan 应按以下独立模块拆分：

1. Event schema 与 SQLite event store。
2. Artifact store 与 stable artifact refs。
3. Runtime adapter interface。
4. ClaudeCodeTmuxAdapter 包装现有 `NativeClaudeSession`。
5. Turn state machine 与 idempotency guard。
6. Output ingestor 与 transcript watcher。
7. CompletionDetector。
8. Workflow projections 与 recovery reconciler。
9. Supervisor dynamic path 重构为 workflow steps。
10. CLI/UI event inspection 与 recovery controls。
