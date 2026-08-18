"""Unit tests for the hindsight-api extraction-slot drop-in writer (ADR-0023).

``apply_extraction_slot`` writes a systemd drop-in pinning
``HINDSIGHT_API_LLM_MODEL=hal0/<slot>`` and restarts hindsight-api so the
engine's native extraction LLM follows the operator's chosen slot. The writer is
best-effort (returns a status dict rather than raising) so an unprivileged hal0
-api surfaces a partial result instead of 500ing.
"""

from __future__ import annotations

import subprocess
from pathlib import Path

from hal0.memory.extraction_env import (
    DROP_IN_PATH,
    EXTRACTION_FLOOR_ENV,
    EXTRACTION_MIN_CONTEXT_TOKENS,
    apply_extraction_slot,
    drop_in_matches,
    extraction_floor,
    extraction_model_name,
    render_drop_in,
    resolve_extraction_window,
)
from hal0.system.seam import SEAM_BIN, SystemCtlSeam


def _seam_recorder(rc: int = 0, stderr: str = ""):
    """Record ``(argv, stdin-body)`` for every seam invocation."""
    calls: list[tuple[list[str], str | None]] = []

    def _run(argv, **kwargs):
        calls.append((list(argv), kwargs.get("input")))
        done = subprocess.CompletedProcess(list(argv), rc, "", stderr)
        if rc and kwargs.get("check", False):
            raise subprocess.CalledProcessError(rc, list(argv), "", stderr)
        return done

    return calls, _run


def _forbidden(*_a, **_k):  # pragma: no cover — a call here is the bug
    raise AssertionError("privileged work must route through the seam, not bare subprocess")


def test_render_drop_in_pins_hal0_virtual():
    out = render_drop_in("utility")
    assert "HINDSIGHT_API_LLM_MODEL=hal0/utility" in out
    assert "[Service]" in out


def test_render_drop_in_tracks_the_slot_name():
    assert "HINDSIGHT_API_LLM_MODEL=hal0/agent" in render_drop_in("agent")
    assert "HINDSIGHT_API_LLM_MODEL=hal0/coder-mini" in render_drop_in("coder-mini")


def test_drop_in_path_is_a_systemd_override():
    # The override lives in the hindsight-api drop-in dir so it layers over the
    # installer-owned base unit without hand-editing it.
    assert DROP_IN_PATH.name == "extraction-model.conf"
    assert "hindsight-api.service.d" in str(DROP_IN_PATH)


def test_apply_writes_drop_in_and_reports_status(monkeypatch, tmp_path: Path):
    # Redirect the drop-in to a tmp dir + inject a fake runner so the test never
    # touches /etc or the real service.
    import hal0.memory.extraction_env as ee

    drop_in = tmp_path / "hindsight-api.service.d" / "extraction-model.conf"
    monkeypatch.setattr(ee, "DROP_IN_DIR", drop_in.parent)
    monkeypatch.setattr(ee, "DROP_IN_PATH", drop_in)

    calls, run = _seam_recorder()
    seam = SystemCtlSeam(run=run, is_hal0_user=lambda: False)

    result = apply_extraction_slot("utility", seam=seam)
    ran = [c[0] for c in calls]

    assert result["error"] is None
    assert result["written"] is True
    assert result["daemon_reloaded"] is True
    assert result["restarted"] is True
    assert result["model"] == "hal0/utility"
    assert drop_in.read_text().count("HINDSIGHT_API_LLM_MODEL=hal0/utility") == 1
    # daemon-reload then restart, in order. Off the hal0 service account the
    # seam is a passthrough, so these are the bare argv.
    assert ran[0][:2] == ["systemctl", "daemon-reload"]
    assert ran[1] == ["systemctl", "restart", "hindsight-api.service"]


def test_apply_no_restart_skips_systemctl(monkeypatch, tmp_path: Path):
    import hal0.memory.extraction_env as ee

    drop_in = tmp_path / "hindsight-api.service.d" / "extraction-model.conf"
    monkeypatch.setattr(ee, "DROP_IN_DIR", drop_in.parent)
    monkeypatch.setattr(ee, "DROP_IN_PATH", drop_in)

    seam = SystemCtlSeam(run=_forbidden, is_hal0_user=lambda: False)

    result = apply_extraction_slot("agent", restart=False, seam=seam)
    assert result["written"] is True
    assert result["restarted"] is False
    assert result["error"] is None
    assert drop_in.exists()


def test_render_drop_in_includes_llm_timeout():
    # Default mirrors MemoryGraphConfig.llm_timeout_s (300s); explicit values
    # ride the same drop-in so one file owns both hindsight LLM env overrides.
    assert "HINDSIGHT_API_LLM_TIMEOUT=300" in render_drop_in("utility")
    assert "HINDSIGHT_API_LLM_TIMEOUT=600" in render_drop_in("utility", timeout_s=600)


def test_apply_threads_timeout_into_drop_in_and_status(monkeypatch, tmp_path: Path):
    import hal0.memory.extraction_env as ee

    drop_in = tmp_path / "hindsight-api.service.d" / "extraction-model.conf"
    monkeypatch.setattr(ee, "DROP_IN_DIR", drop_in.parent)
    monkeypatch.setattr(ee, "DROP_IN_PATH", drop_in)

    result = apply_extraction_slot("agent", timeout_s=900, restart=False)
    assert result["timeout_s"] == 900
    text = drop_in.read_text()
    assert "HINDSIGHT_API_LLM_MODEL=hal0/agent" in text
    assert "HINDSIGHT_API_LLM_TIMEOUT=900" in text


# ── #1641: the unprivileged hal0-api path ────────────────────────────────────
#
# hal0-api runs as the unprivileged ``hal0`` service user (User=hal0), and
# /etc/systemd/system/hindsight-api.service.d is root:root. Writing the drop-in
# directly is EPERM and a bare ``systemctl restart`` escalates through polkit
# ("Interactive authentication required"), so on every standard install the
# propagation silently no-opped while hal0.toml recorded the new slot. Every
# privileged step must route through the hal0-systemctl seam instead.


def test_apply_routes_every_step_through_the_seam_as_the_hal0_user(monkeypatch, tmp_path: Path):
    import hal0.memory.extraction_env as ee

    drop_in = tmp_path / "hindsight-api.service.d" / "extraction-model.conf"
    monkeypatch.setattr(ee, "DROP_IN_DIR", drop_in.parent)
    monkeypatch.setattr(ee, "DROP_IN_PATH", drop_in)
    monkeypatch.setattr(ee.subprocess, "run", _forbidden)

    calls, run = _seam_recorder()
    seam = SystemCtlSeam(run=run, is_hal0_user=lambda: True)

    result = apply_extraction_slot("utility", timeout_s=420, seam=seam)

    assert result["error"] is None
    assert result["written"] is True
    assert result["daemon_reloaded"] is True
    assert result["restarted"] is True
    # Never written directly — the root side owns the literal path.
    assert not drop_in.exists()
    assert [c[0] for c in calls] == [
        ["sudo", "-n", SEAM_BIN, "write-hindsight-dropin"],
        ["sudo", "-n", SEAM_BIN, "daemon-reload"],
        ["sudo", "-n", SEAM_BIN, "svc-restart", "hindsight"],
    ]
    body = calls[0][1]
    assert "HINDSIGHT_API_LLM_MODEL=hal0/utility" in body
    assert "HINDSIGHT_API_LLM_TIMEOUT=420" in body


def test_apply_surfaces_a_seam_write_failure(monkeypatch, tmp_path: Path):
    import hal0.memory.extraction_env as ee

    drop_in = tmp_path / "hindsight-api.service.d" / "extraction-model.conf"
    monkeypatch.setattr(ee, "DROP_IN_DIR", drop_in.parent)
    monkeypatch.setattr(ee, "DROP_IN_PATH", drop_in)
    monkeypatch.setattr(ee.subprocess, "run", _forbidden)

    _calls, run = _seam_recorder(rc=1, stderr="sudo: a password is required")
    seam = SystemCtlSeam(run=run, is_hal0_user=lambda: True)

    result = apply_extraction_slot("utility", seam=seam)

    assert result["written"] is False
    assert result["restarted"] is False
    assert result["error"] and "sudo" in result["error"]


def test_apply_bounds_the_privileged_write(monkeypatch, tmp_path: Path):
    """A stalled sudo must not park an API worker thread forever."""
    import hal0.memory.extraction_env as ee

    drop_in = tmp_path / "hindsight-api.service.d" / "extraction-model.conf"
    monkeypatch.setattr(ee, "DROP_IN_PATH", drop_in)

    seen: list[object] = []

    def _run(argv, **kwargs):
        seen.append(kwargs.get("timeout"))
        return subprocess.CompletedProcess(list(argv), 0, "", "")

    seam = SystemCtlSeam(run=_run, is_hal0_user=lambda: True)
    apply_extraction_slot("utility", seam=seam)

    # write, daemon-reload, restart — every one bounded.
    assert seen == [ee._SYSTEMCTL_TIMEOUT_S] * 3


def test_apply_names_the_stale_wrapper_as_the_cause(monkeypatch, tmp_path: Path):
    """`hal0 update` never refreshes ${LIB_DIR}/bin, so new Python can meet an
    old wrapper. Exit 64 / `bad cmd` is a fixable operator condition — say so."""
    import hal0.memory.extraction_env as ee

    monkeypatch.setattr(ee, "DROP_IN_PATH", tmp_path / "extraction-model.conf")

    _calls, run = _seam_recorder(rc=64, stderr="hal0-systemctl: bad cmd: write-hindsight-dropin")
    seam = SystemCtlSeam(run=run, is_hal0_user=lambda: True)

    result = apply_extraction_slot("utility", seam=seam)

    assert result["written"] is False
    assert "install.sh" in result["error"]


def test_apply_does_not_blame_the_wrapper_for_other_failures(monkeypatch, tmp_path: Path):
    import hal0.memory.extraction_env as ee

    monkeypatch.setattr(ee, "DROP_IN_PATH", tmp_path / "extraction-model.conf")

    _calls, run = _seam_recorder(rc=1, stderr="sudo: a password is required")
    seam = SystemCtlSeam(run=run, is_hal0_user=lambda: True)

    assert "install.sh" not in (apply_extraction_slot("utility", seam=seam)["error"] or "")


def test_drop_in_matches_true_when_content_is_identical(monkeypatch, tmp_path: Path):
    import hal0.memory.extraction_env as ee

    path = tmp_path / "extraction-model.conf"
    path.write_text(render_drop_in("agent", 300))
    monkeypatch.setattr(ee, "DROP_IN_PATH", path)

    assert drop_in_matches("agent", 300) is True


def test_drop_in_matches_false_on_stale_content(monkeypatch, tmp_path: Path):
    """The recorded slot changed but the drop-in still names the old one —
    exactly the divergence a broken host can be stuck in."""
    import hal0.memory.extraction_env as ee

    path = tmp_path / "extraction-model.conf"
    path.write_text(render_drop_in("utility", 300))
    monkeypatch.setattr(ee, "DROP_IN_PATH", path)

    assert drop_in_matches("agent", 300) is False


def test_drop_in_matches_false_when_missing(monkeypatch, tmp_path: Path):
    """#1682 review: a host where the privileged write previously failed
    silently (pre-seam bug) has hal0.toml recording a slot that was NEVER
    actually applied — no drop-in file at all. That must read as "does not
    match", not error out, so the caller knows to (re)propagate."""
    import hal0.memory.extraction_env as ee

    monkeypatch.setattr(ee, "DROP_IN_PATH", tmp_path / "never-written.conf")

    assert drop_in_matches("agent", 300) is False


def test_drop_in_matches_false_on_undecodable_bytes(monkeypatch, tmp_path: Path):
    """#1717 review: ``Path.read_text()`` raises ``UnicodeDecodeError`` (NOT
    an ``OSError``) on malformed/tampered content. That must still read as
    "does not match" so the caller reconciles by rewriting the file, instead
    of an otherwise-idempotent graph PUT 500ing on a decode failure."""
    import hal0.memory.extraction_env as ee

    path = tmp_path / "extraction-model.conf"
    path.write_bytes(b"\xff\xfe not valid utf-8 \x80\x81")
    monkeypatch.setattr(ee, "DROP_IN_PATH", path)

    assert drop_in_matches("agent", 300) is False


def test_drop_in_matches_reads_utf8_regardless_of_locale(monkeypatch, tmp_path: Path):
    """#1717 review: the drop-in (and the em dash in its header comment) is
    always written UTF-8 by SystemCtlSeam — the comparison must decode it as
    UTF-8 explicitly rather than via ``Path.read_text()``'s locale-dependent
    default, or a byte-for-byte correct file can misread as drift under
    e.g. ``LC_ALL=C`` and trigger a needless rewrite + restart on every
    enabled graph PUT."""
    import hal0.memory.extraction_env as ee

    path = tmp_path / "extraction-model.conf"
    # The rendered template contains a real em dash (U+2014) in its header.
    content = render_drop_in("agent", 300)
    assert "—" in content
    path.write_text(content, encoding="utf-8")
    monkeypatch.setattr(ee, "DROP_IN_PATH", path)

    real_read_text = Path.read_text

    def _locale_sensitive_read_text(self, *args, **kwargs):
        # Fail unless the caller passed an explicit encoding — the same
        # failure mode as a real ASCII-locale default.
        if kwargs.get("encoding") != "utf-8" and (len(args) < 1 or args[0] != "utf-8"):
            raise UnicodeDecodeError("ascii", b"\xe2\x80\x94", 0, 1, "ordinal not in range(128)")
        return real_read_text(self, *args, **kwargs)

    monkeypatch.setattr(Path, "read_text", _locale_sensitive_read_text)

    assert drop_in_matches("agent", 300) is True


def test_apply_writes_directly_when_not_the_hal0_user(monkeypatch, tmp_path: Path):
    """Root / dev / CI keeps the pre-seam behaviour: a direct atomic write."""
    import hal0.memory.extraction_env as ee

    drop_in = tmp_path / "hindsight-api.service.d" / "extraction-model.conf"
    monkeypatch.setattr(ee, "DROP_IN_DIR", drop_in.parent)
    monkeypatch.setattr(ee, "DROP_IN_PATH", drop_in)

    calls, run = _seam_recorder()
    seam = SystemCtlSeam(run=run, is_hal0_user=lambda: False)

    result = apply_extraction_slot("agent", seam=seam)

    assert result["error"] is None
    assert drop_in.read_text().count("HINDSIGHT_API_LLM_MODEL=hal0/agent") == 1
    assert [c[0] for c in calls] == [
        ["systemctl", "daemon-reload"],
        ["systemctl", "restart", "hindsight-api.service"],
    ]


# ── #1903: extraction-slot context-window preflight ─────────────────────────
#
# Hindsight's native extraction call is dispatched to ``hal0/<extraction_slot>``
# regardless of the hal0-side enable toggle (reporting-only on this engine).
# Nothing ever preflighted that dispatch's window before this — an undersized
# resolved slot used to answer /add with HTTP 200 + a document id and then
# either drop the retain (Hindsight's own "Context size has been exceeded")
# or persist the extraction prompt's own scaffolding as fact. These pin the
# resolution logic reused from #1877's `resolve_anchor_window`.


def test_extraction_model_name_matches_the_drop_in_spelling():
    assert extraction_model_name("utility") == "hal0/utility"
    assert extraction_model_name("agent") == "hal0/agent"


def test_resolve_extraction_window_below_floor_on_the_repro_shape(tmp_path: Path):
    """The reported ct151-cpu-fresh shape: extraction slot resolves to a live 4096-token
    window — clearly under the extraction prompt's own footprint."""
    entry = {"id": "hal0/utility", "context_length": 4096}
    catalog = [{"id": "utility", "context_length": 4096}]

    window = resolve_extraction_window("utility", entry=entry, catalog=catalog, slots_dir=tmp_path)

    assert window.verdict == "below_floor"
    assert window.effective == 4096
    assert window.floor == EXTRACTION_MIN_CONTEXT_TOKENS
    assert window.slot == "utility"


def test_resolve_extraction_window_ok_when_the_slot_clears_the_floor(tmp_path: Path):
    entry = {"id": "hal0/utility", "context_length": 32768}
    catalog = [{"id": "utility", "context_length": 32768}]

    window = resolve_extraction_window("utility", entry=entry, catalog=catalog, slots_dir=tmp_path)

    assert window.verdict == "ok"


def test_resolve_extraction_window_unknown_when_the_catalog_has_no_evidence(tmp_path: Path):
    """No advertised context anywhere — the caller must treat this as
    "cannot prove", never as a silent pass (#1831's lesson, reused here)."""
    window = resolve_extraction_window("utility", entry=None, catalog=[], slots_dir=tmp_path)

    assert window.verdict == "unknown"


def test_resolve_extraction_window_uses_a_dedicated_floor_not_hermes(tmp_path: Path):
    """The extraction floor is the prompt's own footprint, not Hermes' much
    larger MINIMUM_CONTEXT_LENGTH — an extraction slot deliberately sized
    well below 64,000 (typical for a small local utility model) must not be
    flagged just because it would fail Hermes' anchor check."""
    entry = {"id": "hal0/utility", "context_length": 16384}
    catalog = [{"id": "utility", "context_length": 16384}]

    window = resolve_extraction_window("utility", entry=entry, catalog=catalog, slots_dir=tmp_path)

    assert window.verdict == "ok"
    assert window.floor < 64_000


def test_below_floor_message_names_memory_extraction_not_hermes(tmp_path: Path):
    """Finding 2 on PR #1917: the parent ``AnchorWindow.message`` is
    hard-coded for Hermes' 64,000-token anchor preflight. The 503 an operator
    sees for a refused memory write must talk about memory extraction — the
    right subsystem, the right floor semantics, the right failure scope —
    while keeping the resolver's numbers, slot, and fix command."""
    entry = {"id": "hal0/utility", "context_length": 4096}
    catalog = [{"id": "utility", "context_length": 4096}]

    window = resolve_extraction_window("utility", entry=entry, catalog=catalog, slots_dir=tmp_path)
    msg = window.message()

    assert window.verdict == "below_floor"
    assert "Hermes" not in msg
    assert "EVERY turn" not in msg
    assert "memory" in msg
    assert "extraction" in msg
    assert "4,096" in msg
    assert f"{EXTRACTION_MIN_CONTEXT_TOKENS:,}" in msg
    assert "slot 'utility'" in msg


def test_below_floor_message_includes_the_fix_command_for_a_binding_ceiling(tmp_path: Path):
    (tmp_path / "utility.toml").write_text("[model]\ncontext_size = 4096\n")
    entry = {"id": "hal0/utility", "context_length": 4096}
    catalog = [{"id": "utility", "context_length": 4096}]

    window = resolve_extraction_window("utility", entry=entry, catalog=catalog, slots_dir=tmp_path)
    msg = window.message()

    assert "context_size = 4,096" in msg
    assert "hal0 slot edit utility --ctx-size 8192" in msg


def test_unknown_and_ok_messages_are_extraction_specific(tmp_path: Path):
    unknown = resolve_extraction_window("utility", entry=None, catalog=[], slots_dir=tmp_path)
    ok = resolve_extraction_window(
        "utility",
        entry={"id": "hal0/utility", "context_length": 32768},
        catalog=[{"id": "utility", "context_length": 32768}],
        slots_dir=tmp_path,
    )

    assert "Hermes" not in unknown.message()
    assert "Hermes" not in ok.message()
    assert "memory extraction" in unknown.message()
    assert "memory extraction" in ok.message()


# ── HAL0_MEMORY_EXTRACTION_FLOOR override (PR #1917 review, finding 7) ──────


def test_extraction_floor_defaults_without_the_env_var(monkeypatch):
    monkeypatch.delenv(EXTRACTION_FLOOR_ENV, raising=False)
    assert extraction_floor() == (EXTRACTION_MIN_CONTEXT_TOKENS, "hal0:extraction-prompt-floor")


def test_extraction_floor_env_override_is_honoured(monkeypatch, tmp_path: Path):
    """The floor is a documented estimate hal0 cannot measure; an operator
    whose engine's prompt is genuinely smaller must have a knob that doesn't
    require resizing a slot."""
    monkeypatch.setenv(EXTRACTION_FLOOR_ENV, "4000")

    assert extraction_floor() == (4000, f"env:{EXTRACTION_FLOOR_ENV}")

    entry = {"id": "hal0/utility", "context_length": 4096}
    catalog = [{"id": "utility", "context_length": 4096}]
    window = resolve_extraction_window("utility", entry=entry, catalog=catalog, slots_dir=tmp_path)

    assert window.verdict == "ok"
    assert window.floor == 4000


def test_extraction_floor_garbage_override_falls_back(monkeypatch):
    """A typo'd override must not 500 every write — it is ignored."""
    for garbage in ("8k", "-1", "0", "  "):
        monkeypatch.setenv(EXTRACTION_FLOOR_ENV, garbage)
        floor, source = extraction_floor()
        assert floor == EXTRACTION_MIN_CONTEXT_TOKENS
        assert source == "hal0:extraction-prompt-floor"


def test_explicit_floor_argument_still_wins_over_the_env(monkeypatch, tmp_path: Path):
    monkeypatch.setenv(EXTRACTION_FLOOR_ENV, "4000")
    entry = {"id": "hal0/utility", "context_length": 4096}
    catalog = [{"id": "utility", "context_length": 4096}]

    window = resolve_extraction_window(
        "utility", entry=entry, catalog=catalog, floor=8192, slots_dir=tmp_path
    )

    assert window.verdict == "below_floor"
    assert window.floor == 8192


def test_invalid_floor_override_warns_once_not_per_write(monkeypatch):
    """Codex on PR #1917: extraction_floor() runs on the memory-write hot
    path, so a persistent typo'd override must not emit a journal warning on
    every auto-retain — once per distinct value is enough."""
    from hal0.memory import extraction_env as mod

    monkeypatch.setattr(mod, "_floor_override_warned", set())
    monkeypatch.setenv(EXTRACTION_FLOOR_ENV, "8k")
    warnings: list[str] = []
    monkeypatch.setattr(mod.log, "warning", lambda event, **kw: warnings.append(event))

    for _ in range(3):
        assert extraction_floor() == (EXTRACTION_MIN_CONTEXT_TOKENS, "hal0:extraction-prompt-floor")

    assert warnings == ["hal0.memory.extraction_floor_override_invalid"]
