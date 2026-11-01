"""halogen footprint estimator and the provider hook in the capacity path.

``hal0.slots.capacity.halogen_footprint_mb`` is the arithmetic (ADR-0039: memory
figures have one owner); ``Provider.footprint_mb`` is the thin hook that lets a
slot's provider supply its own figure; ``build_per_slot`` asks the provider
first and falls back to the file-size + KV estimate when it has none.

The eviction-side call site (``preload_evict._estimate_incoming_footprint_mb``)
is covered in ``test_preload_eviction.py``.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from hal0.slots.capacity import (
    HALOGEN_TABLE_CACHE_ADVISORY_MB,
    build_per_slot,
    halogen_footprint_mb,
)

GIB = 1024.0  # MiB per GiB: ``halogen_footprint_mb`` returns MiB


def _footprint(**over: Any) -> float:
    args: dict[str, Any] = {
        "checkpoint_bytes": 0,
        "pool_positions": 0,
        "kv_slots": 0,
        "max_tok": 0,
        "kv8": False,
        "vision": False,
    }
    args.update(over)
    return halogen_footprint_mb(**args)


# ── the estimator ────────────────────────────────────────────────────────


def test_halogen_footprint_seed_layout_ht43() -> None:
    mb = halogen_footprint_mb(
        checkpoint_bytes=int(53.7 * 2**30),
        pool_positions=262144,
        kv_slots=2,
        max_tok=16384,
        kv8=False,
        vision=False,
    )
    assert abs(mb / GIB - 72.0) < 1.0


def test_halogen_footprint_vendor_rows_within_5_gib() -> None:
    # docs/host-setup.md "If you must share it", v2 checkpoint.
    v2 = int(62.1 * 2**30)
    rows = [((524288, 4, 32768), 89), ((262144, 2, 32768), 82), ((262144, 2, 16384), 77)]
    for (pool, slots, mt), vendor in rows:
        mb = halogen_footprint_mb(
            checkpoint_bytes=v2,
            pool_positions=pool,
            kv_slots=slots,
            max_tok=mt,
            kv8=False,
            vision=False,
        )
        assert abs(mb / GIB - vendor) < 5.0


def test_kv8_halves_pool_and_tower_adds() -> None:
    base = _footprint(pool_positions=524288, kv_slots=1, max_tok=16384)
    kv8 = _footprint(pool_positions=524288, kv_slots=1, max_tok=16384, kv8=True)
    assert (base - kv8) / GIB == pytest.approx(524288 * (29500 - 16712) / 2**30, abs=0.1)

    no_tower = _footprint(pool_positions=1024, kv_slots=1, max_tok=16384)
    tower = _footprint(pool_positions=1024, kv_slots=1, max_tok=16384, vision=True)
    assert tower - no_tower == pytest.approx(0.84 * 1024, abs=0.1)


def test_table_page_cache_is_an_advisory_never_counted_as_resident() -> None:
    assert HALOGEN_TABLE_CACHE_ADVISORY_MB == 10 * 1024
    # Nothing but working memory (3.8 GiB at max_tok 0) and the default 24
    # prompt-cache entries of 115 MiB: no 10 GiB table cache in the sum.
    assert _footprint() == pytest.approx(3.8 * 1024 + 24 * 115, abs=0.1)
    assert _footprint(cache_entries=0) == pytest.approx(3.8 * 1024, abs=0.1)


def test_working_memory_scales_with_max_tok() -> None:
    small = _footprint(max_tok=0, cache_entries=0)
    big = _footprint(max_tok=32768, cache_entries=0)
    assert (big - small) / GIB == pytest.approx(8.8, abs=0.01)


def test_each_kv_slot_adds_its_fixed_state() -> None:
    one = _footprint(kv_slots=1)
    three = _footprint(kv_slots=3)
    assert three - one == pytest.approx(2 * 120_586_240 / 2**20, abs=0.01)


# ── the Provider hook ────────────────────────────────────────────────────


def test_base_provider_footprint_hook_returns_none() -> None:
    from hal0.providers.kokoro import KokoroProvider

    assert KokoroProvider().footprint_mb({"size_bytes": 1}, {"name": "tts"}, None) is None


# ── build_per_slot asks the slot's provider first ────────────────────────


class _StubProvider:
    """A provider whose ``footprint_mb`` answers a fixed figure."""

    def __init__(self, mb: float | None) -> None:
        self._mb = mb
        self.calls: list[tuple[Any, Any, Any]] = []

    def footprint_mb(self, model_info: Any, slot_cfg: Any, profile: Any) -> float | None:
        self.calls.append((model_info, slot_cfg, profile))
        return self._mb


class _RaisingProvider:
    def footprint_mb(self, model_info: Any, slot_cfg: Any, profile: Any) -> float | None:
        raise RuntimeError("sidecar probe blew up")


def _write_slot_toml(home: str, name: str) -> None:
    slots = Path(home) / "etc" / "hal0" / "slots"
    slots.mkdir(parents=True, exist_ok=True)
    lines = [f'name = "{name}"', "port = 8081", 'backend = "vulkan"', "[model]"]
    lines += ['default = "mymodel"', ""]
    (slots / f"{name}.toml").write_text("\n".join(lines), encoding="utf-8")


class _Registry:
    """One model: 1000 MiB of checkpoint, a 131072-token native window."""

    class _Row:
        size_bytes = 1000 * 1024 * 1024

        def model_dump(self) -> dict[str, Any]:
            return {
                "id": "mymodel",
                "size_bytes": self.size_bytes,
                "path": "/models/mymodel.hgn",
                "metadata": {"context_length": 131072},
            }

    def get(self, model_id: str) -> Any:
        return self._Row()


def _slot(name: str = "brain", state: str = "ready") -> MagicMock:
    slot = MagicMock()
    slot.name = name
    slot.state = state
    slot.model_id = "mymodel"
    slot.backend = "vulkan"
    slot.slot_id = None
    slot.metadata = {"provider": "llama-server", "backend": "vulkan"}
    return slot


async def _per_slot(slot: MagicMock, provider: Any, *, cgroup_bytes: int = 0) -> dict[str, Any]:
    with (
        patch("hal0.providers.container._spec_provider_for", return_value=provider),
        patch(
            "hal0.slots.capacity._container_cgroup_mem_bytes",
            new_callable=AsyncMock,
            return_value=cgroup_bytes,
        ),
    ):
        return await build_per_slot([slot], registry=_Registry(), gpu_capable=True)


async def test_build_per_slot_uses_the_provider_footprint(tmp_hal0_home: str) -> None:
    _write_slot_toml(tmp_hal0_home, "brain")
    provider = _StubProvider(70 * GIB)

    result = await _per_slot(_slot(), provider)

    assert result["brain"]["mem_mb"] == pytest.approx(70 * GIB)
    assert result["brain"]["vram_mb"] == pytest.approx(70 * GIB)
    ((model_info, slot_cfg, profile),) = provider.calls
    assert model_info["size_bytes"] == 1000 * 1024 * 1024
    assert slot_cfg["name"] == "brain"
    assert profile is not None


async def test_build_per_slot_provider_figure_floors_the_cgroup(tmp_hal0_home: str) -> None:
    """Path 2 keeps ``max(cgroup, estimate)``: the provider figure is the estimate."""
    _write_slot_toml(tmp_hal0_home, "brain")

    low = await _per_slot(_slot(), _StubProvider(70 * GIB), cgroup_bytes=2 * 2**30)
    high = await _per_slot(_slot(), _StubProvider(70 * GIB), cgroup_bytes=80 * 2**30)

    assert low["brain"]["mem_mb"] == pytest.approx(70 * GIB)
    assert high["brain"]["mem_mb"] == pytest.approx(80 * GIB)


async def test_build_per_slot_counts_a_starting_slot_at_the_provider_figure(
    tmp_hal0_home: str,
) -> None:
    _write_slot_toml(tmp_hal0_home, "brain")
    slot = _slot(state="starting")
    with patch("hal0.providers.container._spec_provider_for", return_value=_StubProvider(70 * GIB)):
        result = await build_per_slot(
            [slot], registry=_Registry(), gpu_capable=True, include_starting=True
        )

    assert result["brain"]["mem_mb"] == pytest.approx(70 * GIB)


@pytest.mark.parametrize(
    "provider",
    [
        pytest.param(_StubProvider(None), id="provider-has-no-figure"),
        pytest.param(_StubProvider(0.0), id="provider-figure-not-positive"),
        pytest.param(object(), id="provider-lacks-the-hook"),
        pytest.param(_RaisingProvider(), id="provider-hook-raises"),
        pytest.param(None, id="llama-default-no-provider"),
    ],
)
async def test_build_per_slot_falls_back_to_the_file_size_estimate(
    tmp_hal0_home: str, provider: Any
) -> None:
    _write_slot_toml(tmp_hal0_home, "brain")

    result = await _per_slot(_slot(), provider)

    # 1000 MiB checkpoint + the native window (131072) capped at 32768 tokens of KV.
    assert result["brain"]["mem_mb"] == pytest.approx(1000.0 + 32768 / 1000 * 0.5, abs=0.1)


async def test_build_per_slot_does_not_ask_for_a_figure_without_a_slot_config(
    tmp_hal0_home: str,
) -> None:
    """No slot TOML, no slot_cfg to hand the provider: the estimate stands."""
    provider = _StubProvider(70 * GIB)

    result = await _per_slot(_slot("ghost"), provider)

    assert provider.calls == []
    assert result["ghost"]["mem_mb"] == pytest.approx(1000.0 + 131072 / 1000 * 0.5, abs=0.1)
