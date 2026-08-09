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
"""

from __future__ import annotations

import os
import re
import subprocess  # nosec B404 — smoke-tests the local podman
import tomllib
from collections.abc import Callable
from dataclasses import dataclass
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
    """

    outcome: str
    wrote_config: bool = False
    detail: str | None = None


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


def ensure_podman_apparmor_usable(
    *,
    run: Callable[..., Any] = subprocess.run,
    conf_path: Path = CONTAINERS_CONF,
    smoke_argv: tuple[str, ...] = _SMOKE_ARGV,
) -> ApparmorPreflightResult:
    """Detect the unconfined-LXC apparmor failure and converge the fix.

    1. Smoke ``podman run``. Passes → ``ok`` (untouched).
    2. Failure WITHOUT the apparmor signature → ``unrelated`` (untouched).
    3. Failure WITH the signature → write ``apparmor_profile = "unconfined"``
       (skip the write if already set → ``already``) and retry the smoke.
       Retry passes → ``fixed`` / ``already``; still fails → ``retry_failed``.

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
    "ApparmorPreflightResult",
    "ensure_podman_apparmor_usable",
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
    ``/etc/containers/containers.conf``. ``HAL0_CONTAINER_SMOKE_IMAGE`` (the
    same var installer/lib/preflight.sh's own smoke test honours) overrides
    the probe image, so an air-gapped/mirrored-registry install probes with
    an image it can actually reach instead of a hardcoded quay.io one.
    """
    conf_path = Path(os.environ.get("HAL0_APPARMOR_CONF", str(CONTAINERS_CONF)))
    result = ensure_podman_apparmor_usable(conf_path=conf_path, smoke_argv=_default_smoke_argv())
    print(f"apparmor-preflight: {result.outcome} wrote={result.wrote_config}")
    if result.detail:
        print(f"apparmor-preflight-detail: {result.detail}")
    return 1 if result.outcome == "retry_failed" else 0


if __name__ == "__main__":
    import sys

    sys.exit(_main())
