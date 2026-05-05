import json
from unittest.mock import AsyncMock, MagicMock

from codex_claude_orchestrator.mcp_server.tools.crew_execution import register_execution_tools


class FakeServer:
    def __init__(self):
        self.tools = {}

    def tool(self, name):
        def decorator(func):
            self.tools[name] = func
            return func
        return decorator


def test_execution_tools_registered():
    server = FakeServer()
    controller = MagicMock()
    register_execution_tools(server, controller, supervision_loop=None)
    assert "crew_run" in server.tools
    assert "crew_verify" not in server.tools
    assert "crew_merge_plan" not in server.tools


def test_crew_run_no_loop():
    server = FakeServer()
    controller = MagicMock()
    register_execution_tools(server, controller, supervision_loop=None)
    ctx = MagicMock()
    import asyncio
    result = asyncio.run(server.tools["crew_run"](crew_id="c1", ctx=ctx, max_rounds=1))
    data = json.loads(result[0].text)
    assert "error" in data
    assert "supervision_loop not initialized" in data["error"]


def test_crew_run_calls_supervision_loop():
    server = FakeServer()
    controller = MagicMock()
    loop = MagicMock()
    loop.run = AsyncMock(return_value={"crew_id": "c1", "status": "accepted"})
    register_execution_tools(server, controller, supervision_loop=loop)

    ctx = MagicMock()
    import asyncio
    result = asyncio.run(server.tools["crew_run"](
        crew_id="c1", ctx=ctx, max_rounds=3, verification_commands=["pytest"],
    ))
    data = json.loads(result[0].text)
    assert data["status"] == "accepted"
    loop.run.assert_called_once()
    call_kwargs = loop.run.call_args[1]
    assert call_kwargs["crew_id"] == "c1"
    assert call_kwargs["max_rounds"] == 3
    assert call_kwargs["verification_commands"] == ["pytest"]
    assert callable(call_kwargs["sampling_fn"])
