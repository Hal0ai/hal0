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

import pytest

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


def test_never_asks_for_a_switch_without_a_known_mode() -> None:
    """No status, or one without the arbiter's mode: there is nothing to switch from."""
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


def test_prepare_image_mode_asks_again_when_hal0_is_back_in_inference(monkeypatch) -> None:
    """An LLM request reclaimed the GPU after the first switch, before the prompt
    reached ComfyUI's queue: the hook asks for image mode again instead of
    letting the prompt render under the LLM set."""
    mod = _load_node()
    statuses = iter(
        [
            {"mode": "inference", "arbiter": {"switching": False}},
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
    assert calls == ["switch", "switch"]
    assert next(statuses, None) is None


def test_prepare_image_mode_waits_while_hal0_runs_the_switch_it_accepted(monkeypatch) -> None:
    """Between the switchover's 202 and the arbiter taking its lock, hal0 still
    reports inference with the switchover active: that is not a reason to ask again."""
    mod = _load_node()
    statuses = iter(
        [
            {"mode": "inference", "arbiter": {"switching": False}},
            *[
                {
                    "mode": "inference",
                    "arbiter": {"switching": False},
                    "switchover": {"active": True},
                }
            ]
            * 4,
            {"mode": "generation", "arbiter": {"switching": False}},
        ]
    )
    calls: list[str] = []

    monkeypatch.setattr(mod, "_fetch_status", lambda: next(statuses))
    monkeypatch.setattr(mod, "_post_switchover", lambda: calls.append("switch") or True)
    monkeypatch.setattr(mod.time, "sleep", lambda _seconds: None)

    mod.prepare_image_mode()
    assert calls == ["switch"]


def test_prepare_image_mode_fails_closed_when_hal0_keeps_the_gpu_in_inference(
    monkeypatch,
) -> None:
    """hal0 answers but never reaches image mode: the prompt must not render
    under the LLM set. The hook asks a bounded number of times, then refuses."""
    mod = _load_node()
    calls: list[str] = []

    monkeypatch.setattr(mod, "_fetch_status", lambda: {"mode": "inference"})
    monkeypatch.setattr(mod, "_post_switchover", lambda: calls.append("switch") or True)
    monkeypatch.setattr(mod.time, "sleep", lambda _seconds: None)

    with pytest.raises(mod.GpuGateClosed, match="inference"):
        mod.prepare_image_mode()
    assert calls == ["switch"] * mod._SWITCH_MAX_POSTS


def test_prepare_image_mode_fails_closed_when_the_switch_outlives_the_deadline(
    monkeypatch,
) -> None:
    mod = _load_node()
    monkeypatch.setattr(mod, "_SWITCH_TIMEOUT_S", 0.0)
    monkeypatch.setattr(
        mod, "_fetch_status", lambda: {"mode": "generation", "arbiter": {"switching": True}}
    )
    monkeypatch.setattr(mod, "_post_switchover", lambda: pytest.fail("no switch to ask for"))
    monkeypatch.setattr(mod.time, "sleep", lambda _seconds: None)

    with pytest.raises(mod.GpuGateClosed, match="did not finish"):
        mod.prepare_image_mode()


def test_prepare_image_mode_refuses_when_hal0_stays_unreachable(monkeypatch) -> None:
    """With hal0-api down the GPU most likely holds the LLM set (LLM mode is the
    resting state), so after a short retry the prompt is refused."""
    mod = _load_node()
    monkeypatch.setattr(mod, "_UNREACHABLE_RETRY_S", 0.2, raising=False)
    monkeypatch.setattr(mod, "_SWITCH_POLL_S", 0.05)
    reads: list[int] = []
    monkeypatch.setattr(mod, "_fetch_status", lambda: reads.append(1))
    monkeypatch.setattr(mod, "_post_switchover", lambda: pytest.fail("hal0 is unreachable"))

    with pytest.raises(mod.GpuGateClosed, match="unreachable"):
        mod.prepare_image_mode()
    assert len(reads) > 1  # it retried before refusing


def test_prepare_image_mode_refuses_when_hal0_reports_no_gpu_mode(monkeypatch) -> None:
    """hal0 answers but its arbiter does not (the mode is null): it cannot say
    what holds the GPU either."""
    mod = _load_node()
    monkeypatch.setattr(mod, "_UNREACHABLE_RETRY_S", 0.0, raising=False)
    monkeypatch.setattr(mod, "_fetch_status", lambda: {"mode": None, "arbiter": None})
    monkeypatch.setattr(mod, "_post_switchover", lambda: pytest.fail("no mode to switch from"))
    monkeypatch.setattr(mod.time, "sleep", lambda _seconds: None)

    with pytest.raises(mod.GpuGateClosed, match="unreachable"):
        mod.prepare_image_mode()


def test_prepare_image_mode_rides_out_a_short_hal0_outage(monkeypatch) -> None:
    """An api restart: the first reads fail, then hal0 answers and the switch runs."""
    mod = _load_node()
    statuses = iter(
        [
            None,
            None,
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


def test_the_fail_open_switch_lets_prompts_through_while_hal0_is_unreachable(
    monkeypatch,
) -> None:
    """HAL0_COMFYUI_GATE_FAIL_OPEN=1 restores the pass-through for a ComfyUI
    that must keep working while hal0 is down. It covers only an unreachable
    hal0, never one that answered."""
    monkeypatch.setenv("HAL0_COMFYUI_GATE_FAIL_OPEN", "1")
    mod = _load_node()
    monkeypatch.setattr(mod, "_UNREACHABLE_RETRY_S", 0.0, raising=False)
    monkeypatch.setattr(mod, "_fetch_status", lambda: None)
    monkeypatch.setattr(mod, "_post_switchover", lambda: pytest.fail("hal0 is unreachable"))
    monkeypatch.setattr(mod.time, "sleep", lambda _seconds: None)
    mod.prepare_image_mode()  # returns: the prompt goes through

    monkeypatch.setattr(mod, "_fetch_status", lambda: {"mode": "inference"})
    monkeypatch.setattr(mod, "_post_switchover", lambda: False)
    with pytest.raises(mod.GpuGateClosed):
        mod.prepare_image_mode()  # hal0 answered and refused: still refused


def test_a_failed_status_read_during_the_switch_does_not_let_the_prompt_through(
    monkeypatch,
) -> None:
    """One slow status read (the route's own probes can take 2 s) while the LLM
    set is still unloading used to let the prompt render on top of it."""
    mod = _load_node()
    statuses = iter(
        [
            {"mode": "inference", "arbiter": {"switching": False}},
            None,
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
    assert next(statuses, None) is None  # it waited for the switch to finish


def test_hal0_going_silent_after_the_switch_request_refuses_at_the_deadline(
    monkeypatch,
) -> None:
    mod = _load_node()
    monkeypatch.setattr(mod, "_SWITCH_TIMEOUT_S", 0.3)
    monkeypatch.setattr(mod, "_SWITCH_POLL_S", 0.05)
    statuses = iter([{"mode": "inference", "arbiter": {"switching": False}}])
    monkeypatch.setattr(mod, "_fetch_status", lambda: next(statuses, None))
    monkeypatch.setattr(mod, "_post_switchover", lambda: True)

    with pytest.raises(mod.GpuGateClosed, match="stopped answering"):
        mod.prepare_image_mode()


def test_the_gate_reads_hal0s_cheap_arbiter_status_by_default(monkeypatch) -> None:
    """Not the full /status aggregate: its probes can outlast the read timeout."""
    monkeypatch.delenv("HAL0_COMFYUI_STATUS_URL", raising=False)
    mod = _load_node()
    assert mod.HAL0_STATUS_URL == "http://127.0.0.1:8080/api/comfyui/arbiter"


def test_the_gate_deadline_outlasts_the_chat_wait_and_the_drain(monkeypatch) -> None:
    """A prompt that arrives during a long LLM generation must not be refused
    just before the switch it asked for finishes."""
    import hal0.slots.arbiter as arbiter_mod

    monkeypatch.delenv("HAL0_COMFYUI_SWITCH_TIMEOUT_S", raising=False)
    mod = _load_node()
    assert mod._SWITCH_TIMEOUT_S >= 180.0
    assert mod._SWITCH_TIMEOUT_S > arbiter_mod._DRAIN_TIMEOUT_S
    assert mod._SWITCH_TIMEOUT_S > arbiter_mod._LLM_DEMAND_WAIT_S


def test_a_refused_prompt_answers_in_comfyuis_prompt_error_shape() -> None:
    """ComfyUI's editor turns a non-200 /prompt answer of this shape into its
    prompt error dialog, showing ``message: details``."""
    mod = _load_node()
    body = mod.refused_prompt_body("hal0 kept the GPU in inference mode")
    assert body == {
        "error": {
            "type": "hal0_gpu_not_ready",
            "message": "hal0 did not hand the GPU to ComfyUI",
            "details": "hal0 kept the GPU in inference mode",
            "extra_info": {},
        },
        "node_errors": {},
    }


def test_prepare_image_mode_refuses_when_hal0_rejects_the_switch_request(monkeypatch) -> None:
    """hal0 just said the LLM set holds the GPU: letting the prompt through
    would render on top of it."""
    mod = _load_node()
    monkeypatch.setattr(mod, "_fetch_status", lambda: {"mode": "inference"})
    monkeypatch.setattr(mod, "_post_switchover", lambda: False)
    with pytest.raises(mod.GpuGateClosed, match="refused the switch"):
        mod.prepare_image_mode()


def test_a_rejected_switch_request_carries_hal0s_reason(monkeypatch) -> None:
    import io
    import urllib.error

    mod = _load_node()

    def _rejected(*_args, **_kwargs):
        body = io.BytesIO(
            b'{"error": {"code": "comfyui.busy", "message": "2 render job(s) queued"}}'
        )
        raise urllib.error.HTTPError(mod.HAL0_SWITCHOVER_URL, 500, "Server Error", {}, body)

    monkeypatch.setattr(mod, "_fetch_status", lambda: {"mode": "inference"})
    monkeypatch.setattr(mod.urllib.request, "urlopen", _rejected)

    with pytest.raises(mod.GpuGateClosed, match="HTTP 500") as exc:
        mod.prepare_image_mode()
    assert "2 render job(s) queued" in str(exc.value)
