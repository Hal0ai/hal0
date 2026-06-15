"""rich rendering for `hal0 setup` (spec §6.1): a two-column shell redrawn per
step. Left = the step body; right = the always-on context pane."""

from __future__ import annotations

from rich.console import Group, RenderableType
from rich.layout import Layout
from rich.panel import Panel
from rich.table import Table
from rich.text import Text

from hal0.cli.setup_copy import PANE_COPY


def render_shell(*, step_key: str, left_body: RenderableType, hw_footer: str) -> RenderableType:
    """Two-column renderable: left step body, right context pane + hw footer."""
    copy = PANE_COPY[step_key]
    pane = Group(
        Text(f"✦ {copy.headline}", style="bold yellow"),
        Text(""),
        Text(copy.body),
        Text(""),
        Text(f"Detected: {hw_footer}", style="dim"),
    )
    layout = Layout()
    layout.split_row(
        Layout(Panel(left_body, border_style="yellow"), ratio=3, name="step"),
        Layout(Panel(pane, border_style="dim"), ratio=2, name="pane"),
    )
    return Panel(layout, title="hal0 setup", border_style="yellow")


def render_extension_checklist(extensions, state: dict, cursor: int) -> RenderableType:
    """Grouped Apps/Agents checklist. ``state`` maps id→bool; ``cursor`` is the
    highlighted row index across the flat ordered list (Apps then Agents).
    Pass cursor=-1 for no highlight."""
    grouped: dict[str, list] = {"app": [], "agent": []}
    for e in extensions:
        grouped[e.kind].append(e)
    lines: list[RenderableType] = []
    idx = 0
    for label, kind in (("Apps", "app"), ("Agents", "agent")):
        lines.append(Text(label, style="bold"))
        for e in grouped[kind]:
            mark = "[x]" if state.get(e.id) else "[ ]"
            arrow = ">" if idx == cursor else " "
            style = "bold yellow" if idx == cursor else ""
            lines.append(Text(f" {arrow} {mark} {e.name:<12} {e.summary}", style=style))
            idx += 1
    lines.append(Text(""))
    lines.append(Text("↑↓ move · space toggle · enter confirm", style="dim"))
    return Group(*lines)


def render_suggestion_table(suggestions) -> RenderableType:
    t = Table(expand=True)
    t.add_column(" ", width=2)
    t.add_column("Model")
    t.add_column("Size", justify="right")
    t.add_column("Ctx", justify="right")
    t.add_column("Backend")
    for s in suggestions:
        star = "★" if s.recommended else " "
        t.add_row(
            star,
            s.display_name,
            f"{s.size_gb:.1f}GB",
            f"{s.context_length or '—'}",
            s.profile or "—",
        )
    return t
