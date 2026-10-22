# SPDX-License-Identifier: MIT
# Copyright (c) 2025 Nous Research
"""Frozen copy of Hermes ``providers.base.ProviderProfile`` + registration seam.

Adapter lane: **hal0-provider** (dynamic model-provider plugin, chat_completions).

Fields and the ``fetch_models`` / ``register_provider`` / ``get_provider_profile``
signatures are copied verbatim from the reviewed official pin
``4d04303818b61bf3aa09278af48356315515965b`` (``providers/base.py:41-160`` and
``providers/__init__.py:122, :192``, Hermes v0.22.4; ``providers/base.py`` is
unchanged since v0.22.2, and v0.22.3 only taught ``get_provider_profile`` /
``list_providers`` to return nothing inside a safe-mode worker). The hooks hal0-provider
overrides (``get_hostname``, ``prepare_messages``, ``build_extra_body``,
``build_api_kwargs_extras``, ``default_vision_model``, ``get_max_tokens``,
``fetch_models``) keep their 0.19.0 signatures. 0.22 added fields with
defaults that leave a hal0 profile's behaviour unchanged (``hidden``,
``supports_model_listing``, the ``auth_handler`` / ``refresh_credential`` /
``classify_api_error`` hooks, ``supports_prompt_cache_key``,
``native_reasoning_details_type``, the ``process_*`` external-process
fields, ``model_aliases``, ``unsupported_response_formats``,
``model_capabilities``). Since 0.22 ``register_provider`` also lands a
registration made while a ``$HERMES_HOME`` plugin imports in that home's
layer; name-based override still holds.

The provider lane consumes: ``api_mode`` defaulting to ``chat_completions``
(OpenAI-compat transport), ``aliases`` (stable role aliases), ``base_url`` /
``models_url`` (loopback hal0 discovery, never a hard-coded slot port),
``fetch_models`` (live ``/v1/models`` inventory normalization),
``supports_vision`` capability advertisement, and
``register_provider`` / ``get_provider_profile`` (name-based override so a hal0
profile can register under ``plugins/model-providers/hal0/``).
"""

# Upstream spells Callable from typing.
# ruff: noqa: UP035

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Callable

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

    hidden: bool = False

    # Auth & endpoints
    env_vars: tuple = ()
    base_url: str = ""
    models_url: str = ""
    auth_type: str = "api_key"
    supports_health_check: bool = True
    supports_model_listing: bool = True
    auth_handler: Callable[[str, Any], Any] | None = None
    refresh_credential: Callable[[Any], Any] | None = None
    classify_api_error: Callable[..., Any] | None = None

    # Vision support
    supports_vision: bool = False
    supports_vision_tool_messages: bool = True
    supports_prompt_cache_key: bool = False
    native_reasoning_details_type: str | None = None

    # External-process providers
    process_command: str = ""
    process_args: tuple = ()
    process_command_env_vars: tuple = ()
    process_args_env_var: str = ""

    # Model catalog
    fallback_models: tuple = ()
    model_aliases: dict = field(default_factory=dict)
    hostname: str = ""

    # Client-level quirks
    default_headers: dict[str, str] = field(default_factory=dict)

    # Request-level quirks
    fixed_temperature: Any = None
    default_max_tokens: int | None = None
    unsupported_response_formats: tuple = ()
    default_aux_model: str = ""
    model_capabilities: dict[str, dict[str, Any]] = field(default_factory=dict)

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
