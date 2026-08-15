"""Preflight the context window behind Hermes' anchor model (#1867).

Hermes refuses to run at all when the model it is pointed at advertises a
context window below ``agent/model_metadata.py::MINIMUM_CONTEXT_LENGTH``
(64,000 at the pinned commit) — it raises rather than degrading. hal0's side of
that contract is the EFFECTIVE window of the slot behind the anchor
(``hal0/agent`` by default), which is::

    effective = min(model's native/declared window, slot's [model].context_size)

so either half can put the box under the floor:

* the MODEL side — a registry row with no ``defaults.context_size`` resolving
  to a small derived window (#1827's fresh-install half, fixed by #1852), and
* the SLOT side — a configured ceiling that is itself below the floor. An
  in-place upgrade never revisits an existing ``/etc/hal0/slots/<slot>.toml``,
  so a box seeded by an older release keeps e.g. ``context_size = 4096``
  forever even though its model happily declares 96000. #1852's "the ceiling
  wins over the blanket dense cap" change cannot help there: a ceiling only
  ever clamps DOWN.

Nothing used to check this before handing the anchor to Hermes, so the second
shape surfaced ~1.6s into the first turn as an opaque upstream agent error that
named neither the slot nor the ceiling behind it. This module is the check: it
resolves the anchor's effective window, compares it against Hermes' own floor
(read FROM the installed Hermes when possible, see
:func:`read_hermes_minimum_context`), and renders a message an operator can act
on without reading any source — which slot, that slot's configured ceiling, the
required floor, and the exact command that repairs it.

Two rules keep the answer honest, both bought the hard way:

* **Ask the by-id route, never the anchor's spelling.** ``hal0/agent`` is a
  virtual name that resolves through the routing fallback chain, so the slot it
  is *spelled* like need not be the slot serving it. ct152 has an offline agent
  slot, an ``agent.toml`` on disk, and ``hal0/agent`` answering out of the
  BRAIN slot at 32,768 tokens — under the floor. ``GET /v1/models/{id}``
  resolves that chain and carries the resulting window; the ``/v1/models`` LIST
  never even mentions ``agent`` there.
* **Unknown is not a pass.** A window that cannot be read is reported as
  neither — callers skip or warn. On ct152 the alias lookup found nothing,
  called it unknown and passed, which is how a box that refuses every turn read
  green (the #1831 defect, on the detector for #1827).

Deliberately read-only. Rewriting an operator's slot ceiling during an update is
a config mutation nobody has approved yet and is tracked as the other half of
#1867; :func:`resolve_anchor_window` is the resolver that repair would be built
on when it is decided (it already names the slot, its ceiling, and the target
value the repair would need to write).
"""

from __future__ import annotations

import subprocess  # nosec B404 — asks the Hermes venv python for its own constant
import tomllib
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import structlog

log = structlog.get_logger(__name__)

#: hal0's ONLY copy of Hermes' hard context floor — a fallback for when the
#: installed Hermes cannot be asked (see :func:`read_hermes_minimum_context`).
#: Mirrors ``agent/model_metadata.py::MINIMUM_CONTEXT_LENGTH`` in the bundled
#: Hermes (``[tool.hal0.upstream-hermes]`` pin in pyproject.toml). Hermes is not
#: importable from hal0's own venv, so this cannot be a plain re-export; the
#: drift guard is ``tests/agents/test_anchor_window.py``, which asserts every
#: hal0-side copy of the number resolves to this constant and compares against
#: the real Hermes constant whenever a Hermes install is reachable.
HERMES_MINIMUM_CONTEXT_LENGTH = 64_000

#: Where the slot TOMLs an operator (or the installer) writes ceilings into live.
SLOTS_DIR = Path("/etc/hal0/slots")

#: Prefix hal0's virtual model ids carry (``hal0/agent`` → slot ``agent``).
_VIRTUAL_PREFIX = "hal0/"

_FLOOR_PROBE = "from agent.model_metadata import MINIMUM_CONTEXT_LENGTH as m; print(int(m))"


def read_hermes_minimum_context(
    venv_python: str | Path | None,
    *,
    run: Callable[..., Any] = subprocess.run,
) -> tuple[int, str]:
    """Return ``(floor, source)`` — Hermes' own floor when it can be asked.

    hal0 runs from its own venv and never imports Hermes, so the number is read
    the only way it honestly can be: by asking the Hermes venv's interpreter for
    ``agent.model_metadata.MINIMUM_CONTEXT_LENGTH``. Any failure (no venv yet, an
    older Hermes without the constant, a Hermes that cannot import) degrades to
    :data:`HERMES_MINIMUM_CONTEXT_LENGTH` with the reason recorded in ``source``,
    because a preflight that raises is worse than one that uses the pinned value.

    ``source`` is ``"hermes"`` when the live constant was read, else
    ``"fallback:<why>"`` — so a message built off the fallback can say so.
    """
    if not venv_python:
        return (HERMES_MINIMUM_CONTEXT_LENGTH, "fallback:no-venv")
    try:
        proc = run(  # nosec B603 — fixed argv, no shell, hal0-owned venv path
            [str(venv_python), "-c", _FLOOR_PROBE],
            check=False,
            capture_output=True,
            text=True,
            timeout=15,
        )
    except (OSError, subprocess.SubprocessError) as exc:
        return (HERMES_MINIMUM_CONTEXT_LENGTH, f"fallback:{type(exc).__name__}")
    if getattr(proc, "returncode", 1) != 0:
        return (HERMES_MINIMUM_CONTEXT_LENGTH, "fallback:probe-failed")
    try:
        value = int(str(getattr(proc, "stdout", "") or "").strip())
    except (TypeError, ValueError):
        return (HERMES_MINIMUM_CONTEXT_LENGTH, "fallback:unparsable")
    if value <= 0:
        return (HERMES_MINIMUM_CONTEXT_LENGTH, "fallback:non-positive")
    return (value, "hermes")


def anchor_slot_name(model_name: str) -> str:
    """The alias an anchor model id *spells* (``hal0/agent`` → ``agent``).

    A NAMING convention only — emphatically not a routing answer. ``hal0/agent``
    routes to whatever the resolver's fallback chain currently lands on, which
    on a box with an offline agent slot is another slot entirely (ct152:
    ``hal0/agent`` → the ``brain`` slot). Use :func:`serving_slot`, which asks
    the gateway, to learn which slot is actually behind the anchor; this helper
    only supplies the candidate that a live catalog can confirm or refute.
    """
    name = (model_name or "").strip()
    if name.startswith(_VIRTUAL_PREFIX):
        return name[len(_VIRTUAL_PREFIX) :]
    return name


def serving_slot(
    model_name: str,
    *,
    entry: Mapping[str, Any] | None,
    catalog: Sequence[Mapping[str, Any]] | None = None,
    slots_dir: Path = SLOTS_DIR,
) -> str | None:
    """Which slot is actually serving the anchor — ``None`` when unprovable.

    ``entry`` is the ``GET /v1/models/{id}`` row. hal0's gateway builds it by
    copying the RESOLVED slot's catalog row and rewriting only ``id`` back to
    the requested handle (``api/routes/v1.py::_resolve_virtual_model_entry``),
    so the serving slot is the ``/v1/models`` row that is identical to it in
    every field except ``id``. That is a fact about the gateway's own
    construction, not a guess about naming.

    A ``hal0/*`` virtual is NEVER named from its spelling: ct152 has an
    ``agent.toml`` sitting right there while ``hal0/agent`` is served by the
    brain slot, so blaming the spelled slot's ceiling would send the operator
    to edit a file that is not the problem. A bare id is different — there the
    id IS the alias — but even then only when a slot TOML by that name exists,
    without which a raw physical model id (the ``HAL0_HERMES_LIVE_RESOLVE=0``
    shape) would invent a slot and a TOML path that were never on the box.
    """
    spelled = anchor_slot_name(model_name)
    if entry is not None and catalog:
        wanted = _row_identity(entry)
        candidates = [
            str(row.get("id"))
            for row in catalog
            if isinstance(row, Mapping)
            and row.get("id")
            and str(row.get("id")) != model_name
            and _row_identity(row) == wanted
        ]
        if spelled in candidates:
            return spelled
        if len(candidates) == 1:
            return candidates[0]
        if candidates:
            # Two slots serving byte-identical rows: the catalog cannot say
            # which one answered, and a coin flip here names the wrong TOML.
            return None
    if model_name == spelled and spelled and (Path(slots_dir) / f"{spelled}.toml").exists():
        return spelled
    return None


#: Fields that differ between two reads of the *same* row and so cannot take
#: part in identity. ``created`` is stamped per request by the gateway
#: (``api/routes/v1.py`` ``int(time.time())``), and the by-id read and the
#: catalog read are two separate requests — so any pair that straddles a second
#: boundary would compare unequal and the serving slot would come back unnamed,
#: taking the repair command out of the operator's error message. Measured at
#: ~2.5% on an idle box, and worse under load.
_VOLATILE_ROW_FIELDS = frozenset({"id", "created"})


def _row_identity(row: Mapping[str, Any]) -> tuple[tuple[str, str], ...]:
    """A catalog row reduced to its stable fields, order-independent."""
    return tuple(sorted((str(k), repr(v)) for k, v in row.items() if k not in _VOLATILE_ROW_FIELDS))


def read_slot_ceiling(slot: str, *, slots_dir: Path = SLOTS_DIR) -> int | None:
    """The slot's on-disk ``[model].context_size`` ceiling, or ``None``.

    ``None`` covers every "no usable ceiling" case alike — no TOML, no
    ``[model]`` table, no key, and a hand-edited garbage value (``"64k"``) —
    matching how :func:`hal0.providers.container._resolve_context_size` treats
    an unparsable ceiling. ``ctx_size`` is accepted as the dashboard's alias for
    the same key.
    """
    if not slot:
        return None
    path = Path(slots_dir) / f"{slot}.toml"
    try:
        raw = tomllib.loads(path.read_bytes().decode("utf-8"))
    except (OSError, UnicodeDecodeError, tomllib.TOMLDecodeError):
        return None
    model = raw.get("model")
    if not isinstance(model, Mapping):
        return None
    for key in ("context_size", "ctx_size"):
        value = model.get(key)
        if isinstance(value, bool):
            continue
        if isinstance(value, int) and value > 0:
            return value
    return None


def recommended_ceiling(floor: int) -> int:
    """Smallest power-of-two window that clears ``floor`` (64,000 → 65,536).

    The number the fix command tells the operator to write. Power-of-two so it
    matches what the shipped seeds already ask for and what llama-server budgets
    comfortably.
    """
    size = 1024
    while size < floor:
        size *= 2
    return size


@dataclass(frozen=True)
class AnchorWindow:
    """What the anchor actually resolves to, and whether Hermes can use it."""

    model: str
    """The anchor model id Hermes dispatches with (e.g. ``hal0/agent``)."""

    slot: str | None
    """Slot the gateway is actually serving it from; ``None`` when unprovable."""

    effective: int | None
    """Effective window as advertised to Hermes; ``None`` when unknown."""

    ceiling: int | None
    """The slot's configured ``[model].context_size``; ``None`` when unset."""

    floor: int
    """Hermes' ``MINIMUM_CONTEXT_LENGTH``."""

    floor_source: str
    """``"hermes"`` when read live, else ``"fallback:<why>"``."""

    slots_dir: Path = SLOTS_DIR

    endpoint: str = ""
    """Where the window was asked for — named in the ``unknown`` message."""

    @property
    def slot_path(self) -> Path | None:
        if not self.slot:
            return None
        return Path(self.slots_dir) / f"{self.slot}.toml"

    @property
    def verdict(self) -> str:
        """``ok`` | ``below_floor`` | ``unknown``.

        ``unknown`` means *no evidence*, and no evidence is neither a pass nor
        a failure — callers must report it as neither (the probe skips, doctor
        warns). Reporting it as a pass is how #1831 shipped, and doing it on
        the detector for #1827 would hide the very defect it exists to catch.
        """
        if self.effective is None:
            return "unknown"
        return "below_floor" if self.effective < self.floor else "ok"

    @property
    def ceiling_is_binding(self) -> bool:
        """Is the SLOT ceiling what holds the window down (vs the model)?"""
        return (
            self.ceiling is not None
            and self.effective is not None
            and self.ceiling <= self.effective
        )

    @property
    def fix_command(self) -> str:
        """The exact command that repairs a ceiling-bound anchor.

        Empty when the serving slot could not be proven — a command naming a
        slot that may not exist is worse than no command.
        """
        if not self.slot:
            return ""
        target = recommended_ceiling(self.floor)
        return f"hal0 slot edit {self.slot} --ctx-size {target} && hal0 slot restart {self.slot}"

    def message(self) -> str:
        """One operator-facing line: both numbers, the slot, and what to run.

        Written to be actionable with no source reading: which slot, its
        configured ceiling, the floor it is under, and the command to run.
        """
        where = f" at {self.endpoint}" if self.endpoint else ""
        if self.verdict == "unknown":
            return (
                f"anchor {self.model!r} advertises no context window{where} right now — "
                f"cannot check it against Hermes' {self.floor:,}-token floor, so this is "
                f"NOT a pass: re-run once a chat model is loaded"
            )
        if self.verdict == "ok":
            return (
                f"anchor {self.model!r} ({self._slot_note}) resolves to "
                f"{self.effective:,} tokens ≥ Hermes' {self.floor:,} floor"
            )
        floor_note = "" if self.floor_source == "hermes" else " (hal0's pinned copy of it)"
        head = (
            f"anchor {self.model!r} resolves to {self._slot_note} with an effective context "
            f"window of {self.effective:,} tokens, below the {self.floor:,} tokens Hermes "
            f"requires{floor_note} — Hermes raises rather than degrading, so EVERY turn "
            f"will fail until this is raised."
        )
        if self.slot is None:
            return (
                f"{head} hal0 could not prove which slot is serving it, so it cannot name "
                f"the ceiling to raise — run `hal0 slot list` and raise the window of the "
                f"slot behind {self.model!r} to at least {self.floor:,}."
            )
        if self.ceiling_is_binding:
            return (
                f"{head} The limit is this slot's own configured ceiling: "
                f"[model].context_size = {self.ceiling:,} in {self.slot_path}. "
                f"Fix: {self.fix_command}"
            )
        ceiling_note = (
            f"the slot ceiling ({self.ceiling:,} in {self.slot_path}) is not the limit; "
            if self.ceiling is not None
            else f"no ceiling is set in {self.slot_path}; "
        )
        return (
            f"{head} The limit is the model behind the slot — {ceiling_note}"
            f"the model itself only advertises {self.effective:,}. Point slot "
            f"{self.slot!r} at a model whose window is at least {self.floor:,} "
            f"(`hal0 slot edit {self.slot} --model <model-id>`), or set "
            f"defaults.context_size on the current model if it really supports more."
        )

    @property
    def _slot_note(self) -> str:
        return f"slot {self.slot!r}" if self.slot else "an unidentified slot"


def resolve_anchor_window(
    model_name: str,
    *,
    entry: Mapping[str, Any] | None = None,
    catalog: Sequence[Mapping[str, Any]] | None = None,
    contexts: Mapping[str, int] | None = None,
    floor: int = HERMES_MINIMUM_CONTEXT_LENGTH,
    floor_source: str = "fallback:not-probed",
    slots_dir: Path = SLOTS_DIR,
    endpoint: str = "",
) -> AnchorWindow:
    """Resolve the anchor's effective window + configured ceiling into a verdict.

    ``entry`` is the authoritative input: the ``GET /v1/models/{id}`` row for
    the anchor id itself. That route resolves the routing fallback chain the
    same way chat dispatch does and carries the resulting ``context_length``,
    so it answers "what window will Hermes actually get?" rather than "what
    window does a slot spelled like the anchor have?".

    ``catalog`` is the ``GET /v1/models`` list, used only to name the slot the
    entry came from (see :func:`serving_slot`).

    ``contexts`` (the id → ``context_length`` map) is honoured for an EXACT id
    match only — never under the spelled alias. On ct152 the anchor
    ``hal0/agent`` resolves through the fallback chain to the ``brain`` slot's
    32,768-token window while ``/v1/models`` lists no ``agent`` row at all; an
    alias lookup there returns ``None`` and the caller cannot tell "fine" from
    "cannot see", which is exactly how a broken box read green.

    Pure: every input is injected, so both the ct150 shape (ceiling 4096 under
    a 96000-token model) and the ct152 shape are reproducible without a box.
    """
    slot = serving_slot(model_name, entry=entry, catalog=catalog, slots_dir=slots_dir)
    effective = _entry_context(entry)
    if effective is None and contexts:
        effective = _positive_int(contexts.get(model_name))
    ceiling = read_slot_ceiling(slot, slots_dir=slots_dir) if slot else None
    return AnchorWindow(
        model=model_name,
        slot=slot,
        effective=effective,
        ceiling=ceiling,
        floor=floor,
        floor_source=floor_source,
        slots_dir=Path(slots_dir),
        endpoint=endpoint,
    )


def _entry_context(entry: Mapping[str, Any] | None) -> int | None:
    if not isinstance(entry, Mapping):
        return None
    return _positive_int(entry.get("context_length"))


def _positive_int(value: Any) -> int | None:
    if isinstance(value, bool) or not isinstance(value, int) or value <= 0:
        return None
    return value
