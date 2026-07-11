"""Hermes agent vendored sources.

Only the driver lives here now. Plugin sources that ship INSIDE the
Hermes agent's plugin tree at provision time (code that targets the
hermes-agent venv where the upstream ``agent.memory_provider`` and
friends resolve) live at ``installer/agents/hermes/plugins/`` — that is
the canonical, shipped source; ``hal0.agents.hermes_provision._phase_install``
copies it verbatim into ``$HERMES_HOME/plugins/`` at provision time. An
earlier attempt to mirror plugin sources under this package
(``memory_hindsight/``) drifted out of sync with the installer copy and
was removed rather than reconciled — see git history if you need it.
"""

from hal0.agents.hermes.driver import HermesDriver

__all__ = ["HermesDriver"]
