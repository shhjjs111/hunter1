"""局域网访问的访问码门禁。

默认监听 `127.0.0.1` 时**不启用**本模块 —— 单机使用没有任何额外步骤。只有用户
主动用 `--lan`（或 `--host 0.0.0.0`）把界面暴露到局域网时，CLI 才生成一个随机
访问码并挂上这层门禁：手机首次打开会看到一个只含表单的极小页面，输入电脑上
显示的访问码后拿到 HttpOnly Cookie，之后的 SPA 与 API 请求照常走。

为什么不做「URL 里带 token 一点即入」：token 会进入浏览器历史、代理日志和
uvicorn 的访问日志。访问码只印在电脑终端里，输入一次，日志里不出现。

这是**同 Wi-Fi 信任模型**：拿到访问码的人就能使用界面。它挡的是「同一局域网里
随手扫端口的人」，不是公网多租户鉴权。API Key 与数据仍然只存在这台电脑上。
"""

from __future__ import annotations

import asyncio
import contextlib
import hmac
import ipaddress
import os
import secrets
import socket
from http.cookies import CookieError, SimpleCookie
from pathlib import Path
from urllib.parse import parse_qs

from starlette.responses import HTMLResponse, JSONResponse, RedirectResponse
from starlette.types import ASGIApp, Receive, Scope, Send

#: 浏览器保存访问码的 Cookie 名。
COOKIE_NAME = "hunter1_access"

#: 原生客户端（安卓 App）携带访问码的请求头名。
#: 浏览器用 Cookie；原生 HTTP 栈没有同源页面与 Cookie 流程，用显式请求头更直接。
#: 两种方式共用同一个访问码，失败延迟与常量时间比较也共用。
ACCESS_CODE_HEADER = "x-hunter1-access-code"

#: 访问码文件（与 SQLite 放在同一目录）。
TOKEN_FILE_NAME = "access_token"

#: 登录表单的固定路径。它由本中间件自己处理，不进入 FastAPI 路由表。
LOGIN_PATH = "/__access"

#: 登录 Cookie 的有效期：30 天。家庭局域网里反复输入访问码没有意义。
COOKIE_MAX_AGE = 30 * 24 * 60 * 60

#: 登录表单请求体上限。超过就当作空表单，避免把大请求读进内存。
MAX_FORM_BYTES = 4096


def token_path(db_path: str | Path) -> Path:
    """访问码落点：与数据库同目录，跟着 `--db` 走。"""
    return Path(db_path).expanduser().resolve().parent / TOKEN_FILE_NAME


def load_or_create_token(path: Path) -> str:
    """读取或生成访问码。

    访问码会持久化：重启服务后手机上的 Cookie 仍然有效。想作废所有设备，删除
    这个文件再启动即可（会生成新码）。
    """
    try:
        existing = path.read_text(encoding="utf-8").strip()
    except FileNotFoundError:
        existing = ""
    if existing:
        return existing

    token = secrets.token_urlsafe(9)  # 12 个 URL 安全字符，约 72 bit
    path.parent.mkdir(parents=True, exist_ok=True)
    temp = path.with_name(f".{path.name}.{os.getpid()}.tmp")
    temp.write_text(token + "\n", encoding="utf-8", newline="\n")
    # Windows 上 chmod 只有弱语义；失败不影响功能，旁边还有目录权限兜底。
    with contextlib.suppress(OSError):
        os.chmod(temp, 0o600)
    os.replace(temp, path)
    return token


def detect_lan_ip() -> str | None:
    """尽力找出本机在局域网中的 IPv4 地址；找不到返回 None。

    UDP `connect` 不会真的发包，只让内核选一条出口并给出本地地址。断网 / 只有
    回环时退回主机名解析；仍拿不到就不猜，CLI 会让用户看 `ipconfig`。
    """
    try:
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as probe:
            probe.connect(("8.8.8.8", 80))
            address = probe.getsockname()[0]
    except OSError:
        address = ""

    if not address or address.startswith("127.") or address == "0.0.0.0":
        try:
            address = socket.gethostbyname(socket.gethostname())
        except OSError:
            return None
    if address.startswith("127.") or address == "0.0.0.0":
        return None
    return address


def detect_lan_ips() -> tuple[str, ...]:
    """本机所有非回环 IPv4 地址（多网卡 / Wi-Fi + 有线 / VPN 都可能不止一个）。

    Host 白名单必须覆盖手机实际写进 `Host` 的那个地址；只猜一个出口 IP 时，
    多网卡机器上很容易出现「电脑能开、手机 400」。主出口地址排在最前。
    """
    addresses: list[str] = []
    primary = detect_lan_ip()
    if primary:
        addresses.append(primary)
    try:
        infos = socket.getaddrinfo(socket.gethostname(), None, socket.AF_INET)
    except OSError:
        infos = []
    for info in infos:
        address = str(info[4][0])
        if address not in addresses and not address.startswith("127.") and address != "0.0.0.0":
            addresses.append(address)
    return tuple(addresses)


def is_loopback_client(scope: Scope) -> bool:
    """请求是否来自本机。

    本机浏览器不需要访问码 —— 用户已经坐在运行程序的那台电脑前，再让他输入一次
    只是多一道无意义的墙。IPv6 回环（`::1`）同样放行。
    """
    client = scope.get("client")
    if not client:
        return False
    host = client[0]
    try:
        return ipaddress.ip_address(host).is_loopback
    except ValueError:
        return host in {"localhost", "testclient"}


def _cookie_value(scope: Scope, name: str) -> str | None:
    for key, value in scope.get("headers", []):
        if key.lower() != b"cookie":
            continue
        jar = SimpleCookie()
        try:
            jar.load(value.decode("latin-1"))
        except CookieError:
            continue
        if name in jar:
            return jar[name].value
    return None


def _header_value(scope: Scope, name: str) -> str | None:
    wanted = name.encode("latin-1")
    for key, value in scope.get("headers", []):
        if key.lower() == wanted:
            return value.decode("latin-1")
    return None


def _tokens_match(provided: str, expected: str) -> bool:
    if not provided:
        return False
    return hmac.compare_digest(provided.encode("utf-8"), expected.encode("utf-8"))


async def _read_form(receive: Receive) -> dict[str, list[str]]:
    """读取并解析登录表单；超限或类型不对时返回空字典。"""
    chunks: list[bytes] = []
    size = 0
    while True:
        message = await receive()
        if message["type"] != "http.request":
            return {}
        body = message.get("body", b"")
        size += len(body)
        if size > MAX_FORM_BYTES:
            return {}
        chunks.append(body)
        if not message.get("more_body", False):
            break
    try:
        text = b"".join(chunks).decode("utf-8")
    except UnicodeDecodeError:
        return {}
    return parse_qs(text, keep_blank_values=True)


def _login_page(*, error: bool = False, status_code: int = 200) -> HTMLResponse:
    message = (
        '<p class="error" role="alert">访问码不正确，请对照电脑上显示的内容。</p>' if error else ""
    )
    body = f"""<!doctype html>
<html lang="zh-CN">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1, viewport-fit=cover">
  <meta name="theme-color" content="#0a0c11">
  <title>Hunter1 · 手机访问</title>
  <style>
    :root {{ color-scheme: dark; }}
    * {{ box-sizing: border-box; }}
    body {{
      margin: 0; min-height: 100dvh; display: grid; place-items: center;
      padding: 24px; background: #0a0c11; color: #f5f5f7;
      font: 16px/1.5 -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif;
    }}
    main {{
      width: min(420px, 100%); padding: 28px 24px; border-radius: 24px;
      background: rgb(255 255 255 / 0.07); border: 1px solid rgb(255 255 255 / 0.12);
    }}
    h1 {{ margin: 0 0 6px; font-size: 24px; }}
    p {{ margin: 0 0 18px; color: rgb(235 235 245 / 0.72); }}
    label {{
      display: block; margin-bottom: 8px; font-size: 14px;
      color: rgb(235 235 245 / 0.8);
    }}
    input {{
      width: 100%; padding: 13px 14px; border-radius: 14px;
      border: 1px solid rgb(255 255 255 / 0.2);
      background: rgb(0 0 0 / 0.28); color: inherit; font: inherit;
    }}
    button {{
      width: 100%; margin-top: 14px; padding: 13px 14px; border: 0; border-radius: 14px;
      background: #0a84ff; color: white; font: inherit; font-weight: 600;
    }}
    .hint {{ margin: 18px 0 0; font-size: 13px; }}
    .error {{ margin: 14px 0 0; color: #ffb4a9; font-size: 14px; }}
  </style>
</head>
<body>
  <main>
    <h1>Hunter1</h1>
    <p>请输入电脑上显示的访问码。</p>
    <form method="post" action="{LOGIN_PATH}">
      <label for="code">访问码</label>
      <input id="code" name="code" type="text" inputmode="text" autocomplete="one-time-code"
             autocapitalize="off" autocorrect="off" spellcheck="false" autofocus required>
      <button type="submit">进入</button>
    </form>
    {message}
    <p class="hint">访问码只在本机终端显示。输入一次后，这台手机会记住 30 天。</p>
  </main>
</body>
</html>
"""
    headers = {"Cache-Control": "no-store"}
    return HTMLResponse(body, status_code=401 if error else status_code, headers=headers)


class AccessGateMiddleware:
    """非回环请求必须携带访问码 Cookie 或先通过登录表单。"""

    def __init__(self, app: ASGIApp, *, token: str, failure_delay: float = 0.35) -> None:
        if not token:
            raise ValueError("AccessGateMiddleware 需要非空访问码")
        self.app = app
        self.token = token
        self.failure_delay = failure_delay

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http" or is_loopback_client(scope):
            await self.app(scope, receive, send)
            return

        path = scope.get("path", "")
        method = scope.get("method", "GET").upper()

        if path == LOGIN_PATH:
            if method == "POST":
                await self._login(scope, receive, send)
                return
            if method in {"GET", "HEAD"}:
                if _tokens_match(_cookie_value(scope, COOKIE_NAME) or "", self.token):
                    await self._redirect(scope, receive, send, "/")
                else:
                    await _login_page()(scope, receive, send)
                return

        provided = _header_value(scope, ACCESS_CODE_HEADER) or _cookie_value(scope, COOKIE_NAME)
        if _tokens_match(provided or "", self.token):
            await self.app(scope, receive, send)
            return

        if path.startswith("/api/") or method not in {"GET", "HEAD"}:
            response = JSONResponse(
                {"detail": "需要访问码：请在浏览器打开本机显示的地址并输入访问码。"},
                status_code=401,
                headers={"Cache-Control": "no-store"},
            )
            await response(scope, receive, send)
            return
        await _login_page(status_code=401)(scope, receive, send)

    async def _login(self, scope: Scope, receive: Receive, send: Send) -> None:
        form = await _read_form(receive)
        provided = (form.get("code") or [""])[0].strip()
        if _tokens_match(provided, self.token):
            response = RedirectResponse("/", status_code=303)
            response.set_cookie(
                COOKIE_NAME,
                self.token,
                max_age=COOKIE_MAX_AGE,
                httponly=True,
                samesite="strict",
                path="/",
            )
            await response(scope, receive, send)
            return

        if self.failure_delay:
            await asyncio.sleep(self.failure_delay)
        await _login_page(error=True)(scope, receive, send)

    async def _redirect(self, scope: Scope, receive: Receive, send: Send, location: str) -> None:
        await RedirectResponse(location, status_code=303)(scope, receive, send)


__all__ = [
    "ACCESS_CODE_HEADER",
    "COOKIE_MAX_AGE",
    "COOKIE_NAME",
    "LOGIN_PATH",
    "AccessGateMiddleware",
    "detect_lan_ip",
    "detect_lan_ips",
    "is_loopback_client",
    "load_or_create_token",
    "token_path",
]
