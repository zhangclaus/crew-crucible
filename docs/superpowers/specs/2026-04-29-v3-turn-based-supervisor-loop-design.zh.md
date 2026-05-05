# V3 Turn-Based Supervisor Loop Design

日期：2026-04-29
状态：用户选择方案 B，进入实现

## 目标

把 V3 从“Codex 启动多个 Claude Code CLI 窗口”推进到“Codex 监督 Claude worker 的轮次循环”。Codex 负责观察 worker 输出、记录证据、运行验证、挑战失败结果，并决定是否验收；Claude worker 仍然通过原生 CLI/tmux 运行，implementer 默认在 git worktree 中写代码。

## 核心流程

```text
crew run
-> start crew
-> wait/observe explorer marker
-> send explorer context to implementer
-> wait/observe implementer marker
-> record implementer changed files and diff artifact
-> send patch evidence to reviewer
-> wait/observe reviewer marker
-> run verification in implementer worktree
-> if passed: return ready_for_codex_accept
-> if failed: challenge implementer and continue until max rounds
```

`crew supervise` 对已有 crew 执行同样循环；`crew run` 是 `start + supervise`。

## 必须先修的不变量

1. `crew start` 必须是事务性的。任一 worker 启动失败时，已启动 worker 要被 stop，crew 标记为 `failed`，避免 planning/running 残留 session。
2. `crew verify` 必须默认验证 implementer worktree，而不是主 repo。CLI 支持 `--worker` 显式指定验证目标。
3. `worker stop` 和 `crew stop` 要同步记录状态，移除 `active_worker_ids`，否则 prune 和 status 会漂移。
4. implementer change evidence 不只包含文件名，还要包含 diff patch artifact，供 reviewer 和 Codex 审查。

## Supervisor Loop 边界

第一版 loop 是 turn-based，不做无人值守 daemon：

- 通过 `tmux capture-pane` 的 marker 判断一轮完成。
- 每轮最多等待有限次数，超时返回 `waiting_for_worker`，不无限挂死。
- verification 失败时通过 `challenge` 写入 blackboard，并向 implementer 发送修复指令。
- verification 通过时返回 `ready_for_codex_accept`，不自动 merge。
- `accept` 仍然只是 Codex 验收 crew，不把 worktree 自动合并回主仓库。

## CLI

```bash
.venv/bin/orchestrator crew run \
  --repo /path/to/repo \
  --goal "..." \
  --verification-command ".venv/bin/python -m pytest -q" \
  --max-rounds 3

.venv/bin/orchestrator crew supervise \
  --repo /path/to/repo \
  --crew <crew-id> \
  --verification-command ".venv/bin/python -m pytest -q" \
  --max-rounds 3

.venv/bin/orchestrator crew verify \
  --repo /path/to/repo \
  --crew <crew-id> \
  --worker <worker-id> \
  --command ".venv/bin/python -m pytest -q"
```

## 测试策略

- TDD 补 start rollback、stop 状态同步、worktree verification、diff artifact。
- 用 fake worker pool 和 fake verification runner 测 `CrewSupervisorLoop` 的 pass/challenge 分支。
- 最后运行完整 pytest，确保 V1/V2 bridge/session 不受影响。
