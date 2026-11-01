"""``HalogenProvider.footprint_mb``: argument gathering for the halogen estimator.

``hal0.slots.capacity.halogen_footprint_mb`` is the arithmetic (ADR-0039); the
provider hook only reads the checkpoint size, the profile's flags and the slot's
effective tune, and hands them over. The tests compare the hook against the
estimator called with the arguments each case must produce, so the arithmetic
is pinned once, in ``tests/slots/test_capacity_halogen.py``.
"""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

from hal0.providers.container import resolve_named_profile
from hal0.providers.halogen import VISION_SIDECAR, HalogenProvider
from hal0.slots.capacity import halogen_footprint_mb, provider_footprint_mb

GIB = 1024.0  # MiB per GiB: the estimator returns MiB
CHECKPOINT_BYTES = int(53.7 * 2**30)  # the ht43 checkpoint
POOL_262144 = "HALOGEN_KV_POOL_POSITIONS=262144"
POOL_131072 = "HALOGEN_KV_POOL_POSITIONS=131072"
SEED_FLAGS = f"{POOL_262144} HALOGEN_KV_SLOTS=2 HALOGEN_MAX_TOK=16384 HALOGEN_WEIGHTS_LOCK=1"


def _cfg(**overrides: Any) -> dict[str, Any]:
    base: dict[str, Any] = {
        "name": "flash",
        "port": 8099,
        "device": "gpu-rocm",
        "type": "llm",
        "runtime": "container",
        "profile": "halogen",
        "model": {"default": "qwen3.8-flash-next-ht43"},
    }
    base.update(overrides)
    return base


def _info(tmp_path: Path, **overrides: Any) -> dict[str, Any]:
    """A registry-shaped halogen row whose checkpoint sits in ``tmp_path``."""
    base: dict[str, Any] = {
        "_model_key": "qwen3.8-flash-next-ht43",
        "path": str(tmp_path / "qwen38-flash-next-ht43.hgn"),
        "size_bytes": CHECKPOINT_BYTES,
        "backends": ["halogen"],
        "provider": "halogen",
    }
    base.update(overrides)
    return base


def _profile(flags: str = SEED_FLAGS, **fields: Any) -> SimpleNamespace:
    """A resolved profile as the hook receives it (the tune is not merged yet)."""
    values: dict[str, Any] = {
        "name": "halogen",
        "runner": "halogen",
        "flags": flags,
        "ctx_size": 262144,
        "vision": "auto",
    }
    values.update(fields)
    return SimpleNamespace(**values)


def _expected(**over: Any) -> float:
    """What the estimator says for the ht43 checkpoint, seed layout unless overridden."""
    args: dict[str, Any] = {
        "checkpoint_bytes": CHECKPOINT_BYTES,
        "pool_positions": 262144,
        "kv_slots": 2,
        "max_tok": 16384,
        "kv8": False,
        "vision": False,
    }
    args.update(over)
    return halogen_footprint_mb(**args)


def _footprint(
    tmp_path: Path,
    *,
    profile: Any = None,
    cfg: dict[str, Any] | None = None,
    info: dict[str, Any] | None = None,
) -> float | None:
    return HalogenProvider().footprint_mb(
        _info(tmp_path) if info is None else info,
        _cfg() if cfg is None else cfg,
        _profile() if profile is None else profile,
    )


def _with_sidecar(tmp_path: Path) -> None:
    (tmp_path / VISION_SIDECAR).write_bytes(b"HGN1")


# ── the seed layout ─────────────────────────────────────────────────────────


def test_seed_profile_defaults_give_the_estimators_ht43_figure(
    tmp_hal0_home: str, tmp_path: Path
) -> None:
    """Through the real seeded profile: no sidecar beside the checkpoint, so no tower."""
    profile = resolve_named_profile(_cfg(), "halogen")
    mb = _footprint(tmp_path, profile=profile)
    assert mb == pytest.approx(_expected())
    assert mb / GIB == pytest.approx(72.0, abs=1.0)  # the vendor-confirmed ht43 figure


def test_knob_defaults_fill_a_profile_with_no_flags(tmp_path: Path) -> None:
    """No flags: the knob defaults (262144 positions, 2 slots, 16384 chunk, no KV8)."""
    mb = _footprint(tmp_path, profile=_profile(flags="", ctx_size=None))
    assert mb == pytest.approx(_expected())


def test_the_capacity_dispatcher_reaches_the_hook(tmp_hal0_home: str, tmp_path: Path) -> None:
    """The real seam both sizing call sites use: slot cfg -> provider -> profile -> hook."""
    assert provider_footprint_mb(_cfg(), _info(tmp_path)) == pytest.approx(_expected(), abs=0.05)


# ── pool, ctx and the max rule ──────────────────────────────────────────────


def test_a_smaller_profile_pool_lowers_the_figure(tmp_path: Path) -> None:
    """The profile flag moves the pool when the window does not hold it up."""
    profile = _profile(flags=SEED_FLAGS.replace(POOL_262144, POOL_131072), ctx_size=65536)
    mb = _footprint(tmp_path, profile=profile)
    assert mb == pytest.approx(_expected(pool_positions=131072))
    assert mb < _expected()


def test_provider_footprint_uses_max_of_pool_and_ctx(tmp_path: Path) -> None:
    """The spec's ``max(pool, ctx)``: a 131072 pool under a 262144 window books 262144."""
    profile = _profile(flags=SEED_FLAGS.replace(POOL_262144, POOL_131072), ctx_size=262144)
    assert _footprint(tmp_path, profile=profile) == pytest.approx(_expected(pool_positions=262144))


def test_the_slot_tune_window_beats_the_profile_window(tmp_path: Path) -> None:
    """The slot's own ``[tune] ctx_size`` is part of the effective tune the hook reads."""
    profile = _profile(flags=SEED_FLAGS.replace(POOL_262144, POOL_131072), ctx_size=65536)
    cfg = _cfg(tune={"ctx_size": 200000})
    assert _footprint(tmp_path, profile=profile, cfg=cfg) == pytest.approx(
        _expected(pool_positions=200000)
    )


def test_other_knobs_come_from_the_profile_flags(tmp_path: Path) -> None:
    flags = "HALOGEN_KV_SLOTS=4 HALOGEN_MAX_TOK=32768 HALOGEN_KV8=1"
    mb = _footprint(tmp_path, profile=_profile(flags=flags, ctx_size=None))
    assert mb == pytest.approx(_expected(kv_slots=4, max_tok=32768, kv8=True))


# ── vision ──────────────────────────────────────────────────────────────────


def test_vision_is_counted_only_with_the_sidecar_beside_the_checkpoint(tmp_path: Path) -> None:
    without = _footprint(tmp_path)
    assert without == pytest.approx(_expected(vision=False))
    _with_sidecar(tmp_path)
    with_tower = _footprint(tmp_path)
    assert with_tower == pytest.approx(_expected(vision=True))
    assert with_tower is not None and without is not None
    assert with_tower - without == pytest.approx(0.84 * GIB, abs=0.1)


def test_vision_off_ignores_the_sidecar(tmp_path: Path) -> None:
    _with_sidecar(tmp_path)
    assert _footprint(tmp_path, profile=_profile(vision="off")) == pytest.approx(
        _expected(vision=False)
    )


def test_a_slot_tune_can_turn_vision_off(tmp_path: Path) -> None:
    _with_sidecar(tmp_path)
    cfg = _cfg(tune={"vision": "off"})
    assert _footprint(tmp_path, cfg=cfg) == pytest.approx(_expected(vision=False))


# ── no figure, never a raise ────────────────────────────────────────────────


def test_a_model_that_is_not_a_halogen_checkpoint_gets_no_figure(tmp_path: Path) -> None:
    gguf = {
        "_model_key": "qwen3-4b-q4_k_m",
        "path": str(tmp_path / "qwen3-4b-q4_k_m.gguf"),
        "size_bytes": 2 * 2**30,
        "backends": [],
        "provider": "llama-server",
    }
    assert _footprint(tmp_path, info=gguf) is None


@pytest.mark.parametrize("size", [0, None, "unknown"])
def test_a_checkpoint_of_unknown_size_gets_no_figure(tmp_path: Path, size: Any) -> None:
    """A figure built on a zero would book the engine as nearly free."""
    assert _footprint(tmp_path, info=_info(tmp_path, size_bytes=size)) is None


@pytest.mark.parametrize(
    "flags",
    [
        "HALOGEN_KV_POOL_POSITIONS=lots",
        "HALOGEN_KV_POOL_POSITIONS=0",
        "HALOGEN_KV_POOL_POSITIONS=-5",
        "HALOGEN_KV_SLOTS=1.5 HALOGEN_MAX_TOK=999 HALOGEN_KV8=maybe",
        "HALOGEN_KV_SLOTS=99999999999999999999",
        'HALOGEN_KV_SLOTS="unterminated',
        "--ctx-size 4096",
    ],
)
def test_a_malformed_flag_falls_back_to_the_knob_default(tmp_path: Path, flags: str) -> None:
    """Odd values read as the knob default; the launch path reports the real error."""
    mb = _footprint(tmp_path, profile=_profile(flags=flags, ctx_size=None))
    assert mb == pytest.approx(_expected())


def test_a_profile_without_attributes_still_gets_a_figure(tmp_path: Path) -> None:
    assert _footprint(tmp_path, profile=SimpleNamespace()) == pytest.approx(_expected())


def test_a_malformed_slot_tune_does_not_raise(tmp_path: Path) -> None:
    cfg = _cfg(tune={"ctx_size": "huge", "vision": 7})
    assert _footprint(tmp_path, cfg=cfg) is not None
