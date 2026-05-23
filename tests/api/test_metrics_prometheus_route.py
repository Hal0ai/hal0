"""Tests for ``GET /api/metrics/prometheus`` (PR-12).

Validates the integration between the route, the lifespan-attached
metrics shim, and the text-exposition renderer. The shim itself is
unit-tested in ``tests/lemonade/test_metrics_shim.py`` — these tests
focus on the surface contract:

  * The route returns ``text/plain; version=0.0.4`` per Prometheus spec.
  * A missing shim (lifespan never attached one, e.g. Lemonade
    unreachable at boot) returns 200 with an empty body — scrapers
    treat that as "no series".
  * Synthetic snapshot data round-trips through the route exactly.
"""

from __future__ import annotations

from fastapi.testclient import TestClient

from hal0.lemonade.client import LemonadeClient
from hal0.lemonade.metrics_shim import MetricsShim


def test_route_returns_prometheus_content_type(client: TestClient) -> None:
    """text/plain with the version qualifier is the Prometheus contract.

    Scrapers look for ``version=0.0.4`` to pick the parser; without it
    some older collectors fall back to a chunked-binary format and
    silently drop the body.
    """
    resp = client.get("/api/metrics/prometheus")
    assert resp.status_code == 200
    content_type = resp.headers["content-type"]
    assert content_type.startswith("text/plain"), content_type
    assert "version=0.0.4" in content_type


def test_route_with_no_shim_returns_empty_body(client: TestClient) -> None:
    """Lemonade unreachable at boot → no shim on app.state → 200 with
    empty body. Empty Prometheus exposition = "no series", which is the
    correct first-boot signal."""
    # Force the shim off if the lifespan happened to attach one.
    if hasattr(client.app.state, "lemonade_metrics_shim"):
        client.app.state.lemonade_metrics_shim = None
    resp = client.get("/api/metrics/prometheus")
    assert resp.status_code == 200
    assert resp.text == ""


def test_route_renders_attached_shim_snapshot(client: TestClient) -> None:
    """When the lifespan attached a working shim, the route serialises its
    snapshot. We inject a synthetic shim so the test doesn't depend on
    a live lemond — the integration boundary is "route reads .snapshot()
    and renders text", which is exactly what we exercise here.
    """
    shim = MetricsShim(LemonadeClient())
    shim._stats = shim._stats.__class__(  # type: ignore[misc]
        time_to_first_token=0.2,
        tokens_per_second=42.0,
        prompt_tokens=128,
        output_tokens=32,
        input_tokens=128,
    )
    shim._health = shim._health.__class__(  # type: ignore[misc]
        loaded_models=("qwen3:4b",),
        max_models={"llm": 1},
    )
    shim.record_flm_metrics("agent", "gemma3:1b", {"kv_token_occupancy_rate_percentage": 50.0})

    client.app.state.lemonade_metrics_shim = shim
    resp = client.get("/api/metrics/prometheus")
    assert resp.status_code == 200
    body = resp.text
    assert 'hal0_lemonade_ttft_seconds{source="last_request"} 0.2' in body
    assert 'hal0_lemonade_decode_tokens_per_second{source="last_request"} 42' in body
    assert 'hal0_lemonade_models_loaded{model_name="qwen3:4b"} 1' in body
    assert 'hal0_lemonade_max_models{type="llm"} 1' in body
    assert 'hal0_lemonade_kv_occupancy_ratio{model_name="gemma3:1b",slot_name="agent"} 50' in body


def test_route_is_public(client: TestClient) -> None:
    """Like /api/status + /api/metrics, the Prometheus surface is public.

    Auth-gating would block standard Prometheus scrapers that don't
    speak hal0's bearer-token auth. Operators harden via a reverse
    proxy if they want to limit scraper access. Verified by hitting
    the route without any Authorization header.
    """
    resp = client.get("/api/metrics/prometheus")
    # 200 even without credentials — public route.
    assert resp.status_code == 200
