import json
from unittest.mock import MagicMock

from codex_claude_orchestrator.mcp_server.tools.crew_decision import register_decision_tools


class FakeServer:
    def __init__(self):
        self.tools = {}

    def tool(self, name):
        def decorator(func):
            self.tools[name] = func
            return func
        return decorator


def test_decision_tools_registered():
    server = FakeServer()
    controller = MagicMock()
    register_decision_tools(server, controller)
    assert "crew_accept" in server.tools
    assert "crew_challenge" not in server.tools


def test_crew_accept():
    server = FakeServer()
    controller = MagicMock()
    controller.accept.return_value = {"status": "accepted"}
    register_decision_tools(server, controller)
    import asyncio
    result = asyncio.run(server.tools["crew_accept"](crew_id="c1"))
    data = json.loads(result[0].text)
    assert data["status"] == "accepted"
    controller.accept.assert_called_once_with(crew_id="c1", summary="Accepted by user")


def test_crew_accept_passes_summary():
    server = FakeServer()
    controller = MagicMock()
    controller.accept.return_value = {"status": "accepted"}
    register_decision_tools(server, controller)
    import asyncio
    result = asyncio.run(server.tools["crew_accept"](crew_id="c1", summary="looks good"))
    data = json.loads(result[0].text)
    assert data["status"] == "accepted"
    controller.accept.assert_called_once_with(crew_id="c1", summary="looks good")


def test_crew_accept_default_summary():
    server = FakeServer()
    controller = MagicMock()
    controller.accept.return_value = {"status": "accepted"}
    register_decision_tools(server, controller)
    import asyncio
    result = asyncio.run(server.tools["crew_accept"](crew_id="c1"))
    controller.accept.assert_called_once_with(crew_id="c1", summary="Accepted by user")


def test_crew_accept_returns_error_on_exception():
    server = FakeServer()
    controller = MagicMock()
    controller.accept.side_effect = FileNotFoundError("crew not found: c1")
    register_decision_tools(server, controller)
    import asyncio
    result = asyncio.run(server.tools["crew_accept"](crew_id="c1"))
    data = json.loads(result[0].text)
    assert "error" in data
    assert "crew not found" in data["error"]
