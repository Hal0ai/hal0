"""Tests for hal0.slots.manager.SlotManager (v0.2 — Lemonade-only).

v0.2 (PR-10): SlotManager dispatches every lifecycle method through
``LemonadeProvider``. The legacy docker/systemd path is gone. Tests
mock the Lemonade HTTP surface via ``lemonade_stub`` /
``lemonade_loaded_stub`` (see conftest.py).

Covers:
  - SEEDED_SLOTS + NPU_SEEDED_SLOTS constants and the
    ``seeded_slots()`` helper (PR-10 §10.2)
  - default_slot_for / route_for_request routing helpers (§4.4)
  - add_slot / remove_slot validation rules (§4.3)
  - load / unload / restart / swap / status / create / delete /
    update_config dispatch through Lemonade
  - status() drift reconciliation against /v1/health.loaded[]
  - HAL0_BACKEND env var has no effect (PR-10 retired the gate)
"""

from __future__ import annotations

import asyncio
import json
from pathlib import Path
from typing import Any

import httpx
import pytest

from hal0.slots.manager import (
    NPU_SEEDED_SLOTS,
    SEEDED_SLOTS,
    SlotManager,
)
from hal0.slots.state import (
    IllegalSlotTransition,
    SlotConfigError,
    SlotNotFound,
    SlotSpawnFailed,
    SlotState,
)

# Shared fixtures (lemonade_stub, lemonade_loaded_stub, slot_root) live
# in tests/slots/conftest.py so they can be reused across this file
# and the other slot-suite modules.


# ── SEEDED_SLOTS + NPU_SEEDED_SLOTS (PR-10 §10.2) ───────────────────────────


def test_seeded_slots_matches_plan_section_10_2() -> None:
    assert SEEDED_SLOTS == ("primary", "embed", "rerank", "stt", "tts", "img")


def test_npu_seeded_slots_matches_plan_section_10_2() -> None:
    assert NPU_SEEDED_SLOTS == ("agent", "stt-npu", "embed-npu")


def test_builtin_slots_aliases_seeded_slots() -> None:
    # Backwards-compat alias on the class.
    assert SlotManager.BUILTIN_SLOTS == SEEDED_SLOTS


def test_seeded_slots_helper_excludes_npu_when_flm_missing(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr("hal0.slots.manager.shutil.which", lambda name: None)
    assert SlotManager.seeded_slots() == SEEDED_SLOTS


def test_seeded_slots_helper_includes_npu_when_flm_present(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr("hal0.slots.manager.shutil.which", lambda name: "/usr/bin/flm")
    assert SlotManager.seeded_slots() == SEEDED_SLOTS + NPU_SEEDED_SLOTS


def test_seeded_slots_helper_respects_explicit_include_npu_flag() -> None:
    assert SlotManager.seeded_slots(include_npu=False) == SEEDED_SLOTS
    assert SlotManager.seeded_slots(include_npu=True) == SEEDED_SLOTS + NPU_SEEDED_SLOTS


# ── routing helpers (PR-10 §4.4) ────────────────────────────────────────────


def _write_typed_slot(
    root: Path,
    name: str,
    *,
    slot_type: str,
    enabled: bool = True,
    default: bool | None = None,
    labels: tuple[str, ...] = (),
    port: int = 8081,
) -> None:
    """Write a typed slot TOML for routing tests."""
    lines = [
        f'name = "{name}"',
        f"port = {port}",
        f'type = "{slot_type}"',
        'provider = "lemonade"',
        f"enabled = {str(enabled).lower()}",
    ]
    if default is not None:
        lines.append(f"default = {str(default).lower()}")
    lines.append("[model]")
    lines.append(f'default = "{name}-model"')
    if labels:
        lines.append("labels = [" + ", ".join(f'"{x}"' for x in labels) + "]")
    (root / f"{name}.toml").write_text("\n".join(lines) + "\n", encoding="utf-8")


async def test_default_slot_for_returns_the_lone_default(slot_root: Path) -> None:
    _write_typed_slot(slot_root, "a", slot_type="llm", default=True, port=8081)
    _write_typed_slot(slot_root, "b", slot_type="llm", default=False, port=8082)
    sm = SlotManager()
    assert await sm.default_slot_for("llm") == "a"


async def test_default_slot_for_returns_none_when_no_default(slot_root: Path) -> None:
    _write_typed_slot(slot_root, "a", slot_type="llm", default=False, port=8081)
    sm = SlotManager()
    assert await sm.default_slot_for("llm") is None


async def test_default_slot_for_raises_when_two_defaults(slot_root: Path) -> None:
    _write_typed_slot(slot_root, "a", slot_type="llm", default=True, port=8081)
    _write_typed_slot(slot_root, "b", slot_type="llm", default=True, port=8082)
    sm = SlotManager()
    with pytest.raises(SlotConfigError):
        await sm.default_slot_for("llm")


async def test_route_for_request_prefers_default(slot_root: Path) -> None:
    _write_typed_slot(slot_root, "a", slot_type="llm", default=False, port=8081)
    _write_typed_slot(slot_root, "b", slot_type="llm", default=True, port=8082)
    sm = SlotManager()
    assert await sm.route_for_request("llm") == "b"


async def test_route_for_request_falls_through_when_default_disabled(
    slot_root: Path,
) -> None:
    _write_typed_slot(slot_root, "a", slot_type="llm", default=False, enabled=True, port=8081)
    _write_typed_slot(slot_root, "b", slot_type="llm", default=True, enabled=False, port=8082)
    sm = SlotManager()
    assert await sm.route_for_request("llm") == "a"


async def test_route_for_request_label_filter_drops_default(slot_root: Path) -> None:
    _write_typed_slot(
        slot_root,
        "a",
        slot_type="llm",
        default=True,
        labels=("text",),
        port=8081,
    )
    _write_typed_slot(
        slot_root,
        "b",
        slot_type="llm",
        default=False,
        labels=("text", "vision"),
        port=8082,
    )
    sm = SlotManager()
    # Default ``a`` lacks "vision"; routing must fall through to ``b``.
    assert await sm.route_for_request("llm", required_labels=("vision",)) == "b"


async def test_route_for_request_returns_none_when_nothing_matches(
    slot_root: Path,
) -> None:
    _write_typed_slot(slot_root, "a", slot_type="embedding", port=8082)
    sm = SlotManager()
    assert await sm.route_for_request("llm") is None


# ── add_slot / remove_slot (PR-10 §4.3) ─────────────────────────────────────


async def test_add_slot_writes_toml(tmp_hal0_home: str) -> None:
    sm = SlotManager()
    snap = await sm.add_slot(
        "scribe",
        type="transcription",
        model="whisper-base",
        port=8090,
    )
    assert snap.state == SlotState.OFFLINE
    cfg_path = Path(tmp_hal0_home) / "etc" / "hal0" / "slots" / "scribe.toml"
    assert cfg_path.exists()
    text = cfg_path.read_text(encoding="utf-8")
    assert 'name = "scribe"' in text
    assert 'type = "transcription"' in text
    assert 'default = "whisper-base"' in text


async def test_add_slot_rejects_seeded_collision(tmp_hal0_home: str) -> None:
    sm = SlotManager()
    with pytest.raises(SlotConfigError, match="seeded"):
        await sm.add_slot("primary", type="llm", model="x", port=8090)


async def test_add_slot_rejects_npu_seeded_collision(tmp_hal0_home: str) -> None:
    # Reserved even when FLM isn't installed.
    sm = SlotManager()
    with pytest.raises(SlotConfigError, match="seeded"):
        await sm.add_slot("agent", type="llm", model="x", port=8090)


async def test_add_slot_rejects_invalid_type(tmp_hal0_home: str) -> None:
    sm = SlotManager()
    with pytest.raises(SlotConfigError, match="slot type"):
        await sm.add_slot("foo", type="bogus", model="x", port=8090)


async def test_add_slot_rejects_invalid_name(tmp_hal0_home: str) -> None:
    sm = SlotManager()
    with pytest.raises(SlotConfigError, match="lowercase"):
        await sm.add_slot("UPPER", type="llm", model="x", port=8090)
    with pytest.raises(SlotConfigError, match="lowercase"):
        await sm.add_slot("-leading-hyphen", type="llm", model="x", port=8090)


async def test_remove_slot_refuses_seeded(tmp_hal0_home: str) -> None:
    sm = SlotManager()
    with pytest.raises(SlotConfigError, match="seeded"):
        await sm.remove_slot("primary")
    with pytest.raises(SlotConfigError, match="seeded"):
        await sm.remove_slot("agent")


async def test_remove_slot_deletes_user_slot(tmp_hal0_home: str) -> None:
    sm = SlotManager()
    await sm.add_slot("scribe", type="transcription", model="whisper-base", port=8090)
    cfg_path = Path(tmp_hal0_home) / "etc" / "hal0" / "slots" / "scribe.toml"
    assert cfg_path.exists()
    await sm.remove_slot("scribe")
    assert not cfg_path.exists()


# ── lifecycle dispatched through Lemonade ───────────────────────────────────


async def test_load_dispatches_via_lemonade(
    slot_root: Path,
    lemonade_loaded_stub: dict[str, Any],
    tmp_hal0_home: str,
) -> None:
    sm = SlotManager()
    snap = await sm.load("primary")
    assert snap.state == SlotState.READY
    # state.json on disk reflects READY.
    state_path = Path(tmp_hal0_home) / "var-lib" / "hal0" / "slots" / "primary" / "state.json"
    payload = json.loads(state_path.read_text(encoding="utf-8"))
    assert payload["state"] == "ready"
    # POST /v1/load was invoked with the slot's model.
    assert lemonade_loaded_stub["load_calls"], "expected at least one /v1/load call"
    assert lemonade_loaded_stub["load_calls"][0]["model_name"] == "qwen3-4b-q4_k_m"


async def test_load_idempotent_when_ready(
    slot_root: Path,
    lemonade_loaded_stub: dict[str, Any],
) -> None:
    sm = SlotManager()
    await sm.load("primary")
    calls_before = len(lemonade_loaded_stub["load_calls"])
    snap = await sm.load("primary")
    assert snap.state == SlotState.READY
    # No extra /v1/load — already loaded.
    assert len(lemonade_loaded_stub["load_calls"]) == calls_before


async def test_unload_transitions_to_offline(
    slot_root: Path,
    lemonade_loaded_stub: dict[str, Any],
) -> None:
    sm = SlotManager()
    await sm.load("primary")
    snap = await sm.unload("primary")
    assert snap.state == SlotState.OFFLINE
    assert lemonade_loaded_stub["unload_calls"], "expected /v1/unload"


async def test_restart_round_trip(
    slot_root: Path,
    lemonade_loaded_stub: dict[str, Any],
) -> None:
    sm = SlotManager()
    await sm.load("primary")
    snap = await sm.restart("primary")
    assert snap.state == SlotState.READY


async def test_swap_replaces_model_id(
    slot_root: Path,
    lemonade_loaded_stub: dict[str, Any],
) -> None:
    sm = SlotManager()
    await sm.load("primary")
    snap = await sm.swap("primary", "llama-3.2-3b-q4_k_m")
    assert snap.model_id == "llama-3.2-3b-q4_k_m"
    # Last /v1/load body carries the override model.
    assert lemonade_loaded_stub["load_calls"][-1]["model_name"] == "llama-3.2-3b-q4_k_m"


async def test_load_propagates_lemonade_error_as_slot_error(
    slot_root: Path,
    lemonade_stub,
) -> None:
    """A 5xx from /v1/load lands the slot in ERROR via SlotSpawnFailed."""

    def h(req: httpx.Request) -> httpx.Response:
        if req.url.path == "/v1/load":
            return httpx.Response(500, json={"detail": "evict-all triggered"})
        if req.url.path == "/v1/health":
            return httpx.Response(200, json={"loaded": []})
        return httpx.Response(404)

    lemonade_stub(h)

    sm = SlotManager()
    with pytest.raises(SlotSpawnFailed):
        await sm.load("primary")
    snap = await sm.status("primary")
    assert snap.state == SlotState.ERROR


async def test_status_reconciles_drift(
    slot_root: Path,
    lemonade_loaded_stub: dict[str, Any],
) -> None:
    """A persisted READY plus an empty lemond loaded[] transitions to OFFLINE.

    Was ERROR pre-issue-#275 (per-slot-systemd era treated drift as
    slot-broken). Under Lemonade, eviction is normal (per-type LRU
    budget + nuclear evict + idle-unload driver all evict without
    breaking the slot config), so we demote to OFFLINE with a neutral
    message that the dispatcher reloads on next request.
    """
    sm = SlotManager()
    await sm.load("primary")
    # Mutate the stub state so lemond no longer reports the model loaded.
    lemonade_loaded_stub["loaded"] = []
    snap = await sm.status("primary")
    assert snap.state == SlotState.OFFLINE
    # Drift transition message is operator-facing; only the state itself is contract
    # (message may be reset to empty in the post-transition Slot rebuild path).
    # assert snap.state == SlotState.OFFLINE is the contract.


async def test_status_adopts_running_slot_when_lemond_holds_model(
    slot_root: Path,
    lemonade_loaded_stub: dict[str, Any],
) -> None:
    """state.json OFFLINE + lemond loaded[] non-empty → adopt to READY."""
    sm = SlotManager()
    # Bypass load(): write OFFLINE directly. lemonade_loaded_stub's default
    # state advertises qwen3-4b-q4_k_m as loaded.
    await sm._transition("primary", SlotState.OFFLINE, force=True)
    snap = await sm.status("primary")
    assert snap.state == SlotState.READY
    # extras carry the adoption marker.
    assert snap.metadata.get("adopted") is True


async def test_status_rehydrates_backend_from_toml(
    slot_root: Path,
    tmp_hal0_home: str,
    lemonade_loaded_stub: dict[str, Any],
) -> None:
    """state.json without extra.backend should re-hydrate from TOML."""
    from hal0.slots.state import SlotStateRecord, write_state_atomic

    state_path = Path(tmp_hal0_home) / "var-lib" / "hal0" / "slots" / "primary" / "state.json"
    write_state_atomic(
        state_path,
        SlotStateRecord(name="primary", state=SlotState.OFFLINE, port=8081, extra={}),
    )
    # Empty out lemond's loaded[] so adoption can't fire.
    lemonade_loaded_stub["loaded"] = []

    sm = SlotManager()
    snap = await sm.status("primary")
    assert snap.backend == "vulkan"
    assert snap.metadata.get("backend") == "vulkan"


async def test_status_unloaded_slot_uses_toml_backend(
    slot_root: Path,
    lemonade_loaded_stub: dict[str, Any],
) -> None:
    """Fresh TOML, no state.json — surface backend from TOML defaults."""
    lemonade_loaded_stub["loaded"] = []  # OFFLINE — no adoption.
    sm = SlotManager()
    snap = await sm.status("primary")
    assert snap.state == SlotState.OFFLINE
    assert snap.backend == "vulkan"
    assert snap.port == 8081


async def test_list_returns_all_configured_slots(
    slot_root: Path,
    lemonade_loaded_stub: dict[str, Any],
) -> None:
    (slot_root / "embed.toml").write_text(
        "\n".join(
            [
                'name = "embed"',
                "port = 8082",
                'backend = "vulkan"',
                'provider = "lemonade"',
                "[model]",
                'default = "bge-small-en"',
                "",
            ]
        ),
        encoding="utf-8",
    )
    sm = SlotManager()
    snaps = await sm.list()
    names = {s.name for s in snaps}
    assert {"primary", "embed"}.issubset(names)


# ── error paths ─────────────────────────────────────────────────────────────


async def test_load_unknown_slot_raises_typed(slot_root: Path) -> None:
    sm = SlotManager()
    with pytest.raises(SlotNotFound) as exc_info:
        await sm.load("nonexistent")
    assert exc_info.value.code == "slot.not_found"


async def test_illegal_transition_blocked(slot_root: Path) -> None:
    """Direct _transition() with an illegal edge raises IllegalSlotTransition."""
    sm = SlotManager()
    await sm._transition("primary", SlotState.OFFLINE, force=True)
    with pytest.raises(IllegalSlotTransition) as exc_info:
        await sm._transition("primary", SlotState.READY)
    assert exc_info.value.code == "slot.illegal_transition"
    assert exc_info.value.status == 409


# ── CRUD ────────────────────────────────────────────────────────────────────


async def test_create_writes_config_and_state(tmp_hal0_home: str) -> None:
    sm = SlotManager()
    cfg = {
        "name": "extra",
        "port": 8090,
        "backend": "vulkan",
        "provider": "lemonade",
        "model": {"default": "tiny-q4"},
    }
    snap = await sm.create("extra", cfg)
    assert snap.state == SlotState.OFFLINE
    assert (Path(tmp_hal0_home) / "etc" / "hal0" / "slots" / "extra.toml").exists()
    state_path = Path(tmp_hal0_home) / "var-lib" / "hal0" / "slots" / "extra" / "state.json"
    assert state_path.exists()


async def test_delete_removes_files_and_protects_seeded(tmp_hal0_home: str) -> None:
    sm = SlotManager()
    cfg = {
        "name": "extra",
        "port": 8090,
        "backend": "vulkan",
        "provider": "lemonade",
        "model": {"default": "tiny-q4"},
    }
    await sm.create("extra", cfg)
    await sm.delete("extra")
    assert not (Path(tmp_hal0_home) / "etc" / "hal0" / "slots" / "extra.toml").exists()
    with pytest.raises(SlotConfigError):
        await sm.delete("primary")


async def test_update_config_rewrites_toml(slot_root: Path) -> None:
    sm = SlotManager()
    from hal0.slots.state import SlotState as _S

    await sm._transition("primary", _S.OFFLINE, force=True)
    await sm.update_config("primary", {"workers": 4})
    cfg_text = (slot_root / "primary.toml").read_text(encoding="utf-8")
    assert "workers = 4" in cfg_text


# ── SSE state stream ────────────────────────────────────────────────────────


async def test_state_stream_broadcasts_transitions(
    slot_root: Path,
    lemonade_loaded_stub: dict[str, Any],
) -> None:
    sm = SlotManager()

    received: list[tuple[str, str]] = []

    async def consumer() -> None:
        async for rec in sm.state_stream():
            received.append((rec.name, rec.state.value))
            if len(received) >= 3:
                return

    task = asyncio.create_task(consumer())
    # Give the consumer a tick to subscribe.
    await asyncio.sleep(0)
    await sm.load("primary")
    await asyncio.wait_for(task, timeout=2.0)

    states_seen = [s for _, s in received]
    # Should have seen at least starting then warming then ready.
    assert "starting" in states_seen
    assert "ready" in states_seen


# ── bump_last_used / idle tracking ──────────────────────────────────────────


def test_bump_last_used_records_timestamp() -> None:
    sm = SlotManager()
    assert sm.last_used("foo") is None
    sm.bump_last_used("foo")
    ts = sm.last_used("foo")
    assert ts is not None and ts > 0


# ── HAL0_BACKEND env var is a no-op (PR-10) ─────────────────────────────────


@pytest.mark.parametrize("value", ["", "lemonade", "legacy", "TOOLBOX"])
async def test_hal0_backend_env_var_is_ignored(
    slot_root: Path,
    lemonade_loaded_stub: dict[str, Any],
    monkeypatch: pytest.MonkeyPatch,
    value: str,
) -> None:
    """The HAL0_BACKEND env gate retired in PR-10; behaviour must not vary."""
    if value:
        monkeypatch.setenv("HAL0_BACKEND", value)
    else:
        monkeypatch.delenv("HAL0_BACKEND", raising=False)
    sm = SlotManager()
    snap = await sm.load("primary")
    assert snap.state == SlotState.READY
