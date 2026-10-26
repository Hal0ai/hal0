"""hal0 agent subcommands — bundled-agent lifecycle + approval queue.

Mirrors :mod:`hal0.cli.slot_commands` shape (Typer sub-app + thin HTTP
client). The lifecycle subcommands hit the routes in
:mod:`hal0.api.routes.agents`; the ``approvals`` sub-sub-app hits the
MCP-backend's approval queue at ``/api/agent/approvals``.
"""

from __future__ import annotations

import json as jsonlib
import os
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import TYPE_CHECKING, Any, Literal

import typer
from rich.console import Console
from rich.markup import escape as _escape_markup
from rich.panel import Panel
from rich.table import Table

from hal0.cli._shared import (
    CliApiError,
    _api_base,
    _api_unreachable,
    api_delete,
    api_get,
    api_post,
    api_put,
    auth_client,
    die,
)
from hal0.cli._shared import _console as _stderr_console
from hal0.mcp.approval_queue import _PRIMARY_TARGET_ARG
from hal0.system.seam import hal0_service_home, running_hal0_bin

if TYPE_CHECKING:
    from pathlib import Path

app = typer.Typer(help="Manage bundled agents (Phase 8 — Hermes-Agent).")
console = Console()

# Approvals lives as a sub-sub-app so ``hal0 agent approvals list``
# renders correctly in --help. Same pattern as the slot sub-app.
approvals_app = typer.Typer(help="Manage agent approval requests (gated destructives).")
app.add_typer(approvals_app, name="approvals")

# Personas sub-sub-app (PR-3, v0.3) — manages the persona TOML store at
# /var/lib/hal0/.hermes/personas/. Activate writes active.txt and
# triggers a best-effort hot-reload nudge; the API endpoint added in PR-4
# wraps the same persona.activate() helper.
personas_app = typer.Typer(help="Manage Hermes personas (system prompt + tool gating).")
app.add_typer(personas_app, name="personas")


# ── Lifecycle ────────────────────────────────────────────────────────────────


@app.command("install")
def agent_install(
    name: str = typer.Argument(
        ...,
        help="Bundled agent name (hermes, pi).",
    ),
    switch: bool = typer.Option(
        False,
        "--switch",
        help=(
            "If another agent is already installed, atomically uninstall it before "
            "installing this one (single-pick enforced)."
        ),
    ),
    gateway: bool = typer.Option(
        True,
        "--gateway/--no-gateway",
        help=(
            "Hermes only: also install + enable the Telegram/Discord gateway "
            "(hermes-gateway.service), matching install-time wiring "
            "(installer/install.sh). Use --no-gateway to provision Hermes "
            "without the messaging bridge."
        ),
    ),
    reset_personas: bool = typer.Option(
        False,
        "--reset-personas",
        help=(
            "Hermes only: force-overwrite the default personas (hermes, "
            "hal0-brain) and the active.txt pointer back to canonical, even "
            "over operator edits. Without this flag, personas are only ever "
            "seeded idempotently (missing defaults added, existing files "
            "untouched) — this is the explicit, opt-in reset that "
            "--repair used to do implicitly before RELOCATE(brain-lane) "
            "moved persona seeding out of the install pipeline."
        ),
    ),
    terminal_tool: bool | None = typer.Option(
        None,
        "--terminal-tool/--no-terminal-tool",
        help=(
            "Hermes only: give the agent a terminal tool. OFF by default — with "
            "it on, the agent can run shell commands as the hal0 user, which is "
            "root-equivalent on this box. Omit the flag to keep the default "
            "(off on a fresh install; an existing box's setting is preserved)."
        ),
    ),
) -> None:
    """Install a bundled agent.

    Hermes provisions into a hal0-managed venv (toolchain + venv create +
    pip-install hermes-agent + wrapper shim). That multi-minute work can't
    run inside a single HTTP request, so for hermes ``install`` runs the
    bootstrap pipeline locally in the foreground (streaming progress) and
    only consults the daemon at the end to honour ``--switch``. Other
    agents keep the thin API-driven path.

    The Telegram/Discord gateway is enabled by default (``--gateway``) so
    this "deferred" path (run after ``HAL0_SKIP_HERMES=1`` at install time,
    or standalone) has NO downside vs. the install-time provision — issue
    #1102 / decision Q9.

    pi is cli-kind (a terminal coding tool with no daemon/systemd unit) and,
    like hermes, provisions LOCALLY in the CLI foreground — its profile
    lands in the invoking user's home (``~/.pi``) plus the global npm
    prefix, which the hal0-api daemon (a system user with home
    ``/var/lib/hal0``) cannot write. Being cli-kind rather than
    daemon-kind, it coexists with Hermes: single-pick (``--switch``) only
    applies between daemon-kind agents.
    """
    if name == "hermes":
        _install_hermes(
            switch=switch,
            gateway=gateway,
            reset_personas=reset_personas,
            terminal_tool=terminal_tool,
        )
        return

    if name == "pi":
        _install_pi()
        return

    url = _api_base()
    if _api_unreachable(url):
        raise typer.Exit(1)
    payload: dict[str, object] = {"name": name, "switch": switch}
    try:
        rec = api_post("/api/agents/install", json=payload)
    except CliApiError as exc:
        die(str(exc))
        return
    console.print(
        Panel(
            f"[bold green]Installed[/bold green] {rec.get('name', name)}  "
            f"[dim](data: {rec.get('data_dir', '?')})[/dim]",
            border_style="green",
        )
    )


def _install_hermes(
    *,
    switch: bool,
    gateway: bool = True,
    reset_personas: bool = False,
    terminal_tool: bool | None = None,
) -> None:
    """Foreground provision of Hermes into the hal0-managed venv.

    Six steps, all local + foreground:

    0. **Single-pick** — :func:`_enforce_hermes_single_pick` refuses (or,
       with ``--switch``, uninstalls) a different incumbent bundled agent
       BEFORE any provisioning side effect — see its docstring for why this
       can't wait for the daemon call in step 4.
    1. **Toolchain** — ``installer/agents/hermes-prereqs.sh`` ensures
       python3 (>=3.11), python3-venv (the clean-Ubuntu trap), python3-pip
       and pipx via the distro helper. Idempotent.
    2. **Provision** — the bootstrap pipeline builds a Hermes release
       (a checkout of the vetted commit plus an editable venv under
       ``/var/lib/hal0/hermes``, ADR-0032), installs the
       ``/usr/local/bin/hermes`` shim, and writes the manager seed
       (registers the agent).
    3. **Reset personas** — (``reset_personas=True``, opt-in) force-overwrite
       the default personas + ``active.txt`` pointer to canonical via
       :func:`_reset_hermes_personas`. Skipped by default: personas are
       otherwise only ever idempotently seeded (by the hal0-api boot
       lifespan's ``_boot_seeds`` phase), never overwritten.
    4. **Switch** — best-effort daemon call so ``--switch`` still does the
       atomic single-pick swap; provisioning already registered hermes, so
       a daemon hiccup here doesn't un-provision it.
    5. **Gateway** — (``gateway=True``, the default) install + enable the
       Telegram/Discord gateway, the same wiring installer/install.sh runs
       inline at install time. This is what makes the DEFERRED path (run
       manually after ``HAL0_SKIP_HERMES=1``) lossless vs. install-time
       (issue #1102 / Q9): whichever path runs, the gateway ends up wired.
    """
    import subprocess as _subprocess

    from hal0.agents.hermes_provision import REPO_ROOT_FOR_INSTALLER

    # Enforce single-pick BEFORE any provisioning side effect. Hermes
    # provisions locally (this function) and only calls the daemon's
    # /api/agents/install at the very end (see step 3 below) — by then
    # hermes_provision._write_seed_toml has already dropped
    # /etc/hal0/agents/hermes.toml, so AgentManager.install() sees
    # "hermes" already in installed_names() and takes its idempotent
    # no-op path instead of raising AgentAlreadyInstalledError. That let
    # `hal0 agent install hermes` (no --switch) land ALONGSIDE an
    # existing non-coexisting incumbent. Check disk truth here, up
    # front, so hermes gets the same guarantee the manager enforces for
    # the other bundled agents.
    _enforce_hermes_single_pick(switch=switch)

    # Bail out cleanly (before the toolchain shell-out) when we can't write the
    # provisioning trees — otherwise the bootstrap crashes several phases deep
    # with a raw PermissionError and leaves half-owned dirs behind.
    _ensure_hermes_writable_or_die()

    prereqs = REPO_ROOT_FOR_INSTALLER / "installer" / "agents" / "hermes-prereqs.sh"
    if prereqs.is_file():
        console.print("[bold]Ensuring toolchain[/bold] (python · venv · pip · pipx)…")
        rc = _subprocess.run(  # nosec B603 B607 — fixed argv, repo-anchored script
            ["bash", str(prereqs)], check=False
        ).returncode
        if rc != 0:
            die("toolchain prerequisites failed — see the output above.")
            return
    else:
        console.print(
            f"[yellow]prereq script missing at {prereqs}; assuming toolchain present.[/yellow]"
        )

    console.print("[bold]Provisioning Hermes[/bold] → /var/lib/hal0/hermes (release layout) …")
    # §7.4: when invoked as root this drops to the hal0 user so every HERMES_HOME
    # write is born hal0:hal0 (root-only prelude + re-exec). Non-root runs the
    # pipeline in-process. See _provision_hermes.
    rc = _provision_hermes(terminal_tool=terminal_tool)
    if rc != 0:
        die(
            "Hermes provisioning failed — inspect `hal0 agent status hermes` / `hal0 agent log hermes`."
        )
        return

    # §7.4 F.7: no chown-back — provisioning drops to hal0 (see _provision_hermes)
    # so the trees are born hal0:hal0.

    # Explicit, opt-in canonical reset — see _reset_hermes_personas docstring.
    # Runs AFTER provisioning (HERMES_HOME + the personas dir now exist) and
    # BEFORE the register/gateway steps so a fresh install with --reset-personas
    # and a repeat install both converge on the same canonical persona state.
    if reset_personas:
        _reset_hermes_personas()

    # Register + honour --switch via the daemon (venv now present → gate passes).
    url = _api_base()
    if not _api_unreachable(url):
        try:
            api_post("/api/agents/install", json={"name": "hermes", "switch": switch})
        except CliApiError as exc:
            console.print(f"[yellow]Provisioned, but daemon register/switch hint:[/yellow] {exc}")

    # Bring the unit up so the agent actually runs (and survives reboot).
    _enable_and_start_hermes_unit()

    # Telegram/Discord gateway — installer/install.sh runs this inline at
    # install time; folding it in here means the DEFERRED path (this
    # command, run after HAL0_SKIP_HERMES=1 or standalone) wires it too
    # (issue #1102 / Q9). Best-effort: a gateway hiccup doesn't undo an
    # otherwise-successful Hermes install.
    if gateway:
        _install_hermes_gateway()

    console.print(
        Panel(
            "[bold green]Installed[/bold green] hermes  "
            "[dim](live release: /var/lib/hal0/hermes/current)[/dim]",
            border_style="green",
        )
    )


def _install_pi() -> None:
    """Foreground, CLI-local pi install (spec D1 cli-kind).

    pi provisions the INVOKING user's home (``~/.pi``) and the global npm
    prefix, so the work cannot ride the daemon: hal0-api runs as the
    ``hal0`` system user, whose home is ``/var/lib/hal0`` and who can
    write neither ``/root/.pi`` nor ``npm install -g``'s prefix — and
    whose 0700-blindness to the operator's home is also why
    :meth:`PiDriver.status` reads a data-dir marker instead of the home.
    The manager runs in-process instead, with identical semantics (seed
    TOML, registry bookkeeping, data dir); the daemon lists pi afterwards
    because ``AgentManager.list()`` re-reads ``/etc`` on every query.
    ``--switch`` is irrelevant here — cli-kind agents never block nor
    swap (see ``AGENT_KINDS`` in :mod:`hal0.agents.manager`).
    """
    if os.geteuid() != 0:
        die(
            "pi installs into the invoking user's home and the global npm "
            "prefix — run as root (sudo hal0 agent install pi)."
        )
        return

    from hal0.agents.manager import AgentError, AgentManager

    console.print("[bold]Provisioning pi[/bold] → ~/.pi (theme · hal0 provider · memory wiring) …")
    try:
        rec = AgentManager().install("pi")
    except AgentError as exc:
        die(f"pi install failed: {exc}")
        return
    console.print(
        Panel(
            f"[bold green]Installed[/bold green] pi  [dim](data: {rec.data_dir})[/dim]",
            border_style="green",
        )
    )


def _upgrade_pi() -> None:
    """Foreground, CLI-local pi upgrade — the sibling of :func:`_install_pi`.

    Runs :meth:`PiDriver.upgrade` in-process for the same reason install does:
    it re-pins the global npm prefix and redeploys into the invoking user's
    ``~/.pi``, neither of which the ``hal0`` system user can write. The driver
    refuses to run as a different user than the one that installed pi and
    raises :class:`AgentError` when pi is not installed; both surface here as
    a plain error line.
    """
    if os.geteuid() != 0:
        die(
            "run as root (sudo hal0 agent upgrade pi) — pi upgrades the "
            "invoking user's home and the global npm prefix."
        )
        return

    # Call-time imports: the manager pulls in every driver lazily, and a test
    # (or a dev box missing one driver's dependencies) swaps `_driver_for`.
    from hal0.agents.manager import AgentError, _driver_for

    upgrade = getattr(_driver_for("pi"), "upgrade", None)
    if upgrade is None:
        die("pi driver has no upgrade.")
        return
    console.print("[bold]Upgrading pi[/bold] → ~/.pi (extensions · pins · memory wiring) …")
    try:
        # The driver reports what it changed outside pi's own tree; a stand-in
        # driver that returns nothing changed nothing.
        result = upgrade() or {}
    except AgentError as exc:
        die(f"pi upgrade failed: {exc}")
        return
    if result.get("hindsight_alias_added"):
        console.print("Hindsight bank alias added to coding-agent.json")
    console.print(Panel("[bold green]Upgraded[/bold green] pi", border_style="green"))


def _restart_hermes_after_posture_change() -> None:
    """Restart the Hermes consumers so a tool-posture change takes hold.

    Both consumers are covered — the agent unit and the gateway each load the
    config in their own process — and both go through ``try-restart``, which is
    a no-op on an inactive unit. That invariant matters: this runs from every
    provisioning entry point, including ones the operator drove with Hermes
    deliberately stopped, and a plain ``restart`` would resurrect it. Off root
    the seam supplies its own ``try-restart-agent`` / ``svc-try-restart`` verbs
    so the invariant survives the privilege routing rather than only holding
    for root.

    Privilege routing matters here: this runs from every provisioning entry
    point, including the ones the unprivileged ``hal0`` service user drives, and
    a bare ``systemctl`` from an unprivileged euid escalates through polkit —
    which either prompts (nobody is there) or fails. Root uses plain
    ``systemctl``; anyone else goes through the ``hal0-systemctl`` seam's closed
    verb map with ``sudo -n``, so it can fail but can never prompt.

    Loud on failure: the provisioned config is already correct on disk, but the
    operator has to know a live process may still be serving the old posture.
    """
    import os as _os
    import shutil as _shutil
    import subprocess as _subprocess

    from hal0.system.seam import SEAM_BIN, agent_unit_argv

    if _shutil.which("systemctl") is None:
        return
    root = _os.geteuid() == 0
    plans: list[tuple[str, list[str]]] = [
        ("hal0-agent@hermes.service", agent_unit_argv("try-restart", "hermes")),
        (
            "hermes-gateway.service",
            ["systemctl", "try-restart", "hermes-gateway.service"]
            if root
            else ["sudo", "-n", SEAM_BIN, "svc-try-restart", "hermes-gateway"],
        ),
    ]
    for unit, argv in plans:
        rc = _subprocess.run(argv, check=False).returncode  # nosec B603 — seam-validated argv
        if rc != 0:
            console.print(
                f"[yellow]Could not restart {unit} — a running Hermes may still be "
                "serving the previous terminal-tool posture. Restart it manually.[/yellow]"
            )


def _reset_hermes_personas() -> None:
    """Force-overwrite the default personas + ``active.txt`` pointer to canonical.

    ``--reset-personas`` is the explicit, opt-in replacement for the
    force-reset ``hal0 agent install hermes --repair`` used to do implicitly
    before RELOCATE(brain-lane) moved persona seeding out of
    ``hermes_provision._INSTALL_STEPS`` and into the hal0-api boot lifespan's
    ``_boot_seeds`` phase (which seeds idempotently — ``overwrite=False`` —
    and never resets an operator's edits). That overwrite behaviour still
    exists: :func:`hal0.agents.hermes_provision._phase_persona_seed` (kept
    importable specifically for this — see its module comment) sets
    ``overwrite = ctx.repair``, so constructing a ``_StepCtx`` with
    ``repair=True`` and calling it directly re-triggers the exact same
    overwrite path with no persona-writing logic duplicated here.

    Resolves ``$HERMES_HOME`` the same way the boot lifespan's ``_boot_seeds``
    does (``paths.var_lib() / ".hermes"``) so an install-time reset and the
    next boot-time idempotent seed agree on one persona root — including
    under ``HAL0_HOME``-scoped dev/test installs.
    """
    from hal0.agents.hermes_provision import (
        BootstrapState,
        InstallIO,
        _phase_persona_seed,
        _StepCtx,
    )
    from hal0.config import paths as _hal0_paths

    console.print("[bold]--reset-personas[/bold]: overwriting default personas to canonical…")
    hermes_home = _hal0_paths.var_lib() / ".hermes"
    state = BootstrapState(hermes_home=str(hermes_home))
    ctx = _StepCtx(state=state, io=InstallIO(), repair=True)
    result = _phase_persona_seed(ctx)
    seeded = result.details.get("seeded") or []
    console.print(f"[dim]personas reset to canonical: {', '.join(seeded) or '(none)'}[/dim]")


def _bundled_agent_manager() -> Any:
    """Construct the :class:`~hal0.agents.manager.AgentManager` used for the
    hermes single-pick check. Imported lazily (mirrors the rest of this
    module's ``hermes_provision`` imports) so plain ``hal0 agent`` commands
    that never touch hermes don't pay the import cost. Factored out as a
    seam so tests can monkeypatch ``ac._bundled_agent_manager`` to return a
    manager rooted at a tmp_path instead of the real ``/etc`` and
    ``/var/lib`` trees.
    """
    from hal0.agents.manager import AgentManager

    return AgentManager()


def _enforce_hermes_single_pick(*, switch: bool) -> None:
    """Refuse (or atomically clear) a different incumbent bundled agent
    before hermes provisioning writes anything to disk.

    Mirrors the contract :meth:`hal0.agents.manager.AgentManager.install`
    enforces for every bundled agent: at most one non-coexisting agent
    installed at a time, ``switch=True`` required to swap. Hermes can't just call
    ``AgentManager.install()`` up front (provisioning has to happen first —
    there's no venv/binary for the manager to register yet), so this checks
    the same disk-truth ``installed_names()`` the manager uses and either
    dies with the same message shape as ``AgentAlreadyInstalledError``, or
    — with ``--switch`` — uninstalls the incumbent(s) before provisioning
    starts.
    """
    mgr = _bundled_agent_manager()
    current = [n for n in mgr.installed_names() if n != "hermes"]
    if not current:
        return
    if not switch:
        die(
            f"agent {current[0]!r} already installed; pass --switch to "
            "atomically swap to 'hermes' (single-pick enforced)."
        )
        return
    console.print(
        f"[bold]--switch[/bold]: uninstalling {', '.join(current)} before provisioning hermes…"
    )
    for existing in current:
        mgr.uninstall(existing)


# The agent unit runs as this user (User= in installer/systemd/hal0-agent@.service).
_AGENT_RUNTIME_USER = "hal0"
# Trees the bootstrap creates as the installing user that the agent must own to
# run: the venv link (exec), the release tree it points into (which also holds
# the uv-managed interpreters and uv's cache), HERMES_HOME (read+write at
# runtime), bootstrap state. The venv path is a symlink after the first
# migration; the write probe follows it into the live release, and the release
# root is probed in its own right. The pre-layout /var/lib/hal0/python is not
# listed: it is root-owned on existing hosts and builds never write it.
_HERMES_AGENT_TREES = (
    "/var/lib/hal0/venvs/hermes",
    "/var/lib/hal0/hermes",
    "/var/lib/hal0/.hermes",
    "/var/lib/hal0/state/agents/hermes",
)


def _ensure_hermes_writable_or_die() -> None:
    """Abort with a sudo hint when provisioning can't write its trees.

    ``hal0 agent install hermes`` provisions into root-owned ``/var/lib/hal0``
    and is built to run as root on a system install — it then drops the
    provisioning pipeline to the ``hal0`` user so the trees are born hal0:hal0
    (:func:`_provision_hermes`). Run as a normal login user it used to crash
    several phases into the bootstrap with a raw ``PermissionError`` and leave
    half-owned trees behind (observed on a Fedora install). Catch the privilege
    mismatch up front, before the toolchain shell-out, so we abort cleanly with
    no side effects.

    No-op when we're root (root provisions, dropping to hal0 for the writes) or
    on a dev / rootless install that already owns the trees (the probe passes).
    """
    import os as _os

    from hal0.agents.hermes_provision import path_is_writable

    if _os.geteuid() == 0:
        return
    blocked = [t for t in _HERMES_AGENT_TREES if not path_is_writable(t)]
    if not blocked:
        return
    die(
        "Hermes provisioning needs write access to "
        + ", ".join(blocked)
        + f", but you're running as uid={_os.getuid()} and those live under "
        "root-owned /var/lib/hal0.\n\n"
        "Re-run as root:\n    sudo hal0 agent install hermes\n\n"
        f"(Provisioning runs as root, then drops to the "
        f"'{_AGENT_RUNTIME_USER}' agent user to write the trees born-owned.)"
    )


# _chown_hermes_trees_to_agent_user removed (§7.4 F.7): provisioning drops to
# hal0 (see _provision_hermes below), so the trees are born hal0:hal0 with no
# chown-back needed.


# ── §7.4 privilege drop: provision as the hal0 service user ──────────────────
#
# The Hermes bootstrap pipeline writes $HERMES_HOME (config.yaml, personas,
# context, provision.json) plus — through the sudo seams —
# root:root artifacts. To make those HERMES_HOME writes born hal0:hal0 (instead
# of root-then-chown), the provisioning step drops to the hal0 user whenever it
# is invoked as root: run the root-only prelude (install the /usr/local/bin
# wrapper + ensure the setgid hal0-owned parent dirs), then re-exec `hal0 agent
# reprovision hermes` as hal0. This single choke point covers every caller
# (install.sh's `agent install hermes`, a standalone `sudo hal0 agent install
# hermes`, `reprovision`) so F.7 can delete the chown-back
# without stranding any path on root:root files. A non-root (dev / already-hal0)
# caller runs the pipeline in-process unchanged.


#: hal0-owned setgid directories the root prelude creates before it resolves
#: the interpreter (ADR-0032 decision 5): a uv fetch must land in a tree hal0
#: already owns. uv's interpreters and cache live under /var/lib/hal0/hermes.
_HERMES_PRELUDE_DIRS = (
    "/var/lib/hal0",
    "/var/lib/hal0/venvs",
    "/var/lib/hal0/hermes",
    "/var/lib/hal0/state",
    "/var/lib/hal0/state/agents",
)


def _ensure_hermes_prelude_dirs() -> None:
    """Create the hal0-owned setgid parents of every Hermes tree (root only)."""
    import os as _os
    import pwd as _pwd
    from pathlib import Path as _Path

    try:
        ent = _pwd.getpwnam(_AGENT_RUNTIME_USER)
    except KeyError:
        # No hal0 user (dev/rootless layout) — nothing to pre-own.
        return
    # The hal0-owned parents; the provisioner creates its leaves (.hermes,
    # the venvs/hermes link, the releases, state/agents/hermes) under these as
    # hal0 → born hal0:hal0. /var/lib/hal0/hermes holds the release tree, the
    # uv-managed interpreters and uv's cache (ADR-0032 decision 5, ADR-0034
    # decision 2 amendment).
    # A symlink here is left alone: chown/chmod would act on its target. The
    # mkdir, chown and chmod go by descriptor (#2975): these dirs sit inside
    # hal0-owned /var/lib/hal0, so an is_symlink() check followed by a
    # pathname chown let the hal0 account swap a dir for a link in between.
    from hal0.security import nofollow

    for d in _HERMES_PRELUDE_DIRS:
        p = _Path(d)
        if p.is_symlink():
            console.print(f"[dim]hermes prelude: {d} is a symlink; left as is[/dim]")
            continue
        try:
            _os.close(nofollow.open_dir(p, create=True))
            nofollow.chown(p, ent.pw_uid, ent.pw_gid)
            nofollow.chmod(p, 0o2775)
        except OSError as exc:
            # Best-effort: `doctor perms --fix` (install.sh) is the authority for
            # this skeleton, so a gap here usually surfaces downstream as a loud
            # "venv install failed". Surface a hint for diagnosability rather than
            # failing the whole install on a single dir.
            console.print(f"[dim]hermes prelude: could not pre-own {d}: {exc}[/dim]")


def _hand_uv_trees_to_hal0(roots: tuple[Any, ...] | None = None) -> None:
    """``lchown`` the builder's uv interpreter and cache trees to hal0 (root only).

    Through :func:`hal0.security.nofollow.chown_tree` (#2975): the trees sit in
    hal0-owned /var/lib/hal0, and a pathname ``os.walk`` + ``lchown`` protects
    only the last component, so a middle directory swapped for a link mid-walk
    could hand any root file to hal0. ``fwalk`` with ``fchownat`` relative to
    each directory's descriptor never leaves the tree.
    """
    import pwd as _pwd

    from hal0.agents.hermes_layout import HERMES_PYTHON_ROOT, HERMES_UV_CACHE
    from hal0.security import nofollow

    try:
        ent = _pwd.getpwnam(_AGENT_RUNTIME_USER)
    except KeyError:
        return
    for root in roots if roots is not None else (HERMES_PYTHON_ROOT, HERMES_UV_CACHE):
        if root.is_symlink() or not root.is_dir():
            continue
        try:
            nofollow.chown_tree(root, ent.pw_uid, ent.pw_gid)
        except OSError as exc:
            console.print(f"[dim]hermes prelude: could not hand {root} to hal0: {exc}[/dim]")


def _cleanup_pre_layout_python() -> bool:
    """Remove the pre-layout /var/lib/hal0/python once nothing uses it (root only).

    It is root-owned on existing hosts, so only a root path may remove it, and
    only once no release, pre-layout venv, or ``HAL0_HERMES_PYTHON`` value (this
    process' or the persisted one) names an interpreter in it.
    """
    import os as _os

    from hal0.agents.hermes_layout import (
        HERMES_VENV_PATH,
        LEGACY_PYTHON_ROOT,
        remove_legacy_python_if_unused,
    )
    from hal0.agents.hermes_provision import _read_hermes_python_env

    try:
        persisted = _read_hermes_python_env() or ""
    except (ValueError, OSError, UnicodeDecodeError):
        persisted = ""
    prefix = f"{LEGACY_PYTHON_ROOT}/"
    for value in (_os.environ.get("HAL0_HERMES_PYTHON", ""), persisted):
        if value.startswith(prefix):
            return False
    if remove_legacy_python_if_unused(HERMES_VENV_PATH, legacy_root=LEGACY_PYTHON_ROOT):
        console.print("[dim]removed the unused pre-layout /var/lib/hal0/python[/dim]")
        return True
    return False


def _hand_pre_layout_venv_to_hal0() -> None:
    """``lchown`` a real-directory (pre-layout) venvs/hermes to hal0 when it isn't."""
    import pwd as _pwd

    from hal0.agents.hermes_layout import HERMES_VENV_PATH

    venv = HERMES_VENV_PATH
    if venv.is_symlink() or not venv.is_dir():
        return
    try:
        uid = _pwd.getpwnam(_AGENT_RUNTIME_USER).pw_uid
    except KeyError:
        return
    try:
        if venv.stat().st_uid == uid and venv.parent.stat().st_uid == uid:
            return
    except OSError:
        return
    _hand_uv_trees_to_hal0((venv,))


def _hermes_root_prelude(terminal_env: dict[str, str] | None = None) -> None:
    """Root-only prep run before dropping provisioning to hal0 (§7.4).

    Installs the ``/usr/local/bin`` CLI wrapper (which ``_phase_install`` skips
    when it runs as hal0) and ensures the hal0-owned parent directories the
    provisioner writes into exist + are ``hal0:hal0`` setgid, so hal0's writes
    land born-owned. Idempotent; the caller only invokes it when ``euid == 0``.
    """
    import os as _os

    from hal0.agents.hermes_provision import (
        REPO_ROOT_FOR_INSTALLER,
        _install_cli_wrapper,
        _persist_hermes_python,
        pinned_target_minor,
        resolve_hermes_python,
        sweep_stale_agent_dropins_as_root,
    )

    # #2803: the stale hal0-agent@ drop-in sweep is root-only, and install_hermes
    # runs as hal0 after the re-exec, so it must happen here, before the drop.
    sweep_stale_agent_dropins_as_root()

    # Before the interpreter is resolved: a uv fetch writes
    # /var/lib/hal0/hermes/python and must find its parent hal0-owned
    # (ADR-0032 decision 5).
    _ensure_hermes_prelude_dirs()
    # A pre-layout venv left root-owned by a provision from before §7.4 cannot
    # be renamed into the release layout by the hal0 builder (a directory
    # moving to a new parent needs write permission on itself). Hand it to
    # hal0 here, as root, so the first migration can move it.
    _hand_pre_layout_venv_to_hal0()

    try:
        # The pinned ref decides the minor (3.12 for 3ef6bbd2, 3.14 for a
        # ref that admits it), so a valid persisted value for it is kept.
        minor = pinned_target_minor()
        hermes_python = resolve_hermes_python(minor=minor)
        _persist_hermes_python(hermes_python, minor=minor)
        _os.environ["HAL0_HERMES_PYTHON"] = hermes_python
    except (RuntimeError, ValueError) as exc:
        console.print(f"[yellow]hermes Python resolver hint:[/yellow] {exc}")
        raise
    finally:
        # A uv fetch run here, as root, writes root-owned files into the
        # builder's hal0-owned tree; hand them back so the hal0 builder can
        # prune them later.
        _hand_uv_trees_to_hal0()

    _cleanup_pre_layout_python()

    wrapper_src = REPO_ROOT_FOR_INSTALLER / "installer" / "wrappers" / "hermes"
    if wrapper_src.is_file():
        try:
            _install_cli_wrapper(wrapper_src)
        except OSError as exc:
            console.print(f"[yellow]hermes wrapper pre-install hint:[/yellow] {exc}")

    # Terminal-tool posture (#1863): resolve and record it HERE, as root. The
    # marker lives in root-only /etc/hal0/agents/, deliberately out of reach of
    # the `hal0` user the agent runs as, so the unprivileged provisioner below
    # can only read it. This is also where a pre-#1863 box's existing setting
    # gets captured into the marker for the first time.
    from hal0.agents.hermes_provision import HERMES_HOME_DEFAULT, persist_terminal_decision

    # ``terminal_env`` carries the answer the CLI resolved (flag first, then an
    # inherited env value). Without it the prelude would resolve from this
    # process' environment alone and record the OLD posture, which the dropped
    # child then cannot correct — the marker is root-only by design.
    enabled, why, recorded = persist_terminal_decision(
        HERMES_HOME_DEFAULT, env={**_os.environ, **(terminal_env or {})}
    )
    if not recorded:
        console.print(
            "[yellow]could not record the Hermes terminal-tool decision at "
            "/etc/hal0/agents/ — provisioning will fall back to its default-off "
            "rule.[/yellow]"
        )
    elif enabled and why == "opt-in":
        console.print(
            "[yellow]Hermes terminal tool ENABLED — the agent will be able to run "
            "commands as the hal0 user, which is root-equivalent on this box.[/yellow]"
        )

    _mirror_default_key_as_root()


def _mirror_default_key_as_root() -> None:
    """Mirror the vault's default gateway API key into ``$HERMES_HOME/.env``.

    Only root can read the vault, and the provisioner that follows the prelude
    runs as hal0, so this is the one place a box provisioned before the mirror
    existed gets a default key hal0-api can read (ADR-0035, fact 5). Idempotent:
    a usable key already in config or ``.env`` is left alone. A fresh install,
    where the vault does not exist yet, is covered later by the provisioner's
    ``gateway_secrets_wire`` phase, which mirrors the key it mints.
    """
    from hal0.agents import hermes_api

    try:
        if hermes_api.ensure_default_key(hermes_api.hermes_home()):
            console.print("[dim]mirrored the Hermes gateway API key into $HERMES_HOME/.env[/dim]")
    except OSError as exc:
        console.print(f"[yellow]hermes API key mirror hint:[/yellow] {exc}")


def _agent_home() -> str:
    """The hal0 service user's home dir (``/var/lib/hal0`` fallback)."""
    return hal0_service_home()


def _running_hal0_bin() -> str:
    """The ``hal0`` console script belonging to the code that is running (#2092).

    The body lives in :func:`hal0.system.seam.running_hal0_bin` so ``hal0.api``
    can spawn the same binary without importing this CLI module.
    """
    return running_hal0_bin()


def _run_as_hal0(
    argv: list[str], *, stdin: int | None = None, extra_env: dict[str, str] | None = None
) -> int:
    """Run ``argv`` as the hal0 service user, returning its exit code.

    Sanitizes the env (strips ``HERMES_HOME``, sets ``HOME`` to hal0's home) and
    picks a privilege-drop tool (``runuser`` → ``setpriv`` → ``sudo``), mirroring
    ``installer/lib/run-as-hal0.sh`` but as a subprocess (that guard ``exec``s).
    All three tools wrap the command in the same ``env_argv`` so HOME/HERMES_HOME
    are set identically regardless of which tool is available. The caller
    guarantees ``euid == 0``.

    ``stdin`` is forwarded to ``subprocess.run`` (e.g. ``subprocess.DEVNULL`` for
    a caller like ``hermes gateway install`` that prompts interactively with no
    bypass flag — a real TTY would block, and a closed fd 0 crashes it).

    ``extra_env`` is passed as explicit ``NAME=VALUE`` assignments on the same
    ``env`` prefix, so a provisioning answer survives the privilege drop on all
    three tools — the ``sudo`` fallback resets the environment, and relying on
    inheritance would silently lose it there (see HAL0_HERMES_TERMINAL).
    """
    import shutil as _shutil
    import subprocess as _subprocess

    home = _agent_home()
    assignments = [f"{k}={v}" for k, v in sorted((extra_env or {}).items())]
    env_argv = ["env", "-u", "HERMES_HOME", f"HOME={home}", *assignments, *argv]
    if _shutil.which("runuser"):
        cmd = ["runuser", "-u", _AGENT_RUNTIME_USER, "--", *env_argv]
    elif _shutil.which("setpriv"):
        cmd = [
            "setpriv",
            "--reuid",
            _AGENT_RUNTIME_USER,
            "--regid",
            _AGENT_RUNTIME_USER,
            "--init-groups",
            "--",
            *env_argv,
        ]
    else:
        # env_argv already sets HOME explicitly, so no -H needed (and HOME is
        # consistent with the runuser/setpriv paths).
        cmd = ["sudo", "-u", _AGENT_RUNTIME_USER, "--", *env_argv]
    return _subprocess.run(cmd, stdin=stdin, check=False).returncode  # nosec B603 — fixed argv


def _provision_hermes(
    *,
    repair: bool = False,
    offline: bool = False,
    verbose: bool = False,
    terminal_tool: bool | None = None,
) -> int:
    """Run the Hermes bootstrap pipeline, dropping to hal0 first when root (§7.4).

    Non-root (dev / already-hal0): call ``bootstrap_cli`` in-process. Root: run
    the root-only prelude, then re-exec ``hal0 agent reprovision hermes`` as hal0
    so every provisioning write is born ``hal0:hal0``.

    ``terminal_tool`` is the operator's explicit answer to the terminal-tool
    opt-in (``None`` = not asked → the provisioner's default-off / preserve-
    existing rule). It travels as ``HAL0_HERMES_TERMINAL`` because the
    provisioning that consumes it runs in a re-exec'd, privilege-dropped child.

    ``offline`` travels as ``--offline``, which sets ``HAL0_HERMES_OFFLINE`` in
    the child, where the release builder reads it.
    """
    import os as _os

    from hal0.agents.hermes_provision import HERMES_TERMINAL_ENV, bootstrap_cli

    report_before = _last_report_completed_at()

    # The flag wins; otherwise forward an inherited answer (install.sh exports
    # it after prompting) explicitly, since the sudo drop path resets the env.
    if terminal_tool is not None:
        provision_env = {HERMES_TERMINAL_ENV: str(int(terminal_tool))}
    elif _os.environ.get(HERMES_TERMINAL_ENV):
        provision_env = {HERMES_TERMINAL_ENV: _os.environ[HERMES_TERMINAL_ENV]}
    else:
        provision_env = {}

    if _os.geteuid() != 0:
        # In-process path: the pipeline reads the answer off the environment, so
        # set it for the duration and put it back — a permanent mutation of this
        # process' env would outlive the call and colour a later one.
        previous = _os.environ.get(HERMES_TERMINAL_ENV)
        _os.environ.update(provision_env)
        previous_offline = _os.environ.get("HAL0_HERMES_OFFLINE")
        if offline:
            _os.environ["HAL0_HERMES_OFFLINE"] = "1"
        try:
            rc = bootstrap_cli(repair=repair, verbose=verbose)
        finally:
            if offline and previous_offline is None:
                _os.environ.pop("HAL0_HERMES_OFFLINE", None)
            if provision_env:
                if previous is None:
                    _os.environ.pop(HERMES_TERMINAL_ENV, None)
                else:
                    _os.environ[HERMES_TERMINAL_ENV] = previous
    else:
        _hermes_root_prelude(provision_env)
        # #2092: the running tree, not PATH's — see _running_hal0_bin.
        hal0_bin = _running_hal0_bin()
        argv = [hal0_bin, "agent", "reprovision", "hermes"]
        if repair:
            argv.append("--repair")
        if offline:
            argv.append("--offline")
        if verbose:
            argv.append("--verbose")
        # The parent owns the restart: the child reads the same report and
        # would bounce both consumers a second time for one change.
        rc = _run_as_hal0(argv, extra_env={**provision_env, _RESTART_OWNER_ENV: "parent"})

    if _os.environ.get(_RESTART_OWNER_ENV) == "parent":
        return rc
    # A posture change only reaches the model once the process re-reads its
    # config. Done HERE rather than in _install_hermes so every provisioning
    # entry point is covered — `install` and `reprovision` both
    # land in this function and all now honour an explicit answer.
    # A gate THIS run wrote restarts the consumers even when a later phase
    # (smoke tests) failed the run: the mutation is on disk, and a live gateway
    # would keep the backend it loaded (#2878).
    if (rc == 0 and provision_env) or _terminal_gate_changed_in_last_report(since=report_before):
        _restart_hermes_after_posture_change()
    return rc


#: Set for the privilege-dropped re-exec so only the root parent restarts Hermes.
_RESTART_OWNER_ENV = "HAL0_HERMES_RESTART_OWNER"


def _load_last_report() -> dict[str, Any] | None:
    import json

    try:
        data = json.loads(_agent_provision_state_file("hermes").read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    return data if isinstance(data, dict) else None


def _last_report_completed_at() -> str | None:
    data = _load_last_report()
    if not data:
        return None
    return str(data.get("completed_at") or "") or None


def _terminal_gate_changed_in_last_report(*, since: str | None = None) -> bool:
    """Did the run that just finished change ``terminal.backend``?

    A box with a recorded OFF decision and a pre-#2878 ``local`` backend gets
    the sentinel from a plain ``--repair`` with no terminal flag; the running
    gateway keeps serving the loaded backend, and its side agents the shell,
    until it re-reads its config. The phases record the mutation in the
    last-run report; read it back rather than trust the flag alone. ``since``
    is the previous report's ``completed_at``: a report not newer than it is
    the previous run's, not this one's, and counts as no change.
    """
    data = _load_last_report()
    if data is None:
        return False
    if since is not None and str(data.get("completed_at") or "") <= since:
        return False
    phases = data.get("phases") or {}
    return any(
        bool(((phases.get(name) or {}).get("details") or {}).get("terminal_backend_changed"))
        for name in ("config_write", "profile_mcp_wire", "brain_profile_mcp_wire")
    )


def _enable_and_start_hermes_unit() -> None:
    """`systemctl enable --now hal0-agent@hermes` so the agent runs + persists.

    No-op when systemd isn't present (containers / dev). A non-zero rc is
    surfaced as a hint rather than failing the install — the agent is
    provisioned either way and can be started manually.

    DELIBERATELY BARE — not routed through the hal0-systemctl privilege seam,
    unlike ``HermesDriver._stop_services``. Three reasons, judged for this site
    specifically:

    1. *Interactive by construction.* This runs only from
       ``hal0 agent install hermes``, a foreground command a human admin runs
       on the host (it has already shelled out to ``hermes-prereqs.sh`` to
       install OS packages). A polkit prompt here is legitimate and
       answerable — someone is at the terminal. The uninstall/daemon path is
       the opposite: nobody is there to answer, so it must never prompt.
    2. *Already loud.* A cancelled prompt or a denied escalation returns
       non-zero and prints a remedy below. The seam fix was about a path that
       SWALLOWED the failure; this one does not.
    3. *Keeping the grant asymmetric on purpose.* The seam exposes only
       ``stop-agent``/``disable-agent``. Adding ``enable``/``start`` would let
       the unprivileged ``hal0`` service user bring agent units up at boot —
       a persistence primitive — in exchange for convenience on a path that
       does not need it. Stop/disable only ever reduces what is running.
    """
    import shutil as _shutil
    import subprocess as _subprocess

    if _shutil.which("systemctl") is None:
        return
    rc = _subprocess.run(  # nosec B603 B607 — fixed argv
        ["systemctl", "enable", "--now", "hal0-agent@hermes"], check=False
    ).returncode
    if rc != 0:
        console.print(
            "[yellow]Hermes provisioned, but the agent unit didn't start cleanly — "
            "check `systemctl status hal0-agent@hermes` / `hal0 agent log hermes`.[/yellow]"
        )


# ── Gateway (Telegram/Discord) — issue #1102 / Q9 ────────────────────────────
#
# installer/install.sh wires the gateway inline (only reached when THAT run
# just provisioned or already-provisioned Hermes). A box that skipped Hermes
# at install time (HAL0_SKIP_HERMES=1) and installs it later via this CLI
# never hit that bash block, so the gateway silently stayed unconfigured.
# Porting the same steps here closes that gap — "install now" and "install
# later" converge on identical wiring.

_HERMES_BIN = f"{_HERMES_AGENT_TREES[0]}/bin/hermes"
_HERMES_GATEWAY_UNIT = "/etc/systemd/system/hermes-gateway.service"


def _hermes_venv_ready() -> bool:
    """``-x /var/lib/hal0/venvs/hermes/bin/hermes`` — install.sh's own gate
    for whether Hermes is actually provisioned before touching the gateway."""
    from pathlib import Path as _Path

    return _Path(_HERMES_BIN).is_file()


def _gateway_enable_state(unit: str) -> str:
    """``systemctl is-enabled <unit>``: the state word, or ``not-found``.

    Read BEFORE anything lays down, refreshes or enables the unit, so a re-run
    can tell an operator's off switch from a first install (#2815, mirrors
    installer/install.sh ``unit_enable_state``). ``is-enabled`` is read-only
    and needs no privilege, so it stays a bare call like the rest of this
    section; the hal0-systemctl seam (and its allow-list) is untouched.
    """
    import subprocess as _subprocess

    try:
        proc = _subprocess.run(  # nosec B603 B607 — fixed argv
            ["systemctl", "is-enabled", unit],
            capture_output=True,
            text=True,
            check=False,
        )
    except OSError:
        return "not-found"
    out = (getattr(proc, "stdout", "") or "").strip()
    return out.splitlines()[0] if out else "not-found"


def _gateway_unit_active(unit: str) -> bool:
    import subprocess as _subprocess

    try:
        rc = _subprocess.run(  # nosec B603 B607 — fixed argv
            ["systemctl", "is-active", "--quiet", unit], check=False
        ).returncode
    except OSError:
        return False
    return rc == 0


def gateway_turn_on_command(unit: str, state: str) -> str:
    """The command that turns a gateway in ``state`` on (shared with ``hal0 doctor``).

    A masked gateway has no unit file to enable: a persistent mask is the /etc
    unit path itself, and systemd reports a runtime mask only while there is no
    /etc file. After the unmask, ``hal0 agent install hermes`` lays the unit
    down and turns it on, so that is the remedy, not ``systemctl enable --now``
    (#3025, #3030). A runtime mask lives under /run, so only ``unmask --runtime``
    removes it.
    """
    if state == "masked":
        return f"systemctl unmask {unit} && sudo hal0 agent install hermes"
    if state == "masked-runtime":
        return f"systemctl unmask --runtime {unit} && sudo hal0 agent install hermes"
    return f"systemctl enable --now {unit}"


def _gateway_left_off_note(unit: str, state: str) -> None:
    """Report that ``unit`` was left off and how to turn it on (install.sh
    ``unit_left_off_note``)."""
    turn_on = gateway_turn_on_command(unit, state)
    console.print(f"[yellow]{unit} is {state}; left as is (run '{turn_on}' to turn it on)[/yellow]")


def _normalise_gateway_unit_or_warn(has_systemctl: bool, *, turn_off: bool = True) -> bool:
    """Run the gateway unit normaliser; on failure warn and leave the unit off.

    Returns ``True`` when the unit names only stable paths. A unit that still
    names a release path is disabled and stopped, the same posture this command
    takes when the generator itself fails, because that release can be pruned
    from under it.
    """
    import subprocess as _subprocess
    from pathlib import Path as _Path

    from hal0.agents.hermes_gateway_unit import normalise_gateway_unit

    result = normalise_gateway_unit(_Path(_HERMES_GATEWAY_UNIT), reload=has_systemctl)
    if result.ok:
        if result.changed:
            console.print(
                "[dim]hermes-gateway unit normalised to /var/lib/hal0/hermes/current/[/dim]"
            )
        return True
    detail = result.error or "; ".join(result.remaining)
    console.print(
        f"[yellow]hermes-gateway unit could not be normalised ({detail}) — leaving the "
        "gateway disabled. Re-run `sudo hal0 agent install hermes`.[/yellow]"
    )
    if has_systemctl and turn_off:
        _subprocess.run(  # nosec B603 B607 — fixed argv
            ["systemctl", "disable", "--now", "hermes-gateway.service"], check=False
        )
    return False


def _warn_if_linger_failed(message: str) -> None:
    """Warn when the gateway install's message reports a failed enable-linger."""
    if "enable-linger" in message and "failed" in message:
        console.print(
            "[yellow]linger for hal0 is off: Hermes cron and kanban workers need it; "
            "run 'sudo loginctl enable-linger hal0'.[/yellow]"
        )


def _migrate_past_mask() -> None:
    """Move a gateway off hal0's suffixed unit while the canonical unit is masked.

    ``migrate_hashed_units`` never writes through the mask; it stops, disables
    and removes the suffixed unit, so a gateway it ran stops polling (#2950).
    Best-effort: needs root, warns on failure.
    """
    import os as _os
    from pathlib import Path as _Path

    from hal0.agents.hermes_gateway_regen import (
        GatewayRegenError,
        hal0_hashed_units,
        migrate_hashed_units,
    )

    if not hal0_hashed_units(_Path(_HERMES_GATEWAY_UNIT).parent):
        return
    if _os.geteuid() != 0:
        console.print(
            "[yellow]a suffixed hermes gateway unit is still in place next to the mask: "
            "removing it needs root (sudo hal0 agent install hermes).[/yellow]"
        )
        return
    try:
        moved = migrate_hashed_units(unit_path=_Path(_HERMES_GATEWAY_UNIT))
    except GatewayRegenError as exc:
        console.print(
            f"[yellow]the suffixed hermes gateway unit could not be moved off ({exc}); "
            "re-run 'sudo hal0 agent install hermes'.[/yellow]"
        )
        return
    console.print(f"[dim]{moved.message}[/dim]")


def _install_hermes_gateway() -> None:
    """Install ``hermes-gateway.service`` from hal0's template + enable the unit.

    Ports installer/install.sh's post-provision gateway block
    (``hal0.agents.hermes_gateway_regen install`` through the ``systemctl
    enable --now hermes-gateway`` + wait_active check). Upstream's generator
    is not run (#2943).
    Best-effort throughout: every failure mode here warns and returns rather
    than raising, matching install.sh's ``|| warn ... continuing`` posture —
    a broken gateway must never be reported as a failed Hermes install.
    """
    import os as _os
    import shutil as _shutil
    import subprocess as _subprocess
    from pathlib import Path as _Path

    if not _hermes_venv_ready():
        # Provisioning didn't complete (or failed upstream) — nothing to
        # wire the gateway onto. The caller already surfaced that failure.
        return

    # The operator's on/off choice, captured BEFORE the generator runs or the
    # unit is enabled (#2815): re-running this command (e.g. to repair the
    # venv) must not reconnect a gateway that was turned off.
    unit = "hermes-gateway.service"
    has_systemctl = _shutil.which("systemctl") is not None
    # The gateway the operator actually has, by the same rule the move uses
    # (hermes_gateway_regen.canonical_owns_state): on a box a 1.6.0 nightly
    # installed that is upstream's suffixed unit, whose on/off state the
    # template install carries over (#2943), and so it is after a move that
    # was killed half way, which left a disabled, stopped canonical unit
    # beside the enabled suffixed one (#2950).
    from hal0.agents.hermes_gateway_regen import gateway_state_unit

    probe_unit = (
        gateway_state_unit(unit_path=_Path(_HERMES_GATEWAY_UNIT)) if has_systemctl else None
    ) or unit
    state = _gateway_enable_state(probe_unit) if has_systemctl else "not-found"
    disabled = state in ("disabled", "masked-runtime")
    was_active = has_systemctl and disabled and _gateway_unit_active(probe_unit)
    if state in ("masked", "masked-runtime"):
        # Never run the generator over a mask. A persistent mask is the /etc
        # unit path itself (a /dev/null symlink): writing through it would
        # unmask the unit. A runtime mask lives under /run and only holds while
        # there is no /etc file (systemd reads /etc first, and reports
        # `disabled` once one exists), so writing the /etc file would shadow it
        # and turn the gateway back on (#3024).
        # A suffixed gateway still running next to the mask is moved off all
        # the same: the move keeps the mask and stops and removes the
        # suffixed unit, so the mask means off (#2950).
        _migrate_past_mask()
        _gateway_left_off_note(unit, state)
        return

    console.print("[bold]Installing hermes gateway[/bold] (Telegram/Discord bridge)…")

    # Lay the hal0 secrets drop-in down BEFORE `hermes gateway install` starts
    # the unit. `hermes gateway install </dev/null` defaults to start-now, so
    # the vanilla unit goes ACTIVE immediately; hal0 flags a system gateway
    # "foreign" iff it's active with no drop-in present (_detect_foreign_gateways
    # below). Without pre-writing, hal0 would mistake its OWN just-started unit
    # for a foreign poller and skip the enable, leaving the bridge unwired. The
    # bootstrap's gateway_secrets_wire phase already writes this on the fresh
    # path; this call makes the ordering hold on the deferred/standalone path
    # too. Best-effort (non-root/pytest → skipped; write error → reported).
    try:
        from hal0.agents.hermes_provision import write_gateway_secrets_dropin

        dropin = write_gateway_secrets_dropin()
        if dropin.outcome == "failed":
            console.print(
                f"[yellow]gateway secrets drop-in not written ({dropin.reason}) — "
                "continuing; the bridge may come up unwired.[/yellow]"
            )
    except Exception as exc:  # drop-in wiring is strictly best-effort
        console.print(f"[yellow]gateway secrets drop-in pre-write skipped: {exc}[/yellow]")

    # HERMES_HOME is unset so the generator bakes the hal0 default
    # (~/.hermes) rather than a value inherited from the invoking shell.
    #
    # #2061 fix (reverts the m1 drop-to-hal0): `hermes gateway install
    # --system` checks os.geteuid() == 0 INTERNALLY and refuses a non-root
    # caller ("System gateway install requires root. Re-run with sudo."),
    # so routing the root path through `_run_as_hal0` made the bridge
    # sub-install fail on every root install — the same regression PR
    # #1337 already fixed once in installer/install.sh's inline block.
    # Run AS ROOT directly, exactly like install.sh: the #843 stray
    # /root/.hermes safeguard is satisfied by env alone — HOME pinned to
    # hal0's home + HERMES_HOME unset — so `~/.hermes` resolves under
    # /var/lib/hal0, never /root. `--run-as-user hal0` only names the
    # runtime user baked into the unit; it is not a privilege drop.
    # hal0 lays the unit down from its own template (#2943). Running upstream's
    # `hermes gateway install --system` as root under v0.22.4 names the unit
    # hermes-gateway-<hash>.service (root's account home is not hal0's), bakes
    # the release path it ran from and creates a stray /root/.hermes. The
    # template never enables or starts the unit; the off switch below and the
    # enable logic further down decide that, as before.
    from hal0.agents.hermes_gateway_regen import (
        GatewayCleanupError,
        GatewayRegenError,
        hal0_hashed_units,
        install_gateway_unit,
    )

    if _os.geteuid() != 0:
        console.print(
            "[yellow]hermes gateway unit not installed: it needs root "
            "(sudo hal0 agent install hermes) — continuing.[/yellow]"
        )
        return
    try:
        installed = install_gateway_unit()
    except GatewayCleanupError as exc:
        # The move itself finished: hermes-gateway.service already carries the
        # gateway's on/off state. Only the stopped, disabled suffixed file is
        # left; saying the canonical unit "is not enabled" would be wrong
        # (#2950 review).
        console.print(
            f"[yellow]hermes gateway moved to {unit}, but the old suffixed unit "
            f"could not be removed ({exc}); re-run 'sudo hal0 agent install hermes' "
            "to remove it.[/yellow]"
        )
        # install_gateway_unit still ran its tail (linger) before raising.
        if exc.result is not None:
            console.print(f"[dim]{exc.result.message}[/dim]")
            _warn_if_linger_failed(exc.result.message)
        return
    except GatewayRegenError as exc:
        what = (
            "hermes gateway unit written, but its install did not finish"
            if _Path(_HERMES_GATEWAY_UNIT).is_file()
            else "hermes gateway unit not installed"
        )
        # Never enable the canonical unit while a suffixed unit is still there:
        # a failed move's rollback may have put that gateway back, and enabling
        # would start a second poller on the same tokens (#2946 review). Any
        # other failure (a plain daemon-reload, a stale-unit restart) keeps the
        # old behaviour and goes on to apply the operator's on/off decision.
        if hal0_hashed_units(_Path(_HERMES_GATEWAY_UNIT).parent):
            console.print(
                f"[yellow]{what} ({exc}) — the suffixed gateway unit is still in place, "
                "so hermes-gateway.service is not enabled; re-run "
                "'sudo hal0 agent install hermes' to retry the move.[/yellow]"
            )
            return
        console.print(
            f"[yellow]{what} ({exc}) — Telegram/Discord bridge may be unavailable; "
            "continuing.[/yellow]"
        )
        if disabled:
            return
    else:
        console.print(f"[dim]{installed.message}[/dim]")
        _warn_if_linger_failed(installed.message)
    if not _normalise_gateway_unit_or_warn(has_systemctl, turn_off=not disabled):
        return

    if disabled:
        # Operator turned it off: refresh done, enable skipped. A disabled but
        # running gateway (started by hand) is left running.
        if has_systemctl:
            _subprocess.run(["systemctl", "daemon-reload"], check=False)  # nosec B603 B607
            # Backstop for a generator that enabled or started it anyway
            # (install.sh): a disabled-but-running gateway keeps running.
            off_argv = ["systemctl", "disable"] + ([] if was_active else ["--now"]) + [unit]
            _subprocess.run(off_argv, check=False)  # nosec B603 B607
            _gateway_left_off_note(unit, state)
            if was_active:
                console.print(f"[dim]{unit} was running; left running[/dim]")
        return

    if not _Path(_HERMES_GATEWAY_UNIT).is_file():
        console.print(
            f"[yellow]hermes-gateway unit not installed ({_HERMES_GATEWAY_UNIT} missing) — "
            "Telegram/Discord bridge unavailable.[/yellow]\n"
            "[dim]retry with: sudo hal0 agent install hermes[/dim]"
        )
        return

    if _shutil.which("systemctl") is None:
        return

    # Don't start a SECOND poller. If a foreign hermes-gateway is live (a
    # hand-installed system unit, or a `systemctl --user` unit under another
    # user), enabling hal0's unit too puts two long-polls on one Telegram
    # token → HTTP 409 flapping. Skip the enable + tell the operator how to
    # stop the other one. Best-effort: detection errors fall through to enable.
    try:
        from hal0.agents.hermes_provision import _detect_foreign_gateways

        foreign = _detect_foreign_gateways()
    except Exception:  # detection is strictly best-effort
        foreign = []
    if foreign:
        console.print(
            "[yellow]foreign hermes-gateway detected — NOT enabling hal0's unit "
            "(two pollers on one token → HTTP 409). Stop the other one first:[/yellow]"
        )
        for f in foreign:
            console.print(f"[dim]  {f['detail']}\n  stop: {f['stop_cmd']}[/dim]")
        return

    # Also deliberately bare — same interactive-CLI reasoning as
    # _enable_and_start_hermes_unit (see its docstring), plus one specific to
    # this unit: hermes-gateway.service is neither a hal0-agent@ nor a
    # hal0-slot@ instance, so the seam has no verb that could reach it. The
    # seam's posture is "build the unit name here from a validated id"; a fixed
    # foreign unit would need a new literal-only verb (like restart-self),
    # which this path does not need — it runs from `hal0 agent install hermes`
    # in the foreground, where a prompt is answerable, and it surfaces a
    # non-zero rc below instead of swallowing it.
    #
    # (The gateway's secrets DROP-IN write does route through the seam —
    # `write-gateway-dropin` — because that one is reached by the provisioner
    # running as the unprivileged hal0 user. Different caller, different rule.)
    _subprocess.run(["systemctl", "daemon-reload"], check=False)  # nosec B603 B607
    # enabled-runtime lasts until the next boot; keep it that way (#2946 review).
    enable_argv = ["systemctl", "enable", "--now", "hermes-gateway.service"]
    if state == "enabled-runtime":
        enable_argv.insert(2, "--runtime")
    rc = _subprocess.run(enable_argv, check=False).returncode  # nosec B603 B607
    if rc != 0:
        console.print(
            "[yellow]hermes-gateway enable returned non-zero — continuing "
            "(check `journalctl -u hermes-gateway -n 40`).[/yellow]"
        )
        return

    if _wait_active_unit("hermes-gateway.service", timeout=20.0):
        console.print("[dim]hermes-gateway is running (Telegram/Discord)[/dim]")
    else:
        console.print(
            "[yellow]hermes-gateway not yet active; check "
            "`journalctl -u hermes-gateway -n 40`.[/yellow]"
        )


def _wait_active_unit(unit: str, timeout: float = 15.0) -> bool:
    """Poll ``systemctl is-active --quiet <unit>`` — Python port of
    installer/install.sh's ``wait_active`` bash helper."""
    import subprocess as _subprocess
    import time as _time

    deadline = _time.monotonic() + timeout
    while _time.monotonic() < deadline:
        rc = _subprocess.run(  # nosec B603 B607
            ["systemctl", "is-active", "--quiet", unit], check=False
        ).returncode
        if rc == 0:
            return True
        _time.sleep(0.5)
    return False


@app.command("uninstall")
def agent_uninstall(
    name: str = typer.Argument(..., help="Bundled agent name."),
    keep_memory: bool = typer.Option(
        False,
        "--keep-memory",
        help=(
            "Preserve the agent's private:<agent_id> memory namespace + "
            "its identity card. Default: full teardown including memory."
        ),
    ),
    force: bool = typer.Option(
        False,
        "--force",
        "-f",
        help="Skip confirmation prompt.",
    ),
) -> None:
    """Uninstall a bundled agent."""
    if not force:
        memory_note = (
            "its memory will be preserved"
            if keep_memory
            else "this also deletes its private memory"
        )
        typer.confirm(
            f"Uninstall agent {name!r}? {memory_note} — this cannot be undone.",
            abort=True,
        )

    # pi tears down locally for the same reason it installs locally (see
    # _install_pi): the profile lives in the invoking user's home, out of
    # the daemon's reach. No memory branch — pi has no private-namespace
    # identity card (it talks to the shared memory MCP surface only).
    if name == "pi":
        if os.geteuid() != 0:
            die("pi was provisioned into the invoking user's home — run as root to uninstall.")
            return
        from hal0.agents.manager import AgentManager

        if AgentManager().uninstall("pi"):
            console.print("[bold]Uninstalled[/bold] pi.")
        else:
            console.print("[dim]pi was not installed.[/dim]")
        return

    url = _api_base()
    if _api_unreachable(url):
        raise typer.Exit(1)

    # Memory cleanup BEFORE we tear down the agent surface so a failed
    # memory call doesn't leave half-state. Skipped on --keep-memory
    # (per #246 — re-install reuses the existing card).
    if name == "hermes" and not keep_memory:
        outcome = _uninstall_hermes_memory()
        _warn_memory_outcome(outcome)

    try:
        result = api_delete(f"/api/agents/{name}")
    except CliApiError as exc:
        die(str(exc))
        return
    status = (result or {}).get("status", "uninstalled")
    if status == "not_installed":
        console.print(f"[dim]{name} was not installed.[/dim]")
    else:
        console.print(f"[bold]Uninstalled[/bold] {name}.")
        if keep_memory:
            console.print("[dim](memory preserved — re-install will reuse it)[/dim]")
    if name == "hermes":
        _disable_hal0_linger()


def _disable_hal0_linger() -> None:
    """Undo the linger ``hermes gateway install`` enabled (ADR-0034 decision 6).

    The daemon runs as ``hal0`` and cannot change logind state, so the CLI
    does it, as root. Off root it prints the command instead. Best-effort:
    a failure is reported and never fails the uninstall.
    """
    from hal0.system import linger

    state = linger.read_linger()
    if state.enabled is False:
        return
    if os.geteuid() != 0:
        console.print(
            "[yellow]systemd linger may still be on for hal0[/yellow] — "
            "run `sudo loginctl disable-linger hal0`."
        )
        return
    ok, message = linger.disable_linger()
    if ok:
        console.print(f"[dim]{message}[/dim]")
    else:
        console.print(f"[yellow]Could not disable linger for hal0:[/yellow] {message}")


# Outcomes of ``_uninstall_hermes_memory``. See #350 — the bare-swallowed
# exception used to mask cases where the delete reported OK but the dataset
# still held leftover rows (observed 2026-05-26: 9 hermes-agent identity
# cards survived a default uninstall, operator got no signal). The four
# cases below let the CLI distinguish "all clear" from "memory was down"
# from "delete lied" without changing the idempotent exit-0 contract.
MemoryOutcomeStatus = Literal["deleted", "not_found", "unreachable", "leftover"]


@dataclass(frozen=True)
class MemoryUninstallOutcome:
    """Structured result from ``_uninstall_hermes_memory``.

    Attributes
    ----------
    outcome
        One of ``deleted`` / ``not_found`` / ``unreachable`` / ``leftover``.
        ``deleted`` = delete request succeeded AND the post-verify search
        found zero surviving rows. ``not_found`` = the pre-delete search
        already saw zero matching rows (true no-op). ``unreachable`` =
        the memory API couldn't be reached on either the search or the
        delete call. ``leftover`` = delete returned OK but a post-delete
        verify search still saw matching rows (the bug observed in #350).
    deleted_count
        Number of rows the delete call was issued against. Zero for
        ``not_found`` / ``unreachable`` (we never got to the delete).
    leftover_count
        Rows the post-delete verify search saw matching
        ``agent_id=hermes`` (the identity card's real slug — see
        ``BootstrapState.agent_id``). ``None`` when the verify call
        itself couldn't run (transport error, unparseable response,
        etc.) — distinct from a confirmed zero.
    url
        The hal0 API base we tried (handy in stderr warnings so the
        operator knows what endpoint to check).
    """

    outcome: MemoryOutcomeStatus
    deleted_count: int
    leftover_count: int | None
    url: str


def _uninstall_hermes_memory() -> MemoryUninstallOutcome:
    """Best-effort: delete the hermes identity card from the `agents` dataset.

    Failure is tolerated (memory unreachable shouldn't strand the operator
    with a half-uninstalled agent — same contract as before #350), but
    surfaces the outcome to the caller so the CLI can warn on unreachable
    or leftover-row cases. The CLI always exits 0 regardless of outcome.
    """
    import json as _json
    import urllib.error
    import urllib.request

    # The identity card is written by ``_build_identity_card`` in
    # hermes_provision.py with ``metadata.agent_id = state.agent_id``,
    # whose dataclass default is ``"hermes"`` (BootstrapState.agent_id).
    # This helper used to hardcode the pre-rename "hermes-agent" id,
    # which meant it could never find (or delete) the real card, and
    # every search/delete round-trip touched the memory API under a
    # stray "hermes-agent" identity that was never actually provisioned
    # (observed live 2026-07-11: stray private:hermes-agent + agents
    # bank rows from a bootstrap smoke test's uninstall phase). Reuse
    # the same default so this stays in lockstep with the writer.
    from hal0.agents.hermes_provision import BootstrapState
    from hal0.service_identity import open_url

    agent_id = BootstrapState().agent_id

    # #302: REST shims at /api/memory/{search,delete} instead of the
    # broken /mcp/memory JSON-RPC POST. Same idempotent uninstall
    # semantics: failure is tolerated (memory unreachable shouldn't
    # strand the operator with a half-uninstalled agent).
    url = _api_base()
    headers = {"Content-Type": "application/json", "X-hal0-Agent": agent_id}

    def _search_ids() -> list[str] | None:
        """Return matching ids, or ``None`` if the search couldn't run."""
        search_body = _json.dumps(
            {
                "query": agent_id,
                "tags": ["agent-identity"],
                "dataset": "shared",
                "limit": 50,
            }
        ).encode("utf-8")
        req = urllib.request.Request(
            f"{url}/api/memory/search", data=search_body, headers=headers, method="POST"
        )
        try:
            with open_url(req, timeout=5.0) as resp:
                data = _json.loads(resp.read().decode("utf-8"))
        except (urllib.error.URLError, OSError, _json.JSONDecodeError):
            return None
        if not isinstance(data, dict):
            return None
        items = data.get("items") or []
        ids: list[str] = []
        for it in items if isinstance(items, list) else []:
            if not isinstance(it, dict):
                continue
            md = it.get("metadata") or {}
            if md.get("agent_id") == agent_id and it.get("id"):
                ids.append(it["id"])
        return ids

    ids = _search_ids()
    if ids is None:
        return MemoryUninstallOutcome(
            outcome="unreachable", deleted_count=0, leftover_count=None, url=url
        )
    if not ids:
        # Pre-delete search saw zero — true no-op. Skip the verify
        # round-trip since there's nothing to verify.
        return MemoryUninstallOutcome(
            outcome="not_found", deleted_count=0, leftover_count=0, url=url
        )

    del_body = _json.dumps({"ids": ids}).encode("utf-8")
    req2 = urllib.request.Request(
        f"{url}/api/memory/delete", data=del_body, headers=headers, method="POST"
    )
    try:
        with open_url(req2, timeout=5.0):
            pass
    except (urllib.error.URLError, OSError):
        return MemoryUninstallOutcome(
            outcome="unreachable", deleted_count=len(ids), leftover_count=None, url=url
        )

    # Post-delete verify: the 2026-05-26 incident logged in #350 had the
    # delete return 200 but leave 9 rows in place. Re-run the search and
    # surface the gap if rows survive.
    verify_ids = _search_ids()
    if verify_ids is None:
        # Delete returned OK but verify couldn't run. Don't escalate to
        # ``leftover`` — we have no evidence either way; treat as silent
        # success (best-effort contract preserved).
        return MemoryUninstallOutcome(
            outcome="deleted", deleted_count=len(ids), leftover_count=None, url=url
        )
    if verify_ids:
        return MemoryUninstallOutcome(
            outcome="leftover",
            deleted_count=len(ids),
            leftover_count=len(verify_ids),
            url=url,
        )
    return MemoryUninstallOutcome(
        outcome="deleted", deleted_count=len(ids), leftover_count=0, url=url
    )


def _warn_memory_outcome(outcome: MemoryUninstallOutcome) -> None:
    """Surface the two failure-ish outcomes as yellow stderr warnings.

    ``deleted`` / ``not_found`` stay silent (the happy path); only the
    two cases that mean "memory teardown didn't complete cleanly" warn.
    The CLI's exit code is unaffected — operator-facing signal only.
    """
    if outcome.outcome == "unreachable":
        _stderr_console.print(
            f"[yellow]warning[/yellow]: memory teardown skipped — "
            f"hal0 memory API unreachable at {outcome.url}. "
            "Re-run uninstall once the daemon is back, or use "
            "[bold]hal0 agent uninstall hermes --keep-memory[/bold] "
            "if this is intentional."
        )
    elif outcome.outcome == "leftover":
        leftover = outcome.leftover_count if outcome.leftover_count is not None else "?"
        _stderr_console.print(
            f"[yellow]warning[/yellow]: memory teardown incomplete — "
            f"{leftover} hermes identity-card row(s) still in the [bold]agents[/bold] "
            "dataset after delete. Inspect with "
            "[bold]hal0 agent peers[/bold] and clean up by id via "
            "[bold]/api/memory/delete[/bold]."
        )


@app.command("list")
def agent_list(
    json_out: bool = typer.Option(
        False,
        "--json",
        help="Emit the raw /api/agents JSON for CI/pipe use (no Rich table).",
    ),
) -> None:
    """List installed bundled agents."""
    url = _api_base()
    if _api_unreachable(url):
        raise typer.Exit(1)
    try:
        data = api_get("/api/agents")
    except CliApiError as exc:
        die(str(exc))
        return
    if json_out:
        typer.echo(jsonlib.dumps(data, indent=2))
        return
    agents = data.get("agents", []) if isinstance(data, dict) else data
    if not agents:
        console.print("[dim]No bundled agents installed.[/dim]")
        return
    table = Table(title=f"Bundled agents ({len(agents)})")
    table.add_column("Name", style="bold")
    table.add_column("Status")
    table.add_column("Installed at")
    table.add_column("Data dir")
    for a in agents:
        table.add_row(
            a.get("name", "—"),
            a.get("status", "—"),
            a.get("installed_at", "—"),
            a.get("data_dir", "—"),
        )
    console.print(table)


# ── Approvals (MCP-backend owns the route shape) ────────────────────────────


def _fmt_enqueued_at(value: Any) -> str:
    """Project ``enqueued_at`` (epoch seconds float) to a short ISO string.

    Mirrors the dashboard's ``AgentApprovalRow.vue`` tooltip projection
    (``new Date(epoch * 1000).toISOString()``) — keep CLI + UI agreeing
    on a single representation so screenshots and CLI output read the
    same to operators.
    """
    if value in (None, "", "—"):
        return "—"
    try:
        epoch = float(value)
    except (TypeError, ValueError):
        # Already a string-ish timestamp — pass through untouched.
        return str(value)
    return (
        datetime.fromtimestamp(epoch, tz=UTC)
        .replace(microsecond=0)
        .isoformat()
        .replace("+00:00", "Z")
    )


def _approval_summary(entry: dict[str, Any]) -> str:
    """Build a one-line Summary from ``tool`` + primary target arg.

    Mirrors ``AgentApprovalRow.vue``'s ``primaryArg`` projection: the
    arg name comes from :data:`hal0.mcp.approval_queue._PRIMARY_TARGET_ARG`
    so the CLI and the dashboard agree on which field is the
    "distinguishing" one. When the tool has no registered primary arg
    we fall back to the first scalar in ``args`` so the operator still
    sees something more useful than the bare tool name. Truncated to
    60 chars to fit a reasonable terminal width without wrapping.
    """
    tool = str(entry.get("tool") or "—")
    args = entry.get("args")
    if not isinstance(args, dict) or not args:
        return tool[:60]

    primary_key = _PRIMARY_TARGET_ARG.get(tool)
    primary_val: Any = None
    if primary_key is not None:
        primary_val = args.get(primary_key)
    if primary_val is None:
        # No registered primary arg (or it's missing) — fall back to
        # the first scalar value, matching the Vue row's behaviour.
        for v in args.values():
            if isinstance(v, str | int | float | bool) and v != "":
                primary_val = v
                break

    if isinstance(primary_val, list | tuple):
        primary_val = ",".join(str(v) for v in primary_val)
    summary = tool if primary_val is None or primary_val == "" else f"{tool} {primary_val}"
    return summary[:60]


@approvals_app.command("list")
def approvals_list() -> None:
    """List pending agent approval requests."""
    url = _api_base()
    if _api_unreachable(url):
        raise typer.Exit(1)
    try:
        data = api_get("/api/agent/approvals")
    except CliApiError as exc:
        die(str(exc))
        return
    items = data.get("approvals", []) if isinstance(data, dict) else data
    if not items:
        console.print("[dim]No pending approvals.[/dim]")
        return
    table = Table(title=f"Pending approvals ({len(items)})")
    table.add_column("ID", style="bold")
    table.add_column("Tool")
    table.add_column("Agent")
    table.add_column("Requested at")
    table.add_column("Summary")
    for it in items:
        # ApprovalEntry.as_dict() emits ``client_id`` + ``enqueued_at``
        # + ``args`` — NOT ``agent`` / ``requested_at`` / ``summary``.
        # Mirror ui/src/components/agent/AgentApprovalRow.vue's
        # projection so CLI + dashboard show the same row content.
        table.add_row(
            str(it.get("id", "—")),
            str(it.get("tool", "—")),
            str(it.get("client_id") or "—"),
            _fmt_enqueued_at(it.get("enqueued_at")),
            _approval_summary(it),
        )
    console.print(table)


@approvals_app.command("approve")
def approvals_approve(
    approval_id: str = typer.Argument(..., help="Approval request ID."),
) -> None:
    """Approve a pending agent action."""
    url = _api_base()
    if _api_unreachable(url):
        raise typer.Exit(1)
    try:
        api_post(f"/api/agent/approvals/{approval_id}/approve")
    except CliApiError as exc:
        die(str(exc))
        return
    console.print(f"[bold green]Approved[/bold green] {approval_id}.")


@approvals_app.command("deny")
def approvals_deny(
    approval_id: str = typer.Argument(..., help="Approval request ID."),
) -> None:
    """Deny a pending agent action."""
    url = _api_base()
    if _api_unreachable(url):
        raise typer.Exit(1)
    try:
        api_post(f"/api/agent/approvals/{approval_id}/deny")
    except CliApiError as exc:
        die(str(exc))
        return
    console.print(f"[bold]Denied[/bold] {approval_id}.")


# ── Hermes command approvals (Hermes's approvals.mode) ──────────────────────

_COMMAND_APPROVALS_PATH = "/api/agents/hermes/command-approvals"
_COMMAND_APPROVALS_MODE_HELP = {
    "smart": (
        "a call to your hal0/agent model approves a flagged command first; if that "
        "call fails or answers anything but APPROVE, Hermes falls back to asking you"
    ),
    "manual": "you approve every flagged command yourself",
}


@app.command(
    "command-approvals",
    epilog=(
        "Hermes's own gate on dangerous terminal commands and execute_code scripts "
        "(Hermes's approvals.mode). Only matters while the Hermes terminal tool is "
        "on. Not the same as `hal0 agent approvals`, the queue for gated hal0 MCP "
        "tool calls. An approvals.mode in /etc/hal0/agents/hermes/overrides.yaml "
        "wins over this setting."
    ),
)
def agent_command_approvals(
    mode: str | None = typer.Argument(
        None,
        help="smart or manual. Omit to show the current setting.",
        show_default=False,
    ),
    json_out: bool = typer.Option(False, "--json", help="Print the raw API response."),
) -> None:
    """Show or set Hermes command approvals (smart or manual)."""
    if mode is not None and mode not in _COMMAND_APPROVALS_MODE_HELP:
        die(f"mode must be smart or manual; got {mode!r}.")
        return
    url = _api_base()
    if _api_unreachable(url):
        raise typer.Exit(1)
    try:
        if mode is None:
            data = api_get(_COMMAND_APPROVALS_PATH)
        else:
            # The restart inside the PUT can take up to ~30s (restart.py's
            # SYSTEMCTL_TIMEOUT_SECONDS) on top of the config write.
            data = api_put(
                _COMMAND_APPROVALS_PATH,
                json={"mode": mode},
                headers={"X-hal0-Agent": "hal0-cli"},
                timeout=90.0,
            )
    except CliApiError as exc:
        die(str(exc))
        return
    if json_out:
        console.print_json(jsonlib.dumps(data))
        return
    if not isinstance(data, dict):
        die(f"unexpected response: {data!r}")
        return
    if mode is not None and data.get("message"):
        console.print(str(data["message"]))
    _print_command_approvals(data)


def _print_command_approvals(data: dict[str, Any]) -> None:
    setting = str(data.get("mode") or "—")
    effective = str(data.get("effective") or "—")
    source = data.get("source")
    lines = [
        f"Hermes command approvals: [bold]{setting}[/bold]"
        + (" [dim](default)[/dim]" if source == "default" else ""),
        f"  {_COMMAND_APPROVALS_MODE_HELP.get(setting, '')}",
    ]
    if source == "overrides":
        lines.append(
            f"[yellow]In effect: {effective}[/yellow], set by "
            f"{data.get('overrides_path') or 'overrides.yaml'}, which wins over this setting."
        )
    if data.get("seed_unreadable"):
        lines.append(
            f"[yellow]{data.get('seed_path') or 'The seed TOML'} could not be read;[/yellow] "
            f"the stored setting is unknown, so the default ({data.get('default')}) is in use. "
            "Fix the file or run `sudo hal0 agent reprovision hermes --repair`."
        )
    if data.get("invalid_value"):
        lines.append(
            f"[yellow]The stored value {data['invalid_value']!r} is invalid;[/yellow] "
            f"using the default ({data.get('default')})."
        )
    if data.get("in_sync") is False:
        fix = (
            "Run `sudo hal0 agent reprovision hermes` to re-apply overrides.yaml."
            if source == "overrides"
            else f"Run `hal0 agent command-approvals {effective}` to re-apply it."
        )
        lines.append(
            f"[yellow]Hermes's config.yaml says {data.get('rendered')!r}, not {effective!r}.[/yellow] "
            + fix
        )
    console.print("\n".join(lines))


# ── Bootstrap (Hermes provisioning, Phase 10) ────────────────────────────────


@app.command("peers")
def agent_peers() -> None:
    """List discoverable agent identity cards.

    Thin wrapper over ``memory_search`` against the dedicated
    ``agents`` memory dataset. Sibling of ``hal0 agent list`` (which
    shows installed bundled agents on this host); ``peers`` shows
    every card published into the federated registry.
    """
    import ast as _ast
    import json as _json
    import urllib.error
    import urllib.request

    from hal0.service_identity import open_url

    url = _api_base()
    if _api_unreachable(url):
        raise typer.Exit(1)

    # #302: switched from the broken /mcp/memory JSON-RPC POST to the
    # REST shim at /api/memory/search. The MCP server at /mcp/memory
    # requires the FastMCP initialize handshake + session-tagged calls;
    # one-shot POST returns 405. The shim is plain HTTP.
    body = _json.dumps(
        {
            "query": "agent identity",
            "tags": ["agent-identity"],
            "dataset": "shared",
            "limit": 50,
        }
    ).encode("utf-8")
    req = urllib.request.Request(
        f"{url}/api/memory/search",
        data=body,
        headers={
            "Content-Type": "application/json",
            "X-hal0-Agent": "hal0-cli",
        },
        method="POST",
    )
    try:
        with open_url(req, timeout=5.0) as resp:
            data = _json.loads(resp.read().decode("utf-8"))
    except (urllib.error.URLError, OSError, _json.JSONDecodeError) as exc:
        die(f"memory API unreachable: {exc}")
        return

    items = data.get("items") or [] if isinstance(data, dict) else []
    if not items:
        console.print("[dim]No agent identity cards published yet.[/dim]")
        return

    table = Table(title=f"Agent peers ({len(items)})")
    table.add_column("Agent ID", style="bold")
    table.add_column("Display name")
    table.add_column("Roles")
    table.add_column("Endpoint")
    table.add_column("Registered")

    def _unflatten(value: Any) -> Any:
        """Undo the string flattening cards pick up in the memory engine (#1897).

        ``HindsightProvider.add`` stores metadata as ``{k: str(v) for ...}``,
        so a card's nested ``endpoint`` / ``hal0_state`` / ``roles`` come back
        as Python reprs (``{'url': 'http://…'}``) — as JSON on some other
        engines. ``hal0 agent peers`` assumed live objects and died with
        ``AttributeError: 'str' object has no attribute 'get'``, taking the
        whole listing down. Try JSON first, then a literal-eval for the repr
        form (literals only — never ``eval``); an unparseable value is handed
        back as-is for the caller to reject.
        """
        if not isinstance(value, str):
            return value
        text = value.strip()
        try:
            return _json.loads(text)
        except _json.JSONDecodeError:
            pass
        try:
            return _ast.literal_eval(text)
        except (ValueError, SyntaxError):
            return value

    def _as_dict(value: Any) -> dict[str, Any]:
        """One card field as a dict; anything unreadable costs only its own
        columns, never the listing."""
        parsed = _unflatten(value)
        return parsed if isinstance(parsed, dict) else {}

    def _as_list(value: Any) -> list[Any]:
        """One card field as a list; a bare string is a single-element list."""
        parsed = _unflatten(value)
        if isinstance(parsed, list):
            return parsed
        if isinstance(parsed, str) and parsed:
            return [parsed]
        return []

    for item in items:
        md = _as_dict(item.get("metadata")) if isinstance(item, dict) else {}
        endpoint = _as_dict(md.get("endpoint"))
        hal0_state = _as_dict(md.get("hal0_state"))
        roles = _as_list(md.get("roles"))
        table.add_row(
            str(md.get("agent_id") or "—"),
            str(md.get("display_name") or "—"),
            ", ".join(str(r) for r in roles) or "—",
            str(endpoint.get("url") or "—"),
            str(hal0_state.get("registered_at") or "—"),
        )
    console.print(table)


def _reconcile_upstreams_best_effort() -> None:
    """Ask hal0-api to re-register the per-profile Hermes upstreams.

    Provisioning may have added keys or profiles; the API also reconciles at its
    own boot, so an unreachable API is a hint, never a failure.
    """
    try:
        with auth_client(timeout=15) as client:
            client.post(f"{_api_base()}/api/agents/hermes/reconcile-upstreams").raise_for_status()
    except Exception:
        console.print(
            "[dim]hal0-api not reachable; Hermes upstreams reconcile at the next hal0-api start[/dim]"
        )


# ── Bootstrap status / log / upgrade / uninstall (Phase 10, #246) ───────────


def _agent_provision_state_file(name: str) -> Any:
    """Path to ``<name>``'s ``provision.json`` snapshot.

    Factored out as a seam (mirrors :func:`_bundled_agent_manager` above)
    so tests can monkeypatch ``ac._agent_provision_state_file`` to point
    at a tmp_path fixture instead of the real root-owned
    ``/var/lib/hal0`` tree.
    """
    from pathlib import Path

    return Path(f"/var/lib/hal0/state/agents/{name}/provision.json")


@app.command("status")
def agent_status(
    name: str = typer.Argument("hermes", help="Bundled agent name (default: hermes)."),
    json_out: bool = typer.Option(
        False,
        "--json",
        help="Emit the raw provision.json snapshot as JSON for CI/pipe use (no Rich table).",
    ),
) -> None:
    """Pretty-print the agent's last provision report (provision.json snapshot)."""
    import json as _json

    state_file = _agent_provision_state_file(name)
    if not state_file.exists():
        if json_out:
            # Mirror the "not yet bootstrapped" case as valid, parseable
            # JSON instead of leaving stdout empty. Before this fix
            # `--json` wasn't a recognized option at all, so Typer/Click
            # raised a UsageError to stderr and stdout stayed empty
            # (the exact symptom from live install-validation m4).
            typer.echo(_json.dumps({"name": name, "provisioned": False, "phases": {}}, indent=2))
            raise typer.Exit(0)
        console.print(
            f"[dim]{name}: no provision.json yet (run `hal0 agent install {name}` first).[/dim]"
        )
        raise typer.Exit(0)
    data = _json.loads(state_file.read_text())
    if json_out:
        # Same data the table below renders, just machine-readable — no
        # separate source of truth to drift out of sync with the table.
        payload = {"name": name, "provisioned": True, **data}
        typer.echo(_json.dumps(payload, indent=2))
        return
    table = Table(title=f"{name} bootstrap status")
    table.add_column("Phase", style="bold")
    table.add_column("Status")
    table.add_column("Failures")
    table.add_column("At")
    table.add_column("Detail")
    # #1793: a phase can carry recorded failures (smoke_tests) without
    # failing the install — those must not read as a silent "ok". Color +
    # a dedicated Failures column surface the count instead of leaving it
    # buried inside the (possibly truncated) Detail JSON blob.
    status_style = {"ok": "green", "warn": "yellow", "fail": "red bold", "skip": "dim"}
    warn_phases: list[str] = []
    for phase, entry in (data.get("phases") or {}).items():
        status = entry.get("status", "—")
        style = status_style.get(status)
        status_cell = f"[{style}]{status}[/{style}]" if style else status
        failure_count = entry.get("failure_count")
        skipped_count = entry.get("skipped_count")
        failure_bits = []
        if failure_count:
            failure_bits.append(f"[red]{failure_count} failed[/red]")
        if skipped_count:
            failure_bits.append(f"[dim]{skipped_count} skipped[/dim]")
        failures_cell = " ".join(failure_bits) or "—"
        details_blob = entry.get("details") or {}
        phase_failures = details_blob.get("failures") if isinstance(details_blob, dict) else None
        phase_skipped = details_blob.get("skipped") if isinstance(details_blob, dict) else None
        if entry.get("reason"):
            detail = entry["reason"]
        elif phase_failures:
            shown = "; ".join(phase_failures[:2])
            more = len(phase_failures) - 2
            detail = f"{shown} (+{more} more)" if more > 0 else shown
        elif phase_skipped:
            # A skip is not a pass: say which probe skipped and why (a probe
            # held back so it would not cold-load a model slot reads so here).
            shown = "; ".join(phase_skipped[:2])
            more = len(phase_skipped) - 2
            detail = f"{shown} (+{more} more)" if more > 0 else shown
        else:
            detail = _json.dumps(details_blob)[:60]
        # Reasons, failures and skips are plain text from the provisioner, not
        # Rich markup: ``[memory]`` in "could not read [memory] from hal0.toml"
        # would otherwise be swallowed as a style tag.
        table.add_row(
            phase, status_cell, failures_cell, entry.get("at", "—"), _escape_markup(str(detail))
        )
        if status == "warn":
            warn_phases.append(phase)
    console.print(table)
    if warn_phases:
        console.print(
            f"[yellow]warn: {', '.join(warn_phases)} recorded failures — "
            f"see Detail or `--json` for the full list.[/yellow]"
        )
    # Terminal-tool posture gets its own line rather than living inside the
    # truncated Detail blob: it is the one security-relevant setting an operator
    # is most likely to want to confirm at a glance, and `--json` should not be
    # the only way to see it.
    _print_terminal_posture((data.get("phases") or {}).get("config_write") or {})
    console.print(
        f"[dim]hal0={data.get('hal0_version', '?')} "
        f"hermes={data.get('hermes_version', '?')} "
        f"completed_at={data.get('completed_at', '—')}[/dim]"
    )


def _print_terminal_posture(config_write_entry: dict[str, Any]) -> None:
    """One line stating whether the agent can run commands on this box."""
    details = config_write_entry.get("details")
    if not isinstance(details, dict):
        return
    effective = details.get("terminal_tool")
    if effective not in {"on", "off"}:
        return
    decision = details.get("terminal_tool_decision", effective)
    reason = details.get("terminal_tool_reason", "?")
    if effective == "on":
        console.print(
            f"[yellow]terminal tool: ON[/yellow] — the agent can run commands as the "
            f"hal0 user (root-equivalent on this box). [dim]decision={decision} "
            f"({reason})[/dim]"
        )
    else:
        console.print(
            f"[green]terminal tool: off[/green] — the agent cannot run shell commands. "
            f"[dim]({reason})[/dim]"
        )


@app.command("log")
def agent_log(
    name: str = typer.Argument("hermes", help="Bundled agent name."),
    phase: str | None = typer.Option(None, "--phase", help="Dump the named phase's log only."),
) -> None:
    """Show per-phase logs from /var/lib/hal0/state/agents/<name>/provision-logs/."""
    from pathlib import Path

    log_dir = Path(f"/var/lib/hal0/state/agents/{name}/provision-logs")
    if not log_dir.exists():
        console.print(f"[dim]{name}: no logs dir at {log_dir}[/dim]")
        raise typer.Exit(0)
    for log_file in _provision_log_files(log_dir, phase):
        console.print(f"[bold]== {log_file.name} ==[/bold]")
        console.print(log_file.read_text())


def _provision_log_files(log_dir: Path, phase: str | None) -> list[Path]:
    """The logs ``hal0 agent log`` prints: ``<phase>.log``, or every bootstrap log.

    The default listing leaves out ``job-<id>.log``: the transcripts of API-started
    repairs (``POST /api/agents/hermes/repair``) are never pruned, so printing them
    all would bury the bootstrap logs. ``--phase job-<id>`` still names one.
    """
    if phase:
        return sorted(log_dir.glob(f"{phase}.log"))
    return sorted(path for path in log_dir.glob("*.log") if not path.name.startswith("job-"))


@app.command("dashboard-token")
def agent_dashboard_token(
    name: str = typer.Argument("hermes", help="Bundled agent name (default: hermes)."),
) -> None:
    """Print the agent's stable dashboard session token.

    Hermes Desktop in remote mode (token auth) stores this token and puts it
    on every ``/api/ws`` upgrade. ``hal0-agent@<name>`` mints it once, under
    ``/var/lib/hal0/state/agents/<name>/``, and exports it to the dashboard as
    ``HERMES_DASHBOARD_SESSION_TOKEN`` at every start, so a pairing survives
    restarts and ``hal0 update`` (#2899). Run as root or as the ``hal0`` user.
    """
    from hal0.cli.agent_shim import dashboard_session_token_path

    path = dashboard_session_token_path(name)
    try:
        token = path.read_text(encoding="utf-8").strip()
    except FileNotFoundError:
        token = ""
    except PermissionError:
        _stderr_console.print(
            f"[red]{name}: cannot read {path} — run as root or as the hal0 user.[/red]"
        )
        raise typer.Exit(1) from None
    if not token:
        _stderr_console.print(
            f"[red]{name}: no dashboard session token at {path} yet — it is minted the "
            f"first time hal0-agent@{name}.service starts (systemctl start "
            f"hal0-agent@{name}).[/red]"
        )
        raise typer.Exit(1)
    typer.echo(token)


@app.command("upgrade")
def agent_upgrade(
    name: str = typer.Argument(..., help="Bundled agent name (pi)."),
) -> None:
    """Re-pin an installed pi: rerun its pinned installer and redeploy hal0's extensions.

    Re-pins pi, redeploys hal0's extensions into ``~/.pi`` and re-stamps the
    install marker (:meth:`PiDriver.upgrade`); run it the same way you
    installed pi (``sudo hal0 agent upgrade pi``). It refuses when pi's
    profile is in a different home or pi is not installed. Hermes updates with
    ``hal0 update component hermes``.
    """
    if name == "hermes":
        die(
            "Hermes updates with `hal0 update component hermes` "
            "(`--target <sha>` for a vetted commit other than the pin); "
            "`hal0 agent upgrade` re-pins pi only."
        )
        return
    if name != "pi":
        die(f"upgrade currently only supports `pi`; got {name!r}.")
        return
    _upgrade_pi()


# Note: there is no per-agent `rotate-token` subcommand here — agent
# identity flows via the X-hal0-Agent header the wrapper exports from
# $HAL0_AGENT_ID (see #246 sharpening's second correction comment), which
# is orthogonal to the box-level bearer credential. The daemon DOES have
# auth (KB-1/§1, opt-in via [security].require_auth): rotate the admin or
# client box key with `hal0 auth rotate <admin|client>`.


# ── Reprovision (PR-3, v0.3) ────────────────────────────────────────────────


@app.command(
    "reprovision",
    epilog=(
        "Operator config overrides deep-merge from "
        "/etc/hal0/agents/hermes/overrides.yaml (applied last, so a hand override "
        "wins). config.yaml is snapshotted to config.yaml.bak before each render."
    ),
)
def agent_reprovision(
    name: str = typer.Argument("hermes", help="Bundled agent name."),
    repair: bool = typer.Option(
        False,
        "--repair",
        help=(
            "Force re-run of every phase: rebuilds the managed venv, re-writes "
            "persona seeds, re-renders config. The venv rebuild is what recovers a "
            "box whose agent has no MCP tools."
        ),
    ),
    offline: bool = typer.Option(
        False,
        "--offline",
        help=(
            "Never build a Hermes release. When a build is needed the install step "
            "fails and names the step that needs the network; a running host keeps "
            "its live release."
        ),
    ),
    verbose: bool = typer.Option(False, "--verbose", "-v", help="Verbose phase log."),
) -> None:
    """Re-run the convergent install idempotently.

    The one Hermes converge verb: steps whose on-disk artefacts already match
    are skipped, steps whose inputs drifted re-run. As root it re-execs itself
    as the hal0 user (see :func:`_provision_hermes`).
    """
    import os as _os

    if name != "hermes":
        die(f"reprovision currently only supports `hermes`; got {name!r}.")
        return
    if offline:
        # Read by the release builder (hermes_provision._phase_install, #2838).
        _os.environ["HAL0_HERMES_OFFLINE"] = "1"
    # §7.4: drops to hal0 when root (see _provision_hermes) so a re-converge run
    # re-renders config.yaml born hal0:hal0, matching first install.
    rc = _provision_hermes(repair=repair, offline=offline, verbose=verbose)
    if rc == 0:
        _reconcile_upstreams_best_effort()
    raise typer.Exit(rc)


# ── Personas (PR-3, v0.3) ───────────────────────────────────────────────────


@personas_app.command("list")
def personas_list() -> None:
    """List personas + mark the active one.

    Reads ``/var/lib/hal0/.hermes/personas/*.toml`` directly so the
    CLI works without a running hal0-api (mirrors ``hal0 agent status``
    which also reads disk state).
    """
    from hal0.agents import personas as _personas

    items = _personas.list_personas()
    if not items:
        console.print(
            "[dim]No personas seeded yet. Run "
            "[bold]hal0 agent reprovision hermes[/bold] to seed the defaults.[/dim]"
        )
        return
    active = _personas.get_active()
    table = Table(title=f"Hermes personas ({len(items)})")
    table.add_column("ID", style="bold")
    table.add_column("Display name")
    table.add_column("Summary")
    table.add_column("Active")
    for persona in items:
        is_active = persona.id == active
        table.add_row(
            persona.id,
            persona.display_name,
            (persona.summary or "—")[:60],
            "[bold green]yes[/bold green]" if is_active else "—",
        )
    console.print(table)


@personas_app.command("show")
def personas_show(
    persona_id: str = typer.Argument(..., help="Persona id (filename stem)."),
) -> None:
    """Print a persona's TOML body.

    Useful for grabbing a starting template before hand-editing a new
    persona — copy the output, change ``[persona].id``, save under
    ``/var/lib/hal0/.hermes/personas/<new-id>.toml``.
    """
    import tomli_w

    from hal0.agents import personas as _personas

    try:
        persona = _personas.load_persona(persona_id)
    except FileNotFoundError as exc:
        die(str(exc))
        return
    except _personas.PersonaError as exc:
        die(f"persona {persona_id!r} is malformed: {exc}")
        return
    data = persona.to_dict()
    note = ""
    if persona.id != _personas.STEWARD_PERSONA_ID:
        # Only the hal0-brain steward enforces these two; Hermes ignores them.
        data["persona"]["tools"].pop("allowed", None)
        data["persona"]["model"].pop("preferred_model", None)
        note = (
            "\n[dim]tools.allowed and model.preferred_model are steward-only "
            f"(hal0-brain) and have no effect on {persona.id!r}; hidden.[/dim]"
        )
    body = tomli_w.dumps(data)
    console.print(Panel(body, title=f"persona: {persona.id}", border_style="cyan"))
    if note:
        console.print(note.strip())


@personas_app.command("activate")
def personas_activate(
    persona_id: str = typer.Argument(..., help="Persona id to make active."),
    no_apply: bool = typer.Option(
        False,
        "--no-apply",
        help="Only move the active pointer; leave Hermes's config.yaml and processes alone.",
    ),
) -> None:
    """Switch the active persona and apply its prompt to Hermes.

    Writes ``active.txt`` atomically, renders the persona prompt into
    Hermes's ``config.yaml`` (``agent.system_prompt``); Hermes re-reads it for new
    sessions and gateway turns, no restart. The result line says
    what was and was not applied; an unprivileged caller that cannot write
    the config is told to run the repair command. Mirrors
    ``POST /api/agents/{id}/personas/{pid}/activate``.
    """
    from hal0.agents import persona_live
    from hal0.agents import personas as _personas

    try:
        result = _personas.activate(persona_id)
    except FileNotFoundError as exc:
        die(str(exc))
        return
    except _personas.PersonaError as exc:
        die(f"persona {persona_id!r} is malformed: {exc}")
        return
    panel_body = (
        f"[bold green]Activated[/bold green] {result['display_name']} "
        f"([dim]{result['persona_id']}[/dim])\n"
        f"active pointer: {result['active_path']}"
    )
    if no_apply:
        panel_body += "\n[dim]Not applied to Hermes (--no-apply).[/dim]"
    else:
        refresh = persona_live.refresh_persona_prompt(_personas.PERSONAS_ROOT)
        good = refresh["status"] in (
            persona_live.APPLIED,
            persona_live.UNCHANGED,
            persona_live.NOT_INSTALLED,
        )
        colour = "dim" if good else "yellow"
        panel_body += f"\n[{colour}]{refresh['message']}[/{colour}]"
    console.print(Panel(panel_body, border_style="green"))
