# SPDX-License-Identifier: MIT
# Copyright (c) 2025 Nous Research
"""Frozen copy of Hermes ``providers.base.ProviderProfile`` + registration seam.

Adapter lane: **hal0-provider** (dynamic model-provider plugin, chat_completions).

Fields and the ``fetch_models`` / ``register_provider`` / ``get_provider_profile``
signatures are copied verbatim from the reviewed official pin
``3ef6bbd201263d354fd83ec55b3c306ded2eb72a`` (``providers/base.py`` and
``providers/__init__.py``, Hermes v2026.7.20 / 0.19.0).

The provider lane consumes: ``api_mode`` defaulting to ``chat_completions``
(OpenAI-compat transport), ``aliases`` (stable role aliases), ``base_url`` /
``models_url`` (loopback hal0 discovery, never a hard-coded slot port),
``fetch_models`` (live ``/v1/models`` inventory normalization),
``supports_vision`` capability advertisement, and
``register_provider`` / ``get_provider_profile`` (name-based override so a hal0
profile can register under ``plugins/model-providers/hal0/``).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

OMIT_TEMPERATURE = object()


@dataclass
class ProviderProfile:
    """Declarative inference-provider profile contract."""

    # Identity
    name: str
    api_mode: str = "chat_completions"
    aliases: tuple = ()

    # Human-readable metadata
    display_name: str = ""
    description: str = ""
    signup_url: str = ""

    # Auth & endpoints
    env_vars: tuple = ()
    base_url: str = ""
    models_url: str = ""
    auth_type: str = "api_key"
    supports_health_check: bool = True

    # Vision support
    supports_vision: bool = False
    supports_vision_tool_messages: bool = True

    # Model catalog
    fallback_models: tuple = ()
    hostname: str = ""

    # Client-level quirks
    default_headers: dict[str, str] = field(default_factory=dict)

    # Request-level quirks
    fixed_temperature: Any = None
    default_max_tokens: int | None = None
    default_aux_model: str = ""

    def get_hostname(self) -> str:
        return self.hostname

    def prepare_messages(self, messages: list[dict[str, Any]]) -> list[dict[str, Any]]:
        return messages

    def build_extra_body(self, *, session_id: str | None = None, **context: Any) -> dict[str, Any]:
        return {}

    def build_api_kwargs_extras(
        self,
        *,
        reasoning_config: dict | None = None,
        **context: Any,
    ) -> tuple[dict[str, Any], dict[str, Any]]:
        return {}, {}

    # Added upstream in 0.19.0. hal0-provider does not override it, so vision
    # resolution falls back to the chat model / aggregator chain as before.
    def default_vision_model(self) -> str | None:
        return None

    def get_max_tokens(self, model: str | None) -> int | None:
        return self.default_max_tokens

    def fetch_models(
        self,
        *,
        api_key: str | None = None,
        base_url: str | None = None,
        timeout: float = 8.0,
    ) -> list[str] | None:
        return None


# Module-level registration seam (providers/__init__.py). hal0-provider
# registers under its ``name`` and any ``aliases``; a later registration with
# the same name replaces an earlier one (user plugin overrides bundled).
_REGISTRY: dict[str, ProviderProfile] = {}
_ALIASES: dict[str, str] = {}


def register_provider(profile: ProviderProfile) -> None:
    _REGISTRY[profile.name] = profile
    for alias in profile.aliases:
        _ALIASES[alias] = profile.name


def get_provider_profile(name: str) -> ProviderProfile | None:
    canonical = _ALIASES.get(name, name)
    return _REGISTRY.get(canonical)
