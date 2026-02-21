from __future__ import annotations

from collections.abc import Mapping
from typing import Any, Generic, TypeVar

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.sql.elements import ColumnElement

from agendable.db.models import Base

ModelT = TypeVar("ModelT", bound=Base)


class BaseRepository(Generic[ModelT]):  # noqa: UP046
    def __init__(self, session: AsyncSession, model: type[ModelT]) -> None:
        self.session = session
        self.model = model

    async def add(self, obj: ModelT, *, flush: bool = True) -> ModelT:
        self.session.add(obj)
        if flush:
            await self.session.flush()  # assigns PKs, etc.
        return obj

    async def get(self, id_: Any) -> ModelT | None:
        return await self.session.get(self.model, id_)

    async def first_where(self, *predicates: ColumnElement[bool]) -> ModelT | None:
        stmt = select(self.model).where(*predicates).limit(1)
        result = await self.session.execute(stmt)
        return result.scalars().first()

    async def one_where(self, *predicates: ColumnElement[bool]) -> ModelT:
        stmt = select(self.model).where(*predicates)
        result = await self.session.execute(stmt)
        return result.scalars().one()

    async def list(self, *, offset: int = 0, limit: int = 100) -> list[ModelT]:
        stmt = select(self.model).offset(offset).limit(limit)
        result = await self.session.execute(stmt)
        return list(result.scalars().all())

    async def delete(self, obj: ModelT, *, flush: bool = True) -> None:
        await self.session.delete(obj)
        if flush:
            await self.session.flush()

    async def patch(self, obj: ModelT, changes: Mapping[str, Any], *, flush: bool = True) -> ModelT:
        for k, v in changes.items():
            if v is None:
                continue
            setattr(obj, k, v)
        if flush:
            await self.session.flush()
        return obj

    async def commit(self) -> None:
        await self.session.commit()

    async def rollback(self) -> None:
        await self.session.rollback()
