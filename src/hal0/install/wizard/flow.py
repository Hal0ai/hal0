"""The setup screens as a state machine. Pure: no input or output.

`press` takes a state and a key and returns a new state; `view` describes what
to draw. Each screen has one small function that builds its rows and one that
handles Enter, collected in `_SCREENS`, so a new screen is one new entry.
"""

from __future__ import annotations

import math
import os
import posixpath
from collections.abc import Callable, Sequence
from dataclasses import dataclass, replace
from enum import StrEnum
from typing import NamedTuple

from .catalog import Catalog, Component, ModelRow
from .document import ADMIN_KEY, Document, Override, effective, get
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
    editing: str | None = None
    field: str = ""
    error: str | None = None
    show_help: bool = False
    status: Outcome = Outcome.RUNNING
    token: str | None = None


_DOWNLOAD_MBIT = 100
_DEFAULT_HOST = "hal0.local"
_DEFAULT_PORT = 8080
_NO_DOWNLOADS = "nothing yet; models are added after install"
_SIZED_MODEL = "a chat model sized for this box"
_HINT = "Up/Down move, Enter choose, b back, q quit, ? help"
_HINT_FIELD = "Type, Backspace deletes, Enter confirms, Enter on an empty field cancels"
_GPU_REASONS = {
    "no_device": "No graphics card was found on this box.",
    "no_kfd": "A graphics card was found, but its compute driver is not available.",
    "broken_group": "A graphics card was found, but this user is not allowed to use it.",
}
_GPU_REASON_OTHER = "The graphics card on this box cannot be used."


def _row(
    id: str,
    label: str,
    detail: str = "",
    kind: str = "info",
    value: object = None,
    state: str = "open",
    note: str = "",
) -> Row:
    return Row(id, label, detail, kind, value, state, note)


def _gb(value: float) -> str:
    return f"{value:.0f}" if float(value).is_integer() else f"{value:.1f}"


# ---------------------------------------------------------------- values


def _doc(s: State) -> Document:
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


def _key(s: State, question: str) -> str:
    return s.catalog.questions[question].answer_key


def _maybe(s: State, doc: Document, question: str) -> object | None:
    q = s.catalog.questions.get(question)
    return None if q is None else get(doc, q.answer_key)


def _fixed(s: State, key: str) -> Override | None:
    named = [o for o in s.over if o.key == key]
    return named[-1] if named else None


def _record(s: State, key: str, value: object) -> State:
    if _fixed(s, key) is not None:
        return s
    return replace(s, answers={**s.answers, key: value})


def _install_type(s: State) -> str:
    value = get(_doc(s), _key(s, "setup_type"))
    return value if isinstance(value, str) else "recommended"


# ------------------------------------------------------------- the plan


class Pick(NamedTuple):
    label: str
    gb: float | None
    auto: bool


def _fits(row: ModelRow, facts: Facts) -> bool:
    if row.min_gpu_pool_gb is not None and (
        facts.gpu_pool_gb is None or facts.gpu_pool_gb < row.min_gpu_pool_gb
    ):
        return False
    return row.min_ram_gb is None or (facts.ram_gb is not None and facts.ram_gb >= row.min_ram_gb)


def _pick(s: State, doc: Document) -> Pick | None:
    starter = _maybe(s, doc, "starter_model")
    ladder = [r for r in s.catalog.model_ladder if r.role == "starter"]
    if not ladder or starter in (None, "none"):
        return None
    if starter == "auto":
        row = next((r for r in ladder if _fits(r, s.facts)), None)
        return (
            Pick(_SIZED_MODEL, None, False)
            if row is None
            else Pick(row.label, row.download_gb, True)
        )
    named = next((r for r in ladder if r.model == starter), None)
    if named is None:
        return Pick(str(starter), None, False)
    return Pick(named.label, named.download_gb, False)


def _jobs(s: State, doc: Document) -> list[Component]:
    on = []
    for c in s.catalog.components:
        if get(doc, c.answer_key) == c.on and all(
            satisfied(n, s.facts) is not False for n in c.needs
        ):
            on.append(c)
    return on


def _items(s: State, doc: Document) -> list[tuple[str, float | None]]:
    items: list[tuple[str, float | None]] = [
        (f"{c.label} ({_gb(c.download_gb)} GB)", c.download_gb)
        for c in _jobs(s, doc)
        if c.download_gb is not None
    ]
    pick = _pick(s, doc)
    if pick is not None:
        text = pick.label if pick.gb is None else f"{pick.label} ({_gb(pick.gb)} GB)"
        items.append((text, pick.gb))
    return items


def _total(s: State) -> float | None:
    sizes = [gb for _, gb in _items(s, _doc(s)) if gb is not None]
    return sum(sizes) if sizes else None


# ------------------------------------------------------------ GPU screen


def _gpu_consent(s: State) -> bool:
    return s.facts.gpu_outcome == "cpu_only_needs_consent"


def _gpu_title(s: State) -> str:
    if _gpu_consent(s):
        return s.catalog.questions["cpu_only"].title
    return "This box cannot run hal0 yet"


def _gpu_intro(s: State) -> tuple[str, ...]:
    return (_GPU_REASONS.get(s.facts.gpu_reason or "", _GPU_REASON_OTHER),)


def _gpu_rows(s: State) -> tuple[Row, ...]:
    if not _gpu_consent(s):
        return (
            _row(
                "stop",
                "hal0 cannot continue on this box",
                "Fix this, then run the installer again.",
            ),
        )
    fixed = _fixed(s, _key(s, "cpu_only"))
    note = "" if fixed is None else f"set by {fixed.source}"
    state = "open" if fixed is None else "fixed"
    return tuple(
        _row(str(o.value).lower(), o.label, o.help, "choice", o.value, state, note)
        for o in s.catalog.questions["cpu_only"].options
    )


def _gpu_cursor(s: State) -> int:
    value = get(_doc(s), _key(s, "cpu_only"))
    values = [r.value for r in _gpu_rows(s)]
    return values.index(value) if value in values else max(0, len(values) - 1)


def _gpu_enter(s: State) -> State:
    if not _gpu_consent(s):
        return replace(s, status=Outcome.STOPPED)
    key = _key(s, "cpu_only")
    fixed = _fixed(s, key)
    value = fixed.value if fixed is not None else _gpu_rows(s)[s.cursor].value
    if value is not True:
        return replace(s, status=Outcome.STOPPED)
    return _advance(_record(s, key, True))


# ----------------------------------------------------------- TYPE screen


def _facts_lines(f: Facts) -> tuple[str, ...]:
    card = [
        x
        for x in (
            f.gpu_vendor,
            None if f.gpu_pool_gb is None else f"{_gb(f.gpu_pool_gb)} GB memory",
        )
        if x
    ]
    if f.gpu_outcome != "usable":
        card = [*card[:1], "not usable"] if card else []
    podman = {True: "running", False: "not running"}.get(f.podman_runs, "")  # type: ignore[arg-type]
    unknown = "not detected"
    return (
        "This box:",
        f"  Graphics card: {', '.join(card) or unknown}",
        f"  Memory: {unknown if f.ram_gb is None else _gb(f.ram_gb) + ' GB'}",
        f"  NPU: {'found' if f.npu_device else unknown}",
        f"  Podman: {podman or unknown}",
    )


def _type_rows(s: State) -> tuple[Row, ...]:
    q = s.catalog.questions["setup_type"]
    fixed = _fixed(s, q.answer_key)
    state = "open" if fixed is None else "fixed"
    current = _install_type(s)
    return tuple(
        _row(
            str(o.value),
            o.label,
            o.help,
            "choice",
            o.value,
            state,
            f"set by {fixed.source}" if fixed is not None and o.value == current else "",
        )
        for o in q.options
    )


def _type_cursor(s: State) -> int:
    values = [r.value for r in _type_rows(s)]
    current = _install_type(s)
    return values.index(current) if current in values else 0


def _type_enter(s: State) -> State:
    return _advance(_record(s, _key(s, "setup_type"), _type_rows(s)[s.cursor].value))


# --------------------------------------------------------- FOLDER screen


def _disk_of(path: str, disks: Sequence[Disk]) -> Disk | None:
    owners = [d for d in disks if path == d.mount or path.startswith(d.mount.rstrip("/") + "/")]
    return max(owners, key=lambda d: len(d.mount), default=None)


def _disk_row(
    id: str, path: str, disk: Disk | None, total: float | None, fixed: Override | None
) -> Row:
    note = ""
    if fixed is not None:
        note = f"set by {fixed.source}"
    elif disk is not None and total is not None and disk.free_gb < total:
        note = f"less than the {_gb(total)} GB this setup downloads"
    detail = "" if disk is None else f"{_gb(disk.free_gb)} GB free"
    return _row(id, path, detail, "choice", path, "open" if fixed is None else "fixed", note)


def _folder_rows(s: State) -> tuple[Row, ...]:
    key = _key(s, "model_store")
    fixed = _fixed(s, key)
    current = get(_doc(s), key)
    path = current if isinstance(current, str) else ""
    total = _total(s)
    here = _disk_of(path, s.facts.disks) if path else None
    rows = [_disk_row("current", path, here, total, fixed)] if path else []
    if fixed is not None:
        return tuple(rows)
    others = sorted((d for d in s.facts.disks if d != here), key=lambda d: d.free_gb, reverse=True)
    for d in others:
        rows.append(_disk_row(d.mount, posixpath.join(d.mount, "hal0/models"), d, total, None))
    rows.append(_row("other", "Type another folder...", path, "text", None))
    return tuple(rows)


def _folder_intro(s: State) -> tuple[str, ...]:
    total = _total(s)
    return () if total is None else (f"This setup will download about {_gb(total)} GB.",)


def _folder_enter(s: State) -> State:
    key = _key(s, "model_store")
    rows = _folder_rows(s)
    if _fixed(s, key) is not None:
        return _advance(s)
    row = rows[s.cursor]
    if row.id == "other":
        return replace(s, editing="", field="folder", error=None)
    return _advance(_record(s, key, row.value))


def _commit_folder(s: State, text: str) -> State:
    path, error = validate_folder(text)
    if error is not None:
        return replace(s, error=error)
    done = _record(replace(s, editing=None, field="", error=None), _key(s, "model_store"), path)
    return _advance(done)


def _check_path(text: str, what: str) -> tuple[str, str | None]:
    path = text.strip()
    if not path:
        return path, f"{what} is required"
    if any(ord(c) < 32 or ord(c) == 127 for c in path):
        return path, f"{what} has a control character"
    if not path.startswith("/"):
        return path, f"{what} must start with /"
    path = posixpath.normpath(path)
    ancestor = path
    while not os.path.exists(ancestor) and ancestor != "/":
        ancestor = posixpath.dirname(ancestor)
    if not os.path.isdir(ancestor):
        return path, f"{ancestor} is not a folder"
    return path, None


def validate_folder(text: str) -> tuple[str, str | None]:
    """The cleaned folder path and the reason it cannot be used, or None."""
    return _check_path(text, "the folder")


# --------------------------------------------------------- REVIEW screen


def _review_rows(s: State) -> tuple[Row, ...]:
    doc = _doc(s)
    jobs = _jobs(s, doc)
    items = _items(s, doc)
    pick = _pick(s, doc)
    total = _total(s)
    rows = [
        _row("install", "Installing", ", ".join(c.label for c in jobs) or "only the model server"),
        _row("downloads", "Downloads", ", ".join(t for t, _ in items) or _NO_DOWNLOADS),
    ]
    if total is not None:
        minutes = math.ceil(total * 8000 / _DOWNLOAD_MBIT / 60)
        rows.append(
            _row(
                "total",
                "Total",
                f"{_gb(total)} GB, about {minutes} min on a {_DOWNLOAD_MBIT} Mbit/s line",
            )
        )
    rows.append(_row("folder", "Model folder", _folder_text(s, doc)))
    model = "none" if pick is None else pick.label + (" (picked for this box)" if pick.auto else "")
    rows.append(_row("model", "Chat model", model))
    if s.facts.npu_device is True and any(c.id == "npu" for c in jobs):
        rows.append(_row("npu", "NPU", "set up if it passes its check"))
    rows.extend(_access_rows(s, doc))
    rows.extend(_row("warning", "Warning", w) for w in _warnings(s, doc))
    return tuple(rows)


def _folder_text(s: State, doc: Document) -> str:
    path = get(doc, _key(s, "model_store"))
    if not isinstance(path, str):
        return "unchanged"
    disk = _disk_of(path, s.facts.disks)
    return path if disk is None else f"{path} ({_gb(disk.free_gb)} GB free)"


def _access_rows(s: State, doc: Document) -> list[Row]:
    host = _maybe(s, doc, "hostname") or _DEFAULT_HOST
    port = _maybe(s, doc, "port") or _DEFAULT_PORT
    local = _maybe(s, doc, "bind_host") == "127.0.0.1"
    reach = (
        f"http://127.0.0.1:{port} from this machine only"
        if local
        else f"http://{host}:{port} from this network"
    )
    admin = {"generate": "created; shown once when the install ends", "none": "not created"}
    terminal = (
        "ON: the assistant can run commands as a root-equivalent user"
        if _maybe(s, doc, "terminal_tool") is True
        else "off"
    )
    return [
        _row("reachable", "Reachable at", reach),
        _row("admin_key", "Admin key", admin.get(str(get(doc, ADMIN_KEY)), "unchanged")),
        _row("terminal_tool", "Assistant commands", terminal),
    ]


def _warnings(s: State, doc: Document) -> list[str]:
    out = []
    if s.facts.gpu_outcome != "usable" and get(doc, _key(s, "cpu_only")) is True:
        out.append("No graphics card will be used. " + s.catalog.questions["cpu_only"].help)
    path = get(doc, _key(s, "model_store"))
    disk = _disk_of(path, s.facts.disks) if isinstance(path, str) else None
    total = _total(s)
    if disk is not None and total is not None and disk.free_gb < total:
        out.append(f"The model folder has less than the {_gb(total)} GB this setup downloads.")
    return out


def _review_enter(s: State) -> State:
    return replace(s, status=Outcome.DONE)


def _commit_save(s: State, text: str) -> State:
    path, error = _check_path(text, "the file path")
    if error is None and os.path.isdir(path):
        error = f"{path} is a folder, not a file"
    if error is not None:
        return replace(s, error=error)
    return replace(
        s, answers={**s.answers, "_save_copy_to": path}, editing=None, field="", error=None
    )


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
    Screen.GPU: _Spec(_gpu_title, _gpu_intro, _gpu_rows, _gpu_cursor, _gpu_enter, "cpu_only"),
    Screen.TYPE: _Spec(
        lambda s: s.catalog.questions["setup_type"].title,
        lambda s: _facts_lines(s.facts),
        _type_rows,
        _type_cursor,
        _type_enter,
        "setup_type",
    ),
    Screen.FOLDER: _Spec(
        lambda s: s.catalog.questions["model_store"].title,
        _folder_intro,
        _folder_rows,
        _no_cursor,
        _folder_enter,
        "model_store",
    ),
    Screen.REVIEW: _Spec(
        lambda s: "Ready to install",
        lambda s: (),
        _review_rows,
        _no_cursor,
        _review_enter,
        "",
    ),
}
_FIELDS: dict[str, Callable[[State, str], State]] = {"folder": _commit_folder, "save": _commit_save}


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
    screens = sequence(_install_type(s), s.facts)
    fixed = _fixed(s, _key(s, "cpu_only"))
    if _gpu_consent(s) and fixed is not None and fixed.value is True:
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


def press(state: State, key: str) -> State:
    """The state after one key."""
    if state.status is not Outcome.RUNNING:
        return state
    if key == "EOF":
        return replace(state, status=Outcome.QUIT)
    showing = state.show_help
    s = replace(state, show_help=False)
    if s.editing is not None:
        return _edit(s, key)
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
        return _SCREENS[s.screen].enter(s)
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
