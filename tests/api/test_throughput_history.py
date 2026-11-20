"""Tests for ``GET /api/stats/throughput/history``.

Mounts the router on a bare FastAPI app (no lifespan, no full hal0 app)
so we can seed ``app.state.tps_events`` directly and assert on the
computed bucketing without any external dependencies.
"""

from __future__ import annotations

import time
from collections import defaultdict, deque
from collections.abc import Iterator
from itertools import pairwise

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
    all_samples = body["samples"]
    per_slot = body["per_slot"]

    # Leading empty bins are omitted: the first sample is the oldest event's
    # bin. From there every bin is emitted, empty ones as a measured 0 (#3859).
    assert all_samples[0]["total_tps"] > 0
    assert all_samples[-1]["total_tps"] == 0.0
    assert all_samples[-1]["serving_slots"] == 0
    nonzero = [i for i, s in enumerate(all_samples) if s["total_tps"] > 0]
    # Exactly 2 non-empty bins
    assert len(nonzero) == 2
    samples = [all_samples[i] for i in nonzero]

    # per_slot arrays aligned to samples (same length)
    assert len(per_slot["slotA"]) == len(all_samples)
    assert len(per_slot["slotB"]) == len(all_samples)

    # Samples ordered oldest → newest
    assert all(a["ts"] < b["ts"] for a, b in pairwise(all_samples))

    # Bin 0 (older, ~now-50s): slotA=100 + slotB=200 => total=300 tokens / 5s = 60 tps
    bucket_s = 5.0
    assert pytest.approx(samples[0]["total_tps"], abs=0.01) == 300.0 / bucket_s
    assert samples[0]["serving_slots"] == 2

    # Bin 1 (newer, ~now-10s): slotA=50 => 50/5 = 10 tps
    assert pytest.approx(samples[1]["total_tps"], abs=0.01) == 50.0 / bucket_s
    assert samples[1]["serving_slots"] == 1

    # per_slot alignment
    i0, i1 = nonzero
    assert pytest.approx(per_slot["slotA"][i0], abs=0.01) == 100.0 / bucket_s
    assert pytest.approx(per_slot["slotA"][i1], abs=0.01) == 50.0 / bucket_s
    assert pytest.approx(per_slot["slotB"][i0], abs=0.01) == 200.0 / bucket_s
    assert pytest.approx(per_slot["slotB"][i1], abs=0.01) == 0.0


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


# ── stall-guard frame exclusion (#1893 / PR #1918 review) ─────────────────────


@pytest.mark.asyncio
async def test_stall_guard_frame_excluded_from_throughput_and_ttft() -> None:
    """The dispatcher's synthetic stall frame carries a ``"delta":`` but is
    not generated output: it must record neither a token event nor a TTFT
    sample (for a silent upstream that would be a bogus ~idle-timeout TTFT)."""
    from types import SimpleNamespace

    from starlette.responses import StreamingResponse

    from hal0.api.routes.v1 import _instrument_streaming_throughput

    stall_frame = (
        b'\n\ndata: {"id":"hal0-stall-guard","object":"chat.completion.chunk",'
        b'"choices":[{"index":0,"delta":{"role":"assistant","content":"[hal0] stall"},'
        b'"finish_reason":"length"}],'
        b'"x_hal0_stall":{"reason":"idle","elapsed_s":300.0}}\n\ndata: [DONE]\n\n'
    )

    async def _gen():
        yield b'data: {"choices":[{"delta":{"content":"hi"}}]}\n\n'
        yield stall_frame

    app_state = SimpleNamespace(
        tps_events=defaultdict(lambda: deque(maxlen=64)),
        ttft_events=defaultdict(lambda: deque(maxlen=64)),
    )
    response = StreamingResponse(_gen(), media_type="text/event-stream")
    wrapped = _instrument_streaming_throughput(
        response, app_state, "primary", dispatch_started=time.monotonic()
    )
    async for _ in wrapped.body_iterator:
        pass

    # Only the real upstream delta counted — one token event, one TTFT sample.
    assert [tokens for _, tokens in app_state.tps_events["primary"]] == [1]
    assert len(app_state.ttft_events["primary"]) == 1


@pytest.mark.asyncio
async def test_stall_guard_frame_alone_records_nothing() -> None:
    """A guard-cut silent upstream yields only the synthetic frame — no token
    events and, crucially, no TTFT sample at all."""
    from types import SimpleNamespace

    from starlette.responses import StreamingResponse

    from hal0.api.routes.v1 import _instrument_streaming_throughput

    async def _gen():
        yield (
            b'\n\ndata: {"id":"hal0-stall-guard","object":"chat.completion.chunk",'
            b'"choices":[{"index":0,"delta":{"role":"assistant","content":"[hal0] stall"},'
            b'"finish_reason":"length"}],'
            b'"x_hal0_stall":{"reason":"total","elapsed_s":900.0}}\n\ndata: [DONE]\n\n'
        )

    app_state = SimpleNamespace(
        tps_events=defaultdict(lambda: deque(maxlen=64)),
        ttft_events=defaultdict(lambda: deque(maxlen=64)),
    )
    response = StreamingResponse(_gen(), media_type="text/event-stream")
    wrapped = _instrument_streaming_throughput(
        response, app_state, "primary", dispatch_started=time.monotonic()
    )
    async for _ in wrapped.body_iterator:
        pass

    assert len(app_state.tps_events["primary"]) == 0
    assert len(app_state.ttft_events["primary"]) == 0


def test_v1_records_draft_timings_nonstreaming() -> None:
    from types import SimpleNamespace

    from hal0.api.routes.v1 import _record_nonstreaming_throughput

    app_state = SimpleNamespace(
        tps_events=defaultdict(lambda: deque(maxlen=64)),
        spec_events=defaultdict(lambda: deque(maxlen=64)),
    )
    body = b'{"usage":{"completion_tokens":20},"timings":{"draft_n":12,"draft_n_accepted":9}}'
    _record_nonstreaming_throughput(body, app_state, "primary")
    ((_, drafted, accepted),) = app_state.spec_events["primary"]
    assert (drafted, accepted) == (12, 9)


def test_v1_no_draft_timings_records_nothing() -> None:
    from types import SimpleNamespace

    from hal0.api.routes.v1 import _record_nonstreaming_throughput

    app_state = SimpleNamespace(
        tps_events=defaultdict(lambda: deque(maxlen=64)),
        spec_events=defaultdict(lambda: deque(maxlen=64)),
    )
    _record_nonstreaming_throughput(
        b'{"usage":{"completion_tokens":20},"timings":{"predicted_n":20}}',
        app_state,
        "primary",
    )
    assert len(app_state.spec_events["primary"]) == 0


@pytest.mark.asyncio
async def test_v1_records_draft_timings_streaming() -> None:
    from types import SimpleNamespace

    from starlette.responses import StreamingResponse

    from hal0.api.routes.v1 import _instrument_streaming_throughput

    async def _gen():
        yield b'data: {"choices":[{"delta":{"content":"hi"}}]}\n\n'
        yield (
            b'data: {"choices":[{"delta":{}}],'
            b'"timings":{"draft_n":8,"draft_n_accepted":5}}\n\ndata: [DONE]\n\n'
        )

    app_state = SimpleNamespace(
        tps_events=defaultdict(lambda: deque(maxlen=64)),
        spec_events=defaultdict(lambda: deque(maxlen=64)),
    )
    wrapped = _instrument_streaming_throughput(
        StreamingResponse(_gen(), media_type="text/event-stream"), app_state, "primary"
    )
    async for _ in wrapped.body_iterator:
        pass
    ((_, drafted, accepted),) = app_state.spec_events["primary"]
    assert (drafted, accepted) == (8, 5)


# ── #3859: non-streaming over-read + stale hero ───────────────────────────────


def _freeze_clock(monkeypatch: pytest.MonkeyPatch, mono: float, epoch: float) -> None:
    """Pin the endpoint's clocks so bin edges are deterministic."""
    from types import SimpleNamespace

    monkeypatch.setattr(
        throughput_routes, "time", SimpleNamespace(monotonic=lambda: mono, time=lambda: epoch)
    )


def _nonstreaming_state() -> object:
    from types import SimpleNamespace

    return SimpleNamespace(
        tps_events=defaultdict(lambda: deque(maxlen=4096)),
        spec_events=defaultdict(lambda: deque(maxlen=64)),
    )


def test_nonstreaming_completion_spread_over_upstream_decode_time(
    app: FastAPI, client: TestClient
) -> None:
    """963 tokens decoded at 27.8 tok/s must not read 963 / 5 = 192.6 tok/s.

    llama-server reports the decode duration in ``timings.predicted_ms``; the
    recorder spreads the tokens across that span instead of dropping all of
    them into the bin the response finished in.
    """
    from hal0.api.routes.v1 import _record_nonstreaming_throughput

    state = _nonstreaming_state()
    decode_ms = 963 / 27.8 * 1000
    body = (
        b'{"usage":{"completion_tokens":963},'
        b'"timings":{"predicted_n":963,"predicted_ms":' + str(decode_ms).encode() + b"}}"
    )
    _record_nonstreaming_throughput(body, state, "primary")
    events = list(state.tps_events["primary"])
    assert sum(tok for _, tok in events) == 963
    assert len(events) > 1
    span = max(ts for ts, _ in events) - min(ts for ts, _ in events)
    assert span == pytest.approx(decode_ms / 1000, abs=1.5)

    app.state.tps_events = state.tps_events
    samples = client.get("/api/stats/throughput/history").json()["samples"]
    peak = max(s["total_tps"] for s in samples)
    assert peak < 35.0, f"non-streaming completion over-read as {peak} tok/s"
    assert peak > 20.0


def test_nonstreaming_completion_without_timings_uses_dispatch_start() -> None:
    """No upstream timings: spread over the wall time since dispatch."""
    from hal0.api.routes.v1 import _record_nonstreaming_throughput

    state = _nonstreaming_state()
    started = time.monotonic() - 30.0
    _record_nonstreaming_throughput(
        b'{"usage":{"completion_tokens":600}}', state, "primary", dispatch_started=started
    )
    events = list(state.tps_events["primary"])
    assert sum(tok for _, tok in events) == 600
    first = min(ts for ts, _ in events)
    assert first >= started
    assert max(ts for ts, _ in events) - first == pytest.approx(30.0, abs=1.5)


def test_nonstreaming_completion_uses_flm_decoding_speed() -> None:
    """FLM reports ``usage.decoding_speed_tps``: decode span = tokens / speed."""
    from hal0.api.routes.v1 import _record_nonstreaming_throughput

    state = _nonstreaming_state()
    _record_nonstreaming_throughput(
        b'{"usage":{"completion_tokens":200,"decoding_speed_tps":10.0}}', state, "npu"
    )
    events = list(state.tps_events["npu"])
    assert sum(tok for _, tok in events) == 200
    span = max(ts for ts, _ in events) - min(ts for ts, _ in events)
    assert span == pytest.approx(20.0, abs=1.5)


def test_tps_from_events_tolerates_backdated_events() -> None:
    """A spread non-streaming completion appends events older than a
    concurrent stream's last one; the rate uses the true min/max span."""
    from hal0.slots.metrics_collect import tps_from_events

    now = time.monotonic()
    # stream events at now-1 .. now, then a backdated completion's events
    events = [(now - 1.0, 10), (now, 10), (now - 4.0, 10), (now - 3.0, 10)]
    # 40 tokens over a 4 s span, not 40 / (now-3 - (now-1)) misordered
    assert tps_from_events(events, window_s=5.0) == pytest.approx(10.0, rel=0.05)


def test_stream_finished_60s_ago_reads_zero_now(
    app: FastAPI, client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A stream that ended 60 s ago must not stay on as the hero's last sample."""
    mono_now = 10_000.0
    _freeze_clock(monkeypatch, mono_now, 1_700_000_002.0)
    end = mono_now - 60.0
    duration = 963 / 27.8
    n = 963
    events = [(end - duration + duration * (i + 1) / n, 1) for i in range(n)]
    _seed_store(app, {"primary": events})

    samples = client.get("/api/stats/throughput/history").json()["samples"]
    assert max(s["total_tps"] for s in samples) == pytest.approx(27.8, abs=2.0)
    assert samples[-1]["total_tps"] == 0.0
    assert samples[-1]["serving_slots"] == 0


def test_bin_edges_do_not_shift_between_polls(
    app: FastAPI, client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Two polls 1.3 s apart, no new traffic: the same bins, the same values."""
    mono_now = 10_000.0
    epoch_now = 1_700_000_001.2
    _seed_store(app, {"primary": [(mono_now - 32.0, 100), (mono_now - 21.0, 40)]})

    _freeze_clock(monkeypatch, mono_now, epoch_now)
    first = client.get("/api/stats/throughput/history").json()["samples"]
    _freeze_clock(monkeypatch, mono_now + 1.3, epoch_now + 1.3)
    second = client.get("/api/stats/throughput/history").json()["samples"]

    assert first == second
    for s in first:
        assert s["ts"] % 5.0 == pytest.approx(0.0, abs=1e-6)


# ── #3869 review: remainder distribution, short-completion rate, conservation ─


def test_spread_remainder_is_not_front_loaded() -> None:
    """300 tokens over 50 s: both halves read 6.0 tok/s, not 8.0 then 4.0."""
    from hal0.api.routes.v1 import _append_spread_events

    events: deque = deque(maxlen=4096)
    _append_spread_events(events, 300, 50.0, 1000.0)
    assert sum(tok for _, tok in events) == 300
    mid = 1000.0 - 25.0
    first_half = sum(tok for ts, tok in events if ts < mid)
    second_half = sum(tok for ts, tok in events if ts >= mid)
    assert abs(first_half - second_half) <= 1
    assert first_half / 25.0 == pytest.approx(6.0, rel=0.05)


@pytest.mark.parametrize(
    ("tokens", "span"),
    [(100, 0.5), (30, 0.6), (963, 34.64), (7, 3.0), (1, 5.0), (5, 0.0), (5, -2.0)],
)
def test_spread_conserves_tokens_and_stays_in_span(tokens: int, span: float) -> None:
    from hal0.api.routes.v1 import _append_spread_events

    events: deque = deque(maxlen=4096)
    _append_spread_events(events, tokens, span, 500.0)
    assert sum(tok for _, tok in events) == tokens
    assert all(tok >= 1 for _, tok in events)
    assert all(500.0 - max(span, 0.0) - 1e-9 <= ts <= 500.0 + 1e-9 for ts, _ in events)


@pytest.mark.parametrize(("tokens", "span", "expected"), [(100, 0.5, 200.0), (30, 0.6, 50.0)])
def test_tps_from_events_short_completion_reads_true_rate(
    monkeypatch: pytest.MonkeyPatch, tokens: int, span: float, expected: float
) -> None:
    """A short spread completion must read completion/span, not n/(n-1) times it."""
    from hal0.api.routes.v1 import _append_spread_events
    from hal0.slots.metrics_collect import tps_from_events

    monkeypatch.setattr(time, "monotonic", lambda: 1000.0)
    events: deque = deque(maxlen=4096)
    _append_spread_events(events, tokens, span, 1000.0)
    assert tps_from_events(events, window_s=5.0) == pytest.approx(expected, rel=0.01)


def test_history_bins_sum_to_completion_tokens(
    app: FastAPI, client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Spreading conserves tokens: sum over bins * bucket_s == completion_tokens."""
    from hal0.api.routes.v1 import _record_nonstreaming_throughput

    mono, epoch = 10_000.0, 1_700_000_003.0
    monkeypatch.setattr(time, "monotonic", lambda: mono)
    state = _nonstreaming_state()
    decode_ms = 963 / 27.8 * 1000
    body = (
        b'{"usage":{"completion_tokens":963},"timings":{"predicted_ms":'
        + str(decode_ms).encode()
        + b"}}"
    )
    _record_nonstreaming_throughput(body, state, "primary", dispatch_started=mono - 60.0)
    # Finish the completion before the last closed bin edge so it is reported.
    _freeze_clock(monkeypatch, mono + 10.0, epoch + 10.0)
    app.state.tps_events = state.tps_events
    body_json = client.get("/api/stats/throughput/history").json()
    total = sum(s["total_tps"] for s in body_json["samples"]) * body_json["bucket_s"]
    assert total == pytest.approx(963.0, abs=1e-6)
    assert sum(body_json["per_slot"]["primary"]) * body_json["bucket_s"] == pytest.approx(963.0)


def test_nonstreaming_span_never_zero_or_negative_division() -> None:
    """No timings and a dispatch start in the future: span 0, one event, no error."""
    from hal0.api.routes.v1 import _record_nonstreaming_throughput

    state = _nonstreaming_state()
    _record_nonstreaming_throughput(
        b'{"usage":{"completion_tokens":40}}',
        state,
        "primary",
        dispatch_started=time.monotonic() + 100.0,
    )
    assert [tok for _, tok in state.tps_events["primary"]] == [40]
    _record_nonstreaming_throughput(b'{"usage":{"completion_tokens":40}}', state, "primary")
    assert sum(tok for _, tok in state.tps_events["primary"]) == 80


def test_pending_tokens_reports_the_open_interval(
    app: FastAPI, client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Tokens in the not-yet-closed bin are not in ``samples`` but are exposed
    as the additive ``pending_tokens`` (the bench GPU gate reads it)."""
    mono, epoch = 10_000.0, 1_700_000_003.0  # last closed edge is 3 s ago
    _freeze_clock(monkeypatch, mono, epoch)
    _seed_store(app, {"primary": [(mono - 1.0, 25)]})
    body = client.get("/api/stats/throughput/history").json()
    assert body["samples"] == []
    assert body["pending_tokens"] == 25


def test_bench_gate_sees_pending_traffic(monkeypatch: pytest.MonkeyPatch) -> None:
    from hal0.bench import runner

    payload = {"samples": [{"ts": 1.0, "total_tps": 0.0, "serving_slots": 0}], "pending_tokens": 40}
    monkeypatch.setattr(runner, "_get_json", lambda *a, **k: payload)
    assert runner._traffic_in_flight("http://x") is True
    payload = {**payload, "pending_tokens": 0}
    monkeypatch.setattr(runner, "_get_json", lambda *a, **k: payload)
    assert runner._traffic_in_flight("http://x") is False
