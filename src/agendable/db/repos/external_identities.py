from __future__ import annotations

import uuid

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from agendable.db.models import ExternalIdentity
from agendable.db.repos.base import BaseRepository


class ExternalIdentityRepository(BaseRepository[ExternalIdentity]):
    def __init__(self, session: AsyncSession) -> None:
        super().__init__(session, ExternalIdentity)

    async def get_by_provider_subject(self, provider: str, subject: str) -> ExternalIdentity | None:
        result = await self.session.execute(
            select(ExternalIdentity).where(
                ExternalIdentity.provider == provider,
                ExternalIdentity.subject == subject,
            )
        )
        return result.scalar_one_or_none()

    async def list_by_user_id(self, user_id: uuid.UUID) -> list[ExternalIdentity]:
        result = await self.session.execute(
            select(ExternalIdentity)
            .where(ExternalIdentity.user_id == user_id)
            .order_by(ExternalIdentity.provider.asc(), ExternalIdentity.created_at.asc())
        )
        return list(result.scalars().all())

    async def list_by_user_ids(self, user_ids: list[uuid.UUID]) -> list[ExternalIdentity]:
        if not user_ids:
            return []
        result = await self.session.execute(
            select(ExternalIdentity)
            .where(ExternalIdentity.user_id.in_(user_ids))
            .order_by(ExternalIdentity.user_id.asc(), ExternalIdentity.provider.asc())
        )
        return list(result.scalars().all())
