"""Unit tests for the hindsight-api extraction-slot drop-in writer (ADR-0023).

``apply_extraction_slot`` writes a systemd drop-in pinning
``HINDSIGHT_API_LLM_MODEL=hal0/<slot>`` and restarts hindsight-api so the
engine's native extraction LLM follows the operator's chosen slot. The writer is
best-effort (returns a status dict rather than raising) so an unprivileged hal0
-api surfaces a partial result instead of 500ing.
"""

from __future__ import annotations

from pathlib import Path

from hal0.memory.extraction_env import (
    DROP_IN_PATH,
    apply_extraction_slot,
    render_drop_in,
)


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
    # Redirect the drop-in to a tmp dir + stub systemctl so the test never
    # touches /etc or the real service.
    import hal0.memory.extraction_env as ee

    drop_in = tmp_path / "hindsight-api.service.d" / "extraction-model.conf"
    monkeypatch.setattr(ee, "DROP_IN_DIR", drop_in.parent)
    monkeypatch.setattr(ee, "DROP_IN_PATH", drop_in)

    ran: list[list[str]] = []

    def fake_run(args, **_kw):
        ran.append(list(args))

        class _Done:
            returncode = 0

        return _Done()

    monkeypatch.setattr(ee.subprocess, "run", fake_run)

    result = apply_extraction_slot("utility")

    assert result["error"] is None
    assert result["written"] is True
    assert result["daemon_reloaded"] is True
    assert result["restarted"] is True
    assert result["model"] == "hal0/utility"
    assert drop_in.read_text().count("HINDSIGHT_API_LLM_MODEL=hal0/utility") == 1
    # daemon-reload then restart, in order.
    assert ran[0][:2] == ["systemctl", "daemon-reload"]
    assert ran[1] == ["systemctl", "restart", "hindsight-api"]


def test_apply_no_restart_skips_systemctl(monkeypatch, tmp_path: Path):
    import hal0.memory.extraction_env as ee

    drop_in = tmp_path / "hindsight-api.service.d" / "extraction-model.conf"
    monkeypatch.setattr(ee, "DROP_IN_DIR", drop_in.parent)
    monkeypatch.setattr(ee, "DROP_IN_PATH", drop_in)

    def boom(*_a, **_k):  # pragma: no cover — must not be called
        raise AssertionError("systemctl should not run when restart=False")

    monkeypatch.setattr(ee.subprocess, "run", boom)

    result = apply_extraction_slot("agent", restart=False)
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
