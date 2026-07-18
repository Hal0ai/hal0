# SPDX-License-Identifier: MIT
# Copyright (c) 2025 Nous Research
"""Frozen copy of the Hermes ``MemoryProvider`` ABC surface hal0 consumes.

Adapter lane: **hal0-memory** (the one active external memory provider).

Signatures are copied verbatim from the reviewed official pin
``9de9c25f620ff7f1ce0fd5457d596052d5159596`` (``agent/memory_provider.py``,
Hermes v2026.7.7.2 / 0.18.2). A Hermes bump that reshapes any of these
lifecycle seams must re-vendor this file and update the frozen signatures in
``tests/agents/hermes/test_contract_compatibility.py`` — that diff is the
tripwire the ``hermes-sdk-diff`` drift-watch and the compat-pin exist to force.
"""

# Preserve the reviewed upstream ABC and typing spellings verbatim.
# ruff: noqa: B027, UP006, UP035, UP045

from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Any, Dict, List, Optional


class MemoryProvider(ABC):
    """Contract surface required by the hal0 memory provider."""

    @property
    @abstractmethod
    def name(self) -> str:
        """Short identifier for this provider."""

    # -- Core lifecycle ------------------------------------------------------

    @abstractmethod
    def is_available(self) -> bool:
        """Config-only readiness check — no network (design: is_available())."""

    @abstractmethod
    def initialize(self, session_id: str, **kwargs) -> None:
        """Per-session init; kwargs carry server-derived identity/context."""

    def system_prompt_block(self) -> str:
        return ""

    def prefetch(self, query: str, *, session_id: str = "") -> str:
        return ""

    def queue_prefetch(self, query: str, *, session_id: str = "") -> None:
        pass

    def sync_turn(
        self,
        user_content: str,
        assistant_content: str,
        *,
        session_id: str = "",
        messages: Optional[List[Dict[str, Any]]] = None,
    ) -> None:
        pass

    @abstractmethod
    def get_tool_schemas(self) -> List[Dict[str, Any]]:
        """Memory tool schemas (search/recall/add/...)."""

    def handle_tool_call(self, tool_name: str, args: Dict[str, Any], **kwargs) -> str:
        raise NotImplementedError

    def shutdown(self) -> None:
        pass

    # -- Optional hooks ------------------------------------------------------

    def on_turn_start(self, turn_number: int, message: str, **kwargs) -> None:
        pass

    def on_session_end(self, messages: List[Dict[str, Any]]) -> None:
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

    def on_pre_compress(self, messages: List[Dict[str, Any]]) -> str:
        return ""

    def on_delegation(
        self, task: str, result: str, *, child_session_id: str = "", **kwargs
    ) -> None:
        pass

    def get_config_schema(self) -> List[Dict[str, Any]]:
        return []

    def save_config(self, values: Dict[str, Any], hermes_home: str) -> None:
        pass

    def on_memory_write(
        self,
        action: str,
        target: str,
        content: str,
        metadata: Optional[Dict[str, Any]] = None,
    ) -> None:
        pass

    def backup_paths(self) -> List[str]:
        return []
