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

Deliberately read-only. Rewriting an operator's slot ceiling during an update is
a config mutation nobody has approved yet and is tracked as the other half of
#1867; :func:`resolve_anchor_window` is the resolver that repair would be built
on when it is decided (it already names the slot, its ceiling, and the target
value the repair would need to write).
"""

from __future__ import annotations

import subprocess  # nosec B404 — asks the Hermes venv python for its own constant
import tomllib
from collections.abc import Callable, Mapping
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
    """Slot alias behind an anchor model id (``hal0/agent`` → ``agent``).

    A bare id (a slot pinned directly, e.g. under
    ``HAL0_HERMES_LIVE_RESOLVE=0``) is already the alias.
    """
    name = (model_name or "").strip()
    if name.startswith(_VIRTUAL_PREFIX):
        return name[len(_VIRTUAL_PREFIX) :]
    return name


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

    slot: str
    """Slot alias behind it (e.g. ``agent``)."""

    effective: int | None
    """Effective window as advertised to Hermes; ``None`` when unknown."""

    ceiling: int | None
    """The slot's configured ``[model].context_size``; ``None`` when unset."""

    floor: int
    """Hermes' ``MINIMUM_CONTEXT_LENGTH``."""

    floor_source: str
    """``"hermes"`` when read live, else ``"fallback:<why>"``."""

    slots_dir: Path = SLOTS_DIR

    @property
    def slot_path(self) -> Path:
        return Path(self.slots_dir) / f"{self.slot}.toml"

    @property
    def verdict(self) -> str:
        """``ok`` | ``below_floor`` | ``unknown``.

        ``unknown`` is a real answer, not a soft failure: with no advertised
        window (nothing loaded, gateway unreachable) there is no evidence
        either way, and claiming a failure on no evidence is how a preflight
        becomes noise operators learn to ignore.
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
        """The exact command that repairs a ceiling-bound anchor."""
        target = recommended_ceiling(self.floor)
        return f"hal0 slot edit {self.slot} --ctx-size {target} && hal0 slot restart {self.slot}"

    def message(self) -> str:
        """One operator-facing line: both numbers, the slot, and what to run.

        Written to be actionable with no source reading: which slot, its
        configured ceiling, the floor it is under, and the command to run.
        """
        if self.verdict == "unknown":
            return (
                f"anchor {self.model!r} (slot {self.slot!r}) advertises no context window "
                f"right now — cannot check it against Hermes' {self.floor:,}-token floor"
            )
        if self.verdict == "ok":
            return (
                f"anchor {self.model!r} (slot {self.slot!r}) resolves to "
                f"{self.effective:,} tokens ≥ Hermes' {self.floor:,} floor"
            )
        floor_note = "" if self.floor_source == "hermes" else " (hal0's pinned copy of it)"
        head = (
            f"anchor {self.model!r} resolves to slot {self.slot!r} with an effective context "
            f"window of {self.effective:,} tokens, below the {self.floor:,} tokens Hermes "
            f"requires{floor_note} — Hermes raises rather than degrading, so EVERY turn "
            f"will fail until this is raised."
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


def resolve_anchor_window(
    model_name: str,
    *,
    contexts: Mapping[str, int],
    floor: int = HERMES_MINIMUM_CONTEXT_LENGTH,
    floor_source: str = "fallback:not-probed",
    slots_dir: Path = SLOTS_DIR,
) -> AnchorWindow:
    """Resolve the anchor's effective window + configured ceiling into a verdict.

    ``contexts`` is the gateway's ``/v1/models`` id → ``context_length`` map
    (:func:`hal0.agents.hermes_provision._fetch_model_contexts`) — deliberately
    the SAME surface Hermes gates on, so this cannot pass while Hermes refuses.
    The id is looked up as given first, then under the bare slot alias, because
    the gateway advertises chat slots by alias (``agent``) while hermes'
    ``model.default`` is normally the virtual (``hal0/agent``).

    Pure: every input is injected, so the ct150 shape (ceiling 4096 under a
    96000-token model) is reproducible in a test without a box.
    """
    slot = anchor_slot_name(model_name)
    effective = _lookup_context(contexts, model_name, slot)
    ceiling = read_slot_ceiling(slot, slots_dir=slots_dir)
    return AnchorWindow(
        model=model_name,
        slot=slot,
        effective=effective,
        ceiling=ceiling,
        floor=floor,
        floor_source=floor_source,
        slots_dir=Path(slots_dir),
    )


def _lookup_context(contexts: Mapping[str, int], model_name: str, slot: str) -> int | None:
    for key in (model_name, slot):
        if not key:
            continue
        value = contexts.get(key)
        if isinstance(value, bool):
            continue
        if isinstance(value, int) and value > 0:
            return value
    return None
