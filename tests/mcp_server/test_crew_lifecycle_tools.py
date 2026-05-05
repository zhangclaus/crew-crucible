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
    result = asyncio.run(server.tools["crew_status"](crew_id="c1"))
    data = json.loads(result[0].text)
    assert data["crew_id"] == "c1"
    assert "workers" in data
