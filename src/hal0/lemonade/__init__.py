"""hal0 ↔ Lemonade Server integration (v0.2).

Lemonade Server is AMD's unified inference daemon. hal0 v0.2 replaces
the six per-modality toolbox containers with a single Lemonade
instance, driven over HTTP by ``LemonadeClient`` below.

ADRs:
- 0006 — Migrate inference to Lemonade Server (parent decision).
- 0007 — Nuclear-evict-all mitigation (operational hazard wrap-around).

The full module set (sequenced in
``docs/internal/lemonade-migration-plan.md`` §PR sequence):

  ``client.py`` (this PR)  — HTTP wrapper for the lemond control plane.
  ``errors.py`` (this PR)  — exception hierarchy callers raise/catch.
  ``preload.py`` (later)   — file/sha256/GGUF guards before ``/v1/load``.
  ``metrics.py`` (later)   — ``/v1/stats`` + ``/v1/health`` aggregator.

Nothing here is wired into the running stack yet. Activation is gated
behind ``HAL0_BACKEND=lemonade`` (manifest schema v2); the v0.1.x
Provider classes remain the default until ADR-0006 §12 cutover.
"""

from hal0.lemonade.client import LemonadeClient
from hal0.lemonade.errors import (
    LemonadeError,
    LemonadeHTTPError,
    LemonadeLoadError,
    LemonadeTimeoutError,
    LemonadeUnavailableError,
)

__all__ = [
    "LemonadeClient",
    "LemonadeError",
    "LemonadeHTTPError",
    "LemonadeLoadError",
    "LemonadeTimeoutError",
    "LemonadeUnavailableError",
]
