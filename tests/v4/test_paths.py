from __future__ import annotations

from pathlib import Path

import pytest

from codex_claude_orchestrator.v4.paths import V4Paths


def test_v4_paths_use_canonical_artifact_root(tmp_path: Path) -> None:
    paths = V4Paths(repo_root=tmp_path, crew_id="crew-1")

    assert paths.state_root == tmp_path / ".orchestrator"
    assert paths.crew_root == tmp_path / ".orchestrator" / "crews" / "crew-1"
    assert (
        paths.artifact_root
        == tmp_path / ".orchestrator" / "crews" / "crew-1" / "artifacts" / "v4"
    )


def test_v4_paths_resolve_worker_artifacts(tmp_path: Path) -> None:
    paths = V4Paths(repo_root=tmp_path, crew_id="crew-1")

    assert (
        paths.worker_root("worker-1")
        == paths.artifact_root / "workers" / "worker-1"
    )
    assert (
        paths.inbox_path("worker-1", "message-1")
        == paths.artifact_root / "workers" / "worker-1" / "inbox" / "message-1.json"
    )
    assert (
        paths.outbox_path("worker-1", "turn-1")
        == paths.artifact_root / "workers" / "worker-1" / "outbox" / "turn-1.json"
    )
    assert (
        paths.patch_path("worker-1", "turn-1")
        == paths.artifact_root / "workers" / "worker-1" / "patches" / "turn-1.patch"
    )
    assert (
        paths.changes_path("worker-1", "turn-1")
        == paths.artifact_root / "workers" / "worker-1" / "changes" / "turn-1.json"
    )


def test_v4_paths_resolve_crew_artifacts(tmp_path: Path) -> None:
    paths = V4Paths(repo_root=tmp_path, crew_id="crew-1")

    assert paths.merge_path("merge-1") == paths.artifact_root / "merge" / "merge-1.json"
    assert (
        paths.projection_path("crew")
        == paths.artifact_root / "projections" / "crew.json"
    )


@pytest.mark.parametrize(
    "unsafe_id",
    ["", ".", " ", "\t", "/absolute", "../crew", "crew/one", "crew:one"],
)
def test_v4_paths_reject_unsafe_crew_ids(tmp_path: Path, unsafe_id: str) -> None:
    with pytest.raises(ValueError, match="unsafe"):
        V4Paths(repo_root=tmp_path, crew_id=unsafe_id)


@pytest.mark.parametrize(
    "unsafe_id",
    ["", ".", " ", "\t", "/absolute", "../turn", "turn/one", "turn:one"],
)
def test_v4_paths_reject_unsafe_method_ids(tmp_path: Path, unsafe_id: str) -> None:
    paths = V4Paths(repo_root=tmp_path, crew_id="crew-1")

    with pytest.raises(ValueError, match="unsafe"):
        paths.outbox_path("worker-1", unsafe_id)
