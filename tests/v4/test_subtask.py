"""Tests for the SubTask data model used by the parallel supervisor."""

import pytest

from codex_claude_orchestrator.v4.subtask import SubTask


class TestSubTaskDefaults:
    """Verify default field values for a freshly created SubTask."""

    def test_default_status_is_pending(self) -> None:
        task = SubTask(task_id="t-1", description="Do something", scope=["src/**/*.py"])
        assert task.status == "pending"

    def test_default_depends_on_is_empty(self) -> None:
        task = SubTask(task_id="t-1", description="Do something", scope=["src/**/*.py"])
        assert task.depends_on == []

    def test_default_worker_id_is_empty(self) -> None:
        task = SubTask(task_id="t-1", description="Do something", scope=["src/**/*.py"])
        assert task.worker_id == ""

    def test_default_result_is_none(self) -> None:
        task = SubTask(task_id="t-1", description="Do something", scope=["src/**/*.py"])
        assert task.result is None

    def test_default_review_attempts_is_zero(self) -> None:
        task = SubTask(task_id="t-1", description="Do something", scope=["src/**/*.py"])
        assert task.review_attempts == 0


class TestSubTaskStatusTransitions:
    """Verify mutable status field can be updated through valid states."""

    VALID_STATUSES = ["pending", "running", "unit_review", "passed", "failed"]

    def test_all_valid_statuses_can_be_set(self) -> None:
        task = SubTask(task_id="t-1", description="Work", scope=["src/**/*.py"])
        for status in self.VALID_STATUSES:
            task.status = status
            assert task.status == status

    def test_typical_lifecycle(self) -> None:
        task = SubTask(task_id="t-1", description="Work", scope=["src/**/*.py"])
        assert task.status == "pending"

        task.status = "running"
        task.worker_id = "worker-1"
        assert task.status == "running"
        assert task.worker_id == "worker-1"

        task.status = "unit_review"
        task.review_attempts = 1
        assert task.status == "unit_review"
        assert task.review_attempts == 1

        task.status = "passed"
        task.result = {"summary": "All tests pass"}
        assert task.status == "passed"
        assert task.result == {"summary": "All tests pass"}


class TestSubTaskToDict:
    """Verify to_dict() serializes all fields correctly."""

    def test_to_dict_with_all_defaults(self) -> None:
        task = SubTask(task_id="t-1", description="Work", scope=["src/**/*.py"])
        d = task.to_dict()
        assert d == {
            "task_id": "t-1",
            "description": "Work",
            "scope": ["src/**/*.py"],
            "depends_on": [],
            "worker_id": "",
            "status": "pending",
            "result": None,
            "review_attempts": 0,
            "role": "",
            "goal": "",
            "write_scope": [],
            "worker_template": "targeted-code-editor",
        }

    def test_to_dict_with_custom_values(self) -> None:
        task = SubTask(
            task_id="t-2",
            description="Implement feature",
            scope=["src/**/*.py", "tests/**/*.py"],
            depends_on=["t-1"],
            worker_id="worker-3",
            status="unit_review",
            result={"files_changed": 2},
            review_attempts=1,
        )
        d = task.to_dict()
        assert d["task_id"] == "t-2"
        assert d["depends_on"] == ["t-1"]
        assert d["worker_id"] == "worker-3"
        assert d["status"] == "unit_review"
        assert d["result"] == {"files_changed": 2}
        assert d["review_attempts"] == 1


class TestSubTaskFromDict:
    """Verify from_dict() creates a SubTask from a dict round-trip."""

    def test_from_dict_minimal(self) -> None:
        data = {
            "task_id": "t-1",
            "description": "Work",
            "scope": ["src/**/*.py"],
        }
        task = SubTask.from_dict(data)
        assert task.task_id == "t-1"
        assert task.description == "Work"
        assert task.scope == ["src/**/*.py"]
        assert task.status == "pending"
        assert task.depends_on == []
        assert task.worker_id == ""
        assert task.result is None
        assert task.review_attempts == 0

    def test_from_dict_full(self) -> None:
        data = {
            "task_id": "t-2",
            "description": "Fix bug",
            "scope": ["src/bug.py"],
            "depends_on": ["t-1"],
            "worker_id": "worker-1",
            "status": "passed",
            "result": {"ok": True},
            "review_attempts": 2,
        }
        task = SubTask.from_dict(data)
        assert task.task_id == "t-2"
        assert task.depends_on == ["t-1"]
        assert task.worker_id == "worker-1"
        assert task.status == "passed"
        assert task.result == {"ok": True}
        assert task.review_attempts == 2

    def test_round_trip_to_dict_from_dict(self) -> None:
        original = SubTask(
            task_id="t-3",
            description="Refactor module",
            scope=["src/mod.py", "tests/test_mod.py"],
            depends_on=["t-1", "t-2"],
            worker_id="worker-5",
            status="running",
            result=None,
            review_attempts=0,
        )
        restored = SubTask.from_dict(original.to_dict())
        assert restored.task_id == original.task_id
        assert restored.description == original.description
        assert restored.scope == original.scope
        assert restored.depends_on == original.depends_on
        assert restored.worker_id == original.worker_id
        assert restored.status == original.status
        assert restored.result == original.result
        assert restored.review_attempts == original.review_attempts

    def test_round_trip_with_result(self) -> None:
        original = SubTask(
            task_id="t-4",
            description="Deploy",
            scope=["infra/**"],
            status="passed",
            result={"deployed": True, "url": "https://example.com"},
            review_attempts=3,
        )
        restored = SubTask.from_dict(original.to_dict())
        assert restored.result == {"deployed": True, "url": "https://example.com"}
        assert restored.review_attempts == 3

    def test_from_dict_missing_required_key_raises(self) -> None:
        with pytest.raises(KeyError):
            SubTask.from_dict({"task_id": "t-1"})  # missing description, scope
