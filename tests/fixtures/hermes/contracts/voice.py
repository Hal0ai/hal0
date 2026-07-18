# SPDX-License-Identifier: MIT
# Copyright (c) 2025 Nous Research
"""Minimal copy of Hermes voice registration signatures consumed by hal0."""

from __future__ import annotations


class PluginContext:
    """Voice-related portion of ``hermes_cli.plugins.PluginContext``."""

    def register_tts_provider(self, provider) -> None:
        pass

    def register_transcription_provider(self, provider) -> None:
        pass
