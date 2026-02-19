# Copilot instructions (Agendable)

This repo is a Python web app for tracking agenda items and tasks for recurring meetings (e.g., 1:1s), with reminders.

## Tech stack

- FastAPI (async)
- Server-rendered HTML with Jinja2 templates + HTMX
- SQLAlchemy 2.0 ORM (async engine)
- SQLite for local dev/testing; Postgres in long-lived environments (switch via `AGENDABLE_DATABASE_URL`)

## Coding guidelines

- Prefer small, typed modules under `src/agendable/`.
- Keep routes thin; put DB/session helpers in `agendable/db.py`.
- Use SQLAlchemy 2.0 typed ORM (`Mapped[...]`, `mapped_column`, `DeclarativeBase`).
- Validate inputs using Pydantic models or FastAPI form params.
- Maintain strict typing (`mypy --strict`) and lint/format with Ruff.
- Do not add heavy frontend frameworks; HTMX-first. Alpine.js may be introduced later.

## UX constraints

- Keep UI minimal and functional (no extra pages or “nice-to-haves”).
- Prefer single-purpose screens: list meeting series, view series detail, capture agenda/tasks.

## Reminders

- Reminder sending integrations (Slack/email) should be stubbed behind a small interface; avoid hard-coding provider specifics into routes.
- Never log secrets (Slack webhooks, SMTP creds, etc.).
