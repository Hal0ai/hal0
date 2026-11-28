"""The install-matrix orchestrator (scripts/install-matrix/run-shape.sh).

Real bash under subprocess. Every run puts a FAKE ``ssh`` first on PATH: it
records its arguments and plays a small Proxmox host and guest from a state
directory. No real host is ever contacted; the pct/pveam/pvesm/pvesh names are
stubbed to fail loudly in case a code path ran them locally.
"""

from __future__ import annotations

import contextlib
import json
import os
import re
import signal
import stat
import subprocess
import time
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[2]
RUN_SHAPE = REPO / "scripts" / "install-matrix" / "run-shape.sh"
WRAPPER = REPO / "scripts" / "fresh-test-ct.sh"

FAKE_SSH = r"""#!/usr/bin/env bash
# Fake Proxmox host + guest. State lives in $FAKE_STATE.
printf '%s\n' "$*" >> "$FAKE_STATE/ssh.log"
printf '%s %s\n' "$(date +%s.%N)" "${*: -1}" >> "$FAKE_STATE/ssh.times"
cmd="${*: -1}"
plain="${cmd//\\/}"
# An action arrives as `{ pct config V | grep ... ; } && <action>`: run the
# guard for real against the fake guest's config, then answer the action.
if [[ "$cmd" == "{ pct config "* ]]; then
  guard="${cmd%%; \} && *}; }"
  vm="${cmd#\{ pct config }"; vm="${vm%% *}"
  [[ -e "$FAKE_STATE/present" ]] || exit 1
  conf="$(printf 'hostname: hal0-mx-%s\ntags: hal0-matrix\ncores: 8\n' "$vm"
    [[ -z "${FAKE_LOCK:-}" ]] || echo 'lock: create'
    [[ ! -s "$FAKE_STATE/description" ]] || printf 'description: %s\n' "$(cat "$FAKE_STATE/description")")"
  GUARD="$guard" CONF="$conf" bash -c 'pct() { printf "%s\n" "$CONF"; }; export -f pct; exec bash -c "$GUARD"' || exit 1
  cmd="${cmd#*; \} && }"
  plain="${cmd//\\/}"
fi
if [[ " $* " != *" -n "* && " $* " != *" -tt "* ]]; then
  if [[ "$cmd" == *"hal0-src.tgz"* ]]; then cat > "$FAKE_STATE/tree.tgz"; else cat > "$FAKE_STATE/stdin.dump"; fi
fi
case "$cmd" in
  "free -g"*)
    # FAKE_MEM_AFTER: what every read after the first one shows (the memory
    # guard reads first, the watchdog after it). FAKE_MEM_FAIL_AFTER=N: reads
    # after the Nth cannot be made. FAKE_MEM_FAIL_ON=N: only the Nth fails.
    n=$(cat "$FAKE_STATE/free-reads" 2>/dev/null || echo 0); n=$((n + 1))
    echo "$n" > "$FAKE_STATE/free-reads"
    if [[ -n "${FAKE_MEM_FAIL_AFTER:-}" && "$n" -gt "$FAKE_MEM_FAIL_AFTER" ]]; then exit 255; fi
    [[ "${FAKE_MEM_FAIL_ON:-0}" != "$n" ]] || exit 255
    mem="${FAKE_MEM:-100}"
    # FAKE_SUM_SLEEP: memory is low from the moment the summary pull starts.
    [[ -z "${FAKE_SUM_SLEEP:-}" || ! -e "$FAKE_STATE/sum-started" ]] || mem=5
    if [[ -n "${FAKE_MEM_AFTER:-}" && "$n" -gt 1 ]]; then
      mem="$FAKE_MEM_AFTER"
      [[ -e "$FAKE_STATE/low-read-at" ]] || date +%s.%N > "$FAKE_STATE/low-read-at"
    fi
    printf '              total used free shared buff/cache available\nMem: 125 20 60 1 45 %s\n' "$mem"
    [[ -z "${FAKE_GPU_MEM:-}" ]] || printf 'gpu %s\n' "$FAKE_GPU_MEM"
    exit 0 ;;
  "pveam list local")
    printf 'Volume Size\nlocal:vztmpl/ubuntu-26.04-standard_26.04-1_amd64.tar.zst 1\nlocal:vztmpl/ubuntu-24.04-standard_24.04-1_amd64.tar.zst 1\n'
    exit 0 ;;
  "pvesm status"*)
    printf 'Name Type Status Total Used Available %%\nlocal dir active 1000000000 1 %s 1.0%%\n' "${FAKE_STORAGE_AVAIL:-500000000}"
    exit 0 ;;
  "pvesh get"*)
    if [[ -e "$FAKE_STATE/range-guest" ]]; then
      printf '[{"vmid": %s, "type": "lxc", "name": "other"}]\n' "$(cat "$FAKE_STATE/range-guest")"
    else echo '[]'; fi
    exit 0 ;;
  "if test -e /etc/pve/lxc/"*)
    # FAKE_PRESENT_AFTER=N: someone else's guest appears after N presence reads.
    if [[ -n "${FAKE_PRESENT_AFTER:-}" ]]; then
      n=$(cat "$FAKE_STATE/presence-reads" 2>/dev/null || echo 0); n=$((n + 1))
      echo "$n" > "$FAKE_STATE/presence-reads"
      [[ "$n" -le "$FAKE_PRESENT_AFTER" ]] || touch "$FAKE_STATE/present"
    fi
    if [[ -e "$FAKE_STATE/present" ]]; then echo present; else echo absent; fi
    exit 0 ;;
  "bash -s -- gpu"*)
    printf 'dev /dev/dri/renderD128,gid=44\nmajor 226\n'; exit 0 ;;
  "pct config "*)
    [[ -e "$FAKE_STATE/present" ]] || exit 2
    vm="${cmd#pct config }"
    printf 'hostname: hal0-mx-%s\ntags: hal0-matrix\ncores: 8\n' "${vm%% *}"
    [[ -z "${FAKE_LOCK:-}" ]] || echo 'lock: create'
    [[ ! -s "$FAKE_STATE/description" ]] || printf 'description: %s\n' "$(cat "$FAKE_STATE/description")"
    exit 0 ;;
  "pct create "*)
    [[ -z "${FAKE_CREATE_FAILS:-}" ]] || exit 1
    touch "$FAKE_STATE/create-started"
    [[ -z "${FAKE_CREATE_SLEEP:-}" ]] || sleep "$FAKE_CREATE_SLEEP"
    # The description this run asked for, as `pct config` would show it.
    d="${cmd#*--description }"; d="${d%% --*}"; d="${d//\\/}"
    [[ "$cmd" == *"--description "* ]] && printf '%s' "$d" > "$FAKE_STATE/description"
    # FAKE_DESC_ODD: the description reads back in a form the harness does not
    # know (its second line is the bare nonce). FAKE_CREATE_RC: the create made
    # the guest but its ssh call returned this status.
    [[ -z "${FAKE_DESC_ODD:-}" ]] || printf 'hal0-matrix run\n%s' "${d##* }" > "$FAKE_STATE/description"
    touch "$FAKE_STATE/present"
    [[ -z "${FAKE_CREATE_RC:-}" ]] || exit "$FAKE_CREATE_RC"
    exit 0 ;;
  *"pgrep -f"*)
    # FAKE_PGREP_BUSY_READS=N: the first N reads say a pct create is running.
    n=$(cat "$FAKE_STATE/pgrep-reads" 2>/dev/null || echo 0); n=$((n + 1))
    echo "$n" > "$FAKE_STATE/pgrep-reads"
    if [[ "$n" -le "${FAKE_PGREP_BUSY_READS:-0}" ]]; then echo busy; else echo idle; fi
    exit 0 ;;
  "pct start "*)
    [[ -z "${FAKE_START_HANGS:-}" ]] || exec sleep 10
    exit 0 ;;
  *"pct destroy "*)
    touch "$FAKE_STATE/destroy-started"
    [[ -z "${FAKE_DESTROY_HANGS:-}" ]] || exec sleep 10
    [[ -z "${FAKE_DESTROY_SLEEP:-}" ]] || sleep "$FAKE_DESTROY_SLEEP"
    [[ -z "${FAKE_DESTROY_FAILS:-}" ]] || exit 1
    rm -f "$FAKE_STATE/present"; exit 0 ;;
esac
case "$plain" in
  *"getent hosts"*) exit 0 ;;
  *"command -v curl"*) exit 0 ;;
  *"cat > "*) exit 0 ;;
  *"mkdir -p /root/hal0-src"*) exit 0 ;;
  *"-o /root/install.sh"*) exit 0 ;;
  *"collect.sh pre"*)
    [[ -z "${FAKE_BIG_PRE:-}" ]] || head -c "$FAKE_BIG_PRE" /dev/zero | tr '\0' x
    printf 'disk_used_bytes=1000\nnet_rx_bytes=100\n'; exit 0 ;;
  *"collect.sh post"*)
    printf 'disk_used_bytes=5000\nnet_rx_bytes=900\nslot_count=2\nslots=a,b\nfailed_units=\ninstall_log=/var/log/hal0/install-1.log\nfailure_report=/tmp/hal0-install-report-1.txt\n'
    exit 0 ;;
  *"collect.sh probe"*)
    date +%s.%N > "$FAKE_STATE/probe-started"
    [[ -z "${FAKE_PROBE_SLEEP:-}" ]] || exec sleep "$FAKE_PROBE_SLEEP"
    printf 'api_health_s=3\ndoctor_ok=10\ndoctor_warn=1\ndoctor_critical=0\ndoctor_exit=0\nchat_ok=1\nchat_first_token_s=0.5\nchat_model=m\nmemory_write_ok=1\nmemory_read_ok=1\nhermes_reply_ok=1\nhermes_reply_s=2\n'
    exit 0 ;;
  *"cat /var/log/hal0/install-1.log"*)
    [[ -z "${FAKE_BIG_LOG:-}" ]] || head -c "$FAKE_BIG_LOG" /dev/zero | tr '\0' x
    echo "the install log"; exit 0 ;;
  *"cat /tmp/hal0-install-report-1.txt"*) echo "the failure report"; exit 0 ;;
  *"cat /root/hal0-summary.json"*)
    touch "$FAKE_STATE/sum-started"
    [[ -z "${FAKE_SUM_SLEEP:-}" ]] || exec sleep "$FAKE_SUM_SLEEP"
    echo '{"hal0_version": "9.9.9", "elapsed_seconds": 7, "step_durations_seconds": {"Pre-flight checks": 2}, "warnings": 0, "errors": 0}'
    exit 0 ;;
  *"hal0 uninstall"*) exit 0 ;;
  *"list-unit-files"*) echo; exit 0 ;;
  *"summary-json"*)
    touch "$FAKE_STATE/install-started"
    echo "installing hal0"
    [[ -z "${FAKE_BIG_INSTALL:-}" ]] || head -c "$FAKE_BIG_INSTALL" /dev/zero | tr '\0' x
    if [[ -n "${FAKE_INSTALL_SLEEP:-}" ]]; then exec sleep "$FAKE_INSTALL_SLEEP"; fi
    exit "${FAKE_INSTALL_RC:-0}" ;;
esac
echo "fake ssh: unexpected command: $cmd" >&2
exit 97
"""


# Rule for every fake ssh in these tests: a test must stay harmless when the
# code under test is wrong, so the fake cuts its own output and never reads an endless source.
class Host:
    def __init__(self, tmp_path: Path):
        self.root = tmp_path / "host"
        self.root.mkdir()
        self.bin = self.root / "bin"
        self.bin.mkdir()
        self.state = self.root / "state"
        self.state.mkdir()
        self.log = self.state / "ssh.log"
        self.log.write_text("")
        (self.bin / "ssh-body").write_text(FAKE_SSH)
        ssh = self.bin / "ssh"
        # Whatever the fake emits is cut at 1 MiB, whatever the code under test
        # does: a wrong implementation must stay harmless. The cut on stdout is
        # a pipeline stage, so this script exits only after every byte was
        # forwarded: a relay left running after the exit would be hung up with
        # the output unread when the pty's session leader (this script) is gone.
        # stderr's relay ignores that hang-up for the same reason.
        ssh.write_text(
            "#!/usr/bin/env bash\n"
            "set -o pipefail\n"
            "exec 2> >(trap '' HUP; head -c 1048576 >&2)\n"
            f'bash {self.bin}/ssh-body "$@" | head -c 1048576\n'
        )
        ssh.chmod(0o755)
        for name in ("pct", "pveam", "pvesm", "pvesh"):
            p = self.bin / name
            p.write_text(f"#!/bin/sh\necho 'real {name} called locally' >&2\nexit 98\n")
            p.chmod(0o755)
        self.out = tmp_path / "out"

    def env(self, **extra: str) -> dict[str, str]:
        return {
            "PATH": f"{self.bin}:/usr/bin:/bin",
            "HOME": str(self.root),
            "HAL0_PVE": "testpve",
            "HAL0_MATRIX_DESTROY_SLEEP": "0",
            "FAKE_STATE": str(self.state),
            "HAL0_MATRIX_REPORT_ROOT": str(self.root / "reports"),
            **extra,
        }

    def calls(self) -> list[str]:
        return [x for x in self.log.read_text().splitlines() if x]

    def run(self, *args: str, script: Path = RUN_SHAPE, **env: str):
        # Own session, so that whatever the code under test leaves running
        # (a watchdog, a sleep) is killed with its group on every exit path.
        proc = subprocess.Popen(
            ["bash", str(script), *args],
            env=self.env(**env),
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            start_new_session=True,
        )
        try:
            out, err = proc.communicate(timeout=180)
        finally:
            kill_group(proc)
        return subprocess.CompletedProcess(proc.args, proc.returncode, out, err)

    def dry(self, shape: str, *args: str, **env: str):
        return self.run(shape, "--dry-run", "--out", str(self.out), *args, **env)


@pytest.fixture
def host(tmp_path):
    return Host(tmp_path)


def kill_group(proc: subprocess.Popen) -> None:
    """Kill the whole process group of a test's process (it runs in its own
    session); only "no such process" is ignored."""
    with contextlib.suppress(ProcessLookupError):
        os.killpg(proc.pid, signal.SIGKILL)


def spawn(host: Host, *args: str, **env: str) -> subprocess.Popen:
    """Start run-shape.sh in a new session; the caller kills the group in a finally."""
    return subprocess.Popen(
        ["bash", str(RUN_SHAPE), *args],
        env=host.env(**env),
        stdout=subprocess.DEVNULL,
        stderr=subprocess.PIPE,
        text=True,
        start_new_session=True,
    )


def plan_lines(proc) -> list[str]:
    return [x for x in proc.stdout.splitlines() if x.startswith("[dry-run]")]


# --- refusals --------------------------------------------------------------


def test_refuses_shape_not_runnable(host):
    r = host.run("baremetal-amd")
    assert r.returncode == 2
    assert "not runnable" in r.stderr
    assert host.calls() == []


def test_refuses_unknown_shape_and_bad_source(host):
    assert host.run("no-such-shape").returncode == 2
    assert host.run("u2404-cpu", "--source", "weird").returncode == 2
    assert host.run("u2404-cpu", "--source", "tree:/no/such/dir/anywhere").returncode == 2
    assert host.calls() == []


def test_refuses_when_host_low_on_memory(host):
    r = host.run("u2404-cpu", "--out", str(host.out), FAKE_MEM="5")
    assert r.returncode == 2
    assert "5 GB" in r.stderr
    assert not any("pct create" in c for c in host.calls())
    assert not host.out.exists()


def test_run_shape_passes_devices_to_the_guard(host):
    gpu_mem = f"{2 * 1024**3} {96 * 1024**3}"
    r = host.run("u2604-gpu", "--out", str(host.out), FAKE_MEM="16", FAKE_GPU_MEM=gpu_mem)
    assert r.returncode == 2
    assert "16 GB available, need 38" in r.stderr  # floor 22 + half of 32 GB
    assert "gpu memory: 2 GB used of 96 GB" in r.stderr
    assert not any("pct create" in c for c in host.calls())
    cpu = host.run("u2404-cpu", "--dry-run", "--out", str(host.out), FAKE_MEM="20")
    assert cpu.returncode == 0, cpu.stderr


def test_the_start_guard_counts_half_the_containers_memory(host):
    r = host.run("u2404-cpu", "--out", str(host.out), FAKE_MEM="19")
    assert r.returncode == 2
    assert "19 GB available, need 20" in r.stderr
    assert "floor 12" in r.stderr and "8 GB" in r.stderr
    assert not any("pct create" in c for c in host.calls())


def _watch_env(**extra):
    return {"HAL0_MATRIX_WATCH_SECONDS": "1", **extra}


def test_watchdog_stops_the_install_when_host_memory_falls(host):
    r = host.run(
        "u2604-cpu-unattended", "--vmid", "172", "--out", str(host.out),
        **_watch_env(FAKE_MEM_AFTER="5", FAKE_INSTALL_SLEEP="20"),
    )  # fmt: skip
    assert r.returncode == 1, r.stderr
    assert "host memory fell below 12 GB (5 GB available)" in r.stderr
    note = (host.out / "run.txt").read_text().splitlines()
    assert "aborted=host_memory_low" in note
    result = json.loads((host.out / "result.json").read_text())
    assert result["aborted"] == "host_memory_low"
    assert result["abort_detail"] == "host memory fell below 12 GB (5 GB available)"
    assert not (host.state / "present").exists(), "the normal cleanup destroyed it"
    assert "LEFT BEHIND" not in r.stderr
    assert (host.out / "result.json").exists(), "what was measured is still saved"


def test_a_low_reading_stops_the_host_call_in_progress_at_once(host):
    """N1: the probe is a long host call. A low reading while it runs must
    start cleanup within 3 seconds, not when the call returns."""
    proc = spawn(
        host, "u2604-cpu-unattended", "--vmid", "172", "--out", str(host.out),
        **_watch_env(
            HAL0_MATRIX_WATCH_SECONDS="3", FAKE_MEM_AFTER="5", FAKE_PROBE_SLEEP="15"
        ),
    )  # fmt: skip
    try:
        proc.communicate(timeout=60)
    finally:
        kill_group(proc)
    probe_at = float((host.state / "probe-started").read_text())
    low_at = float((host.state / "low-read-at").read_text())
    assert probe_at < low_at, "the low reading must come while the probe call is running"
    # Cleanup's first host call is the wait for a running `pct create`.
    times = [x.split(" ", 1) for x in (host.state / "ssh.times").read_text().splitlines()]
    cleanup_at = next(float(t) for t, c in times if "pgrep -f" in c and float(t) > low_at)
    assert cleanup_at - low_at < 3, (
        f"cleanup began {cleanup_at - low_at:.1f}s after the low reading"
    )
    assert json.loads((host.out / "result.json").read_text())["aborted"] == "host_memory_low"
    assert not (host.state / "present").exists()


def _run_within(host, seconds, *args, **env):
    """run-shape.sh in its own session, killed by group in a finally; returns
    (status, stdout, stderr)."""
    proc = subprocess.Popen(
        ["bash", str(RUN_SHAPE), *args],
        env=host.env(**env),
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        start_new_session=True,
    )
    try:
        out, err = proc.communicate(timeout=seconds)
    finally:
        kill_group(proc)
    return proc.returncode, out, err


def _assert_cleanup_reached_the_runs_stderr(host, rc, out, err):
    assert rc == 1, err
    assert "aborting: host memory fell below 12 GB (5 GB available)" in err
    assert "LEFT BEHIND: 172 (destroy it with: ssh testpve pct destroy 172 --force --purge)" in err
    line = "container 172 exists; if this run dies, destroy it with: ssh testpve pct destroy 172"
    assert err.count(line) >= 2, "once after the create, once at the start of cleanup"
    note = (host.out / "run.txt").read_text().splitlines()
    assert "left_behind=172" in note
    assert "aborted=host_memory_low" in note
    ce = host.out / "collect.err"
    text = ce.read_text(errors="replace") if ce.exists() else ""
    assert "LEFT BEHIND" not in text and "aborting:" not in text, "cleanup wrote into collect.err"
    assert out == "", "cleanup's messages stay off stdout, which carries only the result path"


def test_an_abort_during_the_probe_reports_left_behind_on_the_runs_stderr(host):
    """M1: the probe call redirects stderr to collect.err. Cleanup, started by
    the abort while that call runs, must still write to the run's own stderr."""
    rc, out, err = _run_within(
        host, 60, "u2604-cpu-unattended", "--vmid", "172", "--out", str(host.out),
        **_watch_env(
            HAL0_MATRIX_WATCH_SECONDS="2", FAKE_MEM_AFTER="5", FAKE_PROBE_SLEEP="8",
            FAKE_DESTROY_FAILS="1",
        ),
    )  # fmt: skip
    _assert_cleanup_reached_the_runs_stderr(host, rc, out, err)


def test_an_abort_during_a_pull_reports_left_behind_on_the_runs_stderr(host):
    """M1: the pulls redirect stderr to /dev/null; the abort's messages and
    LEFT BEHIND must not go there."""
    rc, out, err = _run_within(
        host, 60, "u2604-cpu-unattended", "--vmid", "172", "--out", str(host.out),
        **_watch_env(
            HAL0_MATRIX_WATCH_SECONDS="2", FAKE_SUM_SLEEP="8", FAKE_DESTROY_FAILS="1",
        ),
    )  # fmt: skip
    _assert_cleanup_reached_the_runs_stderr(host, rc, out, err)


@pytest.mark.parametrize(
    "env",
    [
        {"FAKE_MEM_AFTER": "5"},
        {"FAKE_MEM_FAIL_AFTER": "1"},
    ],
    ids=["host_memory_low", "host_unreachable"],
)
def test_after_an_abort_cleanup_destroys_first_and_collects_nothing(host, env):
    """N2: with the host short of memory, the container goes before anything is
    collected from it; the result comes from what is already on this side."""
    r = host.run(
        "u2604-cpu-unattended", "--vmid", "172", "--out", str(host.out),
        **_watch_env(FAKE_INSTALL_SLEEP="10", **env),
    )  # fmt: skip
    assert r.returncode == 1, r.stderr
    calls = host.calls()
    install_at = max(i for i, c in enumerate(calls) if "summary-json" in c)
    after = calls[install_at + 1 :]
    destroy_at = next(i for i, c in enumerate(after) if "pct destroy 172" in c)
    assert not [c for c in after[:destroy_at] if "pct exec" in c], "nothing is run in the guest"
    assert not [c for c in after if "collect.sh post" in c or "install-1.log" in c], after
    assert not [c for c in after if "hal0-summary.json" in c]
    result = json.loads((host.out / "result.json").read_text())
    assert result["aborted"] in ("host_memory_low", "host_unreachable")
    assert result["abort_detail"]
    assert not (host.state / "present").exists()


def _session_members(sid: int) -> list[str]:
    out = subprocess.run(
        ["pgrep", "-a", "-s", str(sid)], capture_output=True, text=True, check=False, timeout=10
    )
    return [x for x in out.stdout.splitlines() if x]


def test_a_killed_run_leaves_no_watchdog_and_no_lock_behind(host):
    """N3: after kill -9 of the main shell the watchdog ends by itself, with the
    processes the run had started, and a new run can take the lock."""
    import fcntl

    proc = spawn(
        host, "u2604-cpu-unattended", "--vmid", "172", "--out", str(host.out),
        **_watch_env(FAKE_INSTALL_SLEEP="20"),
    )  # fmt: skip
    try:
        deadline = time.monotonic() + 60
        while not (host.state / "install-started").exists():
            assert proc.poll() is None and time.monotonic() < deadline
            time.sleep(0.05)
        time.sleep(2.5)  # a few watchdog ticks while the install runs
        assert len(_session_members(proc.pid)) > 2, "the run's processes should be there"
        os.kill(proc.pid, signal.SIGKILL)
        proc.wait(timeout=30)
        deadline = time.monotonic() + 8
        left = _session_members(proc.pid)
        while left and time.monotonic() < deadline:
            time.sleep(0.2)
            left = _session_members(proc.pid)
        assert not left, f"processes of the killed run remain: {left}"
        with open(host.root / "reports" / ".lock", "w") as lock:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
    finally:
        kill_group(proc)


def test_watchdog_aborts_after_three_unreadable_readings_in_a_row(host):
    r = host.run(
        "u2604-cpu-unattended", "--vmid", "172", "--out", str(host.out),
        **_watch_env(FAKE_MEM_FAIL_AFTER="1", FAKE_INSTALL_SLEEP="20"),
    )  # fmt: skip
    assert r.returncode == 1, r.stderr
    assert "aborted=host_unreachable" in (host.out / "run.txt").read_text().splitlines()
    assert json.loads((host.out / "result.json").read_text())["aborted"] == "host_unreachable"
    assert not (host.state / "present").exists()
    assert int((host.state / "free-reads").read_text()) == 4  # the guard + three


def test_one_unreadable_reading_is_not_a_reason_to_abort(host):
    r = host.run(
        "u2604-cpu-unattended", "--vmid", "172", "--out", str(host.out),
        **_watch_env(FAKE_MEM_FAIL_ON="2", FAKE_INSTALL_SLEEP="4"),
    )  # fmt: skip
    assert r.returncode == 0, r.stderr
    assert "aborted=" not in (host.out / "run.txt").read_text()
    assert json.loads((host.out / "result.json").read_text())["aborted"] is None


def _capped(**extra):
    return {"HAL0_MATRIX_MAX_STREAM_BYTES": "4096", **extra}


def _notes(host) -> list[str]:
    return (host.out / "run.txt").read_text().splitlines()


def test_the_unattended_transcript_is_cut_at_the_cap(host):
    r = host.run(
        "u2604-cpu-unattended", "--vmid", "172", "--out", str(host.out),
        **_capped(FAKE_BIG_INSTALL="20000"),
    )  # fmt: skip
    assert r.returncode == 0, r.stderr
    assert (host.out / "transcript.txt").stat().st_size == 4096
    assert "truncated=transcript" in _notes(host)
    assert (host.out / "result.json").exists()


def test_a_transcript_under_the_cap_is_not_noted(host):
    r = host.run("u2604-cpu-unattended", "--vmid", "172", "--out", str(host.out), **_capped())
    assert r.returncode == 0, r.stderr
    assert not [x for x in _notes(host) if x.startswith("truncated=")]


def test_collect_output_is_cut_at_the_cap(host):
    r = host.run(
        "u2604-cpu-unattended", "--vmid", "172", "--out", str(host.out),
        **_capped(FAKE_BIG_PRE="20000"),
    )  # fmt: skip
    assert r.returncode == 0, r.stderr
    assert (host.out / "pre.txt").stat().st_size == 4096
    assert "truncated=pre" in _notes(host)


def test_a_huge_install_log_pull_is_cut_at_the_cap(host):
    r = host.run(
        "u2604-cpu-unattended", "--vmid", "172", "--out", str(host.out),
        **_capped(FAKE_BIG_LOG="20000"),
    )  # fmt: skip
    assert r.returncode == 0, r.stderr
    assert (host.out / "install.log").stat().st_size == 4096
    assert "truncated=install.log" in _notes(host)


def test_refuses_a_vmid_that_already_exists(host):
    (host.state / "present").touch()
    r = host.run("u2404-cpu", "--vmid", "172", "--out", str(host.out))
    assert r.returncode == 2
    assert not any("pct create" in c or "pct destroy" in c for c in host.calls())


def test_a_refused_create_never_destroys_the_guest_it_found(host):
    """Another run's guest appears at the VMID after this run's pre-check. The
    create refuses; cleanup must leave that guest alone (it carries the tag and
    the hostname, but not this run's nonce, and this run never created it)."""
    r = host.run("u2404-cpu", "--vmid", "172", "--out", str(host.out), FAKE_PRESENT_AFTER="1")
    assert r.returncode == 1, r.stderr
    calls = host.calls()
    assert not any("pct create" in c for c in calls)
    assert not any("pct destroy" in c for c in calls)
    assert "LEFT BEHIND" not in r.stderr
    assert "UNSURE" not in r.stderr, (
        "a run that never started a create has nothing to be unsure about"
    )
    assert (host.state / "present").exists()


def test_a_lost_race_for_the_vmid_names_no_container_and_no_destroy_command(host):
    """N5: another run's guest appears at the VMID after this run's checks, and
    this run's own pct create fails. It created nothing: no LEFT BEHIND, no
    destroy command, no VMID named, and the other guest is not touched. The
    other guest is known to be foreign because its description carries
    another run's nonce."""
    (host.state / "description").write_text("hal0-matrix run " + "f" * 16)
    r = host.run(
        "u2404-cpu", "--vmid", "172", "--out", str(host.out),
        FAKE_PRESENT_AFTER="2", FAKE_CREATE_FAILS="1",
    )  # fmt: skip
    assert r.returncode == 1, r.stderr
    assert "LEFT BEHIND" not in r.stderr
    assert "destroy it with" not in r.stderr
    assert "pct destroy" not in r.stderr
    assert "created no container" in r.stderr
    assert "left_behind" not in (host.out / "run.txt").read_text()
    assert not any("pct destroy" in c for c in host.calls())
    assert (host.state / "present").exists()
    said = [x for x in r.stderr.splitlines() if "created no container" in x]
    assert said and "172" not in said[0]
    assert "UNSURE" not in r.stderr


UNSURE_LINE = (
    "UNSURE: guest 172 exists and this run started a create for it; it may be this run's. "
    "Check it by hand: pct config 172"
)


def test_an_unrecognised_description_after_a_failed_create_is_unsure_not_silent(host):
    """L1: this run's pct create made the guest but returned 1, and the
    description reads back in a form the nonce check does not recognise. The
    run cannot prove the guest is its own, so it says so with the VMID, gives
    no destroy command, records unsure= and exits non-zero."""
    r = host.run(
        "u2404-cpu", "--vmid", "172", "--out", str(host.out),
        FAKE_DESC_ODD="1", FAKE_CREATE_RC="1",
    )  # fmt: skip
    assert r.returncode == 1, r.stderr
    assert UNSURE_LINE in r.stderr.splitlines()
    for text in ("LEFT BEHIND", "destroy it with", "pct destroy", "created no container"):
        assert text not in r.stderr, text
    note = (host.out / "run.txt").read_text().splitlines()
    assert "unsure=172" in note
    assert not any(
        x.startswith(("left_behind=", "created_nothing=", "destroy_command=")) for x in note
    )
    assert not any("pct destroy" in c for c in host.calls())
    assert (host.state / "present").exists(), "an unproven guest is left alone"


def test_a_failed_create_with_the_right_description_is_proven_and_destroyed(host):
    """The create returned 1 but the guest carries this run's nonce: it is
    this run's, and the nonce-checked destroy removes it."""
    r = host.run(
        "u2404-cpu", "--vmid", "172", "--out", str(host.out), FAKE_CREATE_RC="1"
    )  # fmt: skip
    assert r.returncode == 1, r.stderr
    assert "UNSURE" not in r.stderr
    assert "destroyed container 172" in r.stderr
    assert not (host.state / "present").exists()
    assert "unsure=" not in (host.out / "run.txt").read_text()


def test_the_guest_this_run_created_carries_its_nonce(host):
    r = host.run("u2404-cpu", "--vmid", "172", "--out", str(host.out))
    assert r.returncode == 0, r.stderr
    create = next(c for c in host.calls() if "pct create" in c)
    m = re.search(r"--description hal0-matrix\\ run\\ ([0-9a-f]{16})(?: |$)", create)
    assert m, create


def test_a_second_run_exits_at_once_while_one_holds_the_lock(host):
    import fcntl

    root = host.root / "reports"
    root.mkdir()
    with open(root / ".lock", "w") as held:
        fcntl.flock(held, fcntl.LOCK_EX | fcntl.LOCK_NB)
        r = host.run("u2404-cpu", "--vmid", "172", "--out", str(host.out))
    assert r.returncode == 2
    assert "another install matrix run" in r.stderr
    assert host.calls() == []
    # Released: the same run goes through.
    again = host.run("u2404-cpu", "--vmid", "172", "--out", str(host.out))
    assert again.returncode == 0, again.stderr


def test_refuses_when_any_guest_sits_in_the_range(host):
    (host.state / "range-guest").write_text("171")
    r = host.run("u2404-cpu", "--out", str(host.out))
    assert r.returncode == 2
    assert "171" in r.stderr
    assert not any("pct create" in c for c in host.calls())


def test_refuses_a_privileged_shape_before_any_host_call(host):
    r = host.run("u2604-gpu-privileged", "--out", str(host.out))
    assert r.returncode == 2
    assert "equal to root on the host" in r.stderr
    assert "no production guests" in r.stderr
    assert "HAL0_MATRIX_ALLOW_PRIVILEGED=1" in r.stderr
    assert host.calls() == []
    assert not host.out.exists()


def test_a_privileged_shape_is_allowed_only_with_the_explicit_variable(host):
    r = host.dry("u2604-gpu-privileged", HAL0_MATRIX_ALLOW_PRIVILEGED="1")
    assert r.returncode == 0, r.stderr
    assert "--unprivileged 0" in "\n".join(plan_lines(r))
    bad = host.run(
        "u2604-gpu-privileged", "--out", str(host.out), HAL0_MATRIX_ALLOW_PRIVILEGED="yes"
    )
    assert bad.returncode == 2 and host.calls() == []


def test_a_dry_run_of_a_privileged_shape_still_prints_the_plan(host):
    r = host.dry("u2604-gpu-privileged")
    assert r.returncode == 0, r.stderr
    plan = "\n".join(plan_lines(r))
    assert "pct create 170" in plan and "--unprivileged 0" in plan
    assert "equal to root on the host" in r.stderr  # the warning is still shown
    assert host.calls() == []


def test_refuses_when_the_storage_is_too_small_for_the_disk(host):
    # 60 GB disk + 10: 69 GB free is not enough, 70 is
    r = host.run("u2404-cpu", "--out", str(host.out), FAKE_STORAGE_AVAIL=str(69 * 1048576))
    assert r.returncode == 2
    assert "storage local: 69 GB free, need 70" in r.stderr
    assert not any("pct create" in c for c in host.calls())
    assert not host.out.exists()
    ok = host.run("u2404-cpu", "--out", str(host.out), FAKE_STORAGE_AVAIL=str(70 * 1048576))
    assert ok.returncode == 0, ok.stderr
    assert "storage local: 70 GB free" in ok.stderr


@pytest.mark.parametrize(
    "lo,hi", [("1", "999999999"), ("99", "150"), ("170", "234"), ("180", "170"), ("x", "y")]
)
def test_refuses_a_silly_vmid_range_before_any_host_call(host, lo, hi):
    r = host.run(
        "u2404-cpu", "--out", str(host.out),
        HAL0_MATRIX_VMID_MIN=lo, HAL0_MATRIX_VMID_MAX=hi,
    )  # fmt: skip
    assert r.returncode == 2
    assert "VMID range" in r.stderr
    assert host.calls() == []


def test_refuses_a_vmid_outside_the_range(host):
    r = host.run("u2404-cpu", "--vmid", "100", "--out", str(host.out))
    assert r.returncode == 2
    assert not any("pct create" in c for c in host.calls())


def test_tree_source_refuses_the_box_entry(host):
    r = host.dry("u2604-box", "--source", f"tree:{REPO}")
    assert r.returncode == 2


# --- dry run ---------------------------------------------------------------


def test_dry_run_creates_nothing(host):
    r = host.dry("u2604-gpu")
    assert r.returncode == 0, r.stderr
    calls = host.calls()
    assert calls == [], "a dry run must never reach ssh"
    plan = "\n".join(plan_lines(r))
    assert "pct create 170" in plan  # the plan names what it would do
    assert "--dev0" in plan and "renderD128" in plan
    assert "pct destroy 170" in plan
    assert not host.out.exists()


def test_oneliner_runs_in_a_terminal_and_passes_summary_flag(host):
    r = host.dry("u2404-cpu")
    assert r.returncode == 0, r.stderr
    plan = "\n".join(plan_lines(r))
    assert "ssh -tt" in plan
    assert "| bash -s -- --summary-json=/root/hal0-summary.json" in plan
    assert "pct exec 170 -- bash -c '" in plan
    assert "HAL0_NONINTERACTIVE" not in plan
    assert "cpu-consent.tsv" in plan


def test_box_adds_the_box_flag(host):
    r = host.dry("u2604-box")
    plan = "\n".join(plan_lines(r))
    assert "| bash -s -- --box --summary-json=/root/hal0-summary.json" in plan


def test_unattended_sets_both_variables_and_no_terminal(host):
    r = host.dry("u2604-cpu-unattended")
    assert r.returncode == 0, r.stderr
    plan = "\n".join(plan_lines(r))
    assert "HAL0_NONINTERACTIVE=1" in plan
    assert "HAL0_ALLOW_CPU_ONLY=1" in plan
    assert "-tt" not in plan
    assert "tty:" not in plan


def test_terminal_entry_downloads_first_then_runs_with_a_terminal(host):
    r = host.dry("u2604-cpu-terminal")
    plan = "\n".join(plan_lines(r))
    assert "-o /root/install.sh" in plan.replace("\\", "")
    assert "bash /root/install.sh --summary-json=/root/hal0-summary.json" in plan
    assert "ssh -tt" in plan
    assert "terminal-defaults.tsv" in plan


def test_prereqs_never_include_python(host):
    r = host.dry("deb13-cpu")
    plan = "\n".join(plan_lines(r)).replace("\\", "")
    installs = re.findall(r"install -y(?: -\w+)* ([^;|>}&]+)", plan)
    assert installs, "the prerequisite install should be planned"
    for tail in installs:
        assert set(tail.split()) <= {"curl", "ca-certificates"}, tail
    # Only the one guarded install line may name packages at all.
    assert "python" not in plan.split("command -v curl")[1].split("prereqs=")[0]


@pytest.mark.parametrize("source", ["stable", "preview", "nightly"])
def test_cosign_not_skipped(host, source):
    r = host.dry("u2404-cpu", "--source", source)
    plan = "\n".join(plan_lines(r))
    assert "HAL0_UPDATE_SKIP_COSIGN" not in plan
    assert "HAL0_INSTALL_SKIP_VERIFY" not in plan
    assert "https://hal0.dev/install.sh" in plan
    if source != "stable":
        assert f"HAL0_CHANNEL={source} bash -s --" in plan


def _git_tree(tmp_path: Path) -> Path:
    """A git work tree: one tracked file, one untracked file that is not
    ignored, files that .gitignore hides, and an earlier report."""
    tree = tmp_path / "my tree"
    (tree / "installer").mkdir(parents=True)
    (tree / "installer" / "install.sh").write_text("echo hi\n")
    (tree / ".gitignore").write_text(".env\nlocal-notes/\n")
    (tree / ".env").write_text("SECRET=1\n")
    (tree / "local-notes").mkdir()
    (tree / "local-notes" / "plan.md").write_text("private\n")
    (tree / "untracked.txt").write_text("new work\n")
    reports = tree / "tests" / "harness" / "reports" / "install-matrix" / "run1"
    reports.mkdir(parents=True)
    (reports / "transcript.txt").write_text("a secret printed to a terminal\n")
    (tree / "tests" / "harness" / "keep.sh").write_text("echo keep\n")
    for args in (["init", "-q"], ["add", "installer/install.sh", ".gitignore"]):
        subprocess.run(["git", "-C", str(tree), *args], check=True, timeout=30)
    return tree


def test_tree_source_leaves_out_ignored_files_and_earlier_reports(host, tmp_path):
    import tarfile

    tree = _git_tree(tmp_path)
    r = host.run("u2404-cpu", "--vmid", "172", "--out", str(host.out), "--source", f"tree:{tree}")
    assert r.returncode == 0, r.stderr
    with tarfile.open(host.state / "tree.tgz") as tf:
        names = {n.removeprefix("./") for n in tf.getnames()}
    assert "installer/install.sh" in names and ".gitignore" in names
    assert "untracked.txt" in names, "untracked files that are not ignored still go in"
    assert "tests/harness/keep.sh" in names
    assert ".env" not in names
    assert not [n for n in names if n.startswith("local-notes")]
    assert not [n for n in names if n.startswith("tests/harness/reports")]
    assert not [n for n in names if n.startswith(".git/")]


def test_tree_source_refuses_a_directory_that_is_not_a_git_work_tree(host, tmp_path):
    plain = tmp_path / "plain"
    plain.mkdir()
    (plain / "installer").mkdir()
    for extra in ([], ["--dry-run"]):
        r = host.run("u2404-cpu", "--out", str(host.out), "--source", f"tree:{plain}", *extra)
        assert r.returncode == 2
        assert "not a git work tree" in r.stderr
    assert host.calls() == [], "refused before any host call"


def test_tree_source_packs_and_skips_verify_only_there(host, tmp_path):
    tree = _git_tree(tmp_path)
    r = host.dry("u2404-cpu", "--source", f"tree:{tree}")
    assert r.returncode == 0, r.stderr
    plan = "\n".join(plan_lines(r))
    assert "HAL0_INSTALL_SKIP_VERIFY=1 bash installer/install.sh" in plan
    assert str(tree) not in plan, "a local path must never reach the host"
    assert "HAL0_UPDATE_SKIP_COSIGN" not in plan


def test_dry_run_still_validates_the_source(host):
    r = host.dry("u2404-cpu", "--source", "tree:/definitely/not/here")
    assert r.returncode == 2


# --- cleanup and results ---------------------------------------------------


def test_cleanup_destroys_on_interrupt(host):
    proc = subprocess.Popen(
        ["bash", str(RUN_SHAPE), "u2604-cpu-unattended", "--vmid", "172", "--out", str(host.out)],
        env=host.env(FAKE_INSTALL_SLEEP="20"),
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        start_new_session=True,
    )
    try:
        started = host.state / "install-started"
        deadline = time.monotonic() + 90
        while not started.exists():
            assert proc.poll() is None, "run ended before the install started"
            assert time.monotonic() < deadline, "install never started"
            time.sleep(0.05)
        os.killpg(proc.pid, signal.SIGINT)
        _, err = proc.communicate(timeout=120)
        assert proc.returncode == 130, err
        calls = host.calls()
        install_at = max(i for i, c in enumerate(calls) if "summary-json" in c)
        destroy_at = [i for i, c in enumerate(calls) if "pct destroy 172" in c]
        assert destroy_at and destroy_at[0] > install_at
        assert not (host.state / "present").exists(), "the container must be gone"
        assert "LEFT BEHIND" not in err
        result = json.loads((host.out / "result.json").read_text())
        assert result["install_exit_code"] == 130
    finally:
        kill_group(proc)


def test_failed_destroy_prints_left_behind(host):
    r = host.run("u2404-cpu", "--vmid", "172", "--out", str(host.out), FAKE_DESTROY_FAILS="1")
    assert "LEFT BEHIND: 172" in r.stderr
    assert "pct destroy 172" in r.stderr
    assert r.returncode == 1


DESTROY_CMD = "ssh testpve pct destroy 172 --force --purge"


def test_left_behind_is_also_written_to_run_txt(host):
    r = host.run("u2404-cpu", "--vmid", "172", "--out", str(host.out), FAKE_DESTROY_FAILS="1")
    note = (host.out / "run.txt").read_text().splitlines()
    assert "left_behind=172" in note
    assert f"destroy_command={DESTROY_CMD}" in note, r.stderr


def test_the_destroy_command_is_printed_as_soon_as_the_container_exists(host):
    r = host.run("u2404-cpu", "--vmid", "172", "--out", str(host.out))
    assert r.returncode == 0, r.stderr
    line = f"container 172 exists; if this run dies, destroy it with: {DESTROY_CMD}"
    assert r.stderr.count(line) == 2, r.stderr  # after create, and at the start of cleanup
    first = r.stderr.index(line)
    assert first < r.stderr.index("installing (")


def test_an_interrupt_during_pct_create_waits_for_it_and_then_cleans_up(host):
    proc = subprocess.Popen(
        ["bash", str(RUN_SHAPE), "u2404-cpu", "--vmid", "172", "--out", str(host.out)],
        env=host.env(FAKE_CREATE_SLEEP="3"),
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        start_new_session=True,
    )
    try:
        deadline = time.monotonic() + 90
        while not (host.state / "create-started").exists():
            assert proc.poll() is None and time.monotonic() < deadline
            time.sleep(0.02)
        os.killpg(proc.pid, signal.SIGINT)
        _, err = proc.communicate(timeout=120)
        assert proc.returncode == 130, err
        calls = host.calls()
        created = [i for i, c in enumerate(calls) if "pct create" in c]
        destroyed = [i for i, c in enumerate(calls) if "pct destroy 172" in c]
        assert created and destroyed and destroyed[0] > created[0], (
            "the create ran to its end, then was cleaned up"
        )
        assert not (host.state / "present").exists()
        assert "LEFT BEHIND" not in err
    finally:
        kill_group(proc)


def test_cleanup_waits_until_no_pct_create_runs_before_checking_presence(host):
    r = host.run(
        "u2404-cpu", "--vmid", "172", "--out", str(host.out),
        FAKE_PGREP_BUSY_READS="2", HAL0_MATRIX_POLL_SLEEP="0",
    )  # fmt: skip
    assert r.returncode == 0, r.stderr
    calls = host.calls()
    pgreps = [i for i, c in enumerate(calls) if "pgrep -f" in c]
    assert len(pgreps) == 3  # busy, busy, idle
    assert "pgrep -f '[p]ct create 172( |$)'" in calls[pgreps[0]]
    after = [i for i, c in enumerate(calls) if "if test -e" in c and i > pgreps[-1]]
    assert after, "the presence check comes after the wait"
    assert not [i for i, c in enumerate(calls) if "pct destroy" in c and i < pgreps[-1]]


def test_a_locked_guest_gets_the_unlock_in_the_manual_command(host):
    r = host.run(
        "u2404-cpu", "--vmid", "172", "--out", str(host.out),
        FAKE_LOCK="1", FAKE_DESTROY_FAILS="1",
    )  # fmt: skip
    cmd = "ssh testpve 'pct unlock 172 && pct destroy 172 --force --purge'"
    assert f"LEFT BEHIND: 172 (destroy it with: {cmd})" in r.stderr
    assert f"destroy_command={cmd}" in (host.out / "run.txt").read_text().splitlines()


def test_a_hung_start_is_cut_off_and_the_container_is_still_destroyed(host):
    r = host.run(
        "u2404-cpu", "--vmid", "172", "--out", str(host.out),
        FAKE_START_HANGS="1", HAL0_MATRIX_TIMEOUT_START="1",
    )  # fmt: skip
    assert r.returncode == 1, r.stderr
    assert "could not start container 172" in r.stderr
    assert not (host.state / "present").exists()


def test_a_hung_destroy_ends_in_left_behind_not_a_hang(host):
    r = host.run(
        "u2404-cpu", "--vmid", "172", "--out", str(host.out),
        FAKE_DESTROY_HANGS="1", HAL0_MATRIX_TIMEOUT_DESTROY="1",
    )  # fmt: skip
    assert r.returncode == 1
    assert "LEFT BEHIND: 172" in r.stderr
    assert "left_behind=172" in (host.out / "run.txt").read_text()


def test_a_second_interrupt_during_cleanup_still_reports_left_behind(host):
    proc = subprocess.Popen(
        ["bash", str(RUN_SHAPE), "u2604-cpu-unattended", "--vmid", "172", "--out", str(host.out)],
        env=host.env(FAKE_INSTALL_SLEEP="20", FAKE_DESTROY_SLEEP="3", FAKE_DESTROY_FAILS="1"),
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        start_new_session=True,
    )
    try:
        deadline = time.monotonic() + 90
        while not (host.state / "install-started").exists():
            assert proc.poll() is None and time.monotonic() < deadline
            time.sleep(0.05)
        os.killpg(proc.pid, signal.SIGINT)
        while not (host.state / "destroy-started").exists():
            assert proc.poll() is None and time.monotonic() < deadline
            time.sleep(0.05)
        os.killpg(proc.pid, signal.SIGINT)
        os.killpg(proc.pid, signal.SIGTERM)
        _, err = proc.communicate(timeout=120)
        assert "LEFT BEHIND: 172" in err, err
        assert "left_behind=172" in (host.out / "run.txt").read_text()
    finally:
        kill_group(proc)


def test_failed_install_still_writes_result(host):
    r = host.run(
        "u2604-cpu-unattended", "--vmid", "172", "--out", str(host.out), FAKE_INSTALL_RC="1"
    )
    assert r.returncode == 1
    result = json.loads((host.out / "result.json").read_text())
    assert result["install_exit_code"] == 1
    assert result["schema"] == "hal0.install-matrix.v1"
    assert result["chat"] is None  # the probe only runs after a good install
    assert (host.out / "install.log").read_text().strip() == "the install log"
    assert (host.out / "failure-report.txt").read_text().strip() == "the failure report"
    assert not (host.state / "present").exists()


def test_nothing_alarming_when_create_fails(host):
    r = host.run("u2404-cpu", "--vmid", "172", "--out", str(host.out), FAKE_CREATE_FAILS="1")
    assert r.returncode == 1
    assert "LEFT BEHIND" not in r.stderr
    assert not any("pct destroy" in c for c in host.calls())


def test_successful_terminal_run(host):
    r = host.run("u2604-cpu-terminal", "--vmid", "172", "--out", str(host.out), "--uninstall")
    assert r.returncode == 0, r.stderr
    path = Path(r.stdout.strip().splitlines()[-1])
    assert path == host.out / "result.json"
    result = json.loads(path.read_text())
    assert result["install_exit_code"] == 0
    assert result["hal0_version"] == "9.9.9"
    assert result["chat"]["ok"] is True
    assert result["bytes_downloaded"] == 800
    assert result["disk_used_bytes"] == 4000
    assert "uninstall" not in result and "vmid" not in result
    note = (host.out / "run.txt").read_text().splitlines()
    assert note == ["vmid=172", "uninstall_ok=1", "residue=clean"]
    assert result["harness_prereqs"] == []
    assert stat.S_IMODE(host.out.stat().st_mode) == 0o700
    for f in host.out.iterdir():
        assert stat.S_IMODE(f.stat().st_mode) == 0o600, f.name
    assert "installing hal0" in (host.out / "transcript.txt").read_text()
    assert not (host.state / "present").exists()
    assert any("-tt" in c and "pct exec 172" in c for c in host.calls())
    tty = next(c for c in host.calls() if "-tt" in c)
    assert "-o ServerAliveInterval=15 -o ServerAliveCountMax=4" in tty


def test_terminal_transcript_keeps_output_of_an_ssh_that_exits_at_once(host):
    # The fake ssh prints and exits; a real terminal hangs up whatever is still
    # running in the session when its leader exits. So the fake must not leave
    # its output cap running after it exits: a `head` that is slow to start
    # (a busy CI machine) would be hung up with the output unread and the
    # transcript would be empty. This `head` is slow whenever it writes to a
    # terminal, which makes that ordering happen every time.
    slow = host.bin / "head"
    slow.write_text('#!/bin/sh\n[ ! -t 1 ] || sleep 0.5\nexec /usr/bin/head "$@"\n')
    slow.chmod(0o755)
    r = host.run("u2604-cpu-terminal", "--vmid", "172", "--out", str(host.out))
    assert r.returncode == 0, r.stderr
    assert "installing hal0" in (host.out / "transcript.txt").read_text()


def test_the_terminal_ssh_has_no_escape_character(host):
    # Under -tt a line starting with ~ would be read by the local ssh: -e none
    # switches that off, and it must come before the host alias.
    r = host.run("u2604-cpu-terminal", "--vmid", "172", "--out", str(host.out))
    assert r.returncode == 0, r.stderr
    tty = next(c for c in host.calls() if " -tt " in f" {c} ")
    before_alias = tty.split(" -- ")[0]
    assert " -e none" in f" {before_alias}"
    dry = "\n".join(plan_lines(host.dry("u2404-cpu")))
    assert "ssh -tt -e none " in dry


def test_keep_leaves_the_container(host):
    r = host.run("u2404-cpu", "--vmid", "172", "--keep", "--out", str(host.out))
    assert r.returncode == 0, r.stderr
    assert (host.state / "present").exists()
    assert "pct destroy 172" in r.stderr  # printed as the manual command
    assert not any("pct destroy" in c for c in host.calls())


# --- the wrapper -----------------------------------------------------------


def test_wrapper_prints_the_old_row_shape(host):
    r = host.run("--vmid", "172", "--out", str(host.out), script=WRAPPER)
    assert r.returncode == 0, r.stderr
    row = json.loads(r.stdout.strip().splitlines()[-1])
    assert set(row) == {
        "row", "clone_id", "install_ok", "smoke_ok", "uninstall_ok", "residue", "ip", "elapsed_s",
    }  # fmt: skip
    assert row["row"] == "install-smoke"
    assert row["clone_id"] == 172
    assert row["install_ok"] is True and row["smoke_ok"] is True
    assert row["uninstall_ok"] is True and row["residue"] == "clean"


def test_wrapper_ignores_with_models_once(host):
    r = host.run("--vmid", "172", "--with-models", "--with-models", "--out", str(host.out),
                 script=WRAPPER)  # fmt: skip
    assert r.stderr.count("--with-models") == 1


def test_wrapper_fails_when_the_destroy_failed(host):
    r = host.run("--vmid", "172", "--out", str(host.out), script=WRAPPER, FAKE_DESTROY_FAILS="1")
    assert "LEFT BEHIND: 172" in r.stderr
    assert r.returncode != 0, "an orphaned container must never read as a pass"
    row = json.loads(r.stdout.strip().splitlines()[-1])
    assert set(row) == {
        "row", "clone_id", "install_ok", "smoke_ok", "uninstall_ok", "residue", "ip", "elapsed_s",
    }  # fmt: skip
