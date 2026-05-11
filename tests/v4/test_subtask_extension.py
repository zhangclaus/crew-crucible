"""Tests for SubTask extension with role, goal, write_scope, worker_template."""

from __future__ import annotations

from codex_claude_orchestrator.v4.subtask import SubTask


def test_subtask_existing_fields_still_work():
    """Backward compatibility: existing fields work as before."""
    st = SubTask(task_id="1", description="test task", scope=["src/"])
    assert st.task_id == "1"
    assert st.description == "test task"
    assert st.scope == ["src/"]
    assert st.depends_on == []
    assert st.worker_id == ""
    assert st.status == "pending"


def test_subtask_new_fields_default():
    """New fields have sensible defaults."""
    st = SubTask(task_id="1", description="test", scope=["src/"])
    assert st.role == ""
    assert st.goal == ""
    assert st.write_scope == []
    assert st.worker_template == "targeted-code-editor"


def test_subtask_new_fields_set():
    """New fields can be set."""
    st = SubTask(
        task_id="2a",
        description="实现 JWT API",
        scope=["src/api/auth.py"],
        role="backend-developer",
        goal="实现 JWT 认证 API",
        write_scope=["src/api/auth.py", "src/models/user.py"],
        worker_template="backend-developer",
    )
    assert st.role == "backend-developer"
    assert st.goal == "实现 JWT 认证 API"
    assert st.write_scope == ["src/api/auth.py", "src/models/user.py"]
    assert st.worker_template == "backend-developer"


def test_subtask_to_dict_includes_new_fields():
    """to_dict() includes new fields."""
    st = SubTask(
        task_id="2a",
        description="实现 JWT API",
        scope=["src/api/auth.py"],
        role="backend-developer",
        goal="实现 JWT 认证 API",
        write_scope=["src/api/auth.py"],
        worker_template="backend-developer",
    )
    d = st.to_dict()
    assert d["role"] == "backend-developer"
    assert d["goal"] == "实现 JWT 认证 API"
    assert d["write_scope"] == ["src/api/auth.py"]
    assert d["worker_template"] == "backend-developer"


def test_subtask_from_dict_with_new_fields():
    """from_dict() handles new fields."""
    d = {
        "task_id": "2a",
        "description": "实现 JWT API",
        "scope": ["src/api/auth.py"],
        "role": "backend-developer",
        "goal": "实现 JWT 认证 API",
        "write_scope": ["src/api/auth.py"],
        "worker_template": "backend-developer",
    }
    st = SubTask.from_dict(d)
    assert st.role == "backend-developer"
    assert st.goal == "实现 JWT 认证 API"
    assert st.write_scope == ["src/api/auth.py"]
    assert st.worker_template == "backend-developer"


def test_subtask_from_dict_without_new_fields():
    """from_dict() handles missing new fields with defaults (backward compat)."""
    d = {
        "task_id": "1",
        "description": "old style task",
        "scope": ["src/"],
    }
    st = SubTask.from_dict(d)
    assert st.role == ""
    assert st.goal == ""
    assert st.write_scope == []
    assert st.worker_template == "targeted-code-editor"


def test_subtask_roundtrip_with_all_fields():
    """Full roundtrip with all fields."""
    st = SubTask(
        task_id="2b",
        description="实现登录页面",
        scope=["src/pages/login.tsx"],
        depends_on=["2a"],
        role="frontend-developer",
        goal="实现登录页面",
        write_scope=["src/pages/login.tsx", "src/hooks/useAuth.ts"],
        worker_template="frontend-developer",
        status="running",
        review_attempts=1,
    )
    d = st.to_dict()
    st2 = SubTask.from_dict(d)
    assert st2.task_id == "2b"
    assert st2.depends_on == ["2a"]
    assert st2.role == "frontend-developer"
    assert st2.write_scope == ["src/pages/login.tsx", "src/hooks/useAuth.ts"]
    assert st2.worker_template == "frontend-developer"
    assert st2.status == "running"
    assert st2.review_attempts == 1
