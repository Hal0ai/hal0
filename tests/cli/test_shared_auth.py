"""CLI→API auth attachment (halo150 O2): _api_request sends a bearer token
discovered from env or api.env, so doctor/CLI probes work on auth-on boxes."""

from __future__ import annotations

import pytest

from hal0.cli import _shared


@pytest.fixture(autouse=True)
def _clean_env(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.delenv("HAL0_ADMIN_KEY", raising=False)
    monkeypatch.delenv("HAL0_CLIENT_KEY", raising=False)


def test_auth_headers_prefers_admin_env(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("HAL0_ADMIN_KEY", "adm-123")
    monkeypatch.setenv("HAL0_CLIENT_KEY", "cli-456")
    assert _shared._auth_headers() == {"Authorization": "Bearer adm-123"}


def test_auth_headers_falls_back_to_client_env(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("HAL0_CLIENT_KEY", "cli-456")
    assert _shared._auth_headers() == {"Authorization": "Bearer cli-456"}


def test_auth_headers_reads_api_env_file(
    monkeypatch: pytest.MonkeyPatch, tmp_path
) -> None:
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
        def __init__(self, timeout=None):
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
        def __init__(self, timeout=None):
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
