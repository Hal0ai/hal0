"""``hal0 doctor all`` — one read-only evidence pass over the box (§21.4).

The individual ``doctor`` subcommands each audit one surface (``perms``,
``models``, ``profiles``) and ``doctor verify`` renders the
live-API report card. ``doctor all`` composes the *read-only* evidence into a
single roll-up so an operator (or a bug report) gets the whole picture in one
command, with ``--json`` for machine consumers.

It re-uses the tested :class:`hal0.cli.doctor_verify.Check` row type and the
verify report-card classifiers (API, runners, DNS, capabilities, memory,
OpenWebUI, Hermes), then adds the broader health rows the retrofit calls for:
auth posture, model-store integrity, a stale-dashboard
``HAL0_UI_DIST`` override (#1589), bound slot ports, the ``hal0.target``
boot-enable anchor (r5-sync-assessment §6.1), the privileged sudo seams
every slot op + self-update runs through (#1465), whether the bundled agent
shares ``hal0-api``'s UID (ADR-0002 — advisory, it is the shipped default),
and the context window behind
Hermes' anchor model (#1867 — the smoke probes never run on an in-place
upgrade, which is the only shape that defect survives in). It also flags a
Hermes ``config.yaml`` whose ``approvals.mode`` drifted from the operator's
command-approvals setting (advisory).

Read-only — there is no ``--fix`` here; the per-surface subcommands own
repair. The one exception to pure reads is the memory extraction speed row
(#2353), which may run one bounded completion against the extraction slot
(<= 512 prompt + 32 decode tokens, capped at 30 s). Exit codes:

* 0 — everything clean (advisory ``warn`` rows may still print)
* 1 — at least one non-critical ``fail`` (actionable finding)
* 2 — a critical row failed (API unreachable / zero healthy runners)
"""

from __future__ import annotations

import contextlib
import grp
import json as jsonlib
import os
import pwd
import shutil
import stat
import subprocess
from collections.abc import Callable
from pathlib import Path
from typing import Any

import typer
from rich.console import Console
from rich.table import Table

from hal0.cli.doctor_verify import (
    _FAIL,
    _PASS,
    _WARN,
    Check,
    build_checks,
    gather_payloads,
)
from hal0.config import paths
from hal0.system.seam_check import REMEDIATION, SeamStatus, probe_seams

console = Console()


# ── extra read-only classifiers (pure — take parsed JSON, return a Check) ──────


def check_auth_posture(auth: dict[str, Any] | None) -> Check:
    """Auth exposure posture from ``GET /api/auth/status`` (advisory, #1822).

    ``auth_required`` + ``has_admin_key`` describe whether the box gates
    access; ``lan_exposed`` (added for #1822) says whether the bind reaches
    beyond loopback. The old version of this check printed PASS "dev/loopback"
    for ANY box with ``auth_required`` off, never looking at the bind — a
    LAN-bound, auth-off box (hal0-api's shipped default) got a clean bill of
    health while reachable from every device on the network. Missing
    ``lan_exposed`` from an older API build degrades to ``False`` (unknown ->
    assume loopback) rather than raising, so this stays advisory-safe.
    """
    if auth is None:
        return Check("auth", "Auth posture", _WARN, "auth status unreachable")
    required = bool(auth.get("auth_required"))
    has_key = bool(auth.get("has_admin_key"))
    lan_exposed = bool(auth.get("lan_exposed"))
    if not required:
        if lan_exposed:
            if not has_key:
                return Check(
                    "auth",
                    "Auth posture",
                    _WARN,
                    "reachable from your network with auth off and no admin key set — every "
                    "route, including model pulls, slot deletes and config writes, is "
                    "unauthenticated from any LAN device. Mint an admin key with "
                    "`sudo hal0 auth reset-key` (other devices then need it for admin routes), "
                    "then `hal0 auth require on` to require a key everywhere, or bind "
                    "loopback (HAL0_BIND_HOST=127.0.0.1) to close this.",
                )
            return Check(
                "auth",
                "Auth posture",
                _WARN,
                "reachable from your network with auth off — other devices must sign in "
                "with the admin key for admin routes (settings, memory, logs, approvals, "
                "model pulls, slot and config changes), but inference (/v1), the memory "
                "tool mount (/mcp/memory) and the slot, model and hardware lists "
                "stay open to any LAN device. `hal0 auth require on` requires a key for "
                "those too; binding loopback (HAL0_BIND_HOST=127.0.0.1) takes the box off "
                "the LAN.",
            )
        return Check("auth", "Auth posture", _PASS, "open (auth not required — dev/loopback)")
    if not has_key:
        return Check(
            "auth",
            "Auth posture",
            _WARN,
            "auth required but no admin key set — mint one (HAL0_ADMIN_KEY) with "
            "`sudo hal0 auth reset-key` so an operator can log in",
        )
    return Check("auth", "Auth posture", _PASS, "auth required, admin key configured")


#: Secret-bearing files whose mode ``doctor`` refuses to let drift open.
_SECRET_FILES: tuple[Callable[[], Path], ...] = (
    lambda: paths.api_env(),
    lambda: paths.openwebui_env(),
)


def check_secret_file_modes() -> Check:
    """Secret-bearing config files must not be group- or world-readable.

    An independent backstop for #1466, deliberately NOT derived from
    ``install/perms.py``'s table: that table is what got it wrong — it pinned
    ``api.env`` at 0644 behind a ``FIXME(phase4)`` while the file held live
    provider tokens and, after a rotation, the box's admin key. A check
    generated from the same table would have agreed with the bug. This one
    asserts the property directly, so a fifth writer, a re-widened PermRow, or
    a hand ``chmod`` all surface here.

    Critical: the finding is "every local account can read your API keys".
    A missing file is clean — plenty of boxes have no ``openwebui.env``.
    """
    offenders: list[str] = []
    for resolve in _SECRET_FILES:
        path = resolve()
        try:
            mode = path.stat().st_mode & 0o777
        except OSError:
            continue  # absent or unreadable — nothing to assert
        if mode & (stat.S_IRWXG | stat.S_IRWXO):
            offenders.append(f"{path.name} is {mode:o} ({path})")
    if offenders:
        return Check(
            "secret-modes",
            "Secret file modes",
            _FAIL,
            "world/group-readable secret file(s): "
            + "; ".join(offenders)
            + f" — expected {paths.API_ENV_MODE:o}; fix with `hal0 doctor perms --fix`",
            critical=True,
        )
    return Check("secret-modes", "Secret file modes", _PASS, "secret files are owner-only")


def check_hardware_freshness() -> Check:
    """The cached hardware.json must be fresh enough to trust (#1862/H9).

    Runs the SAME staleness check ``_host_has_capable_gpu`` (VRAM/RAM
    attribution) and the ROCm/Vulkan lane derivation now fall back through
    (:func:`hal0.hardware.freshness.staleness_reason`), so this row explains
    OUT LOUD, on every ``hal0 doctor`` run, why a box might be getting a
    live re-probe on every capacity read instead of the cheap cached one —
    a missing probe (fresh install, ``hal0 config hardware --refresh`` never run), a cache that
    predates a kernel upgrade/reboot, or one written before the #1799
    capability fields existed.

    Warn-only: a live-probe fallback keeps the box CORRECT even while stale
    (never silently wrong), just not as cheap — so this is an actionable
    heads-up, not a failure.
    """
    from hal0.config.loader import load_hardware_info
    from hal0.hardware.freshness import staleness_reason

    name, title = "hardware_freshness", "Hardware probe freshness"
    path = paths.hardware_json()
    has_file = path.exists()
    if not has_file:
        return Check(
            name,
            title,
            _WARN,
            f"{path} does not exist — run `hal0 config hardware --refresh` (until then, VRAM/RAM "
            "attribution and the ROCm/Vulkan lane derivation fall back to a live "
            "re-probe on every read)",
        )
    try:
        info = load_hardware_info()
    except Exception as exc:
        return Check(
            name,
            title,
            _WARN,
            f"{path} is unreadable ({exc}) — run `hal0 config hardware --refresh`",
        )
    reason = staleness_reason(info, has_file=True)
    if reason is None:
        return Check(
            name, title, _PASS, f"hardware.json is fresh (probed {info.probed_at or 'unknown'})"
        )
    return Check(
        name,
        title,
        _WARN,
        f"hardware.json is stale ({reason}) — falling back to a live re-probe on every "
        f"VRAM/RAM and ROCm/Vulkan-lane read; run `hal0 config hardware --refresh` to refresh the cache",
    )


def check_seed_context_envelope(*, slots_dir: Path | None = None) -> Check:
    """A slot profile's ``ctx_size`` must fit this box's memory envelope (#1868).

    ``static_seeds``/``hal0 setup --auto`` clamp freshly-seeded slots at
    install time (by pointing them at a clamped profile clone), but an
    operator-edited profile window, or a box downgraded in RAM since install
    (a VM resize, a moved LXC), is never revisited. Reads each slot's window
    through its profile (ADR-0029, :func:`hal0.agents.anchor_window.
    read_slot_ceiling`) and flags any slot whose window exceeds what
    :func:`hal0.hardware.memory_envelope.max_affordable_context_tokens` says
    this box can spend on a model + KV cache — advisory (the slot may still
    run fine if the model itself is small), never a fail.
    """
    from hal0.agents.anchor_window import SLOTS_DIR, read_slot_ceiling
    from hal0.hardware.freshness import resolve_fresh_hardware_info
    from hal0.hardware.memory_envelope import max_affordable_context_tokens

    name, title = "seed_context_envelope", "Slot profile ctx_size vs. memory envelope"
    directory = slots_dir if slots_dir is not None else SLOTS_DIR
    try:
        names = sorted(p.stem for p in directory.glob("*.toml"))
    except OSError as exc:
        return Check(name, title, _WARN, f"could not list {directory}: {exc}")
    if not names:
        return Check(name, title, _PASS, f"no slot TOMLs found in {directory}")

    try:
        hw, _stale = resolve_fresh_hardware_info()
    except Exception as exc:
        return Check(name, title, _WARN, f"hardware envelope unreadable: {exc}")
    affordable = max_affordable_context_tokens(hw)

    offenders: list[str] = []
    for slot_name in names:
        ceiling = read_slot_ceiling(slot_name, slots_dir=directory)
        if ceiling is not None and ceiling > affordable:
            offenders.append(f"{slot_name} ({ceiling:,} > {affordable:,})")
    if offenders:
        return Check(
            name,
            title,
            _WARN,
            "profile ctx_size above this box's memory envelope: "
            + ", ".join(offenders)
            + " — lower the slot profile's ctx_size (duplicate a seed profile and point "
            "the slot at the copy), or add memory",
        )
    return Check(
        name, title, _PASS, f"every slot profile ctx_size fits the {affordable:,}-token envelope"
    )


#: The API unit, and the agent template whose instances carry the bundled agent.
_API_UNIT = "hal0-api.service"
_AGENT_UNIT_GLOB = "hal0-agent@*.service"


def _unit_user(unit: str) -> str | None:
    """``systemctl show -p User --value <unit>`` → the effective ``User=``.

    ``None`` means the question could not be asked (no ``systemctl``, unit
    unknown); an empty ``User=`` means the unit runs as ``root``, which
    systemd reports as the empty string, so that is normalised here.
    """
    if shutil.which("systemctl") is None:
        return None
    try:
        result = subprocess.run(  # nosec B603 B607
            ["systemctl", "show", "-p", "User", "--value", unit],
            check=False,
            capture_output=True,
            text=True,
            timeout=5,
        )
    except (OSError, subprocess.SubprocessError):
        return None
    if result.returncode != 0:
        return None
    return result.stdout.strip() or "root"


def _agent_units() -> list[str] | None:
    """Provisioned ``hal0-agent@<instance>.service`` units, or ``None`` if unknown.

    Two sources, unioned, because neither alone answers "is a bundled agent
    installed on this box":

    * ``systemctl list-units --all`` only lists units currently *in memory* —
      ``--all`` adds inactive-but-loaded ones, not an instance that was stopped
      and unloaded. That instance is still installed and still the boundary
      this row exists to report on.
    * the persistent artifacts an instance leaves on disk (enable symlink,
      drop-in dir, per-agent config/env) survive both an unload and a
      ``hal0 agent disable`` — see :func:`_provisioned_agent_units`.

    ``None`` is *unknown*, distinct from ``[]`` (definitively no agent): no
    ``systemctl`` on PATH, or the manager failed to answer. Collapsing the two
    would turn a transient systemd error into a clean row.
    """
    if shutil.which("systemctl") is None:
        return None
    try:
        result = subprocess.run(  # nosec B603 B607
            [
                "systemctl",
                "list-units",
                "--all",
                "--plain",
                "--no-legend",
                "--no-pager",
                "--type=service",
                _AGENT_UNIT_GLOB,
            ],
            check=False,
            capture_output=True,
            text=True,
            timeout=5,
        )
    except (OSError, subprocess.SubprocessError):
        return None
    if result.returncode != 0:
        return None
    units: set[str] = set()
    for line in result.stdout.splitlines():
        fields = line.split()
        name = fields[0] if fields else ""
        if name.startswith("hal0-agent@") and name.endswith(".service"):
            units.add(name)
    units |= set(_provisioned_agent_units())
    return sorted(units)


def _provisioned_agent_units(unit_dir: Path | None = None) -> list[str]:
    """Agent instances that exist on disk, whatever systemd currently has loaded.

    ``systemctl list-units`` reports units *in memory*, and the ``.wants``
    symlinks only cover *enabled* instances — but ``hal0 agent disable`` stops
    and disables an instance without removing its env file or its config, and
    ``hal0 agent start`` brings it straight back. That instance is still the
    boundary this row reports on, so discovery also reads the install-state
    artifacts:

    * ``<unit_dir>/*.target.wants/hal0-agent@*.service`` — enable symlinks
    * ``/etc/hal0/agents/<i>.{toml,env}`` — per-instance config / env, written
      by provisioning

    Deliberately **not** the ``hal0-agent@<i>.service.d/`` drop-in dirs:
    ``installer/install.sh`` lays down ``hal0-agent@hermes.service.d/`` on every
    install "whether or not bootstrap has been run", so a box installed with the
    documented ``HAL0_SKIP_HERMES=1`` escape hatch has the drop-in and no agent.
    Counting it would warn about an agent the operator deliberately declined.

    Best effort: an unreadable directory contributes nothing rather than
    raising. Callers treat "found nothing anywhere" as no agent only when the
    systemd probe *also* answered cleanly.
    """
    root = unit_dir if unit_dir is not None else Path("/etc/systemd/system")
    names: set[str] = set()
    with contextlib.suppress(OSError):
        for wants in sorted(root.glob("*.target.wants")):
            names.update(p.name for p in wants.glob(_AGENT_UNIT_GLOB))
    with contextlib.suppress(OSError):
        for cfg in paths.agents_config_dir().iterdir():
            if cfg.suffix in (".toml", ".env") and cfg.stem:
                names.add(f"hal0-agent@{cfg.stem}.service")
    return sorted(names)


def _resolve_uid(user: str) -> int | None:
    """``User=`` value → numeric uid. Accepts a name or a numeric literal."""
    if user.isdigit():
        return int(user)
    try:
        return pwd.getpwnam(user).pw_uid
    except KeyError:
        return None


def check_agent_uid_isolation(
    *,
    unit_user: Callable[[str], str | None] | None = None,
    agent_units: Callable[[], list[str] | None] | None = None,
    resolve_uid: Callable[[str], int | None] | None = None,
) -> Check:
    """Does the bundled agent share ``hal0-api``'s UID? (ADR-0002, advisory.)

    ``hal0-api.service`` and ``hal0-agent@<instance>.service`` both ship with
    ``User=hal0``. Same UID means the agent — which is provisioned with
    ``terminal.backend = local`` and can therefore run shell commands — passes
    ``PTRACE_MODE_READ_FSCREDS`` against the API process and can read
    ``/proc/<pid>/environ``, i.e. every credential the API holds. Yama's
    ``ptrace_scope`` does not stop this; it gates ``PTRACE_MODE_ATTACH``, not
    same-UID reads. The ``hal0`` account additionally holds the sudo grants
    that author rootful podman slot specs, so it is root-equivalent by design
    (``installer/wrappers/hal0-systemctl``'s HONEST BOUNDARY block).

    This is the **shipped 1.0 default** and an accepted, documented risk, so
    the row is a ``warn``, never a ``fail``: it exists to make sure an operator
    reading ``hal0 doctor`` learns where the boundary is rather than inferring
    a boundary that is not there. ADR-0002 tracks the agent-UID split for 1.1.

    All three probes (unit discovery, ``User=`` lookup, uid resolution) are
    injectable so the classification is testable without systemd or a real
    passwd database (mirrors :func:`check_hal0_target`). Every "could not tell"
    answer warns rather than passing: a row whose failure mode is a silent
    all-clear would be worse than no row.
    """
    probe_user = unit_user if unit_user is not None else _unit_user
    probe_units = agent_units if agent_units is not None else _agent_units
    probe_uid = resolve_uid if resolve_uid is not None else _resolve_uid

    units = probe_units()
    if units is None:
        return Check(
            "agent-uid",
            "Agent UID split",
            _WARN,
            "could not enumerate hal0-agent@* units (systemd unavailable or not answering) — "
            "agent/API UID split unknown; see SECURITY.md 'Bundled agent trust boundary'",
        )
    if not units:
        return Check(
            "agent-uid",
            "Agent UID split",
            _PASS,
            "no bundled agent: no hal0-agent@* unit is installed on this box",
        )
    api_user = probe_user(_API_UNIT)
    if api_user is None:
        return Check(
            "agent-uid",
            "Agent UID split",
            _WARN,
            f"could not read User= for {_API_UNIT} — agent/API UID split unknown "
            "(see SECURITY.md 'Bundled agent trust boundary')",
        )
    api_uid = probe_uid(api_user)

    shared: list[str] = []
    unknown: list[str] = []
    for unit in units:
        agent_user = probe_user(unit)
        if agent_user is None:
            unknown.append(unit)
            continue
        # Compare resolved UIDs when both sides resolve: a drop-in may spell one
        # side numerically or via an alias account, and the kernel check this row
        # audits is on the UID, not on the string in the unit file. Fall back to
        # the strings only when a name does not resolve (e.g. a unit naming an
        # account that does not exist on this box).
        agent_uid = probe_uid(agent_user)
        same = (
            api_uid == agent_uid
            if (api_uid is not None and agent_uid is not None)
            else agent_user == api_user
        )
        if same:
            shared.append(unit)
    if shared:
        who = f"{api_user} (uid {api_uid})" if api_uid is not None else api_user
        return Check(
            "agent-uid",
            "Agent UID split",
            _WARN,
            f"{', '.join(shared)} runs as the same user as {_API_UNIT} — {who} — so the "
            "bundled agent's shell can read the API's /proc/<pid>/environ and every credential "
            "in it — accepted, documented 1.0 posture; see SECURITY.md 'Bundled agent trust "
            "boundary' (ADR-0002)",
        )
    if unknown:
        return Check(
            "agent-uid",
            "Agent UID split",
            _WARN,
            f"could not read User= for {', '.join(unknown)} — cannot claim the agent runs "
            "under its own UID; see SECURITY.md 'Bundled agent trust boundary'",
        )
    return Check(
        "agent-uid",
        "Agent UID split",
        _PASS,
        f"agent unit(s) run as a different user than {_API_UNIT} ({api_user})",
    )


#: The group that grants journal read; hal0-api.service carries it as
#: ``SupplementaryGroups=`` (installer/install.sh, #2435).
_JOURNAL_GROUP = "systemd-journal"


def _api_process_ids(proc_root: Path | None = None) -> tuple[int, set[int]] | None:
    """Real uid + supplementary gids of the running hal0-api process.

    Reads ``MainPID`` from systemd and then ``/proc/<pid>/status`` (world
    readable), so the answer reflects what the running process holds — a unit
    file rewritten without a restart, or an old unit, shows up as it is.
    ``None`` when it can't be told: no ``systemctl``, unit not running, or the
    status file unreadable.
    """
    if shutil.which("systemctl") is None:
        return None
    try:
        result = subprocess.run(  # nosec B603 B607
            ["systemctl", "show", "-p", "MainPID", "--value", _API_UNIT],
            check=False,
            capture_output=True,
            text=True,
            timeout=5,
        )
    except (OSError, subprocess.SubprocessError):
        return None
    pid = result.stdout.strip()
    if result.returncode != 0 or not pid.isdigit() or int(pid) == 0:
        return None
    root = proc_root if proc_root is not None else Path("/proc")
    try:
        status = (root / pid / "status").read_text(encoding="utf-8")
    except OSError:
        return None
    uid: int | None = None
    gids: set[int] = set()
    for line in status.splitlines():
        key, _, value = line.partition(":")
        if key == "Uid":
            fields = value.split()
            uid = int(fields[0]) if fields and fields[0].isdigit() else None
        elif key == "Groups":
            gids = {int(g) for g in value.split() if g.isdigit()}
    if uid is None:
        return None
    return uid, gids


def _journal_group_gid() -> int | None:
    """gid of ``systemd-journal`` on this host, or ``None`` if it has none."""
    try:
        return grp.getgrnam(_JOURNAL_GROUP).gr_gid
    except KeyError:
        return None


def check_api_journal_access(
    *,
    process_ids: Callable[[], tuple[int, set[int]] | None] | None = None,
    journal_gid: Callable[[], int | None] | None = None,
) -> Check:
    """Can hal0-api read the journal behind ``/api/logs`` and ``logs_tail``? (#2435)

    hal0-api runs ``User=hal0`` and gets journal read from
    ``SupplementaryGroups=systemd-journal`` on its own unit, written by the
    installer. ``hal0 update`` does not rewrite ``hal0-api.service``, so re-run
    the installer once after updating to 1.4.0; until then the API's log views
    are silently empty. Advisory (``warn``): nothing else breaks. Both probes
    are injectable for tests.
    """
    probe_ids = process_ids if process_ids is not None else _api_process_ids
    probe_gid = journal_gid if journal_gid is not None else _journal_group_gid
    key, title = "api-journal", "API journal read"

    # Probe the process first: a root-run API reads the journal without the
    # group, so a host that lacks it is not a problem for that API.
    ids = probe_ids()
    if ids is not None and ids[0] == 0:
        return Check(key, title, _PASS, f"{_API_UNIT} runs as root and can read the journal")
    gid = probe_gid()
    if gid is None:
        return Check(
            key,
            title,
            _WARN,
            f"this host has no {_JOURNAL_GROUP} group, so hal0-api cannot be granted "
            "journal read — /api/logs and MCP logs_tail return no lines",
        )
    if ids is None:
        return Check(
            key,
            title,
            _WARN,
            f"could not inspect the running {_API_UNIT} process (systemctl unavailable or "
            "unit not running) — journal read for /api/logs unknown",
        )
    _uid, gids = ids
    if gid in gids:
        return Check(key, title, _PASS, f"{_API_UNIT} can read the journal ({_JOURNAL_GROUP})")
    return Check(
        key,
        title,
        _WARN,
        f"{_API_UNIT} is not running with the {_JOURNAL_GROUP} group, so /api/logs and "
        "MCP logs_tail return no lines — re-run the installer once "
        "(curl -fsSL https://hal0.dev/install.sh | sudo bash); `hal0 update` does not "
        "rewrite hal0-api.service",
    )


def _moonshine_slots() -> tuple[dict[str, str], list[str]]:
    """Slots the launch path would run on the moonshine engine.

    Returns ``(users, unreadable)``: ``users`` maps slot name to the profile
    whose weights that slot's spawn enforces; ``unreadable`` lists slots whose
    config could not be loaded (usage is then unknown, not absent).

    Whether a slot uses moonshine is decided by the container dispatch
    authority (:func:`hal0.providers.container._spec_provider_for`), not a
    parallel heuristic: that covers profile-less ``type=transcription`` slots
    on cpu and custom profiles with ``runtime_family = "moonshine"``, and
    ignores the UI-only ``provider`` label (an npu slot routes to FLM
    whatever it says). The profile is the one ``MoonshineProvider`` itself
    resolves: the slot's ``profile``, else its default.
    """
    from hal0.config.loader import list_slots, load_slot_config
    from hal0.providers.container import _spec_provider_for
    from hal0.providers.moonshine import _DEFAULT_PROFILE, MoonshineProvider

    users: dict[str, str] = {}
    unreadable: list[str] = []
    for name in list_slots():
        try:
            cfg = load_slot_config(name).model_dump()
        except Exception:
            unreadable.append(name)
            continue
        try:
            provider = _spec_provider_for(cfg)
        except Exception:
            continue  # unknown family: not a moonshine launch
        if isinstance(provider, MoonshineProvider):
            users[name] = str(cfg.get("profile") or _DEFAULT_PROFILE)
    return users, unreadable


def _moonshine_profile_check(profile_name: str, slots: list[str]) -> Check:
    """Weights preflight for one moonshine profile used by ``slots``."""
    import shlex

    from hal0.errors import Hal0Error
    from hal0.profiles import ProfileCatalog
    from hal0.providers.moonshine import check_moonshine_weights

    label = f"profile {profile_name!r} (slot {', '.join(slots)})"
    try:
        profile = ProfileCatalog().resolve(profile_name)
    except Exception:
        return Check(
            "stt-weights",
            "Moonshine weights",
            _WARN,
            f"{label} cannot be resolved — slot start will fail until it exists",
        )
    tokens = shlex.split(profile.resolved_flags or "")
    model_path = ""
    for i, tok in enumerate(tokens[:-1]):
        if tok == "--model_path":
            model_path = tokens[i + 1]
    if not model_path:
        return Check(
            "stt-weights",
            "Moonshine weights",
            _WARN,
            f"no --model_path in {label} — first slot start will "
            "auto-download from HuggingFace inside the container (slow, needs egress)",
        )
    try:
        check_moonshine_weights(model_path)
    except Hal0Error as exc:
        return Check("stt-weights", "Moonshine weights", _FAIL, f"{label}: {exc}")
    return Check(
        "stt-weights",
        "Moonshine weights",
        _PASS,
        f"staged bundle present at {model_path}",
    )


def check_voice_stt_weights() -> Check:
    """Moonshine STT weights preflight — the same rule slot spawn enforces.

    Only runs when a slot actually uses moonshine (#2437): the seeded
    ``moonshine`` profile exists on every install, so keying off the profile
    alone put a red FAIL on every fresh box that never enabled local STT.
    Usage is decided by the container dispatch authority and the profile
    checked is the one the provider would resolve (see
    :func:`_moonshine_slots`).

    The moonshine ONNX bundle is operator-staged (multi-file, not registry-
    pulled), so a fresh box can configure the stt slot and only find out the
    weights are missing when the container 500s. This row runs
    :func:`hal0.providers.moonshine.check_moonshine_weights` against the
    profile-baked ``--model_path`` so the gap is named here first. An empty
    ``--model_path`` is the in-container HuggingFace auto-download path —
    legitimate but slow on first start, so it warns rather than fails.
    A slot config the doctor cannot read leaves usage unknown, which warns.
    """
    try:
        users, unreadable = _moonshine_slots()
    except Exception:
        users, unreadable = {}, []
    by_profile: dict[str, list[str]] = {}
    for slot, profile_name in users.items():
        by_profile.setdefault(profile_name, []).append(slot)
    results = [_moonshine_profile_check(p, s) for p, s in by_profile.items()]
    rank = {_PASS: 0, _WARN: 1, _FAIL: 2}
    if unreadable:
        results.append(
            Check(
                "stt-weights",
                "Moonshine weights",
                _WARN,
                f"could not read slot config for {', '.join(unreadable)} — cannot tell "
                "whether a slot uses the moonshine STT engine",
            )
        )
    if not results:
        return Check(
            "stt-weights",
            "Moonshine weights",
            _PASS,
            "no slot uses the moonshine STT engine — nothing to preflight",
        )
    worst = max(results, key=lambda c: rank.get(c.status, 0))
    if worst.status == _PASS:
        return results[0]
    flagged = [c.detail for c in results if c.status == worst.status]
    return Check("stt-weights", "Moonshine weights", worst.status, "; ".join(flagged))


def check_model_store(
    models: Any,
    *,
    exists: Any = None,
) -> Check:
    """Registry entries whose file is gone → a non-critical fail.

    Mirrors ``doctor models`` step 1 (registry → file existence) but as a
    single roll-up row. ``exists`` is an injectable seam (defaults to
    ``Path.exists``) so the classifier is testable without touching disk.
    """
    _exists = exists if exists is not None else (lambda p: Path(p).exists())
    if models is None:
        return Check("models", "Model store", _WARN, "models endpoint unreachable")
    rows = models.get("models", models) if isinstance(models, dict) else models
    if not isinstance(rows, list):
        return Check("models", "Model store", _WARN, "unexpected models payload")
    local = [m for m in rows if isinstance(m, dict) and m.get("path")]
    dangling = [m for m in local if not _exists(str(m["path"]))]
    if dangling:
        names = ", ".join(str(m.get("id") or m.get("path")) for m in dangling[:3])
        more = f" (+{len(dangling) - 3} more)" if len(dangling) > 3 else ""
        return Check(
            "models",
            "Model store",
            _FAIL,
            f"{len(dangling)} registry entr(y/ies) point at missing files: {names}{more} "
            "— run `hal0 doctor models` to triage",
        )
    return Check("models", "Model store", _PASS, f"{len(local)} registered file(s) present")


def check_leftover_registry_toml(toml_file: Path | None = None) -> Check:
    """A TOML-era ``registry.toml`` the migrations could not retire (warn).

    Nothing reads the file any more, so it is never fatal — but a file the
    daemon cannot back up or rename (a root-owned ``0600`` one, say) would
    otherwise sit there silently. See :mod:`hal0.registry.toml_retirement`.
    """
    from hal0.registry.toml_retirement import registry_toml_path, retirement_blocker

    path = toml_file if toml_file is not None else registry_toml_path()
    reason = retirement_blocker(path)
    if reason is None:
        return Check("registry-toml", "Leftover registry.toml", _PASS, "none")
    return Check(
        "registry-toml",
        "Leftover registry.toml",
        _WARN,
        f"leftover registry.toml could not be retired: {reason} — nothing reads it; "
        f"remove it manually (`sudo rm {path}`)",
    )


def _read_env_value(
    env_path: Path, key: str, *, read_text: Callable[[Path], str] | None = None
) -> str | None:
    """Best-effort ``KEY=value`` read from an ``EnvironmentFile``-style file.

    Mirrors the line convention :mod:`hal0.api._env_store` writes (a plain
    or double-quoted ``KEY=value``, one per line — see ``_line_targets_key``).
    A commented-out line (``# KEY=value``) does not count as set. Returns
    ``None`` when the file, or the key inside it, is absent — never raises.
    """
    _read = read_text if read_text is not None else (lambda p: p.read_text(encoding="utf-8"))
    try:
        text = _read(env_path)
    except OSError:
        return None
    prefix = f"{key}="
    for line in text.splitlines():
        stripped = line.strip()
        if stripped.startswith(prefix):
            value = stripped[len(prefix) :]
            if len(value) >= 2 and value[0] == value[-1] == '"':
                value = value[1:-1]
            return value
    return None


def check_ui_dist(
    *, api_env_path: Path | None = None, current_ui_dist: Path | None = None
) -> Check:
    """Compare the effective dashboard bundle against the current release.

    ``HAL0_UI_DIST`` in ``api.env`` is installer-owned wiring, not operator
    config — on an FHS box its only correct value is ``<current>/ui/dist``
    (see :func:`hal0.config.paths.usr_lib`). The installer only *writes* that
    line when ``api.env`` doesn't exist yet (fresh install); on every
    subsequent run it preserves whatever sits outside the marker-delimited
    network block, by design, so an operator edit elsewhere in the file
    survives. That same preservation means a box that ever had
    ``HAL0_UI_DIST`` pointed at an older layout (e.g. a pre-FHS git-deploy
    tree) silently stops receiving UI updates on every future upgrade: the
    updater stages a fresh bundle under the new release, and the dashboard
    keeps serving the stale one against the new API (#1589).

    No override at all is the common, healthy case (the FastAPI mount falls
    through to ``<current>/ui/dist`` on its own — see
    ``hal0.api._mount_dashboard``) and passes silently.
    """
    api_env_path = api_env_path if api_env_path is not None else paths.api_env()
    expected = current_ui_dist if current_ui_dist is not None else (paths.usr_lib() / "ui" / "dist")

    override = _read_env_value(api_env_path, "HAL0_UI_DIST")
    if not override:
        return Check(
            "ui-dist",
            "Dashboard bundle",
            _PASS,
            "no HAL0_UI_DIST override in api.env — tracks the current release bundle",
        )

    override_path = Path(override)
    try:
        matches = override_path.resolve() == expected.resolve()
    except OSError:
        matches = override_path == expected
    if matches:
        return Check(
            "ui-dist",
            "Dashboard bundle",
            _PASS,
            f"HAL0_UI_DIST={override} matches the current release bundle",
        )
    return Check(
        "ui-dist",
        "Dashboard bundle",
        _WARN,
        f"HAL0_UI_DIST={override} in api.env does not resolve inside {expected} (the current "
        "release bundle) — the dashboard may be serving a stale UI against the new API. Remove "
        f"the override (or point it at {expected}) in api.env, then restart hal0-api.",
    )


def _hal0_target_enabled_probe() -> bool | None:
    """Real ``systemctl is-enabled --quiet hal0.target`` probe.

    Returns ``True``/``False`` for a definitive answer, ``None`` when the
    question can't be asked at all (no ``systemctl`` on PATH, e.g. a
    container/CI box) — the caller degrades that to an advisory warn rather
    than a fail. Kept as a free function (not inlined) so tests can swap it
    out via the ``is_enabled`` seam without touching subprocess.
    """
    if shutil.which("systemctl") is None:
        return None
    try:
        result = subprocess.run(  # nosec B603 B607
            ["systemctl", "is-enabled", "--quiet", "hal0.target"],
            check=False,
            timeout=5,
        )
    except OSError:
        return None
    return result.returncode == 0


def check_hal0_target(
    *,
    unit_dir: Path | None = None,
    exists: Callable[[Path], bool] | None = None,
    is_enabled: Callable[[], bool | None] | None = None,
) -> Check:
    """``hal0.target`` — the boot-enable anchor every slot Quadlet depends on.

    Every rendered per-slot Quadlet declares
    ``[Install] WantedBy=hal0.target`` (``providers/container.py``); if the
    target unit is missing, or installed but not enabled, slots that looked
    healthy before a reboot silently stay down after one
    (r5-sync-assessment §6.1, launch-blocker #1). ``exists``/``is_enabled``
    are injectable seams (mirrors ``check_model_store``'s ``exists`` param)
    so this is testable without a real systemd or filesystem.
    """
    _unit_dir = unit_dir if unit_dir is not None else Path("/etc/systemd/system")
    _exists = exists if exists is not None else (lambda p: p.exists())
    unit_path = _unit_dir / "hal0.target"
    if not _exists(unit_path):
        return Check(
            "hal0_target",
            "hal0.target",
            _FAIL,
            f"{unit_path} not installed — slots will not autostart after reboot; "
            "re-run the installer (sudo bash install.sh) to ship it",
        )
    _is_enabled = is_enabled if is_enabled is not None else _hal0_target_enabled_probe
    enabled = _is_enabled()
    if enabled is False:
        return Check(
            "hal0_target",
            "hal0.target",
            _FAIL,
            "hal0.target installed but not enabled — run `sudo systemctl enable --now hal0.target`",
        )
    if enabled is None:
        return Check(
            "hal0_target",
            "hal0.target",
            _WARN,
            "hal0.target installed — enabled state unknown (systemctl unavailable)",
        )
    return Check("hal0_target", "hal0.target", _PASS, "installed and enabled")


def check_seams(statuses: list[SeamStatus] | None = None) -> Check:
    """The privileged sudo seams every slot op and self-update runs through (#1465).

    ``install.sh`` installs each wrapper + ``/etc/sudoers.d`` grant best-effort:
    a ``visudo -cf`` failure or a missing source produced only a mid-log warn,
    and *nothing* verified the result afterwards — so a box where that warn
    fired reported all-green from every doctor surface while every slot start,
    unit write and daemon-reload failed undiagnosably. This row is that missing
    verification.

    Required seams (``hal0-systemctl``, ``hal0-update``) failing is a non-critical
    ``fail``; an optional seam, or a grant we could not test from this account,
    is an advisory ``warn``. ``statuses`` is injectable so the classification is
    testable without root or a provisioned box (mirrors ``check_hal0_target``).
    """
    rows = probe_seams() if statuses is None else statuses
    if not rows:
        return Check("seams", "Privileged seams", _WARN, "no seams to check")

    broken_required = [s for s in rows if s.spec.required and not s.ok]
    if broken_required:
        problems = "; ".join(p for s in broken_required for p in s.problems)
        roles = ", ".join(s.spec.role for s in broken_required)
        return Check(
            "seams",
            "Privileged seams",
            _FAIL,
            f"{problems} — {roles} cannot work; {REMEDIATION}",
        )

    broken_optional = [s for s in rows if not s.spec.required and not s.ok]
    if broken_optional:
        problems = "; ".join(p for s in broken_optional for p in s.problems)
        return Check("seams", "Privileged seams", _WARN, f"{problems} — {REMEDIATION}")

    untested = [s for s in rows if s.spec.probe is not None and s.grant_ok is None]
    if untested:
        names = ", ".join(s.spec.name for s in untested)
        return Check(
            "seams",
            "Privileged seams",
            _WARN,
            f"{len(rows)} seam(s) installed; grant not exercised for {names} "
            "(re-run as root to prove it end-to-end)",
        )

    proved = [s for s in rows if s.grant_ok is True]
    return Check(
        "seams",
        "Privileged seams",
        _PASS,
        f"{len(rows)} installed root:root; {len(proved)} grant(s) verified via sudo -n",
    )


def check_ports(slots: Any) -> Check:
    """Bound slot ports (informational evidence, always advisory-clean).

    Surfaces the ports hal0's slots currently occupy so a port-collision
    diagnosis has the evidence in the same report. Never fails — a box with no
    slots yet is legal.

    Two distinct non-answers are kept apart (#1501). ``None`` means the fetch
    itself produced nothing — which covers a genuinely down API *and* a healthy
    one that simply outran the request budget, so the copy must not assert
    unreachability. A non-list body means the route answered with a shape this
    check doesn't understand, which is a contract fault, not a connectivity
    one. Collapsing both into "unreachable" is what made this row cry wolf on a
    box serving all 18 slots.
    """
    if slots is None:
        return Check(
            "ports",
            "Slot ports",
            _WARN,
            "no answer from the slots endpoint (down, or slower than the probe budget) "
            "— run `hal0 doctor ports`",
        )
    if not isinstance(slots, list):
        return Check(
            "ports",
            "Slot ports",
            _WARN,
            f"unexpected slots payload: {type(slots).__name__}, expected a list "
            "— run `hal0 doctor ports`",
        )
    bound = sorted({int(s["port"]) for s in slots if isinstance(s, dict) and s.get("port")})
    if not bound:
        return Check("ports", "Slot ports", _PASS, "no slot ports bound yet")
    return Check("ports", "Slot ports", _PASS, f"{len(bound)} bound: {', '.join(map(str, bound))}")


def check_port_dnat(slots: Any, audit: Callable[..., Any] | None = None) -> Check:
    """Stale netavark DNAT rules on published slot ports (#1814).

    A container that dies without netavark's teardown running leaves its DNAT
    rule behind in ``inet netavark``. nftables is first-match, so that dead rule
    permanently shadows the live one and the port black-holes even though the
    container is healthy. Two heuristic-free signals — more than one DNAT rule
    for a port, or a first match pointing at an IP no running container holds.

    ``warn``, not ``fail``, when the audit can't run at all (no netavark table,
    no ``nft``, no ``podman``, or the slots payload never arrived): the absence
    of evidence is not evidence of corruption. Real corruption is a ``fail``
    and names ``hal0 doctor ports --fix``.
    """
    label = "Port DNAT rules"
    if not isinstance(slots, list):
        return Check("port_dnat", label, _WARN, "no slots payload — run `hal0 doctor ports`")
    ports = sorted({int(s["port"]) for s in slots if isinstance(s, dict) and s.get("port")})
    if not ports:
        return Check("port_dnat", label, _PASS, "no slot ports bound yet")

    from hal0.system import netavark

    try:
        rules = netavark.read_dnat_rules()
        live = netavark.read_live_container_ips()
    except (netavark.NetavarkUnavailable, OSError, subprocess.SubprocessError) as exc:
        return Check("port_dnat", label, _WARN, f"audit unavailable: {exc}")

    verdicts = (audit or netavark.audit_ports)(ports, rules, live)
    corrupt = [v for v in verdicts if v.corrupt]
    if not corrupt:
        return Check("port_dnat", label, _PASS, f"{len(ports)} port(s) clean in the netavark table")
    stale = sum(len(v.stale) for v in corrupt)
    names = ", ".join(str(v.port) for v in corrupt)
    return Check(
        "port_dnat",
        label,
        _FAIL,
        f"{stale} stale DNAT rule(s) black-holing port(s) {names} "
        "— repair with `hal0 doctor ports --fix`",
    )


def check_netns_durability(probe: Callable[[], Any] | None = None) -> Check:
    """Can container network namespaces survive a root login/logout cycle? (#1814)

    This is the *source* of the stale-DNAT leak the row above detects. podman
    keeps each container's netns as a bind mount under ``/run/user/0/netns``,
    which logind unmounts when root's last session ends unless root lingers.
    Every container running at that moment is then unrepairable: its teardown
    fails and leaks a DNAT rule that black-holes the port.

    ``fail`` when a netns is ALREADY dangling (the leak is guaranteed on the
    next stop), ``warn`` when linger is merely off but nothing is stranded yet,
    ``pass`` otherwise.
    """
    label = "Container netns"
    from hal0.system import netavark

    try:
        state = (probe or netavark.read_netns_durability)()
    except (netavark.NetavarkUnavailable, OSError, subprocess.SubprocessError) as exc:
        return Check("netns", label, _WARN, f"probe unavailable: {exc}")

    if not state.sandbox_keys:
        return Check("netns", label, _PASS, "no running containers to check")
    dangling = state.dangling
    if dangling:
        return Check(
            "netns",
            label,
            _FAIL,
            f"{len(dangling)} running container(s) have a stranded netns — their next stop "
            "WILL leak a DNAT rule. Fix: `loginctl enable-linger root`, then restart the slots",
        )
    if state.volatile:
        return Check(
            "netns",
            label,
            _WARN,
            "netns live under /run/user/0 and root linger is off — a root logout will "
            "strand them. Fix: `loginctl enable-linger root`",
        )
    return Check("netns", label, _PASS, f"{len(state.sandbox_keys)} netns durable")


def _default_mcp_probe_targets() -> tuple[tuple[str, str], ...]:
    """(name, mount-root URL) pairs for hal0's two bundled MCP servers."""
    return (
        ("hal0-admin", "http://127.0.0.1:8080/mcp/admin"),
        ("hal0-memory", "http://127.0.0.1:8080/mcp/memory"),
    )


def probe_builtin_mcp_mounts(
    probe: Callable[..., dict[str, Any]] | None = None,
) -> dict[str, dict[str, Any]]:
    """Run the real MCP ``initialize`` → ``tools/list`` handshake against
    both bundled mounts, using the box service key the same way a
    provisioned Hermes config.yaml does.

    Reuses :func:`hal0.agents.hermes_provision._probe_mcp_server` — the same
    initialize/notifications/tools-list sequence + bearer resolution a real
    provision run already exercises — rather than re-implementing MCP
    transport here. Late import keeps a bad import in the (much larger)
    provisioning module from taking down `hal0 doctor`.
    """
    if probe is None:
        from hal0.agents.hermes_provision import _probe_mcp_server as probe

    return {
        name: probe(url, agent_id="hal0-doctor", private=(name == "hal0-memory"))
        for name, url in _default_mcp_probe_targets()
    }


def check_mcp_mounts(results: dict[str, dict[str, Any]] | None = None) -> Check:
    """Live regression guard for the silent-401 class (§ MCP bearer wiring).

    A provisioned box can look completely healthy — config.yaml has the
    right shape, the daemon is up — and still have every hermes-issued MCP
    call silently 401 (bearer never resolved at provision time, a key
    rotated since, or the ADR-0013 allow-list quietly filtering the builtins
    out). This is the row that actually dials both mounts and proves the
    handshake completes under the box's CURRENT auth posture, instead of
    trusting that a static config implies a working connection.

    ``results`` is injectable (mirrors ``check_hal0_target``/``check_seams``)
    so the classifier is testable without a live API. PASS requires BOTH
    mounts to answer ``tools/list`` with a non-empty tool list; a 401/403 is
    called out by name with the exact repair command, other transport
    failures (connection refused, timeout, 5xx) get their raw error text.
    """
    rows = results if results is not None else probe_builtin_mcp_mounts()
    failures: list[str] = []
    for name, probe in rows.items():
        if not probe.get("ok"):
            err = str(probe.get("error") or "unreachable")
            if any(marker in err for marker in ("401", "403", "Unauthorized", "Forbidden")):
                failures.append(
                    f"{name}: {err} — agent token missing/stale: "
                    "run `hal0 agent bootstrap hermes --repair`"
                )
            else:
                failures.append(f"{name}: {err}")
        elif not probe.get("tools"):
            failures.append(f"{name}: reachable but advertised zero tools")
    if failures:
        return Check("mcp_mounts", "MCP mounts (admin/memory)", _FAIL, "; ".join(failures))
    tool_counts = ", ".join(f"{n}={len(p.get('tools') or [])}" for n, p in rows.items())
    return Check(
        "mcp_mounts",
        "MCP mounts (admin/memory)",
        _PASS,
        f"both mounts completed initialize -> tools/list ({tool_counts})",
    )


def check_hermes_mcp_config_auth(
    *,
    auth: dict[str, Any] | None,
    config_path: Path | None = None,
    read_text: Callable[[Path], str] | None = None,
) -> Check:
    """Hermes' rendered ``config.yaml`` must carry ``Authorization`` on both
    ``mcp_servers`` entries whenever the box requires auth.

    Static counterpart to :func:`check_mcp_mounts`: that row proves the LIVE
    handshake works right now (as the box's own service key, from wherever
    `hal0 doctor` runs); this one proves the specific artifact
    ``hal0 agent bootstrap hermes`` renders is what actually carries the
    bearer forward into every Hermes-issued call — the drift class where
    ``HAL0_ADMIN_KEY`` was set or rotated *after* the last provision/repair
    pass, so the live config.yaml is stale even though the box's current key
    would probe clean.

    A missing ``config.yaml`` (hermes never provisioned) passes — nothing to
    check yet. An auth-open box passes unconditionally — a tokenless
    config.yaml is exactly the expected shape (see
    ``test_overlay_omits_bearer_when_no_key_discoverable``).
    """
    from hal0.agents.hermes_provision import HERMES_HOME_DEFAULT

    path = config_path if config_path is not None else (HERMES_HOME_DEFAULT / "config.yaml")
    _read = read_text if read_text is not None else (lambda p: p.read_text(encoding="utf-8"))
    if not path.exists():
        return Check(
            "hermes_mcp_auth",
            "Hermes MCP config auth",
            _PASS,
            "hermes not provisioned — nothing to check",
        )
    if not bool((auth or {}).get("auth_required")):
        return Check(
            "hermes_mcp_auth",
            "Hermes MCP config auth",
            _PASS,
            "auth not required — a tokenless config.yaml is expected",
        )
    try:
        text = _read(path)
    except OSError as exc:
        return Check(
            "hermes_mcp_auth", "Hermes MCP config auth", _WARN, f"could not read {path}: {exc}"
        )

    import yaml

    try:
        data = yaml.safe_load(text) or {}
    except yaml.YAMLError as exc:
        return Check(
            "hermes_mcp_auth", "Hermes MCP config auth", _WARN, f"could not parse {path}: {exc}"
        )
    servers = data.get("mcp_servers") if isinstance(data, dict) else None
    servers = servers if isinstance(servers, dict) else {}
    missing = [
        name
        for name in ("hal0-admin", "hal0-memory")
        if isinstance(servers.get(name), dict)
        and not ((servers[name].get("headers") or {}).get("Authorization"))
    ]
    if missing:
        return Check(
            "hermes_mcp_auth",
            "Hermes MCP config auth",
            _FAIL,
            f"auth is required but {', '.join(missing)} carries no Authorization header in "
            f"{path} — run `hal0 agent bootstrap hermes --repair`",
        )
    return Check(
        "hermes_mcp_auth",
        "Hermes MCP config auth",
        _PASS,
        "config.yaml carries Authorization for both bundled mounts",
    )


def _header(server: dict[str, Any], name: str) -> Any:
    """``server.headers[name]`` when ``headers`` is a mapping, else ``None``."""
    headers = server.get("headers")
    return headers.get(name) if isinstance(headers, dict) else None


def check_hermes_profiles_mcp(
    *,
    auth: dict[str, Any] | None,
    home: Path | None = None,
) -> Check:
    """Every named Hermes profile carries hal0's MCP servers, with a bearer when
    the box requires auth (#2891, ADR-0035).

    A profile cloned by the operator starts with no ``mcp_servers``; hal0-api
    wires hal0-admin + hal0-memory into it on every reconcile sweep
    (``hermes_upstreams.sync_hermes_upstreams``), so a profile still unwired is
    a WARN naming the sweep. An entry without ``Authorization`` on an auth-on
    box is a FAIL like the default profile's row: Hermes would hang on the
    401 at every discovery pass. ``hal0: {wire_mcp: false}`` opts a profile
    out and is reported as such. ADVISORY (never critical).
    """
    from hal0.agents import hermes_api as ha
    from hal0.agents import hermes_provision as hp
    from hal0.agents.hermes_provision import PROFILE_MCP_OPT_OUT_KEY

    key, label = "hermes_profiles_mcp", "Hermes profile MCP wiring"
    root = home if home is not None else ha.hermes_home()
    profiles = root / "profiles"
    # Hermes chmods its home 0700: a non-hal0 operator cannot see the profiles
    # (``is_dir()`` is simply False then). Say so instead of a green "no
    # profiles" (same posture as the hermes-api row).
    unreadable_msg = f"cannot read {profiles}; run hal0 doctor as hal0 or root"
    if os.path.islink(root) or os.path.islink(profiles):
        return Check(
            key,
            label,
            _WARN,
            f"{root if os.path.islink(root) else profiles} is a symlink; hal0 wires profiles "
            "only through a real Hermes home",
        )
    if os.path.lexists(root) and not os.access(root, os.R_OK | os.X_OK):
        return Check(key, label, _WARN, unreadable_msg)
    if os.path.lexists(profiles) and not os.access(profiles, os.R_OK | os.X_OK):
        return Check(key, label, _WARN, unreadable_msg)
    linked: list[str] = []
    try:
        entries = []
        for p in sorted(profiles.iterdir(), key=lambda p: p.name):
            if p.is_symlink():  # the writer never follows it: unmanaged, say so
                if p.name != "hal0-brain" and hp.HERMES_PROFILE_ID_RE.match(p.name):
                    linked.append(p.name)
                continue
            if p.is_dir():
                entries.append(p.name)
    except FileNotFoundError:
        entries = []
    except OSError as exc:  # enumeration itself failed: never a green "no profiles"
        return Check(key, label, _WARN, f"cannot list {profiles}: {exc}")
    names = [n for n in entries if n != "hal0-brain" and hp.HERMES_PROFILE_ID_RE.match(n)]
    if not names and not linked:
        return Check(key, label, _PASS, "no named Hermes profiles")
    auth_required = bool((auth or {}).get("auth_required"))

    import yaml

    unwired: list[str] = []
    no_config: list[str] = []
    unauthed: list[str] = []
    drifted: list[str] = []
    opted_out: list[str] = []
    unreadable: list[str] = []
    wired: list[str] = []
    for name in names:
        cfg = root / "profiles" / name / "config.yaml"
        # Opened once, without following links or blocking on a FIFO, checked
        # with fstat and read from that same descriptor, as the writer does.
        try:
            fd = os.open(cfg, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
        except FileNotFoundError:
            no_config.append(name)  # the writer skips it too: Hermes owns creation
            continue
        except OSError:
            unreadable.append(name)
            continue
        try:
            with os.fdopen(fd, "rb") as fh:
                if not stat.S_ISREG(os.fstat(fh.fileno()).st_mode):
                    unreadable.append(name)
                    continue
                data = yaml.safe_load(fh.read().decode("utf-8"))
        except (OSError, UnicodeDecodeError, yaml.YAMLError):
            unreadable.append(name)
            continue
        if data is None:  # an empty file is an empty mapping
            data = {}
        if not isinstance(data, dict):  # ``[]`` / ``false``: the writer refuses these too
            unreadable.append(name)
            continue
        servers = data.get("mcp_servers")
        servers = servers if isinstance(servers, dict) else {}
        block = data.get("hal0")
        if isinstance(block, dict) and block.get(PROFILE_MCP_OPT_OUT_KEY) is False:
            # Opt-out means hands off: hal0 neither adds nor removes. Say what
            # the profile still carries, so a revoke is a deliberate hand edit.
            kept = [n for n in ("hal0-admin", "hal0-memory") if isinstance(servers.get(n), dict)]
            opted_out.append(f"{name} (still carries {', '.join(kept)})" if kept else name)
            continue
        missing = [n for n in ("hal0-admin", "hal0-memory") if not isinstance(servers.get(n), dict)]
        if missing:
            unwired.append(name)
            continue
        # The identity and target must be the ones hal0 wires: a copied
        # X-hal0-Agent would read another profile's private memory, a missing
        # X-hal0-Private would write to the shared bank. The bearer must be the
        # box's current service key when one is discoverable (a rotated key
        # leaves a stale one behind), else at least a ``Bearer …`` string.
        desired = hp._profile_wire_desired(hp.profile_agent_id(name))
        if auth_required and any(
            not _bearer_ok(
                _header(servers[n], "Authorization"), want["headers"].get("Authorization")
            )
            for n, want in desired["mcp_servers"].items()
        ):
            unauthed.append(name)
            continue
        memory = data.get("memory")
        provider = memory.get("provider") if isinstance(memory, dict) else None
        if provider != desired["memory"]["provider"] or any(
            servers[n].get("type") != want["type"]
            or servers[n].get("url") != want["url"]
            or servers[n].get("timeout") != want["timeout"]
            or any(
                _header(servers[n], h) != v
                for h, v in want["headers"].items()
                if h != "Authorization"
            )
            for n, want in desired["mcp_servers"].items()
        ):
            drifted.append(name)
            continue
        wired.append(name)

    parts: list[str] = []
    if unauthed:
        parts.append(
            f"hal0 MCP entries in profile(s) {', '.join(unauthed)} carry no usable or a stale "
            "Authorization bearer; Hermes fails the 401 at discovery; run "
            "POST /api/agents/hermes/reconcile-upstreams or sudo hal0 agent reprovision hermes --repair"
        )
    if unwired:
        parts.append(
            f"profile(s) {', '.join(unwired)} not wired to hal0-admin/hal0-memory yet; "
            "hal0-api wires them within 120 s, or POST /api/agents/hermes/reconcile-upstreams"
        )
    if drifted:
        parts.append(
            f"profile(s) {', '.join(drifted)} carry hal0 MCP entries that differ from hal0's "
            "wiring (identity, target or memory provider); the next hal0-api sweep rewrites them"
        )
    if unreadable:
        parts.append(
            f"config.yaml of profile(s) {', '.join(unreadable)} is unreadable or not a mapping; "
            "hal0 leaves it alone, fix it by hand"
        )
    if no_config:
        parts.append(
            f"profile(s) {', '.join(no_config)} have no config.yaml; hal0 wires a profile once "
            "Hermes creates its config"
        )
    if linked:
        parts.append(
            f"profile(s) {', '.join(linked)} are symlinks; hal0 never wires or repairs a linked "
            "profile, replace the link with a real directory"
        )
    if unauthed:
        return Check(key, label, _FAIL, "; ".join(parts))
    if unwired or drifted or unreadable or linked:
        return Check(key, label, _WARN, "; ".join(parts))
    detail = f"wired: {', '.join(wired)}" if wired else "no profile wired"
    if opted_out:
        detail += f"; opted out: {', '.join(opted_out)}"
    if parts:  # only the informational no-config note is left
        detail += "; " + "; ".join(parts)
    return Check(key, label, _PASS, detail)


def _bearer_ok(value: Any, wanted: Any) -> bool:
    """``value`` is the bearer hal0 would write (``wanted``), or a ``Bearer …`` string when
    no service key is discoverable from where doctor runs."""
    if not isinstance(value, str) or not value.startswith("Bearer ") or len(value) <= 7:
        return False
    return value == wanted if isinstance(wanted, str) else True


def check_hermes_generation_extras(*, home: Path | None = None, venv: Path | None = None) -> Check:
    """A committed pm generation for the live release must carry its extras (#2891).

    Hermes's package manager can stage its own environment for a checkout and
    boot every later process into it instead of the release venv hal0 built
    (``hermes_platforms.committed_generation``). That generation starts with
    no extras, so a gateway booted from one runs without ``mcp`` and has no
    hal0 tools, silently. No generation committed means the gateway runs from
    the release venv, which is the converged state. ADVISORY.
    """
    from hal0.agents import hermes_api as ha
    from hal0.agents import hermes_platforms as hpl
    from hal0.agents import hermes_provision as hp
    from hal0.agents import hermes_release as rel

    key, label = "hermes_generation", "Hermes pm generation"
    root = home if home is not None else ha.hermes_home()
    layout = rel.Layout.for_venv(venv if venv is not None else hp.HERMES_VENV_DEFAULT)
    try:
        release = rel.live_release(layout)
    except OSError:
        release = None
    if release is None:
        if rel._link_release_id(layout.current):
            return Check(
                key,
                label,
                _WARN,
                f"{layout.current} names a release whose release.json cannot be read; "
                "run sudo hal0 agent reprovision hermes --repair",
            )
        return Check(key, label, _PASS, "no live Hermes release")
    # ``committed_generation`` answers None for an unreadable install state too;
    # across a 0700 home that is not proof that no generation is committed.
    installs = root / "installs"
    for probe in (root, installs):
        if os.path.lexists(probe) and not os.access(probe, os.R_OK | os.X_OK):
            return Check(key, label, _WARN, f"cannot read {probe}; run hal0 doctor as hal0 or root")
    try:
        generation = hpl.committed_generation(root, release.src)
    except OSError:
        generation = None
    if generation is None:
        return Check(
            key, label, _PASS, "gateway runs from the release venv (no generation committed)"
        )
    # ``extras`` is everything the release installed; ``platform_extras`` the
    # gateway platforms' share of it. Union both in case a writer records only one.
    wanted: set[str] = set()
    for field in ("extras", "platform_extras"):
        value = release.data.get(field)
        if isinstance(value, list):
            wanted.update(e for e in value if isinstance(e, str))
    have = set(hpl.generation_extras(root, release.src))
    missing = sorted(wanted - have - hpl.NEVER_CARRY)
    if missing:
        return Check(
            key,
            label,
            _WARN,
            f"the committed generation {generation.name} lacks extra(s) {', '.join(missing)} the "
            f"release has; a gateway booted from it has no {'/'.join(missing)} tools; run "
            "sudo hal0 agent reprovision hermes --repair",
        )
    return Check(key, label, _PASS, f"generation {generation.name} carries the release extras")


def check_hermes_command_approvals(
    *,
    status_fn: Callable[[], dict[str, Any]] | None = None,
    config_path: Path | None = None,
) -> Check:
    """Hermes' rendered ``approvals.mode`` must match the operator's choice.

    ADVISORY (warn, never fail). The setting lives in the root-owned seed TOML
    (``hal0 agent command-approvals``), but Hermes reads ``approvals.mode`` from
    its own ``config.yaml`` under ``HERMES_HOME``, which the ``hal0`` user owns.
    A hand edit, or a Hermes agent with the terminal tool on, can change that
    file directly (for example to ``off``) without any API call, so drift is
    worth surfacing. An ``approvals.mode`` in overrides.yaml is the expected
    value when it is set.
    """
    from hal0.agents import hermes_command_approvals as cmd
    from hal0.agents.hermes_provision import HERMES_HOME_DEFAULT

    name, label = "hermes_command_approvals", "Hermes command approvals"
    path = config_path if config_path is not None else (HERMES_HOME_DEFAULT / "config.yaml")
    if not path.exists():
        return Check(name, label, _PASS, "hermes not provisioned — nothing to check")
    try:
        st = (status_fn or cmd.status)()
    except Exception as exc:  # a broken read must not crash the roll-up
        return Check(name, label, _WARN, f"could not read the setting: {exc}")
    if st.get("seed_unreadable"):
        return Check(
            name,
            label,
            _WARN,
            f"{st.get('seed_path')} could not be read or parsed, so the default "
            f"({st.get('default')}) is in use — fix the file or run "
            "`hal0 agent reprovision hermes --repair`",
        )
    if st.get("invalid_value"):
        return Check(
            name,
            label,
            _WARN,
            f"stored value {st['invalid_value']!r} is invalid; using the default "
            f"({st.get('default')}) — run `hal0 agent command-approvals smart|manual`",
        )
    rendered, effective = st.get("rendered"), st.get("effective")
    if rendered is None:
        return Check(
            name,
            label,
            _PASS,
            f"could not read approvals.mode from {path} — run as root to check it",
        )
    if st.get("in_sync") is False:
        fix = (
            "run `hal0 agent reprovision hermes` to re-apply overrides.yaml"
            if st.get("source") == "overrides"
            else f"run `hal0 agent command-approvals {effective}` to re-apply it"
        )
        return Check(
            name,
            label,
            _WARN,
            f"config.yaml has approvals.mode {rendered!r} but {effective!r} is expected "
            f"(source: {st.get('source')}); a hand edit or a terminal-enabled agent "
            f"changed it — {fix}",
        )
    return Check(name, label, _PASS, f"{effective} ({st.get('source')})")


def check_hermes_delegation_slot(
    slots: Any,
    *,
    slot_name: str | None = None,
) -> Check:
    """The operator-chosen Hermes delegation slot must be ready.

    ADVISORY. Only an explicit non-default ``[hermes].delegation_slot`` is
    checked: the default (``agent``) is the parent's own slot. When the named
    slot is absent or not dispatchable, provisioning does not render the
    delegation block; a block rendered earlier stays in Hermes' config.yaml
    until a repair runs while the slot is ready. A chosen slot only takes
    effect at the next repair.
    """
    from hal0.agents import hermes_provision as hp
    from hal0.config.schema import HERMES_DEFAULT_DELEGATION_SLOT

    name, label = "hermes_delegation_slot", "Hermes delegation slot"
    chosen = slot_name if slot_name is not None else hp.read_delegation_slot()
    if chosen == HERMES_DEFAULT_DELEGATION_SLOT:
        return Check(name, label, _PASS, f"{chosen} (default)")
    if not isinstance(slots, list):
        return Check(name, label, _PASS, f"{chosen}; slot state unavailable")
    if hp._find_named_ready_slot(slots, chosen) is None:
        return Check(
            name,
            label,
            _WARN,
            f"delegation_slot is {chosen!r} but that slot is not ready; delegate_task "
            "children inherit the parent model, or still target the last-rendered "
            "model if delegation was rendered earlier. Start the slot and run "
            "`hal0 agent reprovision hermes`, or set [hermes].delegation_slot back to "
            "'agent' in /etc/hal0/agents/hermes.toml",
        )
    return Check(name, label, _PASS, f"{chosen} (ready; applied at the next repair)")


def _hermes_http_get(url: str, headers: dict[str, str]) -> tuple[int, Any]:
    """GET ``url`` with ``headers``; ``(status, json_or_None)``, ``(0, None)`` on error.

    A loopback target ignores the proxy environment: the request carries a
    profile's bearer key, which must never reach an ``HTTP_PROXY``.
    """
    import httpx

    from hal0.service_identity import is_loopback_url

    try:
        resp = httpx.get(url, headers=headers, timeout=5.0, trust_env=not is_loopback_url(url))
    except httpx.HTTPError:
        return 0, None
    try:
        body = resp.json()
    except ValueError:
        body = None
    return resp.status_code, body


def _upstream_names(payload: Any) -> set[str]:
    rows = payload.get("upstreams") if isinstance(payload, dict) else payload
    if not isinstance(rows, list):
        return set()
    return {r["name"] for r in rows if isinstance(r, dict) and isinstance(r.get("name"), str)}


def check_hermes_api_upstreams(
    *,
    home: Path | None = None,
    probe: Callable[[str], int] | None = None,
    http_get: Callable[[str, dict[str, str]], tuple[int, Any]] | None = None,
    upstreams_payload: Any = None,
    slots_payload: Any = None,
) -> Check:
    """Each served Hermes profile's API path: key, authenticated ``/v1/models``, upstream.

    ADVISORY (never critical). Checks the API path only, not the MCP tools.
    """
    from hal0.agents import hermes_api as ha

    key, label = "hermes-api", "Hermes API upstreams"
    tail = " (API path only; MCP tools are not checked, see #2891)"
    root = home if home is not None else ha.hermes_home()
    # Hermes chmods its home 0700, so a non-hal0 operator cannot see into it.
    # Report that honestly instead of a green "not installed" (Ruling 2).
    unreadable = Check(key, label, _WARN, f"cannot read {root}; run hal0 doctor as hal0 or root")
    try:
        is_home = root.is_dir()
    except PermissionError:
        return unreadable
    if is_home and not os.access(root, os.R_OK | os.X_OK):
        return unreadable
    try:
        (root / "config.yaml").stat()
    except PermissionError:
        return unreadable
    except OSError:
        return Check(key, label, _PASS, "no Hermes agent installed")
    get = http_get if http_get is not None else _hermes_http_get
    base = ha.api_server_base(root)
    if not ha.gateway_alive(root, probe=probe):
        # A gateway the operator turned off (systemctl disable --now, #2815)
        # is a legitimate state, not a broken API path: say what is not
        # checked and how to turn it on, instead of a FAIL per profile.
        return Check(
            key,
            label,
            _WARN,
            f"Hermes gateway not answering {base}/health, so the API path is not checked; "
            "if the gateway is meant to run: sudo systemctl enable --now hermes-gateway" + tail,
        )
    served = ha.served_profiles(root, probe=probe)
    registered = _upstream_names(upstreams_payload)
    api_down = upstreams_payload is None
    fails: list[str] = []
    warns: list[str] = []
    ok: list[str] = []
    for profile in served:
        model = ha.model_id_for(profile, root)
        api_key = ha.api_key_for(profile, root)
        if api_key is None:
            nokey = f"profile {profile} has no usable API key; " + (
                "run sudo hal0 agent reprovision hermes --repair "
                "(it mirrors the gateway's vault key into $HERMES_HOME/.env)"
                if profile == ha.DEFAULT_PROFILE
                else "run sudo hal0 agent reprovision hermes --repair"
            )
            env = (
                root / ".env"
                if profile == ha.DEFAULT_PROFILE
                else root / "profiles" / profile / ".env"
            )
            try:
                env.read_text(encoding="utf-8")
            except PermissionError:
                warns.append(f"cannot read {env}; run hal0 doctor as hal0 or root")
            except (OSError, UnicodeDecodeError):
                warns.append(nokey)
            else:
                warns.append(nokey)
            continue
        prefix = "" if profile == ha.DEFAULT_PROFILE else f"/p/{profile}"
        status, _body = get(f"{base}{prefix}/v1/models", {"Authorization": f"Bearer {api_key}"})
        if status != 200:
            fails.append(
                f"profile {profile}: GET {prefix}/v1/models answered {status}, expected 200"
            )
            continue
        if api_down:
            warns.append(f"profile {profile}: hal0-api unreachable, upstream not verified")
        elif ha.upstream_name_for(profile) not in registered:
            warns.append(
                f"profile {profile}: upstream {ha.upstream_name_for(profile)} missing; hal0-api has "
                "not reconciled; restart hal0-api or POST /api/agents/hermes/reconcile-upstreams"
            )
        else:
            ok.append(f"{profile}→{model}")
    owned = {ha.model_id_for(p, root): p for p in served}
    if isinstance(slots_payload, list):
        for slot in slots_payload:
            if not isinstance(slot, dict):
                continue
            # /api/slots lists remote upstreams too (kind "remote"), the Hermes
            # entries among them. Only a local slot can shadow an owned id.
            if slot.get("kind") == "remote":
                continue
            mid = next(
                (
                    slot[k]
                    for k in ("model_id", "model", "default_model")
                    if isinstance(slot.get(k), str)
                ),
                None,
            )
            if mid in owned:
                name = slot.get("name") or slot.get("alias") or slot.get("slug") or "?"
                warns.append(
                    f"slot {name} advertises {mid}, which Hermes profile {owned[mid]} owns; "
                    "the Hermes upstream wins"
                )
    if fails:
        return Check(key, label, _FAIL, "; ".join(fails + warns) + tail)
    if warns:
        return Check(key, label, _WARN, "; ".join(warns) + tail)
    return Check(key, label, _PASS, "; ".join(ok) + tail)


#: The Hermes gateway's system unit, written by ``hermes gateway install``.
HERMES_GATEWAY_UNIT = Path("/etc/systemd/system/hermes-gateway.service")


def check_hal0_linger(
    *,
    probe: Callable[[], Any] | None = None,
    gateway_unit: Path | None = None,
    gateway_in_use: Callable[[], bool] | None = None,
) -> Check:
    """Is systemd linger on for ``hal0``? (ADR-0034 decision 6)

    Read-only. Hermes v0.22's ``hermes gateway install --system --run-as-user
    hal0`` turns linger on so its cron and kanban workers can reach
    ``systemd-run --user``; hal0 accepts that and shows it here. Verdicts:

    * gateway installed, linger on, unit generated by Hermes 0.22+ — ``pass``;
    * gateway installed, linger off — ``warn``: kanban workers cannot start
      and cron runs without a scope;
    * gateway installed but its unit predates Hermes 0.22 (no
      ``user@<uid>.service`` ordering) — ``warn``: it was generated at the
      0.19.0 pin and still starts v0.22.2, but without the ordering the
      gateway can race the user manager at boot and lose its worker bus;
    * gateway installed but disabled and stopped — ``pass`` (info): it never
      runs, so neither its unit shape nor linger matters;
    * no gateway, linger on — ``warn``: left over from an uninstall that
      could not remove it;
    * no gateway, linger off — ``pass``;
    * state unreadable — ``warn``.
    """
    from hal0.system import linger

    name, label = "hal0_linger", "hal0 user linger"
    try:
        state = (probe or linger.read_linger)()
    except Exception as exc:  # a read-only row must never raise
        return Check(name, label, _WARN, f"could not read linger state: {exc}")
    unit = gateway_unit if gateway_unit is not None else HERMES_GATEWAY_UNIT
    gateway = unit.exists()
    regenerate = (
        "Fix: `sudo hal0 agent install hermes` (regenerates the gateway unit and turns linger on)"
    )
    if state.enabled is None:
        return Check(name, label, _WARN, f"could not read linger state ({state.detail})")
    if gateway and not (gateway_in_use or linger.gateway_in_use)():
        return Check(
            name,
            label,
            _PASS,
            f"{'on' if state.enabled else 'off'}; the Hermes gateway is installed but disabled "
            "and stopped, so linger is not needed until you enable it",
        )
    if gateway and not state.enabled:
        return Check(
            name,
            label,
            _WARN,
            "off, but the Hermes gateway is installed — kanban workers cannot start "
            f"and cron jobs run without a scope. {regenerate}",
        )
    if gateway:
        try:
            unit_text = unit.read_text(encoding="utf-8")
        except OSError:
            unit_text = ""
        if unit_text and "user@" not in unit_text:
            return Check(
                name,
                label,
                _WARN,
                "on, but the gateway unit predates Hermes 0.22 (no user@<uid>.service "
                f"ordering), so the gateway can miss its worker bus at boot. {regenerate}",
            )
        return Check(name, label, _PASS, "on — Hermes cron and kanban workers can start")
    if state.enabled:
        return Check(
            name,
            label,
            _WARN,
            "on, but the Hermes gateway is not installed — "
            "`loginctl disable-linger hal0` removes it",
        )
    return Check(name, label, _PASS, "off (Hermes gateway not installed)")


#: The placeholder credential the hindsight-api unit ships for the auth-off
#: posture — see installer/systemd/hindsight-api.service.
_HINDSIGHT_LLM_PLACEHOLDER = "hal0-local-noauth"


def check_hindsight_llm_auth(
    *,
    auth: dict[str, Any] | None,
    unit_path: Path | None = None,
    env_path: Path | None = None,
) -> Check:
    """The memory engine's LLM credential must be a real key when auth is on.

    hindsight-api calls back into hal0's ``/v1`` (CLIENT tier) for every
    extraction and reflect. Its unit ships the ``hal0-local-noauth``
    placeholder, overridden by ``/etc/hal0/hindsight-llm.env``. On an
    auth-required box a missing/placeholder/stale key means every retain
    fails extraction with a 401 — silently, because retain is async
    (#1543's engine-side sibling).
    """
    unit = unit_path if unit_path is not None else Path("/etc/systemd/system/hindsight-api.service")
    env_file = env_path if env_path is not None else Path("/etc/hal0/hindsight-llm.env")
    name, title = "hindsight_llm_auth", "Memory engine LLM auth"
    if not unit.exists():
        return Check(name, title, _PASS, "memory engine not installed — nothing to check")
    if not bool((auth or {}).get("auth_required")):
        return Check(name, title, _PASS, "auth not required — placeholder credential is fine")

    key = ""
    try:
        for line in env_file.read_text(encoding="utf-8").splitlines():
            if line.startswith("HINDSIGHT_API_LLM_API_KEY="):
                key = line.split("=", 1)[1].strip()
    except OSError:
        key = ""
    if not key or key == _HINDSIGHT_LLM_PLACEHOLDER:
        return Check(
            name,
            title,
            _FAIL,
            f"auth is required but {env_file} carries no real LLM key — every retain/reflect "
            "401s against /v1. Write a client-tier key there (`hal0 auth rotate` refreshes it "
            "automatically from now on) and restart hindsight-api",
        )

    from hal0.service_identity import keys_from_api_env

    live = set(keys_from_api_env().values())
    if live and key not in live:
        return Check(
            name,
            title,
            _FAIL,
            f"{env_file} carries a key that matches neither current box key (stale after a "
            "rotation?) — refresh it and restart hindsight-api",
        )
    return Check(name, title, _PASS, "memory engine carries a current client-tier LLM key")


def check_memory_engine_version(*, memory: dict[str, Any] | None) -> Check:
    """The engine should be at the pinned version — staleness signal only.

    Both upgrade paths converge the engine venv via the post-activation
    migration pass (``hal0.memory.engine_upgrade``); a box that reports an
    older ``api_version`` missed or failed that pass. Advisory by
    convention: behavior gates on ``/version`` ``features``, never the
    version string (see memory_admin's document-transfer gate), so a stale
    engine WARNs with the remedy and never fails the roll-up.

    ``memory`` is the ``GET /api/memory/engine`` payload the roll-up already
    fetched — one probe, two rows.
    """
    from hal0.memory.engine_upgrade import HINDSIGHT_API_PIN

    name, title = "memory_engine_version", "Memory engine version"
    if memory is None:
        return Check(name, title, _WARN, "engine status unreachable — is hal0-api up?")
    if memory.get("engine") in (None, "") or not memory.get("enabled", True):
        return Check(name, title, _PASS, "memory disabled — nothing to check")
    if not memory.get("reachable"):
        # check_memory (the shared classifier) already reports reachability;
        # a second row saying the same thing would be noise.
        return Check(name, title, _PASS, "engine unreachable — see the memory row")
    version = memory.get("version")
    if version == HINDSIGHT_API_PIN:
        return Check(name, title, _PASS, f"hindsight-api {version} matches the pin")
    return Check(
        name,
        title,
        _WARN,
        f"engine reports {version or 'unknown'} but this release pins {HINDSIGHT_API_PIN} — "
        "the engine-upgrade pass missed or failed; run `hal0 update` (or re-run install.sh) "
        "and check the journal for updater.memory_engine_upgrade_failed",
    )


#: ``POST /api/memory/graph/throughput`` may run the 30 s probe (#2353).
THROUGHPUT_PROBE_TIMEOUT_S: float = 45.0


def _measure_extraction_throughput(base: str | None) -> dict[str, Any] | None:
    from hal0.cli._shared import CliApiError, api_post

    try:
        payload = api_post(
            "/api/memory/graph/throughput", base=base, timeout=THROUGHPUT_PROBE_TIMEOUT_S
        )
    except CliApiError:
        return None
    est = payload.get("throughput") if isinstance(payload, dict) else None
    return est if isinstance(est, dict) else None


def check_memory_extraction_throughput(
    *,
    memory: dict[str, Any] | None,
    base: str | None = None,
    measure: Callable[[str | None], dict[str, Any] | None] | None = None,
) -> Check:
    """The extraction slot must finish one extraction inside ``llm_timeout_s`` (#2353).

    A slot that fits the extraction prompt (the #1903 window floor) can still
    be too slow to process it: the #1834 CPU box prefilled at ~12 tok/s, so
    every attempt hit the 300 s timeout and the bank stayed empty with no
    signal until the retries ran out. This row is the one place in ``doctor``
    that is not a pure read: it asks the API for the bounded measurement
    (the slot's metrics, else one <= 512 + 32 token completion capped at
    30 s). A slow slot is advisory (``warn``), matching the enable path, which
    warns and never refuses.
    """
    name, title = "memory_extraction_throughput", "Memory extraction speed"
    if memory is None:
        return Check(name, title, _WARN, "memory engine status unreachable")
    if memory.get("engine") in (None, "") or not memory.get("enabled", True):
        return Check(name, title, _PASS, "memory disabled — nothing to check")
    if not memory.get("reachable"):
        return Check(name, title, _PASS, "engine unreachable — see the memory row")
    est = (measure or _measure_extraction_throughput)(base)
    if est is None:
        return Check(name, title, _WARN, "could not measure the extraction slot's throughput")
    detail = str(est.get("message") or "no estimate")
    return Check(name, title, _PASS if est.get("verdict") == "ok" else _WARN, detail)


def check_hermes_anchor_window(
    *,
    base: str | None = None,
    config_path: Path | None = None,
    fetch: Callable[..., tuple[dict[str, Any] | None, list[dict[str, Any]]]] | None = None,
    slots_dir: Path | None = None,
    venv_python: Path | None = None,
) -> Check:
    """The window behind Hermes' anchor must clear Hermes' hard floor (#1867).

    Hermes raises below ``agent/model_metadata.py::MINIMUM_CONTEXT_LENGTH``
    instead of degrading, so a box whose anchor resolves under it refuses EVERY
    turn with an opaque upstream error that names neither the slot nor the
    profile window behind it.

    This row exists because the smoke probe alone cannot cover the shape the
    defect actually lives in: ``installer/install.sh`` calls
    ``install_hermes`` only when the hermes venv is ABSENT ("covers the upgrade
    path where no provision ran"), so on an in-place upgrade — the one case
    where a slot profile with a small ``ctx_size`` survives — no smoke test
    ever runs. ``hal0 doctor`` is what an operator runs when the
    agent misbehaves, so the check has to be here too.

    Verdicts: below the floor is a ``fail`` with the repair; a window
    that cannot be read is a ``warn`` (never a pass — see #1831); an
    unprovisioned Hermes passes with nothing to check.
    """
    from hal0.agents.anchor_window import read_hermes_minimum_context, resolve_anchor_window
    from hal0.agents.hermes_provision import (
        HERMES_HOME_DEFAULT,
        HERMES_VENV_DEFAULT,
        _fetch_anchor_models,
        _venv_python,
    )

    name, title = "hermes_anchor_window", "Hermes anchor context window"
    path = config_path if config_path is not None else (HERMES_HOME_DEFAULT / "config.yaml")
    if not path.exists():
        return Check(name, title, _PASS, "hermes not provisioned — nothing to check")

    import yaml

    try:
        data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    except (OSError, yaml.YAMLError) as exc:
        return Check(name, title, _WARN, f"could not read {path}: {exc}")
    model_cfg = (data.get("model") or {}) if isinstance(data, dict) else {}
    model_name = str(model_cfg.get("default") or "").strip()
    if not model_name:
        return Check(name, title, _WARN, f"model.default is unset in {path}")
    base_url = str(model_cfg.get("base_url", "") or "")

    python = venv_python if venv_python is not None else _venv_python(HERMES_VENV_DEFAULT)
    floor, floor_source = read_hermes_minimum_context(python if python.exists() else None)
    fetcher = fetch if fetch is not None else _fetch_anchor_models
    try:
        entry, catalog = fetcher(model_name, base_url, base or "")
    except Exception as exc:  # a read-only row must never raise
        return Check(name, title, _WARN, f"could not read the advertised window: {exc}")

    window = resolve_anchor_window(
        model_name,
        entry=entry,
        catalog=catalog,
        floor=floor,
        floor_source=floor_source,
        slots_dir=slots_dir if slots_dir is not None else Path("/etc/hal0/slots"),
        endpoint=base_url,
    )
    if window.verdict == "below_floor":
        return Check(name, title, _FAIL, window.message())
    if window.verdict == "unknown":
        return Check(name, title, _WARN, window.message())
    return Check(name, title, _PASS, window.message())


# ── orchestration ──────────────────────────────────────────────────────────────

# GET /api/slots is the slowest read-only route hal0 serves: the aggregator
# merges SlotManager-backed entries with upstream-backed ones and container-
# probes each, so cost scales with slot count. Measured on lxc105 (18 slots,
# main @ 5be7f2a5): 11.2-14.6s, against the 10.0s default every other doctor
# fetch uses — which is exactly how a healthy box got reported unreachable
# (#1501). The budget is generous because this is a diagnostic: waiting is
# strictly better than a false negative on the operator's first-line tool.
#
# NOTE: this makes `doctor` honest about a slow endpoint; it does not make the
# endpoint fast. The underlying /api/slots latency is tracked separately — the
# dashboard polls that same route.
SLOTS_PROBE_TIMEOUT_S: float = 30.0


def _get_any(path: str, base: str | None, *, timeout: float = 10.0) -> Any:
    """Best-effort GET returning the raw parsed body (dict OR list), else None.

    ``timeout`` is per-call so a slow-but-legitimate route can be given room
    without loosening the budget for every other probe in the roll-up.
    """
    from hal0.cli._shared import CliApiError, api_get

    try:
        return api_get(path, base=base, timeout=timeout)
    except CliApiError:
        return None


def build_all_checks(base: str | None = None) -> list[Check]:
    """Fetch every read-only source and compose the full ordered check list."""
    payloads = gather_payloads(base)
    verify_rows = build_checks(
        health=payloads["health"],
        urls=payloads["urls"],
        system=payloads["system"],
        capabilities=payloads["capabilities"],
        memory=payloads["memory"],
        services=payloads["services"],
    )

    auth_payload = _get_any("/api/auth/status", base)
    # One fetch, two rows: /api/slots container-probes every slot, so paying for
    # it twice would double the roll-up's slowest leg.
    slots_payload = _get_any("/api/slots", base, timeout=SLOTS_PROBE_TIMEOUT_S)
    extra_rows = [
        check_auth_posture(auth_payload),
        check_model_store(_get_any("/api/models", base)),
        check_leftover_registry_toml(),
        check_ui_dist(),
        check_ports(slots_payload),
        check_port_dnat(slots_payload),
        check_netns_durability(),
        check_hal0_target(),
        check_secret_file_modes(),
        check_hardware_freshness(),
        check_seed_context_envelope(),
        check_agent_uid_isolation(),
        check_api_journal_access(),
        check_voice_stt_weights(),
        check_seams(),
        check_mcp_mounts(),
        check_hermes_api_upstreams(
            upstreams_payload=_get_any("/api/upstreams", base), slots_payload=slots_payload
        ),
        check_hermes_mcp_config_auth(auth=auth_payload),
        check_hermes_profiles_mcp(auth=auth_payload),
        check_hermes_generation_extras(),
        check_hermes_command_approvals(),
        check_hermes_delegation_slot(slots_payload),
        check_hermes_anchor_window(base=base),
        check_hal0_linger(),
        check_hindsight_llm_auth(auth=auth_payload),
        check_memory_engine_version(memory=payloads["memory"]),
        check_memory_extraction_throughput(memory=payloads["memory"], base=base),
    ]
    return verify_rows + extra_rows


def overall_verdict(checks: list[Check]) -> str:
    """Roll the rows up to ``ok`` | ``fail`` | ``critical``.

    ``critical`` iff any critical row failed; ``fail`` iff any non-critical
    ``fail`` is present; else ``ok`` (advisory ``warn`` rows do not block).
    """
    if any(c.status == _FAIL and c.critical for c in checks):
        return "critical"
    if any(c.status == _FAIL for c in checks):
        return "fail"
    return "ok"


_BADGE = {
    _PASS: "[green]✔ PASS[/green]",
    _WARN: "[yellow]▲ WARN[/yellow]",
    _FAIL: "[red]✖ FAIL[/red]",
}
_CRIT_BADGE = "[bold red]✖ FAIL[/bold red]"


def render_all(con: Console, checks: list[Check]) -> None:
    """Print the aggregate evidence table."""
    table = Table(title="hal0 doctor — evidence roll-up")
    table.add_column("Status", width=9)
    table.add_column("Check", style="bold", width=18)
    table.add_column("Detail")
    for c in checks:
        badge = _CRIT_BADGE if (c.status == _FAIL and c.critical) else _BADGE[c.status]
        table.add_row(badge, c.label, c.detail)
    con.print(table)


def _exit_code(checks: list[Check]) -> int:
    verdict = overall_verdict(checks)
    return {"ok": 0, "fail": 1, "critical": 2}[verdict]


def doctor_all_cmd(
    json_output: bool = typer.Option(
        False,
        "--json",
        help="Emit the check rows as JSON instead of the human table.",
    ),
) -> None:
    """Run every read-only doctor check in one pass and roll up a verdict.

    Composes the ``doctor verify`` report card (API, runners, DNS, capability
    slots, memory, OpenWebUI, Hermes) with auth posture, model-store integrity,
    bound slot ports, the ``hal0.target`` boot-enable
    anchor, and the privileged sudo seams. Read-only — use the per-surface subcommands (``perms``/``models``)
    for ``--fix``.

    Exit codes: 0 clean, 1 an actionable fail, 2 a critical failure (API
    unreachable / zero healthy runners).
    """
    checks = build_all_checks()
    if json_output:
        rows = [
            {
                "key": c.key,
                "label": c.label,
                "status": c.status,
                "detail": c.detail,
                "critical": c.critical,
            }
            for c in checks
        ]
        console.print_json(jsonlib.dumps(rows))
    else:
        render_all(console, checks)
    raise typer.Exit(_exit_code(checks))


__all__ = [
    "SLOTS_PROBE_TIMEOUT_S",
    "build_all_checks",
    "check_auth_posture",
    "check_hal0_target",
    "check_hermes_anchor_window",
    "check_hermes_mcp_config_auth",
    "check_hindsight_llm_auth",
    "check_mcp_mounts",
    "check_memory_engine_version",
    "check_memory_extraction_throughput",
    "check_model_store",
    "check_ports",
    "check_seams",
    "check_ui_dist",
    "doctor_all_cmd",
    "overall_verdict",
    "probe_builtin_mcp_mounts",
    "render_all",
]
