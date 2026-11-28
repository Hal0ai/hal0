"""The GPU, type, folder and review screens, and the folder path check."""

from __future__ import annotations

import math
import os
import posixpath
from collections.abc import Sequence
from dataclasses import replace

from .document import ADMIN_KEY, Document, Override, get
from .facts import Disk, Facts
from .flow_model import (
    Outcome,
    Row,
    State,
    current_type,
    download_items,
    download_total,
    effective_doc,
    fixed_override,
    fmt_gb,
    go_on,
    gpu_consent,
    jobs_on,
    key_of,
    make_row,
    maybe_value,
    pick_model,
    record_answer,
)

_DOWNLOAD_MBIT = 100
_DEFAULT_HOST = "hal0.local"
_DEFAULT_PORT = 8080
_NO_DOWNLOADS = "nothing yet; models are added after install"
_GPU_REASONS = {
    "no_device": "No graphics card was found on this box.",
    "no_kfd": "A graphics card was found, but its compute driver is not available.",
    "broken_group": "A graphics card was found, but this user is not allowed to use it.",
}
_GPU_REASON_OTHER = "The graphics card on this box cannot be used."


# ------------------------------------------------------------ GPU screen


def gpu_title(s: State) -> str:
    if gpu_consent(s):
        return s.catalog.questions["cpu_only"].title
    return "This box cannot run hal0 yet"


def gpu_intro(s: State) -> tuple[str, ...]:
    return (_GPU_REASONS.get(s.facts.gpu_reason or "", _GPU_REASON_OTHER),)


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
    note = "" if fixed is None else f"set by {fixed.source}"
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


def facts_lines(f: Facts) -> tuple[str, ...]:
    card = [
        x
        for x in (
            f.gpu_vendor,
            None if f.gpu_pool_gb is None else f"{fmt_gb(f.gpu_pool_gb)} GB memory",
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
        f"  Memory: {unknown if f.ram_gb is None else fmt_gb(f.ram_gb) + ' GB'}",
        f"  NPU: {'found' if f.npu_device else unknown}",
        f"  Podman: {podman or unknown}",
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
            f"set by {fixed.source}" if fixed is not None and o.value == current else "",
        )
        for o in q.options
    )


def type_cursor(s: State) -> int:
    values = [r.value for r in type_rows(s)]
    current = current_type(s)
    return values.index(current) if current in values else 0


def type_enter(s: State) -> State:
    return go_on(record_answer(s, key_of(s, "setup_type"), type_rows(s)[s.cursor].value))


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
        note = f"less than the {fmt_gb(total)} GB this setup downloads"
    detail = "" if disk is None else f"{fmt_gb(disk.free_gb)} GB free"
    return make_row(id, path, detail, "choice", path, "open" if fixed is None else "fixed", note)


def folder_rows(s: State) -> tuple[Row, ...]:
    key = key_of(s, "model_store")
    fixed = fixed_override(s, key)
    current = get(effective_doc(s), key)
    path = current if isinstance(current, str) else ""
    total = download_total(s)
    here = _disk_of(path, s.facts.disks) if path else None
    rows = [_disk_row("current", path, here, total, fixed)] if path else []
    if fixed is not None:
        return tuple(rows)
    others = sorted((d for d in s.facts.disks if d != here), key=lambda d: d.free_gb, reverse=True)
    for d in others:
        rows.append(_disk_row(d.mount, posixpath.join(d.mount, "hal0/models"), d, total, None))
    rows.append(make_row("other", "Type another folder...", path, "text", None))
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


def commit_folder(s: State, text: str) -> State:
    path, error = validate_folder(text)
    if error is not None:
        return replace(s, error=error)
    done = record_answer(
        replace(s, editing=None, field="", error=None), key_of(s, "model_store"), path
    )
    return go_on(done)


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


def review_rows(s: State) -> tuple[Row, ...]:
    doc = effective_doc(s)
    jobs = jobs_on(s, doc)
    items = download_items(s, doc)
    pick = pick_model(s, doc)
    total = download_total(s)
    rows = [
        make_row(
            "install", "Installing", ", ".join(c.label for c in jobs) or "only the model server"
        ),
        make_row("downloads", "Downloads", ", ".join(t for t, _ in items) or _NO_DOWNLOADS),
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
    model = "none" if pick is None else pick.label + (" (picked for this box)" if pick.auto else "")
    rows.append(make_row("model", "Chat model", model))
    if s.facts.npu_device is True and any(c.id == "npu" for c in jobs):
        rows.append(make_row("npu", "NPU", "set up if it passes its check"))
    rows.extend(_access_rows(s, doc))
    rows.extend(make_row("warning", "Warning", w) for w in _warnings(s, doc))
    return tuple(rows)


def _folder_text(s: State, doc: Document) -> str:
    path = get(doc, key_of(s, "model_store"))
    if not isinstance(path, str):
        return "unchanged"
    disk = _disk_of(path, s.facts.disks)
    return path if disk is None else f"{path} ({fmt_gb(disk.free_gb)} GB free)"


def _access_rows(s: State, doc: Document) -> list[Row]:
    host = maybe_value(s, doc, "hostname") or _DEFAULT_HOST
    port = maybe_value(s, doc, "port") or _DEFAULT_PORT
    local = maybe_value(s, doc, "bind_host") == "127.0.0.1"
    reach = (
        f"http://127.0.0.1:{port} from this machine only"
        if local
        else f"http://{host}:{port} from this network"
    )
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


def _warnings(s: State, doc: Document) -> list[str]:
    out = []
    if s.facts.gpu_outcome != "usable" and get(doc, key_of(s, "cpu_only")) is True:
        out.append("No graphics card will be used. " + s.catalog.questions["cpu_only"].help)
    path = get(doc, key_of(s, "model_store"))
    disk = _disk_of(path, s.facts.disks) if isinstance(path, str) else None
    total = download_total(s)
    if disk is not None and total is not None and disk.free_gb < total:
        out.append(f"The model folder has less than the {fmt_gb(total)} GB this setup downloads.")
    return out


def review_enter(s: State) -> State:
    return replace(s, status=Outcome.DONE)


def commit_save(s: State, text: str) -> State:
    path, error = _check_path(text, "the file path")
    if error is None and os.path.isdir(path):
        error = f"{path} is a folder, not a file"
    if error is not None:
        return replace(s, error=error)
    return replace(
        s, answers={**s.answers, "_save_copy_to": path}, editing=None, field="", error=None
    )
