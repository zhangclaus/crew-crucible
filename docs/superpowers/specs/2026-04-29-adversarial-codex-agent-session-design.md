# Adversarial Codex-Agent Session Design

- Date: 2026-04-29
- Status: Confirmed in conversation, ready for implementation planning
- Scope: Extend the existing local orchestrator from one-shot dispatch into a multi-round adversarial demand-service loop with governed skill evolution

## 1. Problem Statement

The current orchestrator can dispatch a single task from Codex to a worker agent, record the run, and evaluate the result once. That is useful for delegation, but it does not yet model the deeper workflow the user wants:

1. A demand-side Codex turns a user requirement into explicit acceptance criteria.
2. A worker-side agent attempts the task in a controlled workspace.
3. The demand side verifies, challenges, and searches for counterexamples.
4. The worker repairs or defends its answer.
5. The loop repeats until the requirement is accepted, blocked, or escalated.
6. The system learns reusable procedural knowledge from the session without blindly changing its long-term behavior.

The goal is not only to visualize Claude output. The goal is to make the interaction process observable, replayable, and improvable.

## 2. Chosen Direction

Use a Codex-to-Agent abstraction:

- The demand side is Codex in the current app/session.
- The worker side is an abstract `Agent`.
- The first concrete worker remains the existing Claude Code adapter.
- A later Codex adapter can be added without changing the session model.

The first version should implement a full automatic multi-round loop with bounded control:

```text
specify
→ execute
→ light_verify
→ challenge
→ repair
→ repeat until max_rounds / accepted / blocked
→ final_verify
→ accept / needs_human / generate pending skills
```

Validation is split into two levels:

- Per-round light verification checks for policy violations, obvious test failures, changed-file scope, and whether the worker provided evidence.
- Final hard verification runs project verification commands plus demand-side adversarial checks before the requirement is accepted.

Skill learning follows a Hermes-inspired model: sessions can generate skill candidates, but active skills are only changed through a governed candidate-to-approved flow.

## 3. Goals

- Convert one-shot dispatch into a durable adversarial session.
- Record each round of demand, execution, challenge, repair, verification, and decision.
- Support fully automatic multi-round execution with explicit maximum rounds and stop conditions.
- Combine existing project verification commands with Codex-generated adversarial checks.
- Persist learning artifacts as candidate skills that can later become active procedural memory.
- Keep the current local-first design: subprocess adapters, local `.orchestrator/` state, deterministic policy gates, and inspectable artifacts.
- Keep the architecture ready for a future Codex worker adapter and richer UI.

## 4. Non-Goals

- No model fine-tuning in the first version.
- No hosted SaaS control plane.
- No uncontrolled peer-to-peer agent mesh.
- No automatic promotion of generated skills into active runtime behavior without validation.
- No guarantee that the first version can directly launch another Codex App instance as a worker.
- No complex real-time streaming UI in the first implementation step.

## 5. References Borrowed From Hermes

Hermes Agent treats skills as on-demand knowledge documents that can be created or updated from experience, while keeping context usage bounded through progressive disclosure:

- Skills System: https://hermes-agent.nousresearch.com/docs/user-guide/features/skills
- Persistent Memory: https://hermes-agent.nousresearch.com/docs/user-guide/features/memory/

This design borrows the following ideas:

- Skills are procedural memory, not raw chat logs.
- Skills are loaded only when relevant.
- Skill files can use a `SKILL.md` structure with trigger conditions, procedure, pitfalls, and verification.
- Session history and curated memory are separate from reusable skills.

This design adds stricter governance:

- Generated skills first enter `pending`.
- Skills require validation and approval before becoming `active`.
- Active skills are versioned and reversible.
- Security scanning blocks secrets, prompt injection, dangerous commands, and policy bypass instructions.

## 6. Core Concepts

### 6.1 Demand-Side Codex

The demand side owns the requirement contract and final decision. Its responsibilities are:

- Convert the user goal into a requirement contract.
- Define acceptance criteria and failure criteria.
- Identify risks and edge cases.
- Generate verification plans.
- Generate adversarial challenges after each worker attempt.
- Decide whether to continue, repair, accept, or escalate.

### 6.2 Worker Agent

The worker side executes tasks and responds to challenges. In the first implementation, the worker is Claude Code through the existing adapter. Later, it can be Codex CLI, another Claude profile, Gemini, or an A2A-compatible agent.

The worker responsibilities are:

- Execute the assigned task in the allocated workspace.
- Return structured output.
- Explain changes and evidence.
- Repair work after challenge.
- State uncertainty or failure explicitly when blocked.

### 6.3 Adversarial Session

A session represents one user requirement from start to final decision. A session contains multiple turns. Each turn can include:

- task package
- worker result
- light verification
- demand-side challenge
- repair instruction
- final or interim decision

The session is the new durable object above the existing run model. Existing `RunRecord` remains useful as the execution unit for each worker invocation.

### 6.4 Skill Evolution

Skill evolution converts repeated or high-value session experience into reusable process knowledge.

The first version should generate candidate skills only at the end of a session, using the full evidence trail. Candidate skills are not active by default.

Lifecycle:

```text
session evidence
→ learning note
→ pending skill
→ security scan
→ validation check
→ approval
→ active skill
→ future matching sessions load it
```

## 7. Data Model

Add these records to `models.py` or a new focused module if the file becomes too broad.

### 7.1 SessionRecord

Fields:

- `session_id`
- `root_goal`
- `repo`
- `assigned_agent`
- `status`: `running`, `accepted`, `needs_human`, `failed`, `blocked`
- `max_rounds`
- `current_round`
- `acceptance_criteria`
- `failure_criteria`
- `verification_commands`
- `generated_checks`
- `active_skill_ids`
- `created_at`
- `updated_at`
- `ended_at`
- `final_summary`

### 7.2 TurnRecord

Fields:

- `turn_id`
- `session_id`
- `round_index`
- `phase`: `execute`, `light_verify`, `challenge`, `repair`, `final_verify`
- `run_id`
- `from_agent`
- `to_agent`
- `message`
- `decision`
- `created_at`

### 7.3 ChallengeRecord

Fields:

- `challenge_id`
- `session_id`
- `turn_id`
- `challenge_type`: `counterexample`, `missing_test`, `scope_risk`, `policy_risk`, `quality_risk`
- `question`
- `expected_evidence`
- `severity`

### 7.4 VerificationRecord

Fields:

- `verification_id`
- `session_id`
- `turn_id`
- `kind`: `command`, `policy`, `diff`, `generated_check`, `human`
- `command`
- `passed`
- `summary`
- `stdout_artifact`
- `stderr_artifact`

### 7.5 LearningNote

Fields:

- `learning_id`
- `session_id`
- `source_turn_ids`
- `pattern`
- `trigger_conditions`
- `recommended_skill_name`
- `evidence_summary`
- `confidence`

### 7.6 SkillRecord

Fields:

- `skill_id`
- `name`
- `version`
- `status`: `pending`, `active`, `rejected`, `archived`
- `source_session_id`
- `trigger_conditions`
- `path`
- `validation_summary`
- `approval_mode`: `automatic_strict`, `human`
- `created_at`
- `updated_at`

## 8. Storage Layout

Keep state local under `.orchestrator/`:

```text
.orchestrator/
  runs/
    <run_id>/
      run.json
      task.json
      result.json
      evaluation.json
      events.jsonl
      artifacts/
  sessions/
    <session_id>/
      session.json
      turns.jsonl
      challenges.jsonl
      verifications.jsonl
      learning.json
      final_report.json
  skills/
    active/
      <skill_name>/
        SKILL.md
        metadata.json
    pending/
      <skill_name>/
        SKILL.md
        metadata.json
        evidence.json
    rejected/
      <skill_name>/
        metadata.json
    index.json
```

Runs remain the low-level execution records. Sessions compose runs into a requirement-level story.

## 9. Session Flow

### 9.1 Specify

The demand-side Codex builds a requirement contract:

- user goal
- explicit acceptance criteria
- explicit failure criteria
- relevant active skills
- verification command candidates
- generated adversarial checks
- max rounds

For the first version, this can be deterministic plus prompt-driven:

- deterministic defaults for `max_rounds`, workspace mode, and required final verification
- prompt-compiled criteria and checks stored as structured JSON

### 9.2 Execute

The session engine creates a `TaskRecord` and calls the existing `Supervisor.dispatch`. This preserves the current adapter, workspace, policy, recorder, and evaluator boundaries.

### 9.3 Light Verify

After each run:

- inspect worker exit code
- inspect structured output
- inspect policy decision
- inspect changed files
- optionally run cheap verification commands if configured
- record a `VerificationRecord`

Light verification is allowed to trigger repair before a challenge is generated.

### 9.4 Challenge

If the result is not clearly accepted, the demand side generates one or more challenge records. Challenge examples:

- "What edge case would break this?"
- "Which acceptance criterion is not proven by the current tests?"
- "Did the worker edit files outside the declared scope?"
- "What command output proves the fix?"
- "Does the implementation handle the negative case?"

For MVP, generate a single high-value challenge per round to keep the loop readable.

### 9.5 Repair

The worker receives the prior result, verification evidence, and challenge. It must either:

- repair the implementation
- provide stronger evidence
- explain why the challenge is invalid
- declare itself blocked

Each repair is another dispatch run linked to the same session.

### 9.6 Stop Conditions

Stop the loop when one of these is true:

- final hard verification passes
- `max_rounds` is reached
- policy gate blocks the worker
- worker repeatedly fails the same class of issue
- verification commands cannot run
- the demand side marks the requirement as needing human review

### 9.7 Final Verify

Final verification runs after the adversarial loop. It must combine:

- project verification commands
- deterministic policy checks
- diff/scope review
- Codex-generated adversarial checks

Only final verification can mark the session as `accepted`.

If final verification fails and rounds remain, the session can perform one repair round. If no rounds remain, mark `needs_human` with evidence.

### 9.8 Learn

After final decision, the session engine creates learning notes. If the session produced a reusable workflow, failure pattern, or verification trick, it creates a pending skill.

Generated skills should follow this structure:

```text
---
name: <skill-name>
description: <when this skill helps>
version: 0.1.0
---

# <Skill Title>

## When to Use

## Procedure

## Pitfalls

## Verification

## Source Evidence
```

The `Source Evidence` section should reference session id and summarized evidence, not raw logs or secrets.

## 10. CLI Surface

Add session commands while keeping existing commands intact.

```bash
orchestrator session start \
  --repo /path/to/repo \
  --goal "implement X" \
  --assigned-agent claude \
  --workspace-mode isolated \
  --max-rounds 3 \
  --verification-command ".venv/bin/python -m pytest -v"
```

Inspection:

```bash
orchestrator sessions list --repo /path/to/repo
orchestrator sessions show --repo /path/to/repo --session-id <id>
```

Skill management:

```bash
orchestrator skills list --repo /path/to/repo
orchestrator skills show --repo /path/to/repo --skill-id <id>
orchestrator skills approve --repo /path/to/repo --skill-id <id>
orchestrator skills reject --repo /path/to/repo --skill-id <id> --reason "too broad"
```

The first implementation can output JSON only. A local UI can be built on the same records later.

## 11. UI Direction

The UI should show process, not just stdout:

- Session list with status, round count, final decision, and assigned agent.
- Two-column turn view: demand-side challenge on the left, worker response on the right.
- Evidence panel for tests, command output, changed files, and policy decisions.
- Skill evolution panel showing learning notes and pending skills.
- Control panel for accepting, retrying, approving skills, or marking human review.

The first implementation does not need the UI. The recorded session format should make the UI straightforward.

## 12. Policy and Safety

The policy layer must govern three areas:

1. Worker execution
   - Keep existing workspace and command guards.
   - Default implementation work to isolated workspaces.
   - Require explicit opt-in for shared writes.

2. Verification commands
   - Treat verification commands as user/project-supplied commands.
   - Record stdout and stderr as artifacts.
   - Avoid auto-running destructive commands.

3. Skill evolution
   - Reject skills containing secrets or credentials.
   - Reject prompt-injection-like instructions.
   - Reject instructions that bypass policy gates.
   - Prefer narrow trigger conditions.
   - Require approval before activation unless a future strict automatic policy is implemented.

## 13. Testing Strategy

Unit tests:

- session record serialization
- session recorder persistence
- turn/challenge/verification append behavior
- session engine stop conditions
- final verification decision logic
- skill candidate generation
- skill security scanning
- skill approval/rejection flow

Integration tests:

- fake adapter completes after one round
- fake adapter fails first and repairs second
- final verification failure triggers repair when rounds remain
- max rounds produces `needs_human`
- pending skill is generated from learning evidence but not active automatically

Manual smoke:

- Run `orchestrator session start` on a tiny fixture repo.
- Confirm `.orchestrator/sessions/<id>/` contains session, turns, verification, and learning artifacts.
- Confirm existing `orchestrator runs list/show` still works.

## 14. Delivery Phases

### Phase 1: Session Records and CLI

Add models, recorder, and JSON inspection commands:

- `session start` can initially run a single execute/final-verify cycle.
- `sessions list/show` reads persisted records.

### Phase 2: Multi-Round Engine

Add automatic bounded loop:

- light verification
- challenge generation
- repair dispatch
- stop conditions
- final verification

### Phase 3: Skill Evolution

Add governed procedural memory:

- learning notes
- pending skill generation
- security scan
- approve/reject commands
- active skill loading into future session contracts

### Phase 4: Visual Process UI

Build a local browser UI on top of session records:

- session list
- turn timeline
- evidence panel
- skill evolution panel

## 15. Open Decisions Resolved

- Worker abstraction: support `Agent`; implement first with Claude, later Codex adapter.
- Control mode: full automatic multi-round loop with maximum rounds and stop conditions.
- Validation mode: combine project verification commands with demand-side adversarial checks.
- Learning mode: Hermes-inspired skill evolution with pending-to-active governance.
- Acceptance rule: only final hard verification can accept a requirement.

## 16. Acceptance Criteria for This Feature

The implementation is successful when:

- A user can start an adversarial session from the CLI.
- The session runs at least one worker execution through the existing adapter path.
- The session records turns, verification evidence, and final decision.
- The loop can perform multiple rounds when the first attempt fails or is challenged.
- The final decision distinguishes accepted, failed, blocked, and needs-human outcomes.
- The system can generate a pending skill from session learning evidence.
- Pending skills do not affect future sessions until approved.
- Existing dispatch and run inspection commands continue to pass tests.
