"""Tests for request/response compatibility with the current Donetick API (v0.1.79)."""

import json

import pytest
from pytest_httpx import HTTPXMock

from donetick_mcp.client import DonetickClient
from donetick_mcp.models import ChoreCreate, ChoreDetail, ChoreHistory, ChoreUpdate

BASE_URL = "https://donetick.test"
CHORE_URL = f"{BASE_URL}/api/v1/chores/1"
CHORES_URL = f"{BASE_URL}/api/v1/chores/"

CHORE = {
    "id": 1,
    "name": "Empty washing machine",
    "frequencyType": "trigger",
    "frequency": 1,
    "nextDueDate": None,
    "assignedTo": 5,
    "assignees": [{"userId": 5}],
    "assignStrategy": "least_completed",
    "isActive": True,
    "labelsV2": [],
    "circleId": 1,
    "createdAt": "2026-09-01T10:00:00Z",
    "updatedAt": "2026-09-02T10:00:00Z",
    "createdBy": 5,
    "status": 0,
    "priority": 2,
    "isPrivate": False,
    "thingChore": {"thingId": 3, "choreId": 1, "triggerState": "false", "condition": "eq"},
    "syncVersion": 7,
}


@pytest.fixture
def client():
    return DonetickClient(
        base_url=BASE_URL,
        username="test_user",
        password="test_password",
        rate_limit_per_second=100.0,
        rate_limit_burst=100,
    )


@pytest.fixture
def login(httpx_mock: HTTPXMock):
    httpx_mock.add_response(
        url=f"{BASE_URL}/api/v1/auth/login", method="POST", json={"token": "jwt"}
    )


def _put_payload(httpx_mock: HTTPXMock) -> dict:
    return json.loads(httpx_mock.get_request(url=CHORES_URL, method="PUT").content)


class TestChoreModels:
    """Models match Donetick's JSON."""

    def test_create_payload_uses_next_due_date_and_priority(self):
        payload = ChoreCreate(name="Test", dueDate="2026-09-20").model_dump(exclude_none=True)

        assert "dueDate" not in payload
        assert payload["nextDueDate"] == "2026-09-20T12:00:00Z"
        # Donetick dereferences priority without nil check
        assert payload["priority"] == 0

    def test_update_normalizes_due_date(self):
        assert ChoreUpdate(nextDueDate="2026-09-20").nextDueDate == "2026-09-20T12:00:00Z"

    def test_history_status_and_notes(self):
        entry = ChoreHistory(
            id=1,
            choreId=2,
            completedBy=3,
            performedAt=None,
            status=6,
            notes="moved to Friday",
            dueDate="2026-09-10T12:00:00Z",
            syncVersion=4,
        )

        assert entry.status == "rescheduled"
        assert entry.note == "moved to Friday"

    def test_history_unknown_status_rejected(self):
        with pytest.raises(ValueError):
            ChoreHistory(id=1, choreId=2, completedBy=3, status=42)

    def test_chore_detail_real_response(self):
        """GET /chores/{id}/details returns a subset of chore fields."""
        detail = ChoreDetail(
            id=1,
            name="Vacuum",
            description=None,
            frequencyType="weekly",
            nextDueDate="2026-09-20T12:00:00Z",
            assignedTo=5,
            lastCompletedDate="2026-09-13T12:00:00Z",
            lastCompletedBy=5,
            totalCompletedCount=4,
            priority=1,
            notes=None,
            createdBy=5,
            status=0,
            duration=1800,
            startTime=None,
            timerUpdatedAt=None,
            isActive=True,
            syncVersion=3,
        )

        assert detail.totalCompletedCount == 4
        assert detail.duration == 1800


class TestListChores:
    """Donetick omits inactive chores unless includeArchived=true."""

    async def test_default_lists_active_chores(self, client, httpx_mock: HTTPXMock, login):
        httpx_mock.add_response(url=CHORES_URL, json={"res": [CHORE]})

        async with client:
            chores = await client.list_chores()

        assert [c.id for c in chores] == [1]

    async def test_inactive_chores_request_archived(self, client, httpx_mock: HTTPXMock, login):
        inactive = {**CHORE, "id": 2, "isActive": False}
        httpx_mock.add_response(
            url=f"{CHORES_URL}?includeArchived=true", json={"res": [CHORE, inactive]}
        )

        async with client:
            chores = await client.list_chores(filter_active=False)

        assert [c.id for c in chores] == [2]


class TestCircleMembers:
    """Donetick's route is /circles/members; a trailing slash is answered with 301."""

    async def test_members_path_without_trailing_slash(self, client, httpx_mock: HTTPXMock, login):
        httpx_mock.add_response(
            url=f"{BASE_URL}/api/v1/circles/members",
            json={
                "res": [
                    {
                        "id": 1,
                        "userId": 2,
                        "circleId": 1,
                        "role": "member",
                        "isActive": True,
                        "username": "",
                        "displayName": "Konstantin",
                    }
                ]
            },
        )

        async with client:
            # Users without username are found by display name
            assert await client.lookup_user_ids(["Konstantin"]) == {"Konstantin": 2}


class TestUpdateChore:
    """Full chore updates via PUT /api/v1/chores/."""

    async def test_update_keeps_thing_trigger_and_assignee_objects(
        self, client, httpx_mock: HTTPXMock, login
    ):
        httpx_mock.add_response(url=CHORE_URL, json={"res": CHORE})
        httpx_mock.add_response(
            url=CHORES_URL, method="PUT", json={"message": "Chore updated successfully"}
        )
        httpx_mock.add_response(url=CHORE_URL, json={"res": {**CHORE, "name": "Renamed"}})

        async with client:
            chore = await client.update_chore(1, ChoreUpdate(name="Renamed"))

        assert chore.name == "Renamed"
        payload = _put_payload(httpx_mock)
        assert payload["id"] == 1
        # Donetick drops the thing link unless thingTrigger is sent again
        assert payload["thingTrigger"] == {"thingID": 3, "triggerState": "false", "condition": "eq"}
        assert "thingChore" not in payload
        assert payload["assignees"] == [{"userId": 5}]

    async def test_update_can_remove_thing_trigger(self, client, httpx_mock: HTTPXMock, login):
        httpx_mock.add_response(url=CHORE_URL, json={"res": CHORE})
        httpx_mock.add_response(url=CHORES_URL, method="PUT", json={"message": "ok"})
        httpx_mock.add_response(url=CHORE_URL, json={"res": {**CHORE, "thingChore": None}})

        async with client:
            await client.update_chore(
                1, ChoreUpdate(frequencyType="once"), remove_thing_trigger=True
            )

        payload = _put_payload(httpx_mock)
        assert "thingTrigger" not in payload
        assert payload["frequencyType"] == "once"

    async def test_due_date_only_uses_due_date_endpoint(self, client, httpx_mock: HTTPXMock, login):
        httpx_mock.add_response(url=CHORE_URL, json={"res": CHORE})
        httpx_mock.add_response(
            url=f"{CHORE_URL}/dueDate",
            method="PUT",
            match_json={"dueDate": "2026-09-20T12:00:00Z", "updatedAt": "2026-09-02T10:00:00Z"},
            json={"res": CHORE},
        )
        httpx_mock.add_response(
            url=CHORE_URL, json={"res": {**CHORE, "nextDueDate": "2026-09-20T12:00:00Z"}}
        )

        async with client:
            chore = await client.update_chore(1, ChoreUpdate(nextDueDate="2026-09-20"))

        assert chore.nextDueDate == "2026-09-20T12:00:00Z"
        assert httpx_mock.get_request(url=CHORES_URL, method="PUT") is None


class TestUpdateChoreRelations:
    """Assignees, labels and sub-tasks via the full chore update."""

    CHORE_WITH_RELATIONS = {
        **CHORE,
        "labelsV2": [{"id": 7, "name": "Kitchen", "color": "#fff", "created_by": 5}],
        "subTasks": [
            {"id": 11, "name": "Wipe table", "orderId": 0, "completedAt": None, "completedBy": 0, "parentId": None},
            {"id": 12, "name": "Mop floor", "orderId": 1, "completedAt": None, "completedBy": 0, "parentId": None},
        ],
    }

    def _mock_update(self, httpx_mock: HTTPXMock, updated: dict):
        httpx_mock.add_response(url=CHORE_URL, json={"res": self.CHORE_WITH_RELATIONS})
        httpx_mock.add_response(url=CHORES_URL, method="PUT", json={"message": "ok"})
        httpx_mock.add_response(url=CHORE_URL, json={"res": updated})

    async def test_subtasks_added_and_removed(self, client, httpx_mock: HTTPXMock, login):
        self._mock_update(httpx_mock, self.CHORE_WITH_RELATIONS)

        async with client:
            await client.update_chore(
                1, ChoreUpdate(), add_subtask_names=["Empty bin"], remove_subtask_ids=[11]
            )

        subtasks = _put_payload(httpx_mock)["subTasks"]
        # Existing sub-tasks must be sent again, otherwise Donetick deletes them
        assert [s["id"] for s in subtasks] == [12, -1]
        assert subtasks[1] == {
            "id": -1,
            "name": "Empty bin",
            "orderId": 2,
            "completedAt": None,
            "completedBy": 0,
            "parentId": None,
        }

    async def test_unknown_subtask_id(self, client, httpx_mock: HTTPXMock, login):
        httpx_mock.add_response(url=CHORE_URL, json={"res": self.CHORE_WITH_RELATIONS})

        async with client:
            with pytest.raises(ValueError, match="Sub-task.*99 not found"):
                await client.update_chore(1, ChoreUpdate(), remove_subtask_ids=[99])

        assert httpx_mock.get_request(url=CHORES_URL, method="PUT") is None

    async def test_assignees_replaced_and_assigned_to_follows(
        self, client, httpx_mock: HTTPXMock, login
    ):
        """The current assignee (5) is not in the new list, so the first new one is used."""
        self._mock_update(httpx_mock, {**self.CHORE_WITH_RELATIONS, "assignedTo": 6})

        async with client:
            await client.update_chore(1, ChoreUpdate(assignees=[{"userId": 6}, {"userId": 7}]))

        payload = _put_payload(httpx_mock)
        assert payload["assignees"] == [{"userId": 6}, {"userId": 7}]
        assert payload["assignedTo"] == 6
        # Existing sub-tasks are sent back unchanged
        assert [s["id"] for s in payload["subTasks"]] == [11, 12]

    async def test_assignees_keep_current_assignee(self, client, httpx_mock: HTTPXMock, login):
        self._mock_update(httpx_mock, self.CHORE_WITH_RELATIONS)

        async with client:
            await client.update_chore(1, ChoreUpdate(assignees=[{"userId": 6}, {"userId": 5}]))

        assert _put_payload(httpx_mock)["assignedTo"] == 5

    async def test_update_chore_tool_relations(self, call_tool, httpx_mock: HTTPXMock):
        httpx_mock.add_response(
            url=f"{BASE_URL}/api/v1/circles/members",
            json={
                "res": [
                    {"id": 1, "userId": 5, "circleId": 1, "role": "admin", "isActive": True,
                     "username": "me"},
                    {"id": 2, "userId": 2, "circleId": 1, "role": "member", "isActive": True,
                     "username": "", "displayName": "Konstantin"},
                ]
            },
        )
        httpx_mock.add_response(
            url=f"{BASE_URL}/api/v1/labels",
            json={"res": [{"id": 7, "name": "Kitchen"}, {"id": 8, "name": "Weekly"}]},
        )
        # Label 7 was added by another user, so Donetick keeps it
        self._mock_update(
            httpx_mock,
            {
                **self.CHORE_WITH_RELATIONS,
                "assignedTo": 2,
                "labelsV2": [{"id": 7, "name": "Kitchen"}, {"id": 8, "name": "Weekly"}],
            },
        )

        result = await call_tool(
            "update_chore",
            {
                "chore_id": 1,
                "usernames": ["Konstantin"],
                "label_names": ["Weekly"],
                "add_subtask_names": ["Empty bin"],
            },
        )

        assert not result.is_error
        payload = _put_payload(httpx_mock)
        assert payload["assignees"] == [{"userId": 2}]
        assert payload["assignedTo"] == 2
        assert payload["labelsV2"] == [{"id": 8}]
        assert [s["id"] for s in payload["subTasks"]] == [11, 12, -1]
        assert "Label(s) not removed: Kitchen" in result[0].text

    async def test_update_chore_tool_conflicting_arguments(self, call_tool):
        result = await call_tool(
            "update_chore", {"chore_id": 1, "usernames": ["a"], "assignee_ids": [1]}
        )

        assert result.is_error
        assert "either usernames or assignee_ids" in result[0].text


class TestChoreActions:
    """Dedicated chore action endpoints."""

    async def test_complete_sends_empty_json_body(self, client, httpx_mock: HTTPXMock, login):
        httpx_mock.add_response(
            url=f"{CHORE_URL}/do", method="POST", match_json={}, json={"res": CHORE}
        )

        async with client:
            await client.complete_chore(1)

        request = httpx_mock.get_request(url=f"{CHORE_URL}/do")
        assert request.headers["Content-Type"] == "application/json"
        assert request.url.params.get("completedBy") is None

    async def test_complete_pending_approval_message(self, call_tool, httpx_mock: HTTPXMock):
        httpx_mock.add_response(
            url=f"{CHORE_URL}/do", method="POST", json={"res": {**CHORE, "status": 3}}
        )

        result = await call_tool("complete_chore", {"chore_id": 1})

        assert "pending approval" in result[0].text

    async def test_subtask_not_found(self, client, httpx_mock: HTTPXMock, login):
        httpx_mock.add_response(url=CHORE_URL, json={"res": {**CHORE, "subTasks": []}})

        async with client:
            with pytest.raises(ValueError, match="Subtask 9 not found"):
                await client.update_subtask_completion(1, 9, True)

    async def test_update_assignee_keeps_thing_trigger(self, client, httpx_mock: HTTPXMock, login):
        httpx_mock.add_response(url=CHORE_URL, json={"res": CHORE})
        httpx_mock.add_response(url=CHORES_URL, method="PUT", json={"message": "ok"})
        httpx_mock.add_response(url=CHORE_URL, json={"res": {**CHORE, "assignedTo": 6}})

        async with client:
            await client.update_chore_assignee(1, 6)

        payload = _put_payload(httpx_mock)
        assert payload["assignedTo"] == 6
        assert payload["assignees"] == [{"userId": 6}]
        assert payload["thingTrigger"]["thingID"] == 3


class TestCreateChoreTool:
    """create_chore tool payloads."""

    async def test_date_only_due_date_uses_timezone(self, call_tool, httpx_mock: HTTPXMock):
        httpx_mock.add_response(url=CHORES_URL, method="POST", json={"res": 1})
        httpx_mock.add_response(url=CHORE_URL, json={"res": CHORE})

        result = await call_tool(
            "create_chore",
            {"name": "Dentist", "due_date": "2026-09-20", "timezone": "Europe/Berlin"},
        )

        assert "Successfully created" in result[0].text
        payload = json.loads(httpx_mock.get_request(url=CHORES_URL, method="POST").content)
        # 12:00 in Berlin (CEST, UTC+2)
        assert payload["nextDueDate"] == "2026-09-20T10:00:00Z"
        assert payload["priority"] == 0

    async def test_invalid_due_date(self, call_tool, httpx_mock: HTTPXMock):
        result = await call_tool("create_chore", {"name": "Dentist", "due_date": "20.09.2026"})

        assert "Validation Error" in result[0].text
        assert httpx_mock.get_request(url=CHORES_URL, method="POST") is None


class TestHistoryTool:
    """get_all_chores_history parameters."""

    async def test_days_and_members(self, call_tool, httpx_mock: HTTPXMock):
        httpx_mock.add_response(
            url=f"{BASE_URL}/api/v1/chores/history?limit=30&members=true", json={"res": []}
        )

        result = await call_tool(
            "get_all_chores_history", {"days": 30, "include_circle_members": True}
        )

        assert "last 30 days" in result[0].text
