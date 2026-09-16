"""MCP tool definitions, grouped by domain."""

from mcp.server.mcpserver import MCPServer

from . import chore_actions, chores, circle, history, labels, things


def register_tools(mcp: MCPServer) -> None:
    """Register all Donetick tools on the server."""
    for module in (chores, chore_actions, labels, circle, history, things):
        module.register(mcp)
