"""Runner-image component arm (spec §1).

``resolve_runner_image`` (env override → manifest pin) answers "what does each
runner launch"; this arm reports that per runner as ``detail`` rows and, on an
actual update (``apply=True``), refreshes the runner-image catalogue from the
runner-images repo's ``images.json``. The catalogue sync is best-effort: a
failed fetch is reported on the result and never fails the update.
"""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

import structlog

log = structlog.get_logger(__name__)


def _detail_rows() -> list[dict[str, Any]]:
    from hal0.runners import RUNNER_IMAGES, resolve_runner_image

    rows: list[dict[str, Any]] = []
    for key, runner in sorted(RUNNER_IMAGES.items()):
        try:
            image: str | None = resolve_runner_image(runner)
        except Exception as exc:
            log.warning("components.runner_image_unresolved", runner=key, error=str(exc))
            image = None
        rows.append({"key": key, "image": image})
    return rows


def _sync_catalogue() -> int:
    from hal0.registry.runner_image_sync import sync_catalogue_blocking

    result = sync_catalogue_blocking()
    if result.images_json_error:
        raise RuntimeError(result.images_json_error)
    return len(result.images)


def converge_runner_images(
    *,
    job_id: str | None = None,
    apply: bool = True,
    sync: Callable[[], int] | None = None,
) -> dict[str, Any]:
    result: dict[str, Any] = {"detail": _detail_rows(), "status": "converged"}
    if not apply:
        return result
    try:
        result["catalogue_rows"] = (sync or _sync_catalogue)()
    except Exception as exc:
        # Best-effort by contract: the catalogue is display state. The next
        # API boot (or a manual Sync) retries; the update itself is fine.
        log.warning("components.runner_catalogue_sync_failed", job_id=job_id, error=str(exc))
        result["catalogue_sync_error"] = str(exc)
    return result
