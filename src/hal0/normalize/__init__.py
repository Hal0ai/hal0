"""Request normalization for lemond-bound chat traffic (model resolution + thinking)."""

from hal0.normalize.resolver import (  # noqa: F401
    SlotView,
    Resolution,
    DEFAULT_CHAINS,
    VIRTUAL_ALIASES,
    is_npu_or_flm,
    resolve_chain,
    LiveSlotResolver,
)
