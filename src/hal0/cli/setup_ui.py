"""rich rendering for `hal0 setup` (spec §6.1): a two-column shell redrawn per
step. Left = the step body; right = the always-on context pane."""

from __future__ import annotations

import asyncio
import os
import sys
from dataclasses import dataclass
from pathlib import Path

import typer
from rich.console import Console, Group, RenderableType
from rich.layout import Layout
from rich.panel import Panel
from rich.prompt import Confirm, Prompt
from rich.table import Table
from rich.text import Text

from hal0.cli.setup_command import _BRAIN_SLOT, _SETUP_SLOTS, _existing_slot_names
from hal0.cli.setup_copy import PANE_COPY
from hal0.cli.setup_plan import _free_space_gib, _port_in_use
from hal0.config.schema import HardwareInfo
from hal0.install.extensions import EXTENSIONS, get_extension
from hal0.install.network import network_env, resolve_hostname
from hal0.install.orchestrate import Selections, SlotSelection
from hal0.install.profile_derive import derive_device, derive_profile, npu_healthy
from hal0.install.suggest import suggest_models
from hal0.registry.curated import get_curated
from hal0.registry.model_store import describe_store_state

#: ComfyUI generation modes (mirrors ``hal0.install.answers._GEN_MODES``). The
#: interactive default is ``scaffold_only`` — wire the slot without downloading
#: weights, so a first install stays fast.
GEN_MODES = ("off", "scaffold_only", "scaffold_and_download")
_GEN_MODES_ON = ("scaffold_only", "scaffold_and_download")

_con = Console()


# ── Raw-key TUI primitives ──────────────────────────────────────────────────
#
# The guided setup is keyboard-driven: ↑↓ (or j/k) to move, space to toggle,
# enter to confirm. That needs raw-tty single-keypress reads. When stdin/stdout
# isn't a terminal — a piped answer file, CI, the test suite — we fall back to
# the numbered ``Prompt.ask`` paths further down (same choices, no arrow keys),
# so automation and tests keep working unchanged.

#: sentinel indices returned by the model picker for the non-model rows.
_SCAFFOLD = "__scaffold__"
_SKIP = "__skip__"


def _interactive() -> bool:
    """True when a raw-tty arrow-key UI is drivable: both std streams are TTYs
    and we're not under pytest or an explicit opt-out (``HAL0_SETUP_NO_TUI``).
    Callers fall back to the numbered ``Prompt.ask`` flow otherwise."""
    if os.environ.get("HAL0_SETUP_NO_TUI") or os.environ.get("PYTEST_CURRENT_TEST"):
        return False
    try:
        return sys.stdin.isatty() and sys.stdout.isatty()
    except Exception:
        return False


def _read_key() -> str:
    """Block for one keypress on a raw tty and return a normalized token:
    ``up`` ``down`` ``left`` ``right`` ``enter`` ``space`` ``esc``, or the
    literal character (digit / letter, lower-cased). Ctrl-C raises
    ``KeyboardInterrupt``. POSIX only — gate on :func:`_interactive` first.

    Arrow keys arrive as a CSI escape sequence (``ESC [ A/B/C/D``); we poll for
    the tail with a short ``select`` timeout so a lone ESC returns promptly
    instead of blocking on bytes that never come."""
    import select
    import termios
    import tty

    fd = sys.stdin.fileno()
    old = termios.tcgetattr(fd)
    try:
        tty.setraw(fd)
        ch = os.read(fd, 1).decode("utf-8", "ignore")
        if ch == "\x03":  # Ctrl-C
            raise KeyboardInterrupt
        if ch in ("\r", "\n"):
            return "enter"
        if ch == " ":
            return "space"
        if ch != "\x1b":
            return ch.lower()
        if not select.select([fd], [], [], 0.05)[0]:
            return "esc"
        if os.read(fd, 1).decode("utf-8", "ignore") != "[":
            return "esc"
        if not select.select([fd], [], [], 0.05)[0]:
            return "esc"
        final = os.read(fd, 1).decode("utf-8", "ignore")
        return {"A": "up", "B": "down", "C": "right", "D": "left"}.get(final, "esc")
    finally:
        termios.tcsetattr(fd, termios.TCSADRAIN, old)


def _nav_footer(*, multi: bool = False) -> Text:
    """Consistent key-hint footer for the interactive pickers."""
    tail = "space toggle · enter confirm" if multi else "enter select"
    return Text(f"↑↓/jk move · {tail}", style="dim")


def _select_index(
    step_key: str,
    hw: HardwareInfo,
    *,
    render,
    count: int,
    default: int = 0,
) -> int:
    """Raw-tty single-select loop. ``render(cursor)`` returns the body for a
    given highlighted row; returns the chosen 0-based index. ↑↓/jk wrap-move,
    enter selects, and a digit key jumps to that 1-based row and selects it."""
    cursor = max(0, min(default, count - 1))
    while True:
        _draw(step_key, render(cursor), hw)
        key = _read_key()
        if key in ("up", "k"):
            cursor = (cursor - 1) % count
        elif key in ("down", "j"):
            cursor = (cursor + 1) % count
        elif key == "enter":
            return cursor
        elif key.isdigit():
            n = int(key)
            if 1 <= n <= count:
                return n - 1


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


def _extensions_in_display_order(extensions) -> list:
    """Apps first, then Agents — the flat order the checklist renders, so a row
    number / cursor index refers to the same entry in the render and in the
    numbered-toggle fallback. ``sorted`` is stable, so original order within
    each kind is preserved."""
    rank = {"app": 0, "agent": 1}
    return sorted(extensions, key=lambda e: rank.get(e.kind, 9))


def render_extension_checklist(extensions, state: dict, cursor: int) -> RenderableType:
    """Grouped Apps/Agents checklist. ``state`` maps id→bool; ``cursor`` is the
    highlighted row index across the flat ordered list (Apps then Agents).
    Pass cursor=-1 for no highlight (the numbered-toggle fallback)."""
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
            lines.append(Text(f" {arrow} {idx + 1}. {mark} {e.name:<12} {e.summary}", style=style))
            idx += 1
    lines.append(Text(""))
    if cursor >= 0:
        lines.append(_nav_footer(multi=True))
    else:
        lines.append(Text("type numbers (comma-separated) · enter confirms", style="dim"))
    return Group(*lines)


def render_provision_picker(slot_name, suggestions, cursor: int) -> RenderableType:
    """Model picker as navigable rows: each fitting model, then an explicit
    'Scaffold empty' and 'Skip' row. ``cursor`` highlights a row across the flat
    list ``[*suggestions, scaffold, skip]``; pass -1 for no highlight.

    Scaffold/skip are real rows (not a dim legend line) so they read cleanly and
    are selectable with the same ↑↓/enter as a model."""
    t = Table(expand=True, box=None, pad_edge=False)
    t.add_column(" ", width=2)  # cursor
    t.add_column(" ", width=3)  # number / key
    t.add_column(" ", width=2)  # recommended star
    t.add_column("Model")
    t.add_column("Size", justify="right")
    t.add_column("Ctx", justify="right")
    t.add_column("Backend")
    row = 0
    for s in suggestions:
        highlight = row == cursor
        t.add_row(
            ">" if highlight else " ",
            f"{row + 1}.",
            "★" if s.recommended else " ",
            s.display_name,
            f"{s.size_gb:.1f}GB",
            f"{s.context_length or '—'}",
            s.profile or "—",
            style="bold yellow" if highlight else "",
        )
        row += 1
    for key, label in (("s)", "Scaffold empty (choose later)"), ("x)", "Skip this slot")):
        highlight = row == cursor
        t.add_row(
            ">" if highlight else " ",
            key,
            " ",
            label,
            "",
            "",
            "",
            style="bold yellow" if highlight else "",
        )
        row += 1
    return Group(Text(f"{slot_name} slot", style="bold"), t, _nav_footer())


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


def plan_steps(*, extensions: dict, npu_present: bool, npu_ok: bool = False) -> list[str]:
    """Ordered list of step keys to show, gated on extension picks (spec §6.3).
    Main shows whenever OWUI OR any agent is enabled; Agent shows iff any agent
    is enabled.

    NPU gating (WS-F / #1109): the ``npu`` enable offer shows ONLY when the NPU
    is present AND healthy (``npu_ok`` — the #1097 ``npu.validated`` fact via
    :func:`~hal0.install.profile_derive.npu_healthy`). A present-but-broken NPU
    gets the ``npu_broken`` remedy step instead of the enable offer, so we never
    advertise an NPU lane the operator can't actually use."""
    steps = ["welcome", "storage", "extensions"]
    needs_main = bool(extensions.get("openwebui")) or _any_agent(extensions)
    if needs_main:
        steps.append("main")
    if _any_agent(extensions):
        steps.append("agent")
    if npu_present:
        steps.append("npu" if npu_ok else "npu_broken")
    # Capability slots (embed/rerank/stt/tts/vision) are always offered — they
    # don't depend on a chat consumer.
    steps.append("capabilities")
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


def _provision_body(slot_name, suggestions) -> RenderableType:
    legend = Text("s) scaffold empty — choose a model later    x) skip this slot", style="dim")
    if suggestions:
        return Group(
            Text(f"{slot_name} slot", style="bold"), render_suggestion_table(suggestions), legend
        )
    return Group(
        Text(f"{slot_name} slot", style="bold"),
        Text("No curated model fits this hardware — scaffold empty or skip.", style="dim"),
        legend,
    )


def _provision_slot(
    step_key, capability, hw, slot_name, port, *, prefer_coder=False, npu_opt_in=False
):
    """Guide the operator through one slot: pick a fitting model, scaffold the
    slot empty (``model_id=None``), or skip it. Returns a ``SlotSelection`` or
    ``None`` to skip. Never auto-selects a model on the operator's behalf.

    ``npu_opt_in`` is passed straight through to :func:`suggest_models` so the
    advertised ``device`` matches the lane ``apply_setup`` will use (e.g. the
    NPU-only ``stt`` slot only shows an NPU device once the operator opted in).

    Arrow-key picker on a real terminal; numbered ``Prompt.ask`` fallback over a
    pipe / in CI / under pytest."""
    sugg = suggest_models(capability, hw, limit=3, prefer_coder=prefer_coder, npu_opt_in=npu_opt_in)
    if _interactive():
        return _provision_slot_tui(step_key, capability, hw, slot_name, port, sugg)
    return _provision_slot_numbered(step_key, capability, hw, slot_name, port, sugg)


def _provision_slot_tui(step_key, capability, hw, slot_name, port, sugg):
    """Arrow-key model picker. Rows are ``[*sugg, scaffold, skip]``; the default
    cursor is the recommended model, else the scaffold row."""
    n = len(sugg)
    default = next((i for i, s in enumerate(sugg) if s.recommended), n)  # scaffold if none fit
    chosen = _select_index(
        step_key,
        hw,
        render=lambda cur: render_provision_picker(slot_name, sugg, cur),
        count=n + 2,
        default=default,
    )
    if chosen < n:
        return SlotSelection(capability, slot_name, port, sugg[chosen].model_id)
    if chosen == n:  # scaffold empty
        return SlotSelection(capability, slot_name, port, None)
    return None  # skip


def _provision_slot_numbered(step_key, capability, hw, slot_name, port, sugg):
    """Numbered fallback (no raw tty): render the legend + a single Prompt.ask."""
    _draw(step_key, _provision_body(slot_name, sugg), hw)
    choices = [str(i + 1) for i in range(len(sugg))] + ["s", "x"]
    # Default to the recommended pick when one fits; otherwise to "scaffold".
    default = next((str(i + 1) for i, s in enumerate(sugg) if s.recommended), "s")
    choice = Prompt.ask(
        f"{slot_name}: model number, [s]caffold empty, or [x] skip",
        choices=choices,
        default=default,
    )
    if choice == "x":
        return None
    if choice == "s":
        return SlotSelection(capability, slot_name, port, None)
    return SlotSelection(capability, slot_name, port, sugg[int(choice) - 1].model_id)


def _toggle_extensions(state: dict, hw: HardwareInfo) -> None:
    """Apps/Agents multi-select. Arrow-key checklist (↑↓/jk move, space toggle,
    enter confirm) on a real terminal; numbered-toggle fallback over a pipe / in
    CI / under pytest."""
    if _interactive():
        _toggle_extensions_tui(state, hw)
    else:
        _toggle_extensions_numbered(state, hw)


def _toggle_extensions_tui(state: dict, hw: HardwareInfo) -> None:
    """Raw-tty checklist: move the cursor, space toggles the row, enter confirms.
    A digit key toggles that 1-based row directly (discoverable shortcut)."""
    flat = _extensions_in_display_order(EXTENSIONS)
    cursor = 0
    while True:
        _draw("extensions", render_extension_checklist(EXTENSIONS, state, cursor=cursor), hw)
        key = _read_key()
        if key in ("up", "k"):
            cursor = (cursor - 1) % len(flat)
        elif key in ("down", "j"):
            cursor = (cursor + 1) % len(flat)
        elif key == "space":
            eid = flat[cursor].id
            state[eid] = not state.get(eid, False)
        elif key == "enter":
            return
        elif key.isdigit():
            n = int(key)
            if 1 <= n <= len(flat):
                eid = flat[n - 1].id
                state[eid] = not state.get(eid, False)


def _toggle_extensions_numbered(state: dict, hw: HardwareInfo) -> None:
    """Numbered-toggle loop (works without raw-tty, e.g. over a pipe)."""
    flat = _extensions_in_display_order(EXTENSIONS)
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


# ── Resolved-choice model ───────────────────────────────────────────────────

#: Ports hal0 itself owns — the API (8080) and the chat-proxy front door
#: (3001). A slot that lands on one of these would shadow a core service.
_RESERVED_PORTS = {8080, 3001}


@dataclass
class NetworkChoice:
    """The bind / mDNS / origins shape chosen in the network step."""

    bind_host: str  # "127.0.0.1" (loopback) | "0.0.0.0" (LAN)
    hostname: str
    public_url: str | None = None


@dataclass
class SetupPlan:
    """Every interactive choice, resolved — the single object the REVIEW gate
    renders and the apply step consumes. Building it writes NOTHING."""

    hw: HardwareInfo
    network: NetworkChoice
    storage_dir: str
    hf_token: str | None
    extensions: dict[str, bool]
    slots: list[SlotSelection]
    npu_opt_in: bool
    gen_mode: str
    comfyui_defaults: tuple[tuple[str, str], ...] = ()

    def selections(self) -> Selections:
        """The apply-core :class:`Selections` view of this plan."""
        return Selections(
            storage_dir=self.storage_dir,
            slots=self.slots,
            extensions=self.extensions,
            npu_opt_in=self.npu_opt_in,
            comfyui_defaults=self.comfyui_defaults,
        )


# ── Step functions (each draws, prompts, returns a choice) ───────────────────


def _step_network(hw: HardwareInfo) -> NetworkChoice:
    """Bind shape + advertised hostname. Nothing is written — the resolved env
    triple is only applied at :func:`_apply` time."""
    _draw("network", "Choose how the dashboard + API are reached.", hw)
    lan = Confirm.ask(
        "Expose hal0 on your LAN (bind 0.0.0.0)? No keeps it loopback-only",
        default=False,
    )
    bind_host = "0.0.0.0" if lan else "127.0.0.1"
    hostname = Prompt.ask("Hostname to advertise (mDNS + origins)", default=resolve_hostname())
    public_url = Prompt.ask("Public reverse-proxy URL (optional, blank to skip)", default="")
    return NetworkChoice(bind_host=bind_host, hostname=hostname, public_url=public_url or None)


def _validate_store(path: str) -> tuple[bool, str]:
    """Return ``(ok, reason)``. A store is usable when it is an absolute path
    that — if it already exists — is a writable directory. A not-yet-created
    path is accepted (the installer / apply step own creation) as long as its
    nearest existing ancestor is reachable (non-zero free space)."""
    path = (path or "").strip()
    if not path:
        return False, "a model store path is required (nothing downloads without one)"
    if not Path(path).is_absolute():
        return False, f"store path {path!r} must be absolute"
    probe = describe_store_state(path)
    if probe.exists and not probe.is_dir:
        return False, f"{path} exists but is not a directory"
    if probe.exists and not probe.writable:
        return False, f"{path} is not writable by this user"
    if not probe.exists and probe.free_bytes == 0:
        return False, f"cannot reach {path} — no writable parent directory exists yet"
    return True, ""


def _step_store(hw: HardwareInfo, default_dir: str) -> str:
    """Mandatory, validated model-store selection. Loops until a usable path is
    entered — this gates every model download downstream."""
    current = default_dir
    while True:
        _draw("storage", f"Default: {current}", hw)
        path = Prompt.ask("Model storage directory", default=current).strip()
        ok, reason = _validate_store(path)
        if ok:
            return path
        _con.print(f"[red]✗ {reason}[/red]")
        current = path or default_dir


def _step_hf_token(hw: HardwareInfo) -> str | None:
    """Optional Hugging Face token for gated pulls. An ``HF_TOKEN`` already in
    the environment wins and is kept without re-prompting."""
    env = os.environ.get("HF_TOKEN") or os.environ.get("HUGGING_FACE_HUB_TOKEN")
    _draw("hf", "Paste a read token for gated models, or skip.", hw)
    if env:
        _con.print("[dim]HF_TOKEN found in the environment — using it.[/dim]")
        return None
    tok = Prompt.ask(
        "Hugging Face token (optional, blank to skip)", default="", password=True
    ).strip()
    return tok or None


#: (label, mode) rows for the generation step — index 1 (scaffold_only) is the
#: default so pressing enter keeps a first install fast.
_GEN_CHOICES = (
    ("off — no image/video generation", "off"),
    ("scaffold only — wire ComfyUI, download weights later  [default]", "scaffold_only"),
    ("scaffold + download — wire ComfyUI and pull default models now", "scaffold_and_download"),
)


def _step_gen(hw: HardwareInfo) -> str:
    """ComfyUI image/video generation mode. Default is scaffold-only (wire the
    slot, download weights later) so a first install stays fast. Arrow-key select
    on a real terminal; numbered ``Prompt.ask`` fallback otherwise."""
    if _interactive():

        def _render(cur: int) -> RenderableType:
            lines: list[RenderableType] = [Text("Image / video generation", style="bold")]
            for i, (label, _mode) in enumerate(_GEN_CHOICES):
                arrow = ">" if i == cur else " "
                style = "bold yellow" if i == cur else ""
                lines.append(Text(f" {arrow} {i + 1}. {label}", style=style))
            lines.append(Text(""))
            lines.append(_nav_footer())
            return Group(*lines)

        idx = _select_index("gen", hw, render=_render, count=len(_GEN_CHOICES), default=1)
        return _GEN_CHOICES[idx][1]

    _draw(
        "gen",
        Group(
            Text("1) off — no image/video generation"),
            Text("2) scaffold only — wire ComfyUI, download weights later  [default]"),
            Text("3) scaffold + download — wire ComfyUI and pull default models now"),
        ),
        hw,
    )
    choice = Prompt.ask("Image/video generation", choices=["1", "2", "3"], default="2")
    return {"1": "off", "2": "scaffold_only", "3": "scaffold_and_download"}[choice]


def _comfyui_defaults() -> tuple[tuple[str, str], ...]:
    """Default ``(capability_id, family)`` pairs for ComfyUI, mirroring
    ``build_auto_selections`` / the answer-file resolver."""
    from hal0.comfyui.capabilities import CAPABILITIES as _CAPS

    return tuple((cap_id, cap.alternatives[0].family) for cap_id, cap in _CAPS.items())


def _fmt_duration(seconds: int) -> str:
    """Compact human ETA for a fetch: ``45s`` / ``6m`` / ``1h05m``."""
    seconds = int(seconds or 0)
    if seconds < 60:
        return f"{seconds}s"
    minutes = seconds // 60
    if minutes < 60:
        return f"{minutes}m"
    hours, minutes = divmod(minutes, 60)
    return f"{hours}h{minutes:02d}m"


def _variant_label(v) -> str:
    """One-line human label for a ComfyUI variant: family + precision + lora."""
    bits = [v.family]
    if v.precision:
        bits.append(v.precision)
    if v.lora:
        bits.append(v.lora)
    return " · ".join(bits)


def _render_gen_variants(cap) -> RenderableType:
    """Per-capability variant table: number, model, size + time estimates.

    Row 1 is the default (first) variant; ``s`` scaffolds the capability without
    downloading anything, so the operator can gate each pull independently."""
    t = Table(title=f"{cap.label}", expand=True)
    t.add_column(" ", width=2)
    t.add_column("Variant")
    t.add_column("Size", justify="right")
    t.add_column("Est. time", justify="right")
    for i, v in enumerate(cap.alternatives):
        star = "★" if i == 0 else " "
        t.add_row(
            star, _variant_label(v), f"~{v.approx_gb:.1f}GB", f"~{_fmt_duration(v.est_seconds)}"
        )
    return Group(
        t, Text("number = download that variant now · s = scaffold (download later)", style="dim")
    )


def _pick_gen_variants(hw: HardwareInfo) -> tuple[tuple[str, str], ...]:
    """Per-capability variant picker for ``scaffold + download`` (WS-G, #1113).

    Walks every ComfyUI capability (txt2img/img2img/txt2video/img2video/upscale),
    shows its variants with size + time estimates, and returns the selected
    ``(capability_id, family)`` picks. Choosing ``s`` (scaffold) for a capability
    omits it — no download is queued for it. The default is the recommended
    (first) variant so pressing Enter through the walk pulls the curated set."""
    from hal0.comfyui.capabilities import CAPABILITIES as _CAPS

    picks: list[tuple[str, str]] = []
    for cap_id, cap in _CAPS.items():
        if _interactive():
            idx = _pick_one_variant_tui(cap, hw)
            if idx is None:  # scaffolded — download later
                continue
            picks.append((cap_id, cap.alternatives[idx].family))
            continue
        _draw("gen", _render_gen_variants(cap), hw)
        choices = [str(i + 1) for i in range(len(cap.alternatives))] + ["s"]
        choice = Prompt.ask(
            f"{cap.label}: variant number or [s]caffold (download later)",
            choices=choices,
            default="1",
        )
        if choice == "s":
            continue
        picks.append((cap_id, cap.alternatives[int(choice) - 1].family))
    return tuple(picks)


def _pick_one_variant_tui(cap, hw):
    """Arrow-key variant picker for a single capability. Rows are the variants
    then a 'Scaffold (download later)' row. Returns the chosen 0-based variant
    index, or ``None`` for scaffold."""
    n = len(cap.alternatives)

    def _render(cur: int) -> RenderableType:
        t = Table(title=f"{cap.label}", expand=True, box=None, pad_edge=False)
        t.add_column(" ", width=2)
        t.add_column(" ", width=3)
        t.add_column("Variant")
        t.add_column("Size", justify="right")
        t.add_column("Est. time", justify="right")
        for i, v in enumerate(cap.alternatives):
            hl = i == cur
            t.add_row(
                ">" if hl else " ",
                f"{i + 1}.",
                _variant_label(v),
                f"~{v.approx_gb:.1f}GB",
                f"~{_fmt_duration(v.est_seconds)}",
                style="bold yellow" if hl else "",
            )
        hl = cur == n
        t.add_row(
            ">" if hl else " ",
            "s)",
            "Scaffold (download later)",
            "",
            "",
            style="bold yellow" if hl else "",
        )
        return Group(t, _nav_footer())

    idx = _select_index("gen", hw, render=_render, count=n + 1, default=0)
    return None if idx == n else idx


# ── REVIEW gate ──────────────────────────────────────────────────────────────


def _slot_size_gb(model_id: str | None) -> float:
    """Curated download size for a model id (0.0 for a scaffold / unknown)."""
    if not model_id:
        return 0.0
    curated = get_curated(model_id)
    return float(getattr(curated, "size_gb", 0.0) or 0.0) if curated else 0.0


def _port_issues(slots: list[SlotSelection]) -> list[str]:
    """Human-readable port problems: duplicates within the plan, reserved-port
    shadows, and ports already bound on this host."""
    issues: list[str] = []
    seen: dict[int, str] = {}
    for s in slots:
        if s.port in seen:
            issues.append(f"port {s.port}: '{seen[s.port]}' and '{s.slot_name}' both claim it")
        seen[s.port] = s.slot_name
    for s in slots:
        if s.port in _RESERVED_PORTS:
            issues.append(f"port {s.port} ('{s.slot_name}') is a reserved hal0 port")
        elif _port_in_use(s.port):
            issues.append(f"port {s.port} ('{s.slot_name}') is already in use on this host")
    return issues


def render_review(plan: SetupPlan) -> RenderableType:
    """Accurate 'will create' summary the REVIEW gate shows. Reflects the
    resolved plan exactly — slots (with derived device/profile + a live port
    check), store + free space, bind / mDNS / origins, HF token, gen mode,
    extensions, and total download size. Renders only; writes nothing."""
    hw, net = plan.hw, plan.network
    free_gib = _free_space_gib(plan.storage_dir)
    free_txt = f"{free_gib:.1f} GiB free" if free_gib is not None else "free space unknown"

    facts = Table.grid(padding=(0, 2))
    facts.add_column(style="bold")
    facts.add_column()
    facts.add_row("Model store", f"{plan.storage_dir}  ({free_txt})")
    bind_label = "LAN (0.0.0.0)" if net.bind_host == "0.0.0.0" else "loopback (127.0.0.1)"
    facts.add_row("Bind", bind_label)
    facts.add_row("mDNS", f"{net.hostname}.local")
    origins = network_env(
        bind_host=net.bind_host, hostname=net.hostname, public_url=net.public_url
    )["HAL0_ALLOWED_ORIGINS"]
    facts.add_row("Origins", f"{len(origins.split(','))} allowed")
    facts.add_row("HF token", "set" if plan.hf_token else "not set (open models only)")
    facts.add_row("Gen", plan.gen_mode)

    slot_table = Table(title="Slots to create", expand=True)
    slot_table.add_column("Name")
    slot_table.add_column("Port", justify="right")
    slot_table.add_column("Model")
    slot_table.add_column("Device")
    slot_table.add_column("Profile")
    slot_table.add_column("Size", justify="right")
    slot_table.add_column("Port state")
    total_gb = 0.0
    for s in plan.slots:
        device = s.device or derive_device(s.capability, hw, npu_opt_in=plan.npu_opt_in)
        profile = s.profile or (derive_profile(s.capability, device) if device else None)
        size = _slot_size_gb(s.model_id)
        total_gb += size
        clash = s.port in _RESERVED_PORTS or _port_in_use(s.port)
        slot_table.add_row(
            s.slot_name,
            str(s.port),
            s.model_id or "(scaffold — choose later)",
            device or "—",
            profile or "—",
            f"{size:.1f}GB" if size else "—",
            "[red]IN USE[/red]" if clash else "free",
        )
    if not plan.slots:
        slot_table.add_row("(none)", "", "", "", "", "", "")

    enabled = ", ".join(k for k, v in plan.extensions.items() if v) or "(none)"
    parts: list[RenderableType] = [
        facts,
        Text(""),
        slot_table,
        Text(f"Apps/agents: {enabled}"),
        Text(f"Total download: ~{total_gb:.1f} GB"),
    ]
    # Opt-in ComfyUI downloads (scaffold + download): surface the extra pull cost
    # so the size/time estimate gates the decision at the review gate too.
    if plan.gen_mode == "scaffold_and_download" and plan.comfyui_defaults:
        from hal0.comfyui.provision import estimate_totals, resolve_variants

        gen_variants, _unknown = resolve_variants(plan.comfyui_defaults)
        gen_gb, gen_s = estimate_totals(gen_variants)
        parts.append(
            Text(
                f"Generation models: {len(gen_variants)} variant(s), "
                f"~{gen_gb:.1f} GB, ~{_fmt_duration(gen_s)}"
            )
        )
    issues = _port_issues(plan.slots)
    if issues:
        parts.append(Text(""))
        parts.append(Text("Port conflicts:", style="bold red"))
        parts.extend(Text(f"  • {msg}", style="red") for msg in issues)
    parts.append(Text(""))
    parts.append(Text("Nothing has been written yet.", style="dim"))
    return Group(*parts)


# ── Apply / verify ───────────────────────────────────────────────────────────


def _persist_store(storage_dir: str) -> None:
    """Persist ``[models].store`` so downloads land in the chosen store. Best-
    effort: a non-root ``hal0 setup`` that can't write hal0.toml still proceeds
    (the daemon persists it later)."""
    try:
        from hal0.config.loader import load_hal0_config, save_hal0_config

        cfg = load_hal0_config()
        merged_models = cfg.models.model_copy(update={"store": storage_dir})
        save_hal0_config(cfg.model_copy(update={"models": merged_models}))
    except Exception as exc:  # best-effort — never abort the apply on this
        _con.print(f"[yellow]note: could not persist model store to hal0.toml ({exc})[/yellow]")


def _verify_report(plan: SetupPlan) -> None:
    """Post-apply confirmation: what landed on disk (sentinel + slot count) and
    how hal0 is reached."""
    from hal0.config import paths

    sentinel = paths.var_lib() / ".first_run_done"
    typer.echo("")
    typer.echo("Setup verification:")
    typer.echo(f"  model store         {plan.storage_dir}")
    typer.echo(f"  bind / mDNS         {plan.network.bind_host} · {plan.network.hostname}.local")
    typer.echo(f"  slots configured    {len(plan.slots)}")
    typer.echo(f"  first-run sentinel  {'written' if sentinel.exists() else 'MISSING'}")


def _apply(plan: SetupPlan) -> None:
    """Persist the network + store choices, then run the hybrid apply. Env
    exports (network + HF token) happen HERE — never during plan building — so a
    'No' at the review gate leaves the environment untouched too."""
    os.environ.update(
        network_env(
            bind_host=plan.network.bind_host,
            hostname=plan.network.hostname,
            public_url=plan.network.public_url,
        )
    )
    if plan.hf_token:
        os.environ["HF_TOKEN"] = plan.hf_token
    _persist_store(plan.storage_dir)

    from hal0.cli.setup_install import run_install

    asyncio.run(run_install(plan.selections(), plan.hw))
    _provision_gen_downloads(plan)
    _verify_report(plan)


def _provision_gen_downloads(plan: SetupPlan) -> None:
    """Queue the ComfyUI per-variant fetch for a ``scaffold + download`` plan and
    activate the img slot once the first model lands (WS-G, #1113).

    No-op for ``off`` / ``scaffold_only`` — those never download here. Reuses the
    working fetch (#1110) via :func:`hal0.comfyui.provision.provision_comfyui_downloads`;
    the img slot flips live on the first landed model (enable-on-pull-success,
    #1108). Progress prints one line per settled variant."""
    if plan.gen_mode != "scaffold_and_download" or not plan.comfyui_defaults:
        return
    from hal0.comfyui.provision import (
        estimate_totals,
        provision_comfyui_downloads,
        resolve_variants,
    )

    variants, _unknown = resolve_variants(plan.comfyui_defaults)
    total_gb, total_s = estimate_totals(variants)
    typer.echo(
        f"Downloading {len(variants)} generation model(s) "
        f"(~{total_gb:.1f} GB, ~{_fmt_duration(total_s)}). The img slot activates "
        "when the first model lands…"
    )

    def _on_status(_job_id, variant, status) -> None:
        mark = "done" if status == "done" else status.upper()
        typer.echo(f"  {_variant_label(variant)}: {mark}")

    result = provision_comfyui_downloads(plan.comfyui_defaults, on_status=_on_status)
    if result.activated:
        typer.echo(f"Image generation ready — {len(result.landed)} model(s) landed.")
    elif result.failed:
        typer.echo(
            "No generation model finished downloading — the img slot stays inactive. "
            "Retry later from the dashboard (Image-Gen tab).",
            err=True,
        )


def run_interactive(hw: HardwareInfo, *, storage_dir: str) -> None:
    """The guided Stage-2 flow (issue #1112). Decision tree, in order:
    Platform → network → model store (mandatory) → HF token → apps → LLM slots
    → NPU intro → capability slots → ComfyUI/gen → REVIEW gate → apply →
    sentinel → verify. Choosing 'No' at the REVIEW gate writes NOTHING."""
    # 1. Platform Report
    _draw("welcome", "Detected hardware shown on the right.", hw)
    Prompt.ask("Press Enter to begin", default="")

    # 2. Network shape (bind / mDNS / origins)
    network = _step_network(hw)

    # 3. Model store — MANDATORY + validated (gates every download)
    storage_dir = _step_store(hw, storage_dir)

    # 4. Hugging Face token (optional)
    hf_token = _step_hf_token(hw)

    # 5. Apps / agents (gates which LLM slots are offered — spec §6.3)
    state = {e.id: e.default_enabled for e in EXTENSIONS}
    _toggle_extensions(state, hw)

    steps = plan_steps(extensions=state, npu_present=bool(hw.npu.present), npu_ok=npu_healthy(hw))

    # 6. LLM slots (main + coder)
    slots: list[SlotSelection] = []
    if "main" in steps:
        name, port = _SETUP_SLOTS["chat"]
        s = _provision_slot("main", "chat", hw, name, port)
        if s:
            slots.append(s)
        # The steward's `brain` slot rides along silently — an empty
        # chat-capability scaffold (no model prompt): the sidebar agent
        # chat targets hal0/brain and falls back to `agent` until the
        # operator binds a model. Never overwrites an existing config.
        brain_name, brain_port = _BRAIN_SLOT
        if brain_name not in _existing_slot_names():
            slots.append(SlotSelection("chat", brain_name, brain_port, None))
    if "agent" in steps:
        name, port = _SETUP_SLOTS["coder"]
        s = _provision_slot("agent", "coder", hw, name, port, prefer_coder=True)
        if s:
            slots.append(s)

    # 7. NPU intro (only when present + healthy)
    npu_opt_in = False
    if "npu" in steps:
        # Present + healthy: offer to route STT to the NPU. This is what
        # apply_setup actually provisions on opt-in (derive_device sends
        # stt→npu; embed stays on the GPU lane, tts on cpu). The full FLM
        # trio (chat/ASR/embed on one column) is a per-slot dashboard
        # setting, not a setup-time selection — don't promise it here.
        _draw("npu", "Run speech-to-text on the NPU?", hw)
        npu_opt_in = Confirm.ask("Enable NPU inference (STT offload)?", default=True)
    elif "npu_broken" in steps:
        # Present but NOT healthy (npu.validated is False/None): show the remedy,
        # never the enable offer — apply_setup would skip an NPU lane here.
        _draw(
            "npu_broken",
            Text(
                "NPU detected but functional validation failed — leaving it off. "
                "See the remedy on the right, then re-run `hal0 setup` once it validates.",
                style="yellow",
            ),
            hw,
        )
        Prompt.ask("Press Enter to continue", default="")

    # 8. Capability slots (embed/rerank/tts/vision, +stt when the NPU trio is on)
    if "capabilities" in steps:
        # STT is NPU-only (derive_device returns None without an NPU), so only
        # offer it when the NPU trio is on.
        caps = ["embed", "rerank", "tts", "vision"]
        if npu_opt_in:
            caps.insert(2, "stt")
        for cap in caps:
            name, port = _SETUP_SLOTS[cap]
            s = _provision_slot("capabilities", cap, hw, name, port, npu_opt_in=npu_opt_in)
            if s:
                slots.append(s)

    # 9. ComfyUI / image+video generation — gen.mode drives the comfyui extension
    gen_mode = _step_gen(hw)
    if gen_mode == "scaffold_and_download":
        # Opt-in downloads: a per-capability variant picker (size + time) gates
        # each pull. The engine is NOT wired up-front — activation is deferred to
        # enable-on-pull-success (after the first model lands, in _apply), so we
        # never advertise an empty img slot. If every variant is scaffolded
        # (nothing picked), fall back to wiring the engine now (grey, no models).
        comfyui_defaults = _pick_gen_variants(hw)
        state["comfyui"] = not comfyui_defaults
    elif gen_mode == "scaffold_only":
        comfyui_defaults = _comfyui_defaults()
        state["comfyui"] = True
    else:  # off
        comfyui_defaults = ()
        state["comfyui"] = False

    plan = SetupPlan(
        hw=hw,
        network=network,
        storage_dir=storage_dir,
        hf_token=hf_token,
        extensions=state,
        slots=slots,
        npu_opt_in=npu_opt_in,
        gen_mode=gen_mode,
        comfyui_defaults=comfyui_defaults,
    )

    # 10. REVIEW gate — accurate 'will create' table; 'No' writes NOTHING
    _draw("review", render_review(plan), hw)
    if not Confirm.ask("Build now?", default=True):
        _con.print("Aborted — nothing was written (no slots, sentinel, or downloads).")
        return

    # 11-13. apply → sentinel → verify
    _apply(plan)

    # 14. WS-K report card (#1114): pass/warn/fail over the live health seams +
    # computed URLs + help links. A SINGLE localized, best-effort call — the
    # report is non-blocking, so a failure here must never abort a good setup.
    try:
        from hal0.cli.doctor_verify import run_verify

        run_verify(console=_con)
    except Exception as exc:  # pragma: no cover — defensive, never fail setup
        _con.print(f"[dim]setup verify skipped ({exc})[/dim]")
