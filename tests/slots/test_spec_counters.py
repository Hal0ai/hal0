"""Speculative-decoding counters on the slot (ADR-0031 §6)."""

from __future__ import annotations

import collections
import time
from types import SimpleNamespace
from typing import Any

import pytest

from hal0.slots import metrics_collect


class _Resp:
    def __init__(self, text: str = "", json_data: Any = None, status_code: int = 200) -> None:
        self.status_code = status_code
        self.text = text
        self._json = json_data

    def json(self) -> Any:
        return self._json


def _patch_scrape(monkeypatch: pytest.MonkeyPatch, metrics: _Resp, slots: _Resp) -> None:
    class _Client:
        async def __aenter__(self) -> _Client:
            return self

        async def __aexit__(self, *_exc: Any) -> None:
            return None

        async def get(self, url: str) -> _Resp:
            return metrics if url.endswith("/metrics") else slots

    monkeypatch.setattr("hal0.http_client.async_client", lambda **_kw: _Client())


_COUNTERS = (
    "llamacpp:spec_decode_num_draft_tokens_total 300\n"
    "llamacpp:spec_decode_num_accepted_tokens_total 210\n"
    "llamacpp:spec_decode_num_drafts_total 80\n"
)


@pytest.mark.asyncio
async def test_llama_metrics_parses_spec_counters_and_speculative_flag(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _patch_scrape(
        monkeypatch,
        _Resp(text="llamacpp:requests_processing 0\n" + _COUNTERS),
        _Resp(json_data=[{"id": 0, "n_ctx": 4096, "speculative": True}]),
    )
    out = await metrics_collect.llama_metrics(8081)
    assert out["spec"] == {
        "active": True,
        "draft_tokens": 300,
        "accepted_tokens": 210,
        "drafts": 80,
        "acceptance": pytest.approx(210 / 300),
        "mean_accepted_len": pytest.approx(1 + 210 / 80),
    }


@pytest.mark.asyncio
async def test_llama_metrics_loaded_but_not_drafting_is_inactive(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    zero = (
        "llamacpp:spec_decode_num_draft_tokens_total 0\n"
        "llamacpp:spec_decode_num_accepted_tokens_total 0\n"
        "llamacpp:spec_decode_num_drafts_total 0\n"
    )
    _patch_scrape(
        monkeypatch,
        _Resp(text=zero),
        _Resp(json_data=[{"id": 0, "n_ctx": 4096, "speculative": False}]),
    )
    spec = (await metrics_collect.llama_metrics(8081))["spec"]
    assert spec["active"] is False
    assert spec["acceptance"] is None
    assert spec["mean_accepted_len"] is None


@pytest.mark.asyncio
async def test_llama_metrics_without_spec_signals_has_no_spec(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _patch_scrape(
        monkeypatch,
        _Resp(text="llamacpp:requests_processing 1\n"),
        _Resp(json_data=[{"id": 0, "n_ctx": 4096}]),
    )
    assert "spec" not in await metrics_collect.llama_metrics(8081)


def _store(**slots: list[tuple[float, int, int]]) -> Any:
    store: dict[str, collections.deque] = {k: collections.deque(v) for k, v in slots.items()}
    return SimpleNamespace(spec_events=store)


def test_local_spec_acceptance_windows_and_skips_no_drafts() -> None:
    now = time.monotonic()
    state = _store(
        a=[(now - 100, 50, 0), (now - 5, 10, 6), (now - 2, 10, 4)],
        b=[(now - 100, 10, 10)],
    )
    out = metrics_collect.local_spec_acceptance(state, window_s=30.0)
    assert out == {"a": pytest.approx(0.5)}


def test_local_spec_acceptance_missing_store() -> None:
    assert metrics_collect.local_spec_acceptance(SimpleNamespace(), 30.0) == {}
