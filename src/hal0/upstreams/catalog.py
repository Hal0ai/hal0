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


@dataclass(frozen=True)
class NotEnabled:
    """A requested model a taking-part provider stores but has not enabled.

    Returned rather than raised: the router raises :attr:`error` only when no
    later step routes the request. ``canonical`` says whether the request
    named the provider (``provider/model``) or was a bare id.
    """

    error: ModelNotEnabled
    canonical: bool


def _not_enabled(provider: str, model: str, *, listed: bool = True) -> ModelNotEnabled:
    state = "is not enabled" if listed else "is not enabled, or is not offered,"
    return ModelNotEnabled(
        f"model {model!r} {state} on provider {provider!r}; enable it with "
        f"`hal0 upstream enable {provider} {model}` or on the provider's model list "
        "in the dashboard",
        details={"provider": provider, "model": model},
    )


def resolve_requested_model(
    requested: str, upstreams: Iterable[Upstream]
) -> Resolved | NotEnabled | None:
    """Map a requested model id to one curated provider, from stored rows only.

    Only curated, enabled, remote upstreams that are neither slot-backed nor
    agent-managed take part. In order:

    1. ``provider/model`` naming a taking-part provider with an enabled row
       for ``model`` resolves there.
    2. The whole string as a bare id, enabled on exactly one provider, resolves
       there; enabled on several raises :class:`ModelAmbiguous`.
    3. A ``provider/model`` from step 1 whose row is disabled or missing is a
       canonical :class:`NotEnabled`.
    4. A bare id stored disabled on a taking-part provider is a bare
       :class:`NotEnabled` naming the alphabetically first such provider.

    Anything else returns ``None``.
    """
    live = {u.name: u for u in upstreams if participates(u)}

    pending: NotEnabled | None = None
    ref = split_ref(requested)
    if ref is not None and ref[0] in live:
        provider, model = ref
        row = next((m for m in live[provider].models if m.id == model), None)
        if row is not None and row.enabled:
            return Resolved(live[provider], model)
        pending = NotEnabled(_not_enabled(provider, model, listed=row is not None), canonical=True)

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
    if pending is not None:
        return pending
    for name in sorted(live):
        if any(m.id == requested and not m.enabled for m in live[name].models):
            return NotEnabled(_not_enabled(name, requested), canonical=False)
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
                curated_model_ids(u, model_cache)
        return
    u = upstreams.get(name)
    if u is None:
        model_cache.pop(name, None)
    elif u.curated:
        curated_model_ids(u, model_cache)


def curated_model_ids(u: Upstream, model_cache: MutableMapping[str, list[str]]) -> list[str]:
    """Store and return a curated upstream's enabled ids as its cache entry.

    The one place a curated entry is written, so callers never fall back to the
    provider's network list (an empty enabled list is a real answer).
    """
    ids = list(enabled_ids(u))
    model_cache[u.name] = ids
    return ids
