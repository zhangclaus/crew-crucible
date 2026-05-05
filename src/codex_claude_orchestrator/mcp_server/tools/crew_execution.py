from __future__ import annotations

import json

from mcp.server.fastmcp import Context, FastMCP
from mcp.types import TextContent


def register_execution_tools(server: FastMCP, controller, supervision_loop=None) -> None:

    @server.tool("crew_run")
    async def crew_run(
        crew_id: str,
        ctx: Context,
        max_rounds: int = 3,
        verification_commands: list[str] | None = None,
    ) -> list[TextContent]:
        """运行完整监督循环。需要决策时通过 sampling 请求 supervisor。长时间运行，调一次等最终结果。"""
        if supervision_loop is None:
            return [TextContent(type="text", text=json.dumps({
                "error": "supervision_loop not initialized"
            }))]

        result = await supervision_loop.run(
            crew_id=crew_id,
            max_rounds=max_rounds,
            verification_commands=verification_commands or [],
            sampling_fn=lambda msgs, sys_prompt, max_tok: ctx.session.create_message(
                messages=msgs,
                max_tokens=max_tok,
                system_prompt=sys_prompt,
            ),
        )
        return [TextContent(type="text", text=json.dumps(result, ensure_ascii=False))]
