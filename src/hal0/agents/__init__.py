"""hal0 Agents subsystem (Phase 8, v0.2).

Bundled third-party agent apps. Single-pick at install. Shim-first
ownership: hal0 owns the install scripts under ``installer/agents/*.sh``;
this package wraps them in Python so the CLI + API speak one interface.

This is NOT a first-party agent runtime — see ADR-0004 §1 ("Bundle,
don't build"). The drivers in :mod:`hal0.agents.pi_coder` and
:mod:`hal0.agents.hermes` only handle install/uninstall wiring + config
writes. Runtime is whatever the bundled upstream does natively.
"""

from __future__ import annotations

from hal0.agents.manager import (
    BUNDLED_AGENTS,
    AgentAlreadyInstalledError,
    AgentManager,
    AgentNotFoundError,
    AgentRecord,
    HermesNotHal0AwareError,
)

__all__ = [
    "BUNDLED_AGENTS",
    "AgentAlreadyInstalledError",
    "AgentManager",
    "AgentNotFoundError",
    "AgentRecord",
    "HermesNotHal0AwareError",
]
