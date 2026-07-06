from rich.console import Console

from hal0.cli.setup_copy import PANE_COPY
from hal0.cli.setup_ui import (
    plan_steps,
    render_extension_checklist,
    render_shell,
    render_suggestion_table,
)
from hal0.install.extensions import EXTENSIONS
from hal0.install.suggest import Suggestion


def test_pane_copy_has_every_step():
    for key in (
        "welcome",
        "storage",
        "extensions",
        "main",
        "agent",
        "npu",
        "capabilities",
        "review",
        "install",
    ):
        assert key in PANE_COPY and PANE_COPY[key].body


def test_capabilities_step_always_planned():
    steps = plan_steps(extensions={"openwebui": True}, npu_present=False)
    assert "capabilities" in steps


def test_provision_slot_pick_scaffold_and_skip(monkeypatch):
    from hal0.cli import setup_ui
    from hal0.install.suggest import Suggestion

    sugg = [
        Suggestion(
            "m1", "M1", 1.0, 0.0, 4096, "gpu-rocm", "embed", "embed", False, recommended=True
        )
    ]
    monkeypatch.setattr(setup_ui, "suggest_models", lambda *a, **k: sugg)
    monkeypatch.setattr(setup_ui, "_draw", lambda *a, **k: None)

    monkeypatch.setattr(setup_ui.Prompt, "ask", lambda *a, **k: "x")
    assert setup_ui._provision_slot("capabilities", "embed", None, "embed", 8083) is None

    monkeypatch.setattr(setup_ui.Prompt, "ask", lambda *a, **k: "s")
    scaffold = setup_ui._provision_slot("capabilities", "embed", None, "embed", 8083)
    assert scaffold is not None and scaffold.slot_name == "embed" and scaffold.model_id is None

    monkeypatch.setattr(setup_ui.Prompt, "ask", lambda *a, **k: "1")
    picked = setup_ui._provision_slot("capabilities", "embed", None, "embed", 8083)
    assert picked.model_id == "m1"


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


def test_extension_checklist_marks_enabled():
    state = {"openwebui": True, "hermes": True, "pi": False}
    r = render_extension_checklist(EXTENSIONS, state, cursor=0)
    con = Console(width=80, record=True)
    con.print(r)
    text = con.export_text()
    assert "Open WebUI" in text and "Hermes" in text and "Pi" in text
    assert "Apps" in text and "Agents" in text


def test_suggestion_table_stars_recommended():
    sugg = [
        Suggestion(
            "qwen3-4b",
            "Qwen3 4B",
            2.4,
            0.0,
            32768,
            "gpu-rocm",
            "rocm",
            "chat",
            False,
            recommended=True,
        )
    ]
    con = Console(width=80, record=True)
    con.print(render_suggestion_table(sugg))
    assert "Qwen3 4B" in con.export_text()


def test_no_agent_skips_agent_step():
    steps = plan_steps(
        extensions={"openwebui": True, "hermes": False, "pi": False}, npu_present=True
    )
    assert "agent" not in steps
    assert "main" in steps  # OWUI on → main shown


def test_agent_on_shows_agent_and_main():
    steps = plan_steps(
        extensions={"openwebui": False, "hermes": True, "pi": False}, npu_present=True
    )
    assert "main" in steps and "agent" in steps  # agent routes to main too


def test_nothing_consuming_chat_hides_main():
    steps = plan_steps(
        extensions={"openwebui": False, "hermes": False, "pi": False}, npu_present=False
    )
    assert "main" not in steps and "agent" not in steps and "npu" not in steps


def test_no_npu_skips_npu_step():
    steps = plan_steps(extensions={"openwebui": True}, npu_present=False)
    assert "npu" not in steps
