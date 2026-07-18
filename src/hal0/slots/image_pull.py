"""Container-image pull orchestration for slots (extracted from routes/slots.py).

The route layer previously inlined the whole vertical: a layer-oriented job
object, the background pull runner that drives
``container_provider().pull_image_stream``, and — copied three times across the
POST / stream / status handlers — the profile→image resolver and the
present|missing image inspection. Those moved here (P3-routers §J) so the
``pull_slot_image*`` handlers are request→service→envelope shells (the two SSE /
BackgroundTasks wrappers stay in the route because they hold ``StreamingResponse``
/ the task-scheduler).

Interface contract:

    ImagePullJob(slot_name, image)
        Layer-oriented job (state pulling|completed|failed|present|missing,
        layer/total_layers progress, error); ``as_dict()`` is the wire frame.
    run_image_pull(job, request=None) -> None
        Drive the container pull in the background, updating ``job`` per line.
    resolve_slot_image(sm, name) -> str | None
        Resolve a slot's container image via its profile; None if unset.
    inspect_image_state(image) -> str
        "present" | "missing" for a resolved image (fail-soft → "missing").

The container/profile IO is done via lazy imports of
``hal0.providers.container`` / ``hal0.config.loader`` so the test-suite's
patches on ``ContainerProvider`` / ``load_profiles_config`` continue to bind.
"""

from __future__ import annotations

import asyncio
from typing import Any


class ImagePullJob:
    """Lightweight job object for a container-image pull.

    Tracks state (pulling | completed | failed), layer progress, and is
    polled by the 0.5-s SSE loop on each line of output.

    Unlike the HF-model PullJob (byte-oriented), this job is layer-oriented:
    layer = layers finished, total_layers = layers discovered.
    """

    __slots__ = ("error", "image", "layer", "slot_name", "state", "total_layers")

    def __init__(self, slot_name: str, image: str) -> None:
        self.slot_name = slot_name
        self.image = image
        self.state: str = "pulling"
        self.layer: int = 0
        self.total_layers: int = 0
        self.error: str | None = None

    def as_dict(self) -> dict[str, Any]:
        return {
            "slot_name": self.slot_name,
            "image": self.image,
            "state": self.state,
            "layer": self.layer,
            "total_layers": self.total_layers,
            "error": self.error,
        }


async def run_image_pull(job: ImagePullJob, request: Any = None) -> None:
    """Run the container pull in background, updating ``job`` per line.

    Writes progress into ``job`` so the 0.5-s polling SSE loop picks it up.
    ``request`` is accepted for signature compatibility (future event bus /
    slot invalidation) but not read currently.
    """
    from hal0.providers.container import container_provider

    cp = container_provider()
    try:
        async for chunk in cp.pull_image_stream(job.image):
            job.state = chunk.get("state", "pulling")
            job.layer = int(chunk.get("layer", job.layer))
            job.total_layers = int(chunk.get("total_layers", job.total_layers))
            if chunk.get("error"):
                job.error = str(chunk["error"])
            if job.state in ("completed", "failed"):
                break
    except Exception as exc:
        job.state = "failed"
        job.error = str(exc)


async def resolve_slot_image(sm: Any, name: str) -> str | None:
    """Resolve slot ``name``'s container image via its profile, or None.

    Fail-soft: any config/profile lookup error yields None so the caller
    reports a clean 400 / ``missing`` rather than 500-ing.
    """
    image: str | None = None
    try:
        configs = await sm.iter_configs()
        for cfg in configs:
            if str(cfg.get("name", "")) == name:
                profile_name = str(cfg.get("profile") or "")
                if profile_name:
                    from hal0.config.loader import load_profiles_config

                    catalog = load_profiles_config()
                    prof = catalog.profile.get(profile_name)
                    if prof:
                        image = prof.image
                break
    except Exception:
        pass
    return image


async def inspect_image_state(image: str | None) -> str:
    """Return "present" | "missing" for ``image`` (fail-soft → "missing")."""
    if not image:
        return "missing"
    try:
        from hal0.providers.container import container_provider

        present = await asyncio.get_event_loop().run_in_executor(
            None, container_provider().image_present, image
        )
        return "present" if present else "missing"
    except Exception:
        return "missing"
