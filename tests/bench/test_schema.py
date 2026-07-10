"""test_schema.py — cell_key stability, canonicalization, and the identity/host
split (DESIGN §3.2).

These lock in the two properties the whole auto-update mechanism rests on:
  1. cell_key is a pure function of the identity block, invariant to dict key
     order (canonical JSON), so equal-as-data identities hash identically; and
  2. host/environment is NOT in cell_key — a hal0 version or GPU change must not
     fork a cell — while any identity change (argv, digest, depth, sampler) DOES.
"""

from __future__ import annotations

from hal0.bench.schema import (
    Config,
    Engine,
    Host,
    Identity,
    Model,
    Outcome,
    Record,
    Workload,
    canonical_json,
    cell_key,
)


def _identity(**over) -> Identity:
    base = dict(
        model=Model(id="m1", sha256="abc", quant="ROCmFPX"),
        engine=Engine(kind="llama-bench", image_digest="sha256:img", llamacpp_build="b9438"),
        lane="rocm",
        config=Config(argv=["-b", "512", "-fa", "on"], kv={"main_k": "q8_0"}, ctx=32768),
        workload=Workload(kind="tg", depth=2048, n_gen=256),
    )
    base.update(over)
    return Identity(**base)


def test_cell_key_is_deterministic():
    assert cell_key(_identity()) == cell_key(_identity())


def test_cell_key_canonicalizes_key_order():
    # canonical_json sorts keys, so a dict built in a different order hashes the
    # same. Feed cell_key an identity-as-dict with shuffled keys.
    from dataclasses import asdict

    d = asdict(_identity())
    shuffled = {k: d[k] for k in reversed(list(d.keys()))}
    assert cell_key(shuffled) == cell_key(_identity())


def test_cell_key_prefix():
    key = cell_key(_identity())
    assert key.startswith("sha256:")
    assert len(key) == len("sha256:") + 64


def test_host_does_not_change_cell_key():
    # host is environment, not identity — it never feeds cell_key. The record's
    # cell_key must be identical regardless of host fields.
    ident = _identity()
    r1 = Record(
        run_id="2026-01-01T00:00:00Z-aaa",
        suite="s",
        trigger="manual",
        identity=ident,
        host=Host(hal0_version="0.9.0", gpu="8060S"),
        outcome=Outcome.OK,
    )
    r2 = Record(
        run_id="2026-02-01T00:00:00Z-bbb",
        suite="s",
        trigger="manual",
        identity=ident,
        host=Host(hal0_version="1.0.0", gpu="9090X"),
        outcome=Outcome.OK,
    )
    assert r1.cell_key == r2.cell_key == cell_key(ident)


def test_identity_change_changes_cell_key():
    base = cell_key(_identity())
    # each of these is inside the identity block, so each must move the key
    assert cell_key(_identity(lane="vulkan_radv")) != base
    assert cell_key(_identity(config=Config(argv=["-b", "1024"]))) != base
    assert cell_key(_identity(workload=Workload(kind="tg", depth=131072))) != base
    assert cell_key(_identity(model=Model(id="m1", sha256="def"))) != base
    assert cell_key(_identity(engine=Engine(llamacpp_build="b9999"))) != base


def test_record_post_init_fills_cell_key():
    ident = _identity()
    r = Record(
        run_id="2026-01-01T00:00:00Z-aaa",
        suite="s",
        trigger="manual",
        identity=ident,
        host=Host(),
        outcome=Outcome.OK,
    )
    assert r.cell_key == cell_key(ident)


def test_to_dict_flattens_outcome_enum():
    r = Record(
        run_id="2026-01-01T00:00:00Z-aaa",
        suite="s",
        trigger="manual",
        identity=_identity(),
        host=Host(),
        outcome=Outcome.OK,
    )
    d = r.to_dict()
    assert d["outcome"] == "ok"  # string, not Enum — so the JSON line is valid


def test_canonical_json_is_sorted_and_compact():
    s = canonical_json({"b": 1, "a": 2})
    assert s == '{"a":2,"b":1}'
