"""Model detection — derive backends + capabilities from a file on disk.

Pure inspection: no registry mutation, no network. The output is a
:class:`DetectionResult` that callers (the scan endpoint, the single-file
register path) merge into a :class:`hal0.registry.model.Model` before
persisting.

Detection strategy, cheapest first:

1. ``.gguf`` files → :func:`hal0.registry.gguf_header.read_gguf_header`
   to pull arch + context_length + pooling_type. Strong evidence →
   ``confidence='high'``. The four GGUF backends are seeded:
   ``vulkan``, ``rocm``, ``cuda``, ``cpu``. Capability is ``embed`` when
   ``pooling_type`` is present and non-zero (llama.cpp marks pooling_type
   = NONE as 0 for chat models, > 0 for embed/rerank pooled outputs),
   else ``chat``.
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


def _heuristic_only(path: Path) -> DetectionResult:
    """Fallback detection: filename heuristic, no header read."""
    name = path.name.lower()
    cap = _filename_capability(name)

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
    if "moonshine" in name:
        backends = ["moonshine"]
        caps = ["asr"]
        kind = "moonshine"
    elif "kokoro" in name:
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
    elif path.suffix.lower() == ".gguf":
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


def detect(path: str | Path) -> DetectionResult:
    """Inspect ``path`` and return a :class:`DetectionResult`.

    Never raises for an unreadable / missing / non-GGUF file: we fall
    back to the filename heuristic and lower the confidence.
    """
    p = Path(path)
    suffix = p.suffix.lower()

    # Try GGUF magic bytes regardless of extension — HF blob cache stores
    # GGUF data under content-hash filenames with no suffix.
    header = read_gguf_header(p)
    if header is not None or suffix == ".gguf":
        if header is None:
            # Suffix claimed .gguf but magic failed: degrade to heuristic
            # with the GGUF backend seed.
            r = _heuristic_only(p)
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

        # Filename token fallback in case pooling_type is missing but the
        # file is clearly embed/rerank (some converters drop it). Uses the
        # full shared token table, NOT the narrower ``_filename_capability``
        # helper above — that one deliberately collapses "rerank" to None
        # for detect()'s *older* embed/asr/tts-only contract, which is
        # exactly the gap that let a rerank filename fall through to "chat"
        # here. ``discover.scan_and_register`` (the install-time auto-scan)
        # reads the same full table via ``_guess_capability`` and gets
        # rerank right for the identical file — reuse its source of truth
        # instead of re-diverging.
        if not is_rerank and not is_embed:
            filename_cap = capability_from_filename(p.name)
            if filename_cap == "rerank":
                is_rerank = True
            elif filename_cap == "embed":
                is_embed = True

        caps = ["rerank"] if is_rerank else (["embed"] if is_embed else ["chat"])

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
            confidence="high",
            suggested_name=suggested_name,
            kind="llama",
            raw_hints={
                "source": "gguf_header",
                "architecture": arch,
                "pooling_type": pooling,
                "version": header.get("version"),
                "name": header.get("general.name"),
                "basename": header.get("general.basename"),
                "size_label": header.get("general.size_label"),
                "file_type": header.get("general.file_type"),
            },
            quant=quant,
        )

    # Non-GGUF file: filename heuristic only.
    return _heuristic_only(p)


__all__ = [
    "DetectionResult",
    "detect",
    "quant_from_file_type",
    "quant_from_filename",
    "quant_from_rocmfpx_filename",
]
