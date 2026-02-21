## Agendable

[![CI](https://github.com/radiation/agendable-v3/actions/workflows/ci.yml/badge.svg)](https://github.com/radiation/agendable-v3/actions/workflows/ci.yml)
[![codecov](https://codecov.io/gh/radiation/agendable-v3/graph/badge.svg)](https://codecov.io/gh/radiation/agendable-v3)
[![Python](https://img.shields.io/badge/python-3.14%2B-blue.svg)](https://www.python.org/downloads/)
[![Ruff](https://img.shields.io/badge/lint-ruff-46aef7.svg)](https://github.com/astral-sh/ruff)

Minimal app for tracking agenda items and tasks for recurring meetings (e.g. 1:1s), with reminder stubs (email/Slack).

### Run locally (SQLite)

- Install dependencies: `uv sync`
- Initialize DB (migrations): `uv run alembic upgrade head`
- Start the server: `uv run uvicorn agendable.app:app --reload`
- Open: `http://127.0.0.1:8000/`

First time: go to `/login` and sign in (new users are auto-provisioned in the MVP).

SQLite is the default via `AGENDABLE_DATABASE_URL=sqlite+aiosqlite:///./agendable.db`.

### Run with Docker + Postgres (live reload)

First time (or after new migrations):

- Start Postgres + apply migrations: `docker compose run --rm web alembic upgrade head`

Then run the app:

- Build + start: `docker compose up --build`
- Open: `http://127.0.0.1:8000/`
- Stop: `docker compose down`

The compose setup includes:

- Postgres (`postgres:17`) with a persistent Docker volume (`postgres_data`)
- A bind mount from local repo to container (`.:/app`)
- Live reload command in the app container:
	- `uvicorn agendable.app:app --host 0.0.0.0 --port 8000 --reload --reload-dir /app/src`

So local code changes under `src/` reload automatically without rebuilding the image.

If dependencies change, rebuild once:

- `docker compose up --build`

### Run in a long-lived environment (Postgres)

Set `AGENDABLE_DATABASE_URL` to an asyncpg URL, for example:

- `AGENDABLE_DATABASE_URL=postgresql+asyncpg://user:pass@host:5432/agendable`

Tables are managed by Alembic migrations.

### CLI

- Initialize DB (creates tables): `uv run agendable init-db`
- Run reminder sender stub: `uv run agendable run-reminders`

### Migrations (Alembic)

Recommended workflow (especially for Postgres / long-lived environments):

- Apply migrations: `uv run alembic upgrade head`
- Create a new migration (autogenerate): `uv run alembic revision --autogenerate -m "..."`

In long-lived environments, set `AGENDABLE_AUTO_CREATE_DB=false` and use Alembic instead of startup-time `create_all()`.

If you see `table users already exists` when running `alembic upgrade head`, it usually means the DB tables were created outside Alembic.
For a dev DB you can delete `agendable.db` and re-run `alembic upgrade head`. If you need to keep the DB contents, use `alembic stamp head`
*after* verifying the schema matches the current migrations.

For production, override the session secret:

- `AGENDABLE_SESSION_SECRET='...'`

### SSO groundwork

The app includes an `external_identities` table to map external identity provider subjects (OIDC `sub`, SAML NameID, etc.) to internal users.
This lets us add OAuth/OIDC and/or SAML later without changing the rest of the data model.

#### Google OIDC (optional)

Setup (Google Cloud Console):

- Create/select a Google Cloud project
- Configure OAuth consent screen (Internal for Workspace-only, External for Gmail/public testing)
- Create credentials: OAuth client ID → **Web application**
- Add an authorized redirect URI that matches how you access the app locally:
	- `http://127.0.0.1:8000/auth/google/callback`
	- (optional) `http://localhost:8000/auth/google/callback`

Note: `localhost` and `127.0.0.1` are treated as different origins by Google OAuth; add whichever you use.

To enable "Sign in with Google", set:

- `AGENDABLE_GOOGLE_CLIENT_ID='...'`
- `AGENDABLE_GOOGLE_CLIENT_SECRET='...'`

Optional restriction:

- `AGENDABLE_ALLOWED_EMAIL_DOMAIN='example.com'` (only allows `@example.com` users)

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

### GitHub Actions + coverage gates

This repo includes CI at `.github/workflows/ci.yml` that runs on PRs and pushes to `main`:

- `ruff check`
- `mypy --strict src`
- `pytest` with coverage report upload

Coverage is uploaded to Codecov using `.github/codecov.yml`, with ratcheting-style checks:

- `codecov/project` target is `auto` (do not regress overall coverage)
- `codecov/patch` target is `auto` (require new/changed code to maintain patch coverage)

Recommended GitHub repo settings:

1. Enable branch protection for `main`.
2. Require pull requests before merging.
3. Require status checks to pass before merging:
	- `Lint, typecheck, test, coverage`
4. Require Codecov checks for PRs:
	- `codecov/project`
	- `codecov/patch`

For private repos, add `CODECOV_TOKEN` in GitHub Actions secrets if required by your Codecov setup.
For this public repo, tokenless uploads should work, but adding `CODECOV_TOKEN` is also valid and supported by the workflow.
