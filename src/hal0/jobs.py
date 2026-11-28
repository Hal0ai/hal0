"""Optional jobs a box may not have switched on, and the one refusal that names them.

A fresh install creates three slots (``agent``, ``brain``, ``memory``). The slots for
embeddings, reranking, speech, transcription, image generation and the NPU exist only
after their job is switched on. A route that needs one of those slots by role says so
plainly through :class:`CapabilityNotEnabled` instead of a 404, a 500 or a quiet fall
back to a chat model.

This module is the single owner of three things: which slot configs serve each job
(:func:`job_slot_present`), how the job is switched on (the command inside each message),
and the message text (:func:`capability_not_enabled_message`). Routes and checks call
those; none of them spells a message of its own.

Chat is not a job here. A chat request for a slot that is missing still ends at the
``agent`` slot through the routing fallback chain (ADR-0023).
"""

from __future__ import annotations

from collections.abc import Collection, Iterable, Mapping
from dataclasses import dataclass
from typing import Any

from hal0.dispatcher._capability_resolve import (
    _EMBED_PATHS,
    _IMAGE_PATHS,
    _RERANK_PATHS,
    _STT_PATHS,
    _TTS_PATHS,
)
from hal0.errors import Hal0Error

#: The wire code of the refusal. It is deliberately not namespaced: clients key on it.
CAPABILITY_NOT_ENABLED = "capability_not_enabled"


@dataclass(frozen=True)
class OptionalJob:
    """One job whose slot may be absent."""

    capability: str  #: the value of ``error.capability``
    label: str  #: the name the message opens with
    stock_name: str  #: the seed slot name the job is created under
    slot_types: frozenset[str]  #: ``[type]`` values of the slots that serve it
    switch_on: str | None  #: the command that switches it on, ``None`` when there is none


#: ``<model>`` is a placeholder: no job can be switched on without a model, so the message
#: tells the reader to choose it in the dashboard and shows this form for the CLI. Every
#: command here is run by ``tests/api/test_absent_job_slot.py`` against a box holding only
#: ``agent``, ``brain`` and ``memory``.
JOBS: dict[str, OptionalJob] = {
    "embed": OptionalJob(
        "embed",
        "Embedding",
        "embed",
        frozenset({"embedding"}),
        "hal0 capabilities set embed embed --model <model> --enabled",
    ),
    "rerank": OptionalJob(
        "rerank",
        "Reranking",
        "rerank",
        frozenset({"reranking"}),
        "hal0 capabilities set embed rerank --model <model> --enabled",
    ),
    "tts": OptionalJob(
        "tts",
        "Speech synthesis",
        "tts",
        frozenset({"tts"}),
        "hal0 capabilities set voice tts --model <model> --enabled",
    ),
    "stt": OptionalJob(
        "stt",
        "Transcription",
        "stt",
        frozenset({"transcription"}),
        "hal0 capabilities set voice stt --model <model> --enabled",
    ),
    "image": OptionalJob(
        "image",
        "Image generation",
        "img",
        frozenset({"image"}),
        "hal0 comfyui models install <model>",
    ),
    # The NPU slot is created by the NPU setup when a device is found and passes its
    # check, not by a command an operator runs, so no command is named for it.
    "npu": OptionalJob("npu", "NPU", "flm", frozenset(), None),
}

#: Request path fragment -> job. First match wins. The fragments are the dispatcher's own
#: path pins, imported so that :mod:`hal0.dispatcher._capability_resolve` is the one place
#: that owns them; the transcription path is the one the dispatcher never pinned. The job
#: -> slot type map (``slot_types`` above) cannot be derived from the resolver, so
#: ``tests/api/test_jobs_drift.py`` fails when the two stop agreeing.
_PATH_JOBS: tuple[tuple[tuple[str, ...], str], ...] = (
    (_EMBED_PATHS, "embed"),
    (_RERANK_PATHS, "rerank"),
    (_TTS_PATHS, "tts"),
    (_STT_PATHS, "stt"),
    (_IMAGE_PATHS, "image"),
)


def job_for_path(path: str) -> str | None:
    """The job a request path asks for, or ``None`` for chat and every other path."""
    for fragments, capability in _PATH_JOBS:
        if any(fragment in path for fragment in fragments):
            return capability
    return None


def job_for_slot_name(name: str) -> str | None:
    """The job a seed slot name stands for (``embed`` -> ``embed``, ``img`` -> ``image``)."""
    for job in JOBS.values():
        if job.stock_name == name:
            return job.capability
    return None


def capability_not_enabled_message(capability: str) -> str:
    """The sentence every refusal, health row and doctor row uses for ``capability``."""
    job = JOBS[capability]
    text = f"{job.label} is not set up on this box. Turn it on in the dashboard under Slots"
    if job.switch_on is None:
        return text + "."
    return f"{text} and choose a model, or run: {job.switch_on}"


def not_set_up_detail(capability: str) -> str:
    """Short form for a health or doctor row: informational, never a warning."""
    return f"not set up: {JOBS[capability].label.lower()} is switched off on this box"


class CapabilityNotEnabled(Hal0Error):
    """503: the slot for this job does not exist because the job was never switched on.

    Rendered by the error middleware as the usual envelope with ``capability`` carried
    beside ``code`` and ``message``.
    """

    code = CAPABILITY_NOT_ENABLED
    status = 503

    def __init__(self, capability: str) -> None:
        super().__init__(
            capability_not_enabled_message(capability),
            details={"capability": capability},
        )
        self.capability = capability

    @property
    def envelope_fields(self) -> dict[str, str]:
        """Extra keys the error middleware places in ``error`` next to ``code``."""
        return {"capability": self.capability}


def _matches(job: OptionalJob, cfg: Mapping[str, Any]) -> bool:
    """Whether the slot config ``cfg`` is one that serves ``job``."""
    if job.capability == "npu":
        return str(cfg.get("device") or "") == "npu" or cfg.get("name") == job.stock_name
    if cfg.get("name") == job.stock_name:
        return True
    if str(cfg.get("type") or "").strip().lower() in job.slot_types:
        return True
    # An image slot is also recognised by its ComfyUI provider or profile (a config
    # written before ``type`` was stamped carries only those).
    return job.capability == "image" and "comfyui" in (cfg.get("provider"), cfg.get("profile"))


def configs_serve(capability: str, configs: Iterable[Mapping[str, Any]]) -> bool:
    """Whether any slot config in ``configs`` serves the job."""
    job = JOBS[capability]
    return any(isinstance(cfg, Mapping) and _matches(job, cfg) for cfg in configs)


async def job_slot_present(
    slot_manager: Any,
    capability: str,
    *,
    upstream_names: Collection[str] = (),
) -> bool | None:
    """Whether the box has a slot for ``capability``; ``None`` when that cannot be told.

    ``None`` (no slot manager, or its inventory cannot be read) means the caller must
    behave as it did before: this module only ever turns a refusal that already happened
    into a clearer one. ``upstream_names`` lets a caller count an upstream registered
    under the job's seed name (a hand-registered slot) as present.
    """
    if slot_manager is None:
        return None
    try:
        configs = await slot_manager.iter_configs()
    except Exception:  # TIER2 — an unreadable inventory is "unknown", never "absent"
        return None
    if configs_serve(capability, configs):
        return True
    return JOBS[capability].stock_name in upstream_names


__all__ = [
    "CAPABILITY_NOT_ENABLED",
    "JOBS",
    "CapabilityNotEnabled",
    "OptionalJob",
    "capability_not_enabled_message",
    "configs_serve",
    "job_for_path",
    "job_for_slot_name",
    "job_slot_present",
    "not_set_up_detail",
]
