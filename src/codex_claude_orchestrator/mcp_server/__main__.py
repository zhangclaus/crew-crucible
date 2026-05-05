from __future__ import annotations

import asyncio

from mcp.server.stdio import stdio_server

from codex_claude_orchestrator.mcp_server.server import create_server


async def main() -> None:
    server = create_server()
    async with stdio_server() as (read_stream, write_stream):
        await server.run(read_stream, write_stream, server.create_initialization_options())


if __name__ == "__main__":
    asyncio.run(main())
