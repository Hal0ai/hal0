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
    apply_extraction_slot,
    drop_in_matches,
    render_drop_in,
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
