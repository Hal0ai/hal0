"""test_runner.py — the session runner: exact engine argv shapes, path
resolution, subprocess lifecycle, and an end-to-end plan→run→append→reindex
integration against a FAKED seam (no sudo, no GPU, no network).

The argv-shape tests exist because every fatal bug this module has shipped
lived in a command line nobody asserted on: ``--mode ab`` with no variant
(every chat cell failed), ``-p <depth>`` instead of ``-d <depth>`` (the depth
axis measured nothing), and the reps flag the harness silently overrode.
"""

from __future__ import annotations

import json
import os
import stat
import time
from pathlib import Path

import pytest

from hal0.bench import runner
from hal0.bench.planner import plan
from hal0.bench.schema import Host, Outcome
from hal0.bench.store import Store
from hal0.bench.suites import suite_from_dict


@pytest.fixture
def sandbox(tmp_path, monkeypatch):
    """A HAL0_HOME sandbox so v1_runs_dir()/model paths resolve under tmp."""
    monkeypatch.setenv("HAL0_HOME", str(tmp_path))
    monkeypatch.delenv("HAL0_BENCH_STATE", raising=False)
    return tmp_path


def _suite(kinds, depths=(2048,), reps=3, configs=None):
    matrix = {"lanes": ["default"], "depths": list(depths), "samplers": ["greedy"], "reps": reps}
    if configs is not None:
        matrix["configs"] = configs
    return suite_from_dict(
        {
            "suite": {"id": "t", "priority": 50},
            "selector": {"installed": True},
            "matrix": matrix,
            "cells": {"kinds": list(kinds)},
            "staleness": {"max_age_days": 30},
        }
    )


def _registry(gguf="/mnt/ai-models/dir/Model.gguf"):
    return [
        {
            "id": "m1",
            "installed": True,
            "caps": ["chat"],
            "sha256": "abc",
            "default_lane": "rocm",
            "gguf": gguf,
            "n_gen": 256,
        }
    ]


def _cells(kinds, store, **kw):
    return plan(_suite(kinds, **kw), _registry(), store)


# --------------------------------------------------------------------------- #
# Tier-A argv shape
# --------------------------------------------------------------------------- #


class TestTierACmd:
    def test_depth_axis_is_dash_d_not_dash_p(self, tmp_path):
        cells = _cells(["pp", "tg"], Store(tmp_path), depths=[32768])
        cmd = runner._tier_a_cmd(cells[0], exclusive=True, pp_prompt=512, tg_gen=256)

        assert cmd[:4] == ["sudo", "-n", runner.BENCHCTL, "sweep"]
        assert cmd[4] == "dir/Model.gguf"  # legacy root stripped
        assert cmd[5] == "rocm"
        assert "--exclusive" in cmd
        # The measurement sizes are fixed; the depth axis is the KV fill.
        # (Search past the leading `sudo -n` — "-n" appears there too.)
        args = cmd[4:]
        assert args[args.index("-p") + 1] == "512"
        assert args[args.index("-n") + 1] == "256"
        assert args[args.index("-d") + 1] == "32768"
        assert args[args.index("-r") + 1] == "3"

    def test_seam_timeout_is_always_passed(self, tmp_path):
        cells = _cells(["pp"], Store(tmp_path))
        cmd = runner._tier_a_cmd(cells[0], exclusive=False, pp_prompt=512, tg_gen=256)
        per_attempt, outer = runner._tier_a_timeouts(cells[0])

        assert cmd[cmd.index("--timeout-s") + 1] == str(per_attempt)
        # The Python watchdog must outlast the harness's 6 crash-retries.
        assert outer > 6 * per_attempt

    def test_variant_flags_ride_the_sweep_sorted(self, tmp_path):
        cells = _cells(
            ["pp"],
            Store(tmp_path),
            configs=[{"label": "no-fa", "flags": {"-ub": 1024, "-fa": 0}}],
        )
        cmd = runner._tier_a_cmd(cells[0], exclusive=True, pp_prompt=512, tg_gen=256)

        tail = cmd[cmd.index("-r") + 2 :]
        assert tail == ["-fa", "0", "-ub", "1024"]  # sorted, stable


# --------------------------------------------------------------------------- #
# Tier-B/C argv shape
# --------------------------------------------------------------------------- #


class TestTierBCCmd:
    def test_chat_cell_passes_its_config_variant(self, tmp_path):
        cells = _cells(
            ["chat"],
            Store(tmp_path),
            configs=[{"label": "fa-off", "flags": {"-fa": 0}}],
        )
        cmd = runner._tier_bc_cmd(cells[0], slot="s1", api="http://x", out="/o.json")

        assert cmd[cmd.index("--mode") + 1] == "ab"
        # The single --variant carries label + flags so the server actually
        # runs what the record is labelled with (a variant-less --mode ab is
        # rejected by server_ab and failed every chat cell).
        assert cmd[cmd.index("--variant") + 1] == "fa-off:-fa 0"

    def test_default_chat_cell_passes_a_baseline_variant(self, tmp_path):
        cells = _cells(["chat"], Store(tmp_path))
        cmd = runner._tier_bc_cmd(cells[0], slot="s1", api="http://x")
        assert cmd[cmd.index("--variant") + 1] == "default:"

    def test_embed_has_no_variant(self, tmp_path):
        cells = _cells(["embed"], Store(tmp_path))
        cmd = runner._tier_bc_cmd(cells[0], slot="s1", api="http://x")
        assert cmd[cmd.index("--mode") + 1] == "embed"
        assert "--variant" not in cmd

    def test_unknown_kind_is_rejected_at_plan_time(self, tmp_path):
        with pytest.raises(ValueError, match="unknown cell kind"):
            plan(_suite(["batch"]), _registry(), Store(tmp_path))


# --------------------------------------------------------------------------- #
# Path resolution + stems
# --------------------------------------------------------------------------- #


class TestPaths:
    def test_rel_gguf_strips_resolved_store_root(self, monkeypatch):
        from hal0.config import paths as hal0_paths

        monkeypatch.setattr(hal0_paths, "model_store_root", lambda: "/var/lib/hal0/models")
        assert runner._rel_gguf("/var/lib/hal0/models/d/M.gguf") == "d/M.gguf"

    def test_rel_gguf_still_strips_the_legacy_root(self):
        assert runner._rel_gguf("/mnt/ai-models/d/M.gguf") == "d/M.gguf"

    def test_sweep_stem_matches_the_harness_sanitisation(self):
        # run_benchmarks.sh: tr -c 'A-Za-z0-9._-' '_'
        assert runner._sweep_stem("/mnt/ai-models/d/We ird(1).gguf") == "We_ird_1_"

    def test_sweep_output_path_is_exact(self, sandbox):
        p = runner._sweep_output_path("/mnt/ai-models/d/M.gguf", "rocm")
        assert p.name == "M__rocm__sweep.json"
        assert p.parent == runner.v1_runs_dir()


# --------------------------------------------------------------------------- #
# Subprocess lifecycle
# --------------------------------------------------------------------------- #


class TestRunSubprocess:
    def test_ok_run_tees_output(self, tmp_path):
        log = tmp_path / "x.log"
        rc, tail = runner._run_subprocess(["echo", "hello"], 10, log)
        assert rc == 0
        assert "hello" in tail
        assert "hello" in log.read_text()

    def test_timeout_kills_the_whole_process_group(self, tmp_path):
        # A shell that spawns a child: the old kill left the child running.
        marker = tmp_path / "still-alive"
        cmd = [
            "bash",
            "-c",
            f"(sleep 3 && touch {marker}) & sleep 30",
        ]
        t0 = time.monotonic()
        rc, tail = runner._run_subprocess(cmd, 0.5, tmp_path / "x.log")
        assert rc == -9
        assert tail == "watchdog-timeout"
        assert time.monotonic() - t0 < 15
        time.sleep(3.5)
        assert not marker.exists(), "background child survived the group kill"


# --------------------------------------------------------------------------- #
# Integration: plan → run_session → append → reindex, faked seam
# --------------------------------------------------------------------------- #


def _install_fake_seam(sandbox: Path, monkeypatch, calls: Path) -> None:
    """A fake `sudo` on PATH + a fake benchctl that writes a llama-bench-shaped
    sweep JSON exactly where the real seam would."""
    bin_dir = sandbox / "fakebin"
    bin_dir.mkdir()
    (bin_dir / "sudo").write_text('#!/bin/bash\nshift\nexec "$@"\n')

    runs = runner.v1_runs_dir()
    rows = [
        {
            "n_prompt": 512,
            "n_gen": 0,
            "n_depth": 2048,
            "samples_ts": [100.0, 101.0, 102.0],
            "samples_ns": [1e9, 1e9, 1e9],
            "build_number": 1,
            "build_commit": "abc",
        },
        {
            "n_prompt": 0,
            "n_gen": 256,
            "n_depth": 2048,
            "samples_ts": [50.0, 51.0, 52.0],
            "samples_ns": [1e9, 1e9, 1e9],
            "build_number": 1,
            "build_commit": "abc",
        },
    ]
    benchctl = bin_dir / "benchctl"
    benchctl.write_text(
        "#!/bin/bash\n"
        f"echo run >> {calls}\n"
        f"mkdir -p {runs}\n"
        'rel="$2"\n'
        'lane="$3"\n'
        "stem=\"$(basename \"$rel\" .gguf | tr -c 'A-Za-z0-9._-' '_')\"\n"
        'stem="${stem%_}"\n'  # tr appends _ for the trailing newline
        f"cat > \"{runs}/${{stem}}__${{lane}}__sweep.json\" <<'EOF'\n"
        + json.dumps(rows)
        + "\nEOF\n"
        f"cat > \"{runs}/${{stem}}__${{lane}}__sweep.meta.json\" <<'EOF'\n"
        + json.dumps({"image": "img:1", "backend": "rocm", "reps": 3})
        + "\nEOF\n"
    )
    for f in (bin_dir / "sudo", benchctl):
        f.chmod(f.stat().st_mode | stat.S_IEXEC)

    monkeypatch.setenv("PATH", f"{bin_dir}{os.pathsep}{os.environ['PATH']}")
    monkeypatch.setattr(runner, "BENCHCTL", str(benchctl))
    monkeypatch.setattr(runner, "_traffic_in_flight", lambda *a, **k: False)


class TestSessionIntegration:
    def test_tier_a_plan_run_reindex_converges(self, sandbox, monkeypatch, tmp_path):
        calls = sandbox / "calls"
        _install_fake_seam(sandbox, monkeypatch, calls)
        store = Store(tmp_path / "state")
        host = Host(hal0_version="1.0.0", exclusive=True)

        suite = _suite(["pp", "tg"])
        cells = plan(suite, _registry(), store)
        assert len(cells) == 2

        result = runner.run_session(
            cells, store, host, suite_id="t", api="http://x", trigger="scheduled"
        )

        assert result.aborted is None
        assert result.cells_ok == 2
        # pp + tg siblings share ONE memoised sweep — one seam call.
        assert calls.read_text().count("run") == 1

        records = list(store.iter_records())
        assert [r["outcome"] for r in records] == ["ok", "ok"]
        assert {r["identity"]["workload"]["kind"] for r in records} == {"pp", "tg"}
        assert all(r["trigger"] == "scheduled" for r in records)
        by_kind = {r["identity"]["workload"]["kind"]: r for r in records}
        assert by_kind["pp"]["summary"]["prefill_ts_med"] == 101.0
        assert by_kind["tg"]["summary"]["decode_ts_med"] == 51.0

        # Convergence: replanning after the run finds nothing stale.
        assert plan(suite, _registry(), store) == []
        # And the derived index serves the fresh rows without a manual reindex,
        # with the config-variant label as a first-class column.
        rows = store.results()
        assert len(rows) == 2
        assert {r["config"] for r in rows} == {"default"}

    def test_tier_bc_run_appends_a_failed_record_not_a_crash(self, sandbox, monkeypatch, tmp_path):
        """A server_ab that exits non-zero yields outcome=failed and the
        session continues — never an exception, never a dropped cell."""
        fake_ab = sandbox / "fake_server_ab"
        fake_ab.write_text("#!/bin/bash\necho boom >&2\nexit 2\n")
        fake_ab.chmod(fake_ab.stat().st_mode | stat.S_IEXEC)
        monkeypatch.setattr(runner, "SERVER_AB", str(fake_ab))
        monkeypatch.setattr(runner, "_traffic_in_flight", lambda *a, **k: False)
        monkeypatch.setattr(runner, "_slot_for_model", lambda api, mid: "slot1")

        store = Store(tmp_path / "state")
        cells = _cells(["chat"], store)
        result = runner.run_session(
            cells, store, Host(hal0_version="1.0.0", exclusive=True), suite_id="t", api="http://x"
        )

        assert result.cells_failed == 1
        [rec] = list(store.iter_records())
        assert rec["outcome"] == Outcome.FAILED.value
        assert "boom" in rec["note"]
