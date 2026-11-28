"""State, views and the values every setup screen shares. Reads nothing outside the state."""

from __future__ import annotations

import dataclasses
from collections.abc import Sequence
from dataclasses import dataclass, replace
from enum import StrEnum
from typing import NamedTuple

from .catalog import Catalog, Component, ModelRow
from .document import Document, Override, effective, get
from .facts import Disk, Facts, satisfied


class Screen(StrEnum):
    GPU = "gpu"
    TYPE = "type"
    FOLDER = "folder"
    JOBS = "jobs"
    MODELS = "models"
    STORAGE = "storage"
    NETWORK = "network"
    ASSISTANT = "assistant"
    REVIEW = "review"


class Outcome(StrEnum):
    RUNNING = "running"
    DONE = "done"
    QUIT = "quit"
    STOPPED = "stopped"


@dataclass(frozen=True)
class Row:
    id: str
    label: str
    detail: str
    kind: str
    value: object
    state: str
    note: str


@dataclass(frozen=True)
class View:
    screen: Screen
    title: str
    position: str
    intro: tuple[str, ...]
    rows: tuple[Row, ...]
    cursor: int
    editing: str | None
    error: str | None
    hint: str


@dataclass(frozen=True)
class State:
    catalog: Catalog
    facts: Facts
    saved: dict[str, object] | None
    given: dict[str, object] | None
    over: tuple[Override, ...]
    answers: dict[str, object]
    screen: Screen
    cursor: int = 0
    editing: str | None = dataclasses.field(default=None, repr=False)
    field: str = ""
    error: str | None = None
    show_help: bool = False
    status: Outcome = Outcome.RUNNING
    token: str | None = dataclasses.field(default=None, repr=False)
    proceed: bool = False
    step: int = 0


DEFAULT_HOST = "hal0.local"
DEFAULT_PORT = 8080
SIZED_MODEL = "a chat model sized for this box"


def make_row(
    id: str,
    label: str,
    detail: str = "",
    kind: str = "info",
    value: object = None,
    state: str = "open",
    note: str = "",
) -> Row:
    return Row(id, label, detail, kind, value, state, note)


def fmt_gb(value: float) -> str:
    """A size in GB: a whole number from 10 up, else one decimal unless it is whole."""
    if value >= 10:
        return f"{value:.0f}"
    tenths = round(value, 1)
    if tenths >= 10 or tenths.is_integer():
        return f"{tenths:.0f}"
    return f"{tenths:.1f}"


def disk_of(path: str, disks: Sequence[Disk]) -> Disk | None:
    owners = [d for d in disks if path == d.mount or path.startswith(d.mount.rstrip("/") + "/")]
    return max(owners, key=lambda d: len(d.mount), default=None)


def set_by(fixed: Override) -> str:
    """The note on a row whose key a flag or variable sets."""
    return f"set by {fixed.source}"


def short_on_space(disk: Disk | None, total: float | None) -> bool:
    """Whether a disk is known to have less free space than the download."""
    return disk is not None and total is not None and disk.free_gb < total


# ---------------------------------------------------------------- values


def effective_doc(s: State) -> Document:
    return effective(
        s.catalog,
        chosen=s.answers,
        saved=s.saved,
        given=s.given,
        over=s.over,
        hal0_version="",
        now="",
        install_state=s.facts.install_state,
    )


def key_of(s: State, question: str) -> str:
    return s.catalog.questions[question].answer_key


def maybe_value(s: State, doc: Document, question: str) -> object | None:
    q = s.catalog.questions.get(question)
    return None if q is None else get(doc, q.answer_key)


def fixed_override(s: State, key: str) -> Override | None:
    named = [o for o in s.over if o.key == key]
    return named[-1] if named else None


def record_answer(s: State, key: str, value: object) -> State:
    if fixed_override(s, key) is not None:
        return s
    return replace(s, answers={**s.answers, key: value})


def row_help(s: State, row: Row | None, question: str) -> str:
    """The help of a highlighted job or field, else the help of the screen's question."""
    if row is not None and row.kind in ("check", "text"):
        q = s.catalog.questions.get(row.id)
        if q is not None:
            return q.help
        c = next((c for c in s.catalog.components if c.id == row.id), None)
        if c is not None:
            return c.help
    q = s.catalog.questions.get(question)
    return "" if q is None else q.help


def current_type(s: State) -> str:
    value = get(effective_doc(s), key_of(s, "setup_type"))
    return value if isinstance(value, str) else "recommended"


# ------------------------------------------------------------- the plan


class Pick(NamedTuple):
    label: str
    gb: float | None
    auto: bool


def fits_box(row: ModelRow, facts: Facts) -> bool:
    if row.min_gpu_pool_gb is not None and (
        facts.gpu_pool_gb is None or facts.gpu_pool_gb < row.min_gpu_pool_gb
    ):
        return False
    return row.min_ram_gb is None or (facts.ram_gb is not None and facts.ram_gb >= row.min_ram_gb)


def auto_row(s: State) -> ModelRow | None:
    """The first starter row that fits this box: what `auto` resolves to."""
    return next(
        (r for r in s.catalog.model_ladder if r.role == "starter" and fits_box(r, s.facts)), None
    )


def pick_model(s: State, doc: Document) -> Pick | None:
    starter = maybe_value(s, doc, "starter_model")
    ladder = [r for r in s.catalog.model_ladder if r.role == "starter"]
    if not ladder or starter in (None, "none"):
        return None
    if starter == "auto":
        row = auto_row(s)
        return (
            Pick(SIZED_MODEL, None, False)
            if row is None
            else Pick(row.label, row.download_gb, True)
        )
    named = next((r for r in ladder if r.model == starter), None)
    if named is None:
        return Pick(str(starter), None, False)
    return Pick(named.label, named.download_gb, False)


def model_requirement(row: ModelRow, facts: Facts) -> tuple[str, str]:
    """Whether a model row can be picked here (`open` or `unavailable`) and why not."""
    checks = (
        ("GPU memory", row.min_gpu_pool_gb, facts.gpu_pool_gb),
        ("memory", row.min_ram_gb, facts.ram_gb),
    )
    unchecked = ""
    for what, need, have in checks:
        if need is None:
            continue
        text = f"needs {fmt_gb(need)} GB of {what}"
        if have is None:
            unchecked = unchecked or f"{text}; could not be checked on this box"
        elif have < need:
            return "unavailable", f"{text}; this box has {fmt_gb(have)}"
    return "open", unchecked


def jobs_wanted(s: State, doc: Document) -> list[Component]:
    """Jobs the answers turn on, whether or not this box can run them."""
    return [c for c in s.catalog.components if get(doc, c.answer_key) == c.on]


def failing_need(s: State, c: Component) -> str | None:
    """The first need of a job that is known to fail on this box."""
    return next((n for n in c.needs if satisfied(n, s.facts) is False), None)


def jobs_on(s: State, doc: Document) -> list[Component]:
    return [c for c in jobs_wanted(s, doc) if failing_need(s, c) is None]


def download_items(s: State, doc: Document) -> list[tuple[str, float | None]]:
    items: list[tuple[str, float | None]] = [
        (f"{c.label} ({fmt_gb(c.download_gb)} GB)", c.download_gb)
        for c in jobs_on(s, doc)
        if c.download_gb is not None
    ]
    pick = pick_model(s, doc)
    if pick is not None:
        text = pick.label if pick.gb is None else f"{pick.label} ({fmt_gb(pick.gb)} GB)"
        items.append((text, pick.gb))
    return items


def download_total(s: State) -> float | None:
    sizes = [gb for _, gb in download_items(s, effective_doc(s)) if gb is not None]
    return sum(sizes) if sizes else None


def gpu_consent(s: State) -> bool:
    return s.facts.gpu_outcome == "cpu_only_needs_consent"


def go_on(s: State) -> State:
    """Ask the driver to move to the next screen once this key is handled."""
    return replace(s, proceed=True)
