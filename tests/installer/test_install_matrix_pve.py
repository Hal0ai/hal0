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
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[2]
PVE_SH = REPO / "scripts" / "install-matrix" / "lib" / "pve.sh"
HOST_DEVICES_SH = REPO / "scripts" / "install-matrix" / "lib" / "host-devices.sh"
HAL0_SH = REPO / "scripts" / "proxmox-ve" / "hal0.sh"

FEATURES = "nesting=1,keyctl=1,fuse=1,mknod=1"

TAGGED_CONF = "hostname: hal0-mx-{vmid}\ntags: hal0-matrix\ncores: 2\n"
UNTAGGED_CONF = "hostname: hal0-mx-{vmid}\ncores: 2\n"
WRONG_HOST_CONF = "hostname: hal0\ntags: hal0-matrix\ncores: 2\n"


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
            f"exec bash {shlex.quote(str(self.dir / 'handler.sh'))} \"$cmd\"\n"
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
        lines = ["#!/usr/bin/env bash", 'cmd="$1"']
        for i, (needle, out, rc) in enumerate(self.rules):
            f = self.dir / f"out{i}"
            f.write_bytes(out if isinstance(out, bytes) else out.encode())
            lines.append(
                f"if [[ \"$cmd\" == *{shlex.quote(needle)}* ]]; then "
                f"cat {shlex.quote(str(f))}; exit {rc}; fi"
            )
        lines.append("echo \"fake ssh: unexpected command: $cmd\" >&2; exit 97")
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

    return json.dumps(
        [{"vmid": v, "type": "lxc", "tags": t, "name": f"g{v}"} for v, t in rows]
    )


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
    assert "host has 9 GB available, need 12" in r.stdout
    assert "rc=1" in r.stdout


def test_headroom_passes_at_floor(fake):
    fake.rule("free -g", _free(12))
    r = fake.run("pve_require_headroom; echo rc=$?")
    assert "rc=0" in r.stdout


def test_headroom_refuses_when_host_unreadable(fake):
    fake.rule("free -g", "", 255)
    r = fake.run("pve_require_headroom 2>&1; echo rc=$?")
    assert "rc=1" in r.stdout


def test_pve_run_passes_one_argument_with_batch_options(fake):
    fake.rule("echo hi", "hi\n")
    fake.run("pve_run 'echo hi; echo there'")
    fake.run("pve_run 'echo hi'")
    call = fake.calls()[-1]
    assert call == "-n -o BatchMode=yes -o ConnectTimeout=10 -- testpve echo hi"


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
    assert r.stdout.strip() == "mystore" and fake.calls() == []


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


def test_storage_rejects_hostile_env(fake):
    r = fake.run("pve_storage", env={"HAL0_MATRIX_STORAGE": "a;b"})
    assert r.returncode == 1


# --- vmid choice ------------------------------------------------------------


def test_pick_vmid_skips_taken(fake):
    fake.rule("pvesh get /cluster/resources", _guests((170, ""), (171, "other"), (105, "")))
    r = fake.run("pve_pick_vmid")
    assert r.returncode == 0 and r.stdout.strip() == "172"


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
    call = [c for c in fake.calls() if "pct create" in c][0]
    assert FEATURES in call
    assert "--tags hal0-matrix" in call
    assert "--hostname hal0-mx-172" in call
    assert "--swap 1024" in call and "--onboot 0" in call
    assert "--net0 name=eth0,bridge=vmbr0,ip=dhcp" in call
    assert "--rootfs local:40" in call
    assert "--unprivileged 0" in call
    assert "--dev0 /dev/dri/renderD128,gid=44" in call
    assert "--dev1 /dev/kfd" in call


def test_create_uses_bridge_env(fake):
    _create(fake, env={"HAL0_MATRIX_BRIDGE": "vmbr9"})
    call = [c for c in fake.calls() if "pct create" in c][0]
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
    assert r.returncode == 1
    assert [c for c in fake.calls() if "pct create" in c] == []


def test_create_refuses_an_existing_vmid(fake):
    fake.rule("pvesm status --content rootdir", STORAGE)
    fake.rule("test -e /etc/pve/lxc/172.conf", "present\n")
    r = fake.run("ct_create 172 local:vztmpl/x.tar.zst 0 4 8192 40")
    assert r.returncode == 1
    assert [c for c in fake.calls() if "pct create" in c] == []


def test_feature_list_matches_proxmox_script():
    m = re.search(r'FEATURES="\$\{FEATURES:-([^}]*)\}"', HAL0_SH.read_text())
    assert m, "FEATURES default not found in hal0.sh"
    theirs = m.group(1)
    mine = re.search(r'^_PVE_FEATURES="([^"]*)"', PVE_SH.read_text(), re.M)
    assert mine, "_PVE_FEATURES not found in pve.sh"
    assert mine.group(1) == theirs == FEATURES


# --- device rules, exec, push/pull -----------------------------------------


def test_apply_device_rules_appends_idempotently(fake):
    _owned_rules(fake, 172)
    fake.rule("/etc/pve/lxc/172.conf", "")
    r = fake.run("ct_apply_device_rules 172 0 226 238 226")
    assert r.returncode == 0, r.stderr
    call = [c for c in fake.calls() if "/etc/pve/lxc/172.conf" in c][0]
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
    call = [c for c in fake.calls() if "pct exec" in c][0]
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
    assert shlex.split(pull_inner) == ["cat", "/root/a b; id"]


def test_wait_network_succeeds_when_resolves(fake):
    _owned_rules(fake, 172)
    fake.rule("pct exec 172", "")
    r = fake.run("ct_wait_network 172 5; echo rc=$?")
    assert "rc=0" in r.stdout
    assert "hal0.dev" in [c for c in fake.calls() if "pct exec" in c][0]


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
    return [c for c in fake.calls() if "/etc/pve/lxc/172.conf" in c][0]


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
    theirs = HAL0_SH.read_text()
    mine = PVE_SH.read_text()
    for line in (
        "lxc.cgroup2.devices.allow: c ${maj}:* rwm",
        "lxc.prlimit.memlock: unlimited",
        "lxc.apparmor.profile: unconfined",
    ):
        assert line in theirs, line
        assert line in mine, line


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
    r = fake.run(
        f"ct_create 172 local:vztmpl/x.tar.zst 0 4 8192 40\necho {MARK}", strict=True
    )
    assert MARK in r.stdout, r.stderr


@pytest.mark.parametrize(
    "body,rules",
    [
        ("pve_mem_available_gb", [("free -g", _free(20))]),
        ("pve_require_headroom", [("free -g", _free(20))]),
        ("pve_template_volid debian-12", [("pveam list local", TEMPLATES)]),
        ("pve_storage", [("pvesm status --content rootdir", STORAGE)]),
        ("pve_pick_vmid", [("pvesh get /cluster/resources", _guests((170, "")))]),
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
        'if [[ "$cmd" == *"pct config 172"* ]]; then\n'
        f"  n=$(cat {c}); echo $((n+1)) > {c}\n"
        f'  if [[ "$n" -ge 1 ]]; then printf "%s" {conf_bad}; else printf "%s" {conf_ok}; fi\n'
        "  exit 0\n"
        "fi\n"
        'if [[ "$cmd" == *"test -e /etc/pve/lxc/172.conf"* ]]; then echo present; exit 0; fi\n'
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
