"""Request normalization for lemond-bound chat traffic (model resolution + thinking)."""

from hal0.normalize.resolver import (  # noqa: F401
    DEFAULT_CHAINS,
    VIRTUAL_ALIASES,
    LiveSlotResolver,
    Resolution,
    SlotView,
    is_npu_or_flm,
    resolve_chain,
)
