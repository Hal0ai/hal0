"""#2037 — load-phase exit-code translation in the runner entrypoint.

``packaging/runner/rocmfpx/entrypoint.sh`` is PID 1 of every llama-server
slot container. llama-server exits 1 for every failure class, so systemd
cannot tell "doomed model, never retry" from "transient crash, retry with
backoff". The entrypoint owns the child and can see the one signal that
separates them — did ``/health`` ever answer 200 — and translates a
died-during-load into exit 64 for ``RestartPreventExitStatus=64`` to act on.

These tests run the real script with a fake server binary
(``HAL0_RUNNER_SERVER`` override) — no image build, no GPU, no root. Same
posture as the ``hal0-systemctl`` wrapper suites.
"""

from __future__ import annotations

import socket
import stat
import subprocess
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
ENTRYPOINT = REPO / "packaging" / "runner" / "rocmfpx" / "entrypoint.sh"


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


def test_signal_death_during_load_is_not_translated(tmp_path: Path) -> None:
    """A SIGKILL'd load (OOM-killer, GPU reset) may be transient — keep the
    backoff runway, propagate 128+sig untouched."""
    proc = _run_with_fake_server(
        tmp_path, "#!/bin/sh\nkill -KILL $$\n", "--port", str(_free_port())
    )
    assert proc.returncode == 137, proc.stderr


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
