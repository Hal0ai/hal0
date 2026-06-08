"""FHS-aligned path resolver for hal0.

All filesystem paths used by hal0 flow through this module.  The
HAL0_HOME environment variable overrides all roots for dev installs and
integration tests.

FHS layout (when HAL0_HOME is unset):
    /usr/lib/hal0/current/    — code (symlink to versioned dir)
    /etc/hal0/                — user-editable config (preserved on update)
    /var/lib/hal0/            — mutable runtime state (preserved on update)
    /var/log/hal0/            — optional log files (journald is primary)

HAL0_HOME layout (when HAL0_HOME=/some/path):
    $HAL0_HOME/usr-lib/       — code root
    $HAL0_HOME/etc/           — config root
    $HAL0_HOME/var-lib/       — state root
    $HAL0_HOME/var-log/       — log root

Port target: haloai lib/paths.py (adapted for hal0's FHS layout).
See PLAN.md §2 (filesystem layout) and PLAN.md §3 (module port plan).
"""

from __future__ import annotations

import os
from pathlib import Path


def _hal0_home() -> Path | None:
    """Return the HAL0_HOME override path, or None if unset."""
    val = os.environ.get("HAL0_HOME", "").strip()
    return Path(val) if val else None


def usr_lib() -> Path:
    """Return the hal0 code root.

    FHS: /usr/lib/hal0/current  (the current symlink)
    HAL0_HOME: $HAL0_HOME/usr-lib/hal0/current
    """
    home = _hal0_home()
    if home is not None:
        return home / "usr-lib" / "hal0" / "current"
    return Path("/usr/lib/hal0/current")


def etc() -> Path:
    """Return the hal0 config root (/etc/hal0 or $HAL0_HOME/etc/hal0).

    Files under this path are preserved across updates and uninstall
    (unless --purge is passed).
    """
    home = _hal0_home()
    if home is not None:
        return home / "etc" / "hal0"
    return Path("/etc/hal0")


def var_lib() -> Path:
    """Return the hal0 runtime state root (/var/lib/hal0 or $HAL0_HOME/var-lib/hal0).

    Preserved across updates.  Survives uninstall when --keep-data is passed.
    """
    home = _hal0_home()
    if home is not None:
        return home / "var-lib" / "hal0"
    return Path("/var/lib/hal0")


def var_log() -> Path:
    """Return the hal0 log directory (/var/log/hal0 or $HAL0_HOME/var-log/hal0).

    journald is the primary log sink; this directory is for optional
    supplementary files (e.g. installer transcript).
    """
    home = _hal0_home()
    if home is not None:
        return home / "var-log" / "hal0"
    return Path("/var/log/hal0")


# ── Derived paths ──────────────────────────────────────────────────────────────
# These functions build on the four roots above.  Using functions (rather than
# module-level constants) means HAL0_HOME changes during tests are always
# reflected.


def slots_config_dir() -> Path:
    """Return the slot config directory (/etc/hal0/slots/)."""
    return etc() / "slots"


def registry_dir() -> Path:
    """Return the model registry directory (/var/lib/hal0/registry/)."""
    return var_lib() / "registry"


def agents_config_dir() -> Path:
    """Return the per-agent allow-list config directory.

    ADR-0013 §1: each bundled or user-added agent gets one TOML at
    ``/etc/hal0/agents/<name>.toml`` carrying its workspace path,
    enabled MCP servers, per-server auth, and the three-tier tool
    classification (allow / gated / blocked).
    """
    return etc() / "agents"


def agent_workspace_dir(agent_name: str) -> Path:
    """Return the filesystem sandbox root for a bundled agent.

    ADR-0013 §5: the agent driver chroots/bind-mounts the agent process
    to this path; writes outside require ADR-0004 approval. Each agent
    gets its own subtree so a malicious / buggy agent can't poke at
    another's workspace.
    """
    return var_lib() / "agents" / agent_name / "workspace"


def models_dir() -> Path:
    """Return the default model cache directory (/var/lib/hal0/models/)."""
    return var_lib() / "models"


def slot_data_dir(slot_name: str) -> Path:
    """Return the per-slot working directory (/var/lib/hal0/slots/<name>/)."""
    return var_lib() / "slots" / slot_name


def openwebui_data_dir() -> Path:
    """Return the OpenWebUI state directory (/var/lib/hal0/openwebui/)."""
    return var_lib() / "openwebui"


def hardware_json() -> Path:
    """Return the hardware probe result path (/etc/hal0/hardware.json)."""
    return etc() / "hardware.json"


def openwebui_env() -> Path:
    """Return the OpenWebUI env file path (/etc/hal0/openwebui.env)."""
    return etc() / "openwebui.env"


def hal0_toml() -> Path:
    """Return the top-level config file path (/etc/hal0/hal0.toml)."""
    return etc() / "hal0.toml"


def first_run_lock() -> Path:
    """Return the first-run claim lockfile path.

    The lockfile is dropped by ``installer/install.sh`` on a fresh
    install and contains a single-use OTP (UUID hex) that the wizard
    presents back to the API to claim ownership before any password is
    set. Once the wizard finishes and the operator's password is set,
    the auth surface uses cookies + Bearer tokens and the lockfile is
    deleted.

    Location: ``$HAL0_HOME/var-lib/hal0/.first-run.lock`` (or
    ``/var/lib/hal0/.first-run.lock`` in production). Lives alongside
    ``.first_run_done`` so a single ``rm -rf /var/lib/hal0`` clears
    both. Mode 0600 — the OTP is the key to first-run claim, so it
    must not be world-readable.

    See FINDINGS.md §28 (lockfile consumption) and §36 (the auth-on-
    by-default flip this lockfile bridges).
    """
    return var_lib() / ".first-run.lock"


def bundle_chosen_marker() -> Path:
    """Return the bundle-picker completion marker path.

    Dropped by ``POST /api/bundles/{name}`` (or
    ``GET /api/bundles/skip``) once the operator has engaged the first-
    run bundle picker (ADR-0010). The dashboard reads this to decide
    whether to render the picker or the regular dashboard on load.

    Location: ``$HAL0_HOME/var-lib/hal0/.bundle-chosen`` (or
    ``/var/lib/hal0/.bundle-chosen`` in production). Lives alongside
    ``.first_run_done`` so a single ``rm -rf /var/lib/hal0`` resets
    both. Contains a JSON blob with the picked tier name + npu opt-in
    flag + ISO timestamp; treat as advisory not authoritative — the
    canonical record of selections is ``capabilities.toml``.
    """
    return var_lib() / ".bundle-chosen"


def profiles_toml() -> Path:
    """Return the profile catalog path (/etc/hal0/profiles.toml).

    The file is optional — :func:`hal0.config.loader.load_profiles_config`
    returns the built-in seed profiles when it is absent.

    FHS:       /etc/hal0/profiles.toml
    HAL0_HOME: $HAL0_HOME/etc/hal0/profiles.toml
    """
    return etc() / "profiles.toml"


def manifest_json() -> Path:
    """Return the release manifest path.

    The manifest pins toolbox image digests per hal0 release;
    `scripts/update-toolbox-digests.sh` refreshes them from ghcr.io
    before a release (see PLAN.md §12). At runtime we prefer the
    installed copy under /etc, falling back to the in-tree manifest at
    the source root for dev installs.

    FHS:        /etc/hal0/manifest.json
    HAL0_HOME:  $HAL0_HOME/etc/hal0/manifest.json
    Source dev: <repo>/manifest.json (looked up by the loader)
    """
    return etc() / "manifest.json"
