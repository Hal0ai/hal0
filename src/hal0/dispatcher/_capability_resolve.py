"""Capability/path routing heuristics — dispatcher resolution Step 4.

Extracted from :mod:`hal0.dispatcher.router` (finding DR-12) so the router
module no longer mixes the last-resort path/name heuristics with the core
registry/passthrough resolution logic.

This is a **leaf** module: it depends only on :mod:`hal0.errors`,
:mod:`hal0.slots.manager` (``SLOT_ALIASES``) and
:mod:`hal0.upstreams.registry` — never on ``router`` — so ``router`` can
``from hal0.dispatcher._capability_resolve import resolve_by_capability``
back without creating an import cycle.  ``router`` re-exports
:func:`resolve_by_capability` and :class:`LegacyResolutionFailed` verbatim
(and keeps them in ``__all__``), so every existing call site and importer
keeps working unchanged.  Routing behaviour is preserved verbatim — this is
a lift-and-shift, not a logic change.
"""

from __future__ import annotations

from typing import Any

from hal0.errors import Hal0Error
from hal0.slots.manager import SLOT_ALIASES
from hal0.upstreams.registry import Upstream, UpstreamRegistry

# Path default used only for routing — never written back into the body.
# Mirrors haloai lib/dispatcher.py:_DEFAULT_MODEL.
# ADR-0023: the default/fallback LLM anchor is the `agent` slot (was `chat`).
# Kept here (a leaf module) and imported by ``router`` so the two resolution
# steps that reference it share a single source without an import cycle.
_DEFAULT_MODEL = "agent"


# ── Capability/path routing constants ─────────────────────────────────────────
#
# Path + model-name heuristics that route a request to a slot when the registry
# and warm caches have nothing to say (the last-resort dispatch step).  Absorbed
# from the retired ``dispatcher.proxy`` module (ported from haloai
# ``lib/proxy.py``); operator muscle memory ("slot named coding-1m" addressing)
# and the four capability endpoints (embed/rerank/tts/image — whose model names
# the registry doesn't bind) depend on these heuristics.

# Path fragments that pin a request to the embed slot regardless of model.
# Mirrors haloai lib/proxy.py:51-58 (embeddings only; rerank split to its own
# dedicated slot in Phase C — see _RERANK_PATHS below).
_EMBED_PATHS = ("/embeddings",)

# Path fragments that pin a request to the dedicated rerank slot (vulkan
# llama-server with --reranking, port 8083, added Phase C task C5).
# Both /rerankings (hal0's public OpenAI-compat route) and /rerank (llama-
# server's native endpoint shape) route here; the dispatcher applies
# _UPSTREAM_PATH_REWRITES to translate /v1/rerankings → /v1/rerank on the
# outgoing upstream request (llama-server serves POST /rerank natively, not
# /rerankings).
_RERANK_PATHS = ("/rerankings", "/rerank")

# Path fragments that pin a request to the TTS slot (kokoro container).
# Model-id matching is unreliable — the kokoro container advertises "kokoro"
# while clients send "kokoro-v1", "tts", etc. — so we route by path instead.
# Only /audio/speech (synthesis); /audio/transcriptions is STT, not TTS.
_TTS_PATHS = ("/audio/speech",)

# Path fragments that pin a request to the image-gen slot (ComfyUI). The
# OpenAI shape is `/v1/images/generations` — when that hits this step we don't
# want it routed to the chat slot.
_IMAGE_PATHS = ("/images/generations", "/images/edits", "/images/variations")

# Substrings in the model name that pin to known slot roles.  Order matters:
# the ":" (FLM tag-style id) check runs before the bare-name substring checks
# so that "qwen3.5:embed" still routes to the NPU rather than to embed.
_EMBED_NAME_HINTS = ("embed", "rerank")

# Model id prefixes that pin to the image-gen slot. Curated catalogue uses
# these prefixes (sdxl-turbo, sd-1.5-..., flux-*). Anything matching
# these in the bare-model lookup goes to the `img` slot before slot-name
# resolution kicks in.
_IMAGE_NAME_PREFIXES = ("sdxl", "sd-1.5", "sd15", "flux")


# ── Typed error ───────────────────────────────────────────────────────────────


class LegacyResolutionFailed(Hal0Error):
    """Raised when the capability/path heuristics find no slot to serve a request.

    Carries a ``dispatch.legacy_unresolved`` code so the structured error
    envelope distinguishes "nothing in registry AND nothing in capability
    routing" from "registry binding pointed at an unknown upstream."  The
    code string is part of the error contract (clients/dashboard key on it),
    so it is preserved verbatim from the retired ``dispatcher.proxy`` module.

    ``dispatch()`` wraps this into a client-facing ``NoRouteFound``
    (404) — callers never see ``dispatch.legacy_unresolved`` directly, only
    via ``details.legacy_error``.
    """

    code = "dispatch.legacy_unresolved"
    status = 404


def resolve_by_capability(  # TIER1
    path: str,
    body: dict[str, Any] | None,
    upstreams: UpstreamRegistry,
) -> Upstream:
    """Resolve a request to a slot Upstream using path+name heuristics.

    The last-resort dispatch step (Step 4): used when the registry and warm
    caches have nothing to say.  Absorbed verbatim from the retired
    ``dispatcher.proxy.resolve_slot`` (haloai ``lib/proxy.py``); returns a
    typed :class:`Upstream` (or raises a typed error) instead of the old
    ``(slot_name, port)`` tuple.

    Resolution rules (in order):
      1. ``/embeddings`` in path → ``embed`` slot.
      2. ``/rerankings`` or ``/rerank`` in path → ``rerank`` slot (Phase C;
         outgoing path is rewritten to ``/v1/rerank`` by the dispatcher —
         llama-server's native reranking endpoint is ``POST /rerank``, not
         ``/rerankings``).
      3. ``/audio/speech`` in path → ``tts`` slot (kokoro; model-id unreliable).
      4. ``/images/...`` in path → ``img`` slot (ComfyUI).

    Path-pinned candidates (rules 1-4, plus the rule-6 model-prefix pin)
    accept either a local ``kind="slot"`` upstream or a container-backed
    ``kind="remote"`` upstream whose ``slot_name`` matches the candidate
    (container slots register as remotes via
    ``SlotManager._register_container_upstream``, #656).  All other rules
    require ``kind="slot"``.
      5. Model id contains ``:`` (FLM tag-style) → ``npu`` slot.
      6. Model id starts with ``sdxl``/``sd-1.5``/``sd15``/``flux`` → ``img`` slot.
      7. Model id contains ``embed`` or ``rerank`` substring → ``embed`` slot.
      8. Model id exactly matches a registered slot upstream name (other
         than the default ``agent`` anchor) → that slot.  Back-compat alias
         (``agent-hermes`` → ``agent``) is resolved first.
      9. Fallback → ``agent`` slot (ADR-0023 default anchor).

    Args:
        path:       The original request path (e.g. "/v1/chat/completions").
        body:       Parsed JSON body dict (may be None for GETs).
        upstreams:  Registry to resolve slot names against.

    Returns:
        An :class:`Upstream` representing the slot to forward to.

    Raises:
        LegacyResolutionFailed: If the heuristics select a slot name but no
            matching slot Upstream is registered.  Carries a
            ``dispatch.legacy_unresolved`` code via the typed Hal0Error envelope.
    """
    candidate: str | None = None
    # Path-pinned candidates ("route purely by path") may also resolve to a
    # container-backed kind="remote" upstream for that slot — container slots
    # register via SlotManager._register_container_upstream as kind="remote"
    # with slot_name set (#656), and a registered container remote for a
    # path-pinned slot is exactly the right target.  Model-name rules keep
    # the strict kind=="slot" gate.
    path_pinned = False

    # Rule 1 — path-based pin (embeddings → embed slot).
    if any(frag in path for frag in _EMBED_PATHS):
        candidate = "embed"
        path_pinned = True
    # Rule 2 — rerank path pin (/rerankings or /rerank → rerank slot, Phase C).
    # The public route is /v1/rerankings; the upstream rewrite
    # (/v1/rerankings → /v1/rerank) is applied by the dispatcher before
    # forwarding (llama-server's native endpoint is POST /rerank).
    elif any(frag in path for frag in _RERANK_PATHS):
        candidate = "rerank"
        path_pinned = True
    # Rule 3 — TTS path pin (/audio/speech → tts slot).
    # Model-id matching is unreliable for kokoro (server advertises "kokoro",
    # clients send "kokoro-v1"/"tts"/etc.) so we route purely by path.
    elif any(frag in path for frag in _TTS_PATHS):
        candidate = "tts"
        path_pinned = True
    # Rule 4 — image-generation path pins to the img slot.  img is a
    # container slot post-Phase-D (ComfyUI via podman, registers as a
    # kind="remote" upstream) — same container-remote acceptance as the
    # embed/tts/rerank path pins.
    elif any(frag in path for frag in _IMAGE_PATHS):
        candidate = "img"
        path_pinned = True
    elif body:
        model = body.get("model", "")
        if isinstance(model, str) and model:
            m = model.lower()
            # Rule 5 — FLM tag format "name:tag" routes to NPU.
            if ":" in model:
                candidate = "flm"
            # Rule 6 — image-gen model id prefix pin (sdxl-/sd-1.5-/flux-).
            # path_pinned here means "deterministically pinned": the curated
            # sdxl-/sd-1.5-/flux- prefixes are exact catalogue prefixes, the
            # same trust level as a path pin — so the container-backed img
            # remote must qualify here too (Phase D).
            elif any(m.startswith(prefix) for prefix in _IMAGE_NAME_PREFIXES):
                candidate = "img"
                path_pinned = True
            # Rule 7 — name-substring pin (embed/rerank → embed slot).
            elif any(hint in m for hint in _EMBED_NAME_HINTS):
                candidate = "embed"
            else:
                # Rule 8 — explicit slot-name addressing.
                # Resolve back-compat aliases (agent-hermes→agent) before the
                # upstream lookup so old callers still land correctly.
                m_resolved = SLOT_ALIASES.get(m, m)
                slot_match = upstreams.get(m_resolved)
                # ADR-0023: the anchor (`agent`) is reached via the rule-9 default,
                # not explicit name matching here — guard against double-routing.
                if (
                    slot_match is not None
                    and slot_match.kind == "slot"
                    and m_resolved != _DEFAULT_MODEL
                ):
                    candidate = m_resolved

    # Rule 9 — fallback default slot (ADR-0023: the `agent` anchor).
    if candidate is None:
        candidate = _DEFAULT_MODEL

    upstream = upstreams.get(candidate)
    # Acceptance: a local slot upstream always qualifies.  For PATH-pinned
    # candidates only, a container-backed remote (kind="remote" with
    # slot_name == candidate — how Step 0 preemption identifies container
    # slots) qualifies too: kokoro's tts container registers as a remote, so
    # the old kind=="slot"-only gate sent /audio/speech to NoRouteFound and
    # a dead legacy tts slot.  Genuine external remotes (slot_name=None)
    # are still rejected.
    acceptable = upstream is not None and (
        upstream.kind == "slot"
        or (path_pinned and upstream.kind == "remote" and upstream.slot_name == candidate)
    )
    if upstream is None or not acceptable:
        raise LegacyResolutionFailed(
            f"capability routing selected slot {candidate!r} but no matching slot upstream is registered",
            details={"slot": candidate, "path": path},
        )
    return upstream


__all__ = [
    "LegacyResolutionFailed",
    "resolve_by_capability",
]
