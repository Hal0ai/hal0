"""Request normalization for dispatcher-bound chat traffic (model resolution + thinking)."""

from hal0.normalize.resolver import (  # noqa: F401
    DEFAULT_CHAINS,
    VIRTUAL_ALIASES,
    LiveSlotResolver,
    Resolution,
    SlotView,
    resolve_chain,
)
from hal0.normalize.thinking import apply_thinking_policy  # noqa: F401
