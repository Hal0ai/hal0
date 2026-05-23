"""NPU trio swap-in-progress detection (PR-20, plan §5.3, ADR-0009).

Tests the pure helper ``compute_npu_swap_status`` and the async
``fetch_npu_swap_status`` wrapper that probes Lemonade's ``/v1/health``.

The signal we publish: the configured NPU LLM slot's ``model.default``
is NOT in Lemonade's ``loaded[]`` AND a different ``recipe=flm`` entry
IS loaded (the old trio chat still serving while the new one warms up).
"""

from __future__ import annotations

from typing import Any
from unittest.mock import AsyncMock

import pytest

from hal0.dispatcher.npu_swap_status import (
    NpuSwapStatus,
    compute_npu_swap_status,
    fetch_npu_swap_status,
)
from hal0.lemonade.errors import LemonadeError

# ── Helpers ───────────────────────────────────────────────────────────


def _flm_loaded(model_name: str, backend_url: str = "http://127.0.0.1:9001") -> dict[str, Any]:
    return {
        "model_name": model_name,
        "backend_url": backend_url,
        "recipe": "flm",
        "type": "llm",
    }


def _npu_llm_slot(
    name: str = "agent",
    model: str = "llama-3.2-3b-npu",
    enabled: bool = True,
) -> dict[str, Any]:
    return {
        "name": name,
        "device": "npu",
        "type": "llm",
        "enabled": enabled,
        "model": {"default": model},
    }


def _gpu_slot(name: str = "primary", model: str = "phi3") -> dict[str, Any]:
    return {
        "name": name,
        "device": "gpu-vulkan",
        "type": "llm",
        "enabled": True,
        "model": {"default": model},
    }


# ── compute_npu_swap_status — pure helper ──────────────────────────────


def test_no_npu_slot_means_no_swap() -> None:
    """Nothing configured → swap is not in progress."""
    status = compute_npu_swap_status(
        slot_configs=[_gpu_slot()],
        health={"loaded": []},
    )
    assert status == NpuSwapStatus(in_progress=False, from_model=None, to_model=None)


def test_disabled_npu_slot_ignored() -> None:
    """A disabled NPU LLM slot doesn't drive a swap."""
    status = compute_npu_swap_status(
        slot_configs=[_npu_llm_slot(enabled=False)],
        health={"loaded": []},
    )
    assert status.in_progress is False
    assert status.to_model is None


def test_npu_configured_but_nothing_loaded_is_not_swap() -> None:
    """Configured + nothing in loaded[] = fresh first load, not a swap."""
    status = compute_npu_swap_status(
        slot_configs=[_npu_llm_slot(model="llama-3.2-3b-npu")],
        health={"loaded": []},
    )
    assert status.in_progress is False
    assert status.to_model == "llama-3.2-3b-npu"
    assert status.from_model is None


def test_npu_same_model_loaded_is_steady_state() -> None:
    """Configured matches the loaded FLM → steady state."""
    status = compute_npu_swap_status(
        slot_configs=[_npu_llm_slot(model="gemma3:1b")],
        health={"loaded": [_flm_loaded("gemma3:1b")]},
    )
    assert status.in_progress is False
    assert status.from_model == "gemma3:1b"
    assert status.to_model == "gemma3:1b"


def test_npu_different_model_loaded_is_swap() -> None:
    """Configured != loaded FLM → swap in progress."""
    status = compute_npu_swap_status(
        slot_configs=[_npu_llm_slot(model="llama-3.2-3b-npu")],
        health={"loaded": [_flm_loaded("gemma3:1b")]},
    )
    assert status.in_progress is True
    assert status.from_model == "gemma3:1b"
    assert status.to_model == "llama-3.2-3b-npu"


def test_swap_in_progress_with_gpu_peers_present() -> None:
    """Non-NPU peer slots don't affect the swap signal."""
    status = compute_npu_swap_status(
        slot_configs=[
            _gpu_slot(name="primary", model="phi3"),
            _npu_llm_slot(name="agent", model="llama-3.2-3b-npu"),
            _gpu_slot(name="nano", model="qwen3-1b"),
        ],
        health={"loaded": [_flm_loaded("gemma3:1b")]},
    )
    assert status.in_progress is True
    assert status.from_model == "gemma3:1b"
    assert status.to_model == "llama-3.2-3b-npu"


def test_non_flm_loaded_entries_ignored() -> None:
    """A llama.cpp entry in loaded[] does NOT count as the trio chat."""
    status = compute_npu_swap_status(
        slot_configs=[_npu_llm_slot(model="llama-3.2-3b-npu")],
        health={
            "loaded": [
                {
                    "model_name": "phi3",
                    "backend_url": "http://127.0.0.1:9002",
                    "recipe": "llamacpp",
                    "type": "llm",
                },
            ],
        },
    )
    # No FLM loaded → not a swap, just a pending first load.
    assert status.in_progress is False
    assert status.from_model is None
    assert status.to_model == "llama-3.2-3b-npu"


def test_alt_health_key_all_models_loaded() -> None:
    """Forward-compat: ``all_models_loaded`` also recognised."""
    status = compute_npu_swap_status(
        slot_configs=[_npu_llm_slot(model="llama-3.2-3b-npu")],
        health={"all_models_loaded": [_flm_loaded("gemma3:1b")]},
    )
    assert status.in_progress is True


def test_health_none_means_no_swap() -> None:
    """Health=None (lemond unreachable) degrades to no-swap."""
    status = compute_npu_swap_status(
        slot_configs=[_npu_llm_slot(model="llama-3.2-3b-npu")],
        health=None,
    )
    assert status.in_progress is False
    assert status.from_model is None
    assert status.to_model == "llama-3.2-3b-npu"


def test_empty_slot_model_default_means_no_swap() -> None:
    """NPU LLM slot with empty model.default → no to_model, no swap."""
    cfg = _npu_llm_slot()
    cfg["model"] = {"default": ""}
    status = compute_npu_swap_status(
        slot_configs=[cfg],
        health={"loaded": [_flm_loaded("gemma3:1b")]},
    )
    assert status.in_progress is False
    assert status.to_model is None


def test_missing_model_section_means_no_swap() -> None:
    """NPU LLM slot missing the [model] section entirely → no to_model."""
    cfg = {
        "name": "agent",
        "device": "npu",
        "type": "llm",
        "enabled": True,
    }
    status = compute_npu_swap_status(
        slot_configs=[cfg],
        health={"loaded": [_flm_loaded("gemma3:1b")]},
    )
    assert status.in_progress is False
    assert status.to_model is None


def test_malformed_entries_skipped() -> None:
    """Non-dict entries in loaded[] don't crash the walker."""
    status = compute_npu_swap_status(
        slot_configs=[_npu_llm_slot(model="llama-3.2-3b-npu")],
        health={"loaded": ["not-a-dict", None, _flm_loaded("gemma3:1b")]},
    )
    assert status.in_progress is True
    assert status.from_model == "gemma3:1b"


def test_to_dict_shape() -> None:
    """NpuSwapStatus.to_dict matches the wire shape."""
    status = NpuSwapStatus(
        in_progress=True,
        from_model="gemma3:1b",
        to_model="llama-3.2-3b-npu",
    )
    assert status.to_dict() == {
        "in_progress": True,
        "from_model": "gemma3:1b",
        "to_model": "llama-3.2-3b-npu",
    }


# ── fetch_npu_swap_status — async wrapper ──────────────────────────────


async def test_fetch_swallows_lemonade_error() -> None:
    """When lemond is unreachable, fetch degrades to no-swap."""
    client = AsyncMock()
    client.health = AsyncMock(side_effect=LemonadeError("connection refused"))

    status = await fetch_npu_swap_status(
        slot_configs=[_npu_llm_slot(model="llama-3.2-3b-npu")],
        lemonade_client=client,
    )
    assert status.in_progress is False
    assert status.from_model is None
    assert status.to_model == "llama-3.2-3b-npu"


async def test_fetch_with_none_client() -> None:
    """No lemonade client wired → degrades to no-swap."""
    status = await fetch_npu_swap_status(
        slot_configs=[_npu_llm_slot(model="llama-3.2-3b-npu")],
        lemonade_client=None,
    )
    assert status.in_progress is False
    assert status.to_model == "llama-3.2-3b-npu"


async def test_fetch_returns_swap_when_health_reports_old_model() -> None:
    """Happy path: live /v1/health reports the prior chat, slot points at new."""
    client = AsyncMock()
    client.health = AsyncMock(return_value={"loaded": [_flm_loaded("gemma3:1b")]})
    status = await fetch_npu_swap_status(
        slot_configs=[_npu_llm_slot(model="llama-3.2-3b-npu")],
        lemonade_client=client,
    )
    assert status.in_progress is True
    assert status.from_model == "gemma3:1b"
    assert status.to_model == "llama-3.2-3b-npu"


async def test_fetch_swallows_unexpected_error() -> None:
    """Defensive: any non-LemonadeError from .health() also degrades."""
    client = AsyncMock()
    client.health = AsyncMock(side_effect=RuntimeError("nginx hiccup"))
    status = await fetch_npu_swap_status(
        slot_configs=[_npu_llm_slot(model="llama-3.2-3b-npu")],
        lemonade_client=client,
    )
    assert status.in_progress is False


async def test_fetch_handles_non_dict_health_body() -> None:
    """If lemond returns a list (malformed), treat as no-swap."""
    client = AsyncMock()
    client.health = AsyncMock(return_value=["unexpected", "shape"])
    status = await fetch_npu_swap_status(
        slot_configs=[_npu_llm_slot(model="llama-3.2-3b-npu")],
        lemonade_client=client,
    )
    assert status.in_progress is False


# ── HTTP route smoke test ──────────────────────────────────────────────


def test_swap_status_endpoint_returns_default_shape(client: Any) -> None:
    """GET /api/npu/swap-status returns the shape even with no NPU configured."""
    resp = client.get("/api/npu/swap-status")
    assert resp.status_code == 200
    body = resp.json()
    assert set(body.keys()) == {"in_progress", "from_model", "to_model"}
    assert body["in_progress"] is False


def test_swap_status_endpoint_observes_npu_slot(
    client: Any,
    tmp_hal0_home: str,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """When an NPU LLM slot is configured and lemond reports a different
    FLM as loaded, the endpoint surfaces in_progress=True.

    Seeds a slot TOML on disk so SlotManager.iter_configs() picks it up;
    patches the lifespan-wired LemonadeClient.health() with a fake.
    """
    from pathlib import Path

    slots_dir = Path(tmp_hal0_home) / "etc" / "hal0" / "slots"
    slots_dir.mkdir(parents=True, exist_ok=True)
    (slots_dir / "agent.toml").write_text(
        'name = "agent"\n'
        "port = 8092\n"
        'device = "npu"\n'
        'type = "llm"\n'
        "enabled = true\n"
        'backend = "flm"\n'
        "[model]\n"
        'default = "llama-3.2-3b-npu"\n',
        encoding="utf-8",
    )

    # Patch app.state.lemonade_client.health for this request only.
    app = client.app
    fake_client = AsyncMock()
    fake_client.health = AsyncMock(return_value={"loaded": [_flm_loaded("gemma3:1b")]})
    monkeypatch.setattr(app.state, "lemonade_client", fake_client)

    resp = client.get("/api/npu/swap-status")
    assert resp.status_code == 200
    body = resp.json()
    assert body["in_progress"] is True
    assert body["from_model"] == "gemma3:1b"
    assert body["to_model"] == "llama-3.2-3b-npu"


__all__ = []
