from codex_claude_orchestrator.crew.merge_arbiter import MergeArbiter


def test_merge_arbiter_detects_overlapping_changed_files():
    plan = MergeArbiter().build_plan(
        "crew-1",
        changed_files_by_worker={
            "worker-a": ["src/app.py"],
            "worker-b": ["src/app.py"],
        },
    )

    assert plan["can_merge"] is False
    assert plan["conflicts"] == [{"path": "src/app.py", "workers": ["worker-a", "worker-b"]}]
    assert plan["recommendation"] == "requires_codex_decision"


def test_merge_arbiter_allows_non_overlapping_changed_files():
    plan = MergeArbiter().build_plan(
        "crew-1",
        changed_files_by_worker={
            "worker-a": ["src/app.py"],
            "worker-b": ["tests/test_app.py"],
        },
    )

    assert plan["can_merge"] is True
    assert plan["conflicts"] == []
    assert plan["recommendation"] == "ready_for_codex_review"
