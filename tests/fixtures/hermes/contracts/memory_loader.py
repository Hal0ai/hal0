# SPDX-License-Identifier: MIT
# Copyright (c) 2025 Nous Research
"""Frozen copy of the Hermes memory-plugin loader contract hal0-memory targets.

Adapter lane: **hal0-memory** (installed seed layout + registration collector).

Copied from the reviewed official pin
``4d04303818b61bf3aa09278af48356315515965b`` (``plugins/memory/__init__.py``,
Hermes v0.22.4; the loader is unchanged since v0.22.2).

Frozen facts:

* **Directory layout** — bundled providers live in ``plugins/memory/<name>/``
  inside the Hermes tree; a user-installed provider is discovered from
  ``$HERMES_HOME/plugins/<name>/`` (``plugins/memory/__init__.py:1-7``,
  ``plugins/plugin_loader.py:81-87``). hal0 provisioning installs the seed at
  ``$HERMES_HOME/plugins/hal0-memory/``
  (``hal0.agents.hermes_provision`` plugin targets).
* **Registration collector** — the loader passes a collector context whose
  ``register_memory_provider(self, provider)`` captures the single external
  provider (``plugins/memory/__init__.py:397-485``). Since 0.22 the collector
  forwards any other ``register_*`` call to a real ``PluginContext``.
* **The general PluginContext** — gained an INERT
  ``register_memory_provider`` in 0.22 (``hermes_cli/plugins.py:719-728``): it
  only records the provider. Activation still belongs to the memory loader,
  through ``memory.provider``.
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

    def register_hook(self, hook_name, callback):
        pass

    def register_memory_provider(self, provider):
        self.provider = provider

    def register_skill(self, *args, **kwargs):
        pass

    def register_cli_command(self, *args, **kwargs):
        pass
