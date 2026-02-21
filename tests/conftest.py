from __future__ import annotations

from collections.abc import AsyncIterator
from pathlib import Path

import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)

import agendable.db as db
from agendable.app import create_app
from agendable.db.models import Base


@pytest.fixture
async def test_engine(tmp_path: Path) -> AsyncIterator[AsyncEngine]:
    db_path = tmp_path / "test.db"
    engine = create_async_engine(f"sqlite+aiosqlite:///{db_path}", future=True)

    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

    db.SessionMaker = async_sessionmaker(engine, expire_on_commit=False)

    yield engine

    await engine.dispose()


@pytest.fixture
async def client(test_engine: AsyncEngine) -> AsyncIterator[AsyncClient]:
    _ = test_engine
    app = create_app()
    transport = ASGITransport(app=app)

    async with AsyncClient(transport=transport, base_url="http://testserver") as c:
        yield c


@pytest.fixture
async def db_session(test_engine: AsyncEngine) -> AsyncIterator[AsyncSession]:
    _ = test_engine
    async with db.SessionMaker() as session:
        yield session
