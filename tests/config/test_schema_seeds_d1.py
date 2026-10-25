"""Tests for Phase D1 — comfyui seed profile, img slot seed, [image] section (#599)."""

import tomllib
from pathlib import Path

import pytest
from pydantic import ValidationError

from hal0.config.loader import ConfigParseError, load_slot_config, save_slot_config
from hal0.config.schema import SEED_PROFILES, ImageGenConfig, SlotConfig

_REPO_ROOT = Path(__file__).resolve().parents[2]
_SEEDED_SLOTS_DIR = _REPO_ROOT / "installer" / "etc-hal0" / "slots"


def test_comfyui_seed_profile() -> None:
    p = SEED_PROFILES["comfyui"]
    assert "device_class" not in p
    assert "--disable-mmap" in p["flags"]


def test_seed_img_toml_validates() -> None:
    raw = tomllib.loads((_SEEDED_SLOTS_DIR / "img.toml").read_text(encoding="utf-8"))
    cfg = SlotConfig.model_validate(raw)
    assert cfg.runtime == "container"
    assert cfg.profile == "comfyui"
    assert cfg.provider == "comfyui"
    assert cfg.port == 8188
    assert cfg.device == "gpu-rocm"
    assert (cfg.image_gen.default_size, cfg.image_gen.default_steps) == ("", 0)  # #599 [image]
    assert "idle_restore_minutes" not in raw["image"]


def test_seed_profiles_toml_materialises_no_seeds() -> None:
    """Seeds are virtual — the shipped installer file must NOT materialise any.

    A re-tuned seed (new flags / bumped image) must reach every install via the
    code overlay, not be frozen by a stale on-disk copy. The reference file is
    documentation + a home for operator custom profiles only, so it defines no
    ``[profile.*]`` tables (and certainly none named after a seed).
    """
    raw = tomllib.loads(
        (_REPO_ROOT / "installer" / "etc-hal0" / "profiles.toml").read_text(encoding="utf-8")
    )
    on_disk = raw.get("profile", {})
    assert set(on_disk).isdisjoint(SEED_PROFILES), (
        f"installer profiles.toml materialises seed profiles: {set(on_disk) & set(SEED_PROFILES)}"
    )


def test_port_range_admits_comfyui_stock_port() -> None:
    # ComfyUI's stock port 8188 sits above the historical 8081-8099 slot
    # range — _SLOT_PORT_MAX must admit it without ValidationError.
    SlotConfig(name="x", port=8188)


def test_image_gen_section_defaults() -> None:
    # An empty default_size is "the model's native size": the render path falls through to it.
    s = ImageGenConfig()
    assert (s.default_size, s.default_steps) == ("", 0)


def test_bare_string_image_is_refused() -> None:
    # The top-level string `image` (the old per-slot container-image override)
    # is gone: `image_pin` replaced it and the profile-centric conversion
    # rewrites it. `image` is the [image] settings table, so a string fails the
    # strict schema naming the key (ADR-0029 §5).
    with pytest.raises(ValidationError) as exc:
        SlotConfig.model_validate({"name": "x", "port": 8081, "image": "ghcr.io/foo:v1"})
    assert exc.value.errors()[0]["loc"] == ("image",)
    # The replacement is a declared field.
    assert SlotConfig(name="x", port=8081, image_pin="ghcr.io/foo:v1").image_pin == (
        "ghcr.io/foo:v1"
    )


def test_load_slot_config_populates_image_gen(tmp_path: Path) -> None:
    # Real loader path: _flatten_slot_toml hands the [image] table to the
    # typed field.
    p = tmp_path / "img.toml"
    p.write_text(
        "[slot]\n"
        'name = "img"\n'
        "port = 8188\n"
        "\n"
        "[image]\n"
        'default_size = "512x512"\n'
        "default_steps = 12\n",
        encoding="utf-8",
    )
    cfg = load_slot_config("img", path=p)
    assert cfg.image_gen.default_size == "512x512"
    assert cfg.image_gen.default_steps == 12


def test_save_load_round_trip_preserves_image_gen(tmp_path: Path) -> None:
    src = tmp_path / "img.toml"
    src.write_text(
        '[slot]\nname = "img"\nport = 8188\n\n[image]\ndefault_steps = 9\n',
        encoding="utf-8",
    )
    cfg = load_slot_config("img", path=src)
    dst = tmp_path / "img-rt.toml"
    save_slot_config(cfg, path=dst)
    reloaded = load_slot_config("img", path=dst)
    assert reloaded.image_gen.default_steps == 9
    assert reloaded.image_gen == cfg.image_gen


def test_load_slot_config_refuses_a_string_image_naming_image_pin(tmp_path: Path) -> None:
    p = tmp_path / "agent.toml"
    p.write_text(
        '[slot]\nname = "agent"\nport = 8085\nimage = "ghcr.io/foo:v1"\n',
        encoding="utf-8",
    )
    with pytest.raises(ConfigParseError) as exc:
        load_slot_config("agent", path=p)
    assert "'image'" in str(exc.value)
    assert "image_pin" in str(exc.value)


def test_manifest_comfyui_pin_is_hal0s_own_runner_image() -> None:
    from hal0.runners.pins import load_runner_pins

    pin = load_runner_pins(_REPO_ROOT / "manifest.json")["comfyui"]
    assert pin.image == "ghcr.io/hal0ai/hal0-runner-comfyui"
    # A published pin is digest-pinned: the ref is repo:tag@digest.
    if pin.published:
        assert pin.digest is not None
        assert pin.ref == f"{pin.image}:{pin.tag}@{pin.digest}"
