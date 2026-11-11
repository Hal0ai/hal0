"""``hal0 memory embedder switch`` (#3518, ADR-0058 decision 2).

The engine is an ``httpx.MockTransport`` behind the real
``HindsightRestClient``; it keeps banks in memory and answers the routes the
switch uses the way hindsight-api 1.2.0 does (shapes read from its OpenAPI).
The host side (drop-in write and restart, the engine's live environment, the
admin rename) is injected, and every step lands in one ordered event list.
"""

from __future__ import annotations

import asyncio
import fcntl
import io
import json
import os
import re
import subprocess
import zipfile
from pathlib import Path
from typing import Any

import httpx
import pytest

from hal0.memory import embedder_record as er
from hal0.memory import embedder_switch as es
from hal0.memory import write_gate
from hal0.memory.embedder_record import Embedder, EmbedderRecord
from hal0.memory.hindsight_client import HindsightRestClient

BGE = Embedder("local", "BAAI/bge-small-en-v1.5")
HS = Embedder("local", "hindsight-ai/hs-embed-1")

FAST = es.Timeouts(
    poll_s=0.0,
    export_s=5.0,
    settle_s=3.0,
    health_s=3.0,
    restore_floor_s=5.0,
    restore_factor=1.0,
    quiet_s=3.0,
    rename_s=5.0,
    http_s=5.0,
)


def _counts(facts: int, observations: int = 0, models: int = 0, directives: int = 0, docs: int = 1):
    return {
        "facts": facts,
        "observations": observations,
        "mental_models": models,
        "directives": directives,
        "documents": docs,
    }


class FakeEngine:
    """hindsight-api, as far as the switch can tell."""

    def __init__(self, events: list[str], banks: dict[str, dict[str, int]]) -> None:
        self.events = events
        self.banks: dict[str, dict[str, int]] = {k: dict(v) for k, v in banks.items()}
        self.queue: dict[str, dict[str, int]] = {}
        self.features = {"document_export_api": True, "document_import_api": True}
        self.reachable = True
        self.healthy = True
        self.ops: dict[str, dict[str, Any]] = {}
        self.files: dict[str, bytes] = {}
        self.exported: dict[str, dict[str, int]] = {}
        #: bank -> counts delta applied to what a restore of it writes.
        self.restore_delta: dict[str, dict[str, int]] = {}
        #: Called with the bank on every export (to move a bank mid-run).
        self.on_export: Any = None
        #: Set to make a restore hang until released.
        self.hold_restore: asyncio.Event | None = None
        self.writes: list[tuple[str, str]] = []

    # ── helpers ──

    def _zip(self, bank: str, counts: dict[str, int]) -> bytes:
        buf = io.BytesIO()
        with zipfile.ZipFile(buf, "w") as zf:
            zf.writestr(
                "manifest.json",
                json.dumps(
                    {
                        "schema_version": 1,
                        "archive_type": "bank",
                        "source_bank_id": bank,
                        "document_count": counts["documents"],
                        "fact_count": counts["facts"],
                        "observation_count": counts["observations"],
                        "directive_count": counts["directives"],
                        # Standalone models only, as the engine writes it.
                        "mental_model_count": 0,
                        "knowledge_page_count": 0,
                    }
                ),
            )
            zf.writestr("mental_models.json", json.dumps([{}] * counts["mental_models"]))
        return buf.getvalue()

    def _stats(self, bank: str) -> dict[str, Any]:
        c = self.banks[bank]
        q = self.queue.get(bank, {})
        return {
            "bank_id": bank,
            "total_nodes": c["facts"] + c["observations"],
            "total_links": c["facts"] * 3,
            "total_documents": c["documents"],
            "nodes_by_fact_type": {
                "world": c["facts"] // 2,
                "experience": c["facts"] - c["facts"] // 2,
                "observation": c["observations"],
            },
            "total_observations": c["observations"],
            "operations_by_status": {
                "completed": 5,
                "pending": q.get("pending", 0),
                "processing": q.get("processing", 0),
            },
        }

    # ── the transport ──

    async def handler(self, request: httpx.Request) -> httpx.Response:
        if not self.reachable:
            raise httpx.ConnectError("connection refused", request=request)
        method, path = request.method, request.url.path
        params = dict(request.url.params)
        if method != "GET":
            self.writes.append((method, path))
        if path == "/health":
            return httpx.Response(200 if self.healthy else 503, json={"status": "healthy"})
        if not self.healthy:
            return httpx.Response(503, json={"detail": "starting"})
        if path == "/version":
            return httpx.Response(200, json={"api_version": "1.2.0", "features": self.features})
        if path == "/v1/default/banks":
            offset, limit = int(params.get("offset", 0)), int(params.get("limit", 100))
            ids = list(self.banks)
            rows = [{"bank_id": b} for b in ids[offset : offset + limit]]
            return httpx.Response(
                200, json={"banks": rows, "total": len(ids), "limit": limit, "offset": offset}
            )
        if path.startswith("/v1/default/files/download/"):
            return httpx.Response(200, content=self.files[path.rsplit("/", 1)[-1]])
        m = re.fullmatch(r"/v1/default/banks/([^/]+)(/.*)?", path)
        assert m, f"unexpected {method} {path}"
        bank, rest = m.group(1), m.group(2) or ""
        if rest.startswith("/operations/"):
            op = self.ops.get(rest.rsplit("/", 1)[-1])
            if method == "DELETE":
                self.events.append(f"cancel:{bank}")
                if op is not None:
                    op["status"] = "cancelled"
                return httpx.Response(200, json={"success": True})
            if op is not None and op.get("held") and self.hold_restore is not None:
                await self.hold_restore.wait()
            return httpx.Response(200, json=op or {"status": "not_found"})
        if rest == "/transfer/export" and method == "POST":
            if self.on_export is not None:
                self.on_export(bank)
            self.events.append(f"export:{bank}")
            key = f"exp{len(self.files)}"
            self.exported[bank] = dict(self.banks[bank])
            self.files[key] = self._zip(bank, self.banks[bank])
            self.ops[key] = {
                "status": "completed",
                "result_metadata": {"download_url": f"/v1/default/files/download/{key}"},
            }
            return httpx.Response(202, json={"operation_id": key, "status": "pending"})
        if rest == "/transfer/import" and method == "POST":
            assert params["mode"] == "restore"
            target = params["target_bank_id"]
            assert b"PK" in request.content, "the archive itself is uploaded"
            if target in self.banks:
                return httpx.Response(409, json={"detail": "bank exists"})
            self.events.append(f"restore:{bank}->{target}")
            counts = dict(self.exported[bank])
            for key, delta in self.restore_delta.get(bank, {}).items():
                counts[key] += delta
            op_id = f"imp{len(self.ops)}"
            self.ops[op_id] = {"status": "completed", "held": self.hold_restore is not None}
            self.banks[target] = counts
            return httpx.Response(202, json={"operation_id": op_id})
        if bank not in self.banks:
            return httpx.Response(404, json={"detail": "bank not found"})
        if rest == "" and method == "DELETE":
            self.events.append(f"delete:{bank}")
            del self.banks[bank]
            return httpx.Response(200, json={"success": True})
        if rest == "/stats":
            assert params.get("refresh") == "true", "counts must bypass the stats cache"
            return httpx.Response(200, json=self._stats(bank))
        if rest == "/mental-models":
            return httpx.Response(
                200, json={"items": [], "total": self.banks[bank]["mental_models"]}
            )
        if rest == "/directives":
            assert params.get("active_only") == "false", "inactive directives count too"
            return httpx.Response(200, json={"items": [], "total": self.banks[bank]["directives"]})
        raise AssertionError(f"unexpected {method} {path}")


class Host:
    """The box: the engine's environment, its drop-in, its admin command."""

    def __init__(self, events: list[str], engine: FakeEngine, live: Embedder) -> None:
        self.events = events
        self.engine = engine
        self.live = live
        #: Models the engine refuses to start on.
        self.dead_on: set[str] = set()
        #: When set, a restart does not change what the engine runs.
        self.override: Embedder | None = None
        self.dropin_error: dict[str, str] = {}
        self.rename_fails: set[str] = set()

    def apply_dropin(self, embedder: Embedder) -> dict[str, Any]:
        self.events.append(f"engine:{embedder.model}")
        if embedder.model in self.dropin_error:
            return {"error": self.dropin_error[embedder.model]}
        self.live = self.override or embedder
        self.engine.healthy = embedder.model not in self.dead_on
        return {"error": None, "written": True, "restarted": True}

    def evidence(self) -> dict[str, Any]:
        pair = self.live._asdict()
        return {"live": pair, "on_disk": pair, "on_disk_source": "unit"}

    def rename(self, old: str, new: str) -> str:
        self.events.append(f"rename:{old}->{new}")
        if new in self.rename_fails:
            raise es.SwitchError(f"hindsight-admin rename-bank exited 1: boom ({new})", bank=new)
        self.engine.banks[new] = self.engine.banks.pop(old)
        return "Renamed"


class Box:
    def __init__(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("HAL0_HOME", str(tmp_path))
        self.events: list[str] = []
        self.engine = FakeEngine(
            self.events,
            {
                "alpha": _counts(120, 40, 2, 1, 6),
                "Hal0::Beta": _counts(60, 10, 0, 3, 4),
                "gamma": _counts(9, 0, 1, 0, 2),
            },
        )
        self.host = Host(self.events, self.engine, BGE)
        self.hs = tmp_path / "hs"
        (self.hs / ".venv" / "bin").mkdir(parents=True)
        admin = self.hs / ".venv" / "bin" / "hindsight-admin"
        admin.write_text("#!/bin/sh\n")
        admin.chmod(0o755)
        (self.hs / ".pg0").mkdir()
        (self.hs / ".pg0" / "data").write_bytes(b"x" * 4096)
        er.save_embedder_record(EmbedderRecord(*BGE, source=er.SOURCE_ENGINE_DEFAULT))
        self.privileged = True
        self._http = httpx.AsyncClient(
            transport=httpx.MockTransport(self.engine.handler), base_url="http://127.0.0.1:9177"
        )
        real_engage, real_lift = write_gate.engage_gate, write_gate.lift_gate

        def engage(**kw: Any) -> dict[str, Any]:
            self.events.append("gate:on")
            return real_engage(**kw)

        def lift(*a: Any, **kw: Any) -> bool:
            self.events.append("gate:off")
            return real_lift(*a, **kw)

        monkeypatch.setattr(write_gate, "engage_gate", engage)
        monkeypatch.setattr(write_gate, "lift_gate", lift)

    def switch(self, target: Embedder | None = HS, **kw: Any) -> es.EmbedderSwitch:
        async def no_sleep(_s: float) -> None:
            await asyncio.sleep(0)

        ticks = iter(range(10**9))
        kw.setdefault("timeouts", FAST)
        kw.setdefault("evidence", self.host.evidence)
        return es.EmbedderSwitch(
            HindsightRestClient(http_client=self._http, api_key="hal0-local-noauth"),
            target=target,
            configured=HS,
            apply_dropin=self.host.apply_dropin,
            rename_bank=self.host.rename,
            privileged=lambda: self.privileged,
            hs_dir=self.hs,
            sleep=no_sleep,
            # Every look at the clock is one second later: a wait that polls
            # forever runs into its timeout instead of hanging the test.
            clock=lambda: float(next(ticks)),
            **kw,
        )

    def record(self) -> EmbedderRecord:
        record = er.load_embedder_record()
        assert record is not None
        return record

    def archives(self) -> list[Path]:
        return sorted(es.archive_root().glob("memory-embedder-switch-*/*.zip"))


@pytest.fixture
def box(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Box:
    return Box(tmp_path, monkeypatch)


def _codes(plan: dict[str, Any]) -> list[str]:
    return [r["code"] for r in plan["refusals"]]


ORIGINALS = ("alpha", "Hal0::Beta", "gamma")


# ── dry run ──────────────────────────────────────────────────────────────────


async def test_a_dry_run_reports_the_plan_and_changes_nothing(box: Box) -> None:
    before = er.embedder_record_path().read_bytes()

    plan = await box.switch().preflight()

    assert plan["refusals"] == []
    assert [b["bank"] for b in plan["banks"]] == list(ORIGINALS)
    assert plan["banks"][0]["counts"] == _counts(120, 40, 2, 1, 6)
    assert plan["totals"]["facts"] == 189 and plan["totals"]["observations"] == 50
    # (189 facts + 50 observations) at the measured 60 per second.
    assert plan["estimate_s"] == round(239 / es.RESTORE_FACTS_PER_S)
    assert plan["recorded"]["model"] == BGE.model and plan["target"] == HS._asdict()
    assert plan["disk"]["needed_bytes"] == int(4096 * es.DISK_FREE_FACTOR)
    # Nothing was written anywhere: no engine write, no restart, no file.
    assert box.engine.writes == []
    assert box.events == []
    assert er.embedder_record_path().read_bytes() == before
    assert write_gate.read_gate() is None
    assert not es.journal_path().exists() and not es.lock_path().exists()
    assert not es.archive_root().exists()


async def test_a_dry_run_on_a_box_with_no_record_writes_none(box: Box) -> None:
    er.embedder_record_path().unlink()

    plan = await box.switch().preflight()

    assert plan["refusals"] == []
    assert plan["recorded"]["model"] == BGE.model
    assert any("not written down yet" in w for w in plan["warnings"])
    assert not er.embedder_record_path().exists()


# ── preflight refusals ───────────────────────────────────────────────────────


async def test_refuses_the_embedder_the_store_is_already_on(box: Box) -> None:
    plan = await box.switch(BGE).preflight()
    assert _codes(plan) == ["same_embedder"]


async def test_a_mixed_store_may_be_switched_to_its_own_embedder(box: Box) -> None:
    er.save_embedder_record(EmbedderRecord(*BGE, source=er.SOURCE_PROCESS, mixed=True))
    plan = await box.switch(BGE).preflight()
    assert plan["refusals"] == [] and plan["mixed"] is True


async def test_refuses_a_target_the_config_would_not_accept(box: Box) -> None:
    plan = await box.switch(Embedder("local", "bad model; rm -rf")).preflight()
    assert _codes(plan) == ["invalid_target"]


async def test_refuses_when_the_engine_does_not_answer(box: Box) -> None:
    box.engine.reachable = False
    plan = await box.switch().preflight()
    assert _codes(plan) == ["engine_unreachable"]


async def test_refuses_a_record_that_cannot_be_read(box: Box) -> None:
    er.embedder_record_path().write_text("{not json")
    plan = await box.switch().preflight()
    assert "record_unreadable" in _codes(plan)
    assert er.embedder_record_path().read_text() == "{not json"


async def test_refuses_while_record_and_engine_disagree(box: Box) -> None:
    box.host.live = Embedder("local", "example-org/other")
    plan = await box.switch().preflight()
    assert _codes(plan) == ["record_conflict"]
    assert "example-org/other" in plan["refusals"][0]["message"]


async def test_refuses_when_the_engine_environment_cannot_be_read(box: Box) -> None:
    switch = box.switch(evidence=lambda: {"live": None, "on_disk": None, "on_disk_source": None})
    assert "engine_environment" in _codes(await switch.preflight())


@pytest.mark.parametrize(
    ("feature", "code"),
    [("document_export_api", "export_disabled"), ("document_import_api", "import_disabled")],
)
async def test_refuses_when_export_or_import_is_off(box: Box, feature: str, code: str) -> None:
    box.engine.features[feature] = False
    plan = await box.switch().preflight()
    assert _codes(plan) == [code]
    assert feature in plan["refusals"][0]["message"]


async def test_refuses_queued_work_and_reports_it_per_bank(box: Box) -> None:
    box.engine.queue["Hal0::Beta"] = {"pending": 7, "processing": 2}

    plan = await box.switch().preflight()

    assert _codes(plan) == ["pending_operations"]
    assert "Hal0::Beta (7 pending, 2 processing)" in plan["refusals"][0]["message"]
    beta = plan["banks"][1]
    assert (beta["pending"], beta["processing"]) == (7, 2)


async def test_allow_pending_turns_the_refusal_into_a_warning(box: Box) -> None:
    box.engine.queue["Hal0::Beta"] = {"pending": 7, "processing": 0}
    plan = await box.switch(allow_pending=True).preflight()
    assert plan["refusals"] == []
    assert any("not carried over" in w for w in plan["warnings"])


async def test_refuses_without_room_for_the_rebuilt_banks_and_archives(
    box: Box, monkeypatch: pytest.MonkeyPatch
) -> None:
    usage = type("U", (), {"free": 1024})
    monkeypatch.setattr(es.shutil, "disk_usage", lambda _p: usage)
    plan = await box.switch().preflight()
    assert _codes(plan) == ["disk_space"]


async def test_refuses_without_the_engine_rename_command(box: Box) -> None:
    (box.hs / ".venv" / "bin" / "hindsight-admin").unlink()
    assert _codes(await box.switch().preflight()) == ["rename_tool_missing"]


async def test_refuses_during_an_engine_upgrade(box: Box) -> None:
    (box.hs / ".venv.new").mkdir()
    assert _codes(await box.switch().preflight()) == ["engine_upgrade"]


async def test_refuses_an_untrusted_engine_data_directory(box: Box) -> None:
    (box.hs / ".pg0.restore-failed").write_text("snapshot\n")
    assert _codes(await box.switch().preflight()) == ["engine_data_untrusted"]


async def test_refuses_a_box_with_no_store(box: Box) -> None:
    import shutil

    er.embedder_record_path().unlink()
    shutil.rmtree(box.hs / ".pg0")
    switch = box.switch(evidence=lambda: {"live": None, "on_disk": None, "on_disk_source": None})
    assert "no_store" in _codes(await switch.preflight())


async def test_an_unprivileged_run_is_refused_before_anything_changes(box: Box) -> None:
    box.privileged = False

    with pytest.raises(es.SwitchRefused) as err:
        await box.switch().run()

    assert _codes(err.value.plan) == ["not_privileged"]
    assert box.engine.writes == [] and box.events == []
    assert not es.journal_path().exists()


# ── the run ──────────────────────────────────────────────────────────────────


async def test_the_switch_runs_its_steps_in_order(box: Box) -> None:
    result = await box.switch().run()

    temps = {b: es.temp_bank_id(b, result["run_id"]) for b in ORIGINALS}
    assert box.events == [
        *(f"export:{b}" for b in ORIGINALS),
        "gate:on",
        f"engine:{HS.model}",
        *(f"restore:{b}->{temps[b]}" for b in ORIGINALS),
        # Every bank is rebuilt and compared before the first original goes.
        *(e for b in ORIGINALS for e in (f"delete:{b}", f"rename:{temps[b]}->{b}")),
        "gate:off",
    ]
    assert result["status"] == "switched"
    assert sorted(box.engine.banks) == sorted(ORIGINALS)
    assert box.engine.banks["alpha"] == _counts(120, 40, 2, 1, 6)
    record = box.record()
    assert (record.embedder, record.mixed, record.conflict) == (HS, False, None)
    assert record.source == er.SOURCE_SWITCH
    assert write_gate.read_gate() is None and not es.journal_path().exists()


async def test_the_archives_are_kept_private_and_reported(box: Box, tmp_path: Path) -> None:
    result = await box.switch().run()

    run_dir = Path(result["archive_dir"])
    # hal0's shared restore-point directory, one private directory per run.
    assert run_dir.parent == es.archive_root() == tmp_path / "var-lib" / "hal0" / "backups"
    assert run_dir.name.startswith("memory-embedder-switch-")
    assert run_dir.name.endswith(result["run_id"])
    assert oct(run_dir.stat().st_mode & 0o777) == "0o700"
    archives = box.archives()
    assert [a.name for a in archives] == ["000-alpha.zip", "001-Hal0__Beta.zip", "002-gamma.zip"]
    assert all(oct(a.stat().st_mode & 0o777) == "0o600" for a in archives)
    assert result["delete_archives"] == f"rm -r {run_dir}"
    # The finished journal sits beside the archives it describes.
    done = json.loads((run_dir / "journal.json").read_text())
    assert done["phase"] == es.PHASE_COMPLETE
    assert [b["step"] for b in done["banks"]] == [es.STEP_RENAMED] * 3


async def test_a_mixed_store_is_repaired_by_a_switch_to_its_own_embedder(box: Box) -> None:
    er.save_embedder_record(EmbedderRecord(*BGE, source=er.SOURCE_PROCESS, mixed=True))

    await box.switch(BGE).run()

    assert (box.record().embedder, box.record().mixed) == (BGE, False)
    assert f"engine:{BGE.model}" in box.events


async def test_a_bank_written_between_its_export_and_the_pause_is_exported_again(box: Box) -> None:
    def late_write(bank: str) -> None:
        # A retain lands in alpha while the last bank is being exported.
        if bank == "gamma" and box.events.count("export:gamma") == 0:
            box.engine.banks["alpha"]["facts"] += 1
            box.engine.banks["alpha"]["documents"] += 1

    box.engine.on_export = late_write

    await box.switch().run()

    assert box.events[:5] == [
        "export:alpha",
        "export:Hal0::Beta",
        "export:gamma",
        "gate:on",
        "export:alpha",
    ]
    assert box.engine.banks["alpha"]["facts"] == 121


async def test_a_bank_that_keeps_moving_during_export_stops_the_run(box: Box) -> None:
    # Every archive is one fact short of the bank it was taken from.
    real_zip = box.engine._zip
    box.engine._zip = lambda bank, counts: real_zip(bank, {**counts, "facts": counts["facts"] - 1})

    with pytest.raises(es.SwitchError) as err:
        await box.switch().run()

    assert err.value.bank == "alpha" and "does not match the bank" in err.value.message
    assert "delete:alpha" not in box.events and write_gate.read_gate() is None
    assert box.record().embedder == BGE


# ── failures before the swap are undone ──────────────────────────────────────


def _assert_rolled_back(box: Box) -> None:
    assert sorted(box.engine.banks) == sorted(ORIGINALS), "originals intact, no temporary bank"
    assert not any(e in box.events for e in (f"delete:{b}" for b in ORIGINALS))
    assert not any(e.startswith("rename:") for e in box.events)
    record = box.record()
    assert (record.embedder, record.mixed) == (BGE, False)
    assert box.host.live == BGE
    assert write_gate.read_gate() is None, "the write gate is always lifted"
    assert box.events[-1] == "gate:off"
    assert not es.journal_path().exists()


async def test_a_count_mismatch_on_the_second_bank_rolls_everything_back(box: Box) -> None:
    box.engine.restore_delta["Hal0::Beta"] = {"facts": -3, "directives": -1}

    with pytest.raises(es.SwitchError) as err:
        await box.switch().run()

    assert err.value.bank == "Hal0::Beta" and not err.value.resumable
    assert err.value.details["differs"] == {
        "facts": {"expected": 60, "got": 57},
        "directives": {"expected": 3, "got": 2},
    }
    assert "Hal0::Beta" in err.value.message and "facts 57 (expected 60)" in err.value.message
    # The third bank was never restored; both temporary banks were removed.
    assert sum(e.startswith("restore:") for e in box.events) == 2
    assert sum(e.startswith("delete:") and ".switch-" in e for e in box.events) == 2
    # The engine went to the target and came back.
    assert [e for e in box.events if e.startswith("engine:")] == [
        f"engine:{HS.model}",
        f"engine:{BGE.model}",
    ]
    _assert_rolled_back(box)
    assert box.archives(), "the archives survive a failed run"


async def test_an_engine_that_does_not_come_up_on_the_target_is_rolled_back(box: Box) -> None:
    box.host.dead_on.add(HS.model)

    with pytest.raises(es.SwitchError) as err:
        await box.switch().run()

    assert "did not answer /health" in err.value.message
    assert not any(e.startswith("restore:") for e in box.events)
    _assert_rolled_back(box)
    assert box.engine.healthy


async def test_an_engine_that_comes_up_on_another_embedder_is_rolled_back(box: Box) -> None:
    """A later-sorting drop-in pins the embedder: the restart changes nothing."""
    box.host.override = BGE

    with pytest.raises(es.SwitchError) as err:
        await box.switch().run()

    assert "overrides the embedder" in err.value.message
    assert not any(e.startswith("restore:") for e in box.events)
    _assert_rolled_back(box)


async def test_a_refused_drop_in_write_is_rolled_back(box: Box) -> None:
    box.host.dropin_error[HS.model] = "could not write the drop-in: sudo: a password is required"

    with pytest.raises(es.SwitchError) as err:
        await box.switch().run()

    assert "a password is required" in err.value.message
    _assert_rolled_back(box)


async def test_a_writer_that_bypasses_hal0_stops_the_run_before_any_delete(box: Box) -> None:
    """The gate cannot stop a client on the engine's own port; the last check does."""
    switch = box.switch()
    real_check = switch._check_before_swap

    async def check(journal: es.Journal) -> None:
        box.engine.banks["gamma"]["facts"] += 2  # written during the window
        await real_check(journal)

    switch._check_before_swap = check  # type: ignore[method-assign]

    with pytest.raises(es.SwitchError) as err:
        await switch.run()

    assert err.value.bank == "gamma" and "without going through hal0" in err.value.message
    assert not any(e in box.events for e in (f"delete:{b}" for b in ORIGINALS))
    # gamma took a write while the engine ran the target: recorded as mixed.
    assert box.record().embedder == BGE and box.record().mixed is True
    assert write_gate.read_gate() is None and not es.journal_path().exists()


async def test_an_interrupt_before_the_swap_undoes_the_run_and_lifts_the_gate(box: Box) -> None:
    box.engine.hold_restore = asyncio.Event()
    task = asyncio.create_task(box.switch().run())
    while not any(e.startswith("restore:") for e in box.events):
        await asyncio.sleep(0)
    assert write_gate.read_gate() is not None

    task.cancel()  # what Ctrl-C and SIGTERM do to the run
    box.engine.hold_restore.set()
    with pytest.raises(asyncio.CancelledError):
        await task

    _assert_rolled_back(box)


async def test_a_rollback_that_cannot_finish_keeps_writes_paused_until_resume(box: Box) -> None:
    box.engine.restore_delta["alpha"] = {"facts": -1}
    box.host.dropin_error[BGE.model] = "systemctl restart hindsight-api.service failed"

    with pytest.raises(es.SwitchError) as err:
        await box.switch().run()

    assert err.value.resumable and "could not be undone" in err.value.message
    assert write_gate.read_gate() is not None
    journal = es.load_journal()
    assert journal is not None and journal.phase == es.PHASE_ROLLBACK_FAILED
    assert "interrupted_switch" in _codes(await box.switch().preflight())

    box.host.dropin_error.clear()
    result = await box.switch(None).resume()

    assert result["status"] == "rolled_back"
    _assert_rolled_back(box)


# ── the swap is resumable ────────────────────────────────────────────────────


async def test_a_failure_in_the_middle_of_the_swap_is_finished_by_resume(box: Box) -> None:
    box.host.rename_fails.add("Hal0::Beta")

    with pytest.raises(es.SwitchError) as err:
        await box.switch().run()

    assert err.value.resumable and err.value.bank == "Hal0::Beta"
    journal = es.load_journal()
    assert journal is not None and journal.phase == es.PHASE_SWAPPING
    steps = {b.bank: b.step for b in journal.banks}
    assert steps == {
        "alpha": es.STEP_RENAMED,
        "Hal0::Beta": es.STEP_DELETED,
        "gamma": es.STEP_RESTORED,
    }
    # Writes stay paused, the engine stays on the target, and no bank is
    # without either its original or its replacement.
    assert write_gate.read_gate() is not None and "gate:off" not in box.events
    assert box.host.live == HS
    for entry in journal.banks:
        assert entry.bank in box.engine.banks or entry.temp in box.engine.banks
    assert "delete:gamma" not in box.events, "the swap stops at the first failure"

    box.host.rename_fails.clear()
    before = len(box.events)
    result = await box.switch(None).resume()

    beta, gamma = journal.banks[1], journal.banks[2]
    assert box.events[before:] == [
        f"rename:{beta.temp}->Hal0::Beta",
        "delete:gamma",
        f"rename:{gamma.temp}->gamma",
        "gate:off",
    ]
    assert result["status"] == "switched"
    assert sorted(box.engine.banks) == sorted(ORIGINALS)
    assert (box.record().embedder, box.record().mixed) == (HS, False)
    assert write_gate.read_gate() is None and not es.journal_path().exists()


async def _stop_in_swap(box: Box, bank: str) -> es.Journal:
    box.host.rename_fails.add(bank)
    with pytest.raises(es.SwitchError):
        await box.switch().run()
    box.host.rename_fails.clear()
    journal = es.load_journal()
    assert journal is not None
    return journal


async def test_resume_restores_a_bank_that_exists_under_neither_id(box: Box) -> None:
    journal = await _stop_in_swap(box, "alpha")
    alpha = journal.banks[0]
    del box.engine.banks[alpha.temp]  # the rebuilt bank is lost too

    await box.switch(None).resume()

    assert "restore:alpha->alpha" in box.events
    assert box.engine.banks["alpha"] == _counts(120, 40, 2, 1, 6)
    assert sorted(box.engine.banks) == sorted(ORIGINALS)


async def test_resume_accepts_a_rename_that_finished_before_the_journal_said_so(box: Box) -> None:
    journal = await _stop_in_swap(box, "alpha")
    alpha = journal.banks[0]
    box.engine.banks["alpha"] = box.engine.banks.pop(alpha.temp)
    before = len(box.events)

    await box.switch(None).resume()

    assert not any(e.endswith("->alpha") for e in box.events[before:])
    assert sorted(box.engine.banks) == sorted(ORIGINALS)


async def test_resume_will_not_delete_a_bank_a_bypassing_writer_recreated(box: Box) -> None:
    journal = await _stop_in_swap(box, "alpha")
    box.engine.banks["alpha"] = _counts(1)  # recreated on demand by a direct write
    before = len(box.events)

    with pytest.raises(es.SwitchError) as err:
        await box.switch(None).resume()

    assert err.value.resumable and "recreated" in err.value.message
    assert box.events[before:] == []
    assert journal.banks[0].temp in box.engine.banks
    assert write_gate.read_gate() is not None


async def test_resume_of_an_interrupted_run_before_the_swap_undoes_it(box: Box) -> None:
    """A run killed outright (no chance to roll back) while it was restoring."""
    switch = box.switch()

    class Killed(BaseException):
        pass

    async def die(*_args: Any) -> None:
        raise Killed

    switch._check_before_swap = die  # type: ignore[method-assign]
    switch._rollback = die  # type: ignore[method-assign]
    with pytest.raises(Killed):
        await switch.run()
    assert write_gate.read_gate() is not None and box.record().embedder == HS
    assert len(box.engine.banks) == 6

    result = await box.switch(None).resume()

    assert result["status"] == "rolled_back"
    _assert_rolled_back(box)


async def test_resume_puts_back_a_pause_that_was_removed_by_hand(box: Box) -> None:
    await _stop_in_swap(box, "Hal0::Beta")
    write_gate.gate_path().unlink()
    before = len(box.events)

    await box.switch(None).resume()

    assert box.events[before] == "gate:on", "paused again before the first delete or rename"
    assert box.events[-1] == "gate:off" and write_gate.read_gate() is None


async def test_a_run_that_cannot_create_its_archive_directory_changes_nothing(
    box: Box, monkeypatch: pytest.MonkeyPatch
) -> None:
    def denied(_self: Any, _run_id: str) -> Path:
        raise PermissionError(13, "Permission denied")

    monkeypatch.setattr(es.EmbedderSwitch, "_make_archive_dir", denied)

    with pytest.raises(es.SwitchRefused) as err:
        await box.switch().run()

    assert "could not create the archive directory" in err.value.message
    assert box.events == [] and box.engine.writes == []
    assert not es.journal_path().exists()


async def test_resume_with_nothing_to_resume_is_refused(box: Box) -> None:
    with pytest.raises(es.SwitchRefused):
        await box.switch(None).resume()
    assert box.events == []


async def test_resume_refuses_a_different_target_than_the_interrupted_run(box: Box) -> None:
    await _stop_in_swap(box, "alpha")
    with pytest.raises(es.SwitchRefused) as err:
        await box.switch(BGE).resume()
    assert "hs-embed-1" in err.value.message


# ── the lock ─────────────────────────────────────────────────────────────────


async def test_a_second_run_is_refused_while_one_holds_the_lock(box: Box) -> None:
    es.lock_path().parent.mkdir(parents=True, exist_ok=True)
    fd = os.open(es.lock_path(), os.O_RDWR | os.O_CREAT, 0o644)
    fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
    try:
        with pytest.raises(es.SwitchRefused) as err:
            await box.switch().run()
        assert "another `hal0 memory embedder switch` is running" in err.value.message
        assert box.engine.writes == [] and box.events == []
        assert "locked" in _codes(await box.switch().preflight())
        with pytest.raises(es.SwitchRefused):
            await box.switch(None).resume()
    finally:
        os.close(fd)

    # The lock dies with its holder: nothing stale is left to clear.
    assert (await box.switch().run())["status"] == "switched"


# ── allow-pending ────────────────────────────────────────────────────────────


async def test_allow_pending_goes_ahead_and_says_what_was_left_behind(box: Box) -> None:
    box.engine.queue["gamma"] = {"pending": 0, "processing": 1}
    switch = box.switch(allow_pending=True)
    real_check = switch._check_before_swap

    async def check(journal: es.Journal) -> None:
        box.engine.banks["gamma"]["facts"] += 4  # the running operation finishes late
        await real_check(journal)

    switch._check_before_swap = check  # type: ignore[method-assign]

    result = await switch.run()

    assert result["status"] == "switched"
    assert box.engine.banks["gamma"]["facts"] == 9, "what the archive held"
    assert any("gamma" in w and "not carried over" in w for w in result["warnings"])


async def test_queued_work_that_never_drains_stops_the_run_before_the_flip(box: Box) -> None:
    def enqueue(bank: str) -> None:
        if bank == "gamma":
            box.engine.queue["alpha"] = {"pending": 1, "processing": 0}

    box.engine.on_export = enqueue

    with pytest.raises(es.SwitchError) as err:
        await box.switch().run()

    assert "did not finish within" in err.value.message and "alpha (1 pending" in err.value.message
    assert not any(e.startswith("engine:") for e in box.events), "nothing was flipped"
    assert box.events[-2:] == ["gate:on", "gate:off"]
    assert box.record().embedder == BGE and not es.journal_path().exists()


# ── pieces ───────────────────────────────────────────────────────────────────


def test_the_temporary_id_fits_the_bank_id_grammar() -> None:
    from hal0.api.routes.memory_admin import _BANK_RE

    assert es.temp_bank_id("Hal0::Agents", "3f9a1c2e") == "Hal0::Agents.switch-3f9a1c2e"
    for bank in ("shared", "coding-agent::ultrareview-1.4", "x" * 128):
        temp = es.temp_bank_id(bank, "3f9a1c2e")
        assert _BANK_RE.match(temp) and len(temp) <= 128
    long_a, long_b = es.temp_bank_id("a" * 128, "3f9a1c2e"), es.temp_bank_id("b" * 128, "3f9a1c2e")
    assert long_a != long_b and long_a.startswith("switch-3f9a1c2e-")


def test_the_rename_runs_the_engine_admin_command_in_the_engine_environment(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from hal0.memory import extraction_env

    monkeypatch.setattr(
        extraction_env,
        "hindsight_unit_env",
        lambda: {"HOME": "/engine/home", "HINDSIGHT_API_DATABASE_SCHEMA": "public"},
    )
    env_file = tmp_path / "hindsight-llm.env"
    env_file.write_text("HINDSIGHT_API_LLM_API_KEY=sk-secret\n")
    monkeypatch.setattr(extraction_env, "HINDSIGHT_LLM_ENV_PATH", env_file)
    seen: dict[str, Any] = {}

    def runner(argv: list[str], **kw: Any) -> subprocess.CompletedProcess[str]:
        seen.update(argv=argv, **kw)
        return subprocess.CompletedProcess(argv, 0, "Renamed: 12 row(s) moved.\n", "")

    out = es.run_admin_rename(
        tmp_path, "shared.switch-ab12cd34", "shared", timeout_s=9, runner=runner, euid=lambda: 1000
    )

    assert out == "Renamed: 12 row(s) moved."
    assert seen["argv"] == [
        str(tmp_path / ".venv" / "bin" / "hindsight-admin"),
        "rename-bank",
        "--from",
        "shared.switch-ab12cd34",
        "--to",
        "shared",
    ]
    assert seen["env"]["HOME"] == "/engine/home"
    assert seen["env"]["HINDSIGHT_API_DATABASE_SCHEMA"] == "public"
    # The unit's EnvironmentFile is layered on top, in the child's environment only.
    assert seen["env"]["HINDSIGHT_API_LLM_API_KEY"] == "sk-secret"
    assert "sk-secret" not in " ".join(seen["argv"])
    assert seen["cwd"] == str(tmp_path) and seen["timeout"] == 9
    assert "user" not in seen, "an unprivileged caller runs it as itself"


def test_a_root_caller_runs_the_rename_as_the_owner_of_the_engine_home(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from hal0.memory import extraction_env

    monkeypatch.setattr(extraction_env, "hindsight_unit_env", lambda: {})
    monkeypatch.setattr(extraction_env, "HINDSIGHT_LLM_ENV_PATH", tmp_path / "absent")
    seen: dict[str, Any] = {}

    def runner(argv: list[str], **kw: Any) -> subprocess.CompletedProcess[str]:
        seen.update(kw)
        return subprocess.CompletedProcess(argv, 0, "", "")

    es.run_admin_rename(tmp_path, "a", "b", timeout_s=1, runner=runner, euid=lambda: 0)

    owner = tmp_path.stat()
    assert (seen["user"], seen["group"]) == (owner.st_uid, owner.st_gid)
    assert seen["env"]["HOME"] == str(tmp_path), "pg0 lives under the engine home"


def test_a_failed_rename_carries_the_command_output(tmp_path: Path, monkeypatch) -> None:
    from hal0.memory import extraction_env

    monkeypatch.setattr(extraction_env, "hindsight_unit_env", lambda: {})
    monkeypatch.setattr(extraction_env, "HINDSIGHT_LLM_ENV_PATH", tmp_path / "absent")

    def runner(argv: list[str], **kw: Any) -> subprocess.CompletedProcess[str]:
        return subprocess.CompletedProcess(
            argv, 1, "", "Error: bank 'a' has 2 pending or processing operation(s)\n"
        )

    with pytest.raises(es.SwitchError) as err:
        es.run_admin_rename(tmp_path, "a", "b", timeout_s=1, runner=runner, euid=lambda: 1000)
    assert "exited 1" in err.value.message and "2 pending or processing" in err.value.message


def test_an_unreadable_journal_is_reported_not_ignored(box: Box) -> None:
    es.journal_path().write_text("{broken")
    left = es.interrupted_switch()
    assert left is not None and left["phase"] == "unreadable"
    with pytest.raises(es.SwitchError):
        es.load_journal()
