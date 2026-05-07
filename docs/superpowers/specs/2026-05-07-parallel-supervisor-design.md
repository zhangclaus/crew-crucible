# 并行 Supervisor：多 Worker 同时驱动 + 两层对抗审查

> 将 supervisor 从串行改为 async 并行，支持多个 worker 同时运行。
> 每个 worker 完成后立刻进入单元对抗审查，全部通过后进入集成对抗审查，最后 merge。

## 目标

当前 supervisor 串行驱动 worker（`watch_turn()` 阻塞等待），多个 worker 虽然各自有 tmux session，但 supervisor 一次只给一个发消息。大任务拆分给多个 worker 并行执行，才能真正利用多 worker 的价值。

## 核心决策

| 决策 | 选择 | 原因 |
|------|------|------|
| 并行模型 | asyncio | MCP Server 本来就是 async，改造成本最低 |
| 并行粒度 | 同角色并行 | 多个 implementer 同时实现不同子任务 |
| 完成策略 | 完成即对抗 | worker 完成就立刻进审查，不用等其他 |
| 对抗模式 | 两层对抗 | 第一层快速反馈 + 第二层集成保证 |
| 失败处理 | 子任务独立 | 失败只影响自己，其他 worker 继续 |

## 流程总览

```
Claude Code → crew_run(repo, goal, parallel=True, max_workers=3)
  → MCP Server
    → ParallelSupervisor.supervise()
      ├── 1. 任务拆分：goal → [SubTask1, SubTask2, SubTask3]
      ├── 2. 并行 spawn：3 个 worker（各自 tmux + worktree）
      ├── 3. 并行 watch + 单元对抗：
      │     asyncio.gather(
      │       watch_and_review(worker1) → unit_review pass ✓
      │       watch_and_review(worker2) → unit_review pass ✓
      │       watch_and_review(worker3) → unit_review fail → challenge → repair → pass ✓
      │     )
      ├── 4. 集成对抗：integration_reviewer 看全部改动 + 冲突 + 测试
      └── 5. Merge：全部通过 → merge 所有 worktree
```

---

## 组件设计

### 1. SubTask 数据模型

```python
@dataclass
class SubTask:
    task_id: str
    description: str          # "实现 JWT 认证中间件"
    scope: list[str]          # ["src/auth/", "tests/auth/"]
    depends_on: list[str]     # 依赖哪些子任务（默认空）
    worker_id: str = ""       # 分配后填入
    status: str = "pending"   # pending / running / unit_review / passed / failed
    result: dict | None = None
    review_attempts: int = 0
```

### 2. 任务拆分

Supervisor 调 LLM 将 goal 拆成 N 个子任务。拆分规则：
- 每个子任务有明确的 scope（文件/目录范围）
- 子任务之间尽量无依赖（可并行）
- 如果有依赖，被依赖的先跑

拆分 prompt 示例：
```
将以下任务拆分成可并行执行的子任务：
Goal: {goal}
Repo structure: {tree}

输出 JSON 数组，每个子任务包含 description 和 scope。
```

### 3. Async 改造

#### tmux_claude.py — watch_turn()

```python
# Before (同步阻塞)
def watch_turn(self, turn, cancel_event=None):
    ...
    time.sleep(delay)
    ...

# After (async 生成器)
async def watch_turn(self, turn, cancel_event=None):
    ...
    await asyncio.sleep(delay)
    ...
```

#### supervisor.py — run_worker_turn()

```python
# Before
def run_worker_turn(self, turn, ...):
    for runtime_event in self._adapter.watch_turn(turn, cancel_event=cancel_event):
        ...

# After
async def run_worker_turn(self, turn, ...):
    async for runtime_event in self._adapter.watch_turn(turn, cancel_event=cancel_event):
        ...
```

#### crew_runner.py — supervise()

```python
# Before (串行)
def supervise(self, ...):
    for round_index in range(max_rounds):
        result = self._run_round(...)  # 阻塞等一个 worker
        ...

# After (并行)
async def supervise(self, ...):
    subtasks = self._split_task(goal)
    for round_index in range(max_rounds):
        result = await self._run_parallel_round(subtasks, ...)
        ...
```

### 4. 并行编排核心

```python
async def _run_parallel_round(self, subtasks: list[SubTask], ...):
    # 1. 并行 spawn + watch + 单元审查
    tasks = [self._watch_and_review(st) for st in subtasks if st.status == "pending"]
    results = await asyncio.gather(*tasks, return_exceptions=True)

    # 2. 检查第一层审查结果
    failed = [r for r in results if isinstance(r, dict) and r.get("unit_review") != "pass"]
    if failed:
        # 挑战失败的 worker，重试
        for f in failed:
            await self._challenge_and_repair(f["task_id"])
        # 只重跑失败的，已通过的保留
        return {"status": "unit_review_failed", "failed": failed}

    # 3. 第二层：集成审查
    integration = await self._run_integration_review(subtasks)
    if integration["status"] != "pass":
        return {"status": "integration_failed", ...}

    # 4. merge
    await self._merge_all(subtasks)
    return {"status": "passed", "merged": True}

async def _watch_and_review(self, subtask: SubTask):
    """单个 worker 的完整生命周期：spawn → watch → unit review"""
    subtask.status = "running"
    worker = self._spawn_worker(subtask)
    subtask.worker_id = worker["worker_id"]

    # 异步等 worker 完成
    async for event in self._adapter.watch_turn(worker_turn):
        self._events.append(...)

    # 第一层：单元对抗审查
    subtask.status = "unit_review"
    review = await self._run_unit_review(subtask)
    if review["verdict"] == "pass":
        subtask.status = "passed"
        return {"task_id": subtask.task_id, "unit_review": "pass"}
    else:
        subtask.status = "failed"
        return {"task_id": subtask.task_id, "unit_review": "fail", "reason": review["reason"]}
```

### 5. 两层对抗审查

#### 第一层：单元审查（每个 worker 独立）

```python
async def _run_unit_review(self, subtask: SubTask):
    """审查单个 worker 的改动"""
    changes = self._controller.changes(crew_id=crew_id, worker_id=subtask.worker_id)

    # 只审查该 worker scope 内的改动
    reviewer = self._spawn_reviewer(
        scope=subtask.scope,
        message=f"审查 worker {subtask.worker_id} 的改动：\n{changes}",
    )

    async for event in self._adapter.watch_turn(reviewer_turn):
        ...

    verdict = self._parse_review_verdict(reviewer_turn)
    return verdict  # {"verdict": "pass"/"block", "reason": "..."}
```

#### 第二层：集成审查（全部通过后）

```python
async def _run_integration_review(self, subtasks: list[SubTask]):
    """审查所有 worker 改动的整体一致性"""
    all_changes = []
    for st in subtasks:
        changes = self._controller.changes(crew_id=crew_id, worker_id=st.worker_id)
        all_changes.append(changes)

    # 检查冲突
    conflicts = self._detect_conflicts(all_changes)
    if conflicts:
        return {"status": "conflict", "conflicts": conflicts}

    # 跑全量测试
    verify = self._controller.verify(crew_id=crew_id, command="pytest")
    if not verify.get("passed"):
        return {"status": "test_failed", "result": verify}

    # 集成 reviewer 审查整体
    reviewer = self._spawn_reviewer(
        message=f"审查整体改动的一致性和质量：\n{all_changes}",
    )
    ...
    return {"status": "pass"}
```

### 6. MCP 工具适配

#### crew_run 参数扩展

```python
crew_run(
    repo="/path",
    goal="实现完整的用户系统",
    max_workers=3,        # 最多并行几个 worker
    parallel=True,        # 是否启用并行模式（默认 False 向后兼容）
    verification=["pytest"],
)
```

#### crew_status Delta 展示

```json
{
  "job_id": "job-abc",
  "phase": "parallel_running",
  "subtasks": [
    {"task_id": "st-1", "description": "实现认证", "status": "unit_review", "worker_id": "w1"},
    {"task_id": "st-2", "description": "实现用户管理", "status": "running", "worker_id": "w2"},
    {"task_id": "st-3", "description": "实现权限", "status": "passed", "worker_id": "w3"}
  ],
  "elapsed": 45,
  "poll_after_seconds": 10
}
```

Delta 模式：只有某个 subtask 的 status 变化时才推有意义的信息。

### 7. 向后兼容

- `parallel=False`（默认）时走原来的串行路径，完全不变
- CLI 模式用 `asyncio.run(supervisor.supervise())` 同步调用
- 现有 MCP 工具接口不变，只扩展参数

---

## 失败场景

| 场景 | 处理 |
|------|------|
| 第一层单元审查失败 | challenge 该 worker 修复，重试审查。其他 worker 不受影响 |
| 第二层集成审查失败（冲突） | 挑战冲突的 worker 修复，只重跑冲突的 |
| 第二层集成审查失败（测试） | 挑战相关 worker 修复，重跑测试 |
| worker 超时/崩溃 | 标记子任务 failed，其他继续。最终汇报部分完成 |
| 全部 worker 失败 | 返回所有失败原因，不 merge |

---

## 文件变更清单

### 改造
| 文件 | 变更 |
|------|------|
| `v4/adapters/tmux_claude.py` | `watch_turn()` → async 生成器 |
| `v4/supervisor.py` | `run_worker_turn()` → async |
| `v4/crew_runner.py` | `supervise()` → async，新增并行编排逻辑 |
| `mcp_server/tools/crew_run.py` | 适配 async supervise，新增 parallel 参数 |
| `mcp_server/job_manager.py` | 适配 async runner |

### 新增
| 文件 | 用途 |
|------|------|
| `v4/parallel_supervisor.py` | 并行编排核心（任务拆分 + 并行 watch + 两层审查） |
| `v4/subtask.py` | SubTask 数据模型 |
| `tests/v4/test_parallel_supervisor.py` | 并行 supervisor 测试 |

### 不变
- `crew/controller.py` — 原样使用
- `workers/pool.py` — 原样使用
- `v4/event_store.py` — 原样使用
- 现有串行路径 — `parallel=False` 时完全不变

---

## 验证

1. `parallel=False` 时行为与现在完全一致
2. `parallel=True, max_workers=3` 时 3 个 worker 同时运行
3. 任意 worker 完成后立刻进单元审查
4. 全部通过后集成审查正确检测冲突
5. 单个 worker 失败不影响其他 worker
6. `crew_status` 正确展示多个 subtask 的进度
7. 所有现有测试不受影响
