"""hal0.ports — the global port-claim registry (2026-07-11).

Pins the invariants that prevented another flm-stt/ops-style double-claim:
claims are recomputed from every source (slot TOMLs incl. disabled slots,
runtime rows with virtual ports, reserved, listeners), deleting a config
releases its port with no bookkeeping, auto-assign skips runtime-only
claims, and explicit-port validation names the conflicting owner.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from hal0 import ports


def _slot_toml(dir: Path, name: str, port: int | None, *, nested: bool = False) -> None:
    body = f'name = "{name}"\n'
    if port is not None:
        body += f"[server]\nport = {port}\n" if nested else f"port = {port}\n"
    (dir / f"{name}.toml").write_text(body)


def _claims(tmp_path: Path, **kw):
    kw.setdefault("include_listeners", False)
    return ports.collect_claims(slots_dir=tmp_path, pool=(8081, 8099), **kw)


# ── collection ───────────────────────────────────────────────────────────────


def test_config_claims_cover_top_level_and_nested_and_disabled(tmp_path) -> None:
    _slot_toml(tmp_path, "a", 8081)
    _slot_toml(tmp_path, "b", 8082, nested=True)
    # Disabled slots still own their port — config is the claim, not state.
    (tmp_path / "c.toml").write_text('name = "c"\nenabled = false\nport = 8083\n')
    claims = _claims(tmp_path)
    assert {(c.port, c.owner) for c in claims} == {
        (8081, "slot:a"),
        (8082, "slot:b"),
        (8083, "slot:c"),
    }


def test_runtime_claims_cover_virtual_ports_no_toml_mentions(tmp_path) -> None:
    """The flm-stt case: TOML says 8088, the runtime row claims 8089."""
    _slot_toml(tmp_path, "flm-stt", 8088)
    claims = _claims(tmp_path, slot_snapshots=[{"name": "flm-stt", "port": 8089}])
    ports_by_owner = {(c.port, c.source) for c in claims if c.owner == "slot:flm-stt"}
    assert ports_by_owner == {(8088, "slot-config"), (8089, "slot-runtime")}


def test_deleting_a_config_releases_its_port(tmp_path) -> None:
    _slot_toml(tmp_path, "dead", 8081)
    assert ports.next_free(_claims(tmp_path), 8081, 8099) == 8082
    (tmp_path / "dead.toml").unlink()
    # No release call, no stored table — the claim vanished with its source.
    assert ports.next_free(_claims(tmp_path), 8081, 8099) == 8081


def test_malformed_toml_is_skipped(tmp_path) -> None:
    (tmp_path / "junk.toml").write_text("port = [not toml")
    _slot_toml(tmp_path, "ok", 8081)
    assert {c.owner for c in _claims(tmp_path)} == {"slot:ok"}


# ── questions ────────────────────────────────────────────────────────────────


def test_next_free_skips_every_source(tmp_path) -> None:
    _slot_toml(tmp_path, "a", 8081)
    claims = _claims(
        tmp_path,
        slot_snapshots=[{"name": "ghost", "port": 8082}],
        reserved={8083: "api"},
    )
    assert ports.next_free(claims, 8081, 8099) == 8084


def test_next_free_exhausted_pool_returns_none(tmp_path) -> None:
    for i, port in enumerate(range(8081, 8084)):
        _slot_toml(tmp_path, f"s{i}", port)
    assert ports.next_free(_claims(tmp_path), 8081, 8083) is None


def test_conflict_requires_two_distinct_owners(tmp_path) -> None:
    _slot_toml(tmp_path, "ops", 8089)
    # Same slot's config + runtime row on one port: NOT a conflict.
    claims = _claims(tmp_path, slot_snapshots=[{"name": "ops", "port": 8089}])
    assert ports.conflicts(claims) == []
    # A second slot's runtime row on the same port: conflict, both named.
    claims = _claims(
        tmp_path,
        slot_snapshots=[{"name": "ops", "port": 8089}, {"name": "flm-stt", "port": 8089}],
    )
    found = ports.conflicts(claims)
    assert len(found) == 1
    assert found[0]["port"] == 8089
    assert found[0]["owners"] == ["slot:flm-stt", "slot:ops"]


def test_claimed_by_other_names_the_owner_not_self(tmp_path) -> None:
    _slot_toml(tmp_path, "ops", 8089)
    claims = _claims(tmp_path)
    assert ports.claimed_by_other(claims, 8089, "slot:ops") == set()
    assert ports.claimed_by_other(claims, 8089, "slot:new") == {"slot:ops"}


def test_port_report_shape(tmp_path) -> None:
    _slot_toml(tmp_path, "a", 8081)
    report = ports.port_report(slots_dir=tmp_path, pool=(8081, 8085))
    assert report["pool"] == {"start": 8081, "end": 8085}
    assert report["conflicts"] == []
    assert any(c["owner"] == "slot:a" for c in report["claims"])
    # 8081 claimed by config; a listener may hold others on the test box —
    # next_free is whatever the registry says, but never the claimed 8081.
    assert report["next_free"] != 8081


# ── slot-route integration: auto-assign sees runtime claims ─────────────────


def test_next_free_slot_port_skips_runtime_claims(tmp_path, monkeypatch) -> None:
    from hal0.api.routes import slots as slots_routes
    from hal0.config import paths as config_paths

    _slot_toml(tmp_path, "flm-stt", 8088)
    monkeypatch.setattr(config_paths, "slots_config_dir", lambda: tmp_path)
    # Registry must see the runtime-only 8089 claim and skip past it.
    port = slots_routes._next_free_slot_port(
        8088, 8099, slot_snapshots=[{"name": "flm-stt", "port": 8089}]
    )
    assert port not in (8088, 8089)


def test_reject_port_conflict_raises_with_owner(tmp_path, monkeypatch) -> None:
    from hal0.api.middleware.error_codes import Hal0Error
    from hal0.api.routes import slots as slots_routes
    from hal0.config import paths as config_paths

    _slot_toml(tmp_path, "ops", 8089)
    monkeypatch.setattr(config_paths, "slots_config_dir", lambda: tmp_path)
    with pytest.raises(Hal0Error) as exc:
        slots_routes._reject_port_conflict(8089, "newslot", [])
    assert "slot:ops" in str(exc.value)
    # A slot re-asserting its own port is fine.
    slots_routes._reject_port_conflict(8089, "ops", [])


def test_coresident_group_sharing_is_not_a_conflict(tmp_path) -> None:
    """The FLM trio shares its container's port by design — via runtime
    group markers AND via device=npu config claims."""
    for n in ("flm", "flm-embed", "flm-stt"):
        (tmp_path / f"{n}.toml").write_text(f'name = "{n}"\ndevice = "npu"\nport = 8088\n')
    trio = [
        {"name": n, "port": 8088, "coresident_group": "npu-flm-trio"}
        for n in ("flm", "flm-embed", "flm-stt")
    ]
    claims = _claims(tmp_path, slot_snapshots=trio)
    assert ports.conflicts(claims) == []
    # A slot OUTSIDE the group landing on the same port still conflicts.
    claims = _claims(tmp_path, slot_snapshots=[*trio, {"name": "ops", "port": 8088}])
    found = ports.conflicts(claims)
    assert len(found) == 1 and "slot:ops" in found[0]["owners"]
