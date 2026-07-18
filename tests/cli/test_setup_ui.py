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
    state = {"openwebui": True, "hermes": True}
    r = render_extension_checklist(EXTENSIONS, state, cursor=0)
    con = Console(width=80, record=True)
    con.print(r)
    text = con.export_text()
    assert "Open WebUI" in text and "Hermes" in text
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
    steps = plan_steps(extensions={"openwebui": True, "hermes": False}, npu_present=True)
    assert "agent" not in steps
    assert "main" in steps  # OWUI on → main shown


def test_agent_on_shows_agent_and_main():
    steps = plan_steps(extensions={"openwebui": False, "hermes": True}, npu_present=True)
    assert "main" in steps and "agent" in steps  # agent routes to main too


def test_nothing_consuming_chat_hides_main():
    steps = plan_steps(extensions={"openwebui": False, "hermes": False}, npu_present=False)
    assert "main" not in steps and "agent" not in steps and "npu" not in steps


def test_no_npu_skips_npu_step():
    steps = plan_steps(extensions={"openwebui": True}, npu_present=False)
    assert "npu" not in steps
    assert "npu_broken" not in steps


def test_present_and_healthy_npu_shows_enable_offer():
    # present + healthy → the "npu" enable offer, NOT the remedy (#1109).
    steps = plan_steps(extensions={"openwebui": True}, npu_present=True, npu_ok=True)
    assert "npu" in steps and "npu_broken" not in steps


def test_present_but_broken_npu_shows_remedy_not_offer():
    # present but NOT healthy → the "npu_broken" remedy, never the enable offer.
    steps = plan_steps(extensions={"openwebui": True}, npu_present=True, npu_ok=False)
    assert "npu_broken" in steps and "npu" not in steps


def test_pane_copy_has_npu_broken_remedy():
    assert "npu_broken" in PANE_COPY and PANE_COPY["npu_broken"].body


# ── WS-F guided flow (issue #1112) ──────────────────────────────────────────


def _hw(npu=False, validated=None, ram_gb=96):
    from hal0.config.schema import GPUInfo, HardwareInfo, NPUInfo

    return HardwareInfo(
        platform="strix-halo",
        ram_mb=ram_gb * 1024,
        unified_memory_mb=ram_gb * 1024,
        gpus=[GPUInfo(vendor="amd", vram_mb=512, compute_capable=True)],
        npu=NPUInfo(present=npu, validated=validated),
    )


def test_pane_copy_has_new_flow_steps():
    for key in ("network", "hf", "gen", "verify"):
        assert key in PANE_COPY and PANE_COPY[key].body


def test_validate_store_gate():
    from hal0.cli import setup_ui

    ok, reason = setup_ui._validate_store("")
    assert not ok and "required" in reason
    ok, _ = setup_ui._validate_store("relative/path")
    assert not ok  # must be absolute


def test_validate_store_accepts_writable_dir(tmp_path):
    from hal0.cli import setup_ui

    ok, reason = setup_ui._validate_store(str(tmp_path))
    assert ok and reason == ""


def test_step_gen_mode_mapping(monkeypatch):
    from hal0.cli import setup_ui

    monkeypatch.setattr(setup_ui, "_draw", lambda *a, **k: None)
    for pick, expected in (("1", "off"), ("2", "scaffold_only"), ("3", "scaffold_and_download")):
        monkeypatch.setattr(setup_ui.Prompt, "ask", lambda *a, _p=pick, **k: _p)
        assert setup_ui._step_gen(_hw()) == expected


def test_port_issues_flags_duplicates_and_reserved(monkeypatch):
    from hal0.cli import setup_ui
    from hal0.install.orchestrate import SlotSelection

    monkeypatch.setattr(setup_ui, "_port_in_use", lambda p: False)
    dupes = setup_ui._port_issues(
        [SlotSelection("chat", "chat", 9000, "m"), SlotSelection("embed", "embed", 9000, None)]
    )
    assert any("both claim it" in m for m in dupes)
    reserved = setup_ui._port_issues([SlotSelection("chat", "chat", 8080, "m")])
    assert any("reserved" in m for m in reserved)


def test_setup_plan_selections_roundtrip():
    from hal0.cli.setup_ui import NetworkChoice, SetupPlan
    from hal0.install.orchestrate import SlotSelection

    plan = SetupPlan(
        hw=_hw(),
        network=NetworkChoice("0.0.0.0", "box", None),
        storage_dir="/srv/models",
        hf_token="tok",
        extensions={"openwebui": True, "comfyui": False},
        slots=[SlotSelection("chat", "chat", 8081, "m1")],
        npu_opt_in=False,
        gen_mode="scaffold_only",
        comfyui_defaults=(("txt2img", "sdxl"),),
    )
    sel = plan.selections()
    assert sel.storage_dir == "/srv/models"
    assert sel.comfyui_defaults == (("txt2img", "sdxl"),)
    assert sel.extensions == {"openwebui": True, "comfyui": False}
    assert [s.model_id for s in sel.slots] == ["m1"]


def test_render_review_shows_slots_store_bind_and_download(monkeypatch):
    from rich.console import Console

    from hal0.cli import setup_ui
    from hal0.cli.setup_ui import NetworkChoice, SetupPlan
    from hal0.install.orchestrate import SlotSelection

    monkeypatch.setattr(setup_ui, "_port_in_use", lambda p: False)
    monkeypatch.setattr(setup_ui, "_free_space_gib", lambda p: 512.0)
    monkeypatch.setattr(setup_ui, "_slot_size_gb", lambda mid: 4.0 if mid else 0.0)

    plan = SetupPlan(
        hw=_hw(),
        network=NetworkChoice("0.0.0.0", "mybox", None),
        storage_dir="/mnt/ai-models",
        hf_token="tok",
        extensions={"openwebui": True, "comfyui": True},
        slots=[
            SlotSelection("chat", "chat", 8081, "qwen3-4b"),
            SlotSelection("embed", "embed", 8083, None),
        ],
        npu_opt_in=False,
        gen_mode="scaffold_only",
    )
    con = Console(width=120, record=True)
    con.print(setup_ui.render_review(plan))
    text = con.export_text()
    assert "/mnt/ai-models" in text and "512.0 GiB free" in text
    assert "LAN (0.0.0.0)" in text and "mybox.local" in text
    assert "qwen3-4b" in text and "8081" in text
    assert "scaffold — choose later" in text  # the empty embed slot
    assert "set" in text  # HF token
    assert "~4.0 GB" in text  # total download


def _stub_flow(monkeypatch, *, build: bool):
    """Stub every interactive step so run_interactive is deterministic. Returns
    a dict recording whether _apply ran."""
    from hal0.cli import setup_ui
    from hal0.cli.setup_ui import NetworkChoice

    rec: dict = {"applied": None}
    monkeypatch.setattr(setup_ui, "_draw", lambda *a, **k: None)
    monkeypatch.setattr(setup_ui, "_step_network", lambda hw: NetworkChoice("127.0.0.1", "h", None))
    monkeypatch.setattr(setup_ui, "_step_store", lambda hw, d: "/var/lib/hal0/models")
    monkeypatch.setattr(setup_ui, "_step_hf_token", lambda hw: None)
    monkeypatch.setattr(setup_ui, "_toggle_extensions", lambda state, hw: None)
    monkeypatch.setattr(setup_ui, "_provision_slot", lambda *a, **k: None)
    monkeypatch.setattr(setup_ui, "_step_gen", lambda hw: "off")
    monkeypatch.setattr(setup_ui.Confirm, "ask", lambda *a, **k: build)
    monkeypatch.setattr(setup_ui.Prompt, "ask", lambda *a, **k: "")
    monkeypatch.setattr(setup_ui, "_apply", lambda plan: rec.__setitem__("applied", plan))
    # WS-K auto-run (#1114): stub the report card so run_interactive stays offline
    # + deterministic; record that the hook fired.
    from hal0.cli import doctor_verify

    monkeypatch.setattr(
        doctor_verify, "run_verify", lambda **k: rec.__setitem__("verified", True) or 0
    )
    return setup_ui, rec


def test_review_no_writes_nothing(monkeypatch):
    # Build? == No → _apply is never called (no slots, sentinel, or pulls).
    setup_ui, rec = _stub_flow(monkeypatch, build=False)
    setup_ui.run_interactive(_hw(), storage_dir="/var/lib/hal0/models")
    assert rec["applied"] is None


def test_review_yes_applies_plan(monkeypatch):
    # Build? == Yes → _apply runs with the resolved plan.
    setup_ui, rec = _stub_flow(monkeypatch, build=True)
    setup_ui.run_interactive(_hw(), storage_dir="/var/lib/hal0/models")
    assert rec["applied"] is not None
    assert rec["applied"].storage_dir == "/var/lib/hal0/models"
    assert rec["applied"].gen_mode == "off"


def test_apply_auto_runs_verify_report(monkeypatch):
    # WS-K (#1114): after a Yes-apply, the verify report card auto-runs.
    setup_ui, rec = _stub_flow(monkeypatch, build=True)
    setup_ui.run_interactive(_hw(), storage_dir="/var/lib/hal0/models")
    assert rec.get("verified") is True


def test_no_apply_skips_verify_report(monkeypatch):
    # Aborting at the REVIEW gate must not auto-run the report either.
    setup_ui, rec = _stub_flow(monkeypatch, build=False)
    setup_ui.run_interactive(_hw(), storage_dir="/var/lib/hal0/models")
    assert rec.get("verified") is None


# ── WS-G ComfyUI gen branch (issue #1113) ────────────────────────────────────


def test_fmt_duration_buckets():
    from hal0.cli import setup_ui

    assert setup_ui._fmt_duration(45) == "45s"
    assert setup_ui._fmt_duration(360) == "6m"
    assert setup_ui._fmt_duration(3900) == "1h05m"


def test_render_gen_variants_shows_size_and_time():
    from hal0.cli import setup_ui
    from hal0.comfyui.capabilities import CAPABILITIES

    con = Console(width=100, record=True)
    con.print(setup_ui._render_gen_variants(CAPABILITIES["txt2img"]))
    text = con.export_text()
    assert "qwen-image" in text and "sdxl" in text
    assert "GB" in text  # size estimate column
    assert "~" in text  # time estimate


def test_pick_gen_variants_picks_and_scaffolds(monkeypatch):
    from hal0.cli import setup_ui

    monkeypatch.setattr(setup_ui, "_draw", lambda *a, **k: None)
    # txt2img→2 (second variant), img2img→1, everything else→scaffold ("s").
    answers = iter(["2", "1", "s", "s", "s"])
    monkeypatch.setattr(setup_ui.Prompt, "ask", lambda *a, **k: next(answers))
    picks = setup_ui._pick_gen_variants(_hw())
    from hal0.comfyui.capabilities import CAPABILITIES

    assert picks[0][0] == "txt2img"
    # choice "2" → the 2nd txt2img alternative (family from the registry order).
    assert picks[0][1] == CAPABILITIES["txt2img"].alternatives[1].family
    assert picks[1][0] == "img2img"
    assert len(picks) == 2  # the three "s" caps were scaffolded (omitted)


def test_scaffold_and_download_defers_engine_and_runs_picker(monkeypatch):
    setup_ui2, rec = _stub_flow(monkeypatch, build=True)
    monkeypatch.setattr(setup_ui2, "_step_gen", lambda hw: "scaffold_and_download")
    monkeypatch.setattr(setup_ui2, "_pick_gen_variants", lambda hw: (("txt2img", "sdxl"),))
    setup_ui2.run_interactive(_hw(), storage_dir="/var/lib/hal0/models")
    plan = rec["applied"]
    assert plan.gen_mode == "scaffold_and_download"
    assert plan.comfyui_defaults == (("txt2img", "sdxl"),)
    # Engine activation deferred to enable-on-pull-success → not pre-enabled.
    assert plan.extensions["comfyui"] is False


def test_provision_gen_downloads_noop_for_scaffold_only(monkeypatch):
    from hal0.cli import setup_ui
    from hal0.cli.setup_ui import NetworkChoice, SetupPlan

    called = {"n": 0}
    import hal0.comfyui.provision as prov

    monkeypatch.setattr(
        prov, "provision_comfyui_downloads", lambda *a, **k: called.__setitem__("n", 1)
    )
    plan = SetupPlan(
        hw=_hw(),
        network=NetworkChoice("127.0.0.1", "h", None),
        storage_dir="/s",
        hf_token=None,
        extensions={"comfyui": True},
        slots=[],
        npu_opt_in=False,
        gen_mode="scaffold_only",
        comfyui_defaults=(("txt2img", "sdxl"),),
    )
    setup_ui._provision_gen_downloads(plan)
    assert called["n"] == 0  # scaffold_only never downloads here


def test_provision_gen_downloads_runs_for_scaffold_and_download(monkeypatch):
    from hal0.cli import setup_ui
    from hal0.cli.setup_ui import NetworkChoice, SetupPlan
    from hal0.comfyui.provision import ProvisionResult

    seen = {}

    def fake_provision(defaults, **kw):
        seen["defaults"] = defaults
        return ProvisionResult(landed=["sdxl"], activated=True)

    monkeypatch.setattr(setup_ui, "_fmt_duration", lambda s: "1m")
    import hal0.comfyui.provision as prov

    monkeypatch.setattr(prov, "provision_comfyui_downloads", fake_provision)
    plan = SetupPlan(
        hw=_hw(),
        network=NetworkChoice("127.0.0.1", "h", None),
        storage_dir="/s",
        hf_token=None,
        extensions={"comfyui": False},
        slots=[],
        npu_opt_in=False,
        gen_mode="scaffold_and_download",
        comfyui_defaults=(("txt2img", "sdxl"),),
    )
    setup_ui._provision_gen_downloads(plan)
    assert seen["defaults"] == (("txt2img", "sdxl"),)


# ── Raw-key arrow-nav TUI (feat/setup-tui-arrow-keys) ────────────────────────


def _feed_keys(monkeypatch, keys):
    """Force _interactive() true, silence _draw, and make _read_key() yield the
    given normalized key tokens in order. Returns the setup_ui module."""
    from hal0.cli import setup_ui

    monkeypatch.setattr(setup_ui, "_interactive", lambda: True)
    monkeypatch.setattr(setup_ui, "_draw", lambda *a, **k: None)
    seq = iter(keys)
    monkeypatch.setattr(setup_ui, "_read_key", lambda: next(seq))
    return setup_ui


def test_read_key_decodes_arrows_enter_space(monkeypatch):
    import select as _select
    import termios
    import tty

    from hal0.cli import setup_ui

    monkeypatch.setattr(termios, "tcgetattr", lambda fd: None)
    monkeypatch.setattr(termios, "tcsetattr", lambda fd, when, old: None)
    monkeypatch.setattr(tty, "setraw", lambda fd: None)

    class _Stdin:
        def fileno(self):
            return 0

    monkeypatch.setattr(setup_ui.sys, "stdin", _Stdin())

    def run(byte_seq):
        data = iter(byte_seq)
        monkeypatch.setattr(setup_ui.os, "read", lambda fd, n: next(data))
        monkeypatch.setattr(_select, "select", lambda r, w, x, t: (list(r), [], []))
        return setup_ui._read_key()

    assert run([b"\x1b", b"[", b"A"]) == "up"
    assert run([b"\x1b", b"[", b"B"]) == "down"
    assert run([b"\r"]) == "enter"
    assert run([b"\n"]) == "enter"
    assert run([b" "]) == "space"
    assert run([b"K"]) == "k"  # letters are lower-cased


def test_read_key_lone_esc_does_not_block(monkeypatch):
    import select as _select
    import termios
    import tty

    from hal0.cli import setup_ui

    monkeypatch.setattr(termios, "tcgetattr", lambda fd: None)
    monkeypatch.setattr(termios, "tcsetattr", lambda fd, when, old: None)
    monkeypatch.setattr(tty, "setraw", lambda fd: None)

    class _Stdin:
        def fileno(self):
            return 0

    monkeypatch.setattr(setup_ui.sys, "stdin", _Stdin())
    monkeypatch.setattr(setup_ui.os, "read", lambda fd, n: b"\x1b")
    # no follow-on bytes ready → returns 'esc' instead of blocking
    monkeypatch.setattr(_select, "select", lambda r, w, x, t: ([], [], []))
    assert setup_ui._read_key() == "esc"


def test_toggle_extensions_tui_moves_and_toggles(monkeypatch):
    from hal0.install.extensions import EXTENSIONS

    setup_ui = _feed_keys(monkeypatch, ["down", "space", "enter"])
    order = setup_ui._extensions_in_display_order(EXTENSIONS)
    state = {e.id: False for e in EXTENSIONS}
    setup_ui._toggle_extensions(state, None)
    # cursor 0 → down → row 1, space toggles row 1 only
    assert state[order[1].id] is True
    assert state[order[0].id] is False


def test_toggle_extensions_tui_digit_shortcut(monkeypatch):
    from hal0.install.extensions import EXTENSIONS

    setup_ui = _feed_keys(monkeypatch, ["2", "enter"])
    order = setup_ui._extensions_in_display_order(EXTENSIONS)
    state = {e.id: False for e in EXTENSIONS}
    setup_ui._toggle_extensions(state, None)
    assert state[order[1].id] is True


def test_provision_slot_tui_arrow_picks_model(monkeypatch):
    from hal0.install.suggest import Suggestion

    sugg = [
        Suggestion("m1", "M1", 1.0, 0.0, 4096, "gpu-rocm", "rocm", "chat", False, recommended=True),
        Suggestion(
            "m2", "M2", 2.0, 0.0, 4096, "gpu-rocm", "rocm", "chat", False, recommended=False
        ),
    ]
    su = _feed_keys(monkeypatch, ["down", "enter"])  # default=recommended(0) → down → m2
    monkeypatch.setattr(su, "suggest_models", lambda *a, **k: sugg)
    sel = su._provision_slot("main", "chat", None, "main", 8081)
    assert sel.model_id == "m2"


def test_provision_slot_tui_scaffold_then_skip(monkeypatch):
    from hal0.install.suggest import Suggestion

    sugg = [
        Suggestion("m1", "M1", 1.0, 0.0, 4096, "gpu-rocm", "rocm", "embed", False, recommended=True)
    ]
    # rows: m1(0), scaffold(1), skip(2); default cursor = 0
    su = _feed_keys(monkeypatch, ["down", "enter"])  # → scaffold
    monkeypatch.setattr(su, "suggest_models", lambda *a, **k: sugg)
    scaffold = su._provision_slot("capabilities", "embed", None, "embed", 8083)
    assert scaffold is not None and scaffold.model_id is None

    su = _feed_keys(monkeypatch, ["down", "down", "enter"])  # → skip
    monkeypatch.setattr(su, "suggest_models", lambda *a, **k: sugg)
    assert su._provision_slot("capabilities", "embed", None, "embed", 8083) is None


def test_step_gen_tui_arrow_and_digit(monkeypatch):
    su = _feed_keys(monkeypatch, ["down", "enter"])  # default idx1 → down → scaffold_and_download
    assert su._step_gen(None) == "scaffold_and_download"
    su = _feed_keys(monkeypatch, ["1"])  # digit jump → off
    assert su._step_gen(None) == "off"


def test_render_provision_picker_has_scaffold_and_skip_rows():
    from hal0.cli import setup_ui
    from hal0.install.suggest import Suggestion

    sugg = [
        Suggestion(
            "m1", "Qwen X", 1.0, 0.0, 4096, "gpu-rocm", "rocm", "chat", False, recommended=True
        )
    ]
    con = Console(width=100, record=True)
    con.print(setup_ui.render_provision_picker("main", sugg, cursor=0))
    text = con.export_text()
    assert "Qwen X" in text
    assert "Scaffold empty" in text and "Skip this slot" in text
    assert ">" in text  # cursor marker present


def test_extension_checklist_numbers_rows():
    state = {"openwebui": False, "comfyui": False, "hermes": False}
    con = Console(width=80, record=True)
    con.print(render_extension_checklist(EXTENSIONS, state, cursor=0))
    text = con.export_text()
    assert "1." in text and "2." in text


def test_render_review_shows_gen_download_estimate():
    from hal0.cli import setup_ui
    from hal0.cli.setup_ui import NetworkChoice, SetupPlan
    from hal0.install.orchestrate import SlotSelection

    plan = SetupPlan(
        hw=_hw(),
        network=NetworkChoice("127.0.0.1", "box", None),
        storage_dir="/mnt/ai-models",
        hf_token=None,
        extensions={"comfyui": False},
        slots=[SlotSelection("chat", "chat", 8081, None)],
        npu_opt_in=False,
        gen_mode="scaffold_and_download",
        comfyui_defaults=(("txt2img", "sdxl"), ("image_upscale", "esrgan")),
    )
    con = Console(width=120, record=True)
    con.print(setup_ui.render_review(plan))
    text = con.export_text()
    assert "Generation models" in text
    assert "2 variant" in text
