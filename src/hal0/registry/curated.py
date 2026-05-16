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
"""

from __future__ import annotations

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


__all__ = [
    "CURATED_BY_ID",
    "CURATED_MODELS",
    "CuratedModel",
    "get_curated",
]
