"""#2037 / #2126 — load-phase exit-code translation in the runner entrypoint.

``packaging/runner/rocmfpx/entrypoint.sh`` is PID 1 of every llama-server
slot container. llama-server exits 1 for every failure class, so systemd
cannot tell "doomed model, never retry" from "transient crash, retry with
backoff". The entrypoint owns the child and can see the one signal that
separates them — did ``/health`` ever answer 200 — and translates a
died-during-load into exit 64 for ``RestartPreventExitStatus=`` to act on.

#2126 extends the same translation to the deterministic-fault signals
(SIGILL/SIGABRT/SIGSEGV) — but only before ``/health`` ever answered, which is
what makes them deterministic. SIGKILL (the OOM-killer) still propagates.

The other half of #2126 lives in this file too, because it is the same
question one layer out: ``_hal0_cpu_lane_has_runner_image`` in
``installer/lib/preflight.sh`` is a shell mirror of
``hal0.runners.cpu_lane_has_runner_image``, and the CPU-only install gate that
consumes it. The entrypoint cannot save a box whose ``cpu`` runner points at a
GPU image it does not even ship this entrypoint in — the install has to refuse
first.

These tests run the real scripts with fake binaries and documented test seams
— no image build, no GPU, no root. Same posture as the ``hal0-systemctl``
wrapper suites.
"""

from __future__ import annotations

import os
import socket
import stat
import subprocess
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
ENTRYPOINT = REPO / "packaging" / "runner" / "rocmfpx" / "entrypoint.sh"
PREFLIGHT = REPO / "installer" / "lib" / "preflight.sh"
INSTALL_SH = REPO / "installer" / "install.sh"


def _free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def _run_with_fake_server(
    tmp_path: Path, fake_body: str, *args: str
) -> subprocess.CompletedProcess[str]:
    fake = tmp_path / "fake-llama-server"
    fake.write_text(fake_body)
    fake.chmod(fake.stat().st_mode | stat.S_IXUSR)
    return subprocess.run(
        [str(ENTRYPOINT), *args],
        env={"PATH": "/usr/bin:/bin", "HAL0_RUNNER_SERVER": str(fake)},
        capture_output=True,
        text=True,
        check=False,
        timeout=60,
    )


def test_death_before_health_translates_to_64(tmp_path: Path) -> None:
    """A server that dies before /health ever answers (corrupt GGUF, bogus
    flag — llama-server says exit 1 for all of them) must surface as 64."""
    proc = _run_with_fake_server(tmp_path, "#!/bin/sh\nexit 1\n", "--port", str(_free_port()))
    assert proc.returncode == 64, proc.stderr
    assert "before" in proc.stderr  # a diagnostic, not a silent remap


def test_clean_exit_is_not_translated(tmp_path: Path) -> None:
    """Exit 0 during load (e.g. --help paths) is not a doomed model."""
    proc = _run_with_fake_server(tmp_path, "#!/bin/sh\nexit 0\n", "--port", str(_free_port()))
    assert proc.returncode == 0, proc.stderr


def test_sigkill_during_load_is_not_translated(tmp_path: Path) -> None:
    """A SIGKILL'd load is the OOM-killer (or a teardown) and may well be
    transient — keep the backoff runway, propagate 128+sig untouched.

    This is the line #2126 does NOT cross: making every signal death terminal
    would turn one memory-pressure spike into a permanently dead slot."""
    proc = _run_with_fake_server(
        tmp_path, "#!/bin/sh\nkill -KILL $$\n", "--port", str(_free_port())
    )
    assert proc.returncode == 137, proc.stderr


def test_sigill_during_load_translates_to_64_and_names_the_mismatch(tmp_path: Path) -> None:
    """#2126: SIGILL means this binary carries opcodes this CPU cannot run.

    That is an image/hardware mismatch — restarting reproduces it byte for
    byte — so it must go terminal with a diagnostic that says so, not burn
    the restart ramp while the slot reads ``warming`` forever.
    """
    proc = _run_with_fake_server(tmp_path, "#!/bin/sh\nkill -ILL $$\n", "--port", str(_free_port()))
    assert proc.returncode == 64, proc.stderr
    assert "SIGILL" in proc.stderr
    # …and says WHAT to look at, not just which signal fired.
    assert "image/hardware mismatch" in proc.stderr


def test_sigsegv_during_load_translates_to_64(tmp_path: Path) -> None:
    """A load-phase SIGSEGV is #1790's shape (stock llama.cpp meeting a
    ROCmFPX quant's custom tensor type ids) — equally deterministic."""
    proc = _run_with_fake_server(
        tmp_path, "#!/bin/sh\nkill -SEGV $$\n", "--port", str(_free_port())
    )
    assert proc.returncode == 64, proc.stderr
    assert "SIGSEGV" in proc.stderr


def test_sigabrt_during_load_translates_to_64(tmp_path: Path) -> None:
    proc = _run_with_fake_server(
        tmp_path, "#!/bin/sh\nkill -ABRT $$\n", "--port", str(_free_port())
    )
    assert proc.returncode == 64, proc.stderr
    assert "SIGABRT" in proc.stderr


def test_sigsegv_after_health_keeps_its_own_code(tmp_path: Path) -> None:
    """The load-phase restriction is what makes the fault deterministic. Once
    /health has answered, a segfault is a SERVING failure — it may be one bad
    request — so it keeps 139 and the restart runway."""
    port = _free_port()
    fake_body = (
        "#!/usr/bin/env python3\n"
        "import http.server, os, signal, sys, threading, time\n"
        "args = sys.argv[1:]\n"
        "port = int(args[args.index('--port') + 1])\n"
        "class H(http.server.BaseHTTPRequestHandler):\n"
        "    def do_GET(self):\n"
        "        self.send_response(200)\n"
        "        self.end_headers()\n"
        "    def log_message(self, *a):\n"
        "        pass\n"
        "srv = http.server.HTTPServer(('127.0.0.1', port), H)\n"
        "threading.Thread(target=srv.serve_forever, daemon=True).start()\n"
        "time.sleep(3)\n"
        "os.kill(os.getpid(), signal.SIGSEGV)\n"
    )
    proc = _run_with_fake_server(tmp_path, fake_body, "--port", str(port))
    assert proc.returncode == 139, proc.stderr


def test_crash_after_health_propagates_real_code(tmp_path: Path) -> None:
    """Once /health has answered 200 the model loaded fine; a later crash is
    a serving failure and keeps its own exit code (and the restart ramp)."""
    port = _free_port()
    fake_body = (
        "#!/usr/bin/env python3\n"
        "import http.server, sys, threading, time\n"
        "args = sys.argv[1:]\n"
        "port = int(args[args.index('--port') + 1])\n"
        "class H(http.server.BaseHTTPRequestHandler):\n"
        "    def do_GET(self):\n"
        "        self.send_response(200)\n"
        "        self.end_headers()\n"
        "    def log_message(self, *a):\n"
        "        pass\n"
        "srv = http.server.HTTPServer(('127.0.0.1', port), H)\n"
        "threading.Thread(target=srv.serve_forever, daemon=True).start()\n"
        "time.sleep(3)\n"
        "sys.exit(7)\n"
    )
    proc = _run_with_fake_server(tmp_path, fake_body, "--port", str(port))
    assert proc.returncode == 7, proc.stderr


def test_port_equals_form_is_parsed(tmp_path: Path) -> None:
    """``--port=NNNN`` single-token form: the template renders the two-token
    form, but operator-edited profile flags may not. A missed parse would
    poll the 8080 default, never see /health, and mistranslate the post-load
    exit below into 64."""
    port = _free_port()
    fake_body = (
        "#!/usr/bin/env python3\n"
        "import http.server, sys, threading, time\n"
        "port = int(\n"
        "    [a for a in sys.argv[1:] if a.startswith('--port=')][0].split('=', 1)[1]\n"
        ")\n"
        "class H(http.server.BaseHTTPRequestHandler):\n"
        "    def do_GET(self):\n"
        "        self.send_response(200)\n"
        "        self.end_headers()\n"
        "    def log_message(self, *a):\n"
        "        pass\n"
        "srv = http.server.HTTPServer(('127.0.0.1', port), H)\n"
        "threading.Thread(target=srv.serve_forever, daemon=True).start()\n"
        "time.sleep(3)\n"
        "sys.exit(7)\n"
    )
    proc = _run_with_fake_server(tmp_path, fake_body, f"--port={port}")
    assert proc.returncode == 7, proc.stderr


def test_gpu_preflight_still_refuses_with_78(tmp_path: Path) -> None:
    """#1936's device-less GPU gate must survive the supervision rewrite."""
    fake = tmp_path / "fake-llama-server"
    fake.write_text("#!/bin/sh\nexit 0\n")
    fake.chmod(fake.stat().st_mode | stat.S_IXUSR)
    proc = subprocess.run(
        [str(ENTRYPOINT), "-ngl", "99", "--port", str(_free_port())],
        env={
            "PATH": "/usr/bin:/bin",
            "HAL0_RUNNER_SERVER": str(fake),
            # force the no-device branch even on a GPU dev box
            "HAL0_RUNNER_DEV_ROOT": str(tmp_path),
        },
        capture_output=True,
        text=True,
        check=False,
        timeout=30,
    )
    assert proc.returncode == 78, proc.stderr


# ── #2126: the CPU-lane runner-image gate ───────────────────────────────────


def _run_preflight(snippet: str, **env_overrides: str) -> subprocess.CompletedProcess[str]:
    """Source preflight.sh and run ``snippet``, returning the whole process.

    ``set -euo pipefail`` mirrors install.sh, so this also proves the new
    functions are safe to source there. The seam vars are stripped from the
    inherited environment unless a test sets them, so the answer never depends
    on the box running the suite.
    """
    script = f"set -euo pipefail\nsource {PREFLIGHT!s}\n{snippet}\n"
    env = {
        k: v
        for k, v in os.environ.items()
        if k
        not in (
            "HAL0_CPU_RUNNER_IMAGE_OVERRIDE",
            "HAL0_RUNNERS_PY_OVERRIDE",
            "HAL0_TOOLBOX_IMAGE_CPU",
        )
    }
    env.update(env_overrides)
    return subprocess.run(
        ["bash", "-c", script], env=env, capture_output=True, text=True, timeout=60
    )


_ASK = "if _hal0_cpu_lane_has_runner_image; then echo yes; else echo no; fi"


def test_the_cpu_lane_mirror_agrees_with_the_python_predicate() -> None:
    """``_hal0_cpu_lane_has_runner_image`` is a shell RE-IMPLEMENTATION of
    ``hal0.runners.cpu_lane_has_runner_image`` (preflight runs before hal0 is
    pip-installed, so it cannot just call it). Two implementations of one
    predicate drift; this is the tripwire — and the one that fires the day
    somebody wires the ``cpu`` runner to a real image and forgets the mirror.
    """
    from hal0.runners import cpu_lane_has_runner_image

    proc = _run_preflight(_ASK)
    shell_says = proc.stdout.strip() == "yes"
    assert shell_says is cpu_lane_has_runner_image(), (
        "the shell mirror and the Python predicate disagree about whether this "
        f"checkout's `cpu` runner has a real CPU image (shell={shell_says})"
    )


def test_the_cpu_lane_has_no_image_in_this_checkout() -> None:
    """The premise of #2126, pinned so the fix's blast radius stays visible:
    the ``cpu`` runner is still the Vulkan GPU toolbox. When somebody
    publishes a CPU toolbox and wires it, this is the test that says "the
    install gate just turned itself off" — the intended behaviour, but it
    should be acknowledged here rather than discovered on a box.
    """
    from hal0.config.schema import FALLBACK_VULKAN_IMAGE
    from hal0.runners import RUNNER_IMAGES, cpu_lane_has_runner_image

    assert RUNNER_IMAGES["cpu"].image == FALLBACK_VULKAN_IMAGE
    assert RUNNER_IMAGES["cpu"].manifest_key is None
    assert cpu_lane_has_runner_image() is False


def test_the_cpu_lane_mirror_honours_the_env_override() -> None:
    """``HAL0_TOOLBOX_IMAGE_CPU`` is tier 1 of ``resolve_runner_image`` and the
    single escape hatch the gate must not close: an operator who built their
    own CPU llama-server names it and the install proceeds."""
    proc = _run_preflight(_ASK, HAL0_TOOLBOX_IMAGE_CPU="ghcr.io/example/llama-cpu:v1")
    assert proc.stdout.strip() == "yes", proc.stderr


def test_the_cpu_lane_mirror_reads_a_repointed_registry(tmp_path: Path) -> None:
    """The gate lifts on its own the day the registry entry is repointed — no
    edit in preflight.sh, no second place to remember."""
    fake = tmp_path / "runners.py"
    fake.write_text(
        "RUNNER_IMAGES = {\n"
        '    "cpu": Runner(\n'
        "        # a comment ahead of the key positional\n"
        '        "cpu",\n'
        '        "ghcr.io/hal0ai/hal0-toolbox-cpu:v1",\n'
        '        "llama-server",\n'
        "    ),\n"
        "}\n"
    )
    proc = _run_preflight(_ASK, HAL0_RUNNERS_PY_OVERRIDE=str(fake))
    assert proc.stdout.strip() == "yes", proc.stderr


def test_the_cpu_lane_mirror_fails_closed_on_an_unreadable_registry(tmp_path: Path) -> None:
    """Opposite asymmetry to the Vulkan mirror's: a false "yes" ships #2126's
    broken box, a false "no" refuses an install with the override that reopens
    it printed on screen. So anything unparseable answers "no"."""
    proc = _run_preflight(_ASK, HAL0_RUNNERS_PY_OVERRIDE=str(tmp_path / "gone.py"))
    assert proc.stdout.strip() == "no", proc.stderr


def test_cpu_only_image_gate_refuses_and_says_why() -> None:
    """The gate itself: non-zero, and the message names the fault (SIGILL /
    132), the lie it replaces ("warming" forever), and the override."""
    proc = _run_preflight("if _cpu_only_image_gate; then echo pass; else echo refused; fi")
    assert proc.stdout.strip() == "refused", proc.stdout
    assert "SIGILL" in proc.stderr
    assert "132" in proc.stderr
    assert "warming" in proc.stderr
    assert "HAL0_TOOLBOX_IMAGE_CPU" in proc.stderr
    assert "#2126" in proc.stderr
    # It must not repeat the promise that got #2126's reporter here.
    assert "To install CPU-only anyway" not in proc.stderr


def test_cpu_only_image_gate_passes_once_an_image_exists() -> None:
    proc = _run_preflight(
        "if _cpu_only_image_gate; then echo pass; else echo refused; fi",
        HAL0_TOOLBOX_IMAGE_CPU="ghcr.io/example/llama-cpu:v1",
    )
    assert proc.stdout.strip() == "pass", proc.stderr


def test_cpu_only_remedy_is_honest_when_there_is_no_cpu_image() -> None:
    """install.sh's GPU-gate refusals used to say "To install CPU-only anyway,
    re-run with HAL0_ALLOW_CPU_ONLY=1." unconditionally. Following that
    printed remedy is exactly how #2126 happened, so it may not be printed
    while the CPU lane has no image."""
    proc = _run_preflight("_cpu_only_remedy_lines")
    assert "To install CPU-only anyway" not in proc.stderr
    assert "no working CPU-only path" in proc.stderr
    assert "HAL0_TOOLBOX_IMAGE_CPU" in proc.stderr


def test_cpu_only_remedy_returns_when_a_cpu_image_exists() -> None:
    proc = _run_preflight(
        "_cpu_only_remedy_lines", HAL0_TOOLBOX_IMAGE_CPU="ghcr.io/example/llama-cpu:v1"
    )
    assert "To install CPU-only anyway, re-run with HAL0_ALLOW_CPU_ONLY=1." in proc.stderr


def test_every_cpu_only_optin_in_install_sh_runs_the_gate_first() -> None:
    """Static-text pin (same technique as test_platform_gate_hardening.py):
    each branch that proceeds CPU-only must call ``_cpu_only_image_gate``
    BEFORE the reassuring warn, and each refusal must use the honest remedy
    helper rather than a hard-coded HAL0_ALLOW_CPU_ONLY line."""
    text = INSTALL_SH.read_text(encoding="utf-8")
    lines = text.splitlines()

    optins = [i for i, line in enumerate(lines) if 'HAL0_ALLOW_CPU_ONLY:-0}" == "1"' in line]
    assert len(optins) == 2, "the CPU-only env opt-in branch count changed"

    proceeds = [
        i
        for i, line in enumerate(lines)
        if "proceeding CPU-only" in line or "confirmed at the prompt" in line
    ]
    assert len(proceeds) == 4, "the CPU-only proceed messages changed shape"
    for i in proceeds:
        assert "_cpu_only_image_gate || exit 1" in lines[i - 1], (
            f"install.sh:{i + 1} announces a CPU-only install without gating on "
            "there being a CPU runner image first (#2126)"
        )

    # The unconditional remedy promise is gone from install.sh entirely, and
    # both GPU-gate refusals go through the honest helper instead. Counted as
    # bare call lines, not substring hits, so a prose mention of the helper in
    # a comment does not satisfy the pin.
    assert "To install CPU-only anyway" not in text
    calls = [line for line in lines if line.strip() == "_cpu_only_remedy_lines"]
    assert len(calls) == 2, "both GPU-gate refusals must print the honest CPU-only remedy"
