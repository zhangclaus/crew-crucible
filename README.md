# Agent Crucible

Multi-agent code review with adversarial verification for Claude Code. One agent implements, another actively tries to break it.

## The Problem

You ask Claude Code to implement a feature. It writes 500 lines, runs the tests, says "done." You merge it. Two days later you find a subtle race condition it never considered.

**One AI agent reviewing its own work has blind spots.** It optimizes for "make the tests pass," not "find what could go wrong." It won't challenge its own assumptions.

Agent Crucible solves this by pitting multiple Claude CLI instances against each other — one implements, another actively tries to break it. The implementer has to defend its code against a hostile reviewer. Bad code doesn't survive.

## How It Works

![Architecture Flow](liuchengtu.png)

1. **User** sends a task request (e.g. "Add user registration with email verification")
2. **LongTaskSupervisor** drives multi-stage execution:
   - **Stage 1**: Think — brainstorming and planning
   - **Stage 2**: PlanAdversary — validate plan quality
   - **Stage 3**: Do — implement with parallel workers
   - Each stage: Workers execute → adversarial agent reviews → challenge/repair loop → merge results
3. **Worktree Isolation** — each worker operates in an independent git worktree
4. **Event Store** (SQLite) — persists all events for full replay

The key insight: **the Reviewer is adversarial**. It doesn't just check "do tests pass?" — it looks for edge cases, race conditions, security holes, and architectural problems. When it finds issues, it emits targeted challenges to specific workers. The Implementer must fix them and prove the fix works.

## Why Multiple Agents?

| Single Claude CLI | Agent Crucible |
|---|---|
| Reviews its own code (blind spots) | Separate reviewer with fresh context |
| One long context window (polluted) | Isolated contexts per role |
| Sequential: write → test → done | Adversarial: write → attack → defend → verify |
| "Tests pass, ship it" | "Tests pass, but what about X?" |

## Quick Start

### 1. Install Plugin

```bash
# Add marketplace
claude plugin marketplace add zhangclaus/agent-crucible

# Install plugin
claude plugin install agent-crucible

# Restart Claude Code
```

### 2. Use in Claude Code

After restarting Claude Code, the plugin is automatically loaded. Use the `/agent-crucible` skill:

```
/agent-crucible 帮我审查这个模块的代码质量
```

Or directly call MCP tools:

```python
# Simple review
crew_run(repo="/path/to/project", goal="Add user authentication")

# Supervisor mode (direct control)
crew_run(repo="/path/to/project", goal="Add user auth", supervisor_mode=True)
```

### 3. CLI (Alternative)

```bash
# Install package
pip install -e .

# Check prerequisites
acr doctor

# Run adversarial code review
acr crew run \
  --repo /path/to/your/project \
  --goal "Add user registration" \
  --verification-command "pytest" \
  --max-rounds 3
```

## Requirements

- Python >= 3.11
- [Claude CLI](https://docs.anthropic.com/en/docs/claude-code) (Anthropic's Claude Code)
- tmux

## Features

- **Adversarial Verification** — Reviewer actively attacks code; Implementer defends; up to 3 challenge/repair rounds
- **AI Supervisor Mode** — Supervisor agent directly controls workers via MCP tools
- **Long Task Supervisor** — Multi-stage execution with dynamic planning for complex tasks
- **Git Worktree Isolation** — Each worker gets an independent worktree; no file conflicts
- **Event-Sourced Audit Trail** — Every state change recorded in SQLite; full replay capability
- **MCP Server** — Integrates with Claude Code as native MCP tools
- **Worker Templates** — Predefined roles for common tasks (frontend, backend, test, review)

## MCP Tools

### Core Tools
| Tool | Description |
|------|-------------|
| `crew_run` | Start a non-blocking review job |
| `crew_job_status` | Poll job status with delta tracking |
| `crew_cancel` | Cancel a running job |
| `crew_verify` | Run a verification command |
| `crew_accept` | Accept and finalize results |

### Supervisor Mode Tools
| Tool | Description |
|------|-------------|
| `crew_spawn` | Spawn worker agent with template or custom label |
| `crew_observe` | Observe worker output (structured, not raw) |
| `crew_changes` | View changed files across all workers |
| `crew_diff` | View diff for specific file |
| `crew_stop_worker` | Stop specific worker |
| `crew_challenge` | Challenge worker with issues |

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

## Testing

```bash
# Run all tests
pytest

# Run specific module tests
pytest tests/v4/ -v
pytest tests/mcp_server/ -v
```

## License

MIT
