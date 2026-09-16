"""Shared helpers for MCP tool modules: client access, error handling, formatting."""

import functools
import json
import logging
from collections.abc import Awaitable, Callable
from typing import Any, TypeVar

import httpx
from mcp.server.mcpserver import Context
from mcp.server.mcpserver.exceptions import ToolError
from mcp.types import ToolAnnotations

from ..client import DonetickAuthError, DonetickClient

logger = logging.getLogger(__name__)

T = TypeVar("T")

# Tool annotation presets (hints for clients, e.g. to ask before destructive calls)
READ_ONLY = ToolAnnotations(readOnlyHint=True, openWorldHint=True)
WRITE = ToolAnnotations(readOnlyHint=False, destructiveHint=False, openWorldHint=True)
IDEMPOTENT_WRITE = ToolAnnotations(
    readOnlyHint=False, destructiveHint=False, idempotentHint=True, openWorldHint=True
)
DESTRUCTIVE = ToolAnnotations(readOnlyHint=False, destructiveHint=True, openWorldHint=True)

HISTORY_STATUS_EMOJI = {
    "started": "▶️",
    "completed": "✅",
    "skipped": "⏭️",
    "pending_approval": "⏳",
    "rejected": "❌",
    "missed": "⚠️",
    "rescheduled": "📅",
}


def get_client(ctx: Context) -> DonetickClient:
    """Get the Donetick client created by the server lifespan."""
    return ctx.request_context.lifespan_context.client


def to_json(data: Any) -> str:
    """Format data as indented JSON for tool output."""
    return json.dumps(data, indent=2)


def _api_error_message(response: httpx.Response) -> str | None:
    """Extract Donetick's error message from a response."""
    try:
        data = response.json()
        if isinstance(data, dict):
            return data.get("error") or data.get("message")
    except ValueError:
        pass
    return response.text[:200] if response.text else None


def _http_error_message(tool_name: str, error: httpx.HTTPStatusError) -> str:
    """Build a helpful error message with hints for an HTTP error."""
    api_error = _api_error_message(error.response)
    status_code = error.response.status_code

    if status_code == 401:
        return (
            "Authentication failed. Please check your username and password.\n\n"
            "💡 Hint: Verify credentials in environment variables or .env file:\n"
            "   - DONETICK_BASE_URL\n"
            "   - DONETICK_USERNAME\n"
            "   - DONETICK_PASSWORD"
        )
    if status_code == 403:
        reason = f" ({api_error})" if api_error else ""
        return (
            f"Permission denied{reason}. You may not have authorization for this operation.\n\n"
            "💡 Hint: Verify that:\n"
            "   - You have the correct credentials\n"
            "   - The resource exists and belongs to your circle\n"
            "   - You have the necessary permissions (e.g., only creators can delete chores)"
        )
    if status_code == 404:
        if "chore" in tool_name:
            return (
                "Chore not found.\n\n"
                "💡 Hint: Use list_chores to see available chores and their IDs.\n"
                "   Chores may have been deleted or the ID may be incorrect."
            )
        if "label" in tool_name:
            return (
                "Label not found.\n\n"
                "💡 Hint: Use list_labels to see available labels and their IDs.\n"
                "   Labels may have been deleted or the ID may be incorrect."
            )
        if "thing" in tool_name:
            return (
                "Thing not found.\n\n"
                "💡 Hint: Use list_things to see your things and their IDs."
            )
        return "Resource not found."
    if status_code == 422:
        base_msg = f"Validation error: {api_error}" if api_error else (
            "Validation error. The API rejected the request parameters."
        )
        return (
            f"{base_msg}\n\n"
            "💡 Hint: Common issues:\n"
            "   - Invalid date format (use YYYY-MM-DD or RFC3339)\n"
            "   - Invalid frequency_type (use: once, daily, weekly, days_of_the_week, etc.)\n"
            "   - Missing required fields (name, due_date for some operations)\n"
            "   - Invalid user or label IDs (use get_circle_members or list_labels first)"
        )
    if status_code == 429:
        return (
            "Rate limit exceeded. The server is receiving too many requests.\n\n"
            "💡 Hint: Wait a few seconds before retrying. The rate limit is\n"
            "   typically 10 requests per second."
        )
    if 400 <= status_code < 500:
        if api_error:
            return (
                f"API Error: {api_error}\n\n"
                "💡 Hint: Review the error message above and check:\n"
                "   - Required fields are provided\n"
                "   - Data types match expectations (IDs are integers, names are strings)\n"
                "   - Values are in correct format (dates, colors, etc.)\n"
                "   - User IDs exist in your circle (use list_circle_users to check)"
            )
        return (
            f"Request failed with status {status_code}. Please check your input.\n\n"
            "💡 Hint: Review the tool's input parameters and ensure:\n"
            "   - Required fields are provided\n"
            "   - Data types match expectations (IDs are integers, names are strings)\n"
            "   - Values are in correct format (dates, colors, etc.)"
        )
    if api_error:
        return (
            f"Server error ({status_code}): {api_error}\n\n"
            "💡 Hint: This is a server-side issue. Try again in a moment.\n"
            "   If the problem persists, check the Donetick server status."
        )
    return (
        f"API request failed with status {status_code}.\n\n"
        "💡 Hint: This is likely a server-side issue. Try again in a moment.\n"
        "   If the problem persists, check the Donetick server status."
    )


def handle_errors(fn: Callable[..., Awaitable[T]]) -> Callable[..., Awaitable[T]]:
    """Turn errors raised by a tool into ToolErrors with helpful hints.

    ToolError results are marked as errors for the model (isError) without leaking
    internal details. functools.wraps keeps the signature, which MCPServer uses to
    build the tool's input schema.
    """
    tool_name = fn.__name__

    @functools.wraps(fn)
    async def wrapper(*args: Any, **kwargs: Any) -> T:
        try:
            return await fn(*args, **kwargs)
        except ToolError:
            raise
        except httpx.HTTPStatusError as e:
            logger.error(
                f"HTTP error executing tool {tool_name}: {e.response.status_code} - {e.response.text}"
            )
            raise ToolError(_http_error_message(tool_name, e)) from e
        except DonetickAuthError as e:
            logger.error(f"Authentication error executing tool {tool_name}: {e}")
            raise ToolError(
                f"Authentication failed. {e}\n\n"
                "💡 Hint: Verify the configuration in environment variables or .env file:\n"
                "   - DONETICK_BASE_URL\n"
                "   - DONETICK_USERNAME\n"
                "   - DONETICK_PASSWORD"
            ) from e
        except httpx.TimeoutException as e:
            logger.error(f"Timeout executing tool {tool_name}: {e}")
            raise ToolError(
                "Request timed out.\n\n"
                "💡 Hint: The Donetick server took too long to respond. This could mean:\n"
                "   - The server is under heavy load\n"
                "   - Network connectivity issues\n"
                "   - The server may be down\n"
                "Try again in a few moments."
            ) from e
        except ValueError as e:
            # Validation errors (safe to expose)
            logger.warning(f"Validation error in tool {tool_name}: {e}")
            raise ToolError(
                f"Validation Error: {e}\n\n"
                "💡 Hint: This is a data validation error. Check that:\n"
                "   - All required parameters are provided\n"
                "   - Data types are correct (numbers as integers, text as strings)\n"
                "   - Values are in expected format (dates, emails, URLs, etc.)"
            ) from e
        except Exception as e:
            # Log full error internally, return generic error (don't leak internals)
            logger.error(f"Unexpected error executing tool {tool_name}: {e}", exc_info=True)
            raise ToolError(
                "An unexpected error occurred while processing your request.\n\n"
                "💡 Hint: This is an internal error. Please:\n"
                "   - Check the server logs for details\n"
                "   - Try the operation again\n"
                "   - If the problem persists, report it as a bug"
            ) from e

    return wrapper
