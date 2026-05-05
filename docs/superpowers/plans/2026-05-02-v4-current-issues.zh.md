# V4 Event-Native Runtime 当前问题文档

Date: 2026-05-02
Last reviewed: 2026-05-03
Status: latest review after V4 foundation, outbox turn completion, message ack, event-store factory, guarded accept, default V4 run/supervise, EventStore protocol cleanup, repeated-failure governed learning feedback, V4-native merge artifacts, RepoIntelligence / PlannerPolicy, transcript cursor watch, typed review outbox, projection-native status, scope-compatible worker reuse, EventStore health checks, filesystem runtime event stream, accept readiness gate, source worker fallback removal, and transcript cursor initialization fix landed in the current working tree. All reopened issues are now closed.

## 结论

当前 `main` 的 V4 主链路已经比上一版完整很多。上一轮最关键的 P1 问题基本已经关闭：

- worker turn 可以通过 structured outbox 完成，不再依赖 marker。
- tmux `capture-pane` 失败不会阻断已写出的 outbox。
- `TurnEnvelope` 已包含 `required_outbox_path`。
- outbox ack 已能生成 `message.read` 并推进 message cursor。
- `crew events` 已通过 V4 event-store factory 读取事件。
- `crew accept` 已走 V4 merge transaction。
- `crew run` / `crew supervise` 默认已走 V4 runner，`--legacy-loop` 才走旧 V3 loop。

所以当前状态不再是”V4 只有底座，主路径还没接上”。更准确的状态是：

- V4 主路径已经接上。
- P1 级别的事实源、消息 ack、基础 merge transaction 已基本关闭。
- accept readiness gate 已通过 `AcceptReadinessGate` 关闭，merge transaction 必须有 ready evidence 才能继续。
- source worker fallback 已移除，`PlannerPolicy` 是唯一的 worker 选择路径。
- transcript cursor 已在 send 前初始化，即时输出不再漏读。

## 本轮核查证据

代码核查点：

- `src/codex_claude_orchestrator/v4/runtime.py`：`TurnEnvelope.required_outbox_path` 已存在。
- `src/codex_claude_orchestrator/v4/adapters/tmux_claude.py`：`watch_turn()` 已接入 `FilesystemRuntimeEventStream`，并在 `observe()` 异常时发出 `runtime.observe_failed` evidence。
- `src/codex_claude_orchestrator/v4/message_ack.py`：`MessageAckProcessor` 已把 valid outbox ack 转为 `message.read`。
- `src/codex_claude_orchestrator/messaging/message_bus.py`：已有 `advance_cursor_for_read_message_ids()`，只推进已确认读取的连续消息。
- `src/codex_claude_orchestrator/v4/event_store_factory.py`：已有统一 `build_v4_event_store()`。
- `src/codex_claude_orchestrator/cli.py`：`crew events`、`crew run`、`crew supervise`、`crew accept` 已接入 V4 factory / runner / merge transaction。
- `src/codex_claude_orchestrator/v4/merge_inputs.py`：`V4MergeInputRecorder` 已把 worker diff 记录为 V4-native patch/result artifacts。
- `src/codex_claude_orchestrator/v4/merge_transaction.py`：已有 dirty gate、base ref check、integration worktree、verification、final main apply check。
- `src/codex_claude_orchestrator/v4/repo_intelligence.py`：已有 repo scope、package boundary、risk tag、verification command inference。
- `src/codex_claude_orchestrator/v4/planner.py`：已有基于 authority、capability、write scope、worker quality 的 worker selection。
- `src/codex_claude_orchestrator/v4/adapters/tmux_claude.py`：已通过 filesystem runtime stream 接入 transcript tail cursor 和 outbox sha 去重，state 在 runtime evidence 入库后 commit，落盘后可恢复。
- `src/codex_claude_orchestrator/v4/outbox.py`：outbox 已支持 typed `review` payload。
- `src/codex_claude_orchestrator/v4/projections.py`：projection 已有 `to_dict()`，`crew status` 有 V4 events 时优先返回 projection。
- `src/codex_claude_orchestrator/workers/pool.py`：worker reuse 已把 write scope compatibility 作为硬条件。
- `src/codex_claude_orchestrator/v4/event_store.py` / `postgres_event_store.py`：SQLite / PostgreSQL event store 均有 schema/version health check。
- `src/codex_claude_orchestrator/v4/event_stream.py`：已有 filesystem runtime event stream，并已接入 tmux adapter 主路径，支持 outbox sha 去重、transcript offset 落盘、marker evidence 派生，以及 durable append 后再推进 stream state。
- `src/codex_claude_orchestrator/cli.py`：新增 `crew event-store-health`。
- `src/codex_claude_orchestrator/v4/accept_readiness.py`：`AcceptReadinessGate` 读取 V4 事件流，验证 ready round 有 review/verification 且无 blocking challenge。
- `src/codex_claude_orchestrator/v4/merge_transaction.py`：`accept()` 开头调用 readiness gate，只加载 ready round 的 worker patches。
- `src/codex_claude_orchestrator/v4/crew_runner.py`：`_source_worker()` 不再 fallback 到任意 implementer；spawn 时过滤不兼容 workers。
- `src/codex_claude_orchestrator/v4/adapters/tmux_claude.py`：`deliver_turn()` 在 send 前初始化 transcript cursor。

测试结果：

```text
.venv/bin/python -m pytest -q
588 passed
```

## 已关闭的历史问题

| 原问题 | 当前状态 | 说明 |
| --- | --- | --- |
| canonical state root 含糊 | 已关闭 | V4 artifact root 已收敛到 crew artifacts 下的 `artifacts/v4`，新路径应继续通过 `V4Paths` 生成。 |
| watchers 直接写 terminal decision | 已关闭 | watcher 负责 raw evidence，turn 终态由 `CompletionDetector` / workflow 决定。 |
| marker-only completion 仍是事实源 | 已关闭 | 普通 source-write turn 需要 valid outbox；marker 缺 outbox 时只能产生 inconclusive 或 legacy fallback。 |
| `turn.delivered` 被当成 read | 已关闭 | cursor 推进已改为 explicit ack / `message.read`。 |
| tmux adapter 不读 outbox | 已关闭 | adapter 已接入 `FilesystemRuntimeEventStream`，其中包含 outbox watcher。 |
| outbox 读取依赖 capture-pane 成功 | 已关闭 | adapter 先读 outbox，`observe()` 失败只记录 evidence。 |
| `TurnEnvelope` 缺 required outbox path | 已关闭 | envelope 和 prompt 均已带 required outbox path。 |
| message ack loop 无 consumer | 已关闭 | `MessageAckProcessor` 已消费 `acknowledged_message_ids`。 |
| `crew events` 硬编码 SQLite | 已关闭 | CLI 已通过 `build_v4_event_store()` 读取。 |
| `crew accept` 绕过 V4 merge transaction | 已关闭 | accept 已接入 guarded merge transaction。 |
| dirty-base protection 缺失 | 已关闭 | merge transaction 已检查 dirty workspace、base ref、transaction 期间主工作区变化。 |
| `crew run` / `crew supervise` 仍走 V3 | 已关闭 | 默认走 V4 runner；旧路径由 `--legacy-loop` 保留。 |

## 当前已关闭的问题

### Closed. accept 没有强制要求 `crew.ready_for_accept`

位置：

- `src/codex_claude_orchestrator/v4/merge_transaction.py`
- `src/codex_claude_orchestrator/v4/accept_readiness.py`

当前状态：

`AcceptReadinessGate` 已添加并在 `V4MergeTransaction.accept()` 开头调用。gate 读取 crew 的 V4 事件流，验证：

- 存在 latest `crew.ready_for_accept` 事件。
- ready event 有有效的 `round_id`。
- same round 有 `review.completed` 且 status 为 ok/warn。
- same round 有 `verification.passed`。
- ready 后无 blocking `challenge.issued` 或 `verification.failed`。

merge inputs 已按 ready round 过滤，stale round 的 patch 不会被合入。

影响：

- 已关闭。

### Closed. source worker fallback 绕过 write scope 兼容性

位置：

- `src/codex_claude_orchestrator/v4/crew_runner.py`

当前状态：

`_source_worker()` 中的 fallback 已移除。planner 返回 None 时直接返回 None，dynamic mode 会 spawn 新 worker。spawn 时已过滤不兼容的 source workers，确保决策策略不会误判为”已有 active source worker”。

影响：

- 已关闭。

### Closed. transcript cursor 初始化发生在 send 之后

位置：

- `src/codex_claude_orchestrator/v4/adapters/tmux_claude.py`

当前状态：

`deliver_turn()` 中 `_initialize_filesystem_stream()` 已移到 `native_session.send()` 之前。transcript offset 在 send 前记录，send 后即时写入的输出会被 `runtime.output.appended` 捕获。

影响：

- 已关闭。

### Closed. Spec status 仍然部分过期

位置：

- `docs/superpowers/specs/2026-05-02-v4-event-native-agent-filesystem-design.md:4`

当前状态：

已更新为：V4 foundation、structured outbox completion、message ack、event-store factory、guarded accept、default V4 run/supervise、repeated-failure governed learning feedback、V4-native merge artifacts、RepoIntelligence / PlannerPolicy、transcript cursor watch、typed review outbox、projection-native status、scope-compatible worker reuse、accept readiness gate、source worker fallback removal、transcript cursor initialization fix 已落地。所有问题已关闭。

影响：

- 已关闭。

### Closed. 少量 V4 模块仍直接类型绑定 SQLiteEventStore

原位置：

- `src/codex_claude_orchestrator/v4/gates.py`
- `src/codex_claude_orchestrator/v4/reconciler.py`

当前状态：

已改为依赖 `EventStore` protocol。SQLite 仍只作为实现、factory fallback、测试后端存在。

影响：

- 已关闭。

### Closed. merge transaction 已安全化，patch 来源已切到 V4-native artifacts

位置：

- `src/codex_claude_orchestrator/v4/merge_inputs.py`
- `src/codex_claude_orchestrator/v4/merge_transaction.py`
- `src/codex_claude_orchestrator/v4/crew_runner.py`

当前状态：

`V4MergeTransaction` 已经解决 accept 安全问题：会读取 worker patch、检查冲突、检查 base ref、创建 integration worktree、运行 verification、再应用主 workspace。

`V4CrewRunner` 会在 `controller.changes(...)` 后调用 `V4MergeInputRecorder`，把 worker diff 记录为 V4-native artifacts：

```text
.orchestrator/crews/<crew_id>/artifacts/v4/workers/<worker_id>/patches/<turn_id>.patch
.orchestrator/crews/<crew_id>/artifacts/v4/workers/<worker_id>/results/<turn_id>.json
```

同时写入事件：

```text
worker.patch.recorded
worker.result.recorded
```

`V4MergeTransaction` 现在优先读取 `worker.result.recorded` 指向的 V4 manifest/patch，并校验：

- artifact ref 必须是安全相对路径。
- patch artifact 必须存在。
- `patch_sha256` 必须匹配。
- patch 实际 touches paths 必须与 manifest `patch_paths` 一致。

只有在没有任何 V4 `worker.result.recorded` 时，才 fallback 到 legacy `changes.json` / `diff.patch`，并记录：

```text
merge.legacy_patch_source_used
```

影响：

- accept 的 merge input 已可从 V4-native artifact/event contract 重建。
- legacy recorder artifact 已降级为 compatibility fallback。
- 已关闭。

### Closed. Planner / RepoIntelligence 还没有成为真正决策核心

位置：

- `src/codex_claude_orchestrator/crew/decision_policy.py`
- `src/codex_claude_orchestrator/v4/crew_runner.py`

当前状态：

V4 runner 已经能串起 source worker、review worker、repair/challenge、verification、ready-for-accept 等步骤。Repeated `review_block` / `verification_failed` 已能生成 `learning.note_created`、`guardrail.candidate_created`、`worker.quality_updated`。

已补齐：

- `RepoIntelligence` 现在会推断 write scope、package boundary、risk tags、suggested verification commands。
- `PlannerPolicy` 现在用 authority、capability、write scope、worker quality 选择 source/review worker。
- `V4CrewRunner` 会读取 learning projection，把 worker quality、active skill/guardrail refs、repo risk tags 输入 planner/decision policy。
- frontend risk tag 会触发 browser verification worker，而不只依赖 goal keyword。

影响：

- 已关闭当前 P2。

### Closed. runtime watch/event-stream hardening

当前状态：

- `ClaudeCodeTmuxAdapter.watch_turn()` 仍保持 outbox-first，并通过 `FilesystemRuntimeEventStream` 统一读取 filesystem evidence。
- 如果 worker 有 transcript artifact，adapter 会读取增量 transcript；如果 required outbox 已写出，会按 outbox sha 去重后发出 `worker.outbox.detected`。
- stream state 会记录 outbox sha 和 transcript offset，并且只在 runtime evidence append 到 EventStore 后 commit；新 adapter 实例可继续从上次 offset 之后读取。
- filesystem stream 可产生 `worker.outbox.detected`、`runtime.output.appended` 和 `marker.detected` evidence；`capture-pane` 失败只产生 `runtime.observe_failed`，不阻断 transcript/outbox evidence。

影响：

- 已关闭当前 P2。更进一步的 async filesystem subscription / runtime push stream 可作为性能增强，不再是事实源可靠性 blocker。

### Closed. V3 compatibility retirement

当前状态：

- `crew run` / `crew supervise` 默认 V4，V3 loop 只在 `--legacy-loop` 显式启用。
- `crew accept` 默认 V4 merge transaction。
- `crew events` 读 V4 EventStore。
- `crew status` 有 V4 events 时优先返回 V4 projection；没有 V4 events 才 fallback 到 legacy controller status。
- worker reuse 已比较 write scope compatibility，避免 V3 pool 仅凭 capability/authority 复用不兼容 worker。
- legacy merge artifact 只作为 compatibility fallback，并记录 evidence。

影响：

- 已关闭当前 P2。完整移除 `CrewController` substrate 属于后续大版本拆分，不再是当前 V4 主路径问题。

### Closed. Review schema 不够 typed

当前状态：

- `WorkerOutboxResult` 支持 typed `review` object。
- `OutboxWatcher` 会保留 typed review payload。
- `V4CrewRunner` 优先消费 typed review；只有缺失 typed payload 时才 fallback 到 summary 文本 parser。

影响：

- 已关闭。

## 当前优先级总表

| 优先级 | 问题 | 状态 |
| --- | --- | --- |
| Closed | accept 强制要求 `crew.ready_for_accept` / review OK / 无 blocking challenge | 已关闭 |
| Closed | source worker fallback 绕过 write scope 兼容性 | 已关闭 |
| Closed | transcript cursor 初始化发生在 send 之后 | 已关闭 |
| Closed | Planner / RepoIntelligence 尚未成为决策核心 | 已关闭 |
| Closed | runtime watch/event-stream hardening | 已关闭 |
| Closed | V3 compatibility retirement | 已关闭 |
| Closed | typed review outbox schema | 已关闭 |
| Closed | worker reuse write scope compatibility | 已关闭 |
| Closed | EventStore migration/version health check | 已关闭 |
| Closed | filesystem runtime event stream | 已关闭 |
| Closed | V4-native merge artifacts | 已关闭 |
| Closed | 正式系统架构文档缺失 | 已关闭 |
| Closed | spec status 部分过期 | 已关闭 |
| Closed | `gates.py` / `reconciler.py` 仍类型绑定 `SQLiteEventStore` | 已关闭 |
| Closed | repeated failure governed learning feedback | 已关闭 |
| Closed | tmux outbox completion | 已关闭 |
| Closed | capture-pane 失败阻断 outbox | 已关闭 |
| Closed | required outbox path | 已关闭 |
| Closed | message ack loop | 已关闭 |
| Closed | `crew events` hardcoded SQLite | 已关闭 |
| Closed | `crew accept` 绕过 V4 transaction | 已关闭 |
| Closed | run/supervise 默认 V4 | 已关闭 |

## 后续增强

当前所有 P1/P2 问题已关闭。后续增强建议按产品成熟度推进，而不是作为当前 blocking issue：

- 更完整的 repo dependency graph 和 ownership inference。
- async filesystem subscription / runtime push stream，进一步减少同步 supervise 调用延迟。
- 将 `CrewController` substrate 拆成 V4-native worker lifecycle service。

## 当前完成标准

本阶段已完成，满足以下标准：

- `crew accept` 必须要求 latest `crew.ready_for_accept`，且无未关闭 blocking challenge。✅
- scope block / review block / missing ready event 后直接 accept 必须 blocked。✅
- source worker 复用不能绕过 `PlannerPolicy` 的 write scope compatibility。✅
- transcript cursor 必须在 send 前初始化，send 后即时输出不能漏读。✅
- 设计文档状态与代码主路径一致。
- V4 服务不再把 SQLite 当成架构类型。
- accept 的 merge input 已可以从 V4-native artifact/event contract 重建。
- planner 能消费 repo intelligence、worker quality、adversarial feedback。
- transcript watch 有可恢复 cursor。
- review verdict 有 typed outbox schema。
- `crew status` 优先使用 V4 projection。
- EventStore 有 schema/version health check。
- filesystem runtime event stream 能去重 outbox、恢复 transcript cursor，并在事件 durable append 后推进 state。
- `pytest -q` 全量通过。
