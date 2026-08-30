"""``RunnerImage`` — one catalogued container image row.

Deliberately small and JSON-native (unlike ``hal0.registry.model.Model``):
the runner-image catalogue has no launch-time recipe/defaults to validate,
just discovery facts (GHCR tag/digest/size) plus optional display metadata
merged in from the ``Hal0ai/hal0-runner-images`` repo's ``images.json``
manifest (schema ``hal0.runner-images.v1``). Every field except ``id``/
``image``/``tag`` is optional — a package discovered on GHCR with no
matching ``images.json`` entry is still a fully valid row.
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


class RunnerImage(BaseModel):
    """A single runner image catalogue entry.

    ``id`` is the stable key: the GHCR repo path with the registry host
    stripped (e.g. ``hal0ai/hal0-toolbox-cpu`` for
    ``ghcr.io/hal0ai/hal0-toolbox-cpu``) — this is what routes address and
    what ``images.json``'s ``image``/``manifest_key`` fields are matched
    against during sync.
    """

    id: str
    image: str
    tag: str = "latest"
    digest: str | None = None
    size_bytes: int | None = None
    #: Every tag the GHCR ``tags/list`` probe saw, newest first (see
    #: ``hal0.registry.runner_image_sync.sort_tags_newest_first``). ``[]``
    #: when the probe failed or hasn't run — the headline ``tag`` above
    #: keeps its own semantics (images.json pin, else newest, else latest).
    available_tags: list[str] = Field(default_factory=list)

    #: Per-tag digest facts (catalogue v3) — transport-only, populated by the
    #: store from runner_image_tag; never JSON-encoded into the row itself.
    tags: list[RunnerImageTag] = Field(default_factory=list)

    # images.json (hal0.runner-images.v1) fields — None when unmatched.
    manifest_key: str | None = None
    ownership: str | None = None  # "owned" | "referenced"
    publish: str | None = None  # "ci" | "external" | "manual"
    notes: str | None = None
    build: dict[str, Any] | None = None

    # Local download state — set by hal0.registry.runner_pull once an
    # image has actually been pulled onto this host.
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
