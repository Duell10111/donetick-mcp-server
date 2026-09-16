"""Pytest configuration and fixtures."""

import pytest
from pytest_httpx import HTTPXMock

from donetick_mcp.config import config

# Base URL used by all mocked tests (never resolved, all HTTP is mocked)
TEST_BASE_URL = "https://donetick.test"


@pytest.fixture(autouse=True)
def test_config(monkeypatch):
    """Point the global configuration at the mocked test instance.

    Keeps mocked tests independent of a developer's .env file or environment.
    Live API tests build their own Config from the environment.
    """
    monkeypatch.setattr(config, "donetick_base_url", TEST_BASE_URL)
    monkeypatch.setattr(config, "donetick_username", "test_user")
    monkeypatch.setattr(config, "donetick_password", "test_password")
    monkeypatch.setattr(config, "donetick_api_token", None)
    return config


@pytest.fixture
def mock_login(httpx_mock: HTTPXMock):
    """Mock the login endpoint for JWT authentication.

    Note: This mock is marked as optional because many tests don't trigger
    authentication (e.g., when the global client is already authenticated).
    """
    httpx_mock.add_response(
        url=f"{TEST_BASE_URL}/api/v1/auth/login",
        json={"token": "test_jwt_token"},
        method="POST",
        is_optional=True,  # Allow tests to not trigger login
    )
    return httpx_mock
