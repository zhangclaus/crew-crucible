# Dynamic Worker Contract Design

日期：2026-04-30
状态：设计草案 v4，等待用户审阅
范围：V3 Crew 从静态 roster / 固定角色升级为 Codex 运行时生成 worker contract 的 agent 封装层

## 1. 背景

当前 V3 已经具备：

- 原生 Claude Code CLI worker，经 tmux 可观察、可 attach、可 stop。
- implementer 默认使用 git worktree。
- `CrewSupervisorLoop` 可以按轮观察 marker、记录 changes、运行验证、challenge 或返回 `ready_for_codex_accept`。
- `WorkerSelectionPolicy` 可以在启动前用 `auto/quick/standard/full` 选择初始 worker 组合。

但它仍然有两个结构性限制：

- worker roster 主要在启动前确定。即使 `auto` 已经避免无脑三件套，它仍然不是“运行中 Codex 按需唤起 agent”。
- `WorkerRole` 目前是固定枚举，容易把系统锁死在 explorer/implementer/reviewer 这类预设角色里。

我们需要把 V3 从静态 roster 变成动态 agent control plane，并把“角色”降级为可读标签；真正驱动调度的是能力、任务合同、证据和生命周期。

## 2. CCteam-creator 可借鉴点

CCteam-creator 的价值不在于某个具体命令，而在于 agent harness 的组织方式：

- 主会话是 `team-lead`，不是普通 dispatcher。它负责用户对齐、范围权衡、阶段门禁、团队规则和全局文件。
- agent 角色是可组合的职责描述，而不是固定三件套。它提供 backend-dev、frontend-dev、researcher、e2e-tester、reviewer、custodian 这类参考画像，并强调不需要全部启用，应由 team lead 按任务推荐组合。
- agent 状态落盘到 `.plans/`：`task_plan.md`、`findings.md`、`progress.md`、`docs/`、每个 agent 的目录，以及 `team-snapshot.md`。
- `team-snapshot.md` 保存完整 onboarding prompt 和技能文件时间戳，用于快速 resume。
- 内置协议比角色本身更重要：Task Confirmation、3-Strike Escalation、Doc-Code Sync、Review Dimensions、Failure-to-Guardrail、Assumption Audit、Golden Rules CI。
- reviewer 不是“温和总结者”，而是 calibrated evaluator：带 review dimensions、anti-leniency rule、BLOCK/WARN/OK verdict。

参考来源：

- CCteam-creator README: https://github.com/jessepwj/CCteam-creator
- Skill definition: https://raw.githubusercontent.com/jessepwj/CCteam-creator/master/skills/CCteam-creator/SKILL.md
- Roles reference: https://raw.githubusercontent.com/jessepwj/CCteam-creator/master/skills/CCteam-creator/references/roles.md
- Templates / team snapshot: https://raw.githubusercontent.com/jessepwj/CCteam-creator/master/skills/CCteam-creator/references/templates.md

## 3. 不照搬的部分

我们不应该直接把 CCteam-creator 的 skill 复制进 orchestrator，也不应该把 tmux/worktree/session 生命周期塞进 skill。

原因：

- Codex orchestrator 需要稳定管理进程、tmux session、worktree、verification、prune、rollback。这些是 runtime core，不是 prompt 层能力。
- Claude Code experimental agent teams 依赖平台能力和环境变量，适合作为 Claude Code 内部团队方案，不适合作为我们 V3 的唯一底座。
- 我们的目标是 Codex-first：Codex 判断是否 spawn / stop / challenge / accept，Claude worker 是执行面。

因此采用分层：

```text
Orchestrator Core
  管理 crew / workers / tmux / worktree / verification / state / lifecycle

Capability / Protocol Packs
  定义能力片段、prompt 模板、协议、产出 artifact schema

Decision Policy
  根据 blackboard、verification、worker 状态和目标动态生成 worker contract 并决定下一步 action
```

## 4. learn-claude-code 可借鉴点

learn-claude-code 的中文教程里，agent 通信方案对 V3 很有启发：

- s04 Subagents：子 agent 用干净上下文执行，父 agent 只接收摘要，避免主上下文被大量工具输出污染。
- s07 Task System：任务图落盘，任务状态和依赖比临时 todo 更可靠。
- s09 Agent Teams：持久队友有 roster/status，并通过 JSONL inbox 通信。
- s10 Team Protocols：通信不是自由聊天，而是 request/response + request_id + FSM，可用于计划审批、优雅关机、交接确认。
- s11 Autonomous Agents：空闲 agent 轮询 inbox 和任务板，能从 idle 恢复到 working。
- s12 Worktree Isolation：任务和 worktree 显式绑定，生命周期事件写 JSONL，崩溃后可恢复现场。

参考来源：

- https://github.com/shareAI-lab/learn-claude-code/tree/main/docs/zh
- https://raw.githubusercontent.com/shareAI-lab/learn-claude-code/main/docs/zh/s04-subagent.md
- https://raw.githubusercontent.com/shareAI-lab/learn-claude-code/main/docs/zh/s07-task-system.md
- https://raw.githubusercontent.com/shareAI-lab/learn-claude-code/main/docs/zh/s09-agent-teams.md
- https://raw.githubusercontent.com/shareAI-lab/learn-claude-code/main/docs/zh/s10-team-protocols.md
- https://raw.githubusercontent.com/shareAI-lab/learn-claude-code/main/docs/zh/s11-autonomous-agents.md
- https://raw.githubusercontent.com/shareAI-lab/learn-claude-code/main/docs/zh/s12-worktree-task-isolation.md

需要注意：我们不能直接照搬“agent 互相自由发消息”的形态。V3 的核心仍然是 Codex 监管。更合理的是 **Codex-mediated mailbox**：

```text
worker transcript / outbox block
  -> Codex supervisor parses message intent
  -> MessageBus records append-only event
  -> DecisionPolicy approves / rejects / routes
  -> Codex injects inbox digest into target worker's next turn
```

也就是说，worker 之间可以“通信”，但不直接互相操作 tmux，也不绕过 Codex 写控制状态。所有消息都是可审计、可重放、可拒绝的 decision input。

## 5. 本地 harness 源码可借鉴点

本地 `/Users/zhanghaoqian/Documents/zhangzhang/agent/skills/harness` 里有两类非常相关的源码：

- Hermes Agent: `hermes agent/hermes-agent`
- Claude Code source / best practice: `claude/claude-code`、`ClaudeCodeBestPractice/claude-code-best-practice`

### 5.1 Hermes Agent

Hermes 的价值在于它把 agent 运行时拆成几个清晰的运行边界：

- `HermesAgentLoop` 是标准 tool-calling loop：每轮 API call、解析 tool calls、校验 tool name、执行工具、记录 `turns_used`、`finished_naturally`、`reasoning_per_turn`、`tool_errors`。V3 supervisor 应该借鉴这种 **turn observation envelope**，不要只看 Claude CLI 最后一屏文本。
- `ToolContext` 用同一个 `task_id` 让 reward/verification 函数访问模型 rollout 使用过的同一终端/浏览器/文件状态。V3 里 verification runner 应该继续绑定 worker/worktree/session，而不是在另一个 cwd 或新环境里验证。
- `ContextEngine` / `ContextCompressor` 把压缩设计成可替换引擎，并明确区分 active instruction 和 reference summary。V3 的 `team_snapshot`、worker resume prompt、mailbox digest 也要加类似 fence，避免旧消息被当作新指令。
- `MemoryManager` 只允许一个外部 memory provider，避免工具 schema 膨胀和冲突。V3 的 capability/provider 也应该有冲突控制：同一类外部 provider 默认只启用一个，显式配置才扩展。
- `tool_result_storage.py` 对大工具输出做三层防护：工具自截断、单结果落盘、单轮总预算落盘。V3 的 tmux transcript、verification output、diff、review report 都应走 artifact reference，不应把巨量输出直接塞回 worker 或 Codex 上下文。
- `acp_adapter/events.py` 把工具开始、工具结束、thinking、agent message 都转换成 session update。V3 可以采用统一 event schema，让 tmux 观察、message bus、verification、progress UI 都产出同一种事件流。
- `gateway/delivery.py` 的 delivery target 思路适合 message routing：`origin`、`local`、显式平台目标。V3 可抽象为 `codex`、`worker:<id>`、`contract:<id>`、`broadcast`、`artifact` 这些 routing target。

不照搬 Hermes 的部分：

- 不把 verifier 变成拥有所有工具的自由 reward 函数；V3 verification 仍由 Codex control plane 触发。
- 不引入长期 gateway daemon 作为核心依赖。
- 不做跨平台消息网关，先只做 crew 内部通信。

### 5.2 Claude Code Source

Claude Code 源码里最值得借鉴的是已经成熟的 agent/task 生命周期：

- `Task.ts` 明确区分 `pending/running/completed/failed/killed`，并提供 `isTerminalTaskStatus()`。V3 的 `WorkerStatus` 和 `CrewTaskStatus` 也应该统一 terminal 判断，避免给 dead worker 发消息或重复清理。
- `QueryEngine` 是“一会话多 turn”的持久对象：`submitMessage()` 只是开启新 turn，messages、file cache、permission denials、usage 都留在 engine 内。V3 的 `CrewSupervisorLoop` 也应该把 crew 当成持久 session，而不是每次 supervise 都重新推断一切。
- `query.ts` 处理了 compact、microcompact、tool summary、fallback、stop hook、token budget continuation。V3 不需要复制这些功能，但应该借鉴它的 **transition reason** 记录方式，每一次 continue/retry/challenge 都写清楚原因。
- `AgentTool/runAgent.ts` 给 subagent 独立工具集、MCP、permission mode、abort controller、transcript subdir 和 hooks，并在结束时清理。V3 的 `AgentProfile` 也要显式包含 tool/permission/scope/abort/transcript 生命周期。
- `LocalAgentTask` 用 `pendingMessages` 在 tool-round 边界注入消息，完成后用 `task-notification` 注入结果，并携带 output file、status、usage、worktree 信息。V3 的 mailbox digest 应该同样只在安全边界注入，结果用 artifact refs，不用实时乱插。
- `SendMessageTool` 支持普通消息、broadcast、`shutdown_request/response`、`plan_approval_response`，还对跨机器 bridge message 强制 user consent。V3 message bus 应该保留结构化类型、request_id 和安全审批，不让任意 prompt 直接跨 worker 注入。
- `TeamCreateTool` 写 `config.json`，保存 team lead、成员、session、cwd、tmux pane、worktree、permission mode。V3 的 `team_snapshot.json` 应该不仅列 worker，还要记录 contract、workspace、terminal pane、permission/authority、last activity。
- `EnterWorktreeTool` / `ExitWorktreeTool` 的设计重点不是创建 worktree，而是安全退出：no-op 防误删、keep/remove、dirty check、恢复 cwd、清缓存、tmux 收尾。V3 的 worker worktree lifecycle 也应该有 `keep/remove` 策略和拒绝危险删除的状态。
- `AgentTool` 的 async/background 设计很实用：前台 agent 运行一段时间后可以 background，完成时异步通知。V3 现在的 Claude CLI worker 本来就是后台 tmux，因此更应该提供 `worker observe/tail/attach` 和 completion notification，而不是阻塞等待。

不照搬 Claude Code 的部分：

- 不直接使用 Claude Code 的内部 `TeamCreate` / `SendMessage` 工具作为 V3 控制层；这些是 Claude session 内部能力，V3 需要外层 Codex 审计。
- 不依赖 feature flag 下的 experimental swarm 行为作为核心。
- 不让 worker 自己决定创建团队或切工作树；Codex supervisor 决定生命周期，worker 只在 contract 内执行。

### 5.3 对 V3 设计的修正

基于本地源码，V3 spec 增加三条硬要求：

1. **Event envelope first**：每个 worker turn、tool/progress observation、message route、verification、challenge、stop 都写成统一事件，至少包含 `event_id`、`crew_id`、`worker_id`、`contract_id`、`type`、`status`、`artifact_refs`、`reason`、`created_at`。
2. **Artifact reference by default**：transcript、diff、verification output、review output、large mailbox body 默认落盘，给 Codex/worker 的上下文只注入摘要和 artifact path。
3. **Safe lifecycle gates**：所有 stop/remove/accept 都必须经过 terminal-status check；涉及 worktree 删除时必须先检查 dirty/unmerged/untracked，不能 silent remove。

## 6. 目标行为

用户仍然只需要：

```bash
.venv/bin/orchestrator crew run \
  --repo /path/to/repo \
  --goal "..." \
  --verification-command ".venv/bin/python -m pytest -q"
```

内部流程变为动态，并且不再要求预设 explorer/implementer/reviewer 这类固定角色：

```text
Codex starts crew with no eager full roster
-> DecisionPolicy builds a NeedFrame from goal, repo signals, blackboard, diff, failures
-> DecisionPolicy creates a WorkerContract only when a concrete need appears
-> WorkerPool reuses a compatible active worker or starts a native Claude CLI session
-> Codex observes marker, records evidence, routes mailbox messages, verifies, challenges, or creates another contract
-> idle / terminal workers are stopped when their contract is complete
```

## 7. 核心模型

### 7.1 Capability，不是固定 Role

第一版文档把 `AgentRoleDefinition` 当作基础单元，这仍然太像固定角色。修正后，基础单元应是 capability 和 contract：

- capability 是 worker 可以承担的能力片段。
- contract 是 Codex 在某一刻为某个具体需求生成的临时工作合同。
- role 只是显示标签，例如 `api-boundary-auditor`、`ui-flow-tester`、`migration-implementer`，不参与核心类型分派。

Capability 可以是开放字符串，不做 enum 锁死。内置 capability 只是初始 vocabulary：

```text
inspect_code
edit_source
edit_tests
review_patch
run_verification
browser_e2e
research_external
write_docs
design_architecture
triage_failure
maintain_guardrails
```

一个 worker 可以同时具备多个 capability，例如：

```json
{
  "label": "retrieval-pipeline-fixer",
  "capabilities": ["inspect_code", "edit_source", "edit_tests", "run_verification"]
}
```

### 7.2 WorkerContract

新增 `WorkerContract`，作为动态 spawn/reuse 的核心对象。

字段：

```text
contract_id
label
mission
required_capabilities: list[str]
authority_level: readonly | source_write | test_write | state_write
workspace_policy: readonly | worktree | shared
write_scope
context_refs
expected_outputs
acceptance_criteria
protocol_refs
communication_policy
completion_marker
max_turns
spawn_reason
stop_policy
```

示例：

```json
{
  "label": "verification-failure-analyst",
  "mission": "Classify the failing pytest output and propose the smallest repair path.",
  "required_capabilities": ["inspect_code", "triage_failure"],
  "authority_level": "readonly",
  "workspace_policy": "readonly",
  "expected_outputs": ["failure_class", "root_cause_hypothesis", "repair_instruction"],
  "spawn_reason": "same verification command failed twice"
}
```

这比 `role_required=WorkerRole.REVIEWER` 更灵活，因为 Codex 可以为每个动态场景创造准确的 worker identity。

### 7.3 AgentProfile

`AgentProfile` 是真正传给 Claude Code CLI 的 prompt 组装结果：

```text
AgentProfile = WorkerContract + capability fragments + protocol packs + project context + completion marker
```

它负责回答：

- 你是谁：动态 label，不是固定角色枚举。
- 你能做什么：capabilities 和 authority。
- 你不能做什么：write scope、forbidden paths、禁止越权。
- 你要产出什么：expected outputs、artifact/report schema。
- 你如何通信：允许向 Codex、某个 worker contract、广播 channel 发什么类型的消息。
- 你什么时候停：completion marker、stop policy。

`WorkerRecord` 应保存 `label`、`contract_id`、`capabilities` 和 `authority_level`。`role` 字段可以短期保留为兼容字段，但不再是调度核心。

### 7.4 Capability / Protocol Packs

原文的 RolePack 应改名为 Capability / Protocol Pack。它是 prompt/protocol 层，不负责启动进程，也不限定固定 worker 身份。

目录建议：

```text
src/codex_claude_orchestrator/agent_packs/
  builtin/
    capabilities/
      inspect_code.md
      edit_source.md
      review_patch.md
      browser_e2e.md
      triage_failure.md
    protocols/
      task_confirmation.md
      review_dimensions.md
      three_strike_escalation.md
      doc_code_sync.md
    common_protocols.md
```

每个 capability fragment 包含：

- capability instruction
- allowed actions
- forbidden actions
- required report format
- artifact output schema
- escalation rule
- completion marker rule

这等价于把 CCteam-creator 的 roles.md / onboarding.md 拆成可组合、可版本化的本地 prompt fragments。

### 7.5 AgentMessageBus

新增 `AgentMessageBus`，借鉴 learn-claude-code 的 JSONL inbox，但改成 append-only + cursor，而不是 drain-on-read。

原因：

- drain-on-read 简单，但会丢审计线索。
- append-only log 更适合 orchestrator 恢复、debug、回放和最终报告。
- 每个 worker 的已读位置用 cursor 保存，避免重复注入。

状态布局：

```text
.orchestrator/crews/<crew_id>/
  messages.jsonl
  message_cursors.json
  protocol_requests.jsonl
  inboxes/
    codex.jsonl
    <worker_id>.jsonl
```

`messages.jsonl` 是全局 append-only 总线；`inboxes/*.jsonl` 是方便观察的投递副本。真实恢复以 `messages.jsonl + message_cursors.json` 为准。

消息 schema：

```json
{
  "message_id": "msg-...",
  "thread_id": "thread-...",
  "request_id": "req-...",
  "crew_id": "...",
  "from": "worker-a",
  "to": "worker-b|codex|broadcast",
  "type": "handoff|question|answer|plan_request|plan_response|shutdown_request|shutdown_response|evidence|status",
  "body": "...",
  "artifact_refs": [],
  "requires_response": false,
  "created_at": "..."
}
```

第一版不要求 Claude Code CLI worker 自己调用专用 message tool。更稳的做法是让 worker 在 transcript 中输出结构化块：

```text
<<<CODEX_MESSAGE
to: codex
type: question
body: Need a second contract to inspect API compatibility before editing.
>>>
```

Supervisor 观察 worker 时解析这些块，写入 bus，再由 DecisionPolicy 决定是否投递、转化为新 contract、或拒绝。

未来可以加 CLI：

```bash
orchestrator crew message send --crew ... --from ... --to ... --type ...
orchestrator crew inbox read --crew ... --worker ...
```

但不把它作为 MVP 的前置条件，因为原生 Claude Code CLI 已经能通过 transcript marker 被 Codex 观察。

### 7.6 ProtocolRequest FSM

借鉴 learn-claude-code s10 的 request/response 模式，所有需要明确确认的跨 agent 协作都用统一 FSM：

```text
pending -> approved
pending -> rejected
pending -> expired
pending -> cancelled
```

适用场景：

- `plan_request`: source_write worker 先提交计划，Codex 或 auditor 批准后再动高风险文件。
- `handoff_request`: 一个 worker 把 findings 交给另一个 worker，目标 worker ack 后才视为可用上下文。
- `shutdown_request`: Codex 请求 worker 优雅退出，worker 可先输出未保存状态再同意。
- `review_request`: Codex 要求独立检查 patch，auditor 输出 `OK/WARN/BLOCK` 并引用同一个 request_id。
- `clarification_request`: worker 请求 Codex 或另一个 contract 澄清边界。

`protocol_requests.jsonl` 保存每次状态变化：

```json
{
  "request_id": "req-...",
  "type": "plan_request",
  "from": "worker-a",
  "to": "codex",
  "status": "pending",
  "subject": "Edit verification pipeline",
  "created_at": "..."
}
```

DecisionPolicy 不直接读 worker 自由文本来判断协作完成，而是优先读 request FSM、artifact_refs、verification evidence。

### 7.7 CrewDecisionPolicy

把当前 `WorkerSelectionPolicy` 升级为运行时 policy。输出不再是 `list[WorkerRole]`，而是 `DecisionAction`，其中可以包含新的 `WorkerContract`。

输入：

```text
goal
crew record
task graph
blackboard entries
active worker status
changed files
diff artifact exists?
verification history
challenge count
elapsed rounds
active worker contracts
capability coverage
pending protocol requests
unrouted messages
```

输出：

```text
DecisionAction
  action_type:
    spawn_worker
    send_worker
    observe_worker
    route_message
    request_protocol_response
    record_changes
    verify
    challenge
    request_independent_check
    request_specialized_verification
    accept_ready
    needs_human
    stop_worker
  contract?
  worker_id?
  task_id?
  message?
  reason
  priority
```

示例规则：

```text
Need source edits and no active source_write worker
-> spawn WorkerContract(label="targeted-code-editor", capabilities=["inspect_code", "edit_source", "edit_tests"])

Uncertainty high before editing
-> spawn WorkerContract(label="repo-context-scout", capabilities=["inspect_code", "design_architecture"], readonly)

Patch exists and risk high
-> spawn WorkerContract(label="patch-risk-auditor", capabilities=["review_patch", "inspect_code"], readonly)

Verification failed once
-> challenge current source_write worker

Verification failed twice or same file changed repeatedly
-> spawn WorkerContract(label="verification-failure-analyst", capabilities=["triage_failure", "inspect_code"], readonly)

Verification failed three times
-> spawn guardrail-maintainer contract OR needs_human depending on failure class

Goal involves browser/UI/user flow
-> spawn WorkerContract(label="browser-flow-tester", capabilities=["browser_e2e", "review_patch"])

Verification passed and required independent checks are OK
-> accept_ready
```

### 7.8 Lazy Worker Lifecycle

新增 WorkerPool API：

```text
ensure_worker(contract, reason) -> WorkerRecord
find_compatible_worker(contract) -> WorkerRecord | None
stop_idle_worker(worker_id, reason)
```

`ensure_worker` 语义：

- 如果存在 active worker，且 capabilities 覆盖 contract、authority/workspace/write_scope 兼容、上下文没有过期，则复用。
- 如果没有兼容 worker，根据 WorkerContract 生成 task、分配 workspace、组装 AgentProfile、启动 native Claude session。
- 写入 blackboard、`decisions.jsonl` 和 message bus：谁被唤起、为什么被唤起、由哪个 decision action 触发。

### 7.9 Team Snapshot

新增：

```text
.orchestrator/crews/<crew_id>/team_snapshot.json
```

字段：

```json
{
  "crew_id": "...",
  "capability_registry_version": "...",
  "decision_policy_version": "...",
  "capabilities_available": [],
  "contracts_created": [],
  "workers_spawned": [],
  "message_cursor_summary": {},
  "open_protocol_requests": [],
  "prompt_artifacts": {},
  "last_decision": {},
  "resume_hint": "Read team_snapshot.json and blackboard before supervising."
}
```

这借鉴 CCteam-creator 的 `team-snapshot.md`，但使用 JSON 方便 orchestrator 恢复和测试。完整 prompt 仍应保存为 artifact：

```text
artifacts/workers/<worker_id>/onboarding_prompt.md
```

### 7.10 Protocol Packs

从 CCteam-creator 借鉴但精简为 V3 第一版可落地协议：

1. Task Confirmation
   - 大任务 source_write worker 先确认理解，再编码。
   - 对小任务可跳过。

2. 3-Strike Escalation
   - 同类 verification/challenge 连续 3 次失败后，不继续 silent retry。
   - DecisionPolicy 选择新的 readonly auditor、guardrail maintainer 或 needs_human。

3. Review Dimensions
   - patch auditor 不只列问题，还输出 `OK/WARN/BLOCK`。
   - 支持项目级 dimensions，后续可存在 `.orchestrator/crews/<id>/review_dimensions.json`。

4. Doc-Code Sync
   - 当 changed files 涉及 API/architecture/config，DecisionPolicy 可以创建 doc-code sync auditor contract。

5. Failure-to-Guardrail
   - 重复 failure 写入 `known_pitfalls.jsonl`，后续 guardrail maintainer contract 可转成检查项。

## 8. Dynamic Supervisor Loop

当前 loop：

```text
explorer -> implementer -> reviewer -> verify
```

新 loop：

```text
while round < max_rounds:
  snapshot = build_supervisor_snapshot()
  action = decision_policy.decide(snapshot)
  execute(action)
  record_decision(action)
  if action is accept_ready / needs_human / waiting:
      return
```

第一版 action executor：

```text
spawn_worker:
  WorkerPool.ensure_worker(contract)

send_worker:
  NativeClaudeSession.send with unique turn_marker

observe_worker:
  capture pane and marker, parse CODEX_MESSAGE blocks

route_message:
  AgentMessageBus append + optional inbox injection to target worker

record_changes:
  WorkerChangeRecorder writes changed files + diff.patch

verify:
  CrewVerificationRunner runs in the current source_write worker worktree

challenge:
  blackboard risk + protocol request + send repair instruction to the responsible worker contract

accept_ready:
  return ready_for_codex_accept, do not auto merge
```

这里的关键不是 while loop 本身，而是每次 loop 都重新判断“当前还缺什么能力”。固定角色方案容易变成“有 reviewer 就 review，没有就跳过”；contract 方案会问“现在是否需要独立只读风险判断、失败分类、UI 验证或文档同步”，需要时再生成精确 worker。

通信层也必须进入 loop，而不是作为旁路线程常驻。Codex 每轮观察 worker 后先抽取消息和 request，再让 DecisionPolicy 决定是否投递、拒绝、转成新 contract，或者等待目标 worker ack。这样能借鉴 agent mailbox，又不让 Claude worker 绕开监督。

## 9. 状态文件布局

保留现有：

```text
.orchestrator/crews/<crew_id>/
  crew.json
  tasks.json
  workers.jsonl
  blackboard.jsonl
  artifacts/
```

新增：

```text
  team_snapshot.json
  events.jsonl
  decisions.jsonl
  known_pitfalls.jsonl
  capability_registry.json
  worker_contracts.jsonl
  agent_profiles.jsonl
  messages.jsonl
  message_cursors.json
  protocol_requests.jsonl
  inboxes/<worker_id>.jsonl
  artifacts/contracts/<contract_id>.json
  artifacts/workers/<worker_id>/onboarding_prompt.md
  artifacts/workers/<worker_id>/agent_profile.md
```

`blackboard.jsonl` 继续保存 evidence 和 claim；`events.jsonl` 保存统一运行事件；`decisions.jsonl` 专门保存 Codex decision action；`messages.jsonl` 保存 agent 通信事件；`protocol_requests.jsonl` 保存 request FSM 状态变化。它们分开存储，方便 replay 和定位问题。

## 10. CLI 设计

启动仍然简单：

```bash
orchestrator crew run --repo ... --goal ... --verification-command ...
```

新增/调整：

```bash
orchestrator crew capabilities list
orchestrator crew capabilities show --capability review_patch
orchestrator crew contracts --repo ... --crew ...
orchestrator crew messages --repo ... --crew ...
orchestrator crew inbox --repo ... --crew ... --worker ...
orchestrator crew protocols --repo ... --crew ...
orchestrator crew decisions --repo ... --crew ...
orchestrator crew snapshot --repo ... --crew ...
orchestrator crew supervise --dynamic
```

`--workers` 仍短期保留，用于兼容旧测试和人工强制 legacy roster：

```bash
--workers implementer
--workers implementer,reviewer
--workers auto
```

但新路径应优先使用：

```bash
--spawn-policy dynamic
--max-workers 4
--seed-contract source_write
```

`--seed-contract` 不是固定角色名，只是给 DecisionPolicy 一个启动提示。例如 `source_write` 表示允许开局就创建一个具备写代码能力的 contract；省略时可以先由 supervisor 判断是否需要只读侦察。

新增：

```bash
--spawn-policy dynamic | static
```

默认：

```text
crew run -> dynamic
crew start -> creates crew state only, optionally seeds contracts
```

## 11. 为什么不全做成 skill

Capability/protocol 适合 skill 化：

- patch auditor 的评分规则
- browser flow tester 的测试协议
- guardrail maintainer 的写法
- repo scout 的 findings 格式

Core lifecycle 不适合 skill 化：

- tmux session 创建/关闭
- worktree 创建/rollback
- verification cwd
- active_worker_ids
- message routing and request FSM
- prune
- team_snapshot replay

因此最终架构是：

```text
Core owns execution.
Skills/capability packs own behavior.
Decision policy binds them together.
```

## 12. 分阶段实现建议

### Phase 1: WorkerContract + Event Envelope 兼容层

- 新增 `WorkerContract`、`AgentProfile`、`DecisionAction`。
- 新增 `CrewEvent` / `WorkerTurnObservation`，把 marker、message、verification、challenge、stop 都归一成事件。
- `WorkerRecord` 增加 `label`、`contract_id`、`capabilities`、`authority_level`。
- `WorkerRole` 保留为兼容字段，但内部 API 开始接受字符串 label 和 capability list。
- WorkerPool 支持 `ensure_worker(contract)` 和 `find_compatible_worker(contract)`。
- TaskGraphPlanner 支持按 contract 动态创建 task，不再预建三件套。

### Phase 2: Message Bus + ProtocolRequest

- 新增 `AgentMessage`、`AgentMessageBus`、`ProtocolRequest`。
- Supervisor 观察 transcript 时解析 `<<<CODEX_MESSAGE ... >>>` 块。
- 消息写入 append-only `messages.jsonl`，按 cursor 注入目标 worker inbox。
- `plan_request`、`handoff_request`、`shutdown_request` 使用统一 FSM。

### Phase 3: Dynamic Decision Loop MVP

- SupervisorLoop 改成 decision-loop。
- 第一版实现：spawn source_write worker、observe、route_message、record_changes、verify/challenge。
- patch 产生后按风险动态创建 readonly auditor contract。
- verification 连续失败后动态创建 failure analyst contract。
- 每个 decision 写入 `decisions.jsonl`。

### Phase 4: Persistent Team Snapshot

- 写 `team_snapshot.json`。
- 每个 worker 保存完整 onboarding prompt artifact。
- snapshot 记录 open protocol requests 和 message cursor summary。
- snapshot 记录 terminal pane、workspace path、permission/authority、last activity、terminal status。
- `crew supervise` 启动时先读取 snapshot 和 blackboard 恢复上下文。

### Phase 5: Capability / Protocol Packs

- 加 capability fragment markdown。
- patch auditor 输出 OK/WARN/BLOCK。
- source_write worker 支持 Task Confirmation。
- verification fail 3 次触发 3-Strike。

### Phase 6: Specialized Contracts

- UI/browser 相关 goal 动态唤起 browser-flow-tester contract。
- 重复 failure / doc-code drift 动态唤起 guardrail-maintainer contract。
- known pitfalls 和 guardrail capture 落盘。

## 13. 第一版不做

- 不接 Claude Code experimental agent teams。
- 不让 agent 直接互相发 tmux 消息；仍由 Codex control plane 路由。
- 不允许 worker 直接修改 `crew.json`、`workers.jsonl`、`protocol_requests.jsonl` 等控制状态。
- 不自动 merge。
- 不把所有 CCteam-creator `.plans/` 模板完整复制进来。
- 不做长期 daemon；仍是显式 `crew run/supervise`。
- 不把 explorer/implementer/reviewer 扩成更多固定 enum；新能力必须通过 contract/capability 表达。

## 14. 验收标准

- 小任务最多只 spawn 一个 source_write contract，不启动固定三件套。
- 上下文不足时，DecisionPolicy 可创建 readonly context-scout contract。
- patch 产生后，DecisionPolicy 可按风险创建 patch auditor contract。
- verification 连续失败后，DecisionPolicy 不再无限 retry，触发 failure analyst / guardrail maintainer / needs_human。
- 每次 spawn 都有 `reason`，写入 `decisions.jsonl` 和 blackboard。
- 每个 worker turn 都生成 `WorkerTurnObservation`，记录 marker、status、message blocks、artifact refs 和 failure reason。
- 大输出默认落盘，worker/Codex 上下文只收到摘要和 artifact path。
- worker 输出的 `CODEX_MESSAGE` 能被 supervisor 解析、落盘、按 decision 投递或拒绝。
- 计划审批、交接确认、优雅关机至少一种协议通过 request_id 完成 pending -> approved/rejected 的状态流。
- `team_snapshot.json` 能说明当前 crew 有哪些 capabilities、contracts、workers，以及每个 worker 为什么存在。
- worktree stop/remove 能拒绝危险删除，并能选择 keep/remove。
- 源码中新的动态调度路径不依赖 `WorkerRole.EXPLORER/IMPLEMENTER/REVIEWER` 决定行为；这些只作为 legacy alias。
- 全量测试通过，V1/V2 bridge/session 不回归。

## 15. 推荐结论

采用 **dynamic worker contract + capability pack** 设计：

- `orchestrator core` 负责生命周期、状态和验证。
- `capability/protocol packs` 封装 agent 能力与协议。
- `decision policy` 在运行中生成 contract，并决定是否 spawn/reuse/stop 某个 worker。
- `message bus + protocol request FSM` 让 worker 之间可以交接、提问、审批和关机，但每一步都被 Codex 记录和监管。
- `event envelope + artifact reference` 让整个过程可观察、可恢复，并避免上下文被工具输出淹没。
- `team snapshot` 负责 resume 和可审计性。

这比固定三件套更接近 CCteam-creator 的 team-lead 思路，也更符合我们之前讨论的“Codex 思考、Claude Code 执行、Codex 持续监督”。核心不是“有几个角色”，而是 Codex 在每一轮知道当前缺什么能力，并把这个能力包装成一个可观察、可停止、可验证的 Claude Code CLI worker。
