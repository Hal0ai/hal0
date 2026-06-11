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


@pytest.fixture(autouse=True)
def _no_spawn_context_refresh(monkeypatch):
    # The runtime writers (swap/apply) fire a detached hal0-agent
    # render-context; stub it so tests never launch real subprocesses.
    import hal0.agents.hermes_refresh as _hr

    monkeypatch.setattr(_hr, "spawn_context_refresh", lambda *a, **k: None)


# ── SEEDED_SLOTS + NPU_SEEDED_SLOTS (PR-10 §10.2) ───────────────────────────


def test_seeded_slots_matches_plan_section_10_2() -> None:
    # ``vision`` added in #515 (first-class vision capability, reusing the
    # curated multimodal MoE primaries + their mmproj sidecar).
    # ``primary`` renamed to ``chat`` in #654/#633.
    assert SEEDED_SLOTS == ("chat", "embed", "rerank", "stt", "tts", "img", "vision", "agent")


def test_npu_seeded_slots_matches_plan_section_10_2() -> None:
    # #679: agent dropped — it's a GPU chat-role slot, not the NPU FLM anchor.
    assert NPU_SEEDED_SLOTS == ("stt-npu", "embed-npu")


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


# ── idle_timeout_by_model (issue #414) ──────────────────────────────────────


def _write_idle_slot(
    root: Path,
    name: str,
    *,
    model_default: str,
    idle_timeout_s: int | None,
    port: int = 8081,
) -> None:
    """Write a minimal slot TOML with an optional flat idle_timeout_s."""
    lines = [
        f'name = "{name}"',
        f"port = {port}",
        'provider = "lemonade"',
        "enabled = true",
    ]
    if idle_timeout_s is not None:
        lines.append(f"idle_timeout_s = {idle_timeout_s}")
    lines.append("[model]")
    lines.append(f'default = "{model_default}"')
    (root / f"{name}.toml").write_text("\n".join(lines) + "\n", encoding="utf-8")


def test_idle_timeout_by_model_maps_model_name_to_ttl(slot_root: Path) -> None:
    """Each slot's [model] default maps to its configured idle_timeout_s."""
    _write_idle_slot(slot_root, "a", model_default="model-a", idle_timeout_s=86400, port=8082)
    _write_idle_slot(slot_root, "b", model_default="model-b", idle_timeout_s=120, port=8083)
    sm = SlotManager()
    m = sm.idle_timeout_by_model()
    assert m["model-a"] == 86400.0
    assert m["model-b"] == 120.0


def test_idle_timeout_by_model_preserves_zero(slot_root: Path) -> None:
    """idle_timeout_s == 0 (disable eviction) round-trips as 0.0, not dropped."""
    _write_idle_slot(slot_root, "keep", model_default="pinned-model", idle_timeout_s=0, port=8082)
    sm = SlotManager()
    m = sm.idle_timeout_by_model()
    assert m["pinned-model"] == 0.0


def test_idle_timeout_by_model_skips_slots_without_model_default(slot_root: Path) -> None:
    """A slot with an empty [model] default contributes no entry."""
    _write_idle_slot(slot_root, "empty", model_default="", idle_timeout_s=300, port=8082)
    sm = SlotManager()
    m = sm.idle_timeout_by_model()
    assert "" not in m


def test_idle_timeout_by_model_tolerates_malformed_toml(slot_root: Path) -> None:
    """A malformed slot TOML is skipped, not fatal — others still map."""
    _write_idle_slot(slot_root, "good", model_default="good-model", idle_timeout_s=42, port=8082)
    (slot_root / "broken.toml").write_text("name = \nport = ", encoding="utf-8")
    sm = SlotManager()
    m = sm.idle_timeout_by_model()
    assert m["good-model"] == 42.0


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
        await sm.add_slot("chat", type="llm", model="x", port=8090)


async def test_add_slot_rejects_npu_seeded_collision(tmp_hal0_home: str) -> None:
    # Reserved even when FLM isn't installed.
    sm = SlotManager()
    with pytest.raises(SlotConfigError, match="seeded"):
        await sm.add_slot("agent", type="llm", model="x", port=8090)


async def test_agent_slot_non_deletable_without_flm(
    tmp_hal0_home: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    # #679: agent is a GPU seed slot, so it is non-deletable regardless of FLM.
    # Regression guard — while agent was NPU-seeded, delete protection vanished
    # on non-FLM boxes (seeded_slots() excludes the NPU trio without flm).
    monkeypatch.setattr("hal0.slots.manager.shutil.which", lambda name: None)
    sm = SlotManager()
    with pytest.raises(SlotConfigError, match="seeded"):
        await sm.delete("agent")


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
        await sm.remove_slot("chat")  # canonical name
    with pytest.raises(SlotConfigError, match="seeded"):
        await sm.remove_slot("primary")  # back-compat alias → chat (still seeded)
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
    snap = await sm.load("chat")
    assert snap.state == SlotState.READY
    # state.json on disk reflects READY.
    state_path = Path(tmp_hal0_home) / "var-lib" / "hal0" / "slots" / "chat" / "state.json"
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
    await sm.load("chat")
    calls_before = len(lemonade_loaded_stub["load_calls"])
    snap = await sm.load("chat")
    assert snap.state == SlotState.READY
    # No extra /v1/load — already loaded.
    assert len(lemonade_loaded_stub["load_calls"]) == calls_before


async def test_unload_transitions_to_offline(
    slot_root: Path,
    lemonade_loaded_stub: dict[str, Any],
) -> None:
    sm = SlotManager()
    await sm.load("chat")
    snap = await sm.unload("chat")
    assert snap.state == SlotState.OFFLINE
    assert lemonade_loaded_stub["unload_calls"], "expected /v1/unload"


async def test_restart_round_trip(
    slot_root: Path,
    lemonade_loaded_stub: dict[str, Any],
) -> None:
    sm = SlotManager()
    await sm.load("chat")
    snap = await sm.restart("chat")
    assert snap.state == SlotState.READY


async def test_swap_replaces_model_id(
    slot_root: Path,
    lemonade_loaded_stub: dict[str, Any],
) -> None:
    sm = SlotManager()
    await sm.load("chat")
    snap = await sm.swap("chat", "llama-3.2-3b-q4_k_m")
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
        await sm.load("chat")
    snap = await sm.status("chat")
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
    await sm.load("chat")
    # Mutate the stub state so lemond no longer reports the model loaded.
    lemonade_loaded_stub["loaded"] = []
    snap = await sm.status("chat")
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
    await sm._transition("chat", SlotState.OFFLINE, force=True)
    snap = await sm.status("chat")
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

    state_path = Path(tmp_hal0_home) / "var-lib" / "hal0" / "slots" / "chat" / "state.json"
    write_state_atomic(
        state_path,
        SlotStateRecord(name="chat", state=SlotState.OFFLINE, port=8081, extra={}),
    )
    # Empty out lemond's loaded[] so adoption can't fire.
    lemonade_loaded_stub["loaded"] = []

    sm = SlotManager()
    snap = await sm.status("chat")
    assert snap.backend == "vulkan"
    assert snap.metadata.get("backend") == "vulkan"


async def test_status_unloaded_slot_uses_toml_backend(
    slot_root: Path,
    lemonade_loaded_stub: dict[str, Any],
) -> None:
    """Fresh TOML, no state.json — surface backend from TOML defaults."""
    lemonade_loaded_stub["loaded"] = []  # OFFLINE — no adoption.
    sm = SlotManager()
    snap = await sm.status("chat")
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
    assert {"chat", "embed"}.issubset(names)


# ── error paths ─────────────────────────────────────────────────────────────


async def test_load_unknown_slot_raises_typed(slot_root: Path) -> None:
    sm = SlotManager()
    with pytest.raises(SlotNotFound) as exc_info:
        await sm.load("nonexistent")
    assert exc_info.value.code == "slot.not_found"


async def test_illegal_transition_blocked(slot_root: Path) -> None:
    """Direct _transition() with an illegal edge raises IllegalSlotTransition."""
    sm = SlotManager()
    await sm._transition("chat", SlotState.OFFLINE, force=True)
    with pytest.raises(IllegalSlotTransition) as exc_info:
        await sm._transition("chat", SlotState.READY)
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
        await sm.delete("chat")


async def test_update_config_rewrites_toml(slot_root: Path) -> None:
    sm = SlotManager()
    from hal0.slots.state import SlotState as _S

    await sm._transition("chat", _S.OFFLINE, force=True)
    await sm.update_config("chat", {"workers": 4})
    cfg_text = (slot_root / "chat.toml").read_text(encoding="utf-8")
    assert "workers = 4" in cfg_text


async def test_update_config_backend_invalidates_state_extras(
    slot_root: Path,
    tmp_hal0_home: str,
    lemonade_loaded_stub: dict[str, Any],
) -> None:
    """Issue #359: changing ``backend`` via update_config() must clear
    the stale ``extra.backend`` mirror in state.json.

    Before the fix, ``status()`` short-circuited to the persisted record
    while the model was in lemond's ``loaded[]`` and reported the old
    backend forever. The adoption probe never re-ran because ``rec``
    already existed.
    """
    from hal0.slots.state import SlotStateRecord, read_state, write_state_atomic

    # Seed an adopted-style state.json: primary is READY with
    # extra.backend=rocm (the boot-time adopted value).
    state_path = Path(tmp_hal0_home) / "var-lib" / "hal0" / "slots" / "chat" / "state.json"
    write_state_atomic(
        state_path,
        SlotStateRecord(
            name="chat",
            state=SlotState.READY,
            model_id="qwen3-4b-q4_k_m",
            port=8081,
            extra={"backend": "rocm", "provider": "lemonade", "adopted": True},
        ),
    )

    sm = SlotManager()
    snap_before = await sm.status("chat")
    # W3: the base ``backend`` field is now derived from the authoritative
    # TOML ``device`` (what the next /v1/load will actually request), not the
    # stale adopted ``extra.backend`` mirror — the primary fixture's device is
    # gpu-vulkan. The adopted/runtime value remains visible via the separate
    # ``actual_backend`` lemonade enrichment.
    assert snap_before.backend == "vulkan"

    snap_after = await sm.update_config("chat", {"backend": "vulkan"})
    # The snapshot returned from update_config() must reflect the new
    # backend (this is what the API handler returns to the client).
    assert snap_after.backend == "vulkan"
    assert snap_after.metadata.get("backend") == "vulkan"

    # And a fresh status() call (after the in-memory cache) reads the
    # same value — the persisted state.json was rewritten.
    snap_via_status = await sm.status("chat")
    assert snap_via_status.backend == "vulkan"

    # state.json on disk reflects the new backend too.
    rec = read_state(state_path)
    assert rec is not None
    assert rec.extra.get("backend") == "vulkan"
    # Unrelated extras (adoption marker) are preserved — we only
    # invalidate the keys the operator actually changed.
    assert rec.extra.get("adopted") is True
    assert rec.extra.get("provider") == "lemonade"


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
    await sm.load("chat")
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


async def test_status_surfaces_last_used_at(
    slot_root: Path,
    lemonade_loaded_stub: dict[str, Any],
    tmp_hal0_home: str,
) -> None:
    """Slot snapshots expose last_used_at so /api/slots can render the

    'recently live within 1h' indicator. None before any request lands;
    bumps to the current wall clock after a request.
    """
    sm = SlotManager()
    await sm.load("chat")
    # Cold slot — clear any bumps internal load paths may have produced
    # so we exercise the "no bumps yet" branch deterministically.
    sm._last_used.pop("chat", None)
    snap = await sm.status("chat")
    assert snap.last_used_at is None
    assert snap.as_dict()["last_used_at"] is None

    sm.bump_last_used("chat")
    snap2 = await sm.status("chat")
    assert snap2.last_used_at is not None
    assert snap2.last_used_at > 0
    payload = snap2.as_dict()
    assert payload["last_used_at"] == snap2.last_used_at


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
    snap = await sm.load("chat")
    assert snap.state == SlotState.READY


async def test_update_config_preserves_sibling_model_keys(slot_root: Path) -> None:
    """Regression: a partial ``{"model": {...}}`` update must not clobber siblings.

    ``PATCH /api/slots/{name}/defaults`` sends only the model sub-keys it
    is changing (e.g. ``ctx_size``/``n_gpu_layers``). The shallow merge
    used to replace the whole ``[model]`` table, silently dropping
    ``[model].default`` (the model name). After a restart the slot could
    no longer resolve a model and the dashboard Start button became a
    silent no-op. update_config must merge nested tables, not clobber.
    """
    from hal0.slots.state import SlotState as _S

    sm = SlotManager()
    await sm._transition("chat", _S.OFFLINE, force=True)
    # Seeded chat.toml carries [model] default = "qwen3-4b-q4_k_m".
    await sm.update_config("chat", {"model": {"ctx_size": 8192}})
    cfg_text = (slot_root / "chat.toml").read_text(encoding="utf-8")
    # ctx_size is normalized to the canonical context_size (#585) but the
    # value lands either way.
    assert "8192" in cfg_text
    # The pre-existing model default MUST survive the partial update.
    assert '"qwen3-4b-q4_k_m"' in cfg_text


async def test_update_config_normalizes_ctx_size_to_context_size(
    slot_root: Path,
) -> None:
    """#585: the dashboard writes the legacy ``ctx_size`` alias; persist it as
    the canonical ``context_size`` so the two keys never diverge on disk.
    """
    from hal0.slots.state import SlotState as _S

    sm = SlotManager()
    await sm._transition("chat", _S.OFFLINE, force=True)
    await sm.update_config("chat", {"model": {"ctx_size": 32768}})
    cfg_text = (slot_root / "chat.toml").read_text(encoding="utf-8")
    assert "context_size = 32768" in cfg_text
    # The legacy alias must NOT linger alongside the canonical key.
    assert "ctx_size = " not in cfg_text


async def test_update_config_ctx_size_alias_wins_over_stale_context_size(
    slot_root: Path,
) -> None:
    """A fresh dashboard write (``ctx_size``) must override a stale
    ``context_size`` seed, then collapse to the single canonical key.
    """
    from hal0.slots.state import SlotState as _S

    # Seed a context_size so the merge sees both keys.
    (slot_root / "chat.toml").write_text(
        "\n".join(
            [
                'name = "chat"',
                "port = 8081",
                'provider = "lemonade"',
                "enabled = true",
                "[model]",
                'default = "qwen3-4b-q4_k_m"',
                "context_size = 4096",
                "",
            ]
        ),
        encoding="utf-8",
    )
    sm = SlotManager()
    await sm._transition("chat", _S.OFFLINE, force=True)
    await sm.update_config("chat", {"model": {"ctx_size": 32768}})
    cfg_text = (slot_root / "chat.toml").read_text(encoding="utf-8")
    assert "context_size = 32768" in cfg_text
    assert "4096" not in cfg_text
    assert "ctx_size = " not in cfg_text
