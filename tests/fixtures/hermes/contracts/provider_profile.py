# SPDX-License-Identifier: MIT
# Copyright (c) 2025 Nous Research
"""Minimal copy of Hermes ``providers.base.ProviderProfile`` fields we consume."""

from __future__ import annotations

from dataclasses import dataclass


@dataclass
class ProviderProfile:
    """Declarative inference-provider profile contract."""

    name: str
    api_mode: str = "chat_completions"
    aliases: tuple = ()
    base_url: str = ""
