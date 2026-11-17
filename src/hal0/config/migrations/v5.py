"""v5 — let the engine lane limits follow their new defaults (#3758, #3759, #3762).

Two things stored in an existing box's ``hal0.toml`` or drop-in directory would
otherwise pin the old behaviour after the update, and each is converted once,
here: ``meta.schema_version`` is the record that it ran.

1. ``[memory.lanes].llm_max_concurrent`` used to default to 1, and a settings
   save writes every non-None default, so a box that ever saved settings carries
   ``llm_max_concurrent = 1`` that the operator never chose. Unset now means
   "follow ``[memory.graph].extraction_max_concurrent``", and a stored 1 would
   read as an explicit choice and keep clamping everything to one call. A stored
   1 is removed. Any other stored value is a choice and stays. After this step
   has run, a 1 an operator sets is respected.

2. A hand-made drop-in in ``hindsight-api.service.d/`` may set
   ``HINDSIGHT_API_REFLECT_WALL_TIMEOUT`` or
   ``HINDSIGHT_API_CONSOLIDATION_LLM_BATCH_SIZE``. hal0 now renders both in its
   managed drop-in, which would silently replace the operator's value. When
   ``hal0.toml`` has no value for the matching key, the drop-in's value (inside
   the schema bounds) is adopted into it. The drop-in file is never touched; the
   log says it is now redundant.

Reading the drop-in directory needs no privilege and fails soft.
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any

import structlog

from hal0.config.migrations import register

log = structlog.get_logger(__name__)

#: Same directory as ``hal0.memory.extraction_env.DROP_IN_DIR`` (a test pins the
#: two together); spelled out so a config migration imports no memory module.
DROP_IN_DIR = Path("/etc/systemd/system/hindsight-api.service.d")

#: The files hal0 writes itself; everything else in the directory is the operator's.
MANAGED_DROP_INS = frozenset({"extraction-model.conf", "showcase-lanes.conf"})

#: ``(env name, table, key, lowest, highest)``; bounds mirror the schema fields.
ADOPTED = (
    ("HINDSIGHT_API_REFLECT_WALL_TIMEOUT", "reflect", "wall_timeout_s", 60, 86400),
    ("HINDSIGHT_API_CONSOLIDATION_LLM_BATCH_SIZE", "consolidation", "batch_size", 1, 32),
)

_ENV_LINE = re.compile(r"""^\s*Environment\s*=\s*["']?([A-Z0-9_]+)=([^"'\s]*)["']?\s*$""")


def operator_dropin_values(directory: Path) -> dict[str, tuple[str, str]]:
    """``{env name: (value, file name)}`` from the operator's drop-ins.

    Files are read in sorted order and the last assignment wins, as systemd
    merges them. An unreadable directory or file yields nothing for it.
    """
    found: dict[str, tuple[str, str]] = {}
    try:
        files = sorted(p for p in directory.glob("*.conf") if p.name not in MANAGED_DROP_INS)
    except OSError:
        return found
    wanted = {name for name, *_ in ADOPTED}
    for path in files:
        try:
            text = path.read_text(encoding="utf-8")
        except (OSError, UnicodeDecodeError):
            continue
        for line in text.splitlines():
            match = _ENV_LINE.match(line)
            if match and match.group(1) in wanted:
                found[match.group(1)] = (match.group(2), path.name)
    return found


@register(5)
def migrate_v4_to_v5(data: dict[str, Any]) -> dict[str, Any]:
    """Drop a stored default ``llm_max_concurrent = 1`` and adopt operator drop-in values."""
    memory = data.get("memory")
    memory = dict(memory) if isinstance(memory, dict) else {}
    lanes = memory.get("lanes")
    lanes = dict(lanes) if isinstance(lanes, dict) else {}
    changed = False

    stored = lanes.get("llm_max_concurrent")
    if stored == 1 and not isinstance(stored, bool):
        del lanes["llm_max_concurrent"]
        changed = True
        log.info(
            "updater.lane_limits_converted",
            removed="[memory.lanes].llm_max_concurrent = 1",
            reason="the old default; unset now follows [memory.graph].extraction_max_concurrent",
        )

    found = operator_dropin_values(DROP_IN_DIR)
    for env_name, table_name, key, low, high in ADOPTED:
        if env_name not in found:
            continue
        value, file_name = found[env_name]
        table = lanes.get(table_name)
        table = dict(table) if isinstance(table, dict) else {}
        if key in table:
            continue  # hal0.toml already says; the managed file wins by design
        if not value.isdigit() or not low <= int(value) <= high:
            log.warning(
                "updater.lane_limits_adopt_skipped",
                drop_in=file_name,
                name=env_name,
                value=value,
                reason=f"outside {low}-{high}",
            )
            continue
        table[key] = int(value)
        lanes[table_name] = table
        changed = True
        log.info(
            "updater.lane_limits_converted",
            adopted=f"[memory.lanes.{table_name}].{key} = {value}",
            drop_in=file_name,
            note=f"{file_name} is now redundant and can be deleted; hal0 left it in place",
        )

    if not changed:
        return data
    memory["lanes"] = lanes
    return {**data, "memory": memory}


__all__ = ["DROP_IN_DIR", "MANAGED_DROP_INS", "migrate_v4_to_v5", "operator_dropin_values"]
