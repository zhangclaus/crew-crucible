# 主流 Agent 框架调研：设计优点与本项目可吸收点

日期：2026-05-03

状态：调研文档，供本项目演进参考

## 1. 调研目标

调研当前主流 agent 框架的设计模式，与本项目（Codex Claude Orchestrator）做对比，识别可以吸收的优点和可优化的方向。

调研对象：

| 框架 | 维护方 | 定位 |
| --- | --- | --- |
| OpenAI Agents SDK | OpenAI | 轻量 agent 编排 + sandbox 执行 |
| AutoGen 0.4 | Microsoft | 事件驱动多 agent 运行时 |
| LangGraph | LangChain | 状态机图 agent 工作流 |
| CrewAI | CrewAI Inc | 角色扮演 crew 协作 |

## 2. 框架核心设计分析

### 2.1 OpenAI Agents SDK

#### 2.1.1 Agent 模型

Agent = LLM + instructions + tools + guardrails + handoffs。

不是独立进程，而是一个配置好的 LLM 调用单元。

#### 2.1.2 双重委托模式

- **Agents as Tools**：子 agent 作为工具被父 agent 调用，父 agent 保留控制权，子 agent 输出内联回父 agent 上下文。
- **Handoffs**：控制权完全转移到另一个 agent，适合任务分工。

Handoffs 是 tool 的一种特殊形式——对 LLM 来说，handoff 就是一个名为 `transfer_to_xxx_agent` 的工具。

Handoff 支持：
- `input_type`：LLM 生成的结构化参数（Pydantic model）
- `input_filter`：控制下游 agent 看到什么历史（如 `remove_all_tools`）
- `on_handoff`：回调钩子
- `is_enabled`：动态开关

#### 2.1.3 Sandbox Agent

新特性，提供容器化执行环境：
- `SandboxAgent` = Agent + `default_manifest` + `capabilities` + `run_as`
- Manifest 声明文件系统内容
- Capabilities：Filesystem、Shell、Memory、Skills、Compaction
- 后端：Unix local sandbox / Docker sandbox
- `SandboxSession` + `SandboxSessionState` 管理生命周期

#### 2.1.4 Guardrails

双层验证：
- Input guardrails：在 agent chain 入口验证用户输入
- Output guardrails：在 agent 输出后验证结果
- Tool guardrails：per-tool 级别验证

可并行运行，不阻塞 agent 执行。

#### 2.1.5 Tracing

内置 tracing 子系统：traces、spans、processor、scope。贯穿所有子系统（包括 voice agent）。

#### 2.1.6 Sessions

自动管理对话历史，支持 Redis 和 SQLAlchemy 作为持久化后端。

---

### 2.2 AutoGen 0.4

#### 2.2.1 分层架构

| 层 | 包 | 职责 |
| --- | --- | --- |
| Core | `autogen-core` | 底层 agent 运行时、消息协议、事件驱动 |
| AgentChat | `autogen-agentchat` | 高层多 agent 对话 API |
| Extensions | `autogen-ext` | 工具、LLM、第三方集成 |

AgentChat 建立在 Core 之上。简单场景用 AgentChat，需要精细控制时降到 Core。

#### 2.2.2 事件驱动运行时

- Agent 不由应用代码直接实例化，由 runtime 管理生命周期
- 消息通过 `message_handler()` 装饰器 + 类型提示路由
- `SingleThreadedAgentRuntime` 用于本地开发
- `stop_when_idle()` 等待所有消息处理完毕

#### 2.2.3 Agent 注册模式

Agent type string -> factory function。多个 type 可以共享同一个 class，但用不同构造参数。

#### 2.2.4 编排模式

| 模式 | 特点 |
| --- | --- |
| Selector Group Chat | 集中式选择器，共享上下文 |
| Swarm | 分布式 tool-based 选择，共享上下文 |
| GraphFlow | 有向图工作流 |
| Magentic-One | 专用多 agent 系统 |

#### 2.2.5 分布式运行时

支持跨进程/跨机器的 agent 部署，通过消息传递协议通信。

---

### 2.3 LangGraph

#### 2.3.1 核心模型：状态机图

StateGraph = 节点 + 边 + 状态。

- 节点：Python 函数，读写 state
- 边：固定转移 or 条件路由
- 状态：TypedDict / dataclass / Pydantic BaseModel

#### 2.3.2 状态管理

- **Reducers**：每个 state key 有独立 reducer。默认覆盖写，`Annotated[list, add]` 做列表追加。
- **多 Schema**：支持 private state channels、input/output schema 约束。
- **MessagesState**：预构建的消息状态，`add_messages` 处理消息追加和 ID 去重。

#### 2.3.3 Command 原子

`Command` = state update + routing：
- `update` 修改状态
- `goto` 导航到节点
- `graph=Command.PARENT` 从子图跳到父图
- `resume` 中断后恢复

#### 2.3.4 检查点与持久化

编译时注入 checkpointer，支持状态持久化。图拓扑变更后，已完成的线程可完全迁移。

#### 2.3.5 多 Agent 模式

- `Send`：map-reduce 模式，条件边返回多个 Send 对象并行执行
- `Command.PARENT`：子图节点跳到父图，实现 agent handoff
- 子图组合：共享或独立 state schema

#### 2.3.6 人机协作

中断机制（interrupt）支持暂停执行等待人类输入/审批。

#### 2.3.7 运行时上下文

`context_schema` 传递非状态依赖（模型名、DB 连接），通过 `Runtime[ContextSchema]` 在节点内访问。

---

### 2.4 CrewAI

#### 2.4.1 Crew 模型

Crew = Agents + Tasks + Process。

- Agent：有 role、goal、backstory 的 LLM 执行者
- Task：绑定到 agent 的工作单元
- Process：sequential（线性）或 hierarchical（manager agent 协调）

#### 2.4.2 Flows（编排层）

Flows 在 Crew 之上，提供事件驱动编排：

- `@start()`：入口点，多个可并行
- `@listen(method)`：上游完成后触发
- `@router()`：条件分支
- `or_()` / `and_()`：组合触发条件

#### 2.4.3 Flow 状态管理

- 非结构化：dict，灵活
- 结构化：Pydantic BaseModel，类型安全
- 每个 flow state 自动生成 UUID

#### 2.4.4 Flow 持久化

`@persist` 装饰器自动保存到 SQLite。支持：
- 恢复：传入 state_id 继续执行
- Fork：从历史快照创建新运行

#### 2.4.5 记忆系统

三层记忆：short-term、long-term、entity memory。基于 embedder（默认 OpenAI）。Flow 层有 `remember` / `recall` / `extract_memories`，跨运行持久化到 LanceDB。

#### 2.4.6 检查点

task 完成后保存 crew 状态，`Crew.from_checkpoint()` 恢复长时运行或中断的任务。

---

## 3. 与本项目对比

### 3.1 对比总表

| 维度 | OpenAI SDK | AutoGen 0.4 | LangGraph | CrewAI | 本项目 |
| --- | --- | --- | --- | --- | --- |
| 控制模型 | LLM 自主 handoff | runtime 管理 | 显式状态机图 | manager agent 协调 | Codex 外部控制平面 |
| Agent 生命周期 | Runner 管理 | runtime 创建/销毁 | 图节点函数 | crew kickoff | tmux session + WorkerPool |
| 状态管理 | Session + Redis/SQL | AgentMetadata | StateGraph + checkpointer | dict/Pydantic + SQLite | JSONL artifacts + EventStore |
| 消息通信 | handoff as tool | 消息类型路由 | state 共享 | 任务链 | CODEX_MESSAGE + inbox/cursor |
| 执行隔离 | Sandbox (Docker/local) | 分布式 runtime | 子图 | worktree（无） | git worktree + tmux |
| 验证/安全 | Guardrails (双层) | 无内置 | 无内置 | SecurityConfig | PolicyGate + verification + review |
| 可观测性 | 内置 tracing | 日志+tracing | LangGraph Studio | callback | JSONL artifacts + event projection |
| 持久化 | Session/Redis/SQL | 有限 | checkpointer | checkpoint + SQLite | JSONL + EventStore (SQLite/PG) |
| 人机协作 | 内置 | human-in-the-loop | interrupt | @human_feedback | needs_human + crew accept |

### 3.2 控制模型差异

**本项目特点**：Codex 是外部控制平面，worker 是被管理的执行者。所有关键动作（spawn、send、verify、accept）都经过 Codex。

**其他框架**：
- OpenAI SDK：LLM 自主决定 handoff，框架只提供机制
- AutoGen：runtime 管理 agent 生命周期，agent 之间直接消息通信
- LangGraph：开发者显式定义状态机，图结构决定流转
- CrewAI：manager agent 在框架内协调

**评估**：本项目的 Codex-mediated 模式在安全性和可审计性上最强，但灵活性最低。当前阶段这是正确选择。

### 3.3 状态管理差异

**本项目特点**：JSONL append-only + EventStore (SQLite/PG)。每个 crew 独立目录，artifact 落盘。

**其他框架**：
- LangGraph：最成熟，StateGraph + reducer + checkpointer，支持图拓扑变更后迁移
- CrewAI：Pydantic state + SQLite persist，支持 fork/resume
- AutoGen：较弱，主要靠 runtime 管理
- OpenAI SDK：Session 抽象 + 外部后端

**评估**：本项目的 EventStore 方向正确（append-only event sourcing），但缺少：
- 类型化的 state reducer
- 检查点/恢复机制
- 状态 schema 版本管理

---

## 4. 可吸收的设计优点

### 4.1 高优先级

#### 4.1.1 Guardrails 双层验证（参考 OpenAI SDK）

**现状**：本项目有 PolicyGate（命令安全）和 verification runner，但都是后置验证。没有 input guardrail 在 agent 执行前拦截不合理的请求。

**可吸收**：
- Input guardrail：在 send_worker 前，验证 turn prompt 是否符合 contract scope
- Output guardrail：在 outbox 解析后，验证 worker 输出是否满足 expected_outputs 和 acceptance_criteria
- 并行运行：guardrail 和 agent 执行可以并行，不增加延迟

**落点**：
```python
class WorkerGuardrail:
    def check_input(self, turn: TurnEnvelope, contract: WorkerContract) -> GuardrailResult
    def check_output(self, outbox: WorkerOutbox, contract: WorkerContract) -> GuardrailResult
```

#### 4.1.2 Handoff input_filter 历史控制（参考 OpenAI SDK）

**现状**：本项目的 inbox digest 机制还不完整。worker 之间传递上下文时，要么全量注入，要么只传摘要。

**可吸收**：
- `input_filter` 概念：控制下游 worker 看到什么历史
- 内置 filter 如 `remove_all_tools`：剥离上游的工具调用细节
- 自定义 filter：按需裁剪历史

**落点**：`TurnContextBuilder` 在组装 turn 时，根据 contract 的 communication_policy 选择 filter 策略。

#### 4.1.3 状态检查点与恢复（参考 LangGraph / CrewAI）

**现状**：本项目有 team_snapshot.json 用于 resume reference，但这只是一个静态快照，不是真正的检查点。如果 crew run 中断，无法从精确断点恢复。

**可吸收**：
- LangGraph 的 checkpointer：在关键状态变更后保存检查点
- CrewAI 的 checkpoint：task 完成后保存状态，支持 from_checkpoint 恢复
- Flow fork：从历史快照创建新运行

**落点**：
```python
class CrewCheckpoint:
    crew_id: str
    turn_id: str
    event_sequence: int
    worker_states: dict
    snapshot_at: datetime

class CheckpointManager:
    def save(self, crew_id: str, event_store: EventStore) -> CrewCheckpoint
    def restore(self, checkpoint_id: str) -> CrewState
    def fork(self, checkpoint_id: str, new_crew_id: str) -> CrewState
```

#### 4.1.4 类型化状态与 Reducer（参考 LangGraph）

**现状**：本项目的状态是 dict/JSON，没有类型约束。EventStore 的 payload 是自由结构。

**可吸收**：
- State schema 定义：用 Pydantic model 定义 crew state 结构
- Reducer：每个 state field 有独立的合并策略（覆盖、追加、去重）
- 多 schema：区分 public state（projection 可读）和 private state（内部使用）

**落点**：`CrewProjection` 可以升级为 typed state，event payload 通过 reducer 聚合。

### 4.2 中优先级

#### 4.2.1 Agent as Tool 模式（参考 OpenAI SDK）

**现状**：本项目的 worker 只有一种交互方式：supervisor 发 turn，worker 写 outbox。没有"把 worker 当工具调用"的模式。

**可吸收**：
- 对于轻量级、同步的子任务（如代码搜索、lint 检查），可以用 tool 模式而非完整 turn 模式
- 减少 tmux session 开销
- 父 worker 直接拿到结果，不需要走完整 outbox pipeline

**落点**：`WorkerContract` 增加 `execution_mode: turn | tool`，tool 模式走同步调用。

#### 4.2.2 事件驱动编排（参考 AutoGen / CrewAI Flows）

**现状**：本项目的 supervisor loop 是同步轮询模式。每轮：send -> poll marker -> observe -> next round。

**可吸收**：
- AutoGen 的事件驱动 runtime：消息到达时触发 handler，而不是轮询
- CrewAI Flows 的 @listen 模式：上游完成后自动触发下游

**落点**：
```python
class EventDrivenSupervisor:
    @on_event("worker.outbox.detected")
    async def on_outbox(self, event: AgentEvent): ...

    @on_event("review.completed")
    async def on_review(self, event: AgentEvent): ...

    @on_event("verification.failed")
    async def on_verify_fail(self, event: AgentEvent): ...
```

这可以把当前的同步 loop 改成事件驱动，降低延迟，支持多 worker 并行。

#### 4.2.3 条件路由图（参考 LangGraph）

**现状**：本项目的 decision policy 是规则列表，按优先级顺序匹配。流程控制是硬编码在 supervisor loop 里的。

**可吸收**：
- LangGraph 的条件边：每个节点的输出决定下一步走哪条路
- 显式图定义：把 supervisor loop 的流程声明为图，而不是代码中的 if/else

**落点**：
```python
crew_graph = StateGraph(CrewState)
crew_graph.add_node("send_turn", send_turn)
crew_graph.add_node("observe", observe_worker)
crew_graph.add_node("record_changes", record_changes)
crew_graph.add_node("review", run_review)
crew_graph.add_node("verify", run_verification)
crew_graph.add_conditional_edges("verify", route_on_verify_result, {
    "pass": "ready",
    "fail": "challenge",
    "fail_escalate": "spawn_analyst",
})
```

这会让流程更可测试、可可视化、可修改。

#### 4.2.4 Sandbox 隔离执行（参考 OpenAI SDK）

**现状**：本项目的 worker 在 git worktree 中执行，文件系统没有真正隔离。worker 理论上可以读写 worktree 外的文件。

**可吸收**：
- OpenAI SDK 的 Sandbox Agent：Manifest 声明 + Capabilities 控制 + 后端隔离
- 分层隔离：worktree（已有）+ capability 声明（已有）+ 运行时限制（缺失）

**落点**：长期方向，当前 worktree 隔离已足够 MVP。未来可考虑 Docker sandbox 后端。

#### 4.2.5 Agent 注册与工厂模式（参考 AutoGen）

**现状**：本项目的 worker contract 和 agent profile 在 WorkerPool 中硬编码组装。

**可吸收**：
- AutoGen 的 agent type -> factory 注册
- contract 类型名映射到 profile 渲染工厂
- 支持运行时注册新的 contract 类型

**落点**：`AgentPackRegistry` 已经接近这个模式，可以进一步抽象为 factory registry。

### 4.3 低优先级

#### 4.3.1 分布式运行时（参考 AutoGen）

**现状**：所有 worker 在本地 tmux 中运行。

**可吸收**：长期可支持远程 worker，通过消息协议通信。当前不需要。

#### 4.3.2 Memory 系统（参考 CrewAI）

**现状**：本项目有 governed learning（learning note、guardrail、worker quality），但没有通用记忆系统。

**可吸收**：
- CrewAI 的三层记忆（short-term、long-term、entity）
- Flow 层的 remember/recall 跨运行持久化

**落点**：当前的 governed learning 已经覆盖了"从失败中学习"的场景。通用记忆可作为后续增强。

#### 4.3.3 Voice/Realtime Agent（参考 OpenAI SDK）

不相关，跳过。

---

## 5. 本项目独特优势（其他框架没有的）

在吸收外部优点的同时，需要认识到本项目已有的独特优势：

| 优势 | 说明 |
| --- | --- |
| 外部控制平面 | Codex 不是 agent 之一，而是监督者。这比所有框架的内部协调都更安全。 |
| Worktree 隔离 | 源码修改在独立 worktree 中，主工作区不受影响。只有 OpenAI SDK 的 Sandbox 能比。 |
| 结构化 outbox | worker 输出必须是结构化 JSON，不是自由文本。比所有框架的自由输出都更可靠。 |
| Merge transaction | 先在 integration worktree 验证，再应用到主工作区。其他框架没有这个安全层。 |
| Governed learning | 失败 -> challenge -> learning note -> guardrail candidate。有治理边界的自动化学习。 |
| Event sourcing | append-only event store，完整审计轨迹。比 snapshot-based 方案更可追溯。 |
| Verification runner | 在 worker workspace 中执行验证命令，结果落盘。其他框架的验证都是 prompt 级别的。 |

---

## 6. 建议的优化路线

### Phase 1：加固现有基础（1-2 周）

| 优化项 | 参考来源 | 工作量 |
| --- | --- | --- |
| Output guardrail：outbox 解析后验证 acceptance_criteria | OpenAI SDK guardrails | 小 |
| Input guardrail：turn 发送前验证 scope | OpenAI SDK guardrails | 小 |
| CrewCheckpoint：event sequence 快照 + 恢复 | LangGraph / CrewAI | 中 |
| Review verdict 严格解析（BLOCK/WARN/OK） | 本项目已有缺口 | 小 |

### Phase 2：提升编排能力（2-4 周）

| 优化项 | 参考来源 | 工作量 |
| --- | --- | --- |
| 事件驱动 supervisor（替换同步 poll） | AutoGen 事件驱动 | 中 |
| 条件路由图（替换硬编码 if/else） | LangGraph StateGraph | 大 |
| input_filter 历史控制 | OpenAI SDK handoffs | 中 |
| Agent as Tool 轻量调用模式 | OpenAI SDK dual delegation | 中 |

### Phase 3：增强状态与持久化（4+ 周）

| 优化项 | 参考来源 | 工作量 |
| --- | --- | --- |
| 类型化 state schema + reducer | LangGraph state | 大 |
| State schema 版本管理 | LangGraph migration | 中 |
| Fork/resume from checkpoint | CrewAI Flow persist | 中 |
| 通用记忆系统 | CrewAI memory | 大 |

---

## 7. 不应吸收的部分

| 设计 | 框架 | 不吸收原因 |
| --- | --- | --- |
| Agent 自主 handoff | OpenAI SDK | 本项目要求 Codex 控制，不允许 worker 自己决定委托 |
| Manager agent 协调 | CrewAI hierarchical | Codex 已是 manager，不需要再加一层 manager agent |
| LLM 驱动的路由决策 | OpenAI SDK / CrewAI | 本项目用 rule-based policy，可测试可解释，不应换成 LLM 决策 |
| 分布式 runtime | AutoGen | 当前本地优先，分布式是远期目标 |
| 完全自治 agent swarm | 所有框架 | 本项目定位是可控半自动 crew，不是自治 swarm |

---

## 8. 一句话总结

本项目的 Codex-mediated 控制平面 + worktree 隔离 + 结构化 outbox + merge transaction + governed learning 组合，在安全性和可审计性上超过所有调研框架。主要差距在：guardrails 体系化、检查点恢复、事件驱动编排、状态类型化。建议按 Phase 1/2/3 逐步吸收。
