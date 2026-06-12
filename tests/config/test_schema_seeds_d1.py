"""Tests for Phase D1 — comfyui seed profile, img slot seed, [image] section (#599)."""

import tomllib
from pathlib import Path

from hal0.config.loader import (
    load_manifest,
    load_slot_config,
    manifest_image_ref,
    save_slot_config,
)
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
    assert cfg.image_gen.idle_restore_minutes == 60  # #599 [image] section


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
    assert (s.idle_restore_minutes, s.default_size, s.default_steps) == (60, "1024x1024", 0)


def test_string_image_override_still_validates_and_round_trips() -> None:
    # Top-level string `image` is the documented per-slot container-image
    # override (llama_server.image_ref / comfyui.image_ref read
    # slot_cfg["image"]). It must not collide with the [image] alias:
    # pre-D1 it passed via extra="allow"; post-D1 it parks under extra.
    cfg = SlotConfig.model_validate({"name": "x", "port": 8081, "image": "ghcr.io/foo:v1"})
    assert cfg.extra["image"] == "ghcr.io/foo:v1"
    # image_gen stays at defaults — the string never reaches the alias.
    assert cfg.image_gen == ImageGenConfig()
    # Round-trip: providers read the override from the dumped config.
    dumped = cfg.model_dump(mode="python")
    assert dumped["extra"]["image"] == "ghcr.io/foo:v1"
    assert "image_gen" not in dumped  # all-defaults ImageGenConfig elided


def test_load_slot_config_populates_image_gen(tmp_path: Path) -> None:
    # Real loader path: [image] travels via _flatten_slot_toml's extra
    # catch-all and must be hoisted into the typed field.
    p = tmp_path / "img.toml"
    p.write_text(
        "[slot]\n"
        'name = "img"\n'
        "port = 8188\n"
        "\n"
        "[image]\n"
        "idle_restore_minutes = 9\n"
        'default_size = "512x512"\n'
        "default_steps = 12\n",
        encoding="utf-8",
    )
    cfg = load_slot_config("img", path=p)
    assert cfg.image_gen.idle_restore_minutes == 9
    assert cfg.image_gen.default_size == "512x512"
    assert cfg.image_gen.default_steps == 12


def test_save_load_round_trip_preserves_image_gen(tmp_path: Path) -> None:
    src = tmp_path / "img.toml"
    src.write_text(
        '[slot]\nname = "img"\nport = 8188\n\n[image]\nidle_restore_minutes = 9\n',
        encoding="utf-8",
    )
    cfg = load_slot_config("img", path=src)
    dst = tmp_path / "img-rt.toml"
    save_slot_config(cfg, path=dst)
    reloaded = load_slot_config("img", path=dst)
    assert reloaded.image_gen.idle_restore_minutes == 9
    assert reloaded.image_gen == cfg.image_gen


def test_load_slot_config_string_image_survives_in_extra(tmp_path: Path) -> None:
    # String `image` key under [slot] via the real loader: _flatten hoists
    # it to top level, the collision guard parks it under extra["image"]
    # (no hoist into image_gen, no ValidationError) — and it survives a
    # save→reload round-trip for providers to read.
    p = tmp_path / "agent.toml"
    p.write_text(
        '[slot]\nname = "agent"\nport = 8085\nimage = "ghcr.io/foo:v1"\n',
        encoding="utf-8",
    )
    cfg = load_slot_config("agent", path=p)
    assert cfg.extra["image"] == "ghcr.io/foo:v1"
    assert cfg.image_gen == ImageGenConfig()
    dst = tmp_path / "agent-rt.toml"
    save_slot_config(cfg, path=dst)
    reloaded = load_slot_config("agent", path=dst)
    assert reloaded.extra["image"] == "ghcr.io/foo:v1"


def test_manifest_comfyui_pinned_to_kyuz0() -> None:
    manifest = load_manifest(_REPO_ROOT / "manifest.json")
    ref = manifest_image_ref("comfyui", manifest=manifest)
    assert ref == (
        "docker.io/kyuz0/amd-strix-halo-comfyui"
        "@sha256:0066678ae9043f69a1c8c7699e70626ceffd35c1a8ca03227a05640ad0241ed2"
    )
