---
description: Multi-agent adversarial code review for Claude Code
disable-model-invocation: false
---

# Agent Crucible

Use this command to perform adversarial code review with multiple agents.

## How It Works

1. One agent implements the code
2. Another agent actively tries to break it
3. If issues found, challenge the implementer
4. Implementer fixes and defends
5. Repeat until verification passes

## Usage

### Simple Review
```
/agent-crucible 帮我审查这个模块的代码质量
```

### With Specific Goal
```
/agent-crucible Add user authentication with email verification
```

## IMPORTANT: When to Use Adversarial Verification

**You MUST use `crew_run()` for these tasks:**
- Implementing new features
- Fixing bugs
- Code review
- Security audit
- Refactoring with behavioral changes

**You can do directly (without crew_run):**
- Cleaning up redundant code
- Querying information
- Reading files
- Simple formatting changes

## Available MCP Tools

### Core Tools (Default Mode)
- `crew_run(repo, goal)` — Start adversarial review
- `crew_run(repo, goal, verification_commands=["pytest"])` — With verification
- `crew_job_status(job_id)` — Poll job status
- `crew_cancel(job_id)` — Cancel a running job
- `crew_verify(crew_id, command)` — Run verification command
- `crew_accept(crew_id, summary)` — Accept and finalize results

## Worker Templates

| Template | Authority | Use Case |
|----------|-----------|----------|
| `targeted-code-editor` | source_write | Implementing code changes |
| `repo-context-scout` | readonly | Exploring codebase |
| `patch-risk-auditor` | readonly | Reviewing changes for risks |
| `verification-failure-analyst` | source_write | Diagnosing test failures |
| `frontend-developer` | source_write | Frontend changes (UI, components, styles) |
| `backend-developer` | source_write | Backend changes (API, services, database) |
| `test-writer` | source_write | Writing and updating tests |

## Examples

### Implement Feature (MUST use crew_run)
```
crew_run(repo="/path/to/project", goal="Add user authentication", verification_commands=["pytest"])
```

### Fix Bug (MUST use crew_run)
```
crew_run(repo="/path/to/project", goal="Fix login validation error", verification_commands=["pytest"])
```

### Code Review (MUST use crew_run)
```
crew_run(repo="/path/to/project", goal="Review authentication module for security issues")
```

### Clean Up (Can do directly)
```
Just do it directly, no need for crew_run()
```

## Notes

- **Default mode** uses V4CrewRunner Python loop (stable, recommended)
- **Supervisor mode** (`supervisor_mode=True`) is experimental and not recommended for production use
- **Always use crew_run() for implementation tasks** to trigger adversarial verification
