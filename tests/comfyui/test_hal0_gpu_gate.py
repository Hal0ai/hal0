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
import json
from pathlib import Path
from typing import Any

import pytest

_NODE_PATH = (
    Path(__file__).resolve().parents[2] / "src" / "hal0" / "comfyui" / "assets" / "hal0_gpu_gate.py"
)


def _load_node(*, stub_announce: bool = True) -> Any:
    spec = importlib.util.spec_from_file_location("hal0_gpu_gate", _NODE_PATH)
    assert spec is not None and spec.loader is not None
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    if stub_announce:
        # hal0 confirms the hand-off for a prompt let through in generation
        # mode, unless a test scripts the announce itself (no network).
        mod._announce_prompt = lambda: True
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


def test_the_gate_announces_a_prompt_it_lets_through_in_generation_mode(monkeypatch) -> None:
    """With no switch to ask for, the gate still tells hal0 the prompt is on its
    way, so hal0 holds the GPU until ComfyUI's queue shows it."""
    mod = _load_node()
    calls: list[str] = []
    monkeypatch.setattr(
        mod, "_fetch_status", lambda: {"mode": "generation", "arbiter": {"switching": False}}
    )
    monkeypatch.setattr(mod, "_post_switchover", lambda: pytest.fail("no switch to ask for"))
    monkeypatch.setattr(mod, "_announce_prompt", lambda: calls.append("announce") or True)
    mod.prepare_image_mode()
    assert calls == ["announce"]


def test_the_gate_switches_when_hal0_has_left_image_mode_by_the_announce(monkeypatch) -> None:
    """A restore finished just before the announce: hal0 answers it is not
    holding the GPU, and the gate asks for the switch instead."""
    mod = _load_node()
    statuses = iter(
        [
            {"mode": "generation", "arbiter": {"switching": False}},
            {"mode": "inference", "arbiter": {"switching": False}},
            {"mode": "generation", "arbiter": {"switching": False}},
        ]
    )
    announces = iter([False, True])
    calls: list[str] = []
    monkeypatch.setattr(mod, "_fetch_status", lambda: next(statuses))
    monkeypatch.setattr(mod, "_post_switchover", lambda: calls.append("switch") or True)
    monkeypatch.setattr(
        mod, "_announce_prompt", lambda: calls.append("announce") or next(announces)
    )
    monkeypatch.setattr(mod.time, "sleep", lambda _seconds: None)
    mod.prepare_image_mode()
    assert calls == ["announce", "switch", "announce"]


def test_the_gate_refuses_when_hal0_never_confirms_the_hold(monkeypatch) -> None:
    mod = _load_node()
    monkeypatch.setattr(mod, "_SWITCH_TIMEOUT_S", 0.2)
    monkeypatch.setattr(mod, "_SWITCH_POLL_S", 0.05)
    monkeypatch.setattr(
        mod, "_fetch_status", lambda: {"mode": "generation", "arbiter": {"switching": False}}
    )
    monkeypatch.setattr(mod, "_announce_prompt", lambda: None)  # no answer
    with pytest.raises(mod.GpuGateClosed, match="stopped answering"):
        mod.prepare_image_mode()


def test_an_http_protocol_error_is_a_failed_read_not_a_crash(monkeypatch) -> None:
    """IncompleteRead and BadStatusLine are not OSErrors: they used to escape to
    aiohttp as a 500 with nothing queued and no reason."""
    import http.client

    mod = _load_node(stub_announce=False)

    def _broken(*_args, **_kwargs):
        raise http.client.IncompleteRead(b"")

    monkeypatch.setattr(mod, "_urlopen", _broken)
    assert mod._fetch_status() is None
    assert mod._post_switchover() is False
    assert "IncompleteRead" in mod._post_error()
    assert mod._announce_prompt() is None


def test_a_bad_switch_answer_refuses_the_prompt_with_a_reason(monkeypatch) -> None:
    import http.client

    mod = _load_node()

    def _bad_status_line(*_args, **_kwargs):
        raise http.client.BadStatusLine("garbage")

    monkeypatch.setattr(mod, "_fetch_status", lambda: {"mode": "inference"})
    monkeypatch.setattr(mod, "_urlopen", _bad_status_line)
    with pytest.raises(mod.GpuGateClosed, match="refused the switch"):
        mod.prepare_image_mode()


def _auth_refused(mod: Any, code: int, calls: list[str]) -> Any:
    import io
    import urllib.error

    def _urlopen(req: Any, *_args: Any, **_kwargs: Any) -> Any:
        url = req if isinstance(req, str) else req.full_url
        calls.append(url)
        body = io.BytesIO(
            b'{"error": {"code": "auth.required", "message": "authentication required"}}'
        )
        raise urllib.error.HTTPError(url, code, "Unauthorized", {}, body)

    return _urlopen


@pytest.mark.parametrize("code", [401, 403])
def test_hal0_requiring_authentication_refuses_at_once_with_its_own_reason(
    monkeypatch, code: int
) -> None:
    """With ``hal0 auth require on`` loopback is not exempt, and the gate holds
    no credential: say so at once, not "unreachable" after a 5 s retry."""
    monkeypatch.delenv("HAL0_COMFYUI_GATE_FAIL_OPEN", raising=False)
    mod = _load_node()
    calls: list[str] = []
    monkeypatch.setattr(mod, "_urlopen", _auth_refused(mod, code, calls))
    monkeypatch.setattr(mod.time, "sleep", lambda _seconds: pytest.fail("no retry loop"))

    with pytest.raises(mod.GpuGateClosed, match="requires authentication") as exc:
        mod.prepare_image_mode()
    assert len(calls) == 1
    assert "HAL0_COMFYUI_GATE_FAIL_OPEN=1" in str(exc.value)
    assert "unreachable" not in str(exc.value)


@pytest.mark.parametrize("code", [401, 403])
def test_a_switch_request_refused_for_authentication_names_it(monkeypatch, code: int) -> None:
    monkeypatch.delenv("HAL0_COMFYUI_GATE_FAIL_OPEN", raising=False)
    mod = _load_node()
    calls: list[str] = []
    monkeypatch.setattr(mod, "_fetch_status", lambda: {"mode": "inference"})
    monkeypatch.setattr(mod, "_urlopen", _auth_refused(mod, code, calls))
    with pytest.raises(mod.GpuGateClosed, match="requires authentication"):
        mod.prepare_image_mode()
    assert calls == [mod.HAL0_SWITCHOVER_URL]


@pytest.mark.parametrize("code", [401, 403])
def test_the_fail_open_switch_restores_the_old_pass_through_when_auth_is_required(
    monkeypatch, code: int
) -> None:
    """The opt-out restores v1.6.5's behaviour, which let these prompts through."""
    monkeypatch.setenv("HAL0_COMFYUI_GATE_FAIL_OPEN", "1")
    mod = _load_node()
    calls: list[str] = []
    monkeypatch.setattr(mod, "_urlopen", _auth_refused(mod, code, calls))
    monkeypatch.setattr(mod.time, "sleep", lambda _seconds: pytest.fail("no retry loop"))
    mod.prepare_image_mode()  # returns: the prompt goes through
    assert len(calls) == 1


class _FakeHal0:
    """A urlopen stand-in answering by path, the way hal0-api does.

    ``answers`` maps a path suffix to a list of answers consumed one per call
    (the last one sticks): a dict is a 200 JSON body, an int an HTTP error
    with that status, an exception instance is raised.
    """

    def __init__(self, answers: dict[str, list[Any]]) -> None:
        self.answers = answers
        self.calls: list[str] = []
        # The Authorization header each request carried, and the copy a
        # redirect would forward (urllib copies ``headers``, never
        # ``unredirected_hdrs``).
        self.auth: list[str | None] = []
        self.redirected_auth: list[str | None] = []

    def __call__(self, req: Any, *_args: Any, **_kwargs: Any) -> Any:
        import io
        import urllib.error

        url = req if isinstance(req, str) else req.full_url
        self.calls.append(url.split("/api/comfyui/")[-1])
        self.auth.append(None if isinstance(req, str) else req.get_header("Authorization"))
        self.redirected_auth.append(
            None if isinstance(req, str) else req.headers.get("Authorization")
        )
        for suffix, queue in self.answers.items():
            if url.endswith(suffix):
                answer = queue.pop(0) if len(queue) > 1 else queue[0]
                break
        else:
            raise AssertionError(f"unexpected request to {url}")
        if isinstance(answer, BaseException):
            raise answer
        if isinstance(answer, int):
            raise urllib.error.HTTPError(url, answer, "error", {}, io.BytesIO(b"{}"))

        class _Resp:
            status = 200

            def read(self) -> bytes:
                return json.dumps(answer).encode("utf-8")

            def __enter__(self) -> Any:
                return self

            def __exit__(self, *_exc: Any) -> None:
                return None

        return _Resp()


_LEGACY_GENERATION = {
    "mode": "generation",
    "arbiter": {"mode": "img", "pinned": False, "saved_llm_slots": []},
    "switchover": {"active": False, "target": None, "error": None},
}
_LEGACY_INFERENCE = {
    "mode": "inference",
    "arbiter": {"mode": "llm", "pinned": False, "saved_llm_slots": []},
    "switchover": {"active": False, "target": None, "error": None},
}


def _real_gate(monkeypatch, hal0: _FakeHal0) -> Any:
    monkeypatch.delenv("HAL0_COMFYUI_GATE_FAIL_OPEN", raising=False)
    mod = _load_node(stub_announce=False)
    monkeypatch.setattr(mod, "_urlopen", hal0)
    monkeypatch.setattr(mod.time, "sleep", lambda _seconds: None)
    monkeypatch.setattr(mod, "_SWITCH_TIMEOUT_S", 0.5)  # a wrong turn fails fast
    return mod


def test_after_a_rollback_the_gate_reads_the_older_hal0s_status_route(monkeypatch) -> None:
    """An older hal0 (1.6.x) has neither /arbiter nor /prompt-handoff: the gate
    reads /status, as the 1.6.x gate did, and counts the prompt as held."""
    hal0 = _FakeHal0({"/arbiter": [404], "/status": [_LEGACY_GENERATION], "/prompt-handoff": [404]})
    mod = _real_gate(monkeypatch, hal0)

    mod.prepare_image_mode()  # returns: the prompt goes through
    assert hal0.calls == ["arbiter", "status", "prompt-handoff"]

    hal0.calls.clear()
    mod.prepare_image_mode()  # the older hal0 is remembered: no second /arbiter probe
    assert hal0.calls == ["status", "prompt-handoff"]


def test_an_older_hal0_in_inference_mode_still_gets_its_switch(monkeypatch) -> None:
    hal0 = _FakeHal0(
        {
            "/arbiter": [404],
            "/status": [_LEGACY_INFERENCE, _LEGACY_GENERATION],
            "/switchover": [{"status": "switching", "mode": "generation"}],
            "/prompt-handoff": [404],
        }
    )
    mod = _real_gate(monkeypatch, hal0)
    mod.prepare_image_mode()
    assert hal0.calls == ["arbiter", "status", "switchover", "status", "prompt-handoff"]


@pytest.mark.parametrize("code", [401, 403])
def test_an_older_hal0_that_requires_authentication_is_still_refused(
    monkeypatch, code: int
) -> None:
    hal0 = _FakeHal0({"/arbiter": [404], "/status": [code]})
    mod = _real_gate(monkeypatch, hal0)
    with pytest.raises(mod.GpuGateClosed, match="requires authentication"):
        mod.prepare_image_mode()


def test_an_unreachable_hal0_is_not_mistaken_for_an_older_one(monkeypatch) -> None:
    """Only a 404 means "older hal0"; a refused connection stays unreachable."""
    import urllib.error

    refused = urllib.error.URLError(ConnectionRefusedError(111, "Connection refused"))
    hal0 = _FakeHal0({"/arbiter": [refused]})
    mod = _real_gate(monkeypatch, hal0)
    monkeypatch.setattr(mod, "_UNREACHABLE_RETRY_S", 0.0)
    with pytest.raises(mod.GpuGateClosed, match="unreachable"):
        mod.prepare_image_mode()
    assert set(hal0.calls) == {"arbiter"}


@pytest.mark.parametrize(
    "legacy_answer",
    [404, {"detail": "Not Found"}],
    ids=["status-404", "not-hal0-shaped"],
)
def test_a_404_that_status_cannot_back_up_is_not_remembered(monkeypatch, legacy_answer) -> None:
    """Something other than an older hal0 answers on the dialled address (a
    mistyped URL, another server on the port): no latch, the prompt is refused
    as unreachable, and the next prompt probes /arbiter again."""
    hal0 = _FakeHal0({"/arbiter": [404], "/status": [legacy_answer]})
    mod = _real_gate(monkeypatch, hal0)
    monkeypatch.setattr(mod, "_UNREACHABLE_RETRY_S", 0.0)
    with pytest.raises(mod.GpuGateClosed, match="unreachable"):
        mod.prepare_image_mode()
    assert hal0.calls[:2] == ["arbiter", "status"]

    hal0.calls.clear()
    with pytest.raises(mod.GpuGateClosed, match="unreachable"):
        mod.prepare_image_mode()
    assert hal0.calls[0] == "arbiter"


def test_a_404_on_the_hand_off_counts_for_that_prompt_only(monkeypatch) -> None:
    """Every prompt is announced: a hand-off route that answers later is used."""
    hal0 = _FakeHal0(
        {
            "/arbiter": [{"mode": "generation", "arbiter": {"switching": False}}],
            "/prompt-handoff": [404, {"held": True}],
        }
    )
    mod = _real_gate(monkeypatch, hal0)
    mod.prepare_image_mode()
    mod.prepare_image_mode()
    assert hal0.calls == ["arbiter", "prompt-handoff", "arbiter", "prompt-handoff"]


@pytest.mark.parametrize("code", [401, 403])
def test_an_announce_refused_for_authentication_refuses_at_once(monkeypatch, code: int) -> None:
    hal0 = _FakeHal0(
        {
            "/arbiter": [{"mode": "generation", "arbiter": {"switching": False}}],
            "/prompt-handoff": [code],
        }
    )
    mod = _real_gate(monkeypatch, hal0)
    with pytest.raises(mod.GpuGateClosed, match="requires authentication"):
        mod.prepare_image_mode()
    assert hal0.calls == ["arbiter", "prompt-handoff"]


@pytest.mark.parametrize("code", [401, 403])
def test_the_fail_open_switch_covers_a_switch_request_refused_for_authentication(
    monkeypatch, code: int
) -> None:
    monkeypatch.setenv("HAL0_COMFYUI_GATE_FAIL_OPEN", "1")
    hal0 = _FakeHal0({"/arbiter": [{"mode": "inference"}], "/switchover": [code]})
    mod = _load_node(stub_announce=False)
    monkeypatch.setattr(mod, "_urlopen", hal0)
    mod.prepare_image_mode()  # returns: unguarded, as v1.6.5 did
    assert hal0.calls == ["arbiter", "switchover"]


def test_a_current_hal0_without_the_hand_off_route_counts_the_prompt_held(monkeypatch) -> None:
    hal0 = _FakeHal0(
        {
            "/arbiter": [{"mode": "generation", "arbiter": {"switching": False}}],
            "/prompt-handoff": [404],
        }
    )
    mod = _real_gate(monkeypatch, hal0)
    mod.prepare_image_mode()
    assert hal0.calls == ["arbiter", "prompt-handoff"]


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
    monkeypatch.setattr(mod, "_urlopen", _rejected)

    with pytest.raises(mod.GpuGateClosed, match="HTTP 500") as exc:
        mod.prepare_image_mode()
    assert "2 render job(s) queued" in str(exc.value)


# ---------------------------------------------------------------------------
# The gate's own credential (#3058)

_TOKEN = "T" * 40 + "a_-"


def _with_token_file(monkeypatch, tmp_path, content: str | None = _TOKEN + "\n") -> None:
    path = tmp_path / "token"
    if content is not None:
        path.write_text(content)
    monkeypatch.setenv("HAL0_COMFYUI_GATE_TOKEN_FILE", str(path))


def test_the_gate_sends_its_token_on_each_of_its_requests(monkeypatch, tmp_path) -> None:
    _with_token_file(monkeypatch, tmp_path)
    hal0 = _FakeHal0(
        {
            "/arbiter": [_LEGACY_INFERENCE, _LEGACY_GENERATION],
            "/switchover": [{"status": "switching"}],
            "/prompt-handoff": [{"held": True}],
        }
    )
    mod = _real_gate(monkeypatch, hal0)

    mod.prepare_image_mode()

    assert hal0.calls == ["arbiter", "switchover", "arbiter", "prompt-handoff"]
    assert hal0.auth == [f"Bearer {_TOKEN}"] * 4
    # Unredirected: a redirect never carries the token to another URL.
    assert hal0.redirected_auth == [None] * 4


def test_the_gate_reads_its_token_on_every_request(monkeypatch, tmp_path) -> None:
    """A replaced token reaches a running ComfyUI without a restart."""
    _with_token_file(monkeypatch, tmp_path)
    hal0 = _FakeHal0({"/arbiter": [_LEGACY_GENERATION], "/prompt-handoff": [{"held": True}]})
    mod = _real_gate(monkeypatch, hal0)
    mod.prepare_image_mode()
    (tmp_path / "token").write_text("N" * 43 + "\n")

    mod.prepare_image_mode()

    assert hal0.auth[-1] == "Bearer " + "N" * 43


@pytest.mark.parametrize(
    "content",
    [None, "", "short", _TOKEN + "\r\nX-Evil: 1", "bad token with spaces and more chars!!"],
)
def test_without_a_usable_token_the_gate_sends_no_credential(
    monkeypatch, tmp_path, content: str | None
) -> None:
    _with_token_file(monkeypatch, tmp_path, content)
    hal0 = _FakeHal0({"/arbiter": [_LEGACY_GENERATION], "/prompt-handoff": [{"held": True}]})
    mod = _real_gate(monkeypatch, hal0)

    mod.prepare_image_mode()

    assert hal0.auth == [None, None]


def test_the_gate_sends_no_credential_without_a_token_file_setting(monkeypatch) -> None:
    monkeypatch.delenv("HAL0_COMFYUI_GATE_TOKEN_FILE", raising=False)
    hal0 = _FakeHal0({"/arbiter": [_LEGACY_GENERATION], "/prompt-handoff": [{"held": True}]})
    mod = _real_gate(monkeypatch, hal0)

    mod.prepare_image_mode()

    assert hal0.auth == [None, None]


@pytest.mark.parametrize("has_token", [True, False])
def test_an_auth_refusal_says_how_to_give_the_gate_its_credential(
    monkeypatch, tmp_path, has_token: bool
) -> None:
    _with_token_file(monkeypatch, tmp_path, _TOKEN if has_token else None)
    hal0 = _FakeHal0({"/arbiter": [401]})
    mod = _real_gate(monkeypatch, hal0)

    with pytest.raises(mod.GpuGateClosed) as excinfo:
        mod.prepare_image_mode()

    reason = str(excinfo.value)
    assert "HTTP 401" in reason
    assert "hal0 slot restart img" in reason
    assert "HAL0_COMFYUI_GATE_FAIL_OPEN=1" in reason
    assert _TOKEN not in reason
    assert ("did not accept" in reason) is has_token


def test_the_gates_requests_ignore_proxies_and_redirects(monkeypatch) -> None:
    """The token goes to hal0-api only: never through a proxy from the
    container's environment, never to a URL a redirect names."""
    import urllib.request

    monkeypatch.setenv("HTTP_PROXY", "http://proxy.invalid:3128")
    mod = _load_node()

    def _proxied(opener: Any) -> bool:
        return any(
            isinstance(h, urllib.request.ProxyHandler) and h.proxies for h in opener.handlers
        )

    assert _proxied(urllib.request.build_opener())  # the default opener would use it
    assert not _proxied(mod._OPENER)
    redirects = [
        h for h in mod._OPENER.handlers if isinstance(h, urllib.request.HTTPRedirectHandler)
    ]
    assert redirects
    req = urllib.request.Request("http://127.0.0.1:8080/api/comfyui/arbiter")
    for handler in redirects:
        assert handler.redirect_request(req, None, 302, "Found", {}, "http://x.invalid/") is None
