from __future__ import annotations

import json
from pathlib import Path

from mcp.server import Server
from mcp.types import TextContent

from codex_claude_orchestrator.crew.models import WorkerRole
from codex_claude_orchestrator.mcp_server.context.compressor import compress_crew_status


def register_lifecycle_tools(server: Server, controller) -> None:

    @server.tool("crew_start")
    async def crew_start(
        repo: str,
        goal: str,
        roles: list[str] | None = None,
    ) -> list[TextContent]:
        """启动一个 Crew。roles 默认为 explorer, implementer, reviewer。"""
        selected = roles or ["explorer", "implementer", "reviewer"]
        worker_roles = [WorkerRole(r) for r in selected]
        crew = controller.start(
            repo_root=Path(repo),
            goal=goal,
            worker_roles=worker_roles,
        )
        return [TextContent(type="text", text=json.dumps({
            "crew_id": crew.crew_id,
            "status": crew.status.value,
        }))]

    @server.tool("crew_stop")
    async def crew_stop(crew_id: str) -> list[TextContent]:
        """停止整个 Crew。"""
        controller.stop(crew_id=crew_id)
        return [TextContent(type="text", text=json.dumps({"status": "stopped", "crew_id": crew_id}))]

    @server.tool("crew_status")
    async def crew_status(crew_id: str) -> list[TextContent]:
        """获取 Crew 状态摘要（压缩后，非原始 dump）。"""
        raw = controller.status(crew_id=crew_id)
        compressed = compress_crew_status(raw)
        return [TextContent(type="text", text=json.dumps(compressed, ensure_ascii=False))]
