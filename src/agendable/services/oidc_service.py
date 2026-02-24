from __future__ import annotations

from collections.abc import Callable, Mapping
from dataclasses import dataclass

from sqlalchemy.ext.asyncio import AsyncSession

from agendable.db.models import User, UserRole
from agendable.db.repos import ExternalIdentityRepository, UserRepository
from agendable.sso_oidc_flow import userinfo_name_parts


@dataclass(frozen=True)
class OidcLinkResolution:
    user: User | None
    create_identity: bool
    error: str | None = None
    should_redirect_login: bool = False


@dataclass(frozen=True)
class OidcLoginResolution:
    user: User | None
    create_identity: bool
    error: str | None = None
    should_redirect_login: bool = False


async def provision_user_for_oidc(
    session: AsyncSession,
    *,
    email: str,
    userinfo: Mapping[str, object],
    is_bootstrap_admin_email: Callable[[str], bool],
) -> User:
    first_name, last_name = userinfo_name_parts(userinfo, email)
    user = User(
        email=email,
        first_name=first_name,
        last_name=last_name,
        display_name=f"{first_name} {last_name}".strip(),
        timezone="UTC",
        role=(UserRole.admin if is_bootstrap_admin_email(email) else UserRole.user),
        password_hash=None,
    )
    session.add(user)
    await session.flush()
    return user


async def resolve_oidc_link_resolution(
    session: AsyncSession,
    *,
    link_user: User,
    sub: str,
    email: str,
) -> OidcLinkResolution:
    if not link_user.is_active:
        return OidcLinkResolution(
            user=None,
            create_identity=False,
            should_redirect_login=True,
        )

    ext_repo = ExternalIdentityRepository(session)
    ext = await ext_repo.get_by_provider_subject("oidc", sub)
    if ext is not None and ext.user_id != link_user.id:
        return OidcLinkResolution(
            user=link_user,
            create_identity=False,
            error="already_linked_other_user",
        )

    if email != link_user.email:
        return OidcLinkResolution(
            user=link_user,
            create_identity=False,
            error="email_mismatch",
        )

    return OidcLinkResolution(
        user=link_user,
        create_identity=(ext is None),
    )


async def resolve_oidc_login_resolution(
    session: AsyncSession,
    *,
    sub: str,
    email: str,
    userinfo: Mapping[str, object],
    is_bootstrap_admin_email: Callable[[str], bool],
) -> OidcLoginResolution:
    ext_repo = ExternalIdentityRepository(session)
    users = UserRepository(session)

    ext = await ext_repo.get_by_provider_subject("oidc", sub)
    if ext is not None:
        user = await users.get_by_id(ext.user_id)
        if user is None:
            return OidcLoginResolution(user=None, create_identity=False, should_redirect_login=True)
        if not user.is_active:
            return OidcLoginResolution(user=user, create_identity=False, error="inactive_user")
        return OidcLoginResolution(user=user, create_identity=False)

    user = await users.get_by_email(email)
    if user is None:
        user = await provision_user_for_oidc(
            session,
            email=email,
            userinfo=userinfo,
            is_bootstrap_admin_email=is_bootstrap_admin_email,
        )
        return OidcLoginResolution(user=user, create_identity=True)

    if not user.is_active:
        return OidcLoginResolution(user=user, create_identity=False, error="inactive_user")
    if user.password_hash is not None:
        return OidcLoginResolution(
            user=user, create_identity=False, error="password_user_requires_link"
        )

    return OidcLoginResolution(user=user, create_identity=True)
