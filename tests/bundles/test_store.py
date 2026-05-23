"""Tests for hal0.bundles.store — marker persistence + capability applier."""

from __future__ import annotations

import json
from typing import Any

import pytest

from hal0.bundles import store as bundle_store
from hal0.bundles.schema import Bundle, BundleManifest, ModelEntry


@pytest.fixture
def hal0_home(tmp_path, monkeypatch):
    monkeypatch.setenv("HAL0_HOME", str(tmp_path))
    return tmp_path


def test_marker_absent_initially(hal0_home):
    assert bundle_store.read_choice() is None
    assert bundle_store.is_picker_pending() is True


def test_mark_bundle_chosen_writes_marker(hal0_home):
    choice = bundle_store.mark_bundle_chosen("hal0-Pro", npu_opt_in=True)
    assert choice.name == "hal0-Pro"
    assert choice.npu_opt_in is True
    assert choice.skipped is False
    # File-backed; re-read returns the same shape.
    restored = bundle_store.read_choice()
    assert restored is not None
    assert restored.name == "hal0-Pro"
    assert bundle_store.is_picker_pending() is False


def test_mark_skipped_writes_marker_with_skip_flag(hal0_home):
    choice = bundle_store.mark_skipped()
    assert choice.skipped is True
    assert choice.name == ""
    assert bundle_store.read_choice() is not None
    assert bundle_store.is_picker_pending() is False


def test_corrupt_marker_treated_as_missing(hal0_home):
    marker = bundle_store.marker_path()
    marker.parent.mkdir(parents=True, exist_ok=True)
    marker.write_text("not json", encoding="utf-8")
    assert bundle_store.read_choice() is None


def test_marker_round_trip_preserves_assignments(hal0_home):
    assignments = (
        {"slot": "embed", "model_name": "nomic-v1.5", "applied": True},
        {"slot": "stt", "model_name": "whisper-tiny", "applied": True},
    )
    bundle_store.mark_bundle_chosen("hal0-Default", npu_opt_in=False, assignments=assignments)
    restored = bundle_store.read_choice()
    assert restored is not None
    assert restored.assignments == assignments


def _fake_manifest() -> BundleManifest:
    bundle = Bundle(
        name="hal0-Default",
        min_ram_gb=32,
        primary=ModelEntry(slot="chat.primary", model_name="qwen3.5-9b", size_gb=6.9),
        coder=None,
        aux=(
            ModelEntry(slot="embed", model_name="nomic-v1.5", size_gb=0.3),
            ModelEntry(slot="stt", model_name="whisper-tiny", size_gb=0.075),
        ),
        npu_trio_shown=False,
        npu_trio_optin=False,
        display_label="hal0-Default",
        display_subtitle="",
        vendor="hal0",
    )
    return BundleManifest(
        schema_version=1,
        bundle=bundle,
        omni={"kind": "collection.omni", "name": "hal0-Default", "members": []},
    )


class _FakeOrchestrator:
    """Records every orchestrator.apply call + lets a test trigger failures."""

    def __init__(self, *, fail_on: set[tuple[str, str]] | None = None):
        self.calls: list[tuple[str, str, dict[str, Any]]] = []
        self._fail_on = fail_on or set()

    async def apply(self, slot, child, body):
        self.calls.append((slot, child, body))
        if (slot, child) in self._fail_on:
            raise RuntimeError(f"forced failure on {slot}/{child}")
        return {
            "model": body.get("model"),
            "enabled": body.get("enabled", False),
            "slot": f"{slot}-{child}",
            "status": "loading",
        }


@pytest.mark.asyncio
async def test_apply_bundle_routes_aux_models_through_orchestrator():
    manifest = _fake_manifest()
    orch = _FakeOrchestrator()
    results = await bundle_store.apply_bundle_to_capabilities(manifest, orch)

    # 1 primary + 0 coder + 2 aux = 3 rows total.
    assert len(results) == 3
    # The primary lands in the no-mapping bucket (chat surface).
    primary_row = next(r for r in results if r["slot"] == "chat.primary")
    assert primary_row["applied"] is False
    assert primary_row.get("reason") == "no_capability_mapping"
    # The two aux rows were applied.
    applied_slots = {r["slot"] for r in results if r["applied"]}
    assert applied_slots == {"embed", "stt"}
    # The orchestrator was called with the right tuples + model body.
    apply_keys = {(slot, child) for (slot, child, _body) in orch.calls}
    assert apply_keys == {("embed", "embed"), ("voice", "stt")}


@pytest.mark.asyncio
async def test_apply_bundle_records_per_row_failure_without_aborting():
    manifest = _fake_manifest()
    orch = _FakeOrchestrator(fail_on={("voice", "stt")})
    results = await bundle_store.apply_bundle_to_capabilities(manifest, orch)
    stt_row = next(r for r in results if r["slot"] == "stt")
    assert stt_row["applied"] is False
    assert "forced failure" in stt_row.get("error", "")
    # The embed row still went through.
    embed_row = next(r for r in results if r["slot"] == "embed")
    assert embed_row["applied"] is True


def test_marker_path_respects_hal0_home(hal0_home):
    expected = hal0_home / "var-lib" / "hal0" / ".bundle-chosen"
    assert bundle_store.marker_path() == expected


def test_choice_to_dict_is_json_serializable(hal0_home):
    choice = bundle_store.mark_bundle_chosen("hal0-Lite", npu_opt_in=False)
    raw = json.dumps(choice.to_dict())
    # Round-trip.
    restored = bundle_store.BundleChoice.from_dict(json.loads(raw))
    assert restored.name == "hal0-Lite"
