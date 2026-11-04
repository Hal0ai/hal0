"""v4 — keep existing memory targets and opt upgrades out of chat priority.

Fresh configs use the memory → agent chain and enable priority by default.
An upgrade keeps the former utility default and leaves priority disabled unless
an operator explicitly stored a value, including in a partially present table.
"""

from __future__ import annotations

from typing import Any

from hal0.config.migrations import register


@register(4)
def migrate_v3_to_v4(data: dict[str, Any]) -> dict[str, Any]:
    """Pure transform: fill only absent values, leaving invalid tables to validation."""
    memory = data.get("memory", {})
    if not isinstance(memory, dict):
        return data
    memory = dict(memory)
    graph = memory.get("graph", {})
    if isinstance(graph, dict):
        memory["graph"] = {"extraction_slot": "utility", **graph}
    priority = memory.get("priority", {})
    if isinstance(priority, dict):
        memory["priority"] = {"enabled": False, **priority}
    return {**data, "memory": memory}
