"""ComfyUI capability registry — Task 2.2."""

from __future__ import annotations

from dataclasses import dataclass, field


@dataclass
class ModelVariant:
    family: str
    precision: str | None
    lora: str | None
    est_seconds: int
    fetch_script: str
    workflow: str
    # Ordered argv sequences for the fetch script.
    # Each inner tuple = positional/flag args for ONE subprocess invocation.
    # Empty tuple = one call with no extra args (e.g. get_esrgan.sh).
    fetch_steps: tuple[tuple[str, ...], ...] = field(default_factory=tuple)


@dataclass
class Capability:
    id: str
    label: str
    default_family: str
    alternatives: list[ModelVariant] = field(default_factory=list)


def default_variant(cap: str | Capability) -> ModelVariant:
    """Return the default (first) variant for a capability id or Capability."""
    if isinstance(cap, str):
        cap = CAPABILITIES[cap]
    return cap.alternatives[0]


CAPABILITIES: dict[str, Capability] = {
    "txt2img": Capability(
        id="txt2img",
        label="Text → Image",
        default_family="qwen-image",
        alternatives=[
            ModelVariant(
                "qwen-image",
                "bf16",
                "lightning-4step",
                75,
                "get_qwen_image.sh",
                "Qwen-Image-2512-BF16-4-Step-LoRA.json",
                fetch_steps=(("1", "bf16"), ("3", "bf16")),
            ),
            ModelVariant(
                "qwen-image",
                "bf16",
                None,
                359,
                "get_qwen_image.sh",
                "Qwen-Image-2512-BF16-20-Steps.json",
                fetch_steps=(("1", "bf16"),),
            ),
            ModelVariant(
                "sdxl",
                "fp16",
                "lightning-8step",
                10,
                "get_sdxl.sh",
                "SDXL-Lightning-8step.json",
                fetch_steps=(("--precision", "fp16"),),
            ),
        ],
    ),
    "img2img": Capability(
        id="img2img",
        label="Image Edit",
        default_family="qwen-image-edit",
        alternatives=[
            ModelVariant(
                "qwen-image-edit",
                "bf16",
                "lightning-4step",
                113,
                "get_qwen_image.sh",
                "Qwen-Image-Edit-2511-BF16-4-Step-LoRA.json",
                fetch_steps=(("2", "bf16"), ("4", "bf16")),
            ),
            ModelVariant(
                "qwen-image-edit",
                "bf16",
                None,
                667,
                "get_qwen_image.sh",
                "Qwen-Image-Edit-2511-BF16-20-Steps.json",
                fetch_steps=(("2", "bf16"),),
            ),
        ],
    ),
    "txt2video": Capability(
        id="txt2video",
        label="Text → Video",
        default_family="ltx2",
        alternatives=[
            ModelVariant(
                "ltx2",
                "bf16",
                None,
                615,
                "get_ltx2.sh",
                "LTX2-T2V-BF16.json",
                fetch_steps=(("common",), ("checkpoint", "bf16"), ("lora",)),
            ),
            ModelVariant(
                "hunyuan15",
                "fp16",
                "lightx2v-4step",
                929,
                "get_hunyuan15.sh",
                "Hunyuan-Video-1.5_720p_t2v-4-step-lora.json",
                fetch_steps=(("common",), ("720p-t2v",), ("lora",)),
            ),
            ModelVariant(
                "wan22",
                "fp16",
                "seko-v2-4step",
                2007,
                "get_wan22.sh",
                "Wan2.2-T2V-A14B-FP16-4steps-lora-rank64-Seko-V2.json",
                fetch_steps=(("common", "fp16"), ("14b-t2v", "fp16"), ("lora",)),
            ),
        ],
    ),
    "img2video": Capability(
        id="img2video",
        label="Image → Video",
        default_family="ltx2",
        alternatives=[
            ModelVariant(
                "ltx2",
                "bf16",
                None,
                616,
                "get_ltx2.sh",
                "LTX2-I2V-BF16.json",
                fetch_steps=(("common",), ("checkpoint", "bf16"), ("lora",)),
            ),
            ModelVariant(
                "hunyuan15",
                "fp16",
                "lightx2v-4step",
                947,
                "get_hunyuan15.sh",
                "Hunyuan-Video-1.5_720p_i2v-4-step-lora.json",
                fetch_steps=(("common",), ("720p-i2v",), ("lora",)),
            ),
            ModelVariant(
                "wan22",
                "fp16",
                "seko-v1-4step",
                2029,
                "get_wan22.sh",
                "Wan2.2-I2V-A14B-4steps-lora-rank64-Seko-V1-FP16.json",
                fetch_steps=(("common", "fp16"), ("14b-i2v", "fp16"), ("lora",)),
            ),
        ],
    ),
    "image_upscale": Capability(
        id="image_upscale",
        label="Upscale",
        default_family="esrgan",
        alternatives=[
            ModelVariant(
                "esrgan",
                None,
                None,
                10,
                "get_esrgan.sh",
                "ESRGAN-4x-Upscale.json",
                fetch_steps=((),),
            ),
        ],
    ),
}
