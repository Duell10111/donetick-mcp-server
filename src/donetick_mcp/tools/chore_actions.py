"""Chore action tools: archive, undo, approval, time tracking, nudges, projects."""

from typing import Annotated

from mcp.server.mcpserver import Context, MCPServer
from pydantic import Field

from ._common import IDEMPOTENT_WRITE, READ_ONLY, WRITE, get_client, handle_errors, to_json

ChoreId = Annotated[int, Field(description="Chore ID")]


def register(mcp: MCPServer) -> None:
    """Register chore action and project tools."""

    @mcp.tool(annotations=READ_ONLY, structured_output=False)
    @handle_errors
    async def list_archived_chores(ctx: Context) -> str:
        """List archived (inactive) chores.

        Donetick archives chores by deactivating them, so completed one-time chores are listed too.
        """
        chores = await get_client(ctx).list_archived_chores()
        if not chores:
            return "No archived chores found."
        brief = [
            {"id": c.id, "name": c.name, "assignedTo": c.assignedTo, "createdBy": c.createdBy}
            for c in chores
        ]
        return to_json({"count": len(brief), "chores": brief})

    @mcp.tool(annotations=IDEMPOTENT_WRITE, structured_output=False)
    @handle_errors
    async def archive_chore(ctx: Context, chore_id: ChoreId) -> str:
        """Archive a chore instead of deleting it: it becomes inactive and sends no notifications.

        Works for the chore creator. Circle admins can also archive chores of other users; Donetick
        does not allow this directly, so the chore is deactivated with a regular update and its
        notifications are turned off. Restore with unarchive_chore.
        """
        chore, via_update = await get_client(ctx).archive_chore(chore_id)
        text = f"Successfully archived chore '{chore.name}' (ID: {chore.id}). Active: {chore.isActive}"
        if via_update:
            text += (
                "\n\nℹ️ Donetick only lets the creator archive a chore, so as circle admin it was "
                "deactivated with a regular update and its notifications were turned off."
            )
        return text

    @mcp.tool(annotations=IDEMPOTENT_WRITE, structured_output=False)
    @handle_errors
    async def unarchive_chore(ctx: Context, chore_id: ChoreId) -> str:
        """Restore an archived chore (creator, or circle admins via a regular update)."""
        chore, via_update = await get_client(ctx).unarchive_chore(chore_id)
        text = f"Successfully unarchived chore '{chore.name}' (ID: {chore.id}). Active: {chore.isActive}"
        if via_update:
            text += (
                "\n\nℹ️ Restored as circle admin with a regular update. Notifications are "
                f"{'on' if chore.notification else 'off'}; enable them with update_chore if needed."
            )
        return text

    @mcp.tool(annotations=WRITE, structured_output=False)
    @handle_errors
    async def undo_chore_action(ctx: Context, chore_id: ChoreId) -> str:
        """Undo your last completion, skip, approval submission or rejection of a chore.

        Restores the previous due date and assignee. Only works for your own action within 5 minutes.
        Note: for one-time and thing-triggered chores Donetick reactivates the chore but clears its
        due date; set it again with update_chore (next_due_date) if needed.
        """
        message, chore = await get_client(ctx).undo_chore_action(chore_id)
        return (
            f"{message} on chore '{chore.name}' (ID: {chore.id}). "
            f"Next due date: {chore.nextDueDate}, assigned to: {chore.assignedTo}"
        )

    @mcp.tool(annotations=WRITE, structured_output=False)
    @handle_errors
    async def approve_chore(ctx: Context, chore_id: ChoreId) -> str:
        """Approve a completion that is pending approval (chores with requireApproval).

        Schedules the next occurrence. Circle admins and managers only.
        """
        chore = await get_client(ctx).approve_chore(chore_id)
        return f"Approved chore '{chore.name}' (ID: {chore.id}). Next due date: {chore.nextDueDate}"

    @mcp.tool(annotations=WRITE, structured_output=False)
    @handle_errors
    async def reject_chore(
        ctx: Context,
        chore_id: ChoreId,
        notes: Annotated[str | None, Field(description="Reason for the rejection (optional)")] = None,
    ) -> str:
        """Reject a completion that is pending approval, optionally with a reason.

        Circle admins and managers only.
        """
        chore = await get_client(ctx).reject_chore(chore_id, notes=notes)
        return f"Rejected completion of chore '{chore.name}' (ID: {chore.id})."

    @mcp.tool(annotations=WRITE, structured_output=False)
    @handle_errors
    async def start_chore_timer(ctx: Context, chore_id: ChoreId) -> str:
        """Start or resume time tracking on a chore and mark it as in progress.

        Only users who can complete the chore can start it.
        """
        chore, timer = await get_client(ctx).start_chore_timer(chore_id)
        return f"Started timer on chore '{chore.name}' (ID: {chore.id}).{_duration_text(timer)}"

    @mcp.tool(annotations=WRITE, structured_output=False)
    @handle_errors
    async def pause_chore_timer(ctx: Context, chore_id: ChoreId) -> str:
        """Pause time tracking on a chore that is in progress."""
        chore, timer = await get_client(ctx).pause_chore_timer(chore_id)
        return f"Paused timer on chore '{chore.name}' (ID: {chore.id}).{_duration_text(timer)}"

    @mcp.tool(annotations=WRITE, structured_output=False)
    @handle_errors
    async def nudge_chore(
        ctx: Context,
        chore_id: ChoreId,
        all_assignees: Annotated[
            bool, Field(description="Nudge all assignees instead of only the current one (default: false)")
        ] = False,
        message: Annotated[str | None, Field(description="Custom message (optional)")] = None,
    ) -> str:
        """Send a push notification reminding the current assignee (or all assignees) about a chore.

        You cannot nudge yourself.
        """
        result, warnings = await get_client(ctx).nudge_chore(
            chore_id, all_assignees=all_assignees, message=message
        )
        if warnings:
            result += "\n\nWarnings:\n" + "\n".join(f"  - {w}" for w in warnings)
        return result

    @mcp.tool(annotations=READ_ONLY, structured_output=False)
    @handle_errors
    async def list_projects(ctx: Context) -> str:
        """List the projects of your circle. Chores can be grouped into projects (project_id)."""
        projects = await get_client(ctx).list_projects()
        if not projects:
            return "No projects found."
        return to_json({"count": len(projects), "projects": [p.model_dump() for p in projects]})


def _duration_text(timer: dict) -> str:
    duration = timer.get("duration")
    return f" Tracked time: {duration}s." if duration is not None else ""
