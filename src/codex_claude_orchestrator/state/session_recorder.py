from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from codex_claude_orchestrator.core.models import (
    ChallengeRecord,
    LearningNote,
    OutputTrace,
    SessionRecord,
    SessionStatus,
    TurnRecord,
    VerificationRecord,
    utc_now,
)


class SessionRecorder:
    def __init__(self, state_root: Path):
        self._state_root = state_root
        self._sessions_root = state_root / "sessions"
        self._sessions_root.mkdir(parents=True, exist_ok=True)

    def start_session(self, session: SessionRecord) -> Path:
        session_dir = self._session_dir(session.session_id)
        session_dir.mkdir(parents=True, exist_ok=True)
        self._write_json(session_dir / "session.json", session.to_dict())
        return session_dir

    def append_turn(self, session_id: str, turn: TurnRecord) -> None:
        self._append_jsonl(session_id, "turns.jsonl", turn.to_dict())

    def append_output_trace(self, session_id: str, trace: OutputTrace) -> None:
        self._append_jsonl(session_id, "output_traces.jsonl", trace.to_dict())

    def append_challenge(self, session_id: str, challenge: ChallengeRecord) -> None:
        self._append_jsonl(session_id, "challenges.jsonl", challenge.to_dict())

    def append_verification(self, session_id: str, verification: VerificationRecord) -> None:
        self._append_jsonl(session_id, "verifications.jsonl", verification.to_dict())

    def append_learning_note(self, session_id: str, learning_note: LearningNote) -> None:
        session_dir = self._session_dir(session_id)
        session_dir.mkdir(parents=True, exist_ok=True)
        learning_path = session_dir / "learning.json"
        learning = self._read_optional_list(learning_path)
        learning.append(learning_note.to_dict())
        self._write_json(learning_path, learning)

    def finalize_session(
        self,
        session_id: str,
        status: SessionStatus,
        final_summary: str,
        *,
        current_round: int | None = None,
    ) -> None:
        session_path = self._session_dir(session_id) / "session.json"
        if not session_path.exists():
            raise FileNotFoundError(f"session not found: {session_id}")

        session = self._read_json(session_path)
        ended_at = utc_now()
        updates = {
            "status": status.value,
            "final_summary": final_summary,
            "updated_at": ended_at,
            "ended_at": ended_at,
        }
        if current_round is not None:
            updates["current_round"] = current_round
        session.update(updates)
        self._write_json(session_path, session)
        self._write_json(
            self._session_dir(session_id) / "final_report.json",
            {
                "session_id": session_id,
                "status": status.value,
                "final_summary": final_summary,
                "ended_at": ended_at,
            },
        )

    def list_sessions(self) -> list[dict[str, Any]]:
        sessions = [self._session_summary(path.name) for path in self._iter_session_dirs()]
        return sorted(sessions, key=lambda item: item["created_at"], reverse=True)

    def read_session(self, session_id: str) -> dict[str, Any]:
        session_dir = self._session_dir(session_id)
        if not session_dir.is_dir():
            raise FileNotFoundError(f"session not found: {session_id}")

        return {
            "session": self._read_json(session_dir / "session.json"),
            "turns": self._read_jsonl(session_dir / "turns.jsonl"),
            "output_traces": self._read_jsonl(session_dir / "output_traces.jsonl"),
            "challenges": self._read_jsonl(session_dir / "challenges.jsonl"),
            "verifications": self._read_jsonl(session_dir / "verifications.jsonl"),
            "learning": self._read_optional_list(session_dir / "learning.json"),
            "final_report": self._read_optional_json(session_dir / "final_report.json"),
            "artifacts": self._list_artifacts(session_dir / "artifacts"),
        }

    def write_text_artifact(self, session_id: str, artifact_name: str, content: str) -> Path:
        artifacts_dir = self._session_dir(session_id) / "artifacts"
        artifacts_dir.mkdir(parents=True, exist_ok=True)
        artifact_path = artifacts_dir / artifact_name
        self._write_text(artifact_path, content)
        return artifact_path

    def _append_jsonl(self, session_id: str, filename: str, payload: dict[str, Any]) -> None:
        session_dir = self._session_dir(session_id)
        session_dir.mkdir(parents=True, exist_ok=True)
        stream_path = session_dir / filename
        with stream_path.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(payload, ensure_ascii=False) + "\n")

    def _iter_session_dirs(self) -> list[Path]:
        if not self._sessions_root.exists():
            return []
        return [path for path in self._sessions_root.iterdir() if path.is_dir()]

    def _session_summary(self, session_id: str) -> dict[str, Any]:
        payload = self.read_session(session_id)
        session = payload["session"]
        return {
            "session_id": session["session_id"],
            "root_task_id": session["root_task_id"],
            "goal": session["goal"],
            "assigned_agent": session["assigned_agent"],
            "status": session["status"],
            "summary": session.get("final_summary", ""),
            "created_at": session["created_at"],
            "ended_at": session.get("ended_at"),
        }

    def _session_dir(self, session_id: str) -> Path:
        return self._sessions_root / session_id

    def _read_json(self, path: Path) -> dict[str, Any]:
        return json.loads(path.read_text(encoding="utf-8"))

    def _read_optional_json(self, path: Path) -> dict[str, Any] | None:
        if not path.exists():
            return None
        return self._read_json(path)

    def _read_optional_list(self, path: Path) -> list[dict[str, Any]]:
        if not path.exists():
            return []
        return json.loads(path.read_text(encoding="utf-8"))

    def _read_jsonl(self, path: Path) -> list[dict[str, Any]]:
        if not path.exists():
            return []
        return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]

    def _list_artifacts(self, artifacts_dir: Path) -> list[str]:
        if not artifacts_dir.exists():
            return []
        return sorted(path.relative_to(artifacts_dir).as_posix() for path in artifacts_dir.rglob("*") if path.is_file())

    def _write_json(self, path: Path, payload: dict[str, Any] | list[dict[str, Any]]) -> None:
        self._write_text(path, json.dumps(payload, indent=2, ensure_ascii=False))

    def _write_text(self, path: Path, content: str) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp_path = path.with_name(f".{path.name}.tmp")
        tmp_path.write_text(content, encoding="utf-8")
        tmp_path.replace(path)
