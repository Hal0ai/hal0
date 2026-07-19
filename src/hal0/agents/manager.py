"""Bundled-agent lifecycle manager (Phase 8).

State on disk:

    /etc/hal0/agents/<name>.toml          — install-time seed config (NOT a
                                            live default; mirrors the
                                            primary.toml seed-vs-runtime
                                            pattern, MEMORY.md
                                            :hal0_primary_slot_seed_vs_runtime)
    /var/lib/hal0/agents/<name>/          — per-agent runtime data dir
    /var/lib/hal0/state/agents/<name>/    — bootstrap state (provision.json,
                                            provision-logs/). Written by
                                            :mod:`hal0.agents.hermes_provision`
                                            (and any future per-agent
                                            bootstrap driver). Lives outside
                                            the data dir so a ``hermes
                                            reset`` upstream subcommand
                                            can't trample hal0's
                                            bookkeeping (#346).

``uninstall()`` removes ALL THREE — seed, data dir, state dir — and the
``installed`` predicate derives from disk truth (any of the three present)
rather than seed-TOML existence alone. That single-witness model used to
let the API + CLI report ``not_installed`` for an agent whose data dir
was still on disk (#346).

Single-pick is enforced here, not in the API or CLI. ``install()``
refuses to add a second agent unless ``switch=True``, in which case it
performs an atomic uninstall-then-install so the operator never ends up
with two bundled agents partially installed.

The actual install work is delegated to per-agent driver modules
(:mod:`hal0.agents.hermes`). Drivers are looked up by :func:`_driver_for`.
Adding a new bundled agent is: drop a driver module + add an entry to
:data:`BUNDLED_AGENTS` (plus, if it installs by shelling out to
``installer/agents/<name>.sh`` the way the now-removed pi-coder/opencode
drivers did, a matching shell script and an entry in
:data:`_SCRIPT_INSTALLED_AGENTS`).
"""

from __future__ import annotations

import contextlib
import datetime
import shutil
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING, Protocol

from hal0.config import paths as _paths

if TYPE_CHECKING:  # pragma: no cover
    from collections.abc import Iterable


# ── Errors ────────────────────────────────────────────────────────────────────


class AgentError(RuntimeError):
    """Base class for agent-subsystem errors."""


class AgentNotFoundError(AgentError):
    """Raised when ``name`` is not in :data:`BUNDLED_AGENTS`."""


class AgentAlreadyInstalledError(AgentError):
    """Raised when ``install()`` is called and a different agent is already
    bundled (single-pick enforcement). Pass ``switch=True``
    to atomically swap."""


class AgentUninstallIncompleteError(AgentError):
    """Uninstall removed some artifacts but could not remove others.

    The canonical trigger is root-owned entries inside an otherwise
    hal0-managed tree (e.g. a ``runtime.json`` written by a root-run bootstrap,
    or files from an old root-clobbering installer): the User=hal0 API service
    cannot ``rmtree`` them, so ``shutil.rmtree`` raises ``PermissionError``.
    Before this class, that exception propagated uncaught through the DELETE
    route and became a bare 500 (O16). Now the manager degrades honestly —
    removing everything it can, best-effort — and raises this with the residual
    paths + reasons so the route can surface a typed 207 partial-removal.

    ``had_artifacts`` mirrors the normal :meth:`AgentManager.uninstall` return
    (did the call find anything to remove?) so the route's status string stays
    honest even on the partial path.
    """

    def __init__(
        self,
        name: str,
        *,
        residual: list[dict[str, str]],
        had_artifacts: bool,
    ) -> None:
        self.agent_name = name
        self.residual = residual
        self.had_artifacts = had_artifacts
        summary = "; ".join(f"{r['path']} ({r['reason']})" for r in residual)
        super().__init__(f"uninstall of {name!r} incomplete — could not remove: {summary}")


class HermesUpstreamMissingError(AgentError):
    """Raised by the Hermes driver when the hal0-owned ``hal0-hermes``
    wrapper is not installed or not functional. The
    wrapper is hal0's integration seam against upstream
    NousResearch/hermes-agent; install path refuses to wire an env file
    the wrapper can't source rather than half-wiring something that
    will not work."""


# Back-compat alias for the pre-wrapper-pivot error class. Existing
# callers in ``hal0.api.routes.agents`` (Team CloseOut surface) catch
# the old name; keep the symbol live until that surface migrates.
HermesNotHal0AwareError = HermesUpstreamMissingError


# ── Bundled catalog ──────────────────────────────────────────────────────────

BUNDLED_AGENTS: tuple[str, ...] = ("hermes",)
"""Canonical names for bundled agents. Adding to this list requires a
matching driver module + ``installer/agents/<name>.sh``."""


# Marker file the Hermes provisioner stamps into ``$HERMES_HOME`` to
# claim the tree (``hermes_provision._HAL0_MANAGED_MARKER``). Mirrored
# here so :meth:`AgentManager.uninstall` can refuse to ``rmtree`` a home
# that hal0 doesn't own (a user's pre-existing ~/.hermes or a shared
# tree). Keep in sync with that module — duplicated rather than imported
# so the manager stays importable without the provisioner's deps (#453).
_HAL0_MANAGED_MARKER = ".hal0-managed"

# Agents whose runtime data dir is a canonical home OUTSIDE the legacy
# ``<var_lib>/agents/<name>`` layout. Hermes uses ``HERMES_HOME``
# (``<var_lib>/.hermes``, set by the provisioner + systemd units); the
# registry must agree or status/list report a dead path and uninstall
# rmtree's the wrong tree (#453). Value is the home subpath under
# ``var_lib()``. Agents not listed here keep the per-name layout.
_AGENT_HOME_SUBDIR: dict[str, str] = {"hermes": ".hermes"}

# Agents that install by shelling out to ``installer/agents/<name>.sh``
# (see :func:`installer_script_path`) and therefore need the
# installer-script-exists precondition in
# :meth:`AgentManager._verify_installable` run before a ``--switch`` tears
# down an incumbent. Empty as of P1-drivers: the two agents that used to
# populate this set (pi-coder, opencode) were speculative bundles that
# could never actually be installed in v0.3 and have been deleted along
# with their drivers + installer scripts. Hermes provisions through a
# separate bootstrap pipeline (:mod:`hal0.agents.hermes_provision`) and
# has no matching shell script, so it doesn't belong here either. Left in
# place (rather than removed) as the seam a future script-installed
# bundled agent would register itself into.
_SCRIPT_INSTALLED_AGENTS: frozenset[str] = frozenset()


def _removal_failure(path: Path, exc: BaseException) -> dict[str, str]:
    """Render an unremovable path + its cause for partial-uninstall reporting."""
    return {"path": str(path), "reason": f"{type(exc).__name__}: {exc}"}


# ── Records ──────────────────────────────────────────────────────────────────


@dataclass
class AgentRecord:
    """In-memory snapshot of an installed agent.

    Mirrors what ``GET /api/agents`` returns. ``status`` is a coarse
    string ("installed" / "broken") — driver-specific health checks
    return their own richer struct via :meth:`AgentDriver.status`.
    """

    name: str
    installed_at: str
    status: str
    data_dir: str
    config_path: str

    def as_dict(self) -> dict[str, str]:
        return {
            "name": self.name,
            "installed_at": self.installed_at,
            "status": self.status,
            "data_dir": self.data_dir,
            "config_path": self.config_path,
        }


# ── Driver protocol ──────────────────────────────────────────────────────────


class AgentDriver(Protocol):
    """Per-agent shim. install/uninstall shell out to
    ``installer/agents/<name>.sh``; status is a quick local check."""

    name: str

    def install(self, *, bearer_token: str | None = None) -> None: ...

    def uninstall(self) -> None: ...

    def status(self) -> str:
        """Return a coarse status string (``"installed"`` / ``"broken"``).

        Drivers MAY do a deeper probe (subprocess invocation of the
        upstream binary, MCP round-trip) but the default contract is a
        cheap "files-on-disk" check.
        """


def _driver_for(name: str) -> AgentDriver:
    """Return the driver instance for ``name``. Lazy import to keep the
    manager importable in environments where one driver's dependencies
    are missing (e.g. a dev box without Hermes credentials)."""
    if name == "hermes":
        from hal0.agents.hermes import HermesDriver

        return HermesDriver()
    raise AgentNotFoundError(
        f"unknown bundled agent {name!r}. Known: {', '.join(BUNDLED_AGENTS)}",
    )


# ── Manager ──────────────────────────────────────────────────────────────────


class AgentManager:
    """Orchestrates install/uninstall/list/switch for bundled agents.

    Stateless w.r.t. its own attributes — every query re-reads
    ``/etc/hal0/agents/*.toml``. That keeps the manager safe to
    instantiate per-request from the API layer without coordination,
    and it keeps a manual edit to the TOML visible immediately
    (consistent with the slot manager's hot-reload posture).
    """

    def __init__(
        self,
        *,
        etc_root: Path | None = None,
        var_root: Path | None = None,
        state_root: Path | None = None,
    ) -> None:
        # Roots are injectable so tests can point at tmp_path without
        # setting HAL0_HOME. Production callers pass nothing and we
        # resolve via :mod:`hal0.config.paths`.
        #
        # ``state_root`` is the parent of per-agent provision state dirs
        # (``<state_root>/<name>/provision.json`` + ``provision-logs/``).
        # Layout matches what :mod:`hal0.agents.hermes_provision` writes
        # at runtime (``/var/lib/hal0/state/agents``); injectable here so
        # the unit tests for #346 can assert the state-dir teardown
        # without touching the real /var/lib.
        self._etc_root = etc_root if etc_root is not None else _paths.etc() / "agents"
        self._var_root = var_root if var_root is not None else _paths.var_lib() / "agents"
        self._state_root = (
            state_root if state_root is not None else _paths.var_lib() / "state" / "agents"
        )

    # ── Public surface ──────────────────────────────────────────────────

    def list(self) -> list[AgentRecord]:
        """Return all installed agents (zero or one for v0.2).

        Derives from disk truth (#346): any bundled agent with a seed
        TOML, data dir, OR state dir present is listed. An agent with
        on-disk artifacts but a missing/corrupt seed gets a synthesised
        ``"broken"`` record so the dashboard surfaces the half-state and
        the operator can recover via ``uninstall``.
        """
        out: list[AgentRecord] = []
        for name in BUNDLED_AGENTS:
            if not self.is_present_on_disk(name):
                continue
            out.append(self._read_record(name))
        return sorted(out, key=lambda r: r.name)

    def installed_names(self) -> list[str]:
        """Return every bundled-agent name with on-disk artifacts (#346).

        Disk truth — seed OR data_dir OR state dir. Used by ``install()``
        for single-pick enforcement; the old behaviour (seed-only) let
        ``install hermes`` succeed against a half-uninstalled pi-coder
        whose seed was gone but whose data dir survived.
        """
        return [name for name in BUNDLED_AGENTS if self.is_present_on_disk(name)]

    def install(
        self, name: str, *, switch: bool = False, bearer_token: str | None = None
    ) -> AgentRecord:
        """Install ``name``. Single-pick enforced.

        ``switch=True`` performs atomic uninstall-then-install when a
        different agent is already bundled. ``switch=True`` with the
        same agent already installed is a no-op (idempotent re-install
        would still re-run the shell script, but we refuse here to keep
        the surface predictable — callers wanting re-run should
        ``uninstall`` then ``install``).

        The swap is crash-safe: before the incumbent is touched, we
        run a cheap precondition check on the target
        (:meth:`_verify_installable`) so a target that can't possibly
        install (e.g. a wheel install missing its bundled installer
        script) never costs the operator a working agent. If the real
        install still fails after the incumbent is gone — the
        precondition passed but the driver blew up deeper in — we make
        a best-effort attempt to reinstall the incumbent before
        re-raising, so a broken target leaves the box in its previous
        state rather than with nothing installed.
        """
        if name not in BUNDLED_AGENTS:
            raise AgentNotFoundError(
                f"unknown bundled agent {name!r}. Known: {', '.join(BUNDLED_AGENTS)}",
            )

        current = self.installed_names()
        if name in current:
            # Already installed — return the existing record. Idempotent.
            return self._read_record(name)

        # Single-pick (ADR-0004 §2): only one bundled agent may be installed
        # at a time. ``blocking`` == ``current`` — anything already
        # installed conflicts with a fresh install.
        blocking = current

        if not blocking:
            # Fresh install — nothing to swap.
            return self._install_and_seed(name, bearer_token=bearer_token)

        if not switch:
            raise AgentAlreadyInstalledError(
                f"agent {blocking[0]!r} already installed; "
                f"pass switch=True to atomically swap to {name!r}",
            )

        # Verify the target can plausibly install BEFORE tearing down
        # the incumbent. Raises without touching anything on disk when
        # the check fails — a missing installer script used to only
        # surface AFTER the incumbent was already uninstalled, bricking
        # a working agent for a swap that could never have succeeded.
        self._verify_installable(name)

        # Atomic swap: tear down the blocking agent(s) first. If the
        # tear-down fails we surface the error and DO NOT proceed
        # — better to leave the old one installed than land in a
        # half-state.
        for existing in blocking:
            self.uninstall(existing)

        try:
            return self._install_and_seed(name, bearer_token=bearer_token)
        except Exception:
            # The precondition check can't catch every failure mode
            # (the script can exist and still fail once actually run).
            # Best-effort restore the torn-down incumbent(s) so the
            # operator isn't left with NO agent installed; rollback
            # failures are swallowed — the original error is what the
            # caller needs to see and act on.
            for existing in blocking:
                with contextlib.suppress(Exception):
                    self._install_and_seed(existing, bearer_token=bearer_token)
            raise

    def _install_and_seed(self, name: str, *, bearer_token: str | None = None) -> AgentRecord:
        """Drive the install + seed-write pair.

        Shared by the main ``install()`` path and the switch-failure
        rollback path below.
        """
        driver = _driver_for(name)
        # Driver does the heavy lifting (shells out to the installer
        # script). On any exception, the manager rolls back the seed
        # TOML so list() doesn't show a phantom row.
        driver.install(bearer_token=bearer_token)
        return self._write_seed(name)

    def _verify_installable(self, name: str) -> None:
        """Cheap precondition check run before ``install(switch=True)``
        tears down the incumbent agent.

        Only the script-based drivers (:data:`_SCRIPT_INSTALLED_AGENTS`)
        are checked — Hermes provisions through a separate bootstrap
        pipeline with no matching shell script, so
        :func:`installer_script_path` doesn't apply to it; its own
        precondition (the managed venv being provisioned) can only be
        evaluated by actually running the driver.

        This doesn't guarantee the install will succeed — the driver
        can still fail once it actually shells out — it exists to
        catch the common "packaged without bundled-agent scripts"
        failure mode up front, before anything is uninstalled.
        """
        if name not in _SCRIPT_INSTALLED_AGENTS:
            return
        script = installer_script_path(name)
        if not script.is_file():
            raise AgentError(
                f"installer script missing at {script}. This hal0 install "
                "looks packaged without the bundled-agent scripts — "
                "reinstall hal0 from a release tarball or git clone. "
                f"Refusing to switch to {name!r} — the currently-installed "
                "agent was left in place."
            )

    def uninstall(self, name: str) -> bool:
        """Tear down ``name`` — driver uninstall + seed + data dir + state dir.

        Returns ``True`` iff at least one on-disk artifact existed
        before teardown (seed, data dir, or state dir). The API DELETE
        handler uses this to render an honest ``uninstalled`` /
        ``not_installed`` status (#346) rather than consulting a
        pre-uninstall ``installed_names()`` snapshot whose seed-only
        view lied when a partial uninstall had previously eaten the
        TOML.

        The three on-disk artifacts are removed best-effort + in
        order — driver first (so it can flush its own caches against a
        still-populated tree), then seed, then data dir, then state
        dir. A failure in one step doesn't abort the rest; the operator
        gets the most-thorough teardown the manager can deliver.

        Every removal is guarded (O16): a ``PermissionError`` on a
        root-owned entry inside a hal0-managed tree — which the User=hal0
        service cannot ``rmtree`` — used to propagate uncaught through the
        DELETE route as a bare 500. Now such failures are collected and the
        method raises :class:`AgentUninstallIncompleteError` with the residual
        paths, after removing everything else it could, so the surface can
        report an honest partial removal.
        """
        if name not in BUNDLED_AGENTS:
            raise AgentNotFoundError(
                f"unknown bundled agent {name!r}. Known: {', '.join(BUNDLED_AGENTS)}",
            )

        # Snapshot the disk witnesses BEFORE we touch anything so the
        # return-value is the honest "did this call do work?" predicate.
        # Driver work is opaque (the protocol returns None and the
        # production drivers may unlink-if-exists), so we treat disk
        # truth as the canonical signal.
        had_artifacts = self.is_present_on_disk(name)

        # Driver lookup is best-effort — if the driver module fails to
        # import for some reason, we still want to clean up the on-disk
        # seed + data dir + state dir so the operator can reinstall
        # cleanly.
        try:
            driver = _driver_for(name)
            driver.uninstall()
        except Exception:
            # Surface? No — log via stderr. The caller (CLI/API) wraps
            # this in their own error envelope. We intentionally
            # swallow so the on-disk cleanup below always runs.
            pass

        residual: list[dict[str, str]] = []

        toml_path = self._config_path(name)
        if toml_path.exists():
            try:
                toml_path.unlink()
            except OSError as exc:
                residual.append(_removal_failure(toml_path, exc))

        # data dir — gated by the .hal0-managed marker. Refusing an UNMANAGED
        # tree (no marker) is correct behaviour, NOT a failure: it's a user's
        # pre-existing ~/.hermes-style tree hal0 doesn't own, so it's left in
        # place silently (distinct from the O16 permission failure below).
        data_dir = self._data_dir(name)
        if data_dir.exists() and self._safe_to_remove_data_dir(name, data_dir):
            residual.extend(self._rmtree_collecting(data_dir))

        # State dir last — see #346. Removed atomically with the seed +
        # data dir so ``hal0 agent status hermes`` doesn't keep
        # rendering a green provision.json table after uninstall.
        state_dir = self._state_dir(name)
        if state_dir.exists():
            residual.extend(self._rmtree_collecting(state_dir))

        if residual:
            raise AgentUninstallIncompleteError(
                name, residual=residual, had_artifacts=had_artifacts
            )
        return had_artifacts

    def switch(self, name: str, *, bearer_token: str | None = None) -> AgentRecord:
        """Convenience: :meth:`install` with ``switch=True``."""
        return self.install(name, switch=True, bearer_token=bearer_token)

    # ── Path helpers ────────────────────────────────────────────────────

    def _config_path(self, name: str) -> Path:
        return self._etc_root / f"{name}.toml"

    def _data_dir(self, name: str) -> Path:
        """Return the per-agent runtime data dir.

        For agents in :data:`_AGENT_HOME_SUBDIR` this is the canonical
        home OUTSIDE the legacy ``<var_root>/<name>`` layout — Hermes
        resolves to ``HERMES_HOME`` (``<var_lib>/.hermes``) so the
        registry agrees with the provisioner + systemd units (#453).
        ``<var_lib>`` is ``self._var_root.parent`` (``_var_root`` is
        ``var_lib()/agents``), which keeps the injectable-root contract
        intact for tests.
        """
        subdir = _AGENT_HOME_SUBDIR.get(name)
        if subdir is not None:
            return self._var_root.parent / subdir
        return self._var_root / name

    def _safe_to_remove_data_dir(self, name: str, data_dir: Path) -> bool:
        """Gate the uninstall ``rmtree`` of a converged agent home (#453).

        Legacy per-name data dirs (``<var_root>/<name>``) are always
        hal0's to remove. But a converged home like ``HERMES_HOME`` may
        be a user's pre-existing ``~/.hermes`` or a shared tree — the
        provisioner only writes into it after stamping the
        ``.hal0-managed`` marker (``_claim_hermes_home``). Mirror that
        contract here: refuse to ``rmtree`` a converged home that lacks
        the marker rather than nuke data hal0 doesn't own.
        """
        if name not in _AGENT_HOME_SUBDIR:
            return True
        return (data_dir / _HAL0_MANAGED_MARKER).exists()

    @staticmethod
    def _rmtree_collecting(path: Path) -> list[dict[str, str]]:
        """``shutil.rmtree`` that removes what it can and reports what it can't.

        Returns a list of ``{"path", "reason"}`` for every entry the service
        user could not remove (e.g. root-owned files → ``PermissionError``).
        Uses the ``onexc`` callback so rmtree does NOT raise mid-walk — it keeps
        going and deletes every removable sibling, giving the operator the
        most-thorough partial teardown (O16). An empty list means a clean sweep.
        """
        failures: list[dict[str, str]] = []

        def _onexc(_func: object, failed_path: str, exc: BaseException) -> None:
            failures.append(_removal_failure(Path(failed_path), exc))

        # onexc (3.12+) is the current rmtree failure callback; runtime is 3.12.
        shutil.rmtree(path, onexc=_onexc)
        return failures

    def _state_dir(self, name: str) -> Path:
        """Per-agent bootstrap state dir (provision.json + logs).

        Mirrors ``hal0.agents.hermes_provision._DEFAULT_STATE_ROOT`` at
        the production path. The manager owns teardown of this tree
        because the bootstrap driver writes it but has no uninstall
        contract of its own (#346).
        """
        return self._state_root / name

    def is_present_on_disk(self, name: str) -> bool:
        """Return True iff ANY install-time artifact for ``name`` is on disk.

        Disk truth, not seed-TOML truth. Used by ``uninstall()`` (to
        decide whether the call actually removed anything) and by the
        API DELETE handler (to choose between ``uninstalled`` /
        ``not_installed`` status, post-#346).

        Three witnesses, any one of which is sufficient:

        * seed TOML at ``/etc/hal0/agents/<name>.toml``
        * data dir at ``/var/lib/hal0/agents/<name>/``
        * state dir at ``/var/lib/hal0/state/agents/<name>/``

        A partial uninstall that lost one witness (e.g. a crashed
        previous run that removed the seed but not the data dir) still
        reports ``True`` here — the API + CLI then correctly tell the
        operator "uninstalled" when the remaining cleanup runs.
        """
        if name not in BUNDLED_AGENTS:
            return False
        return (
            self._config_path(name).exists()
            or self._data_dir(name).exists()
            or self._state_dir(name).exists()
        )

    # ── Seed-TOML I/O ───────────────────────────────────────────────────

    def _write_seed(self, name: str) -> AgentRecord:
        """Write the install-time seed TOML.

        Pattern mirrors ``primary.toml``: the file records install-time
        choices (which agent, when), NOT live state. Live runtime config
        (env files, MCP adapter configs) is written by the driver's
        shell script under ``/etc/hal0/agents/<name>/`` siblings or
        ``/var/lib/hal0/agents/<name>/``.
        """
        try:
            import tomli_w
        except ImportError as exc:  # pragma: no cover
            raise AgentError(
                "tomli_w not installed — required for agent seed writes",
            ) from exc

        self._etc_root.mkdir(parents=True, exist_ok=True)
        # Legacy per-name data dirs are the manager's to create. Converged
        # homes (HERMES_HOME, :data:`_AGENT_HOME_SUBDIR`) are owned by the
        # agent's provisioner, which claims + stamps the ``.hal0-managed``
        # marker before any write (#453). Don't mkdir an unmarked home
        # here — uninstall would then refuse to remove it (no marker) and
        # leave an orphan.
        if name not in _AGENT_HOME_SUBDIR:
            self._data_dir(name).mkdir(parents=True, exist_ok=True)

        installed_at = datetime.datetime.now(tz=datetime.UTC).isoformat()
        payload: dict[str, object] = {
            "agent": {
                "name": name,
                "installed_at": installed_at,
                # Track-latest by design. No version pin.
                "version_pin": False,
            },
            "data_dir": str(self._data_dir(name)),
        }
        toml_path = self._config_path(name)
        toml_path.write_bytes(tomli_w.dumps(payload).encode("utf-8"))

        driver = _driver_for(name)
        return AgentRecord(
            name=name,
            installed_at=installed_at,
            status=driver.status(),
            data_dir=str(self._data_dir(name)),
            config_path=str(toml_path),
        )

    def _read_record(self, name: str) -> AgentRecord:
        """Build an :class:`AgentRecord` from on-disk state.

        Tolerates a missing/corrupt TOML — we report a ``"broken"``
        status rather than raising, so :meth:`list` can show the operator
        that something is wedged and offer ``uninstall`` to recover. A
        record may exist without a seed at all (e.g. data_dir + state dir
        survived a half-uninstall); that case maps to ``"broken"`` with
        an empty ``installed_at`` so the dashboard's repair affordance
        is reachable (#346).
        """
        import tomllib

        toml_path = self._config_path(name)
        installed_at = ""
        seed_readable = False
        if toml_path.exists():
            try:
                with toml_path.open("rb") as fh:
                    data = tomllib.load(fh)
                installed_at = str(data.get("agent", {}).get("installed_at", ""))
                seed_readable = True
            except (OSError, tomllib.TOMLDecodeError):
                seed_readable = False

        if not seed_readable:
            # No seed (or unreadable) but is_present_on_disk says
            # something else is here. Before synthesising ``broken``,
            # consult the driver's live health probe (#432): the
            # ``hal0 agent bootstrap hermes`` path used to leave the
            # data + state dirs on disk without the /etc seed, so a
            # *running, reachable* agent reported ``broken`` purely
            # because the seed write never happened. If the driver says
            # the agent is reachable, report ``installed`` and recover
            # the half-state; otherwise fall through to ``broken`` so
            # the dashboard's repair affordance stays reachable (#346).
            status = "broken"
            with contextlib.suppress(Exception):
                if self.is_present_on_disk(name) and _driver_for(name).status() == "installed":
                    status = "installed"
            return AgentRecord(
                name=name,
                installed_at="",
                status=status,
                data_dir=str(self._data_dir(name)),
                config_path=str(toml_path),
            )

        try:
            driver = _driver_for(name)
            status = driver.status()
        except Exception:
            status = "broken"

        return AgentRecord(
            name=name,
            installed_at=installed_at,
            status=status,
            data_dir=str(self._data_dir(name)),
            config_path=str(toml_path),
        )


def installer_script_path(name: str) -> Path:
    """Return the absolute path to ``installer/agents/<name>.sh``.

    ``install.sh`` pip-installs hal0 as a NON-editable wheel in production
    (v0.9.7.1+; only ``pip install -e <repo>`` dev checkouts differ) — see
    the FHS-layout note in :mod:`hal0.config.paths`. The two install shapes
    resolve ``__file__`` completely differently, so we try both candidates
    and return whichever exists:

    * Editable / dev checkout — ``src/hal0/agents/manager.py`` lives at
      ``<repo_root>/src/hal0/agents/manager.py``; ``parents[3]`` is the
      repo root, so the script is ``<repo_root>/installer/agents/<name>.sh``.
    * FHS / wheel install (the production case) — ``__file__`` resolves
      into ``<venv>/lib/pythonX/site-packages/hal0/...``, which has no
      ``installer/`` sibling. The scripts instead ship alongside the code
      root :func:`hal0.config.paths.usr_lib` resolves
      (``/usr/lib/hal0/current`` by default, honouring ``HAL0_HOME`` for
      dev/test root overrides — same convention the rest of the manager
      uses for its own etc/var roots).

    If neither candidate exists we return the FHS candidate anyway, so the
    caller's "installer script missing" error points at the real
    production path rather than a venv path the operator won't recognise.
    """
    # parents[0]=agents, [1]=hal0, [2]=src, [3]=repo root
    editable_candidate = Path(__file__).resolve().parents[3] / "installer" / "agents" / f"{name}.sh"
    fhs_candidate = _paths.usr_lib() / "installer" / "agents" / f"{name}.sh"

    if editable_candidate.is_file():
        return editable_candidate
    return fhs_candidate


def _all_known_drivers() -> Iterable[str]:
    """Test hook — lets the test suite iterate driver names without
    importing the modules (some CI environments lack pi-mono on PATH)."""
    return BUNDLED_AGENTS
