"""v3 — raise a stored ``[memory.graph].extraction_max_tokens`` that cannot clear the plugin's chunks.

hindsight-api refuses every retain whose resolved ``retain_chunk_size`` is not
strictly below ``retain_max_completion_tokens`` (``hindsight_api/config.py``,
``validate_retain_completion_token_budget``). hal0 writes the cap from
``[memory.graph].extraction_max_tokens``. Before #3242 its default was 4096, and
the Hindsight coding-agent plugin sets ``retain_chunk_size = 12000`` on its
``gitlog``, ``survey``, ``document`` and ``conversation`` strategies, so every
one of those retains failed (#3326). #3242 raised the default to 16384, but a
box whose ``hal0.toml`` had already saved ``4096`` kept it, and kept failing.

This step lifts such a stored value to the current default. A value above the
plugin chunk size is the operator's choice and stays. A missing key already
resolves to the default, so nothing is written for it.
"""

from __future__ import annotations

from typing import Any

from hal0.config.migrations import register

#: Largest ``retain_chunk_size`` the Hindsight coding-agent plugin sets. A cap at
#: or below it fails every retain on those strategies.
PLUGIN_RETAIN_CHUNK_SIZE = 12000

#: What a cap at or below the plugin chunk size is raised to. Equals the
#: ``MemoryGraphConfig.extraction_max_tokens`` default.
RAISED_EXTRACTION_MAX_TOKENS = 16384


@register(3)
def migrate_v2_to_v3(data: dict[str, Any]) -> dict[str, Any]:
    """Raise ``memory.graph.extraction_max_tokens`` to 16384 when it is <= 12000.

    Pure: returns a copy and never mutates ``data``. A non-integer value is left
    for the schema validator to reject with its own message.
    """
    memory = data.get("memory")
    if not isinstance(memory, dict):
        return data
    graph = memory.get("graph")
    if not isinstance(graph, dict):
        return data
    value = graph.get("extraction_max_tokens")
    # bool is an int subclass; a True/False cap is a malformed config, not 1/0.
    if isinstance(value, bool) or not isinstance(value, int):
        return data
    if value > PLUGIN_RETAIN_CHUNK_SIZE:
        return data
    out = dict(data)
    out["memory"] = {
        **memory,
        "graph": {**graph, "extraction_max_tokens": RAISED_EXTRACTION_MAX_TOKENS},
    }
    return out


__all__ = ["PLUGIN_RETAIN_CHUNK_SIZE", "RAISED_EXTRACTION_MAX_TOKENS", "migrate_v2_to_v3"]
