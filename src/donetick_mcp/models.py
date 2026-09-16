"""Pydantic models for Donetick API requests and responses."""

import re
from datetime import UTC, datetime
from typing import Any, Optional
from zoneinfo import ZoneInfo

from pydantic import AliasChoices, BaseModel, ConfigDict, Field, field_validator

# Donetick ChoreHistoryStatus values (internal/chore/model/model.go)
HISTORY_STATUS_NAMES = {
    0: "started",
    1: "completed",
    2: "skipped",
    3: "pending_approval",
    4: "rejected",
    5: "missed",
    6: "rescheduled",
}

# Thing types whose state Donetick can validate (internal/thing/helper.go)
THING_TYPES = ("text", "number", "boolean")

# Conditions for thing-triggered chores; gt/lt/gte/lte only apply to numeric states
THING_TRIGGER_CONDITIONS = ("eq", "neq", "gt", "lt", "gte", "lte")
NUMERIC_TRIGGER_CONDITIONS = ("gt", "lt", "gte", "lte")


def normalize_datetime(value: str, timezone: str = "UTC") -> str:
    """Convert a date or datetime to the RFC3339 format required by Donetick.

    Donetick parses dates as Go time.Time, which only accepts RFC3339 with a
    timezone. A date without time (YYYY-MM-DD) is interpreted as 12:00 and a
    datetime without offset as local time, both in the given timezone.

    Raises:
        ValueError: If the value is not a valid date or datetime
    """
    tz = ZoneInfo(timezone)
    if re.fullmatch(r"\d{4}-\d{2}-\d{2}", value):
        try:
            parsed = datetime.strptime(value, "%Y-%m-%d").replace(hour=12, tzinfo=tz)
        except ValueError as e:
            raise ValueError(f"Invalid date: {value}") from e
        return parsed.astimezone(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")

    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as e:
        raise ValueError(
            "Date must be in RFC3339 format (e.g., 2025-11-10T00:00:00Z) "
            "or YYYY-MM-DD format (e.g., 2025-11-10)"
        ) from e

    if parsed.tzinfo is None:
        return parsed.replace(tzinfo=tz).astimezone(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")
    return value


def normalize_thing_state(thing_type: str, state: Any) -> str:
    """Validate a thing state for its type and return it in Donetick's string format.

    Raises:
        ValueError: If the state is not valid for the thing type
    """
    if thing_type == "boolean":
        if isinstance(state, bool):
            return "true" if state else "false"
        if isinstance(state, str) and state.strip().lower() in ("true", "false"):
            return state.strip().lower()
        raise ValueError(f'State of a boolean thing must be "true" or "false", got: {state!r}')

    if thing_type == "number":
        if isinstance(state, bool):
            raise ValueError(f"State of a number thing must be an integer, got: {state!r}")
        if isinstance(state, int):
            return str(state)
        if isinstance(state, str) and re.fullmatch(r"[+-]?\d+", state.strip()):
            return str(int(state.strip()))
        raise ValueError(f"State of a number thing must be an integer, got: {state!r}")

    if thing_type == "text":
        return "" if state is None else str(state)

    raise ValueError(f'Invalid thing type "{thing_type}". Valid types: {", ".join(THING_TYPES)}')


class Assignee(BaseModel):
    """Chore assignee model."""

    userId: int = Field(..., description="User ID of the assignee")


class Label(BaseModel):
    """Chore label model."""

    id: int = Field(..., description="Label ID")
    name: str = Field(..., description="Label name")
    color: Optional[str] = Field(None, description="Label color (hex code)")
    created_by: Optional[int] = Field(None, alias="createdBy", description="User ID who created the label")


class ThingTrigger(BaseModel):
    """Trigger linking a chore to a thing state (sent as thingTrigger when saving a chore)."""

    thingID: int = Field(..., gt=0, description="ID of the thing that triggers the chore")
    triggerState: str = Field(..., description="State value the condition is compared against")
    condition: Optional[str] = Field(
        "eq",
        description="Comparison: eq, neq (any type) or gt, lt, gte, lte (number things only)",
    )

    @field_validator("condition")
    @classmethod
    def validate_condition(cls, v: Optional[str]) -> str:
        """Validate the trigger condition."""
        if v is None or v == "":
            return "eq"
        if v not in THING_TRIGGER_CONDITIONS:
            raise ValueError(
                f'Invalid trigger condition "{v}". Valid values: {", ".join(THING_TRIGGER_CONDITIONS)}'
            )
        return v


class NotificationMetadata(BaseModel):
    """Notification configuration metadata."""

    nagging: bool = Field(default=False, description="Enable nagging notifications")
    predue: bool = Field(default=False, description="Enable pre-due notifications")


class ChoreCreate(BaseModel):
    """Enhanced model for creating a new chore with full feature support."""

    model_config = ConfigDict(
        populate_by_name=True,
        json_schema_extra={
            "example": {
                "name": "Take out the trash",
                "description": "Weekly trash collection on Monday mornings",
                "nextDueDate": "2025-11-10T09:00:00Z",
                "createdBy": 1,
                "frequencyType": "weekly",
                "frequency": 1,
                "frequencyMetadata": {"days": [1], "time": "09:00"},
                "isRolling": False,
                "assignedTo": 1,
                "assignees": [{"userId": 1}, {"userId": 2}],
                "assignStrategy": "least_completed",
                "notification": True,
                "notificationMetadata": {"nagging": True, "predue": True},
                "priority": 3,
                "labels": ["cleaning", "outdoor"],
                "isActive": True,
                "isPrivate": False,
                "points": 10,
                "completionWindow": 7,
                "requireApproval": False,
                "deadlineOffset": 0,
            }
        }
    )

    # Basic Information
    name: str = Field(
        ...,
        min_length=1,
        max_length=200,
        description="Chore name (required)"
    )
    description: Optional[str] = Field(
        None,
        max_length=5000,
        description="Chore description"
    )
    nextDueDate: Optional[str] = Field(
        None,
        # "dueDate" is accepted for backwards compatibility; Donetick only reads nextDueDate
        validation_alias=AliasChoices("nextDueDate", "dueDate"),
        description="Due date in RFC3339 or YYYY-MM-DD format (sent as RFC3339)",
    )
    createdBy: Optional[int] = Field(
        None,
        description="User ID of creator"
    )

    # Recurrence/Frequency Settings
    frequencyType: Optional[str] = Field(
        default="once",
        description="Frequency type: once, daily, weekly, monthly, yearly, interval_based",
    )
    frequency: Optional[int] = Field(
        default=1,
        ge=0,
        description="Frequency value (e.g., 0=once, 1=weekly, 2=biweekly)",
    )
    frequencyMetadata: Optional[dict[str, Any]] = Field(
        default_factory=dict,
        description="Additional frequency configuration (e.g., days of week, time)",
    )
    isRolling: Optional[bool] = Field(
        default=False,
        description="Rolling schedule (next due date based on completion) vs fixed schedule",
    )

    # User Assignment
    assignedTo: Optional[int] = Field(
        None,
        description="Primary assigned user ID"
    )
    assignees: Optional[list[dict[str, int]]] = Field(
        default_factory=list,
        description="List of assignee objects with userId field",
    )
    assignStrategy: Optional[str] = Field(
        default="least_completed",
        description="Assignment strategy: least_completed, round_robin, random",
    )

    # Notifications
    notification: Optional[bool] = Field(
        default=False,
        description="Enable notifications for this chore"
    )
    notificationMetadata: Optional[dict[str, Any]] = Field(
        default=None,
        description="Notification settings with templates array: [{value: int, unit: str}]",
    )

    # Organization & Priority
    # Donetick dereferences priority on create without a nil check, so always send it
    priority: int = Field(
        0,
        ge=0,
        le=4,
        description="Priority level (0=unset, 1=lowest, 4=highest)"
    )
    labels: Optional[list[str]] = Field(
        default_factory=list,
        description="Label tags for categorization (legacy - use labelsV2)"
    )
    labelsV2: Optional[list[dict[str, int]]] = Field(
        default_factory=list,
        description="Label references - list of objects with 'id' field: [{'id': 1}, {'id': 2}]",
    )

    # Status & Visibility
    isActive: Optional[bool] = Field(
        default=True,
        description="Active status (inactive chores are hidden)"
    )
    isPrivate: Optional[bool] = Field(
        default=False,
        description="Private chore (visible only to creator)"
    )

    # Gamification
    points: Optional[int] = Field(
        None,
        ge=0,
        description="Points awarded for completion"
    )

    # Advanced Features
    subTasks: Optional[list[dict[str, Any]]] = Field(
        default_factory=list,
        description="Sub-tasks/checklist items"
    )
    thingTrigger: Optional[ThingTrigger] = Field(
        None,
        description="Thing state that triggers this chore (use with frequencyType='trigger')"
    )
    projectId: Optional[int] = Field(
        None,
        description="Project the chore belongs to"
    )

    # Completion Settings (NEW)
    completionWindow: Optional[int] = Field(
        None,
        ge=0,
        description="SECONDS before due time when chore can be completed early (e.g., 3600=1hr, 86400=1day)"
    )
    requireApproval: Optional[bool] = Field(
        default=False,
        description="Requires approval to mark complete"
    )

    # Advanced Scheduling (NEW)
    deadlineOffset: Optional[int] = Field(
        None,
        description="SECONDS after due time when deadline is reached (e.g., 3600=1hr grace, 86400=1day)"
    )

    @field_validator('name')
    @classmethod
    def validate_name(cls, v: str) -> str:
        """Validate and sanitize chore name."""
        if not v or not v.strip():
            raise ValueError('Chore name cannot be empty or whitespace only')
        # Remove control characters except newlines/tabs
        sanitized = ''.join(char for char in v if ord(char) >= 32 or char in '\n\r\t')
        return sanitized.strip()

    @field_validator('description')
    @classmethod
    def validate_description(cls, v: Optional[str]) -> Optional[str]:
        """Validate and sanitize description."""
        if v is None:
            return None
        # Remove control characters except newlines/tabs
        sanitized = ''.join(char for char in v if ord(char) >= 32 or char in '\n\r\t')
        return sanitized.strip() if sanitized.strip() else None

    @field_validator('priority', mode='before')
    @classmethod
    def default_priority(cls, v: Any) -> Any:
        """Send priority 0 (unset) when no priority is given."""
        return 0 if v is None else v

    @field_validator('nextDueDate')
    @classmethod
    def validate_due_date(cls, v: Optional[str]) -> Optional[str]:
        """Validate the due date and convert it to RFC3339 (YYYY-MM-DD becomes 12:00 UTC)."""
        if v is None:
            return v
        try:
            return normalize_datetime(v)
        except ValueError as e:
            raise ValueError(f"dueDate: {e}") from e

    @field_validator('frequencyType')
    @classmethod
    def validate_frequency_type(cls, v: Optional[str]) -> Optional[str]:
        """Validate frequency type."""
        if v is None:
            return "once"
        valid_types = [
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
            "no_repeat"
        ]
        if v.lower() not in valid_types:
            raise ValueError(
                f'frequencyType must be one of: {", ".join(valid_types)}'
            )
        # Donetick only accepts "interval"
        if v.lower() == "interval_based":
            return "interval"
        return v.lower()

    @field_validator('assignStrategy')
    @classmethod
    def validate_assign_strategy(cls, v: Optional[str]) -> Optional[str]:
        """Validate assignment strategy."""
        if v is None:
            return "least_completed"
        valid_strategies = [
            "least_completed",
            "least_assigned",
            "round_robin",
            "random",
            "keep_last_assigned",
            "random_except_last_assigned",
            "no_assignee"
        ]
        if v.lower() not in valid_strategies:
            raise ValueError(
                f'assignStrategy must be one of: {", ".join(valid_strategies)}'
            )
        return v.lower()

    @field_validator('notificationMetadata')
    @classmethod
    def validate_notification_metadata(cls, v: Optional[dict]) -> Optional[dict]:
        """Validate notification metadata structure and template limit."""
        if v is None:
            return None

        # Check template limit (Donetick API enforces MAX_TEMPLATES=5)
        templates = v.get('templates', [])
        if len(templates) > 5:
            raise ValueError(
                f'notificationMetadata.templates cannot exceed 5 items (got {len(templates)}). '
                'The Donetick API enforces a maximum of 5 notification templates per chore.'
            )

        # Validate template structure
        for i, template in enumerate(templates):
            if not isinstance(template, dict):
                raise ValueError(
                    f'Template {i} must be an object with "value" and "unit" fields'
                )
            if 'value' not in template or 'unit' not in template:
                raise ValueError(
                    f'Template {i} missing required fields: value (int), unit (str)'
                )
            if template['unit'] not in ('m', 'h', 'd'):
                raise ValueError(
                    f'Template {i} has invalid unit "{template["unit"]}". '
                    'Valid units: "m" (minutes), "h" (hours), "d" (days)'
                )

        return v

    @field_validator('completionWindow')
    @classmethod
    def validate_completion_window(cls, v: Optional[int]) -> Optional[int]:
        """Validate completion window is reasonable."""
        if v is not None and v < 0:
            raise ValueError('completionWindow must be non-negative (in seconds)')
        if v is not None and v > 31536000:  # 1 year in seconds
            raise ValueError('completionWindow cannot exceed 1 year (31536000 seconds)')
        return v

    @field_validator('deadlineOffset')
    @classmethod
    def validate_deadline_offset(cls, v: Optional[int]) -> Optional[int]:
        """Validate deadline offset is reasonable."""
        if v is not None and v > 31536000:  # 1 year in seconds
            raise ValueError('deadlineOffset cannot exceed 1 year (31536000 seconds)')
        return v

    @field_validator('frequencyMetadata')
    @classmethod
    def validate_frequency_metadata(cls, v: Optional[dict]) -> Optional[dict]:
        """Validate frequency metadata structure."""
        if not v:
            return v

        # Validate days are lowercase full names
        if 'days' in v:
            if not isinstance(v['days'], list):
                raise ValueError('frequencyMetadata.days must be an array')
            valid_days = ['monday', 'tuesday', 'wednesday', 'thursday', 'friday', 'saturday', 'sunday']
            for day in v['days']:
                if not isinstance(day, str) or day.lower() not in valid_days:
                    raise ValueError(
                        f'Invalid day "{day}" in frequencyMetadata.days. '
                        f'Must be lowercase full names: {", ".join(valid_days)}'
                    )

        # Validate weekPattern
        if 'weekPattern' in v:
            valid_patterns = ['every_week', 'week_of_month', 'week_of_quarter']
            if v['weekPattern'] not in valid_patterns:
                raise ValueError(
                    f'Invalid weekPattern "{v["weekPattern"]}". '
                    f'Valid values: {", ".join(valid_patterns)}'
                )

        # Validate time format (should be ISO with timezone)
        if 'time' in v and v['time']:
            time_str = v['time']
            if 'T' not in time_str:
                raise ValueError(
                    f'frequencyMetadata.time must be ISO format with timezone '
                    f'(e.g., "2025-11-10T14:00:00-05:00"), got: "{time_str}"'
                )

        # Validate timezone is IANA format
        if 'timezone' in v and v['timezone']:
            try:
                from zoneinfo import ZoneInfo
                ZoneInfo(v['timezone'])
            except Exception:
                raise ValueError(
                    f'Invalid timezone "{v["timezone"]}". '
                    'Use IANA timezone names like "America/New_York", "Europe/London", "UTC"'
                )

        return v


class ChoreUpdate(BaseModel):
    """Model for updating a chore."""

    model_config = ConfigDict(
        json_schema_extra={
            "example": {
                "name": "Take out recycling",
                "description": "Biweekly recycling collection",
                "nextDueDate": "2025-11-17",
                "priority": 2,
                "points": 10,
                "isPrivate": False,
            }
        }
    )

    # Basic fields
    name: Optional[str] = Field(None, min_length=1, max_length=200, description="Chore name")
    description: Optional[str] = Field(None, description="Chore description")
    nextDueDate: Optional[str] = Field(None, description="Next due date (ISO 8601)")

    # Scheduling
    frequencyType: Optional[str] = Field(None, description="Frequency type (once, daily, weekly, etc)")
    frequency: Optional[int] = Field(None, ge=1, description="Frequency value")
    frequencyMetadata: Optional[dict[str, Any]] = Field(None, description="Frequency metadata (days, time, timezone, etc)")
    isRolling: Optional[bool] = Field(None, description="Is rolling schedule")

    # Assignment
    assignStrategy: Optional[str] = Field(None, description="Assignment strategy")
    assignees: Optional[list[dict[str, int]]] = Field(None, description="List of assignees with userId (replaces all)")
    assignedTo: Optional[int] = Field(None, description="Current assignee (must be one of the assignees)")

    # Notifications
    notification: Optional[bool] = Field(None, description="Enable notifications")
    notificationMetadata: Optional[dict[str, Any]] = Field(None, description="Notification metadata (templates, nagging, predue)")

    # Status & Priority
    isActive: Optional[bool] = Field(None, description="Is chore active")
    priority: Optional[int] = Field(None, ge=0, le=4, description="Priority (0=unset, 1=lowest, 4=highest)")

    # Gamification & Labels
    points: Optional[int] = Field(None, ge=0, description="Points awarded for completion")
    labelsV2: Optional[list[dict[str, int]]] = Field(None, description="List of labels with id")

    # Privacy & Approval
    isPrivate: Optional[bool] = Field(None, description="Hide from other circle members")
    requireApproval: Optional[bool] = Field(None, description="Requires approval to mark complete")

    # Completion & Deadline Settings
    completionWindow: Optional[int] = Field(None, ge=0, description="SECONDS before due time for early completion")
    deadlineOffset: Optional[int] = Field(None, ge=0, description="SECONDS after due time for grace period")

    # Subtasks (can be updated)
    subTasks: Optional[list[dict[str, Any]]] = Field(None, description="List of subtasks with name and orderId")

    # Thing trigger and project
    thingTrigger: Optional[ThingTrigger] = Field(None, description="Thing state that triggers this chore")
    projectId: Optional[int] = Field(None, description="Project the chore belongs to")

    @field_validator('nextDueDate')
    @classmethod
    def validate_next_due_date(cls, v: Optional[str]) -> Optional[str]:
        """Convert the due date to RFC3339 (YYYY-MM-DD becomes 12:00 UTC)."""
        if v is None:
            return v
        return normalize_datetime(v)


class Chore(BaseModel):
    """Complete chore model as returned by the API."""

    model_config = ConfigDict(populate_by_name=True)

    id: int = Field(..., description="Chore ID")
    name: str = Field(..., description="Chore name")
    description: Optional[str] = Field(None, description="Chore description")
    frequencyType: str = Field(..., description="Frequency type (once, daily, weekly, etc)")
    frequency: int = Field(..., description="Frequency value")
    frequencyMetadata: Optional[dict[str, Any]] = Field(None, description="Frequency metadata")
    nextDueDate: Optional[str] = Field(None, description="Next due date (ISO 8601)")
    isRolling: bool = Field(default=False, description="Is rolling schedule")
    assignedTo: Optional[int] = Field(None, description="User ID of assigned user")
    assignees: list[Assignee] = Field(default_factory=list, description="List of assignees")
    assignStrategy: str = Field(
        default="least_completed",
        description="Assignment strategy",
    )
    isActive: bool = Field(default=True, description="Is chore active")
    notification: bool = Field(default=False, description="Enable notifications")
    notificationMetadata: Optional[NotificationMetadata] = Field(
        None,
        description="Notification settings",
    )
    labels: Optional[list[str]] = Field(None, description="Legacy labels")
    labelsV2: list[Label] = Field(default_factory=list, description="Chore labels")
    circleId: int = Field(..., description="Circle/household ID")
    createdAt: str = Field(..., description="Creation timestamp (ISO 8601)")
    updatedAt: str = Field(..., description="Last update timestamp (ISO 8601)")
    createdBy: int = Field(..., description="Creator user ID")
    updatedBy: Optional[int] = Field(None, description="Last updater user ID")
    status: Optional[Any] = Field(None, description="Chore status (can be string or int)")
    priority: Optional[int] = Field(None, ge=0, le=4, description="Priority (0=unset, 1=lowest, 4=highest)")
    isPrivate: bool = Field(default=False, description="Is private chore")
    points: Optional[int] = Field(None, description="Points awarded")
    subTasks: list[Any] = Field(default_factory=list, description="Sub-tasks")
    thingChore: Optional[dict[str, Any]] = Field(None, description="Thing trigger linked to this chore")
    completionWindow: Optional[int] = Field(None, description="Days before/after due date for completion window")
    requireApproval: Optional[bool] = Field(None, description="Requires approval to mark complete")
    deadlineOffset: Optional[int] = Field(None, description="Offset in days for deadline calculation")
    projectId: Optional[int] = Field(None, description="Project the chore belongs to")
    syncVersion: Optional[int] = Field(None, description="Server sync version")


class CircleMember(BaseModel):
    """Circle member model."""

    model_config = ConfigDict(populate_by_name=True)

    id: int = Field(..., description="Circle member ID")
    userId: int = Field(..., description="User ID")
    circleId: int = Field(..., description="Circle ID")
    role: str = Field(..., description="Member role (admin, member)")
    isActive: bool = Field(..., description="Whether member is active")
    username: str = Field(..., description="Username")
    displayName: Optional[str] = Field(None, description="Display name")
    image: Optional[str] = Field(None, description="Profile image URL")
    points: Optional[int] = Field(0, description="Member points")
    pointsRedeemed: Optional[int] = Field(0, description="Points redeemed")


class User(BaseModel):
    """User as returned by GET /api/v1/users/.

    Donetick's user objects carry no circle role or points; those come from
    GET /api/v1/circles/members (CircleMember).
    """

    model_config = ConfigDict(populate_by_name=True)

    id: int = Field(..., description="User ID")
    username: str = Field("", description="Username (can be empty)")
    displayName: Optional[str] = Field(None, description="Display name")
    email: Optional[str] = Field(None, description="Email address")
    circleId: Optional[int] = Field(
        None, validation_alias=AliasChoices("circleID", "circleId"), description="Circle ID"
    )
    image: Optional[str] = Field(None, description="Profile image URL")
    timezone: Optional[str] = Field(None, description="User timezone")
    userType: Optional[int] = Field(None, description="0 = regular account, 1 = child account")
    disabled: bool = Field(False, description="Whether the account is disabled")


# Donetick enums (internal/notifier/model, internal/user/model)
NOTIFICATION_PLATFORM_NAMES = {
    0: "none",
    1: "Telegram",
    2: "Pushover",
    3: "Webhook",
    4: "Discord",
    5: "Push (mobile app)",
}
AUTH_PROVIDER_NAMES = {0: "Donetick", 1: "OAuth2", 2: "Google", 3: "Apple"}


class UserProfile(BaseModel):
    """Current user as returned by GET /api/v1/users/profile.

    Points and roles are not part of the profile (see CircleMember), storage usage
    comes from GET /api/v1/users/storage.
    """

    model_config = ConfigDict(populate_by_name=True)

    id: int = Field(..., description="User ID")
    username: str = Field("", description="Username")
    displayName: Optional[str] = Field(None, description="Display name")
    email: Optional[str] = Field(None, description="Email address")
    provider: Optional[int] = Field(None, description="Login provider (0 = Donetick, 1 = OAuth2, 2 = Google, 3 = Apple)")
    circleId: Optional[int] = Field(
        None, validation_alias=AliasChoices("circleID", "circleId"), description="Circle ID"
    )
    image: Optional[str] = Field(None, description="Profile image URL")
    timezone: Optional[str] = Field(None, description="User timezone")
    userType: Optional[int] = Field(None, description="0 = regular account, 1 = child account")
    mfaEnabled: bool = Field(False, description="Multi-factor authentication enabled")
    disabled: bool = Field(False, description="Whether the account is disabled")
    subscription: Optional[str] = Field(None, description="Subscription status (Donetick cloud / Plus)")
    expiration: Optional[str] = Field(None, description="Subscription expiration")
    createdAt: Optional[str] = Field(
        None, validation_alias=AliasChoices("created_at", "createdAt"), description="Account creation timestamp"
    )
    updatedAt: Optional[str] = Field(
        None, validation_alias=AliasChoices("updated_at", "updatedAt"), description="Last update timestamp"
    )
    notificationTarget: Optional[dict[str, Any]] = Field(
        None,
        validation_alias=AliasChoices("notification_target", "notificationTarget"),
        description="Notification target ({type, target_id})",
    )
    webhookURL: Optional[str] = Field(None, description="Circle webhook URL")


class ChoreHistory(BaseModel):
    """Model for chore completion history entry."""

    model_config = ConfigDict(populate_by_name=True)

    id: int = Field(..., description="History record ID")
    choreId: int = Field(..., description="Associated chore ID")
    performedAt: Optional[str] = Field(None, description="When the chore was performed (ISO 8601 datetime)")
    completedBy: int = Field(..., description="User ID who completed the chore")
    assignedTo: Optional[int] = Field(None, description="User ID the chore was assigned to")
    # Donetick returns "notes"; "note" is accepted for backwards compatibility
    note: Optional[str] = Field(
        None,
        validation_alias=AliasChoices("notes", "note"),
        max_length=5000,
        description="Completion note",
    )
    dueDate: Optional[str] = Field(None, description="Original due date (ISO 8601)")
    status: str = Field(
        default="completed",
        description=(
            "Status: started, completed, skipped, pending_approval, rejected, missed, rescheduled"
        ),
    )
    points: Optional[int] = Field(None, ge=0, description="Points awarded for completion")
    duration: Optional[int] = Field(None, ge=0, description="Time to completion in seconds")

    @field_validator('performedAt')
    @classmethod
    def validate_performed_at(cls, v: Optional[str]) -> Optional[str]:
        """Validate performedAt is in RFC3339 or ISO 8601 format."""
        if v is None:
            return v
        try:
            datetime.fromisoformat(v.replace('Z', '+00:00'))
            return v
        except ValueError:
            raise ValueError(
                'performedAt must be in RFC3339 format (e.g., 2025-11-10T14:30:00Z) '
                'or ISO 8601 format'
            )

    @field_validator('dueDate')
    @classmethod
    def validate_history_due_date(cls, v: Optional[str]) -> Optional[str]:
        """Validate dueDate is in RFC3339 or ISO 8601 format."""
        if v is None:
            return v

        try:
            datetime.fromisoformat(v.replace('Z', '+00:00'))
            return v
        except ValueError:
            raise ValueError(
                'dueDate must be in RFC3339 format (e.g., 2025-11-10T00:00:00Z) '
                'or ISO 8601 format'
            )

    @field_validator('status', mode='before')
    @classmethod
    def validate_status(cls, v: Any) -> str:
        """Validate history status; Donetick returns it as an integer."""
        if isinstance(v, int) and not isinstance(v, bool):
            if v not in HISTORY_STATUS_NAMES:
                raise ValueError(f'Unknown history status: {v}')
            return HISTORY_STATUS_NAMES[v]
        valid_statuses = list(HISTORY_STATUS_NAMES.values())
        if not isinstance(v, str) or v.lower() not in valid_statuses:
            raise ValueError(
                f'status must be one of: {", ".join(valid_statuses)}'
            )
        return v.lower()


class ChoreDetail(BaseModel):
    """Extended chore model with statistics and completion history."""

    model_config = ConfigDict(populate_by_name=True)

    # All base chore fields (Donetick's detail response only includes a subset)
    id: int = Field(..., description="Chore ID")
    name: str = Field(..., description="Chore name")
    description: Optional[str] = Field(None, description="Chore description")
    frequencyType: str = Field(..., description="Frequency type (once, daily, weekly, etc)")
    frequency: Optional[int] = Field(None, description="Frequency value")
    frequencyMetadata: Optional[dict[str, Any]] = Field(None, description="Frequency metadata")
    nextDueDate: Optional[str] = Field(None, description="Next due date (ISO 8601)")
    isRolling: bool = Field(default=False, description="Is rolling schedule")
    assignedTo: Optional[int] = Field(None, description="User ID of assigned user")
    assignees: list[Assignee] = Field(default_factory=list, description="List of assignees")
    assignStrategy: str = Field(
        default="least_completed",
        description="Assignment strategy",
    )
    isActive: bool = Field(default=True, description="Is chore active")
    notification: bool = Field(default=False, description="Enable notifications")
    notificationMetadata: Optional[NotificationMetadata] = Field(
        None,
        description="Notification settings",
    )
    labels: Optional[list[str]] = Field(None, description="Legacy labels")
    labelsV2: list[Label] = Field(default_factory=list, description="Chore labels")
    circleId: Optional[int] = Field(None, description="Circle/household ID")
    createdAt: Optional[str] = Field(None, description="Creation timestamp (ISO 8601)")
    updatedAt: Optional[str] = Field(None, description="Last update timestamp (ISO 8601)")
    createdBy: int = Field(..., description="Creator user ID")
    updatedBy: Optional[int] = Field(None, description="Last updater user ID")
    status: Optional[Any] = Field(None, description="Chore status (can be string or int)")
    priority: Optional[int] = Field(None, ge=0, le=4, description="Priority (0=unset, 1=lowest, 4=highest)")
    isPrivate: bool = Field(default=False, description="Is private chore")
    points: Optional[int] = Field(None, description="Points awarded")
    subTasks: Optional[list[Any]] = Field(default_factory=list, description="Sub-tasks")
    thingChore: Optional[dict[str, Any]] = Field(None, description="Thing trigger linked to this chore")
    completionWindow: Optional[int] = Field(None, description="Days before/after due date for completion window")
    requireApproval: Optional[bool] = Field(None, description="Requires approval to mark complete")
    deadlineOffset: Optional[int] = Field(None, description="Offset in days for deadline calculation")
    projectId: Optional[int] = Field(None, description="Project the chore belongs to")
    notes: Optional[str] = Field(None, description="Notes of the most recent completion")
    duration: Optional[int] = Field(None, description="Total time tracked on the chore in seconds")

    # Analytics and statistics fields
    totalCompletedCount: Optional[int] = Field(
        None,
        ge=0,
        description="Total number of times this chore has been completed"
    )
    lastCompletedDate: Optional[str] = Field(
        None,
        description="Most recent completion timestamp (ISO 8601)"
    )
    lastCompletedBy: Optional[int] = Field(
        None,
        description="User ID who completed the chore most recently"
    )
    averageDuration: Optional[float] = Field(
        None,
        ge=0,
        description="Average time to completion in seconds (from due date to completed date)"
    )
    completionHistory: Optional[list[ChoreHistory]] = Field(
        default_factory=list,
        description="List of completion history records for this chore"
    )

    @field_validator('lastCompletedDate')
    @classmethod
    def validate_last_completed_date(cls, v: Optional[str]) -> Optional[str]:
        """Validate lastCompletedDate is in RFC3339 or ISO 8601 format."""
        if v is None:
            return v

        try:
            datetime.fromisoformat(v.replace('Z', '+00:00'))
            return v
        except ValueError:
            raise ValueError(
                'lastCompletedDate must be in RFC3339 format (e.g., 2025-11-10T14:30:00Z) '
                'or ISO 8601 format'
            )


class Project(BaseModel):
    """Project grouping chores within a circle."""

    id: int = Field(..., description="Project ID")
    name: str = Field(..., description="Project name")
    description: Optional[str] = Field(None, description="Project description")
    color: Optional[str] = Field(None, description="Project color")
    icon: Optional[str] = Field(None, description="Project icon")
    circleId: Optional[int] = Field(None, description="Circle ID")
    isDefault: bool = Field(False, description="Whether this is the circle's default project")
    createdAt: Optional[str] = Field(None, description="Creation timestamp (ISO 8601)")
    updatedAt: Optional[str] = Field(None, description="Last update timestamp (ISO 8601)")


class ThingChore(BaseModel):
    """Link between a thing and a chore it triggers."""

    thingId: int = Field(..., description="Thing ID")
    choreId: int = Field(..., description="Chore ID")
    triggerState: str = Field(..., description="State value the condition is compared against")
    condition: Optional[str] = Field(None, description="Comparison: eq (default), neq, gt, lt, gte, lte")


class Thing(BaseModel):
    """Thing (e.g. a sensor or counter) whose state can trigger chores."""

    model_config = ConfigDict(populate_by_name=True)

    id: int = Field(..., description="Thing ID")
    userID: Optional[int] = Field(None, description="Owner user ID (things are private to their owner)")
    circleId: Optional[int] = Field(None, description="Circle ID")
    name: str = Field(..., description="Thing name")
    state: str = Field("", description="Current state as string")
    type: str = Field(..., description="Thing type: text, number or boolean")
    thingChores: Optional[list[ThingChore]] = Field(
        default_factory=list,
        description="Chores triggered by this thing (not included when listing things)",
    )
    createdAt: Optional[str] = Field(None, description="Creation timestamp (ISO 8601)")
    updatedAt: Optional[str] = Field(None, description="Last update timestamp (ISO 8601)")


class ThingHistory(BaseModel):
    """Historic state of a thing."""

    id: int = Field(..., description="History record ID")
    thingId: int = Field(..., description="Thing ID")
    state: str = Field(..., description="State value")
    createdAt: Optional[str] = Field(None, description="When the state was recorded (ISO 8601)")
    updatedAt: Optional[str] = Field(None, description="Last update timestamp (ISO 8601)")


class ThingCreate(BaseModel):
    """Model for creating or updating a thing."""

    name: str = Field(..., min_length=1, max_length=200, description="Thing name")
    type: str = Field(..., description="Thing type: text, number or boolean")
    state: Optional[str] = Field(None, description="Initial state (validated against the type)")

    @field_validator("type")
    @classmethod
    def validate_type(cls, v: str) -> str:
        """Validate the thing type."""
        if v not in THING_TYPES:
            raise ValueError(f'Invalid thing type "{v}". Valid types: {", ".join(THING_TYPES)}')
        return v

    @field_validator("state", mode="before")
    @classmethod
    def validate_state(cls, v: Any, info) -> Optional[str]:
        """Validate the state against the thing type."""
        thing_type = info.data.get("type")
        if v is None or thing_type is None:
            return v
        return normalize_thing_state(thing_type, v)


class APIError(BaseModel):
    """API error response model."""

    error: str = Field(..., description="Error message")
    code: Optional[int] = Field(None, description="Error code")
    details: Optional[dict[str, Any]] = Field(None, description="Additional error details")
