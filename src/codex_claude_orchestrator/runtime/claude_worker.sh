#!/usr/bin/env bash
# claude_worker.sh - Wrapper script for interactive Claude Code CLI worker sessions.
#
# Usage: claude_worker.sh <work_dir>
#
# This script sets up a file-based inbox/outbox protocol between the orchestrator
# and a Claude Code CLI session. The orchestrator writes mission/task files into
# .inbox/ before launching this script. Claude reads them, does work, writes
# results to .outbox/result.json, and prints a sentinel line to signal completion.

set -euo pipefail

if [[ $# -lt 1 ]]; then
    echo "Usage: claude_worker.sh <work_dir>" >&2
    exit 1
fi

WORK_DIR="$1"

if [[ ! -d "$WORK_DIR" ]]; then
    echo "Error: work_dir '$WORK_DIR' does not exist" >&2
    exit 1
fi

# Create inbox and outbox directories
mkdir -p "$WORK_DIR/.inbox" "$WORK_DIR/.outbox"

# --- Build the system prompt ---
SYSTEM_PROMPT_FILE="$WORK_DIR/.inbox/.system_prompt.md"

cat > "$SYSTEM_PROMPT_FILE" <<'SYSTEM_PROMPT_EOF'
# Worker Session Protocol

You are a worker agent running inside an orchestrated multi-agent system.
You communicate with the orchestrator through a file-based inbox/outbox protocol.

## Reading Your Instructions

1. **Read `.inbox/task.md`** for your current task description and requirements.
2. **Read `.inbox/mission.md`** for broader background context about the overall mission.
3. **Consult `.crew-history/index.md`** when you need an overview of previous turns and their outcomes.
4. **Read specific `.crew-history/turn-N-result.json` files** when you need detailed information about a particular prior turn.

## Doing Your Work

- Complete the task described in `.inbox/task.md` using the repository at your working directory.
- Follow the mission context from `.inbox/mission.md`.
- Use history files in `.crew-history/` to avoid repeating work and to build on previous results.

## Reporting Your Results

When you have completed your task (or determined you cannot complete it), write a JSON file to `.outbox/result.json` with the following fields:

```json
{
  "crew_id": "<the crew ID>",
  "worker_id": "<your worker ID>",
  "turn_id": "<the turn number>",
  "status": "completed | failed | partial",
  "summary": "<brief summary of what you did>",
  "changed_files": ["<list of files you modified>"],
  "verification": "<output of any verification commands you ran>",
  "risks": "<any risks, caveats, or concerns>",
  "next_suggested_action": "<what should happen next>"
}
```

## Signaling Completion

After writing `.outbox/result.json`, print the following sentinel line on stdout:

```
<<<WORKER_TURN_DONE>>>
```

This line tells the orchestrator that you have finished your turn and the result file is ready to read.
Do NOT print this line until `.outbox/result.json` has been fully written and flushed.
SYSTEM_PROMPT_EOF

# --- Build the initial user message in a temp file ---
# Using a temp file avoids shell injection from mission/task file contents.
MSG_FILE=$(mktemp)
trap 'rm -f "$MSG_FILE"' EXIT

printf '%s\n' "You are a worker agent. Your working directory is: $WORK_DIR" > "$MSG_FILE"

if [[ -f "$WORK_DIR/.inbox/mission.md" ]]; then
    printf '\n%s\n' "## Mission Context" >> "$MSG_FILE"
    cat "$WORK_DIR/.inbox/mission.md" >> "$MSG_FILE"
fi

if [[ -f "$WORK_DIR/.inbox/task.md" ]]; then
    printf '\n%s\n' "## Your Task" >> "$MSG_FILE"
    cat "$WORK_DIR/.inbox/task.md" >> "$MSG_FILE"
fi

printf '\n%s\n' "Please complete your task following the file protocol described in the system prompt. When done, write your result to .outbox/result.json and print <<<WORKER_TURN_DONE>>>." >> "$MSG_FILE"

# --- Launch Claude Code CLI ---
# Read system prompt and message from files to avoid shell interpretation
SYSTEM_PROMPT=$(cat "$SYSTEM_PROMPT_FILE")
INITIAL_MESSAGE=$(cat "$MSG_FILE")

exec claude \
    --dangerously-skip-permissions \
    --system-prompt "$SYSTEM_PROMPT" \
    "$INITIAL_MESSAGE"
