## Agendable

Minimal app for tracking agenda items and tasks for recurring meetings (e.g. 1:1s), with reminder stubs (email/Slack).

### Run locally (SQLite)

- Install dependencies: `uv sync`
- Start the server: `uv run uvicorn agendable.app:app --reload`
- Open: `http://127.0.0.1:8000/`

First time: click **Bootstrap local user**, then create a meeting series.

SQLite is the default via `AGENDABLE_DATABASE_URL=sqlite+aiosqlite:///./agendable.db`.

### Run in a long-lived environment (Postgres)

Set `AGENDABLE_DATABASE_URL` to an asyncpg URL, for example:

- `AGENDABLE_DATABASE_URL=postgresql+asyncpg://user:pass@host:5432/agendable`

Tables are created on app startup (for now). In production we’ll likely move to Alembic migrations.

### CLI

- Initialize DB (creates tables): `uv run agendable init-db`
- Run reminder sender stub: `uv run agendable run-reminders`

### Dev tooling

- Format: `uv run ruff format .`
- Lint (incl. import sorting): `uv run ruff check . --fix`
- Typecheck: `uv run mypy .`

### Pre-commit hooks

One-time setup:

- `uv sync`
- `uv run pre-commit install`

Notes:

- `ruff` + `mypy` run on `pre-commit`.
- `pytest` is configured for `pre-push` (it’ll matter once we add tests).
