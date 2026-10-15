"""Provider-suite fixtures."""

from __future__ import annotations

from pathlib import Path

import pytest


def stage_kokoro_bundle(store: Path) -> Path:
    """Lay down the pulled kokoro-v1 bundle the way ``hal0 model pull`` does."""
    bundle = store / "tts" / "kokoro-v1"
    bundle.mkdir(parents=True, exist_ok=True)
    (bundle / "kokoro-v1.0.onnx").write_bytes(b"onnx")
    (bundle / "voices-v1.0.bin").write_bytes(b"voices")
    return bundle


@pytest.fixture
def kokoro_store(tmp_path, monkeypatch) -> Path:
    """A model store holding the pulled kokoro-v1 bundle; returns the store root.

    The kokoro provider refuses to launch without the bundle (#2739), so any
    test that renders a kokoro spec needs one.
    """
    store = tmp_path / "store"
    stage_kokoro_bundle(store)
    monkeypatch.setenv("HAL0_MODEL_STORE", str(store))
    return store
