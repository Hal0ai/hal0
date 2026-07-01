"""Request normalization for dispatcher-bound chat traffic (model resolution + thinking)."""

from hal0.normalize.messages import normalize_system_messages  # noqa: F401
from hal0.normalize.resolver import (  # noqa: F401
    DEFAULT_CHAINS,
    LiveSlotResolver,
    Resolution,
    SlotView,
    resolve_chain,
)
from hal0.normalize.thinking import apply_thinking_policy  # noqa: F401
