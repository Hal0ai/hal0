"""#1868 — static slot seeds must clamp their context window to this box's memory
envelope instead of shipping the reference platform's flat 65536 verbatim.

ADR-0029: the window is the slot profile's ``ctx_size``. The clamp therefore
never edits the slot's window — it writes a custom profile ``<seed>-ctx<N>``
(a clone of the seed with the clamped ``ctx_size``) into ``profiles.toml`` and
points the slot's ``profile`` at it.

Companion to ``test_seed_device_derivation.py`` (#2023's device-derivation
pass): same two-pass shape (``seed_static_slots`` calls it after the copy
loop; ``install.sh``'s bash loop calls it via ``python -m
hal0.install.static_seeds`` over the names it just copied), different field.
"""

from __future__ import annotations

import shutil
import tomllib
from pathlib import Path

import pytest

from hal0.config.loader import load_profiles_config
from hal0.config.schema import GPUInfo, HardwareInfo, slot_tuning_keys
from hal0.install.static_seeds import (
    LLAMA_SEED_CAPABILITIES,
    STATIC_SEED_SLOTS,
    apply_context_size_envelope,
    clamped_profile_name,
    ensure_clamped_profile,
    seed_static_slots,
)

_REPO_ROOT = Path(__file__).resolve().parents[2]
_SEED_SRC_DIR = _REPO_ROOT / "installer" / "etc-hal0" / "slots"


def _slot_profile(dest: Path, name: str) -> str:
    return tomllib.loads((dest / f"{name}.toml").read_text(encoding="utf-8"))["profile"]


def _tiny_cpu_box() -> HardwareInfo:
    """A box too small to afford a huge blanket context window."""
    return HardwareInfo(platform="bare-metal-cpu-only", ram_mb=2048, gpus=[])


def _big_strix_halo_box() -> HardwareInfo:
    return HardwareInfo(
        platform="strix-halo",
        ram_mb=131072,
        gpus=[GPUInfo(vendor="amd", vram_mb=131072, compute_capable=True, vulkan_capable=True)],
    )


@pytest.fixture
def profiles_path(tmp_path: Path) -> Path:
    return tmp_path / "profiles.toml"


# ── apply_context_size_envelope ──────────────────────────────────────────────


def test_brain_clamps_to_the_hermes_floor_on_a_tiny_box(
    tmp_path: Path, profiles_path: Path
) -> None:
    """brain is the one llama.cpp seed with a KNOWN default-model footprint
    (lfm2.5-2.6b, 2.87 GiB) — on a 2 GiB CPU-only box that footprint alone
    exceeds the whole envelope, so the clamp floors at Hermes' 64,000 rather
    than the shipped 65536 (never below the floor — #1827). The slot is pointed
    at ``brain-ctx64000``, a clone of the ``brain`` seed with that window."""
    dest = tmp_path / "slots"
    dest.mkdir()
    shutil.copyfile(_SEED_SRC_DIR / "brain.toml", dest / "brain.toml")

    rewritten = apply_context_size_envelope(
        ("brain",), slots_dir=dest, hw=_tiny_cpu_box(), profiles_path=profiles_path
    )
    assert rewritten == {"brain": 64_000}

    assert _slot_profile(dest, "brain") == "brain-ctx64000"
    clone = load_profiles_config(profiles_path).profile["brain-ctx64000"]
    seed = load_profiles_config(profiles_path).profile["brain"]
    assert clone.ctx_size == 64_000
    assert clone.cloned_from == "brain"
    assert clone.intent == "Installer: brain clamped to 64000 for this box"
    # Everything else is the seed's tune, verbatim.
    assert (clone.flags, clone.vision, clone.speculation, clone.enable_thinking, clone.runner) == (
        seed.flags,
        seed.vision,
        seed.speculation,
        seed.enable_thinking,
        seed.runner,
    )
    assert seed.ctx_size == 65_536, "the seed itself is virtual and never rewritten"


def test_the_slot_file_never_gains_a_window_key(tmp_path: Path, profiles_path: Path) -> None:
    """ADR-0029: the clamp writes no ``[model].context_size`` (or any tuning key)."""
    dest = tmp_path / "slots"
    dest.mkdir()
    shutil.copyfile(_SEED_SRC_DIR / "brain.toml", dest / "brain.toml")
    apply_context_size_envelope(
        ("brain",), slots_dir=dest, hw=_tiny_cpu_box(), profiles_path=profiles_path
    )
    cfg = tomllib.loads((dest / "brain.toml").read_text(encoding="utf-8"))
    assert slot_tuning_keys(cfg) == []
    assert "context_size" not in cfg.get("model", {})
    assert "ctx_size" not in cfg


def test_only_the_profile_line_changes(tmp_path: Path, profiles_path: Path) -> None:
    dest = tmp_path / "slots"
    dest.mkdir()
    shutil.copyfile(_SEED_SRC_DIR / "brain.toml", dest / "brain.toml")
    before = (dest / "brain.toml").read_text(encoding="utf-8")
    apply_context_size_envelope(
        ("brain",), slots_dir=dest, hw=_tiny_cpu_box(), profiles_path=profiles_path
    )
    after = (dest / "brain.toml").read_text(encoding="utf-8")
    assert after == before.replace('profile = "brain"', 'profile = "brain-ctx64000"')


def test_agent_ships_model_less_so_its_window_is_never_guessed_at(
    tmp_path: Path, profiles_path: Path
) -> None:
    """agent has no default model (spec-p3-brain.final.md §5b/5c) — with no
    known weight footprint to weigh against the envelope, the raw KV budget
    for 65536 tokens fits even a tiny box, so it is honestly left alone
    rather than clamped off an invented model size."""
    dest = tmp_path / "slots"
    dest.mkdir()
    shutil.copyfile(_SEED_SRC_DIR / "agent.toml", dest / "agent.toml")
    before = (dest / "agent.toml").read_text(encoding="utf-8")

    rewritten = apply_context_size_envelope(
        ("agent",), slots_dir=dest, hw=_tiny_cpu_box(), profiles_path=profiles_path
    )
    assert rewritten == {}
    assert (dest / "agent.toml").read_text(encoding="utf-8") == before
    assert not profiles_path.exists(), "no clamp, no profile written"


def test_generous_envelope_leaves_the_shipped_profile_untouched(
    tmp_path: Path, profiles_path: Path
) -> None:
    dest = tmp_path / "slots"
    dest.mkdir()
    shutil.copyfile(_SEED_SRC_DIR / "agent.toml", dest / "agent.toml")
    before = (dest / "agent.toml").read_text(encoding="utf-8")

    rewritten = apply_context_size_envelope(
        ("agent",), slots_dir=dest, hw=_big_strix_halo_box(), profiles_path=profiles_path
    )
    assert rewritten == {}
    assert (dest / "agent.toml").read_text(encoding="utf-8") == before
    assert not profiles_path.exists()


def test_non_hermes_anchor_seeds_clamp_with_no_floor(tmp_path: Path, profiles_path: Path) -> None:
    """coder/embed/rerank/utility have no Hermes-anchor floor — a tiny box
    may clamp them all the way down, unlike agent/brain. The ``coding`` seed
    sets no ``ctx_size`` (model-native, capped at 32768), so it is clamped as if
    32768 were its window."""
    dest = tmp_path / "slots"
    dest.mkdir()
    shutil.copyfile(_SEED_SRC_DIR / "coder.toml", dest / "coder.toml")

    rewritten = apply_context_size_envelope(
        ("coder",), slots_dir=dest, hw=_tiny_cpu_box(), profiles_path=profiles_path
    )
    # 32768 tokens sit well inside even a 2 GiB CPU envelope's raw KV budget
    # with no competing model footprint assumed — so this seed alone need not
    # clamp; the point of this test is that IF it does, nothing floors it at
    # Hermes' 64,000 the way agent/brain are floored.
    if rewritten:
        assert rewritten["coder"] < 64_000
        assert _slot_profile(dest, "coder").startswith("coding-ctx")


def test_a_box_that_cannot_afford_a_small_window_clamps_it_too(
    tmp_path: Path, profiles_path: Path
) -> None:
    """The clamp applies to any window, not only the 64K ones: an envelope
    smaller than the embed seed's 4096 tokens writes an ``embedding-ctx<N>``."""
    from hal0.hardware.memory_envelope import max_affordable_context_tokens

    dest = tmp_path / "slots"
    dest.mkdir()
    shutil.copyfile(_SEED_SRC_DIR / "embed.toml", dest / "embed.toml")
    hw = HardwareInfo(platform="bare-metal-cpu-only", ram_mb=1, gpus=[])
    affordable = max_affordable_context_tokens(hw)
    rewritten = apply_context_size_envelope(
        ("embed",), slots_dir=dest, hw=hw, profiles_path=profiles_path
    )
    if affordable < 4096:
        assert rewritten == {"embed": affordable}
        assert _slot_profile(dest, "embed") == f"embedding-ctx{affordable}"
    else:
        assert rewritten == {}


def test_ignores_missing_files(tmp_path: Path, profiles_path: Path) -> None:
    dest = tmp_path / "slots"
    dest.mkdir()
    rewritten = apply_context_size_envelope(
        ("agent",), slots_dir=dest, hw=_tiny_cpu_box(), profiles_path=profiles_path
    )
    assert rewritten == {}


def test_a_slot_naming_an_unknown_profile_is_left_alone(
    tmp_path: Path, profiles_path: Path
) -> None:
    dest = tmp_path / "slots"
    dest.mkdir()
    (dest / "brain.toml").write_text('name = "brain"\nprofile = "no-such"\nport = 1\n')
    rewritten = apply_context_size_envelope(
        ("brain",), slots_dir=dest, hw=_tiny_cpu_box(), profiles_path=profiles_path
    )
    assert rewritten == {}
    assert _slot_profile(dest, "brain") == "no-such"


def test_unresolvable_hardware_keeps_verbatim_copy(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, profiles_path: Path
) -> None:
    monkeypatch.setattr("hal0.install.static_seeds._resolve_hardware_info", lambda: None)
    dest = tmp_path / "slots"
    dest.mkdir()
    shutil.copyfile(_SEED_SRC_DIR / "agent.toml", dest / "agent.toml")
    before = (dest / "agent.toml").read_text(encoding="utf-8")
    rewritten = apply_context_size_envelope(("agent",), slots_dir=dest, profiles_path=profiles_path)
    assert rewritten == {}
    assert (dest / "agent.toml").read_text(encoding="utf-8") == before


def test_non_llama_seeds_are_never_touched(tmp_path: Path, profiles_path: Path) -> None:
    dest = tmp_path / "slots"
    dest.mkdir()
    for name in STATIC_SEED_SLOTS:
        shutil.copyfile(_SEED_SRC_DIR / f"{name}.toml", dest / f"{name}.toml")
    rewritten = apply_context_size_envelope(
        STATIC_SEED_SLOTS, slots_dir=dest, hw=_tiny_cpu_box(), profiles_path=profiles_path
    )
    assert set(rewritten).issubset(set(LLAMA_SEED_CAPABILITIES))


# ── ensure_clamped_profile ───────────────────────────────────────────────────


def test_clamped_profile_name_is_deterministic_and_fits_the_name_limit() -> None:
    assert clamped_profile_name("agent", 32768) == "agent-ctx32768"
    long_name = clamped_profile_name("x" * 40, 123456)
    assert len(long_name) <= 32
    assert long_name.endswith("-ctx123456")


def test_ensure_clamped_profile_is_idempotent(profiles_path: Path) -> None:
    first = ensure_clamped_profile("agent", 32768, profiles_path=profiles_path)
    second = ensure_clamped_profile("agent", 32768, profiles_path=profiles_path)
    assert first == second == "agent-ctx32768"
    assert list(load_profiles_config(profiles_path).profile).count("agent-ctx32768") == 1


def test_ensure_clamped_profile_keeps_an_operator_edited_profile(profiles_path: Path) -> None:
    """A same-named profile the operator has since re-tuned is left alone: the
    new clone takes a numeric suffix rather than overwriting it."""
    from hal0.config.loader import save_profiles_config

    cfg = load_profiles_config(profiles_path)
    cfg.profile["agent-ctx32768"] = cfg.profile["agent"].model_copy(
        update={"ctx_size": 32768, "flags": "-fa on -b 512"}
    )
    save_profiles_config(cfg, profiles_path)

    name = ensure_clamped_profile("agent", 32768, profiles_path=profiles_path)
    assert name == "agent-ctx32768-2"
    stored = load_profiles_config(profiles_path).profile
    assert stored["agent-ctx32768"].flags == "-fa on -b 512"
    assert stored[name].flags == stored["agent"].flags


def test_ensure_clamped_profile_unknown_base_raises(profiles_path: Path) -> None:
    with pytest.raises(KeyError):
        ensure_clamped_profile("no-such-profile", 4096, profiles_path=profiles_path)


# ── seed_static_slots wiring ─────────────────────────────────────────────────


def test_seed_static_slots_clamps_the_brain_profile_on_a_tiny_box(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from hal0.config import paths

    home = tmp_path / "home"
    monkeypatch.setenv("HAL0_HOME", str(home))
    dest = tmp_path / "slots"
    seed_static_slots(installer_root=_REPO_ROOT, slots_dir=dest, hw=_tiny_cpu_box())
    assert _slot_profile(dest, "brain") == "brain-ctx64000"
    assert load_profiles_config(paths.profiles_toml()).profile["brain-ctx64000"].ctx_size == 64_000


def test_seed_static_slots_leaves_the_profile_alone_on_a_generous_box(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("HAL0_HOME", str(tmp_path / "home"))
    dest = tmp_path / "slots"
    seed_static_slots(installer_root=_REPO_ROOT, slots_dir=dest, hw=_big_strix_halo_box())
    assert _slot_profile(dest, "agent") == "agent"
    assert _slot_profile(dest, "brain") == "brain"
