"""Turn a View into the lines a person sees. Pure: no input, no output.

Width counts visible characters and treats every character as one column; wide
(East Asian) characters and combining marks are not measured.
"""

from __future__ import annotations

import re
from dataclasses import replace

from .flow_model import Row, View

ACCENT = "\x1b[38;5;214m"  # the installer's amber
DIM = "\x1b[2m"
RESET = "\x1b[0m"

_ANSI = re.compile(r"\x1b\[[0-9;]*[A-Za-z]")
_Piece = tuple[str, str]  # (text, style code or "")
_Line = list[_Piece]
_Segment = tuple[str, str, str, bool]  # (text, style, separator before it, start a new line)


# Text comes from input files too: no control character may reach the terminal.
_CONTROLS = {**{code: "?" for code in range(0x20)}, 0x7F: "?"}


def strip_ansi(text: str) -> str:
    return _ANSI.sub("", text)


def _clean(text: str) -> str:
    return text.translate(_CONTROLS)


def _cleaned(view: View) -> View:
    """The view with every control character in its text replaced by `?`."""
    rows = tuple(
        replace(r, label=_clean(r.label), detail=_clean(r.detail), note=_clean(r.note))
        for r in view.rows
    )
    return replace(
        view,
        title=_clean(view.title),
        position=_clean(view.position),
        intro=tuple(_clean(t) for t in view.intro),
        rows=rows,
        editing=None if view.editing is None else _clean(view.editing),
        error=None if view.error is None else _clean(view.error),
        hint=_clean(view.hint),
    )


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
    for text, style, sep, apart in segments:
        if not text:
            continue
        words = text.split(" ")
        spacer = sep if filled else ""
        if not apart and _length(lines[-1]) + len(spacer) + len(text) <= avail:
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
        out.extend(_flow([(indent, "")], indent, [(paragraph, style, " ", False)], avail))
    return out


def _selected(row: Row, index: int, cursor: int) -> bool:
    """Only a row that can be chosen or changed is ever drawn as the selected one."""
    return index == cursor and row.kind != "info"


def _mark(row: Row, highlighted: bool) -> str:
    if row.kind == "check":
        if row.state == "unavailable":
            return "[-]"
        return "[x]" if row.value else "[ ]"
    if row.kind == "choice":
        return "(*)" if highlighted else "( )"
    return ""


def _segments(row: Row, style: str, detail_under: bool, note_under: bool) -> list[_Segment]:
    if row.kind == "text":
        label, sep = f"{row.label}:", " "
    else:
        label, sep = row.label, "  "
    return [
        (label, style, "", False),
        (row.detail, style, sep, detail_under),
        (row.note, DIM, "  ", note_under),
    ]


def _row_style(row: Row, highlighted: bool, plain: bool) -> str:
    if highlighted and not plain:
        return ACCENT
    return DIM if row.state == "unavailable" else ""


def _head(row: Row, lead: str, highlighted: bool) -> tuple[str, str]:
    """The text before the label, and the indent that continuation lines get."""
    mark = _mark(row, highlighted)
    head = f"{lead}{mark} " if mark else lead
    return head, " " * (len(lead) + (4 if mark else 2))


def _placement(
    rows: tuple[Row, ...], leads: list[str], cursor: int, avail: int, plain: bool
) -> tuple[bool, bool]:
    """Whether details, and separately notes, go under the label on this screen.

    One row that does not fit beside its label moves every row's detail (or note),
    so the details of a screen all sit in the same place."""
    detail_under = note_under = False
    for index, row in enumerate(rows):
        head, indent = _head(row, leads[index], _selected(row, index, cursor))
        style = _row_style(row, _selected(row, index, cursor), plain)
        first: _Line = [(head, style)]
        if row.detail:
            beside = _flow(first, indent, _segments(row, style, False, False)[:2], avail)
            detail_under = detail_under or len(beside) > 1
    for index, row in enumerate(rows):
        if not row.note:
            continue
        head, indent = _head(row, leads[index], _selected(row, index, cursor))
        style = _row_style(row, _selected(row, index, cursor), plain)
        without = _segments(row, style, detail_under, False)[:2]
        with_note = _segments(row, style, detail_under, False)
        first = [(head, style)]
        if len(_flow(first, indent, with_note, avail)) > len(_flow(first, indent, without, avail)):
            note_under = True
    return detail_under, note_under


def _row_lines(
    row: Row, lead: str, highlighted: bool, avail: int, plain: bool, under: tuple[bool, bool]
) -> list[_Line]:
    head, indent = _head(row, lead, highlighted)
    style = _row_style(row, highlighted, plain)
    return _flow([(head, style)], indent, _segments(row, style, *under), avail)


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


_EXTRA_KEYS = ("b back", "? explain", "s save a copy", "q quit")


def _plain_hint(view: View) -> str:
    """What to type on a terminal with no cursor keys, from the shape of the screen."""
    kinds = {row.kind for row in view.rows}
    if view.editing is not None:
        if "nothing is shown" in view.hint:
            return "Type or paste the token and press Enter (nothing is shown). Enter alone skips."
        return "Type the value and press Enter. Enter alone cancels."
    if "choice" in kinds:
        text = "Type a number and press Enter to choose. Enter alone keeps the marked one."
    elif kinds & {"check", "text"}:
        text = "Type a number and press Enter to change that line. Enter alone goes on."
    else:
        return view.hint
    extra = [key for key in _EXTRA_KEYS if key in view.hint]
    return f"{text} Also: {', '.join(extra)}." if extra else text


def render(view: View, *, width: int, plain: bool, color: bool) -> list[str]:
    """The screen as lines no wider than `width - 1` visible characters.

    Control characters in the view's text are drawn as `?`; only the renderer's
    own colour codes reach the terminal.
    """
    view = _cleaned(view)
    avail = max(width - 1, 1)
    colored = color and not plain
    lines: list[_Line] = _title_lines(view, avail)
    lines.append([])
    lines.extend(_wrap(view.title, avail, " "))
    for text in view.intro:
        lines.extend(_wrap(text, avail, " "))
    lines.append([])
    number = 0
    leads: list[str] = []
    for index, row in enumerate(view.rows):
        if plain and row.kind == "info":
            leads.append(" " * 5)
        elif plain:
            number += 1
            leads.append(f"{number:>3}) ")
        else:
            leads.append(" > " if _selected(row, index, view.cursor) else "   ")
    under = _placement(view.rows, leads, view.cursor, avail, plain)
    for index, row in enumerate(view.rows):
        lines.extend(
            _row_lines(row, leads[index], _selected(row, index, view.cursor), avail, plain, under)
        )
    if view.error:
        lines.extend(_wrap(view.error, avail, "   ", ACCENT))
    if view.editing is not None:
        lines.extend(_field_lines(view.editing, avail))
    lines.append([])
    lines.extend(_wrap(_plain_hint(view) if plain else view.hint, avail, " "))
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
