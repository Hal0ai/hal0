"""Decisive crash line surfacing: journal tail → slot metadata → breaker chip.

When a slot container dies during model load, the reason (e.g.
``llama_model_load: error loading model: unknown model architecture`` or
``unable to allocate ROCm0 buffer``) used to live only in
``journalctl -u hal0-slot@<name>`` while the dashboard showed a bare
crash-breaker chip. The manager now tails the unit's journal on a load
failure and stamps the one decisive line onto the state extra
(``last_crash_line`` + ``last_crash_line_at``), which rides slot metadata to
``GET /api/slots``. Everything is best-effort: an unreadable journal must
never make the load failure worse.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from hal0.slots import logs as slot_logs
from hal0.slots.logs import CRASH_LINE_MAX, extract_crash_line, read_crash_line
from hal0.slots.manager import SlotManager
from hal0.slots.state import SlotState
from tests.slots.conftest import FakeContainerProvider

# ── extract_crash_line: pure extraction over real journal shapes ─────────────

_PREFIX = "2026-08-30T01:02:03+0200 ct150 hal0-slot-chat[4242]: "


def _journal(*messages: str) -> str:
    return "\n".join(_PREFIX + m for m in messages)


def test_extracts_unknown_architecture_line() -> None:
    text = _journal(
        "llama_model_loader: loaded meta data with 29 key-value pairs",
        "llama_model_load: error loading model: unknown model architecture",
        "hal0-runner: llama-server exited (rc=1) before /health ever answered "
        "— died during model load.",
        "hal0-runner: translating to exit 64 so systemd can fail-fast instead "
        "of burning the restart ramp (hal0 issue #2037).",
    )
    assert (
        extract_crash_line(text)
        == "llama_model_load: error loading model: unknown model architecture"
    )


def test_extracts_rocm_buffer_alloc_line() -> None:
    text = _journal(
        "llm_load_tensors: offloading 48 repeating layers to GPU",
        "ggml_backend_alloc: unable to allocate ROCm0 buffer",
        "hal0-runner: llama-server exited (rc=1) before /health ever answered "
        "— died during model load.",
    )
    assert extract_crash_line(text) == "ggml_backend_alloc: unable to allocate ROCm0 buffer"


def test_last_engine_error_wins_over_earlier_ones() -> None:
    text = _journal(
        "llama_model_load: error loading model: first attempt",
        "main: retrying",
        "llama_model_load: error loading model: second attempt",
    )
    assert extract_crash_line(text) == "llama_model_load: error loading model: second attempt"


def test_e_level_line_counts_as_engine_error() -> None:
    text = _journal(
        "I model loading started",
        "E failed opening tensor stream",
    )
    assert extract_crash_line(text) == "E failed opening tensor stream"


def test_runner_summary_is_the_fallback_not_the_boilerplate() -> None:
    # SIGILL death (#2126): the engine printed nothing recognisable, so the
    # hal0-runner summary/hint is the best available line — but never the
    # "translating to exit 64" boilerplate that follows it.
    text = _journal(
        "load: loading model /models/chadrock-35b.gguf",
        "hal0-runner: llama-server was killed by SIGILL (illegal instruction) "
        "(rc=132) before /health ever answered.",
        "hal0-runner: this image's llama-server contains CPU instructions this "
        "host cannot execute — an image/hardware mismatch, not a model problem.",
        "hal0-runner: translating to exit 64 — restarting reproduces this "
        "fault exactly (hal0 issue #2126).",
    )
    line = extract_crash_line(text)
    assert line is not None
    assert line.startswith("hal0-runner: this image's llama-server contains CPU instructions")


def test_no_recognisable_line_answers_none() -> None:
    assert extract_crash_line("") is None
    assert (
        extract_crash_line(
            _journal(
                "main: build info",
                "srv update_slots: all slots are idle",
            )
        )
        is None
    )


def test_prefix_stripping_tolerates_bare_lines() -> None:
    # A --output=cat style line (no journal prefix) must still match.
    assert (
        extract_crash_line("llama_model_load: error loading model: unknown model architecture")
        == "llama_model_load: error loading model: unknown model architecture"
    )


def test_long_line_is_truncated() -> None:
    text = _journal("llama_model_load: error loading model: " + "x" * 2000)
    line = extract_crash_line(text)
    assert line is not None
    assert len(line) == CRASH_LINE_MAX


# ── read_crash_line: fail-soft wrapper ───────────────────────────────────────


async def test_read_crash_line_empty_tail_answers_none(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def fake_tail(unit: str, lines: int, quiet: bool = True) -> tuple[str, str | None]:
        return "", "journalctl not available on this host"

    monkeypatch.setattr(slot_logs, "read_tail", fake_tail)
    assert await read_crash_line("hal0-slot@chat.service") is None


async def test_read_crash_line_never_raises(monkeypatch: pytest.MonkeyPatch) -> None:
    async def boom(unit: str, lines: int, quiet: bool = True) -> tuple[str, str | None]:
        raise RuntimeError("journal exploded")

    monkeypatch.setattr(slot_logs, "read_tail", boom)
    assert await read_crash_line("hal0-slot@chat.service") is None


async def test_read_crash_line_redacts_secrets(monkeypatch: pytest.MonkeyPatch) -> None:
    async def fake_tail(unit: str, lines: int, quiet: bool = True) -> tuple[str, str | None]:
        return _journal("llama_model_load: error loading model: api_key=sk-hunter2-secret"), None

    monkeypatch.setattr(slot_logs, "read_tail", fake_tail)
    line = await read_crash_line("hal0-slot@chat.service")
    assert line is not None
    assert "hunter2" not in line


# ── manager: load failure stamps the line; success clears it ─────────────────


async def _fail_load(sm: SlotManager, container_stub: FakeContainerProvider) -> None:
    container_stub.fail_load = RuntimeError("spawn boom")
    with pytest.raises(RuntimeError):
        await sm.load("chat")
    assert sm._current_state("chat") == SlotState.ERROR


async def test_load_failure_stamps_last_crash_line(
    slot_root: Path,
    container_stub: FakeContainerProvider,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    seen_units: list[str] = []

    async def fake_read_crash_line(unit: str, lines: int = 120) -> str | None:
        seen_units.append(unit)
        return "llama_model_load: error loading model: unknown model architecture"

    monkeypatch.setattr(slot_logs, "read_crash_line", fake_read_crash_line)
    sm = SlotManager()
    await _fail_load(sm, container_stub)

    rec = sm._states[sm._key("chat")]
    assert (
        rec.extra["last_crash_line"]
        == "llama_model_load: error loading model: unknown model architecture"
    )
    assert rec.extra["last_crash_line_at"] > 0
    # The unit name went through the naming seam (name-keyed box → name token).
    assert seen_units == ["hal0-slot@chat.service"]

    # …and it surfaces on the status metadata, i.e. GET /api/slots payloads.
    snap = await sm.status("chat")
    assert (
        snap.metadata["last_crash_line"]
        == "llama_model_load: error loading model: unknown model architecture"
    )


async def test_unreadable_journal_never_breaks_the_failure_path(
    slot_root: Path,
    container_stub: FakeContainerProvider,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def boom(unit: str, lines: int = 120) -> str | None:
        raise RuntimeError("journal exploded")

    monkeypatch.setattr(slot_logs, "read_crash_line", boom)
    sm = SlotManager()
    # The original spawn error propagates (not the journal one) and ERROR is
    # stamped without any crash-line keys.
    await _fail_load(sm, container_stub)
    rec = sm._states[sm._key("chat")]
    assert "last_crash_line" not in rec.extra
    assert rec.extra["load_failures"] == 1


async def test_healthy_convergence_clears_the_crash_line(
    slot_root: Path,
    container_stub: FakeContainerProvider,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def fake_read_crash_line(unit: str, lines: int = 120) -> str | None:
        return "ggml_backend_alloc: unable to allocate ROCm0 buffer"

    monkeypatch.setattr(slot_logs, "read_crash_line", fake_read_crash_line)
    sm = SlotManager()
    await _fail_load(sm, container_stub)
    # Open the backoff window, fix the fault, reload.
    key = sm._key("chat")
    count, ts = sm._load_failures[key]
    sm._load_failures[key] = (count, ts - 10_000.0)
    container_stub.fail_load = None

    await sm.load("chat")

    assert sm._current_state("chat") == SlotState.READY
    rec = sm._states[key]
    assert "last_crash_line" not in rec.extra
    assert "last_crash_line_at" not in rec.extra
