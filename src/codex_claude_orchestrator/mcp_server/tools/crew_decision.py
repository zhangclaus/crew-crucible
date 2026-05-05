from __future__ import annotations

import json

from mcp.server.fastmcp import FastMCP
from mcp.types import TextContent


def register_decision_tools(server: FastMCP, controller) -> None:

    @server.tool("crew_accept")
    async def crew_accept(crew_id: str) -> list[TextContent]:
        """接受当前 Crew 结果，触发合并。"""
        result = controller.accept(crew_id=crew_id)
        return [TextContent(type="text", text=json.dumps(result, ensure_ascii=False))]

    @server.tool("crew_challenge")
    async def crew_challenge(
        crew_id: str,
        worker_id: str,
        goal: str,
    ) -> list[TextContent]:
        """对 Worker 发出自定义挑战。"""
        result = controller.challenge(crew_id=crew_id, worker_id=worker_id, goal=goal)
        return [TextContent(type="text", text=json.dumps(result, ensure_ascii=False))]
