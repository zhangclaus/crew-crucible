# Interactive Worker REPL Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replace headless `claude -p` workers with interactive Claude Code CLI sessions using file-based communication (inbox/outbox) and lazy-loaded history (index.md + per-turn result files).

**Architecture:** Workers launch via a wrapper script that starts `claude --dangerously-skip-permissions` in a tmux session. The orchestrator writes task files to an inbox directory, sends a tmux trigger, and watches an outbox directory for structured JSON results. History is maintained as per-turn result files with an index.md for navigation.

**Tech Stack:** Python 3.11+, bash (wrapper script), tmux, pytest

---

## File Structure

| Action | File | Responsibility |
|--------|------|----------------|
| Create | `src/codex_claude_orchestrator/runtime/claude_worker.sh` | Wrapper script: launches interactive claude with system prompt |
| Create | `src/codex_claude_orchestrator/workers/history_manager.py` | Manages history files and index.md |
| Create | `tests/workers/test_history_manager.py` | Tests for HistoryManager |
| Modify | `src/codex_claude_orchestrator/runtime/native_claude_session.py` | `start()` uses wrapper; `send()` writes files; `observe()` watches outbox |
| Modify | `tests/runtime/test_native_claude_session.py` | Update tests for new behavior |
| Modify | `src/codex_claude_orchestrator/workers/pool.py` | Pass work_dir; save history after turns |
| Modify | `tests/workers/test_pool.py` | Update tests for new behavior |
| Modify | `src/codex_claude_orchestrator/v4/adapters/tmux_claude.py` | `deliver_turn()` writes mission/task; `watch_turn()` watches outbox |
| Modify | `tests/v4/test_tmux_claude_adapter.py` | Update tests for new behavior |

---

### Task 1: Create `claude_worker.sh` Wrapper Script

**Files:**
- Create: `src/codex_claude_orchestrator/runtime/claude_worker.sh`

The wrapper script is the entry point for each worker. It creates the inbox/outbox directories, writes the system prompt, and launches interactive `claude` with `--dangerously-skip-permissions`.

- [ ] **Step 1: Create the wrapper script**

```bash
#!/usr/bin/env bash
# claude_worker.sh — Launch an interactive Claude Code worker session.
#
# Usage: claude_worker.sh <work_dir>
#
# The orchestrator writes .inbox/mission.md and .inbox/task.md before
# invoking this script.  Claude reads those files, executes the task,
# and writes .outbox/result.json when done.

set -euo pipefail

WORK_DIR="${1:?Usage: claude_worker.sh <work_dir>}"
INBOX="$WORK_DIR/.inbox"
OUTBOX="$WORK_DIR/.outbox"

mkdir -p "$INBOX" "$OUTBOX"

SYSTEM_PROMPT_FILE="$INBOX/.system_prompt.md"

cat > "$SYSTEM_PROMPT_FILE" <<'PROMPT'
You are a worker agent managed by an orchestrator.

## File Protocol

- **Task file:** `.inbox/task.md` — your current task (read this first)
- **Mission file:** `.inbox/mission.md` — background context (read if needed)
- **History index:** `.crew-history/index.md` — summary of all previous turns
- **History details:** `.crew-history/turn-N-result.json` — detailed results from previous turns

## Execution Flow

1. Read `.inbox/task.md` for your current task
2. If you need context from previous turns:
   - First read `.crew-history/index.md` for an overview
   - Then read specific `.crew-history/turn-N-result.json` files as needed
   - Use grep/read tools to search for relevant information
3. Execute the task
4. Write your result to `.outbox/result.json`

## Result Format

`.outbox/result.json` must be a JSON object with these fields:

```json
{
  "crew_id": "...",
  "worker_id": "...",
  "turn_id": "...",
  "status": "completed|failed|partial",
  "summary": "One sentence describing what was done",
  "changed_files": ["file1.py", "file2.py"],
  "verification": "How the result was verified",
  "risks": ["risk1", "risk2"],
  "next_suggested_action": "What should happen next"
}
```

When you have finished writing the result file, print exactly:
<<<WORKER_TURN_DONE>>>
PROMPT

# Read mission and task files (may not exist on first run)
MISSION=""
TASK=""
[ -f "$INBOX/mission.md" ] && MISSION=$(cat "$INBOX/mission.md")
[ -f "$INBOX/task.md" ] && TASK=$(cat "$INBOX/task.md")

# Compose the initial message
INITIAL_MSG=""
[ -n "$MISSION" ] && INITIAL_MSG="$MISSION"
[ -n "$TASK" ] && INITIAL_MSG="${INITIAL_MSG}

${TASK}"

# Launch interactive Claude with the system prompt
exec claude \
  --dangerously-skip-permissions \
  --system-prompt "$(cat "$SYSTEM_PROMPT_FILE")" \
  $INITIAL_MSG
```

- [ ] **Step 2: Make the script executable**

```bash
chmod +x src/codex_claude_orchestrator/runtime/claude_worker.sh
```

- [ ] **Step 3: Verify the script syntax**

```bash
bash -n src/codex_claude_orchestrator/runtime/claude_worker.sh
```

Expected: no output (syntax valid)

- [ ] **Step 4: Include in package data**

Edit `pyproject.toml` to include the shell script in package data:

```toml
[tool.setuptools.package-data]
"codex_claude_orchestrator" = [
    "agent_packs/builtin/capabilities/*.md",
    "agent_packs/builtin/protocols/*.md",
    "skills/*.md",
    "runtime/*.sh",
]
```

- [ ] **Step 5: Commit**

```bash
git add src/codex_claude_orchestrator/runtime/claude_worker.sh pyproject.toml
git commit -m "feat: add claude_worker.sh wrapper script for interactive workers"
```

---

### Task 2: Create `HistoryManager`

**Files:**
- Create: `src/codex_claude_orchestrator/workers/history_manager.py`
- Create: `tests/workers/test_history_manager.py`

The HistoryManager maintains per-turn result files and an index.md for lazy-loaded context.

- [ ] **Step 1: Write the failing tests**

```python
# tests/workers/test_history_manager.py
import json
from pathlib import Path

from codex_claude_orchestrator.workers.history_manager import HistoryManager


def test_init_creates_history_dir(tmp_path: Path):
    work_dir = tmp_path / "work"
    work_dir.mkdir()
    hm = HistoryManager(work_dir=work_dir)
    assert (work_dir / ".crew-history").is_dir()


def test_save_turn_result_creates_file(tmp_path: Path):
    work_dir = tmp_path / "work"
    work_dir.mkdir()
    hm = HistoryManager(work_dir=work_dir)

    result = {
        "crew_id": "crew-1",
        "worker_id": "worker-1",
        "turn_id": "turn-1",
        "status": "completed",
        "summary": "Implemented module A",
        "changed_files": ["a.py", "b.py"],
        "verification": "tests pass",
        "risks": [],
        "next_suggested_action": "Implement module B",
    }
    path = hm.save_turn_result(turn_number=1, result=result)

    assert path.exists()
    assert path.name == "turn-1-result.json"
    loaded = json.loads(path.read_text())
    assert loaded["summary"] == "Implemented module A"


def test_update_index_creates_and_updates(tmp_path: Path):
    work_dir = tmp_path / "work"
    work_dir.mkdir()
    hm = HistoryManager(work_dir=work_dir)

    hm.update_index(
        turn_number=1,
        task="Analyze requirements",
        status="completed",
        summary="Output requirements doc",
        changed_files=["requirements.md"],
    )
    index = (work_dir / ".crew-history" / "index.md").read_text()
    assert "turn 1" in index.lower() or "Turn 1" in index
    assert "Analyze requirements" in index
    assert "requirements.md" in index

    hm.update_index(
        turn_number=2,
        task="Design architecture",
        status="completed",
        summary="Chose方案B",
        changed_files=["arch.md"],
    )
    index = (work_dir / ".crew-history" / "index.md").read_text()
    assert "Turn 1" in index or "turn 1" in index
    assert "Turn 2" in index or "turn 2" in index
    assert "Design architecture" in index


def test_update_index_tracks_status(tmp_path: Path):
    work_dir = tmp_path / "work"
    work_dir.mkdir()
    hm = HistoryManager(work_dir=work_dir)

    hm.update_index(turn_number=1, task="T1", status="completed", summary="done", changed_files=[])
    hm.update_index(turn_number=2, task="T2", status="failed", summary="error", changed_files=[])

    index = (work_dir / ".crew-history" / "index.md").read_text()
    assert "completed" in index
    assert "failed" in index


def test_list_turns_returns_sorted_numbers(tmp_path: Path):
    work_dir = tmp_path / "work"
    work_dir.mkdir()
    hm = HistoryManager(work_dir=work_dir)

    hm.save_turn_result(turn_number=3, result={"summary": "c"})
    hm.save_turn_result(turn_number=1, result={"summary": "a"})
    hm.save_turn_result(turn_number=2, result={"summary": "b"})

    turns = hm.list_turns()
    assert turns == [1, 2, 3]
```

- [ ] **Step 2: Run tests to verify they fail**

```bash
pytest tests/workers/test_history_manager.py -v
```

Expected: FAIL — `ModuleNotFoundError: No module named 'codex_claude_orchestrator.workers.history_manager'`

- [ ] **Step 3: Implement `HistoryManager`**

```python
# src/codex_claude_orchestrator/workers/history_manager.py
"""Manage per-turn result files and index.md for lazy-loaded worker context."""

from __future__ import annotations

import json
import re
from pathlib import Path


class HistoryManager:
    def __init__(self, *, work_dir: Path):
        self._history_dir = work_dir / ".crew-history"
        self._history_dir.mkdir(parents=True, exist_ok=True)
        self._index_path = self._history_dir / "index.md"

    @property
    def history_dir(self) -> Path:
        return self._history_dir

    @property
    def index_path(self) -> Path:
        return self._index_path

    def save_turn_result(self, *, turn_number: int, result: dict) -> Path:
        """Save a turn result to history. Returns the path."""
        path = self._history_dir / f"turn-{turn_number}-result.json"
        path.write_text(json.dumps(result, indent=2, ensure_ascii=False), encoding="utf-8")
        return path

    def update_index(
        self,
        *,
        turn_number: int,
        task: str,
        status: str,
        summary: str,
        changed_files: list[str],
    ) -> None:
        """Append a turn entry to the index.md file."""
        if not self._index_path.exists():
            self._write_index_header()

        entry = (
            f"| {turn_number} | {_truncate(task, 60)} | {status} "
            f"| {_truncate(summary, 60)} | {', '.join(changed_files) or '—'} |\n"
        )
        with self._index_path.open("a", encoding="utf-8") as f:
            f.write(entry)

    def list_turns(self) -> list[int]:
        """Return sorted list of turn numbers that have result files."""
        turns = []
        for p in self._history_dir.glob("turn-*-result.json"):
            match = re.match(r"turn-(\d+)-result\.json", p.name)
            if match:
                turns.append(int(match.group(1)))
        return sorted(turns)

    def read_turn_result(self, turn_number: int) -> dict | None:
        """Read a specific turn result, or None if not found."""
        path = self._history_dir / f"turn-{turn_number}-result.json"
        if not path.exists():
            return None
        return json.loads(path.read_text(encoding="utf-8"))

    def _write_index_header(self) -> None:
        header = (
            "# Crew Work History\n\n"
            "| Turn | Task | Status | Summary | Changed Files |\n"
            "|------|------|--------|---------|---------------|\n"
        )
        self._index_path.write_text(header, encoding="utf-8")


def _truncate(text: str, max_len: int) -> str:
    text = text.replace("\n", " ").strip()
    return text[:max_len - 3] + "..." if len(text) > max_len else text
```

- [ ] **Step 4: Run tests to verify they pass**

```bash
pytest tests/workers/test_history_manager.py -v
```

Expected: all 5 tests PASS

- [ ] **Step 5: Commit**

```bash
git add src/codex_claude_orchestrator/workers/history_manager.py tests/workers/test_history_manager.py
git commit -m "feat: add HistoryManager for per-turn result files and index.md"
```

---

### Task 3: Modify `NativeClaudeSession`

**Files:**
- Modify: `src/codex_claude_orchestrator/runtime/native_claude_session.py`
- Modify: `tests/runtime/test_native_claude_session.py`

Change `start()` to use the wrapper script, `send()` to write inbox files + tmux trigger, `observe()` to watch outbox result file.

- [ ] **Step 1: Write failing tests for new behavior**

Add these tests to `tests/runtime/test_native_claude_session.py`:

```python
import json


def test_start_uses_wrapper_script(tmp_path: Path):
    """start() should create tmux session running claude_worker.sh."""
    repo_root = tmp_path / "repo"
    repo_root.mkdir()
    transcript = tmp_path / "transcript.txt"
    runner = FakeTmuxRunner()
    session = NativeClaudeSession(
        tmux="tmux",
        runner=runner,
        session_name_factory=lambda worker_id: f"crew-1-{worker_id}",
    )

    started = session.start(
        repo_root=repo_root,
        worker_id="worker-explorer",
        role="explorer",
        instructions="Read only.",
        transcript_path=transcript,
    )

    commands = [call[0] for call in runner.calls]
    # Should create tmux session
    assert any(c[:4] == ["tmux", "new-session", "-d", "-s"] for c in commands)
    # Should send-keys with claude_worker.sh
    send_keys_cmds = [c for c in commands if c[:2] == ["tmux", "send-keys"]]
    assert send_keys_cmds
    assert "claude_worker.sh" in send_keys_cmds[-1][4] if len(send_keys_cmds[-1]) > 4 else False
    # Should return work_dir
    assert "work_dir" in started


def test_send_writes_inbox_files(tmp_path: Path):
    """send() should write .inbox/task.md and send tmux trigger."""
    repo_root = tmp_path / "repo"
    repo_root.mkdir()
    work_dir = tmp_path / "work"
    work_dir.mkdir()
    (work_dir / ".inbox").mkdir()
    (work_dir / ".outbox").mkdir()
    transcript = tmp_path / "transcript.txt"
    runner = FakeTmuxRunner()
    session = NativeClaudeSession(
        tmux="tmux",
        runner=runner,
        session_name_factory=lambda worker_id: f"crew-1-{worker_id}",
    )

    session.start(
        repo_root=repo_root,
        worker_id="worker-explorer",
        role="explorer",
        instructions="Read only.",
        transcript_path=transcript,
    )

    result = session.send(
        terminal_pane="crew-1-worker-explorer:claude.0",
        message="Do the task.",
        work_dir=work_dir,
    )

    # Task file should be written
    task_file = work_dir / ".inbox" / "task.md"
    assert task_file.exists()
    assert "Do the task." in task_file.read_text()

    # Should send tmux trigger
    commands = [call[0] for call in runner.calls]
    trigger_cmds = [c for c in commands if c[:2] == ["tmux", "send-keys"]]
    assert len(trigger_cmds) >= 2  # start + send trigger


def test_observe_watches_outbox(tmp_path: Path):
    """observe() should detect result.json in outbox."""
    work_dir = tmp_path / "work"
    work_dir.mkdir()
    outbox = work_dir / ".outbox"
    outbox.mkdir()
    runner = FakeTmuxRunner()
    session = NativeClaudeSession(tmux="tmux", runner=runner)

    # No result yet
    observed = session.observe(
        terminal_pane="crew-1-worker:claude.0",
        work_dir=work_dir,
    )
    assert observed["marker_seen"] is False

    # Write result
    result = {"status": "completed", "summary": "done"}
    (outbox / "result.json").write_text(json.dumps(result))

    observed = session.observe(
        terminal_pane="crew-1-worker:claude.0",
        work_dir=work_dir,
    )
    assert observed["marker_seen"] is True
    assert observed["result"]["status"] == "completed"


def test_observe_reads_result_content(tmp_path: Path):
    """observe() should return parsed result from outbox."""
    work_dir = tmp_path / "work"
    work_dir.mkdir()
    outbox = work_dir / ".outbox"
    outbox.mkdir()
    runner = FakeTmuxRunner()
    session = NativeClaudeSession(tmux="tmux", runner=runner)

    result = {
        "crew_id": "crew-1",
        "worker_id": "worker-1",
        "turn_id": "turn-1",
        "status": "completed",
        "summary": "Implemented feature X",
        "changed_files": ["x.py"],
    }
    (outbox / "result.json").write_text(json.dumps(result))

    observed = session.observe(
        terminal_pane="crew-1-worker:claude.0",
        work_dir=work_dir,
    )
    assert observed["result"]["summary"] == "Implemented feature X"
    assert observed["result"]["changed_files"] == ["x.py"]
```

- [ ] **Step 2: Run tests to verify they fail**

```bash
pytest tests/runtime/test_native_claude_session.py -v
```

Expected: new tests FAIL (work_dir parameter doesn't exist yet)

- [ ] **Step 3: Modify `NativeClaudeSession`**

Replace the contents of `native_claude_session.py`:

```python
from __future__ import annotations

import json
import re
import shlex
import shutil
import subprocess
from collections.abc import Callable
from pathlib import Path
from subprocess import CalledProcessError, CompletedProcess
from uuid import uuid4


TmuxRunner = Callable[..., CompletedProcess[str]]


def _safe_session_name(worker_id: str) -> str:
    """Sanitize worker_id for use as tmux session name."""
    safe = re.sub(r"[^a-zA-Z0-9_-]", "_", worker_id)
    return f"crew-{safe}-{uuid4().hex[:8]}"


def _escape_turn_markers(message: str) -> str:
    """Prevent injection of fake completion markers."""
    return (
        message
        .replace("<<<CODEX_TURN_DONE", "[MARKER_ESCAPED]")
        .replace("<<<WORKER_TURN_DONE", "[MARKER_ESCAPED]")
    )


def _wrapper_script_path() -> Path:
    """Return the path to claude_worker.sh."""
    return Path(__file__).parent / "claude_worker.sh"


class NativeClaudeSession:
    def __init__(
        self,
        *,
        tmux: str | None = None,
        runner: TmuxRunner | None = None,
        terminal_runner: TmuxRunner | None = None,
        session_name_factory: Callable[[str], str] | None = None,
        turn_marker: str = "<<<WORKER_TURN_DONE>>>",
        open_terminal_on_start: bool = True,
    ):
        self._tmux = tmux or shutil.which("tmux") or "tmux"
        self._runner = runner or subprocess.run
        self._terminal_runner = terminal_runner or subprocess.run
        self._session_name_factory = session_name_factory or _safe_session_name
        self._turn_marker = turn_marker
        self._open_terminal_on_start = open_terminal_on_start

    def start(
        self,
        *,
        repo_root: Path,
        worker_id: str,
        role: str,
        instructions: str,
        transcript_path: Path,
    ) -> dict[str, str]:
        repo_root = repo_root.resolve()
        transcript_path.parent.mkdir(parents=True, exist_ok=True)
        session = self._session_name_factory(worker_id)
        pane = f"{session}:claude.0"

        # Create work directories
        work_dir = repo_root / ".worker" / worker_id
        inbox = work_dir / ".inbox"
        outbox = work_dir / ".outbox"
        history = work_dir / ".crew-history"
        inbox.mkdir(parents=True, exist_ok=True)
        outbox.mkdir(parents=True, exist_ok=True)
        history.mkdir(parents=True, exist_ok=True)

        # Write mission file
        mission_file = inbox / "mission.md"
        mission_file.write_text(
            f"# Mission\n\nRole: {role}\n\n{instructions}",
            encoding="utf-8",
        )

        # Create tmux session
        self._tmux_run(["new-session", "-d", "-s", session, "-c", str(repo_root), "-n", "claude"])

        # Launch wrapper script
        wrapper = _wrapper_script_path()
        command = f'bash -c "{shlex.quote(str(wrapper))} {shlex.quote(str(work_dir))}"'
        self._tmux_run(["send-keys", "-t", pane, command, "C-m"])

        if self._open_terminal_on_start:
            self._open_terminal(session)

        return {
            "native_session_id": session,
            "terminal_session": session,
            "terminal_pane": pane,
            "transcript_artifact": str(transcript_path),
            "turn_marker": self._turn_marker,
            "work_dir": str(work_dir),
        }

    def send(
        self,
        *,
        terminal_pane: str,
        message: str,
        work_dir: Path | None = None,
        turn_marker: str | None = None,
    ) -> dict:
        marker = turn_marker or self._turn_marker

        if work_dir is not None:
            # File-based: write task to inbox, send trigger
            safe_message = _escape_turn_markers(message)
            task_file = work_dir / ".inbox" / "task.md"
            task_file.write_text(safe_message, encoding="utf-8")

            # Send a short trigger to tmux (just re-run wrapper)
            wrapper = _wrapper_script_path()
            trigger = f'bash -c "{shlex.quote(str(wrapper))} {shlex.quote(str(work_dir))}"'
            self._tmux_run(["send-keys", "-t", terminal_pane, trigger, "C-m"])
            return {"message": safe_message, "marker": marker, "method": "file"}
        else:
            # Legacy: direct tmux send-keys (for backward compatibility)
            safe_message = _escape_turn_markers(message)
            full_message = (
                f"{safe_message}\n\n"
                f"When this turn is complete, print exactly: {marker}\n"
                "This turn marker overrides any earlier completion marker."
            )
            escaped_message = full_message.replace('"', '\\"').replace('\n', '\\n')
            command = f'bash -c "claude -p \\"{escaped_message}\\""'
            self._tmux_run(["send-keys", "-t", terminal_pane, command, "C-m"])
            return {"message": full_message, "marker": marker, "method": "legacy"}

    def observe(
        self,
        *,
        terminal_pane: str,
        lines: int = 200,
        turn_marker: str | None = None,
        work_dir: Path | None = None,
    ) -> dict:
        marker = turn_marker or self._turn_marker

        if work_dir is not None:
            # File-based: check outbox for result.json
            result_file = work_dir / ".outbox" / "result.json"
            if result_file.exists():
                try:
                    result = json.loads(result_file.read_text(encoding="utf-8"))
                    return {
                        "snapshot": json.dumps(result, ensure_ascii=False),
                        "marker_seen": True,
                        "marker": marker,
                        "result": result,
                        "method": "file",
                    }
                except (json.JSONDecodeError, OSError):
                    pass
            return {
                "snapshot": "",
                "marker_seen": False,
                "marker": marker,
                "result": None,
                "method": "file",
            }
        else:
            # Legacy: tmux pane capture
            result = self._tmux_run(["capture-pane", "-p", "-t", terminal_pane, "-S", f"-{lines}"])
            snapshot = result.stdout
            return {"snapshot": snapshot, "marker_seen": marker in snapshot, "marker": marker}

    def tail(self, *, transcript_path: Path, limit: int = 80) -> dict:
        if transcript_path.exists():
            lines = transcript_path.read_text(encoding="utf-8", errors="replace").splitlines()[-limit:]
        else:
            lines = []
        return {"transcript_artifact": str(transcript_path), "lines": lines}

    def status(self, *, terminal_session: str) -> dict:
        result = self._tmux_run(["has-session", "-t", terminal_session], check=False)
        return {"running": result.returncode == 0, "terminal_session": terminal_session}

    def stop(self, *, terminal_session: str) -> dict:
        result = self._tmux_run(["kill-session", "-t", terminal_session], check=False)
        return {"terminal_session": terminal_session, "stopped": result.returncode == 0}

    def list_sessions(self) -> list[str]:
        result = self._tmux_run(["list-sessions", "-F", "#{session_name}"], check=False)
        if result.returncode != 0:
            return []
        return [line.strip() for line in result.stdout.splitlines() if line.strip()]

    def prune_orphans(self, *, active_sessions: set[str], prefix: str = "crew-worker-") -> dict:
        pruned_sessions = []
        for session in self.list_sessions():
            if session.startswith(prefix) and session not in active_sessions:
                result = self._tmux_run(["kill-session", "-t", session], check=False)
                if result.returncode == 0:
                    pruned_sessions.append(session)
        return {"active_sessions": sorted(active_sessions), "pruned_sessions": pruned_sessions}

    def attach(self, *, terminal_session: str) -> dict:
        return {"attach_command": f"{self._tmux} attach -t {shlex.quote(terminal_session)}"}

    def _open_terminal(self, terminal_session: str) -> None:
        shell_command = shlex.join([self._tmux, "attach", "-t", terminal_session])
        command = [
            "osascript",
            "-e",
            'tell application "Terminal"',
            "-e",
            "activate",
            "-e",
            f"do script {json.dumps(shell_command, ensure_ascii=False)}",
            "-e",
            "end tell",
        ]
        import subprocess as _sp
        try:
            _sp.Popen(command, stdout=_sp.DEVNULL, stderr=_sp.DEVNULL)
        except Exception:
            pass

    def _tmux_run(self, args: list[str], *, check: bool = True) -> CompletedProcess[str]:
        result = self._runner(
            [self._tmux, *args],
            text=True,
            capture_output=True,
            check=False,
        )
        if check and result.returncode != 0:
            raise CalledProcessError(result.returncode, result.args, output=result.stdout, stderr=result.stderr)
        return result
```

- [ ] **Step 4: Run tests to verify they pass**

```bash
pytest tests/runtime/test_native_claude_session.py -v
```

Expected: all tests PASS

- [ ] **Step 5: Commit**

```bash
git add src/codex_claude_orchestrator/runtime/native_claude_session.py tests/runtime/test_native_claude_session.py
git commit -m "feat: NativeClaudeSession uses wrapper script + file-based communication"
```

---

### Task 4: Modify `WorkerPool`

**Files:**
- Modify: `src/codex_claude_orchestrator/workers/pool.py`
- Modify: `tests/workers/test_pool.py`

Add `work_dir` tracking, integrate `HistoryManager`, save history after each turn.

- [ ] **Step 1: Write failing tests**

Add to `tests/workers/test_pool.py`:

```python
from codex_claude_orchestrator.workers.history_manager import HistoryManager


def test_start_worker_creates_history(tmp_path: Path):
    """start_worker should create history directory and return work_dir."""
    recorder, pool, fake_native, fake_worktree = make_pool(tmp_path)
    crew = make_crew(recorder)
    task = make_task(crew)

    worker = pool.start_worker(repo_root=tmp_path, crew=crew, task=task)

    # work_dir should be in worker record
    assert hasattr(worker, 'work_dir') or 'work_dir' in str(worker)
    # History directory should exist (via NativeClaudeSession.start)
    assert len(fake_native.starts) == 1


def test_observe_worker_saves_to_history(tmp_path: Path):
    """observe_worker should save result to history and update index."""
    recorder, pool, fake_native, fake_worktree = make_pool(tmp_path)
    crew = make_crew(recorder)
    task = make_task(crew)

    worker = pool.start_worker(repo_root=tmp_path, crew=crew, task=task)

    # Simulate worker completing: write outbox result
    work_dir = Path(fake_native.starts[-1]["work_dir"])
    outbox = work_dir / ".outbox"
    outbox.mkdir(parents=True, exist_ok=True)
    result = {
        "crew_id": crew.crew_id,
        "worker_id": worker.worker_id,
        "turn_id": "turn-1",
        "status": "completed",
        "summary": "Did the task",
        "changed_files": ["a.py"],
    }
    (outbox / "result.json").write_text(json.dumps(result))

    # Observe should detect result and save to history
    observed = pool.observe_worker(
        repo_root=tmp_path,
        crew_id=crew.crew_id,
        worker_id=worker.worker_id,
        work_dir=work_dir,
    )

    assert observed["marker_seen"] is True
    # History should be updated
    history_dir = work_dir / ".crew-history"
    assert (history_dir / "turn-1-result.json").exists()
    assert (history_dir / "index.md").exists()
```

Note: The exact test depends on the existing test helpers (`make_pool`, `make_crew`, `make_task`). Adapt as needed based on the existing test file structure.

- [ ] **Step 2: Run tests to verify they fail**

```bash
pytest tests/workers/test_pool.py -v -k "test_start_worker_creates_history or test_observe_worker_saves_to_history"
```

Expected: FAIL

- [ ] **Step 3: Modify `WorkerPool`**

Key changes to `pool.py`:

1. Import `HistoryManager`
2. In `start_worker()` and `ensure_worker()`: pass `work_dir` from `start_info`
3. In `observe_worker()`: add `work_dir` parameter, save result to history after observing
4. Add helper `_save_turn_history()`

```python
# Add import at top:
from codex_claude_orchestrator.workers.history_manager import HistoryManager

# In start_worker(), after start_info = self._native_session.start(...):
# Add work_dir to worker record (WorkerRecord needs this field)
# worker = WorkerRecord(..., work_dir=start_info.get("work_dir", ""), ...)

# Add new method:
def _save_turn_history(
    self,
    *,
    work_dir: Path,
    turn_number: int,
    task_description: str,
    result: dict,
) -> None:
    """Save turn result to history and update index."""
    hm = HistoryManager(work_dir=work_dir)
    hm.save_turn_result(turn_number=turn_number, result=result)
    hm.update_index(
        turn_number=turn_number,
        task=task_description,
        status=result.get("status", "unknown"),
        summary=result.get("summary", ""),
        changed_files=result.get("changed_files", []),
    )

# In observe_worker(), after getting observation:
# If marker_seen and result exists, save to history
```

- [ ] **Step 4: Run tests to verify they pass**

```bash
pytest tests/workers/test_pool.py -v
```

Expected: all tests PASS

- [ ] **Step 5: Commit**

```bash
git add src/codex_claude_orchestrator/workers/pool.py tests/workers/test_pool.py
git commit -m "feat: WorkerPool integrates HistoryManager for turn history"
```

---

### Task 5: Modify `ClaudeCodeTmuxAdapter`

**Files:**
- Modify: `src/codex_claude_orchestrator/v4/adapters/tmux_claude.py`
- Modify: `tests/v4/test_tmux_claude_adapter.py`

Change `deliver_turn()` to write mission/task files. Change `watch_turn()` to watch outbox instead of tmux pane polling.

- [ ] **Step 1: Write failing tests**

Add to `tests/v4/test_tmux_claude_adapter.py`:

```python
def test_deliver_turn_writes_inbox_files(tmp_path: Path):
    """deliver_turn should write mission.md and task.md to inbox."""
    native = FakeNativeSession()
    native.send_result = {"marker": "<<<WORKER_TURN_DONE>>>", "method": "file"}
    adapter = ClaudeCodeTmuxAdapter(native_session=native)

    work_dir = tmp_path / "work"
    work_dir.mkdir()
    (work_dir / ".inbox").mkdir()

    spec = WorkerSpec(
        crew_id="crew-1",
        worker_id="worker-1",
        runtime_type="tmux",
        contract_id="c1",
        workspace_path=str(work_dir),
    )
    adapter.register_worker(spec)

    turn = TurnEnvelope(
        crew_id="crew-1",
        worker_id="worker-1",
        turn_id="turn-1",
        round_id="r1",
        phase="execute",
        message="Implement feature X",
        expected_marker="<<<WORKER_TURN_DONE>>>",
        required_outbox_path=str(work_dir / ".outbox" / "result.json"),
    )

    result = adapter.deliver_turn(turn)

    assert result.delivered is True
    # Check inbox files were written
    task_file = work_dir / ".inbox" / "task.md"
    assert task_file.exists()
    assert "Implement feature X" in task_file.read_text()


def test_watch_turn_detects_outbox_result(tmp_path: Path):
    """watch_turn should detect result.json in outbox."""
    native = FakeNativeSession()
    adapter = ClaudeCodeTmuxAdapter(
        native_session=native,
        poll_initial_delay=0.01,
        poll_timeout=1.0,
    )

    work_dir = tmp_path / "work"
    work_dir.mkdir()
    outbox = work_dir / ".outbox"
    outbox.mkdir()

    spec = WorkerSpec(
        crew_id="crew-1",
        worker_id="worker-1",
        runtime_type="tmux",
        contract_id="c1",
        workspace_path=str(work_dir),
    )
    adapter.register_worker(spec)

    turn = TurnEnvelope(
        crew_id="crew-1",
        worker_id="worker-1",
        turn_id="turn-1",
        round_id="r1",
        phase="execute",
        message="Do the thing",
        expected_marker="<<<WORKER_TURN_DONE>>>",
        required_outbox_path=str(outbox / "result.json"),
    )

    # Write result before watching
    result_data = {"status": "completed", "summary": "done"}
    (outbox / "result.json").write_text(json.dumps(result_data))

    events = list(adapter.watch_turn(turn))
    marker_events = [e for e in events if e.type == "marker.detected"]
    assert len(marker_events) == 1
```

- [ ] **Step 2: Run tests to verify they fail**

```bash
pytest tests/v4/test_tmux_claude_adapter.py -v -k "test_deliver_turn_writes_inbox_files or test_watch_turn_detects_outbox_result"
```

Expected: FAIL

- [ ] **Step 3: Modify `ClaudeCodeTmuxAdapter`**

Key changes to `tmux_claude.py`:

1. In `deliver_turn()`: write mission/task files to inbox before calling `send()`
2. In `watch_turn()` and `async_watch_turn()`: check outbox file first, fall back to tmux pane
3. Pass `work_dir` through from `WorkerSpec.workspace_path`

```python
# In deliver_turn():
def deliver_turn(self, turn: TurnEnvelope) -> DeliveryResult:
    worker = self._workers.get(turn.worker_id)
    terminal_pane = _terminal_pane_for(turn, worker)

    # Write inbox files if work_dir is available
    work_dir = _work_dir_for(worker)
    if work_dir:
        _write_inbox_files(work_dir, turn)

    self._initialize_filesystem_stream(turn, worker)
    result = self._native_session.send(
        terminal_pane=terminal_pane,
        message=_compiled_turn_message(turn),
        turn_marker=turn.expected_marker,
        work_dir=work_dir,
    )
    # ... rest unchanged

# Add helper:
def _work_dir_for(worker: WorkerSpec | None) -> Path | None:
    if worker and worker.workspace_path:
        return Path(worker.workspace_path)
    return None

def _write_inbox_files(work_dir: Path, turn: TurnEnvelope) -> None:
    inbox = work_dir / ".inbox"
    inbox.mkdir(parents=True, exist_ok=True)
    (inbox / "task.md").write_text(turn.message, encoding="utf-8")
    # mission.md is written by NativeClaudeSession.start()

# In watch_turn(), add outbox check at the top:
def watch_turn(self, turn, cancel_event=None):
    worker = self._workers.get(turn.worker_id)
    work_dir = _work_dir_for(worker)
    cancel = cancel_event or self._cancel

    # Check outbox first (file-based completion)
    if work_dir:
        result_file = work_dir / ".outbox" / "result.json"
        if result_file.exists():
            try:
                result = json.loads(result_file.read_text())
                yield RuntimeEvent(
                    type="marker.detected",
                    turn_id=turn.turn_id,
                    worker_id=turn.worker_id,
                    payload={"marker": turn.expected_marker, "source": "outbox", "result": result},
                )
                return
            except (json.JSONDecodeError, OSError):
                pass

    # Fall back to filesystem stream + tmux pane polling
    # ... existing code
```

- [ ] **Step 4: Run tests to verify they pass**

```bash
pytest tests/v4/test_tmux_claude_adapter.py -v
```

Expected: all tests PASS

- [ ] **Step 5: Commit**

```bash
git add src/codex_claude_orchestrator/v4/adapters/tmux_claude.py tests/v4/test_tmux_claude_adapter.py
git commit -m "feat: ClaudeCodeTmuxAdapter writes inbox files and watches outbox"
```

---

### Task 6: Add `work_dir` to `WorkerRecord`

**Files:**
- Modify: `src/codex_claude_orchestrator/crew/models.py`
- Modify: `src/codex_claude_orchestrator/workers/pool.py` (already modified in Task 4)

- [ ] **Step 1: Add `work_dir` field to `WorkerRecord`**

In `crew/models.py`, find the `WorkerRecord` dataclass and add:

```python
work_dir: str = ""
```

- [ ] **Step 2: Update `WorkerPool.start_worker()` and `ensure_worker()`**

Pass `work_dir` from `start_info` to `WorkerRecord`:

```python
worker = WorkerRecord(
    ...
    work_dir=start_info.get("work_dir", ""),
    ...
)
```

- [ ] **Step 3: Run all tests**

```bash
pytest tests/ -v --timeout=30
```

Expected: all tests PASS

- [ ] **Step 4: Commit**

```bash
git add src/codex_claude_orchestrator/crew/models.py src/codex_claude_orchestrator/workers/pool.py
git commit -m "feat: add work_dir field to WorkerRecord"
```

---

### Task 7: Integration Verification

- [ ] **Step 1: Run full test suite**

```bash
pytest tests/ -v --timeout=30
```

Expected: all tests PASS

- [ ] **Step 2: Manual smoke test (if tmux available)**

```bash
# Create a test work directory
mkdir -p /tmp/test-worker/.inbox /tmp/test-worker/.outbox /tmp/test-worker/.crew-history

# Write a test task
echo "# Test Task" > /tmp/test-worker/.inbox/task.md
echo "Read this message and write 'hello' to .outbox/result.json" >> /tmp/test-worker/.inbox/task.md

# Launch wrapper (in a real tmux session)
# src/codex_claude_orchestrator/runtime/claude_worker.sh /tmp/test-worker
```

- [ ] **Step 3: Final commit with all changes**

```bash
git status
# Verify all expected files are tracked
```
