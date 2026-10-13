"""StacksCatalog — read and mutate the stack catalog through one interface.

A stack is a named bundle of slots + embedded profiles + embedded model
metadata. This module concentrates the stack interface, mirroring
``hal0.profiles.ProfileCatalog``:

* full-catalog reads and atomic full-catalog writes (single stacks.toml); seed
  stacks are virtual, overlaid from code on read and never written;
* seed immutability and duplicate-slug checks;
* slug validation.

Routes (PR-4) and the apply engine (PR-2) are adapters over this module.
"""

from __future__ import annotations

import logging
import re
import threading
from dataclasses import dataclass
from pathlib import Path

from hal0.config import paths, schema
from hal0.config.loader import load_stacks_config, save_stacks_config
from hal0.config.schema import (
    ProfileConfig,
    StackConfig,
    StackModelMeta,
    StackSlotEntry,
)
from hal0.errors import Conflict, NotFound

log = logging.getLogger(__name__)

_STACK_NAME_RE = re.compile(r"^[a-z0-9][a-z0-9_-]{0,31}$")


@dataclass(frozen=True)
class ResolvedStack:
    """A stack plus derived fields (slug + seed flag) for API/UI consumption."""

    slug: str
    name: str
    description: str
    author: str
    icon: str
    tags: tuple[str, ...]
    slots: list[StackSlotEntry]
    profiles: dict[str, ProfileConfig]
    models: dict[str, StackModelMeta]
    schema_version: int
    hal0_version: str
    seed: bool


class StacksCatalog:
    """Read and mutate the stack catalog through one interface."""

    def __init__(self, *, path: Path | None = None) -> None:
        self._path = path
        self._lock = threading.Lock()

    def _path_or_default(self) -> Path:
        return self._path or paths.stacks_toml()

    def list(self) -> list[ResolvedStack]:
        cfg = load_stacks_config(self._path)
        return [self._resolve_item(slug, stack) for slug, stack in cfg.stack.items()]

    def resolve(self, slug: str) -> ResolvedStack:
        cfg = load_stacks_config(self._path)
        stack = cfg.stack.get(slug)
        if stack is None:
            raise NotFound(
                f"stack {slug!r} not found",
                code="stacks.not_found",
                details={"stack": slug, "available": sorted(cfg.stack)},
            )
        return self._resolve_item(slug, stack)

    def create(self, slug: str, stack: StackConfig) -> ResolvedStack:
        self._validate_name(slug)
        with self._lock:
            catalog = load_stacks_config(self._path)
            if slug in catalog.stack:
                raise Conflict(
                    f"stack {slug!r} already exists",
                    code="stacks.exists",
                    details={"stack": slug},
                )
            catalog.stack[slug] = stack
            save_stacks_config(catalog, self._path)
        return self._resolve_item(slug, stack)

    def update(self, slug: str, stack: StackConfig) -> ResolvedStack:
        """Replace the stack body wholesale (PUT semantics)."""
        self._guard_custom(slug)
        with self._lock:
            catalog = load_stacks_config(self._path)
            if slug not in catalog.stack:
                raise NotFound(
                    f"stack {slug!r} not found",
                    code="stacks.not_found",
                    details={"stack": slug},
                )
            catalog.stack[slug] = stack
            save_stacks_config(catalog, self._path)
        return self._resolve_item(slug, stack)

    def delete(self, slug: str) -> None:
        self._guard_custom(slug)
        with self._lock:
            catalog = load_stacks_config(self._path)
            if slug not in catalog.stack:
                raise NotFound(
                    f"stack {slug!r} not found",
                    code="stacks.not_found",
                    details={"stack": slug},
                )
            del catalog.stack[slug]
            save_stacks_config(catalog, self._path)

    def _resolve_item(self, slug: str, stack: StackConfig) -> ResolvedStack:
        return ResolvedStack(
            slug=slug,
            name=stack.name,
            description=stack.description,
            author=stack.author,
            icon=stack.icon,
            tags=tuple(stack.tags),
            slots=stack.slots,
            profiles=stack.profiles,
            models=stack.models,
            schema_version=stack.schema_version,
            hal0_version=stack.hal0_version,
            seed=slug in schema.SEED_STACKS,
        )

    def _guard_custom(self, slug: str) -> None:
        if slug in schema.SEED_STACKS:
            raise Conflict(
                f"stack {slug!r} is a seed stack — seed stacks are immutable; "
                "clone under a new name",
                code="stacks.seed_immutable",
                details={"stack": slug},
            )

    def _validate_name(self, slug: str) -> None:
        if not _STACK_NAME_RE.match(slug):
            raise Conflict(
                "stack slug must be kebab-case (a-z0-9_-), ≤32 chars, start with alphanumeric",
                code="stacks.invalid_name",
                details={"stack": slug},
            )
