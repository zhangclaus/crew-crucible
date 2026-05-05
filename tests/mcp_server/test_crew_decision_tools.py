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
    assert "crew_challenge" in server.tools
    assert "crew_decide" not in server.tools
    assert "crew_spawn" not in server.tools


def test_crew_accept():
    server = FakeServer()
    controller = MagicMock()
    controller.accept.return_value = {"status": "accepted"}
    register_decision_tools(server, controller)
    import asyncio
    result = asyncio.run(server.tools["crew_accept"](crew_id="c1"))
    data = json.loads(result[0].text)
    assert data["status"] == "accepted"


def test_crew_challenge():
    server = FakeServer()
    controller = MagicMock()
    controller.challenge.return_value = {"status": "challenged"}
    register_decision_tools(server, controller)
    import asyncio
    result = asyncio.run(server.tools["crew_challenge"](crew_id="c1", worker_id="w1", goal="fix the bug"))
    controller.challenge.assert_called_once()
