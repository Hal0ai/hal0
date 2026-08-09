"""Unit tests for ``hal0.bench.harness`` (Phase 2 of the bench overhaul).

Phase 1 (``129f8879``) built the matrix/composition/retry logic as shell
(``installer/bench/config.sh`` + ``run_benchmarks.sh``), driven through the
``hal0-benchctl sweep`` seam. Phase 2 moves that logic into Python
(``src/hal0/bench/harness.py``, not yet written — every test below is
expected to fail at collection with ``ImportError`` until it lands) and
shrinks the privileged shim to a dumb validate-and-exec (see
``tests/bench/test_benchctl_shim.py``).

This file is the direct successor to the matrix/argv/metadata assertions in
the retired ``tests/bench/test_harness_matrix.py`` (``TestBackendMatrix``,
``TestBackendOrder``, ``TestGpuLabel``, ``TestCpuSweepArgv``,
``TestCpuCellEndToEnd``), translated from shell-sourcing + subprocess
assertions into direct unit tests against the new pure functions.

A few of ``run_cell``'s and ``ExclusiveSlots``' exact call shapes are
underspecified in the interface contract (see the task brief) — where this
file has to guess, the guess is called out in a comment next to the
assertion it backs, so the implementer can reconcile intent instead of
silently overfitting to a guess.
"""

from __future__ import annotations

import json

import pytest
from hal0.bench.harness import (
    BENCHCTL,
    MAX_ATTEMPTS,
    SYSTEMCTL_SEAM,
    CellResult,
    ExclusiveSlots,
    benchctl_exec_argv,
    compose_podman_argv,
    dedupe_flags,
    default_lanes,
    lane_specs,
    run_cell,
)

from hal0.bench.devices import (
    TIER_AMD,
    TIER_CPU,
    TIER_NVIDIA,
    BenchDeviceSpec,
)
from hal0.config.schema import DEFAULT_ROCMFPX_IMAGE, FALLBACK_VULKAN_IMAGE

CPU_BENCH_BIN = "/usr/local/bin/llama-bench"
ROCMFPX_BENCH_BIN = "/opt/rocmfpx/bin/llama-bench"
COMMON_ARGS = [("-fa", "1"), ("-mmp", "0")]

#: One llama-bench ``-o json`` row, the same shape
#: ``tests/bench/fixtures/llama_bench_0.8b_rocm.json`` carries.
_FAKE_BENCH_ROW = {
    "build_commit": "deadbee",
    "build_number": 7,
    "model_filename": "tiny.gguf",
    "model_type": "llama 1B Q4_K - Medium",
    "n_prompt": 512,
    "n_gen": 0,
    "n_batch": 2048,
    "n_ubatch": 512,
    "n_gpu_layers": 0,
    "avg_ts": 12.5,
    "stddev_ts": 0.25,
    "backends": "CPU",
}
_FAKE_BENCH_JSON = json.dumps([_FAKE_BENCH_ROW])


# ── lane_specs ────────────────────────────────────────────────────────────────


class TestLaneSpecs:
    """``lane_specs()`` must carry the same 3-lane matrix Phase 1's
    ``BACKENDS`` associative array did (``installer/bench/config.sh``)."""

    def test_all_three_lanes_are_present(self) -> None:
        specs = lane_specs()

        assert set(specs) == {"rocm", "vulkan_radv", "cpu"}

    def test_cpu_lane_uses_the_fallback_vulkan_image(self) -> None:
        """Regression guard for #1516-class bugs: the CPU lane must resolve
        the LEAN toolbox image, never the 7.5 GB rocmfpx one."""
        spec = lane_specs()["cpu"]

        # Identity with the schema constant, not a string literal — a
        # hand-copied literal silently drifts when the constant is bumped.
        assert spec.image is FALLBACK_VULKAN_IMAGE

    def test_cpu_lane_uses_the_lean_toolbox_bench_binary(self) -> None:
        spec = lane_specs()["cpu"]

        assert spec.bench_bin == CPU_BENCH_BIN

    def test_cpu_lane_pins_zero_gpu_layers_and_no_device(self) -> None:
        spec = lane_specs()["cpu"]

        assert spec.dev_args == (("-ngl", "0"),)
        assert not any(k == "-dev" for k, _ in spec.dev_args)

    def test_cpu_lane_carries_no_extra_env(self) -> None:
        spec = lane_specs()["cpu"]

        assert spec.env == ()

    @pytest.mark.parametrize(
        ("lane", "dev"),
        [("rocm", "ROCm0"), ("vulkan_radv", "Vulkan0")],
    )
    def test_gpu_lanes_use_the_default_rocmfpx_image(self, lane, dev) -> None:
        spec = lane_specs()[lane]

        assert spec.image is DEFAULT_ROCMFPX_IMAGE
        assert spec.bench_bin == ROCMFPX_BENCH_BIN
        assert spec.dev_args == (("-ngl", "99"), ("-dev", dev))

    def test_rocm_lane_ubatch_and_env(self) -> None:
        spec = lane_specs()["rocm"]

        assert spec.ubatch == 2048
        assert spec.env == ("GGML_HIP_ENABLE_UNIFIED_MEMORY=1",)

    def test_vulkan_lane_ubatch_and_no_env(self) -> None:
        """``vulkan_radv`` shares the rocm image but is a distinct lane: half
        the ubatch, no HIP unified-memory env (that flag is ROCm-specific)."""
        spec = lane_specs()["vulkan_radv"]

        assert spec.ubatch == 512
        assert spec.env == ()

    def test_lane_field_matches_the_dict_key(self) -> None:
        specs = lane_specs()

        for key, spec in specs.items():
            assert spec.lane == key


# ── default_lanes ────────────────────────────────────────────────────────────


class TestDefaultLanes:
    """The tier-scoped default sweep set (Phase 1's ``BACKEND_ORDER``)."""

    def test_cpu_tier_defaults_to_the_cpu_lane_only(self) -> None:
        assert default_lanes(TIER_CPU) == ["cpu"]

    def test_amd_tier_defaults_to_rocm_and_vulkan(self) -> None:
        assert default_lanes(TIER_AMD) == ["rocm", "vulkan_radv"]

    def test_nvidia_tier_defaults_to_rocm_and_vulkan(self) -> None:
        assert default_lanes(TIER_NVIDIA) == ["rocm", "vulkan_radv"]


# ── dedupe_flags ─────────────────────────────────────────────────────────────


class TestDedupeFlags:
    """Port of ``run_benchmarks.sh``'s ``dedupe_flag_pairs``: llama-bench
    APPENDS a repeated flag into a sweep dimension instead of overriding, so
    later sources must REPLACE earlier ones (common < lane dev_args < ctx <
    caller extras), while unrepeated flags keep first-seen order."""

    def test_later_source_wins(self) -> None:
        result = dedupe_flags([("-ngl", "0"), ("-ngl", "99")])

        assert result == [("-ngl", "99")]

    def test_first_seen_order_is_preserved_for_distinct_flags(self) -> None:
        result = dedupe_flags([("-fa", "1"), ("-mmp", "0"), ("-ngl", "99")])

        assert [k for k, _ in result] == ["-fa", "-mmp", "-ngl"]

    def test_a_flag_repeated_keeps_its_first_seen_position(self) -> None:
        """The regression the shell version's associative-array + order-list
        combo guards: replacing a flag's value must not move it to the end."""
        result = dedupe_flags([("-fa", "1"), ("-mmp", "0"), ("-fa", "0")])

        assert [k for k, _ in result] == ["-fa", "-mmp"]
        assert dict(result)["-fa"] == "0"

    def test_comma_sweep_values_pass_through_untouched(self) -> None:
        """``-ub 512,1024,2048`` is a single flag value (a llama-bench value
        sweep), never split — dedupe only keys on the FLAG name."""
        result = dedupe_flags([("-ub", "512,1024,2048")])

        assert result == [("-ub", "512,1024,2048")]

    def test_fa_1_then_fa_0_yields_a_single_fa_0(self) -> None:
        """The exact scenario the shell docstring calls out: two ``-fa``
        pairs must never survive as two rows in the result."""
        result = dedupe_flags([("-fa", "1"), ("-fa", "0")])

        assert result == [("-fa", "0")]

    def test_empty_input_yields_empty_output(self) -> None:
        assert dedupe_flags([]) == []


# ── compose_podman_argv ──────────────────────────────────────────────────────


def _amd_devices(kfd="/dev/kfd", render="/dev/dri/renderD128") -> BenchDeviceSpec:
    return BenchDeviceSpec(
        tier=TIER_AMD,
        devices=(kfd, render),
        group_ids=(993, 44),
        source="discovery",
        gpu_label="AMD Radeon 8060S Graphics",
    )


def _cpu_devices() -> BenchDeviceSpec:
    return BenchDeviceSpec(tier=TIER_CPU, source="none", gpu_label="")


class TestComposePodmanArgv:
    def test_cpu_lane_composes_no_device_or_group_add_flags(self) -> None:
        spec = lane_specs()["cpu"]
        argv = compose_podman_argv(spec, _cpu_devices(), "/models/tiny.gguf", "/models", flags=[])

        assert not [a for a in argv if a.startswith("--device")]
        assert not [a for a in argv if a.startswith("--group-add")]

    def test_gpu_lane_carries_the_resolver_device_flags_verbatim(self) -> None:
        spec = lane_specs()["rocm"]
        devices = _amd_devices()
        argv = compose_podman_argv(spec, devices, "/models/tiny.gguf", "/models", flags=[])

        assert devices.podman_flags()
        for flag in devices.podman_flags():
            assert flag in argv

    def test_exactly_one_ngl_in_the_composed_argv(self) -> None:
        spec = lane_specs()["rocm"]
        argv = compose_podman_argv(spec, _amd_devices(), "/models/tiny.gguf", "/models", flags=[])

        assert argv.count("-ngl") == 1
        assert argv[argv.index("-ngl") + 1] == "99"

    def test_caller_flags_override_the_lane_default_ngl(self) -> None:
        """later-source-wins: a caller-supplied ``-ngl`` (e.g. an ``--extra``
        override) replaces the lane default rather than adding a second
        row — compose_podman_argv must run its args through dedupe_flags."""
        spec = lane_specs()["rocm"]
        argv = compose_podman_argv(
            spec, _amd_devices(), "/models/tiny.gguf", "/models", flags=[("-ngl", "50")]
        )

        assert argv.count("-ngl") == 1
        assert argv[argv.index("-ngl") + 1] == "50"

    def test_o_json_is_last(self) -> None:
        spec = lane_specs()["cpu"]
        argv = compose_podman_argv(spec, _cpu_devices(), "/models/tiny.gguf", "/models", flags=[])

        assert argv[-2:] == ["-o", "json"]

    def test_volume_mount_is_read_only(self) -> None:
        spec = lane_specs()["cpu"]
        argv = compose_podman_argv(spec, _cpu_devices(), "/models/tiny.gguf", "/models", flags=[])

        assert "--volume=/models:/models:ro,z" in argv

    def test_rm_flag_is_present(self) -> None:
        spec = lane_specs()["cpu"]
        argv = compose_podman_argv(spec, _cpu_devices(), "/models/tiny.gguf", "/models", flags=[])

        assert "--rm" in argv

    def test_security_opts_are_present(self) -> None:
        spec = lane_specs()["cpu"]
        argv = compose_podman_argv(spec, _cpu_devices(), "/models/tiny.gguf", "/models", flags=[])

        assert "apparmor=unconfined" in argv
        assert "seccomp=unconfined" in argv

    def test_entrypoint_and_image_match_the_lane(self) -> None:
        spec = lane_specs()["cpu"]
        argv = compose_podman_argv(spec, _cpu_devices(), "/models/tiny.gguf", "/models", flags=[])

        assert argv[argv.index("--entrypoint") + 1] == spec.bench_bin
        assert argv[argv.index("--entrypoint") + 2] == spec.image

    def test_rocm_lane_env_flags_are_composed(self) -> None:
        spec = lane_specs()["rocm"]
        argv = compose_podman_argv(spec, _amd_devices(), "/models/tiny.gguf", "/models", flags=[])

        i = argv.index("-e")
        assert argv[i + 1] == "GGML_HIP_ENABLE_UNIFIED_MEMORY=1"

    def test_vulkan_lane_has_no_env_flags(self) -> None:
        spec = lane_specs()["vulkan_radv"]
        argv = compose_podman_argv(spec, _amd_devices(), "/models/tiny.gguf", "/models", flags=[])

        assert "-e" not in argv

    def test_model_path_is_passed_verbatim_after_dash_m(self) -> None:
        spec = lane_specs()["cpu"]
        argv = compose_podman_argv(
            spec, _cpu_devices(), "/models/some/tiny.gguf", "/models", flags=[]
        )

        assert argv[argv.index("-m") + 1] == "/models/some/tiny.gguf"

    def test_common_args_are_present_and_ngl_is_not_duplicated_by_them(self) -> None:
        """``COMMON`` (``-fa 1``, ``-mmp 0``) never carries ``-ngl`` — that is
        the whole reason ``-ngl`` lives in each lane's ``dev_args`` instead
        (Phase 1 regression: a common ``-ngl 99`` plus the CPU lane's
        ``-ngl 0`` would run BOTH and double every CPU cell)."""
        spec = lane_specs()["cpu"]
        argv = compose_podman_argv(spec, _cpu_devices(), "/models/tiny.gguf", "/models", flags=[])

        assert "-fa" in argv
        assert argv[argv.index("-fa") + 1] == "1"
        assert "-mmp" in argv
        assert argv.count("-ngl") == 1
        assert argv[argv.index("-ngl") + 1] == "0"

    def test_full_cpu_argv_shape(self) -> None:
        """A precise end-to-end shape check for the simplest lane (no
        devices, no env) — the property tests above cover the GPU lane and
        the override/dedupe behavior individually."""
        spec = lane_specs()["cpu"]
        argv = compose_podman_argv(spec, _cpu_devices(), "/models/tiny.gguf", "/models", flags=[])

        assert argv == [
            "podman",
            "run",
            "--rm",
            "--security-opt",
            "apparmor=unconfined",
            "--security-opt",
            "seccomp=unconfined",
            "--volume=/models:/models:ro,z",
            "--entrypoint",
            CPU_BENCH_BIN,
            FALLBACK_VULKAN_IMAGE,
            "-m",
            "/models/tiny.gguf",
            "-fa",
            "1",
            "-mmp",
            "0",
            "-ngl",
            "0",
            "-o",
            "json",
        ]


# ── benchctl_exec_argv ───────────────────────────────────────────────────────


class TestBenchctlExecArgv:
    def test_no_timeout_wraps_with_sudo_and_double_dash(self) -> None:
        podman_argv = ["podman", "run", "--rm", "image"]

        argv = benchctl_exec_argv(podman_argv, None)

        assert argv == ["sudo", "-n", BENCHCTL, "exec", "--", *podman_argv]

    def test_timeout_s_inserts_the_flag_before_the_double_dash(self) -> None:
        podman_argv = ["podman", "run", "--rm", "image"]

        argv = benchctl_exec_argv(podman_argv, 5)

        assert argv == [
            "sudo",
            "-n",
            BENCHCTL,
            "exec",
            "--timeout-s",
            "5",
            "--",
            *podman_argv,
        ]

    def test_zero_timeout_is_falsy_like_none(self) -> None:
        """``timeout_s: int | None`` — the docstring's conditional
        (``if timeout_s else []``) reads 0 as "no cap", same as None, since a
        0-second wall-clock cap is not a meaningful request."""
        argv = benchctl_exec_argv(["podman"], 0)

        assert "--timeout-s" not in argv


# ── run_cell ──────────────────────────────────────────────────────────────────


class _ScriptedRunner:
    """A ``runner(argv, timeout) -> (rc, stdout, stderr)`` stub that replays
    a fixed script of results, one per call, and records every argv it saw."""

    def __init__(self, script: list[tuple[int, str, str]]) -> None:
        self._script = list(script)
        self.calls: list[tuple[list[str], int | None]] = []

    def __call__(self, argv: list[str], timeout: float | None = None):
        self.calls.append((argv, timeout))
        if not self._script:
            raise AssertionError("runner called more times than scripted")
        return self._script.pop(0)


class TestRunCell:
    """``run_cell`` composes one argv and drives the Phase-1 retry ladder:
    rc>=128 retries (rocmfpx init-segfault), rc==0 with empty stdout
    normalises to 139 and retries (the ``podman --rm`` reap race), rc<128
    real failures never retry, and a timed-out attempt (rc 124, or 137 past
    the wall-clock cap) never crash-retries either."""

    def _kwargs(self, tmp_path, runner, **overrides):
        kwargs = dict(
            spec=lane_specs()["cpu"],
            devices=_cpu_devices(),
            model_rel="tiny/tiny.gguf",
            model_root=str(tmp_path),
            flags=[],
            timeout_s=None,
            log_path=tmp_path / "cell.log",
            runner=runner,
        )
        kwargs.update(overrides)
        return kwargs

    def test_success_parses_stdout_json_rows(self, tmp_path) -> None:
        runner = _ScriptedRunner([(0, _FAKE_BENCH_JSON, "")])

        result = run_cell(**self._kwargs(tmp_path, runner))

        assert isinstance(result, CellResult)
        assert result.rc == 0
        assert result.rows == [_FAKE_BENCH_ROW]
        assert len(runner.calls) == 1

    def test_rc_1_fails_without_retry(self, tmp_path) -> None:
        runner = _ScriptedRunner([(1, "", "failed to load model")])

        result = run_cell(**self._kwargs(tmp_path, runner))

        assert result.rc == 1
        assert result.rows == []
        assert len(runner.calls) == 1

    def test_rc_139_retries_up_to_max_attempts(self, tmp_path) -> None:
        script = [(139, "", "segv")] * MAX_ATTEMPTS
        runner = _ScriptedRunner(script)

        result = run_cell(**self._kwargs(tmp_path, runner))

        assert result.rc == 139
        assert result.rows == []
        assert len(runner.calls) == MAX_ATTEMPTS

    def test_rc_139_retry_can_succeed_on_a_later_attempt(self, tmp_path) -> None:
        runner = _ScriptedRunner([(139, "", "segv"), (0, _FAKE_BENCH_JSON, "")])

        result = run_cell(**self._kwargs(tmp_path, runner))

        assert result.rc == 0
        assert result.rows == [_FAKE_BENCH_ROW]
        assert len(runner.calls) == 2

    def test_rc_0_with_empty_stdout_normalises_to_139_and_retries(self, tmp_path) -> None:
        """The ``podman --rm`` reap race: the kernel logs the segfault, but
        podman itself returns 0 with 0 bytes of stdout. Must be treated
        exactly like a real rc=139 crash, not a (bogus) success."""
        runner = _ScriptedRunner([(0, "", ""), (0, _FAKE_BENCH_JSON, "")])

        result = run_cell(**self._kwargs(tmp_path, runner))

        assert result.rc == 0
        assert result.rows == [_FAKE_BENCH_ROW]
        assert len(runner.calls) == 2

    def test_rc_124_with_timeout_set_fails_without_retry(self, tmp_path) -> None:
        runner = _ScriptedRunner([(124, "", "")])

        result = run_cell(**self._kwargs(tmp_path, runner, timeout_s=30))

        assert result.rc == 124
        assert len(runner.calls) == 1

    def test_meta_carries_the_legacy_provenance_keys(self, tmp_path) -> None:
        """``meta`` feeds ``artifacts/meta.json``, which ``parsers.py`` reads
        by these exact key names — see the task brief's global constraints.

        ``context``/``tag``/``host``/``extra``/``reps`` are not derivable
        purely from ``run_cell``'s documented kwargs (they are not among
        them), so this only pins the KEY SET plus the values that ARE
        directly derivable from the documented inputs (image, ubatch,
        model_rel, model_path, tier, gpu, backend). How the implementation
        threads the remaining provenance through is left to the implementer;
        reconcile against this test rather than silently dropping a key.
        """
        runner = _ScriptedRunner([(0, _FAKE_BENCH_JSON, "")])

        result = run_cell(**self._kwargs(tmp_path, runner))

        legacy_keys = {
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
        assert legacy_keys <= set(result.meta)
        assert result.meta["backend"] == "cpu"
        assert result.meta["image"] == FALLBACK_VULKAN_IMAGE
        assert result.meta["ubatch"] == 512
        assert result.meta["model_rel"] == "tiny/tiny.gguf"
        assert result.meta["model_path"] == f"{tmp_path}/tiny/tiny.gguf"

    def test_meta_tier_and_gpu_come_from_the_device_spec(self, tmp_path) -> None:
        runner = _ScriptedRunner([(0, _FAKE_BENCH_JSON, "")])
        devices = _amd_devices()

        result = run_cell(
            **self._kwargs(tmp_path, runner, spec=lane_specs()["rocm"], devices=devices)
        )

        assert result.meta["tier"] == TIER_AMD
        assert result.meta["gpu"] == devices.gpu_label

    def test_cpu_tier_gpu_label_is_the_no_passthrough_string(self, tmp_path) -> None:
        """Matches Phase 1's ``GPU_LABEL="CPU (no GPU passthrough)"`` — a CPU
        cell must never be filed under a probed GPU's name."""
        runner = _ScriptedRunner([(0, _FAKE_BENCH_JSON, "")])
        devices = BenchDeviceSpec(
            tier=TIER_CPU, source="env", gpu_label="AMD Radeon 8060S Graphics"
        )

        result = run_cell(**self._kwargs(tmp_path, runner, devices=devices))

        assert result.meta["gpu"] == "CPU (no GPU passthrough)"

    def test_tail_is_the_last_4000_chars_of_stderr(self, tmp_path) -> None:
        long_stderr = "x" * 5000
        runner = _ScriptedRunner([(1, "", long_stderr)])

        result = run_cell(**self._kwargs(tmp_path, runner))

        assert len(result.tail) <= 4000
        assert result.tail == long_stderr[-4000:]

    def test_failure_rows_are_empty(self, tmp_path) -> None:
        runner = _ScriptedRunner([(1, "", "boom")])

        result = run_cell(**self._kwargs(tmp_path, runner))

        assert result.rows == []


# ── ExclusiveSlots ───────────────────────────────────────────────────────────


class _SlotRunner:
    """Records every ``(argv, timeout)`` call and answers the
    ``systemctl list-units`` preflight from a scripted unit list.

    NOTE: the exact argv shape ``ExclusiveSlots`` uses for the unprivileged
    list-units read and the privileged stop/start seam call is prose-only in
    the interface contract (no literal argv is given, unlike ``run_cell``'s
    documented shapes). This stub recognises calls by their FIRST word
    (``systemctl`` for the read, ``sudo`` for the privileged seam call) —
    which is loose on purpose so it survives minor exact-argv differences in
    the eventual implementation; the assertions below check that a stop/start
    round-trip happened and named the right unit instances, not the full argv.
    """

    def __init__(self, active_units: list[str]) -> None:
        self._active_units = active_units
        self.calls: list[list[str]] = []
        self.stopped: list[str] = []
        self.started: list[str] = []

    def __call__(self, argv: list[str], timeout: float | None = None):
        self.calls.append(list(argv))
        if argv and argv[0] == "systemctl":
            return 0, "\n".join(self._active_units), ""
        if argv and argv[0] == "sudo":
            verb, unit = argv[-2], argv[-1]
            if verb == "stop":
                self.stopped.append(unit)
            elif verb == "start":
                self.started.append(unit)
            return 0, "", ""
        raise AssertionError(f"unexpected call: {argv}")


class TestExclusiveSlots:
    def test_stops_active_non_npu_slots_on_enter(self, tmp_path) -> None:
        runner = _SlotRunner(["hal0-slot@agent.service", "hal0-slot@npu.service"])

        with ExclusiveSlots(runner=runner):
            pass

        assert "npu" not in " ".join(runner.stopped)
        assert any("agent" in u for u in runner.stopped)

    def test_restarts_stopped_slots_on_normal_exit(self, tmp_path) -> None:
        runner = _SlotRunner(["hal0-slot@agent.service"])

        with ExclusiveSlots(runner=runner):
            pass

        assert runner.started == runner.stopped

    def test_restarts_stopped_slots_even_when_the_body_raises(self, tmp_path) -> None:
        runner = _SlotRunner(["hal0-slot@agent.service"])

        with pytest.raises(RuntimeError), ExclusiveSlots(runner=runner):
            raise RuntimeError("boom")

        assert runner.started == runner.stopped
        assert runner.started

    def test_is_a_noop_when_no_slots_are_active(self, tmp_path) -> None:
        runner = _SlotRunner([])

        with ExclusiveSlots(runner=runner):
            pass

        assert runner.stopped == []
        assert runner.started == []

    def test_the_privileged_calls_go_through_the_systemctl_seam(self, tmp_path) -> None:
        runner = _SlotRunner(["hal0-slot@agent.service"])

        with ExclusiveSlots(runner=runner):
            pass

        privileged = [c for c in runner.calls if c and c[0] == "sudo"]
        assert privileged
        for call in privileged:
            assert SYSTEMCTL_SEAM in call


def test_module_exposes_the_privileged_seam_paths() -> None:
    """The two hardcoded root-side paths every privileged call routes
    through — a typo here silently widens or breaks a sudoers grant."""
    assert BENCHCTL == "/usr/lib/hal0/bin/hal0-benchctl"
    assert SYSTEMCTL_SEAM == "/usr/lib/hal0/bin/hal0-systemctl"


def test_max_attempts_matches_the_phase_1_retry_ladder() -> None:
    assert MAX_ATTEMPTS == 6


def test_lane_spec_is_frozen() -> None:
    spec = lane_specs()["cpu"]

    with pytest.raises((AttributeError, TypeError)):
        spec.lane = "other"  # type: ignore[misc]
