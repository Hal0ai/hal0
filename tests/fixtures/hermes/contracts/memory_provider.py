# SPDX-License-Identifier: MIT
# Copyright (c) 2025 Nous Research
"""Frozen copy of the Hermes ``MemoryProvider`` ABC surface hal0 consumes.

Adapter lane: **hal0-memory** (the one active external memory provider).

Signatures are copied verbatim from the reviewed official pin
``4d04303818b61bf3aa09278af48356315515965b`` (``agent/memory_provider.py``,
Hermes v0.22.4). A Hermes bump that reshapes any of these lifecycle seams must
re-vendor this file and update the frozen signatures in
``tests/agents/hermes/test_contract_compatibility.py`` — that diff is the
tripwire the ``hermes-sdk-diff`` drift-watch and the compat-pin exist to force.

What changed since 0.19.0 (``3ef6bbd2``):

* the four abstract methods (``name``, ``is_available``, ``initialize``,
  ``get_tool_schemas``) are unchanged;
* new optional hooks with safe defaults: ``unavailable_reason``,
  ``recall_status``, ``identity_signature``, and the
  ``pre_compress_checkpoint_api_version`` class attribute;
* ``sync_turn`` gained a keyword-only ``turn_author``. The memory manager only
  passes it to signatures that accept it (``agent/memory_manager.py:441-460``), so
  hal0-memory's narrower override keeps working;
* annotations moved from ``List``/``Dict`` to builtin ``list``/``dict``.

What changed since v0.22.2 (``d923a5eb``): no signature. The ABC now documents
a multiplexing contract (#140058, ``agent/memory_provider.py:85-103``): every
load gets a new instance, bound from ``initialize()``'s ``hermes_home``, with no
profile state at module or class level. The memory manager enforces the binding
and refuses an instance another profile already claimed
(``agent/memory_manager.py:921-927``). hal0-memory serves one profile and is
constructed per load, so it already meets it.
"""

# Preserve the reviewed upstream ABC and typing spellings verbatim.
# ruff: noqa: B027, UP045

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass
from typing import Any, Optional

INDICATOR_GLYPH = "🧠"


@dataclass(frozen=True)
class RecallStatus:
    """What the last prefetch injected, for the deterministic recall indicator."""

    provider_label: str
    count: int
    glyph: str = INDICATOR_GLYPH


class MemoryProvider(ABC):
    """Contract surface required by the hal0 memory provider."""

    pre_compress_checkpoint_api_version = 1

    @property
    @abstractmethod
    def name(self) -> str:
        """Short identifier for this provider."""

    # -- Core lifecycle ------------------------------------------------------

    @abstractmethod
    def is_available(self) -> bool:
        """Config-only readiness check — no network."""

    @abstractmethod
    def initialize(self, session_id: str, **kwargs) -> None:
        """Per-session init; kwargs carry hermes_home, platform, agent_context, ..."""

    def unavailable_reason(self) -> str:
        return ""

    def system_prompt_block(self) -> str:
        return ""

    def prefetch(self, query: str, *, session_id: str = "") -> str:
        return ""

    def queue_prefetch(self, query: str, *, session_id: str = "") -> None:
        pass

    def recall_status(self) -> Optional[RecallStatus]:
        return None

    def sync_turn(
        self,
        user_content: str,
        assistant_content: str,
        *,
        session_id: str = "",
        messages: Optional[list[dict[str, Any]]] = None,
        turn_author: Optional[dict[str, Any]] = None,
    ) -> None:
        pass

    @abstractmethod
    def get_tool_schemas(self) -> list[dict[str, Any]]:
        """Memory tool schemas (search/recall/add/...)."""

    def handle_tool_call(self, tool_name: str, args: dict[str, Any], **kwargs) -> str:
        raise NotImplementedError

    def shutdown(self) -> None:
        pass

    # -- Optional hooks ------------------------------------------------------

    def on_turn_start(self, turn_number: int, message: str, **kwargs) -> None:
        pass

    def identity_signature(self) -> dict[str, Any]:
        return {}

    def on_session_end(self, messages: list[dict[str, Any]]) -> None:
        pass

    def on_session_switch(
        self,
        new_session_id: str,
        *,
        parent_session_id: str = "",
        reset: bool = False,
        rewound: bool = False,
        **kwargs,
    ) -> None:
        pass

    def on_pre_compress(self, messages: list[dict[str, Any]]) -> str:
        return ""

    def on_delegation(
        self, task: str, result: str, *, child_session_id: str = "", **kwargs
    ) -> None:
        pass

    def get_config_schema(self) -> list[dict[str, Any]]:
        return []

    def save_config(self, values: dict[str, Any], hermes_home: str) -> None:
        pass

    def on_memory_write(
        self,
        action: str,
        target: str,
        content: str,
        metadata: Optional[dict[str, Any]] = None,
    ) -> None:
        pass

    def backup_paths(self) -> list[str]:
        return []
