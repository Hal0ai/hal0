"""Tests for ``hermes_provision._dashboard_url`` (#1099 WS-C).

Acceptance criterion: "Hermes dashboard URL derives from
``/api/config/urls``." Precedence: an explicit ``HAL0_DASHBOARD_URL``
always wins; otherwise the local hal0-api's ``GET /api/config/urls`` is
queried for its ``api`` field; a failed/unreachable daemon falls back to
the legacy ``HAL0_API_URL`` env var / hardcoded default (unchanged
behaviour from before this issue).
"""

from __future__ import annotations

import json
from urllib.error import URLError

import pytest

from hal0.agents import hermes_provision as hp


def test_explicit_env_override_always_wins(monkeypatch: pytest.MonkeyPatch) -> None:
    """An explicit override short-circuits before any network call is made."""
    monkeypatch.setenv("HAL0_DASHBOARD_URL", "https://hal0.example.com/")

    def _boom(*_a: object, **_k: object) -> None:
        raise AssertionError("must not reach the network when an override is set")

    import urllib.request

    monkeypatch.setattr(urllib.request, "urlopen", _boom)
    assert hp._dashboard_url() == "https://hal0.example.com"


def test_derives_from_config_urls_endpoint(monkeypatch: pytest.MonkeyPatch) -> None:
    """Absent an override, the local /api/config/urls "api" field wins."""
    monkeypatch.delenv("HAL0_DASHBOARD_URL", raising=False)
    monkeypatch.delenv("HAL0_API_URL", raising=False)

    class _FakeResp:
        def __enter__(self) -> _FakeResp:
            return self

        def __exit__(self, *exc: object) -> None:
            return None

        def read(self) -> bytes:
            return json.dumps({"api": "http://10.0.1.5:8080"}).encode("utf-8")

    def fake_urlopen(req: object, timeout: float = 3.0) -> _FakeResp:
        assert "/api/config/urls" in req.full_url  # type: ignore[attr-defined]
        return _FakeResp()

    import urllib.request

    monkeypatch.setattr(urllib.request, "urlopen", fake_urlopen)
    assert hp._dashboard_url() == "http://10.0.1.5:8080"


def test_falls_back_when_daemon_unreachable(monkeypatch: pytest.MonkeyPatch) -> None:
    """An unreachable local daemon falls back to the legacy env/default chain."""
    monkeypatch.delenv("HAL0_DASHBOARD_URL", raising=False)
    monkeypatch.delenv("HAL0_API_URL", raising=False)

    def fake_urlopen(*_a: object, **_k: object) -> None:
        raise URLError("connection refused")

    import urllib.request

    monkeypatch.setattr(urllib.request, "urlopen", fake_urlopen)
    assert hp._dashboard_url() == "http://hal0.local:8080"


def test_falls_back_to_hal0_api_url_env_when_daemon_unreachable(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("HAL0_DASHBOARD_URL", raising=False)
    monkeypatch.setenv("HAL0_API_URL", "https://fallback.example.com/")

    def fake_urlopen(*_a: object, **_k: object) -> None:
        raise URLError("connection refused")

    import urllib.request

    monkeypatch.setattr(urllib.request, "urlopen", fake_urlopen)
    assert hp._dashboard_url() == "https://fallback.example.com"


def test_ignores_malformed_response_and_falls_back(monkeypatch: pytest.MonkeyPatch) -> None:
    """A 200 with an unexpected body shape falls back rather than raising."""
    monkeypatch.delenv("HAL0_DASHBOARD_URL", raising=False)
    monkeypatch.delenv("HAL0_API_URL", raising=False)

    class _FakeResp:
        def __enter__(self) -> _FakeResp:
            return self

        def __exit__(self, *exc: object) -> None:
            return None

        def read(self) -> bytes:
            return b"not json"

    def fake_urlopen(*_a: object, **_k: object) -> _FakeResp:
        return _FakeResp()

    import urllib.request

    monkeypatch.setattr(urllib.request, "urlopen", fake_urlopen)
    assert hp._dashboard_url() == "http://hal0.local:8080"
