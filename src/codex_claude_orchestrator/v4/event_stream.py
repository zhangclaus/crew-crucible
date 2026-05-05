"""Filesystem-backed runtime event stream for V4 turns."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any

from codex_claude_orchestrator.v4.runtime import RuntimeEvent
from codex_claude_orchestrator.v4.watchers import MarkerDetector, OutboxWatcher, TranscriptTailWatcher


class FilesystemRuntimeEventStream:
    def __init__(self, *, state_path: Path) -> None:
        self._state_path = state_path
        self._outbox = OutboxWatcher()
        self._transcript = TranscriptTailWatcher()
        self._marker = MarkerDetector()

    def initialize_turn(
        self,
        *,
        turn_id: str,
        transcript_path: Path | None = None,
    ) -> None:
        """Start transcript tailing after already-present output for this turn."""

        if transcript_path is None or not transcript_path.exists():
            return
        state = _read_state(self._state_path)
        transcript_key = _state_key(turn_id, transcript_path)
        state["transcript_offsets"].setdefault(transcript_key, transcript_path.stat().st_size)
        _write_state(self._state_path, state)

    def poll_once(
        self,
        *,
        crew_id: str,
        turn_id: str,
        worker_id: str,
        outbox_path: Path | None = None,
        transcript_path: Path | None = None,
        expected_marker: str = "",
        outbox_artifact_ref: str | None = None,
        transcript_artifact_ref: str | None = None,
        autocommit: bool = True,
    ) -> list[RuntimeEvent]:
        state = _read_state(self._state_path)
        events: list[RuntimeEvent] = []

        if outbox_path is not None and outbox_path.exists():
            outbox_key = _state_key(turn_id, outbox_path)
            digest = _sha256_bytes(outbox_path.read_bytes())
            if state["outbox_sha256"].get(outbox_key) != digest:
                for event in self._outbox.watch(
                    crew_id=crew_id,
                    turn_id=turn_id,
                    worker_id=worker_id,
                    outbox_path=outbox_path,
                    artifact_ref=outbox_artifact_ref,
                ):
                    events.append(
                        _with_stream_state(
                            event,
                            {"kind": "outbox", "key": outbox_key, "sha256": digest},
                        )
                    )
                if autocommit:
                    state["outbox_sha256"][outbox_key] = digest

        if transcript_path is not None and transcript_path.exists():
            transcript_key = _state_key(turn_id, transcript_path)
            offset = int(state["transcript_offsets"].get(transcript_key, 0))
            transcript_events, next_offset = self._transcript.watch(
                turn_id=turn_id,
                worker_id=worker_id,
                transcript_path=transcript_path,
                offset=offset,
                artifact_ref=transcript_artifact_ref,
            )
            if autocommit:
                state["transcript_offsets"][transcript_key] = next_offset
            for event in transcript_events:
                event = _with_stream_state(
                    event,
                    {
                        "kind": "transcript",
                        "key": transcript_key,
                        "next_offset": next_offset,
                    },
                )
                events.append(event)
                text = event.payload.get("text", "")
                if isinstance(text, str):
                    events.extend(
                        self._marker.detect(
                            turn_id=turn_id,
                            worker_id=worker_id,
                            text=text,
                            expected_marker=expected_marker,
                            source="filesystem_event_stream",
                            artifact_refs=event.artifact_refs,
                        )
                    )

        if autocommit:
            _write_state(self._state_path, state)
        return events

    def commit_events(self, events: list[RuntimeEvent]) -> None:
        state = _read_state(self._state_path)
        changed = False
        for event in events:
            metadata = event.payload.get("_stream_state")
            if not isinstance(metadata, dict):
                continue
            kind = metadata.get("kind")
            key = metadata.get("key")
            if kind == "outbox" and isinstance(key, str):
                sha256 = metadata.get("sha256")
                if isinstance(sha256, str):
                    state["outbox_sha256"][key] = sha256
                    changed = True
            if kind == "transcript" and isinstance(key, str):
                next_offset = metadata.get("next_offset")
                if isinstance(next_offset, int) and next_offset >= 0:
                    state["transcript_offsets"][key] = next_offset
                    changed = True
        if changed:
            _write_state(self._state_path, state)


def _read_state(path: Path) -> dict[str, Any]:
    if not path.exists():
        return _empty_state()
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return _empty_state()
    if not isinstance(payload, dict):
        return _empty_state()
    outbox_sha256 = _read_string_map(payload.get("outbox_sha256", {}))
    transcript_offsets = _read_offset_map(payload.get("transcript_offsets", {}))
    return {
        "schema_version": 1,
        "outbox_sha256": outbox_sha256,
        "transcript_offsets": transcript_offsets,
    }


def _write_state(path: Path, state: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp_path = path.with_name(f".{path.name}.tmp")
    tmp_path.write_text(json.dumps(state, ensure_ascii=False, sort_keys=True) + "\n", encoding="utf-8")
    tmp_path.replace(path)


def _empty_state() -> dict[str, Any]:
    return {
        "schema_version": 1,
        "outbox_sha256": {},
        "transcript_offsets": {},
    }


def _state_key(turn_id: str, path: Path) -> str:
    return f"{turn_id}:{path.resolve()}"


def _sha256_bytes(content: bytes) -> str:
    return hashlib.sha256(content).hexdigest()


def _with_stream_state(event: RuntimeEvent, metadata: dict[str, Any]) -> RuntimeEvent:
    payload = dict(event.payload)
    payload["_stream_state"] = metadata
    return RuntimeEvent(
        type=event.type,
        turn_id=event.turn_id,
        worker_id=event.worker_id,
        payload=payload,
        artifact_refs=event.artifact_refs,
    )


def _read_string_map(value: Any) -> dict[str, str]:
    if not isinstance(value, dict):
        return {}
    return {
        key: item
        for key, item in value.items()
        if isinstance(key, str) and isinstance(item, str)
    }


def _read_offset_map(value: Any) -> dict[str, int]:
    if not isinstance(value, dict):
        return {}
    return {
        key: item
        for key, item in value.items()
        if isinstance(key, str) and isinstance(item, int) and item >= 0
    }


__all__ = ["FilesystemRuntimeEventStream"]
