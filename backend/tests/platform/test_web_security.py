"""浏览器安全边界测试 —— Origin 判定、Fetch Metadata 与安全头。"""

from __future__ import annotations

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from starlette.responses import HTMLResponse

from hunter1.platform.web_security import SecurityBoundaryMiddleware, _origin_is_allowed


def _scope(host: str = "127.0.0.1:8000", scheme: str = "http") -> dict[str, object]:
    return {
        "type": "http",
        "scheme": scheme,
        "headers": [(b"host", host.encode("ascii"))],
    }


class TestOriginPolicy:
    def test_same_origin_is_allowed(self) -> None:
        from hunter1.platform.web_security import DEFAULT_DEV_ORIGINS

        assert _origin_is_allowed("http://127.0.0.1:8000", _scope(), DEFAULT_DEV_ORIGINS)

    def test_fixed_vite_origin_is_allowed(self) -> None:
        from hunter1.platform.web_security import DEFAULT_DEV_ORIGINS

        assert _origin_is_allowed("http://127.0.0.1:5173", _scope(), DEFAULT_DEV_ORIGINS)

    @pytest.mark.parametrize(
        "origin",
        [
            "null",
            "file:///tmp/evil.html",
            "https://user:pass@example.com",
            "https://evil.example:not-a-port",
            "http://[",
            "http://[not-an-ip]",
            "http://127.0.0.1:8000/path",
            "http://127.0.0.1:8000?query",
            "http://127.0.0.1:8000#fragment",
            " http://127.0.0.1:8000",
            "http://127.0.0.1:8000\t",
        ],
    )
    def test_malformed_or_foreign_origins_are_rejected(self, origin: str) -> None:
        from hunter1.platform.web_security import DEFAULT_DEV_ORIGINS

        assert not _origin_is_allowed(origin, _scope(), DEFAULT_DEV_ORIGINS)


def test_malformed_origin_is_forbidden_without_running_write() -> None:
    app = FastAPI()
    app.add_middleware(SecurityBoundaryMiddleware)
    writes: list[bool] = []

    @app.post("/write")
    def write() -> dict[str, bool]:
        writes.append(True)
        return {"ok": True}

    with TestClient(app, raise_server_exceptions=False) as client:
        response = client.post("/write", headers={"Origin": "http://["})
    assert response.status_code == 403
    assert response.headers["x-frame-options"] == "DENY"
    assert not writes


def test_cross_site_fetch_metadata_is_rejected_even_without_origin() -> None:
    app = FastAPI()
    app.add_middleware(SecurityBoundaryMiddleware)

    @app.post("/write")
    def write() -> dict[str, bool]:
        return {"ok": True}

    with TestClient(app) as client:
        response = client.post("/write", headers={"Sec-Fetch-Site": "cross-site"})
    assert response.status_code == 403
    assert response.headers["x-frame-options"] == "DENY"


def test_non_browser_unsafe_request_without_origin_is_allowed() -> None:
    app = FastAPI()
    app.add_middleware(SecurityBoundaryMiddleware)

    @app.post("/write")
    def write() -> dict[str, bool]:
        return {"ok": True}

    with TestClient(app) as client:
        response = client.post("/write")
    assert response.status_code == 200


def test_api_docs_get_a_csp_that_allows_swagger_assets() -> None:
    app = FastAPI()
    app.add_middleware(SecurityBoundaryMiddleware)

    @app.get("/api/docs", response_class=HTMLResponse)
    def docs() -> HTMLResponse:
        return HTMLResponse(
            '<script src="https://cdn.jsdelivr.net/npm/swagger-ui-bundle.js"></script>'
            "<script>SwaggerUIBundle({})</script>"
        )

    with TestClient(app) as client:
        response = client.get("/api/docs")
    csp = response.headers["content-security-policy"]
    directives = {}
    for directive in csp.split(";"):
        name, *sources = directive.split()
        directives[name] = set(sources)
    assert {"https://cdn.jsdelivr.net", "'unsafe-inline'"} <= directives["script-src"]
    assert "frame-ancestors 'none'" in csp
