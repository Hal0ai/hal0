"""Slot logs / reset-failed / NPU probe / metrics resolve artefact names through
the naming seam (#1436, sibling of #1417).

Every case uses an id-keyed slot config (``name`` != ``id``): the real artefacts
are ``hal0-slot@<id>.service`` / ``hal0-slot-<id>``, so a call site that formats
the mutable NAME targets something that does not exist.
"""

from __future__ import annotations

import ast
import asyncio
from collections.abc import AsyncIterator
from pathlib import Path
from typing import Any
from unittest.mock import AsyncMock

import pytest
from fastapi.testclient import TestClient

import hal0.api.routes.hardware as hw_mod
import hal0.providers.npu_columns as npu_columns
from hal0.slots import metrics_collect
from hal0.slots.state import SlotState

ID_CFG = {"id": 7, "name": "brain", "slot": {"name": "brain"}}


def _id_keyed(client: TestClient) -> Any:
    sm = client.app.state.slot_manager
    sm.status = AsyncMock(return_value=None)
    sm.get_config = AsyncMock(return_value=ID_CFG)
    return sm


def test_slot_logs_uses_id_derived_unit(client: TestClient, monkeypatch: pytest.MonkeyPatch):
    _id_keyed(client)
    seen: list[str] = []

    async def _fake_read_tail(unit: str, lines: int, quiet: bool) -> tuple[str, None]:
        seen.append(unit)
        return "line", None

    monkeypatch.setattr("hal0.slots.logs.read_tail", _fake_read_tail)
    r = client.get("/api/slots/brain/logs")
    assert r.status_code == 200
    assert seen == ["hal0-slot@7.service"]


def test_slot_logs_stream_uses_id_derived_unit(client: TestClient, monkeypatch: pytest.MonkeyPatch):
    _id_keyed(client)
    seen: list[str] = []

    async def _fake_tail(unit: str, backfill: int, quiet: bool) -> AsyncIterator[str | None]:
        seen.append(unit)
        yield "hello"

    monkeypatch.setattr("hal0.slots.logs.tail_journal_keepalive", _fake_tail)
    monkeypatch.setattr("shutil.which", lambda _n: "/usr/bin/journalctl")
    r = client.get("/api/slots/brain/logs/stream")
    assert r.status_code == 200
    assert seen == ["hal0-slot@7.service"]


def test_npu_unload_reset_failed_uses_id_derived_unit(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
):
    sm = client.app.state.slot_manager
    sm.get_config = AsyncMock(return_value={"id": 7, "name": "npu-x"})
    sm.unload = AsyncMock()

    async def _delete(*_a: Any) -> None:
        # The config is gone once the slot is deleted: the unit must already
        # have been resolved by now.
        sm.get_config = AsyncMock(side_effect=RuntimeError("deleted"))

    sm.delete = _delete
    argv: list[tuple[Any, ...]] = []

    class _Proc:
        async def wait(self) -> int:
            return 0

    async def _fake_exec(*args: Any, **_kw: Any) -> _Proc:
        argv.append(args)
        return _Proc()

    monkeypatch.setattr(asyncio, "create_subprocess_exec", _fake_exec)
    r = client.post("/api/backends/npu/unload", json={"slot_name": "npu-x"})
    assert r.status_code == 200
    assert argv == [("systemctl", "reset-failed", "hal0-slot@7.service")]


def test_npu_occupancy_probes_id_derived_container(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
):
    monkeypatch.setattr(hw_mod, "_npu_status", AsyncMock(return_value={"ok": True}))
    probe = AsyncMock(return_value={"partitions": [{"start_col": 0, "num_cols": 8}], "total": 8})
    monkeypatch.setattr(npu_columns, "cached_aie_columns", probe)

    class _Slot:
        name = "npu"
        state = SlotState.SERVING
        model_id = "gemma3:4b"
        backend = None
        metadata = {"provider": "flm"}  # noqa: RUF012

    sm = client.app.state.slot_manager
    sm.list = AsyncMock(return_value=[_Slot()])
    sm.get_config = AsyncMock(return_value={"id": 3, "name": "npu"})
    r = client.get("/api/npu/occupancy")
    assert r.status_code == 200
    probe.assert_awaited_once_with("hal0-slot-3")


def test_collect_local_uses_id_derived_names(monkeypatch: pytest.MonkeyPatch):
    seen: dict[str, str] = {}

    async def _props(unit: str, *_p: str) -> dict[str, str]:
        seen["unit"] = unit
        return {}

    async def _mem(container: str) -> int:
        seen["container"] = container
        return 0

    monkeypatch.setattr(metrics_collect, "systemd_props", _props)
    monkeypatch.setattr(metrics_collect, "container_mem_bytes", _mem)
    monkeypatch.setattr(metrics_collect, "llama_metrics", AsyncMock(return_value={}))

    class _Slot:
        name = "brain"
        port = 8001

    class _SM:
        async def list(self) -> list[_Slot]:
            return [_Slot()]

        async def get_config(self, _n: str) -> dict[str, Any]:
            return ID_CFG

    asyncio.run(metrics_collect.collect_local(_SM()))
    assert seen == {"unit": "hal0-slot@7.service", "container": "hal0-slot-7"}


# ── grep guard ───────────────────────────────────────────────────────────────

_SRC = Path(__file__).resolve().parents[2] / "src" / "hal0"
_NAMING = _SRC / "slots" / "naming.py"

#: (relative path, literal fragment) pairs that are NOT runtime-artefact names.
_ALLOWED = {
    # Operator-facing hint text printed by the CLI, not an artefact lookup.
    ("cli/slot_commands.py", "journalctl -u hal0-slot@"),
    ("cli/slot_commands.py", "restart of hal0-slot@"),
    # Validation error text in the privilege seam ("not a hal0-slot@ unit: ..."):
    # describes the expected shape, does not build a name.
    ("system/seam.py", "not a hal0-slot@"),
    # asyncio task label, not a systemd/podman artefact.
    ("slots/watchdog.py", "hal0-slot-fail-watch-"),
    # Log text; the id is parsed out of a real ``list-units`` unit name.
    ("bench/harness.py", "hal0-slot@"),
}


def _fstring_hits() -> list[str]:
    hits: list[str] = []
    for path in sorted(_SRC.rglob("*.py")):
        if path == _NAMING:
            continue
        rel = path.relative_to(_SRC).as_posix()
        for node in ast.walk(ast.parse(path.read_text(encoding="utf-8"))):
            if not isinstance(node, ast.JoinedStr):
                continue
            for part in node.values:
                if not (isinstance(part, ast.Constant) and isinstance(part.value, str)):
                    continue
                text = part.value
                if "hal0-slot@" not in text and "hal0-slot-" not in text:
                    continue
                if any(rel == r and frag in text for r, frag in _ALLOWED):
                    continue
                hits.append(f"{rel}:{node.lineno}: f-string {text!r}")
    return hits


def test_no_slot_artefact_fstring_outside_naming_seam():
    """Docstrings / comments / plain strings are ignored; only f-strings that
    format a value into a ``hal0-slot@`` / ``hal0-slot-`` name count."""
    assert _fstring_hits() == []
