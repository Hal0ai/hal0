"""hal0 ↔ Lemonade Server integration (v0.2).

Lemonade Server is AMD's unified inference daemon. hal0 v0.2 replaces
the six per-modality toolbox containers with a single Lemonade
instance, driven over HTTP by ``LemonadeClient`` below.

ADRs:
- 0008 — Lemonade adoption as the unified inference runtime (supersedes
  0006 + 0007). The pre-load sha256/GGUF validation work that lived
  here (``preload.py``) was removed per ADR-0008 §3 — per-type LRU
  plus nuclear-evict's not-found exemption list reduce the original
  hazard below the cost of those checks.

The full module set (sequenced in
``docs/internal/lemonade-adoption-plan-2026-05-22.md`` §11):

  ``client.py``              — HTTP wrapper for the lemond control plane.
  ``errors.py``              — exception hierarchy callers raise/catch.
  ``idle.py``                — idle-unload driver (live in hal0-api).
  ``server_models_gen.py``   — registry.toml → server_models.json.
  ``metrics_shim.py`` (later)— ``/v1/stats`` + ``/v1/health`` aggregator.
"""

from hal0.lemonade.client import LemonadeClient
from hal0.lemonade.errors import (
    LemonadeError,
    LemonadeHTTPError,
    LemonadeLoadError,
    LemonadeTimeoutError,
    LemonadeUnavailableError,
)
from hal0.lemonade.idle import IdleDriver
from hal0.lemonade.server_models_gen import (
    generate_server_models,
    write_server_models,
)

__all__ = [
    "IdleDriver",
    "LemonadeClient",
    "LemonadeError",
    "LemonadeHTTPError",
    "LemonadeLoadError",
    "LemonadeTimeoutError",
    "LemonadeUnavailableError",
    "generate_server_models",
    "write_server_models",
]
