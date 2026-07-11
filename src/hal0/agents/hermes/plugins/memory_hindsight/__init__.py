"""hal0-memory — Hermes ``MemoryProvider`` plugin.

Canonical source; the installer seed at
``installer/agents/hermes/plugins/hal0-memory/`` is a byte-identical copy.
Two-bank model (private:<agent-id> + shared, agent-id ``hermes``, explicit
memory tools). See ``provider.py`` / ``_client.py`` docstrings.

Copied verbatim into ``$HERMES_HOME/plugins/hal0-memory/`` at provision time
by ``hal0.agents.hermes_provision._phase_install``. At runtime it resolves
against the hermes-agent venv, where the ``agent.memory_provider`` ABC lives.

Discovery contract (upstream ``plugins/memory/__init__.py``): a plugin dir
under ``$HERMES_HOME/plugins/<name>/`` is loaded if its ``__init__.py`` exposes
either a top-level ``MemoryProvider`` subclass OR a ``register(ctx)`` callable.
We ship both:
  * Re-export ``Hal0MemoryProvider`` for the ``find a subclass`` fallback.
  * Provide ``register(ctx)`` for the preferred ``_ProviderCollector`` path.
``plugin.yaml`` keeps ``kind: exclusive`` per MemoryManager's
single-external-provider invariant.
"""

from __future__ import annotations

from .provider import Hal0MemoryProvider

__all__ = ["Hal0MemoryProvider", "register"]


def register(ctx) -> None:  # type: ignore[no-untyped-def]
    """Plugin entry point — register the provider with the loader."""
    ctx.register_memory_provider(Hal0MemoryProvider())
