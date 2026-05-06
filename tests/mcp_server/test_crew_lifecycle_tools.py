import json
from unittest.mock import MagicMock

from codex_claude_orchestrator.mcp_server.tools.crew_lifecycle import register_lifecycle_tools


class FakeServer:
    def __init__(self):
        self.tools = {}

    def tool(self, name):
        def decorator(func):
            self.tools[name] = func
            return func
        return decorator


def test_crew_start_registered():
    server = FakeServer()
    controller = MagicMock()
    register_lifecycle_tools(server, controller)
    assert "crew_start" in server.tools
    assert "crew_stop" in server.tools
    assert "crew_status" in server.tools
    assert "crew_spawn" in server.tools
    assert "crew_stop_worker" in server.tools
    assert "crew_verify" in server.tools


def test_crew_status_calls_compress():
    server = FakeServer()
    controller = MagicMock()
    controller.status.return_value = {
        "crew": {"crew_id": "c1", "root_goal": "test", "status": "running"},
        "workers": [],
        "blackboard": [],
        "decisions": [],
        "messages": [],
    }
    register_lifecycle_tools(server, controller)
    import asyncio
    result = asyncio.run(server.tools["crew_status"](repo="/repo", crew_id="c1"))
    data = json.loads(result[0].text)
    assert data["crew_id"] == "c1"
    assert "workers" in data


def test_crew_spawn_with_template():
    """crew_spawn with a known template label uses predefined contract."""
    server = FakeServer()
    controller = MagicMock()
    controller.ensure_worker.return_value = {"worker_id": "w1", "status": "running"}
    register_lifecycle_tools(server, controller)
    import asyncio
    result = asyncio.run(server.tools["crew_spawn"](
        repo="/repo", crew_id="c1", label="targeted-code-editor",
    ))
    data = json.loads(result[0].text)
    assert data["worker_id"] == "w1"
    controller.ensure_worker.assert_called_once()
    call_kwargs = controller.ensure_worker.call_args[1]
    assert call_kwargs["contract"].label == "targeted-code-editor"
    assert call_kwargs["contract"].authority_level.value == "source_write"


def test_crew_spawn_with_custom_label():
    """crew_spawn with a custom label creates a basic contract."""
    server = FakeServer()
    controller = MagicMock()
    controller.ensure_worker.return_value = {"worker_id": "w2", "status": "running"}
    register_lifecycle_tools(server, controller)
    import asyncio
    result = asyncio.run(server.tools["crew_spawn"](
        repo="/repo", crew_id="c1", label="my-worker", mission="do something",
    ))
    data = json.loads(result[0].text)
    assert data["worker_id"] == "w2"
    call_kwargs = controller.ensure_worker.call_args[1]
    assert call_kwargs["contract"].label == "my-worker"
    assert call_kwargs["contract"].mission == "do something"


def test_crew_spawn_template_mission_override():
    """crew_spawn with template + custom mission overrides template mission."""
    server = FakeServer()
    controller = MagicMock()
    controller.ensure_worker.return_value = {"worker_id": "w3", "status": "running"}
    register_lifecycle_tools(server, controller)
    import asyncio
    asyncio.run(server.tools["crew_spawn"](
        repo="/repo", crew_id="c1", label="repo-context-scout", mission="find auth code",
    ))
    call_kwargs = controller.ensure_worker.call_args[1]
    assert call_kwargs["contract"].mission == "find auth code"
    assert call_kwargs["contract"].authority_level.value == "readonly"


def test_crew_spawn_summarizer_template():
    """crew_spawn with summarizer label creates readonly contract."""
    server = FakeServer()
    controller = MagicMock()
    controller.ensure_worker.return_value = {"worker_id": "ws1", "status": "running"}
    register_lifecycle_tools(server, controller)
    import asyncio
    result = asyncio.run(server.tools["crew_spawn"](
        repo="/repo", crew_id="c1", label="summarizer",
    ))
    data = json.loads(result[0].text)
    assert data["worker_id"] == "ws1"
    call_kwargs = controller.ensure_worker.call_args[1]
    assert call_kwargs["contract"].label == "summarizer"
    assert call_kwargs["contract"].authority_level.value == "readonly"
    assert call_kwargs["contract"].workspace_policy.value == "readonly"
    assert "inspect_code" in call_kwargs["contract"].required_capabilities
    assert "summary" in call_kwargs["contract"].mission.lower()


def test_crew_spawn_frontend_developer_template():
    """crew_spawn with frontend-developer label creates source_write contract."""
    server = FakeServer()
    controller = MagicMock()
    controller.ensure_worker.return_value = {"worker_id": "wf1", "status": "running"}
    register_lifecycle_tools(server, controller)
    import asyncio
    result = asyncio.run(server.tools["crew_spawn"](
        repo="/repo", crew_id="c1", label="frontend-developer",
    ))
    data = json.loads(result[0].text)
    assert data["worker_id"] == "wf1"
    call_kwargs = controller.ensure_worker.call_args[1]
    assert call_kwargs["contract"].label == "frontend-developer"
    assert call_kwargs["contract"].authority_level.value == "source_write"
    assert call_kwargs["contract"].workspace_policy.value == "worktree"


def test_crew_spawn_backend_developer_template():
    """crew_spawn with backend-developer label creates source_write contract."""
    server = FakeServer()
    controller = MagicMock()
    controller.ensure_worker.return_value = {"worker_id": "wb1", "status": "running"}
    register_lifecycle_tools(server, controller)
    import asyncio
    asyncio.run(server.tools["crew_spawn"](
        repo="/repo", crew_id="c1", label="backend-developer",
    ))
    call_kwargs = controller.ensure_worker.call_args[1]
    assert call_kwargs["contract"].label == "backend-developer"
    assert call_kwargs["contract"].authority_level.value == "source_write"


def test_crew_spawn_test_writer_template():
    """crew_spawn with test-writer label creates source_write contract."""
    server = FakeServer()
    controller = MagicMock()
    controller.ensure_worker.return_value = {"worker_id": "wt1", "status": "running"}
    register_lifecycle_tools(server, controller)
    import asyncio
    asyncio.run(server.tools["crew_spawn"](
        repo="/repo", crew_id="c1", label="test-writer",
    ))
    call_kwargs = controller.ensure_worker.call_args[1]
    assert call_kwargs["contract"].label == "test-writer"
    assert call_kwargs["contract"].authority_level.value == "source_write"
    assert "test" in call_kwargs["contract"].mission.lower()


def test_crew_spawn_with_write_scope():
    """crew_spawn with write_scope overrides template's default scope."""
    server = FakeServer()
    controller = MagicMock()
    controller.ensure_worker.return_value = {"worker_id": "w4", "status": "running"}
    register_lifecycle_tools(server, controller)
    import asyncio
    asyncio.run(server.tools["crew_spawn"](
        repo="/repo", crew_id="c1", label="frontend-developer",
        mission="implement login page",
        write_scope=["src/components/", "src/pages/"],
    ))
    call_kwargs = controller.ensure_worker.call_args[1]
    assert call_kwargs["contract"].write_scope == ["src/components/", "src/pages/"]
    assert call_kwargs["contract"].mission == "implement login page"


def test_crew_spawn_custom_label_with_write_scope():
    """crew_spawn with custom label and write_scope passes scope to contract."""
    server = FakeServer()
    controller = MagicMock()
    controller.ensure_worker.return_value = {"worker_id": "w5", "status": "running"}
    register_lifecycle_tools(server, controller)
    import asyncio
    asyncio.run(server.tools["crew_spawn"](
        repo="/repo", crew_id="c1", label="my-worker",
        mission="do stuff", write_scope=["src/api/"],
    ))
    call_kwargs = controller.ensure_worker.call_args[1]
    assert call_kwargs["contract"].write_scope == ["src/api/"]


def test_crew_stop_worker():
    """crew_stop_worker delegates to controller.stop_worker."""
    from pathlib import Path
    server = FakeServer()
    controller = MagicMock()
    controller.stop_worker.return_value = {"status": "stopped", "worker_id": "w1"}
    register_lifecycle_tools(server, controller)
    import asyncio
    result = asyncio.run(server.tools["crew_stop_worker"](
        repo="/repo", crew_id="c1", worker_id="w1",
    ))
    data = json.loads(result[0].text)
    assert data["status"] == "stopped"
    controller.stop_worker.assert_called_once_with(
        repo_root=Path("/repo"), crew_id="c1", worker_id="w1",
    )
