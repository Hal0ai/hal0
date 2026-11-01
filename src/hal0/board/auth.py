"""Server-side auth helpers for the Hermes kanban proxy.

hal0-api reads the stable Hermes dashboard session token from the per-install
token file (minted by the agent shim, #2899) and never from the dashboard HTML.
"""

from __future__ import annotations

import os
from collections.abc import Callable, Mapping
from pathlib import Path
from typing import Any

from hal0.board import BoardNoToken
from hal0.cli.agent_shim import dashboard_session_token_path

_BASE_URL_ENV = "HERMES_DASHBOARD_BASE_URL"


def read_dashboard_token(path: Path | None = None) -> str:
    """Return the stripped Hermes dashboard token.

    Raises :class:`BoardNoToken` (with ``details["path"]``) when the file is
    missing, unreadable or blank.
    """
    target = path if path is not None else dashboard_session_token_path("hermes")
    try:
        token = target.read_text(encoding="utf-8").strip()
    except (OSError, UnicodeDecodeError) as exc:
        raise BoardNoToken(
            f"Hermes dashboard token file unreadable: {exc.__class__.__name__}",
            details={"path": str(target)},
        ) from exc
    if not token:
        raise BoardNoToken("Hermes dashboard token file is empty", details={"path": str(target)})
    return token


def hermes_installed(
    *,
    env: Mapping[str, str] = os.environ,
    manager_factory: Callable[[], Any] | None = None,
) -> bool:
    """True when an explicit dashboard base URL is set or Hermes is installed."""
    if env.get(_BASE_URL_ENV, "").strip():
        return True
    if manager_factory is None:
        from hal0.agents.manager import AgentManager  # heavy; import lazily

        manager_factory = AgentManager
    return "hermes" in manager_factory().installed_names()
