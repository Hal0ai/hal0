"""rich rendering for `hal0 setup` (spec §6.1): a two-column shell redrawn per
step. Left = the step body; right = the always-on context pane."""

from __future__ import annotations

import asyncio

from rich.console import Console, Group, RenderableType
from rich.layout import Layout
from rich.panel import Panel
from rich.prompt import Confirm, Prompt
from rich.table import Table
from rich.text import Text

from hal0.cli.setup_copy import PANE_COPY
from hal0.config.schema import HardwareInfo
from hal0.install.extensions import EXTENSIONS, get_extension
from hal0.install.orchestrate import Selections, SlotSelection
from hal0.install.suggest import suggest_models

_con = Console()


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


# ── Step machine ───────────────────────────────────────────────────────────────


def _any_agent(extensions: dict) -> bool:
    return any(
        on and (get_extension(eid) is not None and get_extension(eid).kind == "agent")
        for eid, on in extensions.items()
    )


def plan_steps(*, extensions: dict, npu_present: bool) -> list[str]:
    """Ordered list of step keys to show, gated on extension picks (spec §6.3).
    Main shows whenever OWUI OR any agent is enabled; Agent shows iff any agent
    is enabled; NPU shows iff hardware present."""
    steps = ["welcome", "storage", "extensions"]
    needs_main = bool(extensions.get("openwebui")) or _any_agent(extensions)
    if needs_main:
        steps.append("main")
    if _any_agent(extensions):
        steps.append("agent")
    if npu_present:
        steps.append("npu")
    steps += ["review", "install"]
    return steps


# ── Interactive I/O loop ───────────────────────────────────────────────────────


def _hw_footer(hw: HardwareInfo) -> str:
    ram = int((hw.unified_memory_mb or hw.ram_mb) / 1024)
    npu = "NPU ready" if hw.npu.present else "no NPU"
    return f"{hw.platform} · {ram}GB · {npu}"


def _draw(step_key: str, left, hw: HardwareInfo) -> None:
    _con.clear()
    _con.print(render_shell(step_key=step_key, left_body=left, hw_footer=_hw_footer(hw)))


def _choose_model(step_key, capability, hw, *, prefer_coder=False):
    sugg = suggest_models(capability, hw, limit=3, prefer_coder=prefer_coder)
    if not sugg:
        return None
    _draw(step_key, render_suggestion_table(sugg), hw)
    default = next((str(i + 1) for i, s in enumerate(sugg) if s.recommended), "1")
    choice = Prompt.ask(
        "Pick a model", choices=[str(i + 1) for i in range(len(sugg))], default=default
    )
    return sugg[int(choice) - 1]


def _toggle_extensions(state: dict, hw: HardwareInfo) -> None:
    """Numbered-toggle loop (works without raw-tty, e.g. over a pipe)."""
    flat = list(EXTENSIONS)
    while True:
        _draw("extensions", render_extension_checklist(EXTENSIONS, state, cursor=-1), hw)
        ans = Prompt.ask("Toggle by number (comma-separated) or Enter to confirm", default="")
        if not ans.strip():
            return
        for tok in ans.split(","):
            tok = tok.strip()
            if tok.isdigit() and 1 <= int(tok) <= len(flat):
                eid = flat[int(tok) - 1].id
                state[eid] = not state.get(eid, False)


def _review_table(sel: Selections):
    t = Table(title="Will create", expand=True)
    t.add_column("Slot")
    t.add_column("Model")
    t.add_column("Extensions")
    enabled = ", ".join(k for k, v in sel.extensions.items() if v)
    for i, s in enumerate(sel.slots):
        t.add_row(s.slot_name, s.model_id, enabled if i == 0 else "")
    return t


def run_interactive(hw: HardwareInfo, *, storage_dir: str) -> None:
    _draw("welcome", "Detected hardware shown on the right.", hw)
    Prompt.ask("Press Enter to begin", default="")

    _draw("storage", f"Default: {storage_dir}", hw)
    storage_dir = Prompt.ask("Model storage directory", default=storage_dir)

    state = {e.id: e.default_enabled for e in EXTENSIONS}
    _toggle_extensions(state, hw)

    steps = plan_steps(extensions=state, npu_present=bool(hw.npu.present))
    slots: list[SlotSelection] = []
    if "main" in steps:
        m = _choose_model("main", "chat", hw)
        if m:
            slots.append(SlotSelection("chat", "chat", 8081, m.model_id))
    if "agent" in steps:
        a = _choose_model("agent", "coder", hw, prefer_coder=True)
        if a:
            slots.append(SlotSelection("coder", "coder", 8082, a.model_id))
    npu_opt_in = False
    if "npu" in steps:
        _draw("npu", "Run embed + STT + TTS on the NPU?", hw)
        npu_opt_in = Confirm.ask("Enable NPU trio?", default=True)

    sel = Selections(storage_dir=storage_dir, slots=slots, extensions=state, npu_opt_in=npu_opt_in)
    _draw("review", _review_table(sel), hw)
    if not Confirm.ask("Build now?", default=True):
        _con.print("Aborted — nothing was written.")
        return

    from hal0.cli.setup_install import run_install  # Task 4.1 (lazy — not yet present)

    asyncio.run(run_install(sel, hw))
