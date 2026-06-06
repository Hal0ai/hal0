"""NPU Phase 2 — catalog must surface FLM ``stt`` rows with an ``npu`` backend.

Before Phase 2 the FLM picker fan-out was deliberately scoped to
``{chat, embed}`` (the 2026-05-20 design call) — FLM also serves ``stt``
(whisper-v3 / gemma asr=true) but the NPU voice path through the slot
manager was a later slice. Phase 2 enables ``device=npu`` for
``voice.stt`` driven through the FLM trio, so the orchestrator's
validation (``models_for_capability("stt")``) must now see an ``npu``
backend for an FLM transcription model — otherwise it rejects the npu
selection as an illegal backend/model pair.

This pins that the catalog widens to ``stt``.
"""

from __future__ import annotations

from typing import Any

import pytest

from hal0.capabilities import catalog


@pytest.fixture
def flm_entries() -> list[dict[str, Any]]:
    """An FLM transcription row alongside a chat row."""
    return [
        {
            "tag": "whisper-large-v3",
            "size_bytes": 1_500_000_000,
            "footprint_gb": 1.6,
            "capabilities": ["stt"],
            "installed": True,
        },
        {
            "tag": "gemma3:1b",
            "size_bytes": 1_000_000_000,
            "footprint_gb": 1.2,
            "capabilities": ["chat"],
            "installed": True,
        },
    ]


def test_flm_rows_surface_stt(
    monkeypatch: pytest.MonkeyPatch, flm_entries: list[dict[str, Any]]
) -> None:
    """``_flm_rows_for_capability("stt")`` returns the npu/flm transcription row."""
    monkeypatch.setattr("hal0.providers.flm.flm_served_models", lambda: flm_entries)
    rows = catalog._flm_rows_for_capability("stt")

    ids = {row["id"] for row in rows}
    assert "whisper-large-v3" in ids, (
        f"FLM stt fan-out did not surface the transcription tag: {rows!r}"
    )
    row = next(r for r in rows if r["id"] == "whisper-large-v3")
    assert row["backend"] == "npu"
    assert row["provider"] == "flm"
    assert "stt" in row["capabilities"], f"stt was filtered out of reported_caps: {row!r}"


def test_models_for_capability_stt_has_npu_backend(
    monkeypatch: pytest.MonkeyPatch, flm_entries: list[dict[str, Any]]
) -> None:
    """The orchestrator-facing API yields a model with a legal ``npu`` backend.

    This is what ``_validate_model_in_catalog`` consults to decide whether
    ``device=npu`` is legal for the picked stt model.
    """
    monkeypatch.setattr("hal0.providers.flm.flm_served_models", lambda: flm_entries)
    rows = catalog.models_for_capability("stt")

    match = next((r for r in rows if r["id"] == "whisper-large-v3"), None)
    assert match is not None, f"stt model missing from models_for_capability: {rows!r}"
    legal_backends = [b["id"] for b in match.get("backends", [])]
    assert "npu" in legal_backends, f"npu not a legal backend for the stt model: {legal_backends!r}"
