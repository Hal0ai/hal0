# SPDX-License-Identifier: MIT
# Copyright (c) 2025 Nous Research
"""Minimal copy of the Hermes memory lifecycle signatures hal0 consumes."""

from __future__ import annotations

from abc import ABC
from typing import Any, Dict, List, Optional


class MemoryProvider(ABC):
    """Contract surface required by the hal0 memory provider."""

    def system_prompt_block(self) -> str:
        return ""

    def prefetch(self, query: str, *, session_id: str = "") -> str:
        return ""

    def sync_turn(
        self,
        user_content: str,
        assistant_content: str,
        *,
        session_id: str = "",
        messages: Optional[List[Dict[str, Any]]] = None,
    ) -> None:
        pass
