"""UpdateSeam — the one narrow privileged seam self-update needs post-flip (#1464).

P3-perms flipped ``hal0-api.service`` to ``User=hal0`` and made
:mod:`hal0.install.perms` the ownership authority, where ``/usr/lib/hal0`` is
pinned ``root:root 0755`` and declared "never service-writable at any point".
The updater, however, kept writing that tree in-process: extract to
``<usr_lib>/hal0-<version>/``, swap the ``<usr_lib>/current`` symlink, and
``pip install --force-reinstall`` into the root-owned venv. On a shipped v1.0
box every one of those is ``EACCES``, so self-update was structurally
impossible — and the failure only landed *after* a full download, sha256 and
cosign pass, surfacing as a raw ``Permission denied``.

This module closes that the same way slot lifecycle was closed: a narrow,
argument-validated sudo wrapper (``installer/wrappers/hal0-update`` +
``packaging/sudoers/hal0-update``), fronted by an injectable Python seam that
mirrors :class:`hal0.system.seam.SystemCtlSeam` verb for verb.

**Why the whole staging phase runs root-side.** The obvious smaller grant —
let the unprivileged API download and verify, then ask root to install the
result — is not smaller at all, it is a root-code-execution hole: ``activate``
ends in ``pip install``, which runs the tree's build backend as root, so a
compromised hal0-api would simply skip verification and hand root an
attacker-supplied tarball. Instead root re-fetches the manifest, re-derives the
target version, re-checks the digest and re-runs ``cosign verify-blob`` itself.
The only things that cross the boundary are a channel name from a three-value
allow-list, an optional exact-match version pin, and a ``hal0-<version>``
directory *basename* — never a path, never a file body.

**What deliberately stays unprivileged.** Config migrations, seed-profile
pruning, the mtp / extra-args / image sweeps, the ``hal0.previous`` breadcrumb
and the slot-unit re-render all write ``/etc/hal0``, ``/var/lib/hal0`` or go
through the existing ``hal0-systemctl`` seam — surfaces the ``hal0`` service
account already owns. Running them as root would silently re-own hal0's own
config and SQLite files and break the next unprivileged write.

Gating mirrors :class:`~hal0.system.seam.SystemCtlSeam` exactly: **never** a
bare "not root" check. The seam engages only when the current process runs as
the literal ``hal0`` service account (resolved by name via :mod:`pwd`) — true
for the real ``hal0-api`` process on a provisioned box, never for a dev shell,
a CI runner, or a unit test, none of which have the grant installed. There the
seam is a permanent passthrough and every primitive runs in-process, identical
to pre-#1464 behaviour.

``run``/``is_hal0_user`` are injected seams (default-constructed = production
behaviour) so all of this is unit-testable without sudo, a real ``hal0`` user,
or a privileged filesystem.
"""

from __future__ import annotations

import asyncio
import json
import os
import subprocess
import sys
from collections.abc import Callable
from pathlib import Path
from typing import Any

import structlog

from hal0.system.seam import is_hal0_service_user
from hal0.updater.updater import (
    UpdateError,
    UpdatePrivilegeError,
    _require_release_version,
    _usr_lib_root,
    activate_release,
    assert_release_dir_name,
    assert_trusted_release_dir,
    discard_release,
    stage_release,
)

log = structlog.get_logger(__name__)

#: The wrapper's installed path (``installer/wrappers/hal0-update`` ->
#: ``${LIB_DIR}/bin/hal0-update`` at install time).
SEAM_BIN = "/usr/lib/hal0/bin/hal0-update"

#: The sudoers drop-in that grants ``hal0`` NOPASSWD on :data:`SEAM_BIN`.
SEAM_SUDOERS = "/etc/sudoers.d/hal0-update"

#: Release channels the seam forwards. Mirrors ``validate_channel`` in
#: ``installer/wrappers/hal0-update`` EXACTLY — the wrapper re-validates
#: server-side (as root), this copy is the fail-fast convenience.
CHANNELS = frozenset({"stable", "preview", "nightly"})

#: Sentinel key on the single stdout line the root helper emits. Structured
#: logs go to STDERR precisely so stdout carries nothing but this envelope.
_RESULT_KEY = "hal0_update_result"

#: Bound the root helper. ``stage`` downloads a release tarball and runs cosign;
#: ``activate`` runs a full ``pip install``. Generous, but never unbounded — a
#: wedged child must not pin an update job open forever.
_STAGE_TIMEOUT = 1800.0
_ACTIVATE_TIMEOUT = 1800.0
_SHORT_TIMEOUT = 60.0


def _remediation(detail: str) -> UpdatePrivilegeError:
    """Build the one actionable privilege error, with the fix spelled out."""
    return UpdatePrivilegeError(
        f"self-update cannot reach the root-owned install tree: {detail}",
        details={
            "install_root": str(_usr_lib_root()),
            "seam_bin": SEAM_BIN,
            "sudoers": SEAM_SUDOERS,
            "hint": (
                "re-run the installer (`sudo bash install.sh`) to (re)install the "
                f"update seam, then confirm with `hal0 doctor all` — it reports the "
                f"privileged-seam row. To check by hand: `sudo -n {SEAM_BIN} check`."
            ),
        },
    )


class UpdateSeam:
    """Direct filesystem ops when not the hal0 service user; the ``hal0-update``
    sudo seam when running as it.

    Every method mirrors a module-level primitive in
    :mod:`hal0.updater.updater` (:func:`~hal0.updater.updater.stage_release`,
    :func:`~hal0.updater.updater.activate_release`,
    :func:`~hal0.updater.updater.discard_release`) — this class just adds the
    service-account-gated routing in front of each, so :class:`Updater` doesn't
    need to know which mode it is in.
    """

    def __init__(
        self,
        *,
        run: Callable[..., subprocess.CompletedProcess[str]] = subprocess.run,
        is_hal0_user: Callable[[], bool] = is_hal0_service_user,
        seam_bin: str = SEAM_BIN,
        job_id: str | None = None,
    ) -> None:
        self._run = run
        self._is_hal0_user = is_hal0_user
        self._seam_bin = seam_bin
        self._job_id = job_id

    # ── routing ────────────────────────────────────────────────────────────────

    @property
    def routed(self) -> bool:
        """True when this process must go through the sudo seam."""
        return bool(self._is_hal0_user())

    def _seam_argv(self, *parts: str) -> list[str]:
        return ["sudo", "-n", self._seam_bin, *parts]

    def _invoke(self, *parts: str, timeout: float) -> dict[str, Any]:
        """Run one seam verb as root and return its parsed result envelope.

        ``-n`` is load-bearing: a missing or broken grant fails immediately with
        a non-zero exit instead of prompting for a password inside a daemon.
        The child's stderr (its structured log) is folded into the raised error
        so a failure is diagnosable from the job result alone.
        """
        try:
            proc = self._run(  # nosec B603 — fixed argv; every arg re-validated as root
                self._seam_argv(*parts),
                capture_output=True,
                text=True,
                check=False,
                timeout=timeout,
            )
        except subprocess.TimeoutExpired as exc:
            raise UpdateError(
                f"privileged update seam timed out after {timeout:.0f}s ({parts[0]})",
                details={"verb": parts[0], "timeout": timeout},
            ) from exc
        except OSError as exc:
            raise _remediation(f"could not execute `sudo -n {self._seam_bin}`: {exc}") from exc

        stderr = (proc.stderr or "")[-4000:]
        if proc.returncode != 0:
            raise UpdateError(
                f"privileged update seam failed: {parts[0]} (rc={proc.returncode})",
                details={
                    "verb": parts[0],
                    "returncode": proc.returncode,
                    "stderr": stderr,
                    "seam_bin": self._seam_bin,
                },
            )
        return _parse_result(proc.stdout or "", verb=parts[0], stderr=stderr)

    # ── preflight ──────────────────────────────────────────────────────────────

    def assert_privileges(self) -> None:
        """Fail fast when neither privilege route is usable (#1464).

        Called at the top of ``prepare``/``commit``/``rollback`` so an operator
        learns the seam is missing *before* a multi-hundred-megabyte download,
        and with a remediation instead of a bare ``EACCES`` traceback.

        * routed (we are the ``hal0`` service account) — the grant must exist
          and actually work: ``sudo -n <seam> check`` has to exit 0.
        * not routed (root, a dev box, CI, ``HAL0_HOME``) — the install root
          and the venv must be directly writable.
        """
        if self.routed:
            if not Path(self._seam_bin).exists():
                raise _remediation(f"{self._seam_bin} is not installed")
            try:
                proc = self._run(  # nosec B603 — fixed argv, no caller input
                    self._seam_argv("check"),
                    capture_output=True,
                    text=True,
                    check=False,
                    timeout=_SHORT_TIMEOUT,
                )
            except (OSError, subprocess.SubprocessError) as exc:
                raise _remediation(
                    f"`sudo -n {self._seam_bin} check` could not run: {exc}"
                ) from exc
            if proc.returncode != 0:
                raise _remediation(
                    f"`sudo -n {self._seam_bin} check` exited {proc.returncode} — the "
                    f"{SEAM_SUDOERS} grant is missing or does not apply "
                    f"({(proc.stderr or '').strip()[:300]})"
                )
            return

        # The install root itself may not exist yet (a first staging run, or a
        # HAL0_HOME dev tree), so probe the deepest EXISTING ancestor — that is
        # the directory we would actually have to create it in.
        probe = _usr_lib_root()
        while not probe.exists() and probe.parent != probe:
            probe = probe.parent
        if not os.access(probe, os.W_OK | os.X_OK):
            raise _remediation(
                f"{probe} is not writable by uid {os.geteuid()} and this process is not "
                "the hal0 service account, so the sudo seam does not apply — "
                "run the update as root (`sudo hal0 update apply`)"
            )

    # ── primitives ─────────────────────────────────────────────────────────────

    async def stage(self, channel: str, version: str | None = None) -> dict[str, Any]:
        """Download, authenticate, verify and extract a release (§9 steps 1-6)."""
        chan = _validate_channel(channel)
        pinned = _validate_optional_version(version)
        if not self.routed:
            return await stage_release(chan, pinned, job_id=self._job_id)
        args = ["stage", chan] + ([pinned] if pinned else [])
        return await asyncio.to_thread(self._invoke, *args, timeout=_STAGE_TIMEOUT)

    async def activate(self, dir_name: str) -> dict[str, Any]:
        """Swap ``current`` to ``<usr_lib>/<dir_name>`` and re-pip it (§9 8+8b)."""
        name = _validate_dir_name(dir_name)
        if not self.routed:
            return await asyncio.to_thread(activate_release, name, job_id=self._job_id)
        return await asyncio.to_thread(self._invoke, "activate", name, timeout=_ACTIVATE_TIMEOUT)

    def discard(self, dir_name: str) -> None:
        """Remove a staged ``<usr_lib>/<dir_name>`` tree (idempotent)."""
        name = _validate_dir_name(dir_name)
        if not self.routed:
            discard_release(name, job_id=self._job_id)
            return
        self._invoke("discard", name, timeout=_SHORT_TIMEOUT)


# ── argument validation (client-side fail-fast; root re-validates) ─────────────


def _validate_channel(channel: str) -> str:
    if channel not in CHANNELS:
        raise UpdateError(
            f"unsupported release channel: {channel!r}",
            details={"channel": channel, "supported": sorted(CHANNELS)},
        )
    return channel


def _validate_optional_version(version: str | None) -> str | None:
    """Validate an optional exact version pin.

    Delegates to the updater's own ``_require_release_version`` so a bad pin
    keeps raising the typed ``UpdateManifestInvalid`` (HTTP 400) it always did
    — the seam adds a privilege boundary, not a new error taxonomy.
    """
    if version is None:
        return None
    return _require_release_version(version, field="requested_version")


def _validate_dir_name(dir_name: str) -> str:
    try:
        return assert_release_dir_name(dir_name)
    except ValueError as exc:
        raise UpdateError(str(exc), details={"dir_name": dir_name}) from exc


def _parse_result(stdout: str, *, verb: str, stderr: str = "") -> dict[str, Any]:
    """Extract the result envelope from the root helper's stdout.

    Scans from the END so an unexpected banner (a pip warning that escaped to
    stdout, a motd) cannot displace the answer. Anything that isn't a
    :data:`_RESULT_KEY` envelope is ignored.
    """
    for line in reversed(stdout.splitlines()):
        line = line.strip()
        if not line.startswith("{"):
            continue
        try:
            payload = json.loads(line)
        except ValueError:
            continue
        if isinstance(payload, dict) and _RESULT_KEY in payload:
            result = payload[_RESULT_KEY]
            return result if isinstance(result, dict) else {}
    raise UpdateError(
        f"privileged update seam returned no result envelope for {verb}",
        details={"verb": verb, "stdout": stdout[-2000:], "stderr": stderr},
    )


# ── root-side entry point (`python -m hal0.updater.privileged`) ────────────────


def _emit(result: dict[str, Any] | None) -> None:
    """Write the single result line to stdout."""
    sys.stdout.write(json.dumps({_RESULT_KEY: result or {}}) + "\n")
    sys.stdout.flush()


def _pin_logs_to_stderr() -> None:
    """Send structured logs to stderr so stdout carries only the result line.

    Called from the ``__main__`` guard, never from :func:`main` — reconfiguring
    structlog is process-global, and a unit test calling ``main()`` directly
    must not leave every later test logging into a closed capture buffer.
    """
    structlog.configure(logger_factory=structlog.PrintLoggerFactory(file=sys.stderr))


def main(argv: list[str] | None = None) -> int:
    """Run one privileged verb as root and print its result envelope.

    Invoked ONLY by ``installer/wrappers/hal0-update`` (which is what the
    sudoers grant is pinned to). Every argument is re-validated here, as root,
    because the wrapper's own regexes are a convenience and not the security
    boundary.

    Structured logs are pinned to **stderr** (see :func:`_pin_logs_to_stderr`)
    so stdout carries exactly one line — the result envelope the unprivileged
    parent parses.
    """
    args = list(sys.argv[1:] if argv is None else argv)
    if not args:
        print(
            "usage: python -m hal0.updater.privileged <check|stage|activate|discard>",
            file=sys.stderr,
        )
        return 64
    verb, rest = args[0], args[1:]

    try:
        if verb == "check":
            # Non-mutating liveness probe: proves the grant resolves and the
            # helper can import hal0. `hal0 doctor` and the updater preflight
            # both lean on this.
            _emit({"ok": True, "install_root": str(_usr_lib_root())})
            return 0
        if verb == "stage":
            if not rest or len(rest) > 2:
                raise UpdateError("stage takes <channel> [version]", details={"argv": rest})
            channel = _validate_channel(rest[0])
            version = _validate_optional_version(rest[1]) if len(rest) == 2 else None
            _emit(asyncio.run(stage_release(channel, version)))
            return 0
        if verb == "activate":
            if len(rest) != 1:
                raise UpdateError("activate takes exactly <dir-name>", details={"argv": rest})
            name = _validate_dir_name(rest[0])
            # THE root trust boundary: activate ends in `pip install`, which runs
            # the tree's build backend as root. A tree the unprivileged caller
            # could have written must never reach it.
            assert_trusted_release_dir(_usr_lib_root() / name)
            _emit(activate_release(name))
            return 0
        if verb == "discard":
            if len(rest) != 1:
                raise UpdateError("discard takes exactly <dir-name>", details={"argv": rest})
            discard_release(_validate_dir_name(rest[0]))
            _emit({"ok": True})
            return 0
    except UpdateError as exc:
        log.error("updater.privileged_failed", verb=verb, error=exc.message, details=exc.details)
        print(f"hal0-update: {exc.message}", file=sys.stderr)
        return 1
    except Exception as exc:  # the root boundary logs a line, never a traceback
        log.error("updater.privileged_crashed", verb=verb, error=str(exc))
        print(f"hal0-update: {verb} failed: {exc}", file=sys.stderr)
        return 1

    print(f"hal0-update: unknown verb: {verb}", file=sys.stderr)
    return 64


__all__ = [
    "CHANNELS",
    "SEAM_BIN",
    "SEAM_SUDOERS",
    "UpdateSeam",
    "main",
]


if __name__ == "__main__":  # pragma: no cover — exercised via the wrapper
    _pin_logs_to_stderr()
    raise SystemExit(main())
