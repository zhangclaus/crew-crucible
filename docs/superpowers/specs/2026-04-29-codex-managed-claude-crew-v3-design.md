# Codex-Managed Claude Crew V3 Design

- Date: 2026-04-29
- Status: Confirmed in conversation, ready for implementation planning
- Version: V3
- Scope: Add a Codex-managed multi-Claude worker orchestration layer above the existing V2 adversarial session and Claude bridge system

## 0. Version Boundary

The project now has three clear capability layers.

V1 is one-shot dispatch:

- Codex packages one task.
- Claude Code executes through the CLI adapter.
- The orchestrator records output, changed files, and verification.
- The supervisor accepts, retries, or reports the result.

V2 is single-worker adversarial supervision:

- Codex keeps one long-running Claude bridge session.
- Codex can send follow-up instructions, verify, challenge, accept, or escalate.
- The session engine records multi-round demand, repair, verification, and decision turns.
- The worker is still one main Claude session for one requirement.

V3 is multi-worker crew orchestration:

- Codex can dynamically create and coordinate multiple Claude workers for one user goal.
- Workers can have different roles, workspaces, permissions, and task scopes.
- A shared blackboard stores facts, claims, patches, risks, verification evidence, and decisions.
- Codex remains the only final arbiter for acceptance and merge.

V3 should not replace V2. It should compose multiple V2-style Claude bridge sessions under a higher-level crew controller.

```text
V3 CrewController
  uses WorkerPool
    manages N x V2 ClaudeBridgeSession
      uses V1 ClaudeCliAdapter / WorkspaceManager / Recorder / VerificationRunner
```

## 1. Problem Statement

The current V2 system is already more valuable than a simple subagent call because Codex can supervise a Claude session over time. However, it still has a single-worker shape. For larger coding tasks, one Claude session tends to mix architecture reading, implementation, review, testing, and risk analysis in one context. That creates several problems:

- exploration competes with implementation for context
- review is not independent from the worker that wrote the patch
- tests and evidence are not naturally separated from claims
- parallelizable work still happens serially
- Codex has no first-class object for assigning multiple workers and comparing their outputs

V3 should turn the project into a Codex-first local crew system. Codex should be able to decide that a task needs one worker, three workers, or no worker, then allocate Claude Code sessions with controlled roles and permissions.

The goal is not to rebuild Claude Code's internal subagent or team runtime. Claude Code already has strong agent, teammate, mailbox, and worktree ideas. This project should sit one layer above those mechanisms and provide cross-worker supervision, durable evidence, policy, verification, and merge arbitration.

## 2. Chosen Direction

Build a conservative "Codex-managed Claude Crew" layer:

```text
User Goal
-> Codex Supervisor
-> CrewController
-> TaskGraph
-> WorkerPool
-> ClaudeBridgeWorker[]
-> BlackboardStore
-> VerificationRunner
-> MergeArbiter
-> Final Codex decision
```

Codex owns planning, delegation, challenge, verification, and final acceptance. Claude workers own bounded execution.

The first V3 implementation should prefer a controlled star topology:

```text
           Claude Explorer
                 |
Claude Reviewer -> Blackboard <- Claude Implementer
                 ^
           Claude Verifier
                 |
              Codex
```

Workers do not freely modify each other's tasks or form an uncontrolled mesh. They communicate through the blackboard and through Codex-issued messages. This keeps the system understandable, replayable, and easier to debug.

## 3. Goals

- Let Codex dynamically allocate multiple Claude workers for one user goal.
- Support role-specific workers such as explorer, implementer, reviewer, verifier, and optional competitor.
- Reuse the existing Claude bridge, verification runner, workspace manager, policy gate, and recorders.
- Persist a crew-level record that explains who did what, why, with which evidence.
- Keep final acceptance and merge decisions centralized in Codex.
- Allow parallel read-only exploration and review when task scopes are independent.
- Isolate write-capable workers through worktrees or explicit write scopes.
- Make worker outputs comparable through a shared blackboard schema.
- Provide a path to support non-Claude workers later without changing the crew model.

## 4. Non-Goals

- No uncontrolled peer-to-peer agent swarm.
- No automatic merge to the user's main worktree.
- No dependency on unstable Claude Code internal source files as a runtime API.
- No attempt to reimplement Claude Code's full agent team system.
- No hosted SaaS control plane.
- No model fine-tuning or autonomous long-running background daemon in the initial V3.
- No requirement that every task use multiple workers; Codex may choose a single V2 session for small tasks.

## 5. Relationship To Claude Code Source And Open-Source Patterns

The local Claude Code source snapshot shows several useful patterns:

- agent definitions with role, tools, model, permission mode, memory, hooks, and isolation
- background and asynchronous workers
- team membership files
- task lists with owner, status, dependencies, and blocking relationships
- file-backed teammate mailboxes
- worktree isolation for writing agents
- parent-controlled permission and session metadata

V3 should borrow these concepts without coupling to Claude Code internals. The stable integration surface should remain the Claude CLI and the existing bridge behavior:

- `claude --print`
- `--resume <session_id>`
- `--output-format json`
- `--allowedTools`
- `--permission-mode`
- prompt-level role and task instructions

Open-source orchestrators such as MCO, Composio Agent Orchestrator, Agent Swarm, Orca, and Claude Code agent teams suggest that multi-agent coding systems converge on several common primitives: worker roles, isolated workspaces, shared status, task graphs, review loops, and merge control. V3's differentiator should be Codex-first supervision with local evidence and verification as first-class records.

## 6. Core Concepts

### 6.1 Crew

A crew is one multi-worker attempt to satisfy a user goal. It contains:

- a root goal
- a Codex-authored plan
- a task graph
- one or more workers
- a blackboard
- verification runs
- merge decisions
- final outcome

Crew status values:

- `planning`
- `running`
- `blocked`
- `needs_human`
- `accepted`
- `failed`
- `cancelled`

### 6.2 Worker

A worker is a managed Claude bridge session with role-specific instructions and permissions.

Initial worker roles:

- `explorer`: read-only codebase analysis, architecture mapping, risk discovery
- `implementer`: write-capable worker in an isolated worktree or scoped workspace
- `reviewer`: read-only review of proposed diffs and claims
- `verifier`: runs or proposes verification commands and reports evidence
- `competitor`: optional alternate implementer for high-risk tasks

Each worker has:

- `worker_id`
- `role`
- `bridge_id`
- `workspace_mode`
- `write_scope`
- `allowed_tools`
- `status`
- `current_task_id`
- `budget`
- `created_at`
- `updated_at`

### 6.3 Task Graph

The task graph is the crew's operational plan. It avoids treating multi-agent work as a pile of prompts.

Each task has:

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

Task status values:

- `pending`
- `assigned`
- `running`
- `submitted`
- `challenged`
- `accepted`
- `rejected`
- `blocked`

### 6.4 Blackboard

The blackboard is the shared evidence layer. It should be append-only for auditability, with derived summaries built on top.

Entry types:

- `fact`: grounded observation about the repo or task
- `claim`: worker assertion that requires evidence
- `question`: unresolved issue
- `risk`: potential failure mode or conflict
- `patch`: proposed code change or diff reference
- `verification`: command, result, log, or artifact
- `review`: critique of a claim or patch
- `decision`: Codex acceptance, rejection, merge, or escalation decision

Every blackboard entry should include:

- `entry_id`
- `crew_id`
- `task_id`
- `worker_id` or `codex`
- `type`
- `content`
- `evidence_refs`
- `confidence`
- `created_at`

The blackboard should live under:

```text
.orchestrator/crews/<crew_id>/blackboard.jsonl
```

### 6.5 Merge Arbiter

The merge arbiter protects the main workspace from conflicting worker edits.

Rules:

- Codex is the only actor that can approve final merge.
- Write-capable workers should use isolated worktrees by default.
- Multiple write-capable workers cannot own overlapping write scopes unless the task is explicitly marked as a competing implementation.
- Reviewer and verifier workers are read-only by default.
- Every proposed merge requires evidence: diff summary, changed files, verification result, and reviewer response.

## 7. Data Model

Add crew-level records without breaking existing V1/V2 records.

Suggested new files:

```text
src/codex_claude_orchestrator/crew_models.py
src/codex_claude_orchestrator/crew_controller.py
src/codex_claude_orchestrator/worker_pool.py
src/codex_claude_orchestrator/blackboard.py
src/codex_claude_orchestrator/task_graph.py
src/codex_claude_orchestrator/merge_arbiter.py
```

The exact module split can be adjusted during implementation, but the responsibilities should remain separate.

### 7.1 CrewRecord

Fields:

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

Fields:

- `worker_id`
- `crew_id`
- `role`
- `agent_profile`
- `bridge_id`
- `workspace_mode`
- `workspace_path`
- `write_scope`
- `allowed_tools`
- `status`
- `assigned_task_ids`
- `last_seen_at`
- `created_at`
- `updated_at`

### 7.3 TaskRecord Extension

The existing task record shape can remain for V1/V2. V3 can either introduce `CrewTaskRecord` or extend task metadata with optional crew fields.

Crew task fields:

- `crew_id`
- `owner_worker_id`
- `depends_on`
- `blocked_by`
- `allowed_paths`
- `forbidden_paths`
- `expected_outputs`
- `evidence_refs`

### 7.4 BlackboardEntry

Fields:

- `entry_id`
- `crew_id`
- `task_id`
- `actor_type`: `codex` or `worker`
- `actor_id`
- `type`
- `content`
- `evidence_refs`
- `confidence`
- `created_at`

## 8. Control Flow

### 8.1 Default Crew Flow

```text
crew start
-> Codex creates CrewRecord
-> Codex creates initial TaskGraph
-> CrewController allocates workers
-> Explorer reads repo and posts facts/risks
-> Codex refines task graph
-> Implementer works in isolated workspace
-> Implementer posts patch and evidence
-> Reviewer reviews patch and claims
-> Verifier runs or proposes checks
-> Codex challenges missing evidence
-> Implementer repairs if needed
-> VerificationRunner performs final checks
-> MergeArbiter produces merge recommendation
-> Codex accepts, rejects, or asks human
```

### 8.2 Small Task Fast Path

For small goals, Codex can choose not to create a crew:

```text
User Goal
-> V2 ClaudeBridgeSession
-> verify/challenge/accept
```

This prevents V3 from adding ceremony to simple tasks.

### 8.3 Competitive Implementation Flow

For risky or ambiguous changes, Codex may create two implementers:

```text
Explorer -> shared facts
Implementer A -> patch A
Implementer B -> patch B
Reviewer -> compare A/B
Verifier -> run checks
Codex -> choose, merge, or reject both
```

This mode must use isolated worktrees and non-overlapping final merge decisions.

## 9. Workspace And Permission Model

Workspace modes:

- `readonly`: worker can inspect only
- `shared`: worker can operate in the source repo, reserved for trusted low-risk workflows
- `worktree`: worker writes in an isolated git worktree

Default role permissions:

| Role | Workspace | Write Access | Purpose |
| --- | --- | --- | --- |
| explorer | readonly | no | Understand repo and risks |
| implementer | worktree | yes, scoped | Produce patch |
| reviewer | readonly | no | Review claims and diffs |
| verifier | readonly or shared | no by default | Run checks and collect evidence |
| competitor | worktree | yes, scoped | Produce alternate patch |

Policy rules:

- Protected paths remain protected.
- Dangerous commands remain blocked.
- Write scopes are declared before a worker starts.
- Codex can grant broader scope only through an explicit crew decision record.
- Workers must preserve unrelated user changes.

## 10. CLI And UX

Initial commands:

```bash
orchestrator crew start --repo /path/to/repo --goal "..." --workers explorer,implementer,reviewer
orchestrator crew status --repo /path/to/repo --crew <crew_id>
orchestrator crew tail --repo /path/to/repo --crew <crew_id> --limit 20
orchestrator crew blackboard --repo /path/to/repo --crew <crew_id>
orchestrator crew verify --repo /path/to/repo --crew <crew_id>
orchestrator crew challenge --repo /path/to/repo --crew <crew_id> --task <task_id>
orchestrator crew accept --repo /path/to/repo --crew <crew_id>
```

Useful later commands:

```bash
orchestrator crew worker add --crew <crew_id> --role reviewer
orchestrator crew worker stop --crew <crew_id> --worker <worker_id>
orchestrator crew merge-plan --crew <crew_id>
```

The UI should show:

- crew summary
- task graph
- worker status
- blackboard entries
- changed files by worker
- verification evidence
- Codex decisions

## 11. Error Handling

Worker failure:

- mark worker `failed`
- preserve bridge transcript
- post failure entry to blackboard
- let Codex decide retry, replacement, or escalation

Verification failure:

- record command, return code, stdout, stderr, and artifacts
- post `verification` and `risk` entries
- challenge the responsible worker when repair is possible

Conflict:

- mark affected tasks `blocked`
- ask reviewer or Codex to compare diffs
- require merge arbiter decision before applying any patch

Claude session loss:

- try resume through stored bridge session id
- if resume fails, create replacement worker with prior blackboard summary
- preserve old worker as failed evidence

Policy violation:

- stop the worker's current task
- record the blocked action
- require Codex or human decision before continuing

## 12. Testing Strategy

Unit tests:

- task graph dependency and status transitions
- blackboard append/read/filter behavior
- worker pool allocation and lifecycle state
- merge arbiter conflict detection
- CLI argument parsing

Integration tests:

- fake Claude adapter runs explorer/implementer/reviewer flow
- implementer patch is recorded without touching main workspace
- failed verification triggers challenge
- reviewer rejection prevents acceptance
- worker resume preserves bridge id and blackboard context

Regression tests:

- V1 dispatch still works
- V2 bridge commands still work
- protected paths and dangerous commands remain blocked

Manual verification:

- run a small crew on a toy repo
- inspect `.orchestrator/crews/<crew_id>/`
- confirm transcripts, blackboard, task graph, and verification logs are understandable

## 13. Rollout Plan

### Phase 1: Crew Records And Blackboard

Add persistent crew records, task graph storage, and blackboard append/read APIs. Use fake workers in tests.

### Phase 2: WorkerPool Over ClaudeBridge

Create managed workers on top of existing Claude bridge sessions. Support start, send, status, tail, and stop semantics.

### Phase 3: Default Explorer/Implementer/Reviewer Flow

Implement `crew start` with a controlled three-role workflow. Keep verifier as Codex-run verification at first.

### Phase 4: Worktree Write Isolation And Merge Plan

Make implementer workers use isolated worktrees by default. Add merge plan generation and conflict detection.

### Phase 5: UI And Advanced Scheduling

Expose crew state in the existing UI. Add dynamic worker creation, competitor mode, and richer task graph updates.

## 14. Success Criteria

V3 is successful when:

- Codex can start a crew with at least explorer, implementer, and reviewer roles.
- Each worker has an independent Claude bridge session and clear role prompt.
- Worker outputs are persisted to a crew blackboard.
- Implementer changes do not automatically modify the main workspace.
- Codex can challenge a worker using reviewer or verifier evidence.
- Final acceptance requires verification evidence.
- The final report explains the task graph, worker contributions, risks, changed files, and merge recommendation.
- Existing V1 and V2 commands continue to pass tests.

## 15. Design Principles

- Codex is the supervisor, not just a router.
- Claude workers are capable executors, not final decision makers.
- Evidence beats claims.
- Workspaces protect the user's repo.
- Append-only records make agent work replayable.
- Multi-agent work should be optional and proportional to task complexity.
- Prefer stable CLI boundaries over unstable internal source dependencies.
