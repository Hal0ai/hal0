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


@pytest.fixture(autouse=True)
def _reset_dashboard_url_cache(monkeypatch: pytest.MonkeyPatch) -> None:
    """Force a fresh network/fallback resolution in every test.

    ``_dashboard_url`` memoises its network/fallback result per-process
    (see its docstring — this is what fixes the STATE.md/HERMES.md
    idempotency flake this issue's network call introduced), so without
    resetting it here, whichever test runs first would poison every test
    after it with a stale cached value.
    """
    monkeypatch.setattr(hp, "_dashboard_url_cache", None)


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


def test_network_result_is_cached_across_calls(monkeypatch: pytest.MonkeyPatch) -> None:
    """The network/fallback resolution only hits urlopen once per process.

    This is the fix for the STATE.md/HERMES.md content-hash idempotency
    flake (#1099): ``render_live_context`` calls ``_dashboard_url`` on
    every invocation, but the value must be stable across nearby calls in
    the same process even if the daemon's reachability is momentarily
    flaky — otherwise "same substantive state, different clock-time ->
    NOT rewritten" trips over dashboard_url alone flapping.
    """
    monkeypatch.delenv("HAL0_DASHBOARD_URL", raising=False)
    monkeypatch.delenv("HAL0_API_URL", raising=False)

    calls = {"n": 0}

    class _FakeResp:
        def __enter__(self) -> _FakeResp:
            return self

        def __exit__(self, *exc: object) -> None:
            return None

        def read(self) -> bytes:
            return json.dumps({"api": "http://10.0.1.5:8080"}).encode("utf-8")

    def fake_urlopen(*_a: object, **_k: object) -> _FakeResp:
        calls["n"] += 1
        return _FakeResp()

    import urllib.request

    monkeypatch.setattr(urllib.request, "urlopen", fake_urlopen)
    first = hp._dashboard_url()
    second = hp._dashboard_url()
    assert first == second == "http://10.0.1.5:8080"
    assert calls["n"] == 1


def test_explicit_override_is_never_cached(monkeypatch: pytest.MonkeyPatch) -> None:
    """Unlike the network path, an explicit override is re-read every call."""
    monkeypatch.setenv("HAL0_DASHBOARD_URL", "https://first.example.com")
    assert hp._dashboard_url() == "https://first.example.com"
    monkeypatch.setenv("HAL0_DASHBOARD_URL", "https://second.example.com")
    assert hp._dashboard_url() == "https://second.example.com"
