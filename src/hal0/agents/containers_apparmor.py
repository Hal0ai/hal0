"""Convergent AppArmor preflight for podman on unconfined LXC (halo150 R4).

On a privileged LXC configured ``apparmor.profile: unconfined`` the container
CANNOT load podman's default AppArmor profile, so ``podman run`` dies with::

    install profile containers-default apparmor: exit status 243

and NO slot ever starts. The fix (halo150 R4, load-bearing) is to tell podman to
run containers unconfined via ``/etc/containers/containers.conf``::

    [containers]
    apparmor_profile = "unconfined"

This module makes that fix CONVERGENT and IDEMPOTENT and — critically — detects
the condition from the podman SMOKE FAILURE, never from blind OS/LXC sniffing
(the runbook's explicit instruction). A box whose podman smoke already passes is
left completely untouched; a box that fails with the apparmor signature gets the
config written (once) and the smoke retried.

Every subprocess is injected (``run``) so the whole detect→write→retry chain is
unit-tested against recorded fakes with no real podman or ``/etc`` writes.

#1563: this module is also invoked as a bare script (``python
containers_apparmor.py``, NOT ``python -m hal0.agents.containers_apparmor``)
from ``installer/lib/preflight.sh``'s ``_container_runtime_gate`` — the EARLY
hard podman preflight gate that runs before the venv exists / hal0 is
pip-installed. Bare-script invocation skips the ``hal0.agents`` package
``__init__`` (and its heavy transitive deps), but this file is still imported
directly, so ``structlog`` — the one third-party import here — is made
optional below with a stdlib fallback. Everything else is stdlib-only
(``tomllib`` has been stdlib since 3.11; hal0's floor is 3.12).

#3234: a SECOND, independent remediation lives here. On an UNPRIVILEGED
Ubuntu 24.04 LXC the guest's own AppArmor profiles for podman / crun / buildah
/ runc / rootlesskit / slirp4netns are ``flags=(unconfined)`` userns stubs that
deny every podman ``socket()`` (``socket: permission denied``) — and the
``apparmor_profile = "unconfined"`` fix above cannot help, because the denial
comes from the guest's own profiles, not podman's ``containers-default``. The
fix is the Ubuntu-documented one: symlink each such profile under
``/etc/apparmor.d/disable/`` and unload it with ``apparmor_parser -R``.

That fix is only safe inside a NESTED AppArmor policy namespace (an
unprivileged LXC: ``/sys/kernel/security/apparmor/.ns_level`` > 0). On bare
metal or a privileged container the very same stubs are the userns grant that
``kernel.apparmor_restrict_unprivileged_userns=1`` relies on; disabling them
would silently and persistently break the operator's own rootless podman. So
nothing is mutated unless ``.ns_level`` parses as an integer > 0.

The disable symlink and ``apparmor_parser -R`` act on a WHOLE profile file, so a
file is only touched when EVERY profile block header in it carries
``flags=(unconfined)`` (see :func:`guest_unconfined_profiles_present`). And a
symlink is not proof the profile was unloaded: :class:`ApparmorPreflightResult`
records the profiles whose ``apparmor_parser -R`` failed in ``unload_failed``.
"""

from __future__ import annotations

import os
import re
import subprocess  # nosec B404 — smoke-tests the local podman
import tomllib
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

try:
    import structlog

    log = structlog.get_logger(__name__)
except ImportError:  # pragma: no cover - exercised by the early-installer path
    # structlog isn't on the interpreter yet when this runs as a bare script
    # before `pip install`. Fall back to a tiny stdlib logger with the same
    # `log.info(event, **kw)` call shape used below.
    import logging

    class _FallbackLog:
        _logger = logging.getLogger(__name__)

        def info(self, event: str, **kw: Any) -> None:
            extra = " ".join(f"{k}={v}" for k, v in kw.items())
            self._logger.info("%s", f"{event} {extra}".rstrip())

        def warning(self, event: str, **kw: Any) -> None:
            extra = " ".join(f"{k}={v}" for k, v in kw.items())
            self._logger.warning("%s", f"{event} {extra}".rstrip())

    log = _FallbackLog()  # type: ignore[assignment]

#: Canonical podman config the fix lands in (halo150 R4).
CONTAINERS_CONF = Path("/etc/containers/containers.conf")

#: Default smoke image. The apparmor profile load fails BEFORE the image is
#: pulled/run, so any image would do for detection — but on an air-gapped or
#: mirrored-registry host (installer/lib/preflight.sh's
#: HAL0_CONTAINER_SMOKE_IMAGE override) quay.io itself may be unreachable, in
#: which case a hardcoded quay.io image here would fail the PULL, get
#: misclassified as "unrelated", and skip the fix even on a genuinely
#: apparmor-broken host. `_default_smoke_argv()` below honours the same
#: HAL0_CONTAINER_SMOKE_IMAGE env var so the CLI entry (`_main`) always probes
#: with whatever image the rest of the installer is actually configured to
#: use. No trailing command override (e.g. `true`) — the default entrypoint
#: matches installer/lib/preflight.sh's own smoke shape and avoids a false
#: "unrelated" exec-not-found classification on a distroless image once the
#: real (apparmor) problem is already fixed.
_DEFAULT_SMOKE_IMAGE = "quay.io/podman/hello"
_SMOKE_ARGV: tuple[str, ...] = ("podman", "run", "--rm", _DEFAULT_SMOKE_IMAGE)


def _default_smoke_argv() -> tuple[str, ...]:
    """Smoke argv for the CLI entry, honouring HAL0_CONTAINER_SMOKE_IMAGE."""
    image = os.environ.get("HAL0_CONTAINER_SMOKE_IMAGE", _DEFAULT_SMOKE_IMAGE)
    return ("podman", "run", "--rm", image)


#: Substrings that mark the unconfined-LXC AppArmor profile-load failure in
#: `<rt> run`'s combined output. Requires "apparmor" AND at least one of the
#: more specific shapes below — mirrors (and must stay in lockstep with)
#: installer/lib/preflight.sh's `_is_apparmor_profile_load_failure`, which
#: runs this same classification a layer earlier (before the venv/this module
#: exist) on the raw shell smoke-test output. Two independent failure text
#: shapes are both real-world observed: the OCI-runtime wrapper message
#: ("install profile containers-default apparmor: exit status 243") and the
#: raw `apparmor_parser` failure ("apparmor_parser: ... Access denied").
_APPARMOR_REQUIRED_TOKEN = "apparmor"
_APPARMOR_SIGNATURE_ANY: tuple[str, ...] = (
    "install profile containers-default",
    "access denied",
    "exit status 243",
)

#: #3234 — guest AppArmor profiles that ship ``flags=(unconfined)`` on Ubuntu
#: 24.04 and, inside an UNPRIVILEGED LXC, deny every podman ``socket()``.
GUEST_UNCONFINED_PROFILES: tuple[str, ...] = (
    "podman",
    "crun",
    "buildah",
    "runc",
    "rootlesskit",
    "slirp4netns",
)
#: Only a profile whose text carries this token is touched — an operator who
#: switched one to enforce mode keeps it.
_UNCONFINED_FLAG_TOKEN = "flags=(unconfined)"
#: Smoke-output signature of the #3234 denial (matched case-insensitively).
#: Anchored on the ``: `` that precedes ``socket:`` in real output
#: (``dial udp 9.9.9.9:53: socket: permission denied``) so a path such as
#: ``open /run/podman/podman.socket: permission denied`` does not match.
_SOCKET_DENIED_TOKEN = ": socket: permission denied"
_NS_LEVEL_FILE_DEFAULT = "/sys/kernel/security/apparmor/.ns_level"


def apparmor_d_root() -> Path:
    """AppArmor profile dir; ``HAL0_APPARMOR_D`` overrides it (tests)."""
    return Path(os.environ.get("HAL0_APPARMOR_D", "/etc/apparmor.d"))


def apparmor_ns_level_file() -> Path:
    """AppArmor policy-namespace level file; ``HAL0_APPARMOR_NS_LEVEL_FILE`` overrides it (tests)."""
    return Path(os.environ.get("HAL0_APPARMOR_NS_LEVEL_FILE", _NS_LEVEL_FILE_DEFAULT))


def _apparmor_ns_level_raw() -> str:
    """Stripped text of the ns-level file, or ``"unreadable"`` if it cannot be read."""
    try:
        return apparmor_ns_level_file().read_text(encoding="utf-8").strip()
    except OSError:
        return "unreadable"


def _in_nested_apparmor_namespace() -> bool:
    """True iff ``.ns_level`` parses as a plain integer > 0 (a nested policy namespace)."""
    raw = _apparmor_ns_level_raw()
    return re.fullmatch(r"[0-9]+", raw) is not None and int(raw) > 0


@dataclass
class ApparmorPreflightResult:
    """Outcome of :func:`ensure_podman_apparmor_usable`.

    ``outcome`` is one of:

    * ``"ok"``          — the initial smoke passed; nothing written.
    * ``"fixed"``       — apparmor failure detected, config written, retry passed.
    * ``"already"``     — apparmor failure detected but the config was already
                          set (idempotent rerun); retry passed / attempted.
    * ``"unrelated"``   — the smoke failed for a NON-apparmor reason; untouched.
    * ``"retry_failed"``— config written but the retry smoke still failed.
    * ``"no_podman"``   — podman is not installed / not on PATH.
    * ``"guest_profiles_disabled"`` — #3234: the smoke failed with
                          ``socket: permission denied`` inside a nested AppArmor
                          namespace and the guest's ``flags=(unconfined)``
                          profiles were disabled; ``disabled`` lists them,
                          ``fixed`` says whether the retry smoke passed,
                          ``unload_failed`` lists the ones whose
                          ``apparmor_parser -R`` failed (the disable symlink
                          is in place either way) and ``detail`` carries the
                          retry's error and/or the unload failures.
                          (A root AppArmor namespace, or no such profile file,
                          yields ``"unrelated"`` with nothing changed.)
    """

    outcome: str
    wrote_config: bool = False
    detail: str | None = None
    #: ``guest_profiles_disabled`` only: did the retry smoke pass?
    fixed: bool = False
    #: ``guest_profiles_disabled`` only: the profile names that were disabled.
    disabled: list[str] = field(default_factory=list)
    #: ``guest_profiles_disabled`` only: the subset of ``disabled`` whose
    #: ``apparmor_parser -R`` failed or could not run, so the profile may still
    #: be loaded until the next profile reload / reboot.
    unload_failed: list[str] = field(default_factory=list)


def _smoke(run: Callable[..., Any], argv: tuple[str, ...]) -> subprocess.CompletedProcess[str]:
    return run(  # nosec B603 — fixed argv from this module
        list(argv),
        capture_output=True,
        text=True,
        check=False,
    )


def _is_apparmor_failure(proc: subprocess.CompletedProcess[str]) -> bool:
    if proc.returncode == 0:
        return False
    blob = f"{proc.stdout or ''}\n{proc.stderr or ''}".lower()
    if _APPARMOR_REQUIRED_TOKEN not in blob:
        return False
    return any(sig in blob for sig in _APPARMOR_SIGNATURE_ANY)


def _is_unconfined_profile_network_denial(proc: subprocess.CompletedProcess[str]) -> bool:
    """#3234 signature: ``: socket: permission denied`` and NOT the #1563 shape."""
    if proc.returncode == 0:
        return False
    blob = f"{proc.stdout or ''}\n{proc.stderr or ''}".lower()
    return _SOCKET_DENIED_TOKEN in blob and not _is_apparmor_failure(proc)


#: First token of a profile block header, applied to a comment-stripped,
#: whitespace-stripped line that ends with ``{``: the ``profile`` keyword, a bare
#: attachment path (``/usr/bin/podman flags=(...) {``) or an attachment variable
#: (``@{exec_path} {``). ``^hat`` blocks are not profiles of their own (they
#: belong to the enclosing profile) and rules never end with ``{``.
_PROFILE_HEADER_START_RE = re.compile(r"(?:profile\s|/|@\{)")


def _profile_headers(text: str) -> list[str]:
    """Profile block headers in ``text``: comment-stripped lines that open a profile.

    A header is a line that, after dropping ``#``-to-end-of-line and surrounding
    whitespace, ends with ``{`` and starts with ``profile <name>``, a bare
    ``/path`` or an ``@{var}``. ``include`` / ``abi`` / rule lines and ``}``
    lines are not headers. The Ubuntu 24.04 stubs this module targets use
    ONE-LINE headers (``profile podman /usr/bin/podman flags=(unconfined) {``);
    a header whose ``{`` sits on a following line is deliberately not recognised
    (the caller then skips the file rather than guessing).
    """
    headers: list[str] = []
    for raw in text.splitlines():
        line = raw.split("#", 1)[0].strip()
        if line.endswith("{") and _PROFILE_HEADER_START_RE.match(line):
            headers.append(line)
    return headers


def _unconfined_stub_verdict(text: str) -> tuple[bool, str | None]:
    """``(selected, reason)`` for one profile file's text.

    Selected only when at least one profile header exists and EVERY header
    carries ``flags=(unconfined)``: disabling acts on the whole file, so a file
    that also holds an enforced profile must be left alone. ``reason`` explains
    a skip worth warning about (the file does carry an uncommented
    ``flags=(unconfined)`` token); it is ``None`` for a selected file and for a
    plain enforced profile, which is the normal case and not worth a warning.
    """
    if not any(_UNCONFINED_FLAG_TOKEN in raw.split("#", 1)[0] for raw in text.splitlines()):
        return False, None
    headers = _profile_headers(text)
    if not headers:
        return False, "no one-line profile header found (the '{' must end the header line)"
    unflagged = [h for h in headers if _UNCONFINED_FLAG_TOKEN not in h]
    if unflagged:
        return False, (
            f"profile header without {_UNCONFINED_FLAG_TOKEN}: '{unflagged[0]}' "
            "(disabling the file would unload that profile too)"
        )
    return True, None


def guest_unconfined_profiles_present(apparmor_d: Path) -> list[str]:
    """Names from :data:`GUEST_UNCONFINED_PROFILES` still marked ``flags=(unconfined)``.

    A profile counts only if its file exists under ``apparmor_d`` and EVERY
    profile block header in it (comments stripped; ``#`` starts a comment to end
    of line, so a ``# was flags=(unconfined)`` note does not count) carries
    ``flags=(unconfined)`` — see :func:`_unconfined_stub_verdict`. The disable
    symlink and ``apparmor_parser -R`` act on the WHOLE file, so a customised
    file that holds an enforced profile next to an unconfined one is skipped
    (logged at warning with the reason) rather than losing the enforced one.
    Order follows ``GUEST_UNCONFINED_PROFILES``.
    """
    present: list[str] = []
    for name in GUEST_UNCONFINED_PROFILES:
        try:
            text = (apparmor_d / name).read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue
        selected, reason = _unconfined_stub_verdict(text)
        if selected:
            present.append(name)
        elif reason is not None:
            log.warning("containers_apparmor.profile_skipped", profile=name, reason=reason)
    return present


def _disable_guest_unconfined_profiles(
    apparmor_d: Path, run: Callable[..., Any]
) -> tuple[list[str], list[str]]:
    """Disable every present guest unconfined profile; return ``(names, unload_failed)``.

    Per profile: ``<apparmor_d>/disable/<name>`` becomes a symlink to
    ``<apparmor_d>/<name>`` (an existing link or file is replaced), then
    ``apparmor_parser -R <profile>`` unloads it. A non-zero parser exit — or a
    missing ``apparmor_parser`` — is logged at warning, recorded in the second
    list and otherwise ignored: the symlink already makes the disable persistent
    across reboots, and the caller re-smokes. The link is created BEFORE the
    unload, so ``names`` alone does not prove the profile is no longer loaded;
    ``unload_failed`` is the subset that may still be. Idempotent.
    """
    names = guest_unconfined_profiles_present(apparmor_d)
    if not names:
        return [], []
    disable_dir = apparmor_d / "disable"
    disable_dir.mkdir(parents=True, exist_ok=True)
    unload_failed: list[str] = []
    for name in names:
        link = disable_dir / name
        if link.is_symlink() or link.exists():
            link.unlink()
        os.symlink(apparmor_d / name, link)
        try:
            proc = run(  # nosec B603 — fixed argv from this module
                ["apparmor_parser", "-R", str(apparmor_d / name)],
                capture_output=True,
                text=True,
            )
        except FileNotFoundError:
            log.warning("containers_apparmor.parser_missing", profile=name)
            unload_failed.append(name)
            continue
        if proc.returncode != 0:
            log.warning(
                "containers_apparmor.parser_remove_failed",
                profile=name,
                rc=proc.returncode,
                err=(proc.stderr or proc.stdout or "").strip()[:200],
            )
            unload_failed.append(name)
        else:
            log.info("containers_apparmor.disabled_guest_profile", profile=name)
    return names, unload_failed


def disable_guest_unconfined_profiles(apparmor_d: Path, run: Callable[..., Any]) -> list[str]:
    """Disable every present guest unconfined profile; return the names handled.

    See :func:`_disable_guest_unconfined_profiles` (which also reports the
    profiles whose unload failed). Idempotent.
    """
    return _disable_guest_unconfined_profiles(apparmor_d, run)[0]


def _apparmor_already_unconfined(conf_path: Path) -> bool:
    """True iff ``containers.apparmor_profile`` is already ``"unconfined"``."""
    try:
        data = tomllib.loads(conf_path.read_text(encoding="utf-8"))
    except (OSError, tomllib.TOMLDecodeError):
        return False
    containers = data.get("containers")
    if not isinstance(containers, dict):
        return False
    return containers.get("apparmor_profile") == "unconfined"


#: Matches a TOML table header, tolerating an inline comment after the
#: closing bracket (e.g. ``[containers] # operator settings``). A naive
#: ``stripped.endswith("]")`` check misses that shape entirely, fails to
#: recognize an existing ``[containers]`` section, and appends a SECOND
#: ``[containers]`` header below — invalid TOML
#: (``Cannot declare ('containers',) twice``) that corrupts the host's
#: podman config.
_SECTION_HEADER_RE = re.compile(r"^\[([^\[\]]+)\]")


def _section_header(line: str) -> str | None:
    """Return the bracketed table name if ``line`` is a section header."""
    stripped = line.strip()
    if not stripped.startswith("["):
        return None
    m = _SECTION_HEADER_RE.match(stripped)
    return m.group(1).strip() if m else None


def _write_apparmor_unconfined(conf_path: Path) -> None:
    """Idempotently set ``[containers] apparmor_profile = "unconfined"``.

    Preserves any existing content: replaces an existing ``apparmor_profile``
    line inside ``[containers]``, inserts the key into an existing
    ``[containers]`` section, or appends a fresh section. Atomic (tmp+replace).
    """
    line = 'apparmor_profile = "unconfined"'
    conf_path.parent.mkdir(parents=True, exist_ok=True)

    if not conf_path.exists():
        body = f"# hal0-managed (halo150 R4) — unconfined-LXC apparmor fix.\n[containers]\n{line}\n"
        _atomic_write(conf_path, body)
        return

    existing = conf_path.read_text(encoding="utf-8").splitlines()
    out: list[str] = []
    in_containers = False
    inserted = False
    replaced = False
    for raw in existing:
        section = _section_header(raw)
        if section is not None:
            # Leaving a section: if we were in [containers] and never inserted,
            # add the key just before the next section header.
            if in_containers and not inserted and not replaced:
                out.append(line)
                inserted = True
            in_containers = section == "containers"
            out.append(raw)
            continue
        stripped = raw.strip()
        if in_containers and stripped.startswith("apparmor_profile"):
            out.append(line)
            replaced = True
            continue
        out.append(raw)
    if in_containers and not inserted and not replaced:
        out.append(line)
        inserted = True
    if not in_containers and not inserted and not replaced:
        # No [containers] section anywhere — append a fresh one.
        out.append("[containers]")
        out.append(line)

    _atomic_write(conf_path, "\n".join(out) + "\n")


def _atomic_write(path: Path, content: str) -> None:
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(content, encoding="utf-8")
    tmp.replace(path)


def _remediate_guest_unconfined_profiles(
    first: subprocess.CompletedProcess[str],
    *,
    run: Callable[..., Any],
    smoke_argv: tuple[str, ...],
    apparmor_d: Path | None,
) -> ApparmorPreflightResult:
    """#3234 branch of :func:`ensure_podman_apparmor_usable`."""
    # Guard BEFORE any mutation: only a nested AppArmor policy namespace (an
    # unprivileged LXC) makes these profiles the cause. On the root namespace
    # they are the userns grant, and disabling them would break rootless podman.
    if not _in_nested_apparmor_namespace():
        return ApparmorPreflightResult(
            outcome="unrelated",
            detail=(
                "socket denial seen but AppArmor policy namespace is the root namespace "
                f"(.ns_level={_apparmor_ns_level_raw()}); not the nested-profile case, "
                "nothing changed"
            ),
        )
    root = apparmor_d if apparmor_d is not None else apparmor_d_root()
    names, unload_failed = _disable_guest_unconfined_profiles(root, run)
    if not names:
        return ApparmorPreflightResult(
            outcome="unrelated",
            detail=(
                "socket denial seen, but no unconfined guest AppArmor profiles "
                f"({', '.join(GUEST_UNCONFINED_PROFILES)}) found under {root}: "
                + (first.stderr or first.stdout or "").strip()[:150]
            ),
        )
    retry = _smoke(run, smoke_argv)
    fixed = retry.returncode == 0
    parts: list[str] = []
    if not fixed:
        parts.append((retry.stderr or retry.stdout or "").strip()[:200])
    if unload_failed:
        parts.append(f"unload failed: {', '.join(unload_failed)}")
    return ApparmorPreflightResult(
        outcome="guest_profiles_disabled",
        detail="; ".join(p for p in parts if p) or None,
        fixed=fixed,
        disabled=names,
        unload_failed=unload_failed,
    )


def ensure_podman_apparmor_usable(
    *,
    run: Callable[..., Any] = subprocess.run,
    conf_path: Path = CONTAINERS_CONF,
    smoke_argv: tuple[str, ...] = _SMOKE_ARGV,
    apparmor_d: Path | None = None,
) -> ApparmorPreflightResult:
    """Detect the unconfined-LXC apparmor failure and converge the fix.

    1. Smoke ``podman run``. Passes → ``ok`` (untouched).
    2. Failure WITHOUT the apparmor signature → ``unrelated`` (untouched).
    3. Failure WITH the signature → write ``apparmor_profile = "unconfined"``
       (skip the write if already set → ``already``) and retry the smoke.
       Retry passes → ``fixed`` / ``already``; still fails → ``retry_failed``.

    4. (#3234) Failure with ``: socket: permission denied`` and NOT the #1563
       signature → only when ``.ns_level`` (``HAL0_APPARMOR_NS_LEVEL_FILE`` /
       ``/sys/kernel/security/apparmor/.ns_level``) is an integer > 0, disable
       the guest's ``flags=(unconfined)`` podman-family AppArmor profiles under
       ``apparmor_d`` (default ``HAL0_APPARMOR_D`` / ``/etc/apparmor.d``) and
       retry once → ``guest_profiles_disabled`` (``fixed`` = retry passed;
       ``unload_failed`` = profiles whose ``apparmor_parser -R`` failed). A file
       is only disabled when EVERY profile header in it is ``flags=(unconfined)``.
       A root AppArmor namespace or no such profile present → ``unrelated``,
       nothing changed, no retry.

    Idempotent: a second call on a converged box re-smokes, passes, and returns
    ``ok`` without touching the config.
    """
    try:
        first = _smoke(run, smoke_argv)
    except FileNotFoundError:
        return ApparmorPreflightResult(outcome="no_podman", detail="podman not on PATH")

    if first.returncode == 0:
        return ApparmorPreflightResult(outcome="ok")

    if not _is_apparmor_failure(first):
        if _is_unconfined_profile_network_denial(first):
            return _remediate_guest_unconfined_profiles(
                first, run=run, smoke_argv=smoke_argv, apparmor_d=apparmor_d
            )
        return ApparmorPreflightResult(
            outcome="unrelated",
            detail=(first.stderr or first.stdout or "").strip()[:200],
        )

    already = _apparmor_already_unconfined(conf_path)
    wrote = False
    if not already:
        _write_apparmor_unconfined(conf_path)
        wrote = True
        log.info("containers_apparmor.wrote_unconfined", conf=str(conf_path))

    retry = _smoke(run, smoke_argv)
    if retry.returncode == 0:
        return ApparmorPreflightResult(outcome="fixed" if wrote else "already", wrote_config=wrote)
    return ApparmorPreflightResult(
        outcome="retry_failed",
        wrote_config=wrote,
        detail=(retry.stderr or retry.stdout or "").strip()[:200],
    )


__all__ = [
    "CONTAINERS_CONF",
    "GUEST_UNCONFINED_PROFILES",
    "ApparmorPreflightResult",
    "apparmor_d_root",
    "apparmor_ns_level_file",
    "disable_guest_unconfined_profiles",
    "ensure_podman_apparmor_usable",
    "guest_unconfined_profiles_present",
]


def _main() -> int:
    """CLI entry — run both as ``python -m hal0.agents.containers_apparmor``
    (install.sh's post-install re-check) and as a bare script
    (``python containers_apparmor.py``, install.sh's early
    ``_container_runtime_gate`` remediation, #1563).

    Runs the convergent preflight and prints ``<outcome> wrote=<bool>``. Exit 0
    unless the retry still failed after writing the fix (``retry_failed`` → 1),
    so the installer can surface a genuine, unresolved apparmor blocker.

    ``HAL0_APPARMOR_CONF`` overrides the target config path — used by tests to
    point this at a throwaway file instead of the real
    ``/etc/containers/containers.conf``. ``HAL0_APPARMOR_D`` and
    ``HAL0_APPARMOR_NS_LEVEL_FILE`` likewise override the AppArmor profile dir
    (default ``/etc/apparmor.d``) and the policy-namespace level file for the
    #3234 guest-profile remediation. ``HAL0_CONTAINER_SMOKE_IMAGE`` (the
    same var installer/lib/preflight.sh's own smoke test honours) overrides
    the probe image, so an air-gapped/mirrored-registry install probes with
    an image it can actually reach instead of a hardcoded quay.io one.
    """
    conf_path = Path(os.environ.get("HAL0_APPARMOR_CONF", str(CONTAINERS_CONF)))
    result = ensure_podman_apparmor_usable(conf_path=conf_path, smoke_argv=_default_smoke_argv())
    print(f"apparmor-preflight: {result.outcome} wrote={result.wrote_config}")
    if result.detail:
        print(f"apparmor-preflight-detail: {result.detail}")
    if result.outcome == "guest_profiles_disabled":
        verdict = "retry passed" if result.fixed else "retry still failing"
        root = apparmor_d_root()
        revert = "; ".join(
            f"rm {root}/disable/{n} && apparmor_parser -r {root}/{n}" for n in result.disabled
        )
        unload = (
            f"; unload failed: {', '.join(result.unload_failed)}" if result.unload_failed else ""
        )
        # ASCII only: this prints before a UTF-8 stdout is guaranteed.
        print(
            f"apparmor remediation applied: disabled guest profiles {', '.join(result.disabled)}"
            f" - {verdict}{unload}; revert: {revert}"
        )
    return 1 if result.outcome == "retry_failed" else 0


if __name__ == "__main__":
    import sys

    sys.exit(_main())
