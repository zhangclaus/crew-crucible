# Codex-First Local Multi-Agent Orchestrator Design

- Date: 2026-04-28
- Status: Approved in conversation, written for implementation planning
- Scope: First version of a local personal orchestrator where Codex is the default supervisor and Claude Code is the first external worker agent

## 1. Problem Statement

We want a practical way for Codex and Claude Code to collaborate on real development tasks without turning the first version into a full distributed agent platform.

The desired interaction loop is:

1. Codex receives a user goal.
2. Codex decides whether to act directly or delegate work.
3. Codex generates a structured task package for Claude Code or another worker.
4. The worker executes in a controlled workspace and returns structured results.
5. Codex evaluates the result and decides whether to accept, retry, reroute, ask the human, or merge changes and continue.

The system must support future expansion to more agents such as Gemini and future direct agent-to-agent collaboration, but the first version must optimize for local reliability, clear control boundaries, and fast time to usefulness.

## 2. Goals

The first version must:

- Run as a local personal orchestrator, not a hosted platform.
- Keep Codex as the default supervisor for task decomposition, routing, evaluation, and final decisions.
- Integrate Claude Code as the first worker through a stable programmatic adapter.
- Use isolated workspaces by default, while still allowing explicitly approved shared-workspace collaboration.
- Record enough task state and event history to support retries, audits, replay, and later protocol upgrades.
- Preserve a clean path toward future multi-agent interoperability.

## 3. Non-Goals

The first version does not attempt to provide:

- A fully decentralized mesh where all agents freely route tasks between each other.
- A distributed multi-machine runtime.
- A visual web platform, dashboard, or SaaS control plane.
- A generic third-party SDK for external developers.
- Universal support for every agent framework on day one.
- Fully automatic merging of arbitrary parallel edits without Codex review.

## 4. Design Principles

- Supervisor first: Codex owns the task graph and the final decision.
- Adapter isolation: vendor-specific invocation details stay in adapters, not in orchestration logic.
- Workspace safety: workers should not directly mutate the shared workspace by default.
- Structured over ad hoc: internal task and result objects should be typed and explicit.
- Mesh-ready internals: even though the first version is hub-and-spoke, the internal event model should already look like a future agent network.
- Human control at risky boundaries: dangerous commands, sensitive files, and shared-workspace merges require explicit policy approval.

## 5. Why This Architecture

Three broad approaches were considered:

1. Supervisor plus CLI or SDK bridges
2. A2A gateway first
3. Framework-centric runtime built directly on a multi-agent framework

The chosen direction is approach 1 for version 1.

Reasoning:

- It is the shortest path to a working local system.
- It cleanly matches the intended control model: Codex supervises, workers execute.
- It allows careful workspace isolation and merge gating from the start.
- It does not block a later shift to A2A wrapping or framework-backed orchestration.

Protocol and ecosystem conclusions that shape the design:

- A2A should be treated as the future agent-to-agent protocol layer, not the first internal runtime abstraction.
- MCP should be treated as the tool-access layer, not the agent-routing layer.
- Claude Code already exposes viable programmatic surfaces through CLI structured output and SDK capabilities.
- Existing frameworks such as OpenAI Agents SDK, LangChain subagents, AutoGen, and Microsoft Agent Framework are valuable references, but they are not the right first kernel for this local personal build.

## 6. System Overview

The orchestrator is a local process composed of seven core components:

1. `Supervisor`
2. `PromptCompiler`
3. `AgentAdapter`
4. `WorkspaceManager`
5. `RunRecorder`
6. `ResultEvaluator`
7. `PolicyGate`

High-level flow:

1. User submits a task.
2. Supervisor decides direct execution or delegation.
3. PromptCompiler builds a structured worker task package.
4. WorkspaceManager allocates an execution workspace.
5. AgentAdapter invokes Claude Code in that workspace.
6. RunRecorder stores run inputs, outputs, events, and artifacts.
7. ResultEvaluator judges the outcome.
8. Supervisor decides the next step.
9. PolicyGate controls risky transitions such as merge into shared workspace.

## 7. Component Boundaries

### 7.1 Supervisor

Responsibilities:

- Accept user intent.
- Decide whether to handle work directly or delegate.
- Select worker agent and workspace mode.
- Decide parallel or sequential execution.
- Interpret evaluation results and choose next actions.
- Own final merge, retry, reroute, and finish decisions.

Non-responsibilities:

- Raw CLI invocation details
- Vendor-specific output parsing
- Low-level filesystem isolation mechanics

### 7.2 PromptCompiler

Responsibilities:

- Translate supervisor intent into a structured worker task package.
- Constrain scope, allowed operations, and expected output.
- Produce instructions that are strict enough for machine evaluation later.

Required fields in the first version task package:

- `goal`
- `task_type`
- `scope`
- `workspace_mode`
- `allowed_tools`
- `expected_output_schema`
- `stop_conditions`
- `verification_expectations`
- `human_notes`

The PromptCompiler must not emit an open-ended freeform prompt alone. It must always produce both:

- Human-readable instructions for the worker
- Machine-readable metadata for orchestration

### 7.3 AgentAdapter

Responsibilities:

- Encapsulate how a specific worker is invoked.
- Normalize worker outputs into the orchestrator result format.
- Hide CLI, SDK, session, or transport details from the supervisor.

Version 1 adapters:

- `ClaudeAdapter`

Deferred adapters:

- `GeminiAdapter`
- `A2AAdapter`

The first Claude adapter should prefer CLI structured output, with optional later support for SDK-backed sessions and hooks.

### 7.4 WorkspaceManager

Responsibilities:

- Allocate execution workspaces.
- Enforce workspace mode policies.
- Track which run used which workspace.
- Prepare merge candidates for Codex review.

Supported modes in version 1:

- `isolated`: dedicated worktree or temporary copy, default for implementation work
- `shared`: explicit shared workspace collaboration, only when intentionally selected
- `readonly`: inspection and analysis only

Rules:

- Research, review, and inspection tasks default to `readonly`.
- Implementation tasks default to `isolated`.
- Shared writes require explicit selection and policy approval.

### 7.5 RunRecorder

Responsibilities:

- Persist all significant execution artifacts and events.
- Support replay, debugging, and later observability features.

Minimum stored data per run:

- run id
- task id
- parent task id
- from agent
- to agent
- workspace id
- input task package
- adapter invocation metadata
- stdout and stderr snapshots
- structured result payload
- modified files summary
- diff summary
- verification outputs
- timing
- cost if available
- final status
- next action recommendation

### 7.6 ResultEvaluator

Responsibilities:

- Judge whether a worker result is acceptable for the orchestrator.
- Distinguish execution failure from quality failure.
- Produce explicit next-step recommendations.

The ResultEvaluator can combine:

- hard rules
- schema validation
- diff heuristics
- optional model-based review

Version 1 should start with deterministic rules first and reserve model-based evaluation as an additive layer.

### 7.7 PolicyGate

Responsibilities:

- Prevent risky operations from silently proceeding.
- Guard transitions into shared workspaces and sensitive paths.
- Provide uniform safety rules across all workers.

Examples of guarded actions:

- writing to shared workspace
- touching protected files or directories
- running high-risk shell commands
- auto-merging large or cross-cutting diffs

## 8. Internal Data Model

The internal model should be mesh-ready even in a supervisor-first system.

### 8.1 Task

Required fields:

- `task_id`
- `parent_task_id`
- `origin`
- `assigned_agent`
- `goal`
- `task_type`
- `workspace_mode`
- `status`
- `priority`
- `expected_output_schema`
- `created_at`
- `updated_at`

### 8.2 Run

Required fields:

- `run_id`
- `task_id`
- `agent`
- `adapter`
- `workspace_id`
- `started_at`
- `ended_at`
- `status`
- `result_summary`

### 8.3 Event

Required fields:

- `event_id`
- `task_id`
- `run_id`
- `from_agent`
- `to_agent`
- `event_type`
- `timestamp`
- `payload`

Suggested event types:

- `task_created`
- `task_dispatched`
- `run_started`
- `worker_message`
- `artifact_created`
- `verification_completed`
- `policy_blocked`
- `evaluation_completed`
- `merge_requested`
- `merge_completed`
- `task_completed`
- `task_failed`

### 8.4 Artifact

Required fields:

- `artifact_id`
- `task_id`
- `run_id`
- `kind`
- `path_or_inline_data`
- `summary`

Artifact kinds in version 1:

- `prompt`
- `structured_result`
- `stdout`
- `stderr`
- `diff`
- `verification_log`
- `report`

## 9. Recommended End-to-End Flow

### 9.1 Standard Delegation Flow

1. Supervisor receives the user request.
2. Supervisor determines that Claude should execute a bounded subtask.
3. PromptCompiler emits a worker task package.
4. WorkspaceManager provisions an isolated workspace.
5. ClaudeAdapter invokes Claude in structured-output mode.
6. RunRecorder persists the stream of execution outputs.
7. ResultEvaluator classifies the result.
8. Supervisor chooses one of:
   - accept
   - retry
   - reroute
   - ask human
   - merge and continue
9. If accepted and changes exist, PolicyGate authorizes merge handling.
10. Codex merges or promotes the result into the next phase of work.

### 9.2 Shared Collaboration Flow

This path is intentionally rare.

1. Supervisor explicitly chooses `shared` workspace mode.
2. PolicyGate confirms the task is safe for shared writing.
3. Claude executes with tighter tool and scope constraints than in isolated mode.
4. ResultEvaluator still validates outputs before the supervisor proceeds.

Shared mode exists for convenience, not as the default execution style.

## 10. Claude Code Integration Strategy

Version 1 should use Claude Code through the simplest stable interface that supports structured orchestration:

- `claude -p`
- `--output-format json` or `stream-json`
- `--json-schema` when a strict response contract is required

Why this is the preferred first step:

- minimal orchestration overhead
- easy process isolation
- fast feedback loop for prompt and schema design
- simpler failure handling than long-lived daemon or bidirectional session designs

Deferred, but explicitly planned:

- session resume
- stream-driven progress updates
- SDK-based hooks
- deeper subagent integration

The first implementation should treat one worker task as one Claude run. Long-lived Claude sessions are an optimization for later versions, not a starting requirement.

## 11. Workspace Conflict Strategy

The orchestrator must assume that multiple workers may operate in parallel and that conflicts are normal.

Default rules:

- review and research work may read shared state but should not write it
- implementation work should use isolated workspaces
- only Codex promotes isolated results into the shared workspace
- workers do not directly merge each other's changes

Merge pipeline:

1. file-level screening
2. semantic consistency check between summary and diff
3. targeted verification
4. policy approval
5. merge or reject

This keeps the worker model simple and keeps responsibility for repository integrity in one place.

## 12. Failure Taxonomy

The system should not collapse all failures into a single terminal state.

Required failure classes:

- `invocation_error`: process launch, timeout, permission, transport, or schema handshake failure
- `execution_error`: worker ran but failed to complete the task
- `policy_block`: requested action was intentionally denied
- `quality_reject`: result exists but is not acceptable
- `merge_conflict`: result is acceptable but cannot be safely applied

Each failed or blocked run must include a recommended next action:

- `retry_same_agent`
- `retry_with_tighter_prompt`
- `reroute_other_agent`
- `ask_human`
- `discard_workspace`
- `promote_to_shared_merge`

This recommendation is advisory input to the Supervisor, not an autonomous final decision.

## 13. Security and Safety Boundaries

Version 1 safety assumptions:

- workers are powerful and must be constrained
- shared workspace writes are more dangerous than isolated writes
- vendor adapters should never bypass PolicyGate
- sensitive path protections must be centralized

Mandatory controls:

- allowed-tool restrictions per task
- workspace-mode-aware command and write restrictions
- explicit sensitive path denylist or protected-zone rules
- merge approval checkpoints

## 14. Evolution Path

### 14.1 Version 1

- local Codex supervisor
- Claude adapter
- default isolated workspaces
- structured run recording
- deterministic result evaluation
- policy-gated merge flow

### 14.2 Version 1.5

- Gemini adapter
- improved evaluator
- stream progress UI or terminal summaries
- better retry policies

### 14.3 Version 2

- A2A wrappers around internal agent endpoints
- optional direct agent-to-agent delegation
- richer event subscriptions
- stronger observability

### 14.4 Version 3

- partial decentralization
- remote worker support
- more reusable external interfaces

The key constraint is that version 1 must not require version 2 architecture to be useful.

## 15. Future Protocol Positioning

The long-term protocol stance is:

- use A2A for agent-to-agent interoperability
- use MCP for tool interoperability
- keep the internal orchestration model close enough to A2A concepts that future wrapping is straightforward

Concretely, this means internal events and task objects should already preserve:

- source agent
- destination agent
- task identity
- task lineage
- artifacts
- task state transitions

That choice minimizes rewrite cost when the system eventually exposes A2A-compatible endpoints.

## 16. Recommended Implementation Direction

The first implementation should be a small local orchestrator with these characteristics:

- Codex remains the default supervisor and final decision-maker.
- Claude Code is invoked through a strict adapter with structured output.
- Worker tasks execute in isolated workspaces by default.
- All runs are persisted with task, event, and artifact metadata.
- Shared workspace mutation is policy-gated and merge-mediated by Codex.
- Internal state is modeled in a mesh-ready way, but true mesh behavior is postponed.

## 17. Implementation Readiness

This spec is ready for implementation planning because it defines:

- the chosen architecture
- the component boundaries
- the internal data model
- the execution flow
- the failure model
- the safety model
- the evolution path

The next step should be a written implementation plan that breaks version 1 into milestones, interfaces, storage choices, adapter contracts, and verification checkpoints.
