"""Tests for ``GET /api/stats/throughput/history``.

Mounts the router on a bare FastAPI app (no lifespan, no full hal0 app)
so we can seed ``app.state.tps_events`` directly and assert on the
computed bucketing without any external dependencies.
"""

from __future__ import annotations

import time
from collections import defaultdict, deque
from collections.abc import Iterator

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from hal0.api.routes import throughput as throughput_routes


def _build_app() -> FastAPI:
    app = FastAPI()
    app.include_router(throughput_routes.router, prefix="/api")
    return app


@pytest.fixture
def app() -> FastAPI:
    return _build_app()


@pytest.fixture
def client(app: FastAPI) -> Iterator[TestClient]:
    with TestClient(app) as c:
        yield c


# ── helpers ───────────────────────────────────────────────────────────────────


def _seed_store(app: FastAPI, events: dict[str, list[tuple[float, int]]]) -> None:
    """Populate app.state.tps_events from a {slot_name: [(mono_ts, tokens)]} dict."""
    store: defaultdict = defaultdict(lambda: deque(maxlen=4096))
    for slot_name, ev_list in events.items():
        for mono_ts, tokens in ev_list:
            store[slot_name].append((mono_ts, tokens))
    app.state.tps_events = store


# ── test: empty store ─────────────────────────────────────────────────────────


def test_empty_store_returns_empty_samples(client: TestClient, app: FastAPI) -> None:
    """Missing / empty tps_events => samples:[], per_slot:{}."""
    # No tps_events set at all
    resp = client.get("/api/stats/throughput/history")
    assert resp.status_code == 200
    body = resp.json()
    assert body["samples"] == []
    assert body["per_slot"] == {}
    assert body["window_s"] == 100
    assert body["bucket_s"] == 5.0


def test_empty_deques_return_empty_samples(client: TestClient, app: FastAPI) -> None:
    """tps_events present but all deques empty => empty response."""
    store: defaultdict = defaultdict(lambda: deque(maxlen=4096))
    store["primary"]  # touch it — empty deque
    app.state.tps_events = store

    resp = client.get("/api/stats/throughput/history?buckets=10&window_s=60")
    assert resp.status_code == 200
    body = resp.json()
    assert body["samples"] == []
    assert body["per_slot"] == {}


# ── test: seeded data ─────────────────────────────────────────────────────────


def test_seeded_events_produce_correct_buckets(app: FastAPI, client: TestClient) -> None:
    """Two slots with known events land in the expected bins.

    Window: 100s, buckets: 20 => bucket_s = 5.0 s
    We place events at now-50s and now-10s (both within window).
    They must fall in different bins; total_tps / per_slot must match.
    """
    now_mono = time.monotonic()
    # slot A: 100 tokens at now-50s
    # slot B: 200 tokens at now-50s (same bin as A)
    # slot A: 50 tokens at now-10s (different bin)
    ts_old = now_mono - 50.0
    ts_new = now_mono - 10.0

    _seed_store(
        app,
        {
            "slotA": [(ts_old, 100), (ts_new, 50)],
            "slotB": [(ts_old, 200)],
        },
    )

    resp = client.get("/api/stats/throughput/history?buckets=20&window_s=100")
    assert resp.status_code == 200
    body = resp.json()

    assert body["window_s"] == 100
    assert body["bucket_s"] == 5.0
    samples = body["samples"]
    per_slot = body["per_slot"]

    # Must have exactly 2 non-empty bins
    assert len(samples) == 2

    # per_slot arrays aligned to samples (same length)
    assert len(per_slot["slotA"]) == len(samples)
    assert len(per_slot["slotB"]) == len(samples)

    # Samples ordered oldest → newest
    assert samples[0]["ts"] < samples[1]["ts"]

    # Bin 0 (older, ~now-50s): slotA=100 + slotB=200 => total=300 tokens / 5s = 60 tps
    bucket_s = 5.0
    assert pytest.approx(samples[0]["total_tps"], abs=0.01) == 300.0 / bucket_s
    assert samples[0]["serving_slots"] == 2

    # Bin 1 (newer, ~now-10s): slotA=50 => 50/5 = 10 tps
    assert pytest.approx(samples[1]["total_tps"], abs=0.01) == 50.0 / bucket_s
    assert samples[1]["serving_slots"] == 1

    # per_slot alignment
    assert pytest.approx(per_slot["slotA"][0], abs=0.01) == 100.0 / bucket_s
    assert pytest.approx(per_slot["slotA"][1], abs=0.01) == 50.0 / bucket_s
    assert pytest.approx(per_slot["slotB"][0], abs=0.01) == 200.0 / bucket_s
    assert pytest.approx(per_slot["slotB"][1], abs=0.01) == 0.0


def test_events_outside_window_excluded(app: FastAPI, client: TestClient) -> None:
    """Events older than window_s must not appear in output."""
    now_mono = time.monotonic()
    ts_inside = now_mono - 5.0  # within 30s window
    ts_outside = now_mono - 200.0  # outside 30s window

    _seed_store(
        app,
        {
            "slotA": [(ts_outside, 9999), (ts_inside, 100)],
        },
    )

    resp = client.get("/api/stats/throughput/history?buckets=6&window_s=30")
    assert resp.status_code == 200
    body = resp.json()
    samples = body["samples"]

    # Only the in-window event should produce a bin
    assert len(samples) == 1
    bucket_s = 30.0 / 6  # = 5.0
    assert pytest.approx(samples[0]["total_tps"], abs=0.01) == 100.0 / bucket_s


# ── test: param clamping ──────────────────────────────────────────────────────


def test_buckets_clamped_to_min(client: TestClient) -> None:
    resp = client.get("/api/stats/throughput/history?buckets=0&window_s=60")
    assert resp.status_code == 200
    body = resp.json()
    assert body["bucket_s"] == pytest.approx(60.0 / 1)


def test_buckets_clamped_to_max(client: TestClient) -> None:
    resp = client.get("/api/stats/throughput/history?buckets=9999&window_s=60")
    assert resp.status_code == 200
    body = resp.json()
    assert body["bucket_s"] == pytest.approx(60.0 / 120)


def test_window_clamped_to_min(client: TestClient) -> None:
    resp = client.get("/api/stats/throughput/history?buckets=10&window_s=1")
    assert resp.status_code == 200
    body = resp.json()
    assert body["window_s"] == 5


def test_window_clamped_to_max(client: TestClient) -> None:
    resp = client.get("/api/stats/throughput/history?buckets=10&window_s=99999")
    assert resp.status_code == 200
    body = resp.json()
    assert body["window_s"] == 3600
