from __future__ import annotations

import uuid
from collections.abc import Mapping
from dataclasses import dataclass

from fastapi import Request

from agendable.sso.oidc.client import OidcClient

_OIDC_LINK_USER_ID_SESSION_KEY = "oidc_link_user_id"


@dataclass(frozen=True)
class OidcIdentityClaims:
    sub: str
    email: str
    email_verified: bool


def get_oidc_link_user_id(request: Request) -> uuid.UUID | None:
    raw = request.session.get(_OIDC_LINK_USER_ID_SESSION_KEY)
    if raw is None:
        return None
    try:
        return uuid.UUID(str(raw))
    except ValueError:
        return None


def set_oidc_link_user_id(request: Request, user_id: uuid.UUID) -> None:
    request.session[_OIDC_LINK_USER_ID_SESSION_KEY] = str(user_id)


def clear_oidc_link_user_id(request: Request) -> None:
    request.session.pop(_OIDC_LINK_USER_ID_SESSION_KEY, None)


def build_authorize_params(prompt: str | None) -> dict[str, str]:
    normalized = (prompt or "").strip()
    if not normalized:
        return {}
    return {"prompt": normalized}


def userinfo_name_parts(userinfo: Mapping[str, object], email: str) -> tuple[str, str]:
    first_name = str(userinfo.get("given_name", "")).strip()
    last_name = str(userinfo.get("family_name", "")).strip()

    if not first_name and not last_name:
        full_name = str(userinfo.get("name", "")).strip()
        if full_name:
            parts = full_name.split(maxsplit=1)
            first_name = parts[0]
            if len(parts) > 1:
                last_name = parts[1]

    if not first_name:
        first_name = email.split("@", 1)[0] or "User"

    return first_name, last_name


async def parse_userinfo_from_token(
    oidc_client: OidcClient,
    request: Request,
    token: Mapping[str, object],
) -> Mapping[str, object]:
    parsed_userinfo: Mapping[str, object] | None = None

    if "id_token" in token:
        try:
            parsed_userinfo = await oidc_client.parse_id_token(request, token)
        except TypeError:
            parsed_userinfo = await oidc_client.parse_id_token(token, nonce=None)
        except Exception:
            parsed_userinfo = None

    if parsed_userinfo is None:
        parsed_userinfo = await oidc_client.userinfo(token=token)

    return parsed_userinfo


def _claim_is_truthy(value: object) -> bool:
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        return value.strip().lower() in {"true", "1", "yes", "on"}
    if isinstance(value, int):
        return value != 0
    return False


def parse_identity_claims(userinfo: Mapping[str, object]) -> OidcIdentityClaims:
    return OidcIdentityClaims(
        sub=str(userinfo.get("sub", "")),
        email=str(userinfo.get("email", "")).strip().lower(),
        email_verified=_claim_is_truthy(userinfo.get("email_verified")),
    )
