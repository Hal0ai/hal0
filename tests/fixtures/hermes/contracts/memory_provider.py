# SPDX-License-Identifier: MIT
# Copyright (c) 2025 Nous Research
"""Frozen copy of the Hermes ``MemoryProvider`` ABC surface hal0 consumes.

Adapter lane: **hal0-memory** (the one active external memory provider).

Signatures are copied verbatim from the reviewed official pin
``caafe5389ae585ef77387a962aee05b1bcf17029`` (``agent/memory_provider.py``,
Hermes v0.23.3). A Hermes bump that reshapes any of these lifecycle seams must
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
(``claim_instance``, ``agent/memory_manager.py:951-961``). hal0-memory serves
one profile and is constructed per load, so it already meets it.

What changed since v0.22.4 (``4d043038``): no existing signature, and the four
abstract methods are the same. Four optional methods are new, all with
defaults: ``on_profile_created`` and ``on_profile_renamed`` (no-ops,
``agent/memory_provider.py:245-252``), and ``read_config_fields`` /
``write_config_fields`` (``:256-267``), which Hermes calls only for a provider
whose ``config_schema.py`` declares ``storage=STORAGE_PROVIDER``; hal0-memory
ships no such file. ``initialize()`` may now also receive ``resumed`` and
``message_count``, and ``on_turn_start`` ``previous_message``; all arrive as
keywords a provider reads with ``kwargs.get``.
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

    # -- Profile lifecycle and the dashboard config panel (new at v0.23.3) ---

    def on_profile_created(self, profile_name: str, hermes_home: str) -> None:
        pass

    def on_profile_renamed(self, old_name: str, new_name: str, hermes_home: str) -> None:
        pass

    def read_config_fields(self, hermes_home: str) -> dict[str, Any]:
        return {"values": {}, "placeholders": {}}

    def write_config_fields(self, values: dict[str, Any], hermes_home: str) -> None:
        raise NotImplementedError
