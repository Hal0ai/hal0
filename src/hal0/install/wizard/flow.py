"""The setup screens as a state machine. No terminal input or output.

`press` takes a state and a key and returns a new state; `view` describes what
to draw. Each screen has one small function that builds its rows and one that
handles Enter, collected in `_SCREENS`, so a new screen is one new entry. The
only outside read is the folder check, which looks at the filesystem.
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
    folder_space,
    gpu_cursor,
    gpu_enter,
    gpu_intro,
    gpu_rows,
    gpu_title,
    review_enter,
    review_intro,
    review_rows,
    type_cursor,
    type_enter,
    type_rows,
    validate_folder,
)
from .flow_custom import (
    accept,
    assistant_rows,
    commit_field,
    commit_token,
    jobs_rows,
    jobs_space,
    models_cursor,
    models_enter,
    models_question,
    models_rows,
    models_title,
    network_rows,
    space_field,
    storage_rows,
)
from .flow_model import (
    SAVE_COPY_KEY,
    SAVE_FIELD,
    TOKEN_FIELD,
    Outcome,
    Row,
    Screen,
    State,
    View,
    current_type,
    fixed_override,
    gpu_consent,
    key_of,
    row_help,
    save_field_open,
    secret_field_open,
)

__all__ = [
    "SAVE_COPY_KEY",
    "Outcome",
    "Row",
    "Screen",
    "State",
    "View",
    "chosen",
    "outcome",
    "press",
    "save_field_open",
    "secret",
    "secret_field_open",
    "sequence",
    "start",
    "validate_folder",
    "view",
]

_HINT_CHOICE = "Up/Down move, Enter choose, b back, ? explain, q quit"
_HINT_GO_ON = "Enter go on, b back, ? explain, q quit"
_HINT_ROWS = "Up/Down move, Space change, Enter go on, b back, ? explain, q quit"
_HINT_REVIEW = "Enter start the install, b back, s save a copy, q quit"
_HINT_STOP = "Enter or q to leave"
_HINT_FIELD = "Enter confirm, Enter on an empty line cancels"
_HINT_TOKEN = (
    "Type or paste the token (nothing is shown), Enter confirm, Enter on an empty line skips"
)
_CUSTOM = (Screen.JOBS, Screen.MODELS, Screen.STORAGE, Screen.NETWORK, Screen.ASSISTANT)
_CHOICE_SCREENS = (Screen.GPU, Screen.TYPE, Screen.FOLDER, Screen.MODELS)


# ---------------------------------------------------------------- wiring


class _Spec(NamedTuple):
    title: Callable[[State], str]
    intro: Callable[[State], tuple[str, ...]]
    rows: Callable[[State], tuple[Row, ...]]
    cursor: Callable[[State], int]
    enter: Callable[[State], State]
    question: Callable[[State], str]
    space: Callable[[State], State] = lambda s: s


def _no_cursor(s: State) -> int:
    return 0


def _gpu_question(s: State) -> str:
    return "cpu_only" if gpu_consent(s) else ""


def _fields_screen(
    title: str, rows: Callable[[State], tuple[Row, ...]], cursor: Callable[[State], int]
) -> _Spec:
    return _Spec(
        lambda s: title, lambda s: (), rows, cursor, accept, lambda s: "", space_field(rows)
    )


_SCREENS: dict[Screen, _Spec] = {
    Screen.GPU: _Spec(gpu_title, gpu_intro, gpu_rows, gpu_cursor, gpu_enter, _gpu_question),
    Screen.TYPE: _Spec(
        lambda s: s.catalog.questions["setup_type"].title,
        lambda s: facts_lines(s.facts),
        type_rows,
        type_cursor,
        type_enter,
        lambda s: "setup_type",
    ),
    Screen.FOLDER: _Spec(
        lambda s: s.catalog.questions["model_store"].title,
        folder_intro,
        folder_rows,
        _no_cursor,
        folder_enter,
        lambda s: "model_store",
        folder_space,
    ),
    Screen.REVIEW: _Spec(
        lambda s: "Ready to install",
        review_intro,
        review_rows,
        _no_cursor,
        review_enter,
        lambda s: "",
    ),
    Screen.JOBS: _Spec(
        lambda s: "What this box does",
        lambda s: (),
        jobs_rows,
        _no_cursor,
        accept,
        lambda s: "",
        jobs_space,
    ),
    Screen.MODELS: _Spec(
        models_title, lambda s: (), models_rows, models_cursor, models_enter, models_question
    ),
    Screen.STORAGE: _fields_screen("Storage", storage_rows, _no_cursor),
    Screen.NETWORK: _fields_screen("Network and access", network_rows, _no_cursor),
    Screen.ASSISTANT: _fields_screen("Assistant and memory", assistant_rows, _no_cursor),
}
_FIELDS: dict[str, Callable[[State, str], State]] = {
    "folder": commit_folder,
    SAVE_FIELD: commit_save,
    TOKEN_FIELD: commit_token,
}


def sequence(install_type: str, facts: Facts) -> tuple[Screen, ...]:
    """The screens for an install type, in order."""
    body = (
        (Screen.TYPE, *_CUSTOM, Screen.REVIEW)
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
    moved = replace(s, screen=screen, editing=None, field="", error=None, step=0)
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
        if not text and s.field != TOKEN_FIELD:
            return replace(s, editing=None, field="", error=None)
        return _FIELDS.get(s.field, commit_field)(s, text)
    if key == "BACKSPACE":
        return replace(s, editing=text[:-1], error=None)
    if key == "SPACE":
        key = " "
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
        return replace(s, editing="", field=SAVE_FIELD)
    if key not in ("SPACE", "UP", "DOWN", "ENTER"):
        return s
    spec = _SCREENS[s.screen]
    rows = spec.rows(s)
    # Nothing to move to, act on or choose: the key changes nothing.
    if not rows and (key != "ENTER" or s.screen in _CHOICE_SCREENS):
        return s
    if key == "SPACE":
        return spec.space(s)
    if key in ("UP", "DOWN"):
        if all(r.kind == "choice" and r.state == "fixed" for r in rows):
            return s
        last = len(rows) - 1
        return replace(s, cursor=min(last, max(0, s.cursor + (1 if key == "DOWN" else -1))))
    return _settle(spec.enter(s))


def _position(state: State) -> str:
    screens = tuple(x for x in _screens(state) if x is not Screen.GPU)
    if state.screen is Screen.GPU:
        return ""
    custom = tuple(x for x in screens if x in _CUSTOM)
    if custom and state.screen is Screen.REVIEW:
        return "Review"
    if custom and state.screen in custom:
        return f"Customize {custom.index(state.screen) + 1} of {len(custom)}"
    return f"{screens.index(state.screen) + 1} of {3 if custom else len(screens)}"


def _hint(state: State) -> str:
    if state.editing is not None:
        return _HINT_TOKEN if secret_field_open(state) else _HINT_FIELD
    if state.screen is Screen.REVIEW:
        return _HINT_REVIEW
    if state.screen is Screen.GPU and not gpu_consent(state):
        return _HINT_STOP
    hint = _HINT_CHOICE if state.screen in _CHOICE_SCREENS else _HINT_ROWS
    if all(r.kind == "info" for r in _SCREENS[state.screen].rows(state)):
        hint = _HINT_GO_ON
    return hint.replace("b back, ", "") if state.screen is _screens(state)[0] else hint


def view(state: State) -> View:
    """What to draw for this state."""
    spec = _SCREENS[state.screen]
    intro = spec.intro(state)
    rows = spec.rows(state)
    if state.show_help:
        row = rows[state.cursor] if state.cursor < len(rows) else None
        text = row_help(state, row, spec.question(state))
        if text:
            intro = (*intro, text)
    return View(
        state.screen,
        spec.title(state),
        _position(state),
        intro,
        rows,
        state.cursor,
        "" if secret_field_open(state) else state.editing,
        state.error,
        _hint(state),
    )


def outcome(state: State) -> Outcome:
    return state.status


def chosen(state: State) -> dict[str, object]:
    """The answers the person confirmed, as flat dotted keys."""
    return dict(state.answers)


def secret(state: State) -> str | None:
    """A typed secret, kept apart from the answers; none on these screens."""
    return state.token
