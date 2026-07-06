"""Per-step context-pane copy (spec §6.1). Data only — no logic."""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class PaneCopy:
    headline: str
    body: str


PANE_COPY: dict[str, PaneCopy] = {
    "welcome": PaneCopy(
        "Welcome to hal0",
        "We detected your hardware and tuned the defaults on the left. Press Enter to continue.",
    ),
    "storage": PaneCopy(
        "Where models live",
        "Downloaded models are stored here. Pick a disk with room — chat models run 2-30 GB each.",
    ),
    "extensions": PaneCopy(
        "One-shot perfection",
        "Every app and agent you pick is automagically wired into the hal0 "
        "platform during install — base URLs, routing, and credentials "
        "configured for you. No glue code, no post-install fiddling.",
    ),
    "main": PaneCopy(
        "Your Main model",
        "The primary model every app and agent routes to (hal0/chat). "
        "We recommend the largest pick that fits your memory.",
    ),
    "agent": PaneCopy(
        "The Agent model",
        "Powers your coding/agent extensions. Pick a coder model, reuse your Main model, or skip.",
    ),
    "npu": PaneCopy(
        "Free up your GPU",
        "Your NPU can run embeddings, speech-to-text, and text-to-speech in "
        "parallel — leaving the GPU for chat. Recommended when present.",
    ),
    "npu_broken": PaneCopy(
        "NPU needs attention",
        "An NPU was detected but `flm validate` failed, so we're leaving it "
        "off — enabling it would create slots that never start. Usual causes: "
        "the amdxdna accel device isn't passed through to this container, or "
        "libxrt-npu2 doesn't match the host driver. Fix the passthrough / "
        "driver, then re-run `hal0 setup` to enable the NPU.",
    ),
    "capabilities": PaneCopy(
        "Capability slots",
        "Wire up embeddings, rerank, speech, and vision. For each, pick a "
        "fitting model, scaffold the slot empty to choose a model later, or "
        "skip it — we never choose a model for you.",
    ),
    "review": PaneCopy(
        "Ready to build",
        "Here's exactly what will be created and wired. Nothing has been written yet.",
    ),
    "install": PaneCopy(
        "Building your hal0",
        "Slots are created instantly; models download in the background — you "
        "can start chatting as soon as the Main model lands.",
    ),
}
