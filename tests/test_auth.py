"""Tests for JWT authentication: login, token refresh and error handling."""

import asyncio
import json
from datetime import UTC, datetime, timedelta

import pytest
from pytest_httpx import HTTPXMock

from donetick_mcp import server
from donetick_mcp.client import DonetickAuthError, DonetickClient, _parse_token_expiry
from donetick_mcp.config import Config

BASE_URL = "https://donetick.test"
LOGIN_URL = f"{BASE_URL}/api/v1/auth/login"
REFRESH_URL = f"{BASE_URL}/api/v1/auth/refresh"
CHORES_URL = f"{BASE_URL}/api/v1/chores/"

CHORE = {
    "id": 1,
    "name": "Test Chore",
    "frequencyType": "once",
    "frequency": 1,
    "isActive": True,
    "nextDueDate": "2025-11-10T00:00:00Z",
    "circleId": 1,
    "createdAt": "2025-11-03T00:00:00Z",
    "updatedAt": "2025-11-03T00:00:00Z",
    "createdBy": 1,
}


def _expiry(delta: timedelta) -> str:
    """Format an expiry timestamp like Go's time.Time JSON encoding."""
    return (datetime.now(UTC) + delta).strftime("%Y-%m-%dT%H:%M:%S.123456789Z")


def _token_response(access: str, refresh: str, expires_in: timedelta = timedelta(hours=1)) -> dict:
    """Build a login/refresh response as returned by current Donetick versions."""
    expiry = _expiry(expires_in)
    return {
        "access_token": access,
        "refresh_token": refresh,
        "access_token_expiry": expiry,
        "refresh_token_expiry": _expiry(timedelta(days=7)),
        "token_type": "Bearer",
        "token": access,
        "expire": expiry,
    }


@pytest.fixture
def client():
    """Create a test client instance."""
    return DonetickClient(
        base_url=BASE_URL,
        username="test_user",
        password="test_password",
        rate_limit_per_second=100.0,
        rate_limit_burst=100,
    )


class TestParseTokenExpiry:
    """Tests for parsing expiry timestamps from auth responses."""

    def test_go_nanoseconds(self):
        expiry = _parse_token_expiry("2026-09-16T12:30:00.123456789Z")
        assert expiry == datetime(2026, 9, 16, 12, 30, 0, 123456, tzinfo=UTC)

    def test_offset(self):
        expiry = _parse_token_expiry("2026-09-16T14:30:00+02:00")
        assert expiry == datetime(2026, 9, 16, 12, 30, tzinfo=UTC)

    @pytest.mark.parametrize("value", [None, "", "0001-01-01T00:00:00Z", "not-a-date", 123])
    def test_missing_or_invalid(self, value):
        assert _parse_token_expiry(value) is None


class TestLogin:
    """Tests for the login flow."""

    async def test_login_stores_access_and_refresh_token(self, client, httpx_mock: HTTPXMock):
        httpx_mock.add_response(
            url=LOGIN_URL,
            method="POST",
            json=_token_response("access_1", "refresh_1"),
            headers={"Set-Cookie": "refresh_token=refresh_1; Path=/; Secure; HttpOnly"},
        )

        async with client:
            await client.login()

            assert client._jwt_token == "access_1"
            assert client._refresh_token == "refresh_1"
            assert client._token_expiry is not None
            assert client.client.headers["Authorization"] == "Bearer access_1"
            # Refresh token cookie is dropped so the stored token is sent explicitly
            assert "refresh_token" not in client.client.cookies

        request = httpx_mock.get_request(url=LOGIN_URL)
        assert json.loads(request.content) == {"username": "test_user", "password": "test_password"}

    async def test_login_legacy_response(self, client, httpx_mock: HTTPXMock):
        httpx_mock.add_response(
            url=LOGIN_URL,
            method="POST",
            json={"code": 200, "token": "legacy_token", "expire": "2026-09-16T12:30:00Z"},
        )

        async with client:
            await client.login()

        assert client._jwt_token == "legacy_token"
        assert client._refresh_token is None
        assert client._token_expiry == datetime(2026, 9, 16, 12, 30, tzinfo=UTC)

    async def test_login_mfa_required(self, client, httpx_mock: HTTPXMock):
        httpx_mock.add_response(
            url=LOGIN_URL,
            method="POST",
            json={"mfaRequired": True, "sessionToken": "mfa_session"},
        )

        async with client:
            with pytest.raises(DonetickAuthError, match="multi-factor"):
                await client.login()

        assert client._jwt_token is None

    async def test_login_invalid_credentials(self, client, httpx_mock: HTTPXMock):
        httpx_mock.add_response(
            url=LOGIN_URL, method="POST", status_code=401, json={"error": "Invalid credentials"}
        )

        async with client:
            with pytest.raises(DonetickAuthError, match="invalid username or password"):
                await client.login()

    async def test_login_password_auth_disabled(self, client, httpx_mock: HTTPXMock):
        httpx_mock.add_response(
            url=LOGIN_URL,
            method="POST",
            status_code=403,
            json={"error": "Password authentication is disabled on this instance."},
        )

        async with client:
            with pytest.raises(DonetickAuthError, match="SSO-only"):
                await client.login()

    async def test_login_missing_token(self, client, httpx_mock: HTTPXMock):
        httpx_mock.add_response(url=LOGIN_URL, method="POST", json={"message": "ok"})

        async with client:
            with pytest.raises(DonetickAuthError, match="missing token"):
                await client.login()

    async def test_concurrent_requests_login_once(self, client, httpx_mock: HTTPXMock):
        """Parallel tool calls must not trigger parallel logins."""
        httpx_mock.add_response(
            url=LOGIN_URL, method="POST", json=_token_response("access_1", "refresh_1")
        )
        httpx_mock.add_response(url=CHORES_URL, json={"res": []}, is_reusable=True)

        async with client:
            results = await asyncio.gather(*(client.list_chores() for _ in range(5)))

        assert results == [[]] * 5
        assert len(httpx_mock.get_requests(url=LOGIN_URL)) == 1


class TestTokenRefresh:
    """Tests for refreshing the access token."""

    async def test_refresh_before_expiry(self, client, httpx_mock: HTTPXMock):
        """A token about to expire is refreshed with the refresh token, not a new login."""
        httpx_mock.add_response(
            url=LOGIN_URL,
            method="POST",
            json=_token_response("access_1", "refresh_1", expires_in=timedelta(seconds=30)),
        )
        httpx_mock.add_response(
            url=REFRESH_URL,
            method="POST",
            match_json={"refresh_token": "refresh_1"},
            json=_token_response("access_2", "refresh_2"),
        )
        httpx_mock.add_response(url=CHORES_URL, json={"res": []})

        async with client:
            await client.login()
            await client.list_chores()

        assert client._jwt_token == "access_2"
        assert client._refresh_token == "refresh_2"
        chores_request = httpx_mock.get_request(url=CHORES_URL)
        assert chores_request.headers["Authorization"] == "Bearer access_2"
        assert len(httpx_mock.get_requests(url=LOGIN_URL)) == 1

    async def test_no_refresh_while_token_valid(self, client, httpx_mock: HTTPXMock):
        httpx_mock.add_response(
            url=LOGIN_URL, method="POST", json=_token_response("access_1", "refresh_1")
        )
        httpx_mock.add_response(url=CHORES_URL, json={"res": []})

        async with client:
            await client.list_chores()

        assert httpx_mock.get_requests(url=REFRESH_URL) == []

    async def test_refresh_rejected_falls_back_to_login(self, client, httpx_mock: HTTPXMock):
        httpx_mock.add_response(
            url=LOGIN_URL,
            method="POST",
            json=_token_response("access_1", "refresh_1", expires_in=timedelta(seconds=30)),
        )
        httpx_mock.add_response(
            url=REFRESH_URL,
            method="POST",
            status_code=401,
            json={"error": "Invalid or expired refresh token", "code": "INVALID_REFRESH_TOKEN"},
        )
        httpx_mock.add_response(
            url=LOGIN_URL, method="POST", json=_token_response("access_2", "refresh_2")
        )
        httpx_mock.add_response(url=CHORES_URL, json={"res": []})

        async with client:
            await client.login()
            await client.list_chores()

        assert client._jwt_token == "access_2"
        assert len(httpx_mock.get_requests(url=LOGIN_URL)) == 2

    async def test_401_uses_refresh_token(self, client, httpx_mock: HTTPXMock):
        """A rejected access token is replaced via refresh before falling back to login."""
        httpx_mock.add_response(
            url=LOGIN_URL, method="POST", json=_token_response("access_1", "refresh_1")
        )
        httpx_mock.add_response(url=CHORES_URL, status_code=401)
        httpx_mock.add_response(
            url=REFRESH_URL,
            method="POST",
            match_json={"refresh_token": "refresh_1"},
            json=_token_response("access_2", "refresh_2"),
        )
        httpx_mock.add_response(url=CHORES_URL, json={"res": [CHORE]})

        async with client:
            chores = await client.list_chores()

        assert len(chores) == 1
        assert client._jwt_token == "access_2"
        assert len(httpx_mock.get_requests(url=LOGIN_URL)) == 1
        retried = httpx_mock.get_requests(url=CHORES_URL)[-1]
        assert retried.headers["Authorization"] == "Bearer access_2"


class TestServerAuthErrors:
    """Tests for authentication errors surfaced through MCP tools."""

    async def test_mfa_error_message(self, monkeypatch, httpx_mock: HTTPXMock):
        fresh_client = DonetickClient()
        monkeypatch.setattr(server, "client", fresh_client)
        httpx_mock.add_response(
            url=LOGIN_URL,
            method="POST",
            json={"mfaRequired": True, "sessionToken": "mfa_session"},
        )

        try:
            result = await server.call_tool("list_chores", {})
        finally:
            await fresh_client.close()

        assert len(result) == 1
        assert "Authentication failed" in result[0].text
        assert "multi-factor" in result[0].text


class TestConfig:
    """Tests for configuration validation."""

    def test_import_without_credentials(self, monkeypatch):
        for var in ("DONETICK_BASE_URL", "DONETICK_USERNAME", "DONETICK_PASSWORD"):
            monkeypatch.delenv(var, raising=False)

        config = Config()

        with pytest.raises(ValueError, match="DONETICK_BASE_URL"):
            config.validate()

    def test_https_required(self, monkeypatch):
        monkeypatch.setenv("DONETICK_BASE_URL", "http://donetick.test")
        monkeypatch.setenv("DONETICK_USERNAME", "user")
        monkeypatch.setenv("DONETICK_PASSWORD", "pass")

        with pytest.raises(ValueError, match="HTTPS"):
            Config().validate()

    def test_valid_config_strips_trailing_slash(self, monkeypatch):
        monkeypatch.setenv("DONETICK_BASE_URL", "https://donetick.test/")
        monkeypatch.setenv("DONETICK_USERNAME", "user")
        monkeypatch.setenv("DONETICK_PASSWORD", "pass")

        config = Config()
        config.validate()

        assert config.donetick_base_url == "https://donetick.test"

    def test_api_token_warns(self, monkeypatch, caplog):
        monkeypatch.setenv("DONETICK_BASE_URL", "https://donetick.test")
        monkeypatch.setenv("DONETICK_USERNAME", "user")
        monkeypatch.setenv("DONETICK_PASSWORD", "pass")
        monkeypatch.setenv("DONETICK_API_TOKEN", "token")

        Config().validate()

        assert "not supported yet" in caplog.text
