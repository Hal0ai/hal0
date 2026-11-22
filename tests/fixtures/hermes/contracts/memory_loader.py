# SPDX-License-Identifier: MIT
# Copyright (c) 2025 Nous Research
"""Frozen copy of the Hermes memory-plugin loader contract hal0-memory targets.

Adapter lane: **hal0-memory** (installed seed layout + registration collector).

Copied from the reviewed official pin
``caafe5389ae585ef77387a962aee05b1bcf17029`` (``plugins/memory/__init__.py``,
Hermes v0.23.3).

Frozen facts:

* **Directory layout** — bundled providers live in ``plugins/memory/<name>/``
  inside the Hermes tree; a user-installed provider is discovered from
  ``$HERMES_HOME/plugins/<name>/`` (``plugins/memory/__init__.py:1-8``,
  ``plugins/plugin_loader.py:85``). hal0 provisioning installs the seed at
  ``$HERMES_HOME/plugins/hal0-memory/``
  (``hal0.agents.hermes_provision`` plugin targets).
* **Registration collector** — the loader passes a collector context whose
  ``register_memory_provider(self, provider)`` captures the single external
  provider (``plugins/memory/__init__.py:437-487``). Since 0.22 the collector
  forwards any other ``register_*`` call to a real ``PluginContext``; at
  v0.23.3 its ``register_hook`` also takes keyword-only ``hold_stream`` and it
  wraps a tool a provider registers so only the bound profile can call it
  (``:480-497``, #147659). hal0-memory registers neither.
* **The general PluginContext** — gained an INERT
  ``register_memory_provider`` in 0.22 (``hermes_cli/plugins_context.py:533-542``
  at v0.23.3): it only records the provider. Activation still belongs to the
  memory loader, through ``memory.provider``.
* **Providers that left core** — ``hindsight``, ``honcho``, ``mem0`` and five
  more once-bundled providers are catalog plugins that Hermes installs on
  demand for a home that names one in ``memory.provider``
  (``hermes_cli/memory_provider_migration.py:35``; the same set at v0.22.4).
  ``hal0-memory`` is not in that set and has always been a user plugin, so
  nothing is fetched for it.
"""

from __future__ import annotations

# Bundled-provider discovery root inside the Hermes tree.
MEMORY_PLUGIN_SUBDIR = "plugins/memory"
# User-installed provider root, relative to $HERMES_HOME.
USER_PLUGIN_SUBDIR = "plugins"


class _ProviderCollector:
    """Loader context that captures ``register_memory_provider`` calls."""

    def __init__(self, name: str, *, register_skills: bool = True):
        self.name = name
        self.provider = None

    def register_hook(self, hook_name, callback, *, hold_stream=False):
        pass

    def register_memory_provider(self, provider):
        self.provider = provider

    def register_skill(self, *args, **kwargs):
        pass

    def register_cli_command(self, *args, **kwargs):
        pass
