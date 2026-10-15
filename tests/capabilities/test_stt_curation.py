"""STT curation surfacing (#514, reversed by #2691).

hal0 never shipped a whisper.cpp runtime (ADR-0001): the only Whisper it runs
is ``whisper-v3:turbo`` through the FLM trio on the XDNA NPU. The three
``whispercpp`` curated rows were dead picks and are gone; nothing in the curated
table or the catalog may reintroduce the engine tag.
"""

from __future__ import annotations

import pytest

from hal0.capabilities import catalog
from hal0.capabilities.catalog import models_for_capability
from hal0.registry.curated import CURATED_BY_ID, CURATED_MODELS


def test_no_curated_row_carries_the_whispercpp_backend() -> None:
    assert [m.id for m in CURATED_MODELS if m.backend == "whispercpp"] == []
    for dead in ("Whisper-Tiny", "Whisper-Base", "Whisper-Large-v3-Turbo"):
        assert dead not in CURATED_BY_ID


def test_catalog_does_not_know_the_whispercpp_provider() -> None:
    assert "whispercpp" not in catalog._BACKEND_TO_PROVIDER
    assert "whispercpp" not in catalog._RUNTIME_TO_HOST_BACKENDS
    assert "whispercpp" not in catalog._BACKEND_TO_PROVIDER.values()


def test_stt_dropdown_surfaces_no_whispercpp_row(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("hal0.providers.flm.flm_served_models", lambda: [])
    rows = models_for_capability("stt", registry=None)
    for row in rows:
        assert row["id"] not in {"Whisper-Tiny", "Whisper-Base", "Whisper-Large-v3-Turbo"}
        assert all(b.get("provider") != "whispercpp" for b in row.get("backends", []))
