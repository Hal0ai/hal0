"""The benchmark harness's CPU hardware tier — matrix, argv, and metadata.

PLAN's v1.0 quality bar wants "published throughput + latency baselines for
the default loadout on each supported hardware tier (Strix Halo iGPU, AMD
dGPU, NVIDIA dGPU, CPU)". Device resolution for all four tiers landed with
``hal0.bench.devices`` (#1303), but the CPU tier still could not be swept:
``BACKENDS`` in ``installer/bench/config.sh`` had no ``cpu`` key, and
``hal0-benchctl``'s ``validate_backend`` whitelist rejected the name, so the
tier was unreachable from either end.

Everything here runs without a GPU, without podman, and without network:

* the device-discovery roots are relocated into ``tmp_path`` via the
  ``HAL0_BENCH_KFD_PATH`` / ``HAL0_BENCH_DRI_DIR`` seams (same trick as
  ``tests/bench/test_devices.py``), so the CPU tier resolves for real;
* ``$RUNTIME`` — the harness's own podman seam — is pointed at a recording
  stub that prints a llama-bench-shaped JSON array, so a full cell (result +
  ``.meta.json`` + aggregation) executes end to end;
* ``systemctl`` is shadowed by a no-op on ``$PATH`` so the harness's
  read-only GPU-idle preflight never touches the host's system bus.
"""

from __future__ import annotations

import json
import os
import re
import subprocess
import sys
from pathlib import Path

import pytest

_REPO = Path(__file__).resolve().parents[2]
CONFIG_SH = _REPO / "installer" / "bench" / "config.sh"
RUN_SH = _REPO / "installer" / "bench" / "run_benchmarks.sh"
AGGREGATE_PY = _REPO / "installer" / "bench" / "generate_results_json.py"
BENCHCTL = _REPO / "installer" / "wrappers" / "hal0-benchctl"
REPO_SRC = _REPO / "src"

#: What the CPU lane must resolve to. Mirrors
#: ``hal0.config.schema.FALLBACK_VULKAN_IMAGE`` — the image the production CPU
#: slot uses, deliberately NOT the 7.5 GB rocmfpx one.
CPU_IMAGE = "ghcr.io/hal0ai/amd-strix-halo-toolboxes:vulkan-radv-server"
CPU_BENCH_BIN = "/usr/local/bin/llama-bench"

#: One llama-bench ``-o json`` row, trimmed to the fields the aggregator reads.
_FAKE_BENCH_JSON = json.dumps(
    [
        {
            "build_commit": "deadbee",
            "build_number": 7,
            "model_filename": "tiny.gguf",
            "model_type": "llama 1B Q4_K - Medium",
            "model_size": 1024,
            "model_n_params": 1000,
            "n_prompt": 512,
            "n_gen": 0,
            "n_depth": 0,
            "n_batch": 2048,
            "n_ubatch": 512,
            "n_threads": 8,
            "n_gpu_layers": 0,
            "flash_attn": True,
            "type_k": "f16",
            "type_v": "f16",
            "avg_ts": 12.5,
            "stddev_ts": 0.25,
            "avg_ns": 40_000_000,
            "stddev_ns": 100,
            "gpu_info": "",
            "cpu_info": "AMD Ryzen",
            "backends": "CPU",
        }
    ]
)


# ── harness fixtures ─────────────────────────────────────────────────────────


def _gpu_roots(tmp_path: Path) -> tuple[str, str]:
    """Discovery roots that look like an AMD box (kfd + card1 + renderD128)."""
    dri = tmp_path / "dri"
    dri.mkdir(exist_ok=True)
    (dri / "card1").symlink_to("/dev/null")
    (dri / "renderD128").symlink_to("/dev/null")
    kfd = tmp_path / "kfd"
    kfd.symlink_to("/dev/null")
    return str(kfd), str(dri)


def _cpu_roots(tmp_path: Path) -> tuple[str, str]:
    """Discovery roots with nothing in them — the CPU tier."""
    return str(tmp_path / "absent-kfd"), str(tmp_path / "absent-dri")


def _stub_bin(tmp_path: Path) -> tuple[str, Path]:
    """A ``$PATH`` dir shadowing ``systemctl``, plus a recording ``$RUNTIME``.

    The systemctl stub keeps the harness's ``gpu_slots_active`` preflight —
    which shells out to ``systemctl list-units`` — away from the developer's
    real system bus entirely. It is a no-op, never a mutating verb.
    """
    bindir = tmp_path / "stub-bin"
    bindir.mkdir(exist_ok=True)
    systemctl = bindir / "systemctl"
    systemctl.write_text("#!/bin/sh\nexit 0\n")
    systemctl.chmod(0o755)

    argv_log = tmp_path / "runtime-argv.txt"
    runtime = bindir / "fake-container-runtime"
    runtime.write_text(
        "#!/bin/sh\n"
        f'printf "%s\\n" "$@" >> "{argv_log}"\n'
        f"cat <<'HAL0BENCHJSON'\n{_FAKE_BENCH_JSON}\nHAL0BENCHJSON\n"
    )
    runtime.chmod(0o755)
    return str(runtime), argv_log


def _env(tmp_path: Path, roots: tuple[str, str], **extra: str) -> dict[str, str]:
    kfd, dri = roots
    bindir = tmp_path / "stub-bin"
    bindir.mkdir(exist_ok=True)
    env = {
        "PATH": f"{bindir}:/usr/bin:/bin",
        "PYTHONPATH": str(REPO_SRC),
        "HAL0_HOME": str(tmp_path / "hal0-home"),
        "HAL0_BENCH_PYTHON": sys.executable,
        "HAL0_BENCH_KFD_PATH": kfd,
        "HAL0_BENCH_DRI_DIR": dri,
        "MODEL_DIR": str(tmp_path / "models"),
        "RESULT_DIR": str(tmp_path / "results"),
    }
    env.update(extra)
    return env


def _source_config(tmp_path: Path, roots: tuple[str, str], body: str, **extra: str):
    """Source config.sh with the given roots and run ``body`` after it."""
    script = f'set -uo pipefail\nsource "{CONFIG_SH}"\n{body}'
    return subprocess.run(
        ["bash", "-c", script],
        capture_output=True,
        text=True,
        env=_env(tmp_path, roots, **extra),
        timeout=120,
    )


def _write_hardware_json(tmp_path: Path, payload: dict) -> None:
    etc = tmp_path / "hal0-home" / "etc" / "hal0"
    etc.mkdir(parents=True, exist_ok=True)
    (etc / "hardware.json").write_text(json.dumps(payload))


def _model(tmp_path: Path, rel: str = "tiny/tiny.gguf") -> str:
    path = tmp_path / "models" / rel
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(b"GGUF")
    return rel


def _sweep(tmp_path: Path, roots: tuple[str, str], *args: str, **extra: str):
    return subprocess.run(
        ["bash", str(RUN_SH), *args],
        capture_output=True,
        text=True,
        env=_env(tmp_path, roots, **extra),
        timeout=300,
    )


def _dry_run_argv(stdout: str, backend: str) -> list[str]:
    """The podman argv the harness printed for ``backend`` in ``--dry-run``.

    ``--dry-run`` emits ``printf '%q '`` — shell-quoted words on one line,
    preceded by a ``[run] <backend> / …`` header.
    """
    lines = stdout.splitlines()
    for i, line in enumerate(lines):
        if line.startswith(f"[run] {backend} /"):
            return lines[i + 1].split()
    raise AssertionError(f"no dry-run cell for backend {backend!r} in:\n{stdout}")


# ── the matrix entry ─────────────────────────────────────────────────────────


class TestBackendMatrix:
    """``BACKENDS`` must carry a ``cpu`` lane in the same 5-field shape."""

    def test_cpu_lane_exists_in_the_backends_matrix(self, tmp_path) -> None:
        """The gap this change closes: no ``cpu`` key = no CPU tier at all."""
        proc = _source_config(tmp_path, _cpu_roots(tmp_path), 'echo "${BACKENDS[cpu]:-MISSING}"')

        assert proc.returncode == 0, proc.stderr
        spec = proc.stdout.strip()
        assert spec != "MISSING", "BACKENDS has no `cpu` entry — the CPU tier cannot be swept"
        image, bench_bin, ubatch, envstr, devargs = spec.split("|")
        assert image == CPU_IMAGE
        assert bench_bin == CPU_BENCH_BIN
        assert ubatch.isdigit()
        assert envstr == ""
        assert devargs.split() == ["-ngl", "0"]

    def test_cpu_lane_asks_for_no_device(self, tmp_path) -> None:
        """A ``-dev`` pin on the CPU lane would name a device that is not
        passed through; the whole point of the tier is that none is."""
        proc = _source_config(tmp_path, _cpu_roots(tmp_path), 'echo "${BACKENDS[cpu]}"')

        assert "-dev" not in proc.stdout.split()

    def test_gpu_lanes_keep_full_offload_and_their_device_pin(self, tmp_path) -> None:
        """Regression guard for moving ``-ngl`` out of ``COMMON_BENCH_ARGS``."""
        proc = _source_config(
            tmp_path,
            _gpu_roots(tmp_path),
            'echo "${BACKENDS[rocm]}"\necho "${BACKENDS[vulkan_radv]}"',
        )

        assert proc.returncode == 0, proc.stderr
        rocm, vulkan = proc.stdout.strip().splitlines()
        assert rocm.split("|")[-1] == "-ngl 99 -dev ROCm0"
        assert vulkan.split("|")[-1] == "-ngl 99 -dev Vulkan0"

    def test_common_bench_args_carry_no_ngl(self, tmp_path) -> None:
        """llama-bench APPENDS repeated ``-ngl`` values into a sweep
        dimension rather than overriding, so a common ``-ngl 99`` plus the CPU
        lane's ``-ngl 0`` would run both and double every CPU cell."""
        proc = _source_config(
            tmp_path, _cpu_roots(tmp_path), 'printf "%s\\n" "${COMMON_BENCH_ARGS[@]}"'
        )

        assert proc.returncode == 0, proc.stderr
        assert "-ngl" not in proc.stdout.split()
        assert "-fa" in proc.stdout.split()


class TestBackendOrder:
    """The default sweep set follows the tier the resolver reports."""

    def test_cpu_tier_defaults_to_the_cpu_lane(self, tmp_path) -> None:
        proc = _source_config(
            tmp_path,
            _cpu_roots(tmp_path),
            'echo "TIER=$BENCH_TIER"\necho "ORDER=${BACKEND_ORDER[*]}"',
        )

        assert proc.returncode == 0, proc.stderr
        assert "TIER=cpu" in proc.stdout
        assert "ORDER=cpu" in proc.stdout

    def test_gpu_tier_default_order_is_unchanged(self, tmp_path) -> None:
        proc = _source_config(tmp_path, _gpu_roots(tmp_path), 'echo "ORDER=${BACKEND_ORDER[*]}"')

        assert proc.returncode == 0, proc.stderr
        assert "ORDER=rocm vulkan_radv" in proc.stdout


class TestGpuLabel:
    """``GPU_LABEL`` is stamped into every ``.meta.json`` — it must not name a
    GPU that the run never touched."""

    def test_cpu_tier_label_ignores_a_probed_gpu_name(self, tmp_path) -> None:
        """The realistic way the CPU baseline gets measured: pin the tier on a
        box that HAS a GPU. The probe still reports that GPU's name, and
        taking it would file CPU numbers under the iGPU."""
        _write_hardware_json(
            tmp_path, {"gpus": [{"vendor": "amd", "name": "AMD Radeon 8060S Graphics"}]}
        )

        proc = _source_config(
            tmp_path,
            _gpu_roots(tmp_path),
            'echo "TIER=$BENCH_TIER"\necho "LABEL=$GPU_LABEL"',
            HAL0_BENCH_TIER="cpu",
        )

        assert proc.returncode == 0, proc.stderr
        assert "TIER=cpu" in proc.stdout
        assert "LABEL=CPU (no GPU passthrough)" in proc.stdout
        assert "8060S" not in proc.stdout

    def test_gpu_tier_still_uses_the_probed_label(self, tmp_path) -> None:
        _write_hardware_json(
            tmp_path, {"gpus": [{"vendor": "amd", "name": "AMD Radeon 8060S Graphics"}]}
        )

        proc = _source_config(tmp_path, _gpu_roots(tmp_path), 'echo "LABEL=$GPU_LABEL"')

        assert "LABEL=AMD Radeon 8060S Graphics" in proc.stdout


# ── the sweep argv ───────────────────────────────────────────────────────────


class TestCpuSweepArgv:
    def test_cpu_sweep_resolves_and_emits_no_device_flags(self, tmp_path) -> None:
        """The acceptance criterion: a CPU-tier cell asks podman for no
        ``--device`` and no ``--group-add`` — it must not need a DRI node."""
        rel = _model(tmp_path)
        _stub_bin(tmp_path)

        proc = _sweep(tmp_path, _cpu_roots(tmp_path), "--models", rel, "--dry-run")

        assert proc.returncode == 0, proc.stderr
        assert "Tier     : cpu (CPU (no GPU passthrough))" in proc.stdout
        argv = _dry_run_argv(proc.stdout, "cpu")
        assert not [a for a in argv if a.startswith("--device")]
        assert not [a for a in argv if a.startswith("--group-add")]
        assert "/dev/" not in " ".join(argv)

    def test_cpu_sweep_uses_the_lean_image_and_its_bench_binary(self, tmp_path) -> None:
        rel = _model(tmp_path)
        _stub_bin(tmp_path)

        proc = _sweep(tmp_path, _cpu_roots(tmp_path), "--models", rel, "--dry-run")

        argv = _dry_run_argv(proc.stdout, "cpu")
        assert CPU_IMAGE in argv
        assert "--entrypoint" in argv
        assert argv[argv.index("--entrypoint") + 1] == CPU_BENCH_BIN

    def test_cpu_sweep_pins_zero_gpu_layers_exactly_once(self, tmp_path) -> None:
        """One ``-ngl`` word, and its value is 0 — a second occurrence would
        become a second llama-bench row claiming GPU offload."""
        rel = _model(tmp_path)
        _stub_bin(tmp_path)

        proc = _sweep(tmp_path, _cpu_roots(tmp_path), "--models", rel, "--dry-run")

        argv = _dry_run_argv(proc.stdout, "cpu")
        assert argv.count("-ngl") == 1
        assert argv[argv.index("-ngl") + 1] == "0"

    def test_cpu_lane_is_selectable_by_name_on_a_gpu_box(self, tmp_path) -> None:
        """``--backends cpu`` measures the CPU baseline on hardware that also
        has a GPU — the normal way the fourth tier gets covered."""
        rel = _model(tmp_path)
        _stub_bin(tmp_path)

        proc = _sweep(
            tmp_path, _gpu_roots(tmp_path), "--models", rel, "--backends", "cpu", "--dry-run"
        )

        assert proc.returncode == 0, proc.stderr
        argv = _dry_run_argv(proc.stdout, "cpu")
        assert CPU_IMAGE in argv
        assert argv[argv.index("-ngl") + 1] == "0"

    def test_gpu_lane_argv_still_offloads_every_layer(self, tmp_path) -> None:
        """The ``-ngl`` move must not change what a GPU cell asks for."""
        rel = _model(tmp_path)
        _stub_bin(tmp_path)
        kfd, dri = _gpu_roots(tmp_path)

        proc = _sweep(tmp_path, (kfd, dri), "--models", rel, "--backends", "rocm", "--dry-run")

        argv = _dry_run_argv(proc.stdout, "rocm")
        assert argv.count("-ngl") == 1
        assert argv[argv.index("-ngl") + 1] == "99"
        assert argv[argv.index("-dev") + 1] == "ROCm0"
        assert f"--device={kfd}" in argv


# ── a whole cell, end to end ─────────────────────────────────────────────────


class TestCpuCellEndToEnd:
    """A real (non-dry) CPU cell against a stub ``$RUNTIME``: result JSON,
    ``.meta.json``, and the aggregator's view of both."""

    @pytest.fixture
    def _ran(self, tmp_path):
        rel = _model(tmp_path)
        runtime, argv_log = _stub_bin(tmp_path)
        proc = _sweep(
            tmp_path,
            _cpu_roots(tmp_path),
            "--models",
            rel,
            RUNTIME=runtime,
            HOST_LABEL="testbox",
        )
        assert proc.returncode == 0, f"{proc.stdout}\n{proc.stderr}"
        runs = tmp_path / "results" / "runs"
        meta = json.loads(next(runs.glob("*.meta.json")).read_text())
        return proc, meta, argv_log.read_text().splitlines()

    def test_the_cell_runs_and_is_recorded(self, _ran) -> None:
        proc, meta, _ = _ran

        assert "ran=1 skipped=0 failed=0" in proc.stdout
        assert meta["backend"] == "cpu"

    def test_meta_json_records_the_cpu_tier_not_a_gpu(self, _ran) -> None:
        """No fake GPU name, and the tier is explicit so a CPU cell and an
        iGPU cell for the same model are distinguishable once the per-tier
        baselines are published together."""
        _, meta, _ = _ran

        assert meta["tier"] == "cpu"
        assert meta["gpu"] == "CPU (no GPU passthrough)"
        assert meta["image"] == CPU_IMAGE
        assert meta["host"] == "testbox"

    def test_the_container_was_launched_without_gpu_passthrough(self, _ran) -> None:
        _, _, argv = _ran

        assert not [a for a in argv if a.startswith(("--device", "--group-add"))]
        assert "-ngl" in argv
        assert argv[argv.index("-ngl") + 1] == "0"

    def test_aggregated_records_are_labelled_cpu(self, tmp_path, _ran) -> None:
        """``index.json`` carries the tier and the v2 export stops calling
        every host "strix-halo"."""
        results = tmp_path / "results"
        proc = subprocess.run(
            [sys.executable, str(AGGREGATE_PY), str(results), "--emit-v2"],
            capture_output=True,
            text=True,
            timeout=120,
        )
        assert proc.returncode == 0, proc.stderr

        index = json.loads((results / "index.json").read_text())
        record = (index["records"] if isinstance(index, dict) else index)[0]
        assert record["tier"] == "cpu"
        assert record["gpu"] == "CPU (no GPU passthrough)"

        v2 = json.loads((results / "v2" / "records.jsonl").read_text().splitlines()[0])
        assert v2["host"]["platform"] == "cpu"
        assert v2["host"]["name"] == "testbox"
        assert v2["lane"] == "cpu"


# ── the privileged seam ──────────────────────────────────────────────────────


def _validate_backend(name: str) -> subprocess.CompletedProcess:
    """Call ``hal0-benchctl``'s whitelist directly.

    Sourcing runs the script's dispatch with no argv, which prints usage and
    falls through; the functions are then defined in the calling shell.
    """
    return subprocess.run(
        ["bash", "-c", f'source "{BENCHCTL}" >/dev/null; validate_backend "{name}"; echo ACCEPTED'],
        capture_output=True,
        text=True,
        timeout=60,
    )


class TestSeamWhitelist:
    def test_cpu_is_accepted(self) -> None:
        """Without this the matrix entry is unreachable: ``hal0-benchctl
        sweep <model> cpu`` dies at ``bad backend: cpu``."""
        proc = _validate_backend("cpu")

        assert proc.returncode == 0, proc.stderr
        assert "ACCEPTED" in proc.stdout

    @pytest.mark.parametrize("name", ["rocm", "vulkan_radv"])
    def test_the_gpu_lanes_are_still_accepted(self, name) -> None:
        assert _validate_backend(name).returncode == 0

    @pytest.mark.parametrize("name", ["cuda", "cpu2", "cpu;id", ""])
    def test_the_whitelist_did_not_widen(self, name) -> None:
        """Adding one key must not turn the check into a rubber stamp — this
        is the argument-validation seam a root sudoers grant hangs off."""
        proc = _validate_backend(name)

        assert proc.returncode != 0
        assert "bad backend" in proc.stderr

    def test_help_advertises_the_cpu_backend(self) -> None:
        proc = subprocess.run(
            ["bash", str(BENCHCTL), "help"], capture_output=True, text=True, timeout=60
        )

        assert proc.returncode == 0, proc.stderr
        assert "backends: rocm | vulkan_radv | cpu" in proc.stdout

    @pytest.mark.parametrize("tier", ["amd", "nvidia", "cpu", ""])
    def test_known_tiers_are_accepted(self, tier) -> None:
        proc = subprocess.run(
            ["bash", "-c", f'source "{BENCHCTL}" >/dev/null; validate_tier "{tier}"; echo OK'],
            capture_output=True,
            text=True,
            timeout=60,
        )

        assert proc.returncode == 0, proc.stderr

    @pytest.mark.parametrize("tier", ["gpu", "cpu0", "amd;id"])
    def test_unknown_tiers_are_rejected(self, tier) -> None:
        """The telemetry tier hint is a positional argument to a root-run
        seam, so it gets the same treatment as every other argument."""
        proc = subprocess.run(
            ["bash", "-c", f'source "{BENCHCTL}" >/dev/null; validate_tier "{tier}"; echo OK'],
            capture_output=True,
            text=True,
            timeout=60,
        )

        assert proc.returncode != 0
        assert "bad tier" in proc.stderr


# ── the telemetry sampler ────────────────────────────────────────────────────


class TestTelemetrySampler:
    """Static guards on ``hal0-benchctl telemetry``.

    Not executed: the sampler's output dir is the hardcoded
    ``/var/lib/hal0/benchmarks`` and making it env-overridable would widen a
    root-run seam that ``chown -R``s whatever it is pointed at. These assert
    on the shipped source instead, which is still enough to catch a
    reintroduction of the zero-filling / whole-box-hwmon behaviour.
    """

    @pytest.fixture(scope="class")
    def sampler(self) -> str:
        """The telemetry verb's CODE, with comments stripped — the comments
        quote the old behaviour, so matching them would be circular."""
        text = BENCHCTL.read_text()
        block = re.search(r"^  telemetry\)(.*?)^\s{4}esac", text, re.S | re.M)
        assert block, "telemetry verb not found in hal0-benchctl"
        return "\n".join(
            line for line in block.group(1).splitlines() if not line.lstrip().startswith("#")
        )

    def test_the_cpu_tier_skips_the_gpu_counters(self, sampler) -> None:
        """A CPU run on a box that has a GPU must not log that idle GPU's
        busy/temp trace as if it were the run's."""
        assert '"$tier" != "cpu"' in sampler

    def test_the_tier_hint_is_positional_and_validated(self, sampler) -> None:
        """The sudoers grant has no env_keep, so an env-only hint would never
        survive `sudo hal0-benchctl telemetry start`."""
        assert 'tier="${3:-${HAL0_BENCH_TIER:-}}"' in sampler
        assert 'validate_tier "$tier"' in sampler

    def test_missing_counters_are_null_not_zero(self, sampler) -> None:
        """0 reads as "the GPU sat idle", which is a different claim from
        "there is no GPU here"."""
        assert "printf 'null'" in sampler
        assert 'echo "0"' not in sampler

    def test_it_does_not_read_every_hwmon_on_the_box(self, sampler) -> None:
        """The old glob cat'd every sensor: multi-line output produced
        malformed JSON, and on a GPU-less box it reported an NVMe/CPU-package
        temperature as the GPU's."""
        assert "/sys/class/hwmon/hwmon*" not in sampler
        assert "device/hwmon/hwmon*" in sampler


# ── keep the two lists in step ───────────────────────────────────────────────


def test_every_matrix_backend_is_on_the_seam_whitelist(tmp_path) -> None:
    """A lane in ``BACKENDS`` that the seam rejects is dead weight, and a name
    the seam accepts with no lane behind it fails deep inside the sweep."""
    proc = _source_config(tmp_path, _cpu_roots(tmp_path), 'printf "%s\\n" "${!BACKENDS[@]}"')
    assert proc.returncode == 0, proc.stderr
    lanes = sorted(w for w in proc.stdout.split() if w)

    whitelist = re.search(r"validate_backend\(\)\s*\{.*?\^\((.*?)\)\$", BENCHCTL.read_text(), re.S)
    assert whitelist, "validate_backend regex not found"
    assert sorted(whitelist.group(1).split("|")) == lanes


def test_the_harness_scripts_are_syntactically_valid() -> None:
    for script in (CONFIG_SH, RUN_SH, BENCHCTL):
        proc = subprocess.run(
            ["bash", "-n", str(script)], capture_output=True, text=True, timeout=60
        )
        assert proc.returncode == 0, f"{script.name}: {proc.stderr}"


def test_no_test_here_needed_real_hardware() -> None:
    """Documentation-as-assertion: nothing above may reach a real GPU node."""
    assert not os.environ.get("HAL0_BENCH_GPU_DEVICES")
