"""Tests for ``src/hal0/api/plugins/manifest_proxy.py`` (v0.3, PR-7).

Covers:

* The path-traversal validator (``_safe_plugin_asset_relpath``).
* The plugin-name validator (``_safe_plugin_name``).
* SRI parsing + verification (``_parse_integrity`` + ``_verify_sri``).
* The manifest proxy endpoint:
    - inbound ``Authorization`` / ``Cookie`` stripped
    - outbound ``X-hal0-Agent`` injected
    - ``Content-Security-Policy: script-src 'self' 'strict-dynamic'`` set
    - upstream-unreachable → 503 envelope
* The asset proxy endpoint:
    - traversal / illegal-name rejected at 400 (no upstream hit)
    - SRI mismatch → 502 envelope
    - SRI match → asset bytes pass through verbatim
    - missing-from-manifest asset (no SRI declared) → passes through
      with ``no-store`` cache headers

httpx is monkeypatched at the module-level ``_build_client`` seam so the
tests never touch a real socket.
"""

from __future__ import annotations

import base64
import hashlib
from collections.abc import Callable, Iterator
from typing import Any

import httpx
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from hal0.api.middleware import error_codes
from hal0.api.plugins import manifest_proxy
from hal0.api.plugins.manifest_proxy import (
    _MANIFEST_CSP,
    _expected_integrity_for_asset,
    _manifest_cache_clear,
    _parse_integrity,
    _safe_plugin_asset_relpath,
    _safe_plugin_name,
    _verify_sri,
    router,
)

# ── helpers ─────────────────────────────────────────────────────────────


def _sri(body: bytes, alg: str = "sha384") -> str:
    """Return an SRI token of ``body`` using ``alg``."""
    digest = {
        "sha256": hashlib.sha256,
        "sha384": hashlib.sha384,
        "sha512": hashlib.sha512,
    }[alg](body).digest()
    return f"{alg}-{base64.b64encode(digest).decode()}"


def _build_app() -> FastAPI:
    app = FastAPI()
    error_codes.install(app)
    app.include_router(router)
    return app


@pytest.fixture
def app() -> FastAPI:
    return _build_app()


@pytest.fixture
def client(app: FastAPI) -> Iterator[TestClient]:
    with TestClient(app) as c:
        yield c


@pytest.fixture(autouse=True)
def _reset_manifest_cache() -> Iterator[None]:
    _manifest_cache_clear()
    yield
    _manifest_cache_clear()


def _patch_client(
    monkeypatch: pytest.MonkeyPatch,
    handler: Callable[[httpx.Request], httpx.Response],
) -> list[httpx.Request]:
    """Replace ``_build_client`` with one backed by an ``httpx.MockTransport``.

    Returns a list that receives every outbound request the proxy
    issues; assertions about header rewriting / URL targeting read off
    this list.
    """
    seen: list[httpx.Request] = []

    def _wrap(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return handler(request)

    def _factory(timeout: httpx.Timeout) -> httpx.AsyncClient:
        return httpx.AsyncClient(
            transport=httpx.MockTransport(_wrap),
            timeout=timeout,
        )

    monkeypatch.setattr(manifest_proxy, "_build_client", _factory)
    return seen


# ── _safe_plugin_asset_relpath ─────────────────────────────────────────


@pytest.mark.parametrize(
    "path",
    [
        "dist/index.js",
        "dist/style.css",
        "assets/icons/foo.svg",
        "index.js",
        "deep/nested/asset.json",
    ],
)
def test_safe_plugin_asset_relpath_accepts_normal_paths(path: str) -> None:
    assert _safe_plugin_asset_relpath(path) == path


@pytest.mark.parametrize(
    "path",
    [
        "/etc/passwd",  # absolute
        "../../../etc/passwd",  # naked traversal
        "dist/../../../etc/passwd",  # nested traversal escaping root
        "..",  # parent of root
        "",  # empty
        "   ",  # whitespace only
        "dist\\..\\..\\evil.js",  # windows-style backslashes
    ],
)
def test_safe_plugin_asset_relpath_rejects_traversal(path: str) -> None:
    assert _safe_plugin_asset_relpath(path) is None


def test_safe_plugin_asset_relpath_allows_internal_dotdot_that_stays_inside() -> None:
    # ``a/b/../c`` resolves to ``a/c`` — never escapes the synthetic root.
    assert _safe_plugin_asset_relpath("a/b/../c") == "a/b/../c"


def test_safe_plugin_asset_relpath_rejects_non_string() -> None:
    assert _safe_plugin_asset_relpath(None) is None  # type: ignore[arg-type]
    assert _safe_plugin_asset_relpath(42) is None  # type: ignore[arg-type]


# ── _safe_plugin_name ──────────────────────────────────────────────────


@pytest.mark.parametrize("name", ["kanban", "hal0-memory", "scope.v2", "ab"])
def test_safe_plugin_name_accepts_normal_names(name: str) -> None:
    assert _safe_plugin_name(name) is True


@pytest.mark.parametrize(
    "name",
    [
        "..",
        "../etc",
        "with space",
        "with/slash",
        "",
        "-leading-dash",
        ".leading-dot",
    ],
)
def test_safe_plugin_name_rejects_illegal_names(name: str) -> None:
    assert _safe_plugin_name(name) is False


# ── SRI parse + verify ─────────────────────────────────────────────────


def test_parse_integrity_accepts_sha256_sha384_sha512() -> None:
    body = b"hello"
    for alg in ("sha256", "sha384", "sha512"):
        token = _sri(body, alg)
        parsed = _parse_integrity(token)
        assert parsed is not None
        parsed_alg, _ = parsed
        assert parsed_alg == alg


def test_parse_integrity_rejects_unknown_algorithm() -> None:
    assert _parse_integrity("md5-AAAA") is None


def test_parse_integrity_rejects_malformed_base64() -> None:
    assert _parse_integrity("sha384-!!!not_base64!!!") is None


def test_parse_integrity_rejects_wrong_length_digest() -> None:
    short = base64.b64encode(b"\x00" * 10).decode()
    assert _parse_integrity(f"sha384-{short}") is None


def test_verify_sri_matches_correct_digest() -> None:
    body = b"console.log('plugin')"
    assert _verify_sri(body, _sri(body, "sha384")) is True


def test_verify_sri_rejects_modified_body() -> None:
    body = b"console.log('plugin')"
    bad = b"console.log('evil')"
    assert _verify_sri(bad, _sri(body, "sha384")) is False


def test_verify_sri_rejects_unparseable_integrity() -> None:
    assert _verify_sri(b"x", "not-an-integrity-token") is False


# ── _expected_integrity_for_asset ──────────────────────────────────────


def test_expected_integrity_uses_top_level_for_primary_entry() -> None:
    entry = {
        "name": "kanban",
        "entry": "dist/index.js",
        "integrity": "sha384-xxx",
    }
    assert _expected_integrity_for_asset(entry, "dist/index.js") == "sha384-xxx"


def test_expected_integrity_returns_none_for_non_primary_asset() -> None:
    entry = {
        "name": "kanban",
        "entry": "dist/index.js",
        "integrity": "sha384-xxx",
    }
    assert _expected_integrity_for_asset(entry, "dist/style.css") is None


def test_expected_integrity_uses_per_asset_integrity_map() -> None:
    entry = {
        "name": "kanban",
        "entry": "dist/index.js",
        "integrity_map": {
            "dist/index.js": "sha384-AAA",
            "dist/style.css": "sha384-BBB",
        },
    }
    assert _expected_integrity_for_asset(entry, "dist/style.css") == "sha384-BBB"


# ── manifest endpoint ──────────────────────────────────────────────────


def test_manifest_endpoint_returns_upstream_body_with_csp(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    upstream_manifest = [
        {
            "name": "kanban",
            "label": "Kanban",
            "entry": "dist/index.js",
            "integrity": "sha384-abc",
        }
    ]

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json=upstream_manifest)

    _patch_client(monkeypatch, handler)

    resp = client.get("/api/dashboard/plugins")
    assert resp.status_code == 200
    assert resp.json() == upstream_manifest
    assert resp.headers.get("content-security-policy") == _MANIFEST_CSP
    assert "no-store" in resp.headers.get("cache-control", "")


def test_manifest_endpoint_strips_auth_and_cookie_and_injects_agent(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json=[])

    seen = _patch_client(monkeypatch, handler)

    resp = client.get(
        "/api/dashboard/plugins",
        headers={
            "Authorization": "Bearer secret-do-not-leak",
            "Cookie": "hal0_session=do-not-leak",
        },
    )
    assert resp.status_code == 200

    assert len(seen) == 1
    upstream = seen[0]
    forwarded = {k.lower(): v for k, v in upstream.headers.items()}
    assert "authorization" not in forwarded
    assert "cookie" not in forwarded
    # ``X-hal0-Agent`` is the only outbound identity per ADR-0012.
    assert forwarded.get("x-hal0-agent") == "hermes"


def test_manifest_endpoint_503_when_upstream_unreachable(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("hermes is down")

    _patch_client(monkeypatch, handler)

    resp = client.get("/api/dashboard/plugins")
    assert resp.status_code == 503
    body = resp.json()
    assert body["error"]["code"] == "plugins.unavailable"


def test_manifest_endpoint_caches_for_asset_proxy(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    body = b"// plugin bundle"
    integrity = _sri(body, "sha384")
    upstream_manifest = [{"name": "kanban", "entry": "dist/index.js", "integrity": integrity}]

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/api/dashboard/plugins":
            return httpx.Response(200, json=upstream_manifest)
        if request.url.path == "/dashboard-plugins/kanban/dist/index.js":
            return httpx.Response(
                200, content=body, headers={"content-type": "application/javascript"}
            )
        return httpx.Response(404)

    seen = _patch_client(monkeypatch, handler)

    # Warm the cache via the manifest endpoint.
    assert client.get("/api/dashboard/plugins").status_code == 200
    pre = len(seen)

    # Now hit the asset endpoint — it must NOT re-fetch the manifest.
    resp = client.get("/dashboard-plugins/kanban/dist/index.js")
    assert resp.status_code == 200
    # One extra round trip — the asset fetch only.
    assert len(seen) == pre + 1


# ── asset endpoint ─────────────────────────────────────────────────────


def test_asset_endpoint_rejects_traversal_without_upstream_call(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        raise AssertionError("upstream should never be called")

    seen = _patch_client(monkeypatch, handler)

    resp = client.get("/dashboard-plugins/kanban/..%2F..%2Fetc%2Fpasswd")
    assert resp.status_code == 400
    assert resp.json()["error"]["code"] == "plugins.path_traversal"
    assert seen == []


def test_asset_endpoint_rejects_illegal_plugin_name(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        raise AssertionError("upstream should never be called")

    seen = _patch_client(monkeypatch, handler)

    # Send a control char via a percent-encoded sequence — but Starlette
    # normalises segments, so use a name that fails the regex directly.
    resp = client.get("/dashboard-plugins/-bad/dist/index.js")
    assert resp.status_code == 400
    assert resp.json()["error"]["code"] == "plugins.invalid_name"
    assert seen == []


def test_asset_endpoint_503_when_upstream_unreachable(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("hermes is down")

    _patch_client(monkeypatch, handler)

    resp = client.get("/dashboard-plugins/kanban/dist/index.js")
    assert resp.status_code == 503
    assert resp.json()["error"]["code"] == "plugins.unavailable"


def test_asset_endpoint_passes_bytes_through_when_sri_matches(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    body = b"// the genuine bundle\nwindow.__HAL0_PLUGINS__.register('kanban', x);"
    integrity = _sri(body, "sha384")
    manifest = [{"name": "kanban", "entry": "dist/index.js", "integrity": integrity}]

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/api/dashboard/plugins":
            return httpx.Response(200, json=manifest)
        if request.url.path == "/dashboard-plugins/kanban/dist/index.js":
            return httpx.Response(
                200,
                content=body,
                headers={"content-type": "application/javascript"},
            )
        return httpx.Response(404)

    _patch_client(monkeypatch, handler)

    # Pre-warm the manifest.
    client.get("/api/dashboard/plugins")

    resp = client.get("/dashboard-plugins/kanban/dist/index.js")
    assert resp.status_code == 200
    assert resp.content == body
    assert "immutable" in resp.headers.get("cache-control", "")


def test_asset_endpoint_502_on_sri_mismatch(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    genuine = b"// real bundle"
    tampered = b"// surprise!\nwindow.evil = true;"
    # Manifest declares SRI of the genuine bundle, upstream serves the
    # tampered one.
    integrity = _sri(genuine, "sha384")
    manifest = [{"name": "kanban", "entry": "dist/index.js", "integrity": integrity}]

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/api/dashboard/plugins":
            return httpx.Response(200, json=manifest)
        if request.url.path == "/dashboard-plugins/kanban/dist/index.js":
            return httpx.Response(
                200,
                content=tampered,
                headers={"content-type": "application/javascript"},
            )
        return httpx.Response(404)

    _patch_client(monkeypatch, handler)

    client.get("/api/dashboard/plugins")

    resp = client.get("/dashboard-plugins/kanban/dist/index.js")
    assert resp.status_code == 502
    assert resp.json()["error"]["code"] == "plugins.sri_mismatch"


def test_asset_endpoint_passes_through_when_no_integrity_declared(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    # Manifest does NOT declare integrity for this asset — we still
    # proxy (so the dashboard can decide whether to refuse to mount
    # client-side) but emit ``no-store`` so a swapped-in malicious
    # upstream cannot persist.
    body = b"/* css */"
    manifest = [{"name": "kanban", "entry": "dist/index.js"}]

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/api/dashboard/plugins":
            return httpx.Response(200, json=manifest)
        if request.url.path == "/dashboard-plugins/kanban/dist/style.css":
            return httpx.Response(200, content=body, headers={"content-type": "text/css"})
        return httpx.Response(404)

    _patch_client(monkeypatch, handler)

    client.get("/api/dashboard/plugins")

    resp = client.get("/dashboard-plugins/kanban/dist/style.css")
    assert resp.status_code == 200
    assert resp.content == body
    assert "no-store" in resp.headers.get("cache-control", "")


def test_asset_endpoint_strips_auth_and_injects_agent(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    body = b"// bundle"
    integrity = _sri(body, "sha384")
    manifest = [{"name": "kanban", "entry": "dist/index.js", "integrity": integrity}]

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/api/dashboard/plugins":
            return httpx.Response(200, json=manifest)
        return httpx.Response(200, content=body, headers={"content-type": "application/javascript"})

    seen = _patch_client(monkeypatch, handler)
    client.get("/api/dashboard/plugins")

    client.get(
        "/dashboard-plugins/kanban/dist/index.js",
        headers={
            "Authorization": "Bearer should-not-leak",
            "Cookie": "hal0_session=should-not-leak",
        },
    )

    # The asset fetch is the last call.
    asset_req = seen[-1]
    forwarded = {k.lower(): v for k, v in asset_req.headers.items()}
    assert "authorization" not in forwarded
    assert "cookie" not in forwarded
    assert forwarded.get("x-hal0-agent") == "hermes"


def test_agent_id_env_override(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("HAL0_AGENT_ID", "pi-coder")
    # Re-resolve at call time — the helper reads env dynamically.
    from hal0.api.plugins.manifest_proxy import _agent_id

    assert _agent_id() == "pi-coder"


def test_upstream_base_url_env_override(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("HERMES_DASHBOARD_BASE_URL", "http://127.0.0.1:9999/")
    from hal0.api.plugins.manifest_proxy import _upstream_base_url

    # Trailing slash stripped.
    assert _upstream_base_url() == "http://127.0.0.1:9999"


# ── upstream non-200 propagated ────────────────────────────────────────


def test_asset_endpoint_propagates_upstream_404(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/api/dashboard/plugins":
            return httpx.Response(200, json=[])
        return httpx.Response(404, content=b"missing")

    _patch_client(monkeypatch, handler)

    resp = client.get("/dashboard-plugins/kanban/dist/missing.js")
    assert resp.status_code == 404


# ── public surface guard ───────────────────────────────────────────────


def test_module_exports_expected_symbols() -> None:
    """Lock the public surface so refactors don't silently break tests."""
    public: list[Any] = manifest_proxy.__all__
    must = {
        "router",
        "_manifest_cache_clear",
        "_safe_plugin_asset_relpath",
        "_safe_plugin_name",
        "_parse_integrity",
        "_verify_sri",
        "_expected_integrity_for_asset",
        "_build_client",
        "_upstream_base_url",
        "_agent_id",
        "_MANIFEST_CSP",
    }
    assert must.issubset(set(public))
