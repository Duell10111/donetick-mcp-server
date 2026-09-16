# Donetick MCP Server

[![Python 3.11+](https://img.shields.io/badge/python-3.11+-blue.svg)](https://www.python.org/downloads/)
[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](https://opensource.org/licenses/MIT)
[![MCP SDK 2.x](https://img.shields.io/badge/MCP%20SDK-2.x-blue.svg)](https://github.com/modelcontextprotocol/python-sdk)

A [Model Context Protocol](https://modelcontextprotocol.io) (MCP) server for [Donetick](https://donetick.com), the open source chores and task manager. It lets Claude and other MCP clients manage chores, labels, projects and things of your Donetick instance.

> **Fork notice:** This is a fork of [jason1365/donetick-mcp-server](https://github.com/jason1365/donetick-mcp-server), updated for Donetick v0.1.79 and MCP SDK 2.x. The PyPI package `donetick-mcp-server` is the upstream version; install this fork from Git (see below).

## Features

- **36 MCP tools** for chores, chore actions (archive, undo, approval, timer, nudge), labels, projects, circle members, history and things
- **Things integration**: create things (text, number, boolean), set their state and trigger chores from it, e.g. from Home Assistant
- **Natural language inputs** for `create_chore`: usernames, label names, days of the week, time of day, reminders, sub-task names
- **Donetick v0.1.79 compatible**: request formats verified against the Donetick source (RFC3339 due dates, assignee objects, thing triggers, history status)
- **JWT authentication** with refresh tokens, serialized re-authentication and clear errors for MFA-enabled accounts and SSO-only instances
- **MCP SDK 2.x** (`MCPServer`): schemas generated from type hints, tool annotations (read-only, destructive, idempotent), errors returned with `isError`
- **Rate limiting and retries**: token bucket rate limiter, exponential backoff for 5xx/timeouts, no automatic retries for actions that must not run twice (nudge, undo, approval)
- **Docker image** running as non-root user

## Requirements

- A Donetick instance reachable via **HTTPS**
- A Donetick account with **username and password**
  - The account must **not have MFA enabled** (the server cannot enter TOTP codes)
  - The instance must allow **password login** (instances with `disable_password_auth` / SSO-only are not supported)
- [uv](https://docs.astral.sh/uv/) for the `uvx` setup, or Python 3.11+, or Docker

## Installation

### Option 1: uvx from Git (recommended)

No manual installation needed; `uvx` builds and runs the server from the repository.

**Claude Code:**

```bash
claude mcp add donetick \
  --env DONETICK_BASE_URL=https://donetick.example.com \
  --env DONETICK_USERNAME=your_username \
  --env DONETICK_PASSWORD=your_password \
  -- uvx --from git+https://github.com/Duell10111/donetick-mcp-server donetick-mcp-server
```

**Claude Desktop** (`claude_desktop_config.json`):

```json
{
  "mcpServers": {
    "donetick": {
      "command": "uvx",
      "args": [
        "--from",
        "git+https://github.com/Duell10111/donetick-mcp-server",
        "donetick-mcp-server"
      ],
      "env": {
        "DONETICK_BASE_URL": "https://donetick.example.com",
        "DONETICK_USERNAME": "your_username",
        "DONETICK_PASSWORD": "your_password"
      }
    }
  }
}
```

Config file location: macOS `~/Library/Application Support/Claude/claude_desktop_config.json`, Windows `%APPDATA%\Claude\claude_desktop_config.json`. Restart Claude Desktop after changes.

### Option 2: Docker

```bash
git clone https://github.com/Duell10111/donetick-mcp-server.git
cd donetick-mcp-server
cp .env.example .env   # fill in your credentials
docker compose build
```

MCP clients start the container per session over stdio:

```json
{
  "mcpServers": {
    "donetick": {
      "command": "docker",
      "args": [
        "run", "-i", "--rm",
        "--env-file", "/absolute/path/to/donetick-mcp-server/.env",
        "donetick-mcp-server:latest"
      ]
    }
  }
}
```

### Option 3: pip

```bash
git clone https://github.com/Duell10111/donetick-mcp-server.git
cd donetick-mcp-server
python -m venv venv && source venv/bin/activate
pip install .

donetick-mcp-server   # or: python -m donetick_mcp.server
```

Use `"command": "/path/to/venv/bin/donetick-mcp-server"` with the `env` block from option 1 in your MCP client configuration.

## Configuration

| Variable | Required | Default | Description |
|----------|----------|---------|-------------|
| `DONETICK_BASE_URL` | Yes | – | Donetick instance URL (must use HTTPS) |
| `DONETICK_USERNAME` | Yes | – | Donetick username |
| `DONETICK_PASSWORD` | Yes | – | Donetick password |
| `LOG_LEVEL` | No | `INFO` | `DEBUG`, `INFO`, `WARNING`, `ERROR` (logs go to stderr) |
| `RATE_LIMIT_PER_SECOND` | No | `10.0` | Requests per second |
| `RATE_LIMIT_BURST` | No | `10` | Burst capacity |

Variables can also be set in a `.env` file in the working directory.

### Authentication

The server logs in with username and password (`POST /api/v1/auth/login`) and keeps the tokens in memory only:

- The access token is refreshed with the refresh token shortly before it expires, and again on a `401`; a new login is only done if the refresh fails.
- Login and refresh are serialized, because Donetick revokes all sessions of a login when a refresh token is reused.

**Why no API token?** Donetick's long-lived API tokens (`secretkey` header) are not accepted for `/api/v1/labels` and `/api/v1/users/*`. With a token, 6 of the tools (label management, user list, profile) would not work, so the server uses JWT authentication for now.

## Available Tools

### Chores

| Tool | Description | Key parameters |
|------|-------------|----------------|
| `list_chores` | List chores | `filter_active`, `assigned_to_user_id`, `detail_level` (`brief`/`full`) |
| `get_chore` | Chore details incl. sub-tasks and thing trigger | `chore_id` |
| `create_chore` | Create a chore | `name`, `due_date`, `frequency_type`, `days_of_week`, `time_of_day`, `timezone`, `usernames`, `label_names`, `priority`, `points`, `subtask_names`, `remind_minutes_before`, `thing_id`, `project_id`, … |
| `update_chore` | Change chore fields (only given fields change) | `chore_id`, `name`, `description`, `next_due_date`, `priority`, `frequency_type`, `is_private`, `thing_id`, `remove_thing_trigger`, … |
| `complete_chore` | Mark as done | `chore_id`, `notes`, `completed_at`, `completed_by` (admins) |
| `skip_chore` | Skip the current occurrence | `chore_id` |
| `update_chore_priority` | Set priority 0–4 | `chore_id`, `priority` |
| `update_chore_assignee` | Make a user the only assignee | `chore_id`, `user_id` |
| `update_subtask_completion` | Check/uncheck a sub-task | `chore_id`, `subtask_id`, `completed` |
| `delete_chore` | Delete permanently (creator only) | `chore_id` |

### Chore actions and projects

| Tool | Description | Key parameters |
|------|-------------|----------------|
| `list_archived_chores` | List archived chores | – |
| `archive_chore` / `unarchive_chore` | Archive or restore (creator only) | `chore_id` |
| `undo_chore_action` | Undo your last completion, skip, approval submission or rejection (within 5 minutes) | `chore_id` |
| `approve_chore` / `reject_chore` | Decide on completions pending approval (admins and managers) | `chore_id`, `notes` (reject) |
| `start_chore_timer` / `pause_chore_timer` | Time tracking | `chore_id` |
| `nudge_chore` | Push reminder to the assignee(s) | `chore_id`, `all_assignees`, `message` |
| `list_projects` | List projects of the circle | – |

### Labels, circle and history

| Tool | Description | Key parameters |
|------|-------------|----------------|
| `list_labels` | List labels | – |
| `create_label` / `update_label` | Create or change a label | `name`, `color`, `label_id` (update) |
| `delete_label` | Delete a label | `label_id` |
| `get_circle_members` | Members with user IDs, roles and points | – |
| `list_circle_users` | Users of the circle | – |
| `get_user_profile` | Your profile | – |
| `get_chore_history` | History of one chore (completed, skipped, rescheduled, …) | `chore_id` |
| `get_all_chores_history` | History of the last days | `days` (default 7), `include_circle_members` |
| `get_chore_details` | Statistics of a chore | `chore_id` |

### Things

Things are named states (text, number or boolean) that can make chores due. They are private to their owner.

| Tool | Description | Key parameters |
|------|-------------|----------------|
| `list_things` | List your things | – |
| `create_thing` | Create a thing | `name`, `type` (`text`/`number`/`boolean`), `state` |
| `update_thing` | Rename or change type (does not trigger chores) | `thing_id`, `name`, `type`, `state` |
| `set_thing_state` | Set state and evaluate chore triggers | `thing_id`, `state` or `increment` |
| `get_thing_history` | State history (10 entries per page) | `thing_id`, `offset` |
| `delete_thing` | Delete (only without linked chores) | `thing_id` |

A chore is linked to a thing with `thing_id`, `thing_trigger_state` and `thing_trigger_condition` (`eq`, `neq`, or `gt`/`lt`/`gte`/`lte` for number things). When the thing's state matches, a chore without due date becomes due now.

### Example prompts

```
Create a chore "Take out trash" every Monday and Thursday at 19:00 for Alice with a reminder 15 minutes before
Mark chore 42 as done with the note "also cleaned the bin"
Create a boolean thing "Washing machine running" and a chore "Empty washing machine" that becomes due when it is false
What did everyone in the household do in the last 14 days?
```

## Development

```bash
python -m venv venv && source venv/bin/activate
pip install -e ".[dev]"

pytest -m "not live_api"          # mocked tests, no Donetick instance or env vars needed
pytest --cov=donetick_mcp          # with coverage
ruff check src tests
```

**Live API tests** run against a real (test!) Donetick instance configured in `.env`:

```bash
pytest tests/integration -m live_api -v
```

See [tests/integration/README.md](tests/integration/README.md). Without credentials these tests are skipped.

### Project structure

```
src/donetick_mcp/
├── server.py          # MCPServer setup, lifespan, entry point
├── tools/             # MCP tools by domain (chores, chore_actions, labels, circle, history, things)
├── client.py          # Donetick API client: auth, rate limiting, retries, endpoints
├── models.py          # Pydantic models and API format helpers
└── config.py          # Environment configuration
tests/                 # pytest suite (mocked HTTP), integration/ for live tests
```

## Troubleshooting

| Problem | Solution |
|---------|----------|
| `Failed to start server: Configuration validation failed` | Set `DONETICK_BASE_URL`, `DONETICK_USERNAME` and `DONETICK_PASSWORD`; the URL must start with `https://` |
| `Login failed: invalid username or password` | Check the credentials by logging in to the Donetick web UI |
| `... multi-factor authentication enabled ...` | Use an account without MFA for the MCP server |
| `... password authentication is disabled ... (SSO-only)` | The instance only allows SSO; username/password login is required |
| `Permission denied (...)` | The action needs other rights, e.g. only creators can delete or archive, only admins can approve |
| `Rate limit exceeded` | Lower `RATE_LIMIT_PER_SECOND` |
| Tools missing in the client | Restart the client; check its MCP logs; run the server command manually to see startup errors |

Debug logging: set `LOG_LEVEL=DEBUG` to log every request, retry and update payload to stderr. Debug logs contain full request URLs and chore data, so do not share them unredacted.

## Security

- Credentials only in environment variables or `.env` (gitignored); tokens are kept in memory only
- HTTPS is enforced and certificates are verified
- Unexpected errors are logged on the server; the model only gets a generic message without internal details
- The Docker image runs as a non-root user

## License

MIT License, see [LICENSE](LICENSE).

## Acknowledgments

- [jason1365/donetick-mcp-server](https://github.com/jason1365/donetick-mcp-server), the original project
- [Donetick](https://github.com/donetick/donetick)
- [Model Context Protocol](https://modelcontextprotocol.io)
