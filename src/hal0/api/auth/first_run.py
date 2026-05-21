"""First-run OTP lockfile (FINDINGS §28).

Closes the LAN race where any peer on the local network can beat the
legitimate operator to ``POST /api/auth/password`` on a fresh install and
claim ownership of the box.

The mitigation is a small lockfile written on API startup whenever no
owner password is configured yet. The file lives at
``<state_dir>/.first-run.lock`` (``/var/lib/hal0/.first-run.lock`` in
production, ``$HAL0_HOME/var-lib/hal0/.first-run.lock`` in dev/tests).

It contains a JSON document with:

  - ``otp``         — a 32-char URL-safe token (240 bits of entropy).
  - ``created_at``  — UNIX timestamp; informational only.

Permissions are tightened to ``0600`` so only ``root`` (which owns the
hal0-api service) can read the token. Operators retrieve the token from
the installer transcript (which prints it post-install) or from
``journalctl -u hal0-api`` on the host. They paste it into the wizard,
the route validates it against the lockfile, and the file is unlinked
on a successful set.

Routes accept the OTP in two equivalent ways:

  1. The legacy first-run-no-auth path now requires EITHER the OTP in
     the request body (``{"password": "...", "otp": "..."}``), in an
     ``X-Hal0-First-Run-OTP`` header, OR a request originating from
     ``127.0.0.1`` (loopback). The loopback bypass preserves the
     ``curl http://localhost:8080/api/auth/password -d '{...}'`` UX for
     operators on the host machine. The OTP path preserves the wizard
     UX for operators driving the dashboard from a browser.
  2. Once the password is set, the lockfile is removed and the route
     reverts to writer-scope-required (existing behaviour).
"""

from __future__ import annotations

import json
import os
import secrets
import stat
import time
from dataclasses import dataclass
from pathlib import Path

import structlog

from hal0.config import paths as hal0_paths

log = structlog.get_logger(__name__)


# 32 chars of URL-safe base64 ≈ 192 bits. The exact length is part of
# the installer banner format; operators copy-paste it from the
# transcript so we keep it deterministic. Generated via
# ``secrets.token_urlsafe(24)`` (24 bytes → 32 chars).
_OTP_BYTES: int = 24

# Lockfile permissions: owner-only read/write. The hal0-api service
# runs as root in production (per installer), so the operator reads the
# file from a sudo'd shell or from the systemd journal where install.sh
# echoes it.
_LOCKFILE_MODE: int = 0o600


@dataclass(frozen=True)
class FirstRunLock:
    """In-memory mirror of the lockfile contents."""

    path: Path
    otp: str
    created_at: int


def lockfile_path() -> Path:
    """Return the canonical lockfile path under the state dir.

    Uses :func:`hal0.config.paths.var_lib` so HAL0_HOME-rooted dev
    installs and integration tests get a tmp-scoped path automatically.
    """
    return hal0_paths.var_lib() / ".first-run.lock"


def mint_lockfile(*, path: Path | None = None) -> FirstRunLock:
    """Generate a new OTP and write it to the lockfile.

    The parent directory is created with ``parents=True`` so a fresh
    install (where ``/var/lib/hal0`` may not yet exist) doesn't crash
    on startup. Idempotent on retry: if the file already exists and is
    readable, the existing OTP is preserved — we don't want to rotate
    the token mid-install just because the API restarted before the
    operator finished the wizard.
    """
    target = path or lockfile_path()
    existing = read_lockfile(path=target)
    if existing is not None:
        log.info("auth.first_run_lock.reused", path=str(target))
        return existing

    target.parent.mkdir(parents=True, exist_ok=True)
    otp = secrets.token_urlsafe(_OTP_BYTES)
    payload = {"otp": otp, "created_at": int(time.time())}
    # Write through a tmp file so a crashed mid-write doesn't leave a
    # truncated lockfile that read_lockfile() would treat as malformed
    # and overwrite on next boot.
    tmp = target.with_suffix(target.suffix + ".tmp")
    tmp.write_text(json.dumps(payload), encoding="utf-8")
    os.chmod(tmp, _LOCKFILE_MODE)
    os.replace(tmp, target)
    log.info("auth.first_run_lock.minted", path=str(target))
    return FirstRunLock(path=target, otp=otp, created_at=payload["created_at"])


def read_lockfile(*, path: Path | None = None) -> FirstRunLock | None:
    """Read the current lockfile, returning None when absent/malformed.

    A malformed file (truncated, non-JSON, missing fields) is treated
    as "no lock present" so a corrupt file doesn't permanently lock
    out the wizard — the next ``mint_lockfile`` call will replace it.
    """
    target = path or lockfile_path()
    try:
        raw = target.read_text(encoding="utf-8")
    except FileNotFoundError:
        return None
    except OSError as exc:
        # Permission denied / IO error: log and treat as "no lock".
        log.warning("auth.first_run_lock.read_failed", path=str(target), error=str(exc))
        return None

    try:
        payload = json.loads(raw)
        otp = str(payload["otp"])
        created_at = int(payload.get("created_at", 0))
    except (ValueError, KeyError, TypeError) as exc:
        log.warning("auth.first_run_lock.malformed", path=str(target), error=str(exc))
        return None
    if not otp:
        return None
    return FirstRunLock(path=target, otp=otp, created_at=created_at)


def consume_lockfile(*, path: Path | None = None) -> None:
    """Unlink the lockfile after a successful first-run set_password.

    Absent file is a no-op — once the password is set, the lockfile is
    no longer interesting and we don't want a "file disappeared between
    read and unlink" race to surface as an error to the operator.
    """
    target = path or lockfile_path()
    try:
        target.unlink()
    except FileNotFoundError:
        return
    except OSError as exc:
        log.warning("auth.first_run_lock.unlink_failed", path=str(target), error=str(exc))
        return
    log.info("auth.first_run_lock.consumed", path=str(target))


def verify_lockfile_mode(*, path: Path | None = None) -> bool:
    """Sanity check: confirm the lockfile is 0600-or-stricter.

    Used by tests and as a defensive check inside the route — if the
    permissions are wrong the operator should be told (the OTP is
    burned, mint a fresh lockfile).
    """
    target = path or lockfile_path()
    try:
        mode = stat.S_IMODE(target.stat().st_mode)
    except OSError:
        return False
    # Allow exactly 0600; refuse anything broader (group/other readable
    # or executable bits set).
    return mode == _LOCKFILE_MODE


__all__ = [
    "FirstRunLock",
    "consume_lockfile",
    "lockfile_path",
    "mint_lockfile",
    "read_lockfile",
    "verify_lockfile_mode",
]
