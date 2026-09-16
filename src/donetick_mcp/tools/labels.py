"""Label tools."""

from typing import Annotated

from mcp.server.mcpserver import Context, MCPServer
from pydantic import Field

from ._common import DESTRUCTIVE, IDEMPOTENT_WRITE, READ_ONLY, WRITE, get_client, handle_errors


def register(mcp: MCPServer) -> None:
    """Register label tools."""

    @mcp.tool(annotations=READ_ONLY, structured_output=False)
    @handle_errors
    async def list_labels(ctx: Context) -> str:
        """List all labels in the circle.

        Returns all available labels with their IDs, names, and colors.
        Use these labels to organize and categorize chores.
        """
        labels = await get_client(ctx).get_labels()
        if not labels:
            return "No labels found in this circle."

        labels_text = "Available Labels:\n\n"
        for label in labels:
            color_info = f" (Color: {label.color})" if label.color else ""
            labels_text += f"- ID {label.id}: {label.name}{color_info}\n"
        return labels_text

    @mcp.tool(annotations=WRITE, structured_output=False)
    @handle_errors
    async def create_label(
        ctx: Context,
        name: Annotated[str, Field(description="Label name (required)")],
        color: Annotated[
            str | None, Field(description="Label color in hex format (e.g., '#80d8ff'), optional")
        ] = None,
    ) -> str:
        """Create a new label for organizing chores.

        Labels help categorize and filter chores by type, location, or any custom criteria.
        Optionally specify a color in hex format (e.g., '#FF5733').
        """
        label = await get_client(ctx).create_label(name=name, color=color)
        color_info = f" with color {label.color}" if label.color else ""
        return f"Successfully created label '{label.name}' (ID: {label.id}){color_info}."

    @mcp.tool(annotations=IDEMPOTENT_WRITE, structured_output=False)
    @handle_errors
    async def update_label(
        ctx: Context,
        label_id: Annotated[int, Field(description="The ID of the label to update")],
        name: Annotated[str, Field(description="New label name")],
        color: Annotated[
            str | None, Field(description="New label color in hex format (e.g., '#80d8ff'), optional")
        ] = None,
    ) -> str:
        """Update an existing label's name and/or color."""
        label = await get_client(ctx).update_label(label_id=label_id, name=name, color=color)
        color_info = f" with color {label.color}" if label.color else ""
        return f"Successfully updated label ID {label.id} to '{label.name}'{color_info}."

    @mcp.tool(annotations=DESTRUCTIVE, structured_output=False)
    @handle_errors
    async def delete_label(
        ctx: Context,
        label_id: Annotated[int, Field(description="The ID of the label to delete")],
    ) -> str:
        """Delete a label permanently.

        This will remove the label from all chores that use it. This action cannot be undone.
        """
        await get_client(ctx).delete_label(label_id)
        return f"Successfully deleted label with ID {label_id}."
