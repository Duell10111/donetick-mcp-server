"""Thing tools: things are named states (e.g. sensors or counters) that can trigger chores."""

from typing import Annotated, Literal

from mcp.server.mcpserver import Context, MCPServer
from pydantic import Field

from ._common import (
    DESTRUCTIVE,
    IDEMPOTENT_WRITE,
    READ_ONLY,
    WRITE,
    get_client,
    handle_errors,
    to_json,
)

ThingId = Annotated[int, Field(description="Thing ID")]
ThingType = Literal["text", "number", "boolean"]
ThingState = str | int | bool


def register(mcp: MCPServer) -> None:
    """Register thing tools."""

    @mcp.tool(annotations=READ_ONLY, structured_output=False)
    @handle_errors
    async def list_things(ctx: Context) -> str:
        """List your things.

        Things are named values (text, number or boolean) such as sensors or counters, e.g. set from
        Home Assistant. Their state can trigger chores. Things are private to their owner.
        Returns ID, name, type and current state. A chore's trigger is shown in its thingChore
        field (get_chore).
        """
        things = await get_client(ctx).list_things()
        if not things:
            return "No things found."
        return to_json({"count": len(things), "things": [thing.model_dump() for thing in things]})

    @mcp.tool(annotations=WRITE, structured_output=False)
    @handle_errors
    async def create_thing(
        ctx: Context,
        name: Annotated[str, Field(description="Thing name")],
        type: Annotated[ThingType, Field(description="text, number (integers) or boolean")],
        state: Annotated[
            ThingState | None, Field(description="Initial state matching the type (optional)")
        ] = None,
    ) -> str:
        """Create a thing whose state can trigger chores.

        Examples: {name: 'Washing machine running', type: 'boolean', state: false},
        {name: 'Litter box uses', type: 'number', state: 0}
        """
        thing = await get_client(ctx).create_thing(name, type, state)
        return f"Successfully created thing '{thing.name}' (ID: {thing.id})\n\n{to_json(thing.model_dump())}"

    @mcp.tool(annotations=IDEMPOTENT_WRITE, structured_output=False)
    @handle_errors
    async def update_thing(
        ctx: Context,
        thing_id: ThingId,
        name: Annotated[str | None, Field(description="New name")] = None,
        type: Annotated[
            ThingType | None, Field(description="New type (the state must be valid for it)")
        ] = None,
        state: Annotated[
            ThingState | None, Field(description="New state without evaluating chore triggers")
        ] = None,
    ) -> str:
        """Rename a thing or change its type.

        Changing the state here does NOT trigger chores; use set_thing_state for that.
        """
        thing = await get_client(ctx).update_thing(thing_id, name=name, thing_type=type, state=state)
        return f"Successfully updated thing '{thing.name}' (ID: {thing.id})\n\n{to_json(thing.model_dump())}"

    @mcp.tool(annotations=WRITE, structured_output=False)
    @handle_errors
    async def set_thing_state(
        ctx: Context,
        thing_id: ThingId,
        state: Annotated[ThingState | None, Field(description="New state matching the thing type")] = None,
        increment: Annotated[
            int | None,
            Field(description="Add this value to the current state (number things, may be negative)"),
        ] = None,
    ) -> str:
        """Set a thing's state and evaluate the triggers of linked chores.

        Matching chores without due date become due now. Use increment to add to a number thing.
        """
        thing, triggered = await get_client(ctx).set_thing_state(
            thing_id, state=state, increment=increment
        )
        if triggered:
            trigger_text = (
                f"Triggered chores: {', '.join(str(c) for c in triggered)} "
                "(due now if they had no due date)"
            )
        else:
            trigger_text = "No chore triggers matched."
        return f"Set state of thing '{thing.name}' (ID: {thing.id}) to '{thing.state}'. {trigger_text}"

    @mcp.tool(annotations=READ_ONLY, structured_output=False)
    @handle_errors
    async def get_thing_history(
        ctx: Context,
        thing_id: ThingId,
        offset: Annotated[
            int, Field(ge=0, description="Number of entries to skip, e.g. 10 for the second page (default: 0)")
        ] = 0,
    ) -> str:
        """Get the state history of a thing, newest first (10 entries per page)."""
        history = await get_client(ctx).get_thing_history(thing_id, offset=offset)
        if not history:
            return f"No history found for thing {thing_id}."
        lines = [f"  {entry.createdAt or entry.updatedAt or 'Unknown'}: {entry.state}" for entry in history]
        return (
            f"📊 State history for thing {thing_id} ({len(history)} entries, offset {offset})\n"
            + "\n".join(lines)
        )

    @mcp.tool(annotations=DESTRUCTIVE, structured_output=False)
    @handle_errors
    async def delete_thing(ctx: Context, thing_id: ThingId) -> str:
        """Delete a thing.

        Fails while chores are still triggered by it; remove their trigger first with
        update_chore (remove_thing_trigger).
        """
        await get_client(ctx).delete_thing(thing_id)
        return f"Successfully deleted thing with ID {thing_id}."
