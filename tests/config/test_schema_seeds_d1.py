"""Tests for Phase D1 — comfyui seed profile, img slot seed, [image] section (#599)."""

import tomllib
from pathlib import Path

from hal0.config.loader import load_manifest, manifest_image_ref
from hal0.config.schema import SEED_PROFILES, ImageGenConfig, SlotConfig

_REPO_ROOT = Path(__file__).resolve().parents[2]
_SEEDED_SLOTS_DIR = _REPO_ROOT / "installer" / "etc-hal0" / "slots"


def test_comfyui_seed_profile() -> None:
    p = SEED_PROFILES["comfyui"]
    assert p["device_class"] == "img"
    assert "kyuz0/amd-strix-halo-comfyui" in p["image"]


def test_seed_img_toml_validates() -> None:
    raw = tomllib.loads((_SEEDED_SLOTS_DIR / "img.toml").read_text(encoding="utf-8"))
    cfg = SlotConfig.model_validate(raw)
    assert cfg.runtime == "container"
    assert cfg.profile == "comfyui"
    assert cfg.provider == "comfyui"
    assert cfg.port == 8188
    assert cfg.device == "gpu-rocm"
    assert cfg.image_gen.idle_restore_minutes == 5  # #599 [image] section


def test_seed_profiles_toml_has_comfyui_parity() -> None:
    raw = tomllib.loads(
        (_REPO_ROOT / "installer" / "etc-hal0" / "profiles.toml").read_text(encoding="utf-8")
    )
    assert raw["profile"]["comfyui"] == SEED_PROFILES["comfyui"]


def test_port_range_admits_comfyui_stock_port() -> None:
    # ComfyUI's stock port 8188 sits above the historical 8081-8099 slot
    # range — _SLOT_PORT_MAX must admit it without ValidationError.
    SlotConfig(name="x", port=8188)


def test_image_gen_section_defaults() -> None:
    s = ImageGenConfig()
    assert (s.idle_restore_minutes, s.default_size, s.default_steps) == (5, "1024x1024", 0)


def test_manifest_comfyui_pinned_to_kyuz0() -> None:
    manifest = load_manifest(_REPO_ROOT / "manifest.json")
    ref = manifest_image_ref("comfyui", manifest=manifest)
    assert ref == (
        "docker.io/kyuz0/amd-strix-halo-comfyui"
        "@sha256:0066678ae9043f69a1c8c7699e70626ceffd35c1a8ca03227a05640ad0241ed2"
    )
