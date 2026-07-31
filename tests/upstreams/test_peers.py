"""hal0-peer classification + hal0-internal URL joining (issues #1425, #1427).

These are the *contract* tests for the egress fix: they assert on the URL
string a peer probe would produce, not on a log line, so a regression that
re-enables third-party egress fails here rather than in a journal grep.
"""

from __future__ import annotations

import pytest

from hal0.upstreams.peers import (
    hal0_peer_upstreams,
    is_hal0_peer,
    is_private_host,
    peer_api_url,
)
from hal0.upstreams.registry import Upstream, UpstreamRegistry

# ── the URL join ─────────────────────────────────────────────────────────────


@pytest.mark.parametrize(
    ("base", "suffix", "expected"),
    [
        # The canonical hal0 peer shape: /v1 is a sibling of /api.
        (
            "http://10.0.1.150:8080/v1",
            "/api/slots/metrics",
            "http://10.0.1.150:8080/api/slots/metrics",
        ),
        (
            "http://10.0.1.150:8080/v1/",
            "/api/stats/hardware",
            "http://10.0.1.150:8080/api/stats/hardware",
        ),
        # No /v1 at all.
        ("http://peer.lan:8080", "/api/slots/metrics", "http://peer.lan:8080/api/slots/metrics"),
        # THE #1427 BUG: base path already ends in /api, suffix starts with
        # /api/ → the naive concat produced /api/api/slots/metrics.
        (
            "https://openrouter.ai/api/v1",
            "/api/slots/metrics",
            "https://openrouter.ai/api/slots/metrics",
        ),
        (
            "https://openrouter.ai/api",
            "/api/stats/hardware",
            "https://openrouter.ai/api/stats/hardware",
        ),
        # Suffix without a leading slash is still joined correctly.
        ("http://peer.lan:8080/v1", "api/slots/metrics", "http://peer.lan:8080/api/slots/metrics"),
        # Query/fragment on the base URL never leak onto the joined path.
        (
            "http://peer.lan:8080/v1?x=1#frag",
            "/api/slots/metrics",
            "http://peer.lan:8080/api/slots/metrics",
        ),
        # Surrounding whitespace in hand-authored TOML.
        (
            "  http://peer.lan:8080/v1  ",
            "/api/slots/metrics",
            "http://peer.lan:8080/api/slots/metrics",
        ),
    ],
)
def test_peer_api_url_join(base: str, suffix: str, expected: str) -> None:
    assert peer_api_url(base, suffix) == expected


@pytest.mark.parametrize(
    "base",
    [
        "https://openrouter.ai/api/v1",
        "https://openrouter.ai/api",
        "https://openrouter.ai/api/",
        "https://api.minimax.io/v1",
        "http://10.0.1.150:8080/v1",
        "http://10.0.1.150:8080/api/v1",
        "http://peer.lan/",
        "http://peer.lan",
    ],
)
@pytest.mark.parametrize("suffix", ["/api/slots/metrics", "/api/stats/hardware"])
def test_no_doubled_api_segment_is_constructible(base: str, suffix: str) -> None:
    """No base URL shape may yield ``/api/api/`` — that was its own bug."""
    assert "/api/api/" not in peer_api_url(base, suffix)


# ── host classification ──────────────────────────────────────────────────────


@pytest.mark.parametrize(
    "host",
    [
        "127.0.0.1",
        "0.0.0.0",
        "10.0.1.150",
        "192.168.1.20",
        "172.16.4.9",
        "169.254.10.1",
        "::1",
        "[::1]",
        "fd00::1",
        "localhost",
        "hal0.localhost",
        "peer.local",
        "peer.lan",
        "peer.internal",
        "peer.home.arpa",
        "halo",
    ],
)
def test_private_hosts(host: str) -> None:
    assert is_private_host(host) is True


@pytest.mark.parametrize(
    "host",
    [
        "openrouter.ai",
        "api.minimax.io",
        "api.openai.com",
        "api.anthropic.com",
        "generativelanguage.googleapis.com",
        "8.8.8.8",
        "2606:4700::1111",
        "",
    ],
)
def test_public_hosts(host: str) -> None:
    assert is_private_host(host) is False


# ── peer eligibility ─────────────────────────────────────────────────────────


def _u(name: str, url: str, **kw: object) -> Upstream:
    return Upstream(name=name, kind=kw.pop("kind", "remote"), url=url, **kw)  # type: ignore[arg-type]


def test_third_party_providers_are_never_peers() -> None:
    for name, url in [
        ("openrouter", "https://openrouter.ai/api/v1"),
        ("minimax", "https://api.minimax.io/v1"),
        ("openai", "https://api.openai.com/v1"),
    ]:
        assert is_hal0_peer(_u(name, url)) is False, name


def test_private_remote_is_a_peer_by_default() -> None:
    """The haloai-style LAN fanout keeps working with no operator action."""
    assert is_hal0_peer(_u("peer", "http://10.0.1.150:8080/v1")) is True


def test_slot_kind_is_never_a_peer() -> None:
    """Pre-existing self-call carve-out: a slot upstream is THIS hal0."""
    u = _u("agent", "http://127.0.0.1:8087/v1", kind="slot", slot_name="agent")
    assert is_hal0_peer(u) is False


def test_disabled_upstream_is_never_a_peer() -> None:
    assert is_hal0_peer(_u("peer", "http://10.0.1.150:8080/v1", enabled=False)) is False


def test_explicit_flag_wins_both_ways() -> None:
    # Opt a public-hostname hal0 peer IN.
    assert is_hal0_peer(_u("peer", "https://hal0.example.com/v1", hal0_peer=True)) is True
    # Opt a private host OUT.
    assert is_hal0_peer(_u("nope", "http://10.0.1.150:8080/v1", hal0_peer=False)) is False
    # ...but an explicit flag never overrides the slot-kind self-call guard.
    u = _u("agent", "http://127.0.0.1:8087/v1", kind="slot", slot_name="agent", hal0_peer=True)
    assert is_hal0_peer(u) is False


def test_hal0_peer_upstreams_filters_a_real_registry() -> None:
    reg = UpstreamRegistry()
    reg.add(_u("openrouter", "https://openrouter.ai/api/v1"))
    reg.add(_u("minimax", "https://api.minimax.io/v1"))
    reg.add(_u("peer", "http://10.0.1.150:8080/v1"))
    reg.add(_u("agent", "http://127.0.0.1:8087/v1", kind="slot", slot_name="agent"))
    assert [u.name for u in hal0_peer_upstreams(reg)] == ["peer"]


def test_hal0_peer_survives_the_config_round_trip() -> None:
    from hal0.config.schema import UpstreamEntry
    from hal0.upstreams.registry import upstream_from_entry

    entry = UpstreamEntry(name="peer", url="https://hal0.example.com/v1", hal0_peer=True)
    assert upstream_from_entry(entry).hal0_peer is True
    # Unset stays tri-state None so the auto-derivation runs.
    assert (
        upstream_from_entry(UpstreamEntry(name="x", url="https://openrouter.ai/api/v1")).hal0_peer
        is None
    )
