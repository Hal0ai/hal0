"""Fire-and-forget trigger to refresh the Hermes live-context files.

Called from the asyncio daemon (slot swap / capability apply) where we
must not block the event loop on the urllib probe inside
``render_live_context``. We spawn a detached ``hal0-agent <id>
render-context`` instead — the subcommand owns the probe + atomic writes.
Best-effort: any failure is logged and swallowed; a model swap must never
fail because context refresh couldn't start.
"""

from __future__ import annotations

import logging
import shutil
import subprocess

logger = logging.getLogger(__name__)


def spawn_context_refresh(agent_id: str = "hermes") -> None:
    """Spawn a detached ``hal0-agent <agent_id> render-context``. Never raises."""
    try:
        binary = shutil.which("hal0-agent") or "/usr/local/bin/hal0-agent"
        subprocess.Popen(  # fixed argv, no shell
            [binary, agent_id, "render-context"],
            stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            start_new_session=True,
        )
    except Exception as exc:  # best-effort, never propagate
        logger.debug("hermes context refresh spawn failed (non-fatal): %s", exc)
