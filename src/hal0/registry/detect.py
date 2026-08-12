"""Model detection — derive backends + capabilities from a file on disk.

Pure inspection: no registry mutation, no network. The output is a
:class:`DetectionResult` that callers (the scan endpoint, the single-file
register path) merge into a :class:`hal0.registry.model.Model` before
persisting.

Detection strategy, cheapest first:

1. ``.gguf`` files → :func:`hal0.registry.gguf_header.read_gguf_header`
   to pull arch + context_length + pooling_type + tags + attention.causal.
   The four GGUF backends are seeded: ``vulkan``, ``rocm``, ``cuda``,
   ``cpu``. Capability is derived, cheapest/strongest signal first:

   a. ``pooling_type`` present → ``rerank`` (RANK=4), ``embed``
      (MEAN/CLS/LAST, i.e. any other non-zero value), else ``chat``.
      ``confidence='high'`` — read directly off the header.
   b. ``pooling_type`` absent but ``general.tags`` carries an
      unambiguous rerank/embed token (some converters drop the pooling
      key but keep the tags) → same, ``confidence='high'``.
   c. Neither present → fall back to the filename token table (also
      used by the install-time auto-scan; a HF hub-cache symlink's own
      name, not the resolved sha-blob's). When the filename carries a
      rerank/embed token, that token alone decides the outcome — a
      *guess*, not a header read, even though the GGUF header itself
      parsed fine — ``confidence='medium'`` (#1838: this used to report
      ``'high'`` for a filename-only guess). When the filename carries
      no signal either:
        - ``<arch>.attention.causal`` explicitly ``False`` contradicts
          the ``chat`` default (it rules out a causal chat model) →
          still ``chat`` (we don't know embed vs rerank), but
          ``confidence='medium'``.
        - otherwise the bare ``chat`` default applies and stays
          ``confidence='high'`` — that's the expected, correct answer
          for the overwhelming majority of causal chat ggufs, which
          don't set ``pooling_type`` at all.
   Note: an explicit ``pooling_type=0`` (NONE, the causal-chat value) is
   authoritative and skips (b)/(c) entirely — a stray tag or filename
   token can't override a header that already said "chat".
2. Non-GGUF: filename heuristic only.  Keywords cover the providers we
   currently ship:

   * ``embed``, ``bge``, ``e5``, ``nomic`` → ``capabilities=['embed']``
   * ``whisper``, ``moonshine`` → ``capabilities=['asr']`` (backend
     ``moonshine`` only if name contains ``moonshine``)
   * ``kokoro`` → ``capabilities=['tts']``, backend ``kokoro``
   * fallback for ``.gguf`` w/ unreadable header → ``capabilities=['chat']``

   Filename-only detection always returns ``confidence='low'``.
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Literal

from hal0.model_meta import capability_from_filename
from hal0.registry.gguf_header import read_gguf_header

log = logging.getLogger(__name__)

Confidence = Literal["high", "medium", "low"]

# Backends llama-server can target for any GGUF file. The slot config
# picks one based on hardware probe; detection just lists what's *compatible*.
_GGUF_BACKENDS: list[str] = ["vulkan", "rocm", "cuda", "cpu"]


# ── quantisation extraction (WS-13) ────────────────────────────────────────
#
# Two sources, header-first:
#   1. GGUF ``general.file_type`` (llama.cpp LLAMA_FTYPE enum) — authoritative
#      when the header is readable.
#   2. Filename token — ``Q4_K_M`` / ``IQ2_XS`` / ``f16`` / ``bf16`` / ``f32``
#      style markers, boundary-anchored so ``qwen3`` or ``embF16`` never
#      false-match.
#
# The result is a canonical UPPERCASE label ("Q4_K_M", "IQ2_XS", "F16") or
# ``None`` when unknown. Stored on ``Model.quant`` at registration and
# lazily backfilled at serialisation time for pre-existing registries.

_QUANT_FILENAME_RE = re.compile(
    r"(?<![a-z0-9])(i?q\d[_a-z0-9]*|bf16|f16|f32)(?![a-z0-9])",
    re.IGNORECASE,
)

# llama.cpp LLAMA_FTYPE → label (llama.h; removed/legacy codes omitted).
# The GUESSED flag (1024) is masked off before lookup.
_LLAMA_FTYPE_GUESSED = 1024
_FILE_TYPE_TO_QUANT: dict[int, str] = {
    0: "F32",
    1: "F16",
    2: "Q4_0",
    3: "Q4_1",
    7: "Q8_0",
    8: "Q5_0",
    9: "Q5_1",
    10: "Q2_K",
    11: "Q3_K_S",
    12: "Q3_K_M",
    13: "Q3_K_L",
    14: "Q4_K_S",
    15: "Q4_K_M",
    16: "Q5_K_S",
    17: "Q5_K_M",
    18: "Q6_K",
    19: "IQ2_XXS",
    20: "IQ2_XS",
    21: "Q2_K_S",
    22: "IQ3_XS",
    23: "IQ3_XXS",
    24: "IQ1_S",
    25: "IQ4_NL",
    26: "IQ3_S",
    27: "IQ3_M",
    28: "IQ2_S",
    29: "IQ2_M",
    30: "IQ4_XS",
    31: "IQ1_M",
    32: "BF16",
    36: "TQ1_0",
    37: "TQ2_0",
    38: "MXFP4",
}


def quant_from_filename(name: str) -> str | None:
    """Quant label from a filename token, or ``None`` when nothing matches.

    Matches are boundary-anchored (no alphanumeric on either side) so
    incidental substrings (``qwen3``, ``embF16``, ``headQ6``) do not
    false-positive. The matched token is normalised to UPPERCASE.
    """
    m = _QUANT_FILENAME_RE.search(name or "")
    return m.group(1).upper() if m else None


# ── ROCmFPX family (ciru-ai/ROCmFPX fork of llama.cpp) ─────────────────────
#
# ROCmFPX ships custom AMD GGUF *weight* formats — not stock Q4, MXFP4, or
# NVFP4 — with their own kernels and a custom ``general.file_type`` code that
# is NOT an upstream LLAMA_FTYPE value. So the header maps to ``None`` via
# quant_from_file_type(), and the standard Q<n>_K / F16 filename regex above
# misses their ``ROCmFP4`` / ``ROCmFPX`` tokens. We resolve them to the
# canonical *family* label from the filename's quant-preset or family token.
#
#   Family     GGUF preset(s)                     Role
#   ROCmFP3    Q3_0_ROCMFPX                        smallest experimental ROCmFPX weight format
#   ROCmFP4    Q4_0_ROCMFP4, Q4_0_ROCMFP4_FAST    promoted 4-bit ROCm family baseline
#   ROCmFP6    Q6_0_ROCMFPX                        middle quality/size ROCmFPX weight format
#   ROCmFP8    Q8_0_ROCMFPX                        high-quality ROCmFPX reference format
#
# Explicit presets are checked most-specific-first (``…_FAST`` before its base)
# and win over the bare umbrella name; ``ROCmFPX`` with no family digit stays
# the umbrella label.
_ROCMFPX_PRESET_TO_FAMILY: dict[str, str] = {
    "Q4_0_ROCMFP4_FAST": "ROCmFP4",
    "Q4_0_ROCMFP4": "ROCmFP4",
    "Q3_0_ROCMFPX": "ROCmFP3",
    "Q6_0_ROCMFPX": "ROCmFP6",
    "Q8_0_ROCMFPX": "ROCmFP8",
}

_ROCMFPX_FAMILY_RE = re.compile(
    r"(?<![A-Za-z0-9])(ROCmFP[3468]|ROCmFPX)(?![A-Za-z0-9])",
    re.IGNORECASE,
)
_ROCMFPX_FAMILY_CANON: dict[str, str] = {
    "rocmfp3": "ROCmFP3",
    "rocmfp4": "ROCmFP4",
    "rocmfp6": "ROCmFP6",
    "rocmfp8": "ROCmFP8",
    "rocmfpx": "ROCmFPX",
}


def quant_from_rocmfpx_filename(name: str) -> str | None:
    """ROCmFPX-family quant label from a filename, or ``None`` when no hit.

    Prefers an explicit GGUF preset token (``Q4_0_ROCMFP4``, ``Q8_0_ROCMFPX``)
    and falls back to a bare, boundary-anchored family token (``ROCmFP4``,
    ``ROCmFPX``). Returns the canonical family label (``ROCmFP4``, ``ROCmFP8``,
    …) so the roster shows the real weight family rather than ``None`` for
    these custom-file_type repacks.
    """
    text = name or ""
    upper = text.upper()
    for preset, family in _ROCMFPX_PRESET_TO_FAMILY.items():
        if preset in upper:
            return family
    m = _ROCMFPX_FAMILY_RE.search(text)
    if m:
        return _ROCMFPX_FAMILY_CANON[m.group(1).lower()]
    return None


def quant_from_file_type(code: Any) -> str | None:
    """Map a GGUF ``general.file_type`` value to a quant label.

    Tolerates the LLAMA_FTYPE_GUESSED flag (1024) being OR'd in.
    Unknown / non-int codes return ``None`` (callers fall back to the
    filename token).
    """
    if isinstance(code, bool) or not isinstance(code, int):
        return None
    if code >= _LLAMA_FTYPE_GUESSED:
        code -= _LLAMA_FTYPE_GUESSED
    return _FILE_TYPE_TO_QUANT.get(code)


def _hf_repo_name_from_path(path: Path) -> str | None:
    """Walk up the path looking for ``models--ORG--REPO`` (HF cache layout).

    Returns ``ORG/REPO`` when found, else ``None``. Useful when scanning the
    HF blob cache directly: blob files are content-hash named so the parent
    ``models--ORG--REPO`` dir is the only meaningful label.
    """
    for parent in path.parents:
        seg = parent.name
        if seg.startswith("models--") and "--" in seg[len("models--") :]:
            rest = seg[len("models--") :]
            parts = rest.split("--", 1)
            if len(parts) == 2:
                return f"{parts[0]}/{parts[1]}"
            return rest
    return None


Kind = Literal["llama", "moonshine", "kokoro", "flm", "unknown"]


@dataclass
class DetectionResult:
    """Outcome of a single-file detection pass.

    ``raw_hints`` carries provider-specific bits (the parsed GGUF KV pairs,
    or the matched filename tokens) so downstream UIs can show "why".

    ``kind`` is the runtime family the file belongs to; the UI uses it to
    gate which backends + capabilities are even offered. Mapping:

      llama     → GGUF, backends ∈ {vulkan, rocm, cuda, cpu}, caps {chat, embed, rerank, vision}
      moonshine → ASR provider, backend=moonshine, caps={asr}
      kokoro    → TTS provider, backend=kokoro, caps={tts}
      flm       → AMD NPU, backend=flm, caps={chat, embed}
      unknown   → could not classify; user picks manually if anything
    """

    suggested_backends: list[str]
    suggested_capabilities: list[str]
    context_length: int | None = None
    confidence: Confidence = "low"
    suggested_name: str | None = None
    kind: Kind = "unknown"
    raw_hints: dict[str, Any] = field(default_factory=dict)
    # Quantisation label ("Q4_K_M", "IQ2_XS", "F16", …) or None when unknown.
    # Header-derived (general.file_type) when readable, else filename token.
    quant: str | None = None


# ── helpers ────────────────────────────────────────────────────────────────


def _filename_capability(name: str) -> str | None:
    """Best-effort capability inferred from filename tokens. ``None`` if no hit.

    Delegates to the single shared token table
    (:func:`hal0.model_meta.capability_from_filename`, MR-3) but preserves
    detect's narrower contract: the downstream branching in
    :func:`_heuristic_only` and the GGUF embed fallback in :func:`detect`
    only understand ``embed``/``asr``/``tts``, so any other token the shared
    helper recognises (rerank/vision/video/image) is collapsed back to
    ``None`` here. Extending detect to emit ``rerank`` is a separate,
    pooling-semantics follow-up.
    """
    cap = capability_from_filename(name)
    return cap if cap in ("embed", "asr", "tts") else None


def _heuristic_only(path: Path, *, filename_hint: str | None = None) -> DetectionResult:
    """Fallback detection: filename heuristic, no header read.

    ``filename_hint`` — see :func:`detect`'s docstring (#1838). Used for
    capability-token matching and the ``.gguf`` backend-seed check only;
    identity fields (``suggested_name``, ``quant``, ``raw_hints["stem"/
    "suffix"]``) stay derived from ``path`` itself.
    """
    hint_name = (filename_hint or path.name).lower()
    cap = _filename_capability(hint_name)

    # A file under the ComfyUI models tree is an image-gen asset — tag it
    # image/comfyui so add-by-path / scan-preview file it on the dashboard's
    # image surface instead of "unknown" with empty caps (``_filename_capability``
    # deliberately collapses the image token to None, so this is the only place
    # a manually-added checkpoint gets the image capability).
    if "/comfyui/models/" in str(path).replace("\\", "/"):
        return DetectionResult(
            suggested_backends=["comfyui"],
            suggested_capabilities=["image"],
            context_length=None,
            confidence="low",
            suggested_name=_hf_repo_name_from_path(path),
            kind="unknown",
            raw_hints={"source": "comfyui-tree", "stem": path.stem, "suffix": path.suffix.lower()},
            quant=quant_from_filename(path.name),
        )

    backends: list[str] = []
    caps: list[str] = []
    kind: Kind = "unknown"
    if "moonshine" in hint_name:
        backends = ["moonshine"]
        caps = ["asr"]
        kind = "moonshine"
    elif "kokoro" in hint_name:
        backends = ["kokoro"]
        caps = ["tts"]
        kind = "kokoro"
    elif cap == "asr":
        # whisper or similar — likely moonshine-loadable
        backends = ["moonshine"]
        caps = ["asr"]
        kind = "moonshine"
    elif cap == "tts":
        backends = ["kokoro"]
        caps = ["tts"]
        kind = "kokoro"
    elif cap == "embed":
        # embed-ish filename but extension says it's not GGUF — leave
        # backends empty; user picks. Treat as unknown until format is clear.
        caps = ["embed"]
    elif path.suffix.lower() == ".gguf" or Path(hint_name).suffix == ".gguf":
        # #1838: the resolved path may be an extensionless HF hub-cache
        # blob even though the operator's own filename (the hint) is
        # unmistakably "*.gguf" — either one claiming .gguf is enough to
        # seed the GGUF backends instead of leaving the row unclassified.
        backends = list(_GGUF_BACKENDS)
        caps = ["chat"]
        kind = "llama"
    # else: leave kind=unknown, empty backends/caps

    return DetectionResult(
        suggested_backends=backends,
        suggested_capabilities=caps,
        context_length=None,
        confidence="low",
        suggested_name=_hf_repo_name_from_path(path),
        kind=kind,
        raw_hints={"source": "filename", "stem": path.stem, "suffix": path.suffix.lower()},
        quant=quant_from_rocmfpx_filename(path.name) or quant_from_filename(path.name),
    )


# ── public API ─────────────────────────────────────────────────────────────


def detect(path: str | Path, *, filename_hint: str | None = None) -> DetectionResult:
    """Inspect ``path`` and return a :class:`DetectionResult`.

    Never raises for an unreadable / missing / non-GGUF file: we fall
    back to the filename heuristic and lower the confidence.

    ``filename_hint`` — the operator-typed name to use for capability
    filename-token matching AND the ".gguf claimed" check below, when it
    differs from ``path`` (#1838: a HuggingFace hub-cache symlink's real,
    human-readable name lives at ``snapshots/<rev>/<name>.gguf``; ``path``
    here is usually the *resolved* sha-named blob it points at, which
    carries no filename signal — and no ``.gguf`` suffix — at all).
    Identity fields (``suggested_name``, quant-from-filename, ``stem``)
    stay derived from ``path`` unchanged; only the two things that decide
    "did the operator ask for a GGUF file" use the hint when given.
    Defaults to ``path``'s own name when omitted.
    """
    p = Path(path)
    suffix = p.suffix.lower()
    name_for_capability = filename_hint if filename_hint is not None else p.name
    # #1838: a resolved HF hub-cache blob is extensionless even when the
    # symlink the operator pointed at is unmistakably "*.gguf" — without
    # also checking the hint's suffix, a bad-magic blob silently fell all
    # the way to the generic "unknown, no backends, no warning" path
    # instead of the deliberate "claims .gguf but magic failed" one.
    hint_suffix = Path(name_for_capability).suffix.lower()
    claims_gguf = suffix == ".gguf" or hint_suffix == ".gguf"

    # Try GGUF magic bytes regardless of extension — HF blob cache stores
    # GGUF data under content-hash filenames with no suffix.
    header = read_gguf_header(p)
    if header is not None or claims_gguf:
        if header is None:
            # Suffix (or the operator-typed hint) claimed .gguf but magic
            # failed: degrade to heuristic with the GGUF backend seed.
            r = _heuristic_only(p, filename_hint=filename_hint)
            r.raw_hints["gguf_header_read"] = "failed"
            return r

        arch = header.get("general.architecture")
        ctx_len = header.get("context_length")
        ctx_len_int: int | None = ctx_len if isinstance(ctx_len, int) else None

        pooling = header.get("pooling_type")
        # llama.cpp uses pooling_type=0 (NONE) for causal chat models,
        # MEAN=1/CLS=2/LAST=3 for embedding, and RANK=4 for cross-encoder
        # rerankers. RANK used to be folded into the generic "positive int
        # => embed" bucket below, which collapsed rerankers to "embed" (and,
        # once the filename fallback also missed — see next comment — all
        # the way down to the "chat" default, #1796: a reranker gguf with no
        # pooling_type in its header registered as capabilities: chat).
        is_rerank = pooling == 4
        is_embed = not is_rerank and isinstance(pooling, int) and pooling > 0
        # pooling_type==0 (NONE) is an explicit, authoritative "this is a
        # causal chat model" read too — not just non-zero values — so it
        # also gets the "pooling_type" source/high confidence. The
        # tie-breakers/fallbacks below are for headers that DROP the key
        # entirely, not ones that set it to the chat value. Gate everything
        # past this point on the key being genuinely absent so an explicit
        # 0 can't be second-guessed by a stray tag or filename token.
        pooling_present = pooling is not None
        cap_source = "pooling_type" if pooling_present else None

        causal = header.get("attention_causal")

        # #1838: header tie-breakers for converters that drop pooling_type
        # entirely (the jina-reranker-v1-tiny-en case: no <arch>.pooling_type
        # key at all, so without these the classification came *solely*
        # from the filename while confidence was still reported "high").
        # These are unambiguous when present and are checked BEFORE the
        # filename fallback: general.tags carrying "reranker"/"cross-encoder"
        # or "embed"/"sentence-similarity" tokens.
        if not pooling_present:
            tags = header.get("general.tags")
            tag_set = {str(t).lower() for t in tags} if isinstance(tags, list) else set()
            if tag_set & {"reranker", "cross-encoder", "rerank"}:
                is_rerank = True
                cap_source = "header_tags"
            elif tag_set & {"embed", "embedding", "sentence-similarity", "feature-extraction"}:
                is_embed = True
                cap_source = "header_tags"

        # Filename token fallback in case pooling_type/tags are missing but
        # the file is clearly embed/rerank (some converters drop both).
        # Uses the full shared token table, NOT the narrower
        # ``_filename_capability`` helper above — that one deliberately
        # collapses "rerank" to None for detect()'s *older* embed/asr/tts-
        # only contract, which is exactly the gap that let a rerank
        # filename fall through to "chat" here. ``discover.scan_and_register``
        # (the install-time auto-scan) reads the same full table via
        # ``_guess_capability`` and gets rerank right for the identical
        # file — reuse its source of truth instead of re-diverging.
        #
        # Uses ``name_for_capability`` (the operator-typed name when the
        # caller supplied one), NOT ``p.name`` — a HF hub-cache symlink
        # resolves ``p`` to a sha-named blob whose name carries no signal
        # at all, which would otherwise hide a real filename token like
        # "jina-reranker-*.gguf" from this fallback (#1838).
        #
        # Anything landing here has NO header-derived capability signal —
        # the classification is a filename guess, so confidence is lowered
        # below (#1838) even though the header itself parsed cleanly.
        if not pooling_present and not is_rerank and not is_embed:
            filename_cap = capability_from_filename(name_for_capability)
            if filename_cap == "rerank":
                is_rerank = True
                cap_source = "filename"
            elif filename_cap == "embed":
                is_embed = True
                cap_source = "filename"

        caps = ["rerank"] if is_rerank else (["embed"] if is_embed else ["chat"])
        if cap_source is None:
            cap_source = "default"
            if not pooling_present and causal is False:
                # #1838: the header explicitly rules out a causal chat
                # model (attention.causal=False) but neither tags nor the
                # filename told us which non-chat capability it actually
                # is. Defaulting to "chat" here would contradict evidence
                # the header itself provided — that's worse than the
                # ordinary "no signal at all" default, so it does not get
                # the ordinary default's high confidence.
                cap_source = "causal_conflict"
            # else: neither the header nor the filename carried any
            # capability signal at all. This is the ordinary case for the
            # overwhelming majority of causal chat ggufs (llama.cpp doesn't
            # emit pooling_type for them) — "chat" is the correct, expected
            # answer here, not a guess, so confidence stays high.

        # Confidence reflects how the *capability* (not just the header
        # read) was derived. A filename token OVERRIDING the default, or a
        # "chat" default that contradicts an explicit attention.causal=False
        # read, is a guess (#1838's actual bug: a rerank/embed filename
        # token is what silently decided the outcome while confidence still
        # said "high"). A header-derived signal (pooling_type or the tags
        # tie-breaker) is a read, and the ordinary no-signal "chat" default
        # is the documented, expected fallback — both stay high.
        confidence: Confidence = (
            "medium" if cap_source in ("filename", "causal_conflict") else "high"
        )

        name_candidate = header.get("general.name") or header.get("general.basename")
        suggested_name = (
            str(name_candidate).strip()
            if isinstance(name_candidate, str) and name_candidate.strip()
            else None
        )
        if not suggested_name:
            suggested_name = _hf_repo_name_from_path(p)

        # Quant: a ROCmFPX-family filename token wins first — those repacks
        # carry a custom general.file_type code that quant_from_file_type()
        # can't map (and might collide with an upstream ftype int). Otherwise
        # the header file_type is authoritative, with the generic filename
        # token as the last fallback for converters that drop it.
        quant = (
            quant_from_rocmfpx_filename(p.name)
            or quant_from_file_type(header.get("general.file_type"))
            or quant_from_filename(p.name)
        )

        return DetectionResult(
            suggested_backends=list(_GGUF_BACKENDS),
            suggested_capabilities=caps,
            context_length=ctx_len_int,
            confidence=confidence,
            suggested_name=suggested_name,
            kind="llama",
            raw_hints={
                "source": "gguf_header",
                "architecture": arch,
                "pooling_type": pooling,
                "attention_causal": header.get("attention_causal"),
                "tags": header.get("general.tags"),
                "capability_source": cap_source,
                "version": header.get("version"),
                "name": header.get("general.name"),
                "basename": header.get("general.basename"),
                "size_label": header.get("general.size_label"),
                "file_type": header.get("general.file_type"),
            },
            quant=quant,
        )

    # Non-GGUF file: filename heuristic only.
    return _heuristic_only(p, filename_hint=filename_hint)


__all__ = [
    "DetectionResult",
    "detect",
    "quant_from_file_type",
    "quant_from_filename",
    "quant_from_rocmfpx_filename",
]
