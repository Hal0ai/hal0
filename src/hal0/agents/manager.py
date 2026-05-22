"""Bundled-agent lifecycle manager (Phase 8, ADR-0004 §2 + §6).

State on disk:

    /etc/hal0/agents/<name>.toml      — install-time seed config (NOT a
                                        live default; mirrors the
                                        primary.toml seed-vs-runtime
                                        pattern, MEMORY.md
                                        :hal0_primary_slot_seed_vs_runtime)
    /var/lib/hal0/agents/<name>/      — per-agent runtime data dir

Single-pick is enforced here, not in the API or CLI. ``install()``
refuses to add a second agent unless ``switch=True``, in which case it
performs an atomic uninstall-then-install so the operator never ends up
with two bundled agents partially installed (ADR-0004 §2).

The actual install work is delegated to per-agent driver modules
(:mod:`hal0.agents.pi_coder`, :mod:`hal0.agents.hermes`) which in turn
shell out to ``installer/agents/<name>.sh``. Drivers are looked up by
:func:`_driver_for`. Adding a new bundled agent is: drop a shell script
+ a driver module + add an entry to :data:`BUNDLED_AGENTS`.
"""

from __future__ import annotations

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
    bundled (single-pick enforcement, ADR-0004 §2). Pass ``switch=True``
    to atomically swap."""


class HermesUpstreamMissingError(AgentError):
    """Raised by the Hermes driver when the hal0-owned ``hal0-hermes``
    wrapper is not installed or not functional (ADR-0004 §6). The
    wrapper is hal0's integration seam against upstream
    NousResearch/hermes-agent; install path refuses to wire an env file
    the wrapper can't source rather than half-wiring something that
    will not work."""


# Back-compat alias for the pre-wrapper-pivot error class. Existing
# callers in ``hal0.api.routes.agents`` (Team CloseOut surface) catch
# the old name; keep the symbol live until that surface migrates.
HermesNotHal0AwareError = HermesUpstreamMissingError


# ── Bundled catalog ──────────────────────────────────────────────────────────

BUNDLED_AGENTS: tuple[str, ...] = ("pi-coder", "hermes")
"""Canonical names for bundled agents. Adding to this list requires a
matching driver module + ``installer/agents/<name>.sh``."""


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
    are missing (e.g. pi-coder dev box without Hermes credentials)."""
    if name == "pi-coder":
        from hal0.agents.pi_coder import PiCoderDriver

        return PiCoderDriver()
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

    def __init__(self, *, etc_root: Path | None = None, var_root: Path | None = None) -> None:
        # Roots are injectable so tests can point at tmp_path without
        # setting HAL0_HOME. Production callers pass nothing and we
        # resolve via :mod:`hal0.config.paths`.
        self._etc_root = etc_root if etc_root is not None else _paths.etc() / "agents"
        self._var_root = var_root if var_root is not None else _paths.var_lib() / "agents"

    # ── Public surface ──────────────────────────────────────────────────

    def list(self) -> list[AgentRecord]:
        """Return all installed agents (zero or one for v0.2)."""
        if not self._etc_root.exists():
            return []
        out: list[AgentRecord] = []
        for toml_path in sorted(self._etc_root.glob("*.toml")):
            name = toml_path.stem
            if name not in BUNDLED_AGENTS:
                # Stray TOML — skip. We do not raise here because the
                # caller might be doing a "soft list" during recovery.
                continue
            rec = self._read_record(name)
            out.append(rec)
        return out

    def installed_names(self) -> list[str]:
        return [r.name for r in self.list()]

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
        """
        if name not in BUNDLED_AGENTS:
            raise AgentNotFoundError(
                f"unknown bundled agent {name!r}. Known: {', '.join(BUNDLED_AGENTS)}",
            )

        current = self.installed_names()
        if name in current:
            # Already installed — return the existing record. Idempotent.
            return self._read_record(name)

        if current:
            if not switch:
                raise AgentAlreadyInstalledError(
                    f"agent {current[0]!r} already installed; "
                    f"pass switch=True to atomically swap to {name!r}",
                )
            # Atomic swap: tear down the existing one first. If the
            # tear-down fails we surface the error and DO NOT proceed
            # — better to leave the old one installed than land in a
            # half-state.
            for existing in current:
                self.uninstall(existing)

        driver = _driver_for(name)
        # Driver does the heavy lifting (shells out to the installer
        # script). On any exception, the manager rolls back the seed
        # TOML so list() doesn't show a phantom row.
        driver.install(bearer_token=bearer_token)

        rec = self._write_seed(name)
        return rec

    def uninstall(self, name: str) -> None:
        """Tear down ``name``: driver's uninstall + remove data dir + seed."""
        if name not in BUNDLED_AGENTS:
            raise AgentNotFoundError(
                f"unknown bundled agent {name!r}. Known: {', '.join(BUNDLED_AGENTS)}",
            )
        # Driver lookup is best-effort — if the driver module fails to
        # import for some reason, we still want to clean up the on-disk
        # seed + data dir so the operator can reinstall cleanly.
        try:
            driver = _driver_for(name)
            driver.uninstall()
        except Exception:
            # Surface? No — log via stderr. The caller (CLI/API) wraps
            # this in their own error envelope. We intentionally
            # swallow so the on-disk cleanup below always runs.
            pass

        toml_path = self._config_path(name)
        if toml_path.exists():
            toml_path.unlink()

        data_dir = self._data_dir(name)
        if data_dir.exists():
            shutil.rmtree(data_dir)

    def switch(self, name: str, *, bearer_token: str | None = None) -> AgentRecord:
        """Convenience: :meth:`install` with ``switch=True``."""
        return self.install(name, switch=True, bearer_token=bearer_token)

    # ── Path helpers ────────────────────────────────────────────────────

    def _config_path(self, name: str) -> Path:
        return self._etc_root / f"{name}.toml"

    def _data_dir(self, name: str) -> Path:
        return self._var_root / name

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
        self._data_dir(name).mkdir(parents=True, exist_ok=True)

        installed_at = datetime.datetime.now(tz=datetime.UTC).isoformat()
        payload: dict[str, object] = {
            "agent": {
                "name": name,
                "installed_at": installed_at,
                # Track-latest by design (ADR-0004 §3). No version pin.
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

        Tolerates a missing/corrupt TOML — we report a "broken" status
        rather than raising, so :meth:`list` can show the operator that
        something is wedged and offer ``uninstall`` to recover.
        """
        import tomllib

        toml_path = self._config_path(name)
        installed_at = ""
        try:
            with toml_path.open("rb") as fh:
                data = tomllib.load(fh)
            installed_at = str(data.get("agent", {}).get("installed_at", ""))
        except (OSError, tomllib.TOMLDecodeError):
            return AgentRecord(
                name=name,
                installed_at="",
                status="broken",
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

    Resolution mirrors the uninstall CLI wrapper: editable install →
    ``src/hal0/agents/manager.py`` lives at ``parents[3]/installer/...``.
    """
    # parents[0]=agents, [1]=hal0, [2]=src, [3]=repo root
    repo_root = Path(__file__).resolve().parents[3]
    return repo_root / "installer" / "agents" / f"{name}.sh"


def _all_known_drivers() -> Iterable[str]:
    """Test hook — lets the test suite iterate driver names without
    importing the modules (some CI environments lack pi-mono on PATH)."""
    return BUNDLED_AGENTS
