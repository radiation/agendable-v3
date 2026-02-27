## Agendable

[![CI](https://github.com/radiation/agendable-v3/actions/workflows/ci.yml/badge.svg)](https://github.com/radiation/agendable-v3/actions/workflows/ci.yml)
[![CodeQL](https://github.com/radiation/agendable-v3/actions/workflows/codeql.yml/badge.svg)](https://github.com/radiation/agendable-v3/actions/workflows/codeql.yml)
[![Dependency Scan](https://github.com/radiation/agendable-v3/actions/workflows/dependency-scan.yml/badge.svg)](https://github.com/radiation/agendable-v3/actions/workflows/dependency-scan.yml)
[![Semgrep](https://github.com/radiation/agendable-v3/actions/workflows/semgrep.yml/badge.svg)](https://github.com/radiation/agendable-v3/actions/workflows/semgrep.yml)
[![Complexity](https://github.com/radiation/agendable-v3/actions/workflows/complexity.yml/badge.svg)](https://github.com/radiation/agendable-v3/actions/workflows/complexity.yml)
[![Actionlint](https://github.com/radiation/agendable-v3/actions/workflows/actionlint.yml/badge.svg)](https://github.com/radiation/agendable-v3/actions/workflows/actionlint.yml)
[![codecov](https://img.shields.io/badge/dynamic/json?url=https%3A%2F%2Fapi.codecov.io%2Fapi%2Fv2%2Fgithub%2Fradiation%2Frepos%2Fagendable-v3&query=%24.totals.coverage&suffix=%25&label=codecov)](https://codecov.io/gh/radiation/agendable-v3)
[![Python](https://img.shields.io/badge/python-3.14%2B-blue.svg)](https://www.python.org/downloads/)
[![Ruff](https://img.shields.io/badge/lint-ruff-46aef7.svg)](https://github.com/astral-sh/ruff)
[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](https://opensource.org/licenses/MIT)

Minimal app for tracking agenda items and tasks for recurring meetings (e.g. 1:1s), with reminder stubs (email/Slack).

### Run locally (SQLite)

- Install dependencies: `uv sync`
- Initialize DB (migrations): `uv run alembic upgrade head`
- Start the server: `uv run uvicorn agendable.app:app --reload`
- Open: `http://127.0.0.1:8000/`

First time: go to `/login` and sign in (new users are auto-provisioned in the MVP).

SQLite is the default via `AGENDABLE_DATABASE_URL=sqlite+aiosqlite:///./agendable.db`.

### Run with Docker + Postgres (live reload)

One-command startup (includes automatic migrations):

- Build + start: `docker compose up --build`
- Open: `http://127.0.0.1:8000/`
- Mailpit inbox UI: `http://127.0.0.1:8025/`
- Stop: `docker compose down`

Compose now runs a one-shot `migrate` service (`alembic upgrade head`) before starting both `web` and `reminder-worker`.

The compose setup includes:

- Postgres (`postgres:17`) with a persistent Docker volume (`postgres_data`)
- Mailpit (`axllent/mailpit`) for local email capture/testing
- `migrate` one-shot service that applies Alembic migrations before app startup
- `reminder-worker` service that runs `agendable run-reminders-worker` every 30s
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

### Deploy to GCP

For a production-oriented GCP deployment (Cloud Run web service, Cloud Run reminder job, Cloud SQL, Secret Manager), see:

- `docs/deploy-gcp.md`

### CLI

- Initialize DB (creates tables): `uv run agendable init-db`
- Run reminder sender stub: `uv run agendable run-reminders`

Email reminders can be enabled by configuring SMTP env vars:

- `AGENDABLE_SMTP_HOST`
- `AGENDABLE_SMTP_PORT` (default `587`)
- `AGENDABLE_SMTP_USERNAME` (optional)
- `AGENDABLE_SMTP_PASSWORD` (optional)
- `AGENDABLE_SMTP_FROM_EMAIL`
- `AGENDABLE_SMTP_USE_SSL` (default `false`)
- `AGENDABLE_SMTP_USE_STARTTLS` (default `true`)

Reminder scheduling defaults:

- `AGENDABLE_ENABLE_DEFAULT_EMAIL_REMINDERS` (default `true`)
- `AGENDABLE_DEFAULT_EMAIL_REMINDER_MINUTES_BEFORE` (default `60`)
- `AGENDABLE_REMINDER_WORKER_POLL_SECONDS` (default `60`)

Per-series override:

- When creating a series in the UI, set `Reminder lead (minutes)`.
- That value is used for auto-created reminders on both generated and manually added occurrences.

If SMTP is not configured, `run-reminders` uses a no-op sender for email reminders.

When default reminders are enabled, each new meeting occurrence automatically gets an email reminder row.

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

Session cookie hardening settings:

- `AGENDABLE_SESSION_COOKIE_NAME='agendable_session'`
- `AGENDABLE_SESSION_COOKIE_SAME_SITE='lax'` (`strict` recommended for production if your auth flow allows it)
- `AGENDABLE_SESSION_COOKIE_HTTPS_ONLY='true'` (set `true` in production behind HTTPS)
- `AGENDABLE_SESSION_COOKIE_MAX_AGE_SECONDS='1209600'` (default 14 days)

Auth + identity-linking rate limits:

- `AGENDABLE_AUTH_RATE_LIMIT_ENABLED='true'`
- `AGENDABLE_TRUST_PROXY_HEADERS='false'` (set `true` only when app traffic comes through a trusted reverse proxy)
- `AGENDABLE_LOGIN_RATE_LIMIT_IP_ATTEMPTS='10'`
- `AGENDABLE_LOGIN_RATE_LIMIT_IP_WINDOW_SECONDS='60'`
- `AGENDABLE_LOGIN_RATE_LIMIT_ACCOUNT_ATTEMPTS='5'`
- `AGENDABLE_LOGIN_RATE_LIMIT_ACCOUNT_WINDOW_SECONDS='60'`
- `AGENDABLE_OIDC_CALLBACK_RATE_LIMIT_IP_ATTEMPTS='20'`
- `AGENDABLE_OIDC_CALLBACK_RATE_LIMIT_IP_WINDOW_SECONDS='60'`
- `AGENDABLE_OIDC_CALLBACK_RATE_LIMIT_ACCOUNT_ATTEMPTS='10'`
- `AGENDABLE_OIDC_CALLBACK_RATE_LIMIT_ACCOUNT_WINDOW_SECONDS='60'`
- `AGENDABLE_IDENTITY_LINK_START_RATE_LIMIT_IP_ATTEMPTS='10'`
- `AGENDABLE_IDENTITY_LINK_START_RATE_LIMIT_IP_WINDOW_SECONDS='60'`
- `AGENDABLE_IDENTITY_LINK_START_RATE_LIMIT_ACCOUNT_ATTEMPTS='5'`
- `AGENDABLE_IDENTITY_LINK_START_RATE_LIMIT_ACCOUNT_WINDOW_SECONDS='60'`

Login rate limits are consumed on failed authentication outcomes (not successful logins).

### Logging

Runtime logging is configured via Python's built-in `logging` with environment-driven settings:

- `AGENDABLE_LOG_LEVEL='INFO'` (supports `DEBUG`, `INFO`, `WARNING`, `ERROR`, `CRITICAL`)
- `AGENDABLE_LOG_JSON='false'` (set to `true` for JSON log lines)
- `AGENDABLE_LOG_HTTP_REQUESTS='true'` (set to `false` to suppress per-request lifecycle logs)

Examples:

- Local development verbose logs:
	- `AGENDABLE_LOG_LEVEL='DEBUG'`
- Structured logs in container/staging:
	- `AGENDABLE_LOG_JSON='true'`

Every HTTP response includes an `X-Request-ID` header. If the request already provides one, it is reused; otherwise Agendable generates one.

### SSO groundwork

The app includes an `external_identities` table to map external identity provider subjects (OIDC `sub`, SAML NameID, etc.) to internal users.
This lets us add OAuth/OIDC and/or SAML later without changing the rest of the data model.

#### OIDC setup (optional)

Configure any OIDC provider (Google, Okta, Auth0, Keycloak, etc.) with callback URI:

- `http://127.0.0.1:8000/auth/oidc/callback`
- (optional) `http://localhost:8000/auth/oidc/callback`

To enable SSO, set:

- `AGENDABLE_OIDC_CLIENT_ID='...'`
- `AGENDABLE_OIDC_CLIENT_SECRET='...'`
- `AGENDABLE_OIDC_METADATA_URL='https://<your-provider>/.well-known/openid-configuration'`

Optional login-prompt behavior:

- `AGENDABLE_OIDC_AUTH_PROMPT='select_account'` (default)
	- Helps prevent immediate silent re-login after app logout by asking the IdP for account selection.
	- Set to `login` to force re-authentication each time.
	- Set to empty (`''`) to omit `prompt` entirely.

Optional restriction:

- `AGENDABLE_ALLOWED_EMAIL_DOMAIN='example.com'` (only allows `@example.com` users)

#### Managed OIDC testing (Auth0 / Okta / any OIDC provider)

Use the same OIDC vars above for any managed provider.

Optional callback diagnostics:

- `AGENDABLE_OIDC_DEBUG_LOGGING='true'`

When enabled, the app logs OIDC callback decisions (claim presence/verification, domain gate, auto-provision vs linking) without logging tokens or secrets.
Set it back to `false` (or unset it) after troubleshooting to reduce log noise.

Keep callback URI configured in your provider app/client as:

- `http://127.0.0.1:8000/auth/oidc/callback`

Notes:

- The app route path is `/auth/oidc/*`.
- Users can explicitly link an SSO identity from `Profile` via **Link SSO account**.
	- Password-based accounts must confirm current password before starting the link flow.
	- Unlinking is blocked when it would remove the user's only sign-in method.

#### Local OIDC testing with Keycloak (multiple test users)

This repo includes an optional Keycloak service for local SSO and user-management testing.

Start app stack + Keycloak profile:

- `docker compose --profile sso up --build`

Open Keycloak admin:

- `http://127.0.0.1:8081/`
- admin user: `admin`
- admin password: `admin`

Imported realm seed:

- realm: `agendable`
- client: `agendable-local`
- client secret: `agendable-local-secret`
- test users:
	- `alice@example.com` / `Password123!`
	- `bob@example.com` / `Password123!`

Then set app env vars (for local `.env` or compose override):

- `AGENDABLE_OIDC_CLIENT_ID='agendable-local'`
- `AGENDABLE_OIDC_CLIENT_SECRET='agendable-local-secret'`
- `AGENDABLE_OIDC_METADATA_URL='http://keycloak:8080/realms/agendable/.well-known/openid-configuration'` (inside Docker)

The compose file uses a browser-facing hostname (`127.0.0.1:8081`) plus Keycloak backchannel-dynamic URLs so app-to-Keycloak token exchange from the `web` container works.

If you change Keycloak hostname/env settings, recreate containers:

- `docker compose --profile sso up --build --force-recreate`

If running app outside Docker, use host URL instead:

- `AGENDABLE_OIDC_METADATA_URL='http://127.0.0.1:8081/realms/agendable/.well-known/openid-configuration'`

This gives you quick multi-user SSO validation without pilot users.

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
- `pytest` with coverage + JUnit test results upload

Security scanning workflows:

- `.github/workflows/codeql.yml`
	- Runs GitHub CodeQL on PRs, pushes to `main`, and weekly.
	- Surfaces findings in GitHub code scanning alerts / Security tab.
- `.github/workflows/dependency-scan.yml`
	- Exports locked dependencies from `uv.lock` and runs `pip-audit`.
	- Runs on PRs, pushes to `main`, and weekly.
	- Fails when known vulnerable package versions are detected.
- `.github/workflows/semgrep.yml`
	- Runs Semgrep (`p/default`) on PRs, pushes to `main`, and weekly.
	- Complements CodeQL with additional security/correctness rules.
	- Scope is tuned with `.semgrepignore` to avoid scanning generated/local artifacts.
	- Excludes two noisy rules in this stack:
		- `python.django.security.django-no-csrf-token.django-no-csrf-token`
		- `html.security.audit.missing-integrity.missing-integrity`

Future hardening TODOs:

- Add first-class CSRF protection for server-rendered POST forms.
- Replace CDN script tags with pinned SRI hashes or vendored static assets.
- `.github/workflows/complexity.yml`
	- Runs Xenon complexity gates on PRs and pushes to `main`.
	- Enforces thresholds: max-absolute `B`, max-modules `B`, max-average `A`.
- `.github/workflows/actionlint.yml`
	- Lints GitHub Actions workflow files on PRs and pushes to `main`.
	- Catches workflow syntax/authoring issues before merge.

Dependency automation:

- `.github/dependabot.yml`
	- Weekly updates for GitHub Actions and Python dependencies.
	- Keeps SHA-pinned action references current via Dependabot PRs.

Coverage is uploaded to Codecov using `.github/codecov.yml`, with ratcheting-style checks:

- `codecov/project` target is `auto` (do not regress overall coverage)
- `codecov/patch` target is `auto` (require new/changed code to maintain patch coverage)

Test results are uploaded to Codecov via `codecov/test-results-action`, which provides pass/fail test visibility alongside coverage data.

Recommended GitHub repo settings:

1. Enable branch protection for `main`.
2. Require pull requests before merging.
3. Require status checks to pass before merging:
	- `Lint, typecheck, test, coverage`
4. Require Codecov checks for PRs:
	- `codecov/project`
	- `codecov/patch`
5. Require security checks for PRs:
	- `CodeQL Analyze`
	- `pip-audit`
6. Require additional quality checks for PRs:
	- `Semgrep Scan`
	- `Xenon Complexity Gate`
	- `Actionlint`
	- `Xenon Complexity Gate`

For private repos, add `CODECOV_TOKEN` in GitHub Actions secrets if required by your Codecov setup.
For this public repo, tokenless uploads should work, but adding `CODECOV_TOKEN` is also valid and supported by the workflow.
