"""Filesystem artifact storage for the durable V4 runtime."""

from __future__ import annotations

from dataclasses import dataclass
import json
from pathlib import Path
from typing import Any


@dataclass(frozen=True, slots=True)
class ArtifactRef:
    path: str
    media_type: str

    def to_dict(self) -> dict[str, str]:
        return {
            "path": self.path,
            "media_type": self.media_type,
        }


class ArtifactStore:
    def __init__(self, root: Path) -> None:
        self.root = root.resolve()
        self.root.mkdir(parents=True, exist_ok=True)

    def write_json(self, artifact_path: str, payload: Any) -> ArtifactRef:
        path = self._resolve(artifact_path)
        path.write_text(
            json.dumps(payload, ensure_ascii=False, sort_keys=True),
            encoding="utf-8",
        )
        return ArtifactRef(path=artifact_path, media_type="application/json")

    def write_text(
        self,
        artifact_path: str,
        content: str,
        media_type: str = "text/plain",
    ) -> ArtifactRef:
        path = self._resolve(artifact_path)
        path.write_text(content, encoding="utf-8")
        return ArtifactRef(path=artifact_path, media_type=media_type)

    def read_text(self, artifact_path: str) -> str:
        return self._resolve(artifact_path).read_text(encoding="utf-8", errors="replace")

    def _resolve(self, artifact_path: str) -> Path:
        relative_path = Path(artifact_path)
        if not artifact_path or relative_path.is_absolute() or ".." in relative_path.parts:
            raise ValueError("artifact path must be relative")

        resolved = (self.root / relative_path).resolve()
        if not resolved.is_relative_to(self.root):
            raise ValueError("artifact path must be relative")

        resolved.parent.mkdir(parents=True, exist_ok=True)
        return resolved
