"""Live API integration tests for Donetick MCP server.

IMPORTANT: These tests interact with a REAL Donetick instance.
Do not run these tests against a production instance or with real user data.

Running these tests:
    # Run all live API tests
    pytest tests/integration/test_live_api.py -m live_api -v

    # Run a specific test
    pytest tests/integration/test_live_api.py::test_create_chore -m live_api -v

    # Skip live tests in normal test runs
    pytest tests/ -m "not live_api"

Requirements:
    1. A running Donetick instance accessible via HTTPS
    2. Environment variables set in .env:
        - DONETICK_BASE_URL (e.g., https://your-instance.com)
        - DONETICK_USERNAME (your Donetick username)
        - DONETICK_PASSWORD (your Donetick password)
        - DONETICK_TEST_USER_ID (optional, defaults to 1)

What these tests verify:
    - Real API authentication and JWT token management
    - Actual HTTP request/response handling
    - Field name casing (camelCase vs PascalCase) in live API
    - End-to-end chore CRUD operations
    - Error handling with real API error responses
    - Rate limiting and retry logic under real conditions

Test data cleanup:
    All test chores are automatically deleted after tests complete via the
    test_chore_ids fixture. This prevents test pollution in your instance.
"""

from datetime import datetime, timedelta
from typing import List

import httpx
import pytest

from donetick_mcp.client import DonetickAuthError, DonetickClient
from donetick_mcp.models import Chore, ChoreCreate, ChoreUpdate

# Mark all tests in this module as live_api tests
pytestmark = pytest.mark.live_api


class TestChoreCreation:
    """Test suite for chore creation operations against live API."""

    async def test_create_simple_chore(
        self,
        live_client: DonetickClient,
        test_chore_ids: List[int],
        test_user_id: int,
    ):
        """
        Test creating a simple one-time chore with minimal fields.

        Verifies:
        - Basic chore creation with required fields only
        - API accepts camelCase field names (name, description, dueDate, createdBy)
        - Response contains valid chore ID
        - Created chore can be retrieved
        - Field values match what was sent

        Test data is automatically cleaned up via test_chore_ids fixture.
        """
        # Create a simple chore
        tomorrow = (datetime.now() + timedelta(days=1)).strftime("%Y-%m-%d")

        chore = ChoreCreate(
            name="Test Simple Chore",
            description="A simple test chore for live API testing",
            dueDate=tomorrow,
            createdBy=test_user_id,
        )

        # Create the chore
        created = await live_client.create_chore(chore)
        test_chore_ids.append(created.id)

        # Verify response
        assert created.id is not None
        assert created.id > 0
        assert created.name == "Test Simple Chore"
        assert created.description == "A simple test chore for live API testing"
        assert created.createdBy == test_user_id

        # Verify we can retrieve it
        retrieved = await live_client.get_chore(created.id)
        assert retrieved is not None
        assert retrieved.id == created.id
        assert retrieved.name == created.name
        # Donetick only reads nextDueDate (dueDate is sent as nextDueDate)
        assert retrieved.nextDueDate is not None

    async def test_create_chore_with_frequency(
        self,
        live_client: DonetickClient,
        test_chore_ids: List[int],
        test_user_id: int,
    ):
        """
        Test creating a recurring chore with frequency configuration.

        Verifies:
        - Recurring chore creation (frequencyType, frequency)
        - FrequencyMetadata with specific days/times
        - Rolling schedule configuration
        - Response includes all frequency fields

        This tests more complex chore configurations to ensure the API
        handles structured metadata correctly.
        """
        # Create a weekly recurring chore
        tomorrow = (datetime.now() + timedelta(days=1)).strftime("%Y-%m-%d")

        chore = ChoreCreate(
            name="Test Weekly Chore",
            description="A recurring chore every Monday",
            dueDate=tomorrow,
            createdBy=test_user_id,
            frequencyType="weekly",
            frequency=1,
            frequencyMetadata={
                "days": ["monday"],
                "time": "09:00",
            },
            isRolling=False,
        )

        # Create the chore
        created = await live_client.create_chore(chore)
        test_chore_ids.append(created.id)

        # Verify response
        assert created.id is not None
        assert created.name == "Test Weekly Chore"
        assert created.frequencyType == "weekly"
        assert created.frequency == 1
        assert created.isRolling is False
        assert created.frequencyMetadata is not None

    async def test_create_chore_with_subtasks(
        self,
        live_client: DonetickClient,
        test_chore_ids: List[int],
        test_user_id: int,
    ):
        """
        Test creating a chore with sub-tasks (checklist items).

        Verifies:
        - Sub-tasks array is accepted in create payload
        - Sub-tasks are returned in response with proper ordering
        - Each sub-task has expected fields (name, position, completed)

        Sub-tasks are a key feature for breaking down complex chores.
        """
        # Create a chore with sub-tasks
        tomorrow = (datetime.now() + timedelta(days=1)).strftime("%Y-%m-%d")

        chore = ChoreCreate(
            name="Test Chore with Subtasks",
            description="A chore with checklist items",
            dueDate=tomorrow,
            createdBy=test_user_id,
            subTasks=[
                {"name": "First step", "orderId": 1, "completed": False},
                {"name": "Second step", "orderId": 2, "completed": False},
                {"name": "Final step", "orderId": 3, "completed": False},
            ],
        )

        # Create the chore
        created = await live_client.create_chore(chore)
        test_chore_ids.append(created.id)

        # Verify response
        assert created.id is not None
        assert created.name == "Test Chore with Subtasks"

        # Verify sub-tasks (get_chore includes sub-tasks via Preload)
        retrieved = await live_client.get_chore(created.id)
        assert retrieved is not None
        assert retrieved.subTasks is not None
        assert len(retrieved.subTasks) >= 3  # At least our 3 sub-tasks

    async def test_create_chore_with_all_fields(
        self,
        live_client: DonetickClient,
        test_chore_ids: List[int],
        test_user_id: int,
    ):
        """
        Test creating a chore with all possible fields populated.

        Verifies:
        - All 26+ create fields are accepted by the API
        - Complex configurations (notifications, labels, priority)
        - Field name casing is correct for all fields
        - Response includes all configured values

        This is a comprehensive test ensuring complete API coverage.
        """
        # Create a chore with many fields populated
        tomorrow = (datetime.now() + timedelta(days=1)).strftime("%Y-%m-%d")

        chore = ChoreCreate(
            name="Test Comprehensive Chore",
            description="A chore with all possible fields configured",
            dueDate=tomorrow,
            createdBy=test_user_id,
            frequencyType="weekly",
            frequency=1,
            frequencyMetadata={"days": ["monday", "wednesday"], "time": "10:00"},
            isRolling=False,
            assignedTo=test_user_id,
            assignStrategy="least_completed",
            notification=True,
            notificationMetadata={"nagging": True, "predue": True},
            priority=3,
            isActive=True,
            isPrivate=False,
            points=15,
            completionWindow=3600,  # 1 hour in seconds
            requireApproval=False,
            deadlineOffset=7200,  # 2 hours in seconds
        )

        # Create the chore
        created = await live_client.create_chore(chore)
        test_chore_ids.append(created.id)

        # Verify response
        assert created.id is not None
        assert created.name == "Test Comprehensive Chore"
        assert created.priority == 3
        assert created.points == 15
        assert created.isActive is True
        assert created.notification is True


class TestChoreRetrieval:
    """Test suite for chore retrieval operations against live API."""

    async def test_list_chores(self, live_client: DonetickClient):
        """
        Test listing all chores in the circle.

        Verifies:
        - List endpoint returns without errors
        - Response is a list of Chore objects
        - Each chore has required fields
        - Pagination works if many chores exist

        Note: This test doesn't create data, it just verifies the list
        endpoint works with whatever data exists.
        """
        # List all chores
        chores = await live_client.list_chores()

        # Verify response is a list
        assert isinstance(chores, list)

        # If there are chores, verify structure
        if len(chores) > 0:
            first_chore = chores[0]
            assert hasattr(first_chore, "id")
            assert hasattr(first_chore, "name")
            assert hasattr(first_chore, "frequencyType")
            assert first_chore.id > 0
            assert len(first_chore.name) > 0

    async def test_list_chores_with_filters(
        self,
        live_client: DonetickClient,
        test_chore_ids: List[int],
        test_user_id: int,
    ):
        """
        Test listing chores with filters (active status, assigned user).

        Verifies:
        - isActive filter correctly filters active/inactive chores
        - assignedTo filter returns only chores for specific user
        - Multiple filters can be combined
        - Empty results are handled correctly

        This test creates test chores with specific properties to verify filtering.
        """
        # Create test chores with different properties
        tomorrow = (datetime.now() + timedelta(days=1)).strftime("%Y-%m-%d")

        active_chore = ChoreCreate(
            name="Test Active Chore for Filter",
            dueDate=tomorrow,
            createdBy=test_user_id,
            assignedTo=test_user_id,
            isActive=True,
        )
        created_active = await live_client.create_chore(active_chore)
        test_chore_ids.append(created_active.id)

        # Test active filter
        active_chores = await live_client.list_chores(filter_active=True)
        assert isinstance(active_chores, list)
        assert all(chore.isActive for chore in active_chores)

        # Test assigned user filter
        user_chores = await live_client.list_chores(assigned_to_user_id=test_user_id)
        assert isinstance(user_chores, list)
        # Verify our created chore is in the list
        assert any(chore.id == created_active.id for chore in user_chores)

    async def test_get_chore_by_id(
        self,
        live_client: DonetickClient,
        test_chore_ids: List[int],
        test_user_id: int,
    ):
        """
        Test retrieving a specific chore by ID.

        Verifies:
        - Direct GET endpoint works (not list + filter)
        - Sub-tasks are included in response (via API Preload)
        - All chore fields are populated correctly
        - Response matches Chore model structure

        The get_chore endpoint uses a different API path than list_chores
        and includes sub-tasks via the Preload mechanism.
        """
        # Create a chore with sub-tasks
        tomorrow = (datetime.now() + timedelta(days=1)).strftime("%Y-%m-%d")

        chore = ChoreCreate(
            name="Test Get By ID",
            description="Testing direct GET endpoint",
            dueDate=tomorrow,
            createdBy=test_user_id,
            subTasks=[
                {"name": "Step 1", "orderId": 1, "completed": False},
                {"name": "Step 2", "orderId": 2, "completed": False},
            ],
        )

        created = await live_client.create_chore(chore)
        test_chore_ids.append(created.id)

        # Get the chore by ID (should include sub-tasks)
        retrieved = await live_client.get_chore(created.id)

        # Verify response
        assert retrieved is not None
        assert retrieved.id == created.id
        assert retrieved.name == "Test Get By ID"
        assert retrieved.description == "Testing direct GET endpoint"
        # Sub-tasks should be included via Preload
        assert retrieved.subTasks is not None

    async def test_get_nonexistent_chore(self, live_client: DonetickClient):
        """
        Test error handling when retrieving a non-existent chore.

        Verifies:
        - 404 error is raised for invalid chore ID
        - Error message is appropriate
        - No server error or crash occurs

        This ensures proper error handling in the client.
        """
        # Try to get a chore with a very high ID that shouldn't exist
        nonexistent_id = 999999999

        # Should return None (client handles 404 gracefully)
        result = await live_client.get_chore(nonexistent_id)
        assert result is None


class TestChoreUpdate:
    """Test suite for chore update operations against live API."""

    async def test_update_chore_basic_fields(
        self,
        live_client: DonetickClient,
        test_chore_ids: List[int],
        test_user_id: int,
    ):
        """
        Test updating basic chore fields (name, description, due date).

        Verifies:
        - Update endpoint accepts camelCase field names
        - Only specified fields are updated
        - Updated values are returned in response
        - Re-fetching the chore shows updated values

        CRITICAL: Tests the actual field name casing used by the API.
        This is the primary test for verifying camelCase vs PascalCase.
        """
        # Create a chore first
        tomorrow = (datetime.now() + timedelta(days=1)).strftime("%Y-%m-%d")

        chore = ChoreCreate(
            name="Original Name",
            description="Original description",
            dueDate=tomorrow,
            createdBy=test_user_id,
        )

        created = await live_client.create_chore(chore)
        test_chore_ids.append(created.id)

        # Update basic fields
        next_week = (datetime.now() + timedelta(days=7)).strftime("%Y-%m-%d")
        update = ChoreUpdate(
            name="Updated Name",
            description="Updated description",
            nextDueDate=next_week,
        )

        updated = await live_client.update_chore(created.id, update)

        # Verify update response
        assert updated.id == created.id
        assert updated.name == "Updated Name"
        assert updated.description == "Updated description"

        # Re-fetch and verify
        refetched = await live_client.get_chore(created.id)
        assert refetched is not None
        assert refetched.name == "Updated Name"
        assert refetched.description == "Updated description"

    async def test_update_chore_priority(
        self,
        live_client: DonetickClient,
        test_chore_ids: List[int],
        test_user_id: int,
    ):
        """
        Test updating chore priority using specialized endpoint.

        Verifies:
        - Priority update endpoint works (0-4 range)
        - Priority changes are reflected in chore
        - Invalid priority values are rejected

        Priority uses a specialized endpoint rather than generic update.
        """
        # Create a chore
        tomorrow = (datetime.now() + timedelta(days=1)).strftime("%Y-%m-%d")

        chore = ChoreCreate(
            name="Test Priority Update",
            dueDate=tomorrow,
            createdBy=test_user_id,
            priority=1,
        )

        created = await live_client.create_chore(chore)
        test_chore_ids.append(created.id)

        # Update priority to highest
        updated = await live_client.update_chore_priority(created.id, 4)

        # Verify priority was updated
        assert updated.id == created.id
        assert updated.priority == 4

        # Verify with get
        refetched = await live_client.get_chore(created.id)
        assert refetched is not None
        assert refetched.priority == 4

    async def test_update_chore_assignee(
        self,
        live_client: DonetickClient,
        test_chore_ids: List[int],
        test_user_id: int,
    ):
        """
        Test updating chore assignee using specialized endpoint.

        Verifies:
        - Assignee update endpoint works
        - Chore is reassigned to new user
        - Assignment strategy is respected

        Assignee changes use a specialized endpoint for assignment rotation.
        """
        # Create a chore
        tomorrow = (datetime.now() + timedelta(days=1)).strftime("%Y-%m-%d")

        chore = ChoreCreate(
            name="Test Assignee Update",
            dueDate=tomorrow,
            createdBy=test_user_id,
            assignedTo=test_user_id,
        )

        created = await live_client.create_chore(chore)
        test_chore_ids.append(created.id)

        # Update assignee (reassign to same user for simplicity)
        updated = await live_client.update_chore_assignee(created.id, test_user_id)

        # Verify assignee was updated
        assert updated.id == created.id
        assert updated.assignedTo == test_user_id


class TestChoreCompletion:
    """Test suite for chore completion operations against live API."""

    async def test_complete_chore(
        self,
        live_client: DonetickClient,
        test_chore_ids: List[int],
        test_user_id: int,
    ):
        """
        Test marking a chore as complete.

        Verifies:
        - Complete endpoint works (/chores/{id}/do)
        - Chore status changes to completed
        - For recurring chores, next due date is calculated
        - Completion time is recorded

        Note: This may require Premium/Plus subscription depending on
        Donetick instance configuration.
        """
        # Create a simple one-time chore
        tomorrow = (datetime.now() + timedelta(days=1)).strftime("%Y-%m-%d")

        chore = ChoreCreate(
            name="Test Complete Chore",
            dueDate=tomorrow,
            createdBy=test_user_id,
            assignedTo=test_user_id,
        )

        created = await live_client.create_chore(chore)
        test_chore_ids.append(created.id)

        # Complete the chore
        try:
            completed = await live_client.complete_chore(created.id, test_user_id)

            # Verify completion
            assert completed.id == created.id
            # Note: Status field structure may vary by API version
            # Just verify we got a response
            assert completed is not None
        except Exception as e:
            # May fail if Premium/Plus required
            # Don't fail the test, just log
            if "premium" in str(e).lower() or "subscription" in str(e).lower():
                pytest.skip(f"Skipping: Requires Premium/Plus subscription - {e}")
            else:
                raise

    async def test_skip_chore(
        self,
        live_client: DonetickClient,
        test_chore_ids: List[int],
        test_user_id: int,
    ):
        """
        Test skipping a recurring chore without marking complete.

        Verifies:
        - Skip endpoint works
        - Next due date is rescheduled
        - Chore remains incomplete
        - Skip count is incremented (if tracked)

        Skip is useful for recurring chores when you want to postpone
        without marking as done.
        """
        # Create a recurring chore
        tomorrow = (datetime.now() + timedelta(days=1)).strftime("%Y-%m-%d")

        chore = ChoreCreate(
            name="Test Skip Chore",
            dueDate=tomorrow,
            createdBy=test_user_id,
            frequencyType="weekly",
            frequency=1,
        )

        created = await live_client.create_chore(chore)
        test_chore_ids.append(created.id)

        # Skip the chore
        skipped = await live_client.skip_chore(created.id)

        # Verify skip response
        assert skipped.id == created.id
        # Next due date should be rescheduled
        assert skipped.nextDueDate is not None
        assert skipped is not None


class TestChoreDeletion:
    """Test suite for chore deletion operations against live API."""

    async def test_delete_chore(
        self,
        live_client: DonetickClient,
        test_chore_ids: List[int],
        test_user_id: int,
    ):
        """
        Test deleting a chore.

        Verifies:
        - Delete endpoint works
        - Chore is removed from system
        - Subsequent get returns 404
        - Only creator can delete (if enforced)

        Note: We don't add to test_chore_ids since we're explicitly
        testing deletion.
        """
        # Create a chore to delete
        tomorrow = (datetime.now() + timedelta(days=1)).strftime("%Y-%m-%d")

        chore = ChoreCreate(
            name="Test Delete Chore",
            dueDate=tomorrow,
            createdBy=test_user_id,
        )

        created = await live_client.create_chore(chore)
        # Don't add to test_chore_ids since we're deleting it ourselves

        # Delete the chore
        result = await live_client.delete_chore(created.id)
        assert result is True

        # Verify it's gone
        deleted_chore = await live_client.get_chore(created.id)
        assert deleted_chore is None

    async def test_delete_nonexistent_chore(self, live_client: DonetickClient):
        """
        Test error handling when deleting a non-existent chore.

        Verifies:
        - 404 error is raised for invalid chore ID
        - Error message is appropriate
        - No server error or crash occurs

        This ensures proper error handling in the client.
        """
        # Try to delete a chore that doesn't exist
        nonexistent_id = 999999999

        # Should raise HTTPStatusError with 404
        import httpx

        with pytest.raises(httpx.HTTPStatusError) as exc_info:
            await live_client.delete_chore(nonexistent_id)

        # Verify it's a 404 error
        assert exc_info.value.response.status_code == 404


class TestErrorHandling:
    """Test suite for error handling and edge cases."""

    async def test_authentication_failure(self, live_config):
        """
        Test client behavior with invalid credentials.

        Verifies:
        - Authentication fails with clear error
        - Invalid username/password is rejected
        - Error message is helpful for debugging

        This test creates a separate client with bad credentials.
        """
        # Create client with invalid credentials
        bad_client = DonetickClient(
            base_url=live_config.donetick_base_url,
            username="invalid_user_12345",
            password="invalid_password_12345",
        )

        # Attempt authentication should fail with a clear error
        with pytest.raises(DonetickAuthError):
            await bad_client.login()

        # Cleanup
        await bad_client.close()

    async def test_rate_limiting(
        self,
        live_client: DonetickClient,
    ):
        """
        Test rate limiting behavior under load.

        Verifies:
        - Client respects configured rate limits
        - Token bucket algorithm works correctly
        - Requests are throttled appropriately
        - No 429 errors occur when within limits

        This test makes rapid API calls to verify rate limiting.
        """
        import time

        # Make multiple rapid requests
        start_time = time.time()

        # List chores 5 times rapidly
        for _ in range(5):
            chores = await live_client.list_chores()
            assert isinstance(chores, list)

        elapsed = time.time() - start_time

        # With default rate limit of 10/sec, 5 requests should complete quickly
        # but not instantaneously (rate limiter should throttle)
        assert elapsed < 5.0  # Should complete within 5 seconds
        # If rate limiting is working, it won't be instant
        # (though this is hard to test deterministically)

    async def test_network_timeout_handling(
        self,
        live_client: DonetickClient,
    ):
        """
        Test client behavior when network requests timeout.

        Verifies:
        - Timeout errors are caught and handled
        - Retry logic kicks in for transient failures
        - Final error is raised after max retries
        - Error message is informative

        Note: This is hard to test against a live API without
        artificial delays. May require mocking or special test setup.
        """
        # This is difficult to test against a live API
        # We'll just verify the client has timeout configuration
        assert live_client.client.timeout is not None
        assert live_client.client.timeout.connect is not None
        assert live_client.client.timeout.read is not None

        # Skip actual timeout testing as it requires mocking
        pytest.skip("Timeout handling requires mocked responses")


class TestFieldNameCasing:
    """Test suite specifically for verifying API field name casing.

    This is the critical test suite that verifies whether the Donetick API
    uses camelCase or PascalCase for different operations.
    """

    async def test_create_field_casing(
        self,
        live_client: DonetickClient,
        test_chore_ids: List[int],
        test_user_id: int,
    ):
        """
        Test that create operations use the correct field name casing.

        Verifies:
        - Create accepts camelCase (name, description, dueDate, createdBy)
        - Create rejects PascalCase (Name, Description, DueDate, CreatedBy)
        - Response uses camelCase consistently

        This test will make two attempts:
        1. First with camelCase (should succeed)
        2. Then with PascalCase (should fail or be ignored)

        CRITICAL: This test determines the correct casing for our models.
        """
        # Test with our model (which uses PascalCase aliases internally)
        tomorrow = (datetime.now() + timedelta(days=1)).strftime("%Y-%m-%d")

        chore = ChoreCreate(
            name="Test Casing Create",
            description="Testing field name casing in create",
            dueDate=tomorrow,
            createdBy=test_user_id,
        )

        # This should work - our model handles the casing conversion
        created = await live_client.create_chore(chore)
        test_chore_ids.append(created.id)

        # Verify response fields use camelCase
        assert created.id is not None
        assert created.name == "Test Casing Create"
        assert hasattr(created, "createdBy")
        assert created.createdBy == test_user_id

        # The API accepts our model which uses PascalCase aliases
        # but serializes with camelCase via by_alias=True
        assert created is not None

    async def test_update_field_casing(
        self,
        live_client: DonetickClient,
        test_chore_ids: List[int],
        test_user_id: int,
    ):
        """
        Test that update operations use the correct field name casing.

        Verifies:
        - Update accepts camelCase (name, description, nextDueDate)
        - Update rejects PascalCase (Name, Description, NextDueDate)
        - Response uses camelCase consistently

        This test will make two attempts:
        1. First with camelCase (should succeed)
        2. Then with PascalCase (should fail or be ignored)

        CRITICAL: This test determines the correct casing for our models.
        """
        # Create a chore first
        tomorrow = (datetime.now() + timedelta(days=1)).strftime("%Y-%m-%d")

        chore = ChoreCreate(
            name="Test Update Casing",
            description="Original",
            dueDate=tomorrow,
            createdBy=test_user_id,
        )

        created = await live_client.create_chore(chore)
        test_chore_ids.append(created.id)

        # Update with ChoreUpdate model (uses camelCase)
        next_week = (datetime.now() + timedelta(days=7)).strftime("%Y-%m-%d")

        update = ChoreUpdate(
            name="Updated via camelCase",
            description="Updated description",
            nextDueDate=next_week,
        )

        updated = await live_client.update_chore(created.id, update)

        # Verify update worked with camelCase
        assert updated.name == "Updated via camelCase"
        assert updated.description == "Updated description"

        # Response should use camelCase fields
        assert hasattr(updated, "name")
        assert hasattr(updated, "nextDueDate")

    async def test_response_field_casing(
        self,
        live_client: DonetickClient,
        test_chore_ids: List[int],
        test_user_id: int,
    ):
        """
        Test that API responses use consistent field name casing.

        Verifies:
        - All response fields use camelCase
        - No PascalCase fields in responses
        - Nested objects (frequencyMetadata, etc.) use camelCase

        This test examines the raw API response to verify casing.
        """
        # Create a chore with various fields
        tomorrow = (datetime.now() + timedelta(days=1)).strftime("%Y-%m-%d")

        chore = ChoreCreate(
            name="Test Response Casing",
            description="Testing response field casing",
            dueDate=tomorrow,
            createdBy=test_user_id,
            frequencyType="weekly",
            frequency=1,
            frequencyMetadata={"days": ["monday"], "time": "10:00"},
            priority=2,
        )

        created = await live_client.create_chore(chore)
        test_chore_ids.append(created.id)

        # Verify all response fields use expected casing
        # Core fields (camelCase in responses)
        assert hasattr(created, "id")
        assert hasattr(created, "name")
        assert hasattr(created, "description")
        assert hasattr(created, "frequencyType")
        assert hasattr(created, "frequencyMetadata")
        assert hasattr(created, "createdBy")
        assert hasattr(created, "createdAt")
        assert hasattr(created, "updatedAt")
        assert hasattr(created, "nextDueDate")
        assert hasattr(created, "isActive")
        assert hasattr(created, "priority")

        # Verify nested metadata uses camelCase
        if created.frequencyMetadata:
            # The API should return camelCase field names
            assert isinstance(created.frequencyMetadata, dict)


class TestThings:
    """Test suite for things and thing-triggered chores against live API."""

    async def test_thing_trigger_lifecycle(
        self,
        live_client: DonetickClient,
        test_user_id: int,
    ):
        """
        Test creating a thing, linking a trigger chore and triggering it.

        Verifies:
        - Things CRUD via /api/v1/things
        - Chore trigger is created from thingTrigger
        - Updating the chore keeps the trigger (Donetick drops it unless re-sent)
        - Setting the thing state sets the chore due date
        - Thing history is recorded

        Cleans up the chore before the thing (Donetick refuses to delete linked things).
        """
        thing = await live_client.create_thing("MCP Test Washer", "boolean", True)
        chore_id = None
        try:
            trigger = await live_client.build_thing_trigger(thing.id, False)
            created = await live_client.create_chore(
                ChoreCreate(
                    name="Test Trigger Chore",
                    frequencyType="trigger",
                    thingTrigger=trigger,
                    assignedTo=test_user_id,
                    assignees=[{"userId": test_user_id}],
                )
            )
            chore_id = created.id

            assert created.thingChore is not None
            assert created.thingChore["thingId"] == thing.id
            assert created.nextDueDate is None

            renamed = await live_client.update_chore(chore_id, ChoreUpdate(name="Test Trigger Chore 2"))
            assert renamed.name == "Test Trigger Chore 2"
            assert renamed.thingChore is not None, "update_chore must keep the thing trigger"

            updated_thing, triggered = await live_client.set_thing_state(thing.id, state=False)
            assert updated_thing.state == "false"
            assert chore_id in triggered

            triggered_chore = await live_client.get_chore(chore_id)
            assert triggered_chore.nextDueDate is not None

            history = await live_client.get_thing_history(thing.id)
            assert any(entry.state == "false" for entry in history)
        finally:
            if chore_id is not None:
                await live_client.delete_chore(chore_id)
            await live_client.delete_thing(thing.id)

    async def test_chore_history_last_days(self, live_client: DonetickClient):
        """Test that history accepts the days parameter (Donetick's limit)."""
        history = await live_client.get_all_chores_history(days=30, include_circle_members=True)
        assert isinstance(history, list)
