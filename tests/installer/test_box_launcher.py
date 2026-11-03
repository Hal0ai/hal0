"""Contract tests for the rootless podman box launcher (``installer/box/box.sh``).

The launcher prepares a dedicated host user, builds the box image, creates
one rootless podman container and runs the unchanged ``install.sh`` inside it
(design: ``2026-11-02-hal0-box-single-install-design.md``, #3332).

Nothing here needs root, a real podman, or the network. Every host command
the launcher shells out to is a stub on ``PATH`` that appends one line per
call to a shared log (``<user>\\t<argv>``), and every host path it writes is
re-rooted under a tmp dir through the launcher's test seams:

* ``HAL0_BOX_HOST_ROOT``      prefix for /etc, /home, /run/user, /var, /usr/local, /sys/fs/cgroup
* ``HAL0_BOX_DEV_ROOT``       where device nodes are discovered (fake files here)
* ``HAL0_BOX_UID_MAP_FILE``   stands in for /proc/self/uid_map
* ``HAL0_BOX_DRY_RUN=1``      print the ``podman create`` argv and stop
* ``HAL0_BOX_ALLOW_LOW_MEMLOCK=1``  an unprivileged test cannot raise the hard limit
"""

from __future__ import annotations

import shutil
import stat
import subprocess
from dataclasses import dataclass
from pathlib import Path

import pytest

_REPO_ROOT = Path(__file__).resolve().parents[2]
_BOX_DIR = _REPO_ROOT / "installer" / "box"
_BOX_SH = _BOX_DIR / "box.sh"
_SHIM = _BOX_DIR / "hal0-shim"
_CONTAINERFILE = _BOX_DIR / "Containerfile.in"

_ARGV_MARKER = "--- podman create argv ---"
_PORTS = ("8080", "3001", "8188")


def _write_exec(path: Path, body: str) -> None:
    path.write_text(f"#!/usr/bin/env bash\n{body}\n", encoding="utf-8")
    path.chmod(path.stat().st_mode | stat.S_IEXEC | stat.S_IXGRP | stat.S_IXOTH)


# Every stub logs "<user>\t<name> <args>" so ordering and the acting user are
# both observable. HAL0_STUB_USER is set by the runuser stub.
_LOG = 'printf "%s\\t%s\\n" "${HAL0_STUB_USER:-root}" "$(basename "$0") $*" >> "${HAL0_STUB_STATE}/calls.log"'

# A pattern in fail_next makes the next matching call of any stub fail once.
_FAIL_NEXT = r"""
if [[ -f "${HAL0_STUB_STATE}/fail_next" ]]; then
    _me="$(basename "$0") $*"
    while IFS= read -r _pat; do
        [[ -n "${_pat}" ]] || continue
        if [[ "${_me}" == *"${_pat}"* ]]; then
            grep -vxF -- "${_pat}" "${HAL0_STUB_STATE}/fail_next" > "${HAL0_STUB_STATE}/fail_next.tmp" || true
            mv "${HAL0_STUB_STATE}/fail_next.tmp" "${HAL0_STUB_STATE}/fail_next"
            echo "stub: injected failure for: ${_pat}" >&2
            exit 1
        fi
    done < "${HAL0_STUB_STATE}/fail_next"
fi
"""

_STUBS: dict[str, str] = {
    "podman": r"""
S="${HAL0_STUB_STATE}"
mkdir -p "$S/containers" "$S/volumes"
case "${1:-}" in
    --version) echo "podman version 5.7.0" ;;
    container)
        [[ "${2:-}" == exists ]] && { [[ -f "$S/containers/${3:-}" ]]; exit $?; } ;;
    volume)
        case "${2:-}" in
            exists) [[ -f "$S/volumes/${3:-}" ]]; exit $? ;;
            create) touch "$S/volumes/${3:-}" ;;
            rm) shift 2; for v in "$@"; do [[ "$v" == -* ]] || rm -f "$S/volumes/$v"; done ;;
        esac ;;
    create)
        name=""; prev=""
        for a in "$@"; do [[ "$prev" == --name ]] && name="$a"; prev="$a"; done
        [[ -n "$name" ]] && echo "${HAL0_STUB_USER:-root}" > "$S/containers/$name" ;;
    rm)
        for a in "$@"; do [[ "$a" == rm || "$a" == -* ]] || rm -f "$S/containers/$a"; done ;;
    system)
        [[ "${2:-}" == reset ]] && rm -f "$S"/containers/* "$S"/volumes/* ;;
    exec)
        shift
        while [[ "${1:-}" == -* ]]; do
            case "$1" in -e|--env|-u|--user|-w|--workdir) shift 2 ;; *) shift ;; esac
        done
        name="${1:-}"; shift || true
        [[ -f "$S/containers/$name" ]] || { echo "Error: no container with name or ID \"$name\" found" >&2; exit 125; }
        case "$*" in
            *is-system-running*) echo running ;;
            *install.sh*) echo "$*" >> "$S/installer_runs"; echo "fake installer ran" ;;
        esac ;;
esac
exit 0
""",
    "runuser": r"""
user=""
while [[ $# -gt 0 ]]; do
    case "$1" in -u) user="$2"; shift 2 ;; --) shift; break ;; *) shift ;; esac
done
export HAL0_STUB_USER="${user}"
exec "$@"
""",
    "id": r"""
if [[ $# -eq 1 && "$1" == -u ]]; then echo "${HAL0_STUB_UID:-0}"; exit 0; fi
name="${!#}"
line="$(grep "^${name}:" "${HAL0_STUB_STATE}/users" 2>/dev/null)" || { echo "id: '${name}': no such user" >&2; exit 1; }
uid="${line#*:}"
if [[ "${1:-}" == -u ]]; then echo "${uid}"; else echo "uid=${uid}(${name}) gid=${uid}(${name})"; fi
""",
    "useradd": r"""
home=""; prev=""
for a in "$@"; do [[ "$prev" == -d || "$prev" == --home-dir ]] && home="$a"; prev="$a"; done
name="${!#}"
echo "${name}:2001" >> "${HAL0_STUB_STATE}/users"
[[ -n "${home}" ]] && mkdir -p "${home}"
exit 0
""",
    "userdel": r"""
name="${!#}"
grep -v "^${name}:" "${HAL0_STUB_STATE}/users" > "${HAL0_STUB_STATE}/users.tmp" 2>/dev/null || true
mv "${HAL0_STUB_STATE}/users.tmp" "${HAL0_STUB_STATE}/users"
[[ "${1:-}" == -r && -n "${HAL0_BOX_HOST_ROOT:-}" ]] && rm -rf "${HAL0_BOX_HOST_ROOT}/home/${name}"
exit 0
""",
    "systemctl": r"""
case "$*" in
    *is-active*) echo active ;;
esac
exit 0
""",
    "ufw": r"""
S="${HAL0_STUB_STATE}"; touch "$S/ufw_rules"
case "${1:-}" in
    status)
        if [[ -f "$S/ufw_active" ]]; then
            echo "Status: active"; echo
            while IFS= read -r r; do [[ -n "$r" ]] && printf '%-26s ALLOW       Anywhere\n' "$r"; done < "$S/ufw_rules"
        else
            echo "Status: inactive"
        fi ;;
    allow) echo "$2" >> "$S/ufw_rules" ;;
    delete) grep -vxF -- "$3" "$S/ufw_rules" > "$S/ufw_rules.tmp" || true; mv "$S/ufw_rules.tmp" "$S/ufw_rules" ;;
esac
exit 0
""",
    "firewall-cmd": r"""
S="${HAL0_STUB_STATE}"; touch "$S/firewalld_rules"
for a in "$@"; do
    case "$a" in
        --state) [[ -f "$S/firewalld_active" ]] && { echo running; exit 0; }; echo "not running"; exit 252 ;;
        --query-port=*) grep -qxF -- "${a#*=}" "$S/firewalld_rules"; exit $? ;;
        --add-port=*) echo "${a#*=}" >> "$S/firewalld_rules" ;;
        --remove-port=*) grep -vxF -- "${a#*=}" "$S/firewalld_rules" > "$S/fw.tmp" || true; mv "$S/fw.tmp" "$S/firewalld_rules" ;;
    esac
done
exit 0
""",
    "ss": r"""
cat "${HAL0_STUB_STATE}/listen" 2>/dev/null
exit 0
""",
    "stat": r"""
if [[ "${1:-}" == -c && "${2:-}" == %g && -f "${3:-}.gid" ]]; then cat "$3.gid"; exit 0; fi
exec /usr/bin/stat "$@"
""",
    "getent": r"""
if [[ "${1:-}" == group ]]; then
    grep -E "^[^:]*:${2}$|^${2}:" "${HAL0_STUB_STATE}/groups" 2>/dev/null | head -1 | awk -F: '{print $1":x:"$2":"}' | grep . && exit 0
    exit 2
fi
exit 2
""",
    "hostname": r"""
if [[ "${1:-}" == -I ]]; then echo "192.0.2.10 "; else echo "testhost"; fi
""",
    "loginctl": "exit 0",
    "sleep": "exit 0",
    "pkill": "exit 0",
    "chown": "exit 0",
    "pasta": "exit 0",
    "newuidmap": "exit 0",
    "newgidmap": "exit 0",
    "apt-get": "exit 0",
}

_APPARMOR_STUB = """#!/usr/bin/env python3
import os, sys
with open(os.path.join(os.environ["HAL0_STUB_STATE"], "calls.log"), "a") as f:
    f.write("root\\tcontainers_apparmor.py " + " ".join(sys.argv[1:]) + "\\n")
"""


@dataclass
class Call:
    user: str
    argv: str


class BoxHarness:
    def __init__(self, tmp: Path) -> None:
        self.tmp = tmp
        self.host = tmp / "host"
        self.dev = tmp / "dev"
        self.bin = tmp / "bin"
        self.state = tmp / "stub"
        self.tree = tmp / "tree"
        for d in (self.host, self.dev, self.bin, self.state):
            d.mkdir(parents=True)
        (self.host / "etc").mkdir()
        (self.host / "etc" / "subuid").write_text("")
        (self.host / "etc" / "subgid").write_text("")
        cg = self.host / "sys" / "fs" / "cgroup"
        cg.mkdir(parents=True)
        (cg / "cgroup.controllers").write_text("cpu memory pids\n")
        (self.dev / "net").mkdir()
        self._fake_node("net/tun", gid=0)
        self._fake_node("fuse", gid=0)
        self.uid_map = tmp / "uid_map"
        self.uid_map.write_text("         0          0 4294967295\n")
        (self.state / "groups").write_text("render:993\nvideo:44\n")
        for name, body in _STUBS.items():
            _write_exec(self.bin / name, _LOG + "\n" + _FAIL_NEXT + "\n" + body)
        # The verified tree as bootstrap hands it over: the launcher runs from it.
        shutil.copytree(_BOX_DIR, self.tree / "installer" / "box")
        _write_exec(self.tree / "installer" / "install.sh", "exit 0")
        aa = self.tree / "src" / "hal0" / "agents" / "containers_apparmor.py"
        aa.parent.mkdir(parents=True)
        aa.write_text(_APPARMOR_STUB, encoding="utf-8")

    # ── environment ────────────────────────────────────────────────────
    def env(self, **extra: str) -> dict[str, str]:
        env = {
            "PATH": f"{self.bin}:/usr/bin:/bin",
            "HOME": str(self.tmp),
            "LANG": "C.UTF-8",
            "HAL0_STUB_STATE": str(self.state),
            "HAL0_BOX_HOST_ROOT": str(self.host),
            "HAL0_BOX_DEV_ROOT": str(self.dev),
            "HAL0_BOX_UID_MAP_FILE": str(self.uid_map),
            "HAL0_BOX_ALLOW_LOW_MEMLOCK": "1",
        }
        env.update(extra)
        return env

    def run(self, *args: str, check: bool = True, script: Path | None = None, **extra: str):
        res = subprocess.run(
            ["bash", str(script or self.tree / "installer" / "box" / "box.sh"), *args],
            env=self.env(**extra),
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            timeout=60,
        )
        if check and res.returncode != 0:
            raise AssertionError(f"box.sh {' '.join(args)} exited {res.returncode}:\n{res.stdout}")
        return res

    # ── fixtures of the host ───────────────────────────────────────────
    def _fake_node(self, rel: str, gid: int) -> None:
        node = self.dev / rel
        node.parent.mkdir(parents=True, exist_ok=True)
        node.write_text("")
        Path(f"{node}.gid").write_text(f"{gid}\n")

    def add_device(self, rel: str, gid: int) -> None:
        self._fake_node(rel, gid)

    def remove_device(self, rel: str) -> None:
        (self.dev / rel).unlink()

    def set_uid_map(self, text: str) -> None:
        self.uid_map.write_text(text + "\n")

    def occupy_port(self, port: int, proc: str = "node") -> None:
        with (self.state / "listen").open("a") as f:
            f.write(f'LISTEN 0 4096 0.0.0.0:{port} 0.0.0.0:* users:(("{proc}",pid=42,fd=3))\n')

    def set_firewall(self, kind: str, active: bool) -> None:
        if active:
            (self.state / f"{'ufw' if kind == 'ufw' else 'firewalld'}_active").write_text("")

    def fail_next(self, pattern: str) -> None:
        with (self.state / "fail_next").open("a") as f:
            f.write(pattern + "\n")

    # ── launcher entry points ──────────────────────────────────────────
    def dry_run(self, *args: str) -> list[str]:
        out = self.run("install", str(self.tree), *args, HAL0_BOX_DRY_RUN="1").stdout
        assert _ARGV_MARKER in out, out
        return out.split(_ARGV_MARKER, 1)[1].strip("\n").split("\n")

    def preflight(self, check: bool = True):
        res = self.run("preflight", check=check)
        return res.stdout if check else res

    def install(self, *args: str, check: bool = True):
        return self.run("install", str(self.tree), *args, check=check)

    def uninstall(self, check: bool = True):
        return self.run("uninstall", check=check)

    def rebuild(self, check: bool = True):
        return self.run("rebuild", check=check)

    # ── observations ───────────────────────────────────────────────────
    def subuid(self) -> str:
        p = self.host / "etc" / "subuid"
        return p.read_text() if p.exists() else ""

    def subgid(self) -> str:
        p = self.host / "etc" / "subgid"
        return p.read_text() if p.exists() else ""

    def calls(self) -> list[Call]:
        log = self.state / "calls.log"
        if not log.exists():
            return []
        out = []
        for line in log.read_text().splitlines():
            user, _, argv = line.partition("\t")
            out.append(Call(user, argv))
        return out

    def all_calls(self) -> list[str]:
        return [c.argv for c in self.calls()]

    def first_index(self, prefix: str) -> int:
        for i, argv in enumerate(self.all_calls()):
            if argv.startswith(prefix):
                return i
        raise AssertionError(f"no call starting with {prefix!r}")

    def podman_calls_named(self, sub: str) -> list[Call]:
        return [c for c in self.calls() if c.argv.startswith(f"podman {sub}")]

    def podman_calls(self, sub: str) -> int:
        return len(self.podman_calls_named(sub))

    def ran_installer(self, times: int | None = None) -> bool:
        p = self.state / "installer_runs"
        n = len(p.read_text().splitlines()) if p.exists() else 0
        return n > 0 if times is None else n == times

    def installer_runs(self) -> list[str]:
        p = self.state / "installer_runs"
        return p.read_text().splitlines() if p.exists() else []

    def shim_path(self) -> Path:
        return self.host / "usr" / "local" / "bin" / "hal0"

    def shim_text(self) -> str:
        return self.shim_path().read_text()

    def shim_as_user(self):
        return subprocess.run(
            ["bash", str(_SHIM), "status"],
            env=self.env(HAL0_STUB_UID="1000"),
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            timeout=30,
        )

    def state_is_healthy(self) -> bool:
        return (
            (self.state / "containers" / "hal0-box").exists()
            and (self.host / "var" / "lib" / "hal0-box" / "installed").exists()
            and self.shim_path().exists()
        )

    def firewall_allowed(self) -> set[str]:
        out: set[str] = set()
        for name in ("ufw_rules", "firewalld_rules"):
            p = self.state / name
            if p.exists():
                out |= {line for line in p.read_text().splitlines() if line}
        return out


@pytest.fixture
def box(tmp_path: Path) -> BoxHarness:
    return BoxHarness(tmp_path)


def _flag_value(argv: list[str], flag: str) -> list[str]:
    return [argv[i + 1] for i, a in enumerate(argv[:-1]) if a == flag]


# ── Task 1: preflight and create argv ─────────────────────────────────────


def test_create_argv_network_and_flags(box: BoxHarness) -> None:
    argv = box.dry_run()
    assert argv[:2] == ["podman", "create"]
    assert _flag_value(argv, "--network") == ["pasta:-t,8080,-t,3001,-t,8188"]
    assert "--privileged" not in argv and "-p" not in argv and "--publish" not in argv
    assert not any("slirp4netns" in a for a in argv)
    assert not any("-t,auto" in a or a == "auto" for a in argv)
    for flag in (
        "--systemd=always",
        "--cap-add=all",
    ):
        assert flag in argv
    opts = _flag_value(argv, "--security-opt")
    assert "label=disable" in opts and "unmask=/proc/sys" in opts
    devices = _flag_value(argv, "--device")
    assert f"{box.dev}/fuse:/dev/fuse" in devices
    assert f"{box.dev}/net/tun:/dev/net/tun" in devices
    assert _flag_value(argv, "--ulimit") == ["memlock=-1:-1"]
    assert _flag_value(argv, "--name") == ["hal0-box"]
    assert _flag_value(argv, "--hostname") == ["testhost"]
    assert argv[-1] == "localhost/hal0-box:local"


def test_device_group_is_mapped(box: BoxHarness) -> None:
    box.add_device("dri/renderD128", gid=993)
    box.add_device("kfd", gid=993)
    argv = box.dry_run()
    assert _flag_value(argv, "--gidmap").count("+g993:@993") == 1
    assert f"{box.dev}/dri/renderD128:/dev/dri/renderD128" in _flag_value(argv, "--device")
    assert f"{box.dev}/kfd:/dev/kfd" in _flag_value(argv, "--device")
    assert box.subgid().count("hal0box:993:1") == 1
    # A re-run does not duplicate the line.
    box.dry_run()
    assert box.subgid().count("hal0box:993:1") == 1


def test_root_owned_device_warns(box: BoxHarness) -> None:
    box.add_device("kfd", gid=0)
    out = box.preflight()
    assert "/dev/kfd" in out and "udev" in out
    assert not any("kfd" in a for a in box.dry_run())


def test_device_gid_collision_fails(box: BoxHarness) -> None:
    box.add_device("dri/renderD128", gid=970)
    res = box.preflight(check=False)
    assert res.returncode != 0 and "970" in res.stdout


def test_narrow_map_uses_inner_range(box: BoxHarness) -> None:
    box.set_uid_map("0 100000 65536")
    argv = box.dry_run()
    assert "+u65534:@65534" in _flag_value(argv, "--uidmap")
    assert "+g65534:@65534" in _flag_value(argv, "--gidmap")
    assert "hal0box:20000:40000" in box.subuid() and "hal0box:65534:1" in box.subuid()
    assert "hal0box:20000:40000" in box.subgid() and "hal0box:65534:1" in box.subgid()


def test_wide_map_gets_one_range_after_existing_ones(box: BoxHarness) -> None:
    (box.host / "etc" / "subuid").write_text("ubuntu:100000:65536\n")
    (box.host / "etc" / "subgid").write_text("ubuntu:100000:65536\n")
    argv = box.dry_run()
    assert "--uidmap" not in argv
    assert "hal0box:165536:65536" in box.subuid().splitlines()
    assert "hal0box:165536:65536" in box.subgid().splitlines()
    box.dry_run()
    assert box.subuid().count("hal0box:") == 1


def test_missing_tun_names_the_remedy(box: BoxHarness) -> None:
    box.remove_device("net/tun")
    res = box.preflight(check=False)
    assert res.returncode != 0 and "pct set" in res.stdout and "/dev/net/tun" in res.stdout


def test_busy_port_named_before_create(box: BoxHarness) -> None:
    box.occupy_port(3001)
    res = box.preflight(check=False)
    assert res.returncode != 0 and "3001" in res.stdout
    res = box.install(check=False)
    assert res.returncode != 0 and "3001" in res.stdout
    assert box.podman_calls("create") == 0
    assert not (box.host / "etc" / "systemd").exists()


def test_cgroup_v1_is_refused(box: BoxHarness) -> None:
    (box.host / "sys" / "fs" / "cgroup" / "cgroup.controllers").unlink()
    res = box.preflight(check=False)
    assert res.returncode != 0 and "cgroup v2" in res.stdout


def test_non_root_is_refused(box: BoxHarness) -> None:
    res = box.run("preflight", check=False, HAL0_STUB_UID="1000")
    assert res.returncode != 0 and "root" in res.stdout


def test_existing_bare_metal_hal0_is_not_clobbered(box: BoxHarness) -> None:
    shim = box.shim_path()
    shim.parent.mkdir(parents=True)
    shim.write_text("#!/bin/sh\n# a bare-metal hal0 CLI\n")
    res = box.preflight(check=False)
    assert res.returncode != 0 and "/usr/local/bin/hal0" in res.stdout


def test_base_image_is_pinned_by_digest() -> None:
    text = _BOX_SH.read_text()
    assert "docker.io/library/ubuntu:26.04@sha256:" in text
    assert "@BASE_IMAGE@" in _CONTAINERFILE.read_text()


def test_rendered_containerfile_uses_the_pinned_base(box: BoxHarness, tmp_path: Path) -> None:
    out = tmp_path / "render"
    subprocess.run(
        [
            "bash",
            "-c",
            f'source "{box.tree}/installer/box/box.sh" && box_render_containerfile "{out}"',
        ],
        env=box.env(),
        check=True,
        timeout=30,
    )
    text = (out / "Containerfile").read_text()
    assert "@BASE_IMAGE@" not in text
    assert "\nFROM docker.io/library/ubuntu:26.04@sha256:" in text


def test_containerfile_pins_identity_and_limits() -> None:
    text = _CONTAINERFILE.read_text()
    assert "-u 970" in text and "-g 970" in text
    assert "/var/lib/systemd/linger/root" in text
    assert "DefaultLimitMEMLOCK=infinity" in text
    assert "account required pam_permit.so" in text
    assert 'APT::Sandbox::User "root"' in text and "rm -f /etc/apt/apt.conf.d/99-box-build" in text
    for unit in ("getty.target", "console-getty.service", "systemd-firstboot.service"):
        assert unit in text
    run = text.split("\nRUN ", 1)[1].split("\nSTOPSIGNAL", 1)[0]
    # One && chained RUN: a failed apt must fail the build, and the device
    # group loop exits non-zero on the first failure.
    assert (
        "\n && apt-get update" in run
        and "\n && DEBIAN_FRONTEND=noninteractive apt-get install" in run
    )
    assert "|| exit 1" in run
    assert text.count("\nRUN ") == 1
