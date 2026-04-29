"""App-level Basic Auth — defense in depth поверх nginx auth_basic.

Если порты бэка случайно окажутся доступны напрямую (или nginx упадёт),
этот middleware всё ещё требует логин/пароль.

Если BASIC_AUTH_USER/BASIC_AUTH_PASSWORD пустые — middleware no-op (для dev/тестов).
"""
import base64
import secrets
from typing import Awaitable, Callable

from fastapi import Request
from fastapi.responses import Response
from starlette.middleware.base import BaseHTTPMiddleware

from app.config import settings


# Эндпоинты, которые ВСЕГДА открыты (внутренняя коммуникация / health / webhooks)
PUBLIC_PATHS: tuple[str, ...] = (
    "/api/v1/health",
    "/api/v1/ws",  # WebSocket — отдельная аутентификация (или nginx-side)
    "/api/v1/calltouch/webhook",  # внешний webhook от Calltouch — защита через shared secret
    "/docs",
    "/openapi.json",
    "/redoc",
)


def _is_public(path: str) -> bool:
    return any(path == p or path.startswith(p + "/") for p in PUBLIC_PATHS)


class BasicAuthMiddleware(BaseHTTPMiddleware):
    async def dispatch(
        self,
        request: Request,
        call_next: Callable[[Request], Awaitable[Response]],
    ) -> Response:
        if not settings.basic_auth_user or not settings.basic_auth_password:
            return await call_next(request)

        if _is_public(request.url.path):
            return await call_next(request)

        header = request.headers.get("authorization", "")
        if not header.lower().startswith("basic "):
            return _unauthorized()

        try:
            decoded = base64.b64decode(header[6:]).decode("utf-8", errors="replace")
            user, _, pwd = decoded.partition(":")
        except Exception:
            return _unauthorized()

        ok_user = secrets.compare_digest(user, settings.basic_auth_user)
        ok_pwd = secrets.compare_digest(pwd, settings.basic_auth_password)
        if not (ok_user and ok_pwd):
            return _unauthorized()

        return await call_next(request)


def _unauthorized() -> Response:
    return Response(
        status_code=401,
        content="Unauthorized",
        headers={"WWW-Authenticate": 'Basic realm="Call Analytics"'},
    )
