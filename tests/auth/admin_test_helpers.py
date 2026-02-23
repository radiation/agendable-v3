from __future__ import annotations

import uuid

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from agendable.auth import hash_password
from agendable.db.models import User, UserRole


async def promote_signed_in_user_to_admin(
    db_session: AsyncSession,
    *,
    email: str,
) -> User:
    user = (await db_session.execute(select(User).where(User.email == email))).scalar_one()
    user.role = UserRole.admin
    await db_session.commit()
    return user


async def create_user(
    db_session: AsyncSession,
    *,
    email: str,
    first_name: str,
    last_name: str,
    password: str,
) -> User:
    user = User(
        email=email,
        first_name=first_name,
        last_name=last_name,
        display_name=f"{first_name} {last_name}",
        timezone="UTC",
        password_hash=hash_password(password),
    )
    db_session.add(user)
    await db_session.commit()
    await db_session.refresh(user)
    return user


async def get_user_by_id(db_session: AsyncSession, user_id: uuid.UUID) -> User:
    return (
        await db_session.execute(
            select(User).where(User.id == user_id).execution_options(populate_existing=True)
        )
    ).scalar_one()
