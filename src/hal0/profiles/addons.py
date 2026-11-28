"""The profile addons hal0 publishes itself, and the index that lists them.

An addon is a portable profile envelope and nothing else (see
``docs/reference/profile-addons.mdx``). This module holds the definitions and
builds the envelopes and the ``hal0.addon-index`` catalog from them. Two
consumers share it, so they cannot disagree:

- ``scripts/export_addons.py`` writes the result to ``community/addons/``.
- :mod:`hal0.profiles.addon_catalog` serves the same rows as the dashboard's
  import catalog when the published index cannot be reached.

A name that is also a code-shipped seed (``agent``, ``coding``, ...) does not
belong here: importing it would collide with the seed.
"""

from __future__ import annotations

import re
from typing import Any

from hal0.config.schema import ProfileConfig
from hal0.profiles import _runtime_family
from hal0.profiles.portable import export_envelope

#: The published tunes, as profile definitions. Flags carry only what a
#: profile may hold: no hardware / managed / model-fact flags (hal0 adds
#: ``--jinja`` itself; ``vision = "off"`` replaces ``--no-mmproj``).
ADDON_PROFILES: dict[str, dict[str, object]] = {
    "chat-long-context": {
        "flags": "-fa auto -ctk q8_0 -ctv q8_0 -b 2048 -ub 512 --no-context-shift",
        "ctx_size": 131072,
        "intent": "Long-context chat · tuned for 128K ctx",
    },
    "dense": {
        "flags": (
            "-fa auto -b 2048 -ub 512 --reasoning off --reasoning-budget -1 "
            "--no-context-shift --temp 0 --top-p 0.95 --top-k 20 --seed 123"
        ),
        "ctx_size": 131072,
        "vision": "off",
        "intent": "Generic dense workload · tuned for 128K ctx",
    },
    "moe": {
        "flags": (
            "-fa auto -b 2048 -ub 512 --reasoning off --reasoning-budget -1 "
            "--no-context-shift --temp 0 --top-p 0.95 --top-k 20 --seed 123"
        ),
        "ctx_size": 32768,
        "vision": "off",
        "intent": "Generic MoE workload · tuned for 32K ctx",
    },
    "thinking": {
        "flags": (
            "-fa auto -b 2048 -ub 512 --reasoning on --reasoning-format deepseek "
            "--reasoning-budget -1 --no-context-shift --temp 1.0 --top-p 0.95 "
            "--top-k 20 --min-p 0 --presence-penalty 0"
        ),
        "ctx_size": 32768,
        "enable_thinking": True,
        "vision": "off",
        "intent": "Reasoning-ON workload (qwen3-deepseek, ace-saber thinking) · 32K ctx",
    },
}

#: Frozen, NOT a live clock. The envelopes are committed artifacts: a
#: wall-clock stamp would rewrite every file on every run and make the
#: checksum-stable output look churned. The checksum covers the profile body
#: only, so this value never affects integrity — it is a provenance note.
EXPORTED_AT = "2026-09-01T00:00:00Z"

INDEX_KIND = "hal0.addon-index"
INDEX_SCHEMA_VERSION = 1

#: The oldest release that can install a row from this index.
#:
#: Profile envelope schema 2 (ctx_size / enable_thinking / vision / speculation
#: "auto"|"off"|"ngram", ADR-0029,
#: renamed from `mtp` by ADR-0031) is read from 1.5.0; schema-1 envelopes are refused
#: there. The floor is a property of the CONTRACT, not of today's rows.
MIN_HAL0_VERSION = "1.5.0"

#: First separator in an ``intent`` string: a middot clause (`` · 32K ctx``)
#: or an opening parenthetical. Everything before it is the headline.
_TITLE_SPLIT = re.compile(r"\s*[·(]")


def envelope_filename(name: str) -> str:
    """The file an addon's envelope is published under, relative to the index."""
    return f"{name}.hal0profile.json"


def title_of(intent: str) -> str:
    """The intent's headline — the part before the first ``·`` or ``(``."""
    return _TITLE_SPLIT.split(intent, maxsplit=1)[0].strip() or intent.strip()


def build_envelopes() -> dict[str, dict[str, Any]]:
    """One envelope per published profile, keyed by profile name."""
    return {
        name: export_envelope(
            name,
            ProfileConfig.model_validate(entry),
            exported_at=EXPORTED_AT,
        )
        for name, entry in ADDON_PROFILES.items()
    }


def build_index(envelopes: dict[str, dict[str, Any]]) -> dict[str, Any]:
    """The ``index.json`` catalog — one row per envelope, sorted by name."""
    rows = []
    for name in sorted(envelopes):
        envelope = envelopes[name]
        body = envelope["profile"]
        profile = ProfileConfig.model_validate(ADDON_PROFILES[name])
        intent = body.get("intent", "")
        rows.append(
            {
                "name": name,
                "title": title_of(intent),
                "description": intent,
                "runtime_family": _runtime_family(name, profile),
                "runner": body.get("runner"),
                "checksum": envelope["checksum"],
                "file": envelope_filename(name),
                "min_hal0_version": MIN_HAL0_VERSION,
            }
        )
    return {"kind": INDEX_KIND, "schema_version": INDEX_SCHEMA_VERSION, "addons": rows}
