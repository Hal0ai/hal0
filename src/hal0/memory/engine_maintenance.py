"""Maintenance runs on the memory engine: isolation, waits, archives, journal (ADR-0058).

A maintenance command is one that rebuilds the memory store with nothing
else touching it: ``hal0 memory embedder switch`` today
(:mod:`hal0.memory.embedder_switch`), and the fold-in of the old per-agent banks
next. Each needs the same machinery, and this module holds it; the command
keeps its own plan, its own sequence and its own journal format.

What a command gets from here:

* **Names** (:class:`MaintenanceNames`). Every on-disk name and every
  operator-facing word that differs between commands: the journal, its
  mirror, the lock, the archive directory prefix, the isolation drop-in, the
  command line and how a message names the command. Every function below that
  touches one of those takes the command's names first. The names of a
  command are fixed for good once it has shipped: an interrupted run on an
  existing box must still be found and resumed by them.
* **Isolation of the engine.** The engine is restarted listening only on a
  private loopback port through one drop-in (``names.drop_in``) that clears
  ``ExecStart`` and sets it again to the unit's own command with ``--host
  127.0.0.1 --port <private>`` and the engine's auto-consolidation off.
  :func:`unit_refusals` is the preflight's look at the unit (an ``ExecStart``
  that cannot be rewritten word for word, a later-sorting drop-in that would
  win, a start/reload command that names the normal port);
  :func:`write_isolation_dropin`, :func:`remove_isolation_dropin` and
  :func:`isolated_port` write, remove and read it back;
  :func:`loopback_listener`, :func:`engine_invocation_id` and
  :func:`stop_engine_unit` say which process holds a port, which start of
  the unit runs now, and stop it.
* **The journal on disk.** :func:`journal_path`, :func:`locate_journal`
  (the primary copy, else a mirror in an archive directory),
  :func:`write_synced`, :func:`lock_held`. The format of a journal and the
  rules for reading it (its version, its phases) stay with the command, which
  hands :func:`locate_journal` its own parser.
* **One engine session** (:class:`EngineMaintenance`), which a command
  subclasses: the engine's HTTP API with paging and errors written for the
  operator, bank state and queue waits (``_quiet_state``, ``_wait_quiet``,
  ``_settle``, ``_wait_operation``; each waits longer for the engine's own
  refreshes and consolidations, :data:`ENGINE_OPERATION_TYPES`), the restart onto the private port or back
  with its identity checks (the listening socket belongs to the unit,
  ``/version`` is the engine the preflight saw, the embedder is the one asked
  for, nothing answers at the normal address), the write gate engaged
  (``_pause``), bank export into the run's archive directory and restore from
  it (``_export_bank``, ``_archive_counts``, ``_restore``, ``_await_restore``),
  and the journal saved, mirrored and retired under the run lock (``_save``,
  ``_retire_journal``, ``_lock``).

The write gate itself is :mod:`hal0.memory.write_gate`; this module only
engages it.
"""

from __future__ import annotations

import asyncio
import contextlib
import fcntl
import functools
import hashlib
import json
import os
import re
import subprocess
import time
import zipfile
from collections.abc import Awaitable, Callable, Iterator, Mapping
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Protocol
from urllib.parse import quote, urlsplit

import httpx
import structlog

from hal0.config.paths import var_lib
from hal0.install import perms
from hal0.memory import embedder_record, write_gate
from hal0.memory.embedder_record import (
    ENGINE_DEFAULT_EMBEDDER,
    ENGINE_DEFAULT_PROVIDER,
    Embedder,
    describe_embedder,
)
from hal0.security import nofollow

log = structlog.get_logger(__name__)

SERVICE = "hindsight-api.service"

#: The engine's data directory under its home (``hindsight_dir()``).
PG_DIR = ".pg0"

#: The counts a restored bank must match, in the order they are reported.
COUNT_KEYS: tuple[str, ...] = ("facts", "observations", "mental_models", "directives", "documents")

#: What a bank's fingerprint is made of, and where each signal comes from.
#:
#: The engine is isolated for the whole run (see the module docstring), so the
#: fingerprint is an assertion, not a guard: it is kept to the signals that
#: cannot move by themselves. Everything the engine changes on its own
#: (consolidation and the timestamps, link tallies and operations it leaves
#: behind, a mental model's refreshed content) is left out, because a signal
#: that moves by itself turns a healthy run into one that can never finish.
FINGERPRINT_SIGNALS: dict[str, str] = {
    "directives": "a digest of every directive, inactive ones included",
    "config": "a digest of the bank's configuration overrides",
    "mental_models": "a digest of every mental model's definition (id, name, query, tags, "
    "trigger, token budget), not its generated content",
    "document": "the bank list's last_document_at: when a document was last ingested",
    "knowledge": "a digest of the knowledge-base tree: every folder and page with its id, "
    "kind, name, parent and backing mental model",
}

#: What a knowledge-base node is compared by: its place in the tree, not its
#: generated content or its timestamps.
_KNOWLEDGE_NODE_KEYS = ("id", "kind", "name", "parent_id", "mental_model_id")

#: Counts only a writer can move. ``observations`` is not among them: the
#: engine's consolidation adds and merges observations by itself, and an
#: export leaves out an observation whose source facts are gone.
HARD_COUNTS: tuple[str, ...] = ("facts", "mental_models", "directives", "documents")

_MODEL_DEFINITION_KEYS = ("id", "name", "source_query", "tags", "trigger", "max_tokens")

#: Measured on hindsight-api 1.2.0, CPU embedder, 2026-11-10 (ADR-0058 "Open
#: questions"): a bank restore re-embeds roughly this many facts per second.
RESTORE_FACTS_PER_S = 60.0

_OP_LIVE = ("pending", "processing")
_OP_FAILED = ("failed", "cancelled", "not_found")

#: The operation types the engine queues by itself, as hindsight-api 1.2.0
#: stores them (``async_operations.operation_type``, listed as ``task_type``
#: and ``operation_type``): a mental-model refresh after a restore, after
#: consolidation, or when its start-up recovery requeues a ``processing`` row,
#: and consolidation of new facts. Nothing a client sends while the engine is
#: isolated, and nothing a run can hold back, so a run waits for them to end
#: (bounded by :attr:`Timeouts.engine_ops_s`) instead of reading them as a
#: change to the bank (#3604).
ENGINE_OPERATION_TYPES: tuple[str, ...] = ("refresh_mental_model", "consolidation")
_PAGE = 100


def _op_ref(bank: str, op: Mapping[str, Any]) -> dict[str, Any]:
    return {
        "bank": bank,
        "type": operation_type(op),
        "id": op.get("operation_id") or op.get("id"),
        "status": op.get("status"),
    }


@dataclass(frozen=True)
class MaintenanceNames:
    """What one maintenance command is called, on disk and in what it says.

    The on-disk names are fixed once a command ships: a run interrupted on a
    box is found again, and resumed, by exactly these names.
    """

    #: The command line an operator runs, e.g. ``hal0 memory embedder switch``.
    command: str
    #: How a message names the command in a sentence, e.g. ``the switch``.
    actor: str
    #: The journal's file name under ``<state>/``.
    journal: str
    #: The run lock's file name under ``<state>/``.
    lock: str
    #: Each run's archive directory under ``<backups>/`` is
    #: ``<archive_prefix><UTC stamp>-<run id>``.
    archive_prefix: str
    #: The isolation drop-in's file name in the engine unit's ``.d`` directory.
    drop_in: str
    #: The journal mirror's file name inside the run's archive directory.
    mirror: str
    #: The write-gate reason the run engages (:mod:`hal0.memory.write_gate`).
    gate_reason: str
    #: The structlog event progress lines are logged under.
    log_event: str

    @property
    def actor_title(self) -> str:
        """:attr:`actor` at the start of a sentence."""
        return self.actor[:1].upper() + self.actor[1:]


class MaintenanceError(Exception):
    """A step of a maintenance run failed. The message is written for the operator.

    ``resumable`` is true when the run stopped where it must be carried on,
    not undone: writes stay paused and ``--resume`` carries on.
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
    #: at :data:`RESTORE_FACTS_PER_S`, whichever is more. A restore found
    #: still running on a resume is waited for twice that, never cancelled.
    restore_floor_s: float = 900.0
    restore_factor: float = 6.0
    #: A bank's queue going quiet before it is counted or deleted.
    quiet_s: float = 120.0
    #: The engine's own work on a bank (:data:`ENGINE_OPERATION_TYPES`)
    #: ending, when it is all that keeps the bank busy. A mental-model
    #: refresh is an LLM call over the bank, minutes on a slow local model.
    engine_ops_s: float = 900.0
    delete_s: float = 3600.0
    http_s: float = 120.0


class MaintenanceBank(Protocol):
    """One bank's line in a command's journal, as far as this module reads and writes it."""

    bank: str
    archive: str | None
    counts: dict[str, int]
    #: ``{"bank", "id", "target", "engine_pid", "engine_invocation"}`` of a
    #: restore that may still be running (see :meth:`EngineMaintenance._restore`).
    restore_op: dict[str, Any] | None


class MaintenanceJournal(Protocol):
    """A command's journal, as far as this module reads and writes it."""

    run_id: str
    started_at: str
    archive_dir: str
    allow_pending: bool
    private_port: int | None
    engine_stopped: bool
    updated_at: str | None

    @property
    def banks(self) -> list[Any]: ...

    def to_dict(self) -> dict[str, Any]: ...

    def own_ids(self) -> set[str]: ...


# ── files on disk ────────────────────────────────────────────────────────


def journal_path(names: MaintenanceNames) -> Path:
    """``/var/lib/hal0/state/<names.journal>`` (sandboxed under ``HAL0_HOME``)."""
    return var_lib() / "state" / names.journal


def lock_path(names: MaintenanceNames) -> Path:
    return var_lib() / "state" / names.lock


def archive_root() -> Path:
    """``/var/lib/hal0/backups``: where hal0 keeps restore points, one directory per run here."""
    return var_lib() / "backups"


def read_journal_file[J](path: Path, parse: Callable[[str], J]) -> tuple[J | None, str | None]:
    """``(journal, None)``, ``(None, None)`` when absent, ``(None, why)`` when unusable.

    ``parse`` is the command's own reader; it raises :class:`ValueError` or
    :class:`TypeError` for a journal it will not carry on.
    """
    try:
        raw = path.read_text(encoding="utf-8")
    except FileNotFoundError:
        return None, None
    except (OSError, UnicodeDecodeError) as exc:
        return None, f"{path} cannot be read ({type(exc).__name__})"
    try:
        return parse(raw), None
    except (ValueError, TypeError) as exc:
        return None, f"{path} is not a usable journal ({exc})"


def unfinished_mirrors[J: MaintenanceJournal](
    names: MaintenanceNames, parse: Callable[[str], J], finished: Callable[[J], bool]
) -> list[Path]:
    """Mirrors of runs that neither finished nor were rolled back, newest first."""
    found: list[tuple[str, Path]] = []
    try:
        candidates = sorted(archive_root().glob(f"{names.archive_prefix}*/{names.mirror}"))
    except OSError:
        return []
    for path in candidates:
        journal, _ = read_journal_file(path, parse)
        if journal is not None and not finished(journal):
            found.append((journal.started_at, path))
    return [path for _, path in sorted(found, reverse=True)]


def locate_journal[J: MaintenanceJournal](
    names: MaintenanceNames, parse: Callable[[str], J], finished: Callable[[J], bool]
) -> dict[str, Any]:
    """Find the journal of an unfinished run wherever a copy survives.

    The primary copy first. When it is missing or unusable, the mirror in the
    archive directory the write gate names, then any unfinished mirror under
    ``backups/``. Returns ``{"journal", "source", "error"}``: ``error`` is set
    when a copy exists but none can be used, and when the primary was unusable
    and a mirror stood in for it. Never raises.
    """
    primary, problem = read_journal_file(journal_path(names), parse)
    if primary is not None:
        if finished(primary):
            return {"journal": None, "source": None, "error": None}
        return {"journal": primary, "source": str(journal_path(names)), "error": None}
    mirrors: list[Path] = []
    gate = write_gate.read_gate() or {}
    if isinstance(gate.get("archive_dir"), str) and gate["archive_dir"]:
        mirrors.append(Path(gate["archive_dir"]) / names.mirror)
    mirrors.extend(p for p in unfinished_mirrors(names, parse, finished) if p not in mirrors)
    for path in mirrors:
        journal, why = read_journal_file(path, parse)
        if journal is not None and not finished(journal):
            return {"journal": journal, "source": str(path), "error": problem}
        problem = problem or why
    return {"journal": None, "source": None, "error": problem}


def lock_held(names: MaintenanceNames) -> bool:
    """True when a process holds the command's run lock. Creates nothing."""
    try:
        fd = os.open(lock_path(names), os.O_RDONLY | os.O_NOFOLLOW | os.O_CLOEXEC)
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


# ── helpers ──────────────────────────────────────────────────────────────


def bank_path(bank: str) -> str:
    # Colons are legal in a path segment and bank ids carry them
    # (``Hal0::Agents``); everything else that could change the path is escaped.
    return f"/v1/default/banks/{quote(bank, safe=':')}"


def du_bytes(path: Path) -> int:
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


def device(path: Path) -> int:
    """The filesystem ``path`` is on."""
    return os.stat(path).st_dev


def nearest_existing(path: Path) -> Path:
    for candidate in (path, *path.parents):
        if candidate.exists():
            return candidate
    return Path("/")


def _knowledge_nodes(tree: Mapping[str, Any]) -> list[dict[str, Any]]:
    """Every folder and page in a ``knowledge-base/tree`` answer, flattened."""
    out: list[dict[str, Any]] = []
    stack = list(tree.get("roots") or tree.get("nodes") or [])
    while stack:
        node = stack.pop()
        if not isinstance(node, dict):
            continue
        out.append({k: node.get(k) for k in _KNOWLEDGE_NODE_KEYS})
        stack.extend(node.get("children") or [])
    return out


def diff(
    expected: Mapping[str, int], got: Mapping[str, int], *, observation_floor: bool = False
) -> dict[str, dict[str, int]]:
    """The counts that differ and matter (:data:`HARD_COUNTS`).

    With ``observation_floor``, fewer observations than expected also counts:
    a rebuilt bank may gain observations from the engine's own consolidation,
    but must not have lost any its archive carried.
    """
    keys = list(HARD_COUNTS)
    if observation_floor and int(got.get("observations", 0)) < int(expected.get("observations", 0)):
        keys.append("observations")
    return {
        key: {"expected": int(expected.get(key, 0)), "got": int(got.get(key, 0))}
        for key in COUNT_KEYS
        if key in keys and int(expected.get(key, 0)) != int(got.get(key, 0))
    }


def diff_text(diff: Mapping[str, Mapping[str, int]]) -> str:
    return ", ".join(
        f"{key.replace('_', ' ')} {d['got']} (expected {d['expected']})" for key, d in diff.items()
    )


def signals_moved(
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


def digest(value: Any) -> str:
    """A short digest of ``value``; a list is digested as a set of its rows."""
    if isinstance(value, list):
        value = sorted(json.dumps(_strip(v), sort_keys=True, default=str) for v in value)
    text = json.dumps(_strip(value), sort_keys=True, default=str)
    return hashlib.sha256(text.encode("utf-8")).hexdigest()[:16]


def operation_type(op: Mapping[str, Any]) -> str:
    """An operation row's type, under either name the engine lists it by."""
    return str(op.get("operation_type") or op.get("task_type") or "unknown")


def operations_text(bank: str, ops: list[Mapping[str, Any]]) -> str:
    """Each operation by type, bank, id and status, for a message."""
    return ", ".join(
        f"{operation_type(op)} on {bank} (operation {op.get('operation_id') or op.get('id')}, "
        f"{op.get('status')})"
        for op in ops
    )


def evidence_pair(raw: Mapping[str, Any] | None) -> Embedder | None:
    """A raw evidence layer as a pair; an unset key is the engine default."""
    if raw is None:
        return None
    return Embedder(
        raw.get("provider") or ENGINE_DEFAULT_PROVIDER,
        raw.get("model") or ENGINE_DEFAULT_EMBEDDER,
    )


def write_synced(path: Path, text: str, *, mode: int) -> None:
    """Replace ``path`` atomically and durably: temp file, fsync, rename, fsync the directory."""
    nofollow.write_file(path, text, mode=mode, owner=perms.state_root_owner())
    fd = nofollow.open_dir(path.parent)
    try:
        os.fsync(fd)
    finally:
        os.close(fd)


def isolation_drop_in_path(names: MaintenanceNames) -> Path:
    from hal0.memory import extraction_env

    return extraction_env.DROP_IN_DIR / names.drop_in


def _unit_assignments(unit_text: str) -> list[tuple[str, str, str]]:
    """``(file, key, value)`` for every assignment in ``systemctl cat`` output, in order.

    ``file`` is the path in the ``# /…`` header that precedes it. A line
    ending in a backslash continues on the next, as in systemd; comments and
    section headers are skipped.
    """
    out: list[tuple[str, str, str]] = []
    source = ""
    carried = ""
    for raw in unit_text.splitlines():
        if raw.startswith("# /"):
            source = raw[2:].strip()
            carried = ""
            continue
        line = carried + raw.strip()
        if line.endswith("\\"):
            carried = line[:-1].rstrip() + " "
            continue
        carried = ""
        if not line or line.startswith(("#", ";", "[")) or "=" not in line:
            continue
        key, _, value = line.partition("=")
        out.append((source, key.strip(), value.strip()))
    return out


def _is_ours(names: MaintenanceNames, source: str) -> bool:
    return Path(source).name == names.drop_in


def exec_start_source(names: MaintenanceNames, unit_text: str) -> tuple[str | None, str | None]:
    """``(command, file)``: the ``ExecStart`` the unit runs without this command's drop-in.

    The last ``ExecStart=`` wins and an empty one clears what came before, as
    in systemd, so an operator's own drop-in that replaces ``ExecStart`` (to
    bind the LAN, say) is what comes back, with the file that set it.
    """
    current: tuple[str | None, str | None] = (None, None)
    for source, key, value in _unit_assignments(unit_text):
        if key == "ExecStart" and not _is_ours(names, source):
            current = (value or None, source if value else None)
    return current


def effective_exec_start(names: MaintenanceNames, unit_text: str) -> str | None:
    """The command the engine's unit starts, from ``systemctl cat`` output (:func:`exec_start_source`)."""
    return exec_start_source(names, unit_text)[0]


def later_exec_start_dropins(names: MaintenanceNames, unit_text: str) -> list[tuple[str, str]]:
    """``(file, ExecStart)`` for each drop-in that would win over ``names.drop_in``.

    systemd applies drop-ins in the order of their file names, whichever
    directory they are in, so one whose name sorts after this command's and
    that sets ``ExecStart`` replaces the private command: the engine would
    come back at its normal address.
    """
    found: dict[str, str] = {}
    for source, key, value in _unit_assignments(unit_text):
        name = Path(source).name
        if key == "ExecStart" and source.endswith(".conf") and name > names.drop_in:
            found[source] = value or found.get(source, "")
    return sorted(found.items(), key=lambda item: Path(item[0]).name)


class UnsupportedExecStart(ValueError):
    """An ``ExecStart`` this command will not rewrite; the message says why and what to do."""


#: Programs that run their arguments as a script: the address inside cannot be
#: rewritten reliably, so a command started through one is refused.
_SHELLS = frozenset({"sh", "bash", "dash", "zsh", "ash", "ksh", "busybox", "fish"})
#: systemd's ``ExecStart`` prefixes. ``-`` (a failure exit is ignored) and ``:``
#: (no environment variable expansion) leave the command line itself alone and
#: are kept; the others change how systemd runs it or which word is the
#: program, and are refused.
_KEPT_PREFIXES = frozenset("-:")
_ALL_PREFIXES = frozenset("-@+!:|")


@dataclass(frozen=True)
class _Word:
    raw: str
    value: str


def _split_exec(line: str) -> list[_Word]:
    """Split ``line`` into words the way systemd does, for the subset this command accepts.

    Words are separated by whitespace; a word may be quoted with ``"`` or
    ``'`` (also in part, ``--host="a"``), and the quotes are removed from its
    value. systemd also reads C-style backslash escapes, which ``shlex`` does
    not follow, so a backslash anywhere is refused rather than guessed at.
    Each word keeps its original text, so a word this command does not
    change is written back exactly as it was.
    """
    if "\\" in line:
        raise UnsupportedExecStart("it contains a backslash escape")
    words: list[_Word] = []
    i, n = 0, len(line)
    while i < n:
        while i < n and line[i] in " \t":
            i += 1
        if i >= n:
            break
        start, value = i, []
        while i < n and line[i] not in " \t":
            if line[i] in "\"'":
                quote, i = line[i], i + 1
                end = line.find(quote, i)
                if end < 0:
                    raise UnsupportedExecStart("it has an unterminated quote")
                value.append(line[i:end])
                i = end + 1
            else:
                value.append(line[i])
                i += 1
        words.append(_Word(line[start:i], "".join(value)))
    return words


def private_exec_start(line: str, port: int) -> str:
    """``line`` with its listen address replaced by ``127.0.0.1:<port>``.

    Everything else in the operator's command is kept word for word. An
    address the command does not name (the engine then reads it from its
    environment or uses its default) is appended, and an argument beats the
    environment. Raises :class:`UnsupportedExecStart`, before anything is
    written, for a command this cannot rewrite safely: one started through a
    shell, one with a prefix other than ``-`` or ``:``, one whose host or port
    argument uses a ``%`` specifier or ``$`` expansion, and one systemd quotes
    or escapes in ways this does not follow.
    """
    words = _split_exec(line)
    if not words:
        raise UnsupportedExecStart("it is empty")
    first = words[0]
    prefix_len = 0
    while prefix_len < len(first.value) and first.value[prefix_len] in _ALL_PREFIXES:
        prefix_len += 1
    prefix = first.value[:prefix_len]
    if set(prefix) - _KEPT_PREFIXES:
        raise UnsupportedExecStart(
            f"it starts with the prefix {prefix!r}, which changes how systemd runs it "
            "(only '-' and ':' are kept)"
        )
    program = first.value[prefix_len:]
    if not program:
        raise UnsupportedExecStart("it names no program")
    if Path(program).name in _SHELLS:
        raise UnsupportedExecStart(
            f"it runs the engine through a shell ({program}), so the address is inside a script"
        )
    out = [w.raw for w in words]
    seen = {"--host": False, "--port": False}
    wanted = {"--host": "127.0.0.1", "--port": str(port)}
    i = 1
    while i < len(words):
        value = words[i].value
        for flag in seen:
            if value == flag:
                if i + 1 >= len(words):
                    raise UnsupportedExecStart(f"{flag} is the last word and has no value")
                arg = words[i + 1].value
                _check_address_value(flag, arg)
                out[i + 1] = wanted[flag]
                seen[flag] = True
                i += 1
                break
            if value.startswith(flag + "="):
                _check_address_value(flag, value[len(flag) + 1 :])
                out[i] = f"{flag}={wanted[flag]}"
                seen[flag] = True
                break
        i += 1
    for flag in ("--host", "--port"):
        if not seen[flag]:
            out += [flag, wanted[flag]]
    return " ".join(out)


def _check_address_value(flag: str, value: str) -> None:
    if "%" in value or "$" in value:
        raise UnsupportedExecStart(
            f"its {flag} value {value!r} is expanded by systemd (a % specifier or $ variable), "
            "so the address it binds cannot be read from the unit"
        )


def exec_start_refusal(names: MaintenanceNames, line: str, source: str | None) -> str | None:
    """Why the command will not rewrite ``line``, with the way on, or ``None`` when it can."""
    try:
        private_exec_start(line, 1)
    except UnsupportedExecStart as exc:
        where = f" (set in {source})" if source else ""
        return (
            f"the engine's unit starts `{line}`{where}, which {names.actor} cannot move to a "
            f"private port: {exc}. Make ExecStart a direct command line that names the "
            "engine's binary, with --host and --port as plain values (an ExecStart= drop-in "
            "that sorts before " + names.drop_in + " is fine), run "
            f"`systemctl daemon-reload`, then run {names.actor} again"
        )
    return None


#: Unit keys whose command runs around the engine's start or reload.
_SIDE_COMMANDS = ("ExecStartPre", "ExecStartPost", "ExecCondition", "ExecReload")


def side_commands_on_port(
    names: MaintenanceNames, unit_text: str, port: int
) -> list[tuple[str, str, str]]:
    """``(file, key, command)`` for start/reload commands that name the engine's normal port.

    While the engine is private such a command (a health probe, say) cannot
    reach it, so its start would fail every time and the run would time out
    and roll back. An empty assignment clears the list before it, as in
    systemd; this command's own drop-in sets none.
    """
    mention = re.compile(rf"(?<![0-9]){port}(?![0-9])")
    active: dict[str, list[tuple[str, str]]] = {k: [] for k in _SIDE_COMMANDS}
    for source, key, value in _unit_assignments(unit_text):
        if key not in active or _is_ours(names, source):
            continue
        if not value:
            active[key] = []
        else:
            active[key].append((source, value))
    return [
        (source, key, value)
        for key in _SIDE_COMMANDS
        for source, value in active[key]
        if mention.search(value)
    ]


def unit_refusals(
    names: MaintenanceNames, unit_text: str | None, normal_port: int | None
) -> list[dict[str, str]]:
    """The preflight's look at the engine's unit: every reason it cannot be isolated safely."""
    if unit_text is None:
        return [
            {
                "code": "unit_unreadable",
                "message": f"`systemctl cat {SERVICE}` could not be read, so {names.actor} cannot "
                "tell which command to move to a private port",
            }
        ]
    refusals: list[dict[str, str]] = []
    line, source = exec_start_source(names, unit_text)
    if line is None:
        refusals.append(
            {
                "code": "unit_unreadable",
                "message": f"`systemctl cat {SERVICE}` names no ExecStart",
            }
        )
    else:
        why = exec_start_refusal(names, line, source)
        if why:
            refusals.append({"code": "exec_start_unsupported", "message": why})
    for path, command in later_exec_start_dropins(names, unit_text):
        refusals.append(
            {
                "code": "exec_start_overridden",
                "message": f"{path} sorts after {names.drop_in} and sets ExecStart "
                f"(`{command or '(cleared)'}`), so it would win over {names.actor}'s private "
                "command and the engine would stay reachable at its normal address. Rename it "
                f"so it sorts before {names.drop_in} (any name not starting with "
                f"'zzzz-' does), run `systemctl daemon-reload`, then run {names.actor} again",
            }
        )
    if normal_port is not None:
        for path, key, command in side_commands_on_port(names, unit_text, normal_port):
            refusals.append(
                {
                    "code": "exec_probe_normal_port",
                    "message": f"{path} sets {key}=`{command}`, which names the engine's "
                    f"normal port {normal_port}. While {names.actor} runs, the engine listens only "
                    "on a private port, so this command would fail at every restart. Remove it "
                    "(or move it to a separate unit), run `systemctl daemon-reload`, then run "
                    f"{names.actor} again",
                }
            )
    return refusals


def read_unit_text(
    runner: Callable[..., subprocess.CompletedProcess[str]] = subprocess.run,
) -> str | None:
    """``systemctl cat`` of the engine's unit, or ``None`` when it cannot be read."""
    try:
        proc = runner(
            ["systemctl", "cat", SERVICE], capture_output=True, text=True, timeout=10, check=False
        )
    except (OSError, subprocess.SubprocessError):
        return None
    return proc.stdout or "" if proc.returncode == 0 else None


_HOST_ARG = re.compile(r"(--host)(=|\s+)(\S+)")
_PORT_ARG = re.compile(r"(--port)(=|\s+)(\S+)")


def isolation_body(names: MaintenanceNames, exec_start: str, run_id: str) -> str:
    """The isolation drop-in: the unit's own command on a private port, consolidation off."""
    return (
        f"# Written by `{names.command}` (run {run_id}). While this file exists\n"
        "# the memory engine listens only on a private loopback port, so that nothing but\n"
        f"# {names.actor} can reach it, and does not consolidate by itself. {names.actor_title} removes\n"
        "# this file when the run ends. Do not edit or delete it by hand: run\n"
        f"# `{names.command} --status`.\n"
        "[Service]\n"
        "ExecStart=\n"
        f"ExecStart={exec_start}\n"
        "Environment=HINDSIGHT_API_ENABLE_AUTO_CONSOLIDATION=false\n"
    )


def write_isolation_dropin(
    names: MaintenanceNames,
    port: int,
    run_id: str,
    *,
    runner: Callable[..., subprocess.CompletedProcess[str]] = subprocess.run,
    path: Path | None = None,
) -> dict[str, Any]:
    """Write the isolation drop-in for ``port``. Root only; never raises.

    Reads the unit as systemd sees it (``systemctl cat``), so the command it
    rewrites is the one in force, an operator's ``ExecStart`` drop-in
    included. Writes nothing for a unit the preflight would refuse
    (:func:`unit_refusals`). Returns ``{"error", "exec_start"}``; the caller
    reloads and restarts.
    """
    text = read_unit_text(runner)
    if text is None:
        return {"error": f"could not read the engine's unit (`systemctl cat {SERVICE}`)"}
    line, source = exec_start_source(names, text)
    if not line:
        return {"error": f"could not find an ExecStart in `systemctl cat {SERVICE}`"}
    later = later_exec_start_dropins(names, text)
    if later:
        return {
            "error": f"{later[0][0]} sorts after {names.drop_in} and sets ExecStart "
            f"(`{later[0][1] or '(cleared)'}`), so the engine cannot be isolated"
        }
    try:
        private = private_exec_start(line, port)
    except UnsupportedExecStart:
        return {"error": exec_start_refusal(names, line, source)}
    target = path if path is not None else isolation_drop_in_path(names)
    try:
        target.parent.mkdir(parents=True, exist_ok=True)
        tmp = target.with_suffix(".conf.tmp")
        tmp.write_text(isolation_body(names, private, run_id), encoding="utf-8")
        os.chmod(tmp, 0o644)
        tmp.replace(target)
    except OSError as exc:
        return {"error": f"could not write {target}: {exc}"}
    return {"error": None, "exec_start": line}


def remove_isolation_dropin(names: MaintenanceNames, path: Path | None = None) -> dict[str, Any]:
    """Remove the isolation drop-in. Never raises; the caller reloads and restarts."""
    target = path if path is not None else isolation_drop_in_path(names)
    try:
        target.unlink(missing_ok=True)
    except OSError as exc:
        return {"error": f"could not remove {target}: {exc}"}
    return {"error": None}


def isolated_port(names: MaintenanceNames, path: Path | None = None) -> int | None:
    """The private port the isolation drop-in pins the engine to; ``None`` when there is none.

    ``0`` for a drop-in that is there and names no port this can read: the
    engine is then to be treated as isolated, on a port unknown.
    """
    target = path if path is not None else isolation_drop_in_path(names)
    try:
        text = target.read_text(encoding="utf-8")
    except FileNotFoundError:
        return None
    except (OSError, UnicodeDecodeError):
        return 0
    found = 0
    for line in text.splitlines():
        match = _PORT_ARG.search(line) if line.startswith("ExecStart=") else None
        if match and match.group(3).isdigit():
            found = int(match.group(3))
    return found


def free_loopback_port() -> int:
    """A loopback port nothing listens on now, picked by the kernel."""
    import socket

    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.bind(("127.0.0.1", 0))
        return int(sock.getsockname()[1])


_PROC = Path("/proc")
_CGROUP_ROOT = Path("/sys/fs/cgroup")
#: ``/proc/net/tcp`` state of a listening socket.
_TCP_LISTEN = "0A"


def _listening_inodes(port: int, proc: Path = _PROC) -> set[str]:
    """Socket inodes listening on TCP ``port`` (any local address), from ``/proc/net/tcp{,6}``."""
    found: set[str] = set()
    for name in ("tcp", "tcp6"):
        try:
            lines = (proc / "net" / name).read_text(encoding="ascii").splitlines()[1:]
        except OSError:
            continue
        for row in lines:
            fields = row.split()
            if len(fields) < 10 or fields[3] != _TCP_LISTEN:
                continue
            try:
                local_port = int(fields[1].rsplit(":", 1)[1], 16)
            except (IndexError, ValueError):
                continue
            if local_port == port and fields[9] != "0":
                found.add(fields[9])
    return found


def _pid_sockets(pid: int, proc: Path = _PROC) -> set[str]:
    """Socket inodes ``pid`` holds open. Empty when its fds cannot be read."""
    out: set[str] = set()
    try:
        fds = list((proc / str(pid) / "fd").iterdir())
    except OSError:
        return out
    for fd in fds:
        try:
            target = os.readlink(fd)
        except OSError:
            continue
        if target.startswith("socket:[") and target.endswith("]"):
            out.add(target[8:-1])
    return out


def _comm(pid: int, proc: Path = _PROC) -> str:
    try:
        return (proc / str(pid) / "comm").read_text(encoding="utf-8").strip()
    except OSError:
        return "?"


def _unit_pids(
    runner: Callable[..., subprocess.CompletedProcess[str]] = subprocess.run,
    cgroup_root: Path = _CGROUP_ROOT,
) -> set[int]:
    """The engine unit's processes: its ``MainPID`` and everything in its control group."""
    try:
        proc = runner(
            ["systemctl", "show", "-p", "MainPID", "-p", "ControlGroup", SERVICE],
            capture_output=True,
            text=True,
            timeout=10,
            check=False,
        )
    except (OSError, subprocess.SubprocessError):
        return set()
    props = dict(line.split("=", 1) for line in (proc.stdout or "").splitlines() if "=" in line)
    pids: set[int] = set()
    main = props.get("MainPID", "0").strip()
    if main.isdigit() and int(main) > 0:
        pids.add(int(main))
    group = props.get("ControlGroup", "").strip()
    if group.startswith("/"):
        try:
            text = (cgroup_root / group.lstrip("/") / "cgroup.procs").read_text(encoding="ascii")
        except OSError:
            text = ""
        pids.update(int(p) for p in text.split() if p.isdigit())
    return pids


def loopback_listener(
    port: int,
    *,
    unit_pids: Callable[[], set[int]] | None = None,
    proc: Path = _PROC,
) -> dict[str, Any]:
    """Who listens on TCP ``port``: ``{"owner", "holder", "pid"}``.

    ``owner`` is ``"none"`` (nothing listens), ``"unit"`` (a process of the
    engine's unit owns the listening socket), ``"other"`` (another process
    does; ``holder`` names it) or ``"unknown"`` (a socket listens but no
    process could be matched to it, typically for want of root). Read from
    ``/proc/net/tcp`` and the socket inodes in ``/proc/<pid>/fd``; no new
    dependency, and nothing is written.
    """
    inodes = _listening_inodes(port, proc)
    if not inodes:
        return {"owner": "none", "holder": None, "pid": None}
    for pid in sorted((unit_pids or _unit_pids)()):
        if _pid_sockets(pid, proc) & inodes:
            return {"owner": "unit", "holder": f"pid {pid} ({_comm(pid, proc)})", "pid": pid}
    try:
        candidates = sorted(int(p.name) for p in proc.iterdir() if p.name.isdigit())
    except OSError:
        candidates = []
    for pid in candidates:
        if _pid_sockets(pid, proc) & inodes:
            return {"owner": "other", "holder": f"pid {pid} ({_comm(pid, proc)})", "pid": pid}
    return {
        "owner": "unknown",
        "holder": "a process this account cannot see (run as root)",
        "pid": None,
    }


def stop_engine_unit() -> dict[str, Any]:
    """``systemctl stop`` the engine's unit. ``{"error": ...}`` when it could not be stopped."""
    try:
        proc = subprocess.run(
            ["systemctl", "stop", SERVICE],
            capture_output=True,
            text=True,
            timeout=60,
            check=False,
        )
    except (OSError, subprocess.SubprocessError) as exc:
        return {"error": f"{type(exc).__name__}: {exc}"}
    if proc.returncode != 0:
        detail = " ".join((proc.stderr or proc.stdout or "").split())[:160]
        return {"error": detail or f"systemctl exited {proc.returncode}"}
    return {}


def engine_invocation_id() -> str | None:
    """systemd's ``InvocationID`` of the engine's unit: new on every start, ``None`` when unknown."""
    try:
        proc = subprocess.run(
            ["systemctl", "show", "-p", "InvocationID", "--value", SERVICE],
            capture_output=True,
            text=True,
            timeout=10,
            check=False,
        )
    except (OSError, subprocess.SubprocessError):
        return None
    value = (proc.stdout or "").strip()
    return value if proc.returncode == 0 and value else None


def http_detail(text: str, limit: int = 120) -> str:
    """A short, single-line, markup-safe summary of an HTTP error body.

    A foreign server's HTML page is never echoed: it is named, not shown.
    """
    stripped = (text or "").lstrip()
    if stripped[:1] == "<":
        return "(a non-JSON page, not shown)"
    flat = "".join(ch if ch.isprintable() else " " for ch in stripped)
    flat = " ".join(flat.split())
    for ch in "<>[]":
        flat = flat.replace(ch, "")
    if len(flat) > limit:
        flat = flat[: limit - 1].rstrip() + "…"
    return flat


class EngineMaintenance:
    """One maintenance run's session with the engine. A command subclasses it.

    ``client`` is a :class:`hal0.memory.hindsight_client.HindsightRestClient`
    pointed at the engine itself, not at hal0-api: the run's own imports and
    deletes must not meet the pause it sets. ``names`` is the command's
    :class:`MaintenanceNames`.

    Everything that touches the host is injectable, and defaults to the real
    thing: ``apply_dropin(embedder)`` renders the engine's managed drop-in for
    that pair and restarts the engine (returning the ``apply_showcase_dropin``
    dict), ``evidence()`` is :func:`embedder_record.engine_evidence`,
    ``isolate(port, run_id)`` and ``release()`` write and remove the isolation
    drop-in (:func:`write_isolation_dropin`, :func:`remove_isolation_dropin`;
    the restart that follows either is ``apply_dropin``'s), ``stop_unit()``
    stops the engine's unit, ``isolated_port()`` reads the drop-in back,
    ``client_for(base_url)`` builds the client for the private port,
    ``listener(port)`` says which process listens on a loopback port
    (:func:`loopback_listener`), ``unit_text()`` is ``systemctl cat`` of the
    engine's unit (:func:`read_unit_text`), ``unit_invocation()`` is its
    systemd ``InvocationID`` (:func:`engine_invocation_id`), and ``hook(point)``
    is called at the command's step boundaries for tests to stop a run there.

    A subclass sets :attr:`_error_type` to its own :class:`MaintenanceError`
    subclass, which every failure here is raised as, and overrides
    :meth:`_refusal` for the error a held lock raises.
    """

    #: What every failure in this class is raised as.
    _error_type: type[MaintenanceError] = MaintenanceError

    def __init__(
        self,
        client: Any,
        *,
        names: MaintenanceNames,
        apply_dropin: Callable[[Embedder], dict[str, Any]],
        evidence: Callable[[], Mapping[str, Any]] | None = None,
        isolate: Callable[[int, str], dict[str, Any]] | None = None,
        release: Callable[[], dict[str, Any]] | None = None,
        stop_unit: Callable[[], dict[str, Any]] | None = None,
        isolated_port: Callable[[], int | None] | None = None,
        client_for: Callable[[str], Any] | None = None,
        listener: Callable[[int], Mapping[str, Any]] | None = None,
        unit_text: Callable[[], str | None] | None = None,
        unit_invocation: Callable[[], str | None] | None = None,
        timeouts: Timeouts | None = None,
        sleep: Callable[[float], Awaitable[None]] = asyncio.sleep,
        clock: Callable[[], float] = time.monotonic,
        progress: Callable[[str], None] | None = None,
        hook: Callable[[str], None] | None = None,
    ) -> None:
        self.names = names
        self._public_client = client
        self._private_client: Any | None = None
        self._isolate = isolate or functools.partial(write_isolation_dropin, names)
        self._release = release or functools.partial(remove_isolation_dropin, names)
        self._stop_unit = stop_unit or stop_engine_unit
        self._isolated_port = isolated_port or functools.partial(globals()["isolated_port"], names)
        self._client_for = client_for or self._default_client_for
        self._listener = listener or loopback_listener
        self._unit_text = unit_text or read_unit_text
        self._unit_invocation = unit_invocation or engine_invocation_id
        #: ``api_version`` the preflight read; every restart must bring back the same.
        self._api_version: str | None = None
        self._apply_dropin = apply_dropin
        self._evidence = evidence or embedder_record.engine_evidence
        self.t = timeouts or Timeouts()
        self._sleep = sleep
        self._clock = clock
        self._progress = progress or (lambda _msg: None)
        self._hook = hook or (lambda _point: None)

    def _refusal(self, message: str) -> MaintenanceError:
        """The error raised when the run cannot start, before anything changed."""
        return self._error_type(message)

    def _default_client_for(self, base_url: str) -> Any:
        from hal0.memory.hindsight_client import HindsightRestClient

        return HindsightRestClient(base_url=base_url, api_key=self._public_client.api_key)

    @property
    def _client(self) -> Any:
        """The client for where the engine is now: its private port, else its normal address."""
        return self._private_client if self._private_client is not None else self._public_client

    def _use_port(self, port: int | None) -> None:
        """Talk to the engine on its private ``port``, or on its normal address for ``None``."""
        if port is None:
            self._private_client = None
        elif (
            self._private_client is None
            or self._private_client.base_url != f"http://127.0.0.1:{port}"
        ):
            self._private_client = self._client_for(f"http://127.0.0.1:{port}")

    def _say(self, message: str) -> None:
        log.info(self.names.log_event, step=message)
        self._progress(message)

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
            raise self._error_type(
                f"{what}: the engine answered HTTP {exc.response.status_code} "
                f"{http_detail(exc.response.text)}".rstrip(),
                bank=bank,
            ) from exc
        except httpx.HTTPError as exc:
            raise self._error_type(
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
        page_size: int = _PAGE,
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
                params={**(params or {}), "limit": page_size, "offset": len(rows)},
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
            raise self._error_type(
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

    async def bank_state(self, bank: str) -> dict[str, Any]:
        """Counts, queue depth and fingerprint for one bank, uncached.

        Facts are every memory unit that is not an observation
        (``nodes_by_fact_type``); mental models include the ones that back a
        knowledge page; directives include inactive ones. The fingerprint is
        described by :data:`FINGERPRINT_SIGNALS`.
        """
        base = bank_path(bank)
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
        except MaintenanceError:
            # The per-bank config API can be switched off (features.bank_config_api).
            config = "unavailable"
        try:
            tree = await self._json(
                "GET", f"{base}/knowledge-base/tree", what="reading the knowledge base", bank=bank
            )
            knowledge: Any = sorted(
                (_knowledge_nodes(tree if isinstance(tree, dict) else {})),
                key=lambda n: str(n.get("id")),
            )
        except MaintenanceError:
            knowledge = "unavailable"
        row = await self._bank_row(bank)
        definitions = [
            {k: m.get(k) for k in _MODEL_DEFINITION_KEYS} for m in models if isinstance(m, dict)
        ]
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
            "pending_consolidation": int(stats.get("pending_consolidation") or 0),
            "listed_facts": row.get("fact_count"),
            "nodes_total": int(stats.get("total_nodes") or 0),
            "fingerprint": {
                "directives": digest(directives),
                "config": digest(config),
                "mental_models": digest(definitions),
                "document": row.get("last_document_at"),
                "knowledge": digest(knowledge),
            },
        }

    async def _quiet_state(self, bank: str) -> dict[str, Any]:
        """:meth:`bank_state` once the engine has stopped working on the bank.

        Quiet is no pending or processing operation and a consolidation queue
        that has stopped moving between two reads. Counts taken while the
        engine is still consolidating describe a bank that is about to
        change. Bounded: after ``quiet_s`` the last read is returned as it is,
        and the comparisons made on it treat observations as the one count
        the engine may still move.
        """
        start = self._clock()
        deadline = start + self.t.quiet_s
        last: int | None = None
        while True:
            state = await self.bank_state(bank)
            busy = state["pending"] or state["processing"]
            if not busy and state["pending_consolidation"] == last:
                return state
            # The engine's own refresh or consolidation is waited for longer,
            # and never returned as if it were a change to the bank.
            own = await self._engine_work(bank, state)
            now = self._clock()
            if own:
                if now >= start + self.t.engine_ops_s:
                    raise self._engine_work_error(bank, own)
            elif now >= deadline:
                return state
            last = state["pending_consolidation"]
            await self._sleep(self.t.poll_s)

    async def _live_operations(self, bank: str) -> list[dict[str, Any]]:
        """Every pending and processing operation on ``bank``, as the engine lists them."""
        rows: list[Any] = []
        for status in _OP_LIVE:
            rows += await self._list_all(
                f"{bank_path(bank)}/operations",
                {"status": status},
                key="operations",
                what="listing operations",
                bank=bank,
            )
        return [r for r in rows if isinstance(r, dict) and r.get("status") in _OP_LIVE]

    async def _engine_work(self, bank: str, state: Mapping[str, Any]) -> list[dict[str, Any]]:
        """The engine's own live operations on ``bank`` when they are all that keeps it busy.

        Empty when the bank is idle, and when anything else is pending or
        processing (a retain, an export, an operation the listing does not
        show): that is not the engine's to finish, and the caller treats the
        bank as busy the way it always has.
        """
        busy = int(state["pending"] or 0) + int(state["processing"] or 0)
        if not busy:
            return []
        live = await self._live_operations(bank)
        if len(live) < busy or any(operation_type(op) not in ENGINE_OPERATION_TYPES for op in live):
            return []
        return live

    async def _await_engine_work(
        self, bank: str, state: Mapping[str, Any]
    ) -> tuple[dict[str, Any], list[dict[str, Any]]]:
        """Wait while the engine's own work is all that keeps ``bank`` busy.

        Returns the bank's last state and whatever of that work is still
        live: empty once it has ended, or when other work is queued (which is
        not waited for here). Bounded by ``engine_ops_s``.
        """
        deadline = self._clock() + self.t.engine_ops_s
        current = dict(state)
        while True:
            own = await self._engine_work(bank, current)
            if not own or self._clock() >= deadline:
                return current, own
            await self._sleep(self.t.poll_s)
            current = await self.bank_state(bank)

    def _engine_work_error(self, bank: str, own: list[dict[str, Any]]) -> MaintenanceError:
        return self._error_type(
            f"the engine's own {operations_text(bank, own)} did not finish within "
            f"{self.t.engine_ops_s:g}s. The engine queues this work by itself (a mental-model "
            "refresh after a restore or a restart, consolidation of new facts); "
            f"{self.names.actor} waits for it rather than read it as a change to the bank, and "
            f"never cancels it. Let it finish (`hal0 memory ops list --bank {bank}` shows it), "
            "then try again",
            bank=bank,
            details={"engine_operations": [_op_ref(bank, op) for op in own]},
        )

    async def _wait_operation(
        self, bank: str, operation_id: str, *, timeout_s: float, what: str
    ) -> dict[str, Any]:
        deadline = self._clock() + timeout_s
        path = f"{bank_path(bank)}/operations/{quote(operation_id, safe='')}"
        # A small bank's export or restore is done in well under a second, so
        # the first looks come quickly and only a long one settles at poll_s.
        delay = min(0.2, self.t.poll_s)
        while True:
            op = await self._json("GET", path, what=what, bank=bank)
            op = op if isinstance(op, dict) else {}
            status = op.get("status")
            if status == "completed":
                return op
            if status in _OP_FAILED:
                raise self._error_type(
                    f"{what} ended as {status}: {op.get('error_message') or 'no error message'}",
                    bank=bank,
                )
            if self._clock() >= deadline:
                raise self._error_type(
                    f"{what} did not finish within {timeout_s:g}s (last status: {status})",
                    bank=bank,
                    details={"operation_id": operation_id},
                )
            await self._sleep(delay)
            delay = min(self.t.poll_s, delay * 1.5)

    async def _delete_bank(self, bank: str) -> None:
        """Delete ``bank``. A bank that is already gone is a success."""
        try:
            await self._client.request_json("DELETE", bank_path(bank), timeout_s=self.t.delete_s)
        except httpx.HTTPStatusError as exc:
            if exc.response.status_code == 404:
                return
            raise self._error_type(
                f"deleting the bank: the engine answered HTTP {exc.response.status_code} "
                f"{http_detail(exc.response.text)}".rstrip(),
                bank=bank,
            ) from exc
        except httpx.HTTPError as exc:
            raise self._error_type(
                f"deleting the bank: the engine did not answer ({type(exc).__name__}: {exc})",
                bank=bank,
            ) from exc

    async def _wait_quiet(self, bank: str) -> None:
        """Wait until ``bank`` has no pending or processing operation, before it is deleted.

        The engine's own work (:data:`ENGINE_OPERATION_TYPES`) is waited for
        up to ``engine_ops_s``, and named when it does not end.
        """
        start = self._clock()
        deadline = start + self.t.quiet_s
        while True:
            state = await self.bank_state(bank)
            if not state["pending"] and not state["processing"]:
                return
            own = await self._engine_work(bank, state)
            if own:
                if self._clock() >= start + self.t.engine_ops_s:
                    raise self._engine_work_error(bank, own)
            elif self._clock() >= deadline:
                raise self._error_type(
                    f"bank {bank} still has {state['pending']} pending and {state['processing']} "
                    f"processing operations after {self.t.quiet_s:g}s; it is not deleted "
                    "until they finish",
                    bank=bank,
                )
            await self._sleep(self.t.poll_s)

    async def _public_answers(self) -> bool:
        """True when anything answers at the engine's normal address.

        Only a refused connection means nothing listens there. Anything else,
        a connection that was accepted and then timed out or was reset
        included, means something holds the normal address, so the engine is
        not isolated.
        """
        try:
            await self._public_client.request_json("GET", "/health", timeout_s=3.0)
        except httpx.ConnectError:
            return False
        except (httpx.HTTPError, ValueError):
            return True
        return True

    def _normal_port(self) -> int | None:
        try:
            return urlsplit(str(self._public_client.base_url)).port
        except (AttributeError, ValueError):
            return None

    def _leak_message(self) -> str:
        """Why the engine still answers at its normal address, naming the drop-in when one wins."""
        text = self._unit_text()
        later = later_exec_start_dropins(self.names, text) if text else []
        if later:
            path, command = later[0]
            return (
                f"the engine still answers at its normal address, so it is not isolated: "
                f"{path} sorts after {self.names.drop_in} and sets ExecStart "
                f"(`{command or '(cleared)'}`), which replaces {self.names.actor}'s private "
                f"command. Rename it so it sorts before {self.names.drop_in}, run "
                f"`systemctl daemon-reload`, then run {self.names.actor} again"
            )
        return (
            "the engine still answers at its normal address, so it is not isolated: a "
            f"drop-in that sorts after {self.names.drop_in} replaces ExecStart, or "
            f"something else listens there. {self.names.actor_title} does not run on an engine "
            "other "
            "clients can reach"
        )

    def _listener_owner(self, port: int) -> Mapping[str, Any]:
        try:
            return self._listener(port)
        except Exception as exc:  # a /proc read gone wrong is "cannot tell", not a crash
            return {"owner": "unknown", "holder": f"unreadable ({type(exc).__name__})"}

    async def _foreign(
        self, port: int, seen: Mapping[str, Any], journal: MaintenanceJournal | None = None
    ) -> MaintenanceError:
        """Stop the engine's unit, leave it stopped, and say what to do.

        The unit was started on a port someone else holds, so it cannot bind
        and systemd would restart it every few seconds for ever (each boot
        outlasts ``StartLimitIntervalSec``, so the start limit never trips).
        The gate stays set and the journal stays; ``--resume`` starts it again.
        """
        stopped = await asyncio.to_thread(self._stop_unit)
        error = stopped.get("error")
        if journal is not None:
            journal.engine_stopped = not error
            with contextlib.suppress(OSError):
                self._save(journal)
        who = seen.get("holder") or "another process"
        facts = {"foreign_port": port, "foreign_holder": who}
        held = f"127.0.0.1:{port}, the engine's private port, is held by {who}, not by {SERVICE}. "
        if error:
            return self._error_type(
                held + f"{SERVICE} could not be stopped ({error}), so it may be restarting into "
                f"the taken port: run `systemctl stop {SERVICE}`, free the port, then run "
                f"`{self.names.command} --resume`. Memory writes stay paused",
                details=facts,
                resumable=True,
            )
        return self._error_type(
            held + f"{SERVICE} was stopped so it does not restart into the taken port; it stays "
            "stopped, memory writes and recall stay paused. Free the port (stop that process), "
            f"then run `{self.names.command} --resume`",
            details=facts,
            resumable=True,
        )

    async def _confirm_version(self, where: str) -> None:
        """``GET /version`` answers as the engine the preflight saw, with export and import on."""
        try:
            version = await self._client.request_json("GET", "/version", timeout_s=10.0)
        except (httpx.HTTPError, ValueError) as exc:
            raise self._error_type(
                f"the process on {where} answers /health but not /version "
                f"({type(exc).__name__}), so it is not confirmed to be the memory engine"
            ) from exc
        version = version if isinstance(version, dict) else {}
        features = version.get("features") if isinstance(version.get("features"), dict) else {}
        api = version.get("api_version")
        if not api or (self._api_version is not None and api != self._api_version):
            raise self._error_type(
                f"the process on {where} reports api_version {api!r}, not "
                f"{self._api_version or 'an engine version'!r}: it is not the engine "
                f"{self.names.actor} checked before it started"
            )
        if not features.get("document_export_api") or not features.get("document_import_api"):
            raise self._error_type(
                f"the engine on {where} has its export or import API off "
                "(features on /version) after the restart"
            )

    async def _start_engine(
        self,
        embedder: Embedder,
        *,
        port: int | None,
        run_id: str,
        journal: MaintenanceJournal | None = None,
    ) -> None:
        """Restart the engine on ``embedder``, private on ``port`` or at its normal address.

        Writes or removes the isolation drop-in, renders the embedder drop-in,
        reloads and restarts once, then confirms from the engine itself: it
        answers where it should, its environment names ``embedder``, and when
        private, nothing answers at the normal address any more.
        """
        if port is not None:
            placed = await asyncio.to_thread(self._isolate, port, run_id)
        else:
            placed = await asyncio.to_thread(self._release)
        if placed.get("error"):
            raise self._error_type(
                f"could not {'isolate' if port is not None else 'release'} the engine: "
                f"{placed['error']}"
            )
        self._use_port(port)
        result = await asyncio.to_thread(self._apply_dropin, embedder)
        if result.get("error"):
            raise self._error_type(f"could not restart the engine: {result['error']}")
        if (
            result.get("restarted") is True
            and journal is not None
            and any(e.restore_op for e in journal.banks)
        ):
            # The unit was restarted, which ends every worker it ran: a restore
            # sent before this can no longer be writing.
            for e in journal.banks:
                if e.restore_op:
                    e.restore_op["restarted"] = True
            self._save(journal)
        where = f"its private port {port}" if port is not None else "its normal address"
        deadline = self._clock() + self.t.health_s
        while True:
            if port is not None:
                seen = self._listener_owner(port)
                if seen.get("owner") == "other":
                    # Something else took the port: fail now, not after the timeout.
                    raise await self._foreign(port, seen, journal)
            if await self._healthy():
                break
            # Up, but where it always was: do not wait out the whole timeout.
            if port is not None and await self._public_answers():
                raise self._error_type(self._leak_message())
            if self._clock() >= deadline:
                raise self._error_type(
                    f"the engine did not answer /health on {where} within {self.t.health_s:g}s "
                    f"of its restart on {describe_embedder(*embedder)} "
                    "(see `journalctl -u hindsight-api`)"
                )
            await self._sleep(self.t.poll_s)
        if port is not None:
            # /health on the port proves only that something answers there.
            seen = self._listener_owner(port)
            if seen.get("owner") == "other":
                raise await self._foreign(port, seen, journal)
            if seen.get("owner") != "unit":
                holder = seen.get("holder") or "no listener found"
                raise self._error_type(
                    f"something answers on 127.0.0.1:{port}, but the listening socket could not "
                    f"be matched to a process of {SERVICE} ({holder}), so it is not confirmed "
                    f"to be the engine. Run {self.names.actor} as root"
                )
        await self._confirm_version(where)
        live: Embedder | None = None
        for _ in range(10):
            # Read from the unit's main process; on a private port that is the
            # process confirmed above to own the listening socket.
            live = evidence_pair(self._evidence().get("live"))
            if live == embedder:
                break
            await self._sleep(self.t.poll_s)
        else:
            runs = (
                describe_embedder(*live) if live is not None else "an environment hal0 cannot read"
            )
            raise self._error_type(
                f"the engine is up but runs {runs}, not {describe_embedder(*embedder)}: another "
                "drop-in, or the unit's EnvironmentFile, overrides the embedder"
            )
        if port is not None and await self._public_answers():
            raise self._error_type(self._leak_message())
        if journal is not None and journal.engine_stopped:
            journal.engine_stopped = False
            with contextlib.suppress(OSError):
                self._save(journal)

    async def _ensure_engine(
        self,
        embedder: Embedder,
        *,
        port: int | None,
        run_id: str,
        journal: MaintenanceJournal | None = None,
    ) -> None:
        """Do nothing when the engine is where and what it should be; otherwise put it there.

        A restore embeds with whatever the engine runs at that moment, so this
        is asked before every restore and at the start of every resume, from
        the engine itself and not from the record or the journal.
        """
        self._use_port(port)
        if (
            self._isolated_port() == port
            and await self._healthy()
            and evidence_pair(self._evidence().get("live")) == embedder
            and not (port is not None and await self._public_answers())
            and (port is None or self._listener_owner(port).get("owner") == "unit")
        ):
            if port is not None and journal is not None and journal.engine_stopped:
                # Started again outside the run: the unit is healthy and holds the port.
                journal.engine_stopped = False
                with contextlib.suppress(OSError):
                    self._save(journal)
            return
        self._say(
            f"putting the engine on {describe_embedder(*embedder)}, "
            + ("isolated" if port is not None else "at its normal address")
        )
        await self._start_engine(embedder, port=port, run_id=run_id, journal=journal)

    def _make_archive_dir(self, run_id: str) -> Path:
        """Create this run's ``0700`` archive directory under the shared backups directory."""
        from hal0.config.migrations.runner_system import _backups_dir

        stamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
        run_dir = _backups_dir() / f"{self.names.archive_prefix}{stamp}-{run_id}"
        nofollow.ensure_dir(run_dir, mode=0o700, owner=perms.state_root_owner(), create=True)
        return run_dir

    @contextlib.contextmanager
    def _archive_dir_fd(self, journal: MaintenanceJournal) -> Iterator[int]:
        fd = nofollow.open_dir(journal.archive_dir)
        try:
            yield fd
        finally:
            os.close(fd)

    async def _export_bank(
        self, journal: MaintenanceJournal, entry: MaintenanceBank, index: int
    ) -> dict[str, int]:
        """Export one bank into the run's archive directory; returns the archive's counts."""
        bank = entry.bank
        submit = await self._json(
            "POST",
            f"{bank_path(bank)}/transfer/export",
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
            raise self._error_type("the export returned no operation id", bank=bank)
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
                        raise self._error_type(
                            f"downloading the archive failed ({type(exc).__name__}: {exc})",
                            bank=bank,
                        ) from exc
                    fh.flush()
                    os.fsync(fh.fileno())
                # A bank exported again within a run (it moved, or it is
                # being rebuilt) never overwrites its earlier archive.
                kept = 0
                while self._exists_in(dfd, name):
                    kept += 1
                    earlier = f"{name[:-4]}.earlier-{kept}.zip"
                    if not self._exists_in(dfd, earlier):
                        os.rename(name, earlier, src_dir_fd=dfd, dst_dir_fd=dfd)
                os.replace(part, name, src_dir_fd=dfd, dst_dir_fd=dfd)
                os.fsync(dfd)
            except BaseException:
                with contextlib.suppress(OSError):
                    os.unlink(part, dir_fd=dfd)
                raise
        entry.archive = name
        return self._archive_counts(journal, entry)

    @staticmethod
    def _exists_in(dir_fd: int, name: str) -> bool:
        try:
            os.stat(name, dir_fd=dir_fd, follow_symlinks=False)
        except FileNotFoundError:
            return False
        return True

    def _download_path(self, meta: Mapping[str, Any], bank: str) -> str:
        """The archive's path on the engine. Never a URL on another host."""
        raw = meta.get("download_url")
        if isinstance(raw, str) and raw.startswith("/v1/default/files/download/"):
            return raw
        key = meta.get("storage_key")
        if isinstance(key, str) and key and ".." not in key.split("/"):
            return "/v1/default/files/download/" + quote(key, safe="/")
        raise self._error_type(
            "the export finished without a download location on the engine", bank=bank
        )

    def _archive_counts(
        self, journal: MaintenanceJournal, entry: MaintenanceBank
    ) -> dict[str, int]:
        """What the archive says it holds. Raises :attr:`_error_type` for an unusable one."""
        bank = entry.bank
        try:
            with self._archive_dir_fd(journal) as dfd:
                fd = os.open(str(entry.archive), os.O_RDONLY | os.O_NOFOLLOW, dir_fd=dfd)
                with os.fdopen(fd, "rb") as fh:
                    if os.fstat(fh.fileno()).st_size == 0:
                        raise self._error_type("the archive is empty", bank=bank)
                    with zipfile.ZipFile(fh) as zf:
                        manifest = json.loads(zf.read("manifest.json"))
                        models: int | None = None
                        if "mental_models.json" in zf.namelist():
                            rows = json.loads(zf.read("mental_models.json"))
                            models = len(rows) if isinstance(rows, list) else None
        except MaintenanceError:
            raise
        except (OSError, KeyError, ValueError, zipfile.BadZipFile) as exc:
            raise self._error_type(
                f"the archive is not a readable transfer archive ({type(exc).__name__}: {exc})",
                bank=bank,
            ) from exc
        if not isinstance(manifest, dict) or manifest.get("archive_type") != "bank":
            raise self._error_type("the archive is not a whole-bank archive", bank=bank)
        if manifest.get("source_bank_id") != bank:
            raise self._error_type(
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

    async def _restore(
        self,
        journal: MaintenanceJournal,
        entry: MaintenanceBank,
        target_id: str,
        *,
        embedder: Embedder | None,
        via: str | None = None,
    ) -> dict[str, Any]:
        """Restore ``entry``'s archive into ``target_id``; returns that bank's state.

        A restore embeds every fact with whatever the engine runs, so the
        engine is first put on ``embedder`` (:meth:`_ensure_engine`); passing
        none is a bug in the caller. The engine records a restore against the
        bank named in the URL, which must therefore exist while the target
        must not: ``via`` is that bank, the original by default.
        """
        assert embedder is not None
        await self._ensure_engine(
            embedder, port=journal.private_port, run_id=journal.run_id, journal=journal
        )
        url_bank = via or entry.bank
        try:
            with self._archive_dir_fd(journal) as dfd:
                fd = os.open(str(entry.archive), os.O_RDONLY | os.O_NOFOLLOW, dir_fd=dfd)
                with os.fdopen(fd, "rb") as fh:
                    resp = await self._client._http.post(
                        f"{bank_path(url_bank)}/transfer/import",
                        headers=self._auth(),
                        params={"mode": "restore", "target_bank_id": target_id},
                        files={"file": (str(entry.archive), fh, "application/zip")},
                        timeout=httpx.Timeout(self.t.export_s, connect=3.0),
                    )
            resp.raise_for_status()
        except httpx.HTTPStatusError as exc:
            raise self._error_type(
                f"starting the restore into {target_id}: the engine answered HTTP "
                f"{exc.response.status_code} {http_detail(exc.response.text)}".rstrip(),
                bank=entry.bank,
            ) from exc
        except (OSError, httpx.HTTPError) as exc:
            raise self._error_type(
                f"starting the restore into {target_id} failed ({type(exc).__name__}: {exc})",
                bank=entry.bank,
            ) from exc
        body = resp.json() if resp.content else {}
        operation_id = str((body or {}).get("operation_id") or "")
        if not operation_id:
            raise self._error_type("the restore returned no operation id", bank=entry.bank)
        entry.restore_op = {
            "bank": url_bank,
            "id": operation_id,
            "target": target_id,
            "engine_pid": self._engine_pid(journal),
            "engine_invocation": self._invocation(),
        }
        self._save(journal)
        budget = self._restore_budget(entry)
        try:
            await self._wait_operation(
                url_bank, operation_id, timeout_s=budget, what=f"the restore into {target_id}"
            )
        except MaintenanceError as exc:
            # Recorded against the scratch bank for a restore into the own
            # id; it is still this bank's failure.
            exc.bank = entry.bank
            raise
        entry.restore_op = None
        self._save(journal)
        # Counted once the engine has stopped working on the new bank.
        return await self._quiet_state(target_id)

    def _restore_budget(self, entry: MaintenanceBank) -> float:
        embedded = entry.counts.get("facts", 0) + entry.counts.get("observations", 0)
        return max(self.t.restore_floor_s, self.t.restore_factor * embedded / RESTORE_FACTS_PER_S)

    def _engine_pid(self, journal: MaintenanceJournal) -> int | None:
        """The process that holds the engine's private port, when it is the engine's unit."""
        if journal.private_port is None:
            return None
        seen = self._listener_owner(journal.private_port)
        pid = seen.get("pid")
        return pid if seen.get("owner") == "unit" and isinstance(pid, int) else None

    def _invocation(self) -> str | None:
        """The engine unit's current ``InvocationID``; ``None`` when it cannot be read."""
        try:
            value = self._unit_invocation()
        except Exception:  # cannot tell is not a crash
            return None
        return value if isinstance(value, str) and value else None

    def _engine_restarted_since(
        self, journal: MaintenanceJournal, op: Mapping[str, Any]
    ) -> bool | None:
        """Whether the engine restarted since ``op`` was sent; ``None`` when it cannot be told.

        Either signal is enough: the process on the private port is another
        one, or the unit's ``InvocationID`` (new on every start, an automatic
        restart included) is another one. Each is compared only when it was
        recorded at the send and can be read now. A restart the run made
        itself is marked on the operation (``restarted``) and is enough alone.
        """
        if op.get("restarted"):
            return True
        known = False
        for then, now in (
            (op.get("engine_pid"), self._engine_pid(journal)),
            (op.get("engine_invocation"), self._invocation()),
        ):
            if then is not None and now is not None:
                if then != now:
                    return True
                known = True
        return False if known else None

    def _save(self, journal: MaintenanceJournal) -> None:
        """Write the journal and its mirror, each atomically and synced to disk."""
        journal.updated_at = datetime.now(UTC).isoformat(timespec="seconds")
        text = json.dumps(journal.to_dict(), indent=2) + "\n"
        write_synced(journal_path(self.names), text, mode=0o644)
        write_synced(Path(journal.archive_dir) / self.names.mirror, text, mode=0o600)

    def _retire_journal(self, journal: MaintenanceJournal) -> None:
        """Close a finished run: the mirror keeps the last word, the primary goes."""
        journal.updated_at = datetime.now(UTC).isoformat(timespec="seconds")
        text = json.dumps(journal.to_dict(), indent=2) + "\n"
        # Best effort: a mirror that cannot be written (its directory was
        # removed) must not keep a finished run looking unfinished for ever.
        with contextlib.suppress(OSError):
            write_synced(Path(journal.archive_dir) / self.names.mirror, text, mode=0o600)
        journal_path(self.names).unlink(missing_ok=True)

    @contextlib.contextmanager
    def _lock(self) -> Iterator[None]:
        """Hold the command's run lock for the life of the block.

        An ``flock`` on a file under ``state/``: the kernel drops it when the
        process ends, so a killed run never leaves a stale lock behind.
        """
        path = lock_path(self.names)
        os.close(nofollow.open_dir(path.parent, create=True))
        # Read-only is enough for ``flock``, and it means a lock file left
        # root-owned by one run never locks the service account out of the next.
        fd = os.open(path, os.O_RDONLY | os.O_CREAT | os.O_NOFOLLOW | os.O_CLOEXEC, 0o644)
        try:
            try:
                fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
            except BlockingIOError:
                raise self._refusal(
                    f"another `{self.names.command}` is running on this box "
                    f"(it holds {path}); wait for it to finish"
                ) from None
            owner = perms.state_root_owner()
            if owner is not None:
                with contextlib.suppress(OSError):
                    os.fchown(fd, *owner)
            yield
        finally:
            os.close(fd)

    async def _settle(self, journal: MaintenanceJournal) -> dict[str, dict[str, Any]]:
        """Wait for every bank's queue to drain; returns each bank's state.

        Bounded by ``settle_s``, or by ``engine_ops_s`` while the engine's own
        work (:data:`ENGINE_OPERATION_TYPES`) is all that is left.
        """
        start = self._clock()
        while True:
            ids = [b for b in await self._bank_ids() if b not in journal.own_ids()]
            states = {bank: await self.bank_state(bank) for bank in ids}
            busy = {b: s for b, s in states.items() if s["pending"] or s["processing"]}
            if not busy or journal.allow_pending:
                return states
            own = {b: await self._engine_work(b, s) for b, s in busy.items()}
            engine_only = all(own.values())
            limit = self.t.engine_ops_s if engine_only else self.t.settle_s
            if self._clock() >= start + limit:
                listing = ", ".join(
                    f"{bank} ({s['pending']} pending, {s['processing']} processing"
                    + (f": {operations_text(bank, own[bank])}" if own[bank] else "")
                    + ")"
                    for bank, s in busy.items()
                )
                raise self._error_type(
                    f"operations queued before the pause did not finish within {limit:g}s: "
                    f"{listing}",
                    details={
                        "engine_operations": [
                            _op_ref(b, op) for b, ops in own.items() for op in ops
                        ]
                    },
                )
            await self._sleep(self.t.poll_s)

    async def _pause(self, journal: MaintenanceJournal) -> None:
        """Pause writes through hal0 and let the queues drain before the engine is restarted."""
        self._say("pausing memory writes")
        write_gate.engage_gate(
            run_id=journal.run_id, archive_dir=journal.archive_dir, reason=self.names.gate_reason
        )
        self._hook("paused")
        await self._settle(journal)

    async def _await_restore(self, journal: MaintenanceJournal, entry: MaintenanceBank) -> None:
        """Wait until ``entry``'s restore, if one may still run, has ended. Never cancel it.

        Hindsight 1.2.0 cancels cooperatively: ``DELETE`` on an operation flips
        it to ``cancelled`` at once, but a bank restore never looks at that, so
        its worker goes on writing, and the ``completed`` or ``failed`` it
        writes at the end is suppressed once the row says ``cancelled``
        (``cancel_operation`` and ``_mark_operation_*`` in ``memory_engine.py``).
        A cancelled restore therefore looks ended while it still writes, and
        nothing the engine reports says when it really stops. So a run
        never cancels a restore; it waits for ``completed`` or ``failed``, the
        worker's own last word. A restore found ``cancelled`` (by someone
        else) or gone from the engine is believed ended only when the engine
        has restarted since it was sent (:meth:`_engine_restarted_since`): a
        restart of the unit ends its worker, and the engine's start-up recovery
        requeues only ``processing`` rows. Anything else is refused, and
        nothing is deleted or restored again.
        """
        op = entry.restore_op
        if not op:
            return
        path = f"{bank_path(op['bank'])}/operations/{quote(op['id'], safe='')}"
        target = op.get("target") or entry.bank
        budget = 2 * self._restore_budget(entry)
        deadline = self._clock() + budget
        delay = min(0.2, self.t.poll_s)
        while True:
            found = await self._json("GET", path, what="reading the restore", bank=entry.bank)
            status = found.get("status") if isinstance(found, dict) else None
            if status in ("completed", "failed"):
                break
            if status not in _OP_LIVE:
                restarted = self._engine_restarted_since(journal, op)
                if restarted:
                    break
                blind = (
                    ""
                    if restarted is not None
                    else " If this message comes back after the restart, "
                    f"{self.names.actor} cannot "
                    "see the engine's process: no process of the unit holds "
                    f"127.0.0.1:{journal.private_port} as far as it can tell, or `systemctl "
                    f"show -p InvocationID {SERVICE}` gives nothing, or the restore was "
                    "recorded by a build that wrote neither down. Then stop the engine "
                    f"(`systemctl stop {SERVICE}`), which ends every restore, and run "
                    "--resume: it starts the engine again and records it"
                )
                raise self._error_type(
                    f"the restore of {entry.bank} into {target} (operation {op['id']}) is "
                    f"recorded as {status or 'unknown'}. Hindsight lets a cancelled restore "
                    f"go on writing and never reports when it stops, so {self.names.actor} cannot "
                    f"tell whether {target} is still being written; nothing was deleted or "
                    "restored again. Restart the engine (`systemctl restart "
                    f"{SERVICE}`), which ends that restore, then run "
                    f"`{self.names.command} --resume`." + blind,
                    bank=entry.bank,
                    details={"operation_id": op["id"], "status": status},
                )
            if self._clock() >= deadline:
                raise self._error_type(
                    f"the restore of {entry.bank} into {target} (operation {op['id']}) is "
                    f"still {status} after {budget:g}s. {self.names.actor_title} does not cancel a restore "
                    "(Hindsight would mark it cancelled and let it go on writing), and "
                    f"nothing was deleted or restored again. Let it finish (`journalctl -u "
                    f"{SERVICE}`), then run `{self.names.command} --resume` again",
                    bank=entry.bank,
                    details={"operation_id": op["id"], "status": status},
                )
            await self._sleep(delay)
            delay = min(self.t.poll_s, delay * 1.5)
        entry.restore_op = None
        self._save(journal)


__all__ = [
    "COUNT_KEYS",
    "ENGINE_OPERATION_TYPES",
    "FINGERPRINT_SIGNALS",
    "HARD_COUNTS",
    "PG_DIR",
    "RESTORE_FACTS_PER_S",
    "SERVICE",
    "EngineMaintenance",
    "MaintenanceBank",
    "MaintenanceError",
    "MaintenanceJournal",
    "MaintenanceNames",
    "Timeouts",
    "UnsupportedExecStart",
    "archive_root",
    "bank_path",
    "device",
    "diff",
    "diff_text",
    "digest",
    "du_bytes",
    "effective_exec_start",
    "engine_invocation_id",
    "evidence_pair",
    "exec_start_refusal",
    "exec_start_source",
    "free_loopback_port",
    "http_detail",
    "isolated_port",
    "isolation_body",
    "isolation_drop_in_path",
    "journal_path",
    "later_exec_start_dropins",
    "locate_journal",
    "lock_held",
    "lock_path",
    "loopback_listener",
    "nearest_existing",
    "operation_type",
    "operations_text",
    "private_exec_start",
    "read_journal_file",
    "read_unit_text",
    "remove_isolation_dropin",
    "side_commands_on_port",
    "signals_moved",
    "stop_engine_unit",
    "unfinished_mirrors",
    "unit_refusals",
    "update_in_progress",
    "write_isolation_dropin",
    "write_synced",
]
