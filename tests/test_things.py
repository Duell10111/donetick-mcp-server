"""Tests for the Things API integration and thing-triggered chores."""

import json

import pytest
from pytest_httpx import HTTPXMock

from donetick_mcp import server
from donetick_mcp.client import DonetickClient, evaluate_thing_trigger
from donetick_mcp.models import ThingCreate, normalize_thing_state

BASE_URL = "https://donetick.test"
THINGS_URL = f"{BASE_URL}/api/v1/things"
CHORES_URL = f"{BASE_URL}/api/v1/chores/"

WASHER = {
    "id": 3,
    "userID": 5,
    "circleId": 0,
    "name": "Washing machine running",
    "state": "true",
    "type": "boolean",
    # GET /api/v1/things does not preload linked chores
    "thingChores": None,
    "updatedAt": "2026-09-16T08:00:00Z",
    "createdAt": "2026-09-01T08:00:00Z",
}
COUNTER = {**WASHER, "id": 4, "name": "Litter box uses", "state": "4", "type": "number"}


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


@pytest.fixture
async def server_client(monkeypatch, login):
    """Fresh global client for MCP tool calls, independent of other server tests."""
    fresh_client = DonetickClient(rate_limit_per_second=100.0, rate_limit_burst=100)
    monkeypatch.setattr(server, "client", fresh_client)
    yield fresh_client
    await fresh_client.close()


def _mock_things(httpx_mock: HTTPXMock, *things):
    httpx_mock.add_response(url=THINGS_URL, method="GET", json={"res": list(things)})


class TestThingState:
    """State validation mirrors Donetick's isValidThingState."""

    @pytest.mark.parametrize(
        ("thing_type", "state", "expected"),
        [
            ("boolean", True, "true"),
            ("boolean", "False", "false"),
            ("number", 7, "7"),
            ("number", " -3 ", "-3"),
            ("text", "done", "done"),
            ("text", 12, "12"),
        ],
    )
    def test_valid(self, thing_type, state, expected):
        assert normalize_thing_state(thing_type, state) == expected

    @pytest.mark.parametrize(
        ("thing_type", "state"),
        [("boolean", "yes"), ("number", "1.5"), ("number", True), ("action", "x")],
    )
    def test_invalid(self, thing_type, state):
        with pytest.raises(ValueError):
            normalize_thing_state(thing_type, state)

    def test_create_model_rejects_unsupported_type(self):
        with pytest.raises(ValueError, match="Invalid thing type"):
            ThingCreate(name="Button", type="action")


class TestTriggerEvaluation:
    """Trigger evaluation mirrors Donetick's EvaluateThingChore."""

    @pytest.mark.parametrize(
        ("trigger_state", "condition", "new_state", "expected"),
        [
            ("false", None, "false", True),
            ("false", "eq", "true", False),
            ("false", "neq", "true", True),
            ("10", "gt", "11", True),
            ("10", "gte", "10", True),
            ("10", "lt", "10", False),
            ("10", "lte", "9", True),
            ("10", "gt", "abc", False),
        ],
    )
    def test_conditions(self, trigger_state, condition, new_state, expected):
        assert evaluate_thing_trigger(trigger_state, condition, new_state) is expected


class TestThingsClient:
    """Client methods for /api/v1/things."""

    async def test_list_things(self, client, httpx_mock: HTTPXMock, login):
        _mock_things(httpx_mock, WASHER, COUNTER)

        async with client:
            things = await client.list_things()

        assert [t.name for t in things] == ["Washing machine running", "Litter box uses"]
        assert things[0].thingChores is None

    async def test_create_thing(self, client, httpx_mock: HTTPXMock, login):
        httpx_mock.add_response(
            url=THINGS_URL,
            method="POST",
            match_json={"name": "Washing machine running", "type": "boolean", "state": "false"},
            status_code=201,
            json={"res": {**WASHER, "state": "false"}},
        )

        async with client:
            thing = await client.create_thing("Washing machine running", "boolean", False)

        assert thing.id == 3
        assert thing.state == "false"

    async def test_create_thing_invalid_state_not_sent(self, client, httpx_mock: HTTPXMock):
        async with client:
            with pytest.raises(ValueError, match="integer"):
                await client.create_thing("Counter", "number", "many")

        assert httpx_mock.get_requests() == []

    async def test_update_thing_sends_id_in_body(self, client, httpx_mock: HTTPXMock, login):
        _mock_things(httpx_mock, WASHER)
        httpx_mock.add_response(
            url=THINGS_URL,
            method="PUT",
            match_json={"id": 3, "name": "Washer", "type": "boolean"},
            json={"res": {**WASHER, "name": "Washer"}},
        )

        async with client:
            thing = await client.update_thing(3, name="Washer")

        assert thing.name == "Washer"

    async def test_update_thing_type_revalidates_current_state(
        self, client, httpx_mock: HTTPXMock, login
    ):
        _mock_things(httpx_mock, WASHER)

        async with client:
            with pytest.raises(ValueError, match="integer"):
                await client.update_thing(3, thing_type="number")

    async def test_set_state_reports_triggered_chores(self, client, httpx_mock: HTTPXMock, login):
        _mock_things(httpx_mock, WASHER)
        httpx_mock.add_response(
            url=f"{THINGS_URL}/3/state?value=false",
            method="PUT",
            json={
                "res": {
                    **WASHER,
                    "state": "false",
                    "thingChores": [
                        {"thingId": 3, "choreId": 42, "triggerState": "false", "condition": "eq"},
                        {"thingId": 3, "choreId": 43, "triggerState": "true", "condition": ""},
                    ],
                }
            },
        )

        async with client:
            thing, triggered = await client.set_thing_state(3, state=False)

        assert thing.state == "false"
        assert triggered == [42]

    async def test_set_state_increment(self, client, httpx_mock: HTTPXMock, login):
        _mock_things(httpx_mock, COUNTER)
        httpx_mock.add_response(
            url=f"{THINGS_URL}/4/state?value=5",
            method="PUT",
            json={"res": {**COUNTER, "state": "5", "thingChores": []}},
        )

        async with client:
            thing, triggered = await client.set_thing_state(4, increment=1)

        assert thing.state == "5"
        assert triggered == []

    async def test_increment_requires_number_thing(self, client, httpx_mock: HTTPXMock, login):
        _mock_things(httpx_mock, WASHER)

        async with client:
            with pytest.raises(ValueError, match="number things"):
                await client.set_thing_state(3, increment=1)

    async def test_unknown_thing_is_not_sent(self, client, httpx_mock: HTTPXMock, login):
        """Donetick crashes on unknown IDs in PUT /things/{id}/state, so check first."""
        _mock_things(httpx_mock, WASHER)

        async with client:
            with pytest.raises(ValueError, match="Thing 99 not found"):
                await client.set_thing_state(99, state="true")

        assert httpx_mock.get_request(method="PUT") is None

    async def test_history_sends_offset(self, client, httpx_mock: HTTPXMock, login):
        httpx_mock.add_response(
            url=f"{THINGS_URL}/3/history?offset=0",
            json={
                "res": [
                    {
                        "id": 9,
                        "thingId": 3,
                        "state": "false",
                        "createdAt": "2026-09-16T09:00:00Z",
                        "updatedAt": "2026-09-16T09:00:00Z",
                    }
                ]
            },
        )

        async with client:
            history = await client.get_thing_history(3)

        assert history[0].state == "false"

    async def test_delete_with_linked_chores(self, client, httpx_mock: HTTPXMock, login):
        httpx_mock.add_response(
            url=f"{THINGS_URL}/3",
            method="DELETE",
            status_code=405,
            json={"error": "Unable to delete thing with associated tasks"},
        )

        async with client:
            with pytest.raises(ValueError, match="still triggers chores"):
                await client.delete_thing(3)

    async def test_numeric_condition_requires_number_thing(
        self, client, httpx_mock: HTTPXMock, login
    ):
        _mock_things(httpx_mock, WASHER)

        async with client:
            with pytest.raises(ValueError, match="only works for number things"):
                await client.build_thing_trigger(3, "true", "gt")


class TestThingTools:
    """MCP tools for things and thing-triggered chores."""

    async def test_create_trigger_chore(self, server_client, httpx_mock: HTTPXMock):
        _mock_things(httpx_mock, WASHER)
        httpx_mock.add_response(url=CHORES_URL, method="POST", json={"res": 42})
        httpx_mock.add_response(
            url=f"{BASE_URL}/api/v1/chores/42",
            json={
                "res": {
                    "id": 42,
                    "name": "Empty washing machine",
                    "frequencyType": "trigger",
                    "frequency": 1,
                    "circleId": 1,
                    "createdAt": "2026-09-16T08:00:00Z",
                    "updatedAt": "2026-09-16T08:00:00Z",
                    "createdBy": 5,
                    "thingChore": {"thingId": 3, "choreId": 42, "triggerState": "false"},
                }
            },
        )

        result = await server.call_tool(
            "create_chore",
            {"name": "Empty washing machine", "thing_id": 3, "thing_trigger_state": False},
        )

        assert "Successfully created" in result[0].text
        payload = json.loads(httpx_mock.get_request(url=CHORES_URL, method="POST").content)
        assert payload["frequencyType"] == "trigger"
        assert payload["thingTrigger"] == {"thingID": 3, "triggerState": "false", "condition": "eq"}
        # Trigger chores get their due date from the thing
        assert "nextDueDate" not in payload

    async def test_create_trigger_chore_requires_state(self, server_client, httpx_mock: HTTPXMock):
        result = await server.call_tool("create_chore", {"name": "Empty washer", "thing_id": 3})

        assert "thing_trigger_state is required" in result[0].text
        assert httpx_mock.get_request(url=CHORES_URL, method="POST") is None

    async def test_create_trigger_chore_unknown_thing(self, server_client, httpx_mock: HTTPXMock):
        _mock_things(httpx_mock, WASHER)

        result = await server.call_tool(
            "create_chore", {"name": "Empty washer", "thing_id": 99, "thing_trigger_state": "false"}
        )

        assert "Thing 99 not found" in result[0].text
        assert httpx_mock.get_request(url=CHORES_URL, method="POST") is None

    async def test_set_thing_state_tool(self, server_client, httpx_mock: HTTPXMock):
        _mock_things(httpx_mock, COUNTER)
        httpx_mock.add_response(
            url=f"{THINGS_URL}/4/state?value=10",
            method="PUT",
            json={
                "res": {
                    **COUNTER,
                    "state": "10",
                    "thingChores": [
                        {"thingId": 4, "choreId": 7, "triggerState": "10", "condition": "gte"}
                    ],
                }
            },
        )

        result = await server.call_tool("set_thing_state", {"thing_id": 4, "state": 10})

        assert "to '10'" in result[0].text
        assert "Triggered chores: 7" in result[0].text

    async def test_list_things_tool(self, server_client, httpx_mock: HTTPXMock):
        _mock_things(httpx_mock, WASHER)

        result = await server.call_tool("list_things", {})

        data = json.loads(result[0].text)
        assert data["count"] == 1
        assert data["things"][0]["name"] == "Washing machine running"

    async def test_update_chore_with_thing_and_remove_conflict(
        self, server_client, httpx_mock: HTTPXMock
    ):
        result = await server.call_tool(
            "update_chore",
            {"chore_id": 1, "thing_id": 3, "thing_trigger_state": "true", "remove_thing_trigger": True},
        )

        assert "either thing_id or remove_thing_trigger" in result[0].text
