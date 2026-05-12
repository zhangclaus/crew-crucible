# Agent Crucible Plugin

Multi-agent code review with adversarial verification for Claude Code.

## What It Does

1. One agent implements the code
2. Another agent actively tries to break it
3. The implementer must defend against challenges
4. This cycle repeats until verification passes

## Available Tools

### Core Tools
- `crew_run(repo, goal, supervisor_mode)` — Start adversarial review
- `crew_job_status(job_id)` — Poll job status
- `crew_cancel(job_id)` — Cancel a running job
- `crew_verify(crew_id, command)` — Run verification command
- `crew_accept(crew_id, summary)` — Accept and finalize results

### Supervisor Mode Tools (when supervisor_mode=True)
- `crew_spawn(repo, crew_id, label, mission)` — Spawn worker agent
- `crew_observe(repo, crew_id, worker_id)` — Observe worker output
- `crew_changes(crew_id)` — View changed files
- `crew_diff(crew_id, file)` — View diff for specific file
- `crew_stop_worker(repo, crew_id, worker_id)` — Stop specific worker
- `crew_challenge(crew_id, summary, task_id)` — Challenge worker with issues

## Worker Templates

| Template | Authority | Use Case |
|----------|-----------|----------|
| `targeted-code-editor` | source_write | Implementing code changes |
| `repo-context-scout` | readonly | Exploring codebase |
| `patch-risk-auditor` | readonly | Reviewing changes for risks |
| `verification-failure-analyst` | source_write | Diagnosing test failures |
| `frontend-developer` | source_write | Frontend changes |
| `backend-developer` | source_write | Backend changes |
| `test-writer` | source_write | Writing tests |

## Usage

### Simple Review (Default Mode)
```
crew_run(repo="/path/to/project", goal="Add user authentication")
```

### Supervisor Mode (Direct Control)
```
crew_run(repo="/path/to/project", goal="Add user auth", supervisor_mode=True)
```

Then orchestrate manually:
```
crew_spawn(repo="/path", crew_id="crew-1", label="backend-developer", mission="Implement auth API")
crew_observe(repo="/path", crew_id="crew-1", worker_id="worker-1")
crew_challenge(crew_id="crew-1", summary="Missing input validation")
crew_verify(crew_id="crew-1", command="pytest")
crew_accept(crew_id="crew-1", summary="All tests pass")
```
