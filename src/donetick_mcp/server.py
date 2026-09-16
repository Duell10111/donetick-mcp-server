"""Donetick MCP server implementation."""

import logging
import sys
import urllib.parse
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from dataclasses import dataclass

from mcp.server.mcpserver import MCPServer

from . import __version__
from .client import DonetickClient
from .config import config
from .tools import register_tools

# Configure logging (stderr; stdout is reserved for the stdio transport)
config.configure_logging()
logger = logging.getLogger(__name__)

INSTRUCTIONS = """\
Tools for a Donetick instance (household chores and tasks).
- Look up IDs first: get_circle_members for users, list_labels for labels,
  list_projects for projects, list_things for things.
- create_chore accepts easy inputs (usernames, label_names, days_of_week, time_of_day,
  remind_minutes_before) and converts them to the Donetick format.
- Prefer archive_chore over delete_chore when history should be kept.
- Things hold a state (text, number, boolean) that can make chores due via set_thing_state.
"""


@dataclass
class AppContext:
    """Resources shared by all tool calls of a server session."""

    client: DonetickClient


@asynccontextmanager
async def lifespan(server: MCPServer) -> AsyncIterator[AppContext]:
    """Create the Donetick client for the session and close it on shutdown."""
    client = DonetickClient()
    try:
        yield AppContext(client=client)
    finally:
        await client.close()


mcp = MCPServer(
    name="donetick-chores",
    version=__version__,
    instructions=INSTRUCTIONS,
    lifespan=lifespan,
)
register_tools(mcp)


def sanitize_url(url: str) -> str:
    """Sanitize URL for logging by removing sensitive parts."""
    try:
        parsed = urllib.parse.urlparse(url)
        # Only show scheme and path, hide host details
        return f"{parsed.scheme}://[SERVER]{parsed.path}"
    except Exception:
        return "[URL]"


def main():
    """Main entry point for the MCP server (stdio transport)."""
    try:
        config.validate()
    except ValueError as e:
        logger.error(str(e))
        print(f"Failed to start server: {e}", file=sys.stderr)
        sys.exit(1)

    logger.info(f"Starting Donetick MCP Server v{__version__}")
    logger.info(f"Connecting to: {sanitize_url(config.donetick_base_url)}")
    logger.info(f"Username: {config.donetick_username}")

    try:
        mcp.run("stdio")
    except KeyboardInterrupt:
        logger.info("Server interrupted by user")


if __name__ == "__main__":
    main()
