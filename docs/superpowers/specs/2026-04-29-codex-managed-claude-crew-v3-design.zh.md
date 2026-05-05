# Codex 管理的 Claude Crew V3 设计

- 日期：2026-04-29
- 状态：已在对话中确认，可以进入实现计划阶段
- 版本：V3
- 范围：在现有 V2 对抗式会话之上，新增一个由 Codex 管理、以原生 Claude Code CLI 终端会话为 worker 基座的多 Claude worker 编排层

## 0. 版本边界

这个项目现在可以分成三层能力。

V1 是一次性任务分发：

- Codex 打包一个任务。
- Claude Code 通过 CLI adapter 执行。
- orchestrator 记录输出、改动文件和验证结果。
- supervisor 决定接受、重试或汇报结果。

V2 是单 worker 的对抗式监督：

- Codex 维护一个长期 Claude bridge 会话。
- Codex 可以继续发送指令、验证、挑战、接受或升级给人处理。
- session engine 记录多轮需求、修复、验证和决策。
- 对一个需求来说，worker 仍然主要是一个 Claude 会话。

V3 是多 worker 的 crew 编排：

- Codex 可以为一个用户目标动态创建和协调多个 Claude worker。
- worker 可以有不同角色、工作区、权限和任务范围。
- 共享 blackboard 保存事实、主张、补丁、风险、验证证据和决策。
- Codex 仍然是最终验收和合并的唯一裁决者。

V3 不应该替换 V2，但 V3 的主 worker 形态需要从 `claude --print` 风格的 bridge，切换为可观察、可 attach、可持续交互的原生 Claude Code CLI 终端会话。`ClaudeBridge` 仍然保留为结构化批处理和兼容后备，但不是 V3 编码协作的主路径。

```text
V3 CrewController
  使用 WorkerPool
    管理 N 个 NativeClaudeSession
      每个 session 是 tmux/PTY 中的原生 `claude` CLI
      复用 TmuxConsole / WorktreeManager / WorkspaceManager fallback / Recorder / VerificationRunner / PolicyGate
```

### 0.1 V3 MVP 边界

V3 的第一版应该先实现一个更小、更稳的 MVP：

- Codex 可以启动多个可持续 NativeClaude worker。
- 每个 worker 有角色、terminal session/pane、workspace、状态和任务归属。
- Codex 可以对指定 worker 执行 `send`、`observe`、`tail`、`attach`、`status`。
- worker 输出和 Codex 决策进入 crew blackboard。
- implementer 默认使用 `git worktree` 独立分支，不直接污染主工作区。
- `crew start` 在创建写入 worker 前检查 repo dirty 状态；默认要求 clean base。
- Codex 可以运行 crew 级验证，并把验证结果写入 blackboard。
- Codex 可以查看 implementer worktree 的 branch diff，生成轻量 merge plan。

V3 MVP 不实现 competitor 模式、独立 verifier worker、GUI UI 改造、自动合并和动态 worker add/stop。这些保留为 V3 后续阶段。MVP 可以实现用户显式启动的本地 supervisor loop，但不做无人值守长期 daemon。copy-based workspace 只作为 non-git repo fallback 或 fake 测试路径，不作为默认实现。

## 1. 问题陈述

当前 V2 已经比普通 subagent 调用更有价值，因为 Codex 可以持续监督一个 Claude 会话。但它本质上还是单 worker 形态。对于更大的编码任务，一个 Claude 会话往往会把架构阅读、实现、评审、测试和风险分析混在同一个上下文里，这会带来几个问题：

- 探索和实现会争抢上下文空间。
- 评审不独立，因为评审者和写 patch 的 worker 是同一个会话。
- 测试和证据不容易从主张里自然分离出来。
- 可并行的工作仍然被串行执行。
- Codex 没有一个一等对象来分配多个 worker 并比较它们的输出。
- `claude --print` bridge 不够原生。用户和 Codex 都看不到 Claude Code 正在如何读文件、调用工具、等待权限或推进任务，体验更像一次性大模型调用，而不是 Codex 监督 Claude Code 写代码。

V3 应该把项目推进成一个 Codex-first 的本地 crew 系统。Codex 应该能够判断一个任务需要一个 worker、三个 worker，还是不需要 worker，然后用受控的角色和权限分配 Claude Code 会话。

目标不是重建 Claude Code 内部的 subagent 或 team runtime。Claude Code 已经有很强的 agent、teammate、mailbox 和 worktree 设计。这个项目应该站在这些机制外面一层，提供跨 worker 的监督、持久证据、策略、验证和合并仲裁。

## 2. 选定方向

构建一个保守的 “Codex-managed Claude Crew” 层：

```text
User Goal
-> Codex Supervisor
-> CrewController
-> TaskGraph
-> WorkerPool
-> NativeClaudeWorker[]
-> BlackboardStore
-> VerificationRunner
-> MergeArbiter
-> Final Codex decision
```

Codex 拥有规划、分派、挑战、验证和最终验收权。Claude worker 拥有受边界约束的执行权。

第一版 V3 应该优先采用受控的星型拓扑：

```text
           Claude Explorer
                 |
Claude Reviewer -> Blackboard <- Claude Implementer
                 ^
           Claude Verifier
                 |
              Codex
```

worker 不自由修改彼此任务，也不形成不可控的 mesh。它们通过 blackboard 和 Codex 发出的消息沟通。这样系统更容易理解、回放和调试。

## 3. 目标

- 让 Codex 能够为一个用户目标动态分配多个 Claude worker。
- 支持 explorer、implementer、reviewer、verifier 和可选 competitor 等角色化 worker。
- 复用现有 tmux terminal runner、verification runner、policy gate 和 recorder，并新增 worktree manager 管理写入 worker。
- 使用原生 Claude Code CLI 作为 V3 worker 的默认执行面，让 Codex 和用户都能观察 worker 当前行为。
- 持久化 crew 级别记录，说明谁做了什么、为什么做、证据是什么。
- 让最终验收和合并决策集中在 Codex。
- 在任务范围独立时，允许并行只读探索和评审。
- 通过 worktree 或显式写入范围隔离可写 worker。
- 用共享 blackboard schema 让 worker 输出可比较。
- 为未来支持非 Claude worker 保留路径，同时不改变 crew 模型。

## 4. 非目标

- 不做不受控的 peer-to-peer agent swarm。
- 不自动合并到用户主工作区。
- 不把不稳定的 Claude Code 内部源码当作运行时 API 依赖。
- 不试图重写 Claude Code 完整的 agent team 系统。
- 不做托管 SaaS 控制面。
- 初始 V3 不做模型微调，也不做无人值守长期运行的后台 daemon；只做用户显式启动、可 attach、可停止的本地 supervisor loop。
- 不要求每个任务都使用多个 worker；小任务可以由 Codex 选择继续走单个 native Claude session 或 V2 bridge。

## 5. 与 Claude Code 源码和开源方案的关系

本地 Claude Code 源码快照里有几个值得借鉴的模式：

- agent 定义可以包含角色、工具、模型、权限模式、记忆、hooks 和隔离方式。
- 支持后台和异步 worker。
- 有 team membership 文件。
- 有带 owner、status、dependencies 和 blocking relationship 的任务列表。
- 有基于文件的 teammate mailbox。
- 写入 agent 可以通过 worktree 隔离。
- 父级可以控制权限和 session 元数据。

V3 应该借鉴这些概念，但不要耦合 Claude Code 内部实现。稳定集成面应该是原生 Claude CLI 的终端交互，而不是内部源码 API：

- 在 tmux/PTY 中运行原生 `claude`
- 用 `tmux send-keys` 或 PTY stdin 发送 Codex 指令
- 用 `tmux capture-pane` 或 PTY stdout 观察 Claude Code 当前屏幕
- 用 transcript artifact 保存可回放记录
- 用 Codex 注入的 turn marker 判断一轮是否结束，例如 `<<<CODEX_TURN_DONE>>>`
- prompt 层面的角色、任务、权限和完成标记约定

`claude --print`、`--output-format json` 和现有 ClaudeBridge 继续可用，但它们是批处理/兼容路径，不是 V3 MVP 的主要 worker runtime。

MCO、Composio Agent Orchestrator、Agent Swarm、Orca 和 Claude Code agent teams 这类方案说明，多 agent 编码系统最终会收敛到几个共同原语：worker 角色、隔离工作区、共享状态、任务图、评审循环和合并控制。V3 的差异点应该是 Codex-first 监督，以及把本地证据和验证作为一等记录。

## 6. 核心概念

### 6.1 Crew

crew 是一次为了满足用户目标而启动的多 worker 尝试。它包含：

- 根目标
- Codex 编写的计划
- 任务图
- 一个或多个 worker
- blackboard
- 验证运行
- 合并决策
- 最终结果

Crew 状态：

- `planning`
- `running`
- `blocked`
- `needs_human`
- `accepted`
- `failed`
- `cancelled`

### 6.2 Worker

worker 是一个被管理的原生 Claude Code CLI 终端会话，带有角色化指令、workspace、权限和可观察 transcript。Codex 通过终端通道向 worker 输入任务，通过捕获 pane 输出观察进展，并通过 turn marker 判断一轮完成。

初始 worker 角色：

- `explorer`：只读分析代码库、架构和风险。
- `implementer`：MVP 中在独立 `git worktree` 和 worker branch 里写代码。
- `reviewer`：只读评审 proposed diff 和 worker 主张。
- `verifier`：后续阶段的独立验证 worker；MVP 先由 Codex 运行 crew 级验证。
- `competitor`：后续阶段的备选实现者，用于高风险任务。

每个 worker 包含：

- `worker_id`
- `role`
- `native_session_id`
- `terminal_session`
- `terminal_pane`
- `transcript_artifact`
- `turn_marker`
- `bridge_id`（可选，仅用于兼容 ClaudeBridge 后备）
- `workspace_mode`
- `write_scope`
- `allowed_tools`
- `status`
- `current_task_id`
- `budget`
- `created_at`
- `updated_at`

### 6.2.1 Native Worker 交互协议

Codex 给每个 native worker 的初始 prompt 必须包含：

- worker 角色和任务范围。
- workspace 路径。
- 允许和禁止的写入范围。
- 汇报要求：改了什么、证据是什么、风险是什么。
- 完成标记：每轮结束时输出 `<<<CODEX_TURN_DONE status=ready_for_codex>>>`。

Codex 不通过猜测进程空闲来判断任务完成。原生 `claude` 进程会长期存在，完成一轮的信号来自 marker 和 transcript。人类可以随时 attach 到 worker 的 tmux session 查看 Claude Code 当前状态。

### 6.3 Task Graph

task graph 是 crew 的操作计划。它避免把多 agent 工作变成一堆 prompt。

每个 task 包含：

- `task_id`
- `title`
- `instructions`
- `owner_worker_id`
- `role_required`
- `status`
- `blocked_by`
- `depends_on`
- `allowed_paths`
- `forbidden_paths`
- `expected_outputs`
- `acceptance_criteria`
- `evidence_refs`

Task 状态：

- `pending`
- `assigned`
- `running`
- `submitted`
- `challenged`
- `accepted`
- `rejected`
- `blocked`

### 6.4 Blackboard

blackboard 是共享证据层。为了可审计，它应该是 append-only；派生摘要可以建立在它之上。

Entry 类型：

- `fact`：关于代码库或任务的有依据观察。
- `claim`：worker 提出的、需要证据支撑的主张。
- `question`：尚未解决的问题。
- `risk`：潜在失败模式或冲突。
- `patch`：建议的代码改动或 diff 引用。
- `verification`：命令、结果、日志或 artifact。
- `review`：对 claim 或 patch 的批评。
- `decision`：Codex 的接受、拒绝、合并或升级决策。

每个 blackboard entry 应该包含：

- `entry_id`
- `crew_id`
- `task_id`
- `worker_id` 或 `codex`
- `type`
- `content`
- `evidence_refs`
- `confidence`
- `created_at`

blackboard 存放位置：

```text
.orchestrator/crews/<crew_id>/blackboard.jsonl
```

### 6.5 Merge Arbiter

merge arbiter 用来保护主工作区，避免 worker 改动互相冲突。

规则：

- Codex 是唯一可以批准最终合并的 actor。
- 可写 worker 默认使用隔离 worktree。
- 多个可写 worker 不能拥有重叠写入范围，除非任务被显式标记为竞争实现。
- reviewer 和 verifier 默认只读。
- 每个合并提案都必须有证据：diff 摘要、改动文件、验证结果和 reviewer 回复。

## 7. 数据模型

新增 crew 级别记录，同时不破坏现有 V1/V2 记录。

建议新增文件：

```text
src/codex_claude_orchestrator/crew_models.py
src/codex_claude_orchestrator/crew_controller.py
src/codex_claude_orchestrator/worker_pool.py
src/codex_claude_orchestrator/blackboard.py
src/codex_claude_orchestrator/task_graph.py
src/codex_claude_orchestrator/merge_arbiter.py
```

具体模块拆分可以在实现阶段调整，但职责边界应该保持分离。

### 7.1 CrewRecord

字段：

- `crew_id`
- `root_goal`
- `repo`
- `status`
- `planner_summary`
- `max_workers`
- `active_worker_ids`
- `task_graph_path`
- `blackboard_path`
- `verification_summary`
- `merge_summary`
- `created_at`
- `updated_at`
- `ended_at`
- `final_summary`

### 7.2 WorkerRecord

字段：

- `worker_id`
- `crew_id`
- `role`
- `agent_profile`
- `native_session_id`
- `terminal_session`
- `terminal_pane`
- `transcript_artifact`
- `turn_marker`
- `bridge_id`（兼容字段，可为空）
- `workspace_mode`
- `workspace_path`
- `write_scope`
- `allowed_tools`
- `status`
- `assigned_task_ids`
- `last_seen_at`
- `created_at`
- `updated_at`

### 7.3 TaskRecord 扩展

现有 task record 形状可以继续服务 V1/V2。V3 可以新增 `CrewTaskRecord`，也可以给 task metadata 加 crew 可选字段。

Crew task 字段：

- `crew_id`
- `owner_worker_id`
- `depends_on`
- `blocked_by`
- `allowed_paths`
- `forbidden_paths`
- `expected_outputs`
- `evidence_refs`

### 7.4 BlackboardEntry

字段：

- `entry_id`
- `crew_id`
- `task_id`
- `actor_type`：`codex` 或 `worker`
- `actor_id`
- `type`
- `content`
- `evidence_refs`
- `confidence`
- `created_at`

## 8. 控制流程

### 8.1 默认 Crew 流程

```text
crew start
-> Codex creates CrewRecord
-> Codex creates initial TaskGraph
-> CrewController allocates workers
-> WorkerPool starts native Claude Code CLI sessions
-> Explorer reads repo in native CLI and posts facts/risks
-> Codex refines task graph
-> Implementer works in an isolated git worktree through native CLI
-> Implementer posts patch and evidence
-> Reviewer reviews patch and claims
-> Verifier runs or proposes checks
-> Codex challenges missing evidence
-> Implementer repairs if needed
-> VerificationRunner performs final checks
-> MergeArbiter produces merge recommendation
-> Codex accepts, rejects, or asks human
```

中文语义：

```text
crew start
-> Codex 创建 CrewRecord
-> Codex 创建初始 TaskGraph
-> CrewController 分配 workers
-> WorkerPool 启动原生 Claude Code CLI 终端会话
-> Explorer 在原生 CLI 中阅读代码库并提交 facts/risks
-> Codex 细化 task graph
-> Implementer 在隔离 git worktree 的原生 CLI 中实现
-> Implementer 提交 patch 和证据
-> Reviewer 评审 patch 和主张
-> Verifier 运行或建议检查
-> Codex 挑战缺失证据
-> Implementer 必要时修复
-> VerificationRunner 执行最终检查
-> MergeArbiter 生成合并建议
-> Codex 接受、拒绝或询问人类
```

### 8.2 小任务快速路径

对于小目标，Codex 可以选择不创建 crew：

```text
User Goal
-> NativeClaudeSession 或 V2 ClaudeBridgeSession
-> verify/challenge/accept
```

这样可以避免 V3 给简单任务增加仪式感。

### 8.3 竞争实现流程

对于高风险或含糊的改动，后续阶段 Codex 可以创建两个 implementer：

```text
Explorer -> shared facts
Implementer A -> patch A
Implementer B -> patch B
Reviewer -> compare A/B
Verifier -> run checks
Codex -> choose, merge, or reject both
```

这个模式不进入 V3 MVP。实现时必须使用隔离 worktree，并且最终合并决策不能互相重叠。

## 9. 工作区和权限模型

Workspace mode：

- `readonly`：worker 只能查看。
- `shared`：worker 可以在源 repo 中操作，只用于可信的低风险流程。
- `isolated`：copy-based fallback，只用于 non-git repo 或测试 fake。
- `worktree`：默认写入模式。worker 在隔离 git worktree 和独立 branch 中写入。

默认角色权限：

| Role | Workspace | Write Access | Purpose |
| --- | --- | --- | --- |
| explorer | readonly | no | 理解代码库和风险 |
| implementer | worktree | yes, scoped | 产出 patch |
| reviewer | readonly | no | 评审主张和 diff |
| verifier | deferred | no by default | 运行检查并收集证据 |
| competitor | deferred | yes, scoped | 产出备选 patch |

Worktree 规则：

- 每个可写 worker 创建一个独立 branch，例如 `codex/<crew_id>-<worker_id>`。
- 每个 worktree 放在 `.orchestrator/worktrees/<crew_id>/<worker_id>` 或 state root 下等价路径。
- 写入 worker 启动前默认要求源 repo clean，避免用户未提交改动被隐式遗漏。
- 如果 repo dirty，默认拒绝创建 implementer，并把 dirty paths 写入 blackboard risk。
- 只有显式 `--allow-dirty-base` 才允许把当前 dirty diff 保存为 base patch artifact，并尝试应用到 worker worktree；应用失败则阻塞 worker。
- merge plan 基于 worker branch diff 和 changed files，不直接应用 patch。

策略规则：

- 受保护路径继续受保护。
- 危险命令继续被阻止。
- worker 启动前必须声明写入范围。
- Codex 只有通过显式 crew decision record 才能授予更大范围。
- worker 必须保留无关的用户改动。

## 10. CLI 和 UX

初始命令：

```bash
orchestrator crew start --repo /path/to/repo --goal "..." --workers explorer,implementer,reviewer [--allow-dirty-base]
orchestrator crew status --repo /path/to/repo --crew <crew_id>
orchestrator crew blackboard --repo /path/to/repo --crew <crew_id>
orchestrator crew worker status --repo /path/to/repo --crew <crew_id> --worker <worker_id>
orchestrator crew worker observe --repo /path/to/repo --crew <crew_id> --worker <worker_id>
orchestrator crew worker attach --repo /path/to/repo --crew <crew_id> --worker <worker_id>
orchestrator crew worker tail --repo /path/to/repo --crew <crew_id> --worker <worker_id> --limit 20
orchestrator crew worker send --repo /path/to/repo --crew <crew_id> --worker <worker_id> --message "..."
orchestrator crew supervise --repo /path/to/repo --crew <crew_id> --verification-command "pytest -q"
orchestrator crew verify --repo /path/to/repo --crew <crew_id>
orchestrator crew challenge --repo /path/to/repo --crew <crew_id> --task <task_id>
orchestrator crew changes --repo /path/to/repo --crew <crew_id> --worker <worker_id>
orchestrator crew merge-plan --repo /path/to/repo --crew <crew_id>
orchestrator crew accept --repo /path/to/repo --crew <crew_id>
```

后续可加命令：

```bash
orchestrator crew worker add --crew <crew_id> --role reviewer
orchestrator crew worker stop --crew <crew_id> --worker <worker_id>
```

UI 应该展示：

- crew 摘要
- task graph
- worker 状态
- blackboard entries
- 按 worker 划分的改动文件
- 验证证据
- Codex 决策

## 11. 错误处理

Worker 失败：

- 将 worker 标记为 `failed`。
- 保留 terminal transcript、pane snapshot 和最后一次 Codex 指令。
- 向 blackboard 写入 failure entry。
- 让 Codex 决定重试、替换或升级。

验证失败：

- 记录命令、返回码、stdout、stderr 和 artifacts。
- 写入 `verification` 和 `risk` entry。
- 如果可修复，挑战负责的 worker。

冲突：

- 将受影响 task 标记为 `blocked`。
- 让 reviewer 或 Codex 比较 diffs。
- 应用任何 patch 前，必须经过 merge arbiter 决策。

Native Claude session 丢失：

- 通过已保存 tmux session/pane 和 transcript 判断是否可恢复。
- 如果原生 CLI 已退出或 resume 失败，用已有 blackboard summary 和 transcript 创建 replacement worker。
- 保留旧 worker 作为 failed evidence。

Worktree 创建失败：

- 如果不是 git repo，降级为 `isolated` fallback，且在 blackboard 记录 fallback reason。
- 如果 repo dirty 且没有 `--allow-dirty-base`，阻止写入 worker 启动并要求用户 commit/stash 或显式允许 dirty base。
- 如果 branch 名冲突，创建带短随机后缀的新 branch，并在 allocation artifact 记录原始期望 branch。
- 如果 base patch 应用失败，worker 进入 `blocked`，等待 Codex 或人类决策。

策略违规：

- 停止 worker 当前 task。
- 记录被阻止的操作。
- 继续前必须经过 Codex 或人类决策。

## 12. 测试策略

单元测试：

- task graph 依赖和状态转移。
- blackboard append/read/filter 行为。
- worktree 分配、dirty base 阻塞和 branch 命名。
- worker pool 分配和生命周期状态。
- merge arbiter 冲突检测。
- CLI 参数解析。

集成测试：

- fake NativeClaudeSession 跑通 explorer/implementer/reviewer 流程。
- implementer 在独立 worktree 写入，主 workspace 不被修改。
- 验证失败会触发 challenge。
- reviewer 拒绝会阻止 acceptance。
- worker resume 或 replacement 保留 terminal transcript 和 blackboard context。

回归测试：

- V1 dispatch 继续可用。
- V2 bridge commands 继续可用。
- 受保护路径和危险命令继续被阻止。

手工验证：

- 在 toy repo 上跑一个小 crew。
- 检查 `.orchestrator/crews/<crew_id>/`。
- 确认 transcripts、blackboard、task graph 和 verification logs 可理解。

## 13. 推进计划

### Phase 1：Crew Records 和 Blackboard

新增持久化 crew records、task graph storage 和 blackboard append/read APIs。测试里先使用 fake workers。

### Phase 2：基于 Native Claude CLI 的 WorkerPool

在 tmux/PTY 中创建 managed native Claude Code workers，并为写入 worker 创建独立 git worktree。支持 start、send、observe、attach、tail、status 和 stop 语义。ClaudeBridge 只作为兼容后备。

### Phase 3：默认 Explorer/Implementer/Reviewer 流程

实现 `crew start` 的三角色受控流程。第一版可以先让 verifier 由 Codex-run verification 承担。

### Phase 4：Changed Files 和轻量 Merge Plan

基于 worker branch diff 记录每个 implementer 的 changed files，生成只读 merge plan。MVP 不自动应用 patch。

### Phase 5：UI 和高级调度

在现有 UI 中展示 crew state。新增动态 worker 创建、competitor mode、独立 verifier worker 和更丰富的 task graph updates。

## 14. 成功标准

V3 成功的标准：

- Codex 至少可以启动包含 explorer、implementer 和 reviewer 的 crew。
- 每个 worker 都有独立原生 Claude Code CLI session、terminal transcript 和清晰角色 prompt。
- implementer 的原生 Claude Code CLI 在独立 git worktree 中运行。
- worker 输出被持久化到 crew blackboard。
- implementer 的改动不会自动修改主 workspace。
- Codex 可以利用 reviewer 或 verifier 证据挑战 worker。
- 最终 acceptance 需要验证证据。
- 最终报告解释 task graph、worker 贡献、风险、改动文件和合并建议。
- 现有 V1 和 V2 命令继续通过测试。

## 15. 设计原则

- Codex 是 supervisor，不只是 router。
- Claude workers 是强执行者，但不是最终决策者。
- 证据优先于主张。
- workspace 用来保护用户 repo。
- append-only records 让 agent 工作可回放。
- 多 agent 工作应该是可选的，并且和任务复杂度成比例。
- 优先使用稳定 CLI 边界，而不是依赖不稳定内部源码。
