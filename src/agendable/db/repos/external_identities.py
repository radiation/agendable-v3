from __future__ import annotations

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
