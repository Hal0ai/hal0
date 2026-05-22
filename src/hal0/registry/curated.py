"""Curated model catalogue — the FirstRun wizard's pick list.

The catalogue is a small, hand-picked list of "good defaults" so a fresh
hal0 install can be productive after one click. Each entry points at a
specific GGUF file inside a HuggingFace repo (Q4_K_M usually, picked for
the size/quality sweet spot on Strix Halo's 100 GB unified pool).

The wizard fetches this list via ``GET /api/install/curated-models`` and
renders each as a card. A user who wants something off-catalogue uses
the "custom HF URL" affordance — that goes through the same pull endpoint
without touching the curated list.

# NOTE: the catalogue lives in code (not a TOML file on disk) on purpose:
# it ships *with* a hal0 release so users can't end up on an outdated
# pick list. v0.2 may introduce a remote-fetched manifest with signed
# releases; for v1 a frozen-at-build-time list is plenty.

# TODO(stt/tts curated picks): the FirstRun wizard's STT and TTS
# dropdowns currently fall back on HaloaiModel seed rows (moonshine,
# kokoro, vibevoice), which the capability catalog intentionally
# filters out — they're upstream-routed, not pullable. The blocker for
# adding real CuratedModel picks is the upstream file shape:
#
#   * Moonshine ships its weights as a multi-file ONNX bundle
#     (``encode_model.ort`` + ``decode_model.ort`` + tokenizer JSON
#     under ``quantized/<variant>/``). The pull engine (registry/pull.py)
#     streams a single ``hf_repo/resolve/main/<file>`` URL, and the
#     curated-model schema validation (tests/registry/test_curated.py)
#     restricts ``hf_file`` to ``.gguf``/``.safetensors``/``.ckpt``.
#     ``.ort`` files don't fit either constraint.
#   * Whisper.cpp GGUF mirrors exist (``oxide-lab/whisper-tiny-GGUF``,
#     ``xkeyC/whisper-large-v3-turbo-gguf``) but hal0's STT runtime is
#     the Moonshine toolbox (``_RUNTIME_TO_HOST_BACKENDS["moonshine"]``
#     in capabilities/catalog.py), which can't load whisper GGUFs.
#     Surfacing a whisper.cpp pick needs either a whisper-cpp toolbox
#     image or routing whisper-ggufs through llama-server (which 0.1.x
#     llama-server doesn't support for transcription).
#   * Kokoro's only public weight is ``hexgrad/Kokoro-82M/kokoro-v1_0.pth``
#     — a PyTorch pickle. The ONNX mirror
#     (``onnx-community/Kokoro-82M-v1.0-ONNX``) ships only ``.onnx`` +
#     ``voices/*.bin``. Neither fits the allowed suffix list.
#   * VibeVoice is similarly multi-file safetensors but lives in a
#     diffusers-style repo (config + multiple shards), not a single
#     pullable file.
#
# Resolutions to unblock (any one is enough):
#   1. Add a multi-file pull mode to ``registry/pull.py`` that snapshots
#      a HF repo dir into the model store, and relax the curated
#      ``hf_file`` validator to allow a directory glob.
#   2. Ship a whisper-cpp toolbox image and add a ``whisper`` entry to
#      ``_RUNTIME_TO_HOST_BACKENDS``, then surface whisper.cpp GGUFs
#      under stt.
#   3. Keep the HaloaiModel seed rows visible in the wizard but mark
#      them clearly as "needs upstream routing" — would require
#      narrowing the ``HaloaiModel`` filter in
#      ``capabilities/catalog._flat_rows_for_capability`` (NOT the call
#      this PR makes — that filter is load-bearing).
#
# Until one of those lands, the wizard's STT and TTS dropdowns will
# stay empty on a standalone install and the operator falls back on the
# "skip this capability" path or the post-install Models view.
"""

from __future__ import annotations

import json
from functools import lru_cache
from pathlib import Path

from pydantic import BaseModel, Field

# ── Schema ─────────────────────────────────────────────────────────────────


class CuratedModel(BaseModel):
    """One curated entry surfaced by the FirstRun wizard.

    The wizard renders these as cards with name + size + VRAM badge +
    license badge. The ``hf_repo`` / ``hf_file`` pair is what gets piped
    into ``POST /api/models/{id}/pull`` to actually download the bytes.

    Image-gen models (``recommended_slot="img"``, set ``model_class`` and
    ``comfyui_subdir``) are pulled into ComfyUI's models tree instead of
    the default per-id directory — see :func:`hal0.registry.pull.run_pull`.
    """

    model_config = {"populate_by_name": True, "str_strip_whitespace": True}

    id: str = Field(..., description="Stable id used as the registry key, e.g. 'qwen3-4b'.")
    display_name: str = Field(..., description="Human-readable name for the card.")
    description: str = Field(..., description="One-line value prop.")
    family: str = Field(..., description="Model family — 'qwen', 'llama', 'phi', 'sdxl', etc.")
    size_gb: float = Field(..., description="Approximate on-disk size of the model file(s), in GB.")
    vram_gb_min: float = Field(
        ...,
        description="Minimum recommended VRAM (or unified pool) in GB.",
    )
    license: str = Field(..., description="SPDX-ish license short name, e.g. 'Apache-2.0'.")
    license_url: str = Field(..., description="HTTPS URL to the canonical license text.")
    hf_repo: str = Field(
        ..., description="HuggingFace repo id, e.g. 'Qwen/Qwen3-4B-Instruct-GGUF'."
    )
    hf_file: str = Field(..., description="Filename within the repo (GGUF, safetensors, etc.).")
    context_length: int = Field(
        default=0,
        description=("Native context window in tokens. Zero/omitted for image-gen entries."),
    )
    recommended_slot: str = Field(
        default="primary",
        description="Default slot to assign the model to. 'primary' for chat, 'img' for image-gen.",
    )
    tags: list[str] = Field(
        default_factory=list,
        description="Freeform tags shown as chips ('chat', 'vision', 'fast', etc.).",
    )
    notes: str = Field(
        default="",
        description="Operator-facing rationale for the quant pick or anything else worth surfacing.",
    )
    capability: str = Field(
        default="chat",
        description=(
            "Primary capability — 'chat' (default), 'embed', 'asr', 'tts', 'image'. "
            "The pull layer routes 'image' into the ComfyUI models tree."
        ),
    )
    backend: str = Field(
        default="",
        description=(
            "Runtime backend tag the capability catalog fans out from. "
            "'llamacpp' for GGUF chat/embed/rerank picks (catalog fans "
            "out to gpu-vulkan/gpu-rocm/cpu); empty for image picks (the "
            "catalog routes them to ComfyUI via ``comfyui_subdir``). The "
            "field has no effect on the pull layer — pulls always go "
            "through hf_repo + hf_file."
        ),
    )
    model_class: str = Field(
        default="",
        description=(
            "Image-gen model class discriminator, e.g. 'sdxl-turbo', 'sd-1.5', "
            "'flux-schnell'. Selects which ComfyUI workflow template to render. "
            "Empty for non-image entries."
        ),
    )
    comfyui_subdir: str = Field(
        default="",
        description=(
            "Subdirectory under ComfyUI's models/ tree where this file should "
            "land. Common values: 'checkpoints' (whole-model safetensors), "
            "'loras', 'vae'. Empty means use the default per-id models tree."
        ),
    )


# ── The catalogue ──────────────────────────────────────────────────────────
# Picking Q4_K_M as the default quant: best size/quality trade-off in the
# common 2-5 GB range, ships in every reputable GGUF repo. If a repo only
# ships Q4_0 or Q4 (no _K_M), we fall back to the closest equivalent and
# call it out in ``notes``.

CURATED_MODELS: list[CuratedModel] = [
    # ── 2026-05 refresh: featured chat picks the wizard surfaces first ────
    # Sized for a Strix Halo unified memory pool (~100 GB).  The wizard
    # renders entries in the order listed below; legacy entries below the
    # block stay for backward compatibility (tests + already-resolved
    # registry rows reference their ids).
    CuratedModel(
        id="qwen3-coder-next",
        display_name="Qwen3 Coder Next",
        description="Frontier coding model. Best in class for software work — needs the full Strix Halo pool.",
        family="qwen",
        size_gb=49.0,
        vram_gb_min=56.0,
        license="Apache-2.0",
        license_url="https://www.apache.org/licenses/LICENSE-2.0",
        hf_repo="unsloth/Qwen3-Coder-Next-GGUF",
        hf_file="Qwen3-Coder-Next-UD-Q4_K_XL.gguf",
        context_length=262144,
        recommended_slot="primary",
        tags=["chat", "code", "frontier", "long-context"],
        notes="Unsloth's Q4_K_XL quant. Needs ~56 GB unified RAM headroom.",
    ),
    CuratedModel(
        id="qwen3.6-27b",
        display_name="Qwen3.6 27B",
        description="General-purpose chat — the sweet spot for Strix Halo. Strong reasoning + multilingual.",
        family="qwen",
        size_gb=20.0,
        vram_gb_min=24.0,
        license="Apache-2.0",
        license_url="https://www.apache.org/licenses/LICENSE-2.0",
        hf_repo="unsloth/Qwen3.6-27B-GGUF",
        hf_file="Qwen3.6-27B-UD-Q5_K_XL.gguf",
        context_length=131072,
        recommended_slot="primary",
        tags=["chat", "reasoning", "multilingual", "default"],
        notes="Q5_K_XL — quality margin over Q4 with room to spare on a 100 GB pool.",
    ),
    CuratedModel(
        id="gpt-oss-20b",
        display_name="GPT-OSS 20B",
        description="OpenAI's open-weights 20B. Reasonable RAM footprint, broad capability.",
        family="gpt-oss",
        size_gb=12.0,
        vram_gb_min=16.0,
        license="Apache-2.0",
        license_url="https://www.apache.org/licenses/LICENSE-2.0",
        hf_repo="unsloth/gpt-oss-20b-GGUF",
        hf_file="gpt-oss-20b-Q4_K_M.gguf",
        context_length=131072,
        recommended_slot="primary",
        tags=["chat", "reasoning"],
        notes="Repo + filename are the unsloth GGUF mirror; verify before v1 cut.",
    ),
    CuratedModel(
        id="qwen3.5-9b",
        display_name="Qwen3.5 9B",
        description="Lean default chat — fits comfortably alongside embed/voice slots.",
        family="qwen",
        size_gb=6.0,
        vram_gb_min=8.0,
        license="Apache-2.0",
        license_url="https://www.apache.org/licenses/LICENSE-2.0",
        hf_repo="unsloth/Qwen3.5-9B-GGUF",
        hf_file="Qwen3.5-9B-UD-Q4_K_XL.gguf",
        context_length=131072,
        recommended_slot="primary",
        tags=["chat", "balanced"],
        notes="Q4_K_XL — leaves headroom for an embed slot + voice slot on the same host.",
    ),
    CuratedModel(
        id="qwen3.5-0.8b",
        display_name="Qwen3.5 0.8B",
        description="Tiny first-boot pick. Useful for smoke-testing the install before pulling a real chat model.",
        family="qwen",
        size_gb=0.6,
        vram_gb_min=1.0,
        license="Apache-2.0",
        license_url="https://www.apache.org/licenses/LICENSE-2.0",
        hf_repo="unsloth/Qwen3.5-0.8B-GGUF",
        hf_file="Qwen3.5-0.8B-UD-Q4_K_XL.gguf",
        context_length=32768,
        recommended_slot="primary",
        tags=["chat", "tiny", "smoke-test"],
        notes="Sub-second cold start. Good for verifying the slot lifecycle before downloading a 20+ GB pick.",
    ),
    # ── Kept-in-featured legacy picks (explicit user ask): qwen3-4b for
    # mid-tier Vulkan hosts, phi3-mini for the MIT-licensed pick.  Slot
    # below the 2026-05 refresh — wizard still surfaces them in the main
    # list, just lower in render order.
    CuratedModel(
        id="qwen3-4b",
        display_name="Qwen3 4B Instruct",
        description="Multilingual, fast all-rounder. Great default for a 4-8 GB VRAM budget.",
        family="qwen",
        size_gb=2.5,
        vram_gb_min=4.0,
        license="Apache-2.0",
        license_url="https://www.apache.org/licenses/LICENSE-2.0",
        hf_repo="Qwen/Qwen3-4B-Instruct-GGUF",
        hf_file="qwen3-4b-instruct-q4_k_m.gguf",
        context_length=32768,
        recommended_slot="primary",
        tags=["chat", "multilingual", "balanced"],
        notes="Q4_K_M quant for the size/quality sweet spot.",
    ),
    CuratedModel(
        id="llama32-3b",
        display_name="Llama 3.2 3B Instruct",
        description="Small and fast. Good fit for low-VRAM hosts or quick experimentation.",
        family="llama",
        size_gb=2.0,
        vram_gb_min=3.0,
        license="Llama-3.2-Community",
        license_url="https://www.llama.com/llama3_2/license/",
        hf_repo="bartowski/Llama-3.2-3B-Instruct-GGUF",
        hf_file="Llama-3.2-3B-Instruct-Q4_K_M.gguf",
        context_length=131072,
        recommended_slot="primary",
        tags=["chat", "fast", "low-vram"],
        notes="Q4_K_M from bartowski's GGUF repack (the upstream Meta release ships safetensors only).",
    ),
    CuratedModel(
        id="phi3-mini",
        display_name="Phi-3 Mini 4K Instruct",
        description="Compact reasoning model from Microsoft. MIT licensed.",
        family="phi",
        size_gb=2.4,
        vram_gb_min=3.0,
        license="MIT",
        license_url="https://opensource.org/license/mit",
        hf_repo="microsoft/Phi-3-mini-4k-instruct-gguf",
        hf_file="Phi-3-mini-4k-instruct-q4.gguf",
        context_length=4096,
        recommended_slot="primary",
        tags=["chat", "reasoning", "mit"],
        notes=(
            "Microsoft's official GGUF release ships 'q4' (no _K_M variant); "
            "we use that. Smallest validated catalogue entry — good 'just download something' pick."
        ),
    ),
    # ── Embed picks (llama-server with --embedding) ────────────────────────
    # The wizard's "Embed" capability dropdown surfaces these. Both are
    # llama.cpp-compatible GGUFs so they fan out to gpu-vulkan/gpu-rocm/cpu
    # via _backend_variants. nomic-embed is the canonical light pick
    # (single-file ~150 MB Q8_0); bge-base-en-v1.5 is the medium pick
    # (~70 MB Q4_K_M, better English retrieval quality than nomic on MTEB).
    CuratedModel(
        id="nomic-embed-text-v1.5-q8_0",
        display_name="Nomic Embed Text v1.5 (Q8_0)",
        description=(
            "Fast, accurate English/multilingual embeddings. Tiny enough "
            "to ride alongside any chat slot. The default embed pick."
        ),
        family="nomic",
        size_gb=0.15,
        vram_gb_min=0.5,
        license="Apache-2.0",
        license_url="https://www.apache.org/licenses/LICENSE-2.0",
        hf_repo="nomic-ai/nomic-embed-text-v1.5-GGUF",
        hf_file="nomic-embed-text-v1.5.Q8_0.gguf",
        context_length=8192,
        recommended_slot="embed",
        tags=["embed", "light"],
        notes=(
            "Q8_0 over Q4_K_M because embedding quality is brittle under "
            "aggressive quantization and the size delta (146 MB vs 84 MB) "
            "is irrelevant on Strix Halo's 100 GB pool."
        ),
        capability="embed",
        backend="llamacpp",
    ),
    CuratedModel(
        id="bge-base-en-v1.5-q4_k_m",
        display_name="BGE Base EN v1.5 (Q4_K_M)",
        description=(
            "Higher English retrieval quality than nomic. Good when "
            "RAG accuracy matters more than multilingual coverage."
        ),
        family="bge",
        size_gb=0.07,
        vram_gb_min=0.5,
        license="MIT",
        license_url="https://opensource.org/license/mit",
        hf_repo="CompendiumLabs/bge-base-en-v1.5-gguf",
        hf_file="bge-base-en-v1.5-q4_k_m.gguf",
        context_length=512,
        recommended_slot="embed",
        tags=["embed", "medium"],
        notes=(
            "BAAI's BGE family leads MTEB English retrieval; Q4_K_M is "
            "the standard quality/size sweet spot and the CompendiumLabs "
            "repo is the canonical GGUF mirror."
        ),
        capability="embed",
        backend="llamacpp",
    ),
    # ── Rerank picks (llama-server with --reranking) ───────────────────────
    # Per memory hal0_rerank_slot_wiring, the working recipe is
    # llama-server on a non-8081 port with --reranking; bge-reranker-v2-m3
    # Q4_K_M is already running in production on hal0 LXC.
    CuratedModel(
        id="bge-reranker-base-q4_k_m",
        display_name="BGE Reranker Base (Q4_K_M)",
        description=(
            "Light cross-encoder reranker for English RAG. ~260 MB on "
            "disk, runs on CPU comfortably."
        ),
        family="bge",
        size_gb=0.26,
        vram_gb_min=0.5,
        license="MIT",
        license_url="https://opensource.org/license/mit",
        hf_repo="cstr/bge-reranker-base-GGUF",
        hf_file="bge-reranker-base-q4_k.gguf",
        context_length=512,
        recommended_slot="embed",
        tags=["rerank", "light"],
        notes=(
            "cstr's GGUF mirror includes the classifier-head fix needed "
            "for llama-server --reranking; the upstream BAAI repo ships "
            "PyTorch only. Q4_K is the smallest quant that preserves "
            "rerank ordering on BEIR."
        ),
        capability="rerank",
        backend="llamacpp",
    ),
    CuratedModel(
        id="bge-reranker-v2-m3-q4_k_m",
        display_name="BGE Reranker v2 M3 (Q4_K_M)",
        description=(
            "Multilingual cross-encoder reranker. The production pick on "
            "the hal0 LXC; ~440 MB, runs on CPU or GPU."
        ),
        family="bge",
        size_gb=0.44,
        vram_gb_min=1.0,
        license="Apache-2.0",
        license_url="https://www.apache.org/licenses/LICENSE-2.0",
        hf_repo="gpustack/bge-reranker-v2-m3-GGUF",
        hf_file="bge-reranker-v2-m3-Q4_K_M.gguf",
        context_length=8192,
        recommended_slot="embed",
        tags=["rerank", "medium"],
        notes=(
            "v2-m3 covers 100+ languages and beats v1 base on most "
            "benchmarks. Q4_K_M matches the running config on hal0; "
            "remember to wire the slot to a non-8081 port and pass "
            "--reranking (see memory hal0_rerank_slot_wiring)."
        ),
        capability="rerank",
        backend="llamacpp",
    ),
    # ── STT / TTS picks ────────────────────────────────────────────────────
    # Intentionally empty — see the module docstring TODO. The blocker is
    # the pull layer's single-file shape vs. moonshine/kokoro's multi-file
    # ONNX/PyTorch bundles. The HaloaiModel seed rows
    # (moonshine-small-streaming-en, tts-1, kokoro, vibevoice-realtime-0.5b)
    # remain visible through the /api/models/catalogue surface but are
    # filtered out of the capability dropdowns by design.
    # ── Image-gen models (recommended_slot="img", routed through ComfyUI) ────
    #
    # Curated picks intentionally span the licensing spectrum:
    #   - SDXL Turbo  : SAI Non-Commercial Research Community (research only).
    #   - SD 1.5      : CreativeML Open RAIL-M (research + commercial w/ caveats).
    #   - Flux Schnell: Apache-2.0 (the OSS unicorn — no usage restrictions).
    # The picker UI must surface these license badges so users pick consciously.
    CuratedModel(
        id="sdxl-turbo",
        display_name="SDXL Turbo",
        description=(
            "Stability AI's distilled 1-4 step SDXL. Real-time-ish image gen on "
            "Strix Halo. Research-only license."
        ),
        family="sdxl",
        size_gb=6.5,
        vram_gb_min=8.0,
        license="SAI-NC-Research-Community",
        license_url=("https://huggingface.co/stabilityai/sdxl-turbo/blob/main/LICENSE.TXT"),
        hf_repo="stabilityai/sdxl-turbo",
        hf_file="sd_xl_turbo_1.0_fp16.safetensors",
        context_length=0,
        recommended_slot="img",
        tags=["image", "sdxl", "fast", "research-only"],
        notes=(
            "Single-file FP16 checkpoint. Use the sdxl_turbo_simple workflow "
            "with 4 steps + cfg≈1.0; that's what produces sharp output at this "
            "step count."
        ),
        capability="image",
        model_class="sdxl-turbo",
        comfyui_subdir="checkpoints",
    ),
    CuratedModel(
        id="sd-1.5-pruned-emaonly",
        display_name="Stable Diffusion 1.5",
        description=(
            "RunwayML's classic SD 1.5 (pruned, EMA-only). Tiny by today's "
            "standards, runs on a potato. CreativeML Open RAIL-M."
        ),
        family="sd",
        size_gb=4.3,
        vram_gb_min=4.0,
        license="CreativeML-Open-RAIL-M",
        license_url=("https://huggingface.co/runwayml/stable-diffusion-v1-5/blob/main/LICENSE"),
        hf_repo="runwayml/stable-diffusion-v1-5",
        hf_file="v1-5-pruned-emaonly.safetensors",
        context_length=0,
        recommended_slot="img",
        tags=["image", "sd-1.5", "low-vram"],
        notes=(
            "Use the sd15_simple workflow (20 steps, Euler, cfg 7). Native "
            "512x512 - quality drops above 768."
        ),
        capability="image",
        model_class="sd-1.5",
        comfyui_subdir="checkpoints",
    ),
    CuratedModel(
        id="flux-schnell",
        display_name="FLUX.1 [schnell]",
        description=(
            "Black Forest Labs' Flux Schnell. State-of-the-art quality, "
            "Apache-2.0 licensed (rare in this space)."
        ),
        family="flux",
        size_gb=23.8,
        vram_gb_min=24.0,
        license="Apache-2.0",
        license_url="https://www.apache.org/licenses/LICENSE-2.0",
        hf_repo="black-forest-labs/FLUX.1-schnell",
        hf_file="flux1-schnell.safetensors",
        context_length=0,
        recommended_slot="img",
        tags=["image", "flux", "apache", "high-vram"],
        notes=(
            "23 GB checkpoint — fits Strix Halo's 100 GB unified pool but the "
            "default sdxl_turbo_simple workflow won't load Flux's T5 text "
            "encoder. Ship a flux-specific workflow before promoting this in "
            "the picker UI; for v1 it's catalogued so the curated list is "
            "complete."
        ),
        capability="image",
        model_class="flux-schnell",
        comfyui_subdir="checkpoints",
    ),
]


CURATED_BY_ID: dict[str, CuratedModel] = {m.id: m for m in CURATED_MODELS}


def get_curated(model_id: str) -> CuratedModel | None:
    """Return the curated entry by id, or ``None`` if not in the catalogue."""
    return CURATED_BY_ID.get(model_id)


# ── haloai upstream seed ───────────────────────────────────────────────────
# The haloai LXC exposes a large /v1/models surface (FastFlowLM NPU
# models, llamacpp chat, kokoro/vibevoice/moonshine voice, minimax). A
# frozen snapshot of the curated subset lives at
# ``seeds/haloai_models.json`` so the UI's Models view and ``hal0 model
# list`` can show real upstream model ids without hitting the network at
# import time. Refresh with ``scripts/import_haloai_models.py``.


class HaloaiModel(BaseModel):
    """One upstream-routed model imported from the haloai catalogue.

    Unlike :class:`CuratedModel` (which describes a pullable GGUF/safetensors
    file with HF coordinates), a HaloaiModel is a route into an existing
    upstream service. There is no file to download — the id is what the
    upstream's OpenAI-compatible API answers to.
    """

    model_config = {"populate_by_name": True, "str_strip_whitespace": True}

    id: str = Field(..., description="OpenAI-compatible model id the upstream answers to.")
    owned_by: str = Field(..., description="Upstream's owned_by tag (FastFlowLM, llamacpp, …).")
    upstream: str = Field(default="", description="Logical upstream/slot name (npu, primary, …).")
    capability: str = Field(
        default="chat",
        description="Primary capability: chat | embed | rerank | asr | tts | vision | image.",
    )
    backend: str = Field(
        default="llamacpp",
        description="Backend runtime: flm | llamacpp | kokoro | moonshine | vibevoice | minimax.",
    )
    size_bytes: int | None = Field(
        default=None, description="On-disk size if reported by upstream."
    )
    params: int | None = Field(default=None, description="Parameter count if reported by upstream.")
    context_size: int | None = Field(default=None, description="Native context window if known.")


_HALOAI_SEED_PATH = Path(__file__).parent / "seeds" / "haloai_models.json"


@lru_cache(maxsize=1)
def _load_haloai_seed() -> list[HaloaiModel]:
    """Read the frozen haloai snapshot from disk. Cached after first call."""
    if not _HALOAI_SEED_PATH.is_file():
        return []
    with _HALOAI_SEED_PATH.open("rb") as f:
        raw = json.load(f)
    return [HaloaiModel.model_validate(entry) for entry in raw]


def _build_curated() -> list[CuratedModel | HaloaiModel]:
    """Merge the hand-rolled curated list with the haloai seed.

    Local :data:`CURATED_MODELS` entries win on id collision — their
    fields are intentionally tuned for the FirstRun wizard's UX and must
    not be clobbered by a seed refresh.
    """
    seen: set[str] = {m.id for m in CURATED_MODELS}
    merged: list[CuratedModel | HaloaiModel] = list(CURATED_MODELS)
    for entry in _load_haloai_seed():
        if entry.id in seen:
            continue
        seen.add(entry.id)
        merged.append(entry)
    return merged


#: Merged catalogue surfaced by ``hal0 model list`` and the UI's Models view.
#: Built lazily on first access; safe to import at module load time because
#: no network call is made — the haloai entries come from a frozen JSON seed.
CURATED: list[CuratedModel | HaloaiModel] = _build_curated()


__all__ = [
    "CURATED",
    "CURATED_BY_ID",
    "CURATED_MODELS",
    "CuratedModel",
    "HaloaiModel",
    "get_curated",
]
