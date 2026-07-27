"""#1301 — the hindsight→pgvector degrade ladder must actually fire.

``provider_from_config`` wraps ``_build_hindsight_client`` in a try/except
that is supposed to catch an unreachable daemon and fall back to the
in-memory ``PgVectorProvider``. It never fired: ``_build_hindsight_client``
only called ``HindsightRestClient.from_env()``, which constructs an httpx
client and does **no I/O**. A daemon that was down produced a live-but-broken
``HindsightProvider`` with ``degraded=False``; failures surfaced later as
empty recalls while ``GET /api/status.memory_degraded`` and
``hal0 memory status`` both reported healthy.

The fix is a cheap, timeout-bounded ``/health`` probe at construction time —
the same endpoint the installer waits on (``installer/install.sh``).
"""

from __future__ import annotations

from types import SimpleNamespace

import httpx
import pytest

from hal0.memory import _build_hindsight_client, provider_from_config
from hal0.memory.hindsight_client import HindsightUnreachable, probe_health


def _cfg(engine: str = "hindsight") -> SimpleNamespace:
    return SimpleNamespace(
        memory=SimpleNamespace(
            engine=engine,
            embedding=SimpleNamespace(
                rerank_gateway_url="http://127.0.0.1:8080",
                rerank_model="builtin.jina-reranker-v1-tiny-en-q8",
                rerank_connect_timeout_s=1.0,
                rerank_read_timeout_s=8.0,
            ),
            graph=SimpleNamespace(enabled=False, extraction_slot="utility"),
        )
    )


# ── the probe itself ─────────────────────────────────────────────────────────


def test_probe_health_accepts_a_live_daemon() -> None:
    seen: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request.url.path)
        return httpx.Response(200, json={"status": "ok"})

    probe_health(
        base_url="http://127.0.0.1:9177",
        api_key="hal0-local-noauth",
        transport=httpx.MockTransport(handler),
    )
    assert seen == ["/health"]


def test_probe_health_raises_when_daemon_refuses_connection() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("connection refused", request=request)

    with pytest.raises(HindsightUnreachable):
        probe_health(
            base_url="http://127.0.0.1:9177",
            api_key="k",
            transport=httpx.MockTransport(handler),
        )


def test_probe_health_raises_on_server_error() -> None:
    """A daemon that answers 503 is up but not serving — still degrade."""

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(503, text="starting")

    with pytest.raises(HindsightUnreachable):
        probe_health(
            base_url="http://127.0.0.1:9177",
            api_key="k",
            transport=httpx.MockTransport(handler),
        )


def test_probe_health_tolerates_auth_rejection() -> None:
    """401/403 proves the daemon is up and answering; auth is a separate
    concern from reachability and must not trip the degrade ladder."""

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(401, text="unauthorized")

    probe_health(
        base_url="http://127.0.0.1:9177",
        api_key="wrong",
        transport=httpx.MockTransport(handler),
    )


def test_probe_health_is_timeout_bounded() -> None:
    """Boot must not block on a hung daemon — a read timeout degrades."""

    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ReadTimeout("too slow", request=request)

    with pytest.raises(HindsightUnreachable):
        probe_health(
            base_url="http://127.0.0.1:9177",
            api_key="k",
            timeout_s=0.05,
            transport=httpx.MockTransport(handler),
        )


def test_probe_timeout_default_is_short_enough_for_boot() -> None:
    from hal0.memory.hindsight_client import DEFAULT_PROBE_TIMEOUT_S

    assert 0 < DEFAULT_PROBE_TIMEOUT_S <= 5.0


# ── the ladder it arms ───────────────────────────────────────────────────────


def test_build_client_raises_when_daemon_is_down(monkeypatch: pytest.MonkeyPatch) -> None:
    """The self-flagged P1-6 gap: construction must do I/O and raise."""

    def _boom(**_kwargs: object) -> None:
        raise HindsightUnreachable("no daemon")

    monkeypatch.setattr("hal0.memory.hindsight_client.probe_health", _boom)
    with pytest.raises(HindsightUnreachable):
        _build_hindsight_client(_cfg())


def test_factory_degrades_when_daemon_is_down(monkeypatch: pytest.MonkeyPatch) -> None:
    """End of the ladder: a down daemon yields a provider that says so."""
    from hal0.memory.pgvector_provider import PgVectorProvider

    def _boom(**_kwargs: object) -> None:
        raise HindsightUnreachable("no daemon")

    monkeypatch.setattr("hal0.memory.hindsight_client.probe_health", _boom)
    provider = provider_from_config(_cfg())

    assert isinstance(provider, PgVectorProvider)
    assert provider.degraded is True


def test_factory_keeps_hindsight_when_daemon_is_up(monkeypatch: pytest.MonkeyPatch) -> None:
    from hal0.memory.hindsight_provider import HindsightProvider

    monkeypatch.setattr("hal0.memory.hindsight_client.probe_health", lambda **_k: None)
    provider = provider_from_config(_cfg())

    assert isinstance(provider, HindsightProvider)
    assert not getattr(provider, "degraded", False)


def test_degrade_logs_the_reason(monkeypatch: pytest.MonkeyPatch) -> None:
    """An operator reading journald must see WHY memory went volatile."""
    from structlog.testing import capture_logs

    def _boom(**_kwargs: object) -> None:
        raise HindsightUnreachable("connect refused to http://127.0.0.1:9177/health")

    monkeypatch.setattr("hal0.memory.hindsight_client.probe_health", _boom)
    with capture_logs() as logs:
        provider_from_config(_cfg())

    events = [e for e in logs if e.get("event") == "hal0.memory.hindsight_unavailable"]
    assert events, f"no unavailability warning logged; got {[e.get('event') for e in logs]}"
    assert "9177" in str(events[0].get("error", ""))
