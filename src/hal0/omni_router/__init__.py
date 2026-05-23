"""Client-side OmniRouter — OpenAI tool-calling loop owned by hal0.

ADR-0008 §8 + plan §7. Lemonade provides the local tool endpoints
(``/v1/images/*``, ``/v1/audio/*``, ``/v1/embeddings``, ``/v1/rerank``);
hal0 provides the LLM loop that dispatches them.

Eight tools ship in v0.2:

  * **Upstream-mirrored** — ``generate_image``, ``edit_image``,
    ``text_to_speech``, ``transcribe_audio``, ``analyze_image``.
  * **hal0-custom** — ``embed_text``, ``rerank_documents``,
    ``route_to_chat``.

Dynamic filtering: a tool ships to the LLM only if at least one
enabled slot of its target type exists and (for label-gated tools) at
least one of those slots has a model with the required labels. LLMs
without the ``tool-calling`` label receive no tools at all.

Public surface:

  * :class:`OmniRouter` — the router; ``active_tools``, ``dispatch``,
    ``run_loop``.
  * :class:`ToolDefinition` — typed view of a tool entry.
  * :data:`TOOL_DEFINITIONS` — the eight tool definitions loaded from
    ``tool_definitions.json``.
"""

from __future__ import annotations

from hal0.omni_router.router import OmniRouter
from hal0.omni_router.tools import TOOL_DEFINITIONS, ToolDefinition, tools_by_name

__all__ = [
    "TOOL_DEFINITIONS",
    "OmniRouter",
    "ToolDefinition",
    "tools_by_name",
]
