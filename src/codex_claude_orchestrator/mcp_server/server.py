from __future__ import annotations

from mcp.server import Server


def create_server() -> Server:
    server = Server("crew-orchestrator")
    return server
