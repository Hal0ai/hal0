"""``RunnerImage`` — one runner-image catalogue row.

One row per hal0 runner, keyed by the runner key (``standard``, ``cpu``, …).
Facts come from two places merged at sync time
(:mod:`hal0.registry.runner_image_sync`): the runner-images repo's
``images.json`` (schema ``hal0.runner-images.v2``: title, summary, image,
tag, digest, backends, lanes_gated, family, tier) and an anonymous GHCR probe
(size, per-tag digests, build provenance labels).
"""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, Field


class RunnerImageTag(BaseModel):
    """One catalogued tag of a runner image, with its resolved digest."""

    tag: str
    digest: str | None = None
    size_bytes: int | None = None
    last_seen: str | None = None
    #: llama.cpp build provenance read from the image config blob's OCI
    #: labels during sync: ``{"source_repo", "revision", "patch_count"}``.
    #: ``None`` when the probe failed or the image carries none of them.
    provenance: dict[str, Any] | None = None


class RunnerImage(BaseModel):
    """A single runner-image catalogue entry. ``id`` is the runner key."""

    id: str
    image: str
    tag: str
    digest: str | None = None
    size_bytes: int | None = None
    title: str | None = None
    summary: str | None = None
    #: Runtime the image's entrypoint serves (``hal0.runners.RuntimeFamily``).
    family: str | None = None
    #: ``"default"`` | ``"optional"`` as the catalogue publishes it.
    tier: str | None = None
    #: Backends the image's runner binary can execute.
    backends: list[str] = Field(default_factory=list)
    #: Backends the hardware gate passed for ``digest``.
    lanes_gated: list[str] = Field(default_factory=list)
    #: Tags the GHCR ``tags/list`` probe saw, newest first.
    available_tags: list[str] = Field(default_factory=list)
    #: Per-tag digest facts — transport-only, populated by the store from
    #: ``runner_image_tag``; never JSON-encoded into the row itself.
    tags: list[RunnerImageTag] = Field(default_factory=list)

    # Local download state — set by hal0.registry.runner_pull once an image
    # has actually been pulled onto this host.
    local_path: str | None = None
    downloaded_at: str | None = None

    discovered_at: str | None = None
    updated_at: str | None = None
    extra: dict[str, Any] = Field(default_factory=dict)

    @property
    def downloaded(self) -> bool:
        """True once a local pull has landed for this image."""
        return bool(self.local_path)


__all__ = ["RunnerImage", "RunnerImageTag"]
