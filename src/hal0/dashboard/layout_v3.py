"""The v3 dashboard layout model — fixed bands, swap-in-place (#1460).

The free-form 12-column grid (v2: ``order``/``enabled``/``spans``/``pinned``)
was replaced by a FIXED-BAND layout in #1061. Rows and cell widths are defined
by the system; the operator can only

  * swap which widget occupies a swappable cell, from a curated per-cell
    whitelist, and
  * toggle the quick-actions strip.

Schema::

    {"v": 3, "cells": {cellId: widgetId, ...}, "quickActions": bool}

This module is the SERVER-side mirror of ``CELL_DEFS`` / ``WIDGET_DEFS`` in
``ui/src/api/hooks/useDashLayout.ts``. It exists because the PUT route has to
reconcile against the same whitelists the client does — validating only the
JSON shape would let a malformed or stale client persist a cell holding a
widget that cell cannot render, which the next GET would faithfully hand back.
The two lists are duplicated across the language boundary on purpose (there is
no shared schema artefact); ``reconcile`` below states the rules so a drift is
a behaviour change, not a silent one.

Adding a widget or a cell means editing BOTH this file and useDashLayout.ts.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Final

LAYOUT_VERSION: Final[int] = 3


@dataclass(frozen=True)
class CellDef:
    """One fixed cell. ``accepts`` is the swap whitelist, in display order."""

    id: str
    accepts: tuple[str, ...]
    default_widget: str
    #: Locked cells render no swap affordance at all in the UI.
    locked: bool = False


#: Widget id → is it actually BUILT? Unbuilt widgets stay listed in the cell
#: tooltip (design intent) but must never be persisted as a cell's occupant.
WIDGET_BUILT: Final[dict[str, bool]] = {
    "memorybar": True,
    "memtreemap": False,
    "memring": False,
    "throughput": True,
    "slottrack": True,
    "power": True,
    "utilization": True,
    "gauges": False,
    "requests": True,
    "clients": False,
    "slots": True,
    "activity": True,
    "heatmap": False,
    "services": True,
    "quickchat": True,
    "attention": True,
}

#: One entry per fixed cell, top → bottom / left → right.
CELL_DEFS: Final[tuple[CellDef, ...]] = (
    CellDef("memory", ("memorybar", "memtreemap", "memring"), "memorybar"),
    CellDef("a1", ("throughput", "slottrack", "power"), "throughput"),
    CellDef("a2", ("utilization", "power", "gauges"), "utilization"),
    CellDef("a3", ("requests", "clients"), "requests"),
    CellDef("slots", ("slots",), "slots", locked=True),
    CellDef("c1", ("activity", "heatmap"), "activity"),
    CellDef("c2", ("services", "quickchat"), "services"),
    CellDef("c3", ("attention",), "attention", locked=True),
)

CELL_MAP: Final[dict[str, CellDef]] = {c.id: c for c in CELL_DEFS}


def default_layout() -> dict[str, Any]:
    """The layout an install starts from — every cell holding its default."""
    return {
        "v": LAYOUT_VERSION,
        "cells": {c.id: c.default_widget for c in CELL_DEFS},
        "quickActions": True,
    }


def reconcile(layout: dict[str, Any] | None) -> dict[str, Any]:
    """Return a normalised v3 layout. Pure — never raises, never mutates.

    Mirrors ``reconcile()`` in ui/src/api/hooks/useDashLayout.ts:

    1. Every cell in :data:`CELL_DEFS` is present in the result.
    2. A cell keeps its assigned widget only when the assignment is a string,
       is on that cell's own ``accepts`` whitelist, AND the widget is built.
       Anything else falls back to the cell's ``default_widget``, so a stale
       or hand-edited layout can never blank a band.
    3. Cells the registry doesn't know about are dropped.
    4. ``quickActions`` is true unless explicitly ``false`` (an absent flag
       means "unset", and the strip ships on).
    """
    raw_cells = layout.get("cells") if isinstance(layout, dict) else None
    if not isinstance(raw_cells, dict):
        raw_cells = {}

    cells: dict[str, str] = {}
    for cell in CELL_DEFS:
        assigned = raw_cells.get(cell.id)
        valid = (
            isinstance(assigned, str)
            and assigned in cell.accepts
            and WIDGET_BUILT.get(assigned, False)
        )
        cells[cell.id] = assigned if valid else cell.default_widget

    quick_actions = layout.get("quickActions") if isinstance(layout, dict) else None
    return {
        "v": LAYOUT_VERSION,
        "cells": cells,
        "quickActions": quick_actions is not False,
    }


__all__ = [
    "CELL_DEFS",
    "CELL_MAP",
    "LAYOUT_VERSION",
    "WIDGET_BUILT",
    "CellDef",
    "default_layout",
    "reconcile",
]
