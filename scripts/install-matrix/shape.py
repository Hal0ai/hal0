#!/usr/bin/env python3
"""Read the install matrix shapes.

    python3 -I shape.py [--file PATH] list
    python3 -I shape.py [--file PATH] get <id>

``list`` prints one shape id per line in row order. ``get`` prints one
``SHAPE_KEY=value`` line per field. Values never contain a newline or ``=``,
so bash can read them with ``while IFS='=' read -r key value`` and no eval.
Exit 2 on an unknown id or an invalid file. Standard library only.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

DEFAULT_FILE = Path(__file__).resolve().with_name("shapes.json")

HARDWARE = ("cpu", "gpu", "gpu+npu")
ENTRY = ("oneliner", "oneliner-unattended", "terminal", "box", "vm", "update")
RUNNABLE = ("yes", "needs-image", "needs-hardware", "needs-existing-box")

# Output order: (output key, shapes.json field).
FIELDS = (
    ("SHAPE_ROW", "row"),
    ("SHAPE_TEMPLATE", "template"),
    ("SHAPE_UNPRIVILEGED", "unprivileged"),
    ("SHAPE_HARDWARE", "hardware"),
    ("SHAPE_ENTRY", "entry"),
    ("SHAPE_CORES", "cores"),
    ("SHAPE_MEMORY_MB", "memory_mb"),
    ("SHAPE_DISK_GB", "disk_gb"),
    ("SHAPE_RUNNABLE", "runnable"),
)


class ShapeError(Exception):
    pass


def _check_text(label: str, field: str, value: object) -> str:
    if not isinstance(value, str) or not value:
        raise ShapeError(f"{label}: field {field!r} must be a non-empty string")
    if "\n" in value or "\r" in value or "=" in value:
        raise ShapeError(f"{label}: field {field!r} must not contain a newline or '='")
    return value


def _check_int(label: str, field: str, value: object, allowed=None) -> int:
    if isinstance(value, bool) or not isinstance(value, int):
        raise ShapeError(f"{label}: field {field!r} must be an integer")
    if allowed is not None and value not in allowed:
        raise ShapeError(f"{label}: field {field!r} must be one of {list(allowed)}")
    if allowed is None and value <= 0:
        raise ShapeError(f"{label}: field {field!r} must be positive")
    return value


def _choice(label: str, field: str, value: object, allowed) -> str:
    text = _check_text(label, field, value)
    if text not in allowed:
        raise ShapeError(f"{label}: field {field!r} must be one of {'|'.join(allowed)}, got {text!r}")
    return text


def answers_for(hardware: str, entry: str) -> str:
    """Answers file name, by ruling: cpu + oneliner/box, or terminal."""
    if entry == "terminal":
        return "terminal-defaults.tsv"
    if hardware == "cpu" and entry in ("oneliner", "box"):
        return "cpu-consent.tsv"
    return ""


def load(path: Path) -> list[dict]:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        raise ShapeError(f"cannot read {path}: {exc}") from exc
    shapes = data.get("shapes") if isinstance(data, dict) else None
    if not isinstance(shapes, list) or not shapes:
        raise ShapeError("file must hold a non-empty 'shapes' list")
    out, seen = [], set()
    for index, raw in enumerate(shapes, 1):
        if not isinstance(raw, dict):
            raise ShapeError(f"shape #{index}: must be an object")
        sid = _check_text(f"shape #{index}", "id", raw.get("id"))
        label = f"shape {sid}"
        if sid in seen:
            raise ShapeError(f"{label}: duplicate id")
        seen.add(sid)
        hardware = _choice(label, "hardware", raw.get("hardware"), HARDWARE)
        entry = _choice(label, "entry", raw.get("entry"), ENTRY)
        shape = {
            "id": sid,
            "row": _check_int(label, "row", raw.get("row")),
            "template": _check_text(label, "template", raw.get("template")),
            "unprivileged": _check_int(label, "unprivileged", raw.get("unprivileged"), (0, 1)),
            "hardware": hardware,
            "entry": entry,
            "cores": _check_int(label, "cores", raw.get("cores")),
            "memory_mb": _check_int(label, "memory_mb", raw.get("memory_mb")),
            "disk_gb": _check_int(label, "disk_gb", raw.get("disk_gb")),
            "runnable": _choice(label, "runnable", raw.get("runnable"), RUNNABLE),
        }
        out.append(shape)
    return sorted(out, key=lambda s: s["row"])


def render(shape: dict) -> list[str]:
    lines = [f"{key}={shape[field]}" for key, field in FIELDS]
    lines.append(f"SHAPE_ANSWERS={answers_for(shape['hardware'], shape['entry'])}")
    return lines


def main(argv: list[str]) -> int:
    args = list(argv)
    path = DEFAULT_FILE
    if args[:1] == ["--file"]:
        if len(args) < 2:
            print("shape.py: --file needs a path", file=sys.stderr)
            return 2
        path = Path(args[1])
        args = args[2:]
    try:
        if args == ["list"]:
            print("\n".join(s["id"] for s in load(path)))
            return 0
        if len(args) == 2 and args[0] == "get":
            for shape in load(path):
                if shape["id"] == args[1]:
                    print("\n".join(render(shape)))
                    return 0
            print(f"shape.py: unknown shape id {args[1]!r}", file=sys.stderr)
            return 2
    except ShapeError as exc:
        print(f"shape.py: invalid shapes file: {exc}", file=sys.stderr)
        return 2
    print("usage: shape.py [--file PATH] list | get <id>", file=sys.stderr)
    return 2


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
