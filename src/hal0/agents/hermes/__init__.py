"""Hermes agent vendored sources.

Holds Python code that ships INSIDE the Hermes agent's plugin tree at
provision time. These modules are NOT imported by hal0 itself; they
target the hermes-agent venv where the upstream ``agent.memory_provider``
and friends resolve.

See ``installer/agents/hermes/plugins/`` for the legacy mirror location
that the bootstrap copies from today. PR-3 (hermes_provision overhaul)
will switch the copy source to this tree.
"""

from hal0.agents.hermes.driver import HermesDriver

__all__ = ["HermesDriver"]
