"""Proxmox host library for the install matrix (scripts/install-matrix/lib).

Real bash under subprocess. Every call the library makes to the Proxmox host
goes through ``ssh``; these tests put a fake ``ssh`` first on PATH that records
its arguments and answers from canned output. No real host is ever contacted.
The pct/pveam/pvesm/pvesh names are stubbed to fail loudly, in case a code path
ever tried to run them locally.
"""

from __future__ import annotations

import os
import re
import shlex
import subprocess
import threading
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[2]
PVE_SH = REPO / "scripts" / "install-matrix" / "lib" / "pve.sh"
HOST_DEVICES_SH = REPO / "scripts" / "install-matrix" / "lib" / "host-devices.sh"
PROXMOX_DIR = REPO / "scripts" / "proxmox-ve"
HELPER_CT_SH = PROXMOX_DIR / "ct" / "hal0.sh"
HELPER_INSTALL_SH = PROXMOX_DIR / "install" / "hal0-install.sh"
HELPER_NPU_SH = PROXMOX_DIR / "hal0-npu-passthrough"
HELPER_README = PROXMOX_DIR / "README.md"

FEATURES = "nesting=1,keyctl=1,fuse=1,mknod=1"

TAGGED_CONF = "hostname: hal0-mx-{vmid}\ntags: hal0-matrix\ncores: 2\n"
UNTAGGED_CONF = "hostname: hal0-mx-{vmid}\ncores: 2\n"
WRONG_HOST_CONF = "hostname: hal0\ntags: hal0-matrix\ncores: 2\n"
NONCE = "0123456789abcdef"
NONCE_CONF = TAGGED_CONF + "description: hal0-matrix run " + NONCE + "\n"


# An action now arrives as `{ pct config V | grep ... ; } && <action>`. The fake
# runs the guard for real (real grep, a `pct` that prints the config the rules
# give for `pct config V`) and answers the action only when the guard passes.
GUARD_PREAMBLE = r"""
if [[ "$cmd" == "{ pct config "* ]]; then
  guard="${cmd%%; \} && *}; }"
  action="${cmd#*; \} && }"
  vm="${cmd#\{ pct config }"; vm="${vm%% *}"
  conf="$(bash "$0" "pct config $vm")" || exit 1
  GUARD="$guard" CONF="$conf" bash -c 'pct() { printf "%s\n" "$CONF"; }; export -f pct; exec bash -c "$GUARD"' || exit 1
  cmd="$action"
fi
"""

HANG = "__HANG__"  # a rule answering this never returns (until killed)


# Rule for every fake ssh in these tests: a test must stay harmless when the
# code under test is wrong, so the fake cuts its own output and never reads an endless source.
class Fake:
    """A fake ssh on PATH. ``rules`` is a list of (needle, stdout, rc); the first
    rule whose needle occurs in the command string answers. Unmatched commands
    exit 97 so a stray call is loud."""

    def __init__(self, tmp_path: Path):
        self.dir = tmp_path / "fake"
        self.dir.mkdir()
        self.bin = self.dir / "bin"
        self.bin.mkdir()
        self.log = self.dir / "ssh.log"
        self.log.write_text("")
        self.rules: list[tuple[str, str, int]] = []
        self.stdin_dump = self.dir / "stdin.dump"
        ssh = self.bin / "ssh"
        ssh.write_text(
            "#!/usr/bin/env bash\n"
            f'printf "%s\\n" "$*" >> {shlex.quote(str(self.log))}\n'
            'has_n=0; for a in "$@"; do [[ "$a" == -n ]] && has_n=1; [[ "$a" == -- ]] && break; done\n'
            f'[[ "$has_n" == 1 ]] || cat > {shlex.quote(str(self.stdin_dump))}\n'
            'cmd="${*: -1}"\n'
            # Whatever the handler emits is cut at 1 MiB here, whatever the code
            # under test does: a wrong implementation must stay harmless.
            # (the cut sits inside the process, not in a pipe, so that killing
            # the fake on a deadline kills what is hanging, too)
            "exec > >(head -c 1048576) 2> >(head -c 1048576 >&2)\n"
            f'exec bash {shlex.quote(str(self.dir / "handler.sh"))} "$cmd"\n'
        )
        ssh.chmod(0o755)
        for name in ("pct", "pveam", "pvesm", "pvesh"):
            p = self.bin / name
            p.write_text(f"#!/bin/sh\necho 'real {name} called locally' >&2\nexit 98\n")
            p.chmod(0o755)
        self.write_handler()

    def rule(self, needle: str, stdout: str | bytes = "", rc: int = 0):
        self.rules.append((needle, stdout, rc))
        self.write_handler()

    def write_handler(self):
        lines = ["#!/usr/bin/env bash", 'cmd="$1"', GUARD_PREAMBLE]
        for i, (needle, out, rc) in enumerate(self.rules):
            f = self.dir / f"out{i}"
            f.write_bytes(out if isinstance(out, bytes) else out.encode())
            if out == HANG:
                lines.append(f'if [[ "$cmd" == *{shlex.quote(needle)}* ]]; then exec sleep 10; fi')
                continue
            lines.append(
                f'if [[ "$cmd" == *{shlex.quote(needle)}* ]]; then '
                f"cat {shlex.quote(str(f))}; exit {rc}; fi"
            )
        lines.append('echo "fake ssh: unexpected command: $cmd" >&2; exit 97')
        (self.dir / "handler.sh").write_text("\n".join(lines) + "\n")

    def calls(self) -> list[str]:
        return [x for x in self.log.read_text().splitlines() if x]

    def run(self, body: str, env: dict | None = None, strict: bool = False):
        pre = "set -euo pipefail\n" if strict else ""
        script = f"{pre}source {shlex.quote(str(PVE_SH))}\n{body}\n"
        full = {
            "PATH": f"{self.bin}:/usr/bin:/bin",
            "HOME": str(self.dir),
            "HAL0_PVE": "testpve",
            "HAL0_MATRIX_DESTROY_SLEEP": "0",
            **(env or {}),
        }
        return subprocess.run(
            ["bash", "-c", script],
            env=full,
            capture_output=True,
            text=True,
            check=False,
            timeout=60,  # a red test must fail, never hang
        )


@pytest.fixture
def fake(tmp_path):
    return Fake(tmp_path)


def _free(avail: int) -> str:
    return (
        "               total        used        free      shared  buff/cache   available\n"
        f"Mem:             125          20          60           1          45         {avail}\n"
        "Swap:              8           0           8\n"
    )


def _guests(*rows: tuple[int, str]) -> str:
    import json

    return json.dumps([{"vmid": v, "type": "lxc", "tags": t, "name": f"g{v}"} for v, t in rows])


def _owned_rules(fake: Fake, vmid: int, conf: str = TAGGED_CONF):
    fake.rule(f"pct config {vmid}", conf.format(vmid=vmid))


# --- the safety harness itself ---------------------------------------------


def test_fake_ssh_is_first_on_path(fake):
    r = fake.run("command -v ssh")
    assert r.stdout.strip() == str(fake.bin / "ssh")


def test_sourcing_sets_no_options_or_traps(fake):
    r = fake.run('echo "opts=$-"; trap -p; type ct_destroy >/dev/null && echo ok')
    assert "ok" in r.stdout
    opts = re.search(r"opts=(\S*)", r.stdout).group(1)
    assert "e" not in opts and "u" not in opts
    assert "trap --" not in r.stdout


# --- headroom ---------------------------------------------------------------


def test_headroom_refuses_below_floor(fake):
    fake.rule("free -g", _free(9))
    r = fake.run("pve_require_headroom 2>&1; echo rc=$?")
    assert "host has 9 GB available now, the floor is 12 GB" in r.stdout
    assert "rc=1" in r.stdout


def test_headroom_passes_at_floor(fake):
    fake.rule("free -g", _free(12))
    r = fake.run("pve_require_headroom; echo rc=$?")
    assert "rc=0" in r.stdout


def test_headroom_adds_the_containers_whole_memory_limit_to_the_floor(fake):
    fake.rule("free -g", _free(19))
    r = fake.run("pve_require_headroom none 8192 2>&1; echo rc=$?")
    assert "rc=1" in r.stdout
    assert (
        "host has 19 GB available now, the floor is 12 GB, "
        "the container's limit is 8 GB, so 20 GB are needed"
    ) in r.stdout
    assert not any("pct create" in c for c in fake.calls())


def test_headroom_passes_at_floor_plus_limit(fake):
    fake.rule("free -g", _free(20))
    r = fake.run("pve_require_headroom none 8192; echo rc=$?")
    assert "rc=0" in r.stdout


def test_headroom_rounds_the_limit_up_to_a_whole_gb(fake):
    fake.rule("free -g", _free(13))
    r = fake.run("pve_require_headroom none 1100 2>&1; echo rc=$?")  # 1.07 GB -> 2
    assert "need" in r.stdout and "14 GB are needed" in r.stdout and "rc=1" in r.stdout
    r = fake.run("pve_require_headroom none 1024 2>&1; echo rc=$?")  # exactly 1 GB
    assert "rc=0" in r.stdout


def test_headroom_gpu_floor_plus_the_gpu_shapes_memory(fake):
    _gpu_rules(fake, 37, (str(GIB), str(96 * GIB)))
    r = fake.run("pve_require_headroom gpu 32768 2>&1; echo rc=$?")
    assert "the floor is 22 GB" in r.stdout and "32 GB" in r.stdout
    assert "54 GB are needed" in r.stdout and "rc=1" in r.stdout


def test_headroom_need_prints_the_sum(fake):
    r = fake.run("pve_headroom_need none 8192; pve_headroom_need gpu 32768")
    assert r.stdout.split("\n")[:2] == ["20 12 8", "54 22 32"]
    bad = fake.run("pve_headroom_need none '1; id' 2>&1; echo rc=$?")
    assert "rc=1" in bad.stdout and "bad container memory" in bad.stdout


def test_headroom_rejects_a_bad_memory_figure(fake):
    fake.rule("free -g", _free(100))
    r = fake.run("pve_require_headroom none '1; id' 2>&1; echo rc=$?")
    assert "rc=1" in r.stdout and "bad container memory" in r.stdout


def test_headroom_refuses_when_host_unreadable(fake):
    fake.rule("free -g", "", 255)
    r = fake.run("pve_require_headroom 2>&1; echo rc=$?")
    assert "rc=1" in r.stdout


GIB = 1024**3


def _gpu_rules(fake, avail: int, *cards: tuple[str, str]):
    """One answer for the single read: free's table, then a `gpu USED TOTAL`
    line per card the host reports."""
    out = _free(avail) + "".join(f"gpu {u} {t}\n" for u, t in cards)
    fake.rule("free -g", out)


def test_headroom_prints_gpu_memory_when_reported(fake):
    _gpu_rules(fake, 30, (str(3 * GIB), str(96 * GIB)))
    r = fake.run("pve_require_headroom gpu 2>&1; echo rc=$?")
    assert "rc=0" in r.stdout
    assert "30 GB available" in r.stdout
    assert "gpu memory: 3 GB used of 96 GB" in r.stdout
    assert len(fake.calls()) == 1


def test_headroom_without_gpu_files_is_not_an_error(fake):
    fake.rule("free -g", _free(30))
    r = fake.run("pve_require_headroom gpu 2>&1; echo rc=$?")
    assert "rc=0" in r.stdout
    assert "gpu memory: not reported" in r.stdout


def test_gpu_shape_uses_the_higher_floor(fake):
    _gpu_rules(fake, 16, (str(GIB), str(96 * GIB)))
    ok = fake.run("pve_require_headroom none 2>&1; echo rc=$?")
    assert "rc=0" in ok.stdout
    for dev in ("gpu", "gpu+npu"):
        bad = fake.run(f"pve_require_headroom {dev} 2>&1; echo rc=$?")
        assert "rc=1" in bad.stdout
        assert "the floor is 22 GB" in bad.stdout and "16 GB available now" in bad.stdout
    assert not any("pct create" in c for c in fake.calls())


def test_floors_come_from_the_environment(fake):
    _gpu_rules(fake, 16)
    r = fake.run(
        "pve_require_headroom 2>&1; echo rc=$?",
        env={"HAL0_MATRIX_MIN_AVAILABLE_GB": "20"},
    )
    assert "the floor is 20 GB" in r.stdout and "rc=1" in r.stdout
    r = fake.run(
        "pve_require_headroom gpu 2>&1; echo rc=$?",
        env={"HAL0_MATRIX_MIN_AVAILABLE_GB_GPU": "15"},
    )
    assert "rc=0" in r.stdout
    r = fake.run(
        "pve_require_headroom gpu 2>&1; echo rc=$?",
        env={"HAL0_MATRIX_MIN_AVAILABLE_GB_GPU": "1; id"},
    )
    assert "rc=1" in r.stdout and "bad headroom floor" in r.stdout


def test_garbage_gpu_value_is_not_reported(fake):
    marker = fake.dir / "pwned"
    fake.rule(
        "free -g",
        _free(30)
        + f"gpu $(touch {marker}) 5\n"
        + "gpu 1;2 99999999999999999999999999\n"
        + "gpu -5 7\n",
    )
    r = fake.run("pve_require_headroom gpu 2>&1; echo rc=$?")
    assert "rc=0" in r.stdout
    assert "gpu memory: not reported" in r.stdout
    assert not marker.exists()


def test_headroom_sends_one_fixed_command(fake):
    _gpu_rules(fake, 30)
    fake.run("pve_require_headroom gpu", env={"HAL0_MATRIX_MIN_AVAILABLE_GB": "1"})
    fake.run("pve_require_headroom")
    first, second = fake.calls()[-2:]
    assert first == second
    assert "mem_info_gtt_used" in first and "mem_info_gtt_total" in first
    assert "$(" not in first.replace("$(cat", "")  # no value from the caller inside


def test_pve_run_passes_one_argument_with_batch_options(fake):
    fake.rule("echo hi", "hi\n")
    fake.run("pve_run 'echo hi; echo there'")
    fake.run("pve_run 'echo hi'")
    call = fake.calls()[-1]
    assert call == (
        "-n -o BatchMode=yes -o ConnectTimeout=10 -o ServerAliveInterval=15 "
        "-o ServerAliveCountMax=4 -- testpve echo hi"
    )


def test_pve_run_rejects_option_like_alias(fake):
    r = fake.run("pve_run 'echo hi'", env={"HAL0_PVE": "-oProxyCommand=id"})
    assert r.returncode == 1 and fake.calls() == []


def test_pve_run_leaves_callers_stdin_alone(fake):
    fake.rule("echo hi", "hi\n")
    r = fake.run(
        "printf 'a\\nb\\n' | while read -r x; do pve_run 'echo hi' >/dev/null; echo \"got $x\"; done"
    )
    assert r.stdout.split() == ["got", "a", "got", "b"]
    assert not fake.stdin_dump.exists() or fake.stdin_dump.read_bytes() == b""


# --- a create that may still be running ------------------------------------

PGREP_CMD = (
    "if pgrep -f '[p]ct create 172( |$)' >/dev/null 2>&1; then echo busy; else echo idle; fi"
)


def test_wait_create_done_asks_one_fixed_question(fake):
    fake.rule("pgrep -f", "idle\n")
    r = fake.run("ct_wait_create_done 172; echo rc=$?")
    assert "rc=0" in r.stdout
    assert fake.calls()[-1].endswith(f"-- testpve {PGREP_CMD}")


def test_wait_create_done_gives_up_after_its_bound(fake):
    fake.rule("pgrep -f", "busy\n")
    r = fake.run("ct_wait_create_done 172 1; echo rc=$?", env={"HAL0_MATRIX_POLL_SLEEP": "0"})
    assert "rc=1" in r.stdout and "still running" in r.stderr


def test_wait_create_done_does_not_take_a_dead_ssh_for_idle(fake):
    fake.rule("pgrep -f", "", 255)
    r = fake.run("ct_wait_create_done 172 1; echo rc=$?", env={"HAL0_MATRIX_POLL_SLEEP": "0"})
    assert "rc=1" in r.stdout


@pytest.mark.parametrize("bad", ["105", "17; id", "", "$(id)"])
def test_wait_create_done_refuses_a_bad_vmid_before_any_ssh(fake, bad):
    r = fake.run(f"ct_wait_create_done {shlex.quote(bad)} 1")
    assert r.returncode == 1 and fake.calls() == []


def test_lock_held_reads_the_lock_line(fake):
    fake.rule("pct config 172", "hostname: x\nlock: create\n")
    assert fake.run("ct_lock_held 172").returncode == 0


def test_lock_held_is_false_without_a_lock_line(fake):
    fake.rule("pct config 172", "hostname: x\ndescription: lock: not a real line\n")
    assert fake.run("ct_lock_held 172").returncode == 1


# --- deadlines --------------------------------------------------------------

TIMED_OUT = ("124", "137")  # timeout's status, or the KILL it sent


def _hangs_then_gives_up(fake, call, env_name, rules=()):
    for needle, out in rules:
        fake.rule(needle, out)
    started = __import__("time").monotonic()
    r = fake.run(f"{call}; echo rc=$?", env={env_name: "1"})
    took = __import__("time").monotonic() - started
    assert took < 20, f"the call was not cut off ({took:.0f}s)"
    return r


@pytest.mark.parametrize(
    "call,env_name,hang",
    [
        ("ct_exec 172 id", "HAL0_MATRIX_TIMEOUT_EXEC", "pct exec 172"),
        ("ct_start 172", "HAL0_MATRIX_TIMEOUT_START", "pct start 172"),
        ("ct_push 172 /etc/hostname /root/x", "HAL0_MATRIX_TIMEOUT_PULL", "pct exec 172"),
        ("ct_pull 172 /root/x /dev/null", "HAL0_MATRIX_TIMEOUT_PULL", "pct exec 172"),
    ],
)
def test_guest_calls_run_under_a_deadline(fake, tmp_path, call, env_name, hang):
    call = call.replace("/dev/null", str(tmp_path / "pulled"))
    _owned_rules(fake, 172)
    r = _hangs_then_gives_up(fake, call, env_name, [(hang, HANG)])
    assert re.search(r"rc=(124|137|1)\b", r.stdout), r.stdout
    assert "deadline" in r.stderr


def test_create_runs_under_a_deadline(fake):
    fake.rule("pvesm status --content rootdir", STORAGE)
    fake.rule("test -e /etc/pve/lxc/172.conf", "absent\n")
    r = _hangs_then_gives_up(
        fake,
        "ct_create 172 local:vztmpl/x.tar.zst 0 4 8192 40",
        "HAL0_MATRIX_TIMEOUT_CREATE",
        [("pct create", HANG)],
    )
    assert re.search(r"rc=(124|137)", r.stdout), r.stdout


def test_destroy_runs_under_a_deadline_and_reports_the_guest_still_there(fake):
    _owned_rules(fake, 172)
    fake.rule("test -e /etc/pve/lxc/172.conf", "present\n")
    r = _hangs_then_gives_up(
        fake, "ct_destroy 172", "HAL0_MATRIX_TIMEOUT_DESTROY", [("pct destroy 172", HANG)]
    )
    assert "rc=1" in r.stdout and "still present" in r.stderr


def test_a_bad_deadline_variable_falls_back_to_the_default(fake):
    fake.rule("echo hi", "hi\n")
    r = fake.run("pve_run 'echo hi'", env={"HAL0_MATRIX_TIMEOUT_READ": "1; id"})
    assert r.stdout.strip() == "hi"


# --- template and storage ---------------------------------------------------

TEMPLATES = (
    "NAME                                                         SIZE\n"
    "local:vztmpl/ubuntu-24.04-standard_24.04-2_amd64.tar.zst     130.00MB\n"
    "local:vztmpl/debian-12-standard_12.7-1_amd64.tar.zst         120.00MB\n"
    "local:vztmpl/ubuntu-26.04-standard_26.04-1_amd64.tar.zst     140.00MB\n"
)


def test_template_single_match(fake):
    fake.rule("pveam list local", TEMPLATES)
    r = fake.run("pve_template_volid debian-12")
    assert r.returncode == 0
    assert r.stdout.strip() == "local:vztmpl/debian-12-standard_12.7-1_amd64.tar.zst"


def test_template_no_match_lists_available(fake):
    fake.rule("pveam list local", TEMPLATES)
    r = fake.run("pve_template_volid fedora")
    assert r.returncode == 1
    assert r.stdout == ""
    assert "debian-12-standard" in r.stderr and "ubuntu-24.04" in r.stderr


def test_template_several_matches_refused(fake):
    fake.rule("pveam list local", TEMPLATES)
    r = fake.run("pve_template_volid ubuntu")
    assert r.returncode == 1
    assert "ubuntu-24.04" in r.stderr and "ubuntu-26.04" in r.stderr


def test_template_rejects_hostile_pattern(fake):
    r = fake.run("pve_template_volid 'x; rm -rf /'")
    assert r.returncode == 1
    assert fake.calls() == []


STORAGE = (
    "Name         Type     Status           Total       Used  Available        %\n"
    "local         dir     active       100000000   10000000   90000000   10.00%\n"
    "tank      zfspool     active       500000000   10000000  490000000    2.00%\n"
)


def test_storage_env_wins(fake):
    r = fake.run("pve_storage", env={"HAL0_MATRIX_STORAGE": "mystore"})
    assert r.stdout.strip() == "mystore" and fake.calls() == []  # no size to check


def test_storage_first_active_rootdir(fake):
    fake.rule("pvesm status --content rootdir", STORAGE)
    r = fake.run("pve_storage")
    assert r.stdout.strip() == "local"


def test_storage_skips_inactive(fake):
    fake.rule(
        "pvesm status --content rootdir",
        STORAGE.replace("local         dir     active", "local         dir   disabled"),
    )
    assert fake.run("pve_storage").stdout.strip() == "tank"


def test_storage_checked_prints_name_and_free_space(fake):
    fake.rule("pvesm status --content rootdir", STORAGE)
    r = fake.run("pve_storage 60")
    assert r.returncode == 0 and r.stdout.strip() == "local"
    assert "storage local: 85 GB free, need 70" in r.stderr


def test_storage_refuses_below_disk_plus_ten(fake):
    fake.rule("pvesm status --content rootdir", STORAGE)
    r = fake.run("pve_storage 76 2>&1; echo rc=$?")  # 85 free, 86 needed
    assert "rc=1" in r.stdout and "need 86" in r.stdout
    ok = fake.run("pve_storage 75; echo rc=$?")
    assert ok.returncode == 0 and "rc=0" in ok.stdout


def test_storage_from_env_is_checked_too(fake):
    fake.rule("pvesm status --content rootdir", STORAGE)
    ok = fake.run("pve_storage 60", env={"HAL0_MATRIX_STORAGE": "tank"})
    assert ok.stdout.strip() == "tank" and "storage tank: 467 GB free" in ok.stderr
    low = fake.run("pve_storage 500; echo rc=$?", env={"HAL0_MATRIX_STORAGE": "tank"})
    assert "rc=1" in low.stdout and low.stdout.strip().splitlines()[0] != "tank"


def test_storage_from_env_must_be_listed_and_active(fake):
    fake.rule(
        "pvesm status --content rootdir",
        STORAGE.replace("tank      zfspool     active", "tank      zfspool   disabled"),
    )
    for name in ("nowhere", "tank"):
        r = fake.run("pve_storage 10; echo rc=$?", env={"HAL0_MATRIX_STORAGE": name})
        assert "rc=1" in r.stdout


@pytest.mark.parametrize("avail", ["-", "1;2", "x", ""])
def test_storage_with_unreadable_free_space_is_refused(fake, avail):
    fake.rule(
        "pvesm status --content rootdir",
        f"Name Type Status Total Used Available %\nlocal dir active 100000000 1 {avail} 1.00%\n",
    )
    r = fake.run("pve_storage 10; echo rc=$?")
    assert "rc=1" in r.stdout


def test_create_checks_the_storage_space_too(fake):
    fake.rule("pvesm status --content rootdir", STORAGE)
    fake.rule("test -e /etc/pve/lxc/172.conf", "absent\n")
    r = fake.run("ct_create 172 local:vztmpl/x.tar.zst 0 4 8192 80")  # 85 free, 90 needed
    assert r.returncode == 90
    assert [c for c in fake.calls() if "pct create" in c] == []


def test_storage_rejects_hostile_env(fake):
    r = fake.run("pve_storage", env={"HAL0_MATRIX_STORAGE": "a;b"})
    assert r.returncode == 1


# --- vmid choice ------------------------------------------------------------


def test_pick_vmid_ignores_guests_outside_the_range(fake):
    fake.rule("pvesh get /cluster/resources", _guests((105, ""), (190, "other"), (169, "")))
    r = fake.run("pve_pick_vmid")
    assert r.returncode == 0 and r.stdout.strip() == "170"


@pytest.mark.parametrize("tags", ["", "other", "a;b"])
def test_pick_vmid_refuses_any_guest_inside_the_range(fake, tags):
    """Tagged or not: a guest in the range may be a run's container that has
    no tag yet, or somebody else's. The message names it."""
    fake.rule("pvesh get /cluster/resources", _guests((105, ""), (171, tags)))
    r = fake.run("pve_pick_vmid")
    assert r.returncode == 1
    assert r.stdout == ""
    assert "a guest already exists inside the harness range 170-189: 171" in r.stderr


def test_pick_vmid_refuses_when_matrix_guest_present(fake):
    fake.rule("pvesh get /cluster/resources", _guests((105, ""), (175, "x;hal0-matrix")))
    r = fake.run("pve_pick_vmid")
    assert r.returncode == 1
    assert r.stdout == ""
    assert "another matrix container is present: 175" in r.stderr


def test_pick_vmid_range_full(fake):
    fake.rule("pvesh get /cluster/resources", _guests((170, ""), (171, "")))
    r = fake.run(
        "pve_pick_vmid", env={"HAL0_MATRIX_VMID_MIN": "170", "HAL0_MATRIX_VMID_MAX": "171"}
    )
    assert r.returncode == 1 and r.stdout == ""


# --- the ownership test travels with the action (one remote command) -------

ACTIONS = [
    ("ct_start 172", "pct start 172"),
    ("ct_exec 172 'id'", "pct exec 172 -- bash -c"),
    ("ct_push 172 /etc/hostname /root/x", "pct exec 172 -- bash -c"),
    ("ct_pull 172 /root/x {tmp}", "pct exec 172 -- bash -c"),
    ("ct_apply_device_rules 172 0 226", "lxc.cgroup2.devices.allow"),
    ("ct_destroy 172", "pct destroy 172 --force --purge"),
]
GUARD_HOST = "pct config 172 | grep -q '^hostname: hal0-mx-172$'"
GUARD_TAG = "pct config 172 | grep -Eq '^tags: ([^;,]*[;,])*hal0-matrix([;,].*)?$'"


@pytest.mark.parametrize("call,marker", ACTIONS)
def test_every_state_changing_call_carries_the_ownership_test(fake, tmp_path, call, marker):
    _owned_rules(fake, 172)
    fake.rule("pct exec 172", "")
    fake.rule("pct start 172", "")
    fake.rule("/etc/pve/lxc/172.conf", "")
    fake.rule("pct destroy 172", "")
    fake.run(call.format(tmp=tmp_path / "out"))
    acting = [c for c in fake.calls() if marker in c]
    assert acting, fake.calls()
    one = acting[0]
    assert GUARD_HOST in one and GUARD_TAG in one
    assert one.index(GUARD_HOST) < one.index(marker)
    assert "; } && " in one[: one.index(marker)]


def test_the_guard_comes_before_the_pkill_of_a_destroy(fake):
    _owned_rules(fake, 172)
    fake.rule("pct destroy 172", "")
    fake.rule("test -e /etc/pve/lxc/172.conf", "absent\n")
    fake.run("ct_destroy 172")
    call = next(c for c in fake.calls() if "pkill" in c)
    assert call.index(GUARD_TAG) < call.index("pkill")
    assert "{ pkill" in call  # the whole sequence sits behind the guard


def test_the_guard_decides_in_the_same_command_when_the_config_changes_under_us(fake):
    """The separate check passes, then the guest's config changes: the action
    must not run, because the guard that travels with it sees the new config."""
    log = fake.dir / "executed.log"
    conf_ok = shlex.quote(TAGGED_CONF.format(vmid=172))
    conf_bad = shlex.quote(WRONG_HOST_CONF)
    (fake.dir / "handler.sh").write_text(
        "#!/usr/bin/env bash\n"
        'cmd="$1"\n'
        f'if [[ "$cmd" == "pct config 172" ]]; then printf "%s" {conf_ok}; exit 0; fi\n'
        + GUARD_PREAMBLE.replace(
            'conf="$(bash "$0" "pct config $vm")" || exit 1', f"conf={conf_bad}"
        )
        + f'echo "ran: $cmd" >> {shlex.quote(str(log))}; exit 0\n'
    )
    for call in ("ct_start 172", "ct_exec 172 id", "ct_destroy 172"):
        r = fake.run(call)
        assert r.returncode != 0, call
    ran = log.read_text() if log.exists() else ""
    # only the harmless presence read may have run; no action did
    assert not re.search(r"pct (start|exec|destroy)|pkill", ran), ran


def _guard_verdict(fake, vmid, conf, nonce=""):
    """Run the guard text itself, with a `pct` that prints `conf`."""
    script = (
        f'g="$(ct_guard {vmid} {nonce})"\n'
        f"export CONF={shlex.quote(conf)}\n"
        "pct() { printf '%s\\n' \"$CONF\"; }\n"
        "export -f pct\n"
        'bash -c "${g}true"; echo rc=$?\n'
    )
    return "rc=0" in fake.run(script).stdout


@pytest.mark.parametrize(
    "conf,vmid,nonce,expected",
    [
        (TAGGED_CONF.format(vmid=172), 172, "", True),
        (UNTAGGED_CONF.format(vmid=172), 172, "", False),
        (WRONG_HOST_CONF, 172, "", False),
        ("hostname: hal0-mx-173\ntags: hal0-matrix\n", 172, "", False),
        ("hostname: hal0-mx-172x\ntags: hal0-matrix\n", 172, "", False),
        ("hostname: hal0-mx-172\ntags: a;hal0-matrix\n", 172, "", True),
        ("hostname: hal0-mx-172\ntags: hal0-matrix,b\n", 172, "", True),
        ("hostname: hal0-mx-172\ntags: hal0-matrix-old;x\n", 172, "", False),
        ("hostname: hal0-mx-172\ntags: xhal0-matrix\n", 172, "", False),
        (NONCE_CONF.format(vmid=172), 172, NONCE, True),
        (TAGGED_CONF.format(vmid=172), 172, NONCE, False),
        (
            TAGGED_CONF.format(vmid=172) + "description: hal0-matrix run ffffffffffffffff\n",
            172,
            NONCE,
            False,
        ),
        (
            TAGGED_CONF.format(vmid=172) + "description: hal0-matrix%20run%20" + NONCE + "\n",
            172,
            NONCE,
            True,
        ),
        (
            TAGGED_CONF.format(vmid=172) + "description: hal0-matrix run " + NONCE + "0\n",
            172,
            NONCE,
            False,
        ),
    ],
)
def test_the_guard_text_accepts_only_what_ct_owned_accepts(fake, conf, vmid, nonce, expected):
    assert _guard_verdict(fake, vmid, conf, nonce) is expected


# --- the range is sanity-checked ---------------------------------------------

BAD_RANGES = [
    ("1", "999999999"),
    ("99", "120"),
    ("170", "234"),  # 65 VMIDs
    ("189", "170"),
    ("a", "b"),
    ("0170", "180"),
    ("170", "18 9"),
]


@pytest.mark.parametrize("lo,hi", BAD_RANGES)
def test_a_silly_range_refuses_everything_before_any_ssh(fake, lo, hi):
    env = {"HAL0_MATRIX_VMID_MIN": lo, "HAL0_MATRIX_VMID_MAX": hi}
    for call in (
        "ct_destroy 175",
        "ct_start 175",
        "ct_exec 175 id",
        "ct_owned 175",
        "pve_pick_vmid",
        "ct_create 175 local:vztmpl/x.tar.zst 1 4 8192 40",
    ):
        r = fake.run(call, env=env)
        assert r.returncode in (1, 90), (call, r.stderr)
    assert fake.calls() == []


def test_pick_vmid_names_the_bad_range(fake):
    r = fake.run(
        "pve_pick_vmid",
        env={"HAL0_MATRIX_VMID_MIN": "1", "HAL0_MATRIX_VMID_MAX": "999999999"},
    )
    assert "bad VMID range 1-999999999" in r.stderr


@pytest.mark.parametrize("lo,hi", [("100", "163"), ("170", "233"), ("170", "170"), ("200", "250")])
def test_a_sane_range_is_accepted(fake, lo, hi):
    env = {"HAL0_MATRIX_VMID_MIN": lo, "HAL0_MATRIX_VMID_MAX": hi}
    assert fake.run(f"_pve_in_range {lo}", env=env).returncode == 0
    assert fake.run(f"_pve_in_range {hi}", env=env).returncode == 0
    assert fake.run("pve_check_range", env=env).returncode == 0


# --- ownership guard --------------------------------------------------------


def _destroy_calls(fake):
    return [c for c in fake.calls() if "destroy" in c]


def test_destroy_refuses_out_of_range(fake):
    _owned_rules(fake, 105)
    r = fake.run("ct_destroy 105")
    assert r.returncode == 1
    assert _destroy_calls(fake) == []


def test_destroy_refuses_untagged_in_range(fake):
    _owned_rules(fake, 171, UNTAGGED_CONF)
    r = fake.run("ct_destroy 171")
    assert r.returncode == 1
    assert _destroy_calls(fake) == []


def test_destroy_refuses_wrong_hostname(fake):
    _owned_rules(fake, 171, WRONG_HOST_CONF)
    r = fake.run("ct_destroy 171")
    assert r.returncode == 1
    assert _destroy_calls(fake) == []


def test_destroy_refuses_when_config_unreadable(fake):
    fake.rule("pct config 171", "", 255)
    r = fake.run("ct_destroy 171")
    assert r.returncode == 1
    assert _destroy_calls(fake) == []


def test_tag_must_match_exactly(fake):
    _owned_rules(fake, 171, "hostname: hal0-mx-171\ntags: hal0-matrix-old;x\n")
    assert fake.run("ct_owned 171").returncode == 1


def test_tag_among_several_is_owned(fake):
    _owned_rules(fake, 171, "hostname: hal0-mx-171\ntags: a;hal0-matrix\n")
    assert fake.run("ct_owned 171").returncode == 0


@pytest.mark.parametrize(
    "bad", ["170; rm -rf /", "170 171", "$(id)", "", "-1", "17a", "0170", "1" * 12]
)
def test_destroy_refuses_hostile_vmid_before_any_ssh(fake, bad):
    r = fake.run(f"ct_destroy {shlex.quote(bad)}")
    assert r.returncode == 1
    assert fake.calls() == []


def test_destroy_owned_calls_destroy_and_verifies(fake):
    _owned_rules(fake, 172)
    fake.rule("pct destroy 172 --force --purge", "")
    fake.rule("test -e /etc/pve/lxc/172.conf", "absent\n")
    r = fake.run("ct_destroy 172; echo rc=$?")
    assert "rc=0" in r.stdout
    calls = fake.calls()
    kill = [c for c in calls if "pkill" in c]
    assert kill and "[l]xc-start -F -n 172" in kill[0]
    assert "rm -f /run/lock/lxc/pve-config-172.lock" in kill[0]
    assert "pct destroy 172 --force --purge" in kill[0]
    assert "test -e /etc/pve/lxc/172.conf" in calls[-1]


def test_destroy_returns_1_when_guest_still_there(fake):
    _owned_rules(fake, 172)
    fake.rule("pct destroy 172", "")
    fake.rule("test -e /etc/pve/lxc/172.conf", "present\n")
    r = fake.run("ct_destroy 172; echo rc=$?")
    assert "rc=1" in r.stdout
    assert len([c for c in fake.calls() if "pct destroy" in c]) == 2


def test_destroy_does_not_trust_a_dead_ssh_as_gone(fake):
    _owned_rules(fake, 172)
    fake.rule("pct destroy 172", "")
    fake.rule("test -e /etc/pve/lxc/172.conf", "", 255)
    r = fake.run("ct_destroy 172; echo rc=$?")
    assert "rc=1" in r.stdout


@pytest.mark.parametrize(
    "call",
    [
        "ct_exec {v} 'id'",
        "ct_start {v}",
        "ct_push {v} /etc/hostname /tmp/x",
        "ct_pull {v} /tmp/x /dev/null",
        "ct_apply_device_rules {v} 0 226",
    ],
)
@pytest.mark.parametrize("conf", [UNTAGGED_CONF, WRONG_HOST_CONF])
def test_guest_actions_refuse_unowned(fake, call, conf):
    _owned_rules(fake, 171, conf)
    r = fake.run(call.format(v=171))
    assert r.returncode == 1
    assert [c for c in fake.calls() if "pct config" not in c] == []


@pytest.mark.parametrize(
    "call",
    ["ct_exec 105 id", "ct_start 105", "ct_apply_device_rules 105 0 226", "ct_destroy 105"],
)
def test_guest_actions_refuse_out_of_range_without_ssh(fake, call):
    r = fake.run(call)
    assert r.returncode == 1
    assert fake.calls() == []


# --- create -----------------------------------------------------------------


def _create(fake, tail="", env=None):
    fake.rule("pvesm status --content rootdir", STORAGE)
    fake.rule("pct create", "")
    fake.rule("test -e /etc/pve/lxc/172.conf", "absent\n")
    return fake.run(
        "ct_create 172 local:vztmpl/ubuntu-26.04-standard_26.04-1_amd64.tar.zst 0 4 8192 40 "
        + tail,
        env=env,
    )


def test_create_mirrors_proxmox_script_features(fake):
    r = _create(fake, "/dev/dri/renderD128,gid=44 /dev/kfd")
    assert r.returncode == 0, r.stderr
    call = next(c for c in fake.calls() if "pct create" in c)
    assert FEATURES in call
    assert "--tags hal0-matrix" in call
    assert "--hostname hal0-mx-172" in call
    assert "--swap 0" in call and "--cores 4 --memory 8192" in call and "--onboot 0" in call
    assert "--net0 name=eth0,bridge=vmbr0,ip=dhcp" in call
    assert "--rootfs local:40" in call
    assert "--unprivileged 0" in call
    assert "--dev0 /dev/dri/renderD128,gid=44" in call
    assert "--dev1 /dev/kfd" in call


def test_create_uses_bridge_env(fake):
    _create(fake, env={"HAL0_MATRIX_BRIDGE": "vmbr9"})
    call = next(c for c in fake.calls() if "pct create" in c)
    assert "bridge=vmbr9" in call


@pytest.mark.parametrize(
    "args",
    [
        "190 local:vztmpl/x.tar.zst 0 4 8192 40",
        "105 local:vztmpl/x.tar.zst 0 4 8192 40",
        "172 'local:vztmpl/x;id' 0 4 8192 40",
        "172 local:vztmpl/x.tar.zst 2 4 8192 40",
        "172 local:vztmpl/x.tar.zst 0 4x 8192 40",
        "172 local:vztmpl/x.tar.zst 0 4 8192 40 '/dev/kfd; id'",
        "172 local:vztmpl/x.tar.zst 0 4 8192 40 /etc/passwd",
        "172 local:vztmpl/x.tar.zst 0 4 8192 40 '/dev/dri/../../etc/x'",
    ],
)
def test_create_refuses_bad_arguments(fake, args):
    fake.rule("pvesm status --content rootdir", STORAGE)
    r = fake.run(f"ct_create {args}")
    assert r.returncode == 90  # refused: nothing was created
    assert [c for c in fake.calls() if "pct create" in c] == []


def test_create_refuses_an_existing_vmid(fake):
    fake.rule("pvesm status --content rootdir", STORAGE)
    fake.rule("test -e /etc/pve/lxc/172.conf", "present\n")
    r = fake.run("ct_create 172 local:vztmpl/x.tar.zst 0 4 8192 40")
    assert r.returncode == 90  # refused: nothing was created
    assert [c for c in fake.calls() if "pct create" in c] == []


def test_create_refuses_when_presence_is_unknown(fake):
    fake.rule("pvesm status --content rootdir", STORAGE)
    fake.rule("test -e /etc/pve/lxc/172.conf", "", 255)
    r = fake.run("ct_create 172 local:vztmpl/x.tar.zst 0 4 8192 40")
    assert r.returncode == 90
    assert [c for c in fake.calls() if "pct create" in c] == []


def test_create_writes_the_run_nonce_into_the_description(fake):
    fake.rule("pvesm status --content rootdir", STORAGE)
    fake.rule("test -e /etc/pve/lxc/172.conf", "absent\n")
    fake.rule("pct create", "")
    r = fake.run("CT_NONCE=0123456789abcdef; ct_create 172 local:vztmpl/x.tar.zst 0 4 8192 40")
    assert r.returncode == 0, r.stderr
    call = [c for c in fake.calls() if "pct create" in c][-1]
    tokens = shlex.split(call)
    assert tokens[tokens.index("--description") + 1] == "hal0-matrix run 0123456789abcdef"


def test_create_refuses_a_malformed_nonce(fake):
    r = fake.run("CT_NONCE='x; id'; ct_create 172 local:vztmpl/x.tar.zst 0 4 8192 40")
    assert r.returncode == 90
    assert fake.calls() == []


def test_destroy_with_nonce_refuses_a_guest_with_another_description(fake):
    _owned_rules(fake, 172, TAGGED_CONF)  # tag and hostname fit, no nonce
    r = fake.run(f"ct_destroy 172 {NONCE}")
    assert r.returncode == 1
    assert _destroy_calls(fake) == []


def test_destroy_with_nonce_refuses_another_runs_nonce(fake):
    _owned_rules(fake, 172, TAGGED_CONF + "description: hal0-matrix run ffffffffffffffff\n")
    r = fake.run(f"ct_destroy 172 {NONCE}")
    assert r.returncode == 1
    assert _destroy_calls(fake) == []


def test_destroy_with_nonce_destroys_the_guest_that_carries_it(fake):
    _owned_rules(fake, 172, NONCE_CONF)
    fake.rule("pct destroy 172 --force --purge", "")
    fake.rule("test -e /etc/pve/lxc/172.conf", "absent\n")
    r = fake.run(f"ct_destroy 172 {NONCE}; echo rc=$?")
    assert "rc=0" in r.stdout
    assert any("pct destroy 172" in c for c in fake.calls())


def test_destroy_without_nonce_keeps_the_manual_rule(fake):
    _owned_rules(fake, 172, TAGGED_CONF)
    fake.rule("pct destroy 172 --force --purge", "")
    fake.rule("test -e /etc/pve/lxc/172.conf", "absent\n")
    assert fake.run("ct_destroy 172").returncode == 0


@pytest.mark.parametrize("bad", ["12345", "0123456789ABCDEF", "x; id", "0123456789abcde"])
def test_destroy_refuses_a_malformed_nonce_before_any_ssh(fake, bad):
    r = fake.run(f"ct_destroy 172 {shlex.quote(bad)}")
    assert r.returncode == 1
    assert fake.calls() == []


# Every `var_*` default ct/hal0.sh declares, sorted into the ones that become a
# `pct create --features` flag (with the value map to the harness's flag) and
# the ones that do not. A variable in neither set fails the test, so a new one
# (say var_tun) or a removed one is noticed and decided on here.
HELPER_FEATURE_VARS = {
    "fuse": ("fuse", {"yes": "1"}),
    "keyctl": ("keyctl", {"1": "1"}),
    "mknod": ("mknod", {"1": "1"}),
}
HELPER_NON_FEATURE_VARS = {
    "tags",
    "cpu",
    "ram",
    "disk",
    "os",
    "version",
    "arm64",
    "unprivileged",
    "gpu",
}
# The helper never sets nesting: the engine does. Sources: README.md (the
# ct/hal0.sh row of the file table) and docs/getting-started/proxmox.mdx (the
# features line of the manual recipe, and the sentence saying nesting is on).
HELPER_ENGINE_FEATURES = {"nesting": "1"}
PROXMOX_MDX = REPO / "docs" / "getting-started" / "proxmox.mdx"


def _helper_var_defaults(path: Path) -> dict[str, str]:
    # Both `var_x="${var_x:-v}"` and a plain `var_x="v"` count as a declaration.
    found = {}
    pattern = r'^var_(\w+)="(?:\$\{var_\w+:-([^}]*)\}|([^"$]*))"'
    for m in re.finditer(pattern, path.read_text(), re.M):
        found[m.group(1)] = m.group(2) if m.group(2) is not None else m.group(3)
    return found


def _helper_features(path: Path) -> str:
    """The features string the helper script declares, or an AssertionError."""
    found = _helper_var_defaults(path)
    unknown = set(found) - set(HELPER_FEATURE_VARS) - HELPER_NON_FEATURE_VARS
    assert not unknown, f"ct/hal0.sh declares var_* the test has no mapping for: {sorted(unknown)}"
    missing = (set(HELPER_FEATURE_VARS) | HELPER_NON_FEATURE_VARS) - set(found)
    assert not missing, f"ct/hal0.sh no longer declares: {sorted(missing)}"
    flags = dict(HELPER_ENGINE_FEATURES)
    for var, (flag, values) in HELPER_FEATURE_VARS.items():
        assert found[var] in values, f"var_{var}={found[var]!r} has no mapping to a flag"
        flags[flag] = values[found[var]]
    order = ("nesting", "keyctl", "fuse", "mknod")
    assert set(flags) == set(order)
    return ",".join(f"{k}={flags[k]}" for k in order)


def test_feature_list_matches_proxmox_script():
    theirs = _helper_features(HELPER_CT_SH)
    # Nesting is set by the engine, not by the script; pin the sources.
    readme_row = next(
        (ln for ln in HELPER_README.read_text().splitlines() if ln.startswith("| `ct/hal0.sh`")),
        "",
    )
    assert "nesting" in readme_row, "README no longer lists nesting for ct/hal0.sh"
    mdx = PROXMOX_MDX.read_text()
    assert re.search(r"--features\s+nesting=1,keyctl=1,fuse=1,mknod=1", mdx), (
        "docs/getting-started/proxmox.mdx must contain the recipe line "
        "`--features nesting=1,keyctl=1,fuse=1,mknod=1`"
    )
    assert re.search(r"nesting\s+is\s+already\s+on", mdx), (
        "docs/getting-started/proxmox.mdx must contain a sentence saying 'nesting is already on'"
    )
    mine = re.search(r'^_PVE_FEATURES="([^"]*)"', PVE_SH.read_text(), re.M)
    assert mine, "_PVE_FEATURES not found in pve.sh"
    assert mine.group(1) == theirs == FEATURES


def test_feature_parser_fails_on_a_new_helper_variable(tmp_path):
    changed = tmp_path / "hal0.sh"
    changed.write_text(
        HELPER_CT_SH.read_text().replace("var_mknod=", 'var_tun="yes"\nvar_mknod=', 1)
    )
    assert _helper_features(HELPER_CT_SH) == FEATURES
    with pytest.raises(AssertionError, match="tun"):
        _helper_features(changed)


def test_feature_parser_fails_on_an_unmapped_value(tmp_path):
    changed = tmp_path / "hal0.sh"
    changed.write_text(
        HELPER_CT_SH.read_text().replace(
            'var_fuse="${var_fuse:-yes}"', 'var_fuse="${var_fuse:-no}"'
        )
    )
    with pytest.raises(AssertionError, match="no mapping"):
        _helper_features(changed)


# --- device rules, exec, push/pull -----------------------------------------


def test_apply_device_rules_appends_idempotently(fake):
    _owned_rules(fake, 172)
    fake.rule("/etc/pve/lxc/172.conf", "")
    r = fake.run("ct_apply_device_rules 172 0 226 238 226")
    assert r.returncode == 0, r.stderr
    call = next(c for c in fake.calls() if "/etc/pve/lxc/172.conf" in c)
    assert call.count("lxc.cgroup2.devices.allow:") >= 2
    assert "c\\ 226:\\*\\ rwm" in call and "c\\ 238:\\*\\ rwm" in call
    assert "lxc.prlimit.memlock:\\ unlimited" in call
    assert "grep -qxF" in call
    # 226 is deduplicated
    assert call.count("c\\ 226:") == 2  # once in grep, once in printf


@pytest.mark.parametrize("bad", ["226;id", "x", "-1", ""])
def test_apply_device_rules_rejects_bad_major(fake, bad):
    _owned_rules(fake, 172)
    r = fake.run(f"ct_apply_device_rules 172 0 {shlex.quote(bad)}")
    assert r.returncode == 1
    assert [c for c in fake.calls() if "/etc/pve/lxc/172.conf" in c] == []


def test_exec_quotes_command_as_one_argument(fake):
    _owned_rules(fake, 172)
    fake.rule("pct exec 172", "")
    cmd = "echo hi; id && rm -rf '/tmp/x y'"
    fake.run(f"ct_exec 172 {shlex.quote(cmd)}")
    call = next(c for c in fake.calls() if "pct exec" in c)
    tokens = shlex.split(call)
    assert tokens[-5:-2] == ["172", "--", "bash"]
    assert tokens[-2:] == ["-c", cmd]
    assert " -t" not in call.replace("testpve", "")


def test_start_runs_pct_start(fake):
    _owned_rules(fake, 172)
    fake.rule("pct start 172", "")
    assert fake.run("ct_start 172").returncode == 0
    assert any(c.endswith("pct start 172") for c in fake.calls())


def test_push_and_pull_quote_paths(fake, tmp_path):
    _owned_rules(fake, 172)
    fake.rule("pct exec 172", "")
    fake.run("ct_push 172 /etc/hostname '/root/a b; id'")
    fake.run(f"ct_pull 172 '/root/a b; id' {shlex.quote(str(tmp_path / 'out'))}")
    execs = [c for c in fake.calls() if "pct exec" in c]
    assert len(execs) == 2
    push_inner = shlex.split(execs[0])[-1]
    pull_inner = shlex.split(execs[1])[-1]
    assert shlex.split(push_inner) == ["cat", ">", "/root/a b; id"]
    # A regular file only, tested in the same remote command as the read.
    assert shlex.split(pull_inner) == [
        "test", "-f", "/root/a b; id", "&&", "cat", "/root/a b; id",
    ]  # fmt: skip


def test_wait_network_succeeds_when_resolves(fake):
    _owned_rules(fake, 172)
    fake.rule("pct exec 172", "")
    r = fake.run("ct_wait_network 172 5; echo rc=$?")
    assert "rc=0" in r.stdout
    assert "hal0.dev" in next(c for c in fake.calls() if "pct exec" in c)


def test_wait_network_times_out(fake):
    _owned_rules(fake, 172)
    fake.rule("pct exec 172", "", 1)
    r = fake.run("ct_wait_network 172 1; echo rc=$?", env={"HAL0_MATRIX_POLL_SLEEP": "0"})
    assert "rc=1" in r.stdout


# --- host-devices.sh --------------------------------------------------------


def _devtree(tmp_path: Path) -> Path:
    root = tmp_path / "devroot"
    (root / "dev" / "dri").mkdir(parents=True)
    (root / "dev" / "accel").mkdir(parents=True)
    (root / "dev" / "dri" / "renderD128").write_text("")
    (root / "dev" / "dri" / "renderD129").write_text("")
    (root / "dev" / "dri" / "by-link").symlink_to("renderD128")
    (root / "dev" / "dri" / "renderD130").symlink_to("renderD129")
    (root / "dev" / "kfd").write_text("")
    (root / "dev" / "accel" / "accel0").write_text("")
    return root


def _stat_stub(tmp_path: Path) -> Path:
    b = tmp_path / "statbin"
    b.mkdir()
    s = b / "stat"
    s.write_text(
        "#!/usr/bin/env bash\n"
        'fmt="$2"; path="$3"\n'
        'case "$fmt:$path" in\n'
        "  %g:*renderD128) echo 44;;\n"
        "  %g:*renderD129) echo 44;;\n"
        "  %g:*kfd) echo 0;;\n"
        "  %g:*accel0) echo 109;;\n"
        "  %t:*renderD*) echo e2;;\n"
        "  %t:*kfd) echo f0;;\n"
        "  %t:*accel0) echo ea;;\n"
        "  *) exit 1;;\n"
        "esac\n"
    )
    s.chmod(0o755)
    return b


def _host_devices(tmp_path: Path, arg: str):
    root = _devtree(tmp_path)
    stub = _stat_stub(tmp_path)
    return subprocess.run(
        ["bash", str(HOST_DEVICES_SH), arg],
        env={
            "PATH": f"{stub}:/usr/bin:/bin",
            "HAL0_MATRIX_DEV_ROOT": str(root),
        },
        capture_output=True,
        text=True,
        check=False,
        timeout=30,
    )


def test_host_devices_skips_symlinks_and_zero_gid(tmp_path):
    r = _host_devices(tmp_path, "gpu")
    assert r.returncode == 0, r.stderr
    lines = r.stdout.splitlines()
    assert "dev /dev/dri/renderD128,gid=44" in lines
    assert "dev /dev/dri/renderD129,gid=44" in lines
    assert "dev /dev/kfd" in lines  # gid 0: no gid=
    assert not any("by-link" in x or "renderD130" in x for x in lines)
    assert not any("accel" in x for x in lines)
    assert sorted(x for x in lines if x.startswith("major ")) == [
        "major 226",
        "major 240",
    ]
    assert not any(str(tmp_path) in x for x in lines)


def test_host_devices_gpu_plus_npu_adds_accel(tmp_path):
    r = _host_devices(tmp_path, "gpu+npu")
    lines = r.stdout.splitlines()
    assert "dev /dev/accel/accel0,gid=109" in lines
    assert "major 234" in lines


def test_host_devices_rejects_unknown_mode(tmp_path):
    r = _host_devices(tmp_path, "everything")
    assert r.returncode != 0 and r.stdout == ""


# --- privileged vs unprivileged raw config ---------------------------------


def _rules_call(fake, unpriv):
    _owned_rules(fake, 172)
    fake.rule("/etc/pve/lxc/172.conf", "")
    r = fake.run(f"ct_apply_device_rules 172 {unpriv} 226 240")
    assert r.returncode == 0, r.stderr
    return next(c for c in fake.calls() if "/etc/pve/lxc/172.conf" in c)


def test_privileged_gpu_guest_gets_apparmor_unconfined(fake):
    call = _rules_call(fake, 0)
    assert "lxc.apparmor.profile:\\ unconfined" in call
    assert "c\\ 226:" in call and "c\\ 240:" in call
    assert "lxc.prlimit.memlock:\\ unlimited" in call


def test_unprivileged_guest_gets_no_apparmor_line(fake):
    call = _rules_call(fake, 1)
    assert "apparmor" not in call
    assert "c\\ 226:" in call and "lxc.prlimit.memlock" in call


def test_device_rules_reject_bad_privilege_flag(fake):
    _owned_rules(fake, 172)
    r = fake.run("ct_apply_device_rules 172 2 226")
    assert r.returncode == 1
    assert [c for c in fake.calls() if "/etc/pve/lxc/172.conf" in c] == []


def test_device_rules_match_proxmox_script_lines():
    """Lockstep for what the helper script and the harness still share."""
    mine = PVE_SH.read_text()
    devices = HOST_DEVICES_SH.read_text()
    npu = HELPER_NPU_SH.read_text()
    install = HELPER_INSTALL_SH.read_text()
    # The memlock line: the helper writes it in hal0-npu-passthrough (the
    # engine does not), the harness writes it for every guest it gives devices.
    assert 'MEMLOCK_LINE="lxc.prlimit.memlock: unlimited"' in npu
    assert "lxc.prlimit.memlock: unlimited" in mine
    # The nodes both sides forward: render nodes, the kfd node, NPU nodes.
    assert "/dev/dri/renderD*" in install and "/dev/dri/renderD" in devices
    assert "/dev/accel/accel*" in npu and "/dev/accel/accel" in devices
    assert "/dev/kfd" in HELPER_README.read_text() and "emit /dev/kfd" in devices
    # A device is forwarded as a devN entry carrying gid=, on both sides.
    assert "--dev${idx}" in npu and "gid=" in npu
    assert "--dev${i}" in mine and "gid=${gid}" in devices


# Where the harness differs from the helper script. Each row is pinned on both
# sides: the helper's side is asserted below, the harness's side by the tests
# above and by test_privileged_gpu_guest_gets_apparmor_unconfined. If the helper
# changes one of these, this table fails and the harness decision is revisited.
#
#   fact                       helper script                      harness            because
#   cgroup2 allow, unpriv.     none (devN entry grants access)    c MAJ:* rwm        harness predates the rebuilt script
#   cgroup2 allow, privileged  `a` (every device)                 c MAJ:* rwm        harness keeps the narrow per-major rule
#   apparmor, privileged       no line (add by hand)              unconfined         harness privileged shape needs it to start slots
#   cap.drop, privileged       cleared (`lxc.cap.drop:`)          not written        harness leaves the default drop list
#   memlock                    only via hal0-npu-passthrough      every device guest harness needs ROCm pinning without the NPU step
#   NPU at create time         after create, passthrough script   at create          harness has no second reboot step
#   device gid=                container's render/video gid       host node's gid    harness reads the host only
#   /dev/dri/card*             forwarded                          not forwarded      harness forwards render nodes only
#
# Not in the table: mount entries, idmaps and AppArmor for unprivileged guests
# are owned by the external engine and cannot be read from this repo
# (unverified). The harness's own create settings (swap, onboot, tags, rootfs,
# net) differ from the helper's defaults (8192 MB, 40 GB) on purpose.
def test_helper_script_differences_are_pinned():
    ct = HELPER_CT_SH.read_text()
    npu = HELPER_NPU_SH.read_text()
    install = HELPER_INSTALL_SH.read_text()
    readme = HELPER_README.read_text()
    mine = PVE_SH.read_text()
    devices = HOST_DEVICES_SH.read_text()
    helper_sources = ct + npu + install
    # Helper default is unprivileged; the harness picks per shape.
    assert 'var_unprivileged="${var_unprivileged:-1}"' in ct
    # No per-major cgroup2 rule and no apparmor line anywhere in the helper
    # sources; the privileged engine rule is documented in the README.
    assert not re.search(r"lxc\.cgroup2\.devices\.allow:\s*c", helper_sources)
    assert not re.search(r"lxc\.apparmor\.profile\s*:", helper_sources)
    assert "lxc.cgroup2.devices.allow: a" in readme
    assert "lxc.cap.drop:" in readme
    assert "It writes no AppArmor line" in readme
    assert "lxc.cap.drop" not in mine
    assert "lxc.cgroup2.devices.allow: c ${maj}:* rwm" in mine
    assert "lxc.apparmor.profile: unconfined" in mine
    # Memlock lives in the NPU tool only; ct/hal0.sh never writes it.
    assert "memlock" not in ct
    # The helper puts the NPU in after create; the harness at create time.
    assert "after the container exists" in readme
    assert "gpu+npu" in devices
    # The helper takes the container's group ids; the harness the host's.
    assert "container's own `render` and `video` group ids" in readme
    assert "stat -c '%g'" in devices
    # The helper forwards card nodes too; the harness does not.
    assert "/dev/dri/card*" in readme
    assert "/dev/dri/card" not in devices


# --- stay alive under the caller's set -euo pipefail ------------------------

MARK = "MARKER-REACHED"


def test_strict_destroy_of_gone_guest_continues(fake):
    _owned_rules(fake, 172)
    fake.rule("pct destroy 172", "")
    fake.rule("test -e /etc/pve/lxc/172.conf", "absent\n")
    r = fake.run(f"ct_destroy 172\necho {MARK}", strict=True)
    assert r.returncode == 0 and MARK in r.stdout, r.stderr


def test_strict_destroy_failure_reports_before_returning(fake):
    _owned_rules(fake, 172)
    fake.rule("pct destroy 172", "boom: storage busy\n", 5)
    fake.rule("test -e /etc/pve/lxc/172.conf", "present\n")
    r = fake.run("ct_destroy 172", strict=True)
    assert r.returncode == 1
    assert "still present" in r.stderr and "boom: storage busy" in r.stderr


def test_strict_create_failure_reaches_its_own_diagnostic(fake):
    fake.rule("pvesm status --content rootdir", STORAGE)
    fake.rule("test -e /etc/pve/lxc/172.conf", "absent\n")
    fake.rule("pct create", "", 7)
    r = fake.run("ct_create 172 local:vztmpl/x.tar.zst 0 4 8192 40", strict=True)
    assert r.returncode == 7  # pct's status, passed on after the message
    assert "pct create failed for 172 (exit 7)" in r.stderr


def test_strict_create_success_continues(fake):
    fake.rule("pvesm status --content rootdir", STORAGE)
    fake.rule("test -e /etc/pve/lxc/172.conf", "absent\n")
    fake.rule("pct create", "")
    r = fake.run(f"ct_create 172 local:vztmpl/x.tar.zst 0 4 8192 40\necho {MARK}", strict=True)
    assert MARK in r.stdout, r.stderr


@pytest.mark.parametrize(
    "body,rules",
    [
        ("pve_mem_available_gb", [("free -g", _free(20))]),
        ("pve_require_headroom", [("free -g", _free(20))]),
        ("pve_template_volid debian-12", [("pveam list local", TEMPLATES)]),
        ("pve_storage", [("pvesm status --content rootdir", STORAGE)]),
        ("pve_pick_vmid", [("pvesh get /cluster/resources", _guests((105, "")))]),
        ("ct_owned 172", [("pct config 172", TAGGED_CONF.format(vmid=172))]),
        (
            "ct_start 172",
            [("pct config 172", TAGGED_CONF.format(vmid=172)), ("pct start", "")],
        ),
        (
            "ct_exec 172 id",
            [("pct config 172", TAGGED_CONF.format(vmid=172)), ("pct exec", "")],
        ),
        (
            "ct_wait_network 172 5",
            [("pct config 172", TAGGED_CONF.format(vmid=172)), ("pct exec", "")],
        ),
        (
            "ct_apply_device_rules 172 0 226",
            [("pct config 172", TAGGED_CONF.format(vmid=172)), ("172.conf", "")],
        ),
    ],
)
def test_strict_success_paths_continue(fake, body, rules):
    for needle, out in rules:
        fake.rule(needle, out)
    r = fake.run(f"{body} >/dev/null\necho {MARK}", strict=True)
    assert MARK in r.stdout and r.returncode == 0, r.stderr


@pytest.mark.parametrize(
    "body,rules",
    [
        ("pve_require_headroom", [("free -g", _free(3))]),
        ("pve_template_volid nothing", [("pveam list local", TEMPLATES)]),
        ("pve_pick_vmid", [("pvesh get /cluster/resources", _guests((175, "hal0-matrix")))]),
        ("ct_owned 172", [("pct config 172", UNTAGGED_CONF.format(vmid=172))]),
        ("ct_owned 105", []),
    ],
)
def test_strict_failures_return_one_not_abort_elsewhere(fake, body, rules):
    for needle, out in rules:
        fake.rule(needle, out)
    r = fake.run(f"{body} >/dev/null 2>&1", strict=True)
    assert r.returncode == 1


# --- push and pull move the bytes intact -----------------------------------

BLOBS = {
    "nul": b"a\x00b\x00\xffc\n",
    "no-newline": b"tail without newline",
    "empty": b"",
}


@pytest.mark.parametrize("name", list(BLOBS))
def test_push_delivers_bytes_intact(fake, tmp_path, name):
    _owned_rules(fake, 172)
    fake.rule("pct exec 172", "")
    src = tmp_path / "src.bin"
    src.write_bytes(BLOBS[name])
    r = fake.run(f"ct_push 172 {shlex.quote(str(src))} /root/dest.bin")
    assert r.returncode == 0, r.stderr
    assert fake.stdin_dump.read_bytes() == BLOBS[name]


@pytest.mark.parametrize("name", list(BLOBS))
def test_pull_writes_bytes_intact(fake, tmp_path, name):
    _owned_rules(fake, 172)
    fake.rule("pct exec 172", BLOBS[name])
    dst = tmp_path / "dst.bin"
    r = fake.run(f"ct_pull 172 /root/src.bin {shlex.quote(str(dst))}")
    assert r.returncode == 0, r.stderr
    assert dst.read_bytes() == BLOBS[name]
    assert [p.name for p in tmp_path.iterdir() if p.name != "fake"] == ["dst.bin"]


def test_pull_failure_leaves_no_file(fake, tmp_path):
    _owned_rules(fake, 172)
    fake.rule("pct exec 172", b"partial", 1)
    dst = tmp_path / "dst.bin"
    r = fake.run(f"ct_pull 172 /root/src.bin {shlex.quote(str(dst))}")
    assert r.returncode == 1
    assert [p.name for p in tmp_path.iterdir() if p.name != "fake"] == []


def test_pull_failure_keeps_an_existing_destination(fake, tmp_path):
    _owned_rules(fake, 172)
    fake.rule("pct exec 172", b"partial", 1)
    dst = tmp_path / "dst.bin"
    dst.write_bytes(b"old")
    fake.run(f"ct_pull 172 /root/src.bin {shlex.quote(str(dst))}")
    assert dst.read_bytes() == b"old"


# --- smaller behaviours -----------------------------------------------------


def test_destroy_shows_refusal_reason_and_destroy_output(fake):
    _owned_rules(fake, 171, UNTAGGED_CONF)
    r = fake.run("ct_destroy 171")
    assert r.returncode == 1 and "not a harness-owned container" in r.stderr


def test_destroy_rechecks_ownership_on_second_attempt(fake, tmp_path):
    """First ownership read is ours, the second shows a foreign guest: the
    second destroy attempt must not happen."""
    counter = fake.dir / "n"
    counter.write_text("0")
    conf_ok = shlex.quote(TAGGED_CONF.format(vmid=172))
    conf_bad = shlex.quote(WRONG_HOST_CONF)
    c = shlex.quote(str(counter))
    (fake.dir / "handler.sh").write_text(
        "#!/usr/bin/env bash\n"
        'cmd="$1"\n'
        'if [[ "$cmd" == "pct config 172" ]]; then\n'
        f"  n=$(cat {c}); echo $((n+1)) > {c}\n"
        f'  if [[ "$n" -ge 1 ]]; then printf "%s" {conf_bad}; else printf "%s" {conf_ok}; fi\n'
        "  exit 0\n"
        "fi\n"
        + GUARD_PREAMBLE.replace(
            'conf="$(bash "$0" "pct config $vm")" || exit 1', f"conf={conf_ok}"
        )
        + 'if [[ "$cmd" == *"test -e /etc/pve/lxc/172.conf"* ]]; then echo present; exit 0; fi\n'
        'if [[ "$cmd" == *"pct destroy 172"* ]]; then exit 0; fi\n'
        'echo "unexpected: $cmd" >&2; exit 97\n'
    )
    r = fake.run("ct_destroy 172")
    assert r.returncode == 1
    assert len([x for x in fake.calls() if "pct destroy" in x]) == 1
    assert "not a harness-owned container" in r.stderr


def test_wait_network_shows_refusal_reason(fake):
    _owned_rules(fake, 171, UNTAGGED_CONF)
    r = fake.run("ct_wait_network 171 1")
    assert r.returncode == 1 and "not a harness-owned container" in r.stderr


def test_wait_network_timeout_includes_last_error(fake):
    _owned_rules(fake, 172)
    fake.rule("pct exec 172", "", 1)
    r = fake.run("ct_wait_network 172 0")
    assert r.returncode == 1 and "did not resolve hal0.dev" in r.stderr


def test_pick_vmid_malformed_json_is_one_clear_line(fake):
    fake.rule("pvesh get /cluster/resources", "<html>not json")
    r = fake.run("pve_pick_vmid")
    assert r.returncode == 1
    assert r.stderr.strip() == "cannot parse the guest list from the host"


# --- F4: what a guest sends is cut on this side -----------------------------

CAP = "4096"


def _noisy_ssh(fake: Fake, out: int = 0, err: int = 0, rc: int = 0) -> None:
    """Replace the fake ssh with one that prints ``out`` bytes to stdout and
    ``err`` bytes to stderr, whatever it is asked."""
    assert out <= 1048576 and err <= 1048576, "a test source is at most 1 MiB"
    ssh = fake.bin / "ssh"
    ssh.write_text(
        "#!/usr/bin/env bash\n"
        f"head -c {out} /dev/zero | tr '\\0' o\n"
        f"head -c {err} /dev/zero | tr '\\0' e >&2\n"
        f"exit {rc}\n"
    )


def _cap_env(**extra: str) -> dict:
    return {"HAL0_MATRIX_MAX_STREAM_BYTES": CAP, **extra}


def test_a_stream_from_the_guest_is_cut_at_the_cap_and_noted(fake, tmp_path):
    _noisy_ssh(fake, out=20000)
    got, note = tmp_path / "got", tmp_path / "note.txt"
    r = fake.run(
        f"PVE_NOTE_FILE={shlex.quote(str(note))}\n"
        f"_PVE_STREAM=pre pve_run x >{shlex.quote(str(got))}; echo rc=$?",
        env=_cap_env(),
    )
    assert "rc=0" in r.stdout, r.stderr
    assert got.stat().st_size == int(CAP)
    assert "truncated=pre" in note.read_text().splitlines()


def test_a_stream_of_exactly_the_cap_is_not_cut(fake, tmp_path):
    _noisy_ssh(fake, out=int(CAP))
    got, note = tmp_path / "got", tmp_path / "note.txt"
    fake.run(
        f"PVE_NOTE_FILE={shlex.quote(str(note))}\npve_run x >{shlex.quote(str(got))}",
        env=_cap_env(),
    )
    assert got.stat().st_size == int(CAP)
    assert not note.exists()


def test_the_guests_stderr_is_cut_too(fake, tmp_path):
    _noisy_ssh(fake, err=20000)
    err, note = tmp_path / "err", tmp_path / "note.txt"
    r = fake.run(
        f"PVE_NOTE_FILE={shlex.quote(str(note))}\n"
        f"_PVE_STREAM=post pve_run x 2>{shlex.quote(str(err))}; echo rc=$?",
        env=_cap_env(),
    )
    assert "rc=0" in r.stdout
    assert err.stat().st_size == int(CAP)
    assert "truncated=post-stderr" in note.read_text().splitlines()


def test_the_exit_status_survives_a_cut(fake, tmp_path):
    _noisy_ssh(fake, out=20000, rc=3)
    got = tmp_path / "got"
    r = fake.run(f"pve_run x >{shlex.quote(str(got))}; echo rc=$?", env=_cap_env())
    assert "rc=3" in r.stdout
    assert got.stat().st_size == int(CAP)


def test_the_cap_inside_a_command_substitution(fake):
    _noisy_ssh(fake, out=20000)
    r = fake.run('v="$(pve_run x)"; echo "len=${#v}"', env=_cap_env())
    assert "len=4096" in r.stdout


@pytest.mark.parametrize("bad", ["", "0", "-5", "10; id", "4k", "99999999999999999999"])
def test_a_bad_cap_variable_means_the_default(fake, bad):
    r = fake.run("_pve_cap", env={"HAL0_MATRIX_MAX_STREAM_BYTES": bad})
    assert r.stdout == str(64 * 1024 * 1024)


def test_a_good_cap_variable_is_used(fake):
    r = fake.run("_pve_cap", env={"HAL0_MATRIX_MAX_STREAM_BYTES": "5000"})
    assert r.stdout == "5000"


def test_a_pull_is_cut_at_the_cap_and_leaves_no_temporary_file(fake, tmp_path):
    _owned_rules(fake, 172)
    fake.rule("pct exec 172", b"x" * 20000)
    dst, note = tmp_path / "install.log", tmp_path / "note.txt"
    r = fake.run(
        f"PVE_NOTE_FILE={shlex.quote(str(note))}\nct_pull 172 /var/log/a.log {shlex.quote(str(dst))}",
        env=_cap_env(),
    )
    assert r.returncode == 0, r.stderr
    assert dst.stat().st_size == int(CAP)
    assert "truncated=install.log" in note.read_text().splitlines()
    assert sorted(p.name for p in tmp_path.iterdir() if p.name != "fake") == [
        "install.log",
        "note.txt",
    ]


def _running_ssh(fake: Fake) -> None:
    """A fake ssh that runs the remote command for real, here, with a `pct`
    that runs the command a `pct exec V -- bash -c X` carries."""
    (fake.bin / "ssh").write_text(
        "#!/usr/bin/env bash\n"
        'cmd="${*: -1}"\n'
        'case "$cmd" in\n'
        '  "pct config "*) printf "hostname: hal0-mx-172\\ntags: hal0-matrix\\n"; exit 0;;\n'
        '  "{ pct config "*) cmd="${cmd#*; \\} && }";;\n'
        "esac\n"
        'pct() { shift 3; "$@"; }\n'
        "export -f pct\n"
        # Bounded in time (a read of a FIFO must end by itself) and in size
        # (1 MiB, whatever the command does), so a wrong pull stays harmless.
        "exec > >(head -c 1048576) 2> >(head -c 1048576 >&2)\n"
        'exec timeout -s KILL 5 bash -c "$cmd"\n'
    )


def test_a_pull_takes_a_regular_file(fake, tmp_path):
    _running_ssh(fake)
    src = tmp_path / "report.txt"
    src.write_bytes(b"the report\n")
    dst = tmp_path / "out.txt"
    r = fake.run(f"ct_pull 172 {shlex.quote(str(src))} {shlex.quote(str(dst))}")
    assert r.returncode == 0, r.stderr
    assert dst.read_bytes() == b"the report\n"


def _fifo_with_a_writer(path: Path) -> threading.Thread:
    """A FIFO whose writer sends 64 KiB and closes, once somebody reads it. If
    nobody ever opens it for reading the writer is released by the caller."""
    os.mkfifo(path)

    def write() -> None:
        with open(path, "wb") as fh:
            fh.write(b"x" * 65536)

    t = threading.Thread(target=write, daemon=True)
    t.start()
    return t


@pytest.mark.parametrize(
    "kind", ["char-device", "symlink-to-char-device", "fifo", "symlink-to-fifo", "directory"]
)
def test_a_pull_never_follows_a_guest_path_to_a_device_or_fifo(fake, tmp_path, kind):
    # Harmless when the code is WRONG: no endless source is used (/dev/null is
    # empty, the FIFO carries 64 KiB), and the fake ssh cuts all output at 1 MiB.
    _running_ssh(fake)
    target = tmp_path / "chosen"
    writer = None
    if kind == "char-device":
        target = Path("/dev/null")
    elif kind == "symlink-to-char-device":
        target.symlink_to("/dev/null")
    elif kind == "fifo":
        writer = _fifo_with_a_writer(target)
    elif kind == "symlink-to-fifo":
        writer = _fifo_with_a_writer(tmp_path / "pipe")
        target.symlink_to(tmp_path / "pipe")
    else:
        target.mkdir()
    dst = tmp_path / "out.txt"
    try:
        r = fake.run(
            f"ct_pull 172 {shlex.quote(str(target))} {shlex.quote(str(dst))}; echo rc=$?",
            env=_cap_env(HAL0_MATRIX_TIMEOUT_PULL="8"),
        )
    finally:
        if writer is not None and writer.is_alive():  # nobody read it: release the writer
            fd = os.open(
                tmp_path / ("pipe" if kind == "symlink-to-fifo" else "chosen"),
                os.O_RDONLY | os.O_NONBLOCK,
            )
            os.close(fd)
            writer.join(timeout=5)
    assert "rc=0" not in r.stdout, "the pull must fail"
    assert not dst.exists()
    assert not list(tmp_path.glob("out.txt.*")), "no temporary file is left"
