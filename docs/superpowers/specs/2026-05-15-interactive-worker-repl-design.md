# 交互式 REPL + 文件通信 + 历史索引

**日期**: 2026-05-15
**状态**: 已批准

## 问题

当前 worker 使用 `claude -p`（print 模式）在隐藏的 tmux session 中运行。问题：

1. 用户看不到 worker 在干什么（无头运行）
2. 通信依赖 tmux pane 输出轮询（`capture-pane`），不稳定
3. 跨轮次上下文要么全量继承（context 溢出），要么完全丢失

## 设计

### 核心思路

- 每轮启动交互式 `claude`（弹出终端窗口，用户可见）
- 文件通信：inbox（任务交付）+ outbox（结果收集）
- 历史按需加载：index.md 做导航，worker 用 grep/read 按需查详情
- 每轮干净启动，不继承上下文

### 架构

```
Orchestrator                        Worker (tmux)
    │                                   │
    ├─ 写 .inbox/mission.md ──────────► │
    ├─ 写 .inbox/task.md ────────────► │
    ├─ 写 .crew-history/index.md ────► │  (worker 按需读取)
    │                                   │
    ├─ tmux: claude --dangerously-skip  │
    │    --system-prompt "协议"         │
    │    "$(cat task.md)"              │
    │                                   │
    │                                   ├─ 读 task.md
    │                                   ├─ 按需读 index.md / 历史文件
    │                                   ├─ 执行任务
    │                                   ├─ 写 .outbox/result.json
    │   watch outbox ◄──────────────────┘
    │
    ├─ 复制 result → history/turn-N.json
    ├─ 更新 index.md
    └─ 清理 outbox，准备下一轮
```

### 目录结构

```
$WORK_DIR/
  .inbox/
    mission.md          ← 背景（不变）
    task.md             ← 当前任务（每轮更新）
  .outbox/
    result.json         ← worker 写入的结果
  .crew-history/
    index.md            ← 总索引（orchestrator 维护）
    turn-1-result.json  ← 历史详细结果
    turn-2-result.json
    ...
```

### Wrapper 脚本 (`claude_worker.sh`)

```bash
#!/bin/bash
WORK_DIR="$1"
INBOX="$WORK_DIR/.inbox"
OUTBOX="$WORK_DIR/.outbox"
mkdir -p "$INBOX" "$OUTBOX"

SYSTEM_PROMPT=$(cat <<'PROMPT'
你是 worker agent。

任务文件：.inbox/task.md
背景文件：.inbox/mission.md
历史索引：.crew-history/index.md（按需读取）
历史详情：.crew-history/turn-N-result.json（需要时 grep/read）

执行流程：
1. 读 task.md
2. 需要上下文时 → 先读 index.md，再按需读具体历史文件
3. 执行任务
4. 写 .outbox/result.json

result.json 必须包含：
crew_id, worker_id, turn_id, status, summary,
changed_files, verification, risks, next_suggested_action
PROMPT
)

exec claude \
  --dangerously-skip-permissions \
  --system-prompt "$SYSTEM_PROMPT" \
  "$(cat "$INBOX/mission.md" 2>/dev/null)

$(cat "$INBOX/task.md" 2>/dev/null)"
```

### Orchestrator 流程

每一轮：

1. 写 `.inbox/mission.md` + `.inbox/task.md`
2. 创建 tmux session → 运行 `claude_worker.sh $WORK_DIR`
3. watch `.outbox/result.json` 出现 → turn 完成
4. 复制 result 到 `.crew-history/turn-N-result.json`
5. 更新 `.crew-history/index.md`（追加本轮摘要）
6. 清理 outbox
7. 下一轮

### result.json Schema

```json
{
  "crew_id": "crew-xxx",
  "worker_id": "worker-xxx",
  "turn_id": "turn-xxx",
  "status": "completed|failed|partial",
  "summary": "一句话描述完成了什么",
  "changed_files": ["file1.py", "file2.py"],
  "verification": "验证方式和结果",
  "risks": ["风险1", "风险2"],
  "next_suggested_action": "建议下一步做什么",
  "acknowledged_message_ids": []
}
```

### index.md 格式

```markdown
# Crew 工作历史

## 当前状态
- 完成轮次：3/5
- 当前阶段：实现核心模块

## 各轮摘要
| 轮次 | 任务 | 结果 | 关键变更 |
|------|------|------|----------|
| 1 | 分析需求 | 完成 | 输出需求文档 |
| 2 | 设计架构 | 完成 | 确定用方案B |
| 3 | 实现模块A | 完成 | 改了3个文件 |

## 关键决策
- 选择方案B（见 turn-2-result.json）
- 数据库用 SQLite（见 turn-2-result.json）

## 需要注意的问题
- turn-3 中发现性能风险，待后续轮次处理
```

### Worker 系统提示设计

Worker 的系统提示告知：
1. 文件协议（inbox/outbox 路径和格式）
2. 历史导航方式（index.md → 按需深入 turn-N 文件）
3. 结果格式要求（result.json schema）

Worker 自行决定：
- 是否需要读历史（简单任务可能不需要）
- 读哪些历史文件（根据 index.md 判断相关性）
- 读多少（按需，不全量加载）

### 变更清单

| 文件 | 变更 |
|------|------|
| `native_claude_session.py` | `start()` 用 wrapper 脚本启动交互式 claude；`send()` 写文件 + tmux 触发；`observe()` watch outbox 文件 |
| `pool.py` | `observe_worker()` 改为基于文件检测；每轮结束后更新 history + index |
| `tmux_claude.py` | `watch_turn()` 优先 watch outbox，移除 tmux pane 轮询 |
| 新增 `claude_worker.sh` | wrapper 脚本 |
| 新增 `history_manager.py` | 管理 history 文件和 index.md 更新 |

### 优势

- **无 context 溢出**：每轮干净启动，按需读历史
- **结构化导航**：index.md 让 worker 快速掌握全貌，不盲找
- **原生 CLI**：弹出真正的 Claude Code 窗口，用户可见
- **文件通信**：不依赖 tmux pane 轮询，outbox 是结构化 JSON
- **可观察**：用户能在终端看到 worker 的完整执行过程

### 风险

- `--dangerously-skip-permissions` 跳过权限检查，安全性降低
- tmux send-keys 的特殊字符转义（通过 wrapper 脚本 + 文件读取缓解）
- worker 不读历史导致上下文丢失（通过系统提示引导缓解）
