"""An xdna slot stamped into the FLM trio's port group gets its own port back.

Before ADR-0057's routing fix, an xdna slot (``binary = "xdna"``,
``device = "npu"``) was given the ``npu-flm-trio`` coresident group at create
time, so the port authority handed it the FLM anchor's shared port. The group
lives on the identity row, which nothing re-derived: the slot kept FLM's port
forever. These tests pin that the boot fold and a config write both re-derive
the group, grant the slot a port of its own, and mirror it into the TOML.
"""

from __future__ import annotations

import tomllib
from pathlib import Path

import pytest

from hal0.ports.authority import PortAuthority
from hal0.slot_config import write_slot_toml
from hal0.slots.identity import SlotIdentityStore
from hal0.slots.manager import SlotManager

_FLM_PORT = 8088


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


def _seed_shared_state(sm: SlotManager, *, xdna_binary: str | None) -> tuple[int, int]:
    """The pre-fix on-disk state: flm anchor + a second npu slot sharing its port.

    Returns ``(flm_id, other_id)``. ``other`` carries ``binary = xdna_binary``
    on disk but its identity row was minted with the FLM trio group, and it
    holds no claim of its own (it shares the group's).
    """
    flm_cfg = {
        "name": "flm",
        "type": "llm",
        "device": "npu",
        "port": _FLM_PORT,
        "model": {"default": "gemma3:1b"},
    }
    other_cfg = {
        "name": "xdna",
        "type": "llm",
        "device": "npu",
        "port": _FLM_PORT,
        "model": {"default": "qwen3.5-0.8b-q4_k_m"},
    }
    if xdna_binary is not None:
        other_cfg["binary"] = xdna_binary
    write_slot_toml(sm._config_file("flm"), flm_cfg)
    write_slot_toml(sm._config_file("xdna"), other_cfg)
    flm = sm._identity.create(
        name="flm", slot_type="llm", device="npu", coresident_group="npu-flm-trio"
    )
    other = sm._identity.create(
        name="xdna", slot_type="llm", device="npu", coresident_group="npu-flm-trio"
    )
    sm._port_authority.acquire(
        flm.id,
        preferred=_FLM_PORT,
        coresident_group="npu-flm-trio",
        owner_label="slot:flm",
        include_listeners=False,
    )
    # The shadow-style share: no row of its own, resolves via the group.
    assert sm._port_authority.acquire(other.id, coresident_group="npu-flm-trio") == _FLM_PORT
    return flm.id, other.id


def _toml_port(sm: SlotManager, name: str) -> int:
    with sm._config_file(name).open("rb") as fh:
        raw = tomllib.load(fh)
    return int((raw.get("slot") or raw)["port"])


async def test_boot_fold_moves_a_stale_xdna_row_off_the_flm_port(hal0_home: Path) -> None:
    sm = _manager(hal0_home)
    flm_id, xdna_id = _seed_shared_state(sm, xdna_binary="xdna")

    await sm.fold_identity()

    row = sm._identity.get_by_name("xdna")
    assert row is not None and row.coresident_group is None
    own = sm._port_authority.held_by(xdna_id)
    assert own is not None and own != _FLM_PORT
    assert sm._port_authority.held_by(flm_id) == _FLM_PORT
    assert _toml_port(sm, "xdna") == own
    assert _toml_port(sm, "flm") == _FLM_PORT
    # Idempotent: a second fold changes nothing.
    await sm.fold_identity()
    assert sm._port_authority.held_by(xdna_id) == own


async def test_boot_fold_leaves_a_real_flm_trio_member_alone(hal0_home: Path) -> None:
    sm = _manager(hal0_home)
    _, other_id = _seed_shared_state(sm, xdna_binary=None)

    await sm.fold_identity()

    row = sm._identity.get_by_name("xdna")
    assert row is not None and row.coresident_group == "npu-flm-trio"
    assert sm._port_authority.held_by(other_id, coresident_group="npu-flm-trio") == _FLM_PORT


async def test_binary_flip_to_xdna_via_config_write_regrants_the_port(
    hal0_home: Path,
) -> None:
    sm = _manager(hal0_home)
    flm_id, other_id = _seed_shared_state(sm, xdna_binary=None)
    # Model-less, so the flip is not an exclusivity question either way.
    await sm.update_config("xdna", {"model": {"default": ""}})

    await sm.update_config("xdna", {"binary": "xdna"})

    row = sm._identity.get_by_name("xdna")
    assert row is not None and row.coresident_group is None
    own = sm._port_authority.held_by(other_id)
    assert own is not None and own != _FLM_PORT
    assert _toml_port(sm, "xdna") == own
    assert sm._port_authority.held_by(flm_id) == _FLM_PORT
