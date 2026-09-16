"""Tests for the MCPServer setup: metadata, schemas, annotations, errors and lifespan."""

import json

from mcp import Client
from pytest_httpx import HTTPXMock

from donetick_mcp import __version__
from donetick_mcp.client import DonetickClient
from donetick_mcp.server import mcp as mcp_server

BASE_URL = "https://donetick.test"
CHORE_URL = f"{BASE_URL}/api/v1/chores/1"
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


class TestServerMetadata:
    """Server info and tool definitions."""

    async def test_server_info_uses_package_version(self):
        async with Client(mcp_server) as client:
            assert client.server_info.name == "donetick-chores"
            assert client.server_info.version == __version__

    async def test_tool_annotations(self, list_tools):
        tools = {tool.name: tool for tool in await list_tools()}

        assert tools["list_chores"].annotations.read_only_hint is True
        assert tools["delete_chore"].annotations.destructive_hint is True
        assert tools["delete_thing"].annotations.destructive_hint is True
        assert tools["create_chore"].annotations.destructive_hint is False

    async def test_tools_have_descriptions_and_no_output_schema(self, list_tools):
        for tool in await list_tools():
            assert tool.description, tool.name
            # Tools return formatted text, not structured output
            assert tool.output_schema is None, tool.name
            for name, prop in tool.input_schema["properties"].items():
                assert prop.get("description"), f"{tool.name}.{name} has no description"

    async def test_context_not_in_schema(self, list_tools):
        tools = {tool.name: tool for tool in await list_tools()}
        assert "ctx" not in tools["get_chore"].input_schema["properties"]
        assert tools["get_chore"].input_schema["required"] == ["chore_id"]


class TestToolErrors:
    """Errors are returned as tool errors (isError) for the model."""

    async def test_invalid_arguments_rejected_before_request(
        self, call_tool, httpx_mock: HTTPXMock
    ):
        result = await call_tool("update_chore_priority", {"chore_id": 1, "priority": 5})

        assert result.is_error
        assert "less than or equal to 4" in result[0].text
        assert httpx_mock.get_request(url=f"{CHORE_URL}/priority") is None

    async def test_not_found_is_error(self, call_tool, httpx_mock: HTTPXMock):
        httpx_mock.add_response(url=f"{BASE_URL}/api/v1/chores/999", status_code=404)

        result = await call_tool("get_chore", {"chore_id": 999})

        assert result.is_error
        assert "not found" in result[0].text

    async def test_success_is_not_error(self, call_tool, httpx_mock: HTTPXMock):
        httpx_mock.add_response(url=f"{BASE_URL}/api/v1/chores/", json={"res": [CHORE]})

        result = await call_tool("list_chores", {"detail_level": "brief"})

        assert not result.is_error
        assert json.loads(result[0].text)["chores"][0]["name"] == "Vacuum"


class TestUpdateChoreArguments:
    """update_chore uses snake_case arguments like the other tools."""

    async def test_snake_case_fields_map_to_api(self, call_tool, httpx_mock: HTTPXMock):
        httpx_mock.add_response(url=CHORE_URL, json={"res": CHORE})
        httpx_mock.add_response(
            url=f"{BASE_URL}/api/v1/chores/", method="PUT", json={"message": "ok"}
        )
        httpx_mock.add_response(url=CHORE_URL, json={"res": {**CHORE, "isPrivate": True}})

        result = await call_tool(
            "update_chore",
            {"chore_id": 1, "is_private": True, "require_approval": True, "project_id": 2},
        )

        assert not result.is_error
        payload = json.loads(
            httpx_mock.get_request(url=f"{BASE_URL}/api/v1/chores/", method="PUT").content
        )
        assert payload["isPrivate"] is True
        assert payload["requireApproval"] is True
        assert payload["projectId"] == 2


class TestLifespan:
    """The lifespan owns the Donetick client."""

    async def test_client_closed_after_session(self, monkeypatch, call_tool, httpx_mock: HTTPXMock):
        closed = []
        original_close = DonetickClient.close

        async def spy_close(self):
            closed.append(self)
            await original_close(self)

        monkeypatch.setattr(DonetickClient, "close", spy_close)
        httpx_mock.add_response(url=f"{BASE_URL}/api/v1/chores/", json={"res": []})

        await call_tool("list_chores", {})

        assert len(closed) == 1
