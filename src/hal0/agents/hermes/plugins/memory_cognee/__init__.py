"""hal0-cognee — Hermes ``MemoryProvider`` plugin (issue #240 / PR-2).

This package is COPIED verbatim into
``$HERMES_HOME/plugins/memory/hal0-cognee/`` at provision time by
``hal0.agents.hermes_provision._phase_install`` (rework lands in PR-3).
At runtime it resolves against the hermes-agent venv, where the
``agent.memory_provider`` ABC lives.

Discovery contract (per upstream ``plugins/memory/__init__.py``):

* Bundled-or-user plugin directory must contain an ``__init__.py`` that
  either exposes a top-level ``MemoryProvider`` subclass OR a
  ``register(ctx)`` callable. We ship both so either discovery path
  works:

  * Re-export ``Hal0CogneeProvider`` so the fallback ``find a subclass``
    branch at ``plugins/memory/__init__.py:_load_provider_from_dir``
    picks it up.
  * Provide ``register(ctx)`` so the preferred path (``_ProviderCollector``)
    fires ``ctx.register_memory_provider(...)``.

* ``plugin.yaml`` keeps ``kind: exclusive`` per ``MemoryManager``'s
  single-external-provider invariant.
"""

from __future__ import annotations

from .provider import Hal0CogneeProvider

__all__ = ["Hal0CogneeProvider", "register"]


def register(ctx) -> None:  # type: ignore[no-untyped-def]
    """Plugin entry point — registers the provider with the loader.

    ``ctx`` is the upstream ``PluginContext`` (or ``_ProviderCollector``
    test double). It exposes ``register_memory_provider(provider)`` per
    ``hermes-agent/plugins/memory/__init__.py:_ProviderCollector:288``.
    """
    ctx.register_memory_provider(Hal0CogneeProvider())
