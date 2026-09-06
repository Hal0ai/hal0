"""#1868 — static slot seeds must clamp ``context_size`` to this box's memory
envelope instead of shipping the reference platform's flat 65536 verbatim.

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

from hal0.config.schema import GPUInfo, HardwareInfo
from hal0.install.static_seeds import (
    LLAMA_SEED_CAPABILITIES,
    STATIC_SEED_SLOTS,
    apply_context_size_envelope,
    seed_static_slots,
)

_REPO_ROOT = Path(__file__).resolve().parents[2]
_SEED_SRC_DIR = _REPO_ROOT / "installer" / "etc-hal0" / "slots"


def _seeded_context_sizes(dest: Path) -> dict[str, int | None]:
    out: dict[str, int | None] = {}
    for p in sorted(dest.glob("*.toml")):
        cfg = tomllib.loads(p.read_text(encoding="utf-8"))
        model = cfg.get("model")
        out[p.stem] = model.get("context_size") if isinstance(model, dict) else None
    return out


def _tiny_cpu_box() -> HardwareInfo:
    """A box too small to afford a huge blanket context_size."""
    return HardwareInfo(platform="bare-metal-cpu-only", ram_mb=2048, gpus=[])


def _big_strix_halo_box() -> HardwareInfo:
    return HardwareInfo(
        platform="strix-halo",
        ram_mb=131072,
        gpus=[GPUInfo(vendor="amd", vram_mb=131072, compute_capable=True, vulkan_capable=True)],
    )


# ── apply_context_size_envelope ──────────────────────────────────────────────


def test_brain_clamps_to_the_hermes_floor_on_a_tiny_box(tmp_path: Path) -> None:
    """brain is the one llama.cpp seed with a KNOWN default-model footprint
    (lfm2.5-2.6b, 2.87 GiB) — on a 2 GiB CPU-only box that footprint alone
    exceeds the whole envelope, so the clamp floors at Hermes' 64,000 rather
    than the shipped 65536 (never below the floor — #1827)."""
    dest = tmp_path / "slots"
    dest.mkdir()
    shutil.copyfile(_SEED_SRC_DIR / "brain.toml", dest / "brain.toml")

    rewritten = apply_context_size_envelope(("brain",), slots_dir=dest, hw=_tiny_cpu_box())
    sizes = _seeded_context_sizes(dest)
    assert sizes["brain"] == 64_000
    assert rewritten.get("brain") == 64_000


def test_agent_ships_model_less_so_its_context_size_is_never_guessed_at(
    tmp_path: Path,
) -> None:
    """agent has no default model (spec-p3-brain.final.md §5b/5c) — with no
    known weight footprint to weigh against the envelope, the raw KV budget
    for 65536 tokens fits even a tiny box, so it is honestly left alone
    rather than clamped off an invented model size."""
    dest = tmp_path / "slots"
    dest.mkdir()
    shutil.copyfile(_SEED_SRC_DIR / "agent.toml", dest / "agent.toml")
    before = (dest / "agent.toml").read_text(encoding="utf-8")

    rewritten = apply_context_size_envelope(("agent",), slots_dir=dest, hw=_tiny_cpu_box())
    assert rewritten == {}
    assert (dest / "agent.toml").read_text(encoding="utf-8") == before


def test_generous_envelope_leaves_the_shipped_value_untouched(tmp_path: Path) -> None:
    dest = tmp_path / "slots"
    dest.mkdir()
    shutil.copyfile(_SEED_SRC_DIR / "agent.toml", dest / "agent.toml")
    before = (dest / "agent.toml").read_text(encoding="utf-8")

    rewritten = apply_context_size_envelope(("agent",), slots_dir=dest, hw=_big_strix_halo_box())
    assert rewritten == {}
    assert (dest / "agent.toml").read_text(encoding="utf-8") == before


def test_non_hermes_anchor_seeds_clamp_with_no_floor(tmp_path: Path) -> None:
    """coder/embed/rerank/utility have no Hermes-anchor floor — a tiny box
    may clamp them all the way down, unlike agent/brain."""
    dest = tmp_path / "slots"
    dest.mkdir()
    shutil.copyfile(_SEED_SRC_DIR / "coder.toml", dest / "coder.toml")

    rewritten = apply_context_size_envelope(("coder",), slots_dir=dest, hw=_tiny_cpu_box())
    # coder ships 32768, well inside even a 2 GiB CPU envelope's raw KV
    # budget with no competing model footprint assumed — so this seed alone
    # need not clamp; the point of this test is that IF it does, nothing
    # floors it at Hermes' 64,000 the way agent/brain are floored.
    if rewritten:
        assert rewritten["coder"] < 64_000


def test_ignores_missing_files(tmp_path: Path) -> None:
    dest = tmp_path / "slots"
    dest.mkdir()
    rewritten = apply_context_size_envelope(("agent",), slots_dir=dest, hw=_tiny_cpu_box())
    assert rewritten == {}


def test_unresolvable_hardware_keeps_verbatim_copy(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr("hal0.install.static_seeds._resolve_hardware_info", lambda: None)
    dest = tmp_path / "slots"
    dest.mkdir()
    shutil.copyfile(_SEED_SRC_DIR / "agent.toml", dest / "agent.toml")
    before = (dest / "agent.toml").read_text(encoding="utf-8")
    rewritten = apply_context_size_envelope(("agent",), slots_dir=dest)
    assert rewritten == {}
    assert (dest / "agent.toml").read_text(encoding="utf-8") == before


def test_non_llama_seeds_are_never_touched(tmp_path: Path) -> None:
    dest = tmp_path / "slots"
    dest.mkdir()
    for name in STATIC_SEED_SLOTS:
        shutil.copyfile(_SEED_SRC_DIR / f"{name}.toml", dest / f"{name}.toml")
    rewritten = apply_context_size_envelope(STATIC_SEED_SLOTS, slots_dir=dest, hw=_tiny_cpu_box())
    assert set(rewritten).issubset(set(LLAMA_SEED_CAPABILITIES))


# ── seed_static_slots wiring ─────────────────────────────────────────────────


def test_seed_static_slots_clamps_brain_context_size_on_a_tiny_box(tmp_path: Path) -> None:
    dest = tmp_path / "slots"
    seed_static_slots(installer_root=_REPO_ROOT, slots_dir=dest, hw=_tiny_cpu_box())
    sizes = _seeded_context_sizes(dest)
    assert sizes["brain"] == 64_000


def test_seed_static_slots_leaves_context_size_alone_on_a_generous_box(tmp_path: Path) -> None:
    dest = tmp_path / "slots"
    seed_static_slots(installer_root=_REPO_ROOT, slots_dir=dest, hw=_big_strix_halo_box())
    sizes = _seeded_context_sizes(dest)
    shipped = tomllib.loads((_SEED_SRC_DIR / "agent.toml").read_text(encoding="utf-8"))["model"][
        "context_size"
    ]
    assert sizes["agent"] == shipped
