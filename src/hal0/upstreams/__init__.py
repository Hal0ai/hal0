"""hal0.upstreams — External and local routing targets.

An Upstream is one HTTP endpoint the Dispatcher can route requests to.
Two kinds:
  - "slot"   — a local hal0 inference slot (managed by SlotManager)
  - "remote" — an external provider (OpenRouter, Anthropic, OpenAI, custom)

UpstreamRegistry loads from /etc/hal0/upstreams.toml at startup and
supports dynamic registration for auto-discovered slots.

integrations.py holds the built-in provider catalog (_CATALOG) with
known base URLs, auth styles, and model lists.

Port targets: haloai lib/upstreams.py (737 lines), lib/integrations.py.
Adds: adaptive cold-boot timeout (PLAN.md §5 Tier 1).
See PLAN.md §3 and §5 Tier 1.

Key exports:
    Upstream         — frozen dataclass representing one routing target.
    UpstreamRegistry — runtime registry of all upstreams.
    ModelFilters     — per-upstream /v1/models advertising filters.
"""

from __future__ import annotations

from hal0.upstreams.filters import ModelFilters, apply_filters, is_advertised
from hal0.upstreams.registry import Upstream, UpstreamRegistry

__all__ = [
    "ModelFilters",
    "Upstream",
    "UpstreamRegistry",
    "apply_filters",
    "is_advertised",
]
