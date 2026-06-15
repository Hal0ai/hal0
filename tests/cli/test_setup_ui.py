from rich.console import Console

from hal0.cli.setup_copy import PANE_COPY
from hal0.cli.setup_ui import render_shell


def test_pane_copy_has_every_step():
    for key in ("welcome", "storage", "extensions", "main", "agent", "npu", "review", "install"):
        assert key in PANE_COPY and PANE_COPY[key].body


def test_render_shell_includes_step_and_pane_text():
    con = Console(width=100, record=True)
    con.print(
        render_shell(
            step_key="extensions", left_body="PICK APPS HERE", hw_footer="Strix Halo · 96GB · NPU"
        )
    )
    text = con.export_text()
    assert "PICK APPS HERE" in text
    assert "one-shot" in text.lower()  # extensions pane headline copy
    assert "Strix Halo" in text
