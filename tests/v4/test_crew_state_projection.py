"""Tests for CrewStateProjection."""

from __future__ import annotations

import pytest

from codex_claude_orchestrator.v4.crew_state_projection import CrewStateProjection
from codex_claude_orchestrator.v4.events import AgentEvent


def _make_event(
    event_id: str,
    type: str,
    stream_id: str = "crew-1",
    crew_id: str = "crew-1",
    worker_id: str = "",
    turn_id: str = "",
    round_id: str = "",
    contract_id: str = "",
    idempotency_key: str = "",
    payload: dict | None = None,
    sequence: int = 1,
    created_at: str = "2026-05-06T00:00:00Z",
) -> AgentEvent:
    return AgentEvent(
        event_id=event_id,
        stream_id=stream_id,
        sequence=sequence,
        type=type,
        crew_id=crew_id,
        worker_id=worker_id,
        turn_id=turn_id,
        round_id=round_id,
        contract_id=contract_id,
        idempotency_key=idempotency_key,
        payload=payload or {},
        created_at=created_at,
    )


class TestCrewStateProjectionFromEvents:
    """Test from_events factory and basic crew lifecycle."""

    def test_from_events_with_crew_started(self) -> None:
        event = _make_event(
            "evt-1",
            "crew.started",
            idempotency_key="crew-1/crew.started",
            payload={"goal": "fix bugs", "repo": "/tmp/repo"},
        )
        proj = CrewStateProjection.from_events([event])

        assert proj.crew["crew_id"] == "crew-1"
        assert proj.crew["root_goal"] == "fix bugs"
        assert proj.crew["repo"] == "/tmp/repo"
        assert proj.crew["status"] == "running"
        assert proj.has_events() is True

    def test_from_events_empty_list(self) -> None:
        proj = CrewStateProjection.from_events([])
        assert proj.has_events() is False
        assert proj.crew == {}

    def test_crew_updated_merges_payload(self) -> None:
        events = [
            _make_event(
                "evt-1",
                "crew.started",
                payload={"goal": "fix bugs", "repo": "/tmp"},
            ),
            _make_event(
                "evt-2",
                "crew.updated",
                payload={"merge_summary": "auto-merge"},
                sequence=2,
            ),
        ]
        proj = CrewStateProjection.from_events(events)

        assert proj.crew["merge_summary"] == "auto-merge"
        assert proj.crew["root_goal"] == "fix bugs"
        assert proj.crew["updated_at"] == "2026-05-06T00:00:00Z"

    def test_crew_stopped_sets_cancelled_status(self) -> None:
        events = [
            _make_event("evt-1", "crew.started", payload={"goal": "test"}),
            _make_event(
                "evt-2",
                "crew.stopped",
                idempotency_key="crew-1/crew.stopped",
                sequence=2,
            ),
        ]
        proj = CrewStateProjection.from_events(events)

        assert proj.crew["status"] == "cancelled"
        assert proj.crew["ended_at"] == "2026-05-06T00:00:00Z"

    def test_crew_finalized_sets_status_and_final_summary(self) -> None:
        events = [
            _make_event("evt-1", "crew.started", payload={"goal": "test"}),
            _make_event(
                "evt-2",
                "crew.finalized",
                idempotency_key="crew-1/crew.finalized",
                payload={"status": "accepted", "final_summary": "all done"},
                sequence=2,
            ),
        ]
        proj = CrewStateProjection.from_events(events)

        assert proj.crew["status"] == "accepted"
        assert proj.crew["final_summary"] == "all done"
        assert proj.crew["ended_at"] == "2026-05-06T00:00:00Z"

    def test_crew_accepted_event(self) -> None:
        events = [
            _make_event("evt-1", "crew.started", payload={"goal": "test"}),
            _make_event(
                "evt-2",
                "crew.accepted",
                idempotency_key="crew-1/crew.accepted",
                payload={"summary": "looks good"},
                sequence=2,
            ),
        ]
        proj = CrewStateProjection.from_events(events)

        assert proj.crew["status"] == "accepted"
        assert proj.crew["final_summary"] == "looks good"

    def test_crew_ready_for_accept(self) -> None:
        events = [
            _make_event("evt-1", "crew.started", payload={"goal": "test"}),
            _make_event(
                "evt-2",
                "crew.ready_for_accept",
                idempotency_key="crew-1/crew.ready_for_accept",
                sequence=2,
            ),
        ]
        proj = CrewStateProjection.from_events(events)

        assert proj.crew["status"] == "ready"

    def test_ready_for_accept_does_not_override_accepted(self) -> None:
        events = [
            _make_event("evt-1", "crew.started", payload={"goal": "test"}),
            _make_event(
                "evt-2",
                "crew.accepted",
                payload={"summary": "ok"},
                sequence=2,
            ),
            _make_event(
                "evt-3",
                "crew.ready_for_accept",
                sequence=3,
            ),
        ]
        proj = CrewStateProjection.from_events(events)

        assert proj.crew["status"] == "accepted"

    def test_human_required_sets_needs_human(self) -> None:
        events = [
            _make_event("evt-1", "crew.started", payload={"goal": "test"}),
            _make_event(
                "evt-2",
                "human.required",
                sequence=2,
            ),
        ]
        proj = CrewStateProjection.from_events(events)

        assert proj.crew["status"] == "needs_human"

    def test_human_required_does_not_override_accepted(self) -> None:
        events = [
            _make_event("evt-1", "crew.started", payload={"goal": "test"}),
            _make_event("evt-2", "crew.accepted", payload={"summary": "ok"}, sequence=2),
            _make_event("evt-3", "human.required", sequence=3),
        ]
        proj = CrewStateProjection.from_events(events)

        assert proj.crew["status"] == "accepted"


class TestWorkerEvents:
    """Test worker lifecycle event handling."""

    def test_worker_spawned(self) -> None:
        events = [
            _make_event("evt-1", "crew.started", payload={"goal": "test"}),
            _make_event(
                "evt-2",
                "worker.spawned",
                worker_id="worker-1",
                idempotency_key="crew-1/worker.spawned/worker-1",
                payload={"role": "implementer", "workspace_path": "/tmp/ws1"},
                sequence=2,
            ),
        ]
        proj = CrewStateProjection.from_events(events)

        assert len(proj.workers) == 1
        assert proj.workers[0]["worker_id"] == "worker-1"
        assert proj.workers[0]["role"] == "implementer"
        assert proj.workers[0]["workspace_path"] == "/tmp/ws1"
        assert proj.workers[0]["status"] == "running"

    def test_worker_claimed_updates_status_to_busy(self) -> None:
        events = [
            _make_event("evt-1", "crew.started", payload={"goal": "test"}),
            _make_event(
                "evt-2",
                "worker.spawned",
                worker_id="worker-1",
                payload={"role": "implementer"},
                sequence=2,
            ),
            _make_event(
                "evt-3",
                "worker.claimed",
                worker_id="worker-1",
                idempotency_key="crew-1/worker.claimed/worker-1",
                sequence=3,
            ),
        ]
        proj = CrewStateProjection.from_events(events)

        assert proj.workers[0]["status"] == "busy"

    def test_worker_released_updates_status_to_idle(self) -> None:
        events = [
            _make_event("evt-1", "crew.started", payload={"goal": "test"}),
            _make_event(
                "evt-2",
                "worker.spawned",
                worker_id="worker-1",
                payload={"role": "implementer"},
                sequence=2,
            ),
            _make_event(
                "evt-3",
                "worker.claimed",
                worker_id="worker-1",
                sequence=3,
            ),
            _make_event(
                "evt-4",
                "worker.released",
                worker_id="worker-1",
                idempotency_key="crew-1/worker.released/worker-1",
                sequence=4,
            ),
        ]
        proj = CrewStateProjection.from_events(events)

        assert proj.workers[0]["status"] == "idle"

    def test_worker_stopped_updates_status(self) -> None:
        events = [
            _make_event("evt-1", "crew.started", payload={"goal": "test"}),
            _make_event(
                "evt-2",
                "worker.spawned",
                worker_id="worker-1",
                payload={"role": "implementer"},
                sequence=2,
            ),
            _make_event(
                "evt-3",
                "worker.stopped",
                worker_id="worker-1",
                idempotency_key="crew-1/worker.stopped/worker-1",
                sequence=3,
            ),
        ]
        proj = CrewStateProjection.from_events(events)

        assert proj.workers[0]["status"] == "stopped"

    def test_worker_status_transitions_claimed_released_stopped(self) -> None:
        events = [
            _make_event("evt-1", "crew.started", payload={"goal": "test"}),
            _make_event(
                "evt-2",
                "worker.spawned",
                worker_id="w-1",
                payload={"role": "reviewer"},
                sequence=2,
            ),
            _make_event("evt-3", "worker.claimed", worker_id="w-1", sequence=3),
            _make_event("evt-4", "worker.released", worker_id="w-1", sequence=4),
            _make_event("evt-5", "worker.claimed", worker_id="w-1", sequence=5),
            _make_event("evt-6", "worker.stopped", worker_id="w-1", sequence=6),
        ]
        proj = CrewStateProjection.from_events(events)

        assert proj.workers[0]["status"] == "stopped"

    def test_worker_contract_recorded(self) -> None:
        events = [
            _make_event("evt-1", "crew.started", payload={"goal": "test"}),
            _make_event(
                "evt-2",
                "worker.contract.recorded",
                contract_id="contract-1",
                idempotency_key="crew-1/worker.contract.recorded/contract-1",
                payload={"label": "fix auth", "mission": "implement OAuth2"},
                sequence=2,
            ),
        ]
        proj = CrewStateProjection.from_events(events)

        assert len(proj.worker_contracts) == 1
        assert proj.worker_contracts[0]["contract_id"] == "contract-1"
        assert proj.worker_contracts[0]["label"] == "fix auth"
        assert proj.worker_contracts[0]["mission"] == "implement OAuth2"

    def test_update_worker_missing_id_does_not_crash(self) -> None:
        events = [
            _make_event("evt-1", "crew.started", payload={"goal": "test"}),
            _make_event(
                "evt-2",
                "worker.spawned",
                worker_id="worker-1",
                payload={"role": "implementer"},
                sequence=2,
            ),
            _make_event(
                "evt-3",
                "worker.claimed",
                worker_id="worker-nonexistent",
                sequence=3,
            ),
        ]
        # Should not raise
        proj = CrewStateProjection.from_events(events)
        assert proj.workers[0]["status"] == "running"


class TestBlackboardAndDecisions:
    """Test blackboard entry and decision recording events."""

    def test_blackboard_entry(self) -> None:
        events = [
            _make_event("evt-1", "crew.started", payload={"goal": "test"}),
            _make_event(
                "evt-2",
                "blackboard.entry",
                idempotency_key="crew-1/blackboard/entry-abc",
                payload={"entry_type": "decision", "content": "chose X over Y"},
                sequence=2,
            ),
        ]
        proj = CrewStateProjection.from_events(events)

        assert len(proj.blackboard) == 1
        assert proj.blackboard[0]["entry_id"] == "entry-abc"
        assert proj.blackboard[0]["type"] == "decision"
        assert proj.blackboard[0]["content"] == "chose X over Y"

    def test_decision_recorded(self) -> None:
        events = [
            _make_event("evt-1", "crew.started", payload={"goal": "test"}),
            _make_event(
                "evt-2",
                "decision.recorded",
                idempotency_key="crew-1/decision/action-123",
                payload={"action_type": "spawn_worker", "reason": "need reviewer"},
                sequence=2,
            ),
        ]
        proj = CrewStateProjection.from_events(events)

        assert len(proj.decisions) == 1
        assert proj.decisions[0]["action_id"] == "action-123"
        assert proj.decisions[0]["action_type"] == "spawn_worker"
        assert proj.decisions[0]["reason"] == "need reviewer"


class TestTasksAndArtifacts:
    """Test task and artifact events."""

    def test_task_created(self) -> None:
        events = [
            _make_event("evt-1", "crew.started", payload={"goal": "test"}),
            _make_event(
                "evt-2",
                "task.created",
                idempotency_key="crew-1/task.created/task-abc",
                payload={"title": "Implement auth module"},
                sequence=2,
            ),
        ]
        proj = CrewStateProjection.from_events(events)

        assert len(proj.tasks) == 1
        assert proj.tasks[0]["task_id"] == "task-abc"
        assert proj.tasks[0]["title"] == "Implement auth module"
        assert proj.tasks[0]["status"] == "pending"

    def test_artifact_written_deduplicates(self) -> None:
        events = [
            _make_event("evt-1", "crew.started", payload={"goal": "test"}),
            _make_event(
                "evt-2",
                "artifact.written",
                payload={"artifact_name": "onboarding_prompt.md"},
                sequence=2,
            ),
            _make_event(
                "evt-3",
                "artifact.written",
                payload={"artifact_name": "onboarding_prompt.md"},
                idempotency_key="crew-1/artifact/onboarding_prompt.md/abc",
                sequence=3,
            ),
            _make_event(
                "evt-4",
                "artifact.written",
                payload={"artifact_name": "changes.json"},
                idempotency_key="crew-1/artifact/changes.json/def",
                sequence=4,
            ),
        ]
        proj = CrewStateProjection.from_events(events)

        assert proj.artifacts == ["onboarding_prompt.md", "changes.json"]

    def test_pitfall_recorded(self) -> None:
        events = [
            _make_event("evt-1", "crew.started", payload={"goal": "test"}),
            _make_event(
                "evt-2",
                "pitfall.recorded",
                idempotency_key="crew-1/pitfall/race-condition/abc123",
                payload={
                    "failure_class": "race-condition",
                    "summary": "concurrent writes to same file",
                    "guardrail": "use file locks",
                },
                sequence=2,
            ),
        ]
        proj = CrewStateProjection.from_events(events)

        assert len(proj.known_pitfalls) == 1
        assert proj.known_pitfalls[0]["failure_class"] == "race-condition"
        assert proj.known_pitfalls[0]["guardrail"] == "use file locks"


class TestToReadCrewDict:
    """Test to_read_crew_dict output shape."""

    def test_output_shape_has_all_required_keys(self) -> None:
        event = _make_event(
            "evt-1",
            "crew.started",
            payload={"goal": "test", "repo": "/tmp"},
        )
        proj = CrewStateProjection.from_events([event])
        result = proj.to_read_crew_dict()

        expected_keys = {
            "crew",
            "tasks",
            "workers",
            "blackboard",
            "events",
            "decisions",
            "worker_contracts",
            "messages",
            "protocol_requests",
            "known_pitfalls",
            "message_cursors",
            "team_snapshot",
            "final_report",
            "artifacts",
            "challenges",
            "verifications",
            "reviews",
        }
        assert set(result.keys()) == expected_keys

    def test_final_report_none_when_not_ended(self) -> None:
        event = _make_event("evt-1", "crew.started", payload={"goal": "test"})
        proj = CrewStateProjection.from_events([event])

        assert proj.to_read_crew_dict()["final_report"] is None

    def test_final_report_populated_when_ended(self) -> None:
        events = [
            _make_event("evt-1", "crew.started", payload={"goal": "test"}),
            _make_event(
                "evt-2",
                "crew.finalized",
                idempotency_key="crew-1/crew.finalized",
                payload={"status": "accepted", "final_summary": "done"},
                sequence=2,
            ),
        ]
        proj = CrewStateProjection.from_events(events)
        result = proj.to_read_crew_dict()

        assert result["final_report"] is not None
        assert result["final_report"]["status"] == "accepted"
        assert result["final_report"]["final_summary"] == "done"

    def test_messages_and_protocol_requests_are_empty_lists(self) -> None:
        event = _make_event("evt-1", "crew.started", payload={"goal": "test"})
        proj = CrewStateProjection.from_events([event])
        result = proj.to_read_crew_dict()

        assert result["messages"] == []
        assert result["protocol_requests"] == []

    def test_team_snapshot_is_none(self) -> None:
        event = _make_event("evt-1", "crew.started", payload={"goal": "test"})
        proj = CrewStateProjection.from_events([event])
        result = proj.to_read_crew_dict()

        assert result["team_snapshot"] is None


class TestEventsList:
    """Test that all events are stored in the events list."""

    def test_all_events_recorded(self) -> None:
        events = [
            _make_event("evt-1", "crew.started", payload={"goal": "test"}),
            _make_event("evt-2", "turn.requested", worker_id="w-1", turn_id="t-1", sequence=2),
            _make_event("evt-3", "turn.completed", worker_id="w-1", turn_id="t-1", sequence=3),
            _make_event(
                "evt-4",
                "verification.passed",
                idempotency_key="crew-1/verification/v1",
                sequence=4,
            ),
            _make_event(
                "evt-5",
                "review.completed",
                idempotency_key="crew-1/review/r1",
                sequence=5,
            ),
        ]
        proj = CrewStateProjection.from_events(events)

        assert len(proj.events) == 5
        assert proj.events[0]["type"] == "crew.started"
        assert proj.events[1]["type"] == "turn.requested"
        assert proj.events[2]["type"] == "turn.completed"
        assert proj.events[3]["type"] == "verification.passed"
        assert proj.events[4]["type"] == "review.completed"

    def test_unknown_event_types_do_not_crash(self) -> None:
        events = [
            _make_event("evt-1", "crew.started", payload={"goal": "test"}),
            _make_event("evt-2", "some.future.event", payload={"data": 1}, sequence=2),
            _make_event("evt-3", "another.unknown.type", sequence=3),
        ]
        # Should not raise
        proj = CrewStateProjection.from_events(events)

        assert len(proj.events) == 3
        assert proj.has_events() is True


class TestHasEvents:
    """Test has_events() behavior."""

    def test_has_events_false_for_empty(self) -> None:
        proj = CrewStateProjection()
        assert proj.has_events() is False

    def test_has_events_false_for_non_crew_events(self) -> None:
        events = [
            _make_event("evt-1", "turn.requested", crew_id=""),
        ]
        proj = CrewStateProjection.from_events(events)
        assert proj.has_events() is False

    def test_has_events_true_after_crew_started(self) -> None:
        events = [
            _make_event("evt-1", "crew.started", payload={"goal": "test"}),
        ]
        proj = CrewStateProjection.from_events(events)
        assert proj.has_events() is True


class TestEventTypeMismatch:
    """H13: verification/challenge/review event types must update projection state."""

    def test_verification_passed_tracked_in_projection(self) -> None:
        """H13: verification.passed events must update projection state."""
        events = [
            _make_event("e1", "crew.started", payload={"goal": "test"}),
            _make_event(
                "e2",
                "verification.passed",
                worker_id="w1",
                round_id="r1",
                payload={"command": "pytest"},
                sequence=2,
            ),
        ]
        proj = CrewStateProjection.from_events(events)
        assert len(proj.verifications) == 1
        assert proj.verifications[0]["worker_id"] == "w1"
        assert proj.verifications[0]["passed"] is True
        assert proj.verifications[0]["command"] == "pytest"

    def test_verification_failed_tracked_in_projection(self) -> None:
        """H13: verification.failed events must update projection state."""
        events = [
            _make_event("e1", "crew.started", payload={"goal": "test"}),
            _make_event(
                "e2",
                "verification.failed",
                worker_id="w1",
                round_id="r1",
                payload={"command": "pytest"},
                sequence=2,
            ),
        ]
        proj = CrewStateProjection.from_events(events)
        assert len(proj.verifications) == 1
        assert proj.verifications[0]["passed"] is False

    def test_challenge_issued_tracked_in_projection(self) -> None:
        """H13: challenge.issued events must update projection state."""
        events = [
            _make_event("e1", "crew.started", payload={"goal": "test"}),
            _make_event(
                "e2",
                "challenge.issued",
                worker_id="w1",
                round_id="r1",
                payload={"finding": "bad code", "category": "review"},
                sequence=2,
            ),
        ]
        proj = CrewStateProjection.from_events(events)
        assert len(proj.challenges) == 1
        assert proj.challenges[0]["finding"] == "bad code"
        assert proj.challenges[0]["category"] == "review"

    def test_repair_requested_tracked_as_challenge(self) -> None:
        """H13: repair.requested events must be tracked in challenges."""
        events = [
            _make_event("e1", "crew.started", payload={"goal": "test"}),
            _make_event(
                "e2",
                "repair.requested",
                worker_id="w1",
                round_id="r1",
                payload={"instruction": "fix it"},
                sequence=2,
            ),
        ]
        proj = CrewStateProjection.from_events(events)
        assert len(proj.challenges) == 1
        assert proj.challenges[0]["category"] == "repair"

    def test_review_completed_tracked_in_projection(self) -> None:
        """H13: review.completed events must update projection state."""
        events = [
            _make_event("e1", "crew.started", payload={"goal": "test"}),
            _make_event(
                "e2",
                "review.completed",
                worker_id="w1",
                turn_id="t1",
                payload={"status": "ok", "summary": "looks good"},
                sequence=2,
            ),
        ]
        proj = CrewStateProjection.from_events(events)
        assert len(proj.reviews) == 1
        assert proj.reviews[0]["status"] == "ok"
        assert proj.reviews[0]["summary"] == "looks good"

    def test_new_fields_in_to_read_crew_dict(self) -> None:
        """H13: to_read_crew_dict must include challenges, verifications, reviews."""
        events = [
            _make_event("e1", "crew.started", payload={"goal": "test"}),
            _make_event(
                "e2",
                "verification.passed",
                worker_id="w1",
                payload={"command": "pytest"},
                sequence=2,
            ),
            _make_event(
                "e3",
                "challenge.issued",
                worker_id="w1",
                payload={"finding": "issue"},
                sequence=3,
            ),
            _make_event(
                "e4",
                "review.completed",
                worker_id="w1",
                payload={"status": "ok"},
                sequence=4,
            ),
        ]
        proj = CrewStateProjection.from_events(events)
        d = proj.to_read_crew_dict()
        assert "challenges" in d
        assert "verifications" in d
        assert "reviews" in d
        assert len(d["verifications"]) == 1
        assert len(d["challenges"]) == 1
        assert len(d["reviews"]) == 1


class TestExtractTrailingId:
    """Test _extract_trailing_id helper."""

    def test_extracts_last_segment(self) -> None:
        from codex_claude_orchestrator.v4.crew_state_projection import _extract_trailing_id

        assert _extract_trailing_id("crew-1/worker.spawned/worker-1") == "worker-1"
        assert _extract_trailing_id("crew-1/blackboard/entry-abc") == "entry-abc"
        assert _extract_trailing_id("crew-1/task.created/task-1") == "task-1"

    def test_returns_full_key_when_no_slash(self) -> None:
        from codex_claude_orchestrator.v4.crew_state_projection import _extract_trailing_id

        assert _extract_trailing_id("simple-key") == "simple-key"
        assert _extract_trailing_id("") == ""
