"""Dynamic per-request tool filtering — plan §7.3.

Given the active chat slot and the live SlotManager state, compute
the subset of tools that:

  1. Have at least one enabled slot of the tool's ``target_slot_type``.
  2. (For label-gated tools) have at least one of those slots with a
     model that carries every required label.
  3. Are gated by the chat slot's own caller-label requirement —
     LLMs without ``tool-calling`` receive an empty list, full stop.

The filter is recomputed per chat completion; the LLM in slot A can be
swapped to a non-tool-calling model mid-conversation and the next
request will simply ship no tools. The "set changes mid-conversation"
language in plan §7.3 is handled here by the fact that this function
is called every request.

Pure-async + dependency-injected against ``SlotManager.iter_configs``
+ ``route_for_request`` so unit tests can drive matrix scenarios
without standing up a full SlotManager.
"""

from __future__ import annotations

from collections.abc import Iterable
from typing import Any, Protocol

from hal0.omni_router.tools import TOOL_DEFINITIONS, ToolDefinition


class SlotManagerLike(Protocol):
    """The narrow SlotManager surface filter.py + dispatch.py need.

    Stated as a Protocol so unit tests can pass a hand-rolled stub
    without subclassing the real SlotManager (which has heavy state
    machinery and a systemd-template surface that's irrelevant here).
    """

    async def iter_configs(self) -> list[dict[str, Any]]: ...

    async def route_for_request(
        self,
        slot_type: str,
        *,
        required_labels: tuple[str, ...] = (),
    ) -> str | None: ...


def _labels_of_model(cfg: dict[str, Any]) -> set[str]:
    """Pull the model.labels list out of a slot config.

    Mirrors :func:`SlotManager.route_for_request` 's ``_labels_of``
    helper — keep the two in sync so the filter's decision matches
    what ``route_for_request`` will pick.
    """
    model = cfg.get("model") or {}
    if isinstance(model, dict):
        raw = model.get("labels", ())
        if isinstance(raw, (list, tuple)):
            return {str(x) for x in raw}
    return set()


def chat_slot_has_tool_calling(cfg: dict[str, Any]) -> bool:
    """Return True iff the chat slot's model carries the ``tool-calling`` label.

    Per plan §7.3 this is the master gate — without ``tool-calling``
    on the caller's model, hal0 ships an empty tool list regardless of
    what other slots are configured. The LLM has no opinion on the
    tools because it never sees them.
    """
    return "tool-calling" in _labels_of_model(cfg)


async def active_tools_for(
    slot_manager: SlotManagerLike,
    chat_slot_name: str,
    *,
    tools: Iterable[ToolDefinition] = TOOL_DEFINITIONS,
) -> list[ToolDefinition]:
    """Return the filtered tool list for a chat slot, per plan §7.3.

    Args:
        slot_manager: source of truth for slot configs + routing.
        chat_slot_name: the slot whose model is driving this request.
        tools: tool universe to filter; defaults to the eight v0.2
            definitions. Overridable in tests.

    Returns:
        Subset of ``tools`` in declaration order. Empty when the
        caller slot lacks ``tool-calling``, or when no other slot
        satisfies any tool's constraints.
    """
    configs = await slot_manager.iter_configs()
    caller_cfg = next((c for c in configs if c.get("name") == chat_slot_name), None)
    if caller_cfg is None:
        # Caller slot vanished mid-flight — fail closed.
        return []
    if not chat_slot_has_tool_calling(caller_cfg):
        return []

    active: list[ToolDefinition] = []
    for tool in tools:
        if tool.name == "route_to_chat":
            # route_to_chat is included iff at least one OTHER enabled
            # chat slot exists. The caller's own tool-calling label has
            # been validated above; the targets don't need it (a target
            # without tool-calling simply returns a non-tool-call
            # response, which is fine).
            has_peer = any(
                c.get("type") == "llm"
                and c.get("enabled", True)
                and c.get("name") != chat_slot_name
                for c in configs
            )
            if has_peer:
                active.append(tool)
            continue

        # Standard tools: ask SlotManager for routing.
        target = await slot_manager.route_for_request(
            tool.target_slot_type,
            required_labels=tool.required_model_labels,
        )
        if target is not None:
            active.append(tool)
    return active


__all__ = [
    "SlotManagerLike",
    "active_tools_for",
    "chat_slot_has_tool_calling",
]
