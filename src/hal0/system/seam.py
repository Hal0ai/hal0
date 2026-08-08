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

# ── Bundled-agent units (hal0-agent@<id>.service) ────────────────────────────
#
# #453 follow-up. The seam started slot-only, so ``HermesDriver._stop_services``
# had nothing to call and shelled out to a BARE ``systemctl stop
# hal0-agent@hermes.service``. Unprivileged systemctl on a system unit escalates
# through polkit — an interactive password dialog in the middle of an uninstall,
# and a unit that stays up when the operator cancels it. The wrapper grew
# ``stop-agent``/``disable-agent`` (installer/wrappers/hal0-systemctl); these
# constants are the Python side of that contract.

#: systemd template unit for a bundled agent.
AGENT_UNIT_PREFIX = "hal0-agent@"

#: Mirrors ``validate_agent_id`` in installer/wrappers/hal0-systemctl EXACTLY.
#: Client-side validation is a fail-fast convenience, never the security
#: boundary — the seam re-validates every id server-side (as root).
_AGENT_ID_RE = re.compile(r"^[A-Za-z0-9_-]{1,64}$")

#: The only agent-unit verbs the seam exposes, mapped bare-systemctl verb ->
#: seam verb. Anything not in here is a programming error, not a runtime input.
#: start/restart/enable joined stop/disable for the dashboard Services page
#: (#1590) — before that every mutating verb on the hermes card fell through
#: to bare systemctl and died on polkit.
AGENT_UNIT_VERBS: dict[str, str] = {
    "stop": "stop-agent",
    "disable": "disable-agent",
    "start": "start-agent",
    "restart": "restart-agent",
    "enable": "enable-agent",
}

#: Companion-service units the seam's ``svc-<verb>`` family may reach (#1590),
#: mapped unit name -> the service key the wrapper's own CLOSED map accepts.
#: Mirrors the ``svc-start|...`` case arm in installer/wrappers/hal0-systemctl
#: exactly — the wrapper is the boundary, this is the client-side spelling.
COMPANION_SERVICE_UNITS: dict[str, str] = {
    "hal0-openwebui.service": "openwebui",
    "hindsight-api.service": "hindsight",
}

#: Fixed, literal drop-in the memory extraction slot / LLM timeout ride on
#: (#1641, ADR-0023). Root-owned like every other ``/etc/systemd/system``
#: fragment, so an unprivileged hal0-api writes it through the wrapper's
#: ``write-hindsight-dropin`` verb (body on stdin, path is a root-side literal)
#: — exactly the ``write-gateway-dropin`` shape.
HINDSIGHT_DROPIN_DIR = Path("/etc/systemd/system/hindsight-api.service.d")
HINDSIGHT_DROPIN_PATH = HINDSIGHT_DROPIN_DIR / "extraction-model.conf"


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


def agent_unit_name(agent_id: str) -> str:
    """Build ``hal0-agent@<id>.service`` from a validated ``agent_id``.

    Raises :class:`ValueError` on anything the wrapper's ``validate_agent_id``
    would reject. Callers never assemble a unit string themselves and never
    hand one to the seam — the seam takes the bare id and builds the unit name
    on the root side. This function exists so the unprivileged side fails
    fast with a readable error instead of burning a sudo round-trip.
    """
    if not _AGENT_ID_RE.match(agent_id):
        raise ValueError(f"bad agent id: {agent_id!r}")
    return f"{AGENT_UNIT_PREFIX}{agent_id}.service"


def privileged_systemctl(
    verb: str,
    *args: str,
    body: str | None = None,
    seam_bin: str = SEAM_BIN,
    check: bool = True,
    capture_output: bool = False,
    timeout: float | None = None,
) -> subprocess.CompletedProcess[str]:
    """Run one hal0-systemctl seam verb as root via ``sudo -n``.

    THE single sudo helper for this seam:
    :func:`hal0.agents.hermes_provision._privileged_systemctl` delegates here,
    and :func:`agent_unit_argv` builds the identical ``sudo -n <bin> <verb>``
    shape, so there is exactly one spelling of the sudo invocation.

    ``-n`` is load-bearing: it makes sudo non-interactive, so a missing grant
    fails immediately with a non-zero exit instead of prompting. That is the
    whole point of routing a daemon/uninstall path through the seam rather
    than letting bare ``systemctl`` escalate via polkit.

    ``body`` (when given) is piped on stdin — used for ``write-gateway-dropin``.
    With ``check=True`` a non-zero seam exit raises ``CalledProcessError`` so
    the caller surfaces the failure instead of masquerading it as success.
    """
    return subprocess.run(  # nosec B603 — fixed argv; every arg re-validated by the seam
        ["sudo", "-n", seam_bin, verb, *args],
        input=body,
        text=True,
        check=check,
        capture_output=capture_output,
        timeout=timeout,
    )


def agent_unit_argv(
    verb: str,
    agent_id: str,
    *,
    seam_bin: str = SEAM_BIN,
    euid: int | None = None,
) -> list[str]:
    """Build the argv for ``systemctl <verb> hal0-agent@<agent_id>.service``.

    A **pure** function — it decides privilege routing and returns argv; it
    never spawns anything. The caller executes it through its own injected
    runner (``HermesDriver._runner``), which is what keeps that injection point
    — and the test fakes hanging off it — intact.

    Gating is on **euid**, deliberately NOT on :func:`is_hal0_service_user`
    like :class:`SystemCtlSeam`. That gate's "pass through directly when not
    the hal0 user" default is safe for the slot ops (file writes into a
    test-owned tmp tree) but is precisely the polkit defect here: a human
    admin running ``hal0 agent uninstall hermes`` is not the hal0 user
    either, and a direct ``systemctl stop`` from their unprivileged euid
    escalates into a password dialog. So:

    * euid 0 — plain ``systemctl <verb> <unit>``. Root needs no seam, and the
      installer/uninstaller runs before/after the seam binary exists.
    * anything else — ``sudo -n <seam_bin> <verb>-agent <id>``. ``-n`` makes
      sudo non-interactive, so this can fail but can never prompt.

    Raises :class:`ValueError` for an invalid ``agent_id`` and :class:`KeyError`
    for a verb outside :data:`AGENT_UNIT_VERBS` — both caller bugs, surfaced
    before anything is executed.
    """
    seam_verb = AGENT_UNIT_VERBS[verb]
    unit = agent_unit_name(agent_id)  # validates client-side; seam re-validates
    if (os.geteuid() if euid is None else euid) == 0:
        return ["systemctl", verb, unit]
    return ["sudo", "-n", seam_bin, seam_verb, agent_id]


def _slot_id_from_unit(unit_name: str) -> str | None:
    """Extract ``<id>`` from ``hal0-slot@<id>.service``, or ``None`` if the
    unit name isn't one (e.g. ``hal0-api.service`` — never routed here)."""
    m = _UNIT_NAME_RE.match(unit_name)
    return m.group(1) if m else None


_AGENT_UNIT_NAME_RE = re.compile(r"^hal0-agent@([A-Za-z0-9_-]{1,64})\.service$")


def _agent_id_from_unit(unit_name: str) -> str | None:
    """Extract ``<id>`` from ``hal0-agent@<id>.service``, or ``None``."""
    m = _AGENT_UNIT_NAME_RE.match(unit_name)
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

    def write_hindsight_dropin(self, body: str, *, path: Path = HINDSIGHT_DROPIN_PATH) -> None:
        """Write the hindsight-api extraction drop-in (#1641).

        Unlike :meth:`write_unit` / :meth:`write_quadlet` there is no id to
        validate: the target is a single fixed file, so the seam verb takes only
        the body on stdin and the root side owns the path outright (the
        ``write-gateway-dropin`` posture). ``path`` is therefore used **only** on
        the direct (root / dev / CI) branch — it exists so tests and callers that
        redirect the drop-in to a tmp tree keep working; the seam branch can only
        ever reach :data:`HINDSIGHT_DROPIN_PATH`.

        The direct write is atomic (temp + rename) so a crash mid-write can never
        leave a half-written override that would wedge hindsight-api.
        """
        if not self._is_hal0_user():
            path.parent.mkdir(parents=True, exist_ok=True)
            tmp = path.with_suffix(".conf.tmp")
            tmp.write_text(body, encoding="utf-8")
            tmp.replace(path)
            return
        self._run(
            self._seam_argv("write-hindsight-dropin"),
            input=body,
            text=True,
            check=True,
            capture_output=True,
        )

    def systemctl(
        self,
        *args: str,
        check: bool = True,
        timeout: float | None = None,
    ) -> subprocess.CompletedProcess[str]:
        """Run ``systemctl <args...>``, routing daemon-reload + hal0-slot@
        unit verbs through the seam when running as the hal0 service user.

        Read-only queries (``is-active``, ``status``, ...) and anything
        touching a NON-``hal0-slot@`` unit (e.g. ``hal0-api.service`` itself —
        use :meth:`restart_self` for that) always pass straight through:
        neither needs the seam, and the seam wrapper doesn't accept them.

        ``timeout`` (seconds, ``None`` = wait forever, the historical
        behaviour) bounds the child process. Callers that must not be able to
        wedge — notably the slot ``systemctl stop`` on a unit systemd has
        already parked in ``failed`` (#1224) — pass an explicit bound and
        handle :class:`subprocess.TimeoutExpired`.
        """
        if not self._is_hal0_user() or not args or args[0] != "systemctl":
            return self._run(
                list(args), capture_output=True, text=True, check=check, timeout=timeout
            )

        verb = args[1] if len(args) > 1 else ""
        if verb == "daemon-reload":
            return self._run(
                self._seam_argv("daemon-reload"),
                capture_output=True,
                text=True,
                check=check,
                timeout=timeout,
            )
        if verb in _UNIT_VERBS and len(args) > 2:
            slot_id = _slot_id_from_unit(args[2])
            if slot_id is not None:
                return self._run(
                    self._seam_argv(verb, slot_id),
                    capture_output=True,
                    text=True,
                    check=check,
                    timeout=timeout,
                )
            # Bundled-agent unit (hal0-agent@<id>.service) — route through the
            # wrapper's <verb>-agent arms (#1590). Same shape as the slot
            # family: the seam takes the bare id and rebuilds the unit name
            # root-side.
            agent_id = _agent_id_from_unit(args[2])
            if agent_id is not None and verb in AGENT_UNIT_VERBS:
                return self._run(
                    self._seam_argv(AGENT_UNIT_VERBS[verb], agent_id),
                    capture_output=True,
                    text=True,
                    check=check,
                    timeout=timeout,
                )
            # Companion-service unit (openwebui / hindsight) — the wrapper's
            # svc-<verb> family takes a service KEY from a closed map, never a
            # unit string (#1590).
            svc_key = COMPANION_SERVICE_UNITS.get(args[2])
            if svc_key is not None and verb != "reset-failed":
                return self._run(
                    self._seam_argv(f"svc-{verb}", svc_key),
                    capture_output=True,
                    text=True,
                    check=check,
                    timeout=timeout,
                )
        # Not a routable hal0-managed op (e.g. is-active, or a foreign unit) —
        # pass through unprivileged; systemctl read-only queries never need root.
        return self._run(list(args), capture_output=True, text=True, check=check, timeout=timeout)

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


__all__ = [
    "AGENT_UNIT_PREFIX",
    "AGENT_UNIT_VERBS",
    "COMPANION_SERVICE_UNITS",
    "HINDSIGHT_DROPIN_DIR",
    "HINDSIGHT_DROPIN_PATH",
    "SEAM_BIN",
    "Hal0SeamMissing",
    "SystemCtlSeam",
    "agent_unit_argv",
    "agent_unit_name",
    "is_hal0_service_user",
    "privileged_systemctl",
]
