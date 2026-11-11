"""``hal0 memory embedder switch`` (#3518, ADR-0058 decision 2).

The engine is an ``httpx.MockTransport`` behind the real
``HindsightRestClient``; it keeps banks in memory and answers the routes the
switch uses the way hindsight-api 1.2.0 does (shapes read from its OpenAPI).
The host side (drop-in write and restart, the engine's live environment, the
admin rename) is injected, and every step lands in one ordered event list.

A run is stopped at an exact point through the switch's own ``hook``.
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

NORMAL_PORT = 9177
PRIVATE_PORT = 45123
BGE = Embedder("local", "BAAI/bge-small-en-v1.5")
HS = Embedder("local", "hindsight-ai/hs-embed-1")

FAST = es.Timeouts(
    poll_s=0.0,
    export_s=5.0,
    settle_s=3.0,
    health_s=3.0,
    restore_floor_s=6.0,
    restore_factor=1.0,
    quiet_s=3.0,
    rename_s=5.0,
    http_s=5.0,
)


class Killed(BaseException):
    """What a ``kill -9`` looks like to the code under test: nothing runs after it."""


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
        #: Per bank: write revision, model/directive text, config, operation ids (newest first).
        self.meta: dict[str, dict[str, Any]] = {}
        self.queue: dict[str, dict[str, int]] = {}
        self.features = {"document_export_api": True, "document_import_api": True}
        #: The one port the engine listens on. Every other port refuses the connection.
        self.port = NORMAL_PORT
        self.reachable = True
        self.healthy = True
        self.ops: dict[str, dict[str, Any]] = {}
        self.files: dict[str, bytes] = {}
        #: bank -> counts delta applied to what a restore of it writes.
        self.restore_delta: dict[str, dict[str, int]] = {}
        #: bank -> counts delta applied to what its archive claims to hold.
        self.archive_delta: dict[str, dict[str, int]] = {}
        #: Called with the bank on every export, and on every restore.
        self.on_export: Any = None
        self.on_restore: Any = None
        #: Set to make a restore hang until released.
        self.hold_restore: asyncio.Event | None = None
        #: Banks whose restore never finishes by itself.
        self.stuck_restore: set[str] = set()
        #: How many more reads a cancelled operation stays ``processing`` for.
        self.cancel_lag = 0
        self.writes: list[tuple[str, str]] = []

    # ── what a test does to the engine behind the switch's back ──

    def m(self, bank: str) -> dict[str, Any]:
        return self.meta.setdefault(
            bank, {"rev": 0, "mm": "", "dir": "", "config": {}, "ops": [], "profile": 0}
        )

    def direct_write(self, bank: str, facts: int = 1) -> None:
        """A retain that reached the engine without going through hal0."""
        self.banks[bank]["facts"] += facts
        self.banks[bank]["documents"] += 1
        self.m(bank)["rev"] += 1

    def create(self, bank: str, counts: dict[str, int] | None = None) -> None:
        self.banks[bank] = dict(counts or _counts(0, docs=0))

    def drop(self, bank: str) -> None:
        self.banks.pop(bank, None)
        self.meta.pop(bank, None)
        self.queue.pop(bank, None)

    def move(self, old: str, new: str) -> None:
        self.banks[new] = self.banks.pop(old)
        if old in self.meta:
            self.meta[new] = self.meta.pop(old)

    # ── helpers ──

    def _zip(self, bank: str, counts: dict[str, int]) -> bytes:
        counts = dict(counts)
        for key, delta in self.archive_delta.get(bank, {}).items():
            counts[key] += delta
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

    @staticmethod
    def _unzip(body: bytes) -> tuple[str, dict[str, int]]:
        """Source bank and counts of the archive inside a multipart upload."""
        with zipfile.ZipFile(io.BytesIO(body)) as zf:
            manifest = json.loads(zf.read("manifest.json"))
            models = len(json.loads(zf.read("mental_models.json")))
        return manifest["source_bank_id"], {
            "facts": manifest["fact_count"],
            "observations": manifest["observation_count"],
            "mental_models": models,
            "directives": manifest["directive_count"],
            "documents": manifest["document_count"],
        }

    def _stats(self, bank: str) -> dict[str, Any]:
        c, q, m = self.banks[bank], self.queue.get(bank, {}), self.m(bank)
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
            "last_memory_write_at": f"write-{m['rev']}",
            "last_consolidated_at": None,
            "pending_consolidation": q.get("consolidating", 0),
            "failed_consolidation": 0,
            "operations_by_status": {
                "completed": len(m["ops"]),
                "pending": q.get("pending", 0),
                "processing": q.get("processing", 0),
            },
        }

    def _row(self, bank: str) -> dict[str, Any]:
        c, m = self.banks[bank], self.m(bank)
        return {
            "bank_id": bank,
            "fact_count": c["facts"] + c["observations"],
            "last_write_at": f"write-{m['rev']}",
            "last_document_at": f"doc-{c['documents']}",
            "updated_at": f"profile-{m['profile']}",
        }

    def _page(self, key: str, rows: list[Any], params: dict[str, str]) -> httpx.Response:
        offset, limit = int(params.get("offset", 0)), int(params.get("limit", 100))
        return httpx.Response(
            200,
            json={key: rows[offset : offset + limit], "total": len(rows), "limit": limit},
        )

    # ── the transport ──

    async def handler(self, request: httpx.Request) -> httpx.Response:
        if not self.reachable or request.url.port != self.port:
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
            wanted = params.get("q", "")
            rows = [self._row(b) for b in self.banks if wanted.lower() in b.lower()]
            return self._page("banks", rows, params)
        if path.startswith("/v1/default/files/download/"):
            return httpx.Response(200, content=self.files[path.rsplit("/", 1)[-1]])
        m = re.fullmatch(r"/v1/default/banks/([^/]+)(/.*)?", path)
        assert m, f"unexpected {method} {path}"
        bank, rest = m.group(1), m.group(2) or ""
        if rest.startswith("/operations/"):
            op = self.ops.get(rest.rsplit("/", 1)[-1])
            if method == "DELETE":
                self.events.append(f"cancel:{bank}")
                if op is not None and op["status"] in ("pending", "processing"):
                    op["cancelled"] = self.cancel_lag
                return httpx.Response(200, json={"success": True})
            if op is not None and op.get("held") and self.hold_restore is not None:
                await self.hold_restore.wait()
            if op is not None and "cancelled" in op:
                self.events.append(f"poll-cancelled:{bank}")
                if op["cancelled"] <= 0:
                    op["status"] = "cancelled"
                op["cancelled"] -= 1
            return httpx.Response(200, json=op or {"status": "not_found"})
        if rest == "" and method == "PUT":
            self.events.append(f"create:{bank}")
            self.banks.setdefault(bank, _counts(0, docs=0))
            return httpx.Response(200, json={"bank_id": bank})
        if rest == "/transfer/import" and method == "POST":
            assert params["mode"] == "restore"
            target = params["target_bank_id"]
            source, counts = self._unzip(request.content)
            assert bank in self.banks, "the engine records a restore against an existing bank"
            if target in self.banks:
                return httpx.Response(409, json={"detail": "bank exists"})
            if self.on_restore is not None:
                self.on_restore(source)
            self.events.append(
                f"restore:{source}->{target}" + ("" if bank == source else f" via {bank}")
            )
            for key, delta in self.restore_delta.get(source, {}).items():
                counts[key] += delta
            op_id = f"imp{len(self.ops)}"
            stuck = source in self.stuck_restore
            self.ops[op_id] = {
                "status": "processing" if stuck else "completed",
                "held": self.hold_restore is not None,
            }
            self.m(bank)["ops"].insert(0, op_id)
            if not stuck:
                self.banks[target] = counts
            return httpx.Response(202, json={"operation_id": op_id})
        if bank not in self.banks:
            return httpx.Response(404, json={"detail": "bank not found"})
        if rest == "/transfer/export" and method == "POST":
            if self.on_export is not None:
                self.on_export(bank)
            self.events.append(f"export:{bank}")
            key = f"exp{len(self.files)}"
            self.files[key] = self._zip(bank, self.banks[bank])
            self.ops[key] = {
                "status": "completed",
                "result_metadata": {"download_url": f"/v1/default/files/download/{key}"},
            }
            self.m(bank)["ops"].insert(0, key)
            return httpx.Response(202, json={"operation_id": key, "status": "pending"})
        if rest == "" and method == "DELETE":
            self.events.append(f"delete:{bank}")
            self.drop(bank)
            return httpx.Response(200, json={"success": True})
        if rest == "/stats":
            assert params.get("refresh") == "true", "counts must bypass the stats cache"
            return httpx.Response(200, json=self._stats(bank))
        if rest == "/mental-models":
            assert params.get("detail") == "content"
            rows = [
                {
                    "id": f"mm-{i}",
                    "bank_id": bank,
                    "name": self.m(bank)["mm"],
                    "source_query": "what matters",
                    # Generated by the engine; refreshed whenever it likes.
                    "content": self.m(bank).get("mm_content", ""),
                    "last_refreshed_at": self.m(bank).get("mm_refreshed"),
                    "is_stale": True,
                }
                for i in range(self.banks[bank]["mental_models"])
            ]
            return self._page("items", rows, params)
        if rest == "/directives":
            assert params.get("active_only") == "false", "inactive directives count too"
            rows = [
                {"id": f"d-{i}", "bank_id": bank, "content": self.m(bank)["dir"]}
                for i in range(self.banks[bank]["directives"])
            ]
            return self._page("items", rows, params)
        if rest == "/config":
            return httpx.Response(200, json={"bank_id": bank, "overrides": self.m(bank)["config"]})
        if rest == "/operations":
            rows = [{"id": op} for op in self.m(bank)["ops"]]
            return httpx.Response(200, json={"operations": rows[: int(params["limit"])]})
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
        #: The port in the isolation drop-in, as written; read at the next restart.
        self.dropin_port: int | None = None
        #: A drop-in that sorts after hal0's and replaces ExecStart: isolation does not take.
        self.exec_start_pinned = False
        self.isolate_error: str | None = None
        #: When set, a restart to the engine's normal address fails with this.
        self.release_restart_error: str | None = None

    def isolate(self, port: int, run_id: str) -> dict[str, Any]:
        self.events.append("isolate")
        if self.isolate_error:
            return {"error": self.isolate_error}
        self.dropin_port = port
        return {"error": None, "exec_start": "hindsight-api --host 127.0.0.1 --port 9177"}

    def release(self) -> dict[str, Any]:
        self.events.append("release")
        self.dropin_port = None
        return {"error": None}

    def isolated_port(self) -> int | None:
        return self.dropin_port

    def apply_dropin(self, embedder: Embedder) -> dict[str, Any]:
        self.events.append(f"engine:{embedder.model}")
        if embedder.model in self.dropin_error:
            return {"error": self.dropin_error[embedder.model]}
        if self.dropin_port is None and self.release_restart_error:
            return {"error": self.release_restart_error}
        self.live = self.override or embedder
        self.engine.healthy = embedder.model not in self.dead_on
        private = self.dropin_port is not None and not self.exec_start_pinned
        self.engine.port = self.dropin_port if private else NORMAL_PORT
        return {"error": None, "written": True, "restarted": True}

    def evidence(self) -> dict[str, Any]:
        pair = self.live._asdict()
        return {"live": pair, "on_disk": pair, "on_disk_source": "unit"}

    def rename(self, old: str, new: str) -> str:
        self.events.append(f"rename:{old}->{new}")
        if new in self.rename_fails:
            raise es.SwitchError(f"hindsight-admin rename-bank exited 1: boom ({new})", bank=new)
        self.engine.move(old, new)
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
        self._http = self.http(f"http://127.0.0.1:{NORMAL_PORT}")
        real_engage, real_lift = write_gate.engage_gate, write_gate.lift_gate

        def engage(**kw: Any) -> dict[str, Any]:
            self.events.append("gate:on")
            return real_engage(**kw)

        #: Whether an unfinished journal was still on disk each time the gate was lifted.
        self.journal_at_lift: list[bool] = []

        def lift(*a: Any, **kw: Any) -> bool:
            self.events.append("gate:off")
            self.journal_at_lift.append(es.journal_path().exists())
            return real_lift(*a, **kw)

        monkeypatch.setattr(write_gate, "engage_gate", engage)
        monkeypatch.setattr(write_gate, "lift_gate", lift)

    def http(self, base_url: str) -> httpx.AsyncClient:
        return httpx.AsyncClient(
            transport=httpx.MockTransport(self.engine.handler), base_url=base_url
        )

    def client(self, base_url: str) -> HindsightRestClient:
        return HindsightRestClient(
            base_url=base_url, http_client=self.http(base_url), api_key="hal0-local-noauth"
        )

    def switch(self, target: Embedder | None = HS, **kw: Any) -> es.EmbedderSwitch:
        async def no_sleep(_s: float) -> None:
            await asyncio.sleep(0)

        ticks = iter(range(10**9))
        kw.setdefault("timeouts", FAST)
        kw.setdefault("evidence", self.host.evidence)
        return es.EmbedderSwitch(
            self.client(f"http://127.0.0.1:{NORMAL_PORT}"),
            target=target,
            configured=HS,
            apply_dropin=self.host.apply_dropin,
            rename_bank=self.host.rename,
            isolate=self.host.isolate,
            release=self.host.release,
            isolated_port=self.host.isolated_port,
            client_for=self.client,
            pick_port=lambda: PRIVATE_PORT,
            privileged=lambda: self.privileged,
            hs_dir=self.hs,
            sleep=no_sleep,
            # Every look at the clock is one second later: a wait that polls
            # forever runs into its timeout instead of hanging the test.
            clock=lambda: float(next(ticks)),
            **kw,
        )

    async def kill_at(self, point: str, **kw: Any) -> es.Journal:
        """Run the switch and kill it at ``point``: no rollback, no cleanup."""

        def hook(reached: str) -> None:
            if reached == point:
                raise Killed

        switch = self.switch(hook=hook, **kw)

        async def no_rollback(*_a: Any) -> None:
            raise Killed

        switch._rollback = no_rollback  # type: ignore[method-assign]
        with pytest.raises(Killed):
            await switch.run()
        journal = es.load_journal()
        assert journal is not None
        return journal

    def record(self) -> EmbedderRecord:
        record = er.load_embedder_record()
        assert record is not None
        return record

    def archives(self) -> list[Path]:
        return sorted(es.archive_root().glob("memory-embedder-switch-*/*.zip"))

    def temps(self) -> list[str]:
        return [b for b in self.engine.banks if es.is_switch_temp(b)]


@pytest.fixture
def box(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Box:
    return Box(tmp_path, monkeypatch)


def _codes(plan: dict[str, Any]) -> list[str]:
    return [r["code"] for r in plan["refusals"]]


ORIGINALS = ("alpha", "Hal0::Beta", "gamma")
START = {
    "alpha": _counts(120, 40, 2, 1, 6),
    "Hal0::Beta": _counts(60, 10, 0, 3, 4),
    "gamma": _counts(9, 0, 1, 0, 2),
}


def _assert_public(box: Box) -> None:
    assert box.engine.port == NORMAL_PORT, "the engine is back at its normal address"
    assert box.host.dropin_port is None, "and the isolation drop-in is gone"


def _assert_switched(box: Box, banks: dict[str, dict[str, int]] | None = None) -> None:
    _assert_public(box)
    assert box.engine.banks == (banks or START), "every bank under its own id, whole"
    record = box.record()
    assert (record.embedder, record.mixed, record.conflict) == (HS, False, None)
    assert box.host.live == HS
    assert write_gate.read_gate() is None and not es.journal_path().exists()
    assert es.interrupted_switch() is None


def _assert_rolled_back(box: Box, banks: dict[str, dict[str, int]] | None = None) -> None:
    _assert_public(box)
    assert box.engine.banks == (banks or START), "originals intact, no temporary bank"
    assert not any(e in box.events for e in (f"delete:{b}" for b in ORIGINALS))
    assert not any(e.startswith("rename:") for e in box.events)
    record = box.record()
    assert (record.embedder, record.mixed) == (BGE, False)
    assert box.host.live == BGE
    assert write_gate.read_gate() is None, "the write gate is always lifted"
    assert box.events[-1] == "gate:off"
    assert not es.journal_path().exists() and es.interrupted_switch() is None


# ── dry run ──────────────────────────────────────────────────────────────────


async def test_a_dry_run_reports_the_plan_and_writes_nothing_at_all(
    box: Box, tmp_path: Path
) -> None:
    before = {p: p.read_bytes() for p in tmp_path.rglob("*") if p.is_file()}

    plan = await box.switch().preflight()

    assert plan["refusals"] == []
    assert [b["bank"] for b in plan["banks"]] == list(ORIGINALS)
    assert plan["banks"][0]["counts"] == _counts(120, 40, 2, 1, 6)
    assert plan["totals"]["facts"] == 189 and plan["totals"]["observations"] == 50
    # What a run costs: three engine restarts, an export and a restore per
    # bank, and (189 facts + 50 observations) at the measured 60 per second.
    assert plan["estimate"] == {"restarts_s": 75, "per_bank_s": 6, "embedding_s": 4}
    assert plan["estimate_s"] == 85
    assert plan["recorded"]["model"] == BGE.model and plan["target"] == HS._asdict()
    # No engine write (so no export), no restart, and not one file created or
    # changed: no gate, no journal, no lock, no archive directory, no record.
    assert box.engine.writes == [] and box.events == []
    assert {p: p.read_bytes() for p in tmp_path.rglob("*") if p.is_file()} == before
    assert not es.archive_root().exists() and not es.lock_path().exists()


async def test_a_dry_run_on_a_box_with_no_record_writes_none(box: Box) -> None:
    er.embedder_record_path().unlink()

    plan = await box.switch().preflight()

    assert plan["refusals"] == []
    assert plan["recorded"]["model"] == BGE.model
    assert any("not written down yet" in w for w in plan["warnings"])
    assert not er.embedder_record_path().exists()


async def test_a_refused_apply_writes_nothing_either(box: Box) -> None:
    """The record is written down only after every refusal has been evaluated."""
    er.embedder_record_path().unlink()
    box.privileged = False

    with pytest.raises(es.SwitchRefused) as err:
        await box.switch().run()

    assert _codes(err.value.plan) == ["not_privileged"]
    assert not er.embedder_record_path().exists()
    assert box.engine.writes == [] and box.events == []
    assert not es.journal_path().exists() and not es.archive_root().exists()


# ── preflight refusals ───────────────────────────────────────────────────────


async def test_refuses_the_embedder_the_store_is_already_on(box: Box) -> None:
    assert _codes(await box.switch(BGE).preflight()) == ["same_embedder"]


async def test_a_mixed_store_may_be_switched_to_its_own_embedder(box: Box) -> None:
    er.save_embedder_record(EmbedderRecord(*BGE, source=er.SOURCE_PROCESS, mixed=True))
    plan = await box.switch(BGE).preflight()
    assert plan["refusals"] == [] and plan["mixed"] is True


async def test_refuses_a_target_the_config_would_not_accept(box: Box) -> None:
    plan = await box.switch(Embedder("local", "bad model; rm -rf")).preflight()
    assert _codes(plan) == ["invalid_target"]


async def test_refuses_when_the_engine_does_not_answer(box: Box) -> None:
    box.engine.reachable = False
    assert _codes(await box.switch().preflight()) == ["engine_unreachable"]


async def test_refuses_a_record_that_cannot_be_read(box: Box) -> None:
    er.embedder_record_path().write_text("{not json")
    assert "record_unreadable" in _codes(await box.switch().preflight())
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


def _usage(free: int) -> Any:
    return type("U", (), {"free": free})


async def test_refuses_without_room_beside_the_engine_data_for_the_rebuilt_banks(
    box: Box, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(es.shutil, "disk_usage", lambda _p: _usage(4096 + 1024))
    plan = await box.switch().preflight()
    assert _codes(plan) == ["disk_space"]
    disk = plan["disk"]
    # Same filesystem here: the rebuilt banks (1x) and the archives (0.5x) add up.
    assert disk["same_filesystem"] is True and disk["store_needed_bytes"] == 4096 + 2048
    assert "engine's data" in plan["refusals"][0]["message"]


async def test_the_engine_filesystem_and_the_archive_filesystem_are_checked_apart(
    box: Box, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Archives on a roomy disk do not excuse a full disk under the engine's data."""
    monkeypatch.setattr(es, "_device", lambda p: 2 if "backups" in str(p) else 1)
    monkeypatch.setattr(es, "_nearest_existing", lambda p: p)
    monkeypatch.setattr(
        es.shutil, "disk_usage", lambda p: _usage(10**12 if "backups" in str(p) else 100)
    )

    plan = await box.switch().preflight()

    assert _codes(plan) == ["disk_space"] and plan["disk"]["same_filesystem"] is False
    assert plan["disk"]["store_needed_bytes"] == 4096


async def test_refuses_without_the_engine_rename_command(box: Box) -> None:
    (box.hs / ".venv" / "bin" / "hindsight-admin").unlink()
    assert _codes(await box.switch().preflight()) == ["rename_tool_missing"]


async def test_refuses_when_the_units_environment_file_cannot_be_read(
    box: Box, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(es, "admin_env_file_unreadable", lambda: "/etc/hal0/hindsight-llm.env")
    plan = await box.switch().preflight()
    assert _codes(plan) == ["env_file_unreadable"]
    assert "run this as root" in plan["refusals"][0]["message"]


async def test_refuses_during_an_engine_upgrade(box: Box) -> None:
    (box.hs / ".venv.new").mkdir()
    assert _codes(await box.switch().preflight()) == ["engine_upgrade"]


async def test_refuses_an_untrusted_engine_data_directory(box: Box) -> None:
    (box.hs / ".pg0.restore-failed").write_text("snapshot\n")
    assert _codes(await box.switch().preflight()) == ["engine_data_untrusted"]


async def test_refuses_while_an_update_is_queued_or_running(box: Box, tmp_path: Path) -> None:
    jobs = tmp_path / "var-lib" / "hal0" / "update-jobs"
    jobs.mkdir(parents=True)
    (jobs / "0123456789ab.json").write_text(json.dumps({"id": "0123456789ab", "state": "running"}))
    (jobs / "ffffffffffff.json").write_text(json.dumps({"id": "ffffffffffff", "state": "done"}))

    plan = await box.switch().preflight()

    assert _codes(plan) == ["update_in_progress"]
    assert "0123456789ab" in plan["refusals"][0]["message"]


async def test_refuses_a_box_with_no_store(box: Box) -> None:
    import shutil

    er.embedder_record_path().unlink()
    shutil.rmtree(box.hs / ".pg0")
    switch = box.switch(evidence=lambda: {"live": None, "on_disk": None, "on_disk_source": None})
    assert "no_store" in _codes(await switch.preflight())


# ── the run ──────────────────────────────────────────────────────────────────


async def test_the_switch_isolates_the_engine_and_runs_its_steps_in_order(box: Box) -> None:
    result = await box.switch().run()

    temps = {b: es.temp_bank_id(b, result["run_id"]) for b in ORIGINALS}
    assert box.events == [
        # Writes are paused, then the engine is taken off its normal address,
        # still on its original embedder, before anything is listed or exported.
        "gate:on",
        "isolate",
        f"engine:{BGE.model}",
        *(f"export:{b}" for b in ORIGINALS),
        "isolate",
        f"engine:{HS.model}",
        *(f"restore:{b}->{temps[b]}" for b in ORIGINALS),
        # Every bank is rebuilt and compared before the first original goes.
        *(e for b in ORIGINALS for e in (f"delete:{b}", f"rename:{temps[b]}->{b}")),
        # The engine goes back to its normal address before writes are let in.
        "release",
        f"engine:{HS.model}",
        "gate:off",
    ]
    assert result["status"] == "switched" and result["mixed"] is False
    assert box.record().source == er.SOURCE_SWITCH
    _assert_switched(box)


async def test_the_gate_names_the_run_and_its_archive_directory(box: Box) -> None:
    journal = await box.kill_at("paused")
    gate = write_gate.read_gate()
    assert gate is not None
    assert gate["run_id"] == journal.run_id and gate["archive_dir"] == journal.archive_dir
    assert Path(gate["archive_dir"]).is_dir()


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
    # The journal's mirror stays beside the archives, with the last word.
    done = json.loads((run_dir / "journal.json").read_text())
    assert done["phase"] == es.PHASE_COMPLETE
    assert [b["step"] for b in done["banks"]] == [es.STEP_RENAMED] * 3


async def test_a_mixed_store_is_repaired_by_a_switch_to_its_own_embedder(box: Box) -> None:
    er.save_embedder_record(EmbedderRecord(*BGE, source=er.SOURCE_PROCESS, mixed=True))

    await box.switch(BGE).run()

    assert (box.record().embedder, box.record().mixed) == (BGE, False)
    assert f"engine:{BGE.model}" in box.events


async def test_a_bank_created_just_before_the_pause_is_part_of_the_run(box: Box) -> None:
    """The bank list is taken under the pause, not from the preflight."""
    switch = box.switch()
    real = switch.preflight

    async def preflight(**kw: Any) -> dict[str, Any]:
        plan = await real(**kw)
        box.engine.create("late", _counts(3))
        return plan

    switch.preflight = preflight  # type: ignore[method-assign]

    await switch.run()

    assert "export:late" in box.events and "delete:late" in box.events
    _assert_switched(box, {**START, "late": _counts(3)})


async def test_an_archive_that_does_not_match_its_bank_stops_the_run(box: Box) -> None:
    # Every archive of alpha is one fact short of the bank it was taken from.
    box.engine.archive_delta["alpha"] = {"facts": -1}

    with pytest.raises(es.SwitchError) as err:
        await box.switch().run()

    assert err.value.bank == "alpha" and "could not be exported" in err.value.message
    assert "a fact that belongs to no document" in err.value.message
    assert box.events.count("export:alpha") == 3, "two retries, then stop"
    assert f"engine:{HS.model}" not in box.events, "the embedder was never flipped"
    _assert_rolled_back(box)


# ── failures before the swap are undone ──────────────────────────────────────


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
        f"engine:{BGE.model}",  # isolated, still on the original embedder
        f"engine:{HS.model}",  # the flip
        f"engine:{BGE.model}",  # back, at its normal address
    ]
    _assert_rolled_back(box)
    assert box.archives(), "the archives survive a failed run"
    rolled = json.loads(next(es.archive_root().glob("*/journal.json")).read_text())
    assert rolled["phase"] == es.PHASE_ROLLED_BACK


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


async def test_rollback_waits_for_a_cancelled_restore_to_really_end(box: Box) -> None:
    """A restore that was cancelled but is still running could create its bank later."""
    box.engine.stuck_restore.add("alpha")
    box.engine.cancel_lag = 3

    with pytest.raises(es.SwitchError) as err:
        await box.switch().run()

    assert "did not finish within" in err.value.message
    cancel = box.events.index("cancel:alpha")
    polls = [i for i, e in enumerate(box.events) if e == "poll-cancelled:alpha"]
    assert len(polls) == 4 and polls[0] > cancel, "polled until the engine said cancelled"
    back = max(i for i, e in enumerate(box.events) if e == f"engine:{BGE.model}")
    assert back > polls[-1], "and only then moved on"
    _assert_rolled_back(box)


async def test_a_restore_that_will_not_stop_keeps_the_pause(box: Box) -> None:
    box.engine.stuck_restore.add("alpha")
    box.engine.cancel_lag = 10**6

    with pytest.raises(es.SwitchError) as err:
        await box.switch().run()

    assert err.value.resumable and "could still appear" in err.value.message
    assert write_gate.read_gate() is not None
    assert es.load_journal().phase == es.PHASE_ROLLBACK_FAILED  # type: ignore[union-attr]


async def test_a_rollback_that_cannot_finish_keeps_writes_paused_until_resume(box: Box) -> None:
    box.engine.restore_delta["alpha"] = {"facts": -1}
    box.host.dropin_error[BGE.model] = "systemctl restart hindsight-api.service failed"

    with pytest.raises(es.SwitchError) as err:
        await box.switch().run()

    assert err.value.resumable and "could not be undone" in err.value.message
    assert write_gate.read_gate() is not None
    journal = es.load_journal()
    assert journal is not None and journal.phase == es.PHASE_ROLLBACK_FAILED
    refused = _codes(await box.switch().preflight())
    assert "interrupted_switch" in refused and "gate_set" in refused

    box.host.dropin_error.clear()
    result = await box.switch(None).resume()

    assert result["status"] == "rolled_back"
    _assert_rolled_back(box)


# ── writers that bypass hal0 ─────────────────────────────────────────────────


async def test_a_bank_created_during_the_exports_refuses_before_the_swap(box: Box) -> None:
    """A direct writer creates a bank after the list was taken: it is not in any archive."""

    def late(bank: str) -> None:
        if bank == "gamma":
            box.engine.create("stray", _counts(5))

    box.engine.on_export = late

    with pytest.raises(es.SwitchError) as err:
        await box.switch().run()

    assert "stray" in err.value.message and "appeared after the switch listed" in err.value.message
    assert err.value.details["stray_banks"] == ["stray"] and not err.value.resumable
    _assert_rolled_back(box, {**START, "stray": _counts(5)})


async def test_a_bank_created_after_the_swap_began_leaves_the_store_mixed(box: Box) -> None:
    def hook(point: str) -> None:
        if point == "deleted:alpha":
            box.engine.create("stray", _counts(5))

    result = await box.switch(hook=hook).run()

    # The journalled banks are finished; the record does not claim one embedder.
    assert result["status"] == "switched" and result["mixed"] is True
    assert result["stray_banks"] == ["stray"]
    assert any("stray" in w and "switch" in w and "again" in w for w in result["warnings"])
    record = box.record()
    assert (record.embedder, record.mixed) == (HS, True)
    assert box.engine.banks == {**START, "stray": _counts(5)}
    assert write_gate.read_gate() is None


async def test_a_same_size_edit_before_the_swap_is_caught_by_the_fingerprint(box: Box) -> None:
    """A directive rewritten in place: every count is unchanged."""

    def hook(point: str) -> None:
        if point == "restored:gamma":
            box.engine.m("Hal0::Beta")["dir"] = "rewritten"

    with pytest.raises(es.SwitchError) as err:
        await box.switch(hook=hook).run()

    assert err.value.bank == "Hal0::Beta"
    assert "changed since its export: directives" in err.value.message
    assert "reaches the engine's private port" in err.value.message
    # Beta took a write while the engine ran the target: recorded as mixed.
    assert box.record().embedder == BGE and box.record().mixed is True
    assert box.engine.banks == START and write_gate.read_gate() is None


@pytest.mark.parametrize(
    ("change", "signal"),
    [
        (lambda e: e.m("gamma").__setitem__("mm", "renamed"), "mental_models"),
        (lambda e: e.m("gamma").__setitem__("config", {"retain_mission": "x"}), "config"),
        (lambda e: e.banks["gamma"].__setitem__("documents", 3), "document"),
    ],
)
async def test_each_fingerprint_signal_is_noticed_on_its_own(
    box: Box, change: Any, signal: str
) -> None:
    def hook(point: str) -> None:
        if point == "flipped":
            change(box.engine)

    with pytest.raises(es.SwitchError) as err:
        await box.switch(hook=hook).run()

    assert err.value.bank == "gamma" and signal in err.value.message


async def test_what_the_engine_moves_by_itself_is_not_a_change(box: Box) -> None:
    """Consolidation and refreshes are the engine's own work: write timestamps,
    new operations, observation counts and a model's generated content move
    without any client. A run must not fail on them, or it could never finish."""

    def hook(point: str) -> None:
        if point == "flipped":
            for bank in ORIGINALS:
                meta = box.engine.m(bank)
                meta["rev"] += 5
                meta["profile"] += 1
                meta["ops"].insert(0, "consolidation-op")
                meta["mm_content"], meta["mm_refreshed"] = "regenerated", "later"
                box.engine.banks[bank]["observations"] += 3

    result = await box.switch(hook=hook).run()

    assert result["status"] == "switched" and result["warnings"] == []
    assert box.engine.banks["alpha"]["observations"] == 40, "rebuilt from the archive"


async def test_the_runs_own_export_and_restore_do_not_look_like_a_change(box: Box) -> None:
    """Both are operations on the original; the fingerprint leaves them out."""
    await box.switch().run()
    _assert_switched(box)


async def test_a_direct_write_to_the_next_bank_while_one_swaps_stops_before_its_delete(
    box: Box,
) -> None:
    """Bank N-1 is being swapped (minutes on a real box) when bank N takes a write."""

    def hook(point: str) -> None:
        if point == "deleted:alpha":
            box.engine.direct_write("Hal0::Beta")

    with pytest.raises(es.SwitchError) as err:
        await box.switch(hook=hook).run()

    assert err.value.resumable and err.value.bank == "Hal0::Beta"
    assert "was not deleted" in err.value.message and "run --resume" in err.value.message
    assert "delete:Hal0::Beta" not in box.events and "delete:gamma" not in box.events
    # alpha is rebuilt; Beta and gamma are on their originals, with the write intact.
    assert box.engine.banks["alpha"] == START["alpha"]
    assert box.engine.banks["Hal0::Beta"]["facts"] == 61
    assert write_gate.read_gate() is not None, "the gate stays set"
    journal = es.load_journal()
    assert journal is not None and journal.phase == es.PHASE_SWAPPING
    assert [b.step for b in journal.banks] == [
        es.STEP_RENAMED,
        es.STEP_RESTORED,
        es.STEP_RESTORED,
    ]

    # The operator stops the writer and resumes: Beta is exported and rebuilt
    # again from what it holds now, then the swap carries on.
    before = len(box.events)
    result = await box.switch(None).resume()

    beta = journal.banks[1].temp
    assert box.events[before:][:5] == [
        f"delete:{beta}",
        "export:Hal0::Beta",
        f"restore:Hal0::Beta->{beta}",
        "delete:Hal0::Beta",
        f"rename:{beta}->Hal0::Beta",
    ]
    assert result["status"] == "switched"
    beta_now = {**START["Hal0::Beta"], "facts": 61, "documents": 5}
    _assert_switched(box, {**START, "Hal0::Beta": beta_now})


async def test_a_bank_that_keeps_moving_stops_the_resume_too(box: Box) -> None:
    def hook(point: str) -> None:
        if point == "deleted:alpha":
            box.engine.direct_write("Hal0::Beta")

    with pytest.raises(es.SwitchError):
        await box.switch(hook=hook).run()
    # The writer was not stopped: it writes again while Beta is rebuilt.
    box.engine.on_restore = lambda source: box.engine.direct_write("Hal0::Beta")

    with pytest.raises(es.SwitchError) as err:
        await box.switch(None).resume()

    assert err.value.resumable and "delete:Hal0::Beta" not in box.events
    assert write_gate.read_gate() is not None


# ── the swap is resumable ────────────────────────────────────────────────────


async def test_the_journal_names_each_destructive_step_before_it_happens(box: Box) -> None:
    """Saved and synced before the engine call, with the step about to happen."""
    seen: dict[str, str] = {}

    def hook(point: str) -> None:
        journal = es.load_journal()
        if journal is not None and journal.banks:
            seen[point] = journal.banks[0].step

    real_delete = es.EmbedderSwitch._delete_bank

    async def delete(self: es.EmbedderSwitch, bank: str) -> None:
        if bank == "alpha":
            seen["at_delete"] = es.load_journal().banks[0].step  # type: ignore[union-attr]
            mirror = Path(es.load_journal().archive_dir) / "journal.json"  # type: ignore[union-attr]
            seen["mirror_at_delete"] = json.loads(mirror.read_text())["banks"][0]["step"]
        await real_delete(self, bank)

    switch = box.switch(hook=hook)
    switch._delete_bank = delete.__get__(switch)  # type: ignore[method-assign]
    real_rename = box.host.rename

    def rename(old: str, new: str) -> str:
        if new == "alpha":
            seen["at_rename"] = es.load_journal().banks[0].step  # type: ignore[union-attr]
        return real_rename(old, new)

    switch._rename_bank = rename

    await switch.run()

    assert seen["at_delete"] == es.STEP_DELETING and seen["mirror_at_delete"] == es.STEP_DELETING
    assert seen["deleted:alpha"] == es.STEP_DELETED
    assert seen["at_rename"] == es.STEP_RENAMING


SWAP_POINTS = [
    "before_swap",
    "before_delete:Hal0::Beta",
    "deleted:Hal0::Beta",
    "before_rename:Hal0::Beta",
    "renamed:Hal0::Beta",
    "before_finish",
]
EARLY_POINTS = [
    "paused",
    "isolated",
    "exported:Hal0::Beta",
    "before_flip",
    "flipped",
    "restored:Hal0::Beta",
]


def test_every_hook_point_is_exercised() -> None:
    covered = {p.split(":")[0] for p in SWAP_POINTS + EARLY_POINTS}
    assert covered == {p.split(":")[0] for p in es.HOOK_POINTS}


@pytest.mark.parametrize("point", EARLY_POINTS)
async def test_a_kill_before_the_swap_is_undone_by_resume(box: Box, point: str) -> None:
    journal = await box.kill_at(point)
    assert write_gate.read_gate() is not None, "the gate survives the kill"
    assert all(b in box.engine.banks for b in ORIGINALS)

    result = await box.switch(None).resume()

    assert result["status"] == "rolled_back" and result["run_id"] == journal.run_id
    _assert_rolled_back(box)


@pytest.mark.parametrize("point", SWAP_POINTS)
async def test_a_kill_inside_the_swap_is_finished_by_resume(box: Box, point: str) -> None:
    journal = await box.kill_at(point)
    assert journal.phase == es.PHASE_SWAPPING
    assert write_gate.read_gate() is not None, "the gate survives the kill"
    for entry in journal.banks:
        assert entry.bank in box.engine.banks or entry.temp in box.engine.banks

    result = await box.switch(None).resume()

    assert result["status"] == "switched"
    _assert_switched(box)
    assert box.events[-1] == "gate:off"


async def test_a_failure_in_the_middle_of_the_swap_is_finished_by_resume(box: Box) -> None:
    box.host.rename_fails.add("Hal0::Beta")

    with pytest.raises(es.SwitchError) as err:
        await box.switch().run()

    assert err.value.resumable and err.value.bank == "Hal0::Beta"
    journal = es.load_journal()
    assert journal is not None and journal.phase == es.PHASE_SWAPPING
    assert {b.bank: b.step for b in journal.banks} == {
        "alpha": es.STEP_RENAMED,
        "Hal0::Beta": es.STEP_RENAMING,
        "gamma": es.STEP_RESTORED,
    }
    assert write_gate.read_gate() is not None and "gate:off" not in box.events
    assert box.host.live == HS
    assert box.engine.port == PRIVATE_PORT, "the engine stays private while the run is resumable"
    assert "delete:gamma" not in box.events, "the swap stops at the first failure"

    box.host.rename_fails.clear()
    before = len(box.events)
    result = await box.switch(None).resume()

    beta, gamma = journal.banks[1], journal.banks[2]
    assert box.events[before:] == [
        f"rename:{beta.temp}->Hal0::Beta",
        "delete:gamma",
        f"rename:{gamma.temp}->gamma",
        "release",
        f"engine:{HS.model}",
        "gate:off",
    ]
    assert box.engine.port == NORMAL_PORT
    assert result["status"] == "switched"
    _assert_switched(box)


async def test_resume_moves_the_engine_back_to_the_target_before_it_restores(box: Box) -> None:
    """Something re-rendered the drop-in meanwhile: the engine runs the old model."""
    journal = await box.kill_at("deleted:alpha")
    box.host.live = BGE
    del box.engine.banks[journal.banks[0].temp]  # forces a restore during the resume
    before = len(box.events)

    await box.switch(None).resume()

    after = box.events[before:]
    assert after[:2] == ["isolate", f"engine:{HS.model}"], "read from the live environment"
    assert after.index(f"engine:{HS.model}") < next(
        i for i, e in enumerate(after) if e.startswith("restore:")
    )
    _assert_switched(box)


async def test_resume_stops_when_the_engine_cannot_be_put_on_the_target(box: Box) -> None:
    await box.kill_at("deleted:alpha")
    box.host.live = BGE
    box.host.override = BGE  # a drop-in that wins over hal0's
    before = len(box.events)

    with pytest.raises(es.SwitchError) as err:
        await box.switch(None).resume()

    assert err.value.resumable and "overrides the embedder" in err.value.message
    assert not any(e.startswith(("restore:", "delete:", "rename:")) for e in box.events[before:])
    assert write_gate.read_gate() is not None


async def test_the_engine_is_checked_again_before_every_restore(box: Box) -> None:
    def drift(source: str) -> None:
        if source == "alpha":
            box.host.live = BGE  # moved under the run after the first restore

    box.engine.on_restore = drift

    await box.switch().run()

    restarts = [i for i, e in enumerate(box.events) if e == f"engine:{HS.model}"]
    second = next(i for i, e in enumerate(box.events) if e.startswith("restore:Hal0::Beta"))
    # The flip, the correction before the second restore, and the return to
    # the normal address at the end.
    assert len(restarts) == 3 and restarts[1] < second
    _assert_switched(box)


# ── resume never trusts a journal step by itself ─────────────────────────────


async def test_a_bank_under_neither_id_is_restored_through_a_scratch_bank(box: Box) -> None:
    journal = await box.kill_at("deleted:alpha")
    alpha = journal.banks[0]
    del box.engine.banks[alpha.temp]  # the rebuilt bank is lost too
    before = len(box.events)

    await box.switch(None).resume()

    scratch = es.scratch_bank_id(journal.run_id)
    after = box.events[before:]
    # Never restored straight into its own id: the engine records the restore
    # against the URL bank, which must exist, while the target must not.
    assert after[:3] == [
        f"create:{scratch}",
        f"restore:alpha->{alpha.temp} via {scratch}",
        f"rename:{alpha.temp}->alpha",
    ]
    assert f"delete:{scratch}" in after, "the scratch bank does not outlive the run"
    _assert_switched(box)


async def test_a_bank_journalled_as_renamed_but_still_under_its_temporary_id_is_renamed_again(
    box: Box,
) -> None:
    """An engine rollback to an older snapshot undid a rename the journal recorded."""
    journal = await box.kill_at("before_finish")
    alpha = journal.banks[0]
    assert alpha.step == es.STEP_RENAMED
    box.engine.move("alpha", alpha.temp)
    before = len(box.events)

    await box.switch(None).resume()

    assert box.events[before] == f"rename:{alpha.temp}->alpha"
    _assert_switched(box)


async def test_a_bank_that_is_already_final_is_not_verified_again_on_resume(box: Box) -> None:
    """Renamed, under its own id, no temporary id left: checked for existence only.

    It was verified when it was renamed. Checking it again on every resume
    would make a resume fail the same way for ever once anything about the
    bank moved, with the gate set and no way forward."""
    await box.kill_at("before_finish")
    box.engine.banks["alpha"]["facts"] -= 7  # whatever happened, it is not this run's to judge

    result = await box.switch(None).resume()

    assert result["status"] == "switched"
    assert write_gate.read_gate() is None and es.interrupted_switch() is None
    _assert_public(box)


async def test_an_original_id_found_alone_after_a_delete_is_checked_not_assumed(box: Box) -> None:
    """Step ``deleted``, rebuilt bank gone, original id present: renamed, or an empty impostor?"""
    journal = await box.kill_at("deleted:alpha")
    alpha = journal.banks[0]
    del box.engine.banks[alpha.temp]
    box.engine.create("alpha")  # an empty bank a direct writer created on demand
    before = len(box.events)

    with pytest.raises(es.SwitchError) as err:
        await box.switch(None).resume()

    assert err.value.resumable and "does not match its archive" in err.value.message
    assert "facts 0 (expected 120)" in err.value.message
    assert box.events[before:] == [], "nothing finished, nothing lifted, nothing moved aside"
    assert write_gate.read_gate() is not None and es.journal_path().exists()


async def test_a_rename_that_finished_before_the_journal_said_so_is_accepted(box: Box) -> None:
    journal = await box.kill_at("before_rename:alpha")
    box.engine.move(journal.banks[0].temp, "alpha")
    before = len(box.events)

    await box.switch(None).resume()

    assert not any(e.endswith("->alpha") for e in box.events[before:])
    _assert_switched(box)


async def test_resume_will_not_delete_a_bank_a_bypassing_writer_recreated(box: Box) -> None:
    journal = await box.kill_at("deleted:alpha")
    box.engine.create("alpha", _counts(1))  # recreated on demand by a direct write
    before = len(box.events)

    with pytest.raises(es.SwitchError) as err:
        await box.switch(None).resume()

    assert err.value.resumable and "exists again" in err.value.message
    # The way out is in the message: what to run against the private port.
    assert f"curl -X DELETE http://127.0.0.1:{PRIVATE_PORT}/v1/default/banks/alpha" in (
        err.value.message
    )
    assert "--resume" in err.value.message
    assert not any(e.startswith(("delete:", "rename:")) for e in box.events[before:])
    assert journal.banks[0].temp in box.engine.banks
    assert write_gate.read_gate() is not None


async def test_resume_puts_back_a_pause_that_was_removed_by_hand(box: Box) -> None:
    await box.kill_at("deleted:Hal0::Beta")
    write_gate.gate_path().unlink()
    before = len(box.events)

    await box.switch(None).resume()

    assert box.events[before] == "gate:on", "paused again before the first delete or rename"
    assert box.events[-1] == "gate:off" and write_gate.read_gate() is None


async def test_resume_with_nothing_to_resume_is_refused(box: Box) -> None:
    with pytest.raises(es.SwitchRefused):
        await box.switch(None).resume()
    assert box.events == []


async def test_resume_refuses_a_different_target_than_the_interrupted_run(box: Box) -> None:
    await box.kill_at("deleted:alpha")
    with pytest.raises(es.SwitchRefused) as err:
        await box.switch(BGE).resume()
    assert "hs-embed-1" in err.value.message


# ── a lost or corrupt journal ────────────────────────────────────────────────


async def test_a_corrupt_journal_is_read_from_its_mirror(box: Box) -> None:
    journal = await box.kill_at("deleted:Hal0::Beta")
    es.journal_path().write_text("{half a jour")

    left = es.interrupted_switch()
    assert left is not None and left["phase"] == es.PHASE_SWAPPING
    assert left["source"] == str(Path(journal.archive_dir) / "journal.json")

    await box.switch(None).resume()

    _assert_switched(box)


async def test_a_deleted_journal_is_found_again_through_the_gate(box: Box) -> None:
    journal = await box.kill_at("renamed:alpha")
    es.journal_path().unlink()

    found = es.locate_journal()
    assert found["journal"] is not None and found["journal"].run_id == journal.run_id
    assert found["journal"].banks[0].step == es.STEP_RENAMING
    # A new run does not start beside it.
    refused = _codes(await box.switch().preflight())
    assert {"interrupted_switch", "gate_set", "leftover_banks"} <= set(refused)

    await box.switch(None).resume()

    _assert_switched(box)


async def test_a_deleted_journal_and_gate_are_still_found_by_the_mirror(box: Box) -> None:
    await box.kill_at("deleted:alpha")
    es.journal_path().unlink()
    write_gate.gate_path().unlink()

    assert es.interrupted_switch() is not None
    assert "interrupted_switch" in _codes(await box.switch().preflight())

    await box.switch(None).resume()

    _assert_switched(box)


async def _lose_every_copy(box: Box, point: str) -> es.Journal:
    journal = await box.kill_at(point)
    (Path(journal.archive_dir) / "journal.json").unlink()
    return journal


async def test_with_no_journal_anywhere_resume_says_which_ids_hold_the_data(box: Box) -> None:
    """Entries at ``deleted`` and ``renamed`` when every copy of the journal is gone."""
    journal = await _lose_every_copy(box, "deleted:Hal0::Beta")
    es.journal_path().unlink()
    beta, gamma = journal.banks[1].temp, journal.banks[2].temp
    before = len(box.events)

    with pytest.raises(es.SwitchRefused) as err:
        await box.switch(None).resume()

    message = err.value.message
    assert "but no journal of a switch was found" in message
    assert "Banks on the engine: alpha, gamma." in message
    assert f"temporary ids from a switch: {beta}, {gamma}." in message
    assert "--status" in message and "--clear-gate" in message
    assert box.events[before:] == [] and write_gate.read_gate() is not None


async def test_with_a_corrupt_journal_and_no_mirror_resume_names_the_ids_and_changes_nothing(
    box: Box,
) -> None:
    journal = await _lose_every_copy(box, "deleted:Hal0::Beta")
    es.journal_path().write_text("{half a jour")
    before = len(box.events)

    with pytest.raises(es.SwitchError) as err:
        await box.switch(None).resume()

    assert "not usable" in err.value.message and "Nothing was changed" in err.value.message
    assert journal.banks[1].temp in err.value.message and "alpha" in err.value.message
    assert box.events[before:] == []


async def test_no_new_run_starts_over_the_leftovers_of_a_lost_one(box: Box) -> None:
    journal = await _lose_every_copy(box, "deleted:Hal0::Beta")
    es.journal_path().unlink()

    with pytest.raises(es.SwitchRefused) as err:
        await box.switch().run()

    codes = _codes(err.value.plan)
    assert "gate_set" in codes and "leftover_banks" in codes
    leftover = next(r for r in err.value.plan["refusals"] if r["code"] == "leftover_banks")
    beta = journal.banks[1].temp
    # The banks, their counts and where the archives are.
    assert f"{beta} (60 facts, 4 documents)" in leftover["message"]
    assert journal.archive_dir in leftover["message"]
    assert not any(e.startswith("export:") for e in box.events[-3:])

    # Even with the gate gone too, a temporary id on the engine stops a new run:
    # it would otherwise be exported and rebuilt as if it were an ordinary bank.
    write_gate.gate_path().unlink()
    assert set(_codes(await box.switch().preflight())) == {"leftover_banks", "engine_private"}


# ── --status and --clear-gate ────────────────────────────────────────────────


async def test_status_with_nothing_in_progress(box: Box) -> None:
    status = await box.switch(None).status()
    assert status["journal"] is None and status["gate"] is None and not status["lock_held"]
    assert status["summary"] == "no switch is in progress"
    assert [b["bank"] for b in status["banks"]] == list(ORIGINALS)
    assert box.engine.writes == []


async def test_status_shows_the_journal_the_gate_the_ids_and_what_resume_would_do(box: Box) -> None:
    journal = await box.kill_at("deleted:Hal0::Beta")
    before = (list(box.events), len(box.engine.writes))

    status = await box.switch(None).status()

    assert status["journal"]["run_id"] == journal.run_id
    assert status["journal_source"] == str(es.journal_path())
    assert status["gate"]["run_id"] == journal.run_id and status["lock_held"] is False
    assert status["engine"] == {
        "reachable": True,
        "live": HS._asdict(),
        "private_port": PRIVATE_PORT,
    }
    assert f"isolated on 127.0.0.1:{PRIVATE_PORT}" in status["summary"]
    roles = {b["bank"]: b["role"] for b in status["banks"]}
    assert roles[journal.banks[1].temp] == "temporary (rebuilt copy of Hal0::Beta)"
    assert roles["alpha"] == "original or rebuilt"
    plan = {r["bank"]: r["action"] for r in status["resume"]}
    assert plan == {
        "alpha": es.ACTION_FINAL,
        "Hal0::Beta": es.ACTION_RENAME,
        "gamma": es.ACTION_SWAP,
    }
    assert all(r["problem"] is None for r in status["resume"])
    assert "finishes it bank by bank" in status["summary"]
    assert (list(box.events), len(box.engine.writes)) == before, "status changes nothing"


async def test_status_of_a_run_that_stopped_before_the_swap(box: Box) -> None:
    await box.kill_at("flipped")
    status = await box.switch(None).status()
    assert "--resume undoes it" in status["summary"] and status["resume"] == []


async def test_status_without_a_journal_but_with_the_gate_set(box: Box) -> None:
    journal = await _lose_every_copy(box, "deleted:alpha")
    es.journal_path().unlink()

    status = await box.switch(None).status()

    assert status["journal"] is None and status["gate"]["archive_dir"] == journal.archive_dir
    assert "nothing to resume" in status["summary"] and "--clear-gate" in status["summary"]
    roles = {b["bank"]: b["role"] for b in status["banks"]}
    assert roles[journal.banks[0].temp] == "temporary (not from the journalled run)"


async def test_status_still_answers_when_the_engine_is_down(box: Box) -> None:
    await box.kill_at("deleted:alpha")
    box.engine.reachable = False
    status = await box.switch(None).status()
    assert status["engine"]["reachable"] is False and status["journal"] is not None


async def test_clear_gate_refuses_while_a_swap_is_unfinished(box: Box) -> None:
    await box.kill_at("deleted:alpha")

    with pytest.raises(es.SwitchRefused) as err:
        await box.switch(None).clear_gate()

    assert "is unfinished" in err.value.message and "--resume" in err.value.message
    assert write_gate.read_gate() is not None


async def test_clear_gate_refuses_while_the_journal_cannot_be_read(box: Box) -> None:
    await _lose_every_copy(box, "deleted:alpha")
    es.journal_path().write_text("{half a jour")

    with pytest.raises(es.SwitchRefused) as err:
        await box.switch(None).clear_gate()

    assert "cannot be ruled out" in err.value.message
    assert write_gate.read_gate() is not None


async def test_clear_gate_lifts_a_pause_that_no_journal_explains(box: Box) -> None:
    journal = await _lose_every_copy(box, "paused")
    es.journal_path().unlink()

    out = await box.switch(None).clear_gate()

    assert out["status"] == "cleared" and out["gate"]["run_id"] == journal.run_id
    assert write_gate.read_gate() is None
    with pytest.raises(es.SwitchRefused):
        await box.switch(None).clear_gate()


# ── updates and engine upgrades ──────────────────────────────────────────────


async def test_an_update_is_refused_while_a_switch_is_unfinished(box: Box) -> None:
    from hal0.updater import updater

    updater._raise_if_embedder_switch()  # nothing in progress: no objection
    journal = await box.kill_at("deleted:alpha")

    with pytest.raises(updater.UpdateBlockedByEmbedderSwitch) as err:
        updater._raise_if_embedder_switch()

    assert err.value.code == "system.update_blocked_by_embedder_switch"
    assert err.value.status == 409
    assert journal.run_id in err.value.message and "--resume" in err.value.message
    # prepare, commit and apply all pass through it before anything else.
    source = Path(updater.__file__).read_text()
    assert source.count("        _raise_if_embedder_switch()\n") == 3


async def test_an_update_is_refused_while_a_switch_is_running(box: Box) -> None:
    from hal0.updater import updater

    fd = os.open(es.lock_path(), os.O_RDONLY | os.O_CREAT, 0o644)
    fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
    try:
        with pytest.raises(updater.UpdateBlockedByEmbedderSwitch) as err:
            updater._raise_if_embedder_switch()
        assert "is running on this box" in err.value.message
    finally:
        os.close(fd)


async def test_the_engine_upgrade_stands_down_while_a_switch_is_unfinished(box: Box) -> None:
    from hal0.memory.engine_upgrade import upgrade_memory_engine

    await box.kill_at("deleted:alpha")
    ran: list[Any] = []

    def runner(*a: Any, **k: Any) -> Any:
        ran.append(a)
        raise AssertionError("an upgrade must not start during a switch")

    out = upgrade_memory_engine(hs_dir=box.hs, runner=runner, http_get=lambda _u: None)

    assert out["status"] == "blocked" and "did not finish" in out["reason"]
    assert ran == [] and not (box.hs / ".venv.new").exists()


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
        with pytest.raises(es.SwitchRefused):
            await box.switch(None).clear_gate()
        assert (await box.switch(None).status())["lock_held"] is True
    finally:
        os.close(fd)

    # The lock dies with its holder: nothing stale is left to clear.
    assert (await box.switch().run())["status"] == "switched"


# ── allow-pending ────────────────────────────────────────────────────────────


async def test_allow_pending_goes_ahead_and_says_what_was_left_behind(box: Box) -> None:
    box.engine.queue["gamma"] = {"pending": 0, "processing": 1}

    def hook(point: str) -> None:
        if point == "restored:gamma":
            box.engine.direct_write("gamma", facts=4)  # the running operation finishes late

    result = await box.switch(allow_pending=True, hook=hook).run()

    assert result["status"] == "switched"
    assert box.engine.banks["gamma"]["facts"] == 9, "what the archive held"
    assert any("gamma" in w and "not carried over" in w for w in result["warnings"])


async def test_queued_work_that_never_drains_stops_the_run_before_any_export(box: Box) -> None:
    def hook(point: str) -> None:
        if point == "paused":
            box.engine.queue["alpha"] = {"pending": 1, "processing": 0}

    with pytest.raises(es.SwitchError) as err:
        await box.switch(hook=hook).run()

    assert "did not finish within" in err.value.message and "alpha (1 pending" in err.value.message
    assert box.events == ["gate:on", "gate:off"], "nothing exported, nothing flipped"
    assert box.record().embedder == BGE and not es.journal_path().exists()


# ── pieces ───────────────────────────────────────────────────────────────────


@pytest.mark.parametrize(
    ("step", "original", "rebuilt", "action"),
    [
        (es.STEP_RESTORED, True, True, es.ACTION_SWAP),
        (es.STEP_DELETING, True, True, es.ACTION_SWAP),
        (es.STEP_RESTORED, True, False, es.ACTION_REBUILD),
        (es.STEP_DELETING, False, True, es.ACTION_RENAME),
        (es.STEP_DELETED, False, True, es.ACTION_RENAME),
        (es.STEP_RENAMING, False, True, es.ACTION_RENAME),
        (es.STEP_RENAMING, True, False, es.ACTION_VERIFY),
        (es.STEP_DELETED, True, False, es.ACTION_VERIFY),
        (es.STEP_DELETED, True, True, es.ACTION_RECREATED),
        (es.STEP_RENAMED, True, False, es.ACTION_FINAL),
        # A journal ahead of the engine (the engine went back to an older snapshot).
        (es.STEP_RENAMED, False, True, es.ACTION_RENAME),
        (es.STEP_RENAMED, True, True, es.ACTION_SWAP),
        (es.STEP_RESTORED, False, False, es.ACTION_RECOVER),
        (es.STEP_DELETED, False, False, es.ACTION_RECOVER),
        (es.STEP_RENAMED, False, False, es.ACTION_RECOVER),
    ],
)
def test_the_ids_present_decide_what_the_swap_does(
    step: str, original: bool, rebuilt: bool, action: str
) -> None:
    assert es.swap_action(step, original, rebuilt) == action


def test_the_temporary_ids_fit_the_bank_id_grammar_and_are_recognised() -> None:
    from hal0.api.routes.memory_admin import _BANK_RE

    assert es.temp_bank_id("Hal0::Agents", "3f9a1c2e") == "Hal0::Agents.switch-3f9a1c2e"
    for bank in ("shared", "coding-agent::ultrareview-1.4", "x" * 128):
        temp = es.temp_bank_id(bank, "3f9a1c2e")
        assert _BANK_RE.match(temp) and len(temp) <= 128 and es.is_switch_temp(temp)
    long_a, long_b = es.temp_bank_id("a" * 128, "3f9a1c2e"), es.temp_bank_id("b" * 128, "3f9a1c2e")
    assert long_a != long_b and long_a.startswith("switch-3f9a1c2e-")
    scratch = es.scratch_bank_id("3f9a1c2e")
    assert _BANK_RE.match(scratch) and es.is_switch_temp(scratch)
    assert not any(es.is_switch_temp(b) for b in ("shared", "hal0:shared", "switch-board", "a.b"))


async def test_a_listing_is_read_to_its_reported_total_not_to_a_short_page(box: Box) -> None:
    """A page shorter than the limit is not the end; the engine's total is."""
    box.engine.banks["alpha"]["directives"] = 7
    real_page = box.engine._page
    asked: list[int] = []

    def short_pages(key: str, rows: list[Any], params: dict[str, str]) -> httpx.Response:
        if key != "items" or len(rows) != 7:
            return real_page(key, rows, params)
        offset = int(params.get("offset", 0))
        asked.append(offset)
        return httpx.Response(200, json={key: rows[offset : offset + 3], "total": len(rows)})

    box.engine._page = short_pages  # type: ignore[method-assign]

    state = await box.switch().bank_state("alpha")

    assert state["counts"]["directives"] == 7 and asked == [0, 3, 6]


async def test_a_listing_without_a_total_is_read_until_an_empty_page(box: Box) -> None:
    box.engine.banks["alpha"]["directives"] = 5
    real_page = box.engine._page
    asked: list[int] = []

    def no_total(key: str, rows: list[Any], params: dict[str, str]) -> httpx.Response:
        if key != "items" or len(rows) != 5:
            return real_page(key, rows, params)
        offset = int(params.get("offset", 0))
        asked.append(offset)
        return httpx.Response(200, json={key: rows[offset : offset + 2]})

    box.engine._page = no_total  # type: ignore[method-assign]

    state = await box.switch().bank_state("alpha")

    assert state["counts"]["directives"] == 5 and asked == [0, 2, 4, 5]


async def test_a_listing_that_disagrees_with_its_own_total_is_an_error(box: Box) -> None:
    real_page = box.engine._page

    def lying(key: str, rows: list[Any], params: dict[str, str]) -> httpx.Response:
        if key != "banks":
            return real_page(key, rows, params)
        offset = int(params.get("offset", 0))
        return httpx.Response(200, json={key: rows[offset:], "total": len(rows) + 2})

    box.engine._page = lying  # type: ignore[method-assign]

    plan = await box.switch().preflight()

    assert _codes(plan) == ["engine_error"]
    assert "reports 5 but listed 3" in plan["refusals"][0]["message"]


async def test_the_bank_list_is_cross_checked_against_the_stats(box: Box) -> None:
    real_row = box.engine._row
    box.engine._row = lambda bank: {**real_row(bank), "fact_count": 1}  # type: ignore[method-assign]
    plan = await box.switch().preflight()
    assert plan["refusals"] == []
    assert any("the bank list says 1 memories, its stats say 160" in w for w in plan["warnings"])


def _admin_env(monkeypatch: pytest.MonkeyPatch, tmp_path: Path, unit: dict[str, str]) -> Path:
    from hal0.memory import extraction_env

    monkeypatch.setattr(extraction_env, "hindsight_unit_env", lambda: unit)
    env_file = tmp_path / "hindsight-llm.env"
    monkeypatch.setattr(extraction_env, "HINDSIGHT_LLM_ENV_PATH", env_file)
    return env_file


def test_the_rename_runs_the_engine_admin_command_in_the_engine_environment(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    env_file = _admin_env(
        monkeypatch, tmp_path, {"HOME": "/engine/home", "HINDSIGHT_API_DATABASE_SCHEMA": "public"}
    )
    env_file.write_text("HINDSIGHT_API_LLM_API_KEY=sk-secret\n")
    seen: dict[str, Any] = {}

    def runner(argv: list[str], **kw: Any) -> subprocess.CompletedProcess[str]:
        seen.update(argv=argv, **kw)
        return subprocess.CompletedProcess(argv, 0, "Renamed: 12 row(s) moved.\n", "")

    out = es.run_admin_rename(
        tmp_path,
        "Hal0::Agents.switch-ab12cd34",
        "Hal0::Agents",
        timeout_s=9,
        runner=runner,
        euid=lambda: 1000,
    )

    assert out == "Renamed: 12 row(s) moved."
    assert seen["argv"] == [
        str(tmp_path / ".venv" / "bin" / "hindsight-admin"),
        "rename-bank",
        "--from",
        "Hal0::Agents.switch-ab12cd34",
        "--to",
        "Hal0::Agents",
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
    _admin_env(monkeypatch, tmp_path, {})
    seen: dict[str, Any] = {}

    def runner(argv: list[str], **kw: Any) -> subprocess.CompletedProcess[str]:
        seen.update(kw)
        return subprocess.CompletedProcess(argv, 0, "", "")

    es.run_admin_rename(tmp_path, "a", "b", timeout_s=1, runner=runner, euid=lambda: 0)

    owner = tmp_path.stat()
    assert (seen["user"], seen["group"]) == (owner.st_uid, owner.st_gid)
    assert seen["env"]["HOME"] == str(tmp_path), "pg0 lives under the engine home"


def test_a_failed_rename_never_passes_raw_tool_output_on(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _admin_env(monkeypatch, tmp_path, {})
    noise = "x" * 3000

    def runner(argv: list[str], **kw: Any) -> subprocess.CompletedProcess[str]:
        return subprocess.CompletedProcess(
            argv,
            1,
            noise,
            "could not connect to postgresql://hindsight:s3cr3t-pw@db.internal:5432/hindsight\n"
            "Error: bank 'a' has 2 pending or processing operation(s)\n",
        )

    with pytest.raises(es.SwitchError) as err:
        es.run_admin_rename(tmp_path, "a", "b", timeout_s=1, runner=runner, euid=lambda: 1000)

    message = err.value.message
    assert "exited 1" in message and "2 pending or processing" in message
    assert "s3cr3t-pw" not in message, "URL credentials are masked"
    assert len(message) < 600, "and the output is capped"


def test_the_rename_fails_early_when_the_environment_file_cannot_be_read(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    env_file = _admin_env(monkeypatch, tmp_path, {})
    env_file.write_text("HINDSIGHT_API_DATABASE_URL=postgresql://x\n")
    monkeypatch.setattr(es.os, "access", lambda p, mode: str(p) != str(env_file))
    ran: list[Any] = []

    with pytest.raises(es.SwitchError) as err:
        es.run_admin_rename(
            tmp_path, "a", "b", timeout_s=1, runner=lambda *a, **k: ran.append(a), euid=lambda: 1000
        )

    assert str(env_file) in err.value.message and "as root" in err.value.message
    assert ran == [], "the tool is not run with half an environment"


def test_an_unreadable_journal_is_reported_not_ignored(box: Box) -> None:
    es.journal_path().write_text("{broken")
    left = es.interrupted_switch()
    assert left is not None and left["phase"] == "unreadable"
    with pytest.raises(es.SwitchError):
        es.load_journal()


# ── isolation ────────────────────────────────────────────────────────────────


async def test_nothing_but_the_switch_reaches_the_engine_from_isolation_to_the_end(
    box: Box,
) -> None:
    where: dict[str, int] = {}

    def hook(point: str) -> None:
        where.setdefault(point.split(":")[0], box.engine.port)

    await box.switch(hook=hook).run()

    assert where.pop("paused") == NORMAL_PORT, "the queues drain before the engine is restarted"
    assert set(where) == {p.split(":")[0] for p in es.HOOK_POINTS} - {"paused"}
    assert set(where.values()) == {PRIVATE_PORT}, "private from the bank list to the last rename"
    _assert_public(box)


async def test_a_client_on_the_normal_port_cannot_connect_while_the_run_is_resumable(
    box: Box,
) -> None:
    outsider = box.client(f"http://127.0.0.1:{NORMAL_PORT}")
    await box.kill_at("deleted:alpha")

    with pytest.raises(httpx.ConnectError):
        await outsider.retain(bank_id="gamma", content="a direct write", document_id="d")
    assert box.host.dropin_port == PRIVATE_PORT, "the drop-in survives the kill, and a reboot"

    await box.switch(None).resume()

    assert (await outsider.request_json("GET", "/health"))["status"] == "healthy"


async def test_an_engine_that_cannot_be_isolated_stops_the_run_before_any_export(
    box: Box,
) -> None:
    """A drop-in sorting after hal0's replaces ExecStart: the engine stays where it was."""
    box.host.exec_start_pinned = True

    with pytest.raises(es.SwitchError) as err:
        await box.switch().run()

    assert "still answers at its normal address" in err.value.message
    assert es.ISOLATION_DROP_IN_NAME in err.value.message
    assert not any(e.startswith(("export:", "restore:")) for e in box.events)
    assert f"engine:{HS.model}" not in box.events
    _assert_rolled_back(box)


async def test_a_unit_that_cannot_be_read_stops_the_run_before_any_export(box: Box) -> None:
    box.host.isolate_error = "could not find an ExecStart in `systemctl cat hindsight-api.service`"

    with pytest.raises(es.SwitchError) as err:
        await box.switch().run()

    assert "could not isolate the engine" in err.value.message
    assert not any(e.startswith("export:") for e in box.events)
    _assert_rolled_back(box)


async def test_a_new_run_is_refused_while_the_engine_is_still_private(box: Box) -> None:
    box.host.dropin_port = PRIVATE_PORT
    box.engine.port = PRIVATE_PORT

    plan = await box.switch().preflight()

    assert _codes(plan) == ["engine_private"]
    # The banks were still read, from where the engine is.
    assert [b["bank"] for b in plan["banks"]] == list(ORIGINALS)


UNIT = """# /etc/systemd/system/hindsight-api.service
[Service]
User=hal0
ExecStart=/var/lib/hal0/memory/hindsight/.venv/bin/hindsight-api --host 127.0.0.1 --port 9177

# /etc/systemd/system/hindsight-api.service.d/extraction-model.conf
[Service]
Environment=HINDSIGHT_API_LLM_MODEL=hal0/memory
"""
LAN_DROPIN = """
# /etc/systemd/system/hindsight-api.service.d/lan.conf
[Service]
ExecStart=
ExecStart=/var/lib/hal0/memory/hindsight/.venv/bin/hindsight-api \\
    --host 0.0.0.0 --port=9177 --workers 2
"""
OURS = f"""
# /etc/systemd/system/hindsight-api.service.d/{es.ISOLATION_DROP_IN_NAME}
[Service]
ExecStart=
ExecStart=/somewhere/else --host 127.0.0.1 --port 45999
"""


def test_the_isolation_rewrites_the_command_the_unit_runs_now() -> None:
    base = "/var/lib/hal0/memory/hindsight/.venv/bin/hindsight-api"
    assert es.effective_exec_start(UNIT) == f"{base} --host 127.0.0.1 --port 9177"
    # An operator's own ExecStart drop-in (a LAN bind) is what gets rewritten:
    # their command and its other arguments are kept, only the address moves.
    lan = es.effective_exec_start(UNIT + LAN_DROPIN)
    assert lan == f"{base} --host 0.0.0.0 --port=9177 --workers 2"
    assert es.private_exec_start(lan, 45123) == f"{base} --host 127.0.0.1 --port=45123 --workers 2"
    # This command's own drop-in is never taken for the unit's command.
    assert es.effective_exec_start(UNIT + LAN_DROPIN + OURS) == lan
    # An address the command leaves to the environment is appended.
    assert es.private_exec_start(base, 45123) == f"{base} --host 127.0.0.1 --port 45123"
    assert es.effective_exec_start("[Service]\nExecStart=\n") is None


def test_the_isolation_drop_in_is_written_from_the_unit_and_read_back(tmp_path: Path) -> None:
    path = tmp_path / "hindsight-api.service.d" / es.ISOLATION_DROP_IN_NAME
    assert es.isolated_port(path) is None

    def runner(argv: list[str], **kw: Any) -> subprocess.CompletedProcess[str]:
        assert argv == ["systemctl", "cat", "hindsight-api.service"]
        return subprocess.CompletedProcess(argv, 0, UNIT + LAN_DROPIN, "")

    out = es.write_isolation_dropin(45123, "3f9a1c2e", runner=runner, path=path)

    assert out["error"] is None
    body = path.read_text()
    assert "[Service]\nExecStart=\nExecStart=" in body, "cleared, then set: it replaces the list"
    assert "--host 127.0.0.1 --port=45123 --workers 2" in body
    assert "Environment=HINDSIGHT_API_ENABLE_AUTO_CONSOLIDATION=false" in body
    assert "run 3f9a1c2e" in body and "--status" in body
    assert es.isolated_port(path) == 45123
    # It sorts after hal0's other drop-ins and after an operator's plain names.
    names = ["extraction-model.conf", "lan.conf", "showcase-lanes.conf", "zz-local.conf"]
    assert sorted([*names, es.ISOLATION_DROP_IN_NAME])[-1] == es.ISOLATION_DROP_IN_NAME

    assert es.remove_isolation_dropin(path) == {"error": None}
    assert es.isolated_port(path) is None

    def missing(argv: list[str], **kw: Any) -> subprocess.CompletedProcess[str]:
        return subprocess.CompletedProcess(argv, 1, "", "No files found")

    out = es.write_isolation_dropin(45123, "x", runner=missing, path=path)
    assert "could not find an ExecStart" in out["error"] and not path.exists()


def test_the_private_port_is_a_free_loopback_port() -> None:
    port = es.free_loopback_port()
    assert 1024 < port < 65536


# ── the ordering of the ends ─────────────────────────────────────────────────


async def test_the_journal_is_retired_before_the_gate_is_lifted(box: Box) -> None:
    await box.switch().run()
    assert box.journal_at_lift == [False], "finish: no unfinished journal when writes come back"

    box.engine.restore_delta["alpha"] = {"facts": -1}
    with pytest.raises(es.SwitchError):
        await box.switch(BGE).run()
    assert box.journal_at_lift == [False, False], "and the same on a rollback"


async def test_rollback_writes_the_previous_record_only_once_the_engine_runs_it(
    box: Box,
) -> None:
    box.engine.restore_delta["alpha"] = {"facts": -1}
    box.host.release_restart_error = "systemctl restart hindsight-api.service failed"

    with pytest.raises(es.SwitchError) as err:
        await box.switch().run()

    # The engine could not be brought back on the previous embedder, so the
    # record still says what the engine was last confirmed on: the target, mixed.
    assert err.value.resumable
    record = box.record()
    assert (record.embedder, record.mixed) == (HS, True)
    assert write_gate.read_gate() is not None

    box.host.release_restart_error = None
    await box.switch(None).resume()

    _assert_rolled_back(box)


async def test_a_bank_found_at_rollback_after_the_flip_leaves_the_store_mixed(box: Box) -> None:
    """It may hold vectors of the target model in a store going back to the old one."""

    def hook(point: str) -> None:
        if point == "flipped":
            box.engine.create("stray", _counts(5))

    box.engine.restore_delta["gamma"] = {"facts": -1}

    with pytest.raises(es.SwitchError):
        await box.switch(hook=hook).run()

    record = box.record()
    assert (record.embedder, record.mixed) == (BGE, True), "not the previous record unmixed"
    rolled = json.loads(next(es.archive_root().glob("*/journal.json")).read_text())
    assert rolled["phase"] == es.PHASE_ROLLED_BACK and rolled["stray_banks"] == ["stray"]
    _assert_public(box)
    assert write_gate.read_gate() is None


# ── the baseline, and the engine's own activity ──────────────────────────────


async def test_a_bank_that_moves_during_its_export_is_exported_again(box: Box) -> None:
    """Read before the export is submitted and again after the download."""

    def edit_once(bank: str) -> None:
        if bank == "alpha" and box.events.count("export:alpha") == 0:
            box.engine.m("alpha")["dir"] = "rewritten while the export ran"

    box.engine.on_export = edit_once

    await box.switch().run()

    assert box.events.count("export:alpha") == 2
    _assert_switched(box)


async def test_counts_are_taken_once_the_engine_is_quiet(box: Box) -> None:
    """Consolidation after an import moves the observation count for a while."""
    switch = box.switch()
    box.engine.queue["alpha"] = {"consolidating": 3}
    real = box.engine._stats

    def consolidating(bank: str) -> dict[str, Any]:
        queue = box.engine.queue.get(bank, {})
        if queue.get("consolidating"):
            queue["consolidating"] -= 1
            box.engine.banks[bank]["observations"] += 1
        return real(bank)

    box.engine._stats = consolidating  # type: ignore[method-assign]

    state = await switch._quiet_state("alpha")

    assert state["pending_consolidation"] == 0
    assert state["counts"]["observations"] == 43, "read after the queue stopped moving"


async def test_a_rebuilt_bank_may_gain_observations_but_not_lose_any(box: Box) -> None:
    box.engine.restore_delta["alpha"] = {"observations": 2}
    await box.switch().run()
    _assert_switched(box, {**START, "alpha": {**START["alpha"], "observations": 42}})

    box.engine.restore_delta["alpha"] = {"observations": -1}
    with pytest.raises(es.SwitchError) as err:
        await box.switch(BGE).run()
    assert "observations 41 (expected 42)" in err.value.message


async def test_observations_the_export_leaves_out_are_noted_not_refused(box: Box) -> None:
    box.engine.archive_delta["gamma"] = {"observations": 0}
    box.engine.banks["alpha"]["observations"] = 40
    box.engine.archive_delta["alpha"] = {"observations": -4}  # sources gone: not exported

    result = await box.switch().run()

    assert result["status"] == "switched"
    assert any("4 observation(s) whose source facts are gone" in w for w in result["warnings"])
    assert box.engine.banks["alpha"]["observations"] == 36


# ── a resume never fails twice for something it can repair ───────────────────


async def test_a_rebuilt_bank_that_no_longer_matches_is_rebuilt_on_resume(box: Box) -> None:
    journal = await box.kill_at("before_swap")
    alpha = journal.banks[0].temp
    box.engine.banks[alpha]["facts"] -= 5
    before = len(box.events)

    await box.switch(None).resume()

    assert box.events[before:][:3] == [
        f"delete:{alpha}",
        "export:alpha",
        f"restore:alpha->{alpha}",
    ]
    _assert_switched(box)


async def test_a_rebuild_keeps_the_earlier_archive_beside_the_new_one(box: Box) -> None:
    journal = await box.kill_at("before_swap")
    box.engine.banks[journal.banks[1].temp]["facts"] -= 1

    result = await box.switch(None).resume()

    names = sorted(p.name for p in Path(result["archive_dir"]).glob("001-*"))
    assert names == ["001-Hal0__Beta.earlier-1.zip", "001-Hal0__Beta.zip"]


async def test_status_runs_the_checks_a_resume_would_and_reports_a_bank_that_moved(
    box: Box,
) -> None:
    await box.kill_at("deleted:alpha")
    box.engine.direct_write("gamma")
    before = (list(box.events), len(box.engine.writes))

    status = await box.switch(None).status()

    gamma = next(r for r in status["resume"] if r["bank"] == "gamma")
    assert gamma["action"] == es.ACTION_SWAP
    assert "the original changed after its archive was taken" in gamma["problem"]
    assert "facts 10 (expected 9)" in gamma["problem"]
    assert "--resume then exports and rebuilds it again" in gamma["operator"]
    assert (list(box.events), len(box.engine.writes)) == before


async def test_status_says_exactly_what_to_do_when_the_switch_cannot_repair_a_bank(
    box: Box,
) -> None:
    journal = await box.kill_at("deleted:alpha")
    del box.engine.banks[journal.banks[0].temp]
    box.engine.create("alpha")  # an empty bank under the original id

    status = await box.switch(None).status()

    alpha = next(r for r in status["resume"] if r["bank"] == "alpha")
    assert alpha["action"] == es.ACTION_VERIFY and "facts 0 (expected 120)" in alpha["problem"]
    assert (
        f"curl -X DELETE http://127.0.0.1:{PRIVATE_PORT}/v1/default/banks/alpha"
        in alpha["operator"]
    )
    assert "1 bank(s) need the operator first" in status["summary"]

    # And the way out works without touching the journal: delete it, resume.
    box.engine.drop("alpha")
    await box.switch(None).resume()
    _assert_switched(box)


async def test_clear_gate_also_returns_a_stranded_engine_to_its_normal_address(box: Box) -> None:
    journal = await _lose_every_copy(box, "isolated")
    es.journal_path().unlink()
    assert box.engine.port == PRIVATE_PORT
    status = await box.switch(None).status()
    assert status["engine"]["private_port"] == PRIVATE_PORT and "--clear-gate" in status["summary"]
    before = len(box.events)

    out = await box.switch(None).clear_gate()

    assert out["was_private_on"] == PRIVATE_PORT and out["gate"]["run_id"] == journal.run_id
    assert box.events[before:] == ["release", f"engine:{BGE.model}", "gate:off"]
    _assert_public(box)
    assert write_gate.read_gate() is None


# ── what the operator is told afterwards ─────────────────────────────────────


async def test_a_switch_away_from_the_configured_model_says_the_config_is_fine(box: Box) -> None:
    result = await box.switch().run()
    assert result["config_note"] is None, "the store is now on the configured model"

    result = await box.switch(BGE).run()

    note = result["config_note"]
    assert "hal0.toml still names hindsight-ai/hs-embed-1" in note and "That is fine" in note
    assert 'embeddings_model = "BAAI/bge-small-en-v1.5"' in note
