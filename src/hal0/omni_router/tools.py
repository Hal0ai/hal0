"""Typed tool definitions loaded from ``tool_definitions.json``.

Plan §7.2 + §7.5. The eight tools are sourced from a JSON file rather
than coded inline so the drift-detection script (plan §7.5,
``scripts/check-tool-definitions.sh``, deferred to a follow-up PR) can
diff the upstream-mirrored fields without re-implementing the Python
side. Each entry carries the dispatch metadata hal0 needs:

  * ``name`` — tool name surfaced to the LLM.
  * ``source`` — ``"upstream"`` (mirrored from Lemonade) or ``"hal0"``.
  * ``target_slot_type`` — slot type that serves the request
    (``image``, ``tts``, ``transcription``, ``embedding``,
    ``reranking``, ``llm``).
  * ``required_model_labels`` — labels the chosen slot's model MUST
    advertise. Empty for ``route_to_chat`` (caller carries the
    ``tool-calling`` label, target carries no constraint).
  * ``endpoint`` — Lemonade path the dispatch handler calls.
    ``None`` for ``route_to_chat`` (internal, no Lemonade endpoint).
  * ``description`` — natural-language hint shown to the LLM.
  * ``parameters`` — OpenAI JSON-schema for the tool's args.

The JSON file's ``_pin`` block carries the upstream pin metadata. The
drift script consults that block; Python ignores it.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any

_DEFINITIONS_PATH = Path(__file__).parent / "tool_definitions.json"


@dataclass(frozen=True)
class ToolDefinition:
    """A single tool entry — immutable after load.

    The dataclass is frozen so a caller can't mutate the shared
    definition between requests; each filter pass returns the same
    instances directly.
    """

    name: str
    source: str
    target_slot_type: str
    required_model_labels: tuple[str, ...]
    endpoint: str | None
    description: str
    parameters: dict[str, Any]

    def to_openai_tool(self) -> dict[str, Any]:
        """Render this tool as the OpenAI ``tools=[...]`` wire shape.

        OpenAI expects ``{"type": "function", "function": {name,
        description, parameters}}``. Lemonade follows the same shape;
        hal0 forwards the result verbatim into the body.
        """
        return {
            "type": "function",
            "function": {
                "name": self.name,
                "description": self.description,
                "parameters": self.parameters,
            },
        }


def _load_tool_definitions() -> tuple[ToolDefinition, ...]:
    """Load + freeze the eight tool definitions at import time.

    Raises:
        ValueError: if the JSON file is missing required fields or has
            an unexpected count. The eight-tool count is invariant for
            v0.2; a mismatch means the upstream-mirror script bumped
            without coordination.
    """
    raw = json.loads(_DEFINITIONS_PATH.read_text(encoding="utf-8"))
    entries = raw.get("tools")
    if not isinstance(entries, list):
        raise ValueError("tool_definitions.json: missing 'tools' list")
    out: list[ToolDefinition] = []
    for entry in entries:
        if not isinstance(entry, dict):
            raise ValueError("tool_definitions.json: every tool must be an object")
        # Required keys; ``endpoint`` may be null for route_to_chat.
        for key in (
            "name",
            "source",
            "target_slot_type",
            "required_model_labels",
            "description",
            "parameters",
        ):
            if key not in entry:
                raise ValueError(f"tool_definitions.json: tool missing '{key}'")
        out.append(
            ToolDefinition(
                name=str(entry["name"]),
                source=str(entry["source"]),
                target_slot_type=str(entry["target_slot_type"]),
                required_model_labels=tuple(entry["required_model_labels"]),
                endpoint=(str(entry["endpoint"]) if entry.get("endpoint") is not None else None),
                description=str(entry["description"]),
                parameters=dict(entry["parameters"]),
            )
        )
    return tuple(out)


# Eight tools — frozen, shared, immutable across requests.
TOOL_DEFINITIONS: tuple[ToolDefinition, ...] = _load_tool_definitions()


def tools_by_name() -> dict[str, ToolDefinition]:
    """Return a name → ToolDefinition map. Built fresh per call.

    Kept as a function (not a module constant) so unit tests that mock
    individual tools can do so without monkey-patching the const.
    """
    return {t.name: t for t in TOOL_DEFINITIONS}


__all__ = [
    "TOOL_DEFINITIONS",
    "ToolDefinition",
    "tools_by_name",
]
