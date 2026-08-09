"""test_harness_impl.py — hal0.bench.harness: argv composition, the retry/
normalisation loop, and ExclusiveSlots. Faked seam throughout (no sudo, no
podman, no systemd, no GPU) — these are the exact rules ported from the shell
harness (dedupe_flag_pairs, the rocmfpx crash-retry loop, --exclusive), so a
regression here means a benchmark cell silently measures the wrong thing or a
GPU slot is left stopped.
"""

from __future__ import annotations

import json

import pytest

from hal0.bench import harness
from hal0.bench.devices import BenchDeviceSpec


def _spec(**overrides) -> harness.LaneSpec:
    base = dict(
        lane="rocm",
        image="ghcr.io/hal0ai/hal0-rocmfpx:test",
        bench_bin="/opt/rocmfpx/bin/llama-bench",
        ubatch=2048,
        env=("GGML_HIP_ENABLE_UNIFIED_MEMORY=1",),
        dev_args=(("-ngl", "99"), ("-dev", "ROCm0")),
    )
    base.update(overrides)
    return harness.LaneSpec(**base)


def _devices(**overrides) -> BenchDeviceSpec:
    base = dict(
        tier="amd",
        devices=("/dev/kfd", "/dev/dri/renderD128"),
        group_ids=(993, 44),
        source="discovery",
        gpu_label="Radeon 8060S",
    )
    base.update(overrides)
    return BenchDeviceSpec(**base)


# --------------------------------------------------------------------------- #
# lane_specs / default_lanes
# --------------------------------------------------------------------------- #


class TestLaneSpecs:
    def test_all_three_lanes_present(self):
        specs = harness.lane_specs()
        assert set(specs) == {"rocm", "vulkan_radv", "cpu"}

    def test_rocm_and_vulkan_share_the_rocmfpx_image_and_binary(self):
        specs = harness.lane_specs()
        assert specs["rocm"].image == specs["vulkan_radv"].image
        assert (
            specs["rocm"].bench_bin
            == specs["vulkan_radv"].bench_bin
            == "/opt/rocmfpx/bin/llama-bench"
        )
        assert specs["rocm"].ubatch == 2048
        assert specs["vulkan_radv"].ubatch == 512

    def test_cpu_lane_uses_the_lean_toolbox_and_no_dev_pin(self):
        cpu = harness.lane_specs()["cpu"]
        assert cpu.bench_bin == "/usr/local/bin/llama-bench"
        assert cpu.env == ()
        assert ("-dev", "cpu") not in cpu.dev_args
        assert all(flag != "-dev" for flag, _ in cpu.dev_args)

    def test_default_lanes_cpu_tier(self):
        assert harness.default_lanes("cpu") == ["cpu"]

    def test_default_lanes_gpu_tier(self):
        assert harness.default_lanes("amd") == ["rocm", "vulkan_radv"]
        assert harness.default_lanes("nvidia") == ["rocm", "vulkan_radv"]


# --------------------------------------------------------------------------- #
# dedupe_flags — port of dedupe_flag_pairs
# --------------------------------------------------------------------------- #


class TestDedupeFlags:
    def test_later_source_wins_first_seen_order(self):
        pairs = [("-fa", "1"), ("-mmp", "0"), ("-ngl", "99"), ("-fa", "0")]
        assert harness.dedupe_flags(pairs) == [("-fa", "0"), ("-mmp", "0"), ("-ngl", "99")]

    def test_no_duplicates_passes_through_unchanged(self):
        pairs = [("-p", "512"), ("-n", "256"), ("-d", "2048")]
        assert harness.dedupe_flags(pairs) == pairs

    def test_empty_input(self):
        assert harness.dedupe_flags([]) == []


# --------------------------------------------------------------------------- #
# compose_podman_argv
# --------------------------------------------------------------------------- #


class TestComposePodmanArgv:
    def test_shape_and_order(self):
        spec = _spec()
        devices = _devices()
        argv = harness.compose_podman_argv(
            spec,
            devices,
            "/store/m/M.gguf",
            "/store",
            [("-p", "512"), ("-n", "0"), ("-d", "2048"), ("-r", "5")],
        )
        assert argv[:3] == ["podman", "run", "--rm"]
        assert "--device=/dev/kfd" in argv
        assert "--device=/dev/dri/renderD128" in argv
        assert "--group-add=993" in argv
        assert "--group-add=44" in argv
        i = argv.index("--security-opt")
        assert argv[i : i + 4] == [
            "--security-opt",
            "apparmor=unconfined",
            "--security-opt",
            "seccomp=unconfined",
        ]
        assert "--volume=/store:/store:ro,z" in argv
        assert argv[argv.index("-e") + 1] == "GGML_HIP_ENABLE_UNIFIED_MEMORY=1"
        assert argv[argv.index("--entrypoint") + 1] == "/opt/rocmfpx/bin/llama-bench"
        assert argv[argv.index("--entrypoint") + 2] == spec.image
        assert argv[argv.index("-m") + 1] == "/store/m/M.gguf"
        assert argv[-2:] == ["-o", "json"]

    def test_common_flags_are_present_once(self):
        argv = harness.compose_podman_argv(_spec(), _devices(), "/x/m.gguf", "/x", [("-r", "5")])
        assert argv.count("-fa") == 1
        assert argv[argv.index("-fa") + 1] == "1"
        assert argv.count("-mmp") == 1

    def test_caller_flags_win_over_lane_dev_args(self):
        # A config variant that overrides -ngl must beat the lane default.
        argv = harness.compose_podman_argv(
            _spec(), _devices(), "/x/m.gguf", "/x", [("-ngl", "50"), ("-r", "5")]
        )
        assert argv[argv.index("-ngl") + 1] == "50"

    def test_cpu_tier_has_no_device_flags(self):
        argv = harness.compose_podman_argv(
            harness.lane_specs()["cpu"],
            BenchDeviceSpec(tier="cpu"),
            "/x/m.gguf",
            "/x",
            [("-r", "5")],
        )
        assert not any(a.startswith("--device=") or a.startswith("--group-add=") for a in argv)

    def test_ub_placeholder_never_appears(self):
        argv = harness.compose_podman_argv(
            _spec(), _devices(), "/x/m.gguf", "/x", [("-ub", "2048")]
        )
        assert "%UB%" not in argv


# --------------------------------------------------------------------------- #
# benchctl_exec_argv
# --------------------------------------------------------------------------- #


class TestBenchctlExecArgv:
    def test_with_timeout(self):
        argv = harness.benchctl_exec_argv(["podman", "run"], 300)
        assert argv == [
            "sudo",
            "-n",
            harness.BENCHCTL,
            "exec",
            "--timeout-s",
            "300",
            "--",
            "podman",
            "run",
        ]

    def test_without_timeout(self):
        argv = harness.benchctl_exec_argv(["podman", "run"], None)
        assert argv == ["sudo", "-n", harness.BENCHCTL, "exec", "--", "podman", "run"]

    def test_zero_timeout_treated_as_no_timeout(self):
        argv = harness.benchctl_exec_argv(["podman", "run"], 0)
        assert "--timeout-s" not in argv


# --------------------------------------------------------------------------- #
# run_cell — retry / normalisation
# --------------------------------------------------------------------------- #


def _rows_json() -> str:
    return json.dumps([{"n_prompt": 512, "n_gen": 0, "samples_ts": [100.0]}])


class TestRunCellRetry:
    def test_first_attempt_success_no_retry(self, tmp_path):
        calls = []

        def runner(argv, timeout_s):
            calls.append(argv)
            return 0, _rows_json(), ""

        result = harness.run_cell(
            _spec(),
            _devices(),
            model_rel="m/M.gguf",
            model_root="/store",
            flags=[("-r", "5")],
            timeout_s=60,
            log_path=tmp_path / "log.txt",
            runner=runner,
        )
        assert len(calls) == 1
        assert result.rc == 0
        assert result.rows == json.loads(_rows_json())
        assert result.meta["image"] == _spec().image
        assert result.meta["reps"] == 5

    def test_signal_exit_retries_up_to_max_attempts(self, tmp_path):
        calls = []

        def runner(argv, timeout_s):
            calls.append(argv)
            return 139, "", "segfault"

        result = harness.run_cell(
            _spec(),
            _devices(),
            model_rel="m/M.gguf",
            model_root="/store",
            flags=[],
            timeout_s=60,
            log_path=tmp_path / "log.txt",
            runner=runner,
        )
        assert len(calls) == harness.MAX_ATTEMPTS
        assert result.rc == 139
        assert result.rows == []

    def test_signal_exit_then_success_stops_retrying(self, tmp_path):
        outcomes = iter([(139, "", "segfault"), (139, "", "segfault"), (0, _rows_json(), "")])

        def runner(argv, timeout_s):
            return next(outcomes)

        result = harness.run_cell(
            _spec(),
            _devices(),
            model_rel="m/M.gguf",
            model_root="/store",
            flags=[],
            timeout_s=60,
            log_path=tmp_path / "log.txt",
            runner=runner,
        )
        assert result.rc == 0
        assert result.rows

    def test_zero_rc_empty_stdout_normalises_and_retries(self, tmp_path):
        calls = []

        def runner(argv, timeout_s):
            calls.append(1)
            return 0, "", ""  # podman --rm race: rc 0, no output

        result = harness.run_cell(
            _spec(),
            _devices(),
            model_rel="m/M.gguf",
            model_root="/store",
            flags=[],
            timeout_s=60,
            log_path=tmp_path / "log.txt",
            runner=runner,
        )
        assert len(calls) == harness.MAX_ATTEMPTS
        assert result.rc == 139
        assert result.meta == {}  # rc != 0 -> no provenance

    def test_real_failure_never_retries(self, tmp_path):
        calls = []

        def runner(argv, timeout_s):
            calls.append(1)
            return 1, "", "model not found"

        result = harness.run_cell(
            _spec(),
            _devices(),
            model_rel="m/M.gguf",
            model_root="/store",
            flags=[],
            timeout_s=60,
            log_path=tmp_path / "log.txt",
            runner=runner,
        )
        assert len(calls) == 1
        assert result.rc == 1
        assert "model not found" in result.tail

    def test_timeout_124_never_retries(self, tmp_path):
        calls = []

        def runner(argv, timeout_s):
            calls.append(1)
            return 124, "", "timed out"

        result = harness.run_cell(
            _spec(),
            _devices(),
            model_rel="m/M.gguf",
            model_root="/store",
            flags=[],
            timeout_s=60,
            log_path=tmp_path / "log.txt",
            runner=runner,
        )
        assert len(calls) == 1
        assert result.rc == 124

    def test_137_past_the_cap_is_a_timeout_not_a_crash_retry(self, tmp_path, monkeypatch):
        calls = []
        # Simulate elapsed time exceeding timeout_s so 137 reads as the
        # shim's --kill-after=30 escalation, not a plain crash.
        times = iter([0.0, 100.0])
        monkeypatch.setattr(harness.time, "monotonic", lambda: next(times))

        def runner(argv, timeout_s):
            calls.append(1)
            return 137, "", "killed"

        result = harness.run_cell(
            _spec(),
            _devices(),
            model_rel="m/M.gguf",
            model_root="/store",
            flags=[],
            timeout_s=60,
            log_path=tmp_path / "log.txt",
            runner=runner,
        )
        assert len(calls) == 1
        assert result.rc == 137

    def test_137_within_the_cap_is_a_crash_retry(self, tmp_path, monkeypatch):
        calls = []
        times = iter([0.0, 1.0] * harness.MAX_ATTEMPTS)
        monkeypatch.setattr(harness.time, "monotonic", lambda: next(times))
        monkeypatch.setattr(harness.time, "sleep", lambda *_: None)

        def runner(argv, timeout_s):
            calls.append(1)
            return 137, "", "killed fast"

        result = harness.run_cell(
            _spec(),
            _devices(),
            model_rel="m/M.gguf",
            model_root="/store",
            flags=[],
            timeout_s=60,
            log_path=tmp_path / "log.txt",
            runner=runner,
        )
        assert len(calls) == harness.MAX_ATTEMPTS
        assert result.rc == 137

    def test_writes_the_attempt_log(self, tmp_path):
        log_path = tmp_path / "sub" / "log.txt"

        def runner(argv, timeout_s):
            return 0, _rows_json(), ""

        harness.run_cell(
            _spec(),
            _devices(),
            model_rel="m/M.gguf",
            model_root="/store",
            flags=[],
            timeout_s=60,
            log_path=log_path,
            runner=runner,
        )
        assert log_path.exists()
        assert "exit=0" in log_path.read_text()

    def test_malformed_json_yields_empty_rows_not_a_crash(self, tmp_path):
        def runner(argv, timeout_s):
            return 0, "not json", ""

        result = harness.run_cell(
            _spec(),
            _devices(),
            model_rel="m/M.gguf",
            model_root="/store",
            flags=[],
            timeout_s=60,
            log_path=tmp_path / "log.txt",
            runner=runner,
        )
        assert result.rc == 0
        assert result.rows == []

    def test_meta_provenance_keys_match_legacy_exactly(self, tmp_path):
        def runner(argv, timeout_s):
            return 0, _rows_json(), ""

        result = harness.run_cell(
            _spec(),
            _devices(tier="amd", gpu_label="Radeon 8060S"),
            model_rel="m/M.gguf",
            model_root="/store",
            flags=[("-r", "5")],
            timeout_s=60,
            log_path=tmp_path / "log.txt",
            runner=runner,
        )
        assert set(result.meta) == {
            "backend",
            "image",
            "context",
            "tag",
            "extra",
            "reps",
            "ubatch",
            "model_rel",
            "model_path",
            "host",
            "tier",
            "gpu",
            "timestamp",
        }
        assert result.meta["tier"] == "amd"
        assert result.meta["gpu"] == "Radeon 8060S"
        assert result.meta["model_rel"] == "m/M.gguf"
        assert result.meta["model_path"] == "/store/m/M.gguf"


# --------------------------------------------------------------------------- #
# ExclusiveSlots
# --------------------------------------------------------------------------- #


class TestExclusiveSlots:
    def _list_units_output(self, ids: list[str]) -> str:
        lines = [f"hal0-slot@{i}.service loaded active running" for i in ids]
        return "\n".join(lines)

    def test_stops_and_restarts_non_npu_slots(self):
        calls: list[list[str]] = []

        def runner(argv):
            calls.append(argv)
            if argv[:2] == ["systemctl", "list-units"]:
                return 0, self._list_units_output(["agent", "npu"]), ""
            return 0, "", ""

        slots = harness.ExclusiveSlots(runner=runner)
        with slots:
            pass

        stop_calls = [c for c in calls if len(c) > 3 and c[3] == "stop"]
        start_calls = [c for c in calls if len(c) > 3 and c[3] == "start"]
        assert [c[4] for c in stop_calls] == ["agent"]  # npu excluded
        assert [c[4] for c in start_calls] == ["agent"]

    def test_no_active_slots_is_a_noop(self):
        calls = []

        def runner(argv):
            calls.append(argv)
            if argv[:2] == ["systemctl", "list-units"]:
                return 0, "", ""
            return 0, "", ""

        with harness.ExclusiveSlots(runner=runner):
            pass

        assert all(c[:2] != ["sudo", "-n"] for c in calls)

    def test_failed_stop_raises_and_restores_what_was_already_stopped(self):
        calls = []

        def runner(argv):
            calls.append(argv)
            if argv[:2] == ["systemctl", "list-units"]:
                return 0, self._list_units_output(["a", "b"]), ""
            if argv[3:5] == ["stop", "a"]:
                return 0, "", ""
            if argv[3:5] == ["stop", "b"]:
                return 1, "", "permission denied"
            return 0, "", ""

        with pytest.raises(RuntimeError), harness.ExclusiveSlots(runner=runner):
            pass

        starts = [c[4] for c in calls if len(c) > 3 and c[3] == "start"]
        assert starts == ["a"]  # only the slot that WAS stopped gets restored

    def test_failed_restart_warns_but_does_not_raise(self, capsys):
        def runner(argv):
            if argv[:2] == ["systemctl", "list-units"]:
                return 0, self._list_units_output(["agent"]), ""
            if argv[3:5] == ["stop", "agent"]:
                return 0, "", ""
            if argv[3:5] == ["start", "agent"]:
                return 1, "", "unit not found"
            return 0, "", ""

        with harness.ExclusiveSlots(runner=runner):
            pass  # must not raise

        assert "WARN" in capsys.readouterr().err

    def test_list_units_failure_treated_as_nothing_active(self):
        def runner(argv):
            if argv[:2] == ["systemctl", "list-units"]:
                return 1, "", "no systemd"
            return 0, "", ""

        with harness.ExclusiveSlots(runner=runner):
            pass  # must not raise
