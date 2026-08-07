"""Draft profile generation from a registered model or a HuggingFace repo.

Profile authoring today means hand-picking a seed profile and guessing
whether it fits a model. This module is the compute-only counterpart to
``POST /api/profiles/generate``: given a model source (a registered
``model_id`` or a HuggingFace ``hf_repo``), it classifies the model, fits
it to THIS host's hardware, clones the closest seed profile, and returns
a draft in the same portable-envelope shape :mod:`hal0.profiles.portable`
already produces — ready to hand straight to ``POST /api/profiles`` or
``POST /api/profiles/import`` unmodified.

Never writes to the catalog (:class:`hal0.profiles.ProfileCatalog` is only
used here for its read-only :meth:`~hal0.profiles.ProfileCatalog.resolve`).
Reuses rather than re-derives:

* :func:`hal0.model_meta.classify` — capability bucket (chat/embed/rerank/
  stt/tts/img), the same function :mod:`hal0.model_fit` uses for fit checks.
* :func:`hal0.install.profile_derive.derive_device` — the install wizard's
  (capability, hardware) → device heuristic.
* :func:`hal0.capabilities.profile_fit.profile_name_for_fit` — the picker/
  apply-time (capability, device) → seed profile name; falls back to
  :func:`hal0.install.profile_derive.derive_profile` when it has no opinion
  (mirrors how the install path itself falls back — see that module).
* :data:`hal0.hardware.recommend._MOE_ARCHITECTURES` — so a MoE chat model
  clones ``profile.moe`` instead of the flat ``profile.chat`` fallback.
* :mod:`hal0.upstreams.huggingface` — the shared HF transport (repo tree +
  metadata fetch, auth headers, error envelopes). This module only adds the
  ``?expand[]=gguf`` call for architecture/context-length, which the shared
  client does not fetch today.
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass, field
from typing import Any

import httpx

from hal0.capabilities.profile_fit import profile_name_for_fit
from hal0.config.loader import load_hardware_info
from hal0.config.schema import SEED_PROFILES, HardwareInfo, ProfileConfig
from hal0.errors import BadRequest
from hal0.hardware.recommend import _MOE_ARCHITECTURES
from hal0.install.profile_derive import derive_device, derive_profile
from hal0.model_meta import classify, model_is_mtp_eligible
from hal0.profiles import ProfileCatalog
from hal0.profiles.portable import export_envelope
from hal0.upstreams.huggingface import (
    _HF_MODELS_URL,
    _hf_headers,
    fetch_repo,
    normalise_repo_slug,
)

log = logging.getLogger(__name__)

_NAME_RE = re.compile(r"^[a-z0-9][a-z0-9_-]{0,31}$")
_GGUF_META_TIMEOUT_S = 6.0
_LLM_TIMEOUT_S = 20.0
_LLM_MAX_TOKENS = 64
_QUANT_TOKEN_RE = re.compile(
    r"(?i)\b(iq[0-9]_[a-z0-9]+|q[0-9]_[a-z0-9_]+|f16|f32|bf16|fp16|fp8|fp4)\b"
)
#: Quant tokens preferred as the "recommended" HF variant when several
#: exist — mirrors the curated catalogue's own "Q4_K_M usually, picked for
#: the size/quality sweet spot" convention (hal0.registry.curated).
_PREFERRED_QUANT_TOKENS: tuple[str, ...] = ("q4_k_m", "q4_k_s", "q5_k_m")


@dataclass(frozen=True, slots=True)
class LlmCallContext:
    """Where + how to reach the platform's utility LLM for the ``use_llm`` path.

    Mirrors the self-call pattern :func:`hal0.brain.chat._resolve_llm` uses
    to reach hal0-api's own ``/v1/chat/completions``: a base URL plus
    pre-resolved auth headers (forward the caller's inbound bearer, else the
    box service identity — see :func:`hal0.service_identity.service_auth_headers`).
    Building this is a route-layer concern (it needs the inbound ``Request``);
    this module only consumes it.
    """

    base_url: str = "http://127.0.0.1:8080"
    headers: dict[str, str] = field(default_factory=dict)
    #: Virtual model addressing the cheap helper slot (hal0.normalize.resolver),
    #: the same role hal0.agents.role_slots reserves for utility-tier work.
    model: str = "hal0/utility"


@dataclass(frozen=True, slots=True)
class GeneratedProfile:
    """Result of :func:`generate_draft_profile`."""

    #: Portable ``.hal0profile.json`` envelope (hal0.profiles.portable.export_envelope
    #: shape) — pass verbatim as the ``envelope`` of ``POST /api/profiles/import``.
    profile: dict[str, Any]
    warnings: tuple[str, ...] = ()
    sources: tuple[str, ...] = ()


@dataclass(frozen=True, slots=True)
class _ModelFacts:
    """Normalised facts about a model, regardless of where it came from."""

    display_name: str
    #: model_meta.classify() bucket: chat | embed | rerank | stt | tts | img.
    capability: str
    architecture: str | None
    quant: str
    context_length: int | None
    size_bytes: int
    mtp_eligible: bool
    tags: tuple[str, ...]
    #: id/repo string used for the "a3b in id" MoE substring fallback.
    id_for_moe: str
    #: Best-effort model-card excerpt fed to the use_llm summarizer.
    card_text: str


# ── naming ───────────────────────────────────────────────────────────────────


def _slugify(raw: str) -> str:
    """Best-effort kebab-case slug matching the profile name pattern.

    Mirrors the ``^[a-z0-9][a-z0-9_-]{0,31}$`` rule every profile-name site
    in this package enforces (hal0.profiles._PROFILE_NAME_RE,
    hal0.api.routes.profiles._PROFILE_NAME_RE) without importing either
    module's private constant — this is a fallback slugifier, not a
    validator, so it always produces a passing name rather than raising.
    """
    base = (raw or "").strip().lower().rsplit("/", 1)[-1]
    base = re.sub(r"[^a-z0-9_-]+", "-", base)
    base = re.sub(r"-{2,}", "-", base).strip("-")
    if not base:
        return "draft-profile"
    if not base[0].isalnum():
        base = f"m-{base}"
    base = base[:32].rstrip("-")
    return base or "draft-profile"


def _resolve_name(requested: str | None, fallback_source: str, warnings: list[str]) -> str:
    if requested and requested.strip():
        slug = _slugify(requested)
        if slug != requested.strip():
            warnings.append(
                f"requested name {requested!r} is not a valid profile name "
                f"(kebab-case, <=32 chars, leading alphanumeric) — sanitized to {slug!r}"
            )
        return slug
    return _slugify(fallback_source)


# ── model facts: registered model_id ────────────────────────────────────────


async def _facts_from_model_id(
    model_id: str,
    *,
    registry: Any,
    warnings: list[str],
    sources: list[str],
) -> _ModelFacts:
    from hal0.registry.store import ModelRegistry

    reg = registry if registry is not None else ModelRegistry()
    model = reg.get(model_id)  # raises ModelNotFound (404 model.not_found) — let it propagate
    sources.append(f"registry:{model.id}")

    capability = classify(model.id, capabilities=model.capabilities)

    context_length: int | None = None
    if isinstance(model.metadata, dict):
        raw_ctx = model.metadata.get("context_length")
        if isinstance(raw_ctx, int) and raw_ctx > 0:
            context_length = raw_ctx
    if context_length is None and model.defaults and model.defaults.context_size:
        context_length = model.defaults.context_size

    card_text = ""
    if model.hf_repo:
        # Best-effort enrichment only — the registry row is already the
        # authoritative source for architecture/quant/size, so a card-fetch
        # failure here degrades to a warning, never to a failed generate.
        try:
            fetched = await fetch_repo(model.hf_repo)
            card_text = str((fetched.get("metadata") or {}).get("readme_excerpt") or "")
            if card_text:
                sources.append(f"huggingface:{model.hf_repo}")
        except Exception as exc:
            warnings.append(f"could not fetch model card from {model.hf_repo!r}: {exc}")

    return _ModelFacts(
        display_name=model.name or model.id,
        capability=capability,
        architecture=model.architecture,
        quant=model.quant or "",
        context_length=context_length,
        size_bytes=model.size_bytes,
        mtp_eligible=model_is_mtp_eligible(model.model_dump()),
        tags=tuple(model.tags or ()),
        id_for_moe=model.id,
        card_text=card_text,
    )


# ── model facts: HuggingFace repo ───────────────────────────────────────────


def _pick_variant(variants: list[dict[str, Any]]) -> dict[str, Any] | None:
    """Pick the "recommended" variant out of an inspected HF repo's file list.

    Prefers a Q4_K_M-ish quant (the curated catalogue's own sweet spot);
    falls back to the size-sorted list's middle entry (``fetch_repo`` already
    sorts ascending by size) so an unfamiliar quant naming still lands on a
    reasonable middle-of-the-road pick rather than the smallest/largest.
    """
    real = [
        v for v in variants if not v.get("flm") and "mmproj" not in str(v.get("id", "")).lower()
    ]
    if not real:
        return variants[0] if variants else None
    for token in _PREFERRED_QUANT_TOKENS:
        for v in real:
            if token in str(v.get("id", "")).lower():
                return v
    return real[len(real) // 2]


async def _fetch_gguf_meta(repo: str) -> dict[str, Any] | None:
    """Best-effort HF ``?expand[]=gguf`` fetch for architecture/context length.

    Not every GGUF repo carries parsed GGUF metadata (HF only populates it
    when it can parse a GGUF header out of the repo), and a miss here must
    never fail the whole generate call — the filename-token quant fallback
    covers the gap. Reuses the shared HF transport's base URL + auth-header
    helper (:mod:`hal0.upstreams.huggingface`) so a configured ``HF_TOKEN``
    applies here too.
    """
    try:
        async with httpx.AsyncClient(timeout=_GGUF_META_TIMEOUT_S, headers=_hf_headers()) as http:
            resp = await http.get(f"{_HF_MODELS_URL}/{repo}", params={"expand[]": "gguf"})
    except httpx.HTTPError:
        return None
    if resp.status_code != 200:
        return None
    try:
        payload = resp.json()
    except ValueError:
        return None
    gguf = payload.get("gguf") if isinstance(payload, dict) else None
    return gguf if isinstance(gguf, dict) else None


async def _facts_from_hf_repo(
    hf_repo: str,
    *,
    warnings: list[str],
    sources: list[str],
) -> tuple[_ModelFacts, str]:
    repo = normalise_repo_slug(hf_repo)
    if "/" not in repo:
        raise BadRequest(
            f"'{hf_repo}' is not a valid org/name HF repo coordinate",
            code="hf.bad_request",
            details={"input": hf_repo},
        )
    # HF unreachable / 404 / 5xx surface as the typed hf.* envelopes fetch_repo
    # already raises (HFUpstreamError -> 502, NotFound -> 404) — propagate as-is.
    fetched = await fetch_repo(repo)
    sources.append(f"huggingface:{repo}")

    tags = tuple(str(t) for t in (fetched.get("tags") or []))
    variants = fetched.get("variants") or []
    variant = _pick_variant(variants)

    capability = classify(repo)
    if capability == "chat" and variant:
        # The repo slug alone is often generic (org/quant-repo); the chosen
        # variant's filename frequently carries the real signal (e.g. a
        # "bge-reranker" file inside a genericly-named quant repo).
        alt = classify(str(variant.get("id", "")))
        if alt != "chat":
            capability = alt

    gguf_meta = await _fetch_gguf_meta(repo)
    architecture: str | None = None
    context_length: int | None = None
    if gguf_meta:
        raw_arch = gguf_meta.get("architecture")
        architecture = str(raw_arch) if raw_arch else None
        raw_ctx = gguf_meta.get("context_length")
        if isinstance(raw_ctx, int) and raw_ctx > 0:
            context_length = raw_ctx
        sources.append(f"huggingface:{repo}#gguf")
    else:
        warnings.append(
            f"HuggingFace did not report parsed GGUF metadata for {repo!r} — "
            "architecture/context length are unavailable (heuristics only)"
        )

    quant = ""
    size_bytes = 0
    if variant:
        size_bytes = int(variant.get("size_bytes") or 0)
        m = _QUANT_TOKEN_RE.search(str(variant.get("id", "")))
        if m:
            quant = m.group(1).upper()

    metadata = fetched.get("metadata") or {}
    card_text = str(metadata.get("readme_excerpt") or "")
    license_label = str(metadata.get("license") or "")
    if license_label:
        card_text = f"License: {license_label}\n{card_text}".strip()

    facts = _ModelFacts(
        display_name=repo,
        capability=capability,
        architecture=architecture,
        quant=quant,
        context_length=context_length,
        size_bytes=size_bytes,
        mtp_eligible=model_is_mtp_eligible({"tags": list(tags)}),
        tags=tags,
        id_for_moe=repo,
        card_text=card_text,
    )
    return facts, repo


# ── device + seed-profile fit ────────────────────────────────────────────────


def _looks_moe(facts: _ModelFacts) -> bool:
    """True when the model's own facts say MoE, mirroring hardware.recommend.

    Same precedence as :func:`hal0.hardware.recommend._resolve_primary_ctx`'s
    is_moe check: a backfilled ``architecture`` wins; otherwise fall back to
    the ``mtp``/``a3b`` tag-or-id heuristic every curated row without an
    ``architecture`` still relies on.
    """
    if facts.architecture and facts.architecture.strip().lower() in _MOE_ARCHITECTURES:
        return True
    lowered = {t.strip().lower() for t in facts.tags}
    if "moe" in lowered or "a3b" in lowered or "mtp" in lowered:
        return True
    return "a3b" in facts.id_for_moe.lower()


def _fit_seed_profile(facts: _ModelFacts, hw: HardwareInfo) -> tuple[str, str, list[str]]:
    """Return ``(device, seed_profile_name, warnings)`` for this host."""
    warnings: list[str] = []
    device = derive_device(facts.capability, hw, npu_opt_in=False)
    if device is None:
        # e.g. stt with no NPU opt-in, or an NPU-only capability on a
        # non-NPU box. A draft still needs a concrete device to clone a
        # seed's flags from; cpu is always installable.
        device = "cpu"
        warnings.append(
            f"no hardware lane resolved for capability={facts.capability!r} on this host "
            "(NPU opt-in required, or hardware.json has not been probed yet) — "
            "defaulted the draft's device to 'cpu'"
        )

    # profile_name_for_fit speaks "image", model_meta.classify() speaks "img".
    capability_for_fit = "image" if facts.capability == "img" else facts.capability
    seed_name = profile_name_for_fit(capability_for_fit, device)
    if not seed_name:
        # profile_name_for_fit has no opinion (e.g. an unrecognised
        # capability/device pairing) — fall back to the install-time
        # heuristic, which always returns a name.
        seed_name = derive_profile(facts.capability, device)

    if seed_name == "chat" and _looks_moe(facts):
        # "chat" is the flat, model-agnostic fallback; "moe" is the workload
        # profile tuned for hybrid-KV MoE models (f16 KV, no context shift).
        seed_name = "moe"

    if seed_name not in SEED_PROFILES:
        # Defensive only — profile_name_for_fit/derive_profile are expected
        # to always name a live seed; guards against future seed-catalog
        # drift rather than a case reachable today.
        warnings.append(
            f"derived seed profile {seed_name!r} is not in the current seed catalog "
            "— falling back to 'chat'"
        )
        seed_name = "chat"
    return device, seed_name, warnings


# ── intent headline: heuristic + use_llm summarizer ─────────────────────────


def _build_intent(facts: _ModelFacts, capability: str, device: str) -> str:
    bits = [facts.display_name]
    if facts.architecture:
        bits.append(facts.architecture)
    if facts.context_length:
        ctx = facts.context_length
        bits.append(f"{ctx // 1024}K ctx" if ctx >= 1024 else f"{ctx} ctx")
    bits.append(f"{capability} · {device}")
    return " · ".join(bits)[:160]


async def _llm_headline(facts: _ModelFacts, llm: LlmCallContext) -> tuple[str | None, str | None]:
    """Ask the utility LLM for a one-line profile headline.

    Returns ``(headline, None)`` on success or ``(None, reason)`` on any
    failure — mirrors the transport/route-status/shape handling
    :func:`hal0.brain.chat._resolve_llm`'s ``_primary_completion`` closure
    uses for hal0-api's own ``/v1/chat/completions`` self-call, minus the
    tool-calling plumbing this summarizer doesn't need.
    """
    if not facts.card_text and not facts.architecture:
        return None, "no model-card text or architecture available to summarize"

    lines = [f"Model: {facts.display_name}", f"Capability: {facts.capability}"]
    if facts.architecture:
        lines.append(f"Architecture: {facts.architecture}")
    if facts.quant:
        lines.append(f"Quant: {facts.quant}")
    if facts.context_length:
        lines.append(f"Context length: {facts.context_length}")
    if facts.card_text:
        lines.append(f"Model card excerpt:\n{facts.card_text}")
    lines.append(
        "\nWrite ONE short line (<=100 characters) describing this model's best use "
        "case, suitable as a runtime profile card headline. Plain text, no quotes, "
        "no markdown."
    )
    body = {
        "model": llm.model,
        "messages": [
            {
                "role": "system",
                "content": (
                    "You write terse, factual one-line profile headlines for an LLM "
                    "inference platform. Output only the line, nothing else."
                ),
            },
            {"role": "user", "content": "\n".join(lines)},
        ],
        "max_tokens": _LLM_MAX_TOKENS,
        "temperature": 0.2,
    }
    try:
        async with httpx.AsyncClient(timeout=_LLM_TIMEOUT_S) as http:
            resp = await http.post(
                f"{llm.base_url.rstrip('/')}/v1/chat/completions",
                json=body,
                headers=llm.headers or None,
            )
    except httpx.HTTPError as exc:
        return None, f"utility LLM transport failure: {exc}"
    if resp.status_code == 404:
        return None, "utility LLM slot unavailable (no route to hal0/utility)"
    if not (200 <= resp.status_code < 300):
        return None, f"utility LLM returned HTTP {resp.status_code}"
    try:
        data = resp.json()
    except ValueError:
        return None, "utility LLM returned non-JSON"
    if isinstance(data, dict) and data.get("error"):
        return None, f"utility LLM error: {data['error']}"
    try:
        text = data["choices"][0]["message"]["content"]
    except (KeyError, IndexError, TypeError):
        return None, "utility LLM returned an unexpected response shape"
    line = (text or "").strip().splitlines()[0].strip().strip('"') if text else ""
    if not line:
        return None, "utility LLM returned an empty completion"
    return line[:160], None


# ── entry point ──────────────────────────────────────────────────────────────


async def generate_draft_profile(
    *,
    model_id: str | None = None,
    hf_repo: str | None = None,
    name: str | None = None,
    use_llm: bool = False,
    exported_at: str,
    llm: LlmCallContext | None = None,
    hw: HardwareInfo | None = None,
    registry: Any = None,
) -> GeneratedProfile:
    """Generate a draft profile (portable envelope) — compute-only, no catalog write.

    Exactly one of ``model_id`` / ``hf_repo`` must be given. ``exported_at``
    is caller-stamped (mirrors :func:`hal0.profiles.portable.export_envelope`'s
    own "no clock here" design — pass ``datetime.now(UTC).isoformat()`` from
    the route). ``llm`` is required for ``use_llm=True`` to have any effect;
    its absence degrades to heuristics with a warning rather than raising.
    ``hw``/``registry`` are test/CLI injection points — production omits both
    and gets the cached ``hardware.json`` / the default on-disk registry.
    """
    has_model = bool(model_id and model_id.strip())
    has_repo = bool(hf_repo and hf_repo.strip())
    if has_model == has_repo:
        raise BadRequest(
            "exactly one of 'model_id' or 'hf_repo' is required",
            code="profiles.generate_bad_source",
            details={"model_id": model_id, "hf_repo": hf_repo},
        )

    warnings: list[str] = []
    sources: list[str] = []

    if has_model:
        assert model_id is not None
        facts = await _facts_from_model_id(
            model_id, registry=registry, warnings=warnings, sources=sources
        )
        fallback_name_source = model_id
    else:
        assert hf_repo is not None
        facts, repo = await _facts_from_hf_repo(hf_repo, warnings=warnings, sources=sources)
        fallback_name_source = repo

    resolved_hw = hw if hw is not None else load_hardware_info()
    device, seed_name, fit_warnings = _fit_seed_profile(facts, resolved_hw)
    warnings.extend(fit_warnings)

    seed = ProfileCatalog().resolve(seed_name)

    intent = _build_intent(facts, facts.capability, device)
    if use_llm:
        if llm is None:
            warnings.append(
                "use_llm requested but no LLM call context was provided — used heuristics only"
            )
        else:
            headline, error = await _llm_headline(facts, llm)
            if headline:
                intent = headline
                sources.append(f"llm:{llm.model}")
            else:
                warnings.append(
                    f"LLM summarization unavailable ({error}) — used heuristic intent instead"
                )

    draft = ProfileConfig(
        flags=seed.flags,
        mtp=bool(seed.mtp or facts.mtp_eligible),
        device_class=seed.device_class,
        backend=seed.backend,
        cloned_from=seed_name,
        intent=intent,
        quant=facts.quant or seed.quant or "",
    )

    profile_name = _resolve_name(name, fallback_name_source, warnings)
    envelope = export_envelope(profile_name, draft, exported_at=exported_at)

    return GeneratedProfile(profile=envelope, warnings=tuple(warnings), sources=tuple(sources))


__all__ = ["GeneratedProfile", "LlmCallContext", "generate_draft_profile"]
