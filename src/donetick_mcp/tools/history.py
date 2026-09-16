"""History and analytics tools."""

import logging
from typing import Annotated

from mcp.server.mcpserver import Context, MCPServer
from pydantic import Field

from ..client import DonetickClient
from ._common import HISTORY_STATUS_EMOJI, READ_ONLY, get_client, handle_errors

logger = logging.getLogger(__name__)


async def _chore_names(client: DonetickClient) -> dict[int, str]:
    """Map chore IDs to names, since history entries only carry the chore ID."""
    try:
        chores = await client.list_chores(include_archived=True)
    except Exception as e:
        logger.warning(f"Could not fetch chore names for history: {e}")
        return {}
    return {chore.id: chore.name for chore in chores}


def register(mcp: MCPServer) -> None:
    """Register history tools."""

    @mcp.tool(annotations=READ_ONLY, structured_output=False)
    @handle_errors
    async def get_chore_history(
        ctx: Context,
        chore_id: Annotated[int, Field(description="The ID of the chore to fetch history for")],
    ) -> str:
        """Get completion history for a specific chore.

        Returns all history records including status (completed, skipped, rescheduled, ...),
        who performed them, when, and notes.
        """
        history = await get_client(ctx).get_chore_history(chore_id)
        if not history:
            return f"No completion history found for chore {chore_id}"

        entries = []
        for entry in history:
            status_emoji = "✅" if entry.performedAt else "⏳"
            completed_by = f"user {entry.completedBy}" if entry.completedBy else "Unknown"
            completed_at = entry.performedAt or "Unknown"
            notes = entry.note or "No notes"
            entries.append(
                f"{status_emoji} Completion ID: {entry.id}\n"
                f"  📌 Status: {entry.status}\n"
                f"  👤 Completed by: {completed_by}\n"
                f"  📅 Completed at: {completed_at}\n"
                f"  📝 Notes: {notes}"
            )

        return (
            f"📊 Completion History for Chore {chore_id}\n"
            f"Total completions: {len(history)}\n\n" + "\n\n".join(entries)
        )

    @mcp.tool(annotations=READ_ONLY, structured_output=False)
    @handle_errors
    async def get_all_chores_history(
        ctx: Context,
        days: Annotated[int, Field(ge=1, description="Number of days to look back (default: 7)")] = 7,
        include_circle_members: Annotated[
            bool, Field(description="Include entries of all circle members (default: false)")
        ] = False,
    ) -> str:
        """Get chore history (completions, skips, reschedules) of the last days across all chores.

        By default only your own entries are returned; set include_circle_members to include
        everyone in the circle.
        """
        client = get_client(ctx)
        history = await client.get_all_chores_history(
            days=days, include_circle_members=include_circle_members
        )
        if not history:
            return f"No chore history found in the last {days} days"

        # Chores deleted since (or private chores of others) fall back to their ID
        names = await _chore_names(client)

        by_chore: dict[int, list] = {}
        for entry in history:
            by_chore.setdefault(entry.choreId, []).append(entry)

        chore_sections = []
        for chore_id, entries in by_chore.items():
            entry_lines = []
            for entry in entries:
                status_emoji = HISTORY_STATUS_EMOJI.get(entry.status, "•")
                completed_by = f"user {entry.completedBy}" if entry.completedBy else "Unknown"
                completed_at = entry.performedAt or "Unknown"
                entry_lines.append(f"  {status_emoji} {entry.status} {completed_at} by {completed_by}")
            title = f"{names[chore_id]} (Chore #{chore_id})" if chore_id in names else f"Chore #{chore_id}"
            chore_sections.append(f"🏷️  {title}\n" + "\n".join(entry_lines))

        return (
            f"📊 Chore History (last {days} days)\n"
            f"Showing {len(history)} entries\n\n" + "\n\n".join(chore_sections)
        )

    @mcp.tool(annotations=READ_ONLY, structured_output=False)
    @handle_errors
    async def get_chore_details(
        ctx: Context,
        chore_id: Annotated[int, Field(description="The ID of the chore to fetch detailed statistics for")],
    ) -> str:
        """Get detailed chore information including completion statistics.

        Returns total completion count, last completion date and user, and tracked time.
        """
        details = await get_client(ctx).get_chore_details(chore_id)

        total_count = details.totalCompletedCount or 0
        last_completed = details.lastCompletedDate or "Never"
        last_user = f"user {details.lastCompletedBy}" if details.lastCompletedBy else "N/A"
        avg_duration = f"{details.averageDuration:.1f}s" if details.averageDuration else "N/A"
        tracked_time = f"{details.duration}s" if details.duration else "N/A"

        recent_history = [
            f"  ✅ {entry.performedAt or 'Unknown'} by "
            f"{f'user {entry.completedBy}' if entry.completedBy else 'Unknown'}"
            for entry in (details.completionHistory or [])[:5]
        ]
        history_text = "\n".join(recent_history) if recent_history else "  No completions yet"

        return (
            f"📊 Chore Details: {details.name}\n"
            f"ID: {details.id}\n\n"
            f"📈 Statistics:\n"
            f"  Total Completions: {total_count}\n"
            f"  Average Duration: {avg_duration}\n"
            f"  Tracked Time: {tracked_time}\n\n"
            f"🕐 Last Completion:\n"
            f"  Date: {last_completed}\n"
            f"  By: {last_user}\n\n"
            f"📜 Recent History (last 5):\n"
            f"{history_text}"
        )
