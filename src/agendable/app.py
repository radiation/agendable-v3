from __future__ import annotations

import logging
from collections.abc import AsyncIterator, Awaitable, Callable
from contextlib import asynccontextmanager
from pathlib import Path
from time import perf_counter
from uuid import uuid4

from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles
from starlette.middleware.sessions import SessionMiddleware
from starlette.requests import Request
from starlette.responses import Response

from agendable.db import engine
from agendable.db.models import Base
from agendable.logging_config import (
    configure_logging,
    log_with_fields,
    reset_request_id,
    set_request_id,
)
from agendable.settings import get_settings
from agendable.web.routes import router as web_router

logger = logging.getLogger("agendable.http")


def create_app() -> FastAPI:
    settings = get_settings()
    configure_logging(settings)

    @asynccontextmanager
    async def lifespan(_: FastAPI) -> AsyncIterator[None]:
        if settings.auto_create_db:
            async with engine.begin() as conn:
                await conn.run_sync(Base.metadata.create_all)
        yield

    app = FastAPI(lifespan=lifespan)

    @app.middleware("http")
    async def request_logging_middleware(
        request: Request,
        call_next: Callable[[Request], Awaitable[Response]],
    ) -> Response:
        request_id = (request.headers.get("x-request-id") or "").strip() or uuid4().hex
        token = set_request_id(request_id)
        start = perf_counter()

        try:
            response = await call_next(request)
        except Exception:
            if settings.log_http_requests:
                duration_ms = (perf_counter() - start) * 1000
                log_with_fields(
                    logger,
                    logging.ERROR,
                    "request failed",
                    method=request.method,
                    path=request.url.path,
                    duration_ms=f"{duration_ms:.2f}",
                    exc_info=True,
                )
            raise
        finally:
            reset_request_id(token)

        response.headers["X-Request-ID"] = request_id
        if settings.log_http_requests:
            duration_ms = (perf_counter() - start) * 1000
            log_with_fields(
                logger,
                logging.INFO,
                "request complete",
                method=request.method,
                path=request.url.path,
                status_code=response.status_code,
                duration_ms=f"{duration_ms:.2f}",
            )
        return response

    app.add_middleware(
        SessionMiddleware,
        secret_key=settings.session_secret.get_secret_value(),
        session_cookie=settings.session_cookie_name,
        same_site="lax",
    )

    app.include_router(web_router)

    static_dir = Path(__file__).resolve().parent / "web" / "static"
    app.mount("/static", StaticFiles(directory=str(static_dir)), name="static")

    return app


app = create_app()
