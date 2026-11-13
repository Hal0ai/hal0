"""An xdna slot stamped into the FLM trio's port group is refused, never migrated.

Before ADR-0057's launch fix, a slot made xdna (``binary = "xdna"``,
``device = "npu"``) could have its identity row stamped ``npu-flm-trio``, so
the port authority shared FLM's port with it. hal0 does not move that claim.
The write that would make such a slot xdna is refused, its load is refused,
and the boot fold logs it and changes nothing. A new xdna slot created beside
FLM gets a port of its own. FLM trio members and GPU slots are untouched.
"""

from __future__ import annotations

import tomllib
from pathlib import Path
from typing import Any

import pytest

from hal0.ports.authority import PortAuthority
from hal0.slot_config import write_slot_toml
from hal0.slots.identity import SlotIdentityStore
from hal0.slots.manager import SlotManager
from hal0.slots.state import XdnaInFlmPortGroup

_FLM_PORT = 8088
_TRIO = "npu-flm-trio"


@pytest.fixture
def hal0_home(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    monkeypatch.setenv("HAL0_HOME", str(tmp_path))
    return tmp_path


def _manager(tmp_path: Path) -> SlotManager:
    db = tmp_path / "hal0.db"
    return SlotManager(
        identity_store=SlotIdentityStore(db_path=db),
        port_authority=PortAuthority(pool=(8081, 8200), db_path=db),
    )


def _write(sm: SlotManager, name: str, **cfg: Any) -> None:
    body = {"name": name, "type": "llm", "device": "npu", "port": _FLM_PORT, **cfg}
    write_slot_toml(sm._config_file(name), body)


def _seed(sm: SlotManager, *, xdna_binary: str | None = "xdna") -> tuple[int, int]:
    """FLM anchor + a second npu slot whose row is in the trio group, sharing FLM's port."""
    _write(sm, "flm", model={"default": "gemma3:1b"})
    extra = {"binary": xdna_binary} if xdna_binary else {}
    _write(sm, "xdna", model={"default": ""}, **extra)
    flm = sm._identity.create(name="flm", slot_type="llm", device="npu", coresident_group=_TRIO)
    other = sm._identity.create(name="xdna", slot_type="llm", device="npu", coresident_group=_TRIO)
    sm._port_authority.acquire(
        flm.id,
        preferred=_FLM_PORT,
        coresident_group=_TRIO,
        owner_label="slot:flm",
        include_listeners=False,
    )
    assert sm._port_authority.acquire(other.id, coresident_group=_TRIO) == _FLM_PORT
    return flm.id, other.id


def _toml(sm: SlotManager, name: str) -> dict[str, Any]:
    with sm._config_file(name).open("rb") as fh:
        return tomllib.load(fh)


def _snapshot(sm: SlotManager) -> tuple[Any, ...]:
    rows = sorted((r.name, r.coresident_group) for r in sm._identity.list_all())
    claims = sorted((c.port, c.slot_id, c.coresident_group) for c in sm._port_authority.claims())
    return tuple(rows), tuple(claims)


# ── 1. write time ────────────────────────────────────────────────────────────


async def test_binary_flip_to_xdna_in_the_flm_group_is_refused(hal0_home: Path) -> None:
    sm = _manager(hal0_home)
    _seed(sm, xdna_binary=None)
    before = _snapshot(sm), _toml(sm, "xdna")

    with pytest.raises(XdnaInFlmPortGroup) as exc:
        await sm.update_config("xdna", {"binary": "xdna"})

    assert exc.value.status == 409
    assert exc.value.code == "slot.xdna_in_flm_port_group"
    assert "delete the slot" in str(exc.value)
    assert (_snapshot(sm), _toml(sm, "xdna")) == before


async def test_create_xdna_on_a_name_still_in_the_flm_group_is_refused(
    hal0_home: Path,
) -> None:
    sm = _manager(hal0_home)
    sm._identity.create(name="xdna", slot_type="llm", device="npu", coresident_group=_TRIO)

    with pytest.raises(XdnaInFlmPortGroup):
        await sm.create(
            "xdna",
            {"name": "xdna", "type": "llm", "device": "npu", "binary": "xdna", "port": 8090},
        )
    assert not sm._config_file("xdna").exists()


async def test_new_xdna_slot_beside_flm_gets_a_port_of_its_own(hal0_home: Path) -> None:
    sm = _manager(hal0_home)
    flm = await sm.create(
        "flm",
        {
            "name": "flm",
            "type": "llm",
            "device": "npu",
            "port": _FLM_PORT,
            "model": {"default": "gemma3:1b"},
        },
    )
    xdna = await sm.create(
        "xdna",
        {
            "name": "xdna",
            "type": "llm",
            "device": "npu",
            "binary": "xdna",
            "port": _FLM_PORT,  # even asking for FLM's port must not share it
            "model": {"default": "qwen3.5-0.8b-q4_k_m"},
        },
    )

    row = sm._identity.get_by_name("xdna")
    assert row is not None and row.coresident_group is None
    own = sm._port_authority.held_by(xdna.slot_id)
    assert own is not None and own != _FLM_PORT
    assert xdna.port == own
    assert sm._port_authority.held_by(flm.slot_id) == _FLM_PORT


# ── 2. boot + load ───────────────────────────────────────────────────────────


async def test_boot_fold_logs_and_changes_nothing(
    hal0_home: Path, caplog: pytest.LogCaptureFixture
) -> None:
    sm = _manager(hal0_home)
    _seed(sm)
    before = _snapshot(sm), _toml(sm, "xdna"), _toml(sm, "flm")

    with caplog.at_level("ERROR"):
        await sm.fold_identity()

    assert (_snapshot(sm), _toml(sm, "xdna"), _toml(sm, "flm")) == before
    errors = [r for r in caplog.records if r.message == "slot.xdna_in_flm_port_group"]
    assert len(errors) == 1
    assert errors[0].slot == "xdna"  # type: ignore[attr-defined]
    assert "create a new xdna slot" in errors[0].hint  # type: ignore[attr-defined]


async def test_load_refuses_instead_of_launching_on_flms_port(hal0_home: Path) -> None:
    sm = _manager(hal0_home)
    _seed(sm)
    cfg = await sm._load_slot_config("xdna")

    with pytest.raises(XdnaInFlmPortGroup):
        await sm._check_load(cfg, "qwen3.5-0.8b-q4_k_m", {})
    with pytest.raises(XdnaInFlmPortGroup):
        await sm._spawn_locked("xdna", cfg, "qwen3.5-0.8b-q4_k_m", model_info={})


# ── untouched ────────────────────────────────────────────────────────────────


async def test_flm_trio_and_gpu_slots_are_untouched(hal0_home: Path) -> None:
    sm = _manager(hal0_home)
    for slot_type, name in (
        ("llm", "flm"),
        ("transcription", "flm-stt"),
        ("embedding", "flm-embed"),
    ):
        row = sm._identity.create(
            name=name, slot_type=slot_type, device="npu", coresident_group=_TRIO
        )
        write_slot_toml(
            sm._config_file(name),
            {
                "name": name,
                "type": slot_type,
                "device": "npu",
                "port": _FLM_PORT,
                "model": {"default": "gemma3:1b"},
            },
        )
        sm._port_authority.acquire(
            row.id,
            preferred=_FLM_PORT,
            coresident_group=_TRIO,
            owner_label=f"slot:{name}",
            include_listeners=False,
        )
    gpu = sm._identity.create(name="agent", slot_type="llm", device="gpu-rocm")
    write_slot_toml(
        sm._config_file("agent"),
        {
            "name": "agent",
            "type": "llm",
            "device": "gpu-rocm",
            "port": 8081,
            "model": {"default": "m"},
        },
    )
    sm._port_authority.acquire(
        gpu.id, preferred=8081, owner_label="slot:agent", include_listeners=False
    )
    before = _snapshot(sm)

    await sm.fold_identity()
    for name in ("flm", "agent"):
        sm._refuse_xdna_in_flm_port_group(name, await sm._load_slot_config(name))

    assert _snapshot(sm) == before
