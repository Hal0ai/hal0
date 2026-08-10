"""`hal0 bench results` — empty-store output (#1796).

Before this fix an empty ``bench.db`` printed nothing at all for the
non-``--json`` path, indistinguishable from a hung/broken command.
"""

from __future__ import annotations

from hal0.bench.cli import main


def test_results_empty_prints_no_records(tmp_path, monkeypatch, capsys):
    monkeypatch.setenv("HAL0_BENCH_STATE", str(tmp_path))
    rc = main(["results"])
    out = capsys.readouterr().out
    assert rc == 0
    assert "no records" in out


def test_results_empty_json_stays_empty_list(tmp_path, monkeypatch, capsys):
    monkeypatch.setenv("HAL0_BENCH_STATE", str(tmp_path))
    rc = main(["results", "--json"])
    out = capsys.readouterr().out
    assert rc == 0
    assert out.strip() == "[]"
