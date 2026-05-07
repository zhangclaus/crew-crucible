from __future__ import annotations

import json

from mcp.server.fastmcp import FastMCP
from mcp.types import TextContent


def register_decision_tools(server: FastMCP, controller) -> None:

    @server.tool("crew_accept")
    async def crew_accept(crew_id: str, summary: str = "accepted by supervisor") -> list[TextContent]:
        """接受当前 Crew 结果，触发合并。"""
        try:
            result = controller.accept(crew_id=crew_id, summary=summary)
            return [TextContent(type="text", text=json.dumps(result, ensure_ascii=False))]
        except FileNotFoundError as exc:
            return [TextContent(type="text", text=json.dumps({"error": str(exc)}, ensure_ascii=False))]
        except ValueError as exc:
            return [TextContent(type="text", text=json.dumps({"error": str(exc)}, ensure_ascii=False))]
        except Exception as exc:
            return [TextContent(type="text", text=json.dumps({"error": f"internal: {exc}"}, ensure_ascii=False))]

    @server.tool("crew_challenge")
    async def crew_challenge(
        crew_id: str,
        summary: str,
        task_id: str | None = None,
    ) -> list[TextContent]:
        """对 Worker 发出挑战，记录 RISK 黑板条目。"""
        try:
            result = controller.challenge(crew_id=crew_id, summary=summary, task_id=task_id)
            return [TextContent(type="text", text=json.dumps(result, ensure_ascii=False))]
        except FileNotFoundError as exc:
            return [TextContent(type="text", text=json.dumps({"error": str(exc)}, ensure_ascii=False))]
        except ValueError as exc:
            return [TextContent(type="text", text=json.dumps({"error": str(exc)}, ensure_ascii=False))]
        except Exception as exc:
            return [TextContent(type="text", text=json.dumps({"error": f"internal: {exc}"}, ensure_ascii=False))]
