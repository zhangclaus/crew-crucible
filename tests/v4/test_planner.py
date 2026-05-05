from codex_claude_orchestrator.v4.planner import PlannerPolicy


def test_planner_selects_highest_quality_compatible_source_worker() -> None:
    workers = [
        {
            "worker_id": "worker-low",
            "status": "running",
            "authority_level": "source_write",
            "capabilities": ["edit_source", "run_verification"],
            "write_scope": ["src/"],
        },
        {
            "worker_id": "worker-high",
            "status": "running",
            "authority_level": "source_write",
            "capabilities": ["edit_source", "run_verification"],
            "write_scope": ["src/"],
        },
    ]

    selected = PlannerPolicy().select_worker(
        workers=workers,
        required_authority="source_write",
        required_capabilities=["edit_source"],
        requested_write_scope=["src/"],
        worker_quality_scores={"worker-low": -3, "worker-high": 4},
    )

    assert selected["worker_id"] == "worker-high"


def test_planner_rejects_worker_with_incompatible_write_scope() -> None:
    selected = PlannerPolicy().select_worker(
        workers=[
            {
                "worker_id": "worker-docs",
                "status": "running",
                "authority_level": "source_write",
                "capabilities": ["edit_source"],
                "write_scope": ["docs/"],
            }
        ],
        required_authority="source_write",
        required_capabilities=["edit_source"],
        requested_write_scope=["src/"],
        worker_quality_scores={"worker-docs": 10},
    )

    assert selected is None
