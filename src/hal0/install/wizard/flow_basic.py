"""The GPU, type, folder and review screens, and the folder path check.

The path check looks at the filesystem; everything else reads only the state.
"""

from __future__ import annotations

import json
import math
import os
import posixpath
import stat
from dataclasses import replace

from .document import ADMIN_KEY, Document, Override, get, preset
from .facts import Disk, Facts
from .flow_model import (
    DEFAULT_HOST,
    DEFAULT_PORT,
    Outcome,
    Pick,
    Row,
    State,
    current_type,
    disk_of,
    download_items,
    download_total,
    effective_doc,
    failing_need,
    fixed_override,
    fmt_gb,
    go_on,
    gpu_consent,
    jobs_on,
    jobs_wanted,
    key_of,
    make_row,
    maybe_value,
    model_requirement,
    pick_model,
    record_answer,
    set_by,
    short_on_space,
)

_DOWNLOAD_MBIT = 100
_NPU_JOB = "npu"
_NO_DOWNLOADS = "nothing yet; models are added after install"
_NO_CHAT_DOWNLOAD = "no chat model to download"
_GPU_REASONS = {
    "no_device": "No graphics card was found on this box.",
    "no_kfd": "A graphics card was found, but its compute driver is not available.",
    "broken_group": "A graphics card was found, but this user is not allowed to use it.",
}
GPU_REASON_OTHER = "The graphics card on this box cannot be used."
_VENDORS = {"amd": "AMD", "intel": "Intel", "nvidia": "NVIDIA"}
_COPY_MAX_BYTES = 2**20  # an answers document is far smaller than this
_OTHER_DISKS = 4  # rows for other disks on the folder screen, most free first


# ------------------------------------------------------------ GPU screen


def gpu_title(s: State) -> str:
    if gpu_consent(s):
        return s.catalog.questions["cpu_only"].title
    return "This box cannot run hal0 yet"


def gpu_intro(s: State) -> tuple[str, ...]:
    return (_GPU_REASONS.get(s.facts.gpu_reason or "", GPU_REASON_OTHER),)


def gpu_rows(s: State) -> tuple[Row, ...]:
    if not gpu_consent(s):
        return (
            make_row(
                "stop",
                "hal0 cannot continue on this box",
                "Fix this, then run the installer again.",
            ),
        )
    fixed = fixed_override(s, key_of(s, "cpu_only"))
    note = "" if fixed is None else set_by(fixed)
    state = "open" if fixed is None else "fixed"
    return tuple(
        make_row(str(o.value).lower(), o.label, o.help, "choice", o.value, state, note)
        for o in s.catalog.questions["cpu_only"].options
    )


def gpu_cursor(s: State) -> int:
    value = get(effective_doc(s), key_of(s, "cpu_only"))
    values = [r.value for r in gpu_rows(s)]
    return values.index(value) if value in values else max(0, len(values) - 1)


def gpu_enter(s: State) -> State:
    if not gpu_consent(s):
        return replace(s, status=Outcome.STOPPED)
    key = key_of(s, "cpu_only")
    fixed = fixed_override(s, key)
    value = fixed.value if fixed is not None else gpu_rows(s)[s.cursor].value
    if value is not True:
        return replace(s, status=Outcome.STOPPED)
    return go_on(record_answer(s, key, True))


# ----------------------------------------------------------- TYPE screen


def _podman_word(f: Facts, unknown: str) -> str:
    if f.podman_present is False:
        return "will be installed"
    if f.podman_runs is None:
        return "installed" if f.podman_present else unknown
    return "installed" if f.podman_runs else "installed but not working"


def facts_lines(f: Facts) -> tuple[str, ...]:
    card = [
        x
        for x in (
            None if f.gpu_vendor is None else _VENDORS.get(f.gpu_vendor, f.gpu_vendor),
            None if f.gpu_pool_gb is None else f"{fmt_gb(f.gpu_pool_gb)} GB memory",
        )
        if x
    ]
    if f.gpu_outcome != "usable":
        card = [*card[:1], "not usable"] if card else []
    unknown = "not detected"
    podman = _podman_word(f, unknown)
    return (
        "This box:",
        f"  Graphics card: {', '.join(card) or unknown}",
        f"  Memory: {unknown if f.ram_gb is None else fmt_gb(f.ram_gb) + ' GB'}",
        f"  NPU: {'found' if f.npu_device else unknown}",
        f"  Podman: {podman}",
    )


def type_rows(s: State) -> tuple[Row, ...]:
    q = s.catalog.questions["setup_type"]
    fixed = fixed_override(s, q.answer_key)
    state = "open" if fixed is None else "fixed"
    current = current_type(s)
    return tuple(
        make_row(
            str(o.value),
            o.label,
            o.help,
            "choice",
            o.value,
            state,
            set_by(fixed) if fixed is not None and o.value == current else "",
        )
        for o in q.options
    )


def type_cursor(s: State) -> int:
    values = [r.value for r in type_rows(s)]
    current = current_type(s)
    return values.index(current) if current in values else 0


def type_enter(s: State) -> State:
    """Record the type; a different type drops what was chosen for the old one."""
    key = key_of(s, "setup_type")
    picked = type_rows(s)[s.cursor].value
    if picked != current_type(s):
        named = (s.catalog.questions.get(q) for q in ("model_store", "cpu_only"))
        kept = {"_save_copy_to", *(q.answer_key for q in named if q is not None)}
        s = replace(s, answers={k: v for k, v in s.answers.items() if k in kept}, token=None)
    return go_on(record_answer(s, key, picked))


# --------------------------------------------------------- FOLDER screen


def _disk_row(
    id: str, path: str, disk: Disk | None, total: float | None, fixed: Override | None
) -> Row:
    note = ""
    if fixed is not None:
        note = set_by(fixed)
    elif total is not None and short_on_space(disk, total):
        note = f"less than the {fmt_gb(total)} GB this setup downloads"
    detail = "" if disk is None else f"{fmt_gb(disk.free_gb)} GB free"
    return make_row(id, path, detail, "choice", path, "open" if fixed is None else "fixed", note)


def folder_rows(s: State) -> tuple[Row, ...]:
    key = key_of(s, "model_store")
    fixed = fixed_override(s, key)
    current = get(effective_doc(s), key)
    path = current if isinstance(current, str) else ""
    total = download_total(s)
    here = disk_of(path, s.facts.disks) if path else None
    rows = [_disk_row("current", path, here, total, fixed)] if path else []
    if fixed is not None:
        return tuple(rows)
    shown: list[Disk | None] = [here] if path else []
    default = get(preset(s.catalog, current_type(s)), key)
    if isinstance(default, str) and default != path:
        home = disk_of(default, s.facts.disks)
        rows.append(_disk_row("preset", default, home, total, None))
        shown.append(home)
    others = sorted(
        (d for d in s.facts.disks if d not in shown), key=lambda d: d.free_gb, reverse=True
    )
    for d in others[:_OTHER_DISKS]:
        rows.append(_disk_row(d.mount, posixpath.join(d.mount, "hal0/models"), d, total, None))
    rows.append(make_row("other", "Type another folder...", "", "text", None))
    return tuple(rows)


def folder_intro(s: State) -> tuple[str, ...]:
    total = download_total(s)
    return () if total is None else (f"This setup will download about {fmt_gb(total)} GB.",)


def folder_enter(s: State) -> State:
    key = key_of(s, "model_store")
    rows = folder_rows(s)
    if fixed_override(s, key) is not None:
        return go_on(s)
    row = rows[s.cursor]
    if row.id == "other":
        return replace(s, editing="", field="folder", error=None)
    return go_on(record_answer(s, key, row.value))


def folder_space(s: State) -> State:
    """Space opens the field on the other-folder row, as Enter does; elsewhere it does nothing."""
    rows = folder_rows(s)
    if fixed_override(s, key_of(s, "model_store")) is None and rows[s.cursor].id == "other":
        return folder_enter(s)
    return s


def commit_folder(s: State, text: str) -> State:
    path, error = validate_folder(text)
    if error is not None:
        return replace(s, error=error)
    done = record_answer(
        replace(s, editing=None, field="", error=None), key_of(s, "model_store"), path
    )
    return go_on(done)


def _clean_path(text: str, what: str, *, percent: bool = False) -> tuple[str, str | None]:
    """The trimmed, normalised absolute path and the reason it cannot be used, or None."""
    path = text.strip()
    if not path:
        return path, f"{what} is required"
    if any(ord(c) < 32 or ord(c) == 127 for c in path):
        return path, f"{what} has a control character"
    if percent and "%" in path:
        return path, f"{what} must not contain %"
    if not path.startswith("/"):
        return path, f"{what} must start with /"
    return posixpath.normpath(path), None


def validate_folder(text: str) -> tuple[str, str | None]:
    """The cleaned folder path and the reason it cannot be used, or None."""
    path, error = _clean_path(text, "the folder", percent=True)
    if error is not None:
        return path, error
    ancestor = path
    while not os.path.exists(ancestor) and ancestor != "/":
        ancestor = posixpath.dirname(ancestor)
    if not os.path.isdir(ancestor):
        return path, f"{ancestor} is not a folder"
    return path, None


def _is_answers_document(path: str) -> bool:
    """Whether a regular file loads as an answers document; never raises."""
    try:
        fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK | os.O_NOCTTY)
    except OSError:
        return False
    try:
        if not stat.S_ISREG(os.fstat(fd).st_mode):
            return False
        with os.fdopen(fd, "rb", closefd=False) as handle:
            raw = handle.read(_COPY_MAX_BYTES + 1)
        if len(raw) > _COPY_MAX_BYTES:
            return False
        data = json.loads(raw.decode("utf-8"))
        meta = data.get("meta") if isinstance(data, dict) else None
        return "version" in data and isinstance(meta, dict) and "saved_by" in meta
    except Exception:
        return False
    finally:
        os.close(fd)


def _check_copy(text: str, confirmed: object) -> tuple[str, str | None]:
    """The path a copy of the answers may be saved to.

    An existing file is replaced only when it is the copy confirmed earlier in
    this run or an earlier answers document; anything else is left alone.
    """
    path, error = _clean_path(text, "the file path")
    if error is not None:
        return path, error
    if os.path.islink(path):
        return path, f"{path} is a symlink"
    if os.path.isdir(path):
        return path, f"{path} is a folder"
    if not os.path.isdir(posixpath.dirname(path)):
        return path, "the folder for this file does not exist"
    try:
        mode = os.lstat(path).st_mode
    except FileNotFoundError:
        return path, None
    except OSError:
        mode = 0
    if stat.S_ISREG(mode) and (path == confirmed or _is_answers_document(path)):
        return path, None
    return path, f"{path} already exists; choose another name"


# --------------------------------------------------------- REVIEW screen


def review_rows(s: State) -> tuple[Row, ...]:
    doc = effective_doc(s)
    jobs = jobs_on(s, doc)
    items = download_items(s, doc)
    pick = pick_model(s, doc)
    total = download_total(s)
    rows = [
        make_row(
            "install",
            "Installing",
            ", ".join(c.label for c in jobs if c.id != _NPU_JOB) or "only the model server",
        ),
        make_row("downloads", "Downloads", ", ".join(t for t, _ in items) or _no_downloads(pick)),
    ]
    if total is not None:
        minutes = math.ceil(total * 8000 / _DOWNLOAD_MBIT / 60)
        rows.append(
            make_row(
                "total",
                "Total",
                f"{fmt_gb(total)} GB, about {minutes} min on a {_DOWNLOAD_MBIT} Mbit/s line",
            )
        )
    rows.append(make_row("folder", "Model folder", _folder_text(s, doc)))
    rows.append(make_row("model", "Chat model", _model_text(s, doc, pick)))
    if s.facts.npu_device is True and any(c.id == _NPU_JOB for c in jobs):
        rows.append(make_row("npu", "NPU", "set up if it passes its check"))
    rows.extend(_access_rows(s, doc))
    rows.extend(make_row("warning", "Warning", w) for w in _skipped(s, doc) + _warnings(s, doc))
    return tuple(rows)


def _no_downloads(pick: Pick | None) -> str:
    """The downloads line when nothing has a size: say why no chat model is coming."""
    return _NO_CHAT_DOWNLOAD if pick is not None and pick.outcome == "too_small" else _NO_DOWNLOADS


def _model_text(s: State, doc: Document, pick: Pick | None) -> str:
    """What the review says about the chat model, for each way it can be set."""
    if pick is not None:
        if pick.auto:
            return f"{pick.label}, picked for this box"
        if pick.outcome == "unknown":
            return "picked for this box once hal0 has checked it"
        return pick.label
    if maybe_value(s, doc, "starter_model") == "auto":
        return "added after install"
    return "none"


def _folder_text(s: State, doc: Document) -> str:
    path = maybe_value(s, doc, "model_store")
    if not isinstance(path, str):
        return "unchanged"
    disk = disk_of(path, s.facts.disks)
    return path if disk is None else f"{path} ({fmt_gb(disk.free_gb)} GB free)"


def _access_rows(s: State, doc: Document) -> list[Row]:
    host = maybe_value(s, doc, "hostname") or DEFAULT_HOST
    port = maybe_value(s, doc, "port") or DEFAULT_PORT
    reach = f"http://{host}:{port} from this network"
    admin = {"generate": "created; shown once when the install ends", "none": "not created"}
    terminal = (
        "ON: the assistant can run commands as a root-equivalent user"
        if maybe_value(s, doc, "terminal_tool") is True
        else "off"
    )
    return [
        make_row("reachable", "Reachable at", reach),
        make_row("admin_key", "Admin key", admin.get(str(get(doc, ADMIN_KEY)), "unchanged")),
        make_row("terminal_tool", "Assistant commands", terminal),
    ]


def _skipped(s: State, doc: Document) -> list[str]:
    """Jobs that are on but cannot run here (the NPU one means "when found")."""
    out = []
    for c in jobs_wanted(s, doc):
        need = failing_need(s, c)
        if need is not None and c.id != _NPU_JOB:
            out.append(f"{c.label}: {s.catalog.reasons[need]}")
    return out


def _warnings(s: State, doc: Document) -> list[str]:
    out = []
    if s.facts.gpu_outcome != "usable" and maybe_value(s, doc, "cpu_only") is True:
        out.append("No graphics card will be used. " + s.catalog.questions["cpu_only"].help)
    path = maybe_value(s, doc, "model_store")
    disk = disk_of(path, s.facts.disks) if isinstance(path, str) else None
    starter = maybe_value(s, doc, "starter_model")
    picked = next(
        (r for r in s.catalog.model_ladder if r.role == "starter" and r.model == starter), None
    )
    if picked is not None:
        note = model_requirement(picked, s.facts)[1]
        if note:
            out.append(f"{picked.model}: {note}")
    total = download_total(s)
    if total is not None and short_on_space(disk, total):
        out.append(f"The model folder has less than the {fmt_gb(total)} GB this setup downloads.")
    return out


def review_intro(s: State) -> tuple[str, ...]:
    path = s.answers.get("_save_copy_to")
    return () if path is None else (f"Saving a copy of these answers to {path}.",)


def review_enter(s: State) -> State:
    return replace(s, status=Outcome.DONE)


def commit_save(s: State, text: str) -> State:
    path, error = _check_copy(text, s.answers.get("_save_copy_to"))
    if error is not None:
        return replace(s, error=error)
    return replace(
        s, answers={**s.answers, "_save_copy_to": path}, editing=None, field="", error=None
    )
