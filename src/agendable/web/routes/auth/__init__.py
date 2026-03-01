from __future__ import annotations

from agendable.sso.oidc.provider import oidc_enabled
from agendable.web.routes.auth.router import (
    get_user_or_404,
    is_bootstrap_admin_email,
    maybe_promote_bootstrap_admin,
    oidc_oauth_client,
    render_login_template,
    render_profile_template,
    router,
)

_oidc_oauth_client = oidc_oauth_client

__all__ = [
    "get_user_or_404",
    "is_bootstrap_admin_email",
    "maybe_promote_bootstrap_admin",
    "oidc_enabled",
    "oidc_oauth_client",
    "render_login_template",
    "render_profile_template",
    "router",
]
