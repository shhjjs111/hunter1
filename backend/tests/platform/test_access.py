"""局域网访问码门禁测试 —— 回环放行、远程拦截、登录换 Cookie。"""

from __future__ import annotations

from pathlib import Path

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from hunter1.platform.access import (
    ACCESS_CODE_HEADER,
    COOKIE_NAME,
    LOGIN_PATH,
    AccessGateMiddleware,
    detect_lan_ip,
    detect_lan_ips,
    is_loopback_client,
    load_or_create_token,
    token_path,
)

TOKEN = "test-access-code"


def _app() -> FastAPI:
    app = FastAPI()
    app.add_middleware(AccessGateMiddleware, token=TOKEN, failure_delay=0)

    @app.get("/")
    def home() -> dict[str, bool]:
        return {"ok": True}

    @app.get("/api/jobs")
    def jobs() -> dict[str, list[object]]:
        return {"items": []}

    @app.post("/api/crawl")
    def crawl() -> dict[str, bool]:
        return {"started": True}

    return app


def _client(host: str = "192.168.1.20") -> TestClient:
    return TestClient(_app(), client=(host, 51234))


def test_loopback_is_not_gated() -> None:
    with _client("127.0.0.1") as client:
        assert client.get("/").status_code == 200
        assert client.get("/api/jobs").status_code == 200


def test_remote_page_gets_login_form_and_api_gets_json() -> None:
    with _client() as client:
        page = client.get("/")
        assert page.status_code == 401
        assert "访问码" in page.text

        api = client.get("/api/jobs")
        assert api.status_code == 401
        assert api.headers["content-type"].startswith("application/json")
        assert "访问码" in api.json()["detail"]


def test_login_sets_cookie_and_unlocks_requests() -> None:
    with _client() as client:
        login = client.post("/__access", data={"code": TOKEN}, follow_redirects=False)
        assert login.status_code == 303
        assert login.headers["location"] == "/"
        assert login.cookies[COOKIE_NAME] == TOKEN
        assert "httponly" in login.headers["set-cookie"].lower()
        assert "samesite=strict" in login.headers["set-cookie"].lower()

        assert client.get("/").status_code == 200
        assert client.post("/api/crawl").status_code == 200


def test_native_client_header_unlocks_api_without_cookie() -> None:
    """安卓 App 用请求头携带访问码 —— 与 Cookie 同一口令，不需要登录表单。"""
    with _client() as client:
        assert client.get("/api/jobs", headers={ACCESS_CODE_HEADER: TOKEN}).status_code == 200
        assert client.post("/api/crawl", headers={ACCESS_CODE_HEADER: TOKEN}).status_code == 200
        assert client.get("/api/jobs", headers={ACCESS_CODE_HEADER: "wrong"}).status_code == 401


def test_wrong_code_is_rejected_without_cookie() -> None:
    with _client() as client:
        response = client.post("/__access", data={"code": "wrong"}, follow_redirects=False)
        assert response.status_code == 401
        assert COOKIE_NAME not in response.cookies
        assert client.get("/").status_code == 401


def test_non_ascii_code_is_rejected_instead_of_crashing() -> None:
    with _client() as client:
        response = client.post("/__access", data={"code": "访问码"}, follow_redirects=False)
        assert response.status_code == 401


def test_authenticated_visitor_is_redirected_away_from_login_page() -> None:
    with _client() as client:
        client.post("/__access", data={"code": TOKEN})
        response = client.get("/__access", follow_redirects=False)
        assert response.status_code == 303
        assert response.headers["location"] == "/"


def test_loopback_detection_handles_ipv4_ipv6_and_unknown_names() -> None:
    assert is_loopback_client({"type": "http", "client": ("127.0.0.1", 1)})
    assert is_loopback_client({"type": "http", "client": ("::1", 1)})
    assert not is_loopback_client({"type": "http", "client": ("192.168.1.20", 1)})
    assert not is_loopback_client({"type": "http", "client": None})


def test_token_file_is_created_reused_and_regenerated(tmp_path: Path) -> None:
    path = token_path(tmp_path / "hunter1.db")
    first = load_or_create_token(path)
    assert first
    assert load_or_create_token(path) == first

    path.unlink()
    second = load_or_create_token(path)
    assert second
    assert second != first


def test_token_path_lives_next_to_the_database(tmp_path: Path) -> None:
    assert token_path(tmp_path / "data" / "hunter1.db") == (tmp_path / "data" / "access_token")


def test_empty_token_is_rejected() -> None:
    with pytest.raises(ValueError):
        AccessGateMiddleware(_app(), token="")


def test_detect_lan_ip_never_returns_loopback() -> None:
    address = detect_lan_ip()
    assert address is None or not address.startswith("127.")


def test_login_page_can_be_opened_directly() -> None:
    with _client() as client:
        response = client.get(LOGIN_PATH)
        assert response.status_code == 200
        assert 'action="/__access"' in response.text


def test_oversized_login_body_is_rejected() -> None:
    with _client() as client:
        response = client.post("/__access", content=b"code=" + b"x" * 5000, follow_redirects=False)
        assert response.status_code == 401


def test_invalid_utf8_login_body_is_rejected() -> None:
    with _client() as client:
        response = client.post("/__access", content=b"\xff\xfe", follow_redirects=False)
        assert response.status_code == 401


def test_malformed_cookie_header_is_ignored() -> None:
    with _client() as client:
        response = client.get("/", headers={"Cookie": "hunter1_access"})
        assert response.status_code == 401


def test_wrong_code_delay_is_applied() -> None:
    app = FastAPI()
    app.add_middleware(AccessGateMiddleware, token=TOKEN, failure_delay=0.001)

    @app.get("/")
    def home() -> dict[str, bool]:
        return {"ok": True}

    with TestClient(app, client=("192.168.1.20", 51234)) as client:
        assert (
            client.post("/__access", data={"code": "wrong"}, follow_redirects=False).status_code
            == 401
        )


def test_localhost_and_testclient_names_count_as_loopback() -> None:
    assert is_loopback_client({"type": "http", "client": ("localhost", 1)})
    assert is_loopback_client({"type": "http", "client": ("testclient", 1)})


def test_detect_lan_ips_returns_only_routable_addresses() -> None:
    addresses = detect_lan_ips()
    assert isinstance(addresses, tuple)
    assert all(not address.startswith("127.") and address != "0.0.0.0" for address in addresses)


def test_detect_lan_ip_falls_back_to_hostname(monkeypatch: pytest.MonkeyPatch) -> None:
    import hunter1.platform.access as access

    def boom(*_args: object, **_kwargs: object) -> None:
        raise OSError("no UDP route")

    monkeypatch.setattr(access.socket, "socket", boom)
    monkeypatch.setattr(access.socket, "gethostbyname", lambda _name: "192.168.1.9")
    assert detect_lan_ip() == "192.168.1.9"


def test_detect_lan_ip_returns_none_when_hostname_also_fails(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import hunter1.platform.access as access

    def boom(*_args: object, **_kwargs: object) -> None:
        raise OSError("no UDP route")

    def no_name(_name: str) -> str:
        raise OSError("no hostname")

    monkeypatch.setattr(access.socket, "socket", boom)
    monkeypatch.setattr(access.socket, "gethostbyname", no_name)
    assert detect_lan_ip() is None


def test_detect_lan_ip_rejects_loopback_hostname(monkeypatch: pytest.MonkeyPatch) -> None:
    import hunter1.platform.access as access

    class _Probe:
        def __enter__(self) -> _Probe:
            return self

        def __exit__(self, *_args: object) -> None:
            return None

        def connect(self, _target: object) -> None:
            return None

        def getsockname(self) -> tuple[str, int]:
            return ("127.0.0.1", 0)

    monkeypatch.setattr(access.socket, "socket", lambda *_a, **_k: _Probe())
    monkeypatch.setattr(access.socket, "gethostbyname", lambda _name: "127.0.0.1")
    assert detect_lan_ip() is None
