"""v6 — the decision model's provider default went "local" -> "off" (#3757).

``[memory.decision].provider`` used to default to ``"local"``, and a settings
save writes every non-None default, so most boxes carry ``provider =
"local"`` that nobody chose, with no decision server behind it. A box that
left it to the default and runs a hand-installed server would instead lose
its lane to the new default. ``meta.schema_version`` is the record that this
ran, so it runs once per box:

* ``enabled = false`` is the operator's off switch and is never changed.
* An explicit ``base_url`` or ``provider = "hindsight-cloud"`` (or ``"off"``)
  is a choice and is left alone.
* ``provider = "local"``, stored or left to the old default, with no
  ``hal0-hs-decide`` unit file: becomes ``"off"`` (a stored value is
  rewritten; an absent one already reads as the new default and stays absent).
* The same with the unit file present: written as ``"local"`` explicitly, so
  the new default cannot switch a working lane off. ``enabled`` is not
  touched.

The one fact read from the box is whether the unit file exists
(:data:`DECISION_UNIT_PATH`, patched in tests). Raw dict edit: no other
default is written.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import structlog

from hal0.config.migrations import register

log = structlog.get_logger(__name__)

#: Same path as ``hal0.system.seam.DECISION_UNIT_PATH`` (a test pins the two
#: together); spelled out so a config migration imports no seam module.
DECISION_UNIT_PATH = Path("/etc/systemd/system/hal0-hs-decide.service")


def _unit_installed() -> bool:
    try:
        return DECISION_UNIT_PATH.is_file()
    except OSError:
        return False


@register(6)
def migrate_v5_to_v6(data: dict[str, Any]) -> dict[str, Any]:
    """Turn an unbacked old-default ``local`` off; pin a backed one as ``local``."""
    memory = data.get("memory")
    memory = dict(memory) if isinstance(memory, dict) else {}
    decision = memory.get("decision")
    decision = dict(decision) if isinstance(decision, dict) else {}

    if decision.get("enabled") is False or decision.get("base_url"):
        return data
    provider = decision.get("provider", "local")  # absent = the old default
    if provider != "local":
        return data
    to = "local" if _unit_installed() else "off"
    if decision.get("provider") == to or ("provider" not in decision and to == "off"):
        # Already right; an absent provider already reads as the new default.
        return data
    decision["provider"] = to
    memory["decision"] = decision
    log.info(
        "updater.memory_decision_provider_converted",
        provider=to,
        reason=(
            "a hal0-hs-decide unit is installed; written out so the new default cannot turn it off"
            if to == "local"
            else 'no decision server is installed (the old default was "local"); '
            "`hal0 memory decision install` turns it on"
        ),
    )
    return {**data, "memory": memory}


__all__ = ["DECISION_UNIT_PATH", "migrate_v5_to_v6"]
