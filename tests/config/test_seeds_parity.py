"""Byte-identical parity test: hal0.config.seeds.* vs the pre-refactor
hardcoded SEED_PROFILES / SEED_STACKS / PROFILE_BENCH / FAMILY_DEFAULTS dicts.

This is the load-bearing safety net for the P3-schema data externalization
(spec-p3-schema.final.md, Tests item 2): every dict below is a frozen
snapshot, hand-copied from ``hal0/config/schema.py`` as it stood immediately
before the TOML externalization landed (see git history for
``rework/p3-schema``'s first commit). If ``hal0.config.seeds`` ever drifts
from this snapshot -- a dropped flag, a retyped bool, a renamed key -- this
test fails with the exact key/value that changed, catching a
flag-string-fidelity regression that a mere "does it validate" test would
miss entirely.

Do NOT "fix" this file by updating the golden dicts to match a code change
to seeds.py/the TOML -- that defeats the point. A deliberate seed-data change
(new bench numbers, a re-tuned flag) should update the TOML AND get this
fixture reviewed as a real behavior change, not silently rubber-stamped.
"""

from __future__ import annotations

from hal0.config import seeds
from hal0.config.schema import (
    DEFAULT_ROCMFPX_IMAGE,
    FALLBACK_VULKAN_IMAGE,
    StackCapabilityRow,
    StackConfig,
    StackSlotEntry,
)

# ── Golden SEED_PROFILES (pre-refactor schema.py, verbatim) ───────────────────

_GOLDEN_SEED_PROFILES: dict[str, dict[str, object]] = {
    "rocm": {
        "image": DEFAULT_ROCMFPX_IMAGE,
        "flags": "-ngl 999 -fa on --jinja",
        "mtp": False,
        "device_class": "gpu",
        "backend": "rocm",
        "intent": "ROCm",
        "quant": "FP4",
    },
    "rocm-dense": {
        "image": DEFAULT_ROCMFPX_IMAGE,
        "flags": "-ngl 999 -fa on -dev ROCm0 -b 512 -ub 512 --parallel 1 --threads 16 --no-mmap --jinja --metrics --no-webui --ctx-checkpoints 0 --checkpoint-every-n-tokens -1",
        "mtp": True,
        "device_class": "gpu",
        "backend": "rocm",
        "intent": "ROCmFPX · DENSE · MTP (sustained-decode)",
        "quant": "ROCmFP4",
    },
    "rocm-moe": {
        "image": DEFAULT_ROCMFPX_IMAGE,
        "flags": "-ngl 999 -fa on -dev ROCm0 -sm none -b 2048 -ub 512 --parallel 1 --threads 16 --no-mmap --no-context-shift --jinja --metrics --no-webui",
        "mtp": True,
        "device_class": "gpu",
        "backend": "rocm",
        "intent": "ROCmFPX · MOE · MTP (prefill-bound / -dev ROCm0)",
        "quant": "ROCmFPX",
    },
    "vulkan-dense": {
        "image": DEFAULT_ROCMFPX_IMAGE,
        "flags": "-ngl 999 -fa on -dev Vulkan0 -b 512 -ub 512 --parallel 1 --threads 16 --no-mmap --no-context-shift --jinja --metrics --no-webui",
        "mtp": True,
        "device_class": "gpu",
        "backend": "vulkan",
        "intent": "VULKFPX · DENSE · MTP (prefill-bound)",
        "quant": "ROCmFP4",
    },
    "vulkan-moe": {
        "image": DEFAULT_ROCMFPX_IMAGE,
        "flags": "-ngl 999 -fa on -dev Vulkan0 -sm none -b 2048 -ub 512 --parallel 1 --threads 16 --no-mmap --no-context-shift --jinja --metrics --no-webui",
        "mtp": True,
        "device_class": "gpu",
        "backend": "vulkan",
        "intent": "VULKFPX · MOE · MTP (best decode t/s)",
        "quant": "ROCmFPX",
    },
    "rocm-dense-nojinja": {
        "image": DEFAULT_ROCMFPX_IMAGE,
        "flags": "-ngl 999 -fa on -dev ROCm0 -b 512 -ub 512 --parallel 1 --threads 16 --no-mmap --metrics --no-webui --ctx-checkpoints 0 --checkpoint-every-n-tokens -1",
        "mtp": False,
        "device_class": "gpu",
        "backend": "rocm",
        "intent": "ROCmFPX · DENSE · no-jinja (baked chat template)",
        "quant": "ROCmFP4",
    },
    "vulkan-dense-nojinja": {
        "image": DEFAULT_ROCMFPX_IMAGE,
        "flags": "-ngl 999 -fa on -dev Vulkan0 -b 512 -ub 512 --parallel 1 --threads 16 --no-mmap --no-context-shift --metrics --no-webui",
        "mtp": False,
        "device_class": "gpu",
        "backend": "vulkan",
        "intent": "VULKFPX · DENSE · no-jinja (baked chat template)",
        "quant": "ROCmFP4",
    },
    "rocm-dense-small": {
        "image": DEFAULT_ROCMFPX_IMAGE,
        "flags": "-ngl 999 -fa on -dev ROCm0 -b 512 -ub 512 --parallel 1 --threads 16 --no-mmap --jinja --metrics --no-webui --ctx-checkpoints 0 --checkpoint-every-n-tokens -1",
        "mtp": False,
        "device_class": "gpu",
        "backend": "rocm",
        "intent": "ROCmFPX · DENSE · small (no MTP draft head)",
        "quant": "ROCmFP4",
    },
    "rocm-longctx": {
        "image": DEFAULT_ROCMFPX_IMAGE,
        "flags": "-ngl 999 -fa on -dev ROCm0 -ctk q8_0 -ctv q8_0 -b 2048 -ub 512 --parallel 1 --threads 16 --no-mmap --no-context-shift --poll 100 --poll-batch 1 --jinja --metrics --no-webui",
        "mtp": False,
        "device_class": "gpu",
        "backend": "rocm",
        "intent": "Dense · long-context (q8_0 KV)",
        "quant": "FP4",
    },
    "vulkan": {
        "image": DEFAULT_ROCMFPX_IMAGE,
        "flags": "-ngl 999 -fa on --jinja",
        "mtp": False,
        "device_class": "gpu",
        "backend": "vulkan",
        "intent": "Vulkan",
        "quant": "Q4_K_M",
    },
    "cuda": {
        "image": "ghcr.io/ggml-org/llama.cpp:server-cuda",
        "flags": "-ngl 999 -fa on -b 512 -ub 512 --parallel 1 --threads 8 --no-mmap --jinja",
        "mtp": False,
        "device_class": "gpu",
        "backend": "cuda",
        "intent": "CUDA · experimental",
        "quant": "Q4_K_M",
    },
    "embed": {
        "image": DEFAULT_ROCMFPX_IMAGE,
        "flags": "--embedding -ngl 999 -fa on -b 8192 -ub 8192 --no-mmap",
        "mtp": False,
        "device_class": "gpu",
        "backend": "rocm",
        "intent": "Embeddings",
        "quant": "",
    },
    "rerank": {
        "image": DEFAULT_ROCMFPX_IMAGE,
        "flags": "--reranking -ngl 999 -fa on -b 8192 -ub 8192 --no-mmap",
        "mtp": False,
        "device_class": "gpu",
        "backend": "rocm",
        "intent": "Reranking",
        "quant": "",
    },
    "vulkan-embed": {
        "image": DEFAULT_ROCMFPX_IMAGE,
        "flags": "--embedding -ngl 999 -fa on -b 8192 -ub 8192 --no-mmap",
        "mtp": False,
        "device_class": "gpu",
        "backend": "vulkan",
        "intent": "Embeddings · Vulkan",
        "quant": "",
    },
    "vulkan-rerank": {
        "image": DEFAULT_ROCMFPX_IMAGE,
        "flags": "--reranking -ngl 999 -fa on -b 8192 -ub 8192 --no-mmap",
        "mtp": False,
        "device_class": "gpu",
        "backend": "vulkan",
        "intent": "Reranking · Vulkan",
        "quant": "",
    },
    "flm": {
        "image": "ghcr.io/hal0ai/hal0-toolbox-flm:0.9.44",
        "flags": "",
        "mtp": False,
        "device_class": "npu",
        "intent": "FLM · NPU",
        "quant": "W4ABF16",
    },
    "tts": {
        "image": "ghcr.io/hal0ai/hal0-toolbox-kokoro:v1",
        "flags": "--model_path /mnt/ai-models/local/kokoro-v1/kokoro-onnx",
        "mtp": False,
        "device_class": "cpu",
        "intent": "TTS · CPU",
        "quant": "",
    },
    "tts-qwen3": {
        "image": "ghcr.io/hal0ai/hal0-toolbox-qwen3tts:v1",
        "flags": (
            "--model_path /mnt/ai-models/local/qwen3-tts/Qwen3-TTS-12Hz-1.7B-CustomVoice "
            "--default_voice Ryan --default_language Auto"
        ),
        "mtp": False,
        "device_class": "gpu",
        "backend": "rocm",
        "intent": "TTS · GPU",
        "quant": "BF16",
    },
    "cpu-llm": {
        "image": FALLBACK_VULKAN_IMAGE,
        "flags": "--threads 4 --threads-batch 8 -b 256 -ub 256 --parallel 1 --jinja",
        "mtp": False,
        "device_class": "cpu",
        "intent": "CPU",
        "quant": "Q4_K_M",
    },
    "comfyui": {
        "image": "docker.io/kyuz0/amd-strix-halo-comfyui@sha256:0066678ae9043f69a1c8c7699e70626ceffd35c1a8ca03227a05640ad0241ed2",
        "flags": "--disable-mmap --bf16-vae --cache-none",
        "mtp": False,
        "device_class": "img",
        "intent": "ComfyUI",
        "quant": "",
    },
}

# ── Golden PROFILE_BENCH (pre-refactor schema.py, verbatim) ───────────────────

_GOLDEN_PROFILE_BENCH: dict[str, dict[str, float]] = {
    "rocm": {"tps": 52.8},
    "vulkan": {"tps": 41.0},
    "flm": {"tps": 38.6},
    "tts": {"rtf": 0.18},
    "tts-qwen3": {"rtf": 0.48},
}

# ── Golden FAMILY_DEFAULTS (pre-refactor schema.py, verbatim) ─────────────────

_GOLDEN_FAMILY_DEFAULTS: dict[str, str] = {
    "gemma": "-ctk f16 -ctv f16 --cache-reuse 0",
}


def _golden_embed_rerank_rows(device: str = "gpu-rocm") -> list[StackCapabilityRow]:
    return [
        StackCapabilityRow(
            child="embed",
            device=device,
            provider="llama-server",
            model="qwen3-embedding-0-6b-q8-0",
            enabled=True,
        ),
        StackCapabilityRow(
            child="rerank",
            device=device,
            provider="llama-server",
            model="bge-reranker-v2-m3-q4_k_m",
            enabled=True,
        ),
    ]


def _golden_seed_stacks() -> dict[str, StackConfig]:
    """Golden SEED_STACKS (pre-refactor schema.py, verbatim)."""
    return {
        "saber": StackConfig(
            name="Saber",
            description="High-speed agentic MoE: a 35B-A3B agent on ROCm with a fast "
            "Vulkan utility helper, plus memory recall.",
            author="hal0",
            icon="⚡",
            tags=["agentic", "moe", "fast"],
            slots=[
                StackSlotEntry(
                    slot="agent",
                    model="qwen3-6-35b-a3b-nsc-ace-saber-mtp-f16-to-rocmfp4-strix-lean",
                    device="gpu-rocm",
                    profile="rocm",
                    mtp=True,
                    capabilities=_golden_embed_rerank_rows(),
                ),
                StackSlotEntry(
                    slot="utility",
                    model="gemma-4-12b-it-ud-q4-k-xl",
                    device="gpu-vulkan",
                    profile="vulkan",
                ),
            ],
        ),
        "forge": StackConfig(
            name="Forge",
            description="Coding-first: a 27B coder agent on ROCm with a small fast "
            "draft coder as the utility, plus codebase retrieval.",
            author="hal0",
            icon="🛠️",
            tags=["coding", "developer"],
            slots=[
                StackSlotEntry(
                    slot="agent",
                    model="qwopus3-6-27b-coder-mtp-q6-k",
                    device="gpu-rocm",
                    profile="rocm",
                    mtp=True,
                    capabilities=_golden_embed_rerank_rows(),
                ),
                StackSlotEntry(
                    slot="utility",
                    model="qwopus3-5-4b-coder-mtp-q6-k",
                    device="gpu-vulkan",
                    profile="vulkan",
                    mtp=True,
                ),
            ],
        ),
        "pi": StackConfig(
            name="Pi",
            description="Always-on support: a q-rich 27B utility for faithful "
            "compaction and recall, with a light Vulkan agent.",
            author="hal0",
            icon="🥧",
            tags=["support", "memory", "compaction"],
            slots=[
                StackSlotEntry(
                    slot="utility",
                    model="chadrock3-6-27b-pi-agent-mtp-rocmfp4-strix-lean",
                    device="gpu-rocm",
                    profile="rocm",
                    mtp=True,
                    capabilities=_golden_embed_rerank_rows(),
                ),
                StackSlotEntry(
                    slot="agent",
                    model="gemma-4-12b-it-ud-q4-k-xl",
                    device="gpu-vulkan",
                    profile="vulkan",
                ),
            ],
        ),
    }


class TestSeedProfilesParity:
    def test_byte_identical_to_pre_refactor_dict(self) -> None:
        actual = seeds.seed_profiles()
        assert actual == _GOLDEN_SEED_PROFILES

    def test_same_keys_same_order_of_magnitude(self) -> None:
        # Belt-and-suspenders: a key-set diff is a much more readable pytest
        # failure than a giant dict diff when someone renames a slug.
        assert set(seeds.seed_profiles().keys()) == set(_GOLDEN_SEED_PROFILES.keys())


class TestSeedStacksParity:
    def test_byte_identical_to_pre_refactor_dict(self) -> None:
        actual = seeds.seed_stacks()
        golden = _golden_seed_stacks()
        assert actual == golden

    def test_same_slugs(self) -> None:
        assert set(seeds.seed_stacks().keys()) == set(_golden_seed_stacks().keys())


class TestProfileBenchParity:
    def test_byte_identical_to_pre_refactor_dict(self) -> None:
        assert seeds.profile_bench() == _GOLDEN_PROFILE_BENCH


class TestFamilyDefaultsParity:
    def test_byte_identical_to_pre_refactor_dict(self) -> None:
        assert seeds.family_defaults() == _GOLDEN_FAMILY_DEFAULTS
