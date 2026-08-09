"""test_bundle.py — bundle = a faithful, selective package of ok records.

Selection contract: only outcome=="ok" records; filterable by run_id set,
suite, and ISO `since` lower bound (record ts derives from run_id, same rule
as store._record_ts). Redaction contract: host.name never leaves the box
unless explicitly requested.
"""

from __future__ import annotations

import json
import tarfile

from hal0.bench.bundle import BundleSpec, select_records
from hal0.bench.store import Store


def _rec(run_id: str, outcome: str = "ok", suite: str = "roster", model: str = "m1") -> dict:
    return {
        "run_id": run_id,
        "cell_key": f"sha256:{model}-{run_id}",
        "suite": suite,
        "trigger": "manual",
        "identity": {
            "model": {"id": model},
            "lane": "rocm",
            "workload": {"kind": "tg", "depth": 2048},
        },
        "host": {"name": "my-secret-hostname", "gpu": "Strix Halo", "hal0_version": "1.0"},
        "outcome": outcome,
        "summary": {"decode_ts_med": 42.0},
        "schema": 2,
    }


def _store(tmp_path, monkeypatch, records) -> Store:
    monkeypatch.setenv("HAL0_BENCH_STATE", str(tmp_path))
    store = Store()
    for r in records:
        store.append_record(r)
    return store


def test_select_only_ok_records(tmp_path, monkeypatch):
    store = _store(
        tmp_path,
        monkeypatch,
        [
            _rec("2026-08-01T00:00:00Z-aaa111"),
            _rec("2026-08-01T00:00:01Z-bbb222", outcome="failed"),
            _rec("2026-08-01T00:00:02Z-ccc333", outcome="oom"),
        ],
    )
    got = select_records(store, BundleSpec())
    assert [r["run_id"] for r in got] == ["2026-08-01T00:00:00Z-aaa111"]


def test_select_by_run_ids(tmp_path, monkeypatch):
    store = _store(
        tmp_path,
        monkeypatch,
        [_rec("2026-08-01T00:00:00Z-aaa111"), _rec("2026-08-02T00:00:00Z-bbb222")],
    )
    spec = BundleSpec(run_ids=["2026-08-02T00:00:00Z-bbb222"])
    got = select_records(store, spec)
    assert [r["run_id"] for r in got] == ["2026-08-02T00:00:00Z-bbb222"]


def test_select_by_suite_and_since(tmp_path, monkeypatch):
    store = _store(
        tmp_path,
        monkeypatch,
        [
            _rec("2026-07-01T00:00:00Z-old111", suite="roster"),
            _rec("2026-08-05T00:00:00Z-new222", suite="roster"),
            _rec("2026-08-05T00:00:00Z-new333", suite="smoke"),
        ],
    )
    got = select_records(store, BundleSpec(suite="roster", since="2026-08-01"))
    assert [r["run_id"] for r in got] == ["2026-08-05T00:00:00Z-new222"]


def test_write_bundle_layout_manifest_and_redaction(tmp_path, monkeypatch):
    store = _store(tmp_path, monkeypatch, [_rec("2026-08-01T00:00:00Z-aaa111")])
    # a profile TOML to carry along
    prof = tmp_path / "chat.toml"
    prof.write_text('[profile.chat]\nflags = "-fa 1"\n')
    out = tmp_path / "out.hal0bench.tar.gz"

    from hal0.bench.bundle import write_bundle

    path, _manifest = write_bundle(
        store,
        BundleSpec(title="strix run", profile_paths=[str(prof)]),
        out,
    )
    assert path == out and out.exists()

    with tarfile.open(out, "r:gz") as tf:
        names = sorted(tf.getnames())
        assert names == ["manifest.json", "profiles/chat.toml", "records.jsonl"]
        m = json.loads(tf.extractfile("manifest.json").read())
        recs = tf.extractfile("records.jsonl").read().decode()

    assert m["bundle_schema"] == 1
    assert m["bundle_id"].startswith("sha256:")
    assert m["title"] == "strix run"
    assert m["records"] == [
        {
            "run_id": "2026-08-01T00:00:00Z-aaa111",
            "cell_key": "sha256:m1-2026-08-01T00:00:00Z-aaa111",
            "kind": "tg",
            "model_id": "m1",
        }
    ]
    assert m["profiles"] == [{"name": "chat.toml", "sha256": m["files"]["profiles/chat.toml"]}]
    # every member except manifest.json is hashed in files{}
    assert set(m["files"]) == {"records.jsonl", "profiles/chat.toml"}
    # hostname redacted in BOTH manifest host block and shipped records
    assert m["host"]["name"] == "redacted"
    assert m["host"]["gpu"] == "Strix Halo"
    assert "my-secret-hostname" not in recs


def test_bundle_id_is_deterministic_and_content_addressed(tmp_path, monkeypatch):
    from hal0.bench.bundle import write_bundle

    store = _store(tmp_path, monkeypatch, [_rec("2026-08-01T00:00:00Z-aaa111")])
    _, m1 = write_bundle(store, BundleSpec(), tmp_path / "a.tar.gz")
    _, m2 = write_bundle(store, BundleSpec(), tmp_path / "b.tar.gz")
    assert m1["bundle_id"] == m2["bundle_id"]

    store.append_record(_rec("2026-08-02T00:00:00Z-bbb222"))
    _, m3 = write_bundle(store, BundleSpec(), tmp_path / "c.tar.gz")
    assert m3["bundle_id"] != m1["bundle_id"]


def test_write_bundle_refuses_empty_selection(tmp_path, monkeypatch):
    import pytest

    from hal0.bench.bundle import write_bundle

    store = _store(tmp_path, monkeypatch, [_rec("2026-08-01T00:00:00Z-aaa111", outcome="failed")])
    with pytest.raises(ValueError, match="no ok records"):
        write_bundle(store, BundleSpec(), tmp_path / "x.tar.gz")


def test_write_bundle_includes_evals_for_selected_models(tmp_path, monkeypatch):
    from hal0.bench.bundle import write_bundle

    store = _store(tmp_path, monkeypatch, [_rec("2026-08-01T00:00:00Z-aaa111", model="m1")])
    # evals.jsonl lives in the same state root (evalrun._evals_path)
    (tmp_path / "evals.jsonl").write_text(
        json.dumps({"run_id": "e1", "model": "m1", "task": "cipher", "score": 1.0})
        + "\n"
        + json.dumps({"run_id": "e2", "model": "OTHER", "task": "cipher", "score": 0.0})
        + "\n"
    )
    _, m = write_bundle(store, BundleSpec(), tmp_path / "out.tar.gz")
    with tarfile.open(tmp_path / "out.tar.gz", "r:gz") as tf:
        evals = [json.loads(x) for x in tf.extractfile("evals.jsonl").read().decode().splitlines()]
    assert [e["model"] for e in evals] == ["m1"]
    assert "evals.jsonl" in m["files"]
