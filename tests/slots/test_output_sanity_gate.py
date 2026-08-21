"""Output-sanity gate: a slot may not reach READY on transport proof alone.

Regression for #1922 / #1888. On ct151 during rc.6 a box read green on every
surface hal0 owns — slot ``ready``, ``/api/health`` green, ``hal0 doctor`` "OK
gpu", an SSE ``done`` frame, 60-120 tok/s — while producing two hours of
garbage (runs of ``)`` with ``--jinja``, empty content on a bare
``/completion``). Every one of those surfaces proves the *port answers*; none
proves the *model produces language*.

The tests below pin both halves of the fix:

* the probe itself, against a REAL loopback HTTP server that plays each of the
  four failure shapes from the issue (``Paris`` / ``)))))`` / empty content /
  never terminating), so the httpx call, the JSON parse and the timeout are
  exercised rather than mocked away;
* the gate's placement in ``SlotManager._await_ready``, so a garbage slot ends
  in ERROR with an actionable message instead of a lying READY — and an
  embedding slot, or an operator who opted out, is never blocked on a chat
  completion.
"""

from __future__ import annotations

import asyncio
import contextlib
import json
from collections.abc import AsyncIterator, Callable
from pathlib import Path
from typing import Any

import pytest

from hal0.slots import output_sanity
from hal0.slots.manager import SlotManager
from hal0.slots.state import SlotOutputSanityFailed, SlotState, SlotTerminateTimeout
from tests.slots.conftest import FakeContainerProvider

# A responder maps (path, request_body) → (status, json_body), or None to
# model a server that accepts the request and never answers.
Responder = Callable[[str, dict[str, Any]], tuple[int, Any] | None]

_REASONS = {200: "OK", 404: "Not Found", 500: "Internal Server Error"}

#: The REAL probe, bound at import — before the suite-wide autouse stub in
#: ``tests/conftest.py`` (which answers the gate for every OTHER test, none of
#: which has a model server) can replace the module attribute. The probe tests
#: below are the one place the actual httpx round-trip must run.
_probe = output_sanity.probe


class FakeSlotServer:
    """A minimal loopback HTTP server standing in for llama-server.

    Deliberately not an httpx mock transport: the never-terminating case (the
    fourth shape the issue asks for) is only a real test if a real socket is
    open with nothing coming back on it.
    """

    def __init__(self) -> None:
        self.port = 0
        self.seen: list[tuple[str, dict[str, Any]]] = []
        self._tasks: set[asyncio.Task[None]] = set()
        self._server: asyncio.AbstractServer | None = None

    async def start(self, responder: Responder) -> None:
        async def _client(reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
            task = asyncio.current_task()
            if task is not None:
                self._tasks.add(task)
            try:
                request_line = await reader.readline()
                if not request_line:
                    return
                path = request_line.decode("latin-1").split(" ")[1]
                length = 0
                while True:
                    line = await reader.readline()
                    if line in (b"\r\n", b"\n", b""):
                        break
                    name, _, value = line.decode("latin-1").partition(":")
                    if name.strip().lower() == "content-length":
                        length = int(value.strip())
                raw = await reader.readexactly(length) if length else b""
                body = json.loads(raw) if raw else {}
                self.seen.append((path, body))
                reply = responder(path, body)
                if reply is None:
                    # Never-terminating server: hold the connection open with
                    # no response until the test tears the fixture down.
                    await asyncio.sleep(300)
                    return
                status, payload = reply
                encoded = json.dumps(payload).encode()
                head = (
                    f"HTTP/1.1 {status} {_REASONS.get(status, 'OK')}\r\n"
                    "Content-Type: application/json\r\n"
                    f"Content-Length: {len(encoded)}\r\n"
                    "Connection: close\r\n\r\n"
                ).encode()
                writer.write(head + encoded)
                await writer.drain()
            except (asyncio.CancelledError, ConnectionError):
                raise
            finally:
                with contextlib.suppress(Exception):
                    writer.close()

        self._server = await asyncio.start_server(_client, "127.0.0.1", 0)
        self.port = self._server.sockets[0].getsockname()[1]

    async def stop(self) -> None:
        for task in self._tasks:
            task.cancel()
        if self._server is not None:
            self._server.close()
            with contextlib.suppress(Exception):
                await self._server.wait_closed()


@pytest.fixture
async def slot_server() -> AsyncIterator[FakeSlotServer]:
    server = FakeSlotServer()
    try:
        yield server
    finally:
        await server.stop()


def _completion(text: str, *, chat_text: str | None = None) -> Responder:
    """A server whose model emits ``text`` on ``/completion``.

    ``chat_text`` is what the same model emits on ``/v1/chat/completions``,
    which the probe only asks when the raw endpoint's answer was wrong; it
    defaults to the same text (one model, one opinion).
    """

    def _respond(path: str, body: dict[str, Any]) -> tuple[int, Any]:
        if path == "/completion":
            return 200, {"content": text, "stop": True}
        if path == "/v1/chat/completions":
            answer = text if chat_text is None else chat_text
            return 200, {"choices": [{"message": {"content": answer}}]}
        return 404, {"error": f"no route {path}"}

    return _respond


def _thinking_model(raw_text: str, *, reasoning: str, content: str = "") -> Responder:
    """A reasoning model: the chat turn answers in ``reasoning_content``.

    Qwen3-class models emit a ``<think>`` block before any visible content;
    llama-server (and every OpenAI-compatible runner that splits reasoning
    out) reports it as ``message.reasoning_content`` with ``content`` empty
    until the block closes. ``raw_text`` is what the same model says on the
    raw ``/completion`` endpoint — wrong, which is what sends the probe to the
    chat fallback in the first place.
    """

    def _respond(path: str, body: dict[str, Any]) -> tuple[int, Any]:
        if path == "/completion":
            return 200, {"content": raw_text, "stop": True}
        if path == "/v1/chat/completions":
            return (
                200,
                {"choices": [{"message": {"content": content, "reasoning_content": reasoning}}]},
            )
        return 404, {"error": f"no route {path}"}

    return _respond


# ── the probe, against a real server ────────────────────────────────────────


async def test_probe_passes_on_paris(slot_server: FakeSlotServer) -> None:
    """A working slot answers the prompt with the expected token → ok."""
    await slot_server.start(_completion(" Paris, and it has been since 987."))

    verdict = await _probe(slot_server.port, timeout_s=5.0)

    assert verdict.ok is True
    assert verdict.status == "ok"


async def test_probe_sends_the_prescribed_greedy_request(
    slot_server: FakeSlotServer,
) -> None:
    """The probe must be greedy and bounded, or its verdict means nothing.

    Temperature 0 makes the answer deterministic (a sampled model can produce
    a correct-looking token by luck); ``n_predict`` keeps the gate inside the
    slot lifecycle budget.
    """
    await slot_server.start(_completion(" Paris"))

    await _probe(slot_server.port, timeout_s=5.0)

    path, body = slot_server.seen[0]
    assert path == "/completion"
    assert body["prompt"] == "The capital of France is"
    assert body["temperature"] == 0
    assert body["n_predict"] == output_sanity.SANITY_N_PREDICT
    assert body["stream"] is False


async def test_probe_fails_on_paren_garbage(slot_server: FakeSlotServer) -> None:
    """The rc.6 ``--jinja`` failure shape: fluent-looking, meaningless.

    The rc.6 garbage reproduced on both endpoints, so the second opinion
    agrees and the slot still fails.
    """
    await slot_server.start(_completion(")))))))))))"))

    verdict = await _probe(slot_server.port, timeout_s=5.0)

    assert verdict.ok is False
    assert verdict.status == "incoherent_output"
    # The operator has to be able to SEE the garbage, not just be told "failed".
    assert ")))" in verdict.sample
    assert [p for p, _ in slot_server.seen] == ["/completion", "/v1/chat/completions"]


async def test_probe_fails_on_empty_content(slot_server: FakeSlotServer) -> None:
    """The rc.6 bare-``/completion`` failure shape: 200 with nothing in it."""
    await slot_server.start(_completion(""))

    verdict = await _probe(slot_server.port, timeout_s=5.0)

    assert verdict.ok is False
    assert verdict.status == "empty_output"


async def test_probe_accepts_a_model_that_only_answers_through_its_template(
    slot_server: FakeSlotServer,
) -> None:
    """A working slot must never be failed on one endpoint's opinion.

    A heavily template-dependent instruct model can wander on a raw prompt and
    answer correctly through its chat template. Parking THAT slot in ERROR
    would make the gate worse than the bug it guards against, so a wrong raw
    answer buys exactly one retry through ``/v1/chat/completions``.
    """
    await slot_server.start(_completion("<|im_start|>assistant", chat_text="Paris."))

    verdict = await _probe(slot_server.port, timeout_s=5.0)

    assert verdict.ok is True
    assert verdict.status == "ok_via_chat"


async def test_probe_fails_on_never_terminating_server(
    slot_server: FakeSlotServer,
) -> None:
    """A timeout is not a pass.

    "The probe was inconclusive so let the slot through" is precisely the
    silent degrade this gate exists to remove. The verdict layer therefore
    reports ``ok=False``; what the *load path* does with an ambiguous failure
    is a separate decision, pinned by
    ``test_a_slow_server_parks_the_slot_warming_instead_of_condemning_it``.
    """
    await slot_server.start(lambda path, body: None)

    verdict = await _probe(slot_server.port, timeout_s=0.5)

    assert verdict.ok is False
    assert verdict.status == "probe_timeout"
    # No second opinion on a timeout: a wedged runner must not cost the load
    # path two full probe budgets.
    assert len(slot_server.seen) == 1


async def test_probe_fails_on_dead_port() -> None:
    """Nothing listening → a fail verdict, never an exception."""
    server = FakeSlotServer()
    await server.start(_completion(" Paris"))
    port = server.port
    await server.stop()
    # Give the listener a beat to actually go away.
    await asyncio.sleep(0.05)

    verdict = await _probe(port, timeout_s=2.0)

    assert verdict.ok is False
    assert verdict.status in ("probe_transport_error", "probe_timeout")


async def test_probe_fails_on_http_error(slot_server: FakeSlotServer) -> None:
    """A 500 from the runner is a failure, not an inconclusive pass."""
    await slot_server.start(lambda path, body: (500, {"error": "context shift failed"}))

    verdict = await _probe(slot_server.port, timeout_s=5.0)

    assert verdict.ok is False
    assert verdict.status == "probe_http_500"
    assert len(slot_server.seen) == 1, "a 5xx is a round-trip fact — no second opinion"


async def test_probe_passes_when_neither_endpoint_exists(
    slot_server: FakeSlotServer,
) -> None:
    """404 on both = not a completion server at all → cannot judge.

    The ONE inconclusive-passes branch, and it is narrow on purpose: a runtime
    family that serves neither endpoint was never in this gate's scope, and
    failing it would break slots the gate was not designed to probe.
    """
    await slot_server.start(lambda path, body: (404, {"error": "not found"}))

    verdict = await _probe(slot_server.port, timeout_s=5.0)

    assert verdict.ok is True
    assert verdict.status == "probe_unsupported"


async def test_probe_falls_back_to_chat_when_only_completion_is_absent(
    slot_server: FakeSlotServer,
) -> None:
    """An OpenAI-only runner is judged on the endpoint it does serve."""

    def _respond(path: str, body: dict[str, Any]) -> tuple[int, Any]:
        if path == "/completion":
            return 404, {"error": "not found"}
        return 200, {"choices": [{"message": {"content": "Paris"}}]}

    await slot_server.start(_respond)

    verdict = await _probe(slot_server.port, timeout_s=5.0)

    assert verdict.ok is True
    assert verdict.status == "ok_via_chat"


async def test_probe_accepts_a_thinking_model_answering_in_reasoning_content(
    slot_server: FakeSlotServer,
) -> None:
    """A working reasoning model must not be failed for thinking out loud.

    The chat fallback exists to serve exactly the template-dependent model
    that wanders on a raw prompt — and a Qwen3-class model spends its budget
    inside ``reasoning_content`` before emitting any ``content`` at all. Read
    only ``content`` and this healthy slot is stamped ERROR and its unit
    stopped: a false FAIL, which is worse than the bug the gate guards
    (``hermes_provision._smoke_chat_completion`` documents the same trap).
    """
    await slot_server.start(
        _thinking_model(
            "<|im_start|>assistant",
            reasoning="Okay, the user is asking about France. Its capital is Paris.",
        )
    )

    verdict = await _probe(slot_server.port, timeout_s=5.0)

    assert verdict.ok is True
    assert verdict.status == "ok_via_chat"


async def test_probe_fails_a_thinking_model_that_reasons_in_garbage(
    slot_server: FakeSlotServer,
) -> None:
    """Reading ``reasoning_content`` must not blunt the gate.

    The rc.6 garbage was garbage in every field the runner emits; a backend
    that mis-computes cannot produce the expected token in its reasoning
    either. If this ever passes, the fix for the thinking-model false-fail has
    turned the gate into a no-op.
    """
    await slot_server.start(_thinking_model(")))))))))", reasoning=")))))))))))))))))"))

    verdict = await _probe(slot_server.port, timeout_s=5.0)

    assert verdict.ok is False


async def test_probe_reads_a_think_block_left_inline_in_content(
    slot_server: FakeSlotServer,
) -> None:
    """Older/non-splitting runners leave the reasoning inside ``content``.

    ``reasoning_content`` is a convention, not a guarantee: llama.cpp builds
    (and ``--jinja``-less slots) hand back the raw ``<think>…</think>`` text in
    ``content``. Both shapes have to read as language, or the gate's verdict
    depends on the runner build rather than on the model.
    """
    await slot_server.start(
        _completion(
            "<|im_start|>",
            chat_text="<think>The user wants the capital of France. That is Paris.</think>",
        )
    )

    verdict = await _probe(slot_server.port, timeout_s=5.0)

    assert verdict.ok is True


async def test_chat_fallback_asks_for_more_tokens_than_the_raw_probe(
    slot_server: FakeSlotServer,
) -> None:
    """A dozen tokens is a raw-completion budget, not a chat one.

    A reasoning model drains a 12-token cap entirely into its ``<think>``
    block and returns empty ``content`` — the fallback then judges a working
    model on an answer it was never given room to write.
    """
    await slot_server.start(_completion("<|im_start|>", chat_text="Paris"))

    await _probe(slot_server.port, timeout_s=5.0)

    chat_path, chat_body = slot_server.seen[1]
    assert chat_path == "/v1/chat/completions"
    assert chat_body["max_tokens"] == output_sanity.SANITY_CHAT_MAX_TOKENS
    assert chat_body["max_tokens"] > output_sanity.SANITY_N_PREDICT
    assert chat_body["temperature"] == 0


async def test_chat_fallback_gets_its_own_wider_wall_clock(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """More tokens need more seconds, or the budget raise is a new false FAIL.

    Charging a 256-token chat turn against the raw probe's tight bound would
    just move the false failure from ``empty_output`` to ``probe_timeout`` on
    any slot that generates slowly (the CPU lane).
    """
    seen: list[tuple[str, float]] = []

    async def _fake_post(
        port: int, path: str, body: dict[str, Any], budget: float
    ) -> output_sanity.SanityVerdict:
        seen.append((path, budget))
        if path == "/completion":
            return output_sanity.SanityVerdict(ok=False, status="empty_output")
        return output_sanity.SanityVerdict(ok=True, status="ok", sample="Paris")

    monkeypatch.setattr(output_sanity, "_post", _fake_post)

    await _probe(8080)

    assert seen == [
        ("/completion", output_sanity.OUTPUT_SANITY_TIMEOUT_S),
        ("/v1/chat/completions", output_sanity.OUTPUT_SANITY_CHAT_TIMEOUT_S),
    ]
    assert output_sanity.OUTPUT_SANITY_CHAT_TIMEOUT_S > output_sanity.OUTPUT_SANITY_TIMEOUT_S


async def test_probe_reads_openai_shaped_bodies(slot_server: FakeSlotServer) -> None:
    """A runner that answers /completion with an OpenAI envelope still parses."""

    def _respond(path: str, body: dict[str, Any]) -> tuple[int, Any]:
        return 200, {"choices": [{"text": " Paris."}]}

    await slot_server.start(_respond)

    verdict = await _probe(slot_server.port, timeout_s=5.0)

    assert verdict.ok is True


# ── classification (pure) ───────────────────────────────────────────────────


@pytest.mark.parametrize(
    "text",
    [" Paris", "PARIS", "paris, France", "The capital of France is Paris."],
)
def test_classify_accepts_the_answer_in_any_casing(text: str) -> None:
    assert output_sanity.classify(text).ok is True


@pytest.mark.parametrize("text", ["París", "Parigi", "巴黎", "Париж"])
def test_classify_accepts_non_english_spellings(text: str) -> None:
    """False-failing a correct answer in another language would be worse than
    the bug: it would park a working slot in ERROR."""
    assert output_sanity.classify(text).ok is True


def test_classify_rejects_missing_content_field() -> None:
    """No text field at all is distinct from an empty one — both fail."""
    assert output_sanity.classify(None).status == "no_content"
    assert output_sanity.classify("   ").status == "empty_output"


def test_classify_never_keys_on_the_garbage_shape() -> None:
    """Any off-topic text fails, whatever shape the garbage takes.

    #1888: the garbage varies with argv, so a blocklist of known-bad strings
    would have caught one lane and missed the others. Only the positive
    assertion is stable.
    """
    for garbage in (")))))))", "!!!!!!", "的的的的的", "London", "���"):
        assert output_sanity.classify(garbage).ok is False


def test_failure_message_names_probe_expected_and_actual() -> None:
    """An ERROR that says "health check failed" is what cost two hours."""
    verdict = output_sanity.classify(")))))))")

    message = output_sanity.failure_message(verdict)

    assert "The capital of France is" in message
    assert "Paris" in message
    assert ")))" in message
    assert output_sanity.SANITY_ENV_VAR in message


# ── scoping / escape hatches ────────────────────────────────────────────────


def test_gate_applies_to_llm_slots() -> None:
    assert output_sanity.skip_reason({"type": "llm"}) is None


@pytest.mark.parametrize(
    "slot_type", ["embedding", "reranking", "tts", "transcription", "image", ""]
)
def test_gate_skips_non_chat_slot_types(slot_type: str) -> None:
    """An embedding slot cannot serve a chat completion — gating it on one
    would take out every embed/rerank/TTS/STT/image slot on the box."""
    assert output_sanity.skip_reason({"type": slot_type}) is not None


def test_gate_honours_the_env_opt_out(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv(output_sanity.SANITY_ENV_VAR, "0")
    assert output_sanity.skip_reason({"type": "llm"}) == "disabled_by_env"


def test_gate_is_on_by_default(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv(output_sanity.SANITY_ENV_VAR, raising=False)
    assert output_sanity.gate_disabled_globally() is False


def test_gate_honours_the_per_slot_opt_out() -> None:
    """One weird model on a box must not force the operator to disable the
    gate for every other slot."""
    flat = {"type": "llm", output_sanity.SANITY_CFG_KEY: False}
    nested = {"type": "llm", "slot": {output_sanity.SANITY_CFG_KEY: "off"}}

    assert output_sanity.skip_reason(flat) == "disabled_by_slot_config"
    assert output_sanity.skip_reason(nested) == "disabled_by_slot_config"


def test_the_per_slot_opt_out_is_writable_through_the_api() -> None:
    """The advertised escape hatch has to survive the write boundary.

    ``failure_message`` tells the operator to set ``output_sanity = false`` in
    the slot TOML — but ``POST /api/slots`` and ``PUT /api/slots/{name}/config``
    reject any key that is neither a ``SlotConfig`` field nor tolerated, so
    without registration the only way to take the hatch was a hand edit on the
    box (Codex P2 on this PR).
    """
    from hal0.slot_config import unknown_slot_config_keys

    assert unknown_slot_config_keys({output_sanity.SANITY_CFG_KEY: False}) == []
    assert unknown_slot_config_keys({"slot": {output_sanity.SANITY_CFG_KEY: False}}) == []


# ── the gate inside the load path ───────────────────────────────────────────


async def test_load_errors_when_the_slot_emits_garbage(
    slot_root: Path,
    container_stub: FakeContainerProvider,
) -> None:
    """THE regression. Unit active, /health 200, tokens flowing — and the
    output is garbage. The slot must NOT be published as READY."""
    container_stub.sanity_output = ")))))))))))))"
    sm = SlotManager()

    with pytest.raises(SlotOutputSanityFailed) as excinfo:
        await sm.load("chat")

    assert sm._current_state("chat") is SlotState.ERROR
    record = sm._states[sm._key("chat")]
    # Actionable: names the probe, the expected token, and what came back.
    assert "The capital of France is" in record.message
    assert "Paris" in record.message
    assert ")))" in record.message
    assert excinfo.value.code == "slot.output_sanity_failed"


async def test_failed_gate_survives_the_next_status_poll(
    slot_root: Path,
    container_stub: FakeContainerProvider,
) -> None:
    """A gate verdict that one dashboard poll can undo is not a gate.

    ``status()``'s inverse-drift branch adopts any ERROR/OFFLINE slot whose
    unit is ACTIVE straight to READY — and a slot that fails the sanity gate
    is exactly that shape (healthy unit, healthy /health, garbage output). So
    the failing load must also stop the unit; otherwise the very next
    ``/api/slots`` poll re-publishes the garbage slot as dispatchable and the
    box is back to reading green.
    """
    container_stub.sanity_output = ")))))))"
    sm = SlotManager()

    with pytest.raises(SlotOutputSanityFailed):
        await sm.load("chat")

    assert "chat" not in container_stub.active, "the garbage unit must be stopped"

    slot = await sm.status("chat")

    assert slot.state is SlotState.ERROR
    assert "Paris" in str(slot.metadata.get("message") or "")


async def test_failed_gate_survives_a_teardown_that_does_not_return(
    slot_root: Path,
    container_stub: FakeContainerProvider,
) -> None:
    """The verdict must not depend on a stop that can fail (#1224).

    ``terminate()`` raises ``SlotTerminateTimeout`` when ``systemctl stop``
    does not return inside its bound — documented, observed, and exactly what
    a wedged podman container does. If stopping the unit is the only thing
    keeping the gate's verdict, that failure hands the garbage slot straight
    back to the inverse-drift adopter: unit ACTIVE + record ERROR is precisely
    the shape it re-publishes as READY.
    """
    container_stub.sanity_output = ")))))))"
    container_stub.fail_unload = SlotTerminateTimeout("stopping slot 'chat' did not return")
    sm = SlotManager()

    with pytest.raises(SlotOutputSanityFailed):
        await sm.load("chat")

    # The unit really is still up — this is the durability question, not a
    # test of the teardown.
    assert "chat" in container_stub.active

    slot = await sm.status("chat")

    assert slot.state is SlotState.ERROR
    # ``adopted`` is the flag ``_maybe_adopt_running_slot`` stamps on the way
    # to READY — its absence is the proof the adopter stood down.
    assert slot.metadata.get("adopted") is not True
    assert "Paris" in str(slot.metadata.get("message") or "")


async def test_failed_gate_is_not_adopted_by_a_later_api_process(
    slot_root: Path,
    container_stub: FakeContainerProvider,
) -> None:
    """The refusal has to live on disk, not in the manager that stamped it.

    A gate failure whose only memory is in-process is undone by the next
    ``systemctl restart hal0-api``: boot-time upstream reconcile finds an
    active unit with an ERROR record and adopts it. Same hole as the status
    poll, one restart later — and it is the pre-upgrade-unit gap too.
    """
    container_stub.sanity_output = ")))))))"
    container_stub.fail_unload = SlotTerminateTimeout("stopping slot 'chat' did not return")
    with pytest.raises(SlotOutputSanityFailed):
        await SlotManager().load("chat")

    # A brand-new manager: nothing in memory, state.json and a live unit on
    # disk — the api-restart shape.
    fresh = SlotManager()

    slot = await fresh.status("chat")

    assert slot.state is SlotState.ERROR
    cfg = await fresh._maybe_load_config("chat")
    assert cfg is not None
    assert await fresh._maybe_adopt_running_slot("chat", cfg) is None


async def test_failed_teardown_is_recorded_not_swallowed(
    slot_root: Path,
    container_stub: FakeContainerProvider,
) -> None:
    """ "Stop failed" must be visible, because the unit is still burning VRAM.

    Suppressing the teardown error left an operator with a red slot, an
    active unit and no hint that the two were related.
    """
    container_stub.sanity_output = ")))))))"
    container_stub.fail_unload = SlotTerminateTimeout("stopping slot 'chat' did not return")
    sm = SlotManager()

    with pytest.raises(SlotOutputSanityFailed):
        await sm.load("chat")

    record = sm._states[sm._key("chat")]
    assert record.extra.get("output_sanity_failed") is True
    assert record.extra.get("output_sanity_unit_stopped") is False
    assert "still active" in record.message


async def test_a_passing_gate_clears_the_refusal(
    slot_root: Path,
    container_stub: FakeContainerProvider,
) -> None:
    """The marker is a verdict, not a tombstone.

    Once a load proves the model produces language, adoption must work again —
    otherwise a fixed box needs a hand-edited state.json. ``restart`` is the
    operator's verb here: it clears the crash-loop breaker the failed load
    armed, and re-enters the same lifecycle (and the same gate).
    """
    container_stub.sanity_output = ")))))))"
    sm = SlotManager()
    with pytest.raises(SlotOutputSanityFailed):
        await sm.load("chat")

    container_stub.sanity_output = " Paris."
    slot = await sm.restart("chat")

    assert slot.state is SlotState.READY
    record = sm._states[sm._key("chat")]
    assert "output_sanity_failed" not in record.extra


async def test_load_reaches_ready_when_the_slot_answers(
    slot_root: Path,
    container_stub: FakeContainerProvider,
) -> None:
    """The gate must not cost a working box its slot."""
    container_stub.sanity_output = " Paris."
    sm = SlotManager()

    slot = await sm.load("chat")

    assert slot.state is SlotState.READY
    assert container_stub.sanity_probes == [8081], "the gate runs exactly once per load"


async def test_a_slow_server_parks_the_slot_warming_instead_of_condemning_it(
    slot_root: Path,
    container_stub: FakeContainerProvider,
    slot_server: FakeSlotServer,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The CPU-lane shape: a real server that is merely SLOW must stay retryable.

    A probe timeout is ambiguous — it says the answer did not arrive inside a
    wall clock, not that the answer was wrong. Treating it as terminal turned
    the gate into a false-fail machine on exactly the box the release gets
    validated on: ct151 measured 0.12 tok/s under load-time contention, the
    container health probe runs no inference sentinel (so the gate is that
    box's first-ever inference), and a no-GPU box binds the unquantized F16
    brain variant. An ERROR there is not recoverable by retrying; it also
    stamps the durable un-adoptable mark.

    Real never-answering socket, real httpx round-trip, real timeout — only
    the wall clock is shrunk, so this is the production path.
    """
    await slot_server.start(lambda path, body: None)
    (slot_root / "chat.toml").write_text(
        "\n".join(
            [
                'name = "chat"',
                f"port = {slot_server.port}",
                'device = "cpu"',
                'provider = "llama-server"',
                "[model]",
                'default = "qwen3-4b-q4_k_m"',
                "",
            ]
        ),
        encoding="utf-8",
    )

    async def _slow(port: int, **_kw: Any) -> output_sanity.SanityVerdict:
        return await _probe(port, timeout_s=0.3)

    monkeypatch.setattr("hal0.slots.output_sanity.probe", _slow)
    sm = SlotManager()

    slot = await sm.load("chat")

    # Retryable, non-dispatchable, and NOT a lying READY.
    assert slot.state is SlotState.WARMING
    record = sm._states[sm._key("chat")]
    # No durable verdict: nothing here is proven bad, so nothing may be made
    # un-adoptable.
    for key in output_sanity.SANITY_EXTRA_KEYS:
        assert key not in record.extra
    # And the unit is left alone — the teardown belongs to a condemned slot.
    assert "chat" in container_stub.active
    assert container_stub.unload_calls == []


async def test_a_probe_timeout_leaves_the_slot_adoptable(
    slot_root: Path,
    container_stub: FakeContainerProvider,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """WARMING must not poison the adoption path the way a real verdict does.

    The durable mark is what makes a FAILED gate survive a restart. An
    ambiguous outcome earns none of that: the next load re-runs the gate and
    the slot converges on evidence, not on a stamp nothing cleared.
    """

    async def _timeout(port: int, **_kw: Any) -> output_sanity.SanityVerdict:
        return output_sanity.SanityVerdict(ok=False, status="probe_timeout")

    monkeypatch.setattr("hal0.slots.output_sanity.probe", _timeout)
    sm = SlotManager()

    await sm.load("chat")

    assert sm._current_state("chat") is SlotState.WARMING
    cfg = await sm._maybe_load_config("chat")
    assert cfg is not None
    assert await sm._maybe_adopt_running_slot("chat", cfg) is not None


async def test_a_slot_parked_warming_re_runs_the_gate_on_the_next_load(
    slot_root: Path,
    container_stub: FakeContainerProvider,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Convergence: WARMING is a retry, not a resting place.

    ``load``'s stale-in-flight branch (PULLING/STARTING/WARMING) tears the
    unit down and re-enters the lifecycle from OFFLINE, so the gate runs
    again on every retry. Without that the slot would sit un-judged forever
    behind an ambiguous verdict.
    """
    timeouts = True

    async def _probe_then_recover(port: int, **_kw: Any) -> output_sanity.SanityVerdict:
        if timeouts:
            return output_sanity.SanityVerdict(ok=False, status="probe_timeout")
        return output_sanity.classify(" Paris.")

    monkeypatch.setattr("hal0.slots.output_sanity.probe", _probe_then_recover)
    sm = SlotManager()
    await sm.load("chat")
    assert sm._current_state("chat") is SlotState.WARMING

    timeouts = False
    slot = await sm.load("chat")

    assert slot.state is SlotState.READY


async def test_a_dead_backend_still_converges_to_error_in_the_same_load(
    slot_root: Path,
    container_stub: FakeContainerProvider,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """WARMING must not become a hiding place for a genuinely dead slot.

    The ambiguity carve-out only says "the probe cannot tell". Something else
    still can: ``load`` asks systemd directly whenever the readiness resolve
    comes back WARMING (#1791), and a unit parked in ``failed`` / gone is
    proof no retry will help. That converts the ambiguous outcome into ERROR
    inside the same load, with the crash-loop bookkeeping the breaker needs —
    so a dead backend never rides a timeout into an unbounded retry loop.
    """

    async def _timeout(port: int, **_kw: Any) -> output_sanity.SanityVerdict:
        return output_sanity.SanityVerdict(ok=False, status="probe_timeout")

    monkeypatch.setattr("hal0.slots.output_sanity.probe", _timeout)
    container_stub.unit_failure_by_slot["chat"] = "start-limit-hit"
    sm = SlotManager()

    with pytest.raises(Exception) as excinfo:
        await sm.load("chat")

    assert "start-limit-hit" in str(excinfo.value)
    assert sm._current_state("chat") is SlotState.ERROR
    assert sm._load_failures[sm._key("chat")][0] == 1


async def test_garbage_stays_terminal_even_though_a_timeout_does_not(
    slot_root: Path,
    container_stub: FakeContainerProvider,
    slot_server: FakeSlotServer,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The #1888 signature is untouched by the timeout carve-out.

    rc.6's garbage box streamed at 60-120 tok/s and closed with a ``done``
    frame: garbage was FAST. A timeout was never its signature, so exempting
    timeouts removes false failures without weakening a single true positive.
    This pins the pair against one real server so the distinction cannot rot
    into "any probe failure is survivable".
    """
    await slot_server.start(_completion(")))))))))))))"))
    (slot_root / "chat.toml").write_text(
        "\n".join(
            [
                'name = "chat"',
                f"port = {slot_server.port}",
                'provider = "llama-server"',
                "[model]",
                'default = "qwen3-4b-q4_k_m"',
                "",
            ]
        ),
        encoding="utf-8",
    )

    async def _real(port: int, **_kw: Any) -> output_sanity.SanityVerdict:
        return await _probe(port, timeout_s=5.0)

    monkeypatch.setattr("hal0.slots.output_sanity.probe", _real)
    sm = SlotManager()

    with pytest.raises(SlotOutputSanityFailed):
        await sm.load("chat")

    assert sm._current_state("chat") is SlotState.ERROR
    record = sm._states[sm._key("chat")]
    assert record.extra.get(output_sanity.SANITY_FAILED_KEY) is True


def test_a_cpu_slot_gets_a_wider_probe_budget() -> None:
    """A slow-but-correct box must be judged on its answer, not on the clock.

    The accelerated lane's 20s is a fine bound for a GPU that decodes a dozen
    tokens instantly. A ``device="cpu"`` slot is the lane the fleet's own
    measurements say cannot clear it — and on a no-GPU box every seeded slot
    is derived to ``cpu`` (``install.profile_derive.derive_device``), so this
    is the default shape there, not an exotic one.
    """
    from hal0.slot_lifecycle_budget import OUTPUT_SANITY_CPU_TIMEOUT_S, OUTPUT_SANITY_TIMEOUT_S

    assert output_sanity.probe_budget_s({"device": "cpu"}) == OUTPUT_SANITY_CPU_TIMEOUT_S
    assert OUTPUT_SANITY_CPU_TIMEOUT_S > OUTPUT_SANITY_TIMEOUT_S
    # An accelerated slot keeps the module defaults (both of them — the raw
    # probe's and the chat fallback's), which ``None`` is the signal for.
    assert output_sanity.probe_budget_s({"device": "gpu-rocm"}) is None
    assert output_sanity.probe_budget_s({"device": "npu"}) is None
    assert output_sanity.probe_budget_s(None) is None
    # Legacy TOMLs carry only ``backend``; the cpu lane must be recognised
    # there too or the widening misses every un-migrated box.
    assert output_sanity.probe_budget_s({"backend": "cpu"}) == OUTPUT_SANITY_CPU_TIMEOUT_S


async def test_the_load_path_hands_the_probe_the_slot_s_budget(
    slot_root: Path,
    container_stub: FakeContainerProvider,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The derived budget is worthless if the gate does not pass it through."""
    from hal0.slot_lifecycle_budget import OUTPUT_SANITY_CPU_TIMEOUT_S

    (slot_root / "chat.toml").write_text(
        "\n".join(
            [
                'name = "chat"',
                "port = 8081",
                'device = "cpu"',
                'provider = "llama-server"',
                "[model]",
                'default = "qwen3-4b-q4_k_m"',
                "",
            ]
        ),
        encoding="utf-8",
    )
    seen: list[float | None] = []

    async def _record(port: int, *, timeout_s: float | None = None) -> output_sanity.SanityVerdict:
        seen.append(timeout_s)
        return output_sanity.classify(" Paris.")

    monkeypatch.setattr("hal0.slots.output_sanity.probe", _record)

    await SlotManager().load("chat")

    assert seen == [OUTPUT_SANITY_CPU_TIMEOUT_S]


async def test_load_skips_the_gate_for_non_llm_slots(
    slot_root: Path,
    container_stub: FakeContainerProvider,
) -> None:
    """An embedding slot loads without ever being asked to chat."""
    (slot_root / "embed.toml").write_text(
        "\n".join(
            [
                'name = "embed"',
                "port = 8093",
                'type = "embedding"',
                'provider = "llama-server"',
                "[model]",
                'default = "nomic-embed-text"',
                "",
            ]
        ),
        encoding="utf-8",
    )
    # A model that would fail the chat probe outright.
    container_stub.sanity_output = ""
    sm = SlotManager()

    slot = await sm.load("embed")

    assert slot.state is SlotState.READY
    assert container_stub.sanity_probes == []


async def test_load_skips_the_gate_when_disabled_by_env(
    slot_root: Path,
    container_stub: FakeContainerProvider,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The escape hatch has to actually reach the load path."""
    monkeypatch.setenv(output_sanity.SANITY_ENV_VAR, "0")
    container_stub.sanity_output = ")))))))"
    sm = SlotManager()

    slot = await sm.load("chat")

    assert slot.state is SlotState.READY
    assert container_stub.sanity_probes == []
