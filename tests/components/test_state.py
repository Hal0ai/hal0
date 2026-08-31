"""components.json state store: atomic write, tolerant read."""

from __future__ import annotations

import pytest

from hal0.components import state


@pytest.fixture(autouse=True)
def _isolated_var_lib(tmp_path, monkeypatch):
    monkeypatch.setenv("HAL0_HOME", str(tmp_path))


def test_record_then_load_roundtrip() -> None:
    state.record_component_result(
        "openwebui", {"status": "converged", "installed": "sha256:" + "a" * 64}
    )
    loaded = state.load_component_state()
    assert loaded["openwebui"]["status"] == "converged"
    assert "ts" in loaded["openwebui"]


def test_record_preserves_other_components() -> None:
    state.record_component_result("openwebui", {"status": "converged"})
    state.record_component_result("hermes", {"status": "build_failed", "error": "pip died"})
    loaded = state.load_component_state()
    assert set(loaded) == {"openwebui", "hermes"}


def test_load_missing_file_returns_empty() -> None:
    assert state.load_component_state() == {}


def test_load_corrupt_file_returns_empty() -> None:
    path = state.components_state_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("{not json", encoding="utf-8")
    assert state.load_component_state() == {}


def test_record_over_corrupt_file_recovers() -> None:
    path = state.components_state_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("{not json", encoding="utf-8")
    state.record_component_result("hindsight", {"status": "upgraded"})
    assert state.load_component_state()["hindsight"]["status"] == "upgraded"


def test_record_never_raises_on_unwritable_dir(monkeypatch) -> None:
    # Fail-soft posture: bookkeeping failure must not fail an update.
    monkeypatch.setattr(
        state, "components_state_path", lambda: state.Path("/proc/nope/components.json")
    )
    state.record_component_result("openwebui", {"status": "converged"})  # must not raise
