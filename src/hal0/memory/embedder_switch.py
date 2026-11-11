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
   Refuses, changing nothing, unless everything below holds.
2. **Export** every bank to ``<state>/backups/memory-embedder-switch-<run>/``
   (``0700``; hal0's directory for restore points) and check each archive
   against the bank's counts.
3. **Pause writes** (:mod:`hal0.memory.write_gate`), wait for the engine's
   queues to drain, and re-export any bank that moved between its export and
   the pause.
4. **Flip**: record the target, re-render the managed drop-in from it, restart
   the engine, wait for health, and confirm from the engine's own environment
   that it runs the target.
5. **Restore** every archive into ``<bank>.switch-<run>`` and compare facts,
   observations, mental models, directives and documents with the counts
   taken before the switch. Every bank is restored and compared before any
   original is touched.
6. **Swap**, bank by bank: delete the original, rename the temporary bank
   into place. Then clear ``mixed`` and ``conflict`` on the record and lift
   the pause. The archives are kept.

Anything that fails before step 6 is undone (:meth:`EmbedderSwitch._rollback`):
temporary banks deleted, record and drop-in put back, engine restarted on the
original embedder, pause lifted. The originals were never touched.

Step 6 cannot be undone, so it is resumable instead. A journal
(:func:`journal_path`) is written before the first original is deleted and
updated around every delete and rename; ``--resume``
(:meth:`EmbedderSwitch.resume`) reads it, looks at which of the two ids the
engine still has for each bank, and finishes. A bank found under neither id
is restored from its archive. Writes stay paused until a resume completes.

Renaming
--------

hindsight-api 1.2.0 has no HTTP rename (its OpenAPI has ``PUT``, ``PATCH`` and
``DELETE`` on ``/v1/default/banks/{bank_id}``, none of which changes the id,
and an alias API that adds a second name without changing the first). The
rename is the engine's own admin command, run from the engine's venv with the
engine unit's environment and as the account that owns the engine home::

    <engine home>/.venv/bin/hindsight-admin rename-bank --from <temp> --to <bank>

It moves every row in one transaction and refuses a bank with pending or
processing operations, which is why the swap checks the temporary bank's
queue before it deletes the original.

What the pause does not cover
-----------------------------

A client that writes to the engine's port directly (the Hindsight
coding-agents plugin does today, ADR-0058 decision 4b) is not stopped by
hal0's pause. The switch therefore compares every original with its archive
again right before the first delete and rolls back if one moved. With
``--allow-pending`` that comparison only warns: the operator has accepted that
work still queued or running at export time is not carried over (the engine's
export leaves in-flight operations out).
"""

from __future__ import annotations

import asyncio
import contextlib
import fcntl
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

#: Measured on hindsight-api 1.2.0, CPU embedder, 2026-11-10 (ADR-0058 "Open
#: questions"): a bank restore re-embeds roughly this many facts per second.
RESTORE_FACTS_PER_S = 60.0

#: Free space wanted, as a multiple of the engine's data directory: the
#: restored banks sit beside the originals until the swap, and the archives
#: (no vectors, no indexes) come on top. The engine upgrade asks the same
#: factor for its snapshot (``engine_upgrade._SNAPSHOT_FREE_FACTOR``).
DISK_FREE_FACTOR = 1.5

#: hal0-api's bank-id grammar (``memory_admin._BANK_RE``) allows 128 chars; a
#: temporary id that would not fit is shortened, never truncated mid-name.
_BANK_ID_MAX = 128

_ADMIN_BIN = "hindsight-admin"
_PG_DIR = ".pg0"

# Journal phases, in the order a run passes through them.
PHASE_EXPORTING = "exporting"
PHASE_PAUSED = "paused"
PHASE_FLIPPING = "flipping"
PHASE_RESTORING = "restoring"
PHASE_SWAPPING = "swapping"
PHASE_ROLLING_BACK = "rolling_back"
PHASE_ROLLBACK_FAILED = "rollback_failed"
PHASE_COMPLETE = "complete"

# Per-bank steps.
STEP_PLANNED = "planned"
STEP_EXPORTED = "exported"
STEP_RESTORED = "restored"
STEP_DELETING = "deleting"
STEP_DELETED = "deleted"
STEP_RENAMED = "renamed"

_OP_FAILED = ("failed", "cancelled", "not_found")


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
    """Preflight refused; nothing was changed. ``plan`` is the full preflight report."""

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
    #: at :data:`RESTORE_FACTS_PER_S`, whichever is more.
    restore_floor_s: float = 900.0
    restore_factor: float = 6.0
    #: The temporary bank's queue going quiet before its rename.
    quiet_s: float = 120.0
    rename_s: float = 3600.0
    http_s: float = 120.0


@dataclass
class BankEntry:
    """One bank's line in the journal."""

    bank: str
    temp: str
    counts: dict[str, int]
    links: int = 0
    archive: str | None = None
    step: str = STEP_PLANNED
    restored_counts: dict[str, int] | None = None
    note: str | None = None


@dataclass
class Journal:
    """The state of one run, on disk at :func:`journal_path` from export to finish.

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
    error: str | None = None
    updated_at: str | None = None
    version: int = JOURNAL_VERSION

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> Journal:
        banks = [BankEntry(**b) for b in data.get("banks") or []]
        fields = {k: v for k, v in data.items() if k in cls.__dataclass_fields__ and k != "banks"}
        return cls(banks=banks, **fields)


def journal_path() -> Path:
    """``/var/lib/hal0/state/memory-embedder-switch.json`` (sandboxed under ``HAL0_HOME``)."""
    return var_lib() / "state" / "memory-embedder-switch.json"


def lock_path() -> Path:
    return var_lib() / "state" / "memory-embedder-switch.lock"


def archive_root() -> Path:
    """``/var/lib/hal0/backups``: where hal0 keeps restore points, one directory per run here."""
    return var_lib() / "backups"


def load_journal(path: Path | None = None) -> Journal | None:
    """The journal of an unfinished run, or ``None``. Raises :class:`SwitchError` if unreadable."""
    target = path if path is not None else journal_path()
    try:
        raw = target.read_text(encoding="utf-8")
    except FileNotFoundError:
        return None
    except (OSError, UnicodeDecodeError) as exc:
        raise SwitchError(f"the switch journal at {target} cannot be read ({exc})") from exc
    try:
        data = json.loads(raw)
        if not isinstance(data, dict) or data.get("version") != JOURNAL_VERSION:
            raise ValueError("not a version 1 switch journal")
        return Journal.from_dict(data)
    except (ValueError, TypeError) as exc:
        raise SwitchError(f"the switch journal at {target} is not usable ({exc})") from exc


def interrupted_switch(path: Path | None = None) -> dict[str, Any] | None:
    """A short description of an unfinished switch for status surfaces, else ``None``.

    Never raises: ``hal0 doctor`` calls it.
    """
    try:
        journal = load_journal(path)
    except SwitchError as exc:
        return {"phase": "unreadable", "error": exc.message}
    if journal is None:
        return None
    return {
        "phase": journal.phase,
        "run_id": journal.run_id,
        "started_at": journal.started_at,
        "target": journal.target,
        "error": journal.error,
        "archive_dir": journal.archive_dir,
    }


def temp_bank_id(bank: str, run_id: str) -> str:
    """``<bank>.switch-<run>``, or a short id built from a digest when that would not fit."""
    candidate = f"{bank}.switch-{run_id}"
    if len(candidate.encode("utf-8")) <= _BANK_ID_MAX:
        return candidate
    import hashlib

    digest = hashlib.sha256(bank.encode("utf-8")).hexdigest()[:16]
    return f"switch-{run_id}-{digest}"


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


def _pair(raw: Mapping[str, Any] | None) -> Embedder | None:
    """A raw evidence layer as a pair; an unset key is the engine default."""
    if raw is None:
        return None
    return Embedder(
        raw.get("provider") or ENGINE_DEFAULT_PROVIDER,
        raw.get("model") or ENGINE_DEFAULT_EMBEDDER,
    )


def admin_command(hs_dir: Path, old: str, new: str) -> list[str]:
    """argv of the engine's own bank rename (``hindsight_api/admin/cli.py``, 1.2.0)."""
    return [str(hs_dir / ".venv" / "bin" / _ADMIN_BIN), "rename-bank", "--from", old, "--to", new]


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
    """Rename bank ``old`` to ``new`` with ``hindsight-admin``; returns its output.

    A root caller runs it as the account that owns the engine home: the
    embedded Postgres belongs to that account and refuses root. Raises
    :class:`SwitchError` with the command's own message on a non-zero exit.
    """
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
        raise SwitchError(f"could not run {_ADMIN_BIN} rename-bank: {exc}", bank=new) from exc
    output = "\n".join(part.strip() for part in (proc.stdout, proc.stderr) if part and part.strip())
    if proc.returncode != 0:
        raise SwitchError(
            f"{_ADMIN_BIN} rename-bank --from {old} --to {new} exited {proc.returncode}: "
            f"{output[-600:] or 'no output'}",
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
    says whether this process may do all of that.
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
        what: str,
        bank: str | None = None,
        timeout_s: float | None = None,
    ) -> Any:
        try:
            return await self._client.request_json(
                method, path, params=params, timeout_s=timeout_s or self.t.http_s
            )
        except httpx.HTTPStatusError as exc:
            body = exc.response.text[:300] if exc.response is not None else ""
            raise SwitchError(
                f"{what}: the engine answered HTTP {exc.response.status_code} {body}".rstrip(),
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

    async def _bank_ids(self) -> list[str]:
        ids: list[str] = []
        offset = 0
        while True:
            page = await self._json(
                "GET",
                "/v1/default/banks",
                params={"limit": 100, "offset": offset},
                what="listing banks",
            )
            rows = page.get("banks") if isinstance(page, dict) else None
            rows = rows if isinstance(rows, list) else []
            ids.extend(str(r["bank_id"]) for r in rows if isinstance(r, dict) and "bank_id" in r)
            offset += len(rows)
            total = page.get("total") if isinstance(page, dict) else None
            if not rows or (isinstance(total, int) and offset >= total):
                return ids

    async def _list_total(self, path: str, params: dict[str, Any], *, what: str, bank: str) -> int:
        """How many rows a paged engine list holds: its ``total``, else a count of every page."""
        first = await self._json("GET", path, params={**params, "limit": 1}, what=what, bank=bank)
        total = first.get("total") if isinstance(first, dict) else None
        if isinstance(total, int):
            return total
        count = offset = 0
        while True:
            page = await self._json(
                "GET", path, params={**params, "limit": 100, "offset": offset}, what=what, bank=bank
            )
            items = page.get("items") if isinstance(page, dict) else None
            n = len(items) if isinstance(items, list) else 0
            count += n
            offset += n
            if n < 100:
                return count

    async def bank_state(self, bank: str) -> dict[str, Any]:
        """Counts and queue depth for one bank, uncached.

        Facts are every memory unit that is not an observation
        (``nodes_by_fact_type``); mental models include the ones that back a
        knowledge page; directives include inactive ones.
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
        return {
            "counts": {
                "facts": facts,
                "observations": observations,
                "mental_models": await self._list_total(
                    f"{base}/mental-models", {}, what="counting mental models", bank=bank
                ),
                "directives": await self._list_total(
                    f"{base}/directives",
                    {"active_only": "false"},
                    what="counting directives",
                    bank=bank,
                ),
                "documents": int(stats.get("total_documents") or 0),
            },
            "links": int(stats.get("total_links") or 0),
            "pending": int(ops.get("pending") or 0),
            "processing": int(ops.get("processing") or 0),
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

        The agreed counts become the baseline every later comparison uses.
        """
        last: dict[str, dict[str, int]] = {}
        for _attempt in range(2):
            archived = await self._export_bank(journal, entry, index)
            now = await self.bank_state(entry.bank)
            last = _diff(now["counts"], archived)
            if not last or journal.allow_pending:
                # --allow-pending: operations may still be writing, so the
                # archive, not the moving bank, is what the rebuilt bank must
                # match.
                entry.counts, entry.links = archived, now["links"]
                entry.step = STEP_EXPORTED
                self._save(journal)
                return
        raise SwitchError(
            f"the archive of bank {entry.bank} does not match the bank: {_diff_text(last)} "
            "(the archive's count, then the bank's). Either something is still writing to "
            "the bank, or it holds memories the engine's export does not carry",
            bank=entry.bank,
            details={"differs": last},
        )

    async def _restore(self, journal: Journal, entry: BankEntry, target_id: str) -> dict[str, int]:
        """Restore ``entry``'s archive into ``target_id`` and return that bank's counts."""
        bank = entry.bank
        try:
            with self._archive_dir_fd(journal) as dfd:
                fd = os.open(str(entry.archive), os.O_RDONLY | os.O_NOFOLLOW, dir_fd=dfd)
                with os.fdopen(fd, "rb") as fh:
                    resp = await self._client._http.post(
                        f"{_bank_path(bank)}/transfer/import",
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
                bank=bank,
            ) from exc
        except (OSError, httpx.HTTPError) as exc:
            raise SwitchError(
                f"starting the restore into {target_id} failed ({type(exc).__name__}: {exc})",
                bank=bank,
            ) from exc
        body = resp.json() if resp.content else {}
        operation_id = str((body or {}).get("operation_id") or "")
        if not operation_id:
            raise SwitchError("the restore returned no operation id", bank=bank)
        entry.note = f"restore operation {operation_id}"
        self._save(journal)
        embedded = entry.counts.get("facts", 0) + entry.counts.get("observations", 0)
        budget = max(self.t.restore_floor_s, self.t.restore_factor * embedded / RESTORE_FACTS_PER_S)
        # The engine records a restore against the bank named in the URL: the
        # target does not exist until the operation has run.
        await self._wait_operation(
            bank, operation_id, timeout_s=budget, what=f"the restore into {target_id}"
        )
        return (await self.bank_state(target_id))["counts"]

    # ── journal, lock, record ───────────────────────────────────────────

    def _save(self, journal: Journal) -> None:
        journal.updated_at = datetime.now(UTC).isoformat(timespec="seconds")
        nofollow.write_file(
            journal_path(),
            json.dumps(journal.to_dict(), indent=2) + "\n",
            mode=0o644,
            owner=perms.state_root_owner(),
        )

    def _retire_journal(self, journal: Journal) -> None:
        """Move the finished journal beside the archives it describes."""
        journal.updated_at = datetime.now(UTC).isoformat(timespec="seconds")
        with contextlib.suppress(OSError):
            nofollow.write_file(
                Path(journal.archive_dir) / "journal.json",
                json.dumps(journal.to_dict(), indent=2) + "\n",
                mode=0o600,
                owner=perms.state_root_owner(),
            )
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
    def _lock_held() -> bool:
        """True when another process holds the switch lock. Creates nothing."""
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
        when that list is empty. With ``apply`` the box's embedder record is
        written down first if it never was (the one write a preflight makes,
        and only on the way into a real run).
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

        pending_journal = interrupted_switch()
        if pending_journal is not None:
            refuse(
                "interrupted_switch",
                f"an earlier switch (run {pending_journal.get('run_id')}, phase "
                f"{pending_journal.get('phase')}) did not finish; run "
                "`hal0 memory embedder switch --resume` first",
            )
        if not apply and self._lock_held():
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
        if apply and not self._privileged():
            refuse(
                "not_privileged",
                "run this as root (sudo) or as the hal0 service account: it rewrites the "
                "engine's drop-in, restarts the engine and renames banks",
            )

        # The record and the engine's environment.
        configured = self._configured_embedder()
        resolver = (
            embedder_record.ensure_embedder_record if apply else embedder_record.resolve_embedder
        )
        view = resolver(configured, hs_dir=hs_dir, evidence=self._evidence())
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
            # A dry run on a box whose record was never written down: a real
            # run writes it first, from the same evidence.
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
            bank_ids = await self._bank_ids()
            for bank in bank_ids:
                state = await self.bank_state(bank)
                plan["banks"].append({"bank": bank, **state})
                for key in COUNT_KEYS:
                    plan["totals"][key] += state["counts"][key]
        except SwitchError as exc:
            refuse("engine_error", exc.message)
            return plan
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
                    "not written when a bank is exported is not carried over"
                )
            else:
                refuse(
                    "pending_operations",
                    message + ". Wait for them (`hal0 memory ops list` shows them), cancel them "
                    "(DELETE /api/memory/banks/<bank>/operations/<id>), or pass --allow-pending "
                    "to go ahead without them",
                )

        # Disk and time.
        store_bytes = _du_bytes(hs_dir / _PG_DIR)
        root = archive_root()
        free = shutil.disk_usage(_nearest_existing(root)).free
        needed = int(store_bytes * DISK_FREE_FACTOR)
        plan["disk"] = {
            "archive_root": str(root),
            "store_bytes": store_bytes,
            "needed_bytes": needed,
            "free_bytes": free,
        }
        if store_bytes == 0:
            warnings.append(
                f"could not measure the engine's data directory ({hs_dir}/{_PG_DIR}); "
                "free space was not checked"
            )
        elif free < needed:
            refuse(
                "disk_space",
                f"{free // 2**20} MiB free under {_nearest_existing(root)}, {needed // 2**20} MiB "
                f"wanted ({DISK_FREE_FACTOR:g}x the engine's data directory: the rebuilt banks "
                "sit beside the originals until the swap, plus the archives)",
            )
        embedded = plan["totals"]["facts"] + plan["totals"]["observations"]
        plan["estimate_s"] = round(embedded / RESTORE_FACTS_PER_S)
        if target != configured:
            warnings.append(
                f"[memory.models] asks for {describe_embedder(*configured)}; after the switch "
                f"set embeddings_provider/embeddings_model to {describe_embedder(*target)} so "
                "config and store agree"
            )
        return plan

    # ── the run ─────────────────────────────────────────────────────────

    async def run(self) -> dict[str, Any]:
        """Preflight, then the whole switch. Raises :class:`SwitchRefused` or :class:`SwitchError`."""
        with self._lock():
            plan = await self.preflight(apply=True)
            if plan["refusals"]:
                raise SwitchRefused(plan["refusals"][0]["message"], plan)
            assert self._target is not None
            run_id = secrets.token_hex(4)
            previous = embedder_record.load_embedder_record()
            if previous is None:
                raise SwitchRefused("the embedder record could not be written down", plan)
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
                phase=PHASE_EXPORTING,
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
                banks=[
                    BankEntry(
                        bank=b["bank"],
                        temp=temp_bank_id(b["bank"], run_id),
                        counts=dict(b["counts"]),
                        links=b["links"],
                    )
                    for b in plan["banks"]
                ],
            )
            self._save(journal)
            try:
                await self._export_all(journal)
                await self._pause_and_settle(journal)
                await self._flip(journal)
                await self._restore_all(journal)
                await self._check_before_swap(journal)
            except BaseException as exc:
                cause = (
                    exc.message if isinstance(exc, SwitchError) else f"{type(exc).__name__}: {exc}"
                )
                await self._rollback(journal, cause)
                raise
            await self._swap_all(journal)
            return self._finish(journal)

    async def resume(self) -> dict[str, Any]:
        """Finish an interrupted run from its journal.

        A run that stopped inside the swap is carried forward; one that
        stopped anywhere earlier is rolled back (its originals are intact).
        """
        with self._lock():
            journal = load_journal()
            if journal is None:
                raise SwitchRefused("there is no interrupted embedder switch to resume")
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
                write_gate.engage_gate(run_id=journal.run_id)
            if journal.phase not in (PHASE_SWAPPING, PHASE_COMPLETE):
                await self._rollback(journal, journal.error or "the run was interrupted")
                return {
                    "status": "rolled_back",
                    "run_id": journal.run_id,
                    "archive_dir": journal.archive_dir,
                    "to": journal.previous,
                }
            if journal.phase == PHASE_SWAPPING:
                if not await self._healthy():
                    self._say("starting the engine on the target embedder")
                    await self._start_engine_on(target)
                await self._swap_all(journal)
            return self._finish(journal)

    async def _export_all(self, journal: Journal) -> None:
        for index, entry in enumerate(journal.banks):
            self._say(f"exporting {entry.bank} ({index + 1}/{len(journal.banks)})")
            await self._export_verified(journal, entry, index)

    async def _pause_and_settle(self, journal: Journal) -> None:
        self._say("pausing memory writes")
        write_gate.engage_gate(run_id=journal.run_id)
        journal.phase = PHASE_PAUSED
        self._save(journal)
        states = {e.bank: await self.bank_state(e.bank) for e in journal.banks}
        if not journal.allow_pending:
            deadline = self._clock() + self.t.settle_s
            while any(s["pending"] or s["processing"] for s in states.values()):
                if self._clock() >= deadline:
                    busy = ", ".join(
                        f"{bank} ({s['pending']} pending, {s['processing']} processing)"
                        for bank, s in states.items()
                        if s["pending"] or s["processing"]
                    )
                    raise SwitchError(
                        f"operations queued before the pause did not finish within "
                        f"{self.t.settle_s:g}s: {busy}"
                    )
                await self._sleep(self.t.poll_s)
                states = {e.bank: await self.bank_state(e.bank) for e in journal.banks}
        # A write that landed between a bank's export and the pause is not in
        # its archive. Export that bank again, now that nothing new can come
        # in through hal0.
        for index, entry in enumerate(journal.banks):
            if _diff(entry.counts, states[entry.bank]["counts"]):
                self._say(f"{entry.bank} changed after its export; exporting it again")
                await self._export_verified(journal, entry, index)

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

    async def _flip(self, journal: Journal) -> None:
        assert self._target is not None
        target = self._target
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

    async def _restore_all(self, journal: Journal) -> None:
        journal.phase = PHASE_RESTORING
        self._save(journal)
        for index, entry in enumerate(journal.banks):
            self._say(f"rebuilding {entry.bank} ({index + 1}/{len(journal.banks)})")
            restored = await self._restore(journal, entry, entry.temp)
            entry.restored_counts = restored
            differs = _diff(entry.counts, restored)
            if differs:
                self._save(journal)
                raise SwitchError(
                    f"bank {entry.bank} did not come back whole: {_diff_text(differs)}",
                    bank=entry.bank,
                    details={"differs": differs},
                )
            entry.step = STEP_RESTORED
            self._save(journal)

    async def _check_before_swap(self, journal: Journal) -> None:
        """The last look before anything is deleted: every original still matches its archive."""
        for entry in journal.banks:
            state = await self.bank_state(entry.bank)
            differs = _diff(entry.counts, state["counts"])
            busy = state["pending"] or state["processing"]
            if not differs and not busy:
                continue
            what = (
                _diff_text(differs)
                if differs
                else (f"{state['pending']} pending and {state['processing']} processing operations")
            )
            if journal.allow_pending:
                entry.note = f"changed after its export and not carried over: {what}"
                self._say(f"warning: {entry.bank} {entry.note}")
                continue
            raise SwitchError(
                f"bank {entry.bank} changed while writes were paused ({what}): a client is "
                "writing to the engine without going through hal0. Stop it and run the "
                "switch again",
                bank=entry.bank,
                details={"differs": differs},
            )
        self._save(journal)

    # ── swap ────────────────────────────────────────────────────────────

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

    async def _swap_one(self, journal: Journal, entry: BankEntry) -> None:
        """Put the rebuilt bank in the original's place. Safe to run again after any stop.

        What to do is decided from the journal step *and* from which of the
        two ids the engine has now, so a stop between any two lines below is
        picked up correctly.
        """
        bank, temp = entry.bank, entry.temp
        existing = set(await self._bank_ids())
        has_orig, has_temp = bank in existing, temp in existing

        if entry.step in (STEP_RESTORED, STEP_DELETING) and has_orig and not has_temp:
            raise SwitchError(
                f"the rebuilt bank {temp} is gone and {bank} still exists; nothing was "
                f"deleted. Its archive is {journal.archive_dir}/{entry.archive}",
                bank=bank,
            )
        if entry.step == STEP_DELETED and has_orig and has_temp:
            raise SwitchError(
                f"bank {bank} exists again after the switch deleted it: a client that "
                f"bypasses hal0 recreated it. Move what it holds or delete it "
                f"(`hal0 memory bank delete`), then run --resume; the rebuilt bank is {temp}",
                bank=bank,
            )

        if entry.step in (STEP_RESTORED, STEP_DELETING) and has_orig:
            # Never delete an original without a whole replacement in hand.
            differs = _diff(entry.counts, (await self.bank_state(temp))["counts"])
            if differs:
                raise SwitchError(
                    f"the rebuilt bank {temp} no longer matches {bank}: {_diff_text(differs)}; "
                    f"{bank} was not touched",
                    bank=bank,
                    details={"differs": differs},
                )
            await self._wait_quiet(temp)
            entry.step = STEP_DELETING
            self._save(journal)
            await self._delete_bank(bank)
            has_orig = False
        if entry.step in (STEP_RESTORED, STEP_DELETING):
            entry.step = STEP_DELETED
            self._save(journal)

        if entry.step == STEP_DELETED:
            if has_temp:
                await self._wait_quiet(temp)
                output = await asyncio.to_thread(self._rename_bank, temp, bank)
                if "could not be rebuilt" in output or " failed" in output:
                    entry.note = output[-400:]
            elif not has_orig:
                # Neither id: put the bank back straight from its archive.
                self._say(f"{bank} exists under neither id; restoring it from its archive")
                await self._restore(journal, entry, bank)
            # else: the rename already happened and only the journal was behind.
            entry.step = STEP_RENAMED
            self._save(journal)

    async def _swap_all(self, journal: Journal) -> None:
        # The journal of per-bank swaps goes to disk before any original is touched.
        journal.phase = PHASE_SWAPPING
        journal.error = None
        self._save(journal)
        for index, entry in enumerate(journal.banks):
            if entry.step == STEP_RENAMED:
                continue
            self._say(f"swapping {entry.bank} into place ({index + 1}/{len(journal.banks)})")
            try:
                await self._swap_one(journal, entry)
                differs = _diff(entry.counts, (await self.bank_state(entry.bank))["counts"])
            except SwitchError as exc:
                journal.error = exc.message
                self._save(journal)
                exc.resumable = True
                raise
            if differs:
                # Nothing left to undo or redo for this bank: say so and keep
                # going, so the other banks are not left half-swapped.
                entry.note = f"counts moved after the swap: {_diff_text(differs)}"
                self._save(journal)
                self._say(
                    f"warning: {entry.bank} is in place but its {entry.note}; its archive is "
                    f"{journal.archive_dir}/{entry.archive}"
                )

    def _finish(self, journal: Journal) -> dict[str, Any]:
        target = Embedder(journal.target["provider"], journal.target["model"])
        embedder_record.save_embedder_record(
            EmbedderRecord(
                provider=target.provider, model=target.model, source=SOURCE_SWITCH, mixed=False
            )
        )
        write_gate.lift_gate()
        journal.phase = PHASE_COMPLETE
        self._retire_journal(journal)
        self._say("memory writes resumed")
        return {
            "status": "switched",
            "run_id": journal.run_id,
            "from": {k: journal.previous.get(k) for k in ("provider", "model")},
            "to": journal.target,
            "banks": [
                {"bank": e.bank, "counts": e.counts, "links": e.links, "note": e.note}
                for e in journal.banks
            ],
            "warnings": [
                f"{e.bank}: {e.note}"
                for e in journal.banks
                if e.note and not e.note.startswith("restore operation ")
            ],
            "archive_dir": journal.archive_dir,
            "delete_archives": f"rm -r {journal.archive_dir}",
        }

    # ── rollback ────────────────────────────────────────────────────────

    async def _drop_temp_banks(self, journal: Journal) -> None:
        """Delete every temporary bank of this run, stopping a restore that still runs first."""
        for entry in journal.banks:
            match = re.fullmatch(r"restore operation (\S+)", entry.note or "")
            if not match or entry.step != STEP_EXPORTED:
                continue
            # A restore that may still be running would write into, or
            # create, its target after the delete below.
            path = f"{_bank_path(entry.bank)}/operations/{quote(match.group(1), safe='')}"
            with contextlib.suppress(SwitchError):
                await self._json("DELETE", path, what="cancelling the restore", bank=entry.bank)
            deadline = self._clock() + self.t.quiet_s
            while self._clock() < deadline:
                try:
                    op = await self._json("GET", path, what="reading the restore", bank=entry.bank)
                except SwitchError:
                    break
                if not isinstance(op, dict) or op.get("status") not in ("pending", "processing"):
                    break
                await self._sleep(self.t.poll_s)
        existing = set(await self._bank_ids())
        for entry in journal.banks:
            if entry.temp in existing:
                await self._delete_bank(entry.temp)

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
                await self._drop_temp_banks(journal)
            if journal.flip_started:
                embedder_record.save_embedder_record(self._record_from(journal.previous))
                await self._start_engine_on(previous)
                await self._drop_temp_banks(journal)
                # Anything written while the engine ran the target was embedded
                # with it, into banks that are staying on the old model.
                for entry in journal.banks:
                    if entry.step == STEP_PLANNED:
                        continue
                    if _diff(entry.counts, (await self.bank_state(entry.bank))["counts"]):
                        drifted.append(entry.bank)
                if drifted and not journal.previous.get("mixed"):
                    embedder_record.save_embedder_record(
                        self._record_from(journal.previous, mixed=True)
                    )
                    self._say(
                        "warning: " + ", ".join(drifted) + " changed while the engine ran the "
                        "target embedder; the store is now recorded as mixed"
                    )
            write_gate.lift_gate()
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


__all__ = [
    "COUNT_KEYS",
    "RESTORE_FACTS_PER_S",
    "BankEntry",
    "EmbedderSwitch",
    "Journal",
    "SwitchError",
    "SwitchRefused",
    "Timeouts",
    "admin_command",
    "admin_environment",
    "archive_root",
    "interrupted_switch",
    "journal_path",
    "load_journal",
    "lock_path",
    "run_admin_rename",
    "temp_bank_id",
]
