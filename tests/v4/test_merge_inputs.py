from pathlib import Path

from codex_claude_orchestrator.v4.event_store import SQLiteEventStore
from codex_claude_orchestrator.v4.merge_inputs import V4MergeInputRecorder
from codex_claude_orchestrator.v4.paths import V4Paths


def test_merge_input_recorder_writes_v4_patch_manifest_and_events(tmp_path: Path) -> None:
    paths = V4Paths(repo_root=tmp_path, crew_id="crew-1")
    store = SQLiteEventStore(tmp_path / "events.sqlite3")
    legacy_root = paths.crew_root / "artifacts"
    legacy_patch = legacy_root / "workers/worker-1/diff.patch"
    legacy_patch.parent.mkdir(parents=True, exist_ok=True)
    legacy_patch.write_text(_patch_for("src/app.py"), encoding="utf-8")

    result = V4MergeInputRecorder(event_store=store, paths=paths).record_from_changes(
        changes={
            "worker_id": "worker-1",
            "base_ref": "base-sha",
            "changed_files": ["src/app.py"],
            "artifact": "workers/worker-1/changes.json",
            "diff_artifact": "workers/worker-1/diff.patch",
        },
        turn_id="round-1-worker-1-source",
        round_id="round-1",
        contract_id="source_write",
    )

    assert result["patch_artifact"] == "workers/worker-1/patches/round-1-worker-1-source.patch"
    assert result["result_artifact"] == "workers/worker-1/results/round-1-worker-1-source.json"
    assert result["patch_paths"] == ["src/app.py"]
    assert paths.patch_path("worker-1", "round-1-worker-1-source").read_text(encoding="utf-8") == _patch_for("src/app.py")
    assert paths.result_path("worker-1", "round-1-worker-1-source").exists()
    assert [event.type for event in store.list_stream("crew-1")] == [
        "worker.patch.recorded",
        "worker.result.recorded",
    ]


def test_merge_input_recorder_is_idempotent_per_turn(tmp_path: Path) -> None:
    paths = V4Paths(repo_root=tmp_path, crew_id="crew-1")
    store = SQLiteEventStore(tmp_path / "events.sqlite3")
    legacy_patch = paths.crew_root / "artifacts/workers/worker-1/diff.patch"
    legacy_patch.parent.mkdir(parents=True, exist_ok=True)
    legacy_patch.write_text(_patch_for("src/app.py"), encoding="utf-8")
    recorder = V4MergeInputRecorder(event_store=store, paths=paths)
    changes = {
        "worker_id": "worker-1",
        "base_ref": "base-sha",
        "changed_files": ["src/app.py"],
        "artifact": "workers/worker-1/changes.json",
        "diff_artifact": "workers/worker-1/diff.patch",
    }

    first = recorder.record_from_changes(
        changes=changes,
        turn_id="round-1-worker-1-source",
        round_id="round-1",
        contract_id="source_write",
    )
    second = recorder.record_from_changes(
        changes=changes,
        turn_id="round-1-worker-1-source",
        round_id="round-1",
        contract_id="source_write",
    )

    assert second == first
    assert [event.type for event in store.list_stream("crew-1")] == [
        "worker.patch.recorded",
        "worker.result.recorded",
    ]


def _patch_for(path: str) -> str:
    return "\n".join(
        [
            f"diff --git a/{path} b/{path}",
            "index e69de29..4b825dc 100644",
            f"--- a/{path}",
            f"+++ b/{path}",
            "@@ -0,0 +1 @@",
            "+hello",
            "",
        ]
    )
