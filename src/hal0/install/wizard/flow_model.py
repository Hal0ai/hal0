"""State, views and the values every setup screen shares. Pure: no input or output."""

from __future__ import annotations

from dataclasses import dataclass, replace
from enum import StrEnum
from typing import NamedTuple

from .catalog import Catalog, Component, ModelRow
from .document import Document, Override, effective, get
from .facts import Facts, satisfied


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
    editing: str | None = None
    field: str = ""
    error: str | None = None
    show_help: bool = False
    status: Outcome = Outcome.RUNNING
    token: str | None = None
    proceed: bool = False


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
    return f"{value:.0f}" if float(value).is_integer() else f"{value:.1f}"


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


def pick_model(s: State, doc: Document) -> Pick | None:
    starter = maybe_value(s, doc, "starter_model")
    ladder = [r for r in s.catalog.model_ladder if r.role == "starter"]
    if not ladder or starter in (None, "none"):
        return None
    if starter == "auto":
        row = next((r for r in ladder if fits_box(r, s.facts)), None)
        return (
            Pick(SIZED_MODEL, None, False)
            if row is None
            else Pick(row.label, row.download_gb, True)
        )
    named = next((r for r in ladder if r.model == starter), None)
    if named is None:
        return Pick(str(starter), None, False)
    return Pick(named.label, named.download_gb, False)


def jobs_on(s: State, doc: Document) -> list[Component]:
    on = []
    for c in s.catalog.components:
        if get(doc, c.answer_key) == c.on and all(
            satisfied(n, s.facts) is not False for n in c.needs
        ):
            on.append(c)
    return on


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
