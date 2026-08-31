"""Runner-image component arm (spec §1).

The heavy lifting already exists: ``resolve_runner_image`` (env override →
manifest pin → bundled default) answers "what should each slot run" and
``retag_stale_slot_images`` moves slot TOMLs + profiles off known former
defaults during an update. This arm wraps them into the component
status-dict shape and contributes per-image ``detail`` rows.
"""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

import structlog

log = structlog.get_logger(__name__)


def converge_runner_images(
    *,
    job_id: str | None = None,
    apply: bool = True,
    retag: Callable[..., int] | None = None,
) -> dict[str, Any]:
    from hal0.runners import RUNNER_IMAGES, resolve_runner_image

    detail = [
        {"key": key, "image": resolve_runner_image(runner)}
        for key, runner in sorted(RUNNER_IMAGES.items())
    ]
    result: dict[str, Any] = {"detail": detail}
    if not apply:
        return {**result, "status": "converged"}
    if retag is None:
        from hal0.updater.updater import retag_stale_slot_images

        retag = retag_stale_slot_images
    try:
        retagged = retag(job_id=job_id)
    except Exception as exc:
        return {**result, "status": "build_failed", "error": f"retag failed: {exc}"}
    return {**result, "status": "converged", "retagged": retagged}
