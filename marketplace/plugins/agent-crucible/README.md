# Agent Crucible Plugin for Claude Code

Multi-agent code review with adversarial verification. One agent implements, another actively tries to break it.

## Installation

```bash
claude plugin install agent-crucible
```

Or manually:
1. Clone this repository
2. Copy `plugin/agent-crucible/` to `~/.claude/plugins/agent-crucible/`

## What You Get

- `/agent-crucible` skill for code review
- MCP tools for multi-agent orchestration
- Worker templates for common roles
- Supervisor mode for direct control

## Quick Start

After installation, use in Claude Code:

```
# Simple review
crew_run(repo="/path/to/project", goal="Add user authentication")

# Supervisor mode (direct control)
crew_run(repo="/path/to/project", goal="Add user auth", supervisor_mode=True)
```

## Features

- **Adversarial Verification** — Reviewer actively attacks code
- **Worker Templates** — Predefined roles for common tasks
- **Supervisor Mode** — Direct control over workers
- **Structured Output** — Compressed, actionable feedback
- **Delta Polling** — Minimal context usage

## License

MIT
