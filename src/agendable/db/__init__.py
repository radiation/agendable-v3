from __future__ import annotations

from collections.abc import AsyncIterator

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from agendable.db import session as _session

create_engine = _session.create_engine
create_sessionmaker = _session.create_sessionmaker
engine = _session.engine

# NOTE: keep SessionMaker at the package level so tests can monkeypatch
# `agendable.db.SessionMaker` and have `get_session()` pick it up.
SessionMaker: async_sessionmaker[AsyncSession] = _session.SessionMaker


async def get_session() -> AsyncIterator[AsyncSession]:
    async with SessionMaker() as session:
        yield session


__all__ = [
    "SessionMaker",
    "create_engine",
    "create_sessionmaker",
    "engine",
    "get_session",
]
