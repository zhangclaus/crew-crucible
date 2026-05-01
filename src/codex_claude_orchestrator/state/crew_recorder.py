from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from codex_claude_orchestrator.crew.models import (
    AgentMessage,
    BlackboardEntry,
    CrewRecord,
    CrewEvent,
    CrewStatus,
    CrewTaskRecord,
    DecisionAction,
    ProtocolRequest,
    WorkerRecord,
    WorkerContract,
)
from codex_claude_orchestrator.core.models import utc_now


class CrewRecorder:
    def __init__(self, state_root: Path):
        self._state_root = state_root
        self._crews_root = state_root / "crews"
        self._crews_root.mkdir(parents=True, exist_ok=True)

    def start_crew(self, crew: CrewRecord) -> Path:
        crew_dir = self._crew_dir(crew.crew_id)
        crew_dir.mkdir(parents=True, exist_ok=True)
        self._write_json(crew_dir / "crew.json", crew.to_dict())
        self._write_text(self._crews_root / "latest", crew.crew_id)
        return crew_dir

    def update_crew(self, crew_id: str, updates: dict[str, Any]) -> dict[str, Any]:
        path = self._crew_dir(crew_id) / "crew.json"
        crew = self._read_json(path)
        crew.update({**updates, "updated_at": utc_now()})
        self._write_json(path, crew)
        return crew

    def append_worker(self, crew_id: str, worker: WorkerRecord) -> None:
        self._append_jsonl(crew_id, "workers.jsonl", worker.to_dict())

    def append_worker_contract(self, crew_id: str, contract: WorkerContract) -> None:
        self._append_jsonl(crew_id, "worker_contracts.jsonl", contract.to_dict())

    def append_event(self, crew_id: str, event: CrewEvent) -> None:
        self._append_jsonl(crew_id, "events.jsonl", event.to_dict())

    def append_decision(self, crew_id: str, action: DecisionAction) -> None:
        self._append_jsonl(crew_id, "decisions.jsonl", action.to_dict())

    def append_message(self, crew_id: str, message: AgentMessage) -> None:
        payload = message.to_dict()
        self._append_jsonl(crew_id, "messages.jsonl", payload)
        inbox_name = self._inbox_file_name(payload["to"])
        self._append_jsonl(crew_id, f"inboxes/{inbox_name}.jsonl", payload)

    def append_protocol_request(self, crew_id: str, request: ProtocolRequest) -> None:
        self._append_jsonl(crew_id, "protocol_requests.jsonl", request.to_dict())

    def append_known_pitfall(
        self,
        crew_id: str,
        *,
        failure_class: str,
        summary: str,
        guardrail: str,
        evidence_refs: list[str] | None = None,
    ) -> dict[str, Any]:
        payload = {
            "failure_class": failure_class,
            "summary": summary,
            "guardrail": guardrail,
            "evidence_refs": evidence_refs or [],
            "created_at": utc_now(),
        }
        self._append_jsonl(crew_id, "known_pitfalls.jsonl", payload)
        return payload

    def update_worker(self, crew_id: str, worker_id: str, updates: dict[str, Any]) -> dict[str, Any]:
        path = self._crew_dir(crew_id) / "workers.jsonl"
        workers = self._read_jsonl(path)
        for worker in workers:
            if worker["worker_id"] == worker_id:
                worker.update({**updates, "updated_at": utc_now()})
                self._write_jsonl(path, workers)
                return worker
        raise FileNotFoundError(f"worker not found: {worker_id}")

    def write_tasks(self, crew_id: str, tasks: list[CrewTaskRecord]) -> None:
        self._write_json(self._crew_dir(crew_id) / "tasks.json", [task.to_dict() for task in tasks])

    def append_blackboard(self, crew_id: str, entry: BlackboardEntry) -> None:
        self._append_jsonl(crew_id, "blackboard.jsonl", entry.to_dict())

    def write_text_artifact(self, crew_id: str, artifact_name: str, content: str) -> Path:
        artifact_path = self._crew_dir(crew_id) / "artifacts" / artifact_name
        self._write_text(artifact_path, content)
        return artifact_path

    def write_json_artifact(self, crew_id: str, artifact_name: str, payload: Any) -> Path:
        return self.write_text_artifact(crew_id, artifact_name, json.dumps(payload, indent=2, ensure_ascii=False))

    def write_team_snapshot(self, crew_id: str, payload: dict[str, Any]) -> Path:
        path = self._crew_dir(crew_id) / "team_snapshot.json"
        self._write_json(path, payload)
        return path

    def read_team_snapshot(self, crew_id: str) -> dict[str, Any] | None:
        return self._read_optional_json(self._crew_dir(crew_id) / "team_snapshot.json")

    def read_jsonl_stream(self, crew_id: str, file_name: str) -> list[dict[str, Any]]:
        return self._read_jsonl(self._crew_dir(crew_id) / file_name)

    def finalize_crew(self, crew_id: str, status: CrewStatus, final_summary: str) -> None:
        ended_at = utc_now()
        crew = self.update_crew(
            crew_id,
            {"status": status.value, "final_summary": final_summary, "ended_at": ended_at},
        )
        self._write_json(
            self._crew_dir(crew_id) / "final_report.json",
            {
                "crew_id": crew_id,
                "status": crew["status"],
                "final_summary": final_summary,
                "ended_at": ended_at,
            },
        )

    def read_crew(self, crew_id: str) -> dict[str, Any]:
        crew_dir = self._crew_dir(crew_id)
        if not crew_dir.is_dir():
            raise FileNotFoundError(f"crew not found: {crew_id}")
        return {
            "crew": self._read_json(crew_dir / "crew.json"),
            "tasks": self._read_optional_json(crew_dir / "tasks.json") or [],
            "workers": self._read_jsonl(crew_dir / "workers.jsonl"),
            "blackboard": self._read_jsonl(crew_dir / "blackboard.jsonl"),
            "events": self._read_jsonl(crew_dir / "events.jsonl"),
            "decisions": self._read_jsonl(crew_dir / "decisions.jsonl"),
            "worker_contracts": self._read_jsonl(crew_dir / "worker_contracts.jsonl"),
            "messages": self._read_jsonl(crew_dir / "messages.jsonl"),
            "protocol_requests": self._read_jsonl(crew_dir / "protocol_requests.jsonl"),
            "known_pitfalls": self._read_jsonl(crew_dir / "known_pitfalls.jsonl"),
            "message_cursors": self._read_optional_json(crew_dir / "message_cursors.json") or {},
            "team_snapshot": self._read_optional_json(crew_dir / "team_snapshot.json"),
            "final_report": self._read_optional_json(crew_dir / "final_report.json"),
            "artifacts": self._list_artifacts(crew_dir / "artifacts"),
        }

    def list_crews(self) -> list[dict[str, Any]]:
        crews = []
        for crew_dir in self._iter_crew_dirs():
            crew = self._read_json(crew_dir / "crew.json")
            crews.append(
                {
                    "crew_id": crew["crew_id"],
                    "root_goal": crew["root_goal"],
                    "status": crew["status"],
                    "summary": crew.get("final_summary") or crew.get("planner_summary", ""),
                    "created_at": crew["created_at"],
                    "ended_at": crew.get("ended_at"),
                }
            )
        return sorted(crews, key=lambda item: item["created_at"], reverse=True)

    def latest_crew_id(self) -> str | None:
        path = self._crews_root / "latest"
        if not path.exists():
            return None
        latest = path.read_text(encoding="utf-8").strip()
        return latest or None

    def _iter_crew_dirs(self) -> list[Path]:
        if not self._crews_root.exists():
            return []
        return [path for path in self._crews_root.iterdir() if path.is_dir()]

    def _crew_dir(self, crew_id: str) -> Path:
        return self._crews_root / crew_id

    def _append_jsonl(self, crew_id: str, file_name: str, payload: dict[str, Any]) -> None:
        crew_dir = self._crew_dir(crew_id)
        crew_dir.mkdir(parents=True, exist_ok=True)
        path = crew_dir / file_name
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(payload, ensure_ascii=False) + "\n")

    def _write_jsonl(self, path: Path, payloads: list[dict[str, Any]]) -> None:
        self._write_text(path, "".join(json.dumps(payload, ensure_ascii=False) + "\n" for payload in payloads))

    def _read_json(self, path: Path) -> dict[str, Any]:
        return json.loads(path.read_text(encoding="utf-8"))

    def _read_optional_json(self, path: Path) -> Any:
        if not path.exists():
            return None
        return json.loads(path.read_text(encoding="utf-8"))

    def _read_jsonl(self, path: Path) -> list[dict[str, Any]]:
        if not path.exists():
            return []
        return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]

    def _list_artifacts(self, artifacts_dir: Path) -> list[str]:
        if not artifacts_dir.exists():
            return []
        return sorted(path.relative_to(artifacts_dir).as_posix() for path in artifacts_dir.rglob("*") if path.is_file())

    def _inbox_file_name(self, recipient: str) -> str:
        return recipient.replace("/", "_").replace(":", "_")

    def _write_json(self, path: Path, payload: Any) -> None:
        self._write_text(path, json.dumps(payload, indent=2, ensure_ascii=False))

    def _write_text(self, path: Path, content: str) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp_path = path.with_name(f".{path.name}.tmp")
        tmp_path.write_text(content, encoding="utf-8")
        tmp_path.replace(path)
