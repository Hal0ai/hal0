"""Tests for ``installer/wrappers/hal0-benchctl``'s ``exec`` and ``telemetry``
verbs (Phase 2 of the bench overhaul).

Phase 1 (``129f8879``) built the shell harness (``config.sh`` +
``run_benchmarks.sh``) and had ``hal0-benchctl`` validate-and-exec THAT
harness. Phase 2 moves the matrix/retry/composition logic into
``hal0.bench.harness`` (Python) and shrinks the privileged shim to a dumb
validate-and-exec: the unprivileged caller composes the FULL
``podman run … llama-bench -o json`` argv itself; this shim re-validates every
element structurally, then execs it. No matrix knowledge, no retries, no
shell evaluation.

These tests exercise the shim as a subprocess. The shim pins its own ``PATH``
(never trusting the caller's at a root boundary), so stubs are injected via
the explicit test seams sudo's ``env_reset`` strips on the privileged path:

* ``HAL0_BENCHCTL_PODMAN`` / ``HAL0_BENCHCTL_TIMEOUT`` point at recording
  stubs that append their argv to a log file — real execution never happens.
* ``HAL0_BENCH_PYTHON`` points at a one-line stub that prints a fake
  model-store root, so ``MODEL_ROOT`` resolves to a ``tmp_path`` directory
  instead of touching a real hal0 install or ``/mnt/ai-models``.
* device nodes are provided via ``HAL0_BENCH_KFD_PATH`` / ``HAL0_BENCH_DRI_DIR``
  — ``/dev/null`` is used as a stand-in character device (the shim only
  checks ``S_ISCHR``, never the node's real purpose).

``telemetry`` is unchanged from the Phase 1 seam, so its tests are ported
here essentially verbatim from ``tests/bench/test_harness_matrix.py``
(``TestTelemetrySampler``).
"""

from __future__ import annotations

import re
import subprocess
from pathlib import Path

import pytest

_REPO = Path(__file__).resolve().parents[2]
BENCHCTL = _REPO / "installer" / "wrappers" / "hal0-benchctl"


# ── stub PATH -----------------------------------------------------------------


def _write_stub(path: Path, body: str) -> None:
    path.write_text(f"#!/bin/sh\n{body}\n")
    path.chmod(0o755)


def _recording_stub(path: Path, argv_log: Path, *, rc: int = 0, stdout: str = "") -> None:
    """A stub that appends its argv (one word per line, blank line separator)
    to ``argv_log`` and exits ``rc`` after printing ``stdout``."""
    body = f'{{ for a in "$@"; do printf \'%s\\n\' "$a"; done; printf \'\\n\'; }} >> "{argv_log}"\n'
    if stdout:
        body += f"printf '%s' {_sh_quote(stdout)}\n"
    body += f"exit {rc}\n"
    _write_stub(path, body)


def _sh_quote(s: str) -> str:
    return "'" + s.replace("'", "'\\''") + "'"


def _stub_bin(tmp_path: Path, model_root: Path, *, argv_log: Path | None = None) -> Path:
    """A dir of stub binaries the shim reaches via its explicit test seams
    (``HAL0_BENCH_PYTHON`` / ``HAL0_BENCHCTL_PODMAN`` / ``HAL0_BENCHCTL_TIMEOUT``
    — set in ``_env``). ``$PATH`` stubbing no longer works on purpose: the
    shim pins its own ``PATH`` before doing anything else.

    Two independent mechanisms pin ``MODEL_ROOT`` to ``model_root``, because
    the shim's resolver checks ``/usr/lib/hal0/venv/bin/python3`` (an
    ABSOLUTE path) after the ``HAL0_BENCH_PYTHON`` override:

    * ``HAL0_MODEL_STORE`` (an env var ``hal0.config.store.store_root``
      honours directly) makes ANY real, hal0-package-aware python resolve to
      ``model_root``, whichever interpreter the shim happens to invoke.
    * The ``python3`` stub (wired via ``HAL0_BENCH_PYTHON``) never imports
      hal0, just echoes ``$HAL0_MODEL_STORE`` (or ``model_root`` if that is
      somehow unset) — kept in lockstep with the env var above rather than a
      second hardcoded value.
    """
    bindir = tmp_path / "stub-bin"
    bindir.mkdir(exist_ok=True)
    _write_stub(
        bindir / "python3",
        f"printf '%s' \"${{HAL0_MODEL_STORE:-{model_root}}}\"",
    )
    if argv_log is not None:
        _recording_stub(bindir / "podman", argv_log)
        # `timeout` logs its argv, then drops `--kill-after=30 <secs>` and
        # execs the rest, so the test can see whether the wrap happened AND
        # the podman recording still fires. POSIX sh only — CI's /bin/sh is
        # dash, not bash.
        timeout_log = argv_log.with_name(argv_log.name + ".timeout")
        body = (
            f'{{ for a in "$@"; do printf \'%s\\n\' "$a"; done; printf \'\\n\'; }} >> "{timeout_log}"\n'
            "shift 2\n"
            'exec "$@"\n'
        )
        _write_stub(bindir / "timeout", body)
    return bindir


def _model_root(tmp_path: Path) -> Path:
    root = tmp_path / "models"
    root.mkdir(exist_ok=True)
    return root


def _write_model(model_root: Path, rel: str = "tiny/tiny.gguf") -> Path:
    path = model_root / rel
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(b"GGUF")
    return path


def _device_roots(tmp_path: Path) -> tuple[str, str]:
    """A KFD path + DRI dir that pass the shim's char-device check, using
    ``/dev/null`` as the fake device node (real char device, wrong purpose —
    the shim only checks ``S_ISCHR``)."""
    dri = tmp_path / "dri"
    dri.mkdir(exist_ok=True)
    kfd = tmp_path / "kfd"
    kfd.symlink_to("/dev/null")
    (dri / "renderD128").symlink_to("/dev/null")
    return str(kfd), str(dri)


def _env(
    tmp_path: Path, bindir: Path, model_root: Path, *, kfd: str = "", dri: str = ""
) -> dict[str, str]:
    env = {
        "PATH": "/usr/bin:/bin",  # ignored: the shim pins its own PATH
        "HOME": str(tmp_path),
        # Belt-and-braces MODEL_ROOT pin — see _stub_bin's docstring for why
        # both this and the python3 stub exist.
        "HAL0_MODEL_STORE": str(model_root),
        "HAL0_BENCH_PYTHON": str(bindir / "python3"),
    }
    if (bindir / "podman").exists():
        env["HAL0_BENCHCTL_PODMAN"] = str(bindir / "podman")
        env["HAL0_BENCHCTL_TIMEOUT"] = str(bindir / "timeout")
    if kfd:
        env["HAL0_BENCH_KFD_PATH"] = kfd
    if dri:
        env["HAL0_BENCH_DRI_DIR"] = dri
    return env


def _run(argv: list[str], env: dict[str, str], timeout: int = 30) -> subprocess.CompletedProcess:
    return subprocess.run(
        ["bash", str(BENCHCTL), *argv],
        capture_output=True,
        text=True,
        env=env,
        timeout=timeout,
    )


# ── canonical argvs -------------------------------------------------------------


def _rocm_argv(model_root: Path, model_abs: Path, kfd: str, dri_render: str) -> list[str]:
    return [
        "podman",
        "run",
        "--rm",
        f"--device={kfd}",
        f"--device={dri_render}",
        "--security-opt",
        "apparmor=unconfined",
        "--security-opt",
        "seccomp=unconfined",
        f"--volume={model_root}:{model_root}:ro,z",
        "-e",
        "GGML_HIP_ENABLE_UNIFIED_MEMORY=1",
        "--entrypoint",
        "/opt/rocmfpx/bin/llama-bench",
        "ghcr.io/hal0ai/hal0-rocmfpx:ade07ba",
        "-m",
        str(model_abs),
        "-fa",
        "1",
        "-mmp",
        "0",
        "-ngl",
        "99",
        "-dev",
        "ROCm0",
        "-o",
        "json",
    ]


def _cpu_argv(model_root: Path, model_abs: Path) -> list[str]:
    return [
        "podman",
        "run",
        "--rm",
        "--security-opt",
        "apparmor=unconfined",
        "--security-opt",
        "seccomp=unconfined",
        f"--volume={model_root}:{model_root}:ro,z",
        "--entrypoint",
        "/usr/local/bin/llama-bench",
        "ghcr.io/hal0ai/amd-strix-halo-toolboxes:vulkan-radv-server",
        "-m",
        str(model_abs),
        "-fa",
        "1",
        "-mmp",
        "0",
        "-ngl",
        "0",
        "-o",
        "json",
    ]


# ── exec: accepted argvs ────────────────────────────────────────────────────


class TestExecAccepts:
    def test_accepts_a_canonical_rocm_argv(self, tmp_path) -> None:
        model_root = _model_root(tmp_path)
        model_abs = _write_model(model_root)
        kfd, dri = _device_roots(tmp_path)
        argv_log = tmp_path / "podman-argv.log"
        bindir = _stub_bin(tmp_path, model_root, argv_log=argv_log)
        env = _env(tmp_path, bindir, model_root, kfd=kfd, dri=dri)

        podman_argv = _rocm_argv(model_root, model_abs, kfd, str(Path(dri) / "renderD128"))
        proc = _run(["exec", "--", *podman_argv], env)

        assert proc.returncode == 0, proc.stderr
        recorded = argv_log.read_text().strip("\n").split("\n\n")
        assert recorded[0].splitlines() == podman_argv[1:]

    def test_accepts_a_cpu_argv_with_no_devices(self, tmp_path) -> None:
        model_root = _model_root(tmp_path)
        model_abs = _write_model(model_root)
        argv_log = tmp_path / "podman-argv.log"
        bindir = _stub_bin(tmp_path, model_root, argv_log=argv_log)
        env = _env(tmp_path, bindir, model_root)

        podman_argv = _cpu_argv(model_root, model_abs)
        proc = _run(["exec", "--", *podman_argv], env)

        assert proc.returncode == 0, proc.stderr

    def test_timeout_s_wraps_with_timeout_kill_after_30(self, tmp_path) -> None:
        model_root = _model_root(tmp_path)
        model_abs = _write_model(model_root)
        argv_log = tmp_path / "podman-argv.log"
        bindir = _stub_bin(tmp_path, model_root, argv_log=argv_log)
        env = _env(tmp_path, bindir, model_root)

        podman_argv = _cpu_argv(model_root, model_abs)
        proc = _run(["exec", "--timeout-s", "5", "--", *podman_argv], env)

        assert proc.returncode == 0, proc.stderr
        timeout_log = argv_log.with_name(argv_log.name + ".timeout")
        assert timeout_log.exists(), "timeout(1) was not invoked"
        wrapped = timeout_log.read_text().strip("\n").split("\n\n")[0].splitlines()
        assert wrapped[0] == "--kill-after=30"
        assert wrapped[1] == "5"
        # The shim replaces the caller's "podman" token with ITS OWN binary
        # (the test seam here) — argv[0] is never caller-controlled.
        assert wrapped[2] == env["HAL0_BENCHCTL_PODMAN"]
        assert wrapped[3:] == podman_argv[1:]


# ── exec: rejections ─────────────────────────────────────────────────────────


class TestExecRejects:
    def _base(self, tmp_path):
        model_root = _model_root(tmp_path)
        model_abs = _write_model(model_root)
        bindir = _stub_bin(tmp_path, model_root)
        env = _env(tmp_path, bindir, model_root)
        return model_root, model_abs, env

    def test_rejects_m_outside_model_root(self, tmp_path) -> None:
        model_root, _, env = self._base(tmp_path)
        outside = tmp_path / "elsewhere.gguf"
        outside.write_bytes(b"GGUF")
        argv = _cpu_argv(model_root, outside)

        proc = _run(["exec", "--", *argv], env)

        assert proc.returncode != 0

    def test_rejects_m_with_path_traversal(self, tmp_path) -> None:
        model_root, model_abs, env = self._base(tmp_path)
        argv = _cpu_argv(model_root, model_abs)
        idx = argv.index("-m") + 1
        argv[idx] = f"{model_root}/../{model_root.name}/tiny/tiny.gguf"

        proc = _run(["exec", "--", *argv], env)

        assert proc.returncode != 0

    def test_rejects_non_whitelisted_podman_flag(self, tmp_path) -> None:
        model_root, model_abs, env = self._base(tmp_path)
        argv = _cpu_argv(model_root, model_abs)
        argv.insert(argv.index("--rm") + 1, "--privileged")

        proc = _run(["exec", "--", *argv], env)

        assert proc.returncode != 0
        assert "podman flag not allowed: --privileged" in proc.stderr

    def test_rejects_bad_security_opt(self, tmp_path) -> None:
        model_root, model_abs, env = self._base(tmp_path)
        argv = _cpu_argv(model_root, model_abs)
        i = argv.index("--security-opt")
        argv[i + 1] = "apparmor=custom-profile"

        proc = _run(["exec", "--", *argv], env)

        assert proc.returncode != 0

    def test_rejects_a_rw_volume(self, tmp_path) -> None:
        model_root, model_abs, env = self._base(tmp_path)
        argv = _cpu_argv(model_root, model_abs)
        i = argv.index(f"--volume={model_root}:{model_root}:ro,z")
        argv[i] = f"--volume={model_root}:{model_root}:rw,z"

        proc = _run(["exec", "--", *argv], env)

        assert proc.returncode != 0

    def test_rejects_a_non_ghcr_image(self, tmp_path) -> None:
        model_root, model_abs, env = self._base(tmp_path)
        argv = _cpu_argv(model_root, model_abs)
        idx = argv.index("--entrypoint") + 2
        argv[idx] = "docker.io/library/alpine:latest"

        proc = _run(["exec", "--", *argv], env)

        assert proc.returncode != 0

    def test_rejects_a_bad_entrypoint(self, tmp_path) -> None:
        model_root, model_abs, env = self._base(tmp_path)
        argv = _cpu_argv(model_root, model_abs)
        idx = argv.index("--entrypoint") + 1
        argv[idx] = "/bin/sh"

        proc = _run(["exec", "--", *argv], env)

        assert proc.returncode != 0

    def test_rejects_o_not_json(self, tmp_path) -> None:
        model_root, model_abs, env = self._base(tmp_path)
        argv = _cpu_argv(model_root, model_abs)
        argv[argv.index("-o") + 1] = "csv"

        proc = _run(["exec", "--", *argv], env)

        assert proc.returncode != 0

    def test_rejects_an_unknown_bench_flag(self, tmp_path) -> None:
        model_root, model_abs, env = self._base(tmp_path)
        argv = _cpu_argv(model_root, model_abs)
        argv[argv.index("-o") : argv.index("-o")] = ["--extra-flag", "1"]

        proc = _run(["exec", "--", *argv], env)

        assert proc.returncode != 0

    def test_missing_rm_is_rejected(self, tmp_path) -> None:
        model_root, model_abs, env = self._base(tmp_path)
        argv = _cpu_argv(model_root, model_abs)
        argv.remove("--rm")

        proc = _run(["exec", "--", *argv], env)

        assert proc.returncode != 0

    def test_rejects_a_runtime_hook_env_value(self, tmp_path) -> None:
        """The -e allowlist is exact-value: ROCr dlopen()s $HSA_TOOLS_LIB
        inside the rootful container, and the caller writes the model store
        the shim bind-mounts — a pattern that admits a path is root code
        execution."""
        model_root, model_abs, env = self._base(tmp_path)
        argv = _cpu_argv(model_root, model_abs)
        argv[argv.index("--entrypoint") : argv.index("--entrypoint")] = [
            "-e",
            f"HSA_TOOLS_LIB={model_root}/evil.so",
        ]

        proc = _run(["exec", "--", *argv], env)

        assert proc.returncode != 0
        assert "env not allowed" in proc.stderr

    def test_rejects_a_device_outside_the_allowed_roots(self, tmp_path) -> None:
        model_root, model_abs, env = self._base(tmp_path)
        argv = _cpu_argv(model_root, model_abs)
        argv.insert(argv.index("--rm") + 1, "--device=/dev/sda")

        proc = _run(["exec", "--", *argv], env)

        assert proc.returncode != 0

    def test_rejects_a_non_numeric_group_add(self, tmp_path) -> None:
        model_root, model_abs, env = self._base(tmp_path)
        argv = _cpu_argv(model_root, model_abs)
        argv.insert(argv.index("--rm") + 1, "--group-add=video")

        proc = _run(["exec", "--", *argv], env)

        assert proc.returncode != 0

    def test_rejects_group_add_zero(self, tmp_path) -> None:
        model_root, model_abs, env = self._base(tmp_path)
        argv = _cpu_argv(model_root, model_abs)
        argv.insert(argv.index("--rm") + 1, "--group-add=0")

        proc = _run(["exec", "--", *argv], env)

        assert proc.returncode != 0

    def test_rejects_a_second_entrypoint(self, tmp_path) -> None:
        """A repeated --entrypoint lands in the image slot and dies on the
        image pattern — nothing can re-open the pre-image flag loop."""
        model_root, model_abs, env = self._base(tmp_path)
        argv = _cpu_argv(model_root, model_abs)
        i = argv.index("--entrypoint")
        argv[i:i] = ["--entrypoint", "/usr/local/bin/llama-bench"]

        proc = _run(["exec", "--", *argv], env)

        assert proc.returncode != 0

    def test_rejects_a_flag_smuggled_after_o_json(self, tmp_path) -> None:
        """Every pair after the image is validated — -o json is not a
        terminator that stops parsing."""
        model_root, model_abs, env = self._base(tmp_path)
        argv = _cpu_argv(model_root, model_abs)
        argv += ["-m", str(model_abs)]  # a second -m is not whitelisted here

        proc = _run(["exec", "--", *argv], env)

        assert proc.returncode != 0


# ── retired verbs ────────────────────────────────────────────────────────────


class TestRetiredVerbs:
    @pytest.mark.parametrize("verb", ["run", "run-model", "sweep", "aggregate", "list"])
    def test_retired_verbs_exit_2(self, tmp_path, verb) -> None:
        model_root = _model_root(tmp_path)
        bindir = _stub_bin(tmp_path, model_root)
        env = _env(tmp_path, bindir, model_root)

        proc = _run([verb], env)

        assert proc.returncode == 2, proc.stderr


# ── telemetry (ported unchanged from Phase 1) ───────────────────────────────


class TestTelemetrySampler:
    """Static guards on ``hal0-benchctl telemetry`` — unchanged since Phase 1.

    Not executed: the sampler's output dir is the hardcoded
    ``/var/lib/hal0/benchmarks`` and making it env-overridable would widen a
    root-run seam that ``chown -R``s whatever it is pointed at. These assert
    on the shipped source instead, which is still enough to catch a
    reintroduction of the zero-filling / whole-box-hwmon behaviour.
    """

    @pytest.fixture(scope="class")
    def sampler(self) -> str:
        text = BENCHCTL.read_text()
        block = re.search(r"^  telemetry\)(.*?)^\s{4}esac", text, re.S | re.M)
        assert block, "telemetry verb not found in hal0-benchctl"
        return "\n".join(
            line for line in block.group(1).splitlines() if not line.lstrip().startswith("#")
        )

    def test_the_cpu_tier_skips_the_gpu_counters(self, sampler) -> None:
        assert '"$tier" != "cpu"' in sampler

    def test_the_tier_hint_is_positional_and_validated(self, sampler) -> None:
        assert 'tier="${3:-${HAL0_BENCH_TIER:-}}"' in sampler
        assert 'validate_tier "$tier"' in sampler

    def test_missing_counters_are_null_not_zero(self, sampler) -> None:
        assert "printf 'null'" in sampler
        assert 'echo "0"' not in sampler

    def test_it_does_not_read_every_hwmon_on_the_box(self, sampler) -> None:
        assert "/sys/class/hwmon/hwmon*" not in sampler
        assert "device/hwmon/hwmon*" in sampler


def test_the_shim_is_syntactically_valid() -> None:
    proc = subprocess.run(["bash", "-n", str(BENCHCTL)], capture_output=True, text=True, timeout=60)
    assert proc.returncode == 0, proc.stderr


class TestLegacyRootMount:
    """A relocated store leaves pre-relocation models under the historic
    /mnt/ai-models; both self-mounts are legitimate (closed two-root set)."""

    def test_legacy_root_mount_and_model_accepted(self, tmp_path) -> None:
        model_root = _model_root(tmp_path)
        legacy_root = tmp_path / "legacy-models"
        legacy_model = legacy_root / "tiny/tiny.gguf"
        legacy_model.parent.mkdir(parents=True)
        legacy_model.write_bytes(b"GGUF")
        argv_log = tmp_path / "podman-argv.log"
        bindir = _stub_bin(tmp_path, model_root, argv_log=argv_log)
        env = _env(tmp_path, bindir, model_root)
        env["HAL0_BENCHCTL_LEGACY_ROOT_TEST"] = "1"  # doc marker only

        argv = _cpu_argv(legacy_root, legacy_model)

        # rewrite the shim's LEGACY_MODEL_ROOT for the test via a patched copy
        patched = tmp_path / "benchctl-patched"
        patched.write_text(
            BENCHCTL.read_text().replace(
                'LEGACY_MODEL_ROOT="/mnt/ai-models"',
                f'LEGACY_MODEL_ROOT="{legacy_root}"',
            )
        )
        proc = subprocess.run(
            ["bash", str(patched), "exec", "--", *argv],
            capture_output=True,
            text=True,
            env=env,
            timeout=30,
        )
        assert proc.returncode == 0, proc.stderr

    def test_model_outside_the_mounted_root_is_rejected(self, tmp_path) -> None:
        model_root = _model_root(tmp_path)
        model_abs = _write_model(model_root)
        bindir = _stub_bin(tmp_path, model_root)
        env = _env(tmp_path, bindir, model_root)
        argv = _cpu_argv(model_root, model_abs)
        # mount the resolved root but point -m at a legacy-root path
        argv[argv.index("-m") + 1] = "/mnt/ai-models/tiny/tiny.gguf"

        proc = _run(["exec", "--", *argv], env)

        assert proc.returncode != 0
        assert "not under the mounted root" in proc.stderr
