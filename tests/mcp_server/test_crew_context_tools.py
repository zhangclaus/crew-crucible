import json
from unittest.mock import MagicMock

from codex_claude_orchestrator.mcp_server.tools.crew_context import register_context_tools


class FakeServer:
    def __init__(self):
        self.tools = {}

    def tool(self, name):
        def decorator(func):
            self.tools[name] = func
            return func
        return decorator


def test_context_tools_registered():
    server = FakeServer()
    controller = MagicMock()
    register_context_tools(server, controller)
    assert "crew_blackboard" in server.tools
    assert "crew_events" in server.tools
    assert "crew_observe" in server.tools
    assert "crew_changes" in server.tools
    assert "crew_diff" in server.tools


def test_crew_blackboard_calls_controller():
    server = FakeServer()
    controller = MagicMock()
    controller.blackboard_entries.return_value = [
        {"entry_id": "e1", "type": "fact", "content": "test"},
    ]
    register_context_tools(server, controller)
    import asyncio
    result = asyncio.run(server.tools["crew_blackboard"](crew_id="c1"))
    data = json.loads(result[0].text)
    assert len(data) == 1
    controller.blackboard_entries.assert_called_once_with(crew_id="c1")


def test_crew_blackboard_filters_by_worker_id():
    server = FakeServer()
    controller = MagicMock()
    controller.blackboard_entries.return_value = [
        {"entry_id": "e1", "actor_id": "w1", "type": "fact", "content": "a"},
        {"entry_id": "e2", "actor_id": "w2", "type": "fact", "content": "b"},
    ]
    register_context_tools(server, controller)
    import asyncio
    result = asyncio.run(server.tools["crew_blackboard"](crew_id="c1", worker_id="w1"))
    data = json.loads(result[0].text)
    assert len(data) == 1
    assert data[0]["actor_id"] == "w1"


def test_crew_blackboard_filters_by_entry_type():
    server = FakeServer()
    controller = MagicMock()
    controller.blackboard_entries.return_value = [
        {"entry_id": "e1", "type": "fact", "content": "a"},
        {"entry_id": "e2", "type": "patch", "content": "b"},
    ]
    register_context_tools(server, controller)
    import asyncio
    result = asyncio.run(server.tools["crew_blackboard"](crew_id="c1", entry_type="patch"))
    data = json.loads(result[0].text)
    assert len(data) == 1
    assert data[0]["type"] == "patch"


def test_crew_events_calls_controller():
    from pathlib import Path
    server = FakeServer()
    controller = MagicMock()
    controller.status.return_value = {
        "decisions": [{"type": "crew.started", "data": {}}],
        "messages": [{"type": "turn.completed", "data": {}}],
    }
    register_context_tools(server, controller)
    import asyncio
    result = asyncio.run(server.tools["crew_events"](repo="/repo", crew_id="c1"))
    data = json.loads(result[0].text)
    assert len(data) == 2
    controller.status.assert_called_once_with(repo_root=Path("/repo"), crew_id="c1")


def test_crew_events_filters_non_key_events():
    server = FakeServer()
    controller = MagicMock()
    controller.status.return_value = {
        "decisions": [
            {"type": "crew.started", "data": {}},
            {"type": "noise.event", "data": {}},
        ],
        "messages": [
            {"type": "turn.completed", "data": {}},
            {"type": "random.event", "data": {}},
        ],
    }
    register_context_tools(server, controller)
    import asyncio
    result = asyncio.run(server.tools["crew_events"](repo="/repo", crew_id="c1"))
    data = json.loads(result[0].text)
    assert len(data) == 2


def test_crew_observe_calls_controller():
    from pathlib import Path
    server = FakeServer()
    controller = MagicMock()
    controller.observe_worker.return_value = {"snapshot": "worker output here"}
    register_context_tools(server, controller)
    import asyncio
    result = asyncio.run(server.tools["crew_observe"](repo="/repo", crew_id="c1", worker_id="w1"))
    data = json.loads(result[0].text)
    assert "snapshot" in data
    controller.observe_worker.assert_called_once_with(
        repo_root=Path("/repo"), crew_id="c1", worker_id="w1",
    )


def test_crew_changes_calls_controller():
    server = FakeServer()
    controller = MagicMock()
    controller.changes.return_value = [
        {"file": "src/foo.py", "status": "modified"},
    ]
    register_context_tools(server, controller)
    import asyncio
    result = asyncio.run(server.tools["crew_changes"](crew_id="c1"))
    data = json.loads(result[0].text)
    assert len(data) == 1
    assert data[0]["file"] == "src/foo.py"
    controller.changes.assert_called_once_with(crew_id="c1")


def test_crew_diff_calls_controller():
    server = FakeServer()
    controller = MagicMock()
    controller.changes.return_value = [
        {"file": "src/foo.py", "diff": "@@ -1 +1 @@"},
        {"file": "src/bar.py", "diff": "@@ -5 +5 @@"},
    ]
    register_context_tools(server, controller)
    import asyncio
    result = asyncio.run(server.tools["crew_diff"](crew_id="c1", file="src/foo.py"))
    data = json.loads(result[0].text)
    assert len(data) == 1
    assert data[0]["file"] == "src/foo.py"


def test_crew_diff_returns_all_when_no_file():
    server = FakeServer()
    controller = MagicMock()
    controller.changes.return_value = [
        {"file": "src/foo.py", "diff": "@@ -1 +1 @@"},
        {"file": "src/bar.py", "diff": "@@ -5 +5 @@"},
    ]
    register_context_tools(server, controller)
    import asyncio
    result = asyncio.run(server.tools["crew_diff"](crew_id="c1"))
    data = json.loads(result[0].text)
    assert len(data) == 2
