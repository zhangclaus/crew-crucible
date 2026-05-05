import asyncio
from unittest.mock import MagicMock, patch

from codex_claude_orchestrator.crew.supervisor_loop import CrewSupervisorLoop


def test_run_accepts_when_verify_passes_and_supervisor_says_accept():
    controller = MagicMock()
    controller.status.return_value = {
        "crew": {"crew_id": "c1", "root_goal": "test", "status": "running"},
        "workers": [{"worker_id": "w1", "status": "idle", "role": "implementer"}],
        "blackboard": [], "decisions": [], "messages": [],
    }
    controller.accept.return_value = {"status": "accepted", "crew_id": "c1"}
    loop = CrewSupervisorLoop(controller=controller)

    async def mock_sampling(messages, system_prompt, max_tokens):
        result = MagicMock()
        result.content.text = "accept"
        return result

    with patch.object(loop, "_wait_for_workers", return_value=True), \
         patch.object(loop, "_auto_verify", return_value={"passed": True, "failure_count": 0}):
        result = asyncio.run(loop.run(
            crew_id="c1", max_rounds=3,
            verification_commands=["pytest"], sampling_fn=mock_sampling,
        ))
    assert result["status"] == "accepted"
    controller.accept.assert_called_once_with(crew_id="c1")


def test_run_auto_challenges_when_verify_fails_less_than_3():
    controller = MagicMock()
    controller.status.return_value = {
        "crew": {"crew_id": "c1", "root_goal": "test", "status": "running"},
        "workers": [{"worker_id": "w1", "status": "idle", "role": "implementer"}],
        "blackboard": [], "decisions": [], "messages": [],
    }
    controller.accept.return_value = {"status": "accepted", "crew_id": "c1"}
    loop = CrewSupervisorLoop(controller=controller)

    async def mock_sampling(messages, system_prompt, max_tokens):
        result = MagicMock()
        result.content.text = "accept"
        return result

    verify_results = [
        {"passed": False, "failure_count": 1, "summary": "fail"},
        {"passed": True, "failure_count": 0},
    ]
    verify_call = {"n": 0}
    def auto_verify_side_effect(*args, **kwargs):
        idx = verify_call["n"]
        verify_call["n"] += 1
        return verify_results[min(idx, len(verify_results) - 1)]

    with patch.object(loop, "_wait_for_workers", return_value=True), \
         patch.object(loop, "_auto_verify", side_effect=auto_verify_side_effect), \
         patch.object(loop, "_auto_challenge"):
        result = asyncio.run(loop.run(
            crew_id="c1", max_rounds=3,
            verification_commands=["pytest"], sampling_fn=mock_sampling,
        ))
    assert result["status"] == "accepted"


def test_run_asks_supervisor_when_verify_fails_3_times():
    controller = MagicMock()
    controller.status.return_value = {
        "crew": {"crew_id": "c1", "root_goal": "test", "status": "running"},
        "workers": [{"worker_id": "w1", "status": "idle", "role": "implementer"}],
        "blackboard": [], "decisions": [], "messages": [],
    }
    loop = CrewSupervisorLoop(controller=controller)
    sampling_calls = []

    async def mock_sampling(messages, system_prompt, max_tokens):
        sampling_calls.append({"messages": messages, "system_prompt": system_prompt})
        result = MagicMock()
        result.content.text = "accept"
        return result

    with patch.object(loop, "_wait_for_workers", return_value=True), \
         patch.object(loop, "_auto_verify", return_value={"passed": False, "failure_count": 3, "summary": "fail 3x"}):
        asyncio.run(loop.run(
            crew_id="c1", max_rounds=1,
            verification_commands=["pytest"], sampling_fn=mock_sampling,
        ))
    assert len(sampling_calls) == 1
    assert "验证失败 3 次" in sampling_calls[0]["messages"][0].content.text


def test_run_spawns_worker_when_supervisor_says_spawn():
    controller = MagicMock()
    controller.status.return_value = {
        "crew": {"crew_id": "c1", "root_goal": "test", "status": "running"},
        "workers": [{"worker_id": "w1", "status": "idle", "role": "implementer"}],
        "blackboard": [], "decisions": [], "messages": [],
    }
    loop = CrewSupervisorLoop(controller=controller)

    async def mock_sampling(messages, system_prompt, max_tokens):
        result = MagicMock()
        result.content.text = 'spawn_worker(label="fixer", mission="fix the tests")'
        return result

    with patch.object(loop, "_wait_for_workers", return_value=True), \
         patch.object(loop, "_auto_verify", return_value={"passed": False, "failure_count": 3, "summary": "fail"}):
        asyncio.run(loop.run(
            crew_id="c1", max_rounds=1,
            verification_commands=["pytest"], sampling_fn=mock_sampling,
        ))
    controller.ensure_worker.assert_called_once()


def test_run_returns_max_rounds_when_exhausted():
    controller = MagicMock()
    controller.status.return_value = {
        "crew": {"crew_id": "c1", "root_goal": "test", "status": "running"},
        "workers": [{"worker_id": "w1", "status": "idle", "role": "implementer"}],
        "blackboard": [], "decisions": [], "messages": [],
    }
    loop = CrewSupervisorLoop(controller=controller)

    async def mock_sampling(messages, system_prompt, max_tokens):
        result = MagicMock()
        result.content.text = "observe"
        return result

    with patch.object(loop, "_wait_for_workers", return_value=True), \
         patch.object(loop, "_auto_verify", return_value={"passed": False, "failure_count": 0, "summary": "no verify"}):
        result = asyncio.run(loop.run(
            crew_id="c1", max_rounds=2,
            verification_commands=[], sampling_fn=mock_sampling,
        ))
    assert result["status"] == "max_rounds_reached"
    assert result["rounds"] == 2


def test_parse_decision_accept():
    loop = CrewSupervisorLoop(controller=MagicMock())
    assert loop._parse_decision("accept") == {"action": "accept"}


def test_parse_decision_spawn_worker():
    loop = CrewSupervisorLoop(controller=MagicMock())
    result = loop._parse_decision('spawn_worker(label="fixer", mission="fix tests")')
    assert result["action"] == "spawn_worker"
    assert result["label"] == "fixer"
    assert result["mission"] == "fix tests"


def test_parse_decision_challenge():
    loop = CrewSupervisorLoop(controller=MagicMock())
    result = loop._parse_decision('challenge(worker_id="w1", goal="improve coverage")')
    assert result["action"] == "challenge"
    assert result["worker_id"] == "w1"
    assert result["goal"] == "improve coverage"


def test_parse_decision_unknown_defaults_to_observe():
    loop = CrewSupervisorLoop(controller=MagicMock())
    assert loop._parse_decision("I'm not sure what to do") == {"action": "observe"}


def test_crew_run_registered_as_tool():
    """crew_run should be registered as an MCP tool."""
    from unittest.mock import MagicMock
    from codex_claude_orchestrator.mcp_server.tools.crew_execution import register_execution_tools

    mock_server = MagicMock()
    registered_tools = {}
    def mock_tool(name):
        def decorator(fn):
            registered_tools[name] = fn
            return fn
        return decorator
    mock_server.tool = mock_tool

    register_execution_tools(mock_server, MagicMock(), supervision_loop=MagicMock())
    assert "crew_run" in registered_tools
    assert "crew_verify" not in registered_tools
    assert "crew_merge_plan" not in registered_tools


def test_run_executes_spawn_decision_when_verify_passes():
    """When verification passes but supervisor says spawn, execute the spawn decision."""
    controller = MagicMock()
    controller.status.return_value = {
        "crew": {"crew_id": "c1", "root_goal": "test", "status": "running"},
        "workers": [{"worker_id": "w1", "status": "idle", "role": "implementer"}],
        "blackboard": [], "decisions": [], "messages": [],
    }
    loop = CrewSupervisorLoop(controller=controller)

    async def mock_sampling(messages, system_prompt, max_tokens):
        result = MagicMock()
        result.content.text = 'spawn_worker(label="fixer", mission="fix the tests")'
        return result

    with patch.object(loop, "_wait_for_workers", return_value=True), \
         patch.object(loop, "_auto_verify", return_value={"passed": True, "failure_count": 0}):
        asyncio.run(loop.run(
            crew_id="c1", max_rounds=1,
            verification_commands=["pytest"], sampling_fn=mock_sampling,
        ))

    controller.ensure_worker.assert_called_once()


def test_crew_decision_only_registers_accept_and_challenge():
    """crew_decision should only register crew_accept and crew_challenge tools."""
    from unittest.mock import MagicMock
    from codex_claude_orchestrator.mcp_server.tools.crew_decision import register_decision_tools

    mock_server = MagicMock()
    registered_tools = {}
    def mock_tool(name):
        def decorator(fn):
            registered_tools[name] = fn
            return fn
        return decorator
    mock_server.tool = mock_tool

    register_decision_tools(mock_server, MagicMock())

    assert "crew_accept" in registered_tools
    assert "crew_challenge" in registered_tools
    assert "crew_decide" not in registered_tools
    assert "crew_spawn" not in registered_tools
