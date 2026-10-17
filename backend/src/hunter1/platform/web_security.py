"""HTTP 安全边界 —— 同源写入校验与浏览器安全响应头。

这里刻意使用**纯 ASGI 中间件**：助手对话是长时间运行的 SSE，基于
`BaseHTTPMiddleware` 的写法会额外包裹响应流，增加缓冲与取消语义上的风险。
本模块只检查请求头，并在 `send` 上补响应头，不读取或缓冲响应体。
"""

from __future__ import annotations

from collections.abc import Iterable
from http import HTTPStatus
from urllib.parse import urlsplit

from starlette.responses import JSONResponse
from starlette.types import ASGIApp, Message, Receive, Scope, Send

#: 会改变服务器状态的 HTTP 方法。跨站写入是 CSRF 的核心风险面。
UNSAFE_METHODS = frozenset({"POST", "PUT", "PATCH", "DELETE"})

#: 开发态 Vite 的固定来源。生产交付是前后端同源，不需要这些条目。
DEFAULT_DEV_ORIGINS = frozenset(
    {
        "http://127.0.0.1:5173",
        "http://localhost:5173",
        "http://[::1]:5173",
    }
)

#: 所有响应统一带上的浏览器安全头。
SECURITY_HEADERS: tuple[tuple[bytes, bytes], ...] = (
    (b"x-frame-options", b"DENY"),
    (b"x-content-type-options", b"nosniff"),
    (b"referrer-policy", b"no-referrer"),
    (b"permissions-policy", b"camera=(), microphone=(), geolocation=()"),
    (
        b"content-security-policy",
        (
            b"default-src 'self'; "
            b"script-src 'self'; "
            b"style-src 'self' 'unsafe-inline'; "
            b"img-src 'self' data:; "
            b"connect-src 'self'; "
            b"object-src 'none'; "
            b"base-uri 'self'; "
            b"form-action 'self'; "
            b"frame-ancestors 'none'"
        ),
    ),
)

#: Swagger UI 由 FastAPI 模板从 jsDelivr 加载脚本/样式，并带一段内联初始化脚本。
#: 只对文档页放宽这两项；业务页面仍保持严格的 `script-src 'self'`。
DOCS_SECURITY_HEADERS: tuple[tuple[bytes, bytes], ...] = tuple(
    (key, value)
    if key != b"content-security-policy"
    else (
        key,
        (
            b"default-src 'self'; "
            b"script-src 'self' 'unsafe-inline' https://cdn.jsdelivr.net; "
            b"style-src 'self' 'unsafe-inline' https://cdn.jsdelivr.net; "
            b"img-src 'self' data: https://fastapi.tiangolo.com; "
            b"connect-src 'self'; "
            b"object-src 'none'; "
            b"base-uri 'self'; "
            b"form-action 'self'; "
            b"frame-ancestors 'none'"
        ),
    )
    for key, value in SECURITY_HEADERS
)


def _header(scope: Scope, name: bytes) -> str | None:
    for key, value in scope.get("headers", []):
        if key.lower() == name:
            return value.decode("latin-1")
    return None


def _origin_is_allowed(origin: str, scope: Scope, dev_origins: frozenset[str]) -> bool:
    """Origin 是否允许发起写入。

    - 同源请求：与当前 Host + scheme 完全一致；
    - 开发态：只额外允许固定的回环 Vite 端口；
    - 非浏览器调用（没有 Origin）由调用方另行放行。
    """
    parts = urlsplit(origin)
    if parts.scheme not in {"http", "https"} or not parts.hostname:
        return False
    if parts.username or parts.password:
        return False
    try:
        _ = parts.port
    except ValueError:
        return False

    normalized = f"{parts.scheme}://{parts.netloc}".lower()
    if normalized in dev_origins:
        return True

    host = _header(scope, b"host")
    if not host:
        return False
    current = f"{scope.get('scheme', 'http')}://{host}".lower()
    return normalized == current


class SecurityBoundaryMiddleware:
    """拒绝跨站不安全请求，并给所有响应补安全头。"""

    def __init__(
        self,
        app: ASGIApp,
        *,
        dev_origins: Iterable[str] = DEFAULT_DEV_ORIGINS,
    ) -> None:
        self.app = app
        self.dev_origins = frozenset(origin.lower() for origin in dev_origins)

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        path = scope.get("path", "")
        selected_headers = (
            DOCS_SECURITY_HEADERS
            if path == "/api/docs" or path.startswith("/api/docs/")
            else SECURITY_HEADERS
        )

        async def send_with_security_headers(message: Message) -> None:
            if message["type"] == "http.response.start":
                headers = list(message.get("headers", []))
                existing = {key.lower() for key, _value in headers}
                headers.extend(
                    (key, value) for key, value in selected_headers if key not in existing
                )
                message = {**message, "headers": headers}
            await send(message)

        if scope.get("method", "").upper() in UNSAFE_METHODS:
            fetch_site = (_header(scope, b"sec-fetch-site") or "").lower()
            origin = _header(scope, b"origin")
            if fetch_site == "cross-site":
                response = JSONResponse(
                    {"detail": "拒绝跨站写入请求。"},
                    status_code=HTTPStatus.FORBIDDEN,
                    headers={"Cache-Control": "no-store"},
                )
                await response(scope, receive, send_with_security_headers)
                return
            if origin and not _origin_is_allowed(origin, scope, self.dev_origins):
                response = JSONResponse(
                    {"detail": "请求来源不受信任。"},
                    status_code=HTTPStatus.FORBIDDEN,
                    headers={"Cache-Control": "no-store"},
                )
                await response(scope, receive, send_with_security_headers)
                return

        await self.app(scope, receive, send_with_security_headers)


__all__ = [
    "DEFAULT_DEV_ORIGINS",
    "DOCS_SECURITY_HEADERS",
    "SECURITY_HEADERS",
    "UNSAFE_METHODS",
    "SecurityBoundaryMiddleware",
]
