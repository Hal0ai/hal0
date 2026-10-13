"""Unit tests for the in-process slot-write boundary (`guard_slot_write_payload`).

The HTTP slot-config routes enforce the slot key partition
(``routes/slots._reject_slot_tuning_keys`` + ``_reject_unknown_config_keys``).
A writer that reaches slot TOML IN-PROCESS without passing a FastAPI handler —
the stacks apply engine, ``SlotManager.update_config`` — is held to the same
contract through ``reconcile_and_guard_slot_config`` / ``reconcile_slot_updates``.

ADR-0029 §5: every tune lives on the slot's profile, so a write carrying
``[model].context_size`` / ``ctx_size``, ``[server].extra_args``, ``parallel``,
``chat_template``, ``mtp``, ``enable_thinking``, ``vision`` or ``workers`` is
refused with ``slot.tuning_key_denied``.

Targeted file run:
    uv run pytest tests/slots/test_write_boundary_guard.py -q
"""

from __future__ import annotations

from pathlib import Path

import pytest

from hal0.errors import BadRequest
from hal0.slots.config_write import (
    guard_slot_write_payload,
    reconcile_and_guard_slot_config,
    reconcile_slot_updates,
)


def _slots_dir(home: str) -> Path:
    d = Path(home) / "etc" / "hal0" / "slots"
    d.mkdir(parents=True, exist_ok=True)
    return d


# ── the partition itself ─────────────────────────────────────────────────────

_TUNING_PAYLOADS = [
    ({"mtp": True}, "mtp"),
    ({"enable_thinking": False}, "enable_thinking"),
    ({"vision": True}, "vision"),
    ({"parallel": 2}, "parallel"),
    ({"chat_template": "chatml"}, "chat_template"),
    ({"workers": 4}, "workers"),
    ({"ctx_size": 8192}, "ctx_size"),
    ({"model": {"default": "m", "context_size": 8192}}, "[model].context_size"),
    ({"model": {"default": "m", "ctx_size": 8192}}, "[model].ctx_size"),
    ({"model": {"default": "m", "n_gpu_layers": 40}}, "[model].n_gpu_layers"),
    ({"model": {"default": "m", "rope_freq_base": 1e6}}, "[model].rope_freq_base"),
    ({"server": {"extra_args": "-ctk q8_0"}}, "[server].extra_args"),
]


@pytest.mark.parametrize(("payload", "key"), _TUNING_PAYLOADS)
def test_guard_rejects_every_tuning_key(payload: dict, key: str) -> None:
    with pytest.raises(BadRequest) as exc:
        guard_slot_write_payload(payload)
    assert exc.value.code == "slot.tuning_key_denied"
    assert exc.value.details["keys"] == [key]
    assert "profile" in exc.value.message


@pytest.mark.parametrize(("payload", "key"), _TUNING_PAYLOADS)
def test_reconcile_slot_updates_rejects_every_tuning_key(payload: dict, key: str) -> None:
    """``SlotManager.update_config``'s merge refuses the same keys, so an
    in-process writer can never persist a file that then fails to load as
    ``slot.unconverted``."""
    with pytest.raises(BadRequest) as exc:
        reconcile_slot_updates({"name": "agent", "port": 8087}, payload)
    assert exc.value.code == "slot.tuning_key_denied"
    assert key in exc.value.details["keys"]


@pytest.mark.parametrize("flags", ["", "-fa auto", "-ngl 99", "--port 9999"])
def test_any_server_extra_args_is_a_tuning_key(flags: str) -> None:
    """Launch flags are profile-owned: ``[server].extra_args`` is refused
    whatever it carries (clean, blank, hardware or managed)."""
    with pytest.raises(BadRequest) as exc:
        guard_slot_write_payload({"server": {"extra_args": flags}})
    assert exc.value.code == "slot.tuning_key_denied"


def test_guard_rejects_a_removed_key() -> None:
    """``enabled`` (#1369) gets its migration pointer, not a spelling hint."""
    with pytest.raises(BadRequest) as exc:
        guard_slot_write_payload({"enabled": False})
    assert exc.value.code == "slot.removed_key_denied"


def test_guard_reports_tuning_before_removed() -> None:
    """Ordering matches the routes: the partition message wins."""
    with pytest.raises(BadRequest) as exc:
        guard_slot_write_payload({"enabled": False, "mtp": True})
    assert exc.value.code == "slot.tuning_key_denied"


def test_guard_allows_a_legitimate_payload() -> None:
    """The guard must not become a general-purpose schema check.

    An ordinary placement/profile/model write passes untouched, and so does
    an unrecognised key (that is ``unknown_slot_config_keys``' job at the HTTP
    boundary).
    """
    guard_slot_write_payload(
        {
            "device": "gpu-rocm",
            "profile": "agent",
            "n_gpu_layers": 99,
            "threads": 8,
            "model": {"default": "m"},
            "server": {"env": {"HSA_OVERRIDE_GFX_VERSION": "11.5.1"}},
            "some_future_key": 1,
        }
    )


# ── placement: the guard sees the PAYLOAD, never the merged result ────────────


def test_unconverted_key_in_the_base_does_not_trip_the_payload_guard(
    tmp_hal0_home: str,
) -> None:
    """The guard screens the PAYLOAD, never the merged result.

    Converting an unconverted slot is the one-shot converter's job
    (``hal0 profile migrate``); the write guard only answers "does this write
    introduce a tuning key", so a model swap carries no blame for a key that
    was already in the base dict.
    """
    slots = _slots_dir(tmp_hal0_home)
    (slots / "agent.toml").write_text(
        'name = "agent"\nport = 8087\nmtp = true\nvision = true\n', encoding="utf-8"
    )
    before = {"name": "agent", "port": 8087, "mtp": True, "vision": True}

    merged = reconcile_and_guard_slot_config(
        "agent", before, {"model": {"default": "new"}}, slots_dir=slots
    )

    assert merged["model"]["default"] == "new"
    assert merged["mtp"] is True, "the legacy value survives; the guard didn't fire"


def test_reconcile_and_guard_refuses_a_tuning_write(tmp_hal0_home: str) -> None:
    """The shared pipeline is where the guard lives, not the stacks call site.

    Placing it here means every future in-process writer that routes through
    ``reconcile_and_guard_slot_config`` inherits the partition by construction —
    that function's stated contract is "a writer can no longer persist what
    ``update_config`` would refuse".
    """
    slots = _slots_dir(tmp_hal0_home)
    (slots / "agent.toml").write_text('name = "agent"\nport = 8087\n', encoding="utf-8")
    with pytest.raises(BadRequest) as exc:
        reconcile_and_guard_slot_config(
            "agent", {"name": "agent", "port": 8087}, {"mtp": True}, slots_dir=slots
        )
    assert exc.value.code == "slot.tuning_key_denied"


def test_guard_runs_before_the_merge(tmp_hal0_home: str) -> None:
    """A rejected write leaves the caller's ``base`` dict untouched.

    The guard is the first statement in the pipeline, so a violation costs no
    merge work and can't leave a half-projected dict behind for a caller that
    catches the error and carries on (which ``StackApplyEngine.plan`` does).
    """
    slots = _slots_dir(tmp_hal0_home)
    (slots / "agent.toml").write_text('name = "agent"\nport = 8087\n', encoding="utf-8")
    base = {"name": "agent", "port": 8087, "model": {"default": "old"}}
    snapshot = {"name": "agent", "port": 8087, "model": {"default": "old"}}
    with pytest.raises(BadRequest):
        reconcile_and_guard_slot_config(
            "agent", base, {"model": {"default": "new"}, "vision": True}, slots_dir=slots
        )
    assert base == snapshot, "base must never be mutated, rejected or not"


# ── SlotManager is held to the same boundary (in-process writers) ─────────────


def _base_cfg(name: str = "chat") -> dict:
    return {
        "name": name,
        "port": 18081,
        "type": "llm",
        "device": "gpu-rocm",
        "profile": "chat",
        "model": {"default": "m"},
    }


def _with_payload(cfg: dict, payload: dict) -> dict:
    """Overlay ``payload`` onto ``cfg`` one table deep (so [model] keeps default)."""
    out = {**cfg}
    for key, value in payload.items():
        if isinstance(value, dict) and isinstance(out.get(key), dict):
            out[key] = {**out[key], **value}
        else:
            out[key] = value
    return out


@pytest.mark.parametrize(("payload", "key"), _TUNING_PAYLOADS)
async def test_slot_manager_create_refuses_every_tuning_key(
    tmp_hal0_home: str, payload: dict, key: str
) -> None:
    from hal0.config import paths
    from hal0.slots.manager import SlotManager

    with pytest.raises(BadRequest) as exc:
        await SlotManager().create("chat", _with_payload(_base_cfg(), payload))
    assert exc.value.code == "slot.tuning_key_denied"
    assert key in exc.value.details["keys"]
    assert not (paths.slots_config_dir() / "chat.toml").exists()


@pytest.mark.parametrize(("payload", "key"), _TUNING_PAYLOADS)
async def test_slot_manager_update_config_refuses_every_tuning_key(
    tmp_hal0_home: str, payload: dict, key: str
) -> None:
    from hal0.config import paths
    from hal0.slots.manager import SlotManager

    sm = SlotManager()
    await sm.create("chat", _base_cfg())
    path = paths.slots_config_dir() / "chat.toml"
    before = path.read_text(encoding="utf-8")

    with pytest.raises(BadRequest) as exc:
        await sm.update_config("chat", payload)
    assert exc.value.code == "slot.tuning_key_denied"
    assert key in exc.value.details["keys"]
    assert path.read_text(encoding="utf-8") == before


async def test_slot_manager_create_refuses_a_removed_key(tmp_hal0_home: str) -> None:
    from hal0.config import paths
    from hal0.slots.manager import SlotManager

    with pytest.raises(BadRequest) as exc:
        await SlotManager().create("chat", {**_base_cfg(), "enabled": True})
    assert exc.value.code == "slot.removed_key_denied"
    assert not (paths.slots_config_dir() / "chat.toml").exists()


async def test_slot_manager_update_config_refuses_a_removed_key(tmp_hal0_home: str) -> None:
    from hal0.config import paths
    from hal0.slots.manager import SlotManager

    sm = SlotManager()
    await sm.create("chat", _base_cfg())
    path = paths.slots_config_dir() / "chat.toml"
    before = path.read_text(encoding="utf-8")

    with pytest.raises(BadRequest) as exc:
        await sm.update_config("chat", {"enabled": False})
    assert exc.value.code == "slot.removed_key_denied"
    assert path.read_text(encoding="utf-8") == before


async def test_slot_manager_clean_create_and_update_still_work(tmp_hal0_home: str) -> None:
    """The guard refuses tunes and removed keys, nothing else: an ordinary
    placement/profile/model write still lands."""
    from hal0.slots.manager import SlotManager

    sm = SlotManager()
    await sm.create("chat", _base_cfg())
    await sm.update_config("chat", {"model": {"default": "new"}, "priority": 3})

    cfg = await sm.get_config("chat")
    assert cfg["model"]["default"] == "new"
    assert cfg["priority"] == 3
    assert cfg["profile"] == "chat"
