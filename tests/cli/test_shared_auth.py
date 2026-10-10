"""CLI→API auth attachment (halo150 O2): _api_request sends a bearer token
discovered from env or api.env, so doctor/CLI probes work on auth-on boxes."""

from __future__ import annotations

import httpx
import pytest

from hal0.cli import _shared


@pytest.fixture(autouse=True)
def _clean_env(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.delenv("HAL0_ADMIN_KEY", raising=False)
    monkeypatch.delenv("HAL0_CLIENT_KEY", raising=False)
    monkeypatch.delenv("HAL0_API_URL", raising=False)


def test_auth_headers_prefers_admin_env(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("HAL0_ADMIN_KEY", "adm-123")
    monkeypatch.setenv("HAL0_CLIENT_KEY", "cli-456")
    assert _shared._auth_headers() == {"Authorization": "Bearer adm-123"}


def test_auth_headers_falls_back_to_client_env(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("HAL0_CLIENT_KEY", "cli-456")
    assert _shared._auth_headers() == {"Authorization": "Bearer cli-456"}


def test_auth_headers_reads_api_env_file(monkeypatch: pytest.MonkeyPatch, tmp_path) -> None:
    etc = tmp_path / "etc"
    etc.mkdir()
    (etc / "api.env").write_text(
        "# comment\nHAL0_PORT=8080\nHAL0_ADMIN_KEY=file-adm\nHAL0_CLIENT_KEY=file-cli\n"
    )
    from hal0.config import paths as cfg_paths

    monkeypatch.setattr(cfg_paths, "etc", lambda: etc)
    assert _shared._auth_headers() == {"Authorization": "Bearer file-adm"}


def test_auth_headers_empty_when_nothing_discoverable(
    monkeypatch: pytest.MonkeyPatch, tmp_path
) -> None:
    from hal0.config import paths as cfg_paths

    monkeypatch.setattr(cfg_paths, "etc", lambda: tmp_path / "nope")
    assert _shared._auth_headers() == {}


def test_api_request_attaches_bearer(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("HAL0_ADMIN_KEY", "adm-789")
    seen: dict = {}

    class _Resp:
        status_code = 200
        content = b"{}"

        def json(self):
            return {}

    class _Client:
        def __init__(self, timeout=None, **_kw):
            pass

        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

        def request(self, method, url, **kw):
            seen.update(kw)
            return _Resp()

    monkeypatch.setattr(_shared.httpx, "Client", _Client)
    _shared.api_get("/api/slots")
    assert seen["headers"]["Authorization"] == "Bearer adm-789"


def test_api_request_respects_caller_authorization(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("HAL0_ADMIN_KEY", "adm-789")
    seen: dict = {}

    class _Resp:
        status_code = 200
        content = b"{}"

        def json(self):
            return {}

    class _Client:
        def __init__(self, timeout=None, **_kw):
            pass

        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

        def request(self, method, url, **kw):
            seen.update(kw)
            return _Resp()

    monkeypatch.setattr(_shared.httpx, "Client", _Client)
    _shared.api_get("/api/slots", headers={"Authorization": "Bearer mine"})
    assert seen["headers"]["Authorization"] == "Bearer mine"


# ── a discovered key never leaves the box (#2499) ─────────────────────────────

REMOTE = "http://203.0.113.7:8080"  # TEST-NET-3, never routable


@pytest.fixture
def api_env_keys(monkeypatch: pytest.MonkeyPatch, tmp_path):
    """api.env holds both box keys; nothing is exported in the env."""
    etc = tmp_path / "etc"
    etc.mkdir()
    (etc / "api.env").write_text("HAL0_ADMIN_KEY=file-adm\nHAL0_CLIENT_KEY=file-cli\n")
    from hal0.config import paths as cfg_paths

    monkeypatch.setattr(cfg_paths, "etc", lambda: etc)


@pytest.mark.usefixtures("api_env_keys")
@pytest.mark.parametrize(
    "url",
    ["http://127.0.0.1:8080", "http://localhost:8080/api/slots", "http://[::1]:8080"],
)
def test_discovered_key_sent_to_loopback(url: str) -> None:
    assert _shared._auth_headers(url) == {"Authorization": "Bearer file-adm"}
    assert not _shared._discovered_key_withheld(url)


@pytest.mark.usefixtures("api_env_keys")
@pytest.mark.parametrize(
    "url",
    [
        REMOTE,
        "http://127.0.0.2:8080",
        "http://127.0.0.1.evil.example:8080",
        "http://evil@127.0.0.1:8080",  # userinfo is not loopback
        "http://:pw@localhost:8080",
    ],
)
def test_discovered_key_withheld_from_non_loopback(url: str) -> None:
    assert _shared._auth_headers(url) == {}
    assert _shared._discovered_key_withheld(url)


@pytest.mark.usefixtures("api_env_keys")
def test_default_target_follows_hal0_api_url(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("HAL0_API_URL", REMOTE)
    assert _shared._auth_headers() == {}
    monkeypatch.setenv("HAL0_API_URL", "http://127.0.0.1:9000")
    assert _shared._auth_headers() == {"Authorization": "Bearer file-adm"}


@pytest.mark.usefixtures("api_env_keys")
@pytest.mark.parametrize(
    ("var", "value"), [("HAL0_ADMIN_KEY", "env-adm"), ("HAL0_CLIENT_KEY", "env-cli")]
)
def test_explicit_env_key_sent_to_remote(
    monkeypatch: pytest.MonkeyPatch, var: str, value: str
) -> None:
    monkeypatch.setenv(var, value)
    assert _shared._auth_headers(REMOTE) == {"Authorization": f"Bearer {value}"}
    assert not _shared._discovered_key_withheld(REMOTE)


def _mock_client(monkeypatch: pytest.MonkeyPatch, handler) -> list[httpx.Request]:
    """Back every ``httpx.Client`` in _shared with ``handler``, keeping the
    caller's own kwargs (``follow_redirects`` above all) intact."""
    seen: list[httpx.Request] = []
    real = httpx.Client

    def _handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return handler(request)

    def _factory(*args, **kwargs):
        return real(*args, transport=httpx.MockTransport(_handler), **kwargs)

    monkeypatch.setattr(_shared.httpx, "Client", _factory)
    return seen


@pytest.mark.usefixtures("api_env_keys")
def test_api_get_to_remote_carries_no_discovered_key(monkeypatch: pytest.MonkeyPatch) -> None:
    seen = _mock_client(monkeypatch, lambda r: httpx.Response(200, json={}))
    _shared.api_get("/api/slots", base=REMOTE)
    assert "authorization" not in seen[0].headers


@pytest.mark.usefixtures("api_env_keys")
def test_api_get_to_loopback_carries_discovered_key(monkeypatch: pytest.MonkeyPatch) -> None:
    seen = _mock_client(monkeypatch, lambda r: httpx.Response(200, json={}))
    _shared.api_get("/api/slots", base="http://127.0.0.1:8080")
    assert seen[0].headers["authorization"] == "Bearer file-adm"


@pytest.mark.usefixtures("api_env_keys")
def test_api_request_does_not_follow_redirects(monkeypatch: pytest.MonkeyPatch) -> None:
    """A loopback API answering 3xx must not walk the bearer to the Location."""

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.host == "127.0.0.1":
            return httpx.Response(307, headers={"Location": f"{REMOTE}/api/slots"})
        return httpx.Response(200, json={})

    seen = _mock_client(monkeypatch, handler)
    assert _shared.api_get("/api/slots", base="http://127.0.0.1:8080") is None
    assert [r.url.host for r in seen] == ["127.0.0.1"]


@pytest.mark.usefixtures("api_env_keys")
def test_auth_client_and_stream_do_not_follow_redirects(monkeypatch: pytest.MonkeyPatch) -> None:
    seen = _mock_client(
        monkeypatch,
        lambda r: httpx.Response(302, headers={"Location": f"{REMOTE}/x"}),
    )
    with _shared.api_stream("GET", "/api/logs/stream", base="http://127.0.0.1:8080") as resp:
        assert resp.status_code == 302
    with _shared.auth_client(target="http://127.0.0.1:8080") as client:
        assert client.follow_redirects is False
    assert [r.url.host for r in seen] == ["127.0.0.1"]


@pytest.mark.usefixtures("api_env_keys")
def test_auth_client_targets_remote_without_discovered_key() -> None:
    with _shared.auth_client(target=REMOTE) as client:
        assert "authorization" not in client.headers
    with _shared.auth_client(target="http://127.0.0.1:8080") as client:
        assert client.headers["authorization"] == "Bearer file-adm"


@pytest.mark.usefixtures("api_env_keys")
def test_401_from_remote_hints_at_withheld_key(monkeypatch: pytest.MonkeyPatch) -> None:
    _mock_client(monkeypatch, lambda r: httpx.Response(401, json={"error": {"message": "no"}}))
    with pytest.raises(_shared.CliApiError) as exc:
        _shared.api_get("/api/slots", base=REMOTE)
    msg = str(exc.value)
    assert _shared.REMOTE_KEY_HINT in msg
    assert "file-adm" not in msg and "file-cli" not in msg


@pytest.mark.usefixtures("api_env_keys")
def test_401_hint_absent_when_key_was_sent_or_explicit(monkeypatch: pytest.MonkeyPatch) -> None:
    _mock_client(monkeypatch, lambda r: httpx.Response(401, json={}))
    with pytest.raises(_shared.CliApiError) as exc:
        _shared.api_get("/api/slots", base="http://127.0.0.1:8080")
    assert _shared.REMOTE_KEY_HINT not in str(exc.value)
    monkeypatch.setenv("HAL0_ADMIN_KEY", "env-adm")
    with pytest.raises(_shared.CliApiError) as exc:
        _shared.api_get("/api/slots", base=REMOTE)
    assert _shared.REMOTE_KEY_HINT not in str(exc.value)
    assert _shared.auth_hint(403, REMOTE) == ""
