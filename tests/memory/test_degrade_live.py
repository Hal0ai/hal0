"""#1301 (runtime half) — ``degraded`` must track the engine's LIVE state.

``tests/memory/test_hindsight_boot_probe.py`` covers the boot half: a bounded
``/health`` probe in ``_build_hindsight_client`` makes ``provider_from_config``'s
degrade ladder actually fire, so a daemon that is down *at boot* yields a
``PgVectorProvider`` with ``degraded=True`` instead of a live-but-broken
``HindsightProvider`` reporting healthy.

That is necessary and not sufficient. A boot probe answers one question, once,
and the answer goes stale immediately: a daemon that dies *after* boot leaves
the ``HindsightProvider`` in place, and a boot-only flag starts lying again the
moment it does. ``/api/status.memory_degraded`` and ``hal0 memory status`` then
report healthy while every recall comes back empty and every retain raises.

So ``HindsightProvider.degraded`` is a live property fed by
:meth:`HindsightProvider._call`, which every engine round-trip funnels through.
This file is the regression guard for that half only.

Two design rules are asserted here because both are load-bearing:

* the flag is **not a one-way latch** — a restarted daemon clears it, which a
  boot probe structurally cannot do;
* a 4xx does **not** degrade (the daemon answered — a stale key or a 404 on one
  bank of a delete sweep is not an outage) while a 5xx does, matching
  ``hindsight_client.probe_health``'s rule so boot and runtime cannot disagree.

The end-to-end ladder test at the bottom deliberately uses a REAL closed
loopback port rather than a patched ``probe_health``: the original bug was that
no I/O happened at all, and a test that stubs the I/O away cannot see that.
"""

from __future__ import annotations

import socket
from types import SimpleNamespace

import pytest

from hal0.memory import provider_from_config
from hal0.memory.hindsight_provider import HindsightProvider
from hal0.memory.pgvector_provider import PgVectorProvider


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


def _closed_port_url() -> str:
    """A loopback URL nothing is listening on (instant ECONNREFUSED)."""
    sock = socket.socket()
    sock.bind(("127.0.0.1", 0))
    port = sock.getsockname()[1]
    sock.close()
    return f"http://127.0.0.1:{port}"


class _FakeClient:
    """Minimal Hindsight client whose outcome the test controls."""

    def __init__(self, error: Exception | None = None) -> None:
        self.error = error

    async def recall(self, **_kwargs: object) -> dict[str, list[object]]:
        if self.error is not None:
            raise self.error
        return {"results": []}

    async def retain(self, **_kwargs: object) -> dict[str, str]:
        if self.error is not None:
            raise self.error
        return {"operation_id": "op-1"}


class _HttpError(Exception):
    """httpx.HTTPStatusError-shaped: the daemon answered, with a status."""

    def __init__(self, status: int) -> None:
        super().__init__(f"HTTP {status}")
        self.response = SimpleNamespace(status_code=status)


# ── the flag tracks the live engine state ────────────────────────────────────


@pytest.mark.asyncio
async def test_degraded_flips_true_when_engine_stops_answering() -> None:
    """THE regression: a daemon that dies after boot kept reporting healthy."""
    client = _FakeClient()
    p = HindsightProvider(client=client, client_id="hermes", unified_bank=True)

    await p.recall("q", dataset="shared", client_id="hermes")
    assert p.degraded is False  # boot state: engine answering

    client.error = ConnectionError("connection refused")
    with pytest.raises(ConnectionError):
        await p.recall("q", dataset="shared", client_id="hermes")

    assert p.degraded is True, "degraded still reports healthy for a dead engine"


@pytest.mark.asyncio
async def test_degraded_clears_when_engine_recovers() -> None:
    """Not a one-way latch: a restarted daemon clears it.

    This is the property a boot probe cannot have at all — it has no later
    observation to revise its answer with.
    """
    client = _FakeClient(error=ConnectionError("connection refused"))
    p = HindsightProvider(client=client, client_id="hermes", unified_bank=True)

    with pytest.raises(ConnectionError):
        await p.recall("q", dataset="shared", client_id="hermes")
    assert p.degraded is True

    client.error = None
    await p.recall("q", dataset="shared", client_id="hermes")
    assert p.degraded is False


@pytest.mark.asyncio
async def test_degraded_tracks_the_write_path_too() -> None:
    """``add`` goes through the same accounting.

    A failed retain is engine state, not merely a caller-visible exception —
    and writes are the calls whose silent loss is least recoverable.
    """
    client = _FakeClient(error=ConnectionError("connection refused"))
    p = HindsightProvider(client=client, client_id="hermes", unified_bank=True)

    with pytest.raises(ConnectionError):
        await p.add("x", dataset="shared", client_id="hermes")
    assert p.degraded is True


@pytest.mark.asyncio
async def test_http_4xx_does_not_mark_the_engine_degraded() -> None:
    """NEGATIVE CONTROL — a 404/401 means the daemon IS up.

    Without this the delete sweep's routine per-bank 404s would flap the flag
    on every scoped delete.
    """
    client = _FakeClient(error=_HttpError(404))
    p = HindsightProvider(client=client, client_id="hermes", unified_bank=True)

    with pytest.raises(_HttpError):
        await p.recall("q", dataset="shared", client_id="hermes")
    assert p.degraded is False


@pytest.mark.asyncio
async def test_http_5xx_marks_the_engine_degraded() -> None:
    """A daemon returning 5xx is not serving memory.

    Same rule as ``hindsight_client.probe_health``, so boot and runtime cannot
    disagree about what "up" means.
    """
    client = _FakeClient(error=_HttpError(503))
    p = HindsightProvider(client=client, client_id="hermes", unified_bank=True)

    with pytest.raises(_HttpError):
        await p.recall("q", dataset="shared", client_id="hermes")
    assert p.degraded is True


@pytest.mark.asyncio
async def test_status_reports_live_degrade() -> None:
    """End of the wire: ``/api/status``'s reader sees the live flag.

    ``_memory_degraded`` does ``getattr(provider, "degraded", False)``, so this
    is what actually decides the value an operator reads in the dashboard and
    in ``hal0 memory status``.
    """
    from hal0.api.routes.health import _memory_degraded

    client = _FakeClient()
    p = HindsightProvider(client=client, client_id="hermes", unified_bank=True)
    request = SimpleNamespace(app=SimpleNamespace(state=SimpleNamespace(memory_provider=p)))

    assert _memory_degraded(request) is False

    client.error = ConnectionError("connection refused")
    with pytest.raises(ConnectionError):
        await p.recall("q", dataset="shared", client_id="hermes")

    assert _memory_degraded(request) is True


# ── anti-vacuity: the boot ladder really does I/O ────────────────────────────


def test_boot_ladder_engages_against_a_real_closed_port(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Nothing below the factory is patched — including ``probe_health``.

    ``test_hindsight_boot_probe.py`` proves the ladder degrades when
    ``probe_health`` raises, by monkeypatching ``probe_health`` to raise. That
    is the right unit test, but it cannot distinguish "the probe ran and
    failed" from "the probe would never have reached the network anyway" —
    which is exactly the shape of the original #1301 bug (``from_env()`` did no
    I/O). Point the factory at a really-closed loopback port and require the
    real degrade.
    """
    monkeypatch.setenv("HAL0_HINDSIGHT_URL", _closed_port_url())

    provider = provider_from_config(_cfg("hindsight"))

    assert isinstance(provider, PgVectorProvider)
    assert provider.degraded is True


# ── #1613: the boot-degraded fallback must self-heal, closures included ──────


class _FakeDegraded:
    degraded = True

    def marker(self) -> str:
        return "degraded"


class _FakeHealthy:
    degraded = False

    def marker(self) -> str:
        return "healthy"


def test_self_heal_swaps_delegate_for_every_captured_reference(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import hal0.memory as mem

    shell = mem.SelfHealingMemoryProvider(_FakeDegraded(), _cfg("hindsight"))

    # A consumer closing over the provider at create_app time — the shape of
    # both MCP mounts and the in-process dispatcher. Must heal WITHOUT rebind.
    captured = (lambda p: lambda: p.marker())(shell)

    # Engine still down: heal fails, delegate unchanged.
    monkeypatch.setattr(mem, "provider_from_config", lambda cfg: _FakeDegraded())
    assert shell.try_heal() is False
    assert captured() == "degraded"

    # Engine back: one probe swaps the delegate; the closure sees it too.
    monkeypatch.setattr(mem, "provider_from_config", lambda cfg: _FakeHealthy())
    assert shell.try_heal() is True
    assert shell.degraded is False
    assert captured() == "healthy"
    assert isinstance(shell.target, _FakeHealthy)


def test_self_heal_is_a_noop_once_healthy(monkeypatch: pytest.MonkeyPatch) -> None:
    import hal0.memory as mem

    shell = mem.SelfHealingMemoryProvider(_FakeHealthy(), _cfg("hindsight"))

    def _boom(cfg: object) -> None:
        raise AssertionError("re-probe must not run for a healthy delegate")

    monkeypatch.setattr(mem, "provider_from_config", _boom)
    assert shell.try_heal() is True


def test_boot_degraded_result_wraps_end_to_end(monkeypatch: pytest.MonkeyPatch) -> None:
    """Real ladder against a closed port, then heal via a patched factory —
    proves the shell composes with the actual provider_from_config output."""
    import hal0.memory as mem

    monkeypatch.setenv("HAL0_HINDSIGHT_URL", _closed_port_url())
    provider = provider_from_config(_cfg("hindsight"))
    assert provider.degraded is True

    shell = mem.SelfHealingMemoryProvider(provider, _cfg("hindsight"))
    assert shell.degraded is True  # delegation before heal

    monkeypatch.setattr(mem, "provider_from_config", lambda cfg: _FakeHealthy())
    assert shell.try_heal() is True
    assert shell.degraded is False
