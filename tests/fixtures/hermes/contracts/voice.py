# SPDX-License-Identifier: MIT
# Copyright (c) 2025 Nous Research
"""Frozen copy of the Hermes voice registration seams hal0-voice consumes.

Adapter lane: **hal0-voice** (routes Hermes STT/TTS through active hal0 slots).

Two seams from the reviewed official pin
``9de9c25f620ff7f1ce0fd5457d596052d5159596`` (Hermes v2026.7.7.2 / 0.18.2):

1. The **command-provider** config path (initial hal0-voice implementation):
   generated ``tts`` / ``stt`` config selecting stable hal0 wrapper commands.
   The frozen ``tts.provider`` / ``stt.provider`` config default keys live in
   ``config_defaults.py`` (``hermes_cli/config.py``).
2. The **Python-registration** path (deferred until streaming/cancellation/
   metadata cannot fit the command contract): ``PluginContext``'s
   ``register_tts_provider`` / ``register_transcription_provider`` (frozen in
   ``plugin_context.py``), which delegate to the module-level
   ``register_provider(provider) -> None`` of ``agent/tts_registry.py`` and
   ``agent/transcription_registry.py``.

``PluginContext`` is re-exported from ``plugin_context.py`` so the historical
``from tests.fixtures.hermes.contracts.voice import PluginContext`` import path
stays valid and there is exactly one frozen copy of the class.
"""

from __future__ import annotations

from tests.fixtures.hermes.contracts.plugin_context import PluginContext

__all__ = ["PluginContext"]
