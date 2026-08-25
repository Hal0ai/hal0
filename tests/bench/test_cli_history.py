"""`hal0 bench history` — empty-store output (#1904).

Before this fix an empty query result (empty store, or a non-matching
``--model``/``--cell`` filter on a populated store) printed nothing at all
for the non-``--json`` path, indistinguishable from a hung/broken command.
``cmd_results`` got this fix in #1807; ``cmd_history`` was missed.
"""

from __future__ import annotations

from hal0.bench.cli import main


def test_history_empty_prints_no_records(tmp_path, monkeypatch, capsys):
    monkeypatch.setenv("HAL0_BENCH_STATE", str(tmp_path))
    rc = main(["history"])
    out = capsys.readouterr().out
    assert rc == 0
    assert "no records" in out


def test_history_empty_json_stays_empty_list(tmp_path, monkeypatch, capsys):
    monkeypatch.setenv("HAL0_BENCH_STATE", str(tmp_path))
    rc = main(["history", "--json"])
    out = capsys.readouterr().out
    assert rc == 0
    assert out.strip() == "[]"
