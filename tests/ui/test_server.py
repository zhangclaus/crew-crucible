from __future__ import annotations

import json
from pathlib import Path

import pytest

from codex_claude_orchestrator.core.models import (
    EvaluationOutcome,
    LearningNote,
    NextAction,
    RunRecord,
    SessionRecord,
    SessionStatus,
    TaskRecord,
    WorkspaceMode,
)
from codex_claude_orchestrator.state.run_recorder import RunRecorder
from codex_claude_orchestrator.state.session_recorder import SessionRecorder
from codex_claude_orchestrator.session.skill_evolution import SkillEvolution
from codex_claude_orchestrator.ui.server import (
    build_ui_state,
    build_v4_ui_state,
    render_index_html,
    resolve_ui_request,
)
from codex_claude_orchestrator.v4.event_store import SQLiteEventStore


def test_build_ui_state_includes_sessions_runs_and_skills(tmp_path: Path):
    repo_root = _repo_with_records(tmp_path)

    state = build_ui_state(repo_root)

    assert state["repo"] == str(repo_root)
    assert state["sessions"][0]["session_id"] == "session-ui"
    assert state["runs"][0]["run_id"] == "run-ui"
    assert state["skills"][0]["name"] == "ui-review"


def test_ui_state_includes_v4_event_summary(tmp_path: Path):
    store = SQLiteEventStore(tmp_path / ".orchestrator" / "v4" / "events.sqlite3")
    store.append(stream_id="crew-1", type="crew.started", crew_id="crew-1")
    store.append(
        stream_id="crew-1",
        type="turn.completed",
        crew_id="crew-1",
        worker_id="worker-1",
        turn_id="turn-1",
    )

    state = build_ui_state(tmp_path)

    assert state["v4"]["event_count"] == 2
    assert state["v4"]["crews"][0]["crew_id"] == "crew-1"
    assert state["v4"]["crews"][0]["status"] == "running"
    assert state["v4"]["crews"][0]["turn_count"] == 1


def test_build_v4_ui_state_without_db_is_read_only(tmp_path: Path):
    state = build_v4_ui_state(tmp_path)

    assert state == {"event_count": 0, "crews": []}
    assert not (tmp_path / ".orchestrator").exists()


def test_render_index_html_contains_operational_shell(tmp_path: Path):
    html = render_index_html(tmp_path)

    assert "Orchestrator V2 Console" in html
    assert "Session Timeline" in html
    assert "OutputTrace" in html
    assert "Agent Runs" in html
    assert "Pending Skills" in html


def test_render_index_html_escapes_repo_path(tmp_path: Path):
    repo_root = tmp_path / "repo<&>"
    repo_root.mkdir()

    html = render_index_html(repo_root)

    assert "repo&lt;&amp;&gt;" in html
    assert "repo<&>" not in html


def test_ui_routes_serve_index_state_session_and_run_artifact(tmp_path: Path):
    repo_root = _repo_with_records(tmp_path)

    index_type, index = resolve_ui_request(repo_root, "/")
    state_type, state_body = resolve_ui_request(repo_root, "/api/state")
    session_type, session_body = resolve_ui_request(repo_root, "/api/sessions/session-ui")
    run_type, run_body = resolve_ui_request(repo_root, "/api/runs/run-ui")
    stdout_type, stdout = resolve_ui_request(repo_root, "/api/run-artifacts/run-ui/stdout.txt")
    state = json.loads(state_body)
    session = json.loads(session_body)
    run = json.loads(run_body)

    assert index_type == "text/html; charset=utf-8"
    assert state_type == "application/json; charset=utf-8"
    assert session_type == "application/json; charset=utf-8"
    assert run_type == "application/json; charset=utf-8"
    assert stdout_type == "text/plain; charset=utf-8"
    assert "Orchestrator V2 Console" in index
    assert state["sessions"][0]["session_id"] == "session-ui"
    assert session["session"]["goal"] == "Visualize session"
    assert run["run"]["run_id"] == "run-ui"
    assert stdout == '{"summary":"done"}'


def test_ui_routes_block_artifact_path_traversal(tmp_path: Path):
    repo_root = _repo_with_records(tmp_path)

    with pytest.raises(ValueError):
        resolve_ui_request(repo_root, "/api/run-artifacts/run-ui/../run.json")


def test_ui_routes_block_resource_id_path_traversal(tmp_path: Path):
    repo_root = _repo_with_records(tmp_path)

    with pytest.raises(ValueError):
        resolve_ui_request(repo_root, "/api/sessions/../runs/run-ui")


def _repo_with_records(tmp_path: Path) -> Path:
    repo_root = tmp_path / "repo"
    repo_root.mkdir()
    state_root = repo_root / ".orchestrator"

    session_recorder = SessionRecorder(state_root)
    session = SessionRecord(
        session_id="session-ui",
        root_task_id="task-ui",
        repo=str(repo_root),
        goal="Visualize session",
        assigned_agent="claude",
    )
    session_recorder.start_session(session)
    session_recorder.finalize_session(session.session_id, SessionStatus.ACCEPTED, "accepted")

    run_recorder = RunRecorder(state_root)
    task = TaskRecord(
        task_id="task-ui",
        parent_task_id=None,
        origin="test",
        assigned_agent="claude",
        goal="Visualize run",
        task_type="review",
        scope=str(repo_root),
        workspace_mode=WorkspaceMode.READONLY,
    )
    run = RunRecord(
        run_id="run-ui",
        task_id=task.task_id,
        agent="claude",
        adapter="fake",
        workspace_id="workspace-ui",
    )
    run_recorder.start_run(run, task)
    run_recorder.write_result(
        run.run_id,
        result=FakeWorkerResult(),
        evaluation=EvaluationOutcome(
            accepted=True,
            next_action=NextAction.ACCEPT,
            summary="done",
        ),
    )

    SkillEvolution(state_root).create_pending_skill(
        LearningNote(
            note_id="learning-ui",
            session_id=session.session_id,
            challenge_ids=["challenge-ui"],
            summary="Review UI output traces.",
            proposed_skill_name="UI Review",
        )
    )
    return repo_root


class FakeWorkerResult:
    raw_output = '{"summary":"done"}'
    stdout = '{"summary":"done"}'
    stderr = ""
    exit_code = 0
    structured_output = {"summary": "done"}
    changed_files: list[str] = []
    parse_error = None

    def to_dict(self):
        return {
            "raw_output": self.raw_output,
            "stdout": self.stdout,
            "stderr": self.stderr,
            "exit_code": self.exit_code,
            "structured_output": self.structured_output,
            "changed_files": self.changed_files,
            "parse_error": self.parse_error,
        }
