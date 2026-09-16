"""Chore tools: list, get, create, update, complete, skip, delete."""

from typing import Annotated, Any, Literal

from mcp.server.mcpserver import Context, MCPServer
from mcp.server.mcpserver.exceptions import ToolError
from pydantic import Field

from ..models import ChoreCreate, ChoreUpdate, normalize_datetime
from ._common import (
    DESTRUCTIVE,
    IDEMPOTENT_WRITE,
    READ_ONLY,
    WRITE,
    get_client,
    handle_errors,
    to_json,
)

FrequencyType = Literal[
    "once",
    "daily",
    "weekly",
    "monthly",
    "yearly",
    "interval_based",
    "interval",
    "days_of_the_week",
    "day_of_the_month",
    "adaptive",
    "trigger",
    "no_repeat",
]
AssignStrategy = Literal[
    "least_completed",
    "least_assigned",
    "round_robin",
    "random",
    "keep_last_assigned",
    "random_except_last_assigned",
    "no_assignee",
]
TriggerCondition = Literal["eq", "neq", "gt", "lt", "gte", "lte"]
ThingState = str | int | bool

FREQUENCY_TYPE_DESCRIPTION = (
    "How often the chore repeats (default: once).\n\n"
    "FREQUENCY TYPES:\n"
    "• once / no_repeat: One-time chore, no recurrence\n"
    "• daily: Repeats every day at specified time\n"
    "• weekly: Repeats every week (use with frequency for bi-weekly: frequency=2)\n"
    "• days_of_the_week: Specific days (Mon, Wed, Fri) - BEST for multiple days/week\n"
    "  → Use with days_of_week parameter: ['Mon', 'Wed', 'Fri']\n"
    "• monthly: Repeats every month\n"
    "• yearly: Repeats every year\n"
    "• day_of_the_month: Specific day of month (e.g., 15th of each month)\n"
    "• interval_based / interval: Custom interval (e.g., every N days)\n"
    "• adaptive: Smart scheduling based on completion patterns\n"
    "• trigger: Due when a thing reaches a state (use thing_id and thing_trigger_state)\n\n"
    "TIP: For chores on specific days (Mon/Wed/Fri), use frequency_type='days_of_the_week' "
    "with days_of_week=['Mon', 'Wed', 'Fri'] instead of frequency_type='weekly'"
)

ChoreId = Annotated[int, Field(description="The ID of the chore")]


def _chore_text(summary: str, chore: Any) -> str:
    return f"{summary}\n\n{to_json(chore.model_dump())}"


def register(mcp: MCPServer) -> None:
    """Register chore tools."""

    @mcp.tool(annotations=READ_ONLY, structured_output=False)
    @handle_errors
    async def list_chores(
        ctx: Context,
        filter_active: Annotated[
            bool | None,
            Field(
                description="true or null = active chores (default), false = inactive chores "
                "(archived chores and completed one-time chores)"
            ),
        ] = None,
        assigned_to_user_id: Annotated[
            int | None, Field(description="Filter by assigned user ID (null=all users)")
        ] = None,
        detail_level: Annotated[
            Literal["brief", "full"],
            Field(
                description="Response format: 'brief' (id, name, status, assignee, dueDate) "
                "or 'full' (all fields). Default: 'full'"
            ),
        ] = "full",
    ) -> str:
        """List chores from Donetick.

        Returns active chores by default; set filter_active=false for inactive ones.
        Optionally filter by assigned user. Returns comprehensive chore details
        including name, description, due dates, assignees, and status. Use detail_level to control
        response size: 'brief' for essential fields only, 'full' for complete details (default).
        """
        chores = await get_client(ctx).list_chores(
            filter_active=filter_active,
            assigned_to_user_id=assigned_to_user_id,
        )

        if not chores:
            return "No chores found."

        if detail_level == "brief":
            chore_list = [
                {
                    "id": chore.id,
                    "name": chore.name,
                    "isActive": chore.isActive,
                    "assignedTo": chore.assignedTo,
                    "nextDueDate": chore.nextDueDate,
                }
                for chore in chores
            ]
        else:
            chore_list = [chore.model_dump() for chore in chores]

        return to_json({"count": len(chore_list), "chores": chore_list})

    @mcp.tool(annotations=READ_ONLY, structured_output=False)
    @handle_errors
    async def get_chore(ctx: Context, chore_id: ChoreId) -> str:
        """Get details of a specific chore by its ID.

        Returns complete chore information including all metadata, assignees, labels,
        sub-tasks, thing trigger and scheduling details.
        """
        chore = await get_client(ctx).get_chore(chore_id)
        if not chore:
            raise ToolError(f"Chore with ID {chore_id} not found.")
        return to_json(chore.model_dump())

    @mcp.tool(annotations=WRITE, structured_output=False)
    @handle_errors
    async def create_chore(
        ctx: Context,
        # Basic information
        name: Annotated[str, Field(description="Chore name (required, 1-200 characters)")],
        description: Annotated[
            str | None, Field(description="Chore description (optional, max 5000 characters)")
        ] = None,
        due_date: Annotated[
            str | None,
            Field(
                description="Due date in RFC3339 or YYYY-MM-DD format (optional). "
                "A date without time means 12:00 in the given timezone."
            ),
        ] = None,
        created_by: Annotated[int | None, Field(description="User ID of the creator (optional)")] = None,
        # Recurrence
        frequency_type: Annotated[
            FrequencyType | None, Field(description=FREQUENCY_TYPE_DESCRIPTION)
        ] = None,
        frequency: Annotated[
            int, Field(ge=1, description="Frequency multiplier (e.g., 1=weekly, 2=biweekly, default: 1)")
        ] = 1,
        frequency_metadata: Annotated[
            dict[str, Any] | None,
            Field(description="Advanced frequency config in API format (days, time, timezone, weekPattern)"),
        ] = None,
        is_rolling: Annotated[
            bool, Field(description="Rolling schedule (next due based on completion) vs fixed (default: false)")
        ] = False,
        # Assignment
        assigned_to: Annotated[int | None, Field(description="Primary assigned user ID (optional)")] = None,
        assignees: Annotated[
            list[dict[str, int]] | None,
            Field(description='Multiple assignees as array of {"userId": int} objects'),
        ] = None,
        assign_strategy: Annotated[
            AssignStrategy, Field(description="Assignment strategy (default: least_completed)")
        ] = "least_completed",
        # Organization
        priority: Annotated[
            int | None,
            Field(ge=0, le=4, description="Priority level: 0=unset, 1=lowest, 2=low, 3=medium, 4=highest"),
        ] = None,
        labels_v2: Annotated[
            list[dict[str, int]] | None,
            Field(description='Labels by ID as array of {"id": int} objects (see list_labels)'),
        ] = None,
        project_id: Annotated[
            int | None, Field(description="Project to add the chore to (see list_projects)")
        ] = None,
        # Status & gamification
        is_active: Annotated[bool, Field(description="Active status (default: true)")] = True,
        is_private: Annotated[
            bool, Field(description="Private chore visible only to creator and assignees (default: false)")
        ] = False,
        points: Annotated[int | None, Field(ge=0, description="Points awarded for completion")] = None,
        completion_window: Annotated[
            int | None,
            Field(ge=0, description="SECONDS before due time when the chore can be completed early"),
        ] = None,
        require_approval: Annotated[
            bool, Field(description="Completion must be approved by a circle admin (default: false)")
        ] = False,
        # Notifications
        notification: Annotated[
            bool | None,
            Field(description="Enable notifications (default: enabled when reminders are configured)"),
        ] = None,
        notification_metadata: Annotated[
            dict[str, Any] | None,
            Field(
                description="Advanced notification config: {templates: [{value: int, unit: 'm'|'h'|'d'}], "
                "nagging: bool, predue: bool}. Negative value = before due time, max 5 templates."
            ),
        ] = None,
        # Sub-tasks
        sub_tasks: Annotated[
            list[dict[str, Any]] | None,
            Field(description="Sub-tasks in API format ({name, orderId})"),
        ] = None,
        # Natural language inputs, transformed to the API format
        usernames: Annotated[
            list[str] | None,
            Field(
                description="EASY: Assign by usernames instead of IDs (e.g., ['Alice', 'Bob']). "
                "First user becomes primary assignee."
            ),
        ] = None,
        label_names: Annotated[
            list[str] | None,
            Field(description="EASY: Label by names instead of IDs (e.g., ['cleaning', 'urgent'])"),
        ] = None,
        days_of_week: Annotated[
            list[str] | None,
            Field(
                description="EASY: Days as short names (e.g., ['Mon', 'Wed', 'Fri'] or ['monday', 'wednesday']). "
                "Auto-sets frequency_type to days_of_the_week. REQUIRED when "
                "frequency_type='days_of_the_week'. Valid values: Mon/Monday, Tue/Tuesday, "
                "Wed/Wednesday, Thu/Thursday, Fri/Friday, Sat/Saturday, Sun/Sunday"
            ),
        ] = None,
        time_of_day: Annotated[
            str | None, Field(description="EASY: Time in HH:MM format (e.g., '16:00' for 4pm)")
        ] = None,
        timezone: Annotated[
            str,
            Field(description="IANA timezone name used for days_of_week, time_of_day and due_date"),
        ] = "America/New_York",
        remind_minutes_before: Annotated[
            int | None,
            Field(description="EASY: Remind X minutes before due time (e.g., 15 for 15 minutes before)"),
        ] = None,
        remind_at_due_time: Annotated[
            bool, Field(description="EASY: Also remind exactly at due time (default: false)")
        ] = False,
        enable_nagging: Annotated[
            bool,
            Field(description="EASY: Repeated reminders if not completed (default: false)"),
        ] = False,
        enable_predue: Annotated[
            bool, Field(description="EASY: Reminders before the due date arrives (default: false)")
        ] = False,
        subtask_names: Annotated[
            list[str] | None,
            Field(description="EASY: Subtask names as simple strings (e.g., ['Do homework', 'Check work'])"),
        ] = None,
        # Thing trigger
        thing_id: Annotated[
            int | None,
            Field(
                description="Thing whose state triggers this chore (see list_things). The chore becomes due "
                "when the thing state matches. Sets frequency_type to 'trigger' unless given. "
                "Requires thing_trigger_state."
            ),
        ] = None,
        thing_trigger_state: Annotated[
            ThingState | None,
            Field(description="State compared against the thing state (e.g. 'false', 10, 'done')"),
        ] = None,
        thing_trigger_condition: Annotated[
            TriggerCondition | None,
            Field(description="eq (default) or neq; gt, lt, gte, lte for number things only"),
        ] = None,
    ) -> str:
        """Create a new chore in Donetick with easy natural language inputs.

        Use simple parameters like usernames, days_of_week, and time_of_day - they're automatically
        transformed to the correct API format.

        EXAMPLES:
        1. Simple recurring chore:
           {name: 'Take out trash', days_of_week: ['Mon', 'Thu'], time_of_day: '19:00', usernames: ['Alice']}
        2. Weekly chore with reminders:
           {name: 'Team meeting', days_of_week: ['Tue'], time_of_day: '14:00', remind_minutes_before: 15,
           usernames: ['Alice', 'Bob']}
        3. With subtasks and labels:
           {name: 'Weekly review', days_of_week: ['Fri'], time_of_day: '17:00',
           subtask_names: ['Check email', 'Update notes'], label_names: ['work', 'weekly']}
        4. Daily chore with points:
           {name: 'Exercise', frequency_type: 'daily', time_of_day: '07:00', points: 10, usernames: ['Bob']}
        5. One-time chore:
           {name: 'Fix leaky faucet', due_date: '2025-11-10', priority: 4, usernames: ['Alice']}
        6. Triggered by a thing (e.g. washing machine finished):
           {name: 'Empty washing machine', thing_id: 3, thing_trigger_state: 'false'}

        Returns the created chore with its assigned ID and all metadata.
        """
        client = get_client(ctx)

        # ===== User assignment =====
        assignees = assignees or []
        if usernames:
            username_map = await client.lookup_user_ids(usernames)
            if not username_map or len(username_map) != len(usernames):
                missing = [u for u in usernames if u not in (username_map or {})]
                raise ToolError(
                    f"Could not find user(s) in circle: {', '.join(missing)}\n\n"
                    "💡 Hint: Use get_circle_members to see available users.\n"
                    "   Valid users must be members of your circle/household."
                )
            # First username is the primary assignee
            assigned_to = username_map.get(usernames[0])
            assignees = [{"userId": uid} for uid in username_map.values()]

        # ===== Labels =====
        labels_v2 = labels_v2 or []
        if label_names:
            label_map = await client.lookup_label_ids(label_names)
            missing_labels = [label for label in label_names if label not in (label_map or {})]
            if missing_labels:
                raise ToolError(
                    f"Label(s) not found: {', '.join(missing_labels)}\n\n"
                    "💡 Hint: Use list_labels to see available labels.\n"
                    "   You can create missing labels with create_label tool."
                )
            labels_v2 = [{"id": label_id} for label_id in label_map.values()]

        # ===== Thing trigger =====
        thing_trigger = None
        if thing_id is not None:
            if thing_trigger_state is None:
                raise ToolError(
                    "thing_trigger_state is required when thing_id is set.\n\n"
                    "💡 Hint: Use list_things to see the thing's type and current state."
                )
            # Validated before creating: Donetick links the thing after saving the chore
            thing_trigger = await client.build_thing_trigger(
                thing_id, thing_trigger_state, thing_trigger_condition
            )

        # ===== Frequency =====
        if frequency_type is None:
            frequency_type = "trigger" if thing_trigger else "once"
        frequency_metadata = frequency_metadata or {}
        days_of_week = days_of_week or []

        # Auto-set frequency type if days are specified (must happen before transform)
        if days_of_week and frequency_type == "once":
            frequency_type = "days_of_the_week"

        if frequency_type == "days_of_the_week" and not days_of_week:
            raise ToolError(
                "days_of_week parameter is required when frequency_type='days_of_the_week'.\n\n"
                "Please provide which days the chore should repeat on, for example:\n"
                "  days_of_week: ['Mon', 'Wed', 'Fri']\n"
                "  days_of_week: ['Monday', 'Tuesday', 'Thursday']\n\n"
                "Valid day values: Mon/Monday, Tue/Tuesday, Wed/Wednesday, Thu/Thursday, "
                "Fri/Friday, Sat/Saturday, Sun/Sunday"
            )

        if days_of_week or time_of_day:
            frequency_metadata = client.transform_frequency_metadata(
                frequency_type=frequency_type,
                days_of_week=days_of_week,
                time=time_of_day,
                timezone=timezone,
            )

        # ===== Notifications =====
        notification_metadata = notification_metadata or {}
        if remind_minutes_before is not None or remind_at_due_time or enable_nagging or enable_predue:
            offset_minutes = -abs(remind_minutes_before) if remind_minutes_before else None
            notification_metadata = client.transform_notification_metadata(
                offset_minutes=offset_minutes,
                remind_at_due_time=remind_at_due_time,
                nagging=enable_nagging,
                predue=enable_predue,
            )
        if notification is None:
            notification = bool(notification_metadata)

        # ===== Sub-tasks =====
        sub_tasks = sub_tasks or []
        if subtask_names:
            sub_tasks = client.transform_subtasks(subtask_names)

        # ===== Due date =====
        if due_date:
            # Donetick only accepts RFC3339; a date without time means 12:00 local time
            due_date = normalize_datetime(due_date, timezone)
        elif frequency_type not in ("once", "trigger"):
            due_date = client.calculate_due_date(
                frequency_type=frequency_type,
                frequency_metadata=frequency_metadata,
                timezone=timezone,
            )

        chore_create = ChoreCreate(
            name=name,
            description=description,
            nextDueDate=due_date,
            createdBy=created_by,
            frequencyType=frequency_type,
            frequency=frequency,
            frequencyMetadata=frequency_metadata,
            isRolling=is_rolling,
            assignedTo=assigned_to,
            assignees=assignees,
            assignStrategy=assign_strategy,
            notification=notification,
            notificationMetadata=notification_metadata,
            priority=priority,
            labelsV2=labels_v2,
            isActive=is_active,
            isPrivate=is_private,
            points=points,
            subTasks=sub_tasks,
            thingTrigger=thing_trigger,
            projectId=project_id,
            completionWindow=completion_window,
            requireApproval=require_approval,
        )

        chore = await client.create_chore(chore_create)
        return _chore_text(f"Successfully created chore '{chore.name}' (ID: {chore.id})", chore)

    @mcp.tool(annotations=WRITE, structured_output=False)
    @handle_errors
    async def complete_chore(
        ctx: Context,
        chore_id: Annotated[int, Field(description="The ID of the chore to mark complete")],
        completed_by: Annotated[
            int | None, Field(description="User ID who completed the chore (optional, circle admins only)")
        ] = None,
        notes: Annotated[str | None, Field(description="Completion note (optional)")] = None,
        completed_at: Annotated[
            str | None,
            Field(description="Completion time in RFC3339 or YYYY-MM-DD format (optional, default: now)"),
        ] = None,
    ) -> str:
        """Mark a chore as complete.

        Optionally add a note, a completion time, or (as circle admin) the user who completed it.
        Chores requiring approval become pending approval instead.
        Returns the updated chore with its next due date.
        """
        chore = await get_client(ctx).complete_chore(
            chore_id,
            completed_by=completed_by,
            notes=notes,
            completed_at=completed_at,
        )

        # Chore status 3 = pending approval
        if chore.status == 3:
            summary = f"Chore '{chore.name}' (ID: {chore.id}) is now pending approval"
        else:
            summary = f"Successfully completed chore '{chore.name}' (ID: {chore.id})"
        return _chore_text(summary, chore)

    @mcp.tool(annotations=IDEMPOTENT_WRITE, structured_output=False)
    @handle_errors
    async def update_chore(
        ctx: Context,
        chore_id: Annotated[int, Field(description="The ID of the chore to update")],
        name: Annotated[str | None, Field(description="New chore name")] = None,
        description: Annotated[str | None, Field(description="New chore description")] = None,
        next_due_date: Annotated[
            str | None,
            Field(
                description="New due date in RFC3339 or YYYY-MM-DD format (date only means 12:00 UTC). "
                "If this is the only change, it is recorded as rescheduled in the history."
            ),
        ] = None,
        priority: Annotated[
            int | None, Field(ge=0, le=4, description="Priority level (0=unset, 1=lowest, 4=highest)")
        ] = None,
        points: Annotated[int | None, Field(ge=0, description="Points awarded for completion")] = None,
        is_active: Annotated[bool | None, Field(description="Enable/disable chore")] = None,
        is_private: Annotated[bool | None, Field(description="Hide from other circle members")] = None,
        require_approval: Annotated[
            bool | None, Field(description="Requires approval to mark complete")
        ] = None,
        frequency_type: Annotated[FrequencyType | None, Field(description="Frequency type")] = None,
        frequency: Annotated[
            int | None, Field(ge=1, description="Frequency value (e.g., 2 for every 2 weeks)")
        ] = None,
        frequency_metadata: Annotated[
            dict[str, Any] | None,
            Field(description="Frequency metadata with days, time, timezone, weekPattern"),
        ] = None,
        is_rolling: Annotated[
            bool | None, Field(description="Rolling schedule (based on completion) vs fixed schedule")
        ] = None,
        assign_strategy: Annotated[
            AssignStrategy | None, Field(description="Assignment rotation strategy")
        ] = None,
        notification: Annotated[bool | None, Field(description="Enable notifications")] = None,
        notification_metadata: Annotated[
            dict[str, Any] | None, Field(description="Notification settings (templates, nagging, predue)")
        ] = None,
        completion_window: Annotated[
            int | None, Field(ge=0, description="SECONDS before due time when early completion is allowed")
        ] = None,
        project_id: Annotated[
            int | None, Field(description="Move the chore to this project (see list_projects)")
        ] = None,
        thing_id: Annotated[
            int | None,
            Field(
                description="Link or replace the thing that triggers this chore (requires thing_trigger_state). "
                "Combine with frequency_type='trigger' to only schedule the chore by the thing."
            ),
        ] = None,
        thing_trigger_state: Annotated[
            ThingState | None, Field(description="State compared against the thing state")
        ] = None,
        thing_trigger_condition: Annotated[
            TriggerCondition | None,
            Field(description="eq (default) or neq; gt, lt, gte, lte for number things only"),
        ] = None,
        remove_thing_trigger: Annotated[
            bool, Field(description="Remove the chore's thing trigger (existing triggers are kept otherwise)")
        ] = False,
    ) -> str:
        """Update an existing chore with new values.

        Can modify any chore property including name, description, schedule, priority, points,
        privacy settings, project and thing trigger. Only provide fields you want to change -
        other fields remain unchanged.
        """
        client = get_client(ctx)

        update_data = {
            "name": name,
            "description": description,
            "nextDueDate": next_due_date,
            "priority": priority,
            "points": points,
            "isActive": is_active,
            "isPrivate": is_private,
            "requireApproval": require_approval,
            "frequencyType": frequency_type,
            "frequency": frequency,
            "frequencyMetadata": frequency_metadata,
            "isRolling": is_rolling,
            "assignStrategy": assign_strategy,
            "notification": notification,
            "notificationMetadata": notification_metadata,
            "completionWindow": completion_window,
            "projectId": project_id,
        }
        update_data = {key: value for key, value in update_data.items() if value is not None}

        if thing_id is not None:
            if remove_thing_trigger:
                raise ValueError("Use either thing_id or remove_thing_trigger, not both")
            if thing_trigger_state is None:
                raise ValueError("thing_trigger_state is required when thing_id is set")
            update_data["thingTrigger"] = await client.build_thing_trigger(
                thing_id, thing_trigger_state, thing_trigger_condition
            )

        chore = await client.update_chore(
            chore_id, ChoreUpdate(**update_data), remove_thing_trigger=remove_thing_trigger
        )
        return _chore_text(f"Successfully updated chore '{chore.name}' (ID: {chore.id})", chore)

    @mcp.tool(annotations=DESTRUCTIVE, structured_output=False)
    @handle_errors
    async def delete_chore(
        ctx: Context,
        chore_id: Annotated[int, Field(description="The ID of the chore to delete")],
    ) -> str:
        """Delete a chore permanently.

        Note: Only the chore creator can delete a chore. Consider archive_chore to keep its history.
        Returns confirmation of deletion.
        """
        await get_client(ctx).delete_chore(chore_id)
        return f"Successfully deleted chore with ID {chore_id}."

    @mcp.tool(annotations=IDEMPOTENT_WRITE, structured_output=False)
    @handle_errors
    async def update_chore_priority(
        ctx: Context,
        chore_id: Annotated[int, Field(description="The ID of the chore to update")],
        priority: Annotated[
            int, Field(ge=0, le=4, description="New priority level (0=unset, 1=lowest, 4=highest)")
        ],
    ) -> str:
        """Update a chore's priority level (0-4).

        Use this to adjust how urgent a chore is without editing other details.
        0=unset, 1=lowest, 2=low, 3=medium, 4=highest. Returns the updated chore.
        """
        chore = await get_client(ctx).update_chore_priority(chore_id, priority)
        return _chore_text(
            f"Successfully updated chore '{chore.name}' (ID: {chore.id}) priority to {priority}", chore
        )

    @mcp.tool(annotations=IDEMPOTENT_WRITE, structured_output=False)
    @handle_errors
    async def update_chore_assignee(
        ctx: Context,
        chore_id: Annotated[int, Field(description="The ID of the chore to update")],
        user_id: Annotated[
            int, Field(description="User ID of the new assignee (from get_circle_members)")
        ],
    ) -> str:
        """Reassign a chore to a different circle member.

        The user becomes the only assignee. Get member IDs from get_circle_members.
        Returns the updated chore.
        """
        chore = await get_client(ctx).update_chore_assignee(chore_id, user_id)
        return _chore_text(
            f"Successfully reassigned chore '{chore.name}' (ID: {chore.id}) to user {user_id}", chore
        )

    @mcp.tool(annotations=WRITE, structured_output=False)
    @handle_errors
    async def skip_chore(
        ctx: Context,
        chore_id: Annotated[int, Field(description="The ID of the chore to skip")],
    ) -> str:
        """Skip a chore without marking it complete.

        For recurring chores, this schedules the next occurrence without completing the current one.
        Useful for chores that aren't needed this cycle. One-time chores will be marked as inactive.
        Returns the updated chore with the new due date.
        """
        chore = await get_client(ctx).skip_chore(chore_id)
        return _chore_text(
            f"Successfully skipped chore '{chore.name}' (ID: {chore.id}). Next due date: {chore.nextDueDate}",
            chore,
        )

    @mcp.tool(annotations=IDEMPOTENT_WRITE, structured_output=False)
    @handle_errors
    async def update_subtask_completion(
        ctx: Context,
        chore_id: Annotated[int, Field(description="The ID of the chore containing the subtask")],
        subtask_id: Annotated[int, Field(description="The ID of the subtask to update")],
        completed: Annotated[bool, Field(description="True to mark complete, False to mark incomplete")],
    ) -> str:
        """Mark a subtask as complete or incomplete within a chore.

        This allows tracking progress on chores with multiple steps without completing the entire chore.
        Returns the updated chore with subtask progress.
        """
        chore = await get_client(ctx).update_subtask_completion(chore_id, subtask_id, completed)

        total_subtasks = len(chore.subTasks)
        completed_subtasks = sum(1 for st in chore.subTasks if st.get("completedAt"))
        progress_pct = (completed_subtasks / total_subtasks * 100) if total_subtasks > 0 else 0

        subtasks_text = "\n".join(
            f"  {'✅' if st.get('completedAt') else '⬜'} {st.get('name', 'Unnamed')} (ID: {st.get('id')})"
            for st in chore.subTasks
        )

        return (
            f"✅ Successfully updated subtask {subtask_id} on chore '{chore.name}' (ID: {chore.id})\n\n"
            f"📊 Progress: {completed_subtasks}/{total_subtasks} subtasks complete ({progress_pct:.0f}%)\n\n"
            f"Subtasks:\n{subtasks_text}"
        )
