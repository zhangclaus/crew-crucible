# V4 Event-Native Agent Filesystem Design

Date: 2026-05-02
Status: Revised after review; pending implementation planning approval

## Purpose

V4 should become the main orchestration path for a stable, powerful multi-agent file-system runtime. The system should no longer treat tmux terminal output as the source of truth. Terminal output is useful evidence, but durable state must come from structured events in remote PostgreSQL, worker result files, message read acknowledgements, merge artifacts, and verification evidence.

The target shape is an event-native agent system:

- Codex is the supervisor and final decision maker.
- Claude Code workers are disposable subagents with explicit contracts.
- Each worker turn produces durable event evidence in PostgreSQL and structured filesystem artifacts under the repository's orchestrator state root.
- The project can recover after process restart by replaying V4 events from PostgreSQL and reading the agent filesystem.
- Accepting work means applying worker changes through a verified merge transaction, not merely finalizing a crew record.

This design follows the direction of strong open-source agent-team systems:

- OpenHands style action-observation/event runtime: <https://docs.openhands.dev/sdk/arch/events>
- LangGraph style durable execution and checkpointing: <https://docs.langchain.com/oss/python/langgraph/durable-execution>
- pi-agentteam style shared task board, typed messaging, and event-driven wakeups: <https://pi.dev/packages/pi-agentteam>
- CrewAI separation of control flow and execution crews: <https://docs.crewai.com/en/introduction>
- AutoGen team patterns with explicit handoffs and team control loops: <https://microsoft.github.io/autogen/dev/user-guide/agentchat-user-guide/tutorial/teams.html>

The implementation should adapt these ideas to this repo instead of copying their abstractions directly.

## Current Problems

The current code already has a V4 event store and workflow foundation, but the active orchestration path still has V3 assumptions.

1. Completion depends too much on tmux output and marker detection.
   `src/codex_claude_orchestrator/runtime/native_claude_session.py` captures the pane and searches for a marker. This is fragile because terminal output can be stale, clipped, quoted, reformatted, or missing a marker.

2. V4's tmux adapter still performs a one-shot observe.
   `src/codex_claude_orchestrator/v4/adapters/tmux_claude.py` wraps the same snapshot behavior instead of tailing transcript/output and ingesting durable events.

3. Dynamic decisions are rule-oriented.
   `src/codex_claude_orchestrator/crew/decision_policy.py` decides primarily from keywords, changed files, worker capabilities, and failure counts. It does not model dependency boundaries, test mapping, ownership, file risk, or worker quality history.

4. Worker reuse is under-constrained.
   `src/codex_claude_orchestrator/workers/pool.py` checks capability, authority, and workspace mode, but not strict write-scope compatibility or contract compatibility before reusing an active worker.

5. Message bus is not closed-loop.
   `src/codex_claude_orchestrator/messaging/message_bus.py` can store messages and read an inbox, but worker turns do not automatically include unread inbox digest, protocol requests, or delivery acknowledgements.

6. The V4 event schema is under-specified for the target workflow.
   Current V4 events do not yet carry first-class `round_id` and `contract_id` fields. The design needs an explicit schema versioning and migration path before these fields become required.

7. Merge and accept are too thin.
   `src/codex_claude_orchestrator/crew/merge_arbiter.py` only detects same-file conflicts. `CrewController.accept()` finalizes and stops workers without applying worker patches into an integration workspace and validating the result.

8. V4 is not yet the main CLI path.
   `src/codex_claude_orchestrator/cli.py` exposes V4 events, but normal crew run/supervision still leans on V3 controller behavior.

## Goals

- Make V4 the default orchestration path for `crew run`, `crew supervise`, `crew status`, `crew accept`, and event inspection.
- Replace marker-only completion with multi-source completion evidence.
- Introduce an agent filesystem where workers write structured outbox/result artifacts.
- Close the message bus loop so every worker turn includes unread inbox and open protocol requests, and message cursors advance only after explicit read acknowledgement.
- Make worker reuse safe by comparing contract compatibility, write scope, workspace mode, authority, and worker state.
- Add a merge transaction that applies worker changes into an integration worktree, validates write scope, resolves conflicts, and verifies the final workspace before accept.
- Protect the user's main workspace from clobbering dirty or diverged changes during accept.
- Upgrade decision-making from simple routing rules to a planner that uses repo intelligence and risk signals.
- Preserve V3 compatibility while moving user-facing commands to V4.

## Non-Goals

- Do not build a general-purpose replacement for Claude Code.
- Do not require workers to run without tmux in the first V4 path. The runtime should support tmux as an adapter, but tmux should not be the source of truth.
- Do not remove V3 immediately. It remains a compatibility layer until V4 commands cover the required workflows.
- Do not commit database passwords or production secrets. PostgreSQL connection details come from environment variables or a secret provider.
- Do not make LLM-based planning the only safety layer. Deterministic gates remain mandatory for scope, merge, and verification.

## Architecture Overview

```mermaid
flowchart TD
    CLI["CLI / UI / API"] --> Supervisor["V4 Supervisor"]
    Supervisor --> Workflow["Workflow Engine"]
    Supervisor --> Planner["Planner Policy"]
    Supervisor --> Turns["Turn Service"]
    Supervisor --> Merge["Merge Transaction"]

    Workflow --> Events["Remote PostgreSQL Event Store"]
    Planner --> RepoIntel["Repo Intelligence"]
    Turns --> Runtime["Runtime Adapter"]
    Runtime --> Claude["Claude Code / tmux"]

    Claude --> Transcript["Transcript"]
    Claude --> Outbox["Worker Outbox Files"]
    Claude --> Worktree["Worker Worktree"]

    Transcript --> Watchers["Runtime Watchers"]
    Outbox --> Watchers
    Worktree --> Watchers
    Runtime --> Watchers
    Watchers --> Events

    Events --> Projections["Status / Readiness Projections"]
    Merge --> Integration["Integration Worktree"]
    Integration --> Verification["Verification Adapter"]
    Verification --> Events
```

The main boundary is:

- Runtime adapters deliver turns and expose raw runtime signals.
- Watchers translate raw signals and filesystem artifacts into raw evidence events only.
- Completion and workflow components are the only writers of terminal turn-state events such as `turn.completed`, `turn.failed`, and `turn.timeout`.
- Workflow and projections decide state from events, not from live terminal state.
- Merge and accept operate on patches/artifacts and final verification evidence.

## Agent Filesystem

Each crew owns a durable local filesystem area under the existing recorder/artifact root. V4 should use one canonical physical root and resolve compatibility aliases through one path resolver.

Canonical physical roots:

- Repository state root: `<repo_root>/.orchestrator`
- Crew record root: `<repo_root>/.orchestrator/crews/<crew_id>`
- V4 agent artifact root: `<repo_root>/.orchestrator/crews/<crew_id>/artifacts/v4`
- Worker worktree root: `<repo_root>/.orchestrator/worktrees/<crew_id>/<worker_id>`
- V4 event store: remote PostgreSQL, not a local event database file

The V4 agent artifact root is the only place new V4 filesystem artifacts should be written. Existing V3 artifact paths may be read through compatibility resolvers, but new V4 modules must not independently choose `.codex`, `.orchestrator/v4`, or raw crew directories.

```text
<repo_root>/.orchestrator/crews/<crew_id>/artifacts/v4/
  manifest.json
  workers/
    <worker_id>/
      contract.json
      allocation.json
      onboarding_prompt.md
      transcript.txt
      inbox/
        <message_id>.json
      outbox/
        <turn_id>.json
      patches/
        <turn_id>.patch
      changes/
        <turn_id>.json
      logs/
        runtime.log
  messages/
    messages.jsonl
    cursors.json
    deliveries.jsonl
  merge/
    plan.json
    integration.patch
    conflicts.json
    verification.json
  projections/
    status.json
    readiness.json
```

Compatibility aliases:

- `workers/<worker_id>/...` without the `v4/` prefix refers to legacy V3 artifacts.
- `.codex/crew/<crew_id>/...` is documentation shorthand only and must not be used as a physical write path.
- All artifact references stored in events should be relative to the V4 agent artifact root unless explicitly marked as legacy.

## PostgreSQL Event Store

Remote PostgreSQL is the canonical event source for V4. The implementation should introduce an `EventStore` protocol and a `PostgresEventStore` production implementation. Any local or in-memory store may be used only for narrow unit tests through the same protocol.

Connection configuration:

```text
PG_HOST      default for this deployment: 124.222.58.173
PG_DB        default for this deployment: ragbase
PG_USER      default for this deployment: ragbase
PG_PORT      default for this deployment: 5432
PG_PASSWORD  required secret, no committed default
```

`PG_PASSWORD` must come from the environment or a secret provider. It must not be committed to code, docs, test fixtures, or default settings. The implementation may provide non-secret defaults for host, database, user, and port, but a missing password should fail fast with a clear configuration error.

Required tables:

```text
event_store_schema_migrations
  version
  checksum
  applied_at

agent_events
  event_id
  stream_id
  sequence
  type
  crew_id
  worker_id
  turn_id
  round_id
  contract_id
  idempotency_key
  payload_jsonb
  artifact_refs_jsonb
  created_at
```

Constraints and indexes:

- Unique `(stream_id, sequence)`.
- Unique non-empty `idempotency_key`.
- Indexes on `crew_id`, `worker_id`, `turn_id`, `round_id`, `contract_id`, and `created_at`.
- Appends use a transaction and row-level advisory or sequence locking per `stream_id`.

Schema versioning:

- Version `1` creates the base event table.
- Version `2` adds first-class `round_id` and `contract_id`.
- Migrations are append-only and recorded in `event_store_schema_migrations`.
- Event readers must tolerate absent optional fields during replay of older events.
- Once V4 becomes the main path, new events must always populate `round_id` and `contract_id` when the domain object exists.

## Event Model

V4 events are the durable control plane. Every event must have:

- `event_id`
- `stream_id`
- `type`
- `crew_id`
- optional `worker_id`
- optional `turn_id`
- optional `round_id`
- optional `contract_id`
- `payload`
- `artifact_refs`
- `created_at`
- `idempotency_key`

Core event families:

```text
crew.started
crew.status_changed
worker.spawn_requested
worker.spawned
worker.stopped
contract.created
contract.superseded
message.created
message.delivered
message.read
turn.requested
turn.delivery_started
turn.delivered
runtime.output.appended
runtime.process_exited
turn.deadline_reached
worker.outbox.detected
worker.patch.detected
marker.detected
turn.completed
turn.inconclusive
turn.failed
turn.timeout
review.requested
review.completed
verification.started
verification.passed
verification.failed
merge.planned
merge.started
merge.conflicted
merge.completed
crew.ready_for_accept
crew.accepted
human.required
```

Event replay must be sufficient to rebuild crew status, active workers, open turns, message cursors, readiness, and acceptability.

Event writer ownership:

```text
Watchers             -> raw evidence events only
CompletionDetector   -> turn.completed / turn.inconclusive / turn.failed / turn.timeout
WorkflowEngine       -> crew lifecycle, readiness, human-required
MessageBus           -> message.created / message.delivered / message.read
MergeTransaction     -> merge.* and verification-linked merge evidence
VerificationAdapter  -> verification.started / verification.passed / verification.failed
```

## Runtime Watchers

The current observe loop should be replaced with a watcher pipeline. It can still poll internally at first, but the state model should be evidence ingestion, not "capture pane and decide." Watchers must not emit terminal turn-state decisions.

Watchers:

1. `TranscriptTailWatcher`
   Reads `transcript.txt` incrementally using byte offsets. Emits `runtime.output.appended` events. This avoids reprocessing the whole terminal snapshot.

2. `OutboxWatcher`
   Reads worker result files from `workers/<worker_id>/outbox/<turn_id>.json`. Emits `worker.outbox.detected` with schema validity, acknowledged message ids, and artifact refs. It does not emit `turn.completed` or `turn.inconclusive`.

3. `PatchWatcher`
   Detects patch/change summaries from worker worktree or explicit patch files. Emits `worker.patch.detected` and links changed-file artifacts.

4. `MarkerDetector`
   Detects expected marker in new transcript output. Emits `marker.detected`. Marker is completion evidence, not the only completion condition.

5. `ProcessWatcher`
   Detects process exit or missing tmux session. Emits `runtime.process_exited`. CompletionDetector decides whether that evidence becomes `turn.failed`.

6. `TimeoutWatcher`
   Checks turn deadline and emits `turn.deadline_reached`. CompletionDetector decides whether that evidence becomes `turn.timeout`.

Completion precedence:

1. Valid outbox result for the current `turn_id` wins.
2. For source-write turns, an expected marker without a valid outbox result becomes `turn.inconclusive` with reason `missing_outbox`.
3. For explicitly legacy/read-only turns that declare `completion_mode=marker_allowed`, an expected marker can complete the turn if no structured result is required.
4. Contract-level marker without turn-specific result is inconclusive.
5. Process exit before completion is failed.
6. Deadline reached before completion is timeout.
7. Any stale marker or quoted marker is ignored unless tied to the current turn.

## Worker Turn Protocol

Every worker turn should be delivered as a structured envelope. The human-readable prompt may remain markdown, but the content should be generated from a structured model.

Turn envelope fields:

```text
crew_id
worker_id
turn_id
round_id
phase
contract_id
message
expected_marker
required_outbox_path
allowed_write_scope
acceptance_criteria
unread_inbox_digest
open_protocol_requests
blackboard_highlights
deadline_at
attempt
```

Workers must be asked to produce a result file:

```json
{
  "crew_id": "crew-v4-runtime",
  "worker_id": "worker-source-1",
  "turn_id": "round-1-worker-source",
  "status": "completed",
  "summary": "Added transcript tail ingestion and outbox completion handling.",
  "changed_files": ["src/codex_claude_orchestrator/v4/watchers.py"],
  "verification": [
    {
      "command": ".venv/bin/python -m pytest tests/v4 -q",
      "status": "passed",
      "summary": "V4 test suite passed."
    }
  ],
  "acknowledged_message_ids": ["msg-review-request"],
  "messages": [],
  "risks": [],
  "next_suggested_action": "review"
}
```

If a worker cannot write the outbox file, it must report why in terminal output. The supervisor can then mark the turn inconclusive and decide whether to retry, repair the prompt, or require human input.

## Message Bus Closed Loop

The message bus should become a real delivery system rather than a passive log.

Changes:

- `AgentMessageBus.send()` still appends messages and emits `message.created`.
- A new `TurnContextBuilder` reads unread inbox messages for the target worker.
- `send_worker` / V4 turn delivery injects unread inbox digest and open protocol requests into the turn envelope.
- `turn.delivered` records delivery to the runtime but does not advance read cursors.
- Message cursors are advanced only after explicit `message.read` or a valid worker outbox result that acknowledges delivered message ids.
- Worker outbox can include responses or handoff messages. `OutboxWatcher` emits raw `worker.outbox.detected`; a message-ingestion service then validates embedded messages, appends them to the bus, and emits `message.created`.
- Delivery records should include `message_id`, `worker_id`, `turn_id`, and delivery event id.

This prevents the failure mode where messages exist in storage but the worker never sees them.

## Worker Lifecycle and Reuse

The default V4 behavior should follow the subagent-driven model:

- Fresh worker per implementation task.
- Separate fresh workers for spec review and code quality review.
- Read-only review or verification workers may be reused only when safe.

Reuse compatibility must check:

- Worker is active and healthy.
- Required capabilities are covered.
- Worker authority covers the requested authority.
- Workspace mode matches.
- Existing worker write scope covers the new contract write scope.
- Contract label/mission is compatible.
- Worker does not have blocking unread protocol requests.
- Worker worktree dirty state is compatible with the new task.
- Worker recent quality score is acceptable.

If any check fails, V4 spawns a new worker rather than reusing context.

## Planner and Repo Intelligence

`CrewDecisionPolicy` should evolve into a planner with deterministic guardrails.

Repo intelligence should provide:

- Changed files and ownership.
- Package/module boundaries.
- Import/dependency graph.
- Test mapping from source paths to likely test commands.
- File risk classification: config, migration, generated file, public API, docs, UI, backend, tests.
- Historical failure clusters.
- Worker quality history.

Planner actions:

- Spawn source worker.
- Spawn context scout.
- Spawn patch reviewer.
- Spawn verification worker.
- Spawn browser/e2e worker.
- Split task into subcontracts.
- Retry current worker with correction.
- Stop or quarantine worker.
- Request human input.
- Start merge transaction.
- Mark ready for accept.

Rules still exist as safety gates, but planning should be informed by repository structure and evidence.

## Merge and Accept Transaction

Accepting a crew must become a transaction.

Transaction stages:

1. Collect worker changes.
   Read worker changes artifacts, patch files, worktree diffs, and declared changed files.

2. Validate scope.
   Every changed path must be allowed by the worker's contract write scope. Out-of-scope changes block merge.

3. Build merge plan.
   Detect same-file conflicts, dependency conflicts, API/test conflicts, generated-file conflicts, migration/config conflicts, and overlapping ownership.

4. Create integration worktree.
   Apply accepted worker patches in deterministic order from a recorded base ref and base tree hash.

5. Run targeted verification.
   Use repo intelligence and worker-provided verification evidence to choose commands.

6. Run final verification.
   Run the final required command set on the integrated workspace, not inside a worker's private worktree.

7. Record merge evidence.
   Write merge plan, patch, conflict summary, verification result, and changed files under `merge/`.

8. Update main workspace.
   Apply the verified integration result to the user's workspace only after the transaction passes and dirty-base protection succeeds.

9. Accept crew.
   Emit `crew.accepted`, finalize projections, and stop workers.

If any step fails, emit a blocking event and leave worker worktrees intact for inspection.

Dirty-base protection before updating the main workspace:

- Record `base_ref`, `base_commit`, and `base_tree_hash` when the crew or merge transaction starts.
- Before applying the integration result, compare the user's current `HEAD`, index, and worktree against the recorded base.
- If `HEAD` moved, rebase or replay the integration patch onto the current `HEAD` in a fresh integration worktree and rerun final verification.
- If the user has dirty files that overlap the integration patch, block accept and emit `human.required`.
- If dirty files are unrelated, either preserve them with a checked patch apply or block according to a conservative `dirty_base_policy`.
- Never overwrite a user-modified file in the main workspace without an explicit clean base check.

## CLI and UI Migration

V4 should become the default behavior:

```text
crew run          -> V4 supervisor
crew supervise    -> V4 workflow loop
crew status       -> V4 projections
crew events       -> V4 event store
crew worker send  -> V4 turn delivery
crew worker tail  -> V4 transcript/artifact tail
crew accept       -> V4 merge transaction
```

V3 remains accessible through explicit legacy paths or compatibility adapters. The CLI should avoid presenting V3 and V4 as two equally primary systems.

## Error Handling

The system should fail into inspectable states.

- Missing marker: inconclusive unless outbox result is valid.
- Missing outbox: inconclusive, then retry or human-required depending on attempts.
- Stale marker: ignored if not tied to the current turn.
- Worker process exit: failed if no completion evidence exists.
- Delivery conflict: existing `turn.delivery_started` prevents duplicate sends.
- Message delivery failure: cursor is not advanced.
- Runtime delivery without worker acknowledgement: cursor is not advanced.
- Scope violation: merge blocked.
- Verification failure: merge blocked and planner receives failure evidence.
- Dirty or diverged main workspace: accept is blocked or integration is replayed in a fresh worktree before final verification.
- Event store duplicate: idempotency key returns existing event.

## Testing Strategy

Unit tests:

- Event append idempotency.
- Projection rebuild from events.
- Transcript tail offset handling.
- Outbox schema validation.
- Watchers emit only raw evidence events, never terminal turn decisions.
- Marker detection ignores stale markers.
- Marker-only source-write completion becomes inconclusive without outbox.
- Message cursor advances only after read acknowledgement.
- Worker reuse rejects incompatible write scope.
- Merge arbiter detects scope and dependency conflicts.
- Merge transaction blocks dirty-base clobbering.
- Completion detector precedence.

Integration tests:

- Turn completes through outbox without marker.
- Source-write turn with marker but no outbox becomes inconclusive.
- Read-only legacy turn with marker and `completion_mode=marker_allowed` can complete.
- Turn fails on process exit without completion.
- Restart and replay resumes waiting turn without duplicate delivery.
- Worker message round-trip appears in next turn context.
- Accept applies patch in integration worktree and verifies before finalization.
- Accept refuses to overwrite dirty user files that overlap the integration patch.

Regression tests:

- Existing V4 tests keep passing.
- Existing V3 CLI compatibility tests keep passing until the command is intentionally migrated.

Target verification after implementation:

```text
.venv/bin/python -m pytest tests/v4 -q
.venv/bin/python -m pytest tests/messaging tests/workers tests/crew -q
.venv/bin/python -m pytest tests/cli/test_cli.py tests/ui/test_server.py -q
.venv/bin/python -m pytest -q
```

## Rollout Plan

Phase 1: V4 protocol foundation

- Add PostgreSQL event store protocol and schema migrations.
- Add turn context builder.
- Add outbox result schema.
- Add message delivery/read events.
- Add worker compatibility checks.

Phase 2: Watcher pipeline

- Add transcript tail watcher.
- Add outbox watcher.
- Add marker detector as event source.
- Add process/timeout events.
- Update V4 supervisor so watchers emit evidence and CompletionDetector owns terminal turn decisions.

Phase 3: Merge transaction

- Add patch collection.
- Add write-scope validation.
- Add integration worktree.
- Add final verification adapter.
- Add dirty-base protection before main workspace update.
- Make accept depend on merge transaction.

Phase 4: V4 main path

- Route primary CLI commands to V4.
- Keep legacy commands explicit.
- Update UI status from V4 projections.

Phase 5: Planner upgrade

- Add repo intelligence.
- Add test mapping.
- Add risk scoring.
- Add worker quality history.
- Upgrade planner actions.

## Acceptance Criteria

- A worker can complete a turn by writing a valid outbox result even if it never prints the marker.
- A missing marker cannot leave the crew permanently stuck in `waiting_for_worker` when valid structured evidence exists.
- A marker alone cannot complete a source-write turn that requires an outbox result.
- Watchers never emit terminal turn-state events.
- The next worker turn always includes unread inbox digest and open protocol requests.
- Message cursors are not advanced before explicit read acknowledgement.
- A worker with incompatible write scope is not reused.
- Accept cannot finalize without a successful merge transaction and final verification in an integration workspace.
- Accept cannot clobber dirty or diverged main-workspace changes.
- V4 event replay can reconstruct crew status and readiness.
- V4 event replay reads from remote PostgreSQL with schema migration/version checks.
- CLI primary crew flow uses V4 supervisor and projections.
- Full test suite passes.

## Risks and Mitigations

Risk: Worker fails to write the required outbox file.

Mitigation: Keep marker detection and transcript evidence as fallback, but classify the turn as inconclusive after bounded retries.

Risk: More event types make projections more complex.

Mitigation: Keep event schemas narrow, add projection tests, and treat projections as derived state that can be rebuilt.

Risk: Merge transaction may be slow for small tasks.

Mitigation: Use targeted verification first, but keep final verification mandatory before accept.

Risk: V3 compatibility becomes confusing.

Mitigation: Make V4 the default CLI path and name V3 access explicitly as legacy.

Risk: Planner becomes overfit to rules.

Mitigation: Separate deterministic safety gates from repo intelligence and planner scoring. Safety gates block dangerous actions; planner chooses useful next actions.
