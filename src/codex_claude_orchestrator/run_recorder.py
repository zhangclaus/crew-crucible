from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from codex_claude_orchestrator.models import (
    EvaluationOutcome,
    EventRecord,
    RunRecord,
    TaskRecord,
    TaskStatus,
    WorkerResult,
    utc_now,
)


class RunRecorder:
    def __init__(self, state_root: Path):
        self._state_root = state_root
        self._runs_root = state_root / "runs"
        self._runs_root.mkdir(parents=True, exist_ok=True)

    def start_run(self, run: RunRecord, task: TaskRecord, compiled_prompt: Any | None = None) -> Path:
        run_dir = self._run_dir(run.run_id)
        run_dir.mkdir(parents=True, exist_ok=True)
        self._write_json(run_dir / "task.json", task.to_dict())
        self._write_json(run_dir / "run.json", run.to_dict())
        if compiled_prompt is not None:
            prompt_text = "\n\n".join(
                [
                    "SYSTEM:",
                    compiled_prompt.system_prompt,
                    "USER:",
                    compiled_prompt.user_prompt,
                ]
            )
            self.write_text_artifact(run.run_id, "prompt.txt", prompt_text)
            self._write_json(run_dir / "artifacts" / "prompt_metadata.json", compiled_prompt.metadata)
            self._write_json(run_dir / "artifacts" / "output_schema.json", compiled_prompt.schema)
        return run_dir

    def append_event(self, run_id: str, event: EventRecord) -> None:
        run_dir = self._run_dir(run_id)
        run_dir.mkdir(parents=True, exist_ok=True)
        events_path = run_dir / "events.jsonl"
        with events_path.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(event.to_dict(), ensure_ascii=False) + "\n")

    def write_result(self, run_id: str, result: WorkerResult, evaluation: EvaluationOutcome) -> None:
        run_dir = self._run_dir(run_id)
        run_dir.mkdir(parents=True, exist_ok=True)
        self._write_json(run_dir / "result.json", result.to_dict())
        self._write_json(run_dir / "evaluation.json", evaluation.to_dict())
        self._finalize_run(run_dir / "run.json", evaluation)
        self.write_text_artifact(run_id, "stdout.txt", result.stdout)
        self.write_text_artifact(run_id, "stderr.txt", result.stderr)

    def list_runs(self) -> list[dict[str, Any]]:
        runs = [self._run_summary(path.name) for path in self._iter_run_dirs()]
        return sorted(runs, key=lambda item: item["started_at"], reverse=True)

    def read_run(self, run_id: str) -> dict[str, Any]:
        run_dir = self._run_dir(run_id)
        if not run_dir.is_dir():
            raise FileNotFoundError(f"run not found: {run_id}")

        return {
            "task": self._read_json(run_dir / "task.json"),
            "run": self._read_json(run_dir / "run.json"),
            "result": self._read_optional_json(run_dir / "result.json"),
            "evaluation": self._read_optional_json(run_dir / "evaluation.json"),
            "events": self._read_events(run_dir / "events.jsonl"),
            "artifacts": self._list_artifacts(run_dir / "artifacts"),
        }

    def write_text_artifact(self, run_id: str, artifact_name: str, content: str) -> Path:
        artifacts_dir = self._run_dir(run_id) / "artifacts"
        artifacts_dir.mkdir(parents=True, exist_ok=True)
        artifact_path = artifacts_dir / artifact_name
        self._write_text(artifact_path, content)
        return artifact_path

    def _iter_run_dirs(self) -> list[Path]:
        if not self._runs_root.exists():
            return []
        return [path for path in self._runs_root.iterdir() if path.is_dir()]

    def _run_summary(self, run_id: str) -> dict[str, Any]:
        payload = self.read_run(run_id)
        run = payload["run"]
        task = payload["task"]
        evaluation = payload["evaluation"] or {}
        return {
            "run_id": run["run_id"],
            "task_id": task["task_id"],
            "agent": run["agent"],
            "status": run["status"],
            "accepted": evaluation.get("accepted"),
            "next_action": evaluation.get("next_action"),
            "summary": evaluation.get("summary") or run.get("result_summary", ""),
            "started_at": run["started_at"],
        }

    def _finalize_run(self, run_path: Path, evaluation: EvaluationOutcome) -> None:
        if not run_path.exists():
            return
        run = self._read_json(run_path)
        if evaluation.accepted:
            status = TaskStatus.COMPLETED.value
        elif evaluation.needs_human:
            status = TaskStatus.NEEDS_REVIEW.value
        else:
            status = TaskStatus.FAILED.value
        evaluation_data = evaluation.to_dict()
        run.update(
            {
                "ended_at": utc_now(),
                "status": status,
                "result_summary": evaluation.summary,
                "failure_class": evaluation_data["failure_class"],
                "next_action": evaluation_data["next_action"],
            }
        )
        self._write_json(run_path, run)

    def _run_dir(self, run_id: str) -> Path:
        return self._runs_root / run_id

    def _read_json(self, path: Path) -> dict[str, Any]:
        return json.loads(path.read_text(encoding="utf-8"))

    def _read_optional_json(self, path: Path) -> dict[str, Any] | None:
        if not path.exists():
            return None
        return self._read_json(path)

    def _read_events(self, path: Path) -> list[dict[str, Any]]:
        if not path.exists():
            return []
        return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]

    def _list_artifacts(self, artifacts_dir: Path) -> list[str]:
        if not artifacts_dir.exists():
            return []
        return sorted(path.relative_to(artifacts_dir).as_posix() for path in artifacts_dir.rglob("*") if path.is_file())

    def _write_json(self, path: Path, payload: dict) -> None:
        self._write_text(path, json.dumps(payload, indent=2, ensure_ascii=False))

    def _write_text(self, path: Path, content: str) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp_path = path.with_name(f".{path.name}.tmp")
        tmp_path.write_text(content, encoding="utf-8")
        tmp_path.replace(path)
