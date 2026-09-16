# Live API Integration Tests

This directory contains integration tests that interact with a **real Donetick instance**. These tests are designed to verify end-to-end functionality and should be run separately from the mocked unit tests.

## Important Warnings

- **DO NOT** run these tests against a production Donetick instance
- **DO NOT** run these tests with real user data
- Tests will create, modify, and delete chores and things in your instance
- All test data is automatically cleaned up, but unexpected failures could leave test chores

## Prerequisites

1. **A running Donetick instance** (accessible via HTTPS)
2. **Valid credentials** in `.env` file:
   ```bash
   DONETICK_BASE_URL=https://your-test-instance.com
   DONETICK_USERNAME=your_test_username
   DONETICK_PASSWORD=your_test_password
   DONETICK_TEST_USER_ID=1  # Optional, defaults to 1
   ```
3. **Test user permissions**: The test user should have permissions to create, update, and delete chores
4. **Password login without MFA**: the client authenticates with username/password (JWT); accounts with MFA and SSO-only instances are not supported

## Running Live Tests

### Run all live API tests
```bash
pytest tests/integration/ -m live_api -v
```

### Run a specific test file
```bash
pytest tests/integration/test_live_api.py -m live_api -v
```

### Run a specific test
```bash
pytest "tests/integration/test_live_api.py::TestChoreCreation::test_create_simple_chore" -m live_api -v
```

### Run with detailed output
```bash
pytest tests/integration/ -m live_api -v -s
```

## Excluding Live Tests

Live tests are **skipped** when `DONETICK_BASE_URL`, `DONETICK_USERNAME` or `DONETICK_PASSWORD` are not set. If your `.env` contains credentials, exclude them explicitly:

```bash
# Run only unit tests (skip live API tests)
pytest tests/ -m "not live_api"
```

The `--strict-markers` flag in `pyproject.toml` ensures you can't accidentally run unmarked live tests.

## Test Structure

### Test Files

- `__init__.py` - Package documentation
- `conftest.py` - Shared fixtures for live testing
- `test_live_api.py` - Main integration test suite
- `README.md` - This file

### Test Suites

1. **TestChoreCreation** - Tests for creating chores with various configurations
2. **TestChoreRetrieval** - Tests for listing and getting chores
3. **TestChoreUpdate** - Tests for updating chore fields
4. **TestChoreCompletion** - Tests for completing and skipping chores
5. **TestChoreDeletion** - Tests for deleting chores
6. **TestErrorHandling** - Tests for error scenarios and edge cases
7. **TestFieldNameCasing** - Critical tests for API field name casing
8. **TestThings** - Things CRUD, thing-triggered chores (trigger kept on update, state change sets due date) and history

### Fixtures

- `live_config` - Loads configuration from environment variables
- `live_client` - Creates authenticated DonetickClient instance
- `test_chore_ids` - Tracks test chore IDs for automatic cleanup
- `test_user_id` - Provides test user ID from environment

## Test Data Cleanup

All test chores are automatically deleted after tests complete via the `test_chore_ids` fixture. `TestThings` cleans up its chore and thing itself (the chore must be deleted before the thing). To use the fixture in a test:

```python
async def test_example(live_client, test_chore_ids, test_user_id):
    # Create a test chore
    chore = await live_client.create_chore(...)

    # Register it for cleanup
    test_chore_ids.append(chore.id)

    # ... test logic ...
    # Cleanup happens automatically after test completes
```

## What These Tests Verify

1. **Authentication**
   - JWT token acquisition and management
   - Invalid credentials raise `DonetickAuthError`

2. **API Operations**
   - Create, read, update, delete operations
   - List and filtering operations
   - Specialized endpoints (priority, assignee, skip)

3. **Field Name Casing** (Critical!)
   - Verify whether API uses camelCase or PascalCase
   - Test both create and update operations
   - Validate response field casing

4. **Error Handling**
   - 404 errors for non-existent resources
   - 400 errors for invalid data
   - Authentication failures
   - Rate limiting behavior

5. **Data Integrity**
   - Created data matches input
   - Updates are applied correctly
   - Related data (sub-tasks, labels) works properly

## Field Name Casing Tests

The `TestFieldNameCasing` suite is particularly important for verifying the correct field name format:

```python
# These tests will determine if the API uses:
# - camelCase: name, description, dueDate, createdBy
# - PascalCase: Name, Description, DueDate, CreatedBy

async def test_create_field_casing(...):
    # Try camelCase (should succeed)
    # Try PascalCase (should fail or be ignored)

async def test_update_field_casing(...):
    # Try camelCase (should succeed)
    # Try PascalCase (should fail or be ignored)
```

Results from these tests will inform model definitions in `src/donetick_mcp/models.py`.

## Troubleshooting

### Tests are skipped

If tests are skipped with messages like "DONETICK_BASE_URL not set", verify your `.env` file:

```bash
# Check environment variables are loaded
python -c "from donetick_mcp.config import Config; c = Config(); print(c.donetick_base_url)"
```

### Authentication failures

- Verify credentials are correct
- Check that the Donetick instance is accessible
- Ensure HTTPS is used (HTTP is rejected for security)

### Rate limiting errors

If you see 429 errors, either:
- Reduce test concurrency
- Increase rate limits in `.env`:
  ```bash
  RATE_LIMIT_PER_SECOND=5.0
  RATE_LIMIT_BURST=10
  ```

### Cleanup failures

If test chores aren't deleted:
- Check test user has delete permissions
- Verify chores were created by the test user (only creator can delete)
- Manually clean up via Donetick UI if needed

## Development Guidelines

### Adding New Tests

1. Create test function in appropriate test class
2. Mark with `@pytest.mark.live_api` (or use `pytestmark` at module level)
3. Use fixtures: `live_client`, `test_chore_ids`, `test_user_id`
4. Register created chores: `test_chore_ids.append(chore.id)`
5. Add comprehensive docstring explaining what's verified

### Test Naming

- Use descriptive names: `test_create_chore_with_frequency`
- Follow pattern: `test_<operation>_<scenario>`
- Group related tests in classes

### Test Independence

- Each test should be independent (no dependencies)
- Use fixtures for setup/teardown
- Don't rely on state from other tests
- Clean up all test data

## CI/CD Integration

These tests are **not** run in CI/CD by default to avoid:
- Requiring live Donetick instance in CI
- Exposing credentials in CI environment
- Potential conflicts with multiple test runs

To run in CI, you would need:
1. Test Donetick instance dedicated for CI
2. Secure credential management (GitHub Secrets, etc.)
3. Separate CI workflow for integration tests
4. Proper test isolation to prevent conflicts

## Future Enhancements

- [ ] Add tests for label operations
- [ ] Add tests for circle member operations
- [ ] Add performance/load testing
- [ ] Add tests for concurrent operations
- [ ] Add tests for webhook notifications (if supported)
- [ ] Add visual regression tests (if UI testing added)
