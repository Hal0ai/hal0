"""Thin back-compat alias for the first-class hal0-brain chat engine.

The brain logic moved to :mod:`hal0.brain.chat` (SPEC §G / R4): the brain is
now a first-class module with ZERO Hermes/board import dependency, and
``POST /api/brain/chat`` is its PRIMARY route. ``POST /api/board/chat`` stays
as a byte-for-byte compatible alias so the dashboard's slide-out chat and the
frozen FE↔BE contract keep working unchanged — its auth gating (KB-1: the
``/api/board`` prefix is ADMIN in :mod:`hal0.security.exposure`) is untouched.

This module DELEGATES rather than duplicates: it rebinds itself to the brain
module object in ``sys.modules`` so ``from hal0.api.routes.board_chat import
X``, ``from hal0.api.routes import board_chat as bc`` (including ``bc._foo``
private helpers and ``monkeypatch.setattr(bc, ...)`` on module globals), and
the ``run_board_chat`` entry point all resolve to the SAME namespace as
:mod:`hal0.brain.chat`. One namespace ⇒ no drift, no re-export list to keep in
sync, and monkeypatching stays coherent with the code that reads the globals.
"""

from __future__ import annotations

import sys

from hal0.brain import chat as _brain_chat

# Rebind this module name to the brain engine's module object so every existing
# ``hal0.api.routes.board_chat`` import path (public + private names,
# ``run_board_chat`` entry point) transparently resolves to the first-class
# ``hal0.brain.chat`` engine. Must be the last statement — nothing after it
# runs against this shim's own namespace.
sys.modules[__name__] = _brain_chat
