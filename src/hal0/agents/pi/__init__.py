"""pi-agent vendored sources.

Holds code that ships into the pi agent's extension tree at provision time.
These modules target the pi (TypeScript/node) runtime; they are NOT imported
by hal0's Python process directly.

See ``src/hal0/agents/pi/plugins/`` for extensions deployed into
``~/.pi/agent/extensions/`` at provision time.
"""

from hal0.agents.pi.driver import PiAgentDriver

__all__ = ["PiAgentDriver"]
