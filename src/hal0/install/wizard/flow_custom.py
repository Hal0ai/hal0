"""The five Customize screens: jobs, models, storage, network, assistant and memory.

Enter accepts a screen and goes on; Space acts on the highlighted row. Each
screen has a function for its rows and one for Enter, wired up in `flow.py`.
Reads only the state; the folder check it uses looks at the filesystem.
"""

from __future__ import annotations

import re
from collections.abc import Callable
from dataclasses import replace

from .catalog import Component, ModelRow, Question
from .document import Document, get
from .facts import satisfied
from .flow_basic import validate_folder
from .flow_model import (
    DEFAULT_HOST,
    DEFAULT_PORT,
    TOKEN_FIELD,
    TOO_SMALL,
    UNCHECKED,
    Row,
    State,
    auto_outcome,
    auto_row,
    disk_of,
    effective_doc,
    failing_need,
    fixed_override,
    fmt_gb,
    go_on,
    jobs_on,
    key_of,
    make_row,
    model_requirement,
    record_answer,
    set_by,
)

_IMAGE_JOB = "images"
_ASSISTANT_JOB = "hermes"
_MEMORY_JOB = "memory"
_NPU_FOUND = "found; set up if it passes its check"
_PROXMOX = "; in a Proxmox container the host has to pass it through first"
_AFTER_CHECK = "chosen after hal0 has checked this box"
_NO_MODELS = "no models to choose from yet; they are added after install"
_FIELD = "q:"
_TOKEN_TEXT = re.compile(r"[A-Za-z0-9_-]+")
_BAD_TOKEN = "a token has only letters, digits, _ and -"
_LABEL = re.compile(r"[A-Za-z0-9]([A-Za-z0-9-]{0,61}[A-Za-z0-9])?")
_STEPS = (("starter", "starter_model"), ("image", "image_model"))


def _question(s: State, qid: str) -> Question | None:
    return s.catalog.questions.get(qid)


# ------------------------------------------------------------ JOBS screen


def _job_state(s: State, c: Component) -> tuple[str, str]:
    fixed = fixed_override(s, c.answer_key)
    if fixed is not None:
        return "fixed", set_by(fixed)
    need = failing_need(s, c)
    if need is not None:
        reason = s.catalog.reasons[need]
        if need == "npu_device" and s.facts.container == "lxc-unprivileged":
            reason += _PROXMOX
        return "unavailable", reason
    if s.facts.npu_device is True and "npu_device" in c.needs:
        return "open", _NPU_FOUND
    if any(satisfied(n, s.facts) is None for n in c.needs):
        return "open", UNCHECKED
    return "open", ""


def jobs_rows(s: State) -> tuple[Row, ...]:
    doc = effective_doc(s)
    rows = []
    for c in s.catalog.components:
        state, note = _job_state(s, c)
        size = "" if c.download_gb is None else f"about {fmt_gb(c.download_gb)} GB"
        ticked = get(doc, c.answer_key) == c.on
        rows.append(make_row(c.id, c.label, size, "check", ticked, state, note))
    return tuple(rows)


def jobs_space(s: State) -> State:
    row = jobs_rows(s)[s.cursor]
    if row.state != "open":
        return s
    c = next(c for c in s.catalog.components if c.id == row.id)
    return _prune(record_answer(s, c.answer_key, c.off if row.value else c.on))


def _prune(s: State) -> State:
    """Drop answers whose job is off, so they are neither written nor reviewed."""
    keep = dict(s.answers)
    for job, qid in ((_ASSISTANT_JOB, "terminal_tool"), (_MEMORY_JOB, "memory_model")):
        q = _question(s, qid)
        if q is not None and _job_off(s, job):
            keep.pop(q.answer_key, None)
    return replace(s, answers=keep)


# ---------------------------------------------------------- MODELS screen


def _role(s: State) -> tuple[str, str]:
    return _STEPS[s.step]


def _image_step(s: State) -> bool:
    on = any(c.id == _IMAGE_JOB for c in jobs_on(s, effective_doc(s)))
    has_rows = any(r.role == "image" for r in s.catalog.model_ladder)
    return on and has_rows and _question(s, "image_model") is not None


def _ladder_row(s: State, row: ModelRow, fixed_note: str | None) -> Row:
    state, note = model_requirement(row, s.facts)
    if fixed_note is not None:
        state, note = "fixed", fixed_note
    size = "" if row.download_gb is None else f"{fmt_gb(row.download_gb)} GB"
    return make_row(row.model, row.label, size, "choice", row.model, state, note)


def _extra_row(s: State, q: Question, value: str, fixed_note: str | None) -> Row | None:
    """The `auto`, `none` or `recommended` row, worded by the question's options."""
    option = next((o for o in q.options if o.value == value), None)
    if option is None:
        return None
    detail = option.help
    if value == "auto":
        pick = auto_row(s)
        if pick is not None:
            size = "" if pick.download_gb is None else f" ({fmt_gb(pick.download_gb)} GB)"
            detail = pick.label + size
        else:
            detail = TOO_SMALL if auto_outcome(s) == "too_small" else _AFTER_CHECK
    state, note = ("open", "") if fixed_note is None else ("fixed", fixed_note)
    return make_row(value, option.label, detail, "choice", value, state, note)


def models_rows(s: State) -> tuple[Row, ...]:
    role, qid = _role(s)
    q = _question(s, qid)
    ladder = [r for r in s.catalog.model_ladder if r.role == role]
    if q is None or not ladder:
        return (make_row("none", q.title if q else "Models", _NO_MODELS, "info"),)
    fixed = fixed_override(s, q.answer_key)
    current = get(effective_doc(s), q.answer_key)

    def note(*values: object) -> str | None:
        if fixed is None:
            return None
        return set_by(fixed) if current in values else ""

    rows = [_ladder_row(s, r, note(r.model, [r.model])) for r in ladder]
    if role != "starter":
        return _present([_extra_row(s, q, "recommended", note([])), *rows])
    auto = _extra_row(s, q, "auto", note("auto"))
    none = _extra_row(s, q, "none", note("none"))
    return _present([auto, *rows, none])


def models_cursor(s: State) -> int:
    rows = models_rows(s)
    q = _question(s, _role(s)[1])
    current = None if q is None else get(effective_doc(s), q.answer_key)
    if s.step == 0:
        for i, r in enumerate(rows):
            if r.value == current:
                return i
        for i, r in enumerate(rows):
            if r.id == "auto":
                return i
    else:
        ids = [r.id for r in rows]
        if isinstance(current, list) and len(current) == 1 and current[0] in ids:
            return ids.index(current[0])
        if "recommended" in ids:
            return ids.index("recommended")
    return next((i for i, r in enumerate(rows) if r.state == "open"), 0)


def models_title(s: State) -> str:
    qid = "hf_token" if s.editing is not None and s.field == TOKEN_FIELD else _role(s)[1]
    q = _question(s, qid)
    return "" if q is None else q.title


def models_question(s: State) -> str:
    return "hf_token" if s.editing is not None and s.field == TOKEN_FIELD else _role(s)[1]


def _models_next(s: State) -> State:
    if s.step == 0 and _image_step(s):
        step = replace(s, step=1)
        return replace(step, cursor=models_cursor(step))
    return go_on(s)


def _image_value(row: Row) -> list[object]:
    """What an image row writes: nothing listed means the recommended set."""
    return [] if row.id == "recommended" else [row.value]


def models_enter(s: State) -> State:
    rows = models_rows(s)
    row = rows[s.cursor]
    if row.state == "unavailable":
        if any(r.state == "open" for r in rows):
            return replace(s, error=row.note)
        return _models_next(s)
    if row.kind == "info" or row.state != "open":
        return _models_next(s)
    role, qid = _role(s)
    key = key_of(s, qid)
    value = row.value if role == "starter" else _image_value(row)
    if get(effective_doc(s), key) != value:
        s = record_answer(s, key, value)
    picked = next(
        (r for r in s.catalog.model_ladder if r.model == row.value and r.role == role), None
    )
    asks = picked is not None and picked.needs_account and _question(s, "hf_token") is not None
    if asks and (s.step == 0 or s.token is None):
        return replace(s, editing="", field=TOKEN_FIELD, error=None)
    # Only the chat step clears a token; the image step keeps what was typed.
    return _models_next(replace(s, token=None) if s.step == 0 and not asks else s)


def commit_token(s: State, text: str) -> State:
    """A blank token skips; the typed one is trimmed, checked and kept apart from the answers.

    A token is written unquoted into an environment file, so it may hold only
    letters, digits, `_` and `-`. A refused one is cleared, never shown.
    """
    if text and not _TOKEN_TEXT.fullmatch(text.strip()):
        return replace(s, editing="", error=_BAD_TOKEN)
    # A blank Enter skips: on the chat step that leaves no token, on the image step
    # it leaves the held one (none, since the field only opens when none is held).
    token = text.strip() or (None if s.step == 0 else s.token)
    done = replace(s, editing=None, field="", error=None, token=token)
    return _models_next(done)


# ------------------------------------------- STORAGE, NETWORK, ASSISTANT


def _option_label(q: Question, value: object) -> str:
    named = next((o.label for o in q.options if o.value == value), None)
    return named or (q.options[0].label if value is None and q.options else str(value))


def _row(s: State, doc: Document, qid: str, detail: str | None = None) -> Row | None:
    q = _question(s, qid)
    if q is None:
        return None
    fixed = fixed_override(s, q.answer_key)
    value = get(doc, q.answer_key)
    if detail is None:
        detail = _option_label(q, value) if q.options else "" if value is None else str(value)
    note = "" if fixed is None else set_by(fixed)
    return make_row(qid, q.title, detail, "text", value, "open" if fixed is None else "fixed", note)


def _folder_row(s: State, doc: Document, qid: str) -> Row | None:
    row = _row(s, doc, qid, "")
    if row is None or not isinstance(row.value, str):
        return row
    disk = disk_of(row.value, s.facts.disks)
    free = "" if disk is None else f" ({fmt_gb(disk.free_gb)} GB free)"
    return replace(row, detail=row.value + free)


def _present(rows: list[Row | None]) -> tuple[Row, ...]:
    return tuple(r for r in rows if r is not None)


def storage_rows(s: State) -> tuple[Row, ...]:
    doc = effective_doc(s)
    rows = [_folder_row(s, doc, "model_store")]
    if s.facts.npu_device is True:
        rows.append(_folder_row(s, doc, "flm_store"))
    return _present(rows)


def network_rows(s: State) -> tuple[Row, ...]:
    doc = effective_doc(s)
    host = _row(s, doc, "hostname")
    port = _row(s, doc, "port")
    origins = _row(s, doc, "allowed_origins")
    if host is not None and host.value is None:
        host = replace(host, detail=f"{DEFAULT_HOST} (default)")
    if port is not None and port.value is None:
        port = replace(port, detail=f"{DEFAULT_PORT} (default)")
    if origins is not None and isinstance(origins.value, list):
        origins = replace(origins, detail=", ".join(str(o) for o in origins.value))
    return _present([host, port, origins])


def _job_off(s: State, job: str) -> str:
    """Why a row's job will not be installed, or nothing when it will."""
    c = next((c for c in s.catalog.components if c.id == job), None)
    if c is None or any(j.id == job for j in jobs_on(s, effective_doc(s))):
        return ""
    return f"{c.label} is not being installed"


def _gated(s: State, row: Row | None, job: str) -> Row | None:
    off = _job_off(s, job)
    return row if row is None or not off else replace(row, state="unavailable", note=off)


def assistant_rows(s: State) -> tuple[Row, ...]:
    doc = effective_doc(s)
    terminal = _row(s, doc, "terminal_tool", "")
    if terminal is not None:
        terminal = replace(terminal, kind="check", value=terminal.value is True)
    return _present(
        [
            _gated(s, terminal, _ASSISTANT_JOB),
            _gated(s, _row(s, doc, "memory_model"), _MEMORY_JOB),
        ]
    )


def space_field(rows: Callable[[State], tuple[Row, ...]]) -> Callable[[State], State]:
    """Space on a row: tick a box, step to the next option, or open its field."""

    def act(s: State) -> State:
        row = rows(s)[s.cursor]
        if row.state != "open":
            return s
        q = s.catalog.questions[row.id]
        if row.kind == "check":
            return record_answer(s, q.answer_key, not row.value)
        if q.options:
            values = [o.value for o in q.options]
            at = values.index(row.value) if row.value in values else 0
            return record_answer(s, q.answer_key, values[(at + 1) % len(values)])
        return replace(s, editing="", field=_FIELD + row.id, error=None)

    return act


def _check_port(text: str) -> tuple[object, str | None]:
    if text.isascii() and text.isdigit() and 1 <= int(text) <= 65535:
        return int(text), None
    return text, "the port must be a whole number between 1 and 65535"


def _check_hostname(text: str) -> tuple[object, str | None]:
    name = text.strip()
    labels = name.split(".")
    if len(name) <= 253 and all(_LABEL.fullmatch(label) for label in labels):
        return name, None
    return name, (
        "a name can use only letters, digits and hyphens, no part longer than 63 "
        "characters, and no part starting or ending with a hyphen"
    )


def _check_origins(text: str) -> tuple[object, str | None]:
    origins = text.replace(",", " ").split()
    if origins and not any(ord(c) < 32 or ord(c) == 127 for c in text):
        return origins, None
    return origins, "enter one or more names, separated by commas or spaces"


_VALIDATORS: dict[str, Callable[[str], tuple[object, str | None]]] = {
    "model_store": validate_folder,
    "flm_store": validate_folder,
    "hostname": _check_hostname,
    "port": _check_port,
    "allowed_origins": _check_origins,
}


def commit_field(s: State, text: str) -> State:
    """Check a typed value; keep the field open with the reason when it is bad."""
    qid = s.field.removeprefix(_FIELD)
    value, error = _VALIDATORS[qid](text)
    if error is not None:
        return replace(s, error=error)
    done = replace(s, editing=None, field="", error=None)
    return record_answer(done, key_of(s, qid), value)


def accept(s: State) -> State:
    return go_on(_prune(s))
