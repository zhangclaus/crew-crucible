# Direct Claude Window Design

## Goal

Make the default human-facing workflow open a real Claude CLI interaction window from the Codex App, instead of forcing users to observe raw tmux orchestration panes.

## Direction

`orchestrator claude open` opens macOS Terminal in the target repository, passes a task prompt into interactive `claude`, copies the same prompt to the clipboard as a fallback, and records prompt/script/transcript paths under `.orchestrator/claude-open/`.

## Rationale

- Direct Claude CLI is easier to trust and understand than raw tmux worker panes.
- Codex can still supervise by reading saved artifacts and asking for verification.
- tmux remains available for advanced multi-window/background workflows.

## First Version

Command:

```bash
orchestrator claude open --repo /path/to/repo --goal "..." --workspace-mode readonly
```

Behavior:

- Validate and resolve the repo path.
- Create `.orchestrator/claude-open/<id>/prompt.txt`.
- Create `.orchestrator/claude-open/<id>/open.zsh`.
- Copy the prompt to clipboard in the launched Terminal session as a fallback.
- Start `claude` interactively through `script` with the prompt as the initial message, so the user can watch and continue the conversation while transcript logging is available.
- Return JSON with the run id, prompt path, script path, transcript path, and open command.

Safety:

- `readonly` prompt explicitly tells Claude not to modify files.
- The launcher does not bypass Claude CLI permissions.
- No shell command is built through unescaped string interpolation.
