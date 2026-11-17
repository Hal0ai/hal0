"""Per-model rows stored on an upstream, plus pure helpers over them.

No I/O and no third-party imports. ``Upstream`` is referenced for annotations
only, because ``registry`` imports :class:`UpstreamModel` from here at runtime.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from hal0.upstreams.registry import Upstream

MODEL_TYPES: frozenset[str] = frozenset({"chat", "embed", "rerank", "tts", "stt", "image"})
"""Model kinds an upstream row may declare."""


@dataclass(frozen=True)
class UpstreamModel:
    """One model an operator has listed under a provider."""

    id: str
    enabled: bool = True
    type: str = "chat"
    context: int | None = None
    price_in: float | None = None
    price_out: float | None = None
    price_unit: str = "Mtok"


def canonical_id(provider: str, model_id: str) -> str:
    """Return the ``provider/model`` reference for a provider's model."""
    return f"{provider}/{model_id}"


def split_ref(ref: str) -> tuple[str, str] | None:
    """Split ``provider/model`` on the first ``/`` only.

    Returns ``None`` when there is no ``/`` or either side is empty.
    """
    provider, sep, model_id = ref.partition("/")
    if not sep or not provider or not model_id:
        return None
    return provider, model_id


def enabled_ids(u: Upstream) -> tuple[str, ...]:
    """Enabled model ids of a curated upstream; ``()`` for an uncurated one."""
    if not u.curated:
        return ()
    return tuple(m.id for m in u.models if m.enabled)
