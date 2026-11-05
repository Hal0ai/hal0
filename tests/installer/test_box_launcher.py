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

import os
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
[[ "${1:-}" == --help ]] && { echo " -P, --pty                       create a new pseudo-terminal"; exit 0; }
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
uid="$(cut -d: -f2 <<<"${line}")"
if [[ "${1:-}" == -u ]]; then echo "${uid}"; else echo "uid=${uid}(${name}) gid=${uid}(${name})"; fi
""",
    # users lines are "<name>:<uid>:<gecos>".
    "useradd": r"""
home=""; comment=""; prev=""
for a in "$@"; do
    [[ "$prev" == -d || "$prev" == --home-dir ]] && home="$a"
    [[ "$prev" == -c || "$prev" == --comment ]] && comment="$a"
    prev="$a"
done
name="${!#}"
echo "${name}:2001:${comment}" >> "${HAL0_STUB_STATE}/users"
[[ -n "${home}" ]] && /usr/bin/mkdir -p "${home}"
exit 0
""",
    "userdel": r"""
name="${!#}"
grep -v "^${name}:" "${HAL0_STUB_STATE}/users" > "${HAL0_STUB_STATE}/users.tmp" 2>/dev/null || true
mv "${HAL0_STUB_STATE}/users.tmp" "${HAL0_STUB_STATE}/users"
[[ "${1:-}" == -r && -n "${HAL0_BOX_HOST_ROOT:-}" ]] && rm -rf "${HAL0_BOX_HOST_ROOT}/home/${name}"
exit 0
""",
    # A "stuck" file makes the box user's processes survive every kill.
    "pgrep": r"""
[[ -f "${HAL0_STUB_STATE}/stuck" ]] && { echo 4242; exit 0; }
exit 1
""",
    "systemctl": r"""
case "$*" in
    *is-active*--quiet*|*--quiet*is-active*) ;;
    *is-active*) echo active ;;
esac
exit 0
""",
    "ufw": r"""
S="${HAL0_STUB_STATE}"; touch "$S/ufw_rules"
case "${1:-}" in
    status)
        if [[ -f "$S/ufw_active" ]]; then
            echo "Status: active"
            # A real ufw writes the rule table after the header; a reader that
            # stops at the first line (head -1, grep -q) must not break on it.
            [[ -f "$S/ufw_slow" ]] && sleep 0.2
            echo
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
# A fake node's gid lives at <state>/gids/<node path>, outside the dev tree,
# so the launcher's device globs never see it.
if [[ "${1:-}" == -c && "${2:-}" == %g && -f "${HAL0_STUB_STATE}/gids${3:-}" ]]; then cat "${HAL0_STUB_STATE}/gids$3"; exit 0; fi
exec /usr/bin/stat "$@"
""",
    # passwd from the users file, group from the groups file ("<name>:<gid>").
    "getent": r"""
db="${1:-}"; key="${2:-}"; found=1
case "${db}" in
    passwd)
        while IFS=: read -r n u g; do
            [[ -n "$n" ]] || continue
            if [[ -z "${key}" || "${key}" == "$n" || "${key}" == "$u" ]]; then
                echo "$n:x:$u:$u:$g:/home/$n:/usr/sbin/nologin"; found=0
            fi
        done < <(cat "${HAL0_STUB_STATE}/users" 2>/dev/null)
        ;;
    group)
        while IFS=: read -r n gid; do
            [[ -n "$n" ]] || continue
            if [[ -z "${key}" || "${key}" == "$n" || "${key}" == "${gid}" ]]; then
                echo "$n:x:${gid}:"; found=0
            fi
        done < <(cat "${HAL0_STUB_STATE}/groups" 2>/dev/null)
        ;;
esac
[[ "${found}" == 0 ]] && exit 0
exit 2
""",
    "hostname": r"""
if [[ "${1:-}" == -I ]]; then echo "192.0.2.10 "; else echo "testhost"; fi
""",
    "loginctl": "exit 0",
    "sleep": "exit 0",
    "pkill": "exit 0",
    "apparmor_parser": "exit 0",
    # Real file tools, logged so the acting user of every write is visible.
    "tee": 'exec /usr/bin/tee "$@"',
    "mkdir": 'exec /usr/bin/mkdir "$@"',
    "chmod": 'exec /usr/bin/chmod "$@"',
    "chown": "exit 0",
    "pasta": "exit 0",
    "newuidmap": "exit 0",
    "newgidmap": "exit 0",
    "apt-get": "exit 0",
}

# Like the real helper: disables each profile named in <state>/aa_names under
# <state>/aa and prints a revert line only for the ones it newly disabled.
_APPARMOR_STUB = """#!/usr/bin/env python3
import os, sys
state = os.environ["HAL0_STUB_STATE"]
with open(os.path.join(state, "calls.log"), "a") as f:
    f.write("root\\tcontainers_apparmor.py " + " ".join(sys.argv[1:]) + "\\n")
names_file = os.path.join(state, "aa_names")
if os.path.exists(names_file):
    aa = os.path.join(state, "aa")
    os.makedirs(os.path.join(aa, "disable"), exist_ok=True)
    for name in open(names_file).read().split():
        link = os.path.join(aa, "disable", name)
        if os.path.islink(link):
            continue
        os.symlink(os.path.join(aa, name), link)
        print(f"revert: rm {link} && apparmor_parser -r {os.path.join(aa, name)}")
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
            # runuser only forwards; failures are injected in the command it runs.
            fail = "" if name == "runuser" else _FAIL_NEXT
            _write_exec(self.bin / name, _LOG + "\n" + fail + "\n" + body)
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
            stdin=subprocess.DEVNULL,
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
        gid_file = Path(f"{self.state}/gids{node}")
        gid_file.parent.mkdir(parents=True, exist_ok=True)
        gid_file.write_text(f"{gid}\n")

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

    def install(self, *args: str, check: bool = True, **extra: str):
        return self.run("install", str(self.tree), *args, check=check, **extra)

    def uninstall(self, *args: str, check: bool = True, **extra: str):
        return self.run("uninstall", *args, check=check, **extra)

    def purge(self, check: bool = True):
        return self.uninstall("--purge", "--yes", check=check)

    def shim(self, *args: str, check: bool = True, **extra: str):
        res = subprocess.run(
            ["bash", str(self.shim_path()), *args],
            env=self.env(**extra),
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            stdin=subprocess.DEVNULL,
            text=True,
            timeout=60,
        )
        if check and res.returncode != 0:
            raise AssertionError(f"hal0 {' '.join(args)} exited {res.returncode}:\n{res.stdout}")
        return res

    def user_exists(self) -> bool:
        p = self.state / "users"
        return p.exists() and any(
            line.startswith("hal0box:") for line in p.read_text().splitlines()
        )

    def volumes(self) -> set[str]:
        d = self.state / "volumes"
        return {v.name for v in d.iterdir()} if d.exists() else set()

    def container_exists(self) -> bool:
        return (self.state / "containers" / "hal0-box").exists()

    def unit_path(self) -> Path:
        return self.host / "home" / "hal0box" / ".config" / "systemd" / "user" / "hal0-box.service"

    def dropin_path(self) -> Path:
        # The useradd stub gives every user uid 2001.
        return self.host / "etc" / "systemd" / "system" / "user@2001.service.d" / "hal0-box.conf"

    def legacy_dropin_path(self) -> Path:
        return self.host / "etc" / "systemd" / "system" / "user@.service.d" / "hal0-box.conf"

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


@pytest.mark.parametrize("group", ["disk", "shadow", "sudo", "kmem", "tty", "adm"])
def test_device_in_a_privileged_group_is_refused(box: BoxHarness, group: str) -> None:
    (box.state / "groups").write_text(f"render:993\n{group}:42\n")
    box.add_device("dri/renderD128", gid=42)
    res = box.preflight(check=False)
    assert res.returncode != 0 and group in res.stdout and "renderD128" in res.stdout
    res = box.install(check=False)
    assert res.returncode != 0 and box.podman_calls("create") == 0
    assert "hal0box:42:1" not in box.subgid()


def test_narrow_map_uses_inner_range(box: BoxHarness) -> None:
    box.set_uid_map("0 100000 65536")
    argv = box.dry_run()
    assert "+u65534:@65534" in _flag_value(argv, "--uidmap")
    assert "+g65534:@65534" in _flag_value(argv, "--gidmap")
    assert "hal0box:20000:40000" in box.subuid() and "hal0box:65534:1" in box.subuid()
    assert "hal0box:20000:40000" in box.subgid() and "hal0box:65534:1" in box.subgid()


def test_narrow_map_refuses_a_host_user_in_its_range(box: BoxHarness) -> None:
    box.set_uid_map("0 100000 65536")
    (box.state / "users").write_text("alice:30000:Alice\n")
    res = box.preflight(check=False)
    assert res.returncode != 0 and "alice" in res.stdout and "30000" in res.stdout
    assert box.install(check=False).returncode != 0
    assert "hal0box" not in box.subuid()


def test_narrow_map_refuses_a_host_group_in_its_range(box: BoxHarness) -> None:
    box.set_uid_map("0 100000 65536")
    (box.state / "groups").write_text("render:993\nproject:59999\n")
    res = box.preflight(check=False)
    assert res.returncode != 0 and "project" in res.stdout and "59999" in res.stdout


def test_narrow_map_refuses_another_users_sub_ids_in_its_range(box: BoxHarness) -> None:
    box.set_uid_map("0 100000 65536")
    (box.host / "etc" / "subgid").write_text("bob:10000:15000\n")
    res = box.preflight(check=False)
    assert res.returncode != 0 and "bob:10000:15000" in res.stdout


def test_narrow_map_allows_accounts_outside_its_range(box: BoxHarness) -> None:
    box.set_uid_map("0 100000 65536")
    (box.state / "users").write_text("alice:1000:Alice\nbob:60000:Bob\n")
    (box.state / "groups").write_text("render:993\nnogroup:65534\n")
    (box.host / "etc" / "subuid").write_text("carol:60000:5000\n")
    box.preflight()
    box.dry_run()
    box.dry_run()


def test_wide_map_ignores_accounts_in_the_narrow_range(box: BoxHarness) -> None:
    (box.state / "users").write_text("alice:30000:Alice\n")
    box.preflight()


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


def test_device_comments_do_not_claim_box_root_is_locked_out() -> None:
    # Box root holds CAP_SETGID in the box's user namespace and the device gid
    # is mapped, so it can join the group and open the node. The boundary is
    # the box as a whole (the hal0box host user), not the box's root account.
    adr = next((_REPO_ROOT / "docs" / "adr").glob("0051-*.md")).read_text()
    for text in (_CONTAINERFILE.read_text(), adr):
        flat = " ".join(text.replace("#", " ").split())
        assert "cannot open the devices" not in flat
        assert "not in the device group and cannot open" not in flat
        assert "CAP_SETGID" in flat


# ── Task 2: install, self-test, shim and uninstall ────────────────────────


def test_install_runs_the_installer_in_the_box(box: BoxHarness) -> None:
    res = box.install("--no-pi", HAL0_BOOTSTRAP_VERIFIED="1", HAL0_CHANNEL="nightly")
    assert box.ran_installer(times=1)
    run = box.installer_runs()[0]
    assert run.startswith("bash /usr/local/src/hal0/installer/install.sh") and run.endswith(
        "--no-pi"
    )
    exec_call = next(c for c in box.podman_calls_named("exec") if "install.sh" in c.argv)
    assert (
        "HAL0_BOOTSTRAP_VERIFIED=1" in exec_call.argv and "HAL0_CHANNEL=nightly" in exec_call.argv
    )
    assert "HAL0_BOOTSTRAP_WORK" not in exec_call.argv and "HAL0_BOX_" not in exec_call.argv
    assert box.podman_calls("cp") == 1
    assert "http://192.0.2.10:8080" in res.stdout
    assert box.state_is_healthy()


def test_rootless_podman_only_ever_runs_as_the_box_user(box: BoxHarness) -> None:
    box.install()
    podman = [c for c in box.calls() if c.argv.startswith("podman ")]
    assert podman
    assert all(c.user == "hal0box" for c in podman if c.argv != "podman --version")


def test_install_writes_units_with_memlock(box: BoxHarness) -> None:
    box.install()
    assert "LimitMEMLOCK=infinity" in box.dropin_path().read_text()
    assert not box.legacy_dropin_path().exists()
    unit = box.host / "home" / "hal0box" / ".config" / "systemd" / "user" / "hal0-box.service"
    text = unit.read_text()
    assert "LimitMEMLOCK=infinity" in text and "start hal0-box" in text and "--rm" not in text
    assert any(c.argv.startswith("loginctl enable-linger hal0box") for c in box.calls())


def test_install_rerun_converges(box: BoxHarness) -> None:
    box.install()
    box.fail_next("podman create")
    assert box.install(check=False).returncode != 0
    assert not box.state_is_healthy()
    box.install()
    assert box.podman_calls("create") >= 3 and box.state_is_healthy()
    assert box.subuid().count("hal0box:") == 1


def test_failed_installer_is_not_reported_healthy(box: BoxHarness) -> None:
    box.fail_next("install.sh")
    res = box.install(check=False)
    assert res.returncode != 0 and "re-run" in res.stdout.lower()
    assert not box.state_is_healthy()
    status = box.run("status", check=False)
    assert status.returncode != 0 and "incomplete" in status.stdout


def test_self_test_failure_stops_before_installer(box: BoxHarness) -> None:
    box.fail_next("wget")
    res = box.install(check=False)
    assert res.returncode != 0 and "passt" in res.stdout
    assert not box.ran_installer()


def test_nested_apparmor_runs_before_first_podman_call(box: BoxHarness) -> None:
    box.install()
    assert box.first_index("containers_apparmor.py --nested-box") < box.first_index("podman ")


def test_image_has_podman_for_the_self_test() -> None:
    run = _CONTAINERFILE.read_text().split("\nRUN ", 1)[1]
    install = run.split("apt-get install", 1)[1].split("\n && ", 1)[0]
    assert " podman" in install


def test_missing_apparmor_helper_fails_clearly(box: BoxHarness) -> None:
    (box.tree / "src" / "hal0" / "agents" / "containers_apparmor.py").unlink()
    res = box.install(check=False)
    assert res.returncode != 0 and "containers_apparmor.py" in res.stdout
    assert box.podman_calls("create") == 0


def test_active_firewall_gets_published_ports(box: BoxHarness) -> None:
    box.set_firewall("ufw", active=True)
    box.install()
    assert {"8080/tcp", "3001/tcp", "8188/tcp"} <= box.firewall_allowed()


def test_active_ufw_is_detected_when_its_table_is_written_late(box: BoxHarness) -> None:
    # Regression for hal0#3364: `ufw status | head -1 | grep -q` under pipefail
    # killed ufw with SIGPIPE whenever head exited before ufw finished writing.
    box.set_firewall("ufw", active=True)
    (box.state / "ufw_slow").write_text("")
    box.install()
    assert {"8080/tcp", "3001/tcp", "8188/tcp"} <= box.firewall_allowed()


def test_uninstall_removes_only_the_firewall_rules_it_added(box: BoxHarness) -> None:
    box.set_firewall("firewalld", active=True)
    (box.state / "firewalld_rules").write_text("3001/tcp\n")
    box.install()
    box.install()
    box.uninstall()
    assert box.firewall_allowed() == {"3001/tcp"}


def test_inactive_firewall_is_left_alone(box: BoxHarness) -> None:
    box.install()
    assert box.firewall_allowed() == set()
    assert not any(c.argv.startswith("ufw allow") for c in box.calls())


def test_shim_raises_memlock_and_requires_root(box: BoxHarness) -> None:
    res = box.shim_as_user()
    assert res.returncode != 0 and "sudo" in res.stdout
    box.install()
    text = box.shim_text()
    assert "ulimit -Hl unlimited" in text and "hal0-box-shim" in text
    assert "@BOX_SH@" not in text
    assert os.access(box.shim_path(), os.X_OK)


def test_shim_execs_hal0_in_the_box_and_routes_uninstall(box: BoxHarness) -> None:
    box.install()
    shim = box.shim_path()
    box.shim("status")
    assert box.podman_calls_named("exec")[-1].argv.endswith("hal0-box hal0 status")
    box.shim("uninstall", "--purge", "--yes")
    assert not shim.exists() and "hal0box" not in box.subuid() and not box.user_exists()


def test_memlock_dropin_is_scoped_to_the_box_user(box: BoxHarness) -> None:
    # user@.service.d would apply to every user's manager on the host.
    box.install()
    assert box.dropin_path().is_file()
    assert not box.legacy_dropin_path().parent.exists()


def test_install_replaces_a_legacy_host_wide_dropin(box: BoxHarness) -> None:
    legacy = box.legacy_dropin_path()
    legacy.parent.mkdir(parents=True)
    legacy.write_text("[Service]\nLimitMEMLOCK=infinity\nDelegate=cpu cpuset io memory pids\n")
    box.install()
    assert not legacy.exists() and not legacy.parent.exists()
    assert "LimitMEMLOCK=infinity" in box.dropin_path().read_text()
    assert any(c.argv == "systemctl restart user@2001.service" for c in box.calls())


def test_uninstall_removes_a_legacy_host_wide_dropin(box: BoxHarness) -> None:
    box.install()
    legacy = box.legacy_dropin_path()
    legacy.parent.mkdir(parents=True)
    legacy.write_text("[Service]\nLimitMEMLOCK=infinity\n")
    box.purge()
    assert not legacy.exists() and not box.dropin_path().exists()


def test_install_forwards_the_huggingface_token(box: BoxHarness) -> None:
    box.install(HF_TOKEN="hf_example", HUGGING_FACE_HUB_TOKEN="hf_twin")
    exec_call = next(c for c in box.podman_calls_named("exec") if "install.sh" in c.argv)
    assert "HF_TOKEN=hf_example" in exec_call.argv
    assert "HUGGING_FACE_HUB_TOKEN=hf_twin" in exec_call.argv


def test_uninstall_is_idempotent(box: BoxHarness) -> None:
    box.add_device("dri/renderD128", gid=993)
    box.install()
    assert "hal0box:993:1" in box.subgid()
    box.purge()
    assert box.purge().returncode == 0
    assert "hal0box" not in box.subuid() and "hal0box" not in box.subgid()
    assert not (box.host / "var" / "lib" / "hal0-box").exists()
    assert not box.dropin_path().exists()
    assert not box.shim_path().exists()
    box.uninstall()
    assert box.uninstall().returncode == 0


def test_uninstall_after_partial_install_cleans_what_remains(box: BoxHarness) -> None:
    box.fail_next("useradd")
    assert box.install(check=False).returncode != 0
    assert box.uninstall().returncode == 0
    assert "hal0box" not in box.subuid() and not (box.host / "var" / "lib" / "hal0-box").exists()


def test_uninstall_keeps_a_foreign_hal0_cli(box: BoxHarness) -> None:
    shim = box.shim_path()
    shim.parent.mkdir(parents=True)
    shim.write_text("#!/bin/sh\n# a bare-metal hal0 CLI\n")
    box.uninstall()
    assert shim.exists()


def test_purge_only_resets_box_user(box: BoxHarness) -> None:
    box.install()
    box.purge()
    resets = box.podman_calls_named("system reset")
    assert resets and all(c.user == "hal0box" for c in resets)


# ── uninstall keeps data unless --purge ───────────────────────────────────


def test_default_uninstall_keeps_the_box_data(box: BoxHarness) -> None:
    box.add_device("dri/renderD128", gid=993)
    box.install()
    res = box.uninstall()
    # Removed: the container, its unit, the shim, the drop-in, linger.
    assert not box.container_exists()
    assert not box.unit_path().exists()
    assert not box.shim_path().exists()
    assert not box.dropin_path().exists()
    assert any(c.argv.startswith("loginctl disable-linger hal0box") for c in box.calls())
    # Kept: the user, its sub-ID lines (volume ownership depends on them)
    # and the three state volumes.
    assert box.user_exists()
    assert "hal0box:" in box.subuid() and "hal0box:993:1" in box.subgid()
    assert box.volumes() == {"hal0-box-etc", "hal0-box-var", "hal0-box-containers"}
    assert box.podman_calls("system reset") == 0
    assert not any(c.argv.startswith("userdel") for c in box.calls())
    assert "--purge" in res.stdout


def test_reinstall_after_default_uninstall_reuses_the_volumes(box: BoxHarness) -> None:
    box.install()
    box.uninstall()
    box.install()
    assert box.state_is_healthy()
    assert len(box.podman_calls_named("volume create")) == 3
    assert box.subuid().count("hal0box:") == 1


def test_purge_refuses_non_interactive_without_yes(box: BoxHarness) -> None:
    box.install()
    res = box.uninstall("--purge", check=False)
    assert res.returncode != 0 and "--yes" in res.stdout
    assert box.state_is_healthy() and box.user_exists()
    assert box.podman_calls("rm") == 0


def test_purge_with_yes_removes_user_and_data(box: BoxHarness) -> None:
    box.install()
    box.purge()
    assert not box.user_exists() and box.volumes() == set()
    assert "hal0box" not in box.subuid()
    assert not (box.host / "var" / "lib" / "hal0-box").exists()


def _uninstall_on_tty(box: BoxHarness, typed: str, *args: str) -> tuple[int, str]:
    import pty

    master, slave = pty.openpty()
    try:
        os.write(master, typed.encode() + b"\n")
        res = subprocess.run(
            ["bash", str(box.tree / "installer" / "box" / "box.sh"), "uninstall", *args],
            env=box.env(),
            stdin=slave,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            timeout=60,
        )
    finally:
        os.close(slave)
        os.close(master)
    return res.returncode, res.stdout


def test_purge_after_a_plain_uninstall_deletes_the_kept_data(box: BoxHarness) -> None:
    box.install()
    box.uninstall()
    n = len(box.calls())
    box.purge()
    later = box.all_calls()[n:]
    # The user manager is down after the plain uninstall; it is started again
    # so the box user's podman can reset its store.
    start = later.index("systemctl start user@2001.service")
    assert start < next(i for i, a in enumerate(later) if a.startswith("podman system reset"))
    assert not box.user_exists() and box.volumes() == set() and "hal0box" not in box.subuid()


def test_purge_on_a_tty_needs_typed_delete(box: BoxHarness) -> None:
    box.install()
    rc, out = _uninstall_on_tty(box, "yes", "--purge")
    assert rc != 0 and "DELETE" in out
    assert box.user_exists() and box.state_is_healthy()
    rc, out = _uninstall_on_tty(box, "DELETE", "--purge")
    assert rc == 0, out
    assert not box.user_exists()


def test_uninstall_help_changes_nothing(box: BoxHarness) -> None:
    box.install()
    for res in (box.uninstall("--help"), box.shim("uninstall", "--help")):
        assert "--purge" in res.stdout and "usage" in res.stdout.lower()
    assert box.state_is_healthy() and box.user_exists()
    assert box.podman_calls("rm") == 0


def test_uninstall_unknown_flag_exits_2(box: BoxHarness) -> None:
    box.install()
    res = box.shim("uninstall", "--purg", check=False)
    assert res.returncode == 2 and "--purg" in res.stdout
    assert box.state_is_healthy() and box.user_exists()


# ── purge really deletes the user, or says it did not ─────────────────────


def test_purge_terminates_the_box_user_before_userdel(box: BoxHarness) -> None:
    box.install()
    box.purge()
    calls = box.all_calls()
    assert "loginctl terminate-user hal0box" in calls
    assert box.first_index("loginctl terminate-user hal0box") < box.first_index("userdel")


def test_purge_fails_when_box_processes_survive(box: BoxHarness) -> None:
    box.install()
    (box.state / "stuck").write_text("")
    res = box.purge(check=False)
    assert res.returncode != 0 and "still running" in res.stdout
    assert "removed" not in res.stdout.splitlines()[-1]
    assert any(c.argv.startswith("pkill -KILL -u hal0box") for c in box.calls())
    assert not any(c.argv.startswith("userdel") for c in box.calls())
    assert box.user_exists() and "hal0box:" in box.subuid()
    assert (box.host / "var" / "lib" / "hal0-box").exists()


def test_purge_fails_when_the_user_cannot_be_deleted(box: BoxHarness) -> None:
    box.install()
    box.fail_next("userdel")
    res = box.purge(check=False)
    assert res.returncode != 0 and "userdel" in res.stdout
    assert box.user_exists() and "hal0box:" in box.subuid()
    assert (box.host / "var" / "lib" / "hal0-box").exists()


# ── a hal0box user hal0 did not create is never adopted ───────────────────


def _foreign_user(box: BoxHarness, gecos: str = "Someone Else") -> None:
    with (box.state / "users").open("a") as f:
        f.write(f"hal0box:1500:{gecos}\n")


def test_install_records_that_hal0_created_the_user(box: BoxHarness) -> None:
    box.install()
    marker = box.host / "var" / "lib" / "hal0-box" / "owner"
    assert marker.read_text().strip() == "hal0box:2001"
    assert "hal0box:2001:hal0 box" in (box.state / "users").read_text()


def test_preexisting_foreign_user_is_refused(box: BoxHarness) -> None:
    _foreign_user(box)
    res = box.preflight(check=False)
    assert res.returncode != 0 and "did not create" in res.stdout
    res = box.install(check=False)
    assert res.returncode != 0 and "did not create" in res.stdout
    assert box.podman_calls("create") == 0 and "hal0box" not in box.subuid()


def test_foreign_user_with_matching_comment_but_no_marker_is_refused(box: BoxHarness) -> None:
    _foreign_user(box, gecos="hal0 box")
    res = box.install(check=False)
    assert res.returncode != 0 and "did not create" in res.stdout


def test_purge_refuses_a_foreign_user(box: BoxHarness) -> None:
    _foreign_user(box)
    (box.host / "etc" / "subuid").write_text("hal0box:300000:65536\n")
    res = box.purge(check=False)
    assert res.returncode != 0 and "did not create" in res.stdout
    assert box.user_exists() and "hal0box:300000:65536" in box.subuid()
    assert not any(c.argv.startswith(("userdel", "podman", "pkill")) for c in box.calls())


def test_plain_uninstall_leaves_a_foreign_user_alone(box: BoxHarness) -> None:
    _foreign_user(box)
    (box.host / "etc" / "subuid").write_text("hal0box:300000:65536\n")
    box.uninstall()
    assert box.user_exists() and "hal0box:300000:65536" in box.subuid()
    assert not any(c.user == "hal0box" for c in box.calls())
    assert not any(c.argv.startswith("loginctl disable-linger") for c in box.calls())


# ── root never writes through the box user's home ─────────────────────────


def _plant_home(box: BoxHarness) -> Path:
    home = box.host / "home" / "hal0box"
    home.mkdir(parents=True)
    return home


def test_containerfile_is_rendered_outside_the_box_home(box: BoxHarness) -> None:
    home = _plant_home(box)
    victim = box.tmp / "victim"
    victim.mkdir()
    (home / "box").symlink_to(victim)
    box.install()
    assert list(victim.iterdir()) == []
    build_dir = box.host / "var" / "lib" / "hal0-box" / "build"
    cf = build_dir / "Containerfile"
    assert "\nFROM docker.io/library/ubuntu:26.04@sha256:" in cf.read_text()
    assert stat.S_IMODE(cf.stat().st_mode) == 0o644
    assert stat.S_IMODE(build_dir.stat().st_mode) == 0o755
    build = box.podman_calls_named("build")[0]
    assert build.user == "hal0box" and build.argv.endswith(str(build_dir))


def test_user_unit_is_written_by_the_box_user(box: BoxHarness) -> None:
    home = _plant_home(box)
    box.install()
    unit = home / ".config" / "systemd" / "user" / "hal0-box.service"
    writes = [c for c in box.calls() if c.argv.startswith("tee") and str(unit) in c.argv]
    assert writes and all(c.user == "hal0box" for c in writes)
    assert "start hal0-box" in unit.read_text()


def test_root_never_writes_or_chowns_under_the_box_home(box: BoxHarness) -> None:
    box.install()
    box.rebuild()
    box.uninstall()
    home = str(box.host / "home" / "hal0box")
    for c in box.calls():
        if c.user == "root" and c.argv.split(" ", 1)[0] in {"tee", "mkdir", "chmod", "chown", "rm"}:
            assert home not in c.argv, c


# ── memory limit when the host is itself a container ──────────────────────

_LXC_MEMTOTAL_KB = 50331648  # 48 GiB, as lxcfs shows the LXC's limit


def _outer_lxc(box: BoxHarness, marker: str | None = "lxc\n") -> None:
    proc = box.host / "proc"
    proc.mkdir(exist_ok=True)
    (proc / "meminfo").write_text(
        f"MemTotal:       {_LXC_MEMTOTAL_KB} kB\nMemAvailable:   40000000 kB\n"
    )
    if marker is not None:
        (box.host / "run" / "systemd").mkdir(parents=True, exist_ok=True)
        (box.host / "run" / "systemd" / "container").write_text(marker)


def test_box_in_an_lxc_gets_the_lxc_memory_as_a_cgroup_limit(box: BoxHarness) -> None:
    _outer_lxc(box)
    argv = box.dry_run()
    assert f"--memory={_LXC_MEMTOTAL_KB * 1024}" in argv


def test_box_in_a_narrow_map_gets_a_memory_limit(box: BoxHarness) -> None:
    _outer_lxc(box, marker=None)
    box.set_uid_map("0 100000 65536")
    assert f"--memory={_LXC_MEMTOTAL_KB * 1024}" in box.dry_run()


def test_bare_metal_box_has_no_memory_limit(box: BoxHarness) -> None:
    _outer_lxc(box, marker=None)
    assert not any(a.startswith("--memory") for a in box.dry_run())


def test_no_memory_controller_warns_instead_of_failing(box: BoxHarness) -> None:
    _outer_lxc(box)
    (box.host / "sys" / "fs" / "cgroup" / "cgroup.controllers").write_text("cpu pids\n")
    out = box.run("install", str(box.tree), HAL0_BOX_DRY_RUN="1").stdout
    assert "--memory=" not in out and "memory controller" in out


# ── bootstrap's verified cosign reaches the box ───────────────────────────

_BOX_COSIGN = "/var/tmp/hal0-bootstrap-cosign"


def _bootstrap_cosign(box: BoxHarness) -> Path:
    work = box.tmp / "hal0-install-xyz"
    work.mkdir()
    cosign = work / "cosign"
    _write_exec(cosign, "echo cosign")
    return cosign


def _installer_exec(box: BoxHarness) -> Call:
    return [c for c in box.podman_calls_named("exec") if "install.sh" in c.argv][-1]


def test_bootstrap_cosign_is_copied_into_the_box(box: BoxHarness) -> None:
    cosign = _bootstrap_cosign(box)
    box.install(HAL0_BOOTSTRAP_COSIGN=str(cosign))
    kept = box.host / "var" / "lib" / "hal0-box" / "cosign"
    assert kept.read_text() == cosign.read_text()
    assert stat.S_IMODE(kept.stat().st_mode) == 0o755
    cps = [c for c in box.podman_calls_named("cp") if c.argv.endswith(f"hal0-box:{_BOX_COSIGN}")]
    assert len(cps) == 1 and cps[0].user == "hal0box" and str(kept) in cps[0].argv
    run = _installer_exec(box).argv
    assert f"HAL0_BOOTSTRAP_COSIGN={_BOX_COSIGN}" in run and str(cosign) not in run
    assert box.first_index(f"podman cp {kept}") < box.first_index("podman exec -e")


def test_rebuild_copies_the_kept_cosign_again(box: BoxHarness) -> None:
    box.install(HAL0_BOOTSTRAP_COSIGN=str(_bootstrap_cosign(box)))
    box.rebuild()
    cps = [c for c in box.podman_calls_named("cp") if c.argv.endswith(f"hal0-box:{_BOX_COSIGN}")]
    assert len(cps) == 2
    assert f"HAL0_BOOTSTRAP_COSIGN={_BOX_COSIGN}" in _installer_exec(box).argv


def test_no_bootstrap_cosign_hands_nothing_over(box: BoxHarness) -> None:
    box.install()
    assert not (box.host / "var" / "lib" / "hal0-box" / "cosign").exists()
    assert "HAL0_BOOTSTRAP_COSIGN" not in _installer_exec(box).argv


# ── AppArmor changes are recorded and reverted ────────────────────────────


def _nested_apparmor(box: BoxHarness, *names: str) -> Path:
    aa = box.state / "aa"
    (aa / "disable").mkdir(parents=True)
    for n in ("usr.bin.pasta", "usr.bin.passt", "operator.profile"):
        (aa / n).write_text("profile x {}\n")
    (box.state / "aa_names").write_text(" ".join(names) + "\n")
    return aa


def test_uninstall_reverts_the_apparmor_profiles_the_box_disabled(box: BoxHarness) -> None:
    aa = _nested_apparmor(box, "usr.bin.pasta", "usr.bin.passt")
    # Disabled by the operator before hal0: never reverted.
    (aa / "disable" / "operator.profile").symlink_to(aa / "operator.profile")
    box.install()
    box.install()
    record = box.host / "var" / "lib" / "hal0-box" / "apparmor"
    assert record.read_text().splitlines() == [
        f"{aa}/disable/usr.bin.pasta {aa}/usr.bin.pasta",
        f"{aa}/disable/usr.bin.passt {aa}/usr.bin.passt",
    ]
    box.uninstall()
    assert not (aa / "disable" / "usr.bin.pasta").exists()
    assert not (aa / "disable" / "usr.bin.passt").exists()
    assert (aa / "disable" / "operator.profile").is_symlink()
    reloads = [c.argv for c in box.calls() if c.argv.startswith("apparmor_parser -r")]
    assert reloads == [
        f"apparmor_parser -r {aa}/usr.bin.pasta",
        f"apparmor_parser -r {aa}/usr.bin.passt",
    ]
    assert not record.exists()


def test_apparmor_revert_leaves_a_link_it_did_not_make(box: BoxHarness) -> None:
    aa = _nested_apparmor(box, "usr.bin.pasta")
    box.install()
    link = aa / "disable" / "usr.bin.pasta"
    link.unlink()
    link.write_text("an operator's file\n")
    box.purge()
    assert link.read_text() == "an operator's file\n"


# ── interactive exec never hands root's terminal to the box user ──────────


def _runuser_calls(box: BoxHarness) -> list[str]:
    return [c.argv for c in box.calls() if c.argv.startswith("runuser") and "podman exec" in c.argv]


def test_exec_on_a_terminal_gives_the_box_user_its_own_pty(box: BoxHarness) -> None:
    import pty

    box.install()
    n = len(_runuser_calls(box))
    master, slave = pty.openpty()
    try:
        res = subprocess.run(
            ["bash", str(box.shim_path()), "status"],
            env=box.env(),
            stdin=slave,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            timeout=60,
        )
    finally:
        os.close(slave)
        os.close(master)
    assert res.returncode == 0, res.stdout
    call = _runuser_calls(box)[n:][-1]
    assert call.startswith("runuser --pty -u hal0box"), call


def test_exec_on_a_terminal_without_runuser_pty_drops_the_tty(box: BoxHarness) -> None:
    import pty

    box.install()
    # An old runuser without --pty: fall back to setsid, so the box process
    # has no controlling terminal for TIOCSTI.
    old = box.bin / "runuser"
    old.write_text(old.read_text().replace("-P, --pty", "-m, --preserve"))
    master, slave = pty.openpty()
    try:
        res = subprocess.run(
            ["bash", str(box.shim_path()), "status"],
            env=box.env(),
            stdin=slave,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            timeout=60,
        )
    finally:
        os.close(slave)
        os.close(master)
    assert res.returncode == 0, res.stdout
    call = [c for c in box.calls() if c.argv.startswith("runuser") and "hal0 status" in c.argv][-1]
    assert "--pty" not in call.argv and " setsid -w podman exec" in call.argv


def test_exec_without_a_terminal_keeps_plain_runuser(box: BoxHarness) -> None:
    box.install()
    n = len(_runuser_calls(box))
    box.shim("status")
    call = _runuser_calls(box)[n:][-1]
    assert call.startswith("runuser -u hal0box"), call
    # Only exec gets a pty: the launcher's own steps never do.
    assert not any("--pty" in c for c in _runuser_calls(box))


def test_bootstrap_work_dir_is_removed_after_success(box: BoxHarness) -> None:
    work = box.tmp / "hal0-install-abc123"
    work.mkdir()
    box.install(HAL0_BOOTSTRAP_WORK=str(work))
    assert not work.exists()


# ── Task 7: state on volumes and rebuild ──────────────────────────────────

_VOLUMES = (
    "hal0-box-etc:/etc/hal0",
    "hal0-box-var:/var/lib/hal0",
    "hal0-box-containers:/var/lib/containers",
)


def test_create_mounts_state_volumes(box: BoxHarness) -> None:
    mounts = _flag_value(box.dry_run(), "--volume")
    for mount in _VOLUMES:
        assert mount in mounts


def test_install_creates_each_volume_once(box: BoxHarness) -> None:
    box.install()
    box.install()
    created = [c.argv.split()[-1] for c in box.podman_calls_named("volume create")]
    assert sorted(created) == ["hal0-box-containers", "hal0-box-etc", "hal0-box-var"]


def test_rebuild_keeps_volumes_and_reruns_installer(box: BoxHarness) -> None:
    box.install("--no-pi", HAL0_BOOTSTRAP_VERIFIED="1")
    box.rebuild()
    assert box.podman_calls_named("volume rm") == []
    assert box.podman_calls("system reset") == 0
    assert box.ran_installer(times=2)
    assert box.installer_runs()[1].endswith("--no-pi")
    last_exec = [c for c in box.podman_calls_named("exec") if "install.sh" in c.argv][-1]
    assert "HAL0_BOOTSTRAP_VERIFIED=1" in last_exec.argv
    builds = box.podman_calls_named("build")
    assert len(builds) == 2 and "--no-cache" in builds[1].argv
    assert box.state_is_healthy()


def test_rebuild_after_an_install_without_args_passes_none(box: BoxHarness) -> None:
    box.install()
    box.rebuild()
    first, second = box.installer_runs()
    assert first == second == "bash /usr/local/src/hal0/installer/install.sh"


def test_rebuild_without_an_install_fails_clearly(box: BoxHarness) -> None:
    res = box.rebuild(check=False)
    assert res.returncode != 0 and "not installed" in res.stdout


def test_image_carries_the_box_marker() -> None:
    run = _CONTAINERFILE.read_text().split("\nRUN ", 1)[1]
    assert "/etc/hal0-box-release" in run


def test_image_has_unattended_upgrades() -> None:
    text = _CONTAINERFILE.read_text()
    install = text.split("\nRUN ", 1)[1].split("apt-get install", 1)[1].split("\n && ", 1)[0]
    assert "unattended-upgrades" in install
    assert 'APT::Periodic::Unattended-Upgrade "1"' in text
