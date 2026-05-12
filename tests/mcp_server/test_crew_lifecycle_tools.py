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


def test_crew_verify_registered():
    server = FakeServer()
    controller = MagicMock()
    register_lifecycle_tools(server, controller)
    assert "crew_verify" in server.tools
    assert "crew_spawn" in server.tools
    assert len(server.tools) == 2


def test_crew_verify_success():
    server = FakeServer()
    controller = MagicMock()
    controller.verify.return_value = {"passed": True, "output": "all tests passed"}
    register_lifecycle_tools(server, controller)
    import asyncio
    result = asyncio.run(server.tools["crew_verify"](crew_id="c1", command="pytest"))
    data = json.loads(result[0].text)
    assert data["passed"] is True
    controller.verify.assert_called_once_with(crew_id="c1", command="pytest", worker_id=None)


def test_crew_verify_with_worker_id():
    server = FakeServer()
    controller = MagicMock()
    controller.verify.return_value = {"passed": True}
    register_lifecycle_tools(server, controller)
    import asyncio
    result = asyncio.run(server.tools["crew_verify"](crew_id="c1", command="pytest", worker_id="w1"))
    controller.verify.assert_called_once_with(crew_id="c1", command="pytest", worker_id="w1")


def test_crew_verify_returns_error_on_value_error():
    server = FakeServer()
    controller = MagicMock()
    controller.verify.side_effect = ValueError("verify not configured")
    register_lifecycle_tools(server, controller)
    import asyncio
    result = asyncio.run(server.tools["crew_verify"](crew_id="c1", command="pytest"))
    data = json.loads(result[0].text)
    assert "error" in data


def test_crew_verify_returns_error_on_file_not_found():
    server = FakeServer()
    controller = MagicMock()
    controller.verify.side_effect = FileNotFoundError("crew not found: c1")
    register_lifecycle_tools(server, controller)
    import asyncio
    result = asyncio.run(server.tools["crew_verify"](crew_id="c1", command="pytest"))
    data = json.loads(result[0].text)
    assert "error" in data


def test_crew_verify_returns_error_on_generic_exception():
    server = FakeServer()
    controller = MagicMock()
    controller.verify.side_effect = RuntimeError("something broke")
    register_lifecycle_tools(server, controller)
    import asyncio
    result = asyncio.run(server.tools["crew_verify"](crew_id="c1", command="pytest"))
    data = json.loads(result[0].text)
    assert "error" in data
    assert "internal:" in data["error"]


class TestCrewSpawn:
    def test_crew_spawn_with_template(self):
        """crew_spawn should create a worker using a predefined template."""
        server = FakeServer()
        controller = MagicMock()
        controller.ensure_worker.return_value = {"worker_id": "w1", "contract_id": "c1"}
        register_lifecycle_tools(server, controller)

        import asyncio
        result = asyncio.run(server.tools["crew_spawn"](
            repo="/tmp/test",
            crew_id="crew-1",
            label="targeted-code-editor",
            mission="Implement auth module",
        ))

        response = json.loads(result[0].text)
        assert response["worker_id"] == "w1"
        controller.ensure_worker.assert_called_once()

    def test_crew_spawn_with_custom_label(self):
        """crew_spawn should create a worker with custom label when no template matches."""
        server = FakeServer()
        controller = MagicMock()
        controller.ensure_worker.return_value = {"worker_id": "w2", "contract_id": "c2"}
        register_lifecycle_tools(server, controller)

        import asyncio
        result = asyncio.run(server.tools["crew_spawn"](
            repo="/tmp/test",
            crew_id="crew-1",
            label="custom-role",
            mission="Do something custom",
        ))

        response = json.loads(result[0].text)
        assert response["worker_id"] == "w2"
