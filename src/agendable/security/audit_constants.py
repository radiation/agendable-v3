from __future__ import annotations

from typing import Final, Literal

# This module centralizes the string values used in security-audit logging.
# Keeping these as constants (rather than inline string literals) helps prevent
# accidental typos and keeps event/reason naming consistent across the codebase.


type AuthAuditEvent = Literal[
    "password_login",
    "signup",
    "logout",
]

type AuthDeniedReason = Literal[
    "rate_limited",
    "account_not_found",
    "inactive_user",
    "invalid_credentials",
    "account_exists",
]

type OIDCAuditEvent = Literal[
    "callback",
    "callback_login",
    "identity_link_start",
    "identity_link",
    "identity_unlink",
]

type OIDCDeniedReason = Literal[
    # Provider / callback flow
    "provider_disabled",
    "oauth_error",
    "missing_required_claims",
    "domain_not_allowed",
    "rate_limited",
    # Login/link resolution errors
    "inactive_user",
    "password_user_requires_link",
    "already_linked_other_user",
    "email_mismatch",
    # Identity management
    "invalid_password",
    "identity_not_found",
    "only_sign_in_method",
]

type AdminAuditEvent = Literal[
    "user_role_update",
    "user_active_update",
]

type AdminDeniedReason = Literal[
    "invalid_role",
    "self_demotion_blocked",
    "self_deactivation_blocked",
]


# Auth events
AUTH_EVENT_PASSWORD_LOGIN: Final[str] = "password_login"
AUTH_EVENT_SIGNUP: Final[str] = "signup"
AUTH_EVENT_LOGOUT: Final[str] = "logout"

# Auth denied reasons
AUTH_REASON_RATE_LIMITED: Final[str] = "rate_limited"
AUTH_REASON_ACCOUNT_NOT_FOUND: Final[str] = "account_not_found"
AUTH_REASON_INACTIVE_USER: Final[str] = "inactive_user"
AUTH_REASON_INVALID_CREDENTIALS: Final[str] = "invalid_credentials"
AUTH_REASON_ACCOUNT_EXISTS: Final[str] = "account_exists"

# OIDC events
OIDC_EVENT_CALLBACK: Final[str] = "callback"
OIDC_EVENT_CALLBACK_LOGIN: Final[str] = "callback_login"
OIDC_EVENT_IDENTITY_LINK_START: Final[str] = "identity_link_start"
OIDC_EVENT_IDENTITY_LINK: Final[str] = "identity_link"
OIDC_EVENT_IDENTITY_UNLINK: Final[str] = "identity_unlink"

# OIDC denied reasons
OIDC_REASON_PROVIDER_DISABLED: Final[str] = "provider_disabled"
OIDC_REASON_OAUTH_ERROR: Final[str] = "oauth_error"
OIDC_REASON_MISSING_REQUIRED_CLAIMS: Final[str] = "missing_required_claims"
OIDC_REASON_DOMAIN_NOT_ALLOWED: Final[str] = "domain_not_allowed"
OIDC_REASON_RATE_LIMITED: Final[str] = "rate_limited"

OIDC_REASON_INACTIVE_USER: Final[str] = "inactive_user"
OIDC_REASON_PASSWORD_USER_REQUIRES_LINK: Final[str] = "password_user_requires_link"
OIDC_REASON_ALREADY_LINKED_OTHER_USER: Final[str] = "already_linked_other_user"
OIDC_REASON_EMAIL_MISMATCH: Final[str] = "email_mismatch"

OIDC_REASON_INVALID_PASSWORD: Final[str] = "invalid_password"
OIDC_REASON_IDENTITY_NOT_FOUND: Final[str] = "identity_not_found"
OIDC_REASON_ONLY_SIGN_IN_METHOD: Final[str] = "only_sign_in_method"

# Admin events
ADMIN_EVENT_USER_ROLE_UPDATE: Final[str] = "user_role_update"
ADMIN_EVENT_USER_ACTIVE_UPDATE: Final[str] = "user_active_update"

# Admin denied reasons
ADMIN_REASON_INVALID_ROLE: Final[str] = "invalid_role"
ADMIN_REASON_SELF_DEMOTION_BLOCKED: Final[str] = "self_demotion_blocked"
ADMIN_REASON_SELF_DEACTIVATION_BLOCKED: Final[str] = "self_deactivation_blocked"
