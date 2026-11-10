"""An xdna slot stuck in the FLM trio's port group gets its own port back.

Before ADR-0057's routing fix, an xdna slot (``binary = "xdna"``,
``device = "npu"``) was given the ``npu-flm-trio`` coresident group at create
time, so the port authority handed it the FLM anchor's shared port. The group
lives on the identity row, which nothing re-derived. These tests pin the narrow
repair (``SlotManager._evict_xdna_from_flm_group``): only that case moves, in
an order that a failure part-way leaves retryable, and every other row (FLM
members, legacy rows with no group, a slot flipped off the NPU) is untouched.
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
    """The pre-fix state: an FLM anchor plus a second npu slot sharing its port.

    Returns ``(flm_id, other_id)``. ``other`` is minted with the trio group
    and holds no claim of its own (it shares the group's).
    """
    _write(sm, "flm", model={"default": "gemma3:1b"})
    extra = {"binary": xdna_binary} if xdna_binary else {}
    _write(sm, "xdna", model={"default": "qwen3.5-0.8b-q4_k_m"}, **extra)
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


def _toml_port(sm: SlotManager, name: str) -> int:
    with sm._config_file(name).open("rb") as fh:
        raw = tomllib.load(fh)
    return int((raw.get("slot") or raw)["port"])


def _snapshot(sm: SlotManager) -> tuple[Any, ...]:
    rows = sorted((r.name, r.coresident_group) for r in sm._identity.list_all())
    claims = sorted((c.port, c.slot_id, c.coresident_group) for c in sm._port_authority.claims())
    return tuple(rows), tuple(claims)


async def test_boot_fold_moves_a_stale_xdna_row_off_the_flm_port(hal0_home: Path) -> None:
    sm = _manager(hal0_home)
    flm_id, xdna_id = _seed(sm)

    await sm.fold_identity()

    row = sm._identity.get_by_name("xdna")
    assert row is not None and row.coresident_group is None
    own = sm._port_authority.held_by(xdna_id)
    assert own is not None and own != _FLM_PORT
    assert sm._port_authority.held_by(flm_id) == _FLM_PORT
    assert _toml_port(sm, "xdna") == own
    assert _toml_port(sm, "flm") == _FLM_PORT

    before = _snapshot(sm)
    await sm.fold_identity()
    assert _snapshot(sm) == before


async def test_failed_acquire_changes_nothing_and_the_next_fold_retries(
    hal0_home: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    sm = _manager(hal0_home)
    _, xdna_id = _seed(sm)
    before = _snapshot(sm)

    def _boom(*_a: Any, **_k: Any) -> int:
        raise RuntimeError("pool exhausted")

    with monkeypatch.context() as m:
        m.setattr(sm._port_authority, "acquire", _boom)
        await sm.fold_identity()
    assert _snapshot(sm) == before
    assert _toml_port(sm, "xdna") == _FLM_PORT

    await sm.fold_identity()
    row = sm._identity.get_by_name("xdna")
    assert row is not None and row.coresident_group is None
    assert sm._port_authority.held_by(xdna_id) not in (None, _FLM_PORT)


async def test_toml_write_failure_rolls_back_the_claim(
    hal0_home: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    sm = _manager(hal0_home)
    _seed(sm)
    before = _snapshot(sm)

    with monkeypatch.context() as m:
        m.setattr(sm, "_write_back_port", lambda *_a, **_k: False)
        await sm.fold_identity()
    assert _snapshot(sm) == before
    assert _toml_port(sm, "xdna") == _FLM_PORT


async def test_explicit_port_in_the_update_is_honoured(hal0_home: Path) -> None:
    sm = _manager(hal0_home)
    _, xdna_id = _seed(sm)

    await sm.update_config("xdna", {"port": 8150})

    assert sm._port_authority.held_by(xdna_id) == 8150
    assert _toml_port(sm, "xdna") == 8150
    row = sm._identity.get_by_name("xdna")
    assert row is not None and row.coresident_group is None


async def test_binary_flip_to_xdna_via_config_write_moves_the_port(hal0_home: Path) -> None:
    sm = _manager(hal0_home)
    flm_id, other_id = _seed(sm, xdna_binary=None)
    await sm.update_config("xdna", {"model": {"default": ""}})

    await sm.update_config("xdna", {"binary": "xdna"})

    row = sm._identity.get_by_name("xdna")
    assert row is not None and row.coresident_group is None
    own = sm._port_authority.held_by(other_id)
    assert own is not None and own != _FLM_PORT
    assert _toml_port(sm, "xdna") == own
    assert sm._port_authority.held_by(flm_id) == _FLM_PORT


async def test_an_id_keyed_flm_trio_is_untouched(hal0_home: Path) -> None:
    """Anchor + shadows, id-keyed stems (names live in the body)."""
    sm = _manager(hal0_home)
    rows = {}
    for slot_type, name in (
        ("llm", "flm"),
        ("transcription", "flm-stt"),
        ("embedding", "flm-embed"),
    ):
        row = sm._identity.create(
            name=name, slot_type=slot_type, device="npu", coresident_group=_TRIO
        )
        rows[name] = row
        write_slot_toml(
            sm._config_file(str(row.id)),
            {
                "id": row.id,
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
    before = _snapshot(sm)

    await sm.fold_identity()

    assert _snapshot(sm) == before


async def test_a_null_group_legacy_flm_row_is_untouched(hal0_home: Path) -> None:
    sm = _manager(hal0_home)
    _write(sm, "flm", model={"default": "gemma3:1b"})
    row = sm._identity.create(name="flm", slot_type="llm", device="npu", coresident_group=None)
    sm._port_authority.acquire(
        row.id, preferred=_FLM_PORT, owner_label="slot:flm", include_listeners=False
    )
    before = _snapshot(sm)

    await sm.fold_identity()

    assert _snapshot(sm) == before


async def test_flipping_flm_off_the_npu_leaves_groups_and_claims_alone(
    hal0_home: Path,
) -> None:
    """No regroup outside the xdna case: identical to before this repair."""
    sm = _manager(hal0_home)
    flm_id, _ = _seed(sm, xdna_binary=None)
    await sm.update_config("xdna", {"model": {"default": ""}})
    before = _snapshot(sm)

    await sm.update_config("flm", {"device": "gpu-vulkan"})

    assert _snapshot(sm) == before
    assert sm._port_authority.held_by(flm_id) == _FLM_PORT


async def test_xdna_owning_the_group_claim_is_refused_loudly(
    hal0_home: Path, caplog: pytest.LogCaptureFixture
) -> None:
    """Moving the group's own claim would strand FLM: log it, change nothing."""
    sm = _manager(hal0_home)
    _write(sm, "xdna", binary="xdna", model={"default": "qwen3.5-0.8b-q4_k_m"})
    row = sm._identity.create(name="xdna", slot_type="llm", device="npu", coresident_group=_TRIO)
    sm._port_authority.acquire(
        row.id,
        preferred=_FLM_PORT,
        coresident_group=_TRIO,
        owner_label="slot:xdna",
        include_listeners=False,
    )
    before = _snapshot(sm)

    with caplog.at_level("ERROR"):
        await sm.fold_identity()

    assert _snapshot(sm) == before
    assert any(r.message == "slot.xdna_holds_flm_group_port" for r in caplog.records)
