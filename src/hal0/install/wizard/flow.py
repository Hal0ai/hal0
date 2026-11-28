"""The setup screens as a state machine. Pure: no input or output.

`press` takes a state and a key and returns a new state; `view` describes what
to draw. Each screen has one small function that builds its rows and one that
handles Enter, collected in `_SCREENS`, so a new screen is one new entry.
"""

from __future__ import annotations

from collections.abc import Callable, Sequence
from dataclasses import replace
from typing import NamedTuple

from .catalog import Catalog
from .document import Override
from .facts import Facts
from .flow_basic import (
    commit_folder,
    commit_save,
    facts_lines,
    folder_enter,
    folder_intro,
    folder_rows,
    gpu_cursor,
    gpu_enter,
    gpu_intro,
    gpu_rows,
    gpu_title,
    review_enter,
    review_rows,
    type_cursor,
    type_enter,
    type_rows,
    validate_folder,
)
from .flow_model import (
    Outcome,
    Row,
    Screen,
    State,
    View,
    current_type,
    fixed_override,
    gpu_consent,
    key_of,
)

__all__ = [
    "Outcome",
    "Row",
    "Screen",
    "State",
    "View",
    "chosen",
    "outcome",
    "press",
    "secret",
    "sequence",
    "start",
    "validate_folder",
    "view",
]

_HINT = "Up/Down move, Enter choose, b back, q quit, ? help"
_HINT_FIELD = "Type, Backspace deletes, Enter confirms, Enter on an empty field cancels"


# ---------------------------------------------------------------- wiring


class _Spec(NamedTuple):
    title: Callable[[State], str]
    intro: Callable[[State], tuple[str, ...]]
    rows: Callable[[State], tuple[Row, ...]]
    cursor: Callable[[State], int]
    enter: Callable[[State], State]
    question: str


def _no_cursor(s: State) -> int:
    return 0


_SCREENS: dict[Screen, _Spec] = {
    Screen.GPU: _Spec(gpu_title, gpu_intro, gpu_rows, gpu_cursor, gpu_enter, "cpu_only"),
    Screen.TYPE: _Spec(
        lambda s: s.catalog.questions["setup_type"].title,
        lambda s: facts_lines(s.facts),
        type_rows,
        type_cursor,
        type_enter,
        "setup_type",
    ),
    Screen.FOLDER: _Spec(
        lambda s: s.catalog.questions["model_store"].title,
        folder_intro,
        folder_rows,
        _no_cursor,
        folder_enter,
        "model_store",
    ),
    Screen.REVIEW: _Spec(
        lambda s: "Ready to install",
        lambda s: (),
        review_rows,
        _no_cursor,
        review_enter,
        "",
    ),
}
_FIELDS: dict[str, Callable[[State, str], State]] = {"folder": commit_folder, "save": commit_save}


def sequence(install_type: str, facts: Facts) -> tuple[Screen, ...]:
    """The screens for an install type, in order."""
    # Customize has no screens of its own yet: it goes straight to the review.
    body = (
        (Screen.TYPE, Screen.REVIEW)
        if install_type == "custom"
        else (Screen.TYPE, Screen.FOLDER, Screen.REVIEW)
    )
    return body if facts.gpu_outcome == "usable" else (Screen.GPU, *body)


def _screens(s: State) -> tuple[Screen, ...]:
    screens = sequence(current_type(s), s.facts)
    fixed = fixed_override(s, key_of(s, "cpu_only"))
    if gpu_consent(s) and fixed is not None and fixed.value is True:
        return tuple(x for x in screens if x is not Screen.GPU)
    return screens


def _enter(s: State, screen: Screen) -> State:
    moved = replace(s, screen=screen, editing=None, field="", error=None)
    return replace(moved, cursor=_SCREENS[screen].cursor(moved))


def _advance(s: State) -> State:
    screens = _screens(s)
    return _enter(s, screens[screens.index(s.screen) + 1])


def _back(s: State) -> State:
    screens = _screens(s)
    index = screens.index(s.screen)
    return s if index == 0 else _enter(s, screens[index - 1])


def start(
    catalog: Catalog,
    facts: Facts,
    *,
    saved: dict[str, object] | None = None,
    given: dict[str, object] | None = None,
    over: Sequence[Override] = (),
) -> State:
    """The first screen, with every value prefilled from the layers."""
    s = State(catalog, facts, saved, given, tuple(over), {}, Screen.TYPE)
    return _enter(s, _screens(s)[0])


def _edit(s: State, key: str) -> State:
    text = s.editing or ""
    if key == "ENTER":
        if not text:
            return replace(s, editing=None, field="", error=None)
        return _FIELDS[s.field](s, text)
    if key == "BACKSPACE":
        return replace(s, editing=text[:-1], error=None)
    if len(key) == 1:
        return replace(s, editing=text + key, error=None)
    return s


def _settle(s: State) -> State:
    """Move on when the handler asked for it."""
    return _advance(replace(s, proceed=False)) if s.proceed else s


def press(state: State, key: str) -> State:
    """The state after one key."""
    if state.status is not Outcome.RUNNING:
        return state
    if key == "EOF":
        return replace(state, status=Outcome.QUIT)
    showing = state.show_help
    s = replace(state, show_help=False)
    if s.editing is not None:
        return _settle(_edit(s, key))
    s = replace(s, error=None)
    if key == "?":
        return replace(s, show_help=not showing)
    if key == "q":
        return replace(s, status=Outcome.QUIT)
    if key == "b":
        return _back(s)
    if key == "s" and s.screen is Screen.REVIEW:
        return replace(s, editing="", field="save")
    if key in ("UP", "DOWN"):
        last = len(_SCREENS[s.screen].rows(s)) - 1
        return replace(s, cursor=min(last, max(0, s.cursor + (1 if key == "DOWN" else -1))))
    if key == "ENTER":
        return _settle(_SCREENS[s.screen].enter(s))
    return s


def view(state: State) -> View:
    """What to draw for this state."""
    spec = _SCREENS[state.screen]
    intro = spec.intro(state)
    question = state.catalog.questions.get(spec.question)
    if state.show_help and question is not None and question.help:
        intro = (*intro, question.help)
    screens = tuple(x for x in _screens(state) if x is not Screen.GPU)
    position = (
        "" if state.screen is Screen.GPU else f"{screens.index(state.screen) + 1} of {len(screens)}"
    )
    return View(
        state.screen,
        spec.title(state),
        position,
        intro,
        spec.rows(state),
        state.cursor,
        state.editing,
        state.error,
        _HINT_FIELD if state.editing is not None else _HINT,
    )


def outcome(state: State) -> Outcome:
    return state.status


def chosen(state: State) -> dict[str, object]:
    """The answers the person confirmed, as flat dotted keys."""
    return dict(state.answers)


def secret(state: State) -> str | None:
    """A typed secret, kept apart from the answers; none on these screens."""
    return state.token
