"""SystemCtlSeam — the one narrow privileged seam hal0-api needs post-flip.

P3-perms flips ``hal0-api.service`` to ``User=hal0`` (``installer/install.sh``)
and makes :mod:`hal0.install.perms` the declarative ownership authority. That
leaves a handful of genuinely-root operations the (now unprivileged) API still
performs for slot lifecycle: writing a per-slot systemd unit under
``/etc/systemd/system`` (root:root, per the ``OwnershipStore`` table),
``daemon-reload``, and starting/stopping/restarting/enabling/disabling a slot
unit. Those route through ``sudo -n /usr/lib/hal0/bin/hal0-systemctl``
(``installer/wrappers/hal0-systemctl`` + ``packaging/sudoers/hal0-systemctl``)
— the entire privileged surface, argument-validated, no shell, no wildcards.

Gating: **never** a bare "not root" check. A dev shell, a CI runner, or any
test process is almost always non-root too, but none of those have the
``hal0-systemctl`` seam installed — a bare ``os.geteuid() != 0`` gate would
make every such process try (and fail) to shell out to a sudo grant that
doesn't exist there. Instead this seam checks whether the CURRENT process is
running as the literal ``hal0`` **service account** (resolved by name via
:mod:`pwd`, mirroring :mod:`hal0.agents.hermes_provision`'s own
``_resolve_user_ids`` seam) — true only for the real ``hal0-api`` /
``hal0-agent@*`` processes on a provisioned box, never for a bare dev/test
euid. When the ``hal0`` user doesn't exist at all (dev box, CI, unit tests),
this seam is permanently a passthrough: every op runs directly, identical to
pre-P3-perms behaviour — no test or dev workflow needs the sudo grant.

``run``/``is_hal0_user`` are injected seams (default-constructed = production
behaviour) so this is unit-testable without sudo, a real ``hal0`` user, or a
privileged filesystem — the same pattern as
:mod:`hal0.install.perms`'s ``chown``/``chmod`` injection and
:class:`hal0.agents.hermes_provision.PhaseIO`.
"""

from __future__ import annotations

import os
import pwd
import re
import subprocess
from collections.abc import Callable
from pathlib import Path

#: The wrapper's installed path (``installer/wrappers/hal0-systemctl`` ->
#: ``${LIB_DIR}/bin/hal0-systemctl`` at install time).
SEAM_BIN = "/usr/lib/hal0/bin/hal0-systemctl"

#: Verbs the seam validates + forwards for a ``hal0-slot@<id>.service`` unit.
_UNIT_VERBS = frozenset({"start", "stop", "restart", "enable", "disable", "reset-failed"})

_UNIT_NAME_RE = re.compile(r"^hal0-slot@([A-Za-z0-9_-]{1,64})\.service$")
#: P3-quadlet: the Podman Quadlet source file a slot's ``.service`` is generated
#: from — ``hal0-slot@<token>.container`` under ``/etc/containers/systemd/``.
#: Root-owned by design; written via ``hal0-systemctl write-quadlet <token>``.
_QUADLET_NAME_RE = re.compile(r"^hal0-slot@([A-Za-z0-9_-]{1,64})\.container$")


class Hal0SeamMissing(RuntimeError):
    """Raised when the hal0-systemctl seam is required but not installed.

    Distinct from a bare ``PermissionError``/``FileNotFoundError`` so a
    caller (``hal0 doctor``) can give a precise remediation instead of a raw
    traceback.
    """


def is_hal0_service_user() -> bool:
    """True only when THIS process's euid is literally the ``hal0`` service
    account's uid. ``False`` (never seam) when the ``hal0`` user doesn't
    exist on this box at all — the dev/CI/unit-test default."""
    try:
        hal0_uid = pwd.getpwnam("hal0").pw_uid
    except KeyError:
        return False
    return os.geteuid() == hal0_uid


def _slot_id_from_unit(unit_name: str) -> str | None:
    """Extract ``<id>`` from ``hal0-slot@<id>.service``, or ``None`` if the
    unit name isn't one (e.g. ``hal0-api.service`` — never routed here)."""
    m = _UNIT_NAME_RE.match(unit_name)
    return m.group(1) if m else None


def _slot_id_from_quadlet(file_name: str) -> str | None:
    """Extract ``<token>`` from ``hal0-slot@<token>.container``, or ``None``."""
    m = _QUADLET_NAME_RE.match(file_name)
    return m.group(1) if m else None


class SystemCtlSeam:
    """Direct systemd/file ops when not the hal0 service user; the
    ``hal0-systemctl`` sudo seam when running as it.

    Every public method mirrors what :class:`hal0.providers.container.ContainerProvider`
    already does directly (``unit_path.write_text``, ``unit_path.unlink``,
    ``subprocess.run(["systemctl", ...])``) — this class just adds the
    euid-gated seam routing in front of each, so callers don't need to know
    which mode they're in.
    """

    def __init__(
        self,
        *,
        run: Callable[..., subprocess.CompletedProcess[str]] = subprocess.run,
        is_hal0_user: Callable[[], bool] = is_hal0_service_user,
        seam_bin: str = SEAM_BIN,
    ) -> None:
        self._run = run
        self._is_hal0_user = is_hal0_user
        self._seam_bin = seam_bin

    def _seam_argv(self, *parts: str) -> list[str]:
        return ["sudo", "-n", self._seam_bin, *parts]

    def write_unit(self, unit_path: Path, unit_text: str) -> None:
        """Write a ``hal0-slot@<id>.service`` unit file."""
        if not self._is_hal0_user():
            unit_path.write_text(unit_text)
            return
        slot_id = _slot_id_from_unit(unit_path.name)
        if slot_id is None:
            raise ValueError(f"not a hal0-slot@ unit: {unit_path.name!r}")
        self._run(
            self._seam_argv("write-unit", slot_id),
            input=unit_text,
            text=True,
            check=True,
        )

    def remove_unit(self, unit_path: Path) -> None:
        """Delete a ``hal0-slot@<id>.service`` unit file (no-op if absent)."""
        if not self._is_hal0_user():
            unit_path.unlink(missing_ok=True)
            return
        slot_id = _slot_id_from_unit(unit_path.name)
        if slot_id is None:
            raise ValueError(f"not a hal0-slot@ unit: {unit_path.name!r}")
        # Mirrors the direct path's tolerance of "already gone" (missing_ok):
        # the wrapper's remove-unit is idempotent (rm -f semantics).
        self._run(self._seam_argv("remove-unit", slot_id), check=False)

    def write_quadlet(self, quadlet_path: Path, unit_text: str) -> None:
        """Write a ``hal0-slot@<token>.container`` Quadlet source file (P3-quadlet).

        The declarative Quadlet replacement for :meth:`write_unit`: root-owned by
        design under ``/etc/containers/systemd/``, so a hal0-service-user install
        routes the write through ``hal0-systemctl write-quadlet <token>`` (body on
        stdin). Dev/CI/test (not the hal0 user) writes directly, exactly as
        before P3-perms.
        """
        if not self._is_hal0_user():
            quadlet_path.parent.mkdir(parents=True, exist_ok=True)
            quadlet_path.write_text(unit_text)
            return
        token = _slot_id_from_quadlet(quadlet_path.name)
        if token is None:
            raise ValueError(f"not a hal0-slot@ quadlet file: {quadlet_path.name!r}")
        self._run(
            self._seam_argv("write-quadlet", token),
            input=unit_text,
            text=True,
            check=True,
        )

    def remove_quadlet(self, quadlet_path: Path) -> None:
        """Delete a ``hal0-slot@<token>.container`` Quadlet file (no-op if absent)."""
        if not self._is_hal0_user():
            quadlet_path.unlink(missing_ok=True)
            return
        token = _slot_id_from_quadlet(quadlet_path.name)
        if token is None:
            raise ValueError(f"not a hal0-slot@ quadlet file: {quadlet_path.name!r}")
        self._run(self._seam_argv("remove-quadlet", token), check=False)

    def systemctl(self, *args: str, check: bool = True) -> subprocess.CompletedProcess[str]:
        """Run ``systemctl <args...>``, routing daemon-reload + hal0-slot@
        unit verbs through the seam when running as the hal0 service user.

        Read-only queries (``is-active``, ``status``, ...) and anything
        touching a NON-``hal0-slot@`` unit (e.g. ``hal0-api.service`` itself —
        use :meth:`restart_self` for that) always pass straight through:
        neither needs the seam, and the seam wrapper doesn't accept them.
        """
        if not self._is_hal0_user() or not args or args[0] != "systemctl":
            return self._run(list(args), capture_output=True, text=True, check=check)

        verb = args[1] if len(args) > 1 else ""
        if verb == "daemon-reload":
            return self._run(
                self._seam_argv("daemon-reload"), capture_output=True, text=True, check=check
            )
        if verb in _UNIT_VERBS and len(args) > 2:
            slot_id = _slot_id_from_unit(args[2])
            if slot_id is not None:
                return self._run(
                    self._seam_argv(verb, slot_id), capture_output=True, text=True, check=check
                )
        # Not a routable hal0-slot@ op (e.g. is-active, or a non-slot unit) —
        # pass through unprivileged; systemctl read-only queries never need root.
        return self._run(list(args), capture_output=True, text=True, check=check)

    def restart_self(self) -> subprocess.CompletedProcess[str]:
        """``systemctl restart hal0-api.service`` — the self-update path."""
        if not self._is_hal0_user():
            return self._run(
                ["systemctl", "restart", "hal0-api.service"],
                capture_output=True,
                text=True,
                check=True,
            )
        return self._run(
            self._seam_argv("restart-self"), capture_output=True, text=True, check=True
        )


__all__ = ["SEAM_BIN", "Hal0SeamMissing", "SystemCtlSeam", "is_hal0_service_user"]
