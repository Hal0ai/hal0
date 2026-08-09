"""test_runner.py — the session runner: exact engine argv shapes, path
resolution, subprocess lifecycle, and an end-to-end plan→run→append→reindex
integration against a FAKED seam (no sudo, no podman, no GPU, no network).

The argv-shape tests exist because every fatal bug this module has shipped
lived in a command line nobody asserted on: ``--mode ab`` with no variant
(every chat cell failed), ``-p <depth>`` instead of ``-d <depth>`` (the depth
axis measured nothing), and the reps flag the harness silently overrode.

Phase 2 (bench-overhaul): Tier-A composes the ``sudo -n hal0-benchctl exec --
podman run …`` argv itself (``hal0.bench.harness``) instead of shelling out to
a privileged ``sweep`` verb — the argv-shape and integration tests below were
rewritten for that shape; the old ``_sweep_stem``/``_sweep_output_path``/
``_clear_stale_sweep``/``_locate_sweep_output``/``v1_runs_dir`` helpers (and
their tests) are gone with the result FILE they located — the engine's JSON
now comes back on stdout.
"""

from __future__ import annotations

import os
import stat
import time
from pathlib import Path

import pytest

from hal0.bench import harness, runner
from hal0.bench.devices import BenchDeviceSpec
from hal0.bench.planner import plan
from hal0.bench.schema import Host, Outcome
from hal0.bench.store import Store
from hal0.bench.suites import suite_from_dict


@pytest.fixture
def sandbox(tmp_path, monkeypatch):
    """A HAL0_HOME sandbox so model-store/state paths resolve under tmp."""
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


_CPU_DEVICES = BenchDeviceSpec(tier="cpu")


# --------------------------------------------------------------------------- #
# Tier-A argv shape
# --------------------------------------------------------------------------- #


class TestTierACmd:
    def test_depth_axis_is_dash_d_not_dash_p(self, tmp_path):
        cells = _cells(["pp", "tg"], Store(tmp_path), depths=[32768])
        cmd = runner._tier_a_cmd(cells[0], pp_prompt=512, tg_gen=256, devices=_CPU_DEVICES)

        assert cmd[:4] == ["sudo", "-n", harness.BENCHCTL, "exec"]
        # Everything past the seam's own "--" separator is the podman argv —
        # search there so sudo's OWN "-n" doesn't shadow llama-bench's "-n".
        podman_argv = cmd[cmd.index("--") + 1 :]
        assert podman_argv[:2] == ["podman", "run"]
        # -m carries the (legacy-root-stripped) relative model path under
        # whatever the live model-store root resolves to.
        m_val = podman_argv[podman_argv.index("-m") + 1]
        assert m_val.endswith("dir/Model.gguf")
        # The measurement sizes are fixed; the depth axis is the KV fill.
        assert podman_argv[podman_argv.index("-p") + 1] == "512"
        assert podman_argv[podman_argv.index("-n") + 1] == "256"
        assert podman_argv[podman_argv.index("-d") + 1] == "32768"
        assert podman_argv[podman_argv.index("-r") + 1] == "3"
        assert cmd[-2:] == ["-o", "json"]

    def test_seam_timeout_is_always_passed(self, tmp_path):
        cells = _cells(["pp"], Store(tmp_path))
        cmd = runner._tier_a_cmd(cells[0], pp_prompt=512, tg_gen=256, devices=_CPU_DEVICES)
        per_attempt = runner._tier_a_per_attempt_s(cells[0])

        assert cmd[cmd.index("--timeout-s") + 1] == str(per_attempt)

    def test_variant_flags_ride_the_argv_and_win_over_lane_defaults(self, tmp_path):
        cells = _cells(
            ["pp"],
            Store(tmp_path),
            configs=[{"label": "no-fa", "flags": {"-ub": 1024, "-fa": 0}}],
        )
        cmd = runner._tier_a_cmd(cells[0], pp_prompt=512, tg_gen=256, devices=_CPU_DEVICES)

        # -fa is a COMMON flag (default "1") — the variant's explicit -fa 0
        # must win via dedupe, and appear exactly once.
        assert cmd.count("-fa") == 1
        assert cmd[cmd.index("-fa") + 1] == "0"
        assert cmd.count("-ub") == 1
        assert cmd[cmd.index("-ub") + 1] == "1024"

    def test_device_flags_ride_the_composed_argv(self, tmp_path):
        cells = _cells(["pp"], Store(tmp_path))
        devices = BenchDeviceSpec(
            tier="amd", devices=("/dev/kfd", "/dev/dri/renderD128"), group_ids=(993, 44)
        )
        cmd = runner._tier_a_cmd(cells[0], pp_prompt=512, tg_gen=256, devices=devices)
        assert "--device=/dev/kfd" in cmd
        assert "--device=/dev/dri/renderD128" in cmd
        assert "--group-add=993" in cmd

    def test_exclusive_flag_is_not_part_of_the_composed_argv(self, tmp_path):
        # Phase 2: exclusivity is a Python-side context manager
        # (harness.ExclusiveSlots), never a podman/seam argv token.
        cells = _cells(["pp"], Store(tmp_path))
        cmd = runner._tier_a_cmd(cells[0], pp_prompt=512, tg_gen=256, devices=_CPU_DEVICES)
        assert "--exclusive" not in cmd


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

    def test_unknown_lane_is_rejected_at_plan_time(self, tmp_path):
        """Finding 5: a suite spelling the registry's own backend hint
        ("vulkan") instead of the lane token _BACKEND_TO_LANE maps it to
        ("vulkan_radv") must fail fast at plan time with a clear message
        naming the bad token and the valid lanes — not a bare KeyError out
        of the runner."""
        suite = suite_from_dict(
            {
                "suite": {"id": "t", "priority": 50},
                "selector": {"installed": True},
                "matrix": {
                    "lanes": ["vulkan"],
                    "depths": [2048],
                    "samplers": ["greedy"],
                    "reps": 3,
                },
                "cells": {"kinds": ["pp"]},
                "staleness": {"max_age_days": 30},
            }
        )
        with pytest.raises(ValueError, match="unknown lane 'vulkan'") as exc_info:
            plan(suite, _registry(), Store(tmp_path))
        assert "vulkan_radv" in str(exc_info.value)  # a valid lane is named

    def test_default_lane_token_still_resolves_normally(self, tmp_path):
        """Guard against the finding-5 fix over-rejecting: "default" (and any
        already-known lane) must plan without raising."""
        cells = _cells(["pp"], Store(tmp_path))
        assert cells and cells[0].lane in ("rocm", "vulkan_radv", "cpu")


# --------------------------------------------------------------------------- #
# Path resolution
# --------------------------------------------------------------------------- #


class TestPaths:
    def test_rel_gguf_strips_resolved_store_root(self, monkeypatch):
        from hal0.config import paths as hal0_paths

        monkeypatch.setattr(hal0_paths, "model_store_root", lambda: "/var/lib/hal0/models")
        assert runner._rel_gguf("/var/lib/hal0/models/d/M.gguf") == "d/M.gguf"

    def test_rel_gguf_still_strips_the_legacy_root(self):
        assert runner._rel_gguf("/mnt/ai-models/d/M.gguf") == "d/M.gguf"

    def test_model_root_is_the_live_resolved_store(self, monkeypatch):
        from hal0.config import paths as hal0_paths

        monkeypatch.setattr(hal0_paths, "model_store_root", lambda: "/var/lib/hal0/models/")
        assert runner._model_root() == "/var/lib/hal0/models"


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


class TestTierARunner:
    def test_ok_run_captures_stdout_and_stderr_separately(self, tmp_path):
        run = runner._tier_a_runner(per_attempt=10)
        rc, out, err = run(["bash", "-c", "echo out; echo err >&2"], 10)
        assert rc == 0
        assert out.strip() == "out"
        assert err.strip() == "err"

    # A "wedged past the shim's own timeout" case is deliberately NOT covered
    # here with a live subprocess: the outer backstop's margin (35s beyond
    # the shim's own --kill-after=30) makes a real trigger a 35s+ test. The
    # kill-the-whole-process-group mechanism itself is exercised by
    # TestRunSubprocess (same Popen/killpg pattern); this class only checks
    # the separate-stdout/stderr capture Tier-A specifically needs.


# --------------------------------------------------------------------------- #
# Integration: plan → run_session → append → reindex, faked seam
# --------------------------------------------------------------------------- #


def _install_fake_seam(sandbox: Path, monkeypatch, calls: Path) -> None:
    """A fake `sudo` on PATH + a fake `hal0-benchctl exec` that prints a
    llama-bench ``-o json`` shaped array straight to stdout — Phase 2's Tier-A
    result channel, no result FILE involved."""
    bin_dir = sandbox / "fakebin"
    bin_dir.mkdir()
    (bin_dir / "sudo").write_text('#!/bin/bash\nshift\nexec "$@"\n')

    rows = (
        '[{"n_prompt": 512, "n_gen": 0, "n_depth": 2048, '
        '"samples_ts": [100.0, 101.0, 102.0], "samples_ns": [1e9, 1e9, 1e9], '
        '"build_number": 1, "build_commit": "abc"}, '
        '{"n_prompt": 0, "n_gen": 256, "n_depth": 2048, '
        '"samples_ts": [50.0, 51.0, 52.0], "samples_ns": [1e9, 1e9, 1e9], '
        '"build_number": 1, "build_commit": "abc"}]'
    )
    benchctl = bin_dir / "benchctl"
    benchctl.write_text(f"#!/bin/bash\necho run >> {calls}\ncat <<'EOF'\n{rows}\nEOF\n")
    for f in (bin_dir / "sudo", benchctl):
        f.chmod(f.stat().st_mode | stat.S_IEXEC)

    monkeypatch.setenv("PATH", f"{bin_dir}{os.pathsep}{os.environ['PATH']}")
    monkeypatch.setattr(harness, "BENCHCTL", str(benchctl))
    monkeypatch.setattr(runner, "resolve_bench_devices", lambda: _CPU_DEVICES)
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

    def test_tier_a_exclusive_stops_and_restarts_slots_once_per_group(
        self, sandbox, monkeypatch, tmp_path
    ):
        calls = sandbox / "calls"
        _install_fake_seam(sandbox, monkeypatch, calls)
        systemctl_calls: list[list[str]] = []

        def fake_systemctl_runner(argv):
            systemctl_calls.append(argv)
            if argv[:2] == ["systemctl", "list-units"]:
                return 0, "hal0-slot@agent.service loaded active running", ""
            return 0, "", ""

        monkeypatch.setattr(
            harness, "_default_shell_runner", lambda argv: fake_systemctl_runner(argv)
        )

        store = Store(tmp_path / "state")
        host = Host(hal0_version="1.0.0", exclusive=True)
        suite = _suite(["pp", "tg"])
        cells = plan(suite, _registry(), store)

        result = runner.run_session(
            cells, store, host, suite_id="t", api="http://x", exclusive=True
        )

        assert result.cells_ok == 2
        stops = [c for c in systemctl_calls if len(c) > 3 and c[3] == "stop"]
        starts = [c for c in systemctl_calls if len(c) > 3 and c[3] == "start"]
        # ONE stop/restart pair for the whole memoised group, not per-cell.
        assert [c[4] for c in stops] == ["agent"]
        assert [c[4] for c in starts] == ["agent"]

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


class TestBogusLaneDefensiveLayer:
    """Finding 5's defensive layer: planner.plan() rejects an unknown lane at
    plan time (see TestTierBCCmd above), but a cell that reaches the runner
    some other way (a hand-built worklist, a future plan-bypassing caller)
    must still degrade gracefully rather than raising a bare KeyError."""

    def test_describe_worklist_notes_an_unknown_lane_without_raising(self, tmp_path):
        cells = _cells(["pp"], Store(tmp_path))
        cells[0].lane = "bogus"

        lines = runner.describe_worklist(cells, exclusive=True, api="http://x")

        assert any("unknown lane" in line and "bogus" in line for line in lines)

    def test_bogus_lane_cell_records_failed_not_a_crash(self, tmp_path, monkeypatch):
        monkeypatch.setattr(runner, "resolve_bench_devices", lambda: _CPU_DEVICES)
        monkeypatch.setattr(runner, "_traffic_in_flight", lambda *a, **k: False)

        store = Store(tmp_path / "state")
        cells = _cells(["pp"], store)
        cells[0].lane = "bogus"

        result = runner.run_session(
            cells, store, Host(hal0_version="1.0.0", exclusive=True), suite_id="t", api="http://x"
        )

        assert result.aborted is None
        assert result.cells_failed == 1
        assert result.cells_ok == 0
        [rec] = list(store.iter_records())
        assert rec["outcome"] == Outcome.FAILED.value
        assert "bogus" in rec["note"]
