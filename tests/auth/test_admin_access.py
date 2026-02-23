from __future__ import annotations

import uuid

import pytest
from httpx import AsyncClient
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from agendable.auth import hash_password
from agendable.db.models import User, UserRole


@pytest.mark.asyncio
async def test_admin_users_requires_admin_role(
    client: AsyncClient, db_session: AsyncSession
) -> None:
    resp = await client.post(
        "/signup",
        data={
            "first_name": "Alice",
            "last_name": "Example",
            "timezone": "UTC",
            "email": "alice-admin-test@example.com",
            "password": "pw123456",
        },
        follow_redirects=True,
    )
    assert resp.status_code == 200

    denied = await client.get("/admin/users")
    assert denied.status_code == 403

    alice = (
        await db_session.execute(select(User).where(User.email == "alice-admin-test@example.com"))
    ).scalar_one()
    alice.role = UserRole.admin
    await db_session.commit()

    allowed = await client.get("/admin/users")
    assert allowed.status_code == 200
    assert "Users" in allowed.text
    assert "alice-admin-test@example.com" in allowed.text


@pytest.mark.asyncio
async def test_bootstrap_admin_email_auto_promotes_user(
    client: AsyncClient, db_session: AsyncSession, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("AGENDABLE_BOOTSTRAP_ADMIN_EMAIL", "bootstrap-admin@example.com")

    resp = await client.post(
        "/signup",
        data={
            "first_name": "Bootstrap",
            "last_name": "Admin",
            "timezone": "UTC",
            "email": "bootstrap-admin@example.com",
            "password": "pw123456",
        },
        follow_redirects=True,
    )
    assert resp.status_code == 200

    user = (
        await db_session.execute(select(User).where(User.email == "bootstrap-admin@example.com"))
    ).scalar_one()
    assert user.role == UserRole.admin

    admin_page = await client.get("/admin/users")
    assert admin_page.status_code == 200


async def _promote_signed_in_user_to_admin(
    db_session: AsyncSession,
    *,
    email: str,
) -> User:
    user = (await db_session.execute(select(User).where(User.email == email))).scalar_one()
    user.role = UserRole.admin
    await db_session.commit()
    return user


async def _create_user(
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


async def _get_user_by_id(db_session: AsyncSession, user_id: uuid.UUID) -> User:
    return (
        await db_session.execute(
            select(User).where(User.id == user_id).execution_options(populate_existing=True)
        )
    ).scalar_one()


@pytest.mark.asyncio
async def test_admin_can_update_role_and_active_status(
    client: AsyncClient, db_session: AsyncSession
) -> None:
    signup = await client.post(
        "/signup",
        data={
            "first_name": "Admin",
            "last_name": "User",
            "timezone": "UTC",
            "email": "admin-actions@example.com",
            "password": "pw123456",
        },
        follow_redirects=True,
    )
    assert signup.status_code == 200

    admin_user = await _promote_signed_in_user_to_admin(
        db_session, email="admin-actions@example.com"
    )
    managed_user = await _create_user(
        db_session,
        email="managed-user@example.com",
        first_name="Managed",
        last_name="User",
        password="pw123456",
    )

    role_resp = await client.post(
        f"/admin/users/{managed_user.id}/role",
        data={"role": "admin"},
        follow_redirects=True,
    )
    assert role_resp.status_code == 200

    promoted = await _get_user_by_id(db_session, managed_user.id)
    assert promoted.role == UserRole.admin

    deactivate_resp = await client.post(
        f"/admin/users/{managed_user.id}/active",
        data={"is_active": "false"},
        follow_redirects=True,
    )
    assert deactivate_resp.status_code == 200

    deactivated = await _get_user_by_id(db_session, managed_user.id)
    assert deactivated.is_active is False

    reactivate_resp = await client.post(
        f"/admin/users/{managed_user.id}/active",
        data={"is_active": "true"},
        follow_redirects=True,
    )
    assert reactivate_resp.status_code == 200

    reactivated = await _get_user_by_id(db_session, managed_user.id)
    assert reactivated.is_active is True

    self_deactivate = await client.post(
        f"/admin/users/{admin_user.id}/active",
        data={"is_active": "false"},
        follow_redirects=True,
    )
    assert self_deactivate.status_code == 400
    assert "You cannot deactivate your own account." in self_deactivate.text


@pytest.mark.asyncio
async def test_deactivated_user_cannot_sign_in(
    client: AsyncClient, db_session: AsyncSession
) -> None:
    signup = await client.post(
        "/signup",
        data={
            "first_name": "Admin",
            "last_name": "User",
            "timezone": "UTC",
            "email": "admin-disable@example.com",
            "password": "pw123456",
        },
        follow_redirects=True,
    )
    assert signup.status_code == 200

    _ = await _promote_signed_in_user_to_admin(db_session, email="admin-disable@example.com")
    managed_user = await _create_user(
        db_session,
        email="disabled-login@example.com",
        first_name="Disabled",
        last_name="Login",
        password="pw123456",
    )

    deactivate_resp = await client.post(
        f"/admin/users/{managed_user.id}/active",
        data={"is_active": "false"},
        follow_redirects=True,
    )
    assert deactivate_resp.status_code == 200

    logout_resp = await client.post("/logout", follow_redirects=False)
    assert logout_resp.status_code == 303

    login_resp = await client.post(
        "/login",
        data={"email": "disabled-login@example.com", "password": "pw123456"},
        follow_redirects=True,
    )
    assert login_resp.status_code == 403
    assert "This account is deactivated. Contact an admin." in login_resp.text


@pytest.mark.asyncio
async def test_admin_mutations_require_admin_role(
    client: AsyncClient, db_session: AsyncSession
) -> None:
    signup = await client.post(
        "/signup",
        data={
            "first_name": "Normal",
            "last_name": "User",
            "timezone": "UTC",
            "email": "normal-admin-guard@example.com",
            "password": "pw123456",
        },
        follow_redirects=True,
    )
    assert signup.status_code == 200

    target = await _create_user(
        db_session,
        email="target-admin-guard@example.com",
        first_name="Target",
        last_name="User",
        password="pw123456",
    )

    role_denied = await client.post(
        f"/admin/users/{target.id}/role",
        data={"role": "admin"},
        follow_redirects=False,
    )
    assert role_denied.status_code == 403

    active_denied = await client.post(
        f"/admin/users/{target.id}/active",
        data={"is_active": "false"},
        follow_redirects=False,
    )
    assert active_denied.status_code == 403


@pytest.mark.asyncio
async def test_admin_cannot_remove_own_admin_role(
    client: AsyncClient, db_session: AsyncSession
) -> None:
    signup = await client.post(
        "/signup",
        data={
            "first_name": "Self",
            "last_name": "Admin",
            "timezone": "UTC",
            "email": "self-demote@example.com",
            "password": "pw123456",
        },
        follow_redirects=True,
    )
    assert signup.status_code == 200

    admin_user = await _promote_signed_in_user_to_admin(db_session, email="self-demote@example.com")

    demote_resp = await client.post(
        f"/admin/users/{admin_user.id}/role",
        data={"role": "user"},
        follow_redirects=True,
    )
    assert demote_resp.status_code == 400
    assert "You cannot remove your own admin role." in demote_resp.text

    refreshed = await _get_user_by_id(db_session, admin_user.id)
    assert refreshed.role == UserRole.admin


@pytest.mark.asyncio
async def test_admin_role_update_invalid_input_and_missing_user(
    client: AsyncClient, db_session: AsyncSession
) -> None:
    signup = await client.post(
        "/signup",
        data={
            "first_name": "Admin",
            "last_name": "Invalid",
            "timezone": "UTC",
            "email": "invalid-role-admin@example.com",
            "password": "pw123456",
        },
        follow_redirects=True,
    )
    assert signup.status_code == 200
    _ = await _promote_signed_in_user_to_admin(db_session, email="invalid-role-admin@example.com")

    target = await _create_user(
        db_session,
        email="invalid-role-target@example.com",
        first_name="Invalid",
        last_name="Role",
        password="pw123456",
    )

    invalid_role_resp = await client.post(
        f"/admin/users/{target.id}/role",
        data={"role": "superadmin"},
        follow_redirects=False,
    )
    assert invalid_role_resp.status_code == 400

    missing_user_resp = await client.post(
        f"/admin/users/{uuid.uuid4()}/role",
        data={"role": "admin"},
        follow_redirects=False,
    )
    assert missing_user_resp.status_code == 404


@pytest.mark.asyncio
async def test_deactivated_user_active_session_is_rejected(
    client: AsyncClient, db_session: AsyncSession
) -> None:
    signup = await client.post(
        "/signup",
        data={
            "first_name": "Session",
            "last_name": "User",
            "timezone": "UTC",
            "email": "session-revoke@example.com",
            "password": "pw123456",
        },
        follow_redirects=True,
    )
    assert signup.status_code == 200

    user = (
        await db_session.execute(select(User).where(User.email == "session-revoke@example.com"))
    ).scalar_one()
    user.is_active = False
    await db_session.commit()

    dashboard_resp = await client.get("/dashboard", follow_redirects=False)
    assert dashboard_resp.status_code == 401


@pytest.mark.asyncio
async def test_admin_active_update_missing_user_returns_404(
    client: AsyncClient, db_session: AsyncSession
) -> None:
    signup = await client.post(
        "/signup",
        data={
            "first_name": "Admin",
            "last_name": "Missing",
            "timezone": "UTC",
            "email": "missing-active-admin@example.com",
            "password": "pw123456",
        },
        follow_redirects=True,
    )
    assert signup.status_code == 200
    _ = await _promote_signed_in_user_to_admin(db_session, email="missing-active-admin@example.com")

    missing_user_resp = await client.post(
        f"/admin/users/{uuid.uuid4()}/active",
        data={"is_active": "true"},
        follow_redirects=False,
    )
    assert missing_user_resp.status_code == 404


@pytest.mark.asyncio
async def test_admin_active_update_accepts_truthy_forms(
    client: AsyncClient, db_session: AsyncSession
) -> None:
    signup = await client.post(
        "/signup",
        data={
            "first_name": "Admin",
            "last_name": "Truthy",
            "timezone": "UTC",
            "email": "truthy-admin@example.com",
            "password": "pw123456",
        },
        follow_redirects=True,
    )
    assert signup.status_code == 200
    _ = await _promote_signed_in_user_to_admin(db_session, email="truthy-admin@example.com")

    target = await _create_user(
        db_session,
        email="truthy-target@example.com",
        first_name="Truthy",
        last_name="Target",
        password="pw123456",
    )

    first_disable = await client.post(
        f"/admin/users/{target.id}/active",
        data={"is_active": "false"},
        follow_redirects=True,
    )
    assert first_disable.status_code == 200

    enable_with_yes = await client.post(
        f"/admin/users/{target.id}/active",
        data={"is_active": " yes "},
        follow_redirects=True,
    )
    assert enable_with_yes.status_code == 200

    refreshed = await _get_user_by_id(db_session, target.id)
    assert refreshed.is_active is True


@pytest.mark.asyncio
async def test_inactive_admin_cannot_access_admin_users(
    client: AsyncClient, db_session: AsyncSession
) -> None:
    signup = await client.post(
        "/signup",
        data={
            "first_name": "Inactive",
            "last_name": "Admin",
            "timezone": "UTC",
            "email": "inactive-admin@example.com",
            "password": "pw123456",
        },
        follow_redirects=True,
    )
    assert signup.status_code == 200

    admin_user = await _promote_signed_in_user_to_admin(
        db_session, email="inactive-admin@example.com"
    )
    admin_user.is_active = False
    await db_session.commit()

    admin_page = await client.get("/admin/users", follow_redirects=False)
    assert admin_page.status_code == 401
