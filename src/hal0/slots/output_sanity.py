"""Output-sanity gate for llm slot readiness (#1922).

WHY THIS EXISTS
---------------
Every readiness surface hal0 owns is a *transport* proof, not an *output*
proof.  ``GET /health`` returning 200 means the runner bound its port and
finished mapping weights; it says nothing about whether the backend can turn
those weights into coherent logits.  On ct151 during ``v1.0.0-rc.6`` a box
served two hours of garbage — runs of ``)`` on ``--jinja`` slots, empty
content on a bare ``/completion`` — while slot state read ``ready``,
``/api/health`` was green, ``hal0 doctor`` said "OK gpu", the SSE stream
closed with a ``done`` frame and throughput measured 60-120 tok/s (#1888).
Five validation lanes reported five different symptoms; all five were that one
defect.

The generic llama-server lane was the hole: :class:`~hal0.providers.flm.
FLMProvider` slots already get a one-shot real-inference gate at the
warm→ready promotion (``verify_inference``), but the container/llama-server
lane had no inference gate at all — ``wait_ready`` → ``/health`` 200 was the
whole proof.  This module closes it with one greedy completion whose answer is
known.

DESIGN RULES (learned from the FLM gate and from #1888)
------------------------------------------------------
* **Positive assertion only.**  Never key on the garbage string: its shape
  varies with argv (``)))))`` vs empty content vs mojibake).  The only stable
  check is "did the expected token appear".
* **One shot, off the hot path.**  Runs exactly once per load, inside
  ``_await_ready`` where the slot holds its lock and is not yet dispatchable.
  It must never join the 2s fail-watch or the per-request readiness gate.
* **A timeout is a FAIL.**  A runner that cannot emit 12 greedy tokens inside
  :data:`OUTPUT_SANITY_TIMEOUT_S` is not ready, and "inconclusive → pass" is
  exactly the lie this gate exists to stop.
* **Scoped to chat-capable slots.**  ``type="llm"`` is the capability signal
  (``hal0.model_meta.modality.slot_type_for`` derives the slot type from the
  model's declared modalities), so an embedding / rerank / TTS / STT / image
  slot is never blocked on a chat completion it cannot serve.
* **Escapable.**  Operators running non-English or non-instruct models can
  turn the gate off globally (``HAL0_SLOT_OUTPUT_SANITY=0``) or per slot
  (``output_sanity = false`` in the slot TOML).  Default ON.
"""

from __future__ import annotations

import logging
import os
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any

from hal0.slot_lifecycle_budget import OUTPUT_SANITY_TIMEOUT_S

log = logging.getLogger(__name__)

#: The probe prompt.  A raw *completion* prompt, not a chat turn: it needs no
#: chat template, so it behaves identically on instruct and base models and on
#: slots launched with or without ``--jinja`` (the argv difference that
#: produced two different garbage shapes in #1888).
SANITY_PROMPT = "The capital of France is"

#: Greedy decode, a dozen tokens.  Enough for the answer to appear after a
#: leading space/newline or a short restatement, cheap enough to be invisible
#: in the slot lifecycle budget.
SANITY_N_PREDICT = 12

#: Accepted continuations, matched case-insensitively as substrings.  English
#: first; the transliterations cover the non-English fine-tunes that would
#: otherwise false-fail on a correct answer.  Extending this list is always
#: safe — it can only turn a FAIL into a PASS, never the reverse.
EXPECTED_TOKENS: tuple[str, ...] = (
    "paris",
    "parís",
    "parigi",
    "parijs",
    "париж",
    "巴黎",
    "パリ",
    "파리",
)

#: Global opt-out.  Any of ``0`` / ``false`` / ``no`` / ``off`` disables the
#: gate for every slot on the box; unset means ON.
SANITY_ENV_VAR = "HAL0_SLOT_OUTPUT_SANITY"

#: Per-slot opt-out key in the slot TOML (top level or under ``[slot]``).
SANITY_CFG_KEY = "output_sanity"

#: Slot types the gate applies to.  Everything else serves a modality a chat
#: completion cannot probe.
_GATED_SLOT_TYPES: frozenset[str] = frozenset({"llm"})

_FALSEY = frozenset({"0", "false", "no", "off"})

#: Longest sample of the model's actual output carried into the error message
#: and the log.  Long enough to recognise the failure shape, short enough that
#: a wall of ``)`` does not swamp the state record's ``message``.
_SAMPLE_CHARS = 120

#: The only failures worth a second opinion: ones where the round-trip
#: SUCCEEDED and the model's answer is what fell short (or where the endpoint
#: simply is not served).  A timeout, a transport error, a 5xx or an
#: unparseable body are facts about the round-trip; asking a second endpoint
#: cannot clarify them and would double the failure path's wall clock.
_SECOND_OPINION_STATUSES: frozenset[str] = frozenset(
    {"empty_output", "incoherent_output", "no_content", "probe_absent"}
)


@dataclass(frozen=True, slots=True)
class SanityVerdict:
    """Outcome of one output-sanity probe.

    ``ok`` is the only field the caller branches on.  ``status`` is the stable
    machine-readable reason (logged, and carried in the typed error's
    ``details``); ``sample`` is the truncated text the model actually produced,
    which is what an operator needs to recognise a Vulkan-garbage box.
    """

    ok: bool
    status: str
    sample: str = ""
    detail: str = ""


def _falsey(raw: Any) -> bool:
    """True when ``raw`` reads as an explicit "off"."""
    if isinstance(raw, bool):
        return not raw
    return str(raw).strip().lower() in _FALSEY


def gate_disabled_globally() -> bool:
    """True when :data:`SANITY_ENV_VAR` turns the gate off box-wide."""
    raw = os.environ.get(SANITY_ENV_VAR, "").strip()
    return bool(raw) and _falsey(raw)


def skip_reason(cfg: Mapping[str, Any] | None) -> str | None:
    """Why the gate must NOT run for this slot — ``None`` means "run it".

    The string is a log/telemetry reason, never a user-facing message.  Order
    matters only for the log line: any single reason is sufficient.
    """
    if gate_disabled_globally():
        return "disabled_by_env"
    if not isinstance(cfg, Mapping):
        return "no_config"
    raw = cfg.get(SANITY_CFG_KEY)
    if raw is None:
        slot_tbl = cfg.get("slot")
        if isinstance(slot_tbl, Mapping):
            raw = slot_tbl.get(SANITY_CFG_KEY)
    if raw is not None and _falsey(raw):
        return "disabled_by_slot_config"
    slot_type = str(cfg.get("type") or "").strip().lower()
    if slot_type not in _GATED_SLOT_TYPES:
        # Includes the empty string: a type-less config is healed to "llm" on
        # load (``_cfg_helpers.heal_missing_llm_type``), so an *unhealed*
        # blank here means the config never went through the loader and we
        # cannot claim it is chat-capable.
        return f"slot_type_{slot_type or 'unset'}"
    return None


def _extract_text(body: Any) -> str | None:
    """Pull the generated text out of a completion response body.

    Handles llama-server's native ``/completion`` shape (``content``) and the
    OpenAI-compatible shapes (``choices[0].text`` /
    ``choices[0].message.content``) so the probe survives a runner that
    answers ``/completion`` with an OpenAI envelope.  ``None`` means "no text
    field at all" (unparseable); ``""`` means "the field was there and empty",
    which is a real, reportable failure shape (#1888: bare ``/completion``
    emitted empty content while streaming a ``done`` frame).
    """
    if not isinstance(body, Mapping):
        return None
    content = body.get("content")
    if isinstance(content, str):
        return content
    choices = body.get("choices")
    if isinstance(choices, list) and choices:
        first = choices[0]
        if isinstance(first, Mapping):
            text = first.get("text")
            if isinstance(text, str):
                return text
            message = first.get("message")
            if isinstance(message, Mapping):
                msg_content = message.get("content")
                if isinstance(msg_content, str):
                    return msg_content
    return None


def classify(text: str | None) -> SanityVerdict:
    """Judge one completion's text.  Pure — no I/O, no config.

    ``None`` (no text field in the body) → ``no_content``; whitespace-only →
    ``empty_output``; text without any :data:`EXPECTED_TOKENS` member →
    ``incoherent_output``; otherwise pass.
    """
    if text is None:
        return SanityVerdict(ok=False, status="no_content")
    stripped = text.strip()
    if not stripped:
        return SanityVerdict(ok=False, status="empty_output")
    sample = stripped[:_SAMPLE_CHARS]
    folded = stripped.casefold()
    if any(token in folded for token in EXPECTED_TOKENS):
        return SanityVerdict(ok=True, status="ok", sample=sample)
    return SanityVerdict(ok=False, status="incoherent_output", sample=sample)


async def _post(port: int, path: str, body: dict[str, Any], budget: float) -> SanityVerdict:
    """POST one probe request and turn the whole round-trip into a verdict.

    Never raises. Every non-content outcome gets a ``probe_``-prefixed status
    so :func:`probe` can tell "the model said something wrong" (worth a second
    opinion) from "the round-trip did not happen" (nothing to second-guess).
    """
    import httpx

    from hal0.http_client import async_client

    try:
        async with async_client(timeout=httpx.Timeout(budget)) as client:
            resp = await client.post(f"http://127.0.0.1:{port}{path}", json=body)
    except httpx.TimeoutException:
        # A runner that cannot produce a dozen greedy tokens inside the budget
        # is wedged. Explicitly a FAIL: "the probe timed out so let it
        # through" is the exact silent degrade #1922 was filed to remove.
        return SanityVerdict(
            ok=False,
            status="probe_timeout",
            detail=f"no completion within {budget:g}s",
        )
    except (httpx.HTTPError, OSError) as exc:
        return SanityVerdict(
            ok=False,
            status="probe_transport_error",
            detail=str(exc) or type(exc).__name__,
        )

    if resp.status_code == 404:
        # This runner does not serve this endpoint at all.
        return SanityVerdict(ok=False, status="probe_absent")
    if resp.status_code != 200:
        return SanityVerdict(
            ok=False,
            status=f"probe_http_{resp.status_code}",
            detail=(resp.text or "")[:_SAMPLE_CHARS],
        )
    try:
        parsed = resp.json()
    except ValueError:
        return SanityVerdict(
            ok=False,
            status="probe_unparseable",
            detail=(resp.text or "")[:_SAMPLE_CHARS],
        )
    return classify(_extract_text(parsed))


async def probe(port: int, *, timeout_s: float | None = None) -> SanityVerdict:
    """Judge whether the server on ``port`` produces language.

    Asks ``/completion`` — the raw, template-free endpoint — for a dozen
    greedy tokens.  If the answer is right, done: one request, no second
    opinion needed.

    If the answer is WRONG, the model gets exactly one more chance, through
    ``/v1/chat/completions`` with the same prompt as a user turn.  A raw
    prompt and a chat-templated prompt are genuinely different inputs, and a
    heavily template-dependent instruct model can answer one well and the
    other poorly — so failing a working slot on the strength of a single
    endpoint is the one mistake this gate must not make.  The retry costs
    nothing on a healthy box (it only runs on the failing path, where the
    load is about to be refused anyway) and it does not weaken the gate: the
    rc.6 garbage reproduced on BOTH endpoints (#1888), which is exactly what
    "the backend mis-computes" means.

    Never raises: every transport failure resolves to a verdict so the caller
    owns the state transition.  Failure is the default for anything that is
    not a clean, on-topic answer — with one exception: a runner that serves
    NEITHER endpoint (404 on both) is not a completion server this gate was
    designed to probe, and "cannot judge" must not read as "judged bad".
    """
    if port <= 0:
        return SanityVerdict(ok=False, status="no_port")

    budget = float(timeout_s if timeout_s is not None else OUTPUT_SANITY_TIMEOUT_S)
    verdict = await _post(
        port,
        "/completion",
        {
            "prompt": SANITY_PROMPT,
            "n_predict": SANITY_N_PREDICT,
            "temperature": 0,
            "stream": False,
            # Never seed the runner's prompt cache from a probe: the gate must
            # measure the model, not warm it.
            "cache_prompt": False,
        },
        budget,
    )
    if verdict.ok or verdict.status not in _SECOND_OPINION_STATUSES:
        return verdict

    chat = await _post(
        port,
        "/v1/chat/completions",
        {
            "messages": [{"role": "user", "content": SANITY_PROMPT}],
            "max_tokens": SANITY_N_PREDICT,
            "temperature": 0,
            "stream": False,
        },
        budget,
    )
    if chat.ok:
        log.info(
            "slot.output_sanity_recovered_via_chat",
            extra={"port": port, "raw_status": verdict.status},
        )
        return SanityVerdict(ok=True, status="ok_via_chat", sample=chat.sample)
    if verdict.status == "probe_absent" and chat.status == "probe_absent":
        # Serves neither endpoint → not a completion server at all. Cannot
        # judge; see the docstring.
        return SanityVerdict(ok=True, status="probe_unsupported")
    # Both endpoints answered badly. Report the RAW verdict — its sample is
    # the diagnostic one (the operator needs to see the ")))") — annotated
    # with what the chat endpoint said, so the retry is visible in the log.
    return SanityVerdict(
        ok=False,
        status=verdict.status if verdict.status != "probe_absent" else chat.status,
        sample=verdict.sample or chat.sample,
        detail=f"chat endpoint agreed ({chat.status})",
    )


def failure_message(verdict: SanityVerdict) -> str:
    """Operator-facing one-liner for a failed verdict.

    Names the probe, the expected token and what the model actually said, then
    points at the escape hatch — an ERROR whose ``message`` reads "health
    check failed" is what made #1888 take two hours to diagnose.
    """
    what = f"output-sanity probe failed ({verdict.status})"
    detail = f"{SANITY_PROMPT!r} → expected {EXPECTED_TOKENS[0].capitalize()!r}"
    if verdict.sample:
        detail += f", got {verdict.sample!r}"
    if verdict.detail:
        detail += f" [{verdict.detail}]"
    return (
        f"{what}: {detail}. The model server answers /health but does not produce "
        f"coherent text — check the runner image and GPU backend. Set "
        f"{SANITY_ENV_VAR}=0 (or {SANITY_CFG_KEY} = false in the slot TOML) to bypass."
    )


__all__ = [
    "EXPECTED_TOKENS",
    "OUTPUT_SANITY_TIMEOUT_S",
    "SANITY_CFG_KEY",
    "SANITY_ENV_VAR",
    "SANITY_N_PREDICT",
    "SANITY_PROMPT",
    "SanityVerdict",
    "classify",
    "failure_message",
    "gate_disabled_globally",
    "probe",
    "skip_reason",
]
