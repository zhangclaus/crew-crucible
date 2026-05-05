from __future__ import annotations

import json
from pathlib import Path

from mcp.server import Server
from mcp.types import TextContent

from codex_claude_orchestrator.mcp_server.context.compressor import (
    compress_blackboard,
    filter_events,
)
from codex_claude_orchestrator.mcp_server.context.token_budget import truncate_json


def register_context_tools(server: Server, controller) -> None:

    @server.tool("crew_blackboard")
    async def crew_blackboard(
        crew_id: str,
        worker_id: str | None = None,
        entry_type: str | None = None,
        limit: int = 10,
    ) -> list[TextContent]:
        """读取黑板条目（过滤后，默认最近 10 条）。"""
        entries = controller.blackboard_entries(crew_id=crew_id)
        filtered = compress_blackboard(entries, limit=limit, worker_id=worker_id, entry_type=entry_type)
        return [TextContent(type="text", text=truncate_json(filtered))]

    @server.tool("crew_events")
    async def crew_events(repo: str, crew_id: str, limit: int = 20) -> list[TextContent]:
        """读取关键事件（过滤中间事件，默认最近 20 条）。"""
        raw = controller.status(repo_root=Path(repo), crew_id=crew_id)
        events = raw.get("decisions", []) + raw.get("messages", [])
        filtered = filter_events(events, limit=limit)
        return [TextContent(type="text", text=truncate_json(filtered))]

    @server.tool("crew_observe")
    async def crew_observe(repo: str, crew_id: str, worker_id: str) -> list[TextContent]:
        """观察某个 Worker 的当前轮次输出。"""
        observation = controller.observe_worker(repo_root=Path(repo), crew_id=crew_id, worker_id=worker_id)
        return [TextContent(type="text", text=truncate_json(observation))]

    @server.tool("crew_changes")
    async def crew_changes(crew_id: str) -> list[TextContent]:
        """查看 Crew 的文件变更列表。"""
        changes = controller.changes(crew_id=crew_id)
        return [TextContent(type="text", text=json.dumps(changes, ensure_ascii=False))]

    @server.tool("crew_diff")
    async def crew_diff(crew_id: str, file: str | None = None) -> list[TextContent]:
        """查看具体文件的 diff。"""
        changes = controller.changes(crew_id=crew_id)
        if file:
            changes = [c for c in changes if c.get("file") == file]
        return [TextContent(type="text", text=truncate_json(changes))]
