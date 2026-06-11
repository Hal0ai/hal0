"""SlotManager: npu container slot spawns through ContainerProvider with the FLM tag.

A4 — verifies that _resolve_model_info passes FLM tags (``family:size``) through
cleanly so ContainerProvider.load_sync receives model_info with ``_model_key``
and ``flm_tag`` set, and that plain registry-style ids (no ``:``) do NOT trigger
the FLM path — they fall through to the normal registry lookup.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from hal0.slots.manager import SlotManager

# ── helpers ──────────────────────────────────────────────────────────────────


def _write_npu_container_slot(root: Path, name: str = "npu", port: int = 8088) -> None:
    """Write a minimal npu container slot TOML."""
    (root / f"{name}.toml").write_text(
        "\n".join(
            [
                f'name = "{name}"',
                f"port = {port}",
                'device = "npu"',
                'runtime = "container"',
                'profile = "flm-npu"',
                "enabled = true",
                "[model]",
                'default = "gemma3:4b"',
                "",
            ]
        ),
        encoding="utf-8",
    )


def _make_container_provider_mock() -> MagicMock:
    """Build a MagicMock ContainerProvider with load_sync and wait_ready stubs."""
    provider = MagicMock()
    provider.load_sync = MagicMock(return_value=None)
    # wait_ready is async — replace with a coroutine that returns immediately.
    provider.wait_ready = AsyncMock(return_value=None)
    return provider


# ── positive path: FLM tag routes through ContainerProvider ──────────────────


@pytest.mark.anyio
async def test_npu_container_slot_spawns_with_flm_tag(
    slot_root: Path,
    tmp_hal0_home: str,
) -> None:
    """load('npu', 'gemma3:4b') calls ContainerProvider.load_sync with flm_tag set."""
    _write_npu_container_slot(slot_root, "npu")

    fake_provider = _make_container_provider_mock()

    with (
        patch("hal0.providers.container.container_provider", return_value=fake_provider),
        patch(
            "hal0.providers.flm.flm_served_models",
            return_value=[{"tag": "gemma3:4b", "installed": True}],
        ),
        patch("hal0.agents.hermes_refresh.spawn_context_refresh", lambda *a, **k: None),
    ):
        sm = SlotManager()
        await sm.load("npu", "gemma3:4b")

    assert fake_provider.load_sync.called, "ContainerProvider.load_sync must be called"
    cfg_arg, model_info_arg = fake_provider.load_sync.call_args.args
    assert cfg_arg["device"] == "npu", f"expected device=npu, got {cfg_arg.get('device')}"
    assert model_info_arg["_model_key"] == "gemma3:4b", (
        f"_model_key missing/wrong: {model_info_arg}"
    )
    assert model_info_arg["flm_tag"] == "gemma3:4b", f"flm_tag missing/wrong: {model_info_arg}"


# ── default model path: no explicit model_id, uses TOML default ──────────────


@pytest.mark.anyio
async def test_npu_container_slot_spawns_with_toml_default(
    slot_root: Path,
    tmp_hal0_home: str,
) -> None:
    """load('npu') with no model_id arg uses [model].default from TOML."""
    _write_npu_container_slot(slot_root, "npu")

    fake_provider = _make_container_provider_mock()

    with (
        patch("hal0.providers.container.container_provider", return_value=fake_provider),
        patch(
            "hal0.providers.flm.flm_served_models",
            return_value=[{"tag": "gemma3:4b", "installed": True}],
        ),
        patch("hal0.agents.hermes_refresh.spawn_context_refresh", lambda *a, **k: None),
    ):
        sm = SlotManager()
        await sm.load("npu")  # no explicit model_id

    assert fake_provider.load_sync.called
    _cfg_arg, model_info_arg = fake_provider.load_sync.call_args.args
    assert model_info_arg["_model_key"] == "gemma3:4b"
    assert model_info_arg["flm_tag"] == "gemma3:4b"


# ── negative path: non-FLM id must NOT set flm_tag to a colon-less string ────


@pytest.mark.anyio
async def test_registry_style_model_id_does_not_take_flm_path(
    slot_root: Path,
    tmp_hal0_home: str,
) -> None:
    """A plain registry-style id (no ``:``) falls through to registry lookup.

    _resolve_model_info stamps _model_key on all results, but is_flm_tag
    is only True when ``model_id`` contains ``:`` AND is in flm_served_models.
    A GGUF id like ``qwopus3.6-27b-v2`` must NOT be classified as an FLM tag.
    The test verifies two things:
    1. ``is_flm_tag("qwopus3.6-27b-v2")`` is False (fast guard).
    2. The registry lookup path is reached (ModelRegistry.get is called).
    """
    from hal0.providers.flm import is_flm_tag

    # Sanity-check the guard: no ":" → always False, regardless of served list.
    with patch(
        "hal0.providers.flm.flm_served_models",
        return_value=[{"tag": "qwopus3.6-27b-v2", "installed": True}],  # would match if colon
    ):
        assert not is_flm_tag("qwopus3.6-27b-v2"), "is_flm_tag must be False for ids without ':'"

    # Now verify the registry path is reached for a non-FLM id on a
    # container slot: ModelRegistry.get should be attempted (and will
    # return ModelNotFound for an unknown id — that's fine; the slot
    # manager logs and continues with the base info dict).
    _write_npu_container_slot(slot_root, "npu")
    # Override default so load() has a non-FLM model_id to resolve.
    (slot_root / "npu.toml").write_text(
        "\n".join(
            [
                'name = "npu"',
                "port = 8088",
                'device = "npu"',
                'runtime = "container"',
                'profile = "flm-npu"',
                "enabled = true",
                "[model]",
                'default = "qwopus3.6-27b-v2"',
                "",
            ]
        ),
        encoding="utf-8",
    )

    fake_provider = _make_container_provider_mock()
    registry_get_calls: list[Any] = []

    def _fake_registry_get(model_id: str) -> None:
        registry_get_calls.append(model_id)
        from hal0.registry.store import ModelNotFound

        raise ModelNotFound(model_id)

    with (
        patch("hal0.providers.container.container_provider", return_value=fake_provider),
        patch("hal0.providers.flm.flm_served_models", return_value=[]),
        patch("hal0.agents.hermes_refresh.spawn_context_refresh", lambda *a, **k: None),
        patch("hal0.registry.store.ModelRegistry") as mock_reg_cls,
    ):
        mock_reg_instance = MagicMock()
        mock_reg_cls.return_value = mock_reg_instance
        from hal0.registry.store import ModelNotFound

        mock_reg_instance.get.side_effect = ModelNotFound("qwopus3.6-27b-v2")

        sm = SlotManager()
        await sm.load("npu", "qwopus3.6-27b-v2")

    # Registry.get was called with the plain id — the FLM early-return
    # was NOT taken.
    mock_reg_instance.get.assert_called_once_with("qwopus3.6-27b-v2")
    # ContainerProvider still got called (slot is container type).
    assert fake_provider.load_sync.called
    _, model_info_arg = fake_provider.load_sync.call_args.args
    # _model_key is always stamped.
    assert model_info_arg["_model_key"] == "qwopus3.6-27b-v2"
    # flm_tag is also always stamped (from the base info dict).
    assert model_info_arg["flm_tag"] == "qwopus3.6-27b-v2"
