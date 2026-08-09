"""CLI wiring for `hal0 bench bundle` — list mode and build mode. Tests drive
main(argv) directly (same pattern as the other verb tests) with the store
pointed at tmp_path via HAL0_BENCH_STATE."""

from __future__ import annotations

import tarfile

from hal0.bench.cli import main
from hal0.bench.store import Store


def _seed(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv("HAL0_BENCH_STATE", str(tmp_path))
    store = Store()
    store.append_record(
        {
            "run_id": "2026-08-01T00:00:00Z-aaa111",
            "cell_key": "sha256:k1",
            "suite": "roster",
            "trigger": "manual",
            "identity": {
                "model": {"id": "qwen3-30b"},
                "lane": "rocm",
                "workload": {"kind": "tg", "depth": 2048},
            },
            "host": {"name": "box", "hal0_version": "1.0"},
            "outcome": "ok",
            "summary": {"decode_ts_med": 42.0},
            "schema": 2,
        }
    )


def test_bundle_list_prints_candidates(tmp_path, monkeypatch, capsys):
    _seed(tmp_path, monkeypatch)
    rc = main(["bundle", "--list"])
    out = capsys.readouterr().out
    assert rc == 0
    assert "2026-08-01T00:00:00Z-aaa111" in out
    assert "qwen3-30b" in out


def test_bundle_writes_archive(tmp_path, monkeypatch, capsys):
    _seed(tmp_path, monkeypatch)
    out_file = tmp_path / "b.hal0bench.tar.gz"
    rc = main(["bundle", "--runs", "2026-08-01T00:00:00Z-aaa111", "-o", str(out_file)])
    assert rc == 0
    assert out_file.exists()
    with tarfile.open(out_file, "r:gz") as tf:
        assert "manifest.json" in tf.getnames()
    assert "sha256:" in capsys.readouterr().out  # prints bundle_id


def test_bundle_empty_selection_fails_cleanly(tmp_path, monkeypatch, capsys):
    monkeypatch.setenv("HAL0_BENCH_STATE", str(tmp_path))
    rc = main(["bundle", "-o", str(tmp_path / "x.tar.gz")])
    assert rc == 1
    assert "no ok records" in capsys.readouterr().err
