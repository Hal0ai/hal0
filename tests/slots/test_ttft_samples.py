"""Tests for the rolling TTFT sample window + fleet-wide aggregation.

``hal0.slots.ttft_samples`` is pure stdlib (time, collections.deque,
dataclasses) — no I/O, no asyncio. Every test here drives the module
with explicit ``now``/timestamp values so nothing depends on
wall-clock timing or real sleeps; the module's own ``time.monotonic()``
fallback is only exercised implicitly (its return type, not its value,
matters) so tests stay deterministic under CI load.
"""

from __future__ import annotations

from collections import deque

import pytest

from hal0.slots.ttft_samples import (
    DEFAULT_WINDOW_S,
    SlotSamples,
    avg_kv_cache_across,
    avg_ttft_across,
    samples_from_events,
)


def test_default_window_matches_module_constant() -> None:
    s = SlotSamples()
    assert s.window_s == DEFAULT_WINDOW_S == 60.0
    assert s.ttft_samples.maxlen == 128
    assert s.inflight == {}
    assert s.throughput_tps == 0.0
    assert s.kv_occupancy_pct == 0.0


def test_request_started_then_first_chunk_records_ttft() -> None:
    s = SlotSamples()
    s.request_started("req-1", now=100.0)
    ttft = s.first_chunk("req-1", now=100.25)
    assert ttft == 0.25
    assert list(s.ttft_samples) == [(100.25, 0.25)]
    # The request is no longer tracked as inflight once its first chunk lands.
    assert "req-1" not in s.inflight


def test_first_chunk_for_unknown_request_returns_none_and_records_nothing() -> None:
    s = SlotSamples()
    assert s.first_chunk("never-started", now=10.0) is None
    assert list(s.ttft_samples) == []


def test_first_chunk_clamps_negative_delta_to_zero() -> None:
    """Defensive clamp: if `now` regresses relative to the recorded start
    (clock skew / bad caller), the TTFT floors at 0.0 instead of going
    negative."""
    s = SlotSamples()
    s.request_started("req-1", now=100.0)
    ttft = s.first_chunk("req-1", now=99.0)
    assert ttft == 0.0
    assert list(s.ttft_samples) == [(99.0, 0.0)]


def test_request_cancelled_drops_inflight_without_recording_a_sample() -> None:
    s = SlotSamples()
    s.request_started("req-1", now=100.0)
    s.request_cancelled("req-1")
    assert "req-1" not in s.inflight
    # A subsequent first_chunk for the same id is now untracked.
    assert s.first_chunk("req-1", now=101.0) is None
    assert list(s.ttft_samples) == []


def test_request_cancelled_is_a_noop_for_unknown_id() -> None:
    s = SlotSamples()
    s.request_cancelled("ghost")  # must not raise
    assert s.inflight == {}


def test_current_ttft_and_avg_with_no_samples_is_none() -> None:
    s = SlotSamples()
    assert s.current_ttft(now=100.0) is None
    assert s.avg_ttft(now=100.0) is None
    assert s.sample_count(now=100.0) == 0


def test_current_ttft_returns_the_latest_sample() -> None:
    s = SlotSamples()
    s.ttft_samples.append((10.0, 0.1))
    s.ttft_samples.append((20.0, 0.3))
    s.ttft_samples.append((30.0, 0.5))
    assert s.current_ttft(now=30.0) == 0.5


def test_avg_ttft_is_windowed_mean_of_recent_samples() -> None:
    s = SlotSamples(window_s=60.0)
    s.ttft_samples.append((0.0, 0.1))
    s.ttft_samples.append((10.0, 0.2))
    s.ttft_samples.append((20.0, 0.3))
    # All three are within 60s of now=20.0.
    assert s.avg_ttft(now=20.0) == pytest.approx((0.1 + 0.2 + 0.3) / 3)
    assert s.sample_count(now=20.0) == 3


def test_stale_samples_are_evicted_from_reads_past_the_window() -> None:
    """A sample older than `window_s` must be excluded from current/avg/count
    reads, without a real sleep — we just advance the injected `now`."""
    s = SlotSamples(window_s=60.0)
    s.ttft_samples.append((0.0, 0.9))  # will go stale
    s.ttft_samples.append((100.0, 0.1))  # stays fresh

    # now=100.0: the ts=0.0 sample is exactly 100s old > 60s window -> stale.
    assert s.sample_count(now=100.0) == 1
    assert s.current_ttft(now=100.0) == 0.1
    assert s.avg_ttft(now=100.0) == 0.1

    # Advance further: still just the one fresh sample, until it too ages out.
    assert s.sample_count(now=159.9) == 1
    assert s.sample_count(now=200.0) == 0
    assert s.current_ttft(now=200.0) is None
    assert s.avg_ttft(now=200.0) is None


def test_window_cutoff_boundary_is_inclusive() -> None:
    """`_recent` keeps samples with ``ts >= now - window_s`` (a sample
    landing exactly on the cutoff is still in-window, not stale)."""
    s = SlotSamples(window_s=60.0)
    s.ttft_samples.append((40.0, 0.4))
    # now - window_s == 40.0 == ts -> included (">=", not ">").
    assert s.sample_count(now=100.0) == 1
    assert s.current_ttft(now=100.0) == 0.4
    # One tick past the boundary, it drops out.
    assert s.sample_count(now=100.0000001) == 0


def test_ttft_samples_deque_maxlen_bounds_memory() -> None:
    s = SlotSamples()
    assert s.ttft_samples.maxlen == 128
    for i in range(200):
        s.ttft_samples.append((float(i), 0.01))
    assert len(s.ttft_samples) == 128
    # Oldest entries were evicted; the deque holds the most recent 128.
    assert s.ttft_samples[0][0] == 72.0
    assert s.ttft_samples[-1][0] == 199.0


def test_avg_ttft_across_returns_none_when_no_slot_has_data() -> None:
    empty_a, empty_b = SlotSamples(), SlotSamples()
    assert avg_ttft_across([empty_a, empty_b], now=100.0) is None
    assert avg_ttft_across([], now=100.0) is None


def test_avg_ttft_across_equally_weights_slots_regardless_of_sample_count() -> None:
    """One slot's churn shouldn't drown another's single sample: the fleet
    average is the mean of *per-slot* averages, not a pooled mean over all
    samples."""
    busy = SlotSamples()
    for i in range(10):
        busy.ttft_samples.append((float(i), 1.0))  # per-slot avg == 1.0
    quiet = SlotSamples()
    quiet.ttft_samples.append((0.0, 3.0))  # per-slot avg == 3.0

    fleet_avg = avg_ttft_across([busy, quiet], now=9.0)
    assert fleet_avg == (1.0 + 3.0) / 2  # == 2.0, NOT the pooled mean (~1.18)


def test_avg_ttft_across_skips_slots_with_no_in_window_data() -> None:
    has_data = SlotSamples()
    has_data.ttft_samples.append((100.0, 0.5))
    no_data = SlotSamples()  # never recorded anything
    stale = SlotSamples(window_s=60.0)
    stale.ttft_samples.append((0.0, 9.0))  # will be stale at now=100.0

    fleet_avg = avg_ttft_across([has_data, no_data, stale], now=100.0)
    assert fleet_avg == 0.5


def test_avg_kv_cache_across_empty_dict_is_none() -> None:
    assert avg_kv_cache_across({}) is None


def test_avg_kv_cache_across_means_reported_slots_only() -> None:
    kv = {"chat": 40.0, "coder": 60.0}
    assert avg_kv_cache_across(kv) == 50.0


def test_samples_from_events_wraps_the_live_deque_by_reference() -> None:
    """samples_from_events is a lazy, read-only *view* over the caller's
    deque (no copy) — mutations to the original app.state deque must be
    visible through the returned SlotSamples, since the capture path
    appends directly to it outside of this view."""
    events: deque[tuple[float, float]] = deque(maxlen=128)
    events.append((10.0, 0.2))
    view = samples_from_events(events, window_s=30.0)

    assert view.window_s == 30.0
    assert view.ttft_samples is events
    assert view.current_ttft(now=10.0) == 0.2

    # Mutate the original deque after the view was built.
    events.append((20.0, 0.4))
    assert view.current_ttft(now=20.0) == 0.4
    assert view.sample_count(now=20.0) == 2


def test_samples_from_events_defaults_to_module_window() -> None:
    events: deque[tuple[float, float]] = deque(maxlen=128)
    view = samples_from_events(events)
    assert view.window_s == DEFAULT_WINDOW_S
