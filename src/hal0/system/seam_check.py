"""Verification for the privileged sudo seams hal0 runs on (#1465).

Every privileged operation hal0 performs post-P3-perms goes through one of a
handful of narrow ``sudo -n /usr/lib/hal0/bin/hal0-*`` wrappers: slot lifecycle
(``hal0-systemctl``), self-update (``hal0-update``), agent env files
(``hal0-agentenv``), the benchmark harness (``hal0-benchctl``) and read-only
podman introspection (``hal0-podman-ro``). ``install.sh`` installs each
best-effort — a ``visudo -cf`` failure, or a missing source file, produced only
a mid-log ``warn`` and the install still printed its success box.

Nothing verified the result afterwards. ``preflight_all`` never touched sudoers,
``doctor verify`` composes only live-API rows, and ``doctor all``'s extra rows
covered auth/models/migrations/ports/hal0.target. So a box where that warn fired
reported **all green from every doctor surface** while every slot start, unit
write and daemon-reload failed undiagnosably.

This module is the missing predicate. It is deliberately pure + injectable
(``stat``/``run``/``euid`` seams, same pattern as
:func:`hal0.cli.doctor_all.check_hal0_target`) so the classification is testable
without root, a real sudoers file, or a provisioned box.

Three independent facts per seam:

* **binary**   — ``${LIB_DIR}/bin/<name>`` exists, is a regular file, is
  ``root:root``, and is mode 0755.
* **sudoers**  — ``/etc/sudoers.d/<name>`` exists, is ``root``-owned, and is
  mode 0440 (sudo ignores — and on some builds refuses — a group/other-writable
  drop-in, so a wrong mode is a silent total failure).
* **grant**    — the end-to-end truth: ``sudo -n <bin> <probe>`` actually exits
  0 *as the hal0 user*. Presence of both files does not imply the grant applies
  (a syntactically valid drop-in can still be shadowed, or name a user that
  doesn't exist).

The grant probe only runs for seams that expose a genuinely side-effect-free
verb. ``hal0-systemctl help`` and ``hal0-update check`` do; the other three are
presence-checked only, which is stated in the row detail rather than silently
implied.
"""

from __future__ import annotations

import os
import stat as stat_mod
import subprocess
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

#: Where the wrapper binaries land (``${LIB_DIR}/bin`` at install time).
SEAM_BIN_DIR = Path("/usr/lib/hal0/bin")

#: Where the matching sudoers drop-ins land.
SUDOERS_DIR = Path("/etc/sudoers.d")

#: The service account the grants are written for.
SERVICE_USER = "hal0"


@dataclass(frozen=True)
class SeamSpec:
    """One privileged seam: its wrapper, its grant, and how to prove it works."""

    name: str
    #: Side-effect-free verb used to prove the grant end-to-end, or ``None``
    #: when the wrapper exposes no such verb (presence-checked only).
    probe: tuple[str, ...] | None
    #: ``True`` when hal0 is unusable without it — a fail, not a warn.
    required: bool
    #: What breaks when this seam is missing (goes into the remediation text).
    role: str


#: THE inventory. Adding a wrapper to ``install.sh`` without adding it here is
#: the exact regression #1465 is about, so keep the two in lock-step.
SEAMS: tuple[SeamSpec, ...] = (
    SeamSpec(
        "hal0-systemctl",
        probe=("help",),
        required=True,
        role="slot lifecycle (unit writes, daemon-reload, start/stop)",
    ),
    SeamSpec(
        "hal0-update",
        probe=("check",),
        required=True,
        role="self-update (stage / activate / discard)",
    ),
    SeamSpec("hal0-agentenv", probe=None, required=False, role="bundled-agent env files"),
    SeamSpec("hal0-benchctl", probe=None, required=False, role="benchmark harness"),
    SeamSpec("hal0-podman-ro", probe=None, required=False, role="podman image introspection"),
)


@dataclass(frozen=True)
class SeamStatus:
    """The three verified facts for one seam, plus why each failed."""

    spec: SeamSpec
    binary_ok: bool
    binary_detail: str
    sudoers_ok: bool
    sudoers_detail: str
    #: ``None`` = not testable from here (no probe verb, or we are neither root
    #: nor the service account) — never conflated with "tested and broken".
    grant_ok: bool | None
    grant_detail: str

    @property
    def ok(self) -> bool:
        """True when nothing we could check is broken."""
        return self.binary_ok and self.sudoers_ok and self.grant_ok is not False

    @property
    def problems(self) -> list[str]:
        out = []
        if not self.binary_ok:
            out.append(self.binary_detail)
        if not self.sudoers_ok:
            out.append(self.sudoers_detail)
        if self.grant_ok is False:
            out.append(self.grant_detail)
        return out


StatFn = Callable[[Path], os.stat_result]
RunFn = Callable[..., "subprocess.CompletedProcess[str]"]


def _default_stat(path: Path) -> os.stat_result:
    return path.lstat()


def grant_probe_argv(
    name: str,
    probe: tuple[str, ...],
    *,
    euid: int,
    bin_dir: Path = SEAM_BIN_DIR,
) -> list[str] | None:
    """Build the argv that proves the grant works, or ``None`` if untestable.

    The grant is written for the ``hal0`` user, so the only honest test is one
    performed *as that user*:

    * euid 0 — ``sudo -n -u hal0 sudo -n <bin> <probe>``: drop to the service
      account first, then exercise its own grant. This is the installer /
      ``sudo hal0 doctor`` case, and the only one that can prove the grant for
      a box the operator is inspecting from a root shell.
    * running AS hal0 — ``sudo -n <bin> <probe>``: exactly what hal0-api does.
    * any other unprivileged user — ``None``. A grant written for ``hal0`` will
      correctly fail for them, and reporting that as a broken seam would be a
      false alarm.
    """
    target = str(bin_dir / name)
    if euid == 0:
        return ["sudo", "-n", "-u", SERVICE_USER, "sudo", "-n", target, *probe]
    try:
        import pwd

        if euid == pwd.getpwnam(SERVICE_USER).pw_uid:
            return ["sudo", "-n", target, *probe]
    except (ImportError, KeyError):
        return None
    return None


def probe_seam(
    spec: SeamSpec,
    *,
    stat: StatFn | None = None,
    run: RunFn | None = None,
    euid: int | None = None,
    bin_dir: Path = SEAM_BIN_DIR,
    sudoers_dir: Path = SUDOERS_DIR,
) -> SeamStatus:
    """Verify one seam's binary, grant file, and (when possible) live grant."""
    _stat = stat or _default_stat
    _run = run or subprocess.run
    _euid = os.geteuid() if euid is None else euid

    binary = bin_dir / spec.name
    binary_ok, binary_detail = _check_file(
        _stat, binary, want_mode=0o755, want_uid=0, want_gid=0, label="wrapper"
    )

    grant = sudoers_dir / spec.name
    sudoers_ok, sudoers_detail = _check_file(
        _stat, grant, want_mode=0o440, want_uid=0, want_gid=None, label="sudoers grant"
    )

    grant_ok: bool | None = None
    grant_detail = ""
    if spec.probe is None:
        grant_detail = f"{spec.name}: no side-effect-free verb — presence-checked only"
    elif not (binary_ok and sudoers_ok):
        grant_detail = f"{spec.name}: grant not probed (wrapper or drop-in is broken)"
    else:
        argv = grant_probe_argv(spec.name, spec.probe, euid=_euid, bin_dir=bin_dir)
        if argv is None:
            grant_detail = (
                f"{spec.name}: grant not testable as uid {_euid} — "
                f"re-run as root or as the {SERVICE_USER} user"
            )
        else:
            try:
                proc = _run(  # nosec B603 — fixed argv built from the static inventory
                    argv, capture_output=True, text=True, check=False, timeout=20
                )
            except (OSError, subprocess.SubprocessError) as exc:
                grant_ok = False
                grant_detail = f"{spec.name}: grant probe could not run ({exc})"
            else:
                grant_ok = proc.returncode == 0
                if not grant_ok:
                    stderr = (proc.stderr or "").strip().splitlines()
                    tail = stderr[-1][:160] if stderr else ""
                    grant_detail = (
                        f"{spec.name}: `sudo -n {spec.name} {' '.join(spec.probe)}` exited "
                        f"{proc.returncode} as {SERVICE_USER} — the {grant} grant does not "
                        f"apply{f' ({tail})' if tail else ''}"
                    )

    return SeamStatus(
        spec=spec,
        binary_ok=binary_ok,
        binary_detail=binary_detail,
        sudoers_ok=sudoers_ok,
        sudoers_detail=sudoers_detail,
        grant_ok=grant_ok,
        grant_detail=grant_detail,
    )


def _check_file(
    stat: StatFn,
    path: Path,
    *,
    want_mode: int,
    want_uid: int,
    want_gid: int | None,
    label: str,
) -> tuple[bool, str]:
    """Existence + ownership + mode for one seam file (pure given ``stat``)."""
    try:
        st = stat(path)
    except OSError:
        return False, f"{label} {path} is missing"
    if not stat_mod.S_ISREG(st.st_mode):
        return False, f"{label} {path} is not a regular file"
    mode = st.st_mode & 0o7777
    if mode != want_mode:
        return False, f"{label} {path} is mode {mode:04o}, expected {want_mode:04o}"
    if st.st_uid != want_uid:
        return False, f"{label} {path} is owned by uid {st.st_uid}, expected {want_uid}"
    if want_gid is not None and st.st_gid != want_gid:
        return False, f"{label} {path} has gid {st.st_gid}, expected {want_gid}"
    return True, f"{label} {path} ok"


def probe_seams(
    specs: tuple[SeamSpec, ...] = SEAMS,
    **kwargs: object,
) -> list[SeamStatus]:
    """Verify every seam in the inventory."""
    return [probe_seam(spec, **kwargs) for spec in specs]  # type: ignore[arg-type]


REMEDIATION = (
    "re-run the installer (`sudo bash install.sh`) to reinstall the wrapper + "
    "sudoers grant, then re-check with `sudo hal0 doctor all`"
)


__all__ = [
    "REMEDIATION",
    "SEAMS",
    "SEAM_BIN_DIR",
    "SERVICE_USER",
    "SUDOERS_DIR",
    "SeamSpec",
    "SeamStatus",
    "grant_probe_argv",
    "probe_seam",
    "probe_seams",
]
