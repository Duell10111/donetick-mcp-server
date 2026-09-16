# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project Overview

MCP server for [Donetick](https://github.com/donetick/donetick) (chores/task manager). Fork of `jason1365/donetick-mcp-server`, maintained at `Duell10111/donetick-mcp-server`.

- **Targets Donetick v0.1.79** (full API `/api/v1/`, JWT auth) and **MCP SDK 2.x** (`MCPServer`)
- Python 3.11+, httpx (async), Pydantic 2, 36 MCP tools over stdio
- `UPGRADE_PLAN.md` documents the compatibility analysis and decisions (e.g. why no API token auth)

## Commands

```bash
# Setup
python -m venv venv && source venv/bin/activate
pip install -e ".[dev]"

# Tests (mocked HTTP; no Donetick instance or DONETICK_* env vars needed)
pytest -m "not live_api"
pytest tests/test_things.py::TestThingTools -v
pytest --cov=donetick_mcp --cov-report=html

# Live tests against a real TEST instance (credentials in .env, skipped otherwise)
pytest tests/integration -m live_api -v

# Lint / format
ruff check src tests
ruff format src

# Run (stdio); requires DONETICK_BASE_URL (https), DONETICK_USERNAME, DONETICK_PASSWORD
donetick-mcp-server            # or: python -m donetick_mcp.server

# Docker
docker compose build
docker run -i --rm --env-file .env donetick-mcp-server:latest
```

## Architecture

```
src/donetick_mcp/
├── server.py          # MCPServer("donetick-chores"), lifespan creates/closes DonetickClient, main()
├── tools/
│   ├── __init__.py    # register_tools(mcp)
│   ├── _common.py     # get_client(ctx), handle_errors decorator, annotation presets, to_json
│   ├── chores.py      # list/get/create/update/complete/skip/delete, priority, assignee, subtasks
│   ├── chore_actions.py  # archive, undo, approve/reject, timer, nudge, list_projects
│   ├── labels.py, circle.py, history.py, things.py
├── client.py          # DonetickClient: auth, _request (rate limit + retries), one method per endpoint
├── models.py          # Pydantic models + API format helpers (normalize_datetime, normalize_thing_state)
└── config.py          # Config from env/.env; validate() is called in main(), not on import
```

**Tool pattern** (schema is generated from the signature; `ctx` is injected and not part of the schema):

```python
@mcp.tool(annotations=READ_ONLY, structured_output=False)
@handle_errors
async def get_chore(ctx: Context, chore_id: Annotated[int, Field(description="...")]) -> str:
    """Docstring becomes the tool description."""
    chore = await get_client(ctx).get_chore(chore_id)
    ...
```

- Tools return formatted text (`structured_output=False`); every parameter needs a `Field(description=...)` (enforced by `tests/test_mcp_server.py`).
- `handle_errors` converts `httpx.HTTPStatusError`, `DonetickAuthError`, timeouts, `ValueError` and unexpected exceptions into `ToolError` with hints → result has `isError=true`. Raise `ToolError` directly for expected failures, `ValueError` for validation problems.
- Tool arguments are snake_case; the client and models use Donetick's camelCase field names.
- Annotation presets: `READ_ONLY`, `WRITE`, `IDEMPOTENT_WRITE`, `DESTRUCTIVE`.

**Client** (`client.py`):
- `_request(method, path, retry_server_errors=True, **httpx_kwargs)`: token bucket rate limit, retries timeouts/5xx with exponential backoff, waits on 429 (`Retry-After`), no retry on other 4xx, re-authenticates once on 401. Returns `{}` for empty bodies.
- Pass `retry_server_errors=False` for actions that must not run twice (nudge, undo, approve, reject, timer, archive): a timed out request may already have been applied.
- Responses are usually wrapped as `{"res": ...}`; unwrap with `data.get("res", data)`.

## Authentication

JWT via username/password only:
- `login()` → `POST /api/v1/auth/login`; response has `access_token`, `refresh_token`, `access_token_expiry` (legacy `token`/`expire` still supported).
- `_ensure_authenticated()` refreshes ~60s before expiry via `POST /api/v1/auth/refresh` (`{"refresh_token": ...}` in body), falls back to login. `_reauthenticate()` does the same after a 401.
- Login/refresh run under `_auth_lock`: Donetick revokes the whole session family when a refresh token is reused. Cookies are cleared after auth because Donetick reads the refresh token from the cookie before the body.
- MFA-enabled accounts get `{"mfaRequired": true}` instead of tokens and SSO-only instances return 403 → `DonetickAuthError` (not recoverable).

**API tokens (`secretkey` header) are intentionally not supported**: Donetick's `MultiAuthMiddleware` accepts them for chores, circles, things, projects, filters, but `/api/v1/labels` and `/api/v1/users/*` are JWT-only.

## Donetick API Quirks

Verify behavior against the Donetick source (`internal/chore/handler.go`, `internal/thing/handler.go`, `internal/auth/`) rather than assuming REST conventions.

**Chores**
- Paths must match the Gin routes exactly; a wrong trailing slash is answered with **301** (not followed by the client). With slash: `/api/v1/chores/`, `/api/v1/users/`. Without: `/api/v1/circles/members`, `/api/v1/labels`, `/api/v1/things`, `/api/v1/projects`.
- `GET /api/v1/chores/` omits inactive chores (archived and completed one-time chores) unless `includeArchived=true`.
- Create (`POST /api/v1/chores/`) returns `{"res": <id>}`; the client fetches the chore afterwards.
- Dates are Go `time.Time`: only RFC3339 with timezone is accepted. The request field is `nextDueDate` (a `dueDate` field is silently ignored). Use `normalize_datetime()`.
- `priority` must always be sent on create (Donetick dereferences it without nil check and panics).
- Update is `PUT /api/v1/chores/` with the **full chore object and the ID in the body**; response is `{"message": ...}`. `_chore_update_payload()` builds it from the fetched chore.
- `assignedTo` must be contained in `assignees`, which are objects: `[{"userId": 5}]` (a bare ID → 400).
- Every edit deletes the chore's thing link and only re-creates it from `thingTrigger` in the request. Chores are *read* with `thingChore` (`{thingId, choreId, triggerState, condition}`) but *written* with `thingTrigger` (`{thingID, triggerState, condition}`).
- Dedicated endpoints: `PUT /{id}/priority` (returns message only), `PUT /{id}/dueDate` (`{dueDate, updatedAt}`, records "rescheduled"), `PUT /{id}/subtask` (`{id, choreId, completedAt}`), `POST /{id}/skip`, `PUT /{id}/archive|unarchive` (creator only, otherwise **500**), `POST /{id}/undo` (own action, 5 minutes), `POST /{id}/approve|reject` (admin/manager; reject requires a JSON body), `PUT /{id}/start|pause`, `POST /{id}/nudge` (`{all_assignees, message}`).
- `POST /{id}/do` binds a JSON body: always send at least `{}`; `completedBy` (admins only), `notes`, `completedTime` go in the body.
- `PUT /{id}/assignee` only accepts users already in `assignees`, so `update_chore_assignee` uses the full update instead.
- Optimistic locking: `updatedAt` in a request is compared with the stored value; the full update omits it (`FIELDS_TO_REMOVE`).
- `frequencyType` values: `once daily weekly monthly yearly adaptive interval days_of_the_week day_of_the_month trigger no_repeat` (`interval_based` is mapped to `interval`). For `days_of_the_week`, the API returns partial `frequencyMetadata` but expects `unit`, `timezone`, `occurrences`, `weekNumbers` on update (added in `update_chore`).
- `POST /{id}/undo` restores due date and assignee, but for `once`/`no_repeat`/`trigger` chores Donetick explicitly clears `nextDueDate` afterwards.
- History: `GET /api/v1/chores/history?limit=<days>&members=true`; `status` is an integer (0 started, 1 completed, 2 skipped, 3 pending approval, 4 rejected, 5 missed, 6 rescheduled), note field is `notes`.
- `GET /{id}/details` returns only a subset of chore fields (no `frequency`, `circleId`, timestamps).
- Chore `status`: 0 none, 1 in progress, 2 paused, 3 pending approval.
- There is no `deadlineOffset` in Donetick.

**Things** (`/api/v1/things`)
- Things are private to their owner (`userID`), not shared with the circle.
- Types `text`, `number` (integers), `boolean` (`"true"`/`"false"`); state is always a string. `action` exists in the model but fails validation.
- `PUT /api/v1/things` has the ID in the body. There is no single-thing GET in the full API; `get_thing()` filters the list.
- `PUT /{id}/state?value=...` evaluates triggers (`eq` default, `neq`, `gt/lt/gte/lte` numeric): matching chores **without due date** become due now. Donetick crashes on unknown IDs here, so the client checks the thing first. This update does not bump the chore's `syncVersion` (Donetick bug), so sync-based apps may not see the new due date immediately.
- The list endpoint does not include `thingChores`; the state update response does.
- `GET /{id}/history?offset=` requires `offset`, returns 10 entries per page. `DELETE /{id}` returns 405 while chores are linked.
- Create chores with triggers only after validating the thing (`build_thing_trigger()`): Donetick saves the chore before linking the thing and returns an error afterwards.

**Users**
- `GET /api/v1/users/` and `/users/profile` return user objects without circle role, points or active flag (only `disabled`); role and points come from `GET /api/v1/circles/members`, storage usage from `GET /api/v1/users/storage` (`{used, total}` in bytes, per circle).
- User JSON mixes casing: `circleID`, `created_at`, `updated_at`, `notification_target`, `webhookURL`.

**Other**
- `GET /api/v1/projects` returns a plain array (no `res` wrapper).
- Donetick has no panic recovery middleware: invalid requests can end in a dropped connection instead of a 4xx.

## Testing

- HTTP is mocked with `pytest-httpx` against `https://donetick.test`; `tests/conftest.py` patches the global config (autouse), so no env vars are needed.
- Tool tests use the `call_tool(name, args)` fixture: it runs the tool through an in-process `mcp.Client`, returns the content list with `.is_error`, and registers a reusable optional login mock. Each call opens a new session (new `DonetickClient`, new login).
- pytest-httpx matches responses in registration order and consumes non-reusable ones. A reusable mock registered earlier wins over later ones for the same URL; tests that need a special login response (e.g. MFA) must not use `call_tool` (see `tests/test_auth.py`).
- Assert request payloads with `match_json=` or `httpx_mock.get_request(...)`; unrequested non-optional mocks fail the test.
- Test files: `test_client.py` (client methods), `test_server.py` (tools), `test_mcp_server.py` (schemas, annotations, errors, lifespan), `test_auth.py`, `test_api_compat.py` (Donetick formats), `test_things.py`, `test_chore_actions.py`, `test_models.py`, `test_transformations.py`, `test_notification_transform.py`, `test_performance.py`, `integration/test_live_api.py` (marked `live_api`).

## Adding a Tool

1. Client method in `client.py` (check the Donetick handler for body/response format; decide `retry_server_errors`).
2. Models in `models.py` if the response needs parsing.
3. Tool in the matching `tools/*.py` module: `@mcp.tool(annotations=..., structured_output=False)` + `@handle_errors`, `Annotated` parameters with descriptions, docstring.
4. Tests: client test with payload assertions, tool test via `call_tool`; update the tool count in `tests/test_server.py::test_list_tools`.
5. Update the tool tables in `README.md` and `CHANGELOG.md`.

## Conventions

- Temporary scripts and analysis files go to `tmp/` (gitignored); permanent tests to `tests/`.
- Don't reference line numbers in docs or comments; they go stale.
- Keep logs on stderr; stdout belongs to the stdio transport.
- Version lives in `pyproject.toml` and `src/donetick_mcp/__init__.py` (reported as MCP server version).
