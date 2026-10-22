# SPDX-License-Identifier: MIT
# Copyright (c) 2025 Nous Research
"""Frozen copy of the Hermes ``PluginContext`` registration seams hal0 uses.

Adapter lanes: **hal0-voice** (register_tts_provider / register_transcription_provider),
**hal0-provider** (register_auxiliary_task for role-scoped side jobs),
**hal0-hermes-executor** / control-plane (register_tool, register_hook,
dispatch_tool, inject_message), and the optional future **hal0-context**
(register_context_engine).

Signatures copied verbatim from the reviewed official pin
``4d04303818b61bf3aa09278af48356315515965b`` (``hermes_cli/plugins.py``,
Hermes v0.22.4; read with ``inspect.signature`` in a v0.22.4 venv, identical
to v0.22.2 for every seam frozen here).
``register(ctx)`` is called once at startup; a registration crash disables
only that plugin (per the design's "every adapter degrades independently"
requirement).

What changed since 0.19.0: most ``register_*`` methods now return a
``PluginRegistration`` handle (``hermes_cli/plugins_ledger.py:29``) so a forced
reload can unwind them; ``inject_message`` gained keyword-only ``session_key``
and ``origin``; ``register_auxiliary_task`` gained ``inherit_from``; the voice
registrars are generated from ``_SCOPED_PROVIDER_REGISTRARS``
(``plugins.py:1050-1060``); and the general context gained an inert
``register_memory_provider`` (see ``memory_loader.py``).
"""

# ruff: noqa: UP035, UP045

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any, Callable, Optional


@dataclass
class PluginRegistration:
    """One host-owned registration plus its inverse (plugins_ledger.py:29-36)."""

    kind: str
    key: str
    release: Callable[[], None]
    plugin_key: str = ""


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
    ) -> Optional[PluginRegistration]:
        return None

    def inject_message(
        self,
        content: str,
        role: str = "user",
        *,
        session_key: str | None = None,
        origin: Mapping[str, Any] | None = None,
    ) -> bool:
        return False

    def dispatch_tool(self, tool_name: str, args: dict, **kwargs) -> str:
        return ""

    def register_context_engine(self, engine) -> Optional[PluginRegistration]:
        return None

    def register_memory_provider(self, provider) -> None:
        pass

    def register_tts_provider(self, provider) -> Optional[PluginRegistration]:
        return None

    def register_transcription_provider(self, provider) -> Optional[PluginRegistration]:
        return None

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
    ) -> Optional[PluginRegistration]:
        return None

    def register_auxiliary_task(
        self,
        key: str,
        *,
        display_name: str,
        description: str,
        defaults: Optional[dict[str, Any]] = None,
        inherit_from: Optional[str] = None,
    ) -> PluginRegistration:
        raise NotImplementedError

    def register_hook(self, hook_name: str, callback: Callable) -> PluginRegistration:
        raise NotImplementedError
