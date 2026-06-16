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
            ),
            ModelVariant(
                "qwen-image",
                "bf16",
                None,
                359,
                "get_qwen_image.sh",
                "Qwen-Image-2512-BF16-20-Steps.json",
            ),
            ModelVariant(
                "sdxl", "fp16", "lightning-8step", 10, "get_sdxl.sh", "SDXL-Lightning-8step.json"
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
            ),
            ModelVariant(
                "qwen-image-edit",
                "bf16",
                None,
                667,
                "get_qwen_image.sh",
                "Qwen-Image-Edit-2511-BF16-20-Steps.json",
            ),
        ],
    ),
    "txt2video": Capability(
        id="txt2video",
        label="Text → Video",
        default_family="ltx2",
        alternatives=[
            ModelVariant("ltx2", "bf16", None, 615, "get_ltx2.sh", "LTX2-T2V-BF16.json"),
            ModelVariant(
                "hunyuan15",
                "fp16",
                "lightx2v-4step",
                929,
                "get_hunyuan15.sh",
                "Hunyuan-Video-1.5_720p_t2v-4-step-lora.json",
            ),
            ModelVariant(
                "wan22",
                "fp16",
                "seko-v2-4step",
                2007,
                "get_wan22.sh",
                "Wan2.2-T2V-A14B-FP16-4steps-lora-rank64-Seko-V2.json",
            ),
        ],
    ),
    "img2video": Capability(
        id="img2video",
        label="Image → Video",
        default_family="ltx2",
        alternatives=[
            ModelVariant("ltx2", "bf16", None, 616, "get_ltx2.sh", "LTX2-I2V-BF16.json"),
            ModelVariant(
                "hunyuan15",
                "fp16",
                "lightx2v-4step",
                947,
                "get_hunyuan15.sh",
                "Hunyuan-Video-1.5_720p_i2v-4-step-lora.json",
            ),
            ModelVariant(
                "wan22",
                "fp16",
                "seko-v1-4step",
                2029,
                "get_wan22.sh",
                "Wan2.2-I2V-A14B-4steps-lora-rank64-Seko-V1-FP16.json",
            ),
        ],
    ),
    "image_upscale": Capability(
        id="image_upscale",
        label="Upscale",
        default_family="esrgan",
        alternatives=[
            ModelVariant("esrgan", None, None, 10, "get_esrgan.sh", "ESRGAN-4x-Upscale.json"),
        ],
    ),
}
