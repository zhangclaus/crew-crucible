"""V4-native merge input artifacts recorded from Codex-observed worker diffs."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any

from codex_claude_orchestrator.v4.event_store_protocol import EventStore
from codex_claude_orchestrator.v4.events import normalize
from codex_claude_orchestrator.v4.paths import V4Paths


class V4MergeInputRecorder:
    def __init__(self, *, event_store: EventStore, paths: V4Paths) -> None:
        self._events = event_store
        self._paths = paths

    def record_from_changes(
        self,
        *,
        changes: dict[str, Any],
        turn_id: str,
        round_id: str,
        contract_id: str,
    ) -> dict[str, Any]:
        worker_id = _required_string(changes, "worker_id")
        legacy_diff_artifact = _required_string(changes, "diff_artifact")
        legacy_changes_artifact = str(changes.get("artifact", ""))
        base_ref = _required_string(changes, "base_ref")
        changed_files = _string_list(changes.get("changed_files", []))

        legacy_patch_path = _resolve_relative(
            self._paths.crew_root / "artifacts",
            legacy_diff_artifact,
        )
        patch = legacy_patch_path.read_text(encoding="utf-8")
        patch_sha256 = _sha256(patch)
        patch_paths = _patch_paths(patch)

        patch_artifact = _artifact_ref(self._paths, self._paths.patch_path(worker_id, turn_id))
        result_artifact = _artifact_ref(self._paths, self._paths.result_path(worker_id, turn_id))
        manifest = {
            "schema_version": 1,
            "crew_id": self._paths.crew_id,
            "worker_id": worker_id,
            "turn_id": turn_id,
            "round_id": round_id,
            "contract_id": contract_id,
            "base_ref": base_ref,
            "changed_files": changed_files,
            "patch_artifact": patch_artifact,
            "result_artifact": result_artifact,
            "patch_sha256": patch_sha256,
            "patch_paths": patch_paths,
            "source": "codex_recorded_workspace_diff",
            "legacy_changes_artifact": legacy_changes_artifact,
            "legacy_diff_artifact": legacy_diff_artifact,
        }

        _write_text_atomic(self._paths.patch_path(worker_id, turn_id), patch)
        _write_json_atomic(self._paths.result_path(worker_id, turn_id), manifest)
        self._append_patch_recorded(
            worker_id=worker_id,
            turn_id=turn_id,
            round_id=round_id,
            contract_id=contract_id,
            manifest=manifest,
        )
        self._append_result_recorded(
            worker_id=worker_id,
            turn_id=turn_id,
            round_id=round_id,
            contract_id=contract_id,
            manifest=manifest,
        )
        return manifest

    def _append_patch_recorded(
        self,
        *,
        worker_id: str,
        turn_id: str,
        round_id: str,
        contract_id: str,
        manifest: dict[str, Any],
    ) -> None:
        payload = {
            "patch_artifact": manifest["patch_artifact"],
            "patch_sha256": manifest["patch_sha256"],
            "patch_paths": manifest["patch_paths"],
            "source": manifest["source"],
            "legacy_diff_artifact": manifest["legacy_diff_artifact"],
        }
        self._events.append(
            stream_id=self._paths.crew_id,
            type="worker.patch.recorded",
            crew_id=self._paths.crew_id,
            worker_id=worker_id,
            turn_id=turn_id,
            round_id=round_id,
            contract_id=contract_id,
            idempotency_key=f"{self._paths.crew_id}/{worker_id}/{turn_id}/worker.patch.recorded",
            payload=payload,
            artifact_refs=[manifest["patch_artifact"]],
        )

    def _append_result_recorded(
        self,
        *,
        worker_id: str,
        turn_id: str,
        round_id: str,
        contract_id: str,
        manifest: dict[str, Any],
    ) -> None:
        self._events.append(
            stream_id=self._paths.crew_id,
            type="worker.result.recorded",
            crew_id=self._paths.crew_id,
            worker_id=worker_id,
            turn_id=turn_id,
            round_id=round_id,
            contract_id=contract_id,
            idempotency_key=f"{self._paths.crew_id}/{worker_id}/{turn_id}/worker.result.recorded",
            payload=manifest,
            artifact_refs=[manifest["result_artifact"], manifest["patch_artifact"]],
        )


def _required_string(payload: dict[str, Any], key: str) -> str:
    value = payload.get(key)
    if not isinstance(value, str) or not value:
        raise ValueError(f"{key} is required")
    return value


def _string_list(value: Any) -> list[str]:
    if not isinstance(value, list):
        raise ValueError("changed_files must be a list")
    if any(not isinstance(item, str) for item in value):
        raise ValueError("changed_files must be a list of strings")
    return list(value)


def _resolve_relative(root: Path, artifact_ref: str) -> Path:
    relative = Path(artifact_ref)
    if not artifact_ref or relative.is_absolute() or ".." in relative.parts:
        raise ValueError("artifact ref must be relative")
    resolved = (root / relative).resolve()
    root = root.resolve()
    if not resolved.is_relative_to(root):
        raise ValueError("artifact ref must be relative")
    return resolved


def _artifact_ref(paths: V4Paths, path: Path) -> str:
    return path.relative_to(paths.artifact_root).as_posix()


def _write_text_atomic(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp_path = path.with_name(f".{path.name}.tmp")
    tmp_path.write_text(content, encoding="utf-8")
    tmp_path.replace(path)


def _write_json_atomic(path: Path, payload: dict[str, Any]) -> None:
    _write_text_atomic(
        path,
        json.dumps(normalize(payload), ensure_ascii=False, indent=2, sort_keys=True) + "\n",
    )


def _sha256(content: str) -> str:
    return hashlib.sha256(content.encode("utf-8")).hexdigest()


def _patch_paths(patch: str) -> list[str]:
    paths: list[str] = []
    for line in patch.splitlines():
        if line.startswith("diff --git "):
            parts = line.split()
            if len(parts) >= 4:
                paths.extend([_clean_diff_path(parts[2]), _clean_diff_path(parts[3])])
        elif line.startswith(("--- ", "+++ ")):
            parts = line.split()
            if len(parts) >= 2:
                paths.append(_clean_diff_path(parts[1]))
    safe_paths = [path for path in paths if path and _safe_relative_path(path)]
    return sorted(set(safe_paths))


def _clean_diff_path(value: str) -> str:
    if value in {"/dev/null", "a/dev/null", "b/dev/null"}:
        return ""
    if value.startswith(("a/", "b/")):
        return value[2:]
    return value


def _safe_relative_path(value: str) -> bool:
    path = Path(value)
    return not path.is_absolute() and ".." not in path.parts


__all__ = ["V4MergeInputRecorder"]
