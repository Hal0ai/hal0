"""Duplicate slot TOMLs (#2542): one name/id must map to exactly one file.

A stale ``agent.toml`` left beside ``1.toml`` (both ``name = "agent"``) used
to load twice, and a model-delete cascade keyed on the name unloaded the live
slot. The enumerator now keeps one file per name and the cascade only sees it.
"""

from __future__ import annotations

import logging
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

import pytest

from hal0.services import models_service as svc
from hal0.slots.layout import shadowed_slot_files
from hal0.slots.manager import SlotManager


def _write(root: Path, stem: str, name: str, model: str, port: int = 8191) -> Path:
    root.mkdir(parents=True, exist_ok=True)
    p = root / f"{stem}.toml"
    p.write_text(
        f'name = "{name}"\nport = {port}\nprovider = "llama-server"\n[model]\ndefault = "{model}"\n',
        encoding="utf-8",
    )
    return p


def _slots_root(home: str) -> Path:
    return Path(home) / "etc" / "hal0" / "slots"


def test_shadowed_slot_files_prefers_id_keyed_file(tmp_path: Path) -> None:
    live = _write(tmp_path, "1", "agent", "live")
    stale = _write(tmp_path, "agent", "agent", "stale")
    other = _write(tmp_path, "chat", "chat", "c")
    assert shadowed_slot_files(tmp_path) == {stale: live}
    assert other not in shadowed_slot_files(tmp_path)


def test_shadowed_slot_files_empty_without_duplicates(tmp_path: Path) -> None:
    _write(tmp_path, "a", "a", "m")
    _write(tmp_path, "b", "b", "m")
    assert shadowed_slot_files(tmp_path) == {}


def test_enumerator_skips_later_duplicate_and_warns(
    tmp_hal0_home: str, caplog: pytest.LogCaptureFixture
) -> None:
    root = _slots_root(tmp_hal0_home)
    live = _write(root, "1", "agent", "live")
    stale = _write(root, "agent", "agent", "stale", port=8192)

    sm = SlotManager()
    with caplog.at_level(logging.WARNING):
        rows = sm._iter_configured_slots()

    assert [(n, p) for _sid, n, p in rows] == [("agent", live)]
    msgs = [r for r in caplog.records if r.getMessage() == "slot.duplicate_toml"]
    assert msgs, "no slot.duplicate_toml warning"
    assert str(stale) in str(msgs[0].__dict__) and str(live) in str(msgs[0].__dict__)


def test_enumerator_reports_shadowed_stem_as_dropped(tmp_hal0_home: str) -> None:
    root = _slots_root(tmp_hal0_home)
    _write(root, "1", "agent", "live")
    _write(root, "agent", "agent", "stale")
    dropped: list[str] = []
    SlotManager()._iter_configured_slots(dropped=dropped)
    assert dropped == ["agent"]


def test_slots_referencing_model_ignores_shadowed_stale_file(tmp_hal0_home: str) -> None:
    root = _slots_root(tmp_hal0_home)
    _write(root, "1", "agent", "live-model")
    _write(root, "agent", "agent", "draft-model")
    assert svc.slots_referencing_model("draft-model") == []
    live = svc.slots_referencing_model("live-model")
    assert [e["name"] for e in live] == ["agent"]


@pytest.mark.asyncio
async def test_cascade_with_stale_referrer_leaves_live_slot_loaded(
    tmp_hal0_home: str, tmp_path: Path
) -> None:
    root = _slots_root(tmp_hal0_home)
    live = _write(root, "1", "agent", "live-model")
    _write(root, "agent", "agent", "draft-model")

    affected = svc.slots_referencing_model("draft-model")
    sm = MagicMock()
    sm.status = AsyncMock()
    sm.unload = AsyncMock()
    registry = MagicMock()
    registry.db_path = tmp_path / "registry.db"  # a bare mock would make a relative path
    registry.get.return_value = None
    await svc.cascade_delete_model(registry, sm, "draft-model", affected, delete_files=False)

    sm.unload.assert_not_called()
    assert 'default = "live-model"' in live.read_text(encoding="utf-8")
