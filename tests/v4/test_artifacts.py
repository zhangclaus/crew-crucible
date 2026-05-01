from __future__ import annotations

import json
from pathlib import Path

import pytest

from codex_claude_orchestrator.v4.artifacts import ArtifactRef, ArtifactStore


def test_artifact_store_writes_json_and_text_artifacts(tmp_path: Path) -> None:
    store = ArtifactStore(tmp_path / "artifacts")

    json_ref = store.write_json("crew-1/result.json", {"z": "雪", "a": 1})
    text_ref = store.write_text("crew-1/logs/output.txt", "hello\n世界", media_type="text/markdown")

    assert json_ref == ArtifactRef(path="crew-1/result.json", media_type="application/json")
    assert json_ref.to_dict() == {
        "path": "crew-1/result.json",
        "media_type": "application/json",
    }
    assert text_ref == ArtifactRef(path="crew-1/logs/output.txt", media_type="text/markdown")
    assert (tmp_path / "artifacts" / "crew-1" / "result.json").read_text(encoding="utf-8") == (
        json.dumps({"z": "雪", "a": 1}, ensure_ascii=False, sort_keys=True)
    )
    assert store.read_text("crew-1/logs/output.txt") == "hello\n世界"


@pytest.mark.parametrize("artifact_path", ["", "/absolute.txt", "crew/../secret.txt"])
def test_artifact_store_blocks_non_relative_artifact_paths(
    tmp_path: Path,
    artifact_path: str,
) -> None:
    store = ArtifactStore(tmp_path / "artifacts")

    with pytest.raises(ValueError, match="artifact path must be relative"):
        store.write_text(artifact_path, "blocked")
