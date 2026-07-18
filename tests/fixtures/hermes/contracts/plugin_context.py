# SPDX-License-Identifier: MIT
# Copyright (c) 2025 Nous Research
"""Frozen copy of the Hermes ``PluginContext`` registration seams hal0 uses.

Adapter lanes: **hal0-voice** (register_tts_provider / register_transcription_provider),
**hal0-provider** (register_auxiliary_task for role-scoped side jobs),
**hal0-hermes-executor** / control-plane (register_tool, register_hook,
dispatch_tool, inject_message), and the optional future **hal0-context**
(register_context_engine).

Signatures copied verbatim from the reviewed official pin
``9de9c25f620ff7f1ce0fd5457d596052d5159596`` (``hermes_cli/plugins.py``,
Hermes v2026.7.7.2 / 0.18.2). ``register(ctx)`` is called once at startup; a
registration crash disables only that plugin (per the design's "every adapter
degrades independently" requirement).

Note: memory providers are NOT registered through this general context —
they use the specialized ``plugins/memory/<name>/`` loader whose collector
context exposes ``register_memory_provider`` (see ``memory_loader.py``).
"""

# ruff: noqa: UP006, UP035, UP045

from __future__ import annotations

from typing import Any, Callable, Dict, Optional


class PluginContext:
    """Registration surface passed to a plugin's ``register(ctx)``."""

    def register_tool(
        self,
        name: str,
        toolset: str,
        schema: dict,
        handler: Callable,
        check_fn: Callable | None = None,
        requires_env: list | None = None,
        is_async: bool = False,
        description: str = "",
        emoji: str = "",
        override: bool = False,
    ) -> None:
        pass

    def inject_message(self, content: str, role: str = "user") -> bool:
        return False

    def dispatch_tool(self, tool_name: str, args: dict, **kwargs) -> str:
        return ""

    def register_context_engine(self, engine) -> None:
        pass

    def register_tts_provider(self, provider) -> None:
        pass

    def register_transcription_provider(self, provider) -> None:
        pass

    def register_platform(
        self,
        name: str,
        label: str,
        adapter_factory: Callable,
        check_fn: Callable,
        validate_config: Callable | None = None,
        required_env: list | None = None,
        install_hint: str = "",
        **entry_kwargs: Any,
    ) -> None:
        pass

    def register_auxiliary_task(
        self,
        key: str,
        *,
        display_name: str,
        description: str,
        defaults: Optional[Dict[str, Any]] = None,
    ) -> None:
        pass

    def register_hook(self, hook_name: str, callback: Callable) -> None:
        pass
