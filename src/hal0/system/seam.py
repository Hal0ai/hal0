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
import shutil
import subprocess
import sys
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from pathlib import Path

from hal0.errors import Hal0Error

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
    # try-restart is a no-op on an inactive unit (#1882). The Hermes
    # terminal-tool posture refresh uses it so re-provisioning can update a
    # running agent without starting one the operator deliberately stopped.
    "try-restart": "try-restart-agent",
    "enable": "enable-agent",
}

#: Companion-service units the seam's ``svc-<verb>`` family may reach (#1590),
#: mapped unit name -> the service key the wrapper's own CLOSED map accepts.
#: Mirrors the ``svc-start|...`` case arm in installer/wrappers/hal0-systemctl
#: exactly — the wrapper is the boundary, this is the client-side spelling.
COMPANION_SERVICE_UNITS: dict[str, str] = {
    "hal0-openwebui.service": "openwebui",
    "hindsight-api.service": "hindsight",
    # The wrapper's svc-* map already carries this key (#1863); without the
    # client-side spelling the unprivileged API's restart fell through to bare
    # systemctl and polkit denied it (#2913).
    "hermes-gateway.service": "hermes-gateway",
    # #3757: the opt-in local decision model server (hal0.memory.decision_component).
    "hal0-hs-decide.service": "hs-decide",
}

#: Fixed, literal drop-in the memory extraction slot / LLM timeout ride on
#: (#1641, ADR-0023). Root-owned like every other ``/etc/systemd/system``
#: fragment, so an unprivileged hal0-api writes it through the wrapper's
#: ``write-hindsight-dropin`` verb (body on stdin, path is a root-side literal)
#: — exactly the ``write-gateway-dropin`` shape.
HINDSIGHT_DROPIN_DIR = Path("/etc/systemd/system/hindsight-api.service.d")
HINDSIGHT_DROPIN_PATH = HINDSIGHT_DROPIN_DIR / "extraction-model.conf"
#: #3492: the Hindsight 1.2 showcase drop-in ([memory.models]/[memory.decision]
#: /[memory.lanes]); ``write-hindsight-showcase-dropin`` owns the path root-side.
HINDSIGHT_SHOWCASE_DROPIN_PATH = HINDSIGHT_DROPIN_DIR / "showcase-lanes.conf"
#: #3520: the hand-maintained H0-98 drop-in the showcase one replaces. It sorts
#: later and would win, so installing the showcase drop-in removes it.
HINDSIGHT_LEGACY_CAP_DROPIN_NAME = "zz-h0-98-llm-global-cap.conf"

# ── Decision model unit (hal0-hs-decide.service, #3757) ──────────────────────
#
# The unit file is root-owned like every /etc/systemd/system fragment. The
# unprivileged hal0-api never sends its text: ``write-hs-decide-unit`` takes
# four validated values (model revision, threads, port, max request tokens)
# and the wrapper renders the unit from its own literal template, so the
# grant cannot change ``User=``, ``ExecStart=``'s binary or the bind address.
# :func:`render_decision_unit` is the byte-identical Python twin used on the
# direct (root / dev) branch; tests/installer keep the two in lockstep.

DECISION_UNIT_NAME = "hal0-hs-decide.service"
DECISION_UNIT_PATH = Path("/etc/systemd/system") / DECISION_UNIT_NAME

_DECISION_REVISION_RE = re.compile(r"^[0-9a-f]{40}$")

#: Literal paths the unit points at (the wrapper's template carries the same).
_DECISION_HOME = "/var/lib/hal0/memory/hindsight-decision"
_DECISION_HF_HOME = "/var/lib/hal0/memory/hindsight/hf-cache"
_DECISION_SNAPSHOTS = f"{_DECISION_HF_HOME}/hub/models--hindsight-ai--hs-decide-1-flash/snapshots"


def _check_decision_unit_params(revision: str, threads: int, port: int, max_tokens: int) -> None:
    """Mirror of the wrapper's validation; a fail-fast convenience, not the boundary."""
    if not isinstance(revision, str) or not _DECISION_REVISION_RE.match(revision):
        raise ValueError(f"bad model revision: {revision!r}")
    for name, value, lo, hi in (
        ("threads", threads, 1, 64),
        ("port", port, 1024, 65535),
        ("max_tokens", max_tokens, 256, 32768),
    ):
        if not isinstance(value, int) or isinstance(value, bool) or not lo <= value <= hi:
            raise ValueError(f"bad {name}: {value!r}")


def render_decision_unit(*, revision: str, threads: int, port: int, max_tokens: int) -> str:
    """The ``hal0-hs-decide.service`` text for these values (validated)."""
    _check_decision_unit_params(revision, threads, port, max_tokens)
    return (
        "# Managed by hal0 (hal0 memory decision install). hal0 rewrites this file.\n"
        "[Unit]\n"
        "Description=hal0 System One decision model (hs-decide-1-flash, CPU)\n"
        "After=network.target\n"
        "Before=hindsight-api.service\n"
        "\n"
        "[Service]\n"
        "User=hal0\n"
        "Group=hal0\n"
        f"WorkingDirectory={_DECISION_HOME}\n"
        f"Environment=HF_HOME={_DECISION_HF_HOME}\n"
        "Environment=HF_HUB_OFFLINE=1\n"
        f"Environment=HS_DECIDE_THREADS={threads}\n"
        f"ExecStart={_DECISION_HOME}/.venv/bin/python {_DECISION_HOME}/hs_decide_serve.py"
        f" --model {_DECISION_SNAPSHOTS}/{revision} --host 127.0.0.1 --port {port}"
        f" --threads {threads} --max-tokens {max_tokens}\n"
        "Nice=5\n"
        "Restart=on-failure\n"
        "RestartSec=5\n"
        "NoNewPrivileges=yes\n"
        "PrivateTmp=yes\n"
        "ProtectSystem=full\n"
        "\n"
        "[Install]\n"
        "WantedBy=multi-user.target\n"
    )


class Hal0SeamMissing(RuntimeError):
    """Raised when the hal0-systemctl seam is required but not installed.

    Distinct from a bare ``PermissionError``/``FileNotFoundError`` so a
    caller (``hal0 doctor``) can give a precise remediation instead of a raw
    traceback.
    """


class SeamTimeout(Hal0Error):
    """A bounded systemctl/podman seam call did not return within its budget
    (#1869/#1870).

    Raised by :func:`bounded_call` in place of the bare
    :class:`subprocess.TimeoutExpired` every direct ``_run`` caller used to
    let escape (or, worse, never bounded at all): a ``Hal0Error`` subclass
    carries a stable ``code`` the API error-envelope middleware renders
    uniformly, and a message an operator (or the CLI's own timeout handler)
    can act on without reading a Python traceback.

    ``details`` carries the three facts #1869 asked for: the ``command`` that
    wedged, the ``budget_s`` it was given, and ``last_state`` — whatever the
    child had already written to stdout/stderr at the moment it was killed
    (``subprocess.TimeoutExpired.stdout``/``.stderr`` when the call captured
    output, which every :class:`SystemCtlSeam` route does). ``None`` when the
    child produced no output before the kill — a wedge that never got as far
    as writing anything, which is itself informative (it did not even reach
    the point of talking to systemd/podman).
    """

    code = "slot.seam_timeout"
    status = 504

    def __init__(self, command: Sequence[str], budget_s: float, last_state: str | None) -> None:
        cmd = " ".join(command)
        message = f"`{cmd}` did not return within {budget_s:g}s"
        if last_state:
            message += f" — last observed: {last_state}"
        super().__init__(
            message,
            details={"command": list(command), "budget_s": budget_s, "last_state": last_state},
        )


def bounded_call(
    argv: Sequence[str],
    *,
    timeout: float | None,
    run: Callable[..., subprocess.CompletedProcess[str]],
    **kwargs: object,
) -> subprocess.CompletedProcess[str]:
    """Run ``argv`` through ``run``, turning a timeout into :class:`SeamTimeout`.

    THE one place a ``systemctl``/``podman`` child's ``subprocess.TimeoutExpired``
    becomes the typed error the rest of hal0 (the slot state machine, the API
    error envelope, the CLI) knows how to handle — every :class:`SystemCtlSeam`
    route calls this instead of ``run`` directly (#1869).

    ``timeout=None`` is a deliberate passthrough (no bound applied, matching
    ``subprocess.run``'s own default) rather than a special case: callers that
    have not been given a budget yet keep today's unbounded wait instead of
    silently gaining a bogus one.
    """
    try:
        return run(list(argv), timeout=timeout, **kwargs)
    except subprocess.TimeoutExpired as exc:
        raw_state = exc.stdout or exc.stderr or None
        last_state: str | None
        if isinstance(raw_state, bytes):
            last_state = raw_state.decode("utf-8", errors="replace").strip() or None
        else:
            last_state = raw_state.strip() or None if raw_state else None
        raise SeamTimeout(list(argv), float(timeout or 0.0), last_state) from exc


def is_hal0_service_user() -> bool:
    """True only when THIS process's euid is literally the ``hal0`` service
    account's uid. ``False`` (never seam) when the ``hal0`` user doesn't
    exist on this box at all — the dev/CI/unit-test default."""
    try:
        hal0_uid = pwd.getpwnam("hal0").pw_uid
    except KeyError:
        return False
    return os.geteuid() == hal0_uid


@dataclass(frozen=True)
class ServiceAccount:
    """The ``hal0`` service account as the kernel sees it."""

    uid: int
    gid: int
    """Primary group."""
    groups: tuple[int, ...]
    """Supplementary gids, sorted, primary excluded (``video``/``render``, a
    shared model-store group, ...)."""


def hal0_service_account() -> ServiceAccount | None:
    """uid, primary gid and supplementary gids of the ``hal0`` account, or
    ``None`` when the account doesn't exist on this box (dev/CI).

    The img slot pins its container process to exactly this identity (#3066):
    the account that owns the data the container mounts, with the same group
    reach it has on the host, so a store shared through a group (gid 2000 on
    one lab box) stays readable inside."""
    try:
        pw = pwd.getpwnam("hal0")
    except KeyError:
        return None
    groups = sorted(set(os.getgrouplist("hal0", pw.pw_gid)) - {pw.pw_gid})
    return ServiceAccount(pw.pw_uid, pw.pw_gid, tuple(groups))


def hal0_service_home() -> str:
    """The hal0 service user's home dir (``/var/lib/hal0`` fallback)."""
    try:
        return pwd.getpwnam("hal0").pw_dir or "/var/lib/hal0"
    except KeyError:
        return "/var/lib/hal0"


def as_hal0_argv(argv: Sequence[str]) -> list[str]:
    """``argv`` wrapped to run as the ``hal0`` service user from a root process.

    ``runuser``, else ``setpriv``, else ``sudo``, the order
    ``installer/lib/run-as-hal0.sh`` uses, behind an ``env`` that drops an
    inherited ``HERMES_HOME`` and sets ``HOME`` to hal0's home, as that script
    does, so all three tools leave the same environment. ``-u`` comes before
    the assignment: after it, ``env`` would run ``-u`` as the command.
    The caller guarantees ``euid == 0`` and that the account exists.
    """
    env_argv = ["env", "-u", "HERMES_HOME", f"HOME={hal0_service_home()}", *argv]
    if shutil.which("runuser"):
        return ["runuser", "-u", "hal0", "--", *env_argv]
    if shutil.which("setpriv"):
        return ["setpriv", "--reuid", "hal0", "--regid", "hal0", "--init-groups", "--", *env_argv]
    return ["sudo", "-u", "hal0", "--", *env_argv]


def running_hal0_bin() -> str:
    """The ``hal0`` console script belonging to the code that is running.

    #2092: the privilege-drop re-exec used to resolve its target with
    ``shutil.which("hal0")`` — whatever is first on PATH, which is not
    necessarily the tree doing the re-exec. On a box carrying a stale wrapper
    at ``/usr/local/bin/hal0`` (#1844 documents exactly that condition), an
    operator running the CURRENT ``/usr/lib/hal0/venv/bin/hal0 agent
    reprovision hermes`` as root had it silently re-exec an rc.3 build, which
    then resolved its installer root relative to its own package location and
    failed with a path that exists in no release
    (``/usr/local/lib/python3.12/installer/...``).

    A venv install puts the console script next to the interpreter, so
    ``sys.executable``'s sibling names the running tree exactly. Fall back to
    PATH (dev checkouts invoked as ``python -m`` have no sibling script), then
    to the bare name so the caller still produces a runnable argv.
    """
    sibling = Path(sys.executable).with_name("hal0")
    if sibling.is_file() and os.access(sibling, os.X_OK):
        return str(sibling)
    return shutil.which("hal0") or "hal0"


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

    # ``write_unit`` and ``remove_unit`` (and the wrapper's ``write-unit`` /
    # ``remove-unit`` verbs) are gone. ``write_unit`` was REMOVED in #1740: it
    # shipped an unvalidated body to a root-owned
    # ``/etc/systemd/system/hal0-slot@<id>.service`` — an unconditional root
    # exec primitive reachable from the unprivileged hal0 account — and
    # P3-quadlet had already left it with no producer: the sole renderer of
    # slot-unit text (``_render_quadlet_from_plan``) emits a ``.container``,
    # written via :meth:`write_quadlet`. ``remove_unit`` (legacy pre-quadlet
    # ``.service`` cleanup) had no production caller either.

    def write_quadlet(
        self, quadlet_path: Path, unit_text: str, *, timeout: float | None = None
    ) -> None:
        """Write a ``hal0-slot@<token>.container`` Quadlet source file (P3-quadlet).

        The declarative Quadlet replacement for the removed ``write_unit``:
        root-owned by design under ``/etc/containers/systemd/``, so a
        hal0-service-user install routes the write through ``hal0-systemctl
        write-quadlet <token>`` (body on stdin). Dev/CI/test (not the hal0 user)
        writes directly, exactly as before P3-perms.

        #1740: ``unit_text`` is NOT trusted by the root side. The wrapper
        allow-lists the body against exactly the sections/keys
        ``_render_quadlet_from_plan`` emits and persists its own validated
        reconstruction, so a body this (unprivileged) process could tamper with
        can no longer carry a host-side ``[Service] ExecStartPre=``. Rendering
        here is a convenience, never the control — see the wrapper's HONEST
        BOUNDARY note for what the allow-list does and does not claim.

        ``timeout`` (seconds, ``None`` = unbounded) bounds the seam child
        (#1869): this write is the first of three seam calls every slot load
        makes before the container ever spawns, so a wedge here is just as
        capable of hanging the CLI/API as the ``systemctl restart`` after it.
        """
        if not self._is_hal0_user():
            quadlet_path.parent.mkdir(parents=True, exist_ok=True)
            quadlet_path.write_text(unit_text)
            return
        token = _slot_id_from_quadlet(quadlet_path.name)
        if token is None:
            raise ValueError(f"not a hal0-slot@ quadlet file: {quadlet_path.name!r}")
        bounded_call(
            self._seam_argv("write-quadlet", token),
            timeout=timeout,
            run=self._run,
            input=unit_text,
            text=True,
            check=True,
        )

    def remove_quadlet(self, quadlet_path: Path, *, timeout: float | None = None) -> None:
        """Delete a ``hal0-slot@<token>.container`` Quadlet file (no-op if absent).

        ``timeout`` bounds the seam child, like :meth:`write_quadlet`'s (#1869).
        """
        if not self._is_hal0_user():
            quadlet_path.unlink(missing_ok=True)
            return
        token = _slot_id_from_quadlet(quadlet_path.name)
        if token is None:
            raise ValueError(f"not a hal0-slot@ quadlet file: {quadlet_path.name!r}")
        bounded_call(
            self._seam_argv("remove-quadlet", token),
            timeout=timeout,
            run=self._run,
            check=False,
        )

    def write_hindsight_dropin(
        self,
        body: str,
        *,
        path: Path = HINDSIGHT_DROPIN_PATH,
        timeout: float | None = None,
    ) -> None:
        """Write the hindsight-api extraction drop-in (#1641).

        Unlike :meth:`write_quadlet` there is no id to
        validate: the target is a single fixed file, so the seam verb takes only
        the body on stdin and the root side owns the path outright (the
        ``write-gateway-dropin`` posture). ``path`` is therefore used **only** on
        the direct (root / dev / CI) branch — it exists so tests and callers that
        redirect the drop-in to a tmp tree keep working; the seam branch can only
        ever reach :data:`HINDSIGHT_DROPIN_PATH`.

        The direct write is atomic (temp + rename) so a crash mid-write can never
        leave a half-written override that would wedge hindsight-api.

        ``timeout`` (seconds, ``None`` = unbounded) bounds the seam child, like
        :meth:`systemctl`'s. A caller on an API worker thread must pass one: a
        stalled ``sudo`` (NSS lookup, a wedged root-side filesystem op) would
        otherwise park that thread forever, and enough retries would drain the
        pool.
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
            timeout=timeout,
        )

    def write_hindsight_showcase_dropin(
        self,
        body: str,
        *,
        path: Path = HINDSIGHT_SHOWCASE_DROPIN_PATH,
        timeout: float | None = None,
    ) -> None:
        """Write the hindsight-api showcase drop-in (#3492).

        Same posture as :meth:`write_hindsight_dropin`: the seam verb takes only
        the body on stdin and owns the path; ``path`` is honoured only on the
        direct (root / dev / CI) branch. Atomic (temp + rename). ``timeout``
        bounds the seam child — API-thread callers must pass one.

        #3520: both branches also remove the hand-maintained
        :data:`HINDSIGHT_LEGACY_CAP_DROPIN_NAME` beside the drop-in (the seam
        verb does it root-side, for its literal path only), so its later-sorting
        ``HINDSIGHT_API_LLM_MAX_CONCURRENT`` stops overriding hal0's.
        """
        if not self._is_hal0_user():
            path.parent.mkdir(parents=True, exist_ok=True)
            tmp = path.with_suffix(".conf.tmp")
            tmp.write_text(body, encoding="utf-8")
            tmp.replace(path)
            (path.parent / HINDSIGHT_LEGACY_CAP_DROPIN_NAME).unlink(missing_ok=True)
            return
        self._run(
            self._seam_argv("write-hindsight-showcase-dropin"),
            input=body,
            text=True,
            check=True,
            capture_output=True,
            timeout=timeout,
        )

    def write_decision_unit(
        self,
        *,
        revision: str,
        threads: int,
        port: int,
        max_tokens: int,
        path: Path = DECISION_UNIT_PATH,
        timeout: float | None = None,
    ) -> None:
        """Install ``hal0-hs-decide.service`` for these values (#3757).

        The seam branch sends only the four values to ``hal0-systemctl
        write-hs-decide-unit``, which validates them and renders the unit from
        its own template; ``path`` is honoured only on the direct (root / dev)
        branch, which writes :func:`render_decision_unit` atomically.
        """
        body = render_decision_unit(
            revision=revision, threads=threads, port=port, max_tokens=max_tokens
        )
        if not self._is_hal0_user():
            path.parent.mkdir(parents=True, exist_ok=True)
            tmp = path.with_suffix(".service.tmp")
            tmp.write_text(body, encoding="utf-8")
            tmp.chmod(0o644)
            tmp.replace(path)
            return
        bounded_call(
            self._seam_argv(
                "write-hs-decide-unit", revision, str(threads), str(port), str(max_tokens)
            ),
            timeout=timeout,
            run=self._run,
            capture_output=True,
            text=True,
            check=True,
        )

    def remove_decision_unit(
        self, *, path: Path = DECISION_UNIT_PATH, timeout: float | None = None
    ) -> None:
        """Delete ``hal0-hs-decide.service`` (no-op if absent) — ``remove-hs-decide-unit``."""
        if not self._is_hal0_user():
            path.unlink(missing_ok=True)
            return
        bounded_call(
            self._seam_argv("remove-hs-decide-unit"),
            timeout=timeout,
            run=self._run,
            capture_output=True,
            text=True,
            check=True,
        )

    def prune_dnat(
        self,
        port: int,
        handle: int,
        *,
        dry_run: bool = False,
        timeout: float | None = 30.0,
    ) -> subprocess.CompletedProcess[str]:
        """Delete one **stale** netavark DNAT rule (#1814).

        A container that dies without netavark's teardown running leaves its
        DNAT rule behind in ``inet netavark`` / ``nv_<netid>_dnat``. nftables is
        first-match, so that dead rule black-holes the port for every later
        container. Removing it needs root, so it routes through the wrapper's
        ``prune-dnat`` verb rather than an ``nft`` subprocess here.

        Only two integers cross the boundary. The root side re-derives the chain
        by walking the live ruleset, requires the rule at ``handle`` to be a DNAT
        rule for ``port``, and refuses outright when the target IP belongs to a
        running container — so this cannot cut a working port, and cannot reach a
        chain outside ``nv_*_dnat``. See the wrapper's STALE-DNAT REPAIR note.

        ``dry_run`` uses the side-effect-free ``check-dnat`` verb instead: same
        guard, no delete. The direct (root / dev / CI) branch is deliberately
        absent — there is no unprivileged fallback for editing nftables, and a
        non-root process without the seam must fail loudly rather than silently
        skip the repair. Raises :class:`subprocess.CalledProcessError` (exit 64
        from the wrapper) with the refusal reason on stderr.
        """
        if not isinstance(port, int) or isinstance(port, bool) or not 1 <= port <= 65535:
            raise ValueError(f"bad port: {port!r}")
        if not isinstance(handle, int) or isinstance(handle, bool) or handle < 1:
            raise ValueError(f"bad handle: {handle!r}")
        verb = "check-dnat" if dry_run else "prune-dnat"
        if not self._is_hal0_user() and os.geteuid() == 0:
            # Already root (the CLI run by an admin, the installer): call the
            # wrapper directly. Same validation, one less sudo hop — and no
            # sudoers grant is needed for `hal0 doctor ports --fix` as root.
            return self._run(
                [self._seam_bin, verb, str(port), str(handle)],
                capture_output=True,
                text=True,
                check=True,
                timeout=timeout,
            )
        return self._run(
            self._seam_argv(verb, str(port), str(handle)),
            capture_output=True,
            text=True,
            check=True,
            timeout=timeout,
        )

    def _firewall_argv(self, verb: str) -> list[str] | None:
        """argv for an argument-less firewall verb, or ``None`` when unreachable.

        The ``hal0`` service account goes through sudo. Root calls the
        installed wrapper directly (one less hop, no sudoers grant needed).
        Anyone else, or root on a box without the wrapper (dev, CI), gets
        ``None`` and the caller decides on its own fallback.
        """
        if self._is_hal0_user():
            return self._seam_argv(verb)
        if os.geteuid() == 0 and os.access(self._seam_bin, os.X_OK):
            return [self._seam_bin, verb]
        return None

    def dnat_snapshot(self, *, timeout: float | None = 20.0) -> str:
        """Framed read of netavark's DNAT rules on both backends (#3724).

        Read-only ``dnat-snapshot`` verb, no arguments: prints the ``inet
        netavark`` nft table, the NETAVARK lines of ``iptables -t nat -S`` and
        root's running containers (name, IPs, published ports) as
        ``@@<section> <ok|absent>`` blocks that
        :func:`hal0.system.netavark.parse_snapshot` reads.

        Raises :class:`Hal0SeamMissing` when the wrapper is not reachable from
        this process, so the caller can read in-process instead;
        :class:`subprocess.CalledProcessError` when the wrapper refused or
        failed.
        """
        argv = self._firewall_argv("dnat-snapshot")
        if argv is None:
            raise Hal0SeamMissing("hal0-systemctl dnat-snapshot not reachable from this process")
        proc = bounded_call(
            argv, timeout=timeout, run=self._run, capture_output=True, text=True, check=True
        )
        return proc.stdout or ""

    def network_reload(self, *, timeout: float | None = 120.0) -> subprocess.CompletedProcess[str]:
        """``podman network reload --all`` through the ``network-reload`` verb (#3724).

        Rebuilds every running container's netavark rules without restarting
        anything; the non-disruptive repair for a MISSING DNAT rule. No
        argument crosses the boundary. Root without the wrapper (dev box) runs
        podman directly; a non-root process without the seam goes through sudo
        and fails loudly rather than silently skipping the repair.
        """
        argv = self._firewall_argv("network-reload")
        if argv is None:
            argv = (
                ["podman", "network", "reload", "--all"]
                if os.geteuid() == 0
                else self._seam_argv("network-reload")
            )
        return bounded_call(
            argv, timeout=timeout, run=self._run, capture_output=True, text=True, check=True
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
        handle :class:`SeamTimeout` (#1869: raised by :func:`bounded_call` in
        place of the bare ``subprocess.TimeoutExpired`` this used to let
        escape uncaught).
        """

        def _exec(argv: list[str]) -> subprocess.CompletedProcess[str]:
            return bounded_call(
                argv, timeout=timeout, run=self._run, capture_output=True, text=True, check=check
            )

        if not self._is_hal0_user() or not args or args[0] != "systemctl":
            return _exec(list(args))

        verb = args[1] if len(args) > 1 else ""
        if verb == "daemon-reload":
            return _exec(self._seam_argv("daemon-reload"))
        if verb == "try-restart" and len(args) > 2:
            # No-op on an inactive unit (#1882): a companion the operator
            # stopped on purpose stays stopped. Companion keys only; the
            # wrapper's svc-try-restart arm shares the closed key map.
            svc_key = COMPANION_SERVICE_UNITS.get(args[2])
            if svc_key is not None:
                return _exec(self._seam_argv("svc-try-restart", svc_key))
        if verb in _UNIT_VERBS and len(args) > 2:
            slot_id = _slot_id_from_unit(args[2])
            if slot_id is not None:
                return _exec(self._seam_argv(verb, slot_id))
            # Bundled-agent unit (hal0-agent@<id>.service) — route through the
            # wrapper's <verb>-agent arms (#1590). Same shape as the slot
            # family: the seam takes the bare id and rebuilds the unit name
            # root-side.
            agent_id = _agent_id_from_unit(args[2])
            if agent_id is not None and verb in AGENT_UNIT_VERBS:
                return _exec(self._seam_argv(AGENT_UNIT_VERBS[verb], agent_id))
            # Companion-service unit (openwebui / hindsight) — the wrapper's
            # svc-<verb> family takes a service KEY from a closed map, never a
            # unit string (#1590).
            svc_key = COMPANION_SERVICE_UNITS.get(args[2])
            if svc_key is not None and verb != "reset-failed":
                return _exec(self._seam_argv(f"svc-{verb}", svc_key))
        # Not a routable hal0-managed op (e.g. is-active, or a foreign unit) —
        # pass through unprivileged; systemctl read-only queries never need root.
        return _exec(list(args))

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
    "DECISION_UNIT_NAME",
    "DECISION_UNIT_PATH",
    "HINDSIGHT_DROPIN_DIR",
    "HINDSIGHT_DROPIN_PATH",
    "HINDSIGHT_LEGACY_CAP_DROPIN_NAME",
    "SEAM_BIN",
    "Hal0SeamMissing",
    "SeamTimeout",
    "SystemCtlSeam",
    "agent_unit_argv",
    "agent_unit_name",
    "bounded_call",
    "is_hal0_service_user",
    "privileged_systemctl",
    "render_decision_unit",
]
