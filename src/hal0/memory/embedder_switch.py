"""Move a memory store to another embedder (#3518, ADR-0058 decision 2).

``hal0 memory embedder switch <model>`` is the one way a store's embedder
changes. hindsight-api's embedder is instance-wide and a stored vector does
not say which model made it, so the only honest way to change the model is to
re-embed everything. The engine does that itself on a bank restore: an export
archive never carries embeddings, and ``transfer/import`` in ``restore`` mode
writes the whole bank into a new id, embedding every fact with the engine's
current model and making no LLM call.

The run, in order (:meth:`EmbedderSwitch.run`):

1. **Preflight** (:meth:`EmbedderSwitch.preflight`; the whole of a dry run).
   Refuses unless everything below holds. It writes nothing, anywhere.
2. **Pause writes** (:mod:`hal0.memory.write_gate`) before anything is
   exported, wait for the engine's queues to drain, then list the banks. That
   list, taken under the pause, is the set this run rebuilds.
3. **Export** every bank to ``<state>/backups/memory-embedder-switch-<run>/``
   and check each archive against the bank's counts. A fingerprint of the
   bank is taken with the counts (:meth:`EmbedderSwitch.bank_state`).
4. **Flip**: record the target, re-render the managed drop-in from it, restart
   the engine, wait for health, and confirm from the engine's own environment
   that it runs the target.
5. **Restore** every archive into ``<bank>.switch-<run>`` and compare facts,
   observations, mental models, directives and documents with the archive.
   Every bank is restored and compared before any original is touched.
6. **Swap**, bank by bank: check the original once more, delete it, rename
   the rebuilt bank into place, check the result. Then clear ``mixed`` and
   ``conflict`` on the record and lift the pause. The archives are kept.

Anything that fails before step 6 is undone (:meth:`EmbedderSwitch._rollback`):
temporary banks deleted, record and drop-in put back, engine restarted on the
original embedder, pause lifted. The originals were never touched.

Step 6 cannot be undone, so it is resumable instead. The journal
(:func:`journal_path`, mirrored into the archive directory on every save) is
written and synced *before* each delete and each rename, naming the step
about to happen. ``--resume`` (:meth:`EmbedderSwitch.resume`) never trusts a
journal step by itself: it looks at which of the two ids the engine has now
(:func:`swap_action`) and checks what it finds against the archive. Writes
stay paused until a resume completes.

What the pause does not cover
-----------------------------

The pause stops every write that goes through hal0. A client that writes to
the engine's own port directly (the Hindsight coding-agents plugin and the Pi
coder's plugin do today, ADR-0058 decision 4b) is not stopped, so every
original is compared with its export again before the first delete and again
immediately before its own delete. Counts alone miss an edit that keeps them
equal, so the comparison also uses a fingerprint
(:data:`FINGERPRINT_SIGNALS`): the engine's write timestamps for the bank,
its link and node tallies, digests of its mental models, directives and
configuration, and its newest operation. A bank that moved before the swap
rolls the run back; one that moved during the swap is not deleted, the run
stops there, and ``--resume`` exports and rebuilds that bank again.

The fingerprint cannot see a change that stores no memory, touches none of
those lists and starts no operation: an edit to a document's stored text or
metadata that the engine does not count as a write, a webhook, an entity.

Bank list
---------

A bank that appears after the list was taken is not in this run. Before the
swap it stops the run (rolled back). After the swap began the run finishes
its own banks and then records the store as ``mixed``, naming the bank.

Renaming
--------

hindsight-api 1.2.0 has no HTTP rename (its OpenAPI has ``PUT``, ``PATCH`` and
``DELETE`` on ``/v1/default/banks/{bank_id}``, none of which changes the id,
and an alias API that adds a second name without changing the first). The
rename is the engine's own admin command, run from the engine's venv with the
engine unit's environment and as the account that owns the engine home::

    <engine home>/.venv/bin/hindsight-admin rename-bank --from <temp> --to <bank>

It moves every row in one transaction and refuses a bank with pending or
processing operations, which is why the swap waits for the rebuilt bank's
queue before it deletes the original.

Test hook
---------

``hook(point)`` is called at every step boundary (:data:`HOOK_POINTS`); tests
raise from it to stop a run exactly there. The command line maps
``HAL0_EMBEDDER_SWITCH_KILL_AT=<point>`` onto it for live verification: the
process sends itself ``SIGKILL`` at that point, which is what a power cut
looks like to the journal.
"""

from __future__ import annotations

import asyncio
import contextlib
import fcntl
import hashlib
import json
import os
import re
import secrets
import shutil
import subprocess
import time
import zipfile
from collections.abc import Awaitable, Callable, Iterator, Mapping
from dataclasses import asdict, dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from urllib.parse import quote

import httpx
import structlog

from hal0.config.paths import var_lib
from hal0.install import perms
from hal0.memory import embedder_record, write_gate
from hal0.memory.embedder_record import (
    ENGINE_DEFAULT_EMBEDDER,
    ENGINE_DEFAULT_PROVIDER,
    SOURCE_CONFIGURED,
    SOURCE_SWITCH,
    SOURCE_UNAVAILABLE,
    Embedder,
    EmbedderRecord,
    describe_embedder,
)
from hal0.security import nofollow

log = structlog.get_logger(__name__)

JOURNAL_VERSION = 1

#: The counts a restored bank must match, in the order they are reported.
COUNT_KEYS: tuple[str, ...] = ("facts", "observations", "mental_models", "directives", "documents")

#: What a bank's fingerprint is made of, and where each signal comes from.
FINGERPRINT_SIGNALS: dict[str, str] = {
    "memory_write": "stats.last_memory_write_at: a memory stored, edited or consolidated",
    "consolidated": "stats.last_consolidated_at",
    "consolidation_queue": "stats.pending_consolidation and failed_consolidation",
    "nodes": "stats.nodes_by_fact_type",
    "links": "stats.total_links",
    "bank_write": "the bank list's last_write_at: a document retained or a fact stored",
    "document": "the bank list's last_document_at",
    "bank_updated": "the bank list's updated_at: the bank's profile or mission",
    "mental_models": "a digest of every mental model (content, tags, refresh times)",
    "directives": "a digest of every directive, inactive ones included",
    "config": "a digest of the bank's configuration overrides",
    "operation": "the id of the bank's newest operation that this run did not start",
}

#: The signals that describe what a bank holds, not when it was written. A
#: rebuilt bank is compared with itself across its rename on these alone: the
#: rename rewrites every row, and may move a timestamp without changing content.
#: Links are left out: the engine's own graph maintenance may still add one
#: to a freshly restored bank.
CONTENT_SIGNALS: tuple[str, ...] = ("nodes", "mental_models", "directives", "config")

#: Measured on hindsight-api 1.2.0, CPU embedder, 2026-11-10 (ADR-0058 "Open
#: questions"): a bank restore re-embeds roughly this many facts per second.
RESTORE_FACTS_PER_S = 60.0

#: Free space wanted on the engine's filesystem, as a multiple of its data
#: directory: the rebuilt banks sit beside the originals until the swap.
STORE_FREE_FACTOR = 1.0
#: And for the archives, which carry no vectors and no indexes.
ARCHIVE_FREE_FACTOR = 0.5

#: hal0-api's bank-id grammar (``memory_admin._BANK_RE``) allows 128 chars; a
#: temporary id that would not fit is shortened, never truncated mid-name.
_BANK_ID_MAX = 128

#: Every id this command ever creates: ``<bank>.switch-<run>``, the digest
#: form of it, and the scratch bank a recovery restore is recorded against.
_TEMP_RE = re.compile(
    r"(?:\.switch-[0-9a-f]{8}$)|(?:^switch-[0-9a-f]{8}-(?:scratch|[0-9a-f]{16})$)"
)

_ADMIN_BIN = "hindsight-admin"
_PG_DIR = ".pg0"

# Journal phases, in the order a run passes through them.
PHASE_PAUSED = "paused"
PHASE_EXPORTING = "exporting"
PHASE_FLIPPING = "flipping"
PHASE_RESTORING = "restoring"
PHASE_SWAPPING = "swapping"
PHASE_ROLLING_BACK = "rolling_back"
PHASE_ROLLBACK_FAILED = "rollback_failed"
PHASE_ROLLED_BACK = "rolled_back"
PHASE_COMPLETE = "complete"
_FINISHED_PHASES = (PHASE_ROLLED_BACK, PHASE_COMPLETE)

# Per-bank steps. ``deleting`` and ``renaming`` are saved before the call
# they name; ``deleted`` and ``renamed`` after it answered.
STEP_PLANNED = "planned"
STEP_EXPORTED = "exported"
STEP_RESTORED = "restored"
STEP_DELETING = "deleting"
STEP_DELETED = "deleted"
STEP_RENAMING = "renaming"
STEP_RENAMED = "renamed"
_BEFORE_DELETE = (STEP_PLANNED, STEP_EXPORTED, STEP_RESTORED, STEP_DELETING)
_AFTER_DELETE = (STEP_DELETED, STEP_RENAMING)

# What :func:`swap_action` can answer.
ACTION_SWAP = "delete the original, then rename the rebuilt bank into place"
ACTION_RENAME = "rename the rebuilt bank into place"
ACTION_VERIFY = "check the bank under its final id against its archive"
ACTION_RECOVER = "restore the bank from its archive, then rename it into place"
ACTION_REBUILD = "export and rebuild the bank again, then swap it"
ACTION_RECREATED = "stop: the original id exists again beside the rebuilt bank"

#: Where ``hook`` is called. ``<bank>`` is the bank's own id.
HOOK_POINTS: tuple[str, ...] = (
    "paused",
    "exported:<bank>",
    "before_flip",
    "flipped",
    "restored:<bank>",
    "before_swap",
    "before_delete:<bank>",
    "deleted:<bank>",
    "before_rename:<bank>",
    "renamed:<bank>",
    "before_finish",
)

_OP_LIVE = ("pending", "processing")
_OP_FAILED = ("failed", "cancelled", "not_found")
_PAGE = 100


class SwitchError(Exception):
    """A step of the switch failed. The message is written for the operator.

    ``resumable`` is true when the run stopped inside the swap, or a rollback
    could not finish: writes stay paused and ``--resume`` carries on.
    """

    def __init__(
        self,
        message: str,
        *,
        bank: str | None = None,
        details: dict[str, Any] | None = None,
        resumable: bool = False,
    ) -> None:
        super().__init__(message)
        self.message = message
        self.bank = bank
        self.details = details or {}
        self.resumable = resumable


class SwitchRefused(SwitchError):
    """Refused before anything was changed. ``plan`` is the preflight report, when there is one."""

    def __init__(self, message: str, plan: dict[str, Any] | None = None) -> None:
        super().__init__(message)
        self.plan = plan or {}


@dataclass(frozen=True)
class Timeouts:
    """Bounds on each wait. Tests shrink them; the CLI uses the defaults."""

    poll_s: float = 2.0
    #: One bank's export operation, and the archive download after it.
    export_s: float = 1800.0
    #: Queues draining after writes are paused.
    settle_s: float = 300.0
    #: The engine answering ``/health`` after a restart. The first start on a
    #: new embedder may download its weights.
    health_s: float = 600.0
    #: A restore is given this long, or ``restore_factor`` times its estimate
    #: at :data:`RESTORE_FACTS_PER_S`, whichever is more. A cancelled restore
    #: is given the same floor to really stop.
    restore_floor_s: float = 900.0
    restore_factor: float = 6.0
    #: The temporary bank's queue going quiet before its rename.
    quiet_s: float = 120.0
    rename_s: float = 3600.0
    http_s: float = 120.0


@dataclass
class BankEntry:
    """One bank's line in the journal.

    ``counts`` and ``fingerprint`` are the bank as its archive was taken;
    ``restored_*`` the rebuilt bank as its restore finished. ``own_ops`` are
    the operations this run started against the original (its export, its
    restore), which the fingerprint's ``operation`` signal leaves out.
    """

    bank: str
    temp: str
    counts: dict[str, int] = field(default_factory=dict)
    links: int = 0
    fingerprint: dict[str, Any] = field(default_factory=dict)
    archive: str | None = None
    step: str = STEP_PLANNED
    restored_counts: dict[str, int] | None = None
    restored_fingerprint: dict[str, Any] | None = None
    own_ops: list[str] = field(default_factory=list)
    #: ``{"bank", "id"}`` of a restore that may still be running.
    restore_op: dict[str, str] | None = None
    note: str | None = None


@dataclass
class Journal:
    """The state of one run, on disk from the pause to the finish.

    ``flip_started`` goes true, and is saved, before the record or the
    drop-in is touched: a rollback puts both back only when it is set.
    """

    run_id: str
    started_at: str
    pid: int
    phase: str
    target: dict[str, str]
    previous: dict[str, Any]
    archive_dir: str
    allow_pending: bool = False
    flip_started: bool = False
    banks: list[BankEntry] = field(default_factory=list)
    stray_banks: list[str] = field(default_factory=list)
    error: str | None = None
    updated_at: str | None = None
    version: int = JOURNAL_VERSION

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> Journal:
        known = set(BankEntry.__dataclass_fields__)
        banks = [
            BankEntry(**{k: v for k, v in b.items() if k in known}) for b in data.get("banks") or []
        ]
        fields = {k: v for k, v in data.items() if k in cls.__dataclass_fields__ and k != "banks"}
        return cls(banks=banks, **fields)

    @property
    def scratch(self) -> str:
        return scratch_bank_id(self.run_id)

    def own_ids(self) -> set[str]:
        """Every id this run may have created."""
        return {e.temp for e in self.banks} | {self.scratch}


def journal_path() -> Path:
    """``/var/lib/hal0/state/memory-embedder-switch.json`` (sandboxed under ``HAL0_HOME``)."""
    return var_lib() / "state" / "memory-embedder-switch.json"


def lock_path() -> Path:
    return var_lib() / "state" / "memory-embedder-switch.lock"


def archive_root() -> Path:
    """``/var/lib/hal0/backups``: where hal0 keeps restore points, one directory per run here."""
    return var_lib() / "backups"


def mirror_name() -> str:
    return "journal.json"


def temp_bank_id(bank: str, run_id: str) -> str:
    """``<bank>.switch-<run>``, or a short id built from a digest when that would not fit."""
    candidate = f"{bank}.switch-{run_id}"
    if len(candidate.encode("utf-8")) <= _BANK_ID_MAX:
        return candidate
    digest = hashlib.sha256(bank.encode("utf-8")).hexdigest()[:16]
    return f"switch-{run_id}-{digest}"


def scratch_bank_id(run_id: str) -> str:
    """An empty bank a recovery restore is recorded against (see ``_recover``)."""
    return f"switch-{run_id}-scratch"


def is_switch_temp(bank: str) -> bool:
    """True for any id shaped like one this command creates."""
    return bool(_TEMP_RE.search(bank))


def _parse_journal(raw: str) -> Journal:
    data = json.loads(raw)
    if not isinstance(data, dict) or data.get("version") != JOURNAL_VERSION:
        raise ValueError("not a version 1 switch journal")
    return Journal.from_dict(data)


def _read_journal_file(path: Path) -> tuple[Journal | None, str | None]:
    """``(journal, None)``, ``(None, None)`` when absent, ``(None, why)`` when unusable."""
    try:
        raw = path.read_text(encoding="utf-8")
    except FileNotFoundError:
        return None, None
    except (OSError, UnicodeDecodeError) as exc:
        return None, f"{path} cannot be read ({type(exc).__name__})"
    try:
        return _parse_journal(raw), None
    except (ValueError, TypeError) as exc:
        return None, f"{path} is not a usable journal ({exc})"


def _unfinished_mirrors() -> list[Path]:
    """Mirrors of runs that neither finished nor were rolled back, newest first."""
    found: list[tuple[str, Path]] = []
    try:
        candidates = sorted(archive_root().glob(f"memory-embedder-switch-*/{mirror_name()}"))
    except OSError:
        return []
    for path in candidates:
        journal, _ = _read_journal_file(path)
        if journal is not None and journal.phase not in _FINISHED_PHASES:
            found.append((journal.started_at, path))
    return [path for _, path in sorted(found, reverse=True)]


def locate_journal() -> dict[str, Any]:
    """Find the journal of an unfinished run wherever a copy survives.

    The primary copy first. When it is missing or unusable, the mirror in the
    archive directory the write gate names, then any unfinished mirror under
    ``backups/``. Returns ``{"journal", "source", "error"}``: ``error`` is set
    when a copy exists but none can be used, and when the primary was unusable
    and a mirror stood in for it. Never raises.
    """
    primary, problem = _read_journal_file(journal_path())
    if primary is not None:
        if primary.phase in _FINISHED_PHASES:
            return {"journal": None, "source": None, "error": None}
        return {"journal": primary, "source": str(journal_path()), "error": None}
    mirrors: list[Path] = []
    gate = write_gate.read_gate() or {}
    if isinstance(gate.get("archive_dir"), str) and gate["archive_dir"]:
        mirrors.append(Path(gate["archive_dir"]) / mirror_name())
    mirrors.extend(p for p in _unfinished_mirrors() if p not in mirrors)
    for path in mirrors:
        journal, why = _read_journal_file(path)
        if journal is not None and journal.phase not in _FINISHED_PHASES:
            return {"journal": journal, "source": str(path), "error": problem}
        problem = problem or why
    return {"journal": None, "source": None, "error": problem}


def load_journal() -> Journal | None:
    """The journal of an unfinished run, or ``None``. Raises :class:`SwitchError` if unusable."""
    found = locate_journal()
    if found["journal"] is None and found["error"]:
        raise SwitchError(f"the switch journal is not usable: {found['error']}")
    return found["journal"]


def interrupted_switch() -> dict[str, Any] | None:
    """A short description of an unfinished switch for status surfaces, else ``None``.

    Never raises: ``hal0 doctor``, the updater and the engine upgrade call it.
    """
    found = locate_journal()
    journal = found["journal"]
    if journal is None:
        if found["error"]:
            return {"phase": "unreadable", "error": found["error"]}
        return None
    return {
        "phase": journal.phase,
        "run_id": journal.run_id,
        "started_at": journal.started_at,
        "target": journal.target,
        "error": journal.error,
        "archive_dir": journal.archive_dir,
        "source": found["source"],
    }


def lock_held() -> bool:
    """True when a process holds the switch lock. Creates nothing."""
    try:
        fd = os.open(lock_path(), os.O_RDONLY | os.O_NOFOLLOW | os.O_CLOEXEC)
    except OSError:
        return False
    try:
        fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except BlockingIOError:
        return True
    except OSError:
        return False
    finally:
        os.close(fd)
    return False


def blocking_reason() -> str | None:
    """Why nothing else may restart or rebuild the memory engine now, or ``None``.

    For ``hal0 update`` and the engine upgrade: an upgrade snapshots the
    engine's data directory and may roll back to it, which in the middle of a
    switch would put renamed banks back under their temporary ids.
    """
    if lock_held():
        return "`hal0 memory embedder switch` is running on this box"
    left = interrupted_switch()
    if left is not None:
        return (
            f"an embedder switch (run {left.get('run_id')}, phase {left.get('phase')}) did not "
            "finish; run `hal0 memory embedder switch --resume` first"
        )
    gate = write_gate.read_gate()
    if gate is not None and gate.get("reason") == write_gate.REASON_EMBEDDER_SWITCH:
        return (
            f"memory writes are paused by an embedder switch (run {gate.get('run_id')}) that "
            "left no journal; see `hal0 memory embedder switch --status`"
        )
    return None


def update_in_progress() -> str | None:
    """The id of a queued or running ``hal0 update`` job, read from its on-disk mirror."""
    try:
        jobs = sorted((var_lib() / "update-jobs").glob("*.json"))
    except OSError:
        return None
    for path in jobs:
        try:
            job = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            continue
        if isinstance(job, dict) and job.get("state") in ("queued", "running"):
            return str(job.get("id") or path.stem)
    return None


def swap_action(step: str, has_original: bool, has_rebuilt: bool) -> str:
    """What the swap does for one bank, from its journal step and the ids the engine has now.

    The ids decide; the step only tells two otherwise identical pictures apart.
    """
    if has_rebuilt and not has_original:
        return ACTION_RENAME
    if not has_rebuilt and not has_original:
        return ACTION_RECOVER
    if has_original and not has_rebuilt:
        # Either the rename already happened, or the rebuilt bank was lost
        # before its original was ever deleted.
        return ACTION_REBUILD if step in _BEFORE_DELETE else ACTION_VERIFY
    # Both ids. After a delete that answered, the original id can only be
    # back because something recreated it.
    return ACTION_RECREATED if step in _AFTER_DELETE else ACTION_SWAP


def _bank_path(bank: str) -> str:
    # Colons are legal in a path segment and bank ids carry them
    # (``Hal0::Agents``); everything else that could change the path is escaped.
    return f"/v1/default/banks/{quote(bank, safe=':')}"


def _du_bytes(path: Path) -> int:
    total = 0
    try:
        for p in path.rglob("*"):
            try:
                if p.is_file() and not p.is_symlink():
                    total += p.stat().st_size
            except OSError:
                continue
    except OSError:
        return 0
    return total


def _device(path: Path) -> int:
    """The filesystem ``path`` is on."""
    return os.stat(path).st_dev


def _nearest_existing(path: Path) -> Path:
    for candidate in (path, *path.parents):
        if candidate.exists():
            return candidate
    return Path("/")


def _diff(expected: Mapping[str, int], got: Mapping[str, int]) -> dict[str, dict[str, int]]:
    return {
        key: {"expected": int(expected.get(key, 0)), "got": int(got.get(key, 0))}
        for key in COUNT_KEYS
        if int(expected.get(key, 0)) != int(got.get(key, 0))
    }


def _diff_text(diff: Mapping[str, Mapping[str, int]]) -> str:
    return ", ".join(
        f"{key.replace('_', ' ')} {d['got']} (expected {d['expected']})" for key, d in diff.items()
    )


def _signals_moved(
    before: Mapping[str, Any] | None, now: Mapping[str, Any], keys: tuple[str, ...] | None = None
) -> list[str]:
    """Names of the fingerprint signals that differ. An absent baseline compares equal."""
    if not before:
        return []
    return [k for k in (keys or tuple(FINGERPRINT_SIGNALS)) if before.get(k) != now.get(k)]


def _strip(value: Any) -> Any:
    """``value`` without the keys that differ between a bank and its rebuilt copy by design."""
    if isinstance(value, dict):
        return {k: _strip(v) for k, v in value.items() if k not in ("bank_id", "is_stale")}
    if isinstance(value, list):
        return [_strip(v) for v in value]
    return value


def _digest(value: Any) -> str:
    """A short digest of ``value``; a list is digested as a set of its rows."""
    if isinstance(value, list):
        value = sorted(json.dumps(_strip(v), sort_keys=True, default=str) for v in value)
    text = json.dumps(_strip(value), sort_keys=True, default=str)
    return hashlib.sha256(text.encode("utf-8")).hexdigest()[:16]


def _pair(raw: Mapping[str, Any] | None) -> Embedder | None:
    """A raw evidence layer as a pair; an unset key is the engine default."""
    if raw is None:
        return None
    return Embedder(
        raw.get("provider") or ENGINE_DEFAULT_PROVIDER,
        raw.get("model") or ENGINE_DEFAULT_EMBEDDER,
    )


def _write_synced(path: Path, text: str, *, mode: int) -> None:
    """Replace ``path`` atomically and durably: temp file, fsync, rename, fsync the directory."""
    nofollow.write_file(path, text, mode=mode, owner=perms.state_root_owner())
    fd = nofollow.open_dir(path.parent)
    try:
        os.fsync(fd)
    finally:
        os.close(fd)


def _safe_output(text: str, limit: int = 400) -> str:
    """Tool output fit to show: secrets and URL credentials masked, then capped."""
    from hal0.redaction import redact_log_line

    lines = [redact_log_line(line) for line in text.strip().splitlines()]
    cleaned = " | ".join(line.strip() for line in lines if line.strip())
    return cleaned[-limit:] if cleaned else "no output"


def admin_command(hs_dir: Path, old: str, new: str) -> list[str]:
    """argv of the engine's own bank rename (``hindsight_api/admin/cli.py``, 1.2.0)."""
    return [str(hs_dir / ".venv" / "bin" / _ADMIN_BIN), "rename-bank", "--from", old, "--to", new]


def admin_env_file_unreadable() -> str | None:
    """The unit's ``EnvironmentFile`` when it exists and this account cannot read it."""
    from hal0.memory import extraction_env

    path = extraction_env.HINDSIGHT_LLM_ENV_PATH
    try:
        if path.exists() and not os.access(path, os.R_OK):
            return str(path)
    except OSError:
        return str(path)
    return None


def admin_environment(hs_dir: Path) -> dict[str, str]:
    """The environment the engine's unit would start it with, for the admin command.

    The admin command must find the same database the engine uses. On a hal0
    box that is the embedded Postgres under ``$HOME/.pg0``, selected by the
    unit's ``HOME=``; an operator who pointed the engine at another database
    did it with ``HINDSIGHT_API_*`` lines in a drop-in or in the unit's
    ``EnvironmentFile``, and both layers are read here in systemd's order.
    The values go to the child's environment only, never to argv or a log.
    """
    from hal0.memory import extraction_env

    env = {"PATH": os.environ.get("PATH", "/usr/local/bin:/usr/bin:/bin")}
    env.update(extraction_env.hindsight_unit_env())
    env.update(extraction_env._read_env_file(extraction_env.HINDSIGHT_LLM_ENV_PATH))
    env.setdefault("HOME", str(hs_dir))
    return env


def run_admin_rename(
    hs_dir: Path,
    old: str,
    new: str,
    *,
    timeout_s: float,
    runner: Callable[..., subprocess.CompletedProcess[str]] = subprocess.run,
    euid: Callable[[], int] = os.geteuid,
) -> str:
    """Rename bank ``old`` to ``new`` with ``hindsight-admin``; returns its output, made safe.

    A root caller runs it as the account that owns the engine home: the
    embedded Postgres belongs to that account and refuses root. Raises
    :class:`SwitchError` on a non-zero exit. The tool's output can quote a
    database URL, so it is never passed on raw (:func:`_safe_output`).
    """
    unreadable = admin_env_file_unreadable()
    if unreadable:
        raise SwitchError(
            f"{unreadable} (the engine unit's EnvironmentFile) is not readable by this account, "
            f"so {_ADMIN_BIN} would not see the engine's settings; run the switch as root",
            bank=new,
        )
    kwargs: dict[str, Any] = {}
    if euid() == 0:
        owner = os.stat(hs_dir)
        if owner.st_uid != 0:
            kwargs.update(user=owner.st_uid, group=owner.st_gid, extra_groups=[])
    try:
        proc = runner(
            admin_command(hs_dir, old, new),
            env=admin_environment(hs_dir),
            cwd=str(hs_dir),
            capture_output=True,
            text=True,
            timeout=timeout_s,
            check=False,
            **kwargs,
        )
    except (OSError, subprocess.SubprocessError) as exc:
        raise SwitchError(
            f"could not run {_ADMIN_BIN} rename-bank ({type(exc).__name__})", bank=new
        ) from None
    output = _safe_output("\n".join(part for part in (proc.stdout, proc.stderr) if part))
    if proc.returncode != 0:
        raise SwitchError(
            f"{_ADMIN_BIN} rename-bank --from {old} --to {new} exited {proc.returncode}: {output}",
            bank=new,
        )
    return output


class EmbedderSwitch:
    """One run of the switch against one engine.

    ``client`` is a :class:`hal0.memory.hindsight_client.HindsightRestClient`
    pointed at the engine itself, not at hal0-api: the switch's own imports
    and deletes must not meet the pause it sets.

    Everything that touches the host is injectable, and defaults to the real
    thing: ``apply_dropin(embedder)`` renders the managed drop-in for that
    pair and restarts the engine (returning the ``apply_showcase_dropin``
    dict), ``evidence()`` is :func:`embedder_record.engine_evidence`,
    ``rename_bank(old, new)`` is :func:`run_admin_rename`, ``privileged()``
    says whether this process may do all of that, and ``hook(point)`` is the
    test hook described in the module docstring.
    """

    def __init__(
        self,
        client: Any,
        *,
        target: Embedder | None = None,
        allow_pending: bool = False,
        configured: Embedder | None = None,
        apply_dropin: Callable[[Embedder], dict[str, Any]] | None = None,
        evidence: Callable[[], Mapping[str, Any]] | None = None,
        rename_bank: Callable[[str, str], str] | None = None,
        privileged: Callable[[], bool] | None = None,
        hs_dir: Path | None = None,
        timeouts: Timeouts | None = None,
        sleep: Callable[[float], Awaitable[None]] = asyncio.sleep,
        clock: Callable[[], float] = time.monotonic,
        progress: Callable[[str], None] | None = None,
        hook: Callable[[str], None] | None = None,
    ) -> None:
        self._client = client
        self._target = target
        self._allow_pending = allow_pending
        self._configured = configured
        self._apply_dropin = apply_dropin or self._default_apply_dropin
        self._evidence = evidence or embedder_record.engine_evidence
        self._rename_bank = rename_bank or self._default_rename_bank
        self._privileged = privileged or self._default_privileged
        self._hs_dir = hs_dir
        self.t = timeouts or Timeouts()
        self._sleep = sleep
        self._clock = clock
        self._progress = progress or (lambda _msg: None)
        self._hook = hook or (lambda _point: None)

    # ── host seams (defaults) ───────────────────────────────────────────

    def _engine_home(self) -> Path:
        if self._hs_dir is None:
            from hal0.memory.engine_upgrade import hindsight_dir

            self._hs_dir = hindsight_dir()
        return self._hs_dir

    @staticmethod
    def _default_privileged() -> bool:
        from hal0.system.seam import is_hal0_service_user

        return os.geteuid() == 0 or is_hal0_service_user()

    @staticmethod
    def _default_apply_dropin(embedder: Embedder) -> dict[str, Any]:
        from hal0.config.loader import load_hal0_config
        from hal0.memory import extraction_env

        mem = load_hal0_config().memory
        return extraction_env.apply_showcase_dropin(
            models=mem.models,
            decision=mem.decision,
            lanes=mem.lanes,
            graph=mem.graph,
            restart=True,
            embedder=embedder,
        )

    def _default_rename_bank(self, old: str, new: str) -> str:
        return run_admin_rename(self._engine_home(), old, new, timeout_s=self.t.rename_s)

    def _configured_embedder(self) -> Embedder:
        if self._configured is None:
            from hal0.config.loader import load_hal0_config

            self._configured = embedder_record.configured_embedder(load_hal0_config().memory.models)
        return self._configured

    def _say(self, message: str) -> None:
        log.info("hal0.memory.embedder_switch", step=message)
        self._progress(message)

    # ── engine calls ────────────────────────────────────────────────────

    def _auth(self) -> dict[str, str]:
        return {k: v for k, v in self._client._headers().items() if k.lower() != "content-type"}

    async def _json(
        self,
        method: str,
        path: str,
        *,
        params: dict[str, Any] | None = None,
        body: Any | None = None,
        what: str,
        bank: str | None = None,
        timeout_s: float | None = None,
    ) -> Any:
        try:
            return await self._client.request_json(
                method, path, params=params, json_body=body, timeout_s=timeout_s or self.t.http_s
            )
        except httpx.HTTPStatusError as exc:
            raise SwitchError(
                f"{what}: the engine answered HTTP {exc.response.status_code} "
                f"{exc.response.text[:300]}".rstrip(),
                bank=bank,
            ) from exc
        except httpx.HTTPError as exc:
            raise SwitchError(
                f"{what}: the engine did not answer ({type(exc).__name__}: {exc})", bank=bank
            ) from exc

    async def _healthy(self) -> bool:
        try:
            await self._client.request_json("GET", "/health", timeout_s=5.0)
        except (httpx.HTTPError, ValueError):
            return False
        return True

    async def _wait_health(self) -> bool:
        deadline = self._clock() + self.t.health_s
        while True:
            if await self._healthy():
                return True
            if self._clock() >= deadline:
                return False
            await self._sleep(self.t.poll_s)

    async def _list_all(
        self,
        path: str,
        params: dict[str, Any] | None = None,
        *,
        key: str,
        what: str,
        bank: str | None = None,
    ) -> list[Any]:
        """Every row of a paged engine list.

        Pages until the engine's own ``total`` is reached or a page comes back
        empty; a short page is never taken for the end. When the engine gives
        a total, the rows collected must add up to it.
        """
        rows: list[Any] = []
        total: int | None = None
        while True:
            page = await self._json(
                "GET",
                path,
                params={**(params or {}), "limit": _PAGE, "offset": len(rows)},
                what=what,
                bank=bank,
            )
            page = page if isinstance(page, dict) else {}
            if isinstance(page.get("total"), int):
                total = page["total"]
            chunk = page.get(key)
            chunk = chunk if isinstance(chunk, list) else []
            rows.extend(chunk)
            if not chunk or (total is not None and len(rows) >= total):
                break
        if total is not None and len(rows) != total:
            raise SwitchError(
                f"{what}: the engine reports {total} but listed {len(rows)}; the listing "
                "changed while it was read",
                bank=bank,
            )
        return rows

    async def _bank_rows(self) -> list[dict[str, Any]]:
        rows = await self._list_all("/v1/default/banks", key="banks", what="listing banks")
        return [r for r in rows if isinstance(r, dict) and "bank_id" in r]

    async def _bank_ids(self) -> list[str]:
        return [str(r["bank_id"]) for r in await self._bank_rows()]

    async def _bank_row(self, bank: str) -> dict[str, Any]:
        rows = await self._list_all(
            "/v1/default/banks", {"q": bank}, key="banks", what="reading the bank", bank=bank
        )
        for row in rows:
            if isinstance(row, dict) and row.get("bank_id") == bank:
                return row
        return {}

    async def bank_state(self, bank: str, *, ignore_ops: list[str] | None = None) -> dict[str, Any]:
        """Counts, queue depth and fingerprint for one bank, uncached.

        Facts are every memory unit that is not an observation
        (``nodes_by_fact_type``); mental models include the ones that back a
        knowledge page; directives include inactive ones. The fingerprint is
        described by :data:`FINGERPRINT_SIGNALS`. ``ignore_ops`` are operation
        ids this run started against the bank.
        """
        base = _bank_path(bank)
        stats = await self._json(
            "GET", f"{base}/stats", params={"refresh": "true"}, what="reading stats", bank=bank
        )
        stats = stats if isinstance(stats, dict) else {}
        by_type = stats.get("nodes_by_fact_type")
        by_type = by_type if isinstance(by_type, dict) else {}
        observations = int(stats.get("total_observations") or by_type.get("observation") or 0)
        facts = sum(int(v or 0) for k, v in by_type.items() if k != "observation")
        ops = stats.get("operations_by_status")
        ops = ops if isinstance(ops, dict) else {}
        models = await self._list_all(
            f"{base}/mental-models",
            {"detail": "content"},
            key="items",
            what="reading mental models",
            bank=bank,
        )
        directives = await self._list_all(
            f"{base}/directives",
            {"active_only": "false"},
            key="items",
            what="reading directives",
            bank=bank,
        )
        try:
            config = await self._json("GET", f"{base}/config", what="reading the config", bank=bank)
        except SwitchError:
            # The per-bank config API can be switched off (features.bank_config_api).
            config = "unavailable"
        recent = await self._json(
            "GET", f"{base}/operations", params={"limit": 20}, what="reading operations", bank=bank
        )
        recent = recent.get("operations") if isinstance(recent, dict) else None
        own = set(ignore_ops or [])
        newest = next(
            (
                str(op.get("id") or op.get("operation_id"))
                for op in (recent if isinstance(recent, list) else [])
                if isinstance(op, dict) and str(op.get("id") or op.get("operation_id")) not in own
            ),
            None,
        )
        row = await self._bank_row(bank)
        return {
            "counts": {
                "facts": facts,
                "observations": observations,
                "mental_models": len(models),
                "directives": len(directives),
                "documents": int(stats.get("total_documents") or 0),
            },
            "links": int(stats.get("total_links") or 0),
            "pending": int(ops.get("pending") or 0),
            "processing": int(ops.get("processing") or 0),
            "listed_facts": row.get("fact_count"),
            "nodes_total": int(stats.get("total_nodes") or 0),
            "fingerprint": {
                "memory_write": stats.get("last_memory_write_at"),
                "consolidated": stats.get("last_consolidated_at"),
                "consolidation_queue": [
                    int(stats.get("pending_consolidation") or 0),
                    int(stats.get("failed_consolidation") or 0),
                ],
                "nodes": sorted([str(k), int(v or 0)] for k, v in by_type.items()),
                "links": int(stats.get("total_links") or 0),
                "bank_write": row.get("last_write_at"),
                "document": row.get("last_document_at"),
                "bank_updated": row.get("updated_at"),
                "mental_models": _digest(models),
                "directives": _digest(directives),
                "config": _digest(config),
                "operation": newest,
            },
        }

    async def _wait_operation(
        self, bank: str, operation_id: str, *, timeout_s: float, what: str
    ) -> dict[str, Any]:
        deadline = self._clock() + timeout_s
        path = f"{_bank_path(bank)}/operations/{quote(operation_id, safe='')}"
        while True:
            op = await self._json("GET", path, what=what, bank=bank)
            op = op if isinstance(op, dict) else {}
            status = op.get("status")
            if status == "completed":
                return op
            if status in _OP_FAILED:
                raise SwitchError(
                    f"{what} ended as {status}: {op.get('error_message') or 'no error message'}",
                    bank=bank,
                )
            if self._clock() >= deadline:
                raise SwitchError(
                    f"{what} did not finish within {timeout_s:g}s (last status: {status})",
                    bank=bank,
                    details={"operation_id": operation_id},
                )
            await self._sleep(self.t.poll_s)

    async def _delete_bank(self, bank: str) -> None:
        """Delete ``bank``. A bank that is already gone is a success."""
        try:
            await self._client.request_json("DELETE", _bank_path(bank), timeout_s=self.t.rename_s)
        except httpx.HTTPStatusError as exc:
            if exc.response.status_code == 404:
                return
            raise SwitchError(
                f"deleting the bank: the engine answered HTTP {exc.response.status_code} "
                f"{exc.response.text[:300]}".rstrip(),
                bank=bank,
            ) from exc
        except httpx.HTTPError as exc:
            raise SwitchError(
                f"deleting the bank: the engine did not answer ({type(exc).__name__}: {exc})",
                bank=bank,
            ) from exc

    async def _wait_quiet(self, bank: str) -> None:
        """Wait until ``bank`` has no pending or processing operation (the rename refuses one)."""
        deadline = self._clock() + self.t.quiet_s
        while True:
            state = await self.bank_state(bank)
            if not state["pending"] and not state["processing"]:
                return
            if self._clock() >= deadline:
                raise SwitchError(
                    f"bank {bank} still has {state['pending']} pending and {state['processing']} "
                    f"processing operations after {self.t.quiet_s:g}s; it cannot be renamed "
                    "until they finish",
                    bank=bank,
                )
            await self._sleep(self.t.poll_s)

    # ── the engine's embedder ───────────────────────────────────────────

    async def _start_engine_on(self, embedder: Embedder) -> None:
        """Render the drop-in for ``embedder``, restart, and confirm the engine runs it."""
        result = await asyncio.to_thread(self._apply_dropin, embedder)
        if result.get("error"):
            raise SwitchError(f"could not move the engine: {result['error']}")
        if not await self._wait_health():
            raise SwitchError(
                f"the engine did not answer /health within {self.t.health_s:g}s of its restart "
                f"on {describe_embedder(*embedder)} (see `journalctl -u hindsight-api`)"
            )
        live: Embedder | None = None
        for _ in range(10):
            live = _pair(self._evidence().get("live"))
            if live == embedder:
                return
            await self._sleep(self.t.poll_s)
        runs = describe_embedder(*live) if live is not None else "an environment hal0 cannot read"
        raise SwitchError(
            f"the engine is up but runs {runs}, not {describe_embedder(*embedder)}: another "
            "drop-in, or the unit's EnvironmentFile, overrides the embedder"
        )

    async def _ensure_engine_on(self, embedder: Embedder) -> None:
        """Do nothing when the engine is up on ``embedder``; otherwise put it there.

        A restore embeds with whatever the engine runs at that moment, so this
        is asked before every restore and at the start of every resume, from
        the engine's live environment and not from the record.
        """
        if await self._healthy() and _pair(self._evidence().get("live")) == embedder:
            return
        self._say(f"the engine is not running {describe_embedder(*embedder)}; restarting it")
        await self._start_engine_on(embedder)

    # ── archives ────────────────────────────────────────────────────────

    def _make_archive_dir(self, run_id: str) -> Path:
        """Create this run's ``0700`` archive directory under the shared backups directory."""
        from hal0.config.migrations.runner_system import _backups_dir

        stamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
        run_dir = _backups_dir() / f"memory-embedder-switch-{stamp}-{run_id}"
        nofollow.ensure_dir(run_dir, mode=0o700, owner=perms.state_root_owner(), create=True)
        return run_dir

    @contextlib.contextmanager
    def _archive_dir_fd(self, journal: Journal) -> Iterator[int]:
        fd = nofollow.open_dir(journal.archive_dir)
        try:
            yield fd
        finally:
            os.close(fd)

    async def _export_bank(self, journal: Journal, entry: BankEntry, index: int) -> dict[str, int]:
        """Export one bank into the run's archive directory; returns the archive's counts."""
        bank = entry.bank
        submit = await self._json(
            "POST",
            f"{_bank_path(bank)}/transfer/export",
            params={
                "include_data": "true",
                "include_bank_config": "true",
                "include_history": "false",
            },
            what="starting the export",
            bank=bank,
        )
        operation_id = str((submit or {}).get("operation_id") or "")
        if not operation_id:
            raise SwitchError("the export returned no operation id", bank=bank)
        entry.own_ops.append(operation_id)
        self._save(journal)
        op = await self._wait_operation(
            bank, operation_id, timeout_s=self.t.export_s, what="the export"
        )
        meta = op.get("result_metadata")
        url = self._download_path(meta if isinstance(meta, dict) else {}, bank)
        name = f"{index:03d}-{re.sub(r'[^A-Za-z0-9._-]', '_', bank)[:80]}.zip"
        owner = perms.state_root_owner()
        with self._archive_dir_fd(journal) as dfd:
            part = f".{name}.part"
            with contextlib.suppress(FileNotFoundError):
                os.unlink(part, dir_fd=dfd)
            fd = os.open(
                part,
                os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW | os.O_CLOEXEC,
                0o600,
                dir_fd=dfd,
            )
            try:
                with os.fdopen(fd, "wb") as fh:
                    if owner is not None and os.geteuid() == 0:
                        os.fchown(fh.fileno(), *owner)
                    try:
                        async with self._client._http.stream(
                            "GET",
                            url,
                            headers=self._auth(),
                            timeout=httpx.Timeout(self.t.export_s, connect=3.0),
                        ) as resp:
                            resp.raise_for_status()
                            async for chunk in resp.aiter_bytes():
                                fh.write(chunk)
                    except httpx.HTTPError as exc:
                        raise SwitchError(
                            f"downloading the archive failed ({type(exc).__name__}: {exc})",
                            bank=bank,
                        ) from exc
                    fh.flush()
                    os.fsync(fh.fileno())
                os.replace(part, name, src_dir_fd=dfd, dst_dir_fd=dfd)
                os.fsync(dfd)
            except BaseException:
                with contextlib.suppress(OSError):
                    os.unlink(part, dir_fd=dfd)
                raise
        entry.archive = name
        return self._archive_counts(journal, entry)

    @staticmethod
    def _download_path(meta: Mapping[str, Any], bank: str) -> str:
        """The archive's path on the engine. Never a URL on another host."""
        raw = meta.get("download_url")
        if isinstance(raw, str) and raw.startswith("/v1/default/files/download/"):
            return raw
        key = meta.get("storage_key")
        if isinstance(key, str) and key and ".." not in key.split("/"):
            return "/v1/default/files/download/" + quote(key, safe="/")
        raise SwitchError(
            "the export finished without a download location on the engine", bank=bank
        )

    def _archive_counts(self, journal: Journal, entry: BankEntry) -> dict[str, int]:
        """What the archive says it holds. Raises :class:`SwitchError` for an unusable one."""
        bank = entry.bank
        try:
            with self._archive_dir_fd(journal) as dfd:
                fd = os.open(str(entry.archive), os.O_RDONLY | os.O_NOFOLLOW, dir_fd=dfd)
                with os.fdopen(fd, "rb") as fh:
                    if os.fstat(fh.fileno()).st_size == 0:
                        raise SwitchError("the archive is empty", bank=bank)
                    with zipfile.ZipFile(fh) as zf:
                        manifest = json.loads(zf.read("manifest.json"))
                        models: int | None = None
                        if "mental_models.json" in zf.namelist():
                            rows = json.loads(zf.read("mental_models.json"))
                            models = len(rows) if isinstance(rows, list) else None
        except SwitchError:
            raise
        except (OSError, KeyError, ValueError, zipfile.BadZipFile) as exc:
            raise SwitchError(
                f"the archive is not a readable transfer archive ({type(exc).__name__}: {exc})",
                bank=bank,
            ) from exc
        if not isinstance(manifest, dict) or manifest.get("archive_type") != "bank":
            raise SwitchError("the archive is not a whole-bank archive", bank=bank)
        if manifest.get("source_bank_id") != bank:
            raise SwitchError(
                f"the archive is of bank {manifest.get('source_bank_id')!r}, not this one",
                bank=bank,
            )
        if models is None:
            # ``mental_model_count`` leaves out the models that back a
            # knowledge page; the pages are counted separately.
            models = int(manifest.get("mental_model_count") or 0) + int(
                manifest.get("knowledge_page_count") or 0
            )
        return {
            "facts": int(manifest.get("fact_count") or 0),
            "observations": int(manifest.get("observation_count") or 0),
            "mental_models": models,
            "directives": int(manifest.get("directive_count") or 0),
            "documents": int(manifest.get("document_count") or 0),
        }

    async def _export_verified(self, journal: Journal, entry: BankEntry, index: int) -> None:
        """Export ``entry`` until the archive and the bank agree.

        The agreed counts and the bank's fingerprint at that moment become
        the baseline every later comparison uses.
        """
        last: dict[str, dict[str, int]] = {}
        for _attempt in range(2):
            archived = await self._export_bank(journal, entry, index)
            now = await self.bank_state(entry.bank, ignore_ops=entry.own_ops)
            last = _diff(now["counts"], archived)
            if not last or journal.allow_pending:
                # --allow-pending: operations may still be writing, so the
                # archive, not the moving bank, is what the rebuilt bank must
                # match.
                entry.counts, entry.links = archived, now["links"]
                entry.fingerprint = now["fingerprint"]
                entry.step = STEP_EXPORTED
                self._save(journal)
                return
        raise SwitchError(
            f"the archive of bank {entry.bank} does not match the bank: {_diff_text(last)} "
            "(the archive's count, then the bank's). Either something is still writing to "
            "the bank, or it holds what the engine's export leaves out: an observation whose "
            "source facts are gone, or a fact that belongs to no document. Nothing was changed",
            bank=entry.bank,
            details={"differs": last},
        )

    async def _restore(
        self, journal: Journal, entry: BankEntry, target_id: str, *, via: str | None = None
    ) -> dict[str, Any]:
        """Restore ``entry``'s archive into ``target_id``; returns that bank's state.

        The engine records a restore against the bank named in the URL, which
        must therefore exist while the target must not: ``via`` is that bank,
        the original by default.
        """
        assert self._target is not None
        await self._ensure_engine_on(self._target)
        url_bank = via or entry.bank
        try:
            with self._archive_dir_fd(journal) as dfd:
                fd = os.open(str(entry.archive), os.O_RDONLY | os.O_NOFOLLOW, dir_fd=dfd)
                with os.fdopen(fd, "rb") as fh:
                    resp = await self._client._http.post(
                        f"{_bank_path(url_bank)}/transfer/import",
                        headers=self._auth(),
                        params={"mode": "restore", "target_bank_id": target_id},
                        files={"file": (str(entry.archive), fh, "application/zip")},
                        timeout=httpx.Timeout(self.t.export_s, connect=3.0),
                    )
            resp.raise_for_status()
        except httpx.HTTPStatusError as exc:
            raise SwitchError(
                f"starting the restore into {target_id}: the engine answered HTTP "
                f"{exc.response.status_code} {exc.response.text[:300]}".rstrip(),
                bank=entry.bank,
            ) from exc
        except (OSError, httpx.HTTPError) as exc:
            raise SwitchError(
                f"starting the restore into {target_id} failed ({type(exc).__name__}: {exc})",
                bank=entry.bank,
            ) from exc
        body = resp.json() if resp.content else {}
        operation_id = str((body or {}).get("operation_id") or "")
        if not operation_id:
            raise SwitchError("the restore returned no operation id", bank=entry.bank)
        entry.restore_op = {"bank": url_bank, "id": operation_id}
        if url_bank == entry.bank:
            entry.own_ops.append(operation_id)
        self._save(journal)
        embedded = entry.counts.get("facts", 0) + entry.counts.get("observations", 0)
        budget = max(self.t.restore_floor_s, self.t.restore_factor * embedded / RESTORE_FACTS_PER_S)
        await self._wait_operation(
            url_bank, operation_id, timeout_s=budget, what=f"the restore into {target_id}"
        )
        entry.restore_op = None
        return await self.bank_state(target_id)

    async def _restore_checked(
        self, journal: Journal, entry: BankEntry, *, via: str | None = None
    ) -> None:
        """Restore into the temporary id and require the result to match the archive."""
        state = await self._restore(journal, entry, entry.temp, via=via)
        entry.restored_counts = state["counts"]
        entry.restored_fingerprint = state["fingerprint"]
        differs = _diff(entry.counts, state["counts"])
        if differs:
            self._save(journal)
            raise SwitchError(
                f"bank {entry.bank} did not come back whole: {_diff_text(differs)}",
                bank=entry.bank,
                details={"differs": differs},
            )

    # ── journal, lock, record ───────────────────────────────────────────

    def _save(self, journal: Journal) -> None:
        """Write the journal and its mirror, each atomically and synced to disk."""
        journal.updated_at = datetime.now(UTC).isoformat(timespec="seconds")
        text = json.dumps(journal.to_dict(), indent=2) + "\n"
        _write_synced(journal_path(), text, mode=0o644)
        _write_synced(Path(journal.archive_dir) / mirror_name(), text, mode=0o600)

    def _retire_journal(self, journal: Journal) -> None:
        """Close a finished run: the mirror keeps the last word, the primary goes."""
        journal.updated_at = datetime.now(UTC).isoformat(timespec="seconds")
        text = json.dumps(journal.to_dict(), indent=2) + "\n"
        _write_synced(Path(journal.archive_dir) / mirror_name(), text, mode=0o600)
        journal_path().unlink(missing_ok=True)

    @contextlib.contextmanager
    def _lock(self) -> Iterator[None]:
        """Hold the one switch lock for the life of the block.

        An ``flock`` on a file under ``state/``: the kernel drops it when the
        process ends, so a killed run never leaves a stale lock behind.
        """
        path = lock_path()
        os.close(nofollow.open_dir(path.parent, create=True))
        # Read-only is enough for ``flock``, and it means a lock file left
        # root-owned by one run never locks the service account out of the next.
        fd = os.open(path, os.O_RDONLY | os.O_CREAT | os.O_NOFOLLOW | os.O_CLOEXEC, 0o644)
        try:
            try:
                fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
            except BlockingIOError:
                raise SwitchRefused(
                    "another `hal0 memory embedder switch` is running on this box "
                    f"(it holds {path}); wait for it to finish"
                ) from None
            owner = perms.state_root_owner()
            if owner is not None:
                with contextlib.suppress(OSError):
                    os.fchown(fd, *owner)
            yield
        finally:
            os.close(fd)

    @staticmethod
    def _record_from(previous: Mapping[str, Any], *, mixed: bool | None = None) -> EmbedderRecord:
        return EmbedderRecord(
            provider=str(previous["provider"]),
            model=str(previous["model"]),
            source=str(previous.get("source") or SOURCE_SWITCH),
            mixed=bool(previous.get("mixed")) if mixed is None else mixed,
            conflict=previous.get("conflict")
            if isinstance(previous.get("conflict"), dict)
            else None,
        )

    # ── preflight ───────────────────────────────────────────────────────

    async def preflight(self, *, apply: bool = False) -> dict[str, Any]:
        """Everything a run needs to be true, checked without changing anything.

        Returns the plan: the embedders, the engine, every bank with its
        counts and queue depth, disk space, the estimated restore time, and
        ``refusals``, a list of ``{"code", "message"}``. A run goes ahead only
        when that list is empty. Nothing is written here, with or without
        ``apply``: not the embedder record, not a lock, not a directory.
        """
        target = self._target
        refusals: list[dict[str, str]] = []
        warnings: list[str] = []

        def refuse(code: str, message: str) -> None:
            refusals.append({"code": code, "message": message})

        plan: dict[str, Any] = {
            "target": target._asdict() if target is not None else None,
            "recorded": None,
            "mixed": False,
            "engine": {"reachable": False, "version": None},
            "banks": [],
            "totals": dict.fromkeys(COUNT_KEYS, 0),
            "disk": None,
            "estimate_s": None,
            "allow_pending": self._allow_pending,
            "refusals": refusals,
            "warnings": warnings,
        }
        if target is None:
            refuse("no_target", "name the embedding model to switch to")
            return plan
        if not embedder_record._valid_pair(target.provider, target.model):
            refuse(
                "invalid_target",
                f"{target.provider!r} / {target.model!r} is not a provider and model "
                "[memory.models] would accept",
            )
            return plan

        left = interrupted_switch()
        if left is not None:
            refuse(
                "interrupted_switch",
                f"an earlier switch (run {left.get('run_id')}, phase {left.get('phase')}) did "
                "not finish; see `hal0 memory embedder switch --status`, then run it with "
                "--resume",
            )
        gate = write_gate.read_gate()
        if gate is not None:
            refuse(
                "gate_set",
                f"memory writes are already paused (run {gate.get('run_id')}, archives in "
                f"{gate.get('archive_dir')}); see `hal0 memory embedder switch --status`",
            )
        if not apply and lock_held():
            refuse("locked", "another `hal0 memory embedder switch` is running on this box")
        hs_dir = self._engine_home()
        if (hs_dir / ".venv.new").exists():
            refuse(
                "engine_upgrade",
                f"a memory-engine upgrade is in progress or was left unfinished ({hs_dir}/.venv.new "
                "exists); let `hal0 update` finish first",
            )
        if (hs_dir / ".pg0.restore-failed").exists():
            refuse(
                "engine_data_untrusted",
                f"{hs_dir}/.pg0.restore-failed marks the engine's data directory as untrusted "
                "after a failed upgrade rollback; run `hal0 update` to restore it first",
            )
        job = update_in_progress()
        if job is not None:
            refuse(
                "update_in_progress",
                f"`hal0 update` job {job} is queued or running; let it finish (it may upgrade "
                "and restart the memory engine)",
            )
        if apply and not self._privileged():
            refuse(
                "not_privileged",
                "run this as root (sudo) or as the hal0 service account: it rewrites the "
                "engine's drop-in, restarts the engine and renames banks",
            )
        unreadable = admin_env_file_unreadable()
        if unreadable:
            refuse(
                "env_file_unreadable",
                f"{unreadable} (the engine unit's EnvironmentFile) is not readable by this "
                f"account, so {_ADMIN_BIN} would not see the engine's settings; run this as root",
            )

        # The record and the engine's environment. Read only: a run writes the
        # record down after every refusal has been evaluated.
        configured = self._configured_embedder()
        view = embedder_record.resolve_embedder(
            configured, hs_dir=hs_dir, evidence=self._evidence()
        )
        record = view.record
        plan["recorded"] = {**record.embedder._asdict(), "source": record.source}
        plan["mixed"] = record.mixed
        plan["configured"] = configured._asdict()
        if view.record_error:
            refuse(
                "record_unreadable",
                f"the embedder record at {view.record_error} cannot be read; repair or remove "
                "it first (hal0 never overwrites a record it cannot read)",
            )
        elif record.source == SOURCE_UNAVAILABLE:
            refuse(
                "store_unavailable",
                "the memory store's location is not available (a link that does not resolve, "
                "or a disk that is not mounted)",
            )
        elif record.source == SOURCE_CONFIGURED and not record.persisted:
            refuse(
                "no_store",
                "this box has no memory store yet, so there is nothing to re-embed: set "
                "[memory.models] and the engine will start on that embedder",
            )
        elif not record.persisted:
            warnings.append(
                f"the embedder record is not written down yet; a run records "
                f"{describe_embedder(*record.embedder)} first"
            )
        conflict = view.conflict
        if conflict and not view.record_error:
            said = "; ".join(
                f"{layer.replace('_', ' ')}: {describe_embedder(v['provider'], v['model'])}"
                for layer, v in conflict.items()
            )
            refuse(
                "record_conflict",
                f"the embedder record says {describe_embedder(*record.embedder)} but the engine "
                f"disagrees ({said}). Remove the override or correct the record, then run "
                "this again (see the memory_embedder row of `hal0 doctor all`)",
            )
        if view.live is None:
            refuse(
                "engine_environment",
                "the running engine's environment cannot be read (the engine is not running, "
                "or this account may not read it; use sudo). The switch confirms from it "
                "that the engine really moved",
            )
        if record.embedder == target and not record.mixed:
            refuse(
                "same_embedder",
                f"the store is already on {describe_embedder(*target)} and is not mixed; "
                "nothing to do",
            )

        # The engine.
        try:
            version = await self._client.request_json("GET", "/version", timeout_s=10.0)
        except httpx.HTTPError as exc:
            refuse(
                "engine_unreachable",
                f"the memory engine does not answer at {self._client.base_url} "
                f"({type(exc).__name__}); start it first",
            )
            return plan
        version = version if isinstance(version, dict) else {}
        features = version.get("features") if isinstance(version.get("features"), dict) else {}
        plan["engine"] = {
            "reachable": True,
            "version": version.get("api_version"),
            "export_api": bool(features.get("document_export_api")),
            "import_api": bool(features.get("document_import_api")),
            "live": view.live._asdict() if view.live is not None else None,
        }
        if not features.get("document_export_api"):
            refuse(
                "export_disabled",
                "the engine's export API is off (features.document_export_api on /version); "
                "remove HINDSIGHT_API_ENABLE_DOCUMENT_EXPORT_API=false from its environment "
                "and restart it",
            )
        if not features.get("document_import_api"):
            refuse(
                "import_disabled",
                "the engine's import API is off (features.document_import_api on /version); "
                "remove HINDSIGHT_API_ENABLE_DOCUMENT_IMPORT_API=false from its environment "
                "and restart it",
            )
        if not os.access(hs_dir / ".venv" / "bin" / _ADMIN_BIN, os.X_OK):
            refuse(
                "rename_tool_missing",
                f"{hs_dir}/.venv/bin/{_ADMIN_BIN} is missing or not executable; the switch "
                "renames each rebuilt bank into place with it",
            )

        # The banks.
        try:
            for bank in await self._bank_ids():
                state = await self.bank_state(bank)
                plan["banks"].append(
                    {k: state[k] for k in ("counts", "links", "pending", "processing")}
                    | {"bank": bank}
                )
                for key in COUNT_KEYS:
                    plan["totals"][key] += state["counts"][key]
                if (
                    isinstance(state["listed_facts"], int)
                    and state["listed_facts"] != state["nodes_total"]
                ):
                    warnings.append(
                        f"bank {bank}: the bank list says {state['listed_facts']} memories, its "
                        f"stats say {state['nodes_total']}; the switch goes by the stats"
                    )
        except SwitchError as exc:
            refuse("engine_error", exc.message)
            return plan
        leftovers = [b for b in plan["banks"] if is_switch_temp(b["bank"])]
        if leftovers:
            listing = "; ".join(
                f"{b['bank']} ({b['counts']['facts']} facts, {b['counts']['documents']} documents)"
                for b in leftovers
            )
            where = (gate or {}).get("archive_dir") or (left or {}).get("archive_dir")
            refuse(
                "leftover_banks",
                f"banks left by an earlier switch are still on the engine: {listing}"
                + (f"; its archives are in {where}" if where else "")
                + ". They may hold the only copy of a bank. See `hal0 memory embedder switch "
                "--status`; delete them only once every bank exists under its own id",
            )
        busy = [b for b in plan["banks"] if b["pending"] or b["processing"]]
        if busy:
            listing = ", ".join(
                f"{b['bank']} ({b['pending']} pending, {b['processing']} processing)" for b in busy
            )
            message = (
                f"the engine still has queued or running operations: {listing}. A queued "
                "retain is not in an export, so it would be lost with the old bank"
            )
            if self._allow_pending:
                warnings.append(
                    message + ". --allow-pending: going ahead; whatever those operations have "
                    "not written when a bank is exported is not carried over, and a bank that "
                    "moves after its export is replaced with a warning instead of a stop"
                )
            else:
                refuse(
                    "pending_operations",
                    message + ". Wait for them (`hal0 memory ops list` shows them), cancel them "
                    "(DELETE /api/memory/banks/<bank>/operations/<id>), or pass --allow-pending "
                    "to go ahead without them",
                )

        # Disk and time.
        plan["disk"] = self._disk_plan(hs_dir, refuse, warnings)
        embedded = plan["totals"]["facts"] + plan["totals"]["observations"]
        plan["estimate_s"] = round(embedded / RESTORE_FACTS_PER_S)
        if target != configured:
            warnings.append(
                f"[memory.models] asks for {describe_embedder(*configured)}; after the switch "
                f"set embeddings_provider/embeddings_model to {describe_embedder(*target)} so "
                "config and store agree"
            )
        return plan

    @staticmethod
    def _disk_plan(
        hs_dir: Path, refuse: Callable[[str, str], None], warnings: list[str]
    ) -> dict[str, Any]:
        """Free space on the two filesystems a run fills: the engine's and the archives'."""
        store_bytes = _du_bytes(hs_dir / _PG_DIR)
        store_at = _nearest_existing(hs_dir / _PG_DIR)
        archive_at = _nearest_existing(archive_root())
        store_free = shutil.disk_usage(store_at).free
        archive_free = shutil.disk_usage(archive_at).free
        store_need = int(store_bytes * STORE_FREE_FACTOR)
        archive_need = int(store_bytes * ARCHIVE_FREE_FACTOR)
        same = _device(store_at) == _device(archive_at)
        disk = {
            "archive_root": str(archive_root()),
            "store_bytes": store_bytes,
            "same_filesystem": same,
            "store_free_bytes": store_free,
            "store_needed_bytes": store_need + (archive_need if same else 0),
            "archive_free_bytes": archive_free,
            "archive_needed_bytes": archive_need,
        }
        if store_bytes == 0:
            warnings.append(
                f"could not measure the engine's data directory ({hs_dir}/{_PG_DIR}); "
                "free space was not checked"
            )
            return disk
        if store_free < disk["store_needed_bytes"]:
            refuse(
                "disk_space",
                f"{store_free // 2**20} MiB free on the filesystem holding the engine's data "
                f"({store_at}), {disk['store_needed_bytes'] // 2**20} MiB wanted: the rebuilt "
                "banks sit beside the originals until the swap"
                + (", and the archives go on the same filesystem" if same else ""),
            )
        if not same and archive_free < archive_need:
            refuse(
                "disk_space",
                f"{archive_free // 2**20} MiB free under {archive_at} for the archives, "
                f"{archive_need // 2**20} MiB wanted",
            )
        return disk

    # ── the run ─────────────────────────────────────────────────────────

    async def run(self) -> dict[str, Any]:
        """Preflight, then the whole switch. Raises :class:`SwitchRefused` or :class:`SwitchError`."""
        with self._lock():
            plan = await self.preflight(apply=True)
            if plan["refusals"]:
                raise SwitchRefused(plan["refusals"][0]["message"], plan)
            assert self._target is not None
            # Every refusal has been evaluated; only now is anything written.
            embedder_record.ensure_embedder_record(
                self._configured_embedder(), hs_dir=self._engine_home(), evidence=self._evidence()
            )
            previous = embedder_record.load_embedder_record()
            if previous is None:
                raise SwitchRefused("the embedder record could not be written down", plan)
            run_id = secrets.token_hex(4)
            try:
                archive_dir = self._make_archive_dir(run_id)
            except OSError as exc:
                raise SwitchRefused(
                    f"could not create the archive directory under {archive_root()}: {exc}", plan
                ) from exc
            journal = Journal(
                run_id=run_id,
                started_at=datetime.now(UTC).isoformat(timespec="seconds"),
                pid=os.getpid(),
                phase=PHASE_PAUSED,
                target=self._target._asdict(),
                previous={
                    "provider": previous.provider,
                    "model": previous.model,
                    "source": previous.source,
                    "mixed": previous.mixed,
                    "conflict": previous.conflict,
                },
                archive_dir=str(archive_dir),
                allow_pending=self._allow_pending,
            )
            self._save(journal)
            try:
                await self._pause(journal)
                await self._export_all(journal)
                await self._flip(journal)
                await self._restore_all(journal)
                await self._check_before_swap(journal)
            except BaseException as exc:
                cause = (
                    exc.message if isinstance(exc, SwitchError) else f"{type(exc).__name__}: {exc}"
                )
                await self._rollback(journal, cause)
                raise
            await self._swap_all(journal, rebuild=False)
            return await self._finish(journal)

    async def resume(self) -> dict[str, Any]:
        """Finish an interrupted run from its journal.

        A run that stopped inside the swap is carried forward; one that
        stopped anywhere earlier is rolled back (its originals are intact).
        The journal says which run it was and what each bank should match;
        what to do with each bank is decided from the engine as it is now.
        """
        with self._lock():
            found = locate_journal()
            journal: Journal | None = found["journal"]
            if journal is None:
                detail = await self._where_the_data_is()
                if found["error"]:
                    raise SwitchError(
                        f"the switch journal is not usable ({found['error']}) and no mirror "
                        f"of it was found. {detail} Nothing was changed"
                    )
                if write_gate.read_gate() is not None:
                    raise SwitchRefused(
                        "memory writes are paused but no journal of a switch was found, so "
                        f"there is nothing to resume from. {detail} See `hal0 memory embedder "
                        "switch --status`; `--clear-gate` lifts the pause once the banks are "
                        "in order"
                    )
                raise SwitchRefused("there is no interrupted embedder switch to resume")
            if found["error"]:
                self._say(f"the journal was read from its mirror {found['source']}")
            target = Embedder(journal.target["provider"], journal.target["model"])
            if self._target is not None and self._target != target:
                raise SwitchRefused(
                    f"the interrupted switch was to {describe_embedder(*target)}, not "
                    f"{describe_embedder(*self._target)}; run --resume without a model"
                )
            if not self._privileged():
                raise SwitchRefused("run this as root (sudo) or as the hal0 service account")
            self._target = target
            self._allow_pending = journal.allow_pending
            if journal.phase != PHASE_COMPLETE and write_gate.read_gate() is None:
                # Someone removed the pause by hand. Nothing below is safe
                # with writes coming in, so it goes back first.
                write_gate.engage_gate(run_id=journal.run_id, archive_dir=journal.archive_dir)
            self._save(journal)
            if journal.phase not in (PHASE_SWAPPING, PHASE_COMPLETE):
                await self._rollback(journal, journal.error or "the run was interrupted")
                return {
                    "status": "rolled_back",
                    "run_id": journal.run_id,
                    "archive_dir": journal.archive_dir,
                    "to": journal.previous,
                }
            try:
                await self._ensure_engine_on(target)
            except SwitchError as exc:
                exc.resumable = True
                raise
            await self._swap_all(journal, rebuild=True)
            return await self._finish(journal)

    async def _where_the_data_is(self) -> str:
        """One sentence naming the banks and temporary ids present, for a run with no journal."""
        try:
            ids = sorted(await self._bank_ids())
        except SwitchError:
            return "The engine does not answer, so the banks it holds could not be listed."
        temps = [b for b in ids if is_switch_temp(b)]
        return (
            f"Banks on the engine: {', '.join(b for b in ids if b not in temps) or 'none'}. "
            f"Banks under temporary ids from a switch: {', '.join(temps) or 'none'}."
        )

    async def _pause(self, journal: Journal) -> None:
        """Pause writes, let the queues drain, and take the list of banks this run rebuilds."""
        self._say("pausing memory writes")
        write_gate.engage_gate(run_id=journal.run_id, archive_dir=journal.archive_dir)
        self._hook("paused")
        deadline = self._clock() + self.t.settle_s
        while True:
            ids = [b for b in await self._bank_ids() if b not in journal.own_ids()]
            states = {bank: await self.bank_state(bank) for bank in ids}
            busy = {b: s for b, s in states.items() if s["pending"] or s["processing"]}
            if not busy or journal.allow_pending:
                break
            if self._clock() >= deadline:
                listing = ", ".join(
                    f"{bank} ({s['pending']} pending, {s['processing']} processing)"
                    for bank, s in busy.items()
                )
                raise SwitchError(
                    f"operations queued before the pause did not finish within "
                    f"{self.t.settle_s:g}s: {listing}"
                )
            await self._sleep(self.t.poll_s)
        journal.banks = [
            BankEntry(
                bank=bank,
                temp=temp_bank_id(bank, journal.run_id),
                counts=states[bank]["counts"],
                links=states[bank]["links"],
            )
            for bank in ids
        ]
        journal.phase = PHASE_EXPORTING
        self._save(journal)

    async def _stray_banks(self, journal: Journal) -> list[str]:
        """Banks on the engine that this run neither lists nor created."""
        known = {e.bank for e in journal.banks} | journal.own_ids()
        return [b for b in await self._bank_ids() if b not in known]

    async def _export_all(self, journal: Journal) -> None:
        for index, entry in enumerate(journal.banks):
            self._say(f"exporting {entry.bank} ({index + 1}/{len(journal.banks)})")
            await self._export_verified(journal, entry, index)
            self._hook(f"exported:{entry.bank}")

    async def _flip(self, journal: Journal) -> None:
        assert self._target is not None
        target = self._target
        self._hook("before_flip")
        self._say(f"moving the engine to {describe_embedder(*target)}")
        journal.phase = PHASE_FLIPPING
        journal.flip_started = True
        self._save(journal)
        # ``mixed`` until the swap is done: from here the engine embeds with
        # the target while every bank still holds the old model's vectors.
        embedder_record.save_embedder_record(
            EmbedderRecord(
                provider=target.provider, model=target.model, source=SOURCE_SWITCH, mixed=True
            )
        )
        await self._start_engine_on(target)
        self._hook("flipped")

    async def _restore_all(self, journal: Journal) -> None:
        journal.phase = PHASE_RESTORING
        self._save(journal)
        for index, entry in enumerate(journal.banks):
            self._say(f"rebuilding {entry.bank} ({index + 1}/{len(journal.banks)})")
            await self._restore_checked(journal, entry)
            entry.step = STEP_RESTORED
            self._save(journal)
            self._hook(f"restored:{entry.bank}")

    async def _original_moved(self, entry: BankEntry) -> str:
        """How the original differs from what its archive was taken from; empty when it does not."""
        state = await self.bank_state(entry.bank, ignore_ops=entry.own_ops)
        reasons: list[str] = []
        differs = _diff(entry.counts, state["counts"])
        if differs:
            reasons.append(_diff_text(differs))
        signals = _signals_moved(entry.fingerprint, state["fingerprint"])
        if signals:
            reasons.append("changed since its export: " + ", ".join(signals))
        if state["pending"] or state["processing"]:
            reasons.append(
                f"{state['pending']} pending and {state['processing']} processing operations"
            )
        return "; ".join(reasons)

    async def _check_before_swap(self, journal: Journal) -> None:
        """The last look before anything is deleted.

        No bank has appeared that this run does not rebuild, and every
        original still is what its archive was taken from.
        """
        strays = await self._stray_banks(journal)
        if strays:
            raise SwitchError(
                f"bank(s) {', '.join(strays)} appeared after the switch listed the banks: a "
                "client is writing to the engine without going through hal0. Stop it and run "
                "the switch again, which will include them",
                details={"stray_banks": strays},
            )
        for entry in journal.banks:
            moved = await self._original_moved(entry)
            if not moved:
                continue
            if journal.allow_pending:
                entry.note = f"changed after its export and not carried over: {moved}"
                self._say(f"warning: {entry.bank} {entry.note}")
                continue
            raise SwitchError(
                f"bank {entry.bank} changed while writes were paused ({moved}): a client is "
                "writing to the engine without going through hal0. Stop it and run the "
                "switch again",
                bank=entry.bank,
            )
        self._save(journal)

    # ── swap ────────────────────────────────────────────────────────────

    async def _rebuilt_differs(self, entry: BankEntry, bank_id: str) -> str:
        """How the rebuilt bank, under ``bank_id``, differs from its archive; empty when not."""
        state = await self.bank_state(bank_id)
        reasons: list[str] = []
        differs = _diff(entry.counts, state["counts"])
        if differs:
            reasons.append(_diff_text(differs))
        signals = _signals_moved(entry.restored_fingerprint, state["fingerprint"], CONTENT_SIGNALS)
        if signals:
            reasons.append("differs from the bank that was rebuilt in: " + ", ".join(signals))
        return "; ".join(reasons)

    async def _rebuild(self, journal: Journal, entry: BankEntry, index: int) -> None:
        """Take the original as it is now and rebuild its replacement from that."""
        self._say(f"{entry.bank} moved after its export; exporting and rebuilding it again")
        await self._delete_bank(entry.temp)
        entry.step = STEP_PLANNED
        entry.note = None
        self._save(journal)
        await self._export_verified(journal, entry, index)
        await self._restore_checked(journal, entry)
        entry.step = STEP_RESTORED
        self._save(journal)

    async def _recover(self, journal: Journal, entry: BankEntry) -> None:
        """Bring back a bank that exists under neither id, into its temporary id.

        The engine records a restore against the bank in the URL and refuses a
        target that exists, so the URL cannot be the bank being restored: the
        restore goes through an empty scratch bank this run creates, into the
        temporary id, and the ordinary rename follows.
        """
        self._say(f"{entry.bank} exists under neither id; restoring it from its archive")
        if journal.scratch not in set(await self._bank_ids()):
            await self._json(
                "PUT",
                _bank_path(journal.scratch),
                body={},
                what="creating the scratch bank",
                bank=journal.scratch,
            )
        await self._restore_checked(journal, entry, via=journal.scratch)

    async def _swap_one(
        self, journal: Journal, entry: BankEntry, index: int, *, rebuild: bool
    ) -> None:
        """Put the rebuilt bank in the original's place. Safe to run again after any stop.

        What to do comes from :func:`swap_action`: the ids the engine has now,
        with the journal step as tie-breaker. Each destructive call is
        preceded by a synced journal save naming it.
        """
        bank, temp = entry.bank, entry.temp
        existing = set(await self._bank_ids())
        action = swap_action(entry.step, bank in existing, temp in existing)

        if action == ACTION_RECREATED:
            raise SwitchError(
                f"bank {bank} exists again after the switch deleted it: a client that bypasses "
                f"hal0 recreated it. The rebuilt bank is {temp}. Move what the new {bank} holds "
                f"or delete it (`hal0 memory bank delete`), then run --resume",
                bank=bank,
            )
        if action == ACTION_REBUILD:
            if not rebuild:
                raise SwitchError(
                    f"the rebuilt bank {temp} is gone and {bank} still exists; nothing was "
                    f"deleted. Run --resume, which rebuilds it from {bank}",
                    bank=bank,
                )
            await self._rebuild(journal, entry, index)
            action = ACTION_SWAP
        if action == ACTION_RECOVER:
            await self._recover(journal, entry)
            action = ACTION_RENAME

        if action == ACTION_SWAP:
            # Never delete an original without a whole replacement in hand,
            # and never one that is no longer what was archived.
            wrong = await self._rebuilt_differs(entry, temp)
            if wrong:
                raise SwitchError(
                    f"the rebuilt bank {temp} does not match the archive of {bank} ({wrong}); "
                    f"{bank} was not touched",
                    bank=bank,
                )
            await self._wait_quiet(temp)
            moved = await self._original_moved(entry)
            if moved and journal.allow_pending:
                entry.note = f"changed after its export and not carried over: {moved}"
                self._say(f"warning: {bank} {entry.note}")
            elif moved and rebuild:
                await self._rebuild(journal, entry, index)
                await self._wait_quiet(temp)
                moved = await self._original_moved(entry)
            if moved and not journal.allow_pending:
                raise SwitchError(
                    f"bank {bank} changed after its archive was taken ({moved}), so it was not "
                    "deleted: a client is writing to the engine without going through hal0. "
                    f"{bank} and every bank after it are still on their originals; the banks "
                    "before it are already rebuilt. Stop that client, then run --resume, which "
                    f"exports and rebuilds {bank} again from what it holds now and carries on",
                    bank=bank,
                )
            self._hook(f"before_delete:{bank}")
            entry.step = STEP_DELETING
            self._save(journal)
            await self._delete_bank(bank)
            entry.step = STEP_DELETED
            self._save(journal)
            self._hook(f"deleted:{bank}")
            action = ACTION_RENAME

        if action == ACTION_RENAME:
            await self._wait_quiet(temp)
            self._hook(f"before_rename:{bank}")
            entry.step = STEP_RENAMING
            self._save(journal)
            output = await asyncio.to_thread(self._rename_bank, temp, bank)
            if "could not be rebuilt" in output or " failed" in output:
                entry.note = output
            self._hook(f"renamed:{bank}")

        # ACTION_VERIFY, and the end of every other path: the bank must be
        # under its own id and match its archive before the journal says so.
        wrong = await self._rebuilt_differs(entry, bank)
        if wrong:
            raise SwitchError(
                f"bank {bank} is under its final id but does not match its archive ({wrong}). "
                f"Nothing more was changed. Its archive is {journal.archive_dir}/{entry.archive}; "
                "see `hal0 memory embedder switch --status` for the ids that hold data",
                bank=bank,
            )
        entry.step = STEP_RENAMED
        self._save(journal)

    async def _swap_all(self, journal: Journal, *, rebuild: bool) -> None:
        # The journal of per-bank swaps goes to disk before any original is touched.
        journal.phase = PHASE_SWAPPING
        journal.error = None
        self._save(journal)
        self._hook("before_swap")
        for index, entry in enumerate(journal.banks):
            self._say(f"swapping {entry.bank} into place ({index + 1}/{len(journal.banks)})")
            try:
                # A bank journalled as renamed goes through this too: the step
                # is only believed once the engine agrees.
                await self._swap_one(journal, entry, index, rebuild=rebuild)
            except SwitchError as exc:
                journal.error = exc.message
                self._save(journal)
                exc.resumable = True
                raise

    async def _finish(self, journal: Journal) -> dict[str, Any]:
        """Close the run: record the embedder, drop the scratch bank, lift the pause."""
        self._hook("before_finish")
        target = Embedder(journal.target["provider"], journal.target["model"])
        try:
            present = set(await self._bank_ids())
            if journal.scratch in present:
                await self._delete_bank(journal.scratch)
            strays = await self._stray_banks(journal)
        except SwitchError as exc:
            journal.error = exc.message
            self._save(journal)
            exc.resumable = True
            raise
        journal.stray_banks = strays
        embedder_record.save_embedder_record(
            EmbedderRecord(
                provider=target.provider,
                model=target.model,
                source=SOURCE_SWITCH,
                mixed=bool(strays),
            )
        )
        write_gate.lift_gate()
        journal.phase = PHASE_COMPLETE
        self._retire_journal(journal)
        self._say("memory writes resumed")
        warnings = [f"{e.bank}: {e.note}" for e in journal.banks if e.note]
        if strays:
            warnings.append(
                f"bank(s) {', '.join(strays)} appeared during the switch and were not rebuilt, "
                "so the store is recorded as mixed. Run `hal0 memory embedder switch "
                f"{target.model}` again: a mixed store may be switched to the model it is on, "
                "and that run rebuilds every bank"
            )
        return {
            "status": "switched",
            "run_id": journal.run_id,
            "from": {k: journal.previous.get(k) for k in ("provider", "model")},
            "to": journal.target,
            "mixed": bool(strays),
            "stray_banks": strays,
            "banks": [
                {"bank": e.bank, "counts": e.counts, "links": e.links, "note": e.note}
                for e in journal.banks
            ],
            "warnings": warnings,
            "archive_dir": journal.archive_dir,
            "delete_archives": f"rm -r {journal.archive_dir}",
        }

    # ── rollback ────────────────────────────────────────────────────────

    async def _stop_restores(self, journal: Journal) -> None:
        """Cancel every restore that may still run and wait until each has really ended."""
        for entry in journal.banks:
            op = entry.restore_op
            if not op:
                continue
            path = f"{_bank_path(op['bank'])}/operations/{quote(op['id'], safe='')}"
            with contextlib.suppress(SwitchError):
                await self._json("DELETE", path, what="cancelling the restore", bank=entry.bank)
            deadline = self._clock() + self.t.restore_floor_s
            while True:
                found = await self._json("GET", path, what="reading the restore", bank=entry.bank)
                status = found.get("status") if isinstance(found, dict) else None
                if status not in _OP_LIVE:
                    break
                if self._clock() >= deadline:
                    raise SwitchError(
                        f"the restore of {entry.bank} was cancelled but is still {status} after "
                        f"{self.t.restore_floor_s:g}s; its temporary bank could still appear",
                        bank=entry.bank,
                    )
                await self._sleep(self.t.poll_s)
            entry.restore_op = None
            self._save(journal)

    async def _sweep(self, journal: Journal) -> None:
        """Delete every id this run created that the engine still has."""
        own = journal.own_ids()
        for bank in await self._bank_ids():
            if bank in own:
                await self._delete_bank(bank)

    async def _rollback(self, journal: Journal, cause: str) -> None:
        """Undo a run that has not reached the swap. Safe to run again.

        Raises :class:`SwitchError` (``resumable``) when a step cannot be
        undone; the pause then stays and ``--resume`` tries again.
        """
        previous = Embedder(str(journal.previous["provider"]), str(journal.previous["model"]))
        self._say(f"undoing the switch: {cause}")
        try:
            journal.phase = PHASE_ROLLING_BACK
            journal.error = cause
            self._save(journal)
            drifted: list[str] = []
            if await self._healthy():
                await self._stop_restores(journal)
                await self._sweep(journal)
            if journal.flip_started:
                embedder_record.save_embedder_record(self._record_from(journal.previous))
                await self._ensure_engine_on(previous)
                await self._stop_restores(journal)
                # Anything written while the engine ran the target was embedded
                # with it, into banks that are staying on the old model.
                for entry in journal.banks:
                    if entry.step == STEP_PLANNED:
                        continue
                    if await self._original_moved(entry):
                        drifted.append(entry.bank)
                if drifted and not journal.previous.get("mixed"):
                    embedder_record.save_embedder_record(
                        self._record_from(journal.previous, mixed=True)
                    )
                    self._say(
                        "warning: " + ", ".join(drifted) + " changed while the engine ran the "
                        "target embedder; the store is now recorded as mixed"
                    )
            # Last, with nothing left running that could create one.
            await self._sweep(journal)
            write_gate.lift_gate()
            journal.phase = PHASE_ROLLED_BACK
            self._retire_journal(journal)
            self._say("memory writes resumed; the store is on its original embedder")
        except BaseException as exc:
            detail = exc.message if isinstance(exc, SwitchError) else f"{type(exc).__name__}: {exc}"
            journal.phase = PHASE_ROLLBACK_FAILED
            journal.error = f"{cause}; and undoing it failed: {detail}"
            with contextlib.suppress(OSError):
                self._save(journal)
            if isinstance(exc, Exception):
                raise SwitchError(
                    f"the switch failed ({cause}) and could not be undone ({detail}). Memory "
                    "writes stay paused. Fix the cause and run "
                    "`hal0 memory embedder switch --resume`",
                    resumable=True,
                ) from exc
            raise

    # ── status and the escape hatch ─────────────────────────────────────

    async def status(self) -> dict[str, Any]:
        """Where a switch stands: journal, pause, lock, banks, and what ``--resume`` would do.

        Reads only. Works without a journal and without the engine.
        """
        found = locate_journal()
        journal: Journal | None = found["journal"]
        out: dict[str, Any] = {
            "journal": journal.to_dict() if journal is not None else None,
            "journal_source": found["source"],
            "journal_error": found["error"],
            "gate": write_gate.read_gate(),
            "lock_held": lock_held(),
            "engine": {"reachable": False, "live": None},
            "banks": [],
            "resume": [],
            "summary": "",
        }
        live = _pair(self._evidence().get("live"))
        out["engine"]["live"] = live._asdict() if live is not None else None
        entries = {e.bank: e for e in journal.banks} if journal is not None else {}
        temps = {e.temp: e for e in journal.banks} if journal is not None else {}
        present: set[str] = set()
        try:
            for bank in await self._bank_ids():
                present.add(bank)
                if bank in entries:
                    role = "original or rebuilt"
                elif bank in temps:
                    role = f"temporary (rebuilt copy of {temps[bank].bank})"
                elif journal is not None and bank == journal.scratch:
                    role = "scratch"
                elif is_switch_temp(bank):
                    role = "temporary (not from the journalled run)"
                else:
                    role = "not in the journal" if journal is not None else "bank"
                out["banks"].append(
                    {"bank": bank, "role": role, "counts": (await self.bank_state(bank))["counts"]}
                )
            out["engine"]["reachable"] = True
        except SwitchError as exc:
            out["engine"]["error"] = exc.message
        if journal is None:
            if found["error"]:
                out["summary"] = (
                    "the journal cannot be used and no mirror was found; --resume will refuse. "
                    "The banks above are where the data is"
                )
            elif out["gate"] is not None:
                out["summary"] = (
                    "memory writes are paused but there is no journal; nothing to resume. "
                    "--clear-gate lifts the pause once the banks above are in order"
                )
            else:
                out["summary"] = "no switch is in progress"
            return out
        if journal.phase != PHASE_SWAPPING:
            out["summary"] = (
                f"the run stopped in phase {journal.phase}, before any original was replaced: "
                "--resume undoes it (temporary banks deleted, engine back on "
                f"{journal.previous.get('model')}, pause lifted)"
            )
            return out
        if out["engine"]["reachable"]:
            for entry in journal.banks:
                out["resume"].append(
                    {
                        "bank": entry.bank,
                        "temp": entry.temp,
                        "step": entry.step,
                        "original_present": entry.bank in present,
                        "rebuilt_present": entry.temp in present,
                        "action": swap_action(
                            entry.step, entry.bank in present, entry.temp in present
                        ),
                    }
                )
        out["summary"] = (
            "the run stopped inside the swap: --resume finishes it bank by bank as listed, "
            "then lifts the pause"
        )
        return out

    def clear_gate(self) -> dict[str, Any]:
        """Lift the write pause by hand. Refuses while any run is unfinished or running.

        For the one case nothing else covers: the pause is set and no journal
        of a run survives anywhere. It checks nothing about the banks.
        """
        if lock_held():
            raise SwitchRefused("a `hal0 memory embedder switch` is running; it owns the pause")
        found = locate_journal()
        if found["journal"] is not None:
            raise SwitchRefused(
                f"an embedder switch (run {found['journal'].run_id}, phase "
                f"{found['journal'].phase}) is unfinished; lifting the pause now would let "
                "writes into banks that are about to be replaced. Run "
                "`hal0 memory embedder switch --resume`"
            )
        if found["error"]:
            raise SwitchRefused(
                f"a switch journal exists but cannot be used ({found['error']}), so an "
                "unfinished swap cannot be ruled out. Check `hal0 memory embedder switch "
                "--status`, put the banks in order, remove that file, then run this again"
            )
        gate = write_gate.read_gate()
        if gate is None:
            raise SwitchRefused("memory writes are not paused")
        write_gate.lift_gate()
        log.warning("hal0.memory.write_gate_cleared_by_hand", run_id=gate.get("run_id"))
        return {"status": "cleared", "gate": gate}


__all__ = [
    "ACTION_REBUILD",
    "ACTION_RECOVER",
    "ACTION_RECREATED",
    "ACTION_RENAME",
    "ACTION_SWAP",
    "ACTION_VERIFY",
    "CONTENT_SIGNALS",
    "COUNT_KEYS",
    "FINGERPRINT_SIGNALS",
    "HOOK_POINTS",
    "RESTORE_FACTS_PER_S",
    "BankEntry",
    "EmbedderSwitch",
    "Journal",
    "SwitchError",
    "SwitchRefused",
    "Timeouts",
    "admin_command",
    "admin_env_file_unreadable",
    "admin_environment",
    "archive_root",
    "blocking_reason",
    "interrupted_switch",
    "is_switch_temp",
    "journal_path",
    "load_journal",
    "locate_journal",
    "lock_held",
    "lock_path",
    "run_admin_rename",
    "scratch_bank_id",
    "swap_action",
    "temp_bank_id",
    "update_in_progress",
]
