"""First-class hal0-brain module — the resident platform steward.

The brain drives a client-side OpenAI tool-calling loop over the shared
tool-loop engine (:mod:`hal0.toolloop.engine`) and administers the whole
instance (slots, models, hardware, the operator board, orchestration).

HARD INVARIANT (SPEC §G / R4): the core brain works WITHOUT Hermes. This
package has ZERO import dependency on any Hermes/agent-plugin or board
module — the operator-board client and the platform self-API are reached
only through ``app.state`` handles injected at runtime. Importing this
package never pulls in a board/Hermes dependency.

:func:`run_brain_chat` is the primary entry point, mounted at
``POST /api/brain/chat``; ``POST /api/board/chat`` is a thin back-compat
alias (:mod:`hal0.api.routes.board_chat`) that delegates here.
"""

from __future__ import annotations

from hal0.brain.chat import run_board_chat, run_brain_chat

__all__ = [
    "run_board_chat",
    "run_brain_chat",
]
