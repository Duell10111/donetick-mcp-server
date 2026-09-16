"""Donetick API client with rate limiting and retry logic."""

import asyncio
import json as json_lib
import logging
import random
import re
import time
from datetime import UTC, datetime, timedelta
from typing import Any, Optional

import httpx

from .config import config
from .models import (
    NUMERIC_TRIGGER_CONDITIONS,
    Chore,
    ChoreCreate,
    ChoreDetail,
    ChoreHistory,
    ChoreUpdate,
    CircleMember,
    Label,
    Thing,
    ThingCreate,
    ThingHistory,
    ThingTrigger,
    User,
    UserProfile,
    normalize_datetime,
    normalize_thing_state,
)

logger = logging.getLogger(__name__)

# Refresh the access token this long before it expires
TOKEN_REFRESH_MARGIN = timedelta(seconds=60)

# Server-generated metadata fields that should be removed before update requests
# These fields are added by the API in responses but should not be sent back in updates
FIELDS_TO_REMOVE = [
    "createdAt",
    "updatedAt",
    "createdBy",
    "updatedBy",
    "circleId",
    "status",
]


class DonetickAuthError(Exception):
    """Authentication with Donetick failed in a way that retrying cannot fix."""


def _parse_token_expiry(value: Any) -> Optional[datetime]:
    """Parse a token expiry timestamp from a Donetick auth response.

    Go encodes time.Time as RFC3339 with up to 9 fractional digits, while
    Python only supports 6. The Go zero time (year 1) means "not set".
    """
    if not isinstance(value, str) or not value:
        return None
    value = re.sub(r"(\.\d{6})\d+", r"\1", value).replace("Z", "+00:00")
    try:
        expiry = datetime.fromisoformat(value)
    except ValueError:
        logger.debug(f"Could not parse token expiry: {value}")
        return None
    if expiry.tzinfo is None:
        expiry = expiry.replace(tzinfo=UTC)
    if expiry.year <= 1:
        return None
    return expiry


def evaluate_thing_trigger(trigger_state: str, condition: Optional[str], new_state: str) -> bool:
    """Check whether a thing state matches a chore trigger (mirrors Donetick's EvaluateThingChore)."""
    if not condition or condition == "eq":
        return new_state == trigger_state
    if condition == "neq":
        return new_state != trigger_state
    try:
        new_value = int(new_state)
        target_value = int(trigger_state)
    except ValueError:
        return False
    comparisons = {
        "gt": new_value > target_value,
        "lt": new_value < target_value,
        "gte": new_value >= target_value,
        "lte": new_value <= target_value,
    }
    return comparisons.get(condition, new_state == trigger_state)


class TokenBucket:
    """Token bucket rate limiter."""

    def __init__(self, rate: float, capacity: int):
        """
        Initialize token bucket.

        Args:
            rate: Tokens per second refill rate
            capacity: Maximum token capacity
        """
        self.rate = rate
        self.capacity = capacity
        self.tokens = float(capacity)
        self.last_update = time.time()
        self.lock = asyncio.Lock()

    async def acquire(self, tokens: int = 1):
        """
        Acquire tokens, waiting if necessary.

        Args:
            tokens: Number of tokens to acquire
        """
        async with self.lock:
            while True:
                now = time.time()
                elapsed = now - self.last_update

                # Refill tokens based on elapsed time
                self.tokens = min(self.capacity, self.tokens + elapsed * self.rate)
                self.last_update = now

                if self.tokens >= tokens:
                    self.tokens -= tokens
                    return

                # Wait until enough tokens available
                wait_time = (tokens - self.tokens) / self.rate
                await asyncio.sleep(wait_time)


class DonetickClient:
    """Async client for Donetick full API (api/v1)."""

    def __init__(
        self,
        base_url: Optional[str] = None,
        username: Optional[str] = None,
        password: Optional[str] = None,
        rate_limit_per_second: Optional[float] = None,
        rate_limit_burst: Optional[int] = None,
    ):
        """
        Initialize Donetick API client.

        Args:
            base_url: Donetick instance URL (defaults to config)
            username: Donetick username (defaults to config)
            password: Donetick password (defaults to config)
            rate_limit_per_second: Rate limit in requests per second (defaults to config)
            rate_limit_burst: Maximum burst size (defaults to config)
        """
        base_url = base_url or config.donetick_base_url
        if not base_url:
            raise ValueError("Donetick base URL is not configured (DONETICK_BASE_URL)")
        self.base_url = base_url.rstrip("/")
        self.username = username or config.donetick_username
        self.password = password or config.donetick_password
        self._jwt_token: Optional[str] = None
        self._refresh_token: Optional[str] = None
        self._token_expiry: Optional[datetime] = None
        # Serializes login/refresh: Donetick revokes the whole session family
        # when a refresh token is reused, so concurrent refreshes must not happen
        self._auth_lock = asyncio.Lock()
        self.rate_limiter = TokenBucket(
            rate=rate_limit_per_second or config.rate_limit_per_second,
            capacity=rate_limit_burst or config.rate_limit_burst,
        )

        # Configure httpx client with connection pooling and timeouts
        self.client = httpx.AsyncClient(
            headers={
                "Content-Type": "application/json",
                "Accept": "application/json",
            },
            limits=httpx.Limits(
                max_connections=100,
                max_keepalive_connections=50,
                keepalive_expiry=30.0,
            ),
            timeout=httpx.Timeout(
                connect=5.0,
                read=30.0,
                write=5.0,
                pool=2.0,
            ),
            verify=True,  # Enforce certificate verification
        )

    async def __aenter__(self):
        """Async context manager entry."""
        return self

    async def __aexit__(self, exc_type, exc_val, exc_tb):
        """Async context manager exit with cleanup."""
        await self.close()

    async def close(self):
        """Close the HTTP client and cleanup resources."""
        if self.client:
            await self.client.aclose()

    async def login(self):
        """
        Authenticate with Donetick API using username and password.

        Makes a POST request to /api/v1/auth/login and stores the returned
        access token (and refresh token, if the instance provides one).

        Raises:
            DonetickAuthError: On invalid credentials, MFA-enabled accounts,
                SSO-only instances or malformed login responses
            httpx.HTTPStatusError: On other HTTP errors (e.g. 429, 5xx)
        """
        url = f"{self.base_url}/api/v1/auth/login"

        logger.debug("Authenticating with Donetick API")

        response = await self.client.post(
            url,
            json={
                "username": self.username,
                "password": self.password,
            },
        )

        if response.status_code == 401:
            logger.error("Authentication failed: invalid username or password")
            raise DonetickAuthError(
                "Login failed: invalid username or password. "
                "Check DONETICK_USERNAME and DONETICK_PASSWORD."
            )
        if response.status_code == 403:
            logger.error("Authentication failed: password login is disabled")
            raise DonetickAuthError(
                "Login failed: password authentication is disabled on this Donetick "
                "instance (SSO-only). Username/password login is required by this server."
            )
        response.raise_for_status()

        try:
            data = response.json()
        except json_lib.JSONDecodeError as e:
            logger.error("Invalid JSON response from login endpoint")
            raise DonetickAuthError(f"Invalid JSON response from login endpoint: {e}") from e

        self._store_tokens(data, source="login")
        logger.info("Successfully authenticated with Donetick API")

    async def refresh_access_token(self) -> bool:
        """
        Get a new access token using the stored refresh token.

        Returns:
            True if the tokens were refreshed, False if no refresh token is
            available or Donetick rejected it (caller should log in again)
        """
        if not self._refresh_token:
            return False

        url = f"{self.base_url}/api/v1/auth/refresh"
        logger.debug("Refreshing Donetick access token")

        try:
            response = await self.client.post(
                url,
                json={"refresh_token": self._refresh_token},
            )
        except httpx.HTTPError as e:
            logger.warning(f"Token refresh request failed: {e}")
            return False

        if response.status_code != 200:
            logger.warning(f"Token refresh rejected ({response.status_code}), logging in again")
            self._refresh_token = None
            return False

        try:
            self._store_tokens(response.json(), source="refresh")
        except (json_lib.JSONDecodeError, DonetickAuthError) as e:
            logger.warning(f"Invalid token refresh response: {e}")
            self._refresh_token = None
            return False

        logger.info("Refreshed Donetick access token")
        return True

    def _store_tokens(self, data: Any, source: str):
        """Store tokens from a login or refresh response and set the auth header."""
        if not isinstance(data, dict):
            raise DonetickAuthError(f"Invalid {source} response: expected a JSON object")

        if data.get("mfaRequired"):
            logger.error("Authentication failed: account has MFA enabled")
            raise DonetickAuthError(
                "Login failed: this Donetick account has multi-factor authentication enabled, "
                "which the MCP server cannot complete. Use an account without MFA."
            )

        # Current Donetick returns access_token/refresh_token; "token"/"expire"
        # are legacy fields kept for older instances
        token = data.get("access_token") or data.get("token")
        if not token:
            logger.error(f"{source.capitalize()} response missing access token")
            raise DonetickAuthError(f"Invalid {source} response: missing token")

        self._jwt_token = token
        self._refresh_token = data.get("refresh_token") or None
        self._token_expiry = _parse_token_expiry(
            data.get("access_token_expiry") or data.get("expire")
        )

        # The refresh token is also set as a cookie, which Donetick prefers over the
        # request body. Drop it so the explicitly stored refresh token is used.
        self.client.cookies.clear()

        # Update client headers with Bearer token
        self.client.headers["Authorization"] = f"Bearer {self._jwt_token}"

    def _token_needs_refresh(self) -> bool:
        """Check whether there is no token or it is about to expire."""
        if self._jwt_token is None:
            return True
        if self._token_expiry is None:
            return False
        return datetime.now(UTC) >= self._token_expiry - TOKEN_REFRESH_MARGIN

    async def _ensure_authenticated(self):
        """Log in or refresh the access token if needed before a request."""
        if not self._token_needs_refresh():
            return

        async with self._auth_lock:
            # Another request may have refreshed the token while we waited
            if not self._token_needs_refresh():
                return
            if self._jwt_token is not None and await self.refresh_access_token():
                return
            await self.login()

    async def _reauthenticate(self, rejected_token: Optional[str]):
        """Get a new access token after the API rejected ``rejected_token``."""
        async with self._auth_lock:
            # Another request may already have replaced the rejected token
            if self._jwt_token is not None and self._jwt_token != rejected_token:
                return
            if await self.refresh_access_token():
                return
            await self.login()

    async def _request(
        self,
        method: str,
        path: str,
        max_retries: int = 3,
        **kwargs: Any,
    ) -> dict[str, Any] | list[Any]:
        """
        Make HTTP request with rate limiting and retry logic.

        Args:
            method: HTTP method (GET, POST, PUT, DELETE)
            path: API endpoint path
            max_retries: Maximum number of retry attempts
            **kwargs: Additional arguments for httpx request

        Returns:
            JSON response data

        Raises:
            httpx.HTTPError: On HTTP errors after all retries exhausted
        """
        # Log in lazily and refresh the token shortly before it expires
        await self._ensure_authenticated()

        url = f"{self.base_url}{path}"
        base_delay = 1.0
        auth_retry_attempted = False

        for attempt in range(max_retries):
            try:
                # Wait for rate limit
                await self.rate_limiter.acquire()

                # Make request
                logger.debug(f"Request {method} {url} (attempt {attempt + 1}/{max_retries})")
                token_used = self._jwt_token
                response = await self.client.request(method, url, **kwargs)

                # Handle rate limit responses
                if response.status_code == 429:
                    retry_after = response.headers.get("Retry-After", "60")
                    wait_time = float(retry_after)
                    logger.warning(f"Rate limited, waiting {wait_time}s")
                    await asyncio.sleep(wait_time)
                    continue

                # Handle authentication errors (401 Unauthorized)
                if response.status_code == 401:
                    if not auth_retry_attempted:
                        logger.warning("Authentication failed (401), refreshing JWT token")
                        auth_retry_attempted = True
                        await self._reauthenticate(rejected_token=token_used)
                        continue
                    else:
                        logger.error("Authentication failed after token refresh")
                        raise httpx.HTTPStatusError(
                            "Authentication failed: Invalid credentials or expired session",
                            request=response.request,
                            response=response
                        )

                # Raise for other HTTP errors
                response.raise_for_status()

                # Parse JSON response with error handling
                try:
                    return response.json()
                except json_lib.JSONDecodeError as e:
                    logger.error(f"Invalid JSON response from {url}: {response.text[:200]}")
                    raise ValueError(f"Invalid JSON response from API: {e}") from e

            except httpx.TimeoutException as e:
                if attempt == max_retries - 1:
                    logger.error(f"Request timeout after {max_retries} attempts: {e}")
                    raise

                # Exponential backoff with jitter
                delay = min(base_delay * (2**attempt), 60.0)
                jitter = delay * random.uniform(-0.25, 0.25)
                wait_time = delay + jitter

                logger.warning(f"Timeout on attempt {attempt + 1}, retrying in {wait_time:.2f}s")
                await asyncio.sleep(wait_time)

            except httpx.HTTPStatusError as e:
                # Don't retry client errors (4xx); 429 and the first 401 are handled above
                if 400 <= e.response.status_code < 500:
                    logger.error(f"Client error: {e.response.status_code} - {e.response.text}")
                    raise

                # Retry server errors (5xx)
                if attempt == max_retries - 1:
                    logger.error(f"Server error after {max_retries} attempts: {e}")
                    raise

                delay = min(base_delay * (2**attempt), 60.0)
                jitter = delay * random.uniform(-0.25, 0.25)
                wait_time = delay + jitter

                logger.warning(
                    f"Server error on attempt {attempt + 1}, retrying in {wait_time:.2f}s"
                )
                await asyncio.sleep(wait_time)

        raise Exception(f"Failed after {max_retries} retries")

    async def list_chores(
        self,
        filter_active: Optional[bool] = None,
        assigned_to_user_id: Optional[int] = None,
    ) -> list[Chore]:
        """
        List all chores with optional filtering.

        Args:
            filter_active: Filter by active status (None = all)
            assigned_to_user_id: Filter by assigned user ID (None = all)

        Returns:
            List of Chore objects
        """
        logger.info("Fetching chores list")
        data = await self._request("GET", "/api/v1/chores/")

        # API returns {'res': [chores]} format
        chores_list = data.get('res', []) if isinstance(data, dict) else data

        # Parse response into Chore objects
        chores = [Chore(**chore_data) for chore_data in chores_list]

        # Apply filters
        if filter_active is not None:
            chores = [c for c in chores if c.isActive == filter_active]
            logger.debug(f"Filtered to active={filter_active}: {len(chores)} chores")

        if assigned_to_user_id is not None:
            chores = [c for c in chores if c.assignedTo == assigned_to_user_id]
            logger.debug(f"Filtered to user {assigned_to_user_id}: {len(chores)} chores")

        logger.info(f"Retrieved {len(chores)} chores")
        return chores

    async def get_chore(self, chore_id: int) -> Optional[Chore]:
        """
        Get a specific chore by ID.

        Uses GET /api/v1/chores/{id} endpoint which includes sub-tasks.

        Args:
            chore_id: Chore ID

        Returns:
            Chore object if found (including sub-tasks), None otherwise
        """
        logger.info(f"Fetching chore {chore_id} (includes sub-tasks)")

        try:
            data = await self._request("GET", f"/api/v1/chores/{chore_id}")

            # API returns {'res': chore} format, unwrap it
            chore_data = data.get('res', data) if isinstance(data, dict) else data
            chore = Chore(**chore_data)

            logger.info(f"Found chore {chore_id}: {chore.name}")
            return chore

        except httpx.HTTPStatusError as e:
            if e.response.status_code == 404:
                logger.warning(f"Chore {chore_id} not found")
                return None
            raise

    async def create_chore(self, chore: ChoreCreate) -> Chore:
        """
        Create a new chore.

        Args:
            chore: ChoreCreate object with chore details

        Returns:
            Created Chore object
        """
        logger.info(f"Creating chore: {chore.name}")
        data = await self._request("POST", "/api/v1/chores/", json=chore.model_dump(exclude_none=True))

        # API returns {'res': chore_id} on successful creation
        chore_id = data.get('res') if isinstance(data, dict) else None

        if chore_id is None:
            raise ValueError("Failed to get chore ID from create response")

        logger.info(f"Created chore with ID: {chore_id}")

        # Fetch the created chore to return full details
        created_chore = await self.get_chore(chore_id)

        if created_chore is None:
            raise ValueError(f"Failed to fetch created chore {chore_id}")

        logger.info(f"Fetched created chore {created_chore.id}: {created_chore.name}")
        return created_chore

    def _chore_update_payload(self, chore: Chore) -> dict[str, Any]:
        """Build a full chore payload for PUT /api/v1/chores/ from a fetched chore."""
        chore_dict = chore.model_dump(exclude_none=True)

        # Remove server-generated metadata fields
        for field in FIELDS_TO_REMOVE:
            chore_dict.pop(field, None)

        # Clean up labels - remove created_by if null
        if "labelsV2" in chore_dict and chore_dict["labelsV2"]:
            for label in chore_dict["labelsV2"]:
                if "created_by" in label and label["created_by"] is None:
                    label.pop("created_by")

        # Donetick removes a chore's thing link on every edit and only re-creates it from
        # "thingTrigger" in the request. Chores are read with "thingChore", so convert it.
        thing_chore = chore_dict.pop("thingChore", None)
        if thing_chore and thing_chore.get("thingId"):
            chore_dict["thingTrigger"] = {
                "thingID": thing_chore["thingId"],
                "triggerState": thing_chore.get("triggerState", ""),
                "condition": thing_chore.get("condition") or "eq",
            }

        return chore_dict

    async def _put_chore(self, chore_id: int, chore_dict: dict[str, Any]) -> Chore:
        """Send a full chore to PUT /api/v1/chores/ and return the updated chore."""
        chore_dict["id"] = chore_id

        # Log the final payload for debugging
        logger.debug(f"Sending update payload: {json_lib.dumps(chore_dict, indent=2)}")

        data = await self._request(
            "PUT",
            "/api/v1/chores/",
            json=chore_dict,
        )

        # API returns {"message": "Chore updated successfully"} instead of the chore object
        # Fetch the updated chore to return it
        if "message" in data:
            logger.info(f"Update API response: {data.get('message')}")
            updated_chore = await self.get_chore(chore_id)
            if updated_chore is None:
                raise ValueError(f"Chore {chore_id} was updated but could not be retrieved")
        else:
            updated_chore = Chore(**data)
        return updated_chore

    async def update_chore(
        self,
        chore_id: int,
        update: ChoreUpdate,
        remove_thing_trigger: bool = False,
    ) -> Chore:
        """
        Update an existing chore.

        Args:
            chore_id: Chore ID to update
            update: ChoreUpdate object with fields to update
            remove_thing_trigger: Remove the chore's thing trigger

        Returns:
            Updated Chore object

        Note:
            The Donetick API requires the full chore object for updates.
            This method fetches the current chore, applies the updates,
            and sends the complete object back to PUT /api/v1/chores/.
            If only the due date changes, the dedicated due date endpoint is used,
            which also records the change as "rescheduled" in the chore history.
        """
        logger.info(f"Updating chore {chore_id}")

        # Fetch current chore to get full object
        current_chore = await self.get_chore(chore_id)
        if current_chore is None:
            raise ValueError(f"Chore {chore_id} not found")

        update_fields = update.model_dump(exclude_none=True)

        if set(update_fields) == {"nextDueDate"} and not remove_thing_trigger:
            return await self.update_chore_due_date(
                chore_id, update_fields["nextDueDate"], current_chore=current_chore
            )

        # Convert to dict and apply updates
        chore_dict = self._chore_update_payload(current_chore)
        chore_dict.update(update_fields)
        if remove_thing_trigger:
            chore_dict.pop("thingTrigger", None)

        # IMPORTANT API CONSTRAINT: If assignedTo is set, it MUST be in the assignees array
        # The API validates this and returns 400 "Assigned to not found in assignees" if violated
        assigned_to = chore_dict.get("assignedTo")
        assignees = chore_dict.get("assignees", [])

        if assigned_to is not None:
            # Ensure assignees is a list
            if not isinstance(assignees, list):
                assignees = []
                chore_dict["assignees"] = assignees

            # Add assignedTo to assignees if not present (assignees are {"userId": id} objects)
            assignee_ids = {a.get("userId") for a in assignees if isinstance(a, dict)}
            if assigned_to not in assignee_ids:
                logger.info(f"Adding assignedTo ({assigned_to}) to assignees array to satisfy API constraint")
                assignees.append({"userId": assigned_to})
                chore_dict["assignees"] = assignees

        # Validate and fix frequencyMetadata ONLY for days_of_the_week frequency type
        # For other frequency types (daily, weekly, etc.), leave frequencyMetadata as-is
        if "frequencyMetadata" in chore_dict and chore_dict["frequencyMetadata"]:
            freq_type = chore_dict.get("frequencyType", "")
            freq_meta = chore_dict["frequencyMetadata"]

            # Only validate for days_of_the_week - other types use different metadata structures
            if freq_type == "days_of_the_week":
                logger.info(f"Validating frequencyMetadata for days_of_the_week: {freq_meta}")

                # API quirk: Returns partial frequencyMetadata but expects full format on update
                # UI sends: unit, timezone, days, time, weekPattern, occurrences, weekNumbers
                # API returns: days, time, weekPattern (missing unit, timezone, occurrences, weekNumbers)
                # API expects on update: ALL fields that UI sends
                if isinstance(freq_meta, dict):
                    # Add required empty arrays if missing
                    if "occurrences" not in freq_meta:
                        freq_meta["occurrences"] = []
                        logger.info("Added missing 'occurrences' array")
                    if "weekNumbers" not in freq_meta:
                        freq_meta["weekNumbers"] = []
                        logger.info("Added missing 'weekNumbers' array")

                    # Add unit if missing (API expects it but doesn't return it)
                    if "unit" not in freq_meta:
                        freq_meta["unit"] = "days"
                        logger.info("Added missing 'unit' field")

                    # Add timezone if missing (API expects it but doesn't return it)
                    if "timezone" not in freq_meta:
                        # Extract timezone from time field if present, otherwise default to UTC
                        if "time" in freq_meta and isinstance(freq_meta["time"], str):
                            # Try to extract timezone from ISO format time
                            time_str = freq_meta["time"]
                            if "-" in time_str or "+" in time_str:
                                # Has timezone offset, try to map to timezone name
                                # For simplicity, default to America/New_York for negative offsets
                                freq_meta["timezone"] = "America/New_York"
                            else:
                                freq_meta["timezone"] = "UTC"
                        else:
                            freq_meta["timezone"] = "UTC"
                        logger.info(f"Added missing 'timezone' field: {freq_meta['timezone']}")

                    chore_dict["frequencyMetadata"] = freq_meta
                    logger.info(f"Fixed frequencyMetadata: {freq_meta}")
            else:
                logger.info(f"Skipping frequencyMetadata validation for frequency type: {freq_type}")

        updated_chore = await self._put_chore(chore_id, chore_dict)

        logger.info(f"Updated chore {chore_id}: {updated_chore.name}")
        return updated_chore

    async def update_chore_due_date(
        self,
        chore_id: int,
        due_date: Optional[str],
        current_chore: Optional[Chore] = None,
    ) -> Chore:
        """
        Change a chore's next due date via PUT /api/v1/chores/{id}/dueDate.

        Args:
            chore_id: Chore ID to update
            due_date: New due date (RFC3339 or YYYY-MM-DD), None to clear it
            current_chore: Already fetched chore (avoids another request)

        Returns:
            Updated Chore object
        """
        if current_chore is None:
            current_chore = await self.get_chore(chore_id)
            if current_chore is None:
                raise ValueError(f"Chore {chore_id} not found")

        payload = {
            "dueDate": normalize_datetime(due_date) if due_date else None,
            # Optimistic locking: rejected if the chore changed since it was fetched
            "updatedAt": current_chore.updatedAt,
        }
        logger.info(f"Updating chore {chore_id} due date to {payload['dueDate']}")
        await self._request("PUT", f"/api/v1/chores/{chore_id}/dueDate", json=payload)

        updated_chore = await self.get_chore(chore_id)
        if updated_chore is None:
            raise ValueError(f"Chore {chore_id} was updated but could not be retrieved")
        return updated_chore

    async def delete_chore(self, chore_id: int) -> bool:
        """
        Delete a chore.

        Note: Only the chore creator can delete a chore.

        Args:
            chore_id: Chore ID to delete

        Returns:
            True if deletion successful
        """
        logger.info(f"Deleting chore {chore_id}")
        await self._request("DELETE", f"/api/v1/chores/{chore_id}")
        logger.info(f"Deleted chore {chore_id}")
        return True

    async def complete_chore(
        self,
        chore_id: int,
        completed_by: Optional[int] = None,
        notes: Optional[str] = None,
        completed_at: Optional[str] = None,
    ) -> Chore:
        """
        Mark a chore as complete.

        Args:
            chore_id: Chore ID to complete
            completed_by: User ID who completed the chore (circle admins only)
            notes: Completion note
            completed_at: Completion time (RFC3339 or YYYY-MM-DD), defaults to now

        Returns:
            Updated Chore object (pending approval if the chore requires approval)
        """
        logger.info(f"Completing chore {chore_id}")

        # Donetick binds the body as JSON and rejects an empty body
        payload: dict[str, Any] = {}
        if completed_by is not None:
            payload["completedBy"] = completed_by
        if notes:
            payload["notes"] = notes
        if completed_at:
            payload["completedTime"] = normalize_datetime(completed_at)

        data = await self._request(
            "POST",
            f"/api/v1/chores/{chore_id}/do",
            json=payload,
        )

        # Handle both direct object and wrapped response
        if isinstance(data, dict) and "res" in data:
            data = data["res"]

        completed_chore = Chore(**data)
        logger.info(f"Completed chore {chore_id}")
        return completed_chore

    async def update_chore_priority(self, chore_id: int, priority: int) -> Chore:
        """
        Update a chore's priority level.

        Args:
            chore_id: Chore ID to update
            priority: Priority level (0=unset, 1=lowest, 4=highest)

        Returns:
            Updated Chore object

        Raises:
            ValueError: If priority is not in range 0-4
        """
        if not 0 <= priority <= 4:
            raise ValueError(f"Priority must be 0-4, got {priority}")

        logger.info(f"Updating chore {chore_id} priority to {priority}")
        data = await self._request(
            "PUT",
            f"/api/v1/chores/{chore_id}/priority",
            json={"priority": priority},
        )

        # API returns {"message": "Priority updated successfully"}; older versions returned the chore
        if isinstance(data, dict) and "res" in data:
            updated_chore = Chore(**data["res"])
        else:
            updated_chore = await self.get_chore(chore_id)
            if updated_chore is None:
                raise ValueError(f"Chore {chore_id} was updated but could not be retrieved")
        logger.info(f"Updated chore {chore_id} priority: {priority}")
        return updated_chore

    async def update_chore_assignee(self, chore_id: int, user_id: int) -> Chore:
        """
        Reassign a chore to a different user.

        Args:
            chore_id: Chore ID to update
            user_id: User ID of the new assignee

        Returns:
            Updated Chore object

        Note:
            Uses the generic PUT /api/v1/chores/ endpoint with the full chore object.
            The specialized /assignee endpoint does not exist in the API.
        """
        logger.info(f"Reassigning chore {chore_id} to user {user_id}")

        # Fetch current chore to get full object
        current_chore = await self.get_chore(chore_id)
        if current_chore is None:
            raise ValueError(f"Chore {chore_id} not found")

        # Convert to dict and update assignee fields
        chore_dict = self._chore_update_payload(current_chore)
        chore_dict["assignedTo"] = user_id
        chore_dict["assignees"] = [{"userId": user_id}]

        # If no strategy is set, use default
        if not chore_dict.get("assignStrategy"):
            chore_dict["assignStrategy"] = "least_completed"

        updated_chore = await self._put_chore(chore_id, chore_dict)

        logger.info(f"Reassigned chore {chore_id} to user {user_id}")
        return updated_chore

    async def skip_chore(self, chore_id: int) -> Chore:
        """
        Skip a chore without marking it complete.

        For recurring chores, this schedules the next occurrence without
        completing the current one. Useful for chores that aren't needed.

        Args:
            chore_id: Chore ID to skip

        Returns:
            Updated Chore object with new due date
        """
        logger.info(f"Skipping chore {chore_id}")
        data = await self._request(
            "POST",
            f"/api/v1/chores/{chore_id}/skip",
        )

        # Handle both direct object and wrapped response
        if isinstance(data, dict) and "res" in data:
            data = data["res"]

        skipped_chore = Chore(**data)
        logger.info(f"Skipped chore {chore_id}, next due: {skipped_chore.nextDueDate}")
        return skipped_chore

    async def update_subtask_completion(
        self,
        chore_id: int,
        subtask_id: int,
        completed: bool
    ) -> Chore:
        """
        Update the completion status of a subtask.

        Marks a subtask as complete or incomplete without completing the entire chore.
        Useful for tracking progress on chores with multiple steps.

        Args:
            chore_id: Chore ID containing the subtask
            subtask_id: Subtask ID to update
            completed: True to mark complete, False to mark incomplete

        Returns:
            Updated Chore object with modified subtask

        Raises:
            ValueError: If subtask not found
        """
        logger.info(f"Updating subtask {subtask_id} on chore {chore_id} to completed={completed}")

        chore = await self.get_chore(chore_id)
        if chore is None:
            raise ValueError(f"Chore {chore_id} not found")
        if not any(subtask.get("id") == subtask_id for subtask in chore.subTasks):
            raise ValueError(f"Subtask {subtask_id} not found in chore {chore_id}")

        # Dedicated endpoint: sets completedBy to the current user and only touches this subtask
        completed_at = datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ") if completed else None
        await self._request(
            "PUT",
            f"/api/v1/chores/{chore_id}/subtask",
            json={"id": subtask_id, "choreId": chore_id, "completedAt": completed_at},
        )

        updated_chore = await self.get_chore(chore_id)
        if updated_chore is None:
            raise ValueError(f"Chore {chore_id} was updated but could not be retrieved")

        logger.info(f"Updated subtask {subtask_id} completion status")
        return updated_chore

    async def get_chore_history(self, chore_id: int) -> list[ChoreHistory]:
        """
        Get completion history for a specific chore.

        Returns all completion records for the given chore, including details
        about when it was completed, by whom, and any notes.

        Args:
            chore_id: Chore ID to fetch history for

        Returns:
            List of ChoreHistory objects for the chore

        Raises:
            httpx.HTTPStatusError: On API errors
        """
        logger.info(f"Fetching history for chore {chore_id}")

        data = await self._request("GET", f"/api/v1/chores/{chore_id}/history")

        # API may return either direct array or wrapped response {'res': [...]}
        history_list = data.get('res', data) if isinstance(data, dict) else data

        if not isinstance(history_list, list):
            logger.warning(f"Expected list for chore {chore_id} history, got {type(history_list)}")
            history_list = []

        # Parse response into ChoreHistory objects
        history = [ChoreHistory(**entry_data) for entry_data in history_list]

        logger.info(f"Retrieved {len(history)} history entries for chore {chore_id}")
        return history

    async def get_all_chores_history(
        self,
        days: int = 7,
        include_circle_members: bool = False,
    ) -> list[ChoreHistory]:
        """
        Get chore history entries of the last days.

        Args:
            days: Number of days to look back (Donetick's "limit" parameter, default: 7)
            include_circle_members: Include entries of all circle members, not only your own

        Returns:
            List of ChoreHistory objects

        Raises:
            httpx.HTTPStatusError: On API errors
        """
        logger.info(f"Fetching chores history (days={days}, members={include_circle_members})")

        params: dict[str, Any] = {"limit": days}
        if include_circle_members:
            params["members"] = "true"
        data = await self._request("GET", "/api/v1/chores/history", params=params)

        # API may return either direct array or wrapped response {'res': [...]}
        history_list = data.get('res', data) if isinstance(data, dict) else data

        if not isinstance(history_list, list):
            logger.warning(f"Expected list for chores history, got {type(history_list)}")
            history_list = []

        # Parse response into ChoreHistory objects
        history = [ChoreHistory(**entry_data) for entry_data in history_list]

        logger.info(f"Retrieved {len(history)} history entries")
        return history

    async def get_chore_details(self, chore_id: int) -> ChoreDetail:
        """
        Get detailed chore information including statistics.

        Returns extended chore details with completion statistics, history,
        and analytics data not available in the standard get_chore endpoint.

        Args:
            chore_id: Chore ID to fetch details for

        Returns:
            ChoreDetail object with statistics and history

        Raises:
            httpx.HTTPStatusError: On API errors
        """
        logger.info(f"Fetching detailed information for chore {chore_id}")

        data = await self._request("GET", f"/api/v1/chores/{chore_id}/details")

        # API may return wrapped response {'res': {...}} or direct object
        detail_data = data.get('res', data) if isinstance(data, dict) and 'res' in data else data

        chore_detail = ChoreDetail(**detail_data)
        logger.info(f"Retrieved details for chore {chore_id}: {chore_detail.name}")
        return chore_detail

    # ==================== THINGS ====================

    async def list_things(self) -> list[Thing]:
        """
        List all things of the current user.

        Things are private to their owner, not shared with the circle.

        Returns:
            List of Thing objects
        """
        logger.info("Fetching things")
        data = await self._request("GET", "/api/v1/things")
        things_data = data.get("res", []) if isinstance(data, dict) else data
        things = [Thing(**thing) for thing in things_data or []]
        logger.info(f"Retrieved {len(things)} things")
        return things

    async def get_thing(self, thing_id: int) -> Thing:
        """
        Get a thing of the current user by ID.

        The full API has no single-thing endpoint, so this filters list_things().

        Raises:
            ValueError: If the thing does not exist or belongs to another user
        """
        for thing in await self.list_things():
            if thing.id == thing_id:
                return thing
        raise ValueError(
            f"Thing {thing_id} not found. Things are private to their owner; "
            "use list_things to see your things."
        )

    async def create_thing(self, name: str, thing_type: str, state: Any = None) -> Thing:
        """
        Create a thing.

        Args:
            name: Thing name
            thing_type: text, number or boolean
            state: Initial state (validated against the type)

        Returns:
            Created Thing object
        """
        thing = ThingCreate(name=name, type=thing_type, state=state)
        logger.info(f"Creating thing: {thing.name}")
        data = await self._request("POST", "/api/v1/things", json=thing.model_dump(exclude_none=True))
        thing_data = data.get("res", data) if isinstance(data, dict) else data
        return Thing(**thing_data)

    async def update_thing(
        self,
        thing_id: int,
        name: Optional[str] = None,
        thing_type: Optional[str] = None,
        state: Any = None,
    ) -> Thing:
        """
        Update a thing's name, type and/or state.

        Changing only the state this way does not trigger chores; use set_thing_state.

        Returns:
            Updated Thing object
        """
        current = await self.get_thing(thing_id)
        new_type = thing_type or current.type
        if state is None and new_type != current.type:
            # Donetick keeps the old state, which must still be valid for the new type
            state = current.state
        thing = ThingCreate(name=name or current.name, type=new_type, state=state)

        payload = {"id": thing_id, **thing.model_dump(exclude_none=True)}
        logger.info(f"Updating thing {thing_id}")
        data = await self._request("PUT", "/api/v1/things", json=payload)
        thing_data = data.get("res", data) if isinstance(data, dict) else data
        return Thing(**thing_data)

    async def set_thing_state(
        self,
        thing_id: int,
        state: Any = None,
        increment: Optional[int] = None,
    ) -> tuple[Thing, list[int]]:
        """
        Set a thing's state, which evaluates the triggers of linked chores.

        Args:
            thing_id: Thing ID
            state: New state (validated against the thing type)
            increment: Add this value to the current state (number things only)

        Returns:
            Tuple of the updated Thing and the IDs of chores whose trigger matched
            (Donetick sets their due date to now if they have none)
        """
        if (state is None) == (increment is None):
            raise ValueError("Provide exactly one of state or increment")

        # Also guards against a Donetick crash on unknown thing IDs
        thing = await self.get_thing(thing_id)

        if increment is not None:
            if thing.type != "number":
                raise ValueError(f"increment only works for number things, thing {thing_id} is {thing.type}")
            current = int(normalize_thing_state("number", thing.state or "0"))
            state = current + increment
        new_state = normalize_thing_state(thing.type, state)

        logger.info(f"Setting thing {thing_id} state to {new_state}")
        data = await self._request(
            "PUT", f"/api/v1/things/{thing_id}/state", params={"value": new_state}
        )
        thing_data = data.get("res", data) if isinstance(data, dict) else data
        updated_thing = Thing(**{**thing.model_dump(), **thing_data})

        # Only this response includes the linked chores (the list endpoint does not preload them)
        triggered = [
            link.choreId
            for link in updated_thing.thingChores or []
            if evaluate_thing_trigger(link.triggerState, link.condition, new_state)
        ]
        return updated_thing, triggered

    async def get_thing_history(self, thing_id: int, offset: int = 0) -> list[ThingHistory]:
        """
        Get the state history of a thing.

        Args:
            thing_id: Thing ID
            offset: Number of entries to skip (Donetick requires this parameter)

        Returns:
            List of ThingHistory objects, newest first (Donetick returns 10 per page)
        """
        logger.info(f"Fetching history for thing {thing_id} (offset={offset})")
        data = await self._request(
            "GET", f"/api/v1/things/{thing_id}/history", params={"offset": offset}
        )
        history_data = data.get("res", []) if isinstance(data, dict) else data
        return [ThingHistory(**entry) for entry in history_data or []]

    async def delete_thing(self, thing_id: int) -> bool:
        """
        Delete a thing.

        Raises:
            ValueError: If chores are still triggered by the thing
        """
        logger.info(f"Deleting thing {thing_id}")
        try:
            await self._request("DELETE", f"/api/v1/things/{thing_id}")
        except httpx.HTTPStatusError as e:
            if e.response.status_code == 405:
                raise ValueError(
                    f"Thing {thing_id} still triggers chores. Remove the thing trigger from "
                    "those chores (update_chore with remove_thing_trigger) before deleting it."
                ) from e
            raise
        return True

    async def build_thing_trigger(
        self,
        thing_id: int,
        trigger_state: Any,
        condition: Optional[str] = None,
    ) -> ThingTrigger:
        """
        Validate a chore trigger against the thing and build the thingTrigger payload.

        Validating up front matters: Donetick creates the chore before linking the thing,
        so an invalid thing leaves a chore without trigger behind.
        """
        thing = await self.get_thing(thing_id)
        condition = condition or "eq"
        if condition in NUMERIC_TRIGGER_CONDITIONS and thing.type != "number":
            raise ValueError(
                f'Condition "{condition}" only works for number things, thing {thing_id} is {thing.type}'
            )
        return ThingTrigger(
            thingID=thing_id,
            triggerState=normalize_thing_state(thing.type, trigger_state),
            condition=condition,
        )

    async def get_circle_members(self) -> list[CircleMember]:
        """
        Get all members in the user's circle.

        Returns:
            List of CircleMember objects
        """
        logger.info("Fetching circle members")
        data = await self._request("GET", "/api/v1/circles/members/")

        # API returns {'res': [...]} format
        members_data = data.get('res', data) if isinstance(data, dict) else data
        members = [CircleMember(**member_data) for member_data in members_data]
        logger.info(f"Retrieved {len(members)} circle members")
        return members

    async def get_labels(self) -> list[Label]:
        """
        Get all labels in the user's circle.

        Returns:
            List of Label objects
        """
        logger.info("Fetching labels")
        data = await self._request("GET", "/api/v1/labels")

        # API returns {'res': [...]} format
        labels_data = data.get('res', data) if isinstance(data, dict) else data
        labels = [Label(**label_data) for label_data in labels_data]
        logger.info(f"Retrieved {len(labels)} labels")
        return labels

    async def create_label(self, name: str, color: Optional[str] = None) -> Label:
        """
        Create a new label.

        Args:
            name: Label name (required)
            color: Label color in hex format (e.g., "#80d8ff"), optional

        Returns:
            Created Label object
        """
        logger.info(f"Creating label: {name}")

        payload = {"name": name}
        if color:
            payload["color"] = color

        data = await self._request("POST", "/api/v1/labels", json=payload)

        # API returns {'res': label} format
        label_data = data.get('res', data) if isinstance(data, dict) else data
        label = Label(**label_data)
        logger.info(f"Created label with ID: {label.id}")
        return label

    async def update_label(self, label_id: int, name: str, color: Optional[str] = None) -> Label:
        """
        Update an existing label.

        Args:
            label_id: Label ID to update
            name: New label name
            color: New label color in hex format, optional

        Returns:
            Updated Label object
        """
        logger.info(f"Updating label {label_id}")

        payload = {
            "id": label_id,
            "name": name
        }
        if color:
            payload["color"] = color

        data = await self._request("PUT", "/api/v1/labels", json=payload)

        # API returns {'res': label} format
        label_data = data.get('res', data) if isinstance(data, dict) else data
        label = Label(**label_data)
        logger.info(f"Updated label {label_id}")
        return label

    async def delete_label(self, label_id: int) -> bool:
        """
        Delete a label.

        Args:
            label_id: Label ID to delete

        Returns:
            True if successful
        """
        logger.info(f"Deleting label {label_id}")
        await self._request("DELETE", f"/api/v1/labels/{label_id}")
        logger.info(f"Deleted label {label_id}")
        return True

    # ==================== Transformation Helpers ====================

    async def lookup_user_ids(self, usernames: list[str]) -> dict[str, int]:
        """
        Lookup user IDs from usernames.

        Args:
            usernames: List of usernames to lookup

        Returns:
            Dictionary mapping username to user ID
        """
        members = await self.get_circle_members()
        username_map = {}

        for member in members:
            # Match by username or display name (case-insensitive)
            member_username = member.username.lower()
            member_display = (member.displayName or "").lower()

            for requested_username in usernames:
                requested_lower = requested_username.lower()
                if requested_lower == member_username or requested_lower == member_display:
                    username_map[requested_username] = member.userId
                    break

        return username_map

    async def lookup_label_ids(self, label_names: list[str]) -> dict[str, int]:
        """
        Lookup label IDs from label names.

        Args:
            label_names: List of label names to lookup

        Returns:
            Dictionary mapping label name to label ID
        """
        labels = await self.get_labels()
        label_map = {}

        for label in labels:
            # Match by name (case-insensitive)
            label_name_lower = label.name.lower()

            for requested_name in label_names:
                if requested_name.lower() == label_name_lower:
                    label_map[requested_name] = label.id
                    break

        return label_map

    async def list_users(self) -> list[User]:
        """
        List all users in the circle.

        Returns:
            List of User objects

        Raises:
            httpx.HTTPStatusError: If the API request fails
        """
        logger.debug("Listing all circle users")

        users_data = await self._request("GET", "/api/v1/users/")

        # Handle both array and object response formats
        if isinstance(users_data, dict):
            users_data = users_data.get("users", users_data.get("res", []))

        users = [User(**user_data) for user_data in users_data]
        logger.info(f"Retrieved {len(users)} users from circle")
        return users

    async def get_user_profile(self) -> UserProfile:
        """
        Get the current user's detailed profile.

        Returns:
            UserProfile object with complete user information

        Raises:
            httpx.HTTPStatusError: If the API request fails
        """
        logger.debug("Getting current user profile")

        profile_data = await self._request("GET", "/api/v1/users/profile")

        # Handle both direct object and wrapped response
        if isinstance(profile_data, dict) and "res" in profile_data:
            profile_data = profile_data["res"]

        profile = UserProfile(**profile_data)
        logger.info(f"Retrieved profile for user: {profile.username} (ID: {profile.id})")
        return profile

    def transform_frequency_metadata(
        self,
        frequency_type: str,
        days_of_week: list[str] | None = None,
        time: str | None = None,
        timezone: str = "America/New_York"
    ) -> dict:
        """
        Transform simple frequency metadata to API format.

        Args:
            frequency_type: Type of frequency (once, daily, weekly, days_of_the_week, etc)
            days_of_week: Day abbreviations like ["Mon", "Wed", "Fri"] or full names like ["monday", "wednesday"]
            time: Time in HH:MM format (e.g., "16:00") or ISO format
            timezone: Timezone name (default: America/New_York)

        Returns:
            API-compatible frequency metadata dictionary

        Raises:
            ValueError: If frequency_type is 'days_of_the_week' but days_of_week is not provided
        """
        from datetime import datetime
        from zoneinfo import ZoneInfo

        # Validate required parameters for days_of_the_week
        if frequency_type == "days_of_the_week" and (not days_of_week or len(days_of_week) == 0):
            raise ValueError(
                "days_of_week parameter is required when frequency_type='days_of_the_week'. "
                "Please provide a list of days like ['Mon', 'Wed', 'Fri'] or ['monday', 'wednesday', 'friday']"
            )

        metadata = {}

        # Handle days of week
        if days_of_week and frequency_type in ("days_of_the_week", "weekly"):
            day_map = {
                "mon": "monday", "monday": "monday",
                "tue": "tuesday", "tuesday": "tuesday",
                "wed": "wednesday", "wednesday": "wednesday",
                "thu": "thursday", "thursday": "thursday",
                "fri": "friday", "friday": "friday",
                "sat": "saturday", "saturday": "saturday",
                "sun": "sunday", "sunday": "sunday",
            }

            # Convert all day names to lowercase full names
            normalized_days = []
            invalid_days = []
            for day in days_of_week:
                day_lower = day.lower().strip()
                if day_lower in day_map:
                    normalized_days.append(day_map[day_lower])
                else:
                    invalid_days.append(day)

            # Fail if any invalid day names were provided
            if invalid_days:
                raise ValueError(
                    f'Invalid day name(s): {", ".join(invalid_days)}. '
                    'Valid values: Mon/Monday, Tue/Tuesday, Wed/Wednesday, Thu/Thursday, '
                    'Fri/Friday, Sat/Saturday, Sun/Sunday'
                )

            # Fail if no valid days after normalization
            if not normalized_days:
                raise ValueError(
                    'No valid days provided in days_of_week parameter. '
                    'At least one day is required for frequency_type="days_of_the_week"'
                )

            metadata["days"] = normalized_days
            metadata["weekPattern"] = "every_week"
            metadata["occurrences"] = []
            metadata["weekNumbers"] = []

        # Handle time
        if time:
            # Check if already in ISO format
            if "T" in time or ":" in time and len(time) <= 5:
                # Simple HH:MM format - convert to RFC3339
                if ":" in time and len(time) <= 5:
                    time_parts = time.split(":")
                    hour = int(time_parts[0])
                    minute = int(time_parts[1]) if len(time_parts) > 1 else 0

                    # Create datetime in specified timezone
                    tz = ZoneInfo(timezone)
                    now = datetime.now(tz)
                    dt = now.replace(hour=hour, minute=minute, second=0, microsecond=0)

                    # Format as RFC3339
                    metadata["time"] = dt.isoformat()
                else:
                    # Already in ISO format
                    metadata["time"] = time

        return metadata

    def transform_notification_metadata(
        self,
        offset_minutes: int | None = None,
        remind_at_due_time: bool = False,
        nagging: bool = False,
        predue: bool = False
    ) -> dict:
        """
        Transform simple notification settings to API format with all three notification mechanisms.

        Args:
            offset_minutes: Minutes before (negative) or after (positive) due time
            remind_at_due_time: Whether to remind at exact due time
            nagging: Enable nagging notifications (repeated reminders)
            predue: Enable pre-due notifications (reminders before due date)

        Returns:
            API-compatible notification metadata with nagging, predue, and templates fields
        """
        metadata = {
            "nagging": nagging,
            "predue": predue
        }

        templates = []

        if offset_minutes is not None and offset_minutes != 0:
            templates.append({
                "value": offset_minutes,
                "unit": "m"
            })

        if remind_at_due_time:
            templates.append({
                "value": 0,
                "unit": "m"
            })

        if templates:
            metadata["templates"] = templates

        return metadata

    def transform_subtasks(self, subtask_names: list[str]) -> list[dict]:
        """
        Transform simple subtask names to API format.

        Args:
            subtask_names: List of subtask names

        Returns:
            API-compatible subtasks with orderId, completedAt, etc.
        """
        if not subtask_names:
            return []

        return [
            {
                "orderId": i,
                "name": task,
                "completedAt": None,
                "completedBy": 0,
                "parentId": None
            }
            for i, task in enumerate(subtask_names)
        ]

    def calculate_due_date(
        self,
        frequency_type: str,
        frequency_metadata: dict,
        timezone: str = "America/New_York"
    ) -> str:
        """
        Calculate initial due date based on frequency type and metadata.

        Args:
            frequency_type: Type of frequency
            frequency_metadata: Frequency metadata (with days, time, etc.)
            timezone: Timezone name

        Returns:
            Due date in RFC3339 format
        """
        from datetime import UTC, datetime, timedelta
        from zoneinfo import ZoneInfo

        tz = ZoneInfo(timezone)
        now = datetime.now(tz)

        if frequency_type == "once":
            # One-time chores - tomorrow at specified time or noon
            due = now + timedelta(days=1)
            due = due.replace(hour=12, minute=0, second=0, microsecond=0)

        elif frequency_type == "days_of_the_week" and "days" in frequency_metadata:
            # Find next occurrence of first day in list
            day_map = {
                "monday": 0, "tuesday": 1, "wednesday": 2, "thursday": 3,
                "friday": 4, "saturday": 5, "sunday": 6
            }

            first_day = frequency_metadata["days"][0]
            target_weekday = day_map.get(first_day, 0)

            # Parse time from metadata
            time_str = frequency_metadata.get("time", "")
            if time_str and "T" in time_str:
                time_dt = datetime.fromisoformat(time_str.replace('Z', '+00:00'))
                target_hour = time_dt.hour
                target_minute = time_dt.minute
            else:
                target_hour = 12
                target_minute = 0

            # Calculate days ahead
            current_weekday = now.weekday()
            days_ahead = (target_weekday - current_weekday) % 7

            if days_ahead == 0:
                target_time = now.replace(hour=target_hour, minute=target_minute, second=0, microsecond=0)
                if now >= target_time:
                    days_ahead = 7

            due = now + timedelta(days=days_ahead)
            due = due.replace(hour=target_hour, minute=target_minute, second=0, microsecond=0)

        elif frequency_type == "daily":
            # Tomorrow at specified time
            due = now + timedelta(days=1)
            time_str = frequency_metadata.get("time", "")
            if time_str and "T" in time_str:
                time_dt = datetime.fromisoformat(time_str.replace('Z', '+00:00'))
                due = due.replace(hour=time_dt.hour, minute=time_dt.minute, second=0, microsecond=0)
            else:
                due = due.replace(hour=12, minute=0, second=0, microsecond=0)

        else:
            # Default to tomorrow at noon
            due = now + timedelta(days=1)
            due = due.replace(hour=12, minute=0, second=0, microsecond=0)

        # Convert to UTC and format as RFC3339
        return due.astimezone(UTC).isoformat().replace('+00:00', 'Z')
