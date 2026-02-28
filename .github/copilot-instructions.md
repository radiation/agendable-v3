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
- For refactors that move/delete files, prefer `git mv` and `git rm` when possible to preserve clearer history and rename tracking.

## UX constraints

- Optimize for clarity, speed, and confidence during real meetings; richer UX is acceptable when it reduces friction or user error.
- Keep primary workflows focused, but allow multi-state/interactive screens when they materially improve in-meeting use.
- Prefer progressive enhancement with HTMX-first patterns; add real-time collaboration updates (polling/SSE/WebSocket) where shared agenda/task editing benefits both participants.
- Avoid unnecessary feature sprawl, but do not block high-value UX improvements behind “minimal-only” constraints.

## Reminders

- Reminder sending integrations (Slack/email) should be stubbed behind a small interface; avoid hard-coding provider specifics into routes.
- Never log secrets (Slack webhooks, SMTP creds, etc.).

## Next UX priorities

- Shared live meeting view: prioritize real-time agenda/task updates for both participants during a meeting.
- Meeting-mode usability: optimize occurrence detail for in-meeting operation (fast capture, low cognitive load, minimal context switching).
- Collaboration cues: add lightweight presence/freshness signals (e.g., last-updated moments, participant activity hints) when useful.
- Safe progressive rollout: prefer HTMX-first incremental enhancements with clear fallback behavior.
