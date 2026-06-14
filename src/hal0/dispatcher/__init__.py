"""hal0.dispatcher — Registry-aware request routing.

The Dispatcher reads the model registry and upstream list to decide where
to forward each OpenAI-compatible API request.  It handles:
  - Registry-exact routing (model_id → slot binding)
  - Cold-cache prefetch with single-flight coalescing
  - Remote upstream fallback (OpenRouter, Anthropic, etc.)
  - Capability/path routing (:func:`hal0.dispatcher.router.resolve_by_capability`),
    the last-resort step that pins embed/rerank/tts/image by path and FLM
    ``name:tag`` models to the npu slot
  - Structured decision logging to journald

The legacy ``proxy.py`` shim has been absorbed into ``router.py`` — its
``resolve_slot`` heuristics now live in ``router.resolve_by_capability``.

Port targets: haloai lib/dispatcher.py (617 lines), lib/proxy.py.
See PLAN.md §3 and §5 Tier 2/3.

Key exports:
    Dispatcher   — primary entry point; inject via FastAPI Depends().
    UpstreamCall — resolved routing decision returned by Dispatcher.dispatch().
"""

from __future__ import annotations

from hal0.dispatcher.router import (
    Dispatcher,
    DispatchError,
    NoRouteFound,
    RegistryLoadFailed,
    UnknownUpstream,
    UpstreamCall,
)
from hal0.dispatcher.single_flight import SingleFlightGroup

__all__ = [
    "DispatchError",
    "Dispatcher",
    "NoRouteFound",
    "RegistryLoadFailed",
    "SingleFlightGroup",
    "UnknownUpstream",
    "UpstreamCall",
]
