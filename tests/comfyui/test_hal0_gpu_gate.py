"""hal0_gpu_gate — the ComfyUI custom node that prepares image mode before
job submission while the iGPU is in inference (llm) mode.

The node ships as the package asset ``src/hal0/comfyui/assets/hal0_gpu_gate.py``
and the provider syncs it into the host-mounted ``custom_nodes`` dir of the
resident ComfyUI container, so the web UI stays fully usable (editor,
/object_info, workflow save/load) while only ``POST /prompt`` triggers a
best-effort arbiter handoff.

Only the pure decision logic is unit-tested here — the aiohttp middleware /
PromptServer wiring needs a live ComfyUI process and is exercised in the
CT105 live verification. The module MUST import cleanly outside ComfyUI
(no top-level ``server``/``aiohttp`` imports) for these tests to even load.
"""

from __future__ import annotations

import importlib.util
from pathlib import Path
from typing import Any

_NODE_PATH = (
    Path(__file__).resolve().parents[2] / "src" / "hal0" / "comfyui" / "assets" / "hal0_gpu_gate.py"
)


def _load_node() -> Any:
    spec = importlib.util.spec_from_file_location("hal0_gpu_gate", _NODE_PATH)
    assert spec is not None and spec.loader is not None
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def test_node_file_exists_and_imports_outside_comfyui() -> None:
    """Import must fail-soft outside ComfyUI (no PromptServer available)."""
    mod = _load_node()
    # ComfyUI custom-node import contract
    assert mod.NODE_CLASS_MAPPINGS == {}


def test_prompt_post_in_inference_mode_prepares_image_mode() -> None:
    mod = _load_node()
    status = {"mode": "inference"}
    assert mod.should_prepare_image_mode("POST", "/prompt", status) is True
    # The frontend posts to /api/prompt on recent ComfyUI versions.
    assert mod.should_prepare_image_mode("POST", "/api/prompt", status) is True


def test_allows_prompt_post_in_generation_mode() -> None:
    mod = _load_node()
    assert mod.should_prepare_image_mode("POST", "/prompt", {"mode": "generation"}) is False


def test_fails_open_when_hal0_api_unreachable() -> None:
    """hal0-api down → status unknown → never brick standalone ComfyUI use."""
    mod = _load_node()
    assert mod.should_prepare_image_mode("POST", "/prompt", None) is False
    assert mod.should_prepare_image_mode("POST", "/prompt", {"unexpected": "shape"}) is False


def test_never_blocks_other_routes_or_methods() -> None:
    """Everything that makes the editor usable must always pass."""
    mod = _load_node()
    status = {"mode": "inference"}
    assert mod.should_prepare_image_mode("GET", "/prompt", status) is False
    assert mod.should_prepare_image_mode("POST", "/object_info", status) is False
    assert mod.should_prepare_image_mode("GET", "/queue", status) is False
    assert mod.should_prepare_image_mode("POST", "/upload/image", status) is False


def test_prepare_image_mode_requests_switchover_and_waits(monkeypatch) -> None:
    mod = _load_node()
    statuses = iter(
        [
            {"mode": "inference"},
            {"mode": "inference", "switchover": {"active": True, "target": "generation"}},
            {"mode": "generation"},
        ]
    )
    calls: list[str] = []

    monkeypatch.setattr(mod, "_fetch_status", lambda: next(statuses))
    monkeypatch.setattr(mod, "_post_switchover", lambda: calls.append("switch") or True)
    monkeypatch.setattr(mod.time, "sleep", lambda _seconds: None)

    mod.prepare_image_mode()
    assert calls == ["switch"]


def test_prepare_image_mode_waits_out_a_running_switch_before_deciding(monkeypatch) -> None:
    """hal0 is restoring the LLM set (mode still generation, the switch lock held):
    the hook waits for it to finish, sees inference, and asks for image mode
    instead of letting the prompt render under LLM weights that are loading."""
    mod = _load_node()
    statuses = iter(
        [
            {"mode": "generation", "arbiter": {"switching": True}},
            {"mode": "generation", "arbiter": {"switching": True}},
            {"mode": "inference", "arbiter": {"switching": False}},
            {"mode": "generation", "arbiter": {"switching": False}},
        ]
    )
    calls: list[str] = []

    monkeypatch.setattr(mod, "_fetch_status", lambda: next(statuses))
    monkeypatch.setattr(mod, "_post_switchover", lambda: calls.append("switch") or True)
    monkeypatch.setattr(mod.time, "sleep", lambda _seconds: None)

    mod.prepare_image_mode()
    assert calls == ["switch"]
    assert next(statuses, None) is None


def test_prepare_image_mode_returns_once_the_switch_has_finished(monkeypatch) -> None:
    """ensure_img persists mode=img before it unloads the LLM set, so a
    generation mode reported while the switch still runs is not ready yet."""
    mod = _load_node()
    statuses = iter(
        [
            {"mode": "inference", "arbiter": {"switching": False}},
            {"mode": "generation", "arbiter": {"switching": True}},
            {"mode": "generation", "arbiter": {"switching": False}},
        ]
    )
    calls: list[str] = []

    monkeypatch.setattr(mod, "_fetch_status", lambda: next(statuses))
    monkeypatch.setattr(mod, "_post_switchover", lambda: calls.append("switch") or True)
    monkeypatch.setattr(mod.time, "sleep", lambda _seconds: None)

    mod.prepare_image_mode()
    assert calls == ["switch"]
    assert next(statuses, None) is None


def test_prepare_image_mode_fails_open_when_switchover_fails(monkeypatch) -> None:
    mod = _load_node()
    monkeypatch.setattr(mod, "_fetch_status", lambda: {"mode": "inference"})
    monkeypatch.setattr(mod, "_post_switchover", lambda: False)
    mod.prepare_image_mode()
