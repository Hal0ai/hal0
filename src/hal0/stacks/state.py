"""Active-stack pointer + content hashing for drift detection (spec §7).

Mirrors the slot state pattern (``hal0.slots.state.write_state_atomic``): a
JSON record written tmpfile+fsync+rename so readers never see a torn file.
The content hash fingerprints the slot-TOML projection a stack applied, so a
later hand-edit can be detected as drift (``clean`` vs ``modified``).
"""

from __future__ import annotations

import hashlib
import json
import os
import tempfile
from contextlib import suppress
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from hal0.errors import Hal0Error


class StacksStateUnreadable(Hal0Error):
    """Stacks state.json / stacks.toml exists but the service cannot read it.

    Namespace mirrors ``hal0.slots.state.SlotError`` (``slot.*`` → ``stacks.*``).
    Raised instead of a raw ``PermissionError`` so /api/stacks surfaces an
    actionable 500 naming the unreadable path (ct105 outage, 2026-07-12→08-24:
    root-written 0600 files left the hal0-user API returning ``system.internal``).
    """

    code = "stacks.state_unreadable"
    status = 500


@dataclass(frozen=True)
class StackStateRecord:
    """Which stack is applied, the hash of what it wrote, and whether converge
    brought runtime up cleanly.

    ``converge_ok`` is ``False`` when the apply committed config to disk (Phase A)
    but one or more per-slot lifecycle steps failed during converge (Phase B), so
    live disk still matches the fingerprint yet the runtime never fully came up.
    Drift detection reads it to tell ``clean`` from ``degraded`` (PS-5 part 2).
    Older records lacking the field default to ``True`` (assume a clean converge).
    """

    active_slug: str
    content_hash: str
    applied_at: float
    converge_ok: bool = True

    def to_dict(self) -> dict[str, Any]:
        return {
            "active_slug": self.active_slug,
            "content_hash": self.content_hash,
            "applied_at": self.applied_at,
            "converge_ok": self.converge_ok,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> StackStateRecord:
        return cls(
            active_slug=str(data.get("active_slug", "")),
            content_hash=str(data.get("content_hash", "")),
            applied_at=float(data.get("applied_at", 0.0)),
            converge_ok=bool(data.get("converge_ok", True)),
        )


def stack_content_hash(projection: dict[str, dict[str, Any] | None]) -> str:
    """sha256 over the canonical slot→TOML-dict projection.

    Canonical serialization is ``json.dumps(sort_keys=True)`` so the hash is
    independent of dict key order (repo convention: slots/state.py, content_hash
    in agents/hermes_provision.py). Keyed by slot name → portable across hosts.
    """
    payload = json.dumps(projection, sort_keys=True, default=str)
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def write_stack_state_atomic(path: Path | str, record: StackStateRecord) -> None:
    """Persist the active-stack pointer atomically (tmpfile + fsync + replace)."""
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = json.dumps(record.to_dict(), indent=2, sort_keys=True) + "\n"

    tmp_path: Path | None = None
    try:
        fd, tmp_str = tempfile.mkstemp(prefix=".hal0-stack-state-", suffix=".tmp", dir=path.parent)
        tmp_path = Path(tmp_str)
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as f:
                f.write(payload)
                f.flush()
                os.fsync(f.fileno())
                # mkstemp creates 0600 by design; this file is service-shared
                # state under a setgid ``hal0`` dir. A root-run CLI leaving it
                # 0600 root:root 500'd every /api/stacks read for the hal0-user
                # API (ct105, 2026-07-12→08-24) — chmod before replace so the
                # group always keeps rw.
                os.fchmod(f.fileno(), 0o664)
        except BaseException:
            with suppress(OSError):
                os.close(fd)
            raise
        os.replace(tmp_path, path)
        tmp_path = None
    finally:
        if tmp_path is not None:
            with suppress(OSError):
                tmp_path.unlink(missing_ok=True)


def read_stack_state(path: Path | str) -> StackStateRecord | None:
    """Read the active-stack pointer, or ``None`` when absent or corrupt.

    A missing file is the normal "no stack applied" case. A corrupt/truncated
    state.json (invalid JSON or a non-object top-level) degrades to the same —
    a cosmetic status read must never raise.

    Raises:
        StacksStateUnreadable: If the file exists but cannot be read
            (permissions) — unlike absence/corruption this is an operator
            problem that must surface, not degrade to "no stack applied".
    """
    try:
        with open(path, encoding="utf-8") as f:
            data = json.load(f)
    except FileNotFoundError:
        return None
    except json.JSONDecodeError:
        return None
    except PermissionError as exc:
        raise StacksStateUnreadable(
            f"stacks state.json unreadable at {path}: {exc}; fix: chgrp hal0 && chmod 640",
            details={"path": str(path)},
        ) from exc
    if not isinstance(data, dict):
        return None
    return StackStateRecord.from_dict(data)
