"""Per-model rows stored on an upstream, plus pure helpers over them.

No I/O and no third-party imports. ``Upstream`` is referenced for annotations
only, because ``registry`` imports :class:`UpstreamModel` from here at runtime.
"""

from __future__ import annotations

from collections.abc import Iterable, MutableMapping
from dataclasses import dataclass
from typing import TYPE_CHECKING, Protocol

from hal0.errors import Hal0Error

if TYPE_CHECKING:
    from hal0.upstreams.registry import Upstream


class UpstreamLookup(Protocol):
    """The slice of ``UpstreamRegistry`` that :func:`reseed_curated_cache` reads."""

    def list(self) -> list[Upstream]: ...

    def get(self, name: str) -> Upstream | None: ...


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


class ModelNotEnabled(Hal0Error):
    """404 - the model is unknown or switched off on the provider."""

    code = "upstream.model_not_enabled"
    status = 404


class ModelAmbiguous(Hal0Error):
    """400 - a bare model id is enabled on more than one provider."""

    code = "upstream.model_ambiguous"
    status = 400


@dataclass(frozen=True)
class Resolved:
    """A requested model mapped to one provider."""

    upstream: Upstream
    model_id: str
    """The id to send to the provider."""


def participates(u: Upstream) -> bool:
    """Does ``u`` take part in catalog routing (curated, enabled, plain remote)?"""
    return u.curated and u.enabled and u.kind == "remote" and not u.slot_name and not u.managed_by


def _not_enabled(provider: str, model: str) -> ModelNotEnabled:
    return ModelNotEnabled(
        f"model {model!r} is not enabled on provider {provider!r}; "
        "enable it on the provider's model list",
        details={"provider": provider, "model": model},
    )


def resolve_requested_model(requested: str, upstreams: Iterable[Upstream]) -> Resolved | None:
    """Map a requested model id to one curated provider.

    Only curated, enabled, remote upstreams that are neither slot-backed nor
    agent-managed take part. A ``provider/model`` reference naming a taking-part
    provider is resolved there (or raises :class:`ModelNotEnabled`). Anything
    else is a bare id, matched against every provider's enabled models. Returns
    ``None`` when nothing claims the id.
    """
    live = {u.name: u for u in upstreams if participates(u)}

    ref = split_ref(requested)
    if ref is not None and ref[0] in live:
        provider, model = ref
        for row in live[provider].models:
            if row.id == model:
                if row.enabled:
                    return Resolved(live[provider], model)
                break
        raise _not_enabled(provider, model)

    enabled = [u for u in live.values() if requested in enabled_ids(u)]
    if len(enabled) == 1:
        return Resolved(enabled[0], requested)
    if enabled:
        candidates = sorted(canonical_id(u.name, requested) for u in enabled)
        raise ModelAmbiguous(
            f"model {requested!r} is enabled on several providers; "
            f"use one of: {', '.join(candidates)}",
            details={"model": requested, "candidates": candidates},
        )
    for name in sorted(live):
        if any(m.id == requested and not m.enabled for m in live[name].models):
            raise _not_enabled(name, requested)
    return None


def reseed_curated_cache(
    upstreams: UpstreamLookup,
    model_cache: MutableMapping[str, list[str]],
    name: str | None = None,
) -> None:
    """Make ``model_cache`` agree with the enabled rows of curated upstreams.

    A curated upstream's cache entry is its enabled ids and never comes from the
    network. With ``name`` only that upstream is touched; otherwise every curated
    upstream is. Call it after any change to an upstream or its rows. A named
    upstream that is gone loses its entry. One that is merely not curated is left
    alone: its cache is the network's, and a caller that has just switched an
    upstream to uncurated must drop the stale entry itself.
    """
    if name is None:
        for u in upstreams.list():
            if u.curated:
                model_cache[u.name] = list(enabled_ids(u))
        return
    u = upstreams.get(name)
    if u is None:
        model_cache.pop(name, None)
    elif u.curated:
        model_cache[name] = list(enabled_ids(u))
