"""Tests for chore actions (archive, undo, approval, timer, nudge) and projects."""

import json

import pytest
from pytest_httpx import HTTPXMock

from donetick_mcp.client import DonetickClient

BASE_URL = "https://donetick.test"
CHORE_URL = f"{BASE_URL}/api/v1/chores/1"
CHORES_URL = f"{BASE_URL}/api/v1/chores/"

CHORE = {
    "id": 1,
    "name": "Vacuum",
    "frequencyType": "weekly",
    "frequency": 1,
    "nextDueDate": "2026-09-20T12:00:00Z",
    "assignedTo": 5,
    "assignees": [{"userId": 5}],
    "isActive": True,
    "circleId": 1,
    "createdAt": "2026-09-01T10:00:00Z",
    "updatedAt": "2026-09-02T10:00:00Z",
    "createdBy": 5,
    "status": 0,
    "priority": 2,
}


def _mock_archive_refused(httpx_mock: HTTPXMock):
    """Donetick answers 500 when a user archives a chore they did not create."""
    httpx_mock.add_response(
        url=f"{CHORE_URL}/archive",
        method="PUT",
        status_code=500,
        json={"error": "Error archiving chore"},
    )


def _mock_me_and_members(httpx_mock: HTTPXMock, my_role: str):
    httpx_mock.add_response(
        url=f"{BASE_URL}/api/v1/users/profile",
        json={"res": {"id": 5, "username": "me", "displayName": "Me"}},
    )
    httpx_mock.add_response(
        url=f"{BASE_URL}/api/v1/circles/members",
        json={
            "res": [
                {"id": 1, "userId": 5, "circleId": 1, "role": my_role, "isActive": True,
                 "username": "me"},
                {"id": 2, "userId": 2, "circleId": 1, "role": "member", "isActive": True,
                 "username": "other"},
            ]
        },
    )


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
        url=f"{BASE_URL}/api/v1/auth/login", method="POST", json={"token": "jwt"}, is_optional=True
    )


class TestArchive:
    """Archiving deactivates chores; only the creator may do it."""

    async def test_archive_fetches_chore(self, client, httpx_mock: HTTPXMock, login):
        httpx_mock.add_response(
            url=f"{CHORE_URL}/archive",
            method="PUT",
            json={"message": "Chore archived successfully"},
        )
        httpx_mock.add_response(url=CHORE_URL, json={"res": {**CHORE, "isActive": False}})

        async with client:
            chore, via_update = await client.archive_chore(1)

        assert chore.isActive is False
        assert via_update is False

    async def test_archive_other_users_chore_as_admin(self, client, httpx_mock: HTTPXMock, login):
        """Donetick only lets the creator archive; admins fall back to a regular update."""
        others_chore = {**CHORE, "createdBy": 2, "notification": True}
        _mock_archive_refused(httpx_mock)
        httpx_mock.add_response(url=CHORE_URL, json={"res": others_chore})  # fallback check
        _mock_me_and_members(httpx_mock, my_role="admin")
        httpx_mock.add_response(url=CHORE_URL, json={"res": others_chore})  # update_chore fetch
        httpx_mock.add_response(url=CHORES_URL, method="PUT", json={"message": "ok"})
        httpx_mock.add_response(
            url=CHORE_URL, json={"res": {**others_chore, "isActive": False, "notification": False}}
        )

        async with client:
            chore, via_update = await client.archive_chore(1)

        assert via_update is True
        assert chore.isActive is False
        payload = json.loads(httpx_mock.get_request(url=CHORES_URL, method="PUT").content)
        assert payload["isActive"] is False
        # Donetick keeps sending notifications of chores deactivated via update
        assert payload["notification"] is False
        # Refused archive request is not retried
        assert len(httpx_mock.get_requests(url=f"{CHORE_URL}/archive")) == 1

    async def test_archive_other_users_chore_not_admin(self, client, httpx_mock: HTTPXMock, login):
        _mock_archive_refused(httpx_mock)
        httpx_mock.add_response(url=CHORE_URL, json={"res": {**CHORE, "createdBy": 2}})
        _mock_me_and_members(httpx_mock, my_role="member")

        async with client:
            with pytest.raises(ValueError, match="only its creator or a circle admin"):
                await client.archive_chore(1)

        assert httpx_mock.get_request(url=CHORES_URL, method="PUT") is None

    async def test_list_archived(self, client, httpx_mock: HTTPXMock, login):
        httpx_mock.add_response(
            url=f"{BASE_URL}/api/v1/chores/archived",
            json={"res": [{**CHORE, "isActive": False}]},
        )

        async with client:
            chores = await client.list_archived_chores()

        assert [c.id for c in chores] == [1]

    async def test_unarchive_tool(self, call_tool, httpx_mock: HTTPXMock):
        httpx_mock.add_response(
            url=f"{CHORE_URL}/unarchive",
            method="PUT",
            json={"message": "Chore unarchived successfully"},
        )
        httpx_mock.add_response(url=CHORE_URL, json={"res": CHORE})

        result = await call_tool("unarchive_chore", {"chore_id": 1})

        assert "Successfully unarchived chore 'Vacuum'" in result[0].text
        assert "circle admin" not in result[0].text

    async def test_archive_tool_explains_admin_fallback(self, call_tool, httpx_mock: HTTPXMock):
        others_chore = {**CHORE, "createdBy": 2}
        _mock_archive_refused(httpx_mock)
        httpx_mock.add_response(url=CHORE_URL, json={"res": others_chore})
        _mock_me_and_members(httpx_mock, my_role="admin")
        httpx_mock.add_response(url=CHORE_URL, json={"res": others_chore})
        httpx_mock.add_response(url=CHORES_URL, method="PUT", json={"message": "ok"})
        httpx_mock.add_response(url=CHORE_URL, json={"res": {**others_chore, "isActive": False}})

        result = await call_tool("archive_chore", {"chore_id": 1})

        assert not result.is_error
        assert "Active: False" in result[0].text
        assert "notifications were turned off" in result[0].text


class TestUndoAndApproval:
    """Undo and approval workflow."""

    async def test_undo_returns_message_and_chore(self, client, httpx_mock: HTTPXMock, login):
        httpx_mock.add_response(
            url=f"{CHORE_URL}/undo",
            method="POST",
            json={"message": "Successfully undid completion action", "res": CHORE},
        )

        async with client:
            message, chore = await client.undo_chore_action(1)

        assert message == "Successfully undid completion action"
        assert chore.nextDueDate == "2026-09-20T12:00:00Z"

    async def test_undo_too_late_shows_api_error(self, call_tool, httpx_mock: HTTPXMock):
        httpx_mock.add_response(
            url=f"{CHORE_URL}/undo",
            method="POST",
            status_code=400,
            json={
                "error": "No recent action found to undo (actions can only be undone within 5 minutes)"
            },
        )

        result = await call_tool("undo_chore_action", {"chore_id": 1})

        assert "within 5 minutes" in result[0].text

    async def test_undo_server_error_not_retried(self, client, httpx_mock: HTTPXMock, login):
        httpx_mock.add_response(
            url=f"{CHORE_URL}/undo",
            method="POST",
            status_code=500,
            json={"error": "Failed to undo action"},
        )

        async with client:
            with pytest.raises(Exception):
                await client.undo_chore_action(1)

        assert len(httpx_mock.get_requests(url=f"{CHORE_URL}/undo")) == 1

    async def test_approve(self, client, httpx_mock: HTTPXMock, login):
        httpx_mock.add_response(
            url=f"{CHORE_URL}/approve",
            method="POST",
            match_json={},
            json={"res": {**CHORE, "nextDueDate": "2026-09-27T12:00:00Z"}, "message": "ok"},
        )

        async with client:
            chore = await client.approve_chore(1)

        assert chore.nextDueDate == "2026-09-27T12:00:00Z"

    async def test_approve_not_admin(self, call_tool, httpx_mock: HTTPXMock):
        httpx_mock.add_response(
            url=f"{CHORE_URL}/approve",
            method="POST",
            status_code=403,
            json={"error": "Only admins can approve chores"},
        )

        result = await call_tool("approve_chore", {"chore_id": 1})

        assert "Permission denied (Only admins can approve chores)" in result[0].text

    async def test_reject_sends_notes(self, client, httpx_mock: HTTPXMock, login):
        httpx_mock.add_response(
            url=f"{CHORE_URL}/reject",
            method="POST",
            match_json={"notes": "Still dusty"},
            json={"res": CHORE, "message": "Chore rejected successfully"},
        )

        async with client:
            chore = await client.reject_chore(1, notes="Still dusty")

        assert chore.id == 1

    async def test_reject_without_notes_sends_json_body(self, client, httpx_mock: HTTPXMock, login):
        httpx_mock.add_response(
            url=f"{CHORE_URL}/reject",
            method="POST",
            match_json={},
            json={"res": CHORE, "message": "Chore rejected successfully"},
        )

        async with client:
            await client.reject_chore(1)


class TestTimerAndNudge:
    """Time tracking and nudges."""

    async def test_start_timer(self, call_tool, httpx_mock: HTTPXMock):
        httpx_mock.add_response(
            url=f"{CHORE_URL}/start",
            method="PUT",
            json={
                "res": {
                    "status": 1,
                    "duration": 120,
                    "timerUpdatedAt": "2026-09-16T10:00:00Z",
                    "syncVersion": 9,
                }
            },
        )
        httpx_mock.add_response(url=CHORE_URL, json={"res": {**CHORE, "status": 1}})

        result = await call_tool("start_chore_timer", {"chore_id": 1})

        assert "Started timer on chore 'Vacuum'" in result[0].text
        assert "Tracked time: 120s" in result[0].text

    async def test_start_timer_empty_response(self, client, httpx_mock: HTTPXMock, login):
        """Donetick can answer 200 without body when a paused chore has no session."""
        httpx_mock.add_response(url=f"{CHORE_URL}/start", method="PUT", content=b"")
        httpx_mock.add_response(url=CHORE_URL, json={"res": CHORE})

        async with client:
            chore, timer = await client.start_chore_timer(1)

        assert chore.id == 1
        assert timer == {}

    async def test_pause_timer(self, client, httpx_mock: HTTPXMock, login):
        httpx_mock.add_response(
            url=f"{CHORE_URL}/pause",
            method="PUT",
            json={"res": {"status": 2, "duration": 300, "timerUpdatedAt": "2026-09-16T10:05:00Z"}},
        )
        httpx_mock.add_response(url=CHORE_URL, json={"res": {**CHORE, "status": 2}})

        async with client:
            chore, timer = await client.pause_chore_timer(1)

        assert chore.status == 2
        assert timer["duration"] == 300

    async def test_nudge(self, call_tool, httpx_mock: HTTPXMock):
        httpx_mock.add_response(
            url=f"{CHORE_URL}/nudge",
            method="POST",
            match_json={"all_assignees": True, "message": "Please vacuum today"},
            json={
                "message": "Nudge sent to 1 user(s) across 2 device(s)",
                "warnings": ["Failed to send to user 7: no devices"],
            },
        )

        result = await call_tool(
            "nudge_chore",
            {"chore_id": 1, "all_assignees": True, "message": "Please vacuum today"},
        )

        assert "Nudge sent to 1 user(s)" in result[0].text
        assert "user 7" in result[0].text

    async def test_nudge_timeout_not_retried(self, client, httpx_mock: HTTPXMock, login):
        """A timed out nudge may have been delivered, so it must not be sent again."""
        import httpx

        httpx_mock.add_exception(httpx.ReadTimeout("timeout"), url=f"{CHORE_URL}/nudge")

        async with client:
            with pytest.raises(httpx.TimeoutException):
                await client.nudge_chore(1)

        assert len(httpx_mock.get_requests(url=f"{CHORE_URL}/nudge")) == 1


class TestProjects:
    """Projects list and chore assignment."""

    async def test_list_projects_plain_array(self, call_tool, httpx_mock: HTTPXMock):
        httpx_mock.add_response(
            url=f"{BASE_URL}/api/v1/projects",
            json=[
                {
                    "id": 2,
                    "name": "Garden",
                    "description": None,
                    "color": "#00ff00",
                    "icon": None,
                    "circleId": 1,
                    "created_by": 5,
                    "createdAt": "2026-09-01T10:00:00Z",
                    "isDefault": False,
                }
            ],
        )

        result = await call_tool("list_projects", {})

        data = json.loads(result[0].text)
        assert data["projects"][0]["name"] == "Garden"

    async def test_create_chore_with_project(self, call_tool, httpx_mock: HTTPXMock):
        httpx_mock.add_response(url=f"{BASE_URL}/api/v1/chores/", method="POST", json={"res": 1})
        httpx_mock.add_response(url=CHORE_URL, json={"res": {**CHORE, "projectId": 2}})

        await call_tool("create_chore", {"name": "Mow lawn", "project_id": 2})

        payload = json.loads(
            httpx_mock.get_request(url=f"{BASE_URL}/api/v1/chores/", method="POST").content
        )
        assert payload["projectId"] == 2
