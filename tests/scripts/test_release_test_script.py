"""Contract tests for scripts/release-test.sh (tier gamma release gate).

Two regressions are pinned here:

* #2262 — ``remote_slot_create`` appended to ``CREATED_SLOTS`` but every
  caller captured it with ``SLOT="$(remote_slot_create …)"``. Command
  substitution runs the function in a subshell, so the append never reached
  the parent shell, the EXIT-trap ``cleanup()`` saw an empty array, and every
  sweep left its test slots loaded on the box.
* #2263 — the remote CLI fell back to a stale ``/opt/hal0/.venv/bin/hal0``.
  The installer's FHS venv is ``/usr/lib/hal0/venv`` (installer/install.sh
  ``VENV_DIR``); the script must prefer that binary, fall back to ``PATH``,
  honour an ``HAL0_TEST_BIN`` override, and log the resolved ``--version``.

The REAL script runs against a stubbed ``ssh`` on a hermetic PATH: the stub
records every remote command and answers just enough of the CLI surface for
each row to reach its slot create/load step. No host is contacted.
"""

from __future__ import annotations

import json
import os
import shutil
import stat
import subprocess
from pathlib import Path

import pytest

_REPO_ROOT = Path(__file__).resolve().parents[2]
_SCRIPT = _REPO_ROOT / "scripts" / "release-test.sh"
_FHS_BIN = "/usr/lib/hal0/venv/bin/hal0"
_PREFIX = "ci-h-contract"

# The stub `ssh`: logs the remote command, then fakes a minimal hal0 box.
_SSH_STUB = r"""#!/usr/bin/env python3
import json, os, subprocess, sys

args = sys.argv[1:]
target = os.environ["STUB_TARGET"]
cmd = " ".join(args[args.index(target) + 1:])
with open(os.environ["STUB_LOG"], "a", encoding="utf-8") as fh:
    fh.write(json.dumps(cmd) + "\n")

if "command -v hal0" in cmd:
    # Execute the binary-resolution probe for real, with the fake remote PATH.
    env = dict(os.environ, PATH=os.environ["STUB_REMOTE_PATH"])
    sys.exit(subprocess.run(["sh", "-c", cmd], env=env).returncode)
if cmd.endswith(" --version"):
    if os.environ.get("STUB_VERSION_FAIL"):
        print("sh: hal0: not found", file=sys.stderr)
        sys.exit(127)
    print("hal0 9.9.9-contract")
    sys.exit(0)
if "model list --json" in cmd:
    print(json.dumps({"models": [
        {"id": "llm-model", "type": "llm", "installed": True},
        {"id": "stt-model", "type": "transcription", "installed": True},
        {"id": "tts-model", "type": "tts", "installed": True},
    ]}))
    sys.exit(0)
if "slot list --json" in cmd:
    print("[]")
    sys.exit(0)
if cmd.startswith("echo "):
    print("http://127.0.0.1:1")
    sys.exit(0)
if "/dev/accel/accel0" in cmd:
    sys.exit(1)
if "/v1/models" in cmd:
    print("1")
    sys.exit(0)
fail = os.environ.get("STUB_FAIL_SUBSTR")
if fail and fail in cmd:
    sys.exit(1)
sys.exit(0)
"""

# macOS `date` has no %N; the script only ever calls `date +%s%N`.
_DATE_STUB = "#!/usr/bin/env python3\nimport time\nprint(time.time_ns())\n"


def _write_exec(path: Path, body: str) -> None:
    path.write_text(body, encoding="utf-8")
    path.chmod(path.stat().st_mode | stat.S_IEXEC | stat.S_IXGRP | stat.S_IXOTH)


def _run(tmp_path: Path, **extra_env: str) -> tuple[subprocess.CompletedProcess[str], list[str]]:
    # A throwaway tree: REPO_ROOT resolves to tmp_path, so the report and the
    # manifest.json read by manifest_digest() never touch the real checkout.
    tree = tmp_path / "tree"
    (tree / "scripts").mkdir(parents=True)
    shutil.copy2(_SCRIPT, tree / "scripts" / "release-test.sh")
    shutil.copy2(_REPO_ROOT / "manifest.json", tree / "manifest.json")

    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    _write_exec(bin_dir / "ssh", _SSH_STUB)
    _write_exec(bin_dir / "date", _DATE_STUB)

    key = tmp_path / "id_test"
    key.write_text("not-a-key\n", encoding="utf-8")
    log = tmp_path / "ssh.log"

    env = {k: v for k, v in os.environ.items() if not k.startswith(("HAL0_TEST_", "STUB_"))}
    env.update(
        PATH=f"{bin_dir}:{os.environ['PATH']}",
        HAL0_TEST_HOST="test-box.invalid",
        HAL0_TEST_USER="tester",
        HAL0_TEST_SSH_KEY=str(key),
        HAL0_TEST_PREFIX=_PREFIX,
        STUB_TARGET="tester@test-box.invalid",
        STUB_LOG=str(log),
        STUB_REMOTE_PATH="/usr/bin:/bin",
    )
    env.update(extra_env)
    proc = subprocess.run(
        ["bash", str(tree / "scripts" / "release-test.sh")],
        cwd=tree,
        env=env,
        capture_output=True,
        text=True,
        timeout=120,
    )
    cmds = [json.loads(line) for line in log.read_text(encoding="utf-8").splitlines()]
    return proc, cmds


def _manifest_has(name: str) -> bool:
    data = json.loads((_REPO_ROOT / "manifest.json").read_text(encoding="utf-8"))
    return bool(data.get("toolbox_images", {}).get(name, {}).get("digest"))


# ── #2262: cleanup sees slots created through the row call sites ────────────


@pytest.mark.parametrize(
    ("fail_substr", "expected_rc"),
    [
        (None, 0),  # every row passes → cleanup on a normal exit
        ("slot load", 1),  # every slot load fails → cleanup on a failing exit
    ],
)
def test_cleanup_tears_down_every_created_slot(
    tmp_path: Path, fail_substr: str | None, expected_rc: int
) -> None:
    extra = {"HAL0_TEST_BIN": "/opt/test/hal0"}
    if fail_substr:
        extra["STUB_FAIL_SUBSTR"] = fail_substr
    proc, cmds = _run(tmp_path, **extra)
    assert proc.returncode == expected_rc, proc.stdout + proc.stderr

    created = [c.split()[3] for c in cmds if c.startswith("/opt/test/hal0 slot create ")]
    expected = [
        f"{_PREFIX}-{suffix}"
        for suffix in ("vulkan", "rocm", "moonshine", "kokoro")
        if _manifest_has(suffix)
    ]
    assert created == expected, cmds
    assert created, "manifest pins no slot-creating row; the test exercises nothing"

    assert "── Cleanup" in proc.stdout
    for slot in created:
        # Teardown happens after the slot's own create, from the EXIT trap.
        create_at = cmds.index(next(c for c in cmds if f"slot create {slot} " in c))
        assert f"/opt/test/hal0 slot delete {slot} --force 2>/dev/null || true" in cmds[create_at:]
        assert f"/opt/test/hal0 slot unload {slot} 2>/dev/null || true" in cmds[create_at:]
        assert f"cleaned up {slot}" in proc.stdout


def test_slot_create_is_never_captured_in_a_subshell() -> None:
    """A `$(remote_slot_create …)` capture silently re-breaks cleanup (#2262)."""
    text = _SCRIPT.read_text(encoding="utf-8")
    code = [ln for ln in text.splitlines() if not ln.lstrip().startswith("#")]
    offenders = [ln for ln in code if "$(remote_slot_create" in ln]
    assert offenders == []
    # The seeded-slot tracker has the same hazard; it must stay a parent-shell append.
    assert not [ln for ln in code if "LOADED_SEEDED_SLOTS+=" in ln and "$(" in ln]


# ── #2263: remote binary resolution ─────────────────────────────────────────


def test_default_probe_prefers_fhs_venv_then_path(tmp_path: Path) -> None:
    fake_remote = tmp_path / "remote-path"
    fake_remote.mkdir()
    _write_exec(fake_remote / "hal0", "#!/bin/sh\nexit 0\n")

    proc, cmds = _run(tmp_path, STUB_REMOTE_PATH=f"{fake_remote}:/usr/bin:/bin")

    probes = [c for c in cmds if "command -v hal0" in c]
    assert len(probes) == 1
    probe = probes[0]
    assert "/opt/hal0" not in probe
    assert probe.index(_FHS_BIN) < probe.index("command -v hal0")

    if Path(_FHS_BIN).exists():
        pytest.skip(f"{_FHS_BIN} exists on this host; PATH fallback not observable")
    # FHS venv absent on this (fake) remote → the PATH binary is resolved and
    # used for the version probe and every subsequent CLI call.
    resolved = f"{fake_remote}/hal0"
    assert f"remote hal0 binary: {resolved}" in proc.stdout
    assert f"{resolved} --version" in cmds
    assert "remote hal0 version: hal0 9.9.9-contract" in proc.stdout
    assert any(c.startswith(f"{resolved} slot create ") for c in cmds)


def test_probe_picks_fhs_venv_over_path(tmp_path: Path) -> None:
    """Run the probe itself with the venv path pointed at a fake install."""
    _proc, cmds = _run(tmp_path, HAL0_TEST_BIN="")  # empty == unset
    probe = next(c for c in cmds if "command -v hal0" in c)

    venv_bin = tmp_path / "venv" / "bin"
    venv_bin.mkdir(parents=True)
    _write_exec(venv_bin / "hal0", "#!/bin/sh\nexit 0\n")
    path_dir = tmp_path / "on-path"
    path_dir.mkdir()
    _write_exec(path_dir / "hal0", "#!/bin/sh\nexit 0\n")
    env = {"PATH": f"{path_dir}:/usr/bin:/bin"}

    both = probe.replace(_FHS_BIN, str(venv_bin / "hal0"))
    out = subprocess.run(["sh", "-c", both], env=env, capture_output=True, text=True)
    assert out.stdout.strip() == str(venv_bin / "hal0")

    no_venv = probe.replace(_FHS_BIN, str(tmp_path / "missing" / "hal0"))
    out = subprocess.run(["sh", "-c", no_venv], env=env, capture_output=True, text=True)
    assert out.stdout.strip() == str(path_dir / "hal0")

    neither = {"PATH": "/usr/bin:/bin"}
    out = subprocess.run(["sh", "-c", no_venv], env=neither, capture_output=True, text=True)
    assert out.stdout.strip() == "hal0"


def test_hal0_test_bin_override_skips_probe(tmp_path: Path) -> None:
    proc, cmds = _run(tmp_path, HAL0_TEST_BIN="/custom/bin/hal0")
    assert proc.returncode == 0, proc.stdout + proc.stderr
    assert not [c for c in cmds if "command -v hal0" in c]
    assert "remote hal0 binary: /custom/bin/hal0" in proc.stdout
    assert "/custom/bin/hal0 --version" in cmds
    hal0_calls = [c for c in cmds if " slot " in c or " model " in c or " update " in c]
    assert hal0_calls
    assert all(c.startswith("/custom/bin/hal0 ") for c in hal0_calls), hal0_calls


def test_unrunnable_cli_stops_at_preflight(tmp_path: Path) -> None:
    proc, cmds = _run(tmp_path, HAL0_TEST_BIN="/nope/hal0", STUB_VERSION_FAIL="1")
    assert proc.returncode == 2, proc.stdout + proc.stderr
    assert "remote hal0 CLI not runnable" in proc.stderr
    assert not [c for c in cmds if "slot create" in c]
