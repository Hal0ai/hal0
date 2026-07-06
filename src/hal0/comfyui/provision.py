"""WS-G (#1113): drive the ComfyUI per-variant download + img-slot activation.

Both entry points that opt into ``scaffold_and_download`` — the guided-setup
variant picker (:mod:`hal0.cli.setup_ui`) and the headless answer file's
``gen.mode: scaffold_and_download`` (:mod:`hal0.install.answers`) — funnel their
selected ``(capability_id, family)`` picks here.

Each pick resolves to a :class:`~hal0.comfyui.capabilities.ModelVariant`, whose
WORKING fetch (#1110's :func:`hal0.comfyui.fetch.fetch_model` — the non-blocking
bash-script downloader, which also provisions the matching workflow JSON and the
correct ``model_meta``/family) is queued. The img slot is activated ONLY after
the first model lands — a ComfyUI analog of WS-E's enable-on-pull-success
(#1108, :func:`hal0.install.orchestrate.run_pull_and_activate`): we never
advertise the img engine against an empty model dir. ``run_pull_and_activate``
itself cannot be reused verbatim (it drives a registry ``PullJob``, not a
ComfyUI subprocess fetch), so this module mirrors its discipline: create the
engine wiring, queue the fetch, and flip the slot live only once the bytes land.
If every fetch fails the img slot stays inactive (grey) and the caller is told.
"""

from __future__ import annotations

import logging
import time
from dataclasses import dataclass, field

from hal0.comfyui.capabilities import ModelVariant
from hal0.comfyui.fetch import fetch_model, get_job
from hal0.comfyui.selection import variant_for

log = logging.getLogger(__name__)

#: fetch-job statuses that mean the job is no longer running.
_TERMINAL = frozenset({"done", "failed", "cancelled"})


@dataclass
class ProvisionResult:
    """Outcome of a ComfyUI download provisioning run."""

    #: job_id → the variant it fetches (queued this run).
    jobs: dict[str, ModelVariant] = field(default_factory=dict)
    #: families whose fetch finished ``done``.
    landed: list[str] = field(default_factory=list)
    #: families whose fetch finished ``failed``/``cancelled``.
    failed: list[str] = field(default_factory=list)
    #: (capability_id, family) picks the resolver could not map to a variant.
    unknown: list[tuple[str, str]] = field(default_factory=list)
    #: True once the img slot was activated (first model landed).
    activated: bool = False


def resolve_variants(
    comfyui_defaults: tuple[tuple[str, str], ...],
) -> tuple[list[ModelVariant], list[tuple[str, str]]]:
    """Map ``(capability_id, family)`` picks to variants.

    Returns ``(variants, unknown)`` — unknown picks (bad capability/family) are
    collected rather than raised so one stale pick never blocks the rest.
    """
    variants: list[ModelVariant] = []
    unknown: list[tuple[str, str]] = []
    for cap_id, family in comfyui_defaults:
        try:
            variants.append(variant_for(cap_id, family))
        except KeyError as exc:
            log.warning(
                "comfyui.provision.unknown_variant",
                extra={"capability": cap_id, "family": family, "error": str(exc)},
            )
            unknown.append((cap_id, family))
    return variants, unknown


def estimate_totals(variants: list[ModelVariant]) -> tuple[float, int]:
    """Total ``(approx_gb, est_seconds)`` across *variants* — the picker/review
    'this download costs …' summary."""
    return (
        sum(v.approx_gb for v in variants),
        sum(v.est_seconds for v in variants),
    )


def _activate_img_slot() -> None:
    """Bring the ComfyUI img slot live (enable ``hal0-slot@img.service``).

    Delegates to :func:`hal0.install.extensions.install_extension` so activation
    goes through the SAME wiring the extension walk uses. Best-effort: a failure
    is logged, never raised — a landed model with an un-activatable slot is still
    a better state than crash-looping the caller (ADR-0010)."""
    try:
        from hal0.install.extensions import install_extension

        install_extension("comfyui")
    except Exception as exc:  # pragma: no cover - defensive
        log.warning("comfyui.provision.activate_failed", extra={"error": str(exc)})


def provision_comfyui_downloads(
    comfyui_defaults: tuple[tuple[str, str], ...],
    *,
    fetch=fetch_model,
    poll=get_job,
    activate=_activate_img_slot,
    sleep=time.sleep,
    poll_interval: float = 1.0,
    on_status=None,
) -> ProvisionResult:
    """Queue the working per-variant fetch for every pick, then wait — activating
    the img slot as soon as the FIRST model lands (enable-on-pull-success).

    Dependencies are injected (``fetch``/``poll``/``activate``/``sleep``) so the
    poll loop is deterministically unit-testable without real subprocesses.
    ``on_status(job_id, variant, status)`` is called on each observed terminal
    transition so a caller (the TUI) can render progress.

    Blocks until every queued fetch settles. Returns a :class:`ProvisionResult`.
    """
    variants, unknown = resolve_variants(comfyui_defaults)
    result = ProvisionResult(unknown=unknown)
    if not variants:
        return result

    for v in variants:
        job_id = fetch(v)
        result.jobs[job_id] = v

    remaining = set(result.jobs)
    while remaining:
        for job_id in list(remaining):
            job = poll(job_id)
            status = job.get("status") if job else "done"
            if status not in _TERMINAL:
                continue
            remaining.discard(job_id)
            variant = result.jobs[job_id]
            if status == "done":
                result.landed.append(variant.family)
                # Enable-on-pull-success: the first landed model makes the img
                # slot serviceable — flip it live now, never before.
                if not result.activated:
                    activate()
                    result.activated = True
            else:
                result.failed.append(variant.family)
            if on_status is not None:
                on_status(job_id, variant, status)
        if remaining:
            sleep(poll_interval)

    return result


__all__ = [
    "ProvisionResult",
    "estimate_totals",
    "provision_comfyui_downloads",
    "resolve_variants",
]
