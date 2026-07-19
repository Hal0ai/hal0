"""hal0 Realtime engine — OpenAI Realtime WebSocket surface (HP-realtime inc-1).

The `WS /v1/realtime` route (``hal0.api.routes.realtime``) is a thin transport
shell; the turn state machine, event vocabulary, audio framing, VAD, and the two
LLM legs live here so they can be unit-tested without a socket or audio hardware.

Public surface (imported by the route + tests):

* :class:`~hal0.realtime.session.RealtimeSession` — per-connection state machine.
* :class:`~hal0.realtime.backends.RealtimeBackends` — STT/TTS/chat seams
  (default: loopback HTTP; tests inject fakes via ``app.state.realtime_backends``).
* :class:`~hal0.realtime.vad.EnergyVAD` — in-process energy-threshold server VAD.
* :mod:`~hal0.realtime.events` — the accepted / rejected / emitted event vocab.
* :mod:`~hal0.realtime.audio` — pcm16 <-> wav wrapping and output frame slicing.

Nothing here imports ``brain/chat.py``, ``mcp/**``, ``providers/**``, ``slots/**``
or ``omni_router/**`` directly — those surfaces are consumed over loopback HTTP
through :class:`RealtimeBackends` only (fence: HP-realtime inc-1).
"""

from __future__ import annotations

from hal0.realtime.backends import RealtimeBackends, get_backends
from hal0.realtime.session import RealtimeSession
from hal0.realtime.vad import EnergyVAD, VadDecision

__all__ = [
    "EnergyVAD",
    "RealtimeBackends",
    "RealtimeSession",
    "VadDecision",
    "get_backends",
]
