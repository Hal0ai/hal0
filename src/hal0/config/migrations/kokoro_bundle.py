"""One-time upgrade pass: fetch the kokoro-v1 store bundle (#2739, ADR-0030).

Before 1.4.0 the kokoro tts slot launched from a profile-baked operator path
and the registry pull of ``kokoro-v1`` left a mislabelled ``model.gguf`` with
no voices pack. From 1.4.0 the provider launches only from the pulled bundle
in ``<store>/tts/kokoro-v1/`` and refuses to start without it. Without this
pass, every upgraded box with tts in use would lose speech until someone ran
``hal0 model pull kokoro-v1`` by hand.

So the post-activation migrations run the pull once, when kokoro is in use and
the store directory holds no loadable bundle. The pull's own cleanup replaces
an old mislabelled ``model.gguf``. A failed pull (an offline box) never fails
the update: the pass reports the outcome and the remedy, and the slot's spawn
preflight keeps naming the same remedy until the bundle is there.

This is a converter step, not a runtime fallback: it reads only the configured
store and fetches only the curated HF coordinates, through the normal pull.
"""

from __future__ import annotations

import asyncio
import os
from typing import Any

import structlog

log = structlog.get_logger(__name__)

#: The command that installs the bundle by hand.
KOKORO_PULL_REMEDY = "hal0 model pull kokoro-v1"


def kokoro_in_use() -> bool:
    """True when this box would launch kokoro.

    Either an enabled capability selection binds ``kokoro-v1``, or a slot
    resolves to the kokoro provider through the container dispatch authority
    (:func:`hal0.providers.container._spec_provider_for`), the same test
    ``hal0 doctor`` uses for moonshine. Unreadable config counts as not in use:
    the slot preflight still reports a missing bundle at load time.
    """
    from hal0.providers.kokoro import KOKORO_MODEL_ID, KokoroProvider

    try:
        from hal0.capabilities.config import load_capabilities_config

        caps = load_capabilities_config()
        for children in caps.selections.values():
            for sel in children.values():
                if sel.enabled and sel.model == KOKORO_MODEL_ID:
                    return True
    except Exception as exc:
        log.debug("updater.kokoro_bundle_capabilities_unreadable", error=str(exc))

    try:
        from hal0.config.loader import list_slots, load_slot_config
        from hal0.providers.container import _spec_provider_for

        for name in list_slots():
            try:
                cfg = load_slot_config(name).model_dump()
                if isinstance(_spec_provider_for(cfg), KokoroProvider):
                    return True
            except Exception:
                continue
    except Exception as exc:
        log.debug("updater.kokoro_bundle_slots_unreadable", error=str(exc))
    return False


async def _pull_kokoro_bundle(job_id: str | None) -> None:
    """Run the curated kokoro-v1 pull into ``<store>/tts/kokoro-v1/``; raise on failure."""
    from hal0.providers.kokoro import KOKORO_MODEL_ID
    from hal0.registry.curated import get_curated
    from hal0.registry.pull import make_job, run_pull
    from hal0.registry.store import ModelRegistry

    curated = get_curated(KOKORO_MODEL_ID)
    if curated is None:  # pragma: no cover - the row ships in curated.py
        raise RuntimeError(f"no curated row for {KOKORO_MODEL_ID}")
    job = make_job(KOKORO_MODEL_ID)
    await run_pull(
        job,
        hf_repo=curated.hf_repo,
        hf_file=curated.hf_file,
        registry=ModelRegistry(),
        hf_token=os.environ.get("HF_TOKEN") or os.environ.get("HUGGING_FACE_HUB_TOKEN"),
        capability=curated.capability,
    )
    if job.state != "completed":
        raise RuntimeError(job.error or f"pull ended {job.state}")


def ensure_kokoro_bundle(*, job_id: str | None = None, fetch: bool = True) -> dict[str, Any] | None:
    """Fetch the kokoro-v1 bundle once if kokoro is in use and it is missing.

    Returns ``None`` when there is nothing to report (kokoro unused, or the
    bundle is already loadable). Otherwise a report entry for the update
    output: ``{"model", "outcome", "path", "remedy", "error"?}`` where
    ``outcome`` is ``"pulled"``, ``"failed"`` or ``"missing"`` (``fetch`` off,
    the boot-time safety net, which never downloads). Never raises.
    """
    try:
        from hal0.providers.kokoro import (
            KOKORO_MODEL_ID,
            _is_kokoro_bundle,
            check_kokoro_bundle,
            kokoro_bundle_dir,
        )

        bundle = kokoro_bundle_dir()
        if bundle.is_dir() and _is_kokoro_bundle(bundle):
            return None
        if not kokoro_in_use():
            return None
        entry: dict[str, Any] = {
            "model": KOKORO_MODEL_ID,
            "path": str(bundle),
            "remedy": KOKORO_PULL_REMEDY,
        }
        if not fetch:
            log.warning(
                "updater.kokoro_bundle_missing",
                job_id=job_id,
                path=str(bundle),
                remedy=KOKORO_PULL_REMEDY,
            )
            return {**entry, "outcome": "missing"}

        log.info("updater.kokoro_bundle_pull_started", job_id=job_id, path=str(bundle))
        try:
            asyncio.run(_pull_kokoro_bundle(job_id))
            check_kokoro_bundle(bundle)
        except Exception as exc:
            log.warning(
                "updater.kokoro_bundle_pull_failed",
                job_id=job_id,
                path=str(bundle),
                error=str(exc),
                remedy=KOKORO_PULL_REMEDY,
            )
            return {**entry, "outcome": "failed", "error": str(exc)}
        log.info("updater.kokoro_bundle_pulled", job_id=job_id, path=str(bundle))
        return {**entry, "outcome": "pulled"}
    except Exception as exc:
        log.warning("updater.kokoro_bundle_pull_failed", job_id=job_id, error=str(exc))
        return {
            "model": "kokoro-v1",
            "outcome": "failed",
            "error": str(exc),
            "remedy": KOKORO_PULL_REMEDY,
        }
