"""rich rendering for `hal0 setup` (spec §6.1): a two-column shell redrawn per
step. Left = the step body; right = the always-on context pane."""

from __future__ import annotations

from rich.console import Group, RenderableType
from rich.layout import Layout
from rich.panel import Panel
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
