# SPDX-License-Identifier: MIT
# Copyright (c) 2025 Nous Research
"""Frozen copy of the Hermes memory-plugin loader contract hal0-memory targets.

Adapter lane: **hal0-memory** (installed seed layout + registration collector).

Copied from the reviewed official pin
``9de9c25f620ff7f1ce0fd5457d596052d5159596`` (``plugins/memory/__init__.py``,
Hermes v2026.7.7.2 / 0.18.2).

Two frozen facts the design doc's LXC105 compatibility-defect correction
(#3 in the research doc) depends on:

* **Directory layout** — an external memory provider is discovered from
  ``plugins/memory/<name>/`` (the *specialized* subtree), NOT the general
  top-level ``plugins/<name>/``. The hal0 provisioning must install the seed at
  ``plugins/memory/hal0-memory/`` for the selected Hermes target.
* **Registration collector** — the loader passes a collector context whose
  ``register_memory_provider(self, provider)`` captures the single external
  provider. The general ``PluginContext`` deliberately has no such method
  (memory providers are routed to this specialized loader instead).
"""

from __future__ import annotations

# Specialized discovery root, relative to a plugin source (bundled,
# $HERMES_HOME/plugins, or a project .hermes/plugins tree).
MEMORY_PLUGIN_SUBDIR = "plugins/memory"


class _ProviderCollector:
    """Loader context that captures ``register_memory_provider`` calls."""

    def __init__(self):
        self.provider = None

    def register_memory_provider(self, provider):
        self.provider = provider

    def register_tool(self, *args, **kwargs):
        pass

    def register_hook(self, *args, **kwargs):
        pass

    def register_cli_command(self, *args, **kwargs):
        pass
