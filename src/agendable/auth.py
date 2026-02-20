from __future__ import annotations

import uuid
from typing import cast

from fastapi import Depends, HTTPException, Request
from passlib.context import CryptContext
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from agendable.db import get_session
from agendable.models import User

_pwd_context = CryptContext(schemes=["bcrypt"], deprecated="auto")


def hash_password(password: str) -> str:
    return cast(str, _pwd_context.hash(password))


def verify_password(password: str, password_hash: str) -> bool:
    return cast(bool, _pwd_context.verify(password, password_hash))


def get_current_user_id(request: Request) -> uuid.UUID | None:
    raw = request.session.get("user_id")
    if raw is None:
        return None

    try:
        return uuid.UUID(str(raw))
    except ValueError:
        return None


async def require_user(
    request: Request,
    session: AsyncSession = Depends(get_session),
) -> User:
    user_id = get_current_user_id(request)
    if user_id is None:
        raise HTTPException(status_code=401, detail="Not authenticated")

    result = await session.execute(select(User).where(User.id == user_id))
    user = result.scalar_one_or_none()
    if user is None:
        raise HTTPException(status_code=401, detail="Not authenticated")

    return user
