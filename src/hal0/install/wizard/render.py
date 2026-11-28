"""Turn a View into the lines a person sees. Pure: no input, no output.

Width counts visible characters and treats every character as one column; wide
(East Asian) characters and combining marks are not measured.
"""

from __future__ import annotations

import re

from .flow_model import Row, View

ACCENT = "\x1b[38;5;214m"  # the installer's amber
DIM = "\x1b[2m"
RESET = "\x1b[0m"

_ANSI = re.compile(r"\x1b\[[0-9;]*[A-Za-z]")
_Piece = tuple[str, str]  # (text, style code or "")
_Line = list[_Piece]
_Segment = tuple[str, str, str]  # (text, style, separator before it)


def strip_ansi(text: str) -> str:
    return _ANSI.sub("", text)


def _length(line: _Line) -> int:
    return sum(len(text) for text, _ in line)


def _chunks(word: str, size: int) -> list[str]:
    size = max(size, 1)
    return [word[i : i + size] for i in range(0, len(word), size)] or [""]


def _flow(first: _Line, rest: str, segments: list[_Segment], avail: int) -> list[_Line]:
    """Fill segments into lines. A segment that fits goes on the current line whole;
    otherwise it starts a new line under `rest` and is wrapped there."""
    lines: list[_Line] = [list(first)]
    pad = rest[: avail // 2]

    def newline() -> None:
        lines.append([(pad, "")] if pad else [])

    # A line holds content once anything beyond its prefix has been put on it.
    filled = False
    for text, style, sep in segments:
        if not text:
            continue
        words = text.split(" ")
        spacer = sep if filled else ""
        if _length(lines[-1]) + len(spacer) + len(text) <= avail:
            lines[-1].append((spacer + text, style))
            filled = True
            continue
        if filled:
            newline()
            filled = False
        for index, word in enumerate(words):
            gap = " " if filled and index else ""
            if _length(lines[-1]) + len(gap) + len(word) <= avail:
                lines[-1].append((gap + word, style))
                filled = True
                continue
            if filled:
                newline()
            room = avail - _length(lines[-1])
            for n, part in enumerate(_chunks(word, room)):
                if n:
                    newline()
                lines[-1].append((part, style))
            filled = True
    return lines


def _wrap(text: str, avail: int, indent: str = "", style: str = "") -> list[_Line]:
    out: list[_Line] = []
    for paragraph in text.split("\n"):
        out.extend(_flow([(indent, "")], indent, [(paragraph, style, " ")], avail))
    return out


def _mark(row: Row, highlighted: bool) -> str:
    if row.kind == "check":
        if row.state == "unavailable":
            return "[-]"
        return "[x]" if row.value else "[ ]"
    if row.kind == "choice":
        return "(*)" if highlighted else "( )"
    return ""


def _row_lines(row: Row, lead: str, highlighted: bool, avail: int, plain: bool) -> list[_Line]:
    mark = _mark(row, highlighted)
    if highlighted and not plain:
        row_style = ACCENT
    elif row.state == "unavailable":
        row_style = DIM
    else:
        row_style = ""
    head = f"{lead}{mark} " if mark else lead
    indent = " " * (len(lead) + (4 if mark else 2))
    if row.kind == "text":
        segments: list[_Segment] = [(f"{row.label}:", row_style, ""), (row.detail, row_style, " ")]
    else:
        segments = [(row.label, row_style, ""), (row.detail, row_style, "  ")]
    segments.append((row.note, DIM, "  "))
    return _flow([(head, row_style)], indent, segments, avail)


def _title_lines(view: View, avail: int) -> list[_Line]:
    left = " hal0 setup"
    pos = view.position
    if pos and len(left) + 2 + len(pos) <= avail:
        gap = " " * (avail - len(left) - len(pos))
        return [[(left, ACCENT), (gap, ""), (pos, DIM)]]
    lines: list[_Line] = [[(left, ACCENT)]]
    if pos:
        lines.append([(" " + pos, DIM)])
    return lines


def _field_lines(text: str, avail: int) -> list[_Line]:
    indent = "   "
    typed = text + "_"
    size = max(avail - len(indent), 1)
    return [[(indent + part, "")] for part in _chunks(typed, size)]


def render(view: View, *, width: int, plain: bool, color: bool) -> list[str]:
    """The screen as lines no wider than `width - 1` visible characters."""
    avail = max(width - 1, 1)
    colored = color and not plain
    lines: list[_Line] = _title_lines(view, avail)
    lines.append([])
    lines.extend(_wrap(view.title, avail, " "))
    for text in view.intro:
        lines.extend(_wrap(text, avail, " "))
    lines.append([])
    number = 0
    for index, row in enumerate(view.rows):
        highlighted = index == view.cursor
        if plain:
            if row.kind == "info":
                lead = " " * 5
            else:
                number += 1
                lead = f"{number:>3}) "
        else:
            lead = " > " if highlighted and row.kind != "info" else "   "
        lines.extend(_row_lines(row, lead, highlighted, avail, plain))
    if view.error:
        lines.extend(_wrap(view.error, avail, "   ", ACCENT))
    if view.editing is not None:
        lines.extend(_field_lines(view.editing, avail))
    lines.append([])
    lines.extend(_wrap(view.hint, avail, " "))
    return [_paint(line, avail, colored) for line in lines]


def _paint(line: _Line, avail: int, colored: bool) -> str:
    """Clip to the width first, then add colour, so escape codes never count."""
    out: list[str] = []
    room = avail
    for text, style in line:
        text = text[:room]
        room -= len(text)
        if not text:
            continue
        out.append(f"{style}{text}{RESET}" if colored and style else text)
    return "".join(out).rstrip() if not colored else "".join(out)
