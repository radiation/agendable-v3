from __future__ import annotations

from collections.abc import Mapping
from typing import Any, Protocol

from fastapi import Request
from starlette.responses import Response


class OidcClient(Protocol):
    async def authorize_redirect(
        self,
        request: Request,
        redirect_uri: str,
        **kwargs: Any,
    ) -> Response: ...

    async def authorize_access_token(self, request: Request) -> dict[str, object]: ...

    async def parse_id_token(self, *args: Any, **kwargs: Any) -> Mapping[str, object]: ...

    async def userinfo(self, *, token: Mapping[str, object]) -> Mapping[str, object]: ...
