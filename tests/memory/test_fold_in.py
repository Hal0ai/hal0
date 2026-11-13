"""``hal0 memory migrate fold-in`` end to end against a fake engine (ADR-0058 decision 7a).

Modelled on the embedder switch's fake (``test_embedder_switch.py``): the
engine is an ``httpx.MockTransport`` behind the real ``HindsightRestClient``,
it keeps banks in memory, and it answers the routes the fold-in uses the way
hindsight-api 1.2.0 does. Its export writes the real archive layout, and its
merge import follows ``engine/transfer/importer.py``: ``skip`` leaves a
document id the bank has, an observation lands only when every source fact
landed in the same call, a mental model whose id the bank has is dropped
(``ON CONFLICT DO NOTHING``), and no directive is taken. The host (drop-ins,
restarts, the engine's environment) is injected, and every step lands in one
ordered event list. A run is stopped at an exact point through its ``hook``.
"""

from __future__ import annotations

import asyncio
import io
import json
import re
import zipfile
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import httpx
import pytest

from hal0.memory import bank_layout, write_gate
from hal0.memory import embedder_switch as es
from hal0.memory import engine_maintenance as em
from hal0.memory import fold_in as fi
from hal0.memory.embedder_record import Embedder
from hal0.memory.hindsight_client import HindsightRestClient

NORMAL_PORT = 9177
PRIVATE_PORT = 45124
BGE = Embedder("local", "BAAI/bge-small-en-v1.5")
SHIPPED_UNIT = "# /etc/systemd/system/hindsight-api.service\n" + (
    Path(__file__).resolve().parents[2] / "installer/systemd/hindsight-api.service"
).read_text(encoding="utf-8")

FAST = em.Timeouts(
    poll_s=0.0,
    export_s=5.0,
    settle_s=3.0,
    health_s=3.0,
    restore_floor_s=6.0,
    restore_factor=1.0,
    quiet_s=3.0,
    engine_ops_s=6.0,
    delete_s=5.0,
    http_s=5.0,
)


class Killed(BaseException):
    """A ``kill -9``: nothing runs after it."""


def fact(text: str, *tags: str) -> dict[str, Any]:
    return {"text": text, "fact_type": "world", "tags": list(tags), "metadata": {}}


def doc(doc_id: str, *facts: dict[str, Any], tags: tuple[str, ...] = ()) -> dict[str, Any]:
    return {
        "id": doc_id,
        "original_text": doc_id,
        "tags": list(tags),
        "chunks": [],
        "facts": list(facts),
    }


def empty_bank() -> dict[str, Any]:
    return {"docs": {}, "obs": [], "models": {}, "pages": [], "directives": []}


class FakeEngine:
    """hindsight-api 1.2.0, as far as the fold-in can tell."""

    def __init__(self, events: list[str]) -> None:
        self.events = events
        self.banks: dict[str, dict[str, Any]] = {}
        self.port = NORMAL_PORT
        self.reachable = True
        self.ops: dict[str, dict[str, Any]] = {}
        self.files: dict[str, bytes] = {}
        self.restarts = 0
        #: Banks whose next import fails part-way: the first document lands.
        self.fail_import: set[str] = set()
        #: Banks whose import stays ``processing`` until :meth:`finish`.
        self.hold_import: set[str] = set()
        #: Per bank: queued retains (``pending``), counted and listed.
        self.queue: dict[str, int] = {}
        #: Facts the export leaves out (they belong to no document).
        self.orphans: dict[str, int] = {}
        self.imports: list[tuple[str, dict[str, Any]]] = []

    # ── state ──

    def counts(self, bank: str) -> dict[str, int]:
        b = self.banks[bank]
        return {
            "facts": sum(len(d["facts"]) for d in b["docs"].values()) + self.orphans.get(bank, 0),
            "observations": len(b["obs"]),
            "mental_models": len(b["models"]),
            "directives": len(b["directives"]),
            "documents": len(b["docs"]),
        }

    def all_tags(self, bank: str) -> list[str]:
        b = self.banks[bank]
        out = [t for d in b["docs"].values() for t in d["tags"]]
        out += [t for d in b["docs"].values() for f in d["facts"] for t in f["tags"]]
        out += [t for o in b["obs"] for t in o["tags"]]
        out += [t for m in b["models"].values() for t in m.get("tags") or []]
        return out

    # ── export and merge ──

    def _export(self, bank: str) -> bytes:
        b = self.banks[bank]
        docs = list(b["docs"].values())
        buf = io.BytesIO()
        with zipfile.ZipFile(buf, "w") as zf:
            zf.writestr(
                "manifest.json",
                json.dumps(
                    {
                        "schema_version": 1,
                        "archive_type": "bank",
                        "source_bank_id": bank,
                        "document_count": len(docs),
                        "fact_count": sum(len(d["facts"]) for d in docs),
                        "observation_count": len(b["obs"]),
                        "directive_count": len(b["directives"]),
                        "mental_model_count": len(b["models"]),
                        "knowledge_page_count": 0,
                    }
                ),
            )
            for i, d in enumerate(docs):
                zf.writestr(f"documents/{i:06d}.json", json.dumps(d))
            zf.writestr("observations.json", json.dumps(b["obs"]))
            zf.writestr("mental_models.json", json.dumps(list(b["models"].values())))
            zf.writestr("knowledge_pages.json", json.dumps(b["pages"]))
            zf.writestr("directives.json", json.dumps(b["directives"]))
        return buf.getvalue()

    def _merge(self, bank: str, data: bytes, conflict: str) -> dict[str, Any]:
        assert conflict == "skip", "the fold-in only ever merges with skip"
        target = self.banks[bank]
        with zipfile.ZipFile(io.BytesIO(data)) as zf:
            names = zf.namelist()
            docs = [
                json.loads(zf.read(n))
                for n in sorted(n for n in names if n.startswith("documents/"))
            ]
            obs = json.loads(zf.read("observations.json")) if "observations.json" in names else []
            models = (
                json.loads(zf.read("mental_models.json")) if "mental_models.json" in names else []
            )
            pages = (
                json.loads(zf.read("knowledge_pages.json"))
                if "knowledge_pages.json" in names
                else []
            )
            assert "directives.json" not in names
        landed: set[str] = set()
        fail = bank in self.fail_import
        for i, d in enumerate(docs):
            if fail and i >= 1:
                self.fail_import.discard(bank)
                return {"status": "failed", "error_message": "worker died"}
            if d["id"] in target["docs"]:
                continue
            target["docs"][d["id"]] = d
            landed.add(d["id"])
        for o in obs:
            if all(s["document_id"] in landed for s in o["sources"]):
                target["obs"].append(o)
        for m in models:
            target["models"].setdefault(m["id"], m)
        target["pages"].extend(pages)
        return {"status": "completed"}

    def finish(self, op_id: str) -> None:
        op = self.ops[op_id]
        op.update(op.pop("land")())

    # ── the transport ──

    def _page(self, key: str, rows: list[Any], params: dict[str, str]) -> httpx.Response:
        offset, limit = int(params.get("offset", 0)), int(params.get("limit", 100))
        return httpx.Response(200, json={key: rows[offset : offset + limit], "total": len(rows)})

    async def handler(self, request: httpx.Request) -> httpx.Response:
        if not self.reachable or request.url.port != self.port:
            raise httpx.ConnectError("connection refused", request=request)
        method, path, params = request.method, request.url.path, dict(request.url.params)
        if path == "/health":
            return httpx.Response(200, json={"status": "healthy"})
        if path == "/version":
            return httpx.Response(
                200,
                json={
                    "api_version": "1.2.0",
                    "features": {"document_export_api": True, "document_import_api": True},
                },
            )
        if path == "/v1/default/banks":
            q = params.get("q", "").lower()
            rows = [
                {"bank_id": b, "last_document_at": f"doc-{len(v['docs'])}"}
                for b, v in self.banks.items()
                if q in b.lower()
            ]
            return self._page("banks", rows, params)
        if path.startswith("/v1/default/files/download/"):
            return httpx.Response(200, content=self.files[path.rsplit("/", 1)[-1]])
        m = re.fullmatch(r"/v1/default/banks/([^/]+)(/.*)?", path)
        assert m, f"unexpected {method} {path}"
        bank, rest = m.group(1), m.group(2) or ""
        if method == "PUT" and rest == "":
            self.events.append(f"create:{bank}")
            self.banks.setdefault(bank, empty_bank())
            return httpx.Response(200, json={"bank_id": bank})
        if rest.startswith("/operations/"):
            op = self.ops.get(rest.rsplit("/", 1)[-1])
            public = {k: v for k, v in (op or {}).items() if k != "land"}
            return httpx.Response(200, json=public or {"status": "not_found"})
        if bank not in self.banks:
            return httpx.Response(404, json={"detail": "bank not found"})
        b = self.banks[bank]
        if rest == "/transfer/export" and method == "POST":
            self.events.append(f"export:{bank}")
            key = f"exp{len(self.files)}"
            self.files[key] = self._export(bank)
            self.ops[key] = {
                "status": "completed",
                "result_metadata": {"download_url": f"/v1/default/files/download/{key}"},
            }
            return httpx.Response(202, json={"operation_id": key})
        if rest == "/transfer/import" and method == "POST":
            assert params == {"mode": "merge", "document_conflict": "skip"}, params
            body = request.content
            start = body.index(b"PK\x03\x04")
            end = body.rindex(b"PK\x05\x06") + 22
            data = body[start:end]
            op_id = f"imp{len(self.ops)}"
            self.events.append(f"import:{bank}")
            self.imports.append((bank, fi._read_archive(data)))
            if bank in self.hold_import:
                self.ops[op_id] = {
                    "status": "processing",
                    "land": lambda: self._merge(bank, data, params["document_conflict"]),
                }
            else:
                self.ops[op_id] = self._merge(bank, data, params["document_conflict"])
            return httpx.Response(202, json={"operation_id": op_id})
        if rest == "/stats":
            c = self.counts(bank)
            return httpx.Response(
                200,
                json={
                    "total_documents": c["documents"],
                    "nodes_by_fact_type": {"world": c["facts"], "observation": c["observations"]},
                    "total_observations": c["observations"],
                    "total_nodes": c["facts"] + c["observations"],
                    "operations_by_status": {"pending": self.queue.get(bank, 0), "processing": 0},
                    "pending_consolidation": 0,
                },
            )
        if rest == "/mental-models":
            return self._page("items", list(b["models"].values()), params)
        if rest == "/directives":
            return self._page("items", b["directives"], params)
        if rest == "/config":
            return httpx.Response(200, json={"overrides": {}})
        if rest == "/knowledge-base/tree":
            return httpx.Response(200, json={"roots": []})
        if rest == "/documents":
            rows = [{"id": d["id"], "tags": d["tags"]} for d in b["docs"].values()]
            return self._page("items", rows, params)
        if rest == "/memories/list":
            rows = [
                {
                    "id": f"{d['id']}#{i}",
                    "document_id": d["id"],
                    "tags": f["tags"],
                    "text": f["text"],
                }
                for d in b["docs"].values()
                for i, f in enumerate(d["facts"])
            ]
            return self._page("items", rows if params.get("type") == "world" else [], params)
        if rest == "/operations":
            rows = [
                {"id": f"r{i}", "operation_type": "retain", "status": "pending"}
                for i in range(self.queue.get(bank, 0))
            ]
            if "status" in params:
                rows = [r for r in rows if r["status"] == params["status"]]
            return self._page("operations", rows, params)
        raise AssertionError(f"unexpected {method} {path}")


class Host:
    """The box: drop-ins, restarts, the engine's environment."""

    def __init__(self, events: list[str], engine: FakeEngine) -> None:
        self.events, self.engine = events, engine
        self.dropin_port: int | None = None
        self.api_restarts = 0
        self.api_error: str | None = None

    def listener(self, port: int) -> dict[str, Any]:
        if self.engine.reachable and self.engine.port == port:
            return {"owner": "unit", "holder": "hindsight-api", "pid": 100 + self.engine.restarts}
        return {"owner": "none", "holder": None, "pid": None}

    def isolate(self, port: int, _run: str) -> dict[str, Any]:
        self.events.append("isolate")
        self.dropin_port = port
        return {"error": None}

    def release(self) -> dict[str, Any]:
        self.events.append("release")
        self.dropin_port = None
        return {"error": None}

    def apply_dropin(self, embedder: Embedder) -> dict[str, Any]:
        self.events.append(f"engine:{embedder.model}")
        self.engine.restarts += 1
        self.engine.port = self.dropin_port or NORMAL_PORT
        return {"error": None, "restarted": True}

    def evidence(self) -> dict[str, Any]:
        return {"live": BGE._asdict()}

    def restart_api(self) -> dict[str, Any]:
        self.events.append("restart-api")
        self.api_restarts += 1
        return {"error": self.api_error} if self.api_error else {}


def memory_config(**kw: Any) -> SimpleNamespace:
    return SimpleNamespace(
        bank=kw.get("bank", "hal0:shared"),
        private_bank="hal0:private",
        compat=SimpleNamespace(shared_document_prefixes=["git:"]),
        projects=None,
        model_fields_set=set(),
        model_extra={},
    )


class Box:
    def __init__(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("HAL0_HOME", str(tmp_path))
        self.events: list[str] = []
        self.engine = FakeEngine(self.events)
        self.host = Host(self.events, self.engine)
        self.hs = tmp_path / "hs"
        (self.hs / ".pg0").mkdir(parents=True)
        (self.hs / ".pg0" / "data").write_bytes(b"x" * 1024)
        self.cfg = memory_config()
        self.configs: list[tuple[str, dict[str, Any]]] = []
        self.clients: set[str] = set()
        self.privileged = True
        bank_layout.save_layout_record(
            bank_layout.BankLayout(
                layout=bank_layout.LAYOUT_LEGACY,
                legacy_platform_bank="shared",
                legacy_unified=True,
                source=bank_layout.SOURCE_STORE_EXISTS,
            )
        )
        self.seed()
        real_engage, real_lift = write_gate.engage_gate, write_gate.lift_gate

        def engage(**kw: Any) -> dict[str, Any]:
            self.events.append("gate:on")
            return real_engage(**kw)

        def lift(*a: Any, **kw: Any) -> bool:
            self.events.append("gate:off")
            return real_lift(*a, **kw)

        monkeypatch.setattr(write_gate, "engage_gate", engage)
        monkeypatch.setattr(write_gate, "lift_gate", lift)

    def seed(self) -> None:
        e = self.engine
        e.banks["shared"] = empty_bank()
        e.banks["shared"]["docs"] = {
            "n1": doc("n1", fact("sky is blue", "agent:hermes"), fact("grass", "agent:hermes")),
            "private:hermes:s1": doc(
                "private:hermes:s1",
                fact("my secret", "agent:hermes", "visibility:private"),
                tags=("visibility:private",),
            ),
        }
        e.banks["shared"]["obs"] = [
            {"text": "nature", "tags": [], "sources": [{"document_id": "n1", "fact_index": 0}]},
            {
                "text": "secret obs",
                "tags": [],
                "sources": [{"document_id": "private:hermes:s1", "fact_index": 0}],
            },
        ]
        e.banks["shared"]["models"] = {
            "overview": {"id": "overview", "name": "O", "tags": [], "content": "x"}
        }
        e.banks["shared"]["directives"] = [{"id": "d1", "name": "Terse", "content": "be terse"}]
        e.banks["coding-agent::web"] = empty_bank()
        e.banks["coding-agent::web"]["docs"] = {
            "conversation:a": doc("conversation:a", fact("we use vite", "agent:claude-code")),
            "git:123": doc("git:123", fact("commit 123")),
        }
        e.banks["coding-agent::web"]["models"] = {
            "overview": {"id": "overview", "name": "Web", "tags": [], "content": "y"}
        }
        e.banks["pi-coder"] = empty_bank()
        e.banks["pi-coder"]["docs"] = {"p1": doc("p1", fact("pi note", "project:web"))}
        e.banks["private__brain"] = empty_bank()
        e.banks["private__brain"]["docs"] = {"b1": doc("b1", fact("brain private"))}
        e.banks["agents"] = empty_bank()

    def http(self, base_url: str) -> httpx.AsyncClient:
        return httpx.AsyncClient(
            transport=httpx.MockTransport(self.engine.handler), base_url=base_url
        )

    def client(self, base_url: str) -> HindsightRestClient:
        return HindsightRestClient(
            base_url=base_url, http_client=self.http(base_url), api_key="hal0-local-noauth"
        )

    def fold(self, **kw: Any) -> fi.FoldIn:
        now = [0.0]

        def clock() -> float:
            now[0] += 1
            return now[0]

        async def no_sleep(_s: float) -> None:
            await asyncio.sleep(0)

        kw.setdefault("timeouts", FAST)
        return fi.FoldIn(
            self.client(f"http://127.0.0.1:{NORMAL_PORT}"),
            memory_config=lambda: self.cfg,
            known_clients=lambda: set(self.clients),
            client_configs=lambda: list(self.configs),
            restart_api=self.host.restart_api,
            privileged=lambda: self.privileged,
            pick_port=lambda: PRIVATE_PORT,
            hs_dir=self.hs,
            apply_dropin=self.host.apply_dropin,
            evidence=self.host.evidence,
            isolate=self.host.isolate,
            release=self.host.release,
            stop_unit=lambda: {},
            isolated_port=lambda: self.host.dropin_port,
            client_for=self.client,
            listener=self.host.listener,
            unit_text=lambda: SHIPPED_UNIT,
            unit_invocation=lambda: f"inv-{self.engine.restarts}",
            sleep=no_sleep,
            clock=clock,
            **kw,
        )

    async def kill_at(self, point: str, **kw: Any) -> fi.Journal:
        def hook(reached: str) -> None:
            if reached == point:
                raise Killed

        run = self.fold(hook=hook, **kw)

        async def no_abandon(*_a: Any) -> None:
            raise Killed

        # A kill -9 runs nothing on its way out, not even the undo of an early stop.
        run._abandon = no_abandon  # type: ignore[method-assign]
        with pytest.raises(Killed):
            await run.run()
        journal = fi.load_journal()
        assert journal is not None
        return journal

    def layout(self) -> bank_layout.BankLayout | None:
        return bank_layout.load_layout_record()


@pytest.fixture
def box(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Box:
    return Box(tmp_path, monkeypatch)


def _codes(plan: dict[str, Any]) -> list[str]:
    return [r["code"] for r in plan["refusals"]]


def _assert_folded(box: Box) -> None:
    e = box.engine
    shared, private = e.banks["hal0:shared"], e.banks["hal0:private"]
    assert set(shared["docs"]) == {
        "n1",
        "web::hal0-legacy-claude-code::conversation:a",
        "web::shared::git:123",
        "web::pi-coder::p1",
    }
    assert set(private["docs"]) == {"private:hermes:s1", "private:brain:b1"}
    assert "visibility:private" not in e.all_tags("hal0:shared")
    assert all(not d.startswith("private:") for d in shared["docs"])
    assert set(shared["models"]) == {"overview", "web-overview"}
    assert shared["directives"] == []
    layout = box.layout()
    assert layout is not None and layout.two_bank
    assert write_gate.read_gate() is None
    assert fi.load_journal() is None
    assert box.host.dropin_port is None
    assert e.port == NORMAL_PORT


# ── the dry run ─────────────────────────────────────────────────────────


async def test_a_dry_run_reports_every_bank_and_writes_nothing(box: Box) -> None:
    plan = await box.fold().preflight()
    assert plan["refusals"] == []
    rows = {r["bank"]: r for r in plan["banks"]}
    assert rows["shared"]["class"] == "platform"
    assert rows["agents"]["action"] == "none"
    fold = rows["shared"]["fold"]
    assert fold["private_facts"] == 1
    assert fold["documents"] == {"shared": 1, "private": 1}
    assert rows["coding-agent::web"]["fold"]["owners"]["claude-code"]["owner"] == (
        "hal0-legacy-claude-code"
    )
    assert rows["pi-coder"]["fold"]["owners"]["pi"]["owner"] == "pi-coder"
    assert rows["pi-coder"]["fold"]["projects"] == {"web": 1}
    assert rows["shared"]["counts"]["directives"] == 1
    assert any("directive" in w for w in plan["warnings"])
    assert box.events == []
    assert box.engine.imports == []
    assert not em.archive_root().exists()
    assert write_gate.read_gate() is None
    assert box.layout().layout == bank_layout.LAYOUT_LEGACY


async def test_a_dry_run_refuses_a_bank_it_does_not_know(box: Box) -> None:
    box.engine.banks["hal0-mono"] = empty_bank()
    plan = await box.fold().preflight()
    assert "unclassified_bank" in _codes(plan)
    mapped = await box.fold(maps={"hal0-mono": "claude-code"}).preflight()
    assert "unclassified_bank" not in _codes(mapped)


async def test_refuses_while_pi_still_writes_to_the_engines_own_port(box: Box) -> None:
    box.configs = [
        (
            "/root/.hindsight/coding-agent.json",
            {"harnesses": {"pi": {"apiUrl": "http://127.0.0.1:9177"}}},
        )
    ]
    plan = await box.fold().preflight()
    assert "pi_direct_engine" in _codes(plan)
    box.configs = [
        (
            "/root/.hindsight/coding-agent.json",
            {"harnesses": {"codex": {"apiUrl": "http://127.0.0.1:9177"}}},
        )
    ]
    plan = await box.fold().preflight()
    assert "pi_direct_engine" not in _codes(plan)
    assert any("codex" in w for w in plan["warnings"])


async def test_refuses_apply_without_root_and_a_target_that_is_the_old_bank(box: Box) -> None:
    box.privileged = False
    assert "not_privileged" in _codes(await box.fold().preflight(apply=True))
    box.privileged = True
    box.cfg = memory_config(bank="shared")
    assert "target_is_old_bank" in _codes(await box.fold().preflight())


async def test_refuses_an_owner_no_client_could_hold(box: Box) -> None:
    plan = await box.fold(owners={"claude-code": "hal0-mine"}).preflight()
    assert "invalid_owner" in _codes(plan)


async def test_refuses_queued_retains(box: Box) -> None:
    box.engine.queue["pi-coder"] = 2
    assert "pending_operations" in _codes(await box.fold().preflight())


async def test_refuses_a_store_already_on_the_two_banks(box: Box) -> None:
    bank_layout.save_layout_record(bank_layout.BankLayout(layout=bank_layout.LAYOUT_TWO_BANK))
    assert "already_two_bank" in _codes(await box.fold().preflight())


# ── the run ─────────────────────────────────────────────────────────────


async def test_apply_folds_every_bank_and_cuts_over(box: Box) -> None:
    result = await box.fold().run()
    assert result["status"] == "folded"
    assert sorted(result["folded"]) == sorted(
        ["shared", "coding-agent::web", "pi-coder", "private__brain"]
    )
    _assert_folded(box)
    e = box.engine
    # The private observation went private, the shared one shared.
    assert [o["text"] for o in e.banks["hal0:shared"]["obs"]] == ["nature"]
    assert [o["text"] for o in e.banks["hal0:private"]["obs"]] == ["secret obs"]
    # Old banks untouched.
    assert set(e.banks["shared"]["docs"]) == {"n1", "private:hermes:s1"}
    assert box.host.api_restarts == 1
    order = [
        ev
        for ev in box.events
        if ev in ("gate:on", "isolate", "release", "restart-api", "gate:off")
    ]
    assert order == ["gate:on", "isolate", "release", "restart-api", "gate:off"]
    record = bank_layout.load_fold_in_record()
    assert {b["bank"] for b in record["folded"]} == set(result["folded"])
    # Every shared archive the engine received was clean.
    for target, archive in e.imports:
        if target == "hal0:shared":
            assert "visibility:private" not in json.dumps(archive)


async def test_a_bank_whose_ids_are_taken_is_left_and_the_run_goes_on(box: Box) -> None:
    box.engine.banks["hal0:shared"] = empty_bank()
    box.engine.banks["hal0:shared"]["docs"]["n1"] = doc("n1", fact("someone else's"))
    result = await box.fold().run()
    assert result["status"] == "folded_with_left"
    assert result["left"] == ["shared"]
    assert "coding-agent::web" in result["folded"]
    # Nothing of the left bank was imported, private part included.
    assert "private:hermes:s1" not in box.engine.banks["hal0:private"]["docs"]
    record = bank_layout.load_fold_in_record()
    assert record["left"][0]["bank"] == "shared"
    assert record["left"][0]["sent_parts"] == []


async def test_a_bank_the_export_cannot_carry_whole_is_left(box: Box) -> None:
    box.engine.orphans["pi-coder"] = 1
    result = await box.fold().run()
    assert result["left"] == ["pi-coder"]
    assert "belongs to no document" in next(
        b["note"] for b in result["banks"] if b["bank"] == "pi-coder"
    )


async def test_a_failed_import_leaves_the_bank_and_a_retry_completes_it(box: Box) -> None:
    # The coding bank's import dies after its first document landed.
    box.engine.fail_import.add("hal0:shared")
    result = await box.fold().run()
    assert result["left"] == ["coding-agent::web"]
    assert "worker died" in next(b["note"] for b in result["banks"] if b["step"] == "left")
    assert box.layout().two_bank
    record = bank_layout.load_fold_in_record()
    assert record["left"][0]["sent_parts"] == ["shared"]
    retry = await box.fold(only=["coding-agent::web"]).run()
    assert retry["folded"] == ["coding-agent::web"]
    _assert_folded(box)
    record = bank_layout.load_fold_in_record()
    assert record["left"] == []
    assert {b["bank"] for b in record["folded"]} == {
        "shared",
        "coding-agent::web",
        "pi-coder",
        "private__brain",
    }
    # A retry does not restart hal0-api: the layout did not change.
    assert box.host.api_restarts == 1


async def test_a_retry_refuses_a_bank_already_folded(box: Box) -> None:
    await box.fold().run()
    plan = await box.fold(only=["pi-coder"]).preflight()
    assert "not_foldable" in _codes(plan)


async def test_a_failure_before_any_import_is_undone(box: Box) -> None:
    def hook(point: str) -> None:
        if point == "isolated":
            raise fi.FoldInError("boom")

    with pytest.raises(fi.FoldInError, match="boom"):
        await box.fold(hook=hook).run()
    assert write_gate.read_gate() is None
    assert fi.load_journal() is None
    assert box.host.dropin_port is None
    assert box.layout().layout == bank_layout.LAYOUT_LEGACY
    assert box.engine.imports == []


# ── resume at every step ───────────────────────────────────────────────


@pytest.mark.parametrize(
    "point",
    [
        "exported:coding-agent::web",
        "rewritten:coding-agent::web",
        "importing:coding-agent::web:shared",
        "imported:coding-agent::web:shared",
        "folded:coding-agent::web",
        "importing:shared:private",
        "before_cutover",
        "released",
        "recorded",
    ],
)
async def test_a_run_killed_at_any_step_completes_with_resume(box: Box, point: str) -> None:
    await box.kill_at(point)
    assert write_gate.read_gate() is not None
    assert fi.blocking_reason() is not None
    result = await box.fold().resume()
    assert result["left"] == []
    _assert_folded(box)


async def test_resume_waits_for_an_import_still_running_and_never_resends_it(box: Box) -> None:
    box.engine.hold_import.add("hal0:shared")
    with pytest.raises(fi.FoldInError, match="still processing") as caught:
        await box.fold().run()
    assert caught.value.resumable
    assert write_gate.read_gate() is not None
    (held,) = [k for k, v in box.engine.ops.items() if v.get("status") == "processing"]
    # Still running: a resume waits for it rather than send it again.
    with pytest.raises(fi.FoldInError, match="still processing"):
        await box.fold().resume()
    box.engine.hold_import.clear()
    box.engine.finish(held)
    await box.fold().resume()
    _assert_folded(box)
    sent = [t for t, _ in box.engine.imports if t == "hal0:shared"]
    # One shared part per bank that has one (shared, coding-agent::web, pi-coder): none twice.
    assert len(sent) == 3


async def test_resume_after_a_kill_between_send_and_record_resends_with_skip(box: Box) -> None:
    await box.kill_at("importing:pi-coder:shared")
    # The kill came before the request: nothing landed, the resend lands it all.
    await box.fold().resume()
    _assert_folded(box)


async def test_resume_without_root_is_refused(box: Box) -> None:
    await box.kill_at("rewritten:shared")
    box.privileged = False
    with pytest.raises(fi.FoldInRefused):
        await box.fold().resume()


async def test_a_lost_hal0_api_restart_is_resumable(box: Box) -> None:
    box.host.api_error = "unit not found"
    with pytest.raises(fi.FoldInError) as caught:
        await box.fold().run()
    assert caught.value.resumable
    assert write_gate.read_gate() is not None
    assert box.layout().two_bank
    box.host.api_error = None
    await box.fold().resume()
    _assert_folded(box)


# ── status ──────────────────────────────────────────────────────────────


async def test_status_reads_the_journal_and_the_record(box: Box) -> None:
    assert box.fold().status()["summary"] == "no fold-in is in progress"
    await box.kill_at("rewritten:shared")
    status = box.fold().status()
    assert status["journal"]["phase"] == fi.PHASE_FOLDING
    assert status["gate"]["reason"] == write_gate.REASON_FOLD_IN
    await box.fold().resume()
    assert "folded 4 bank(s)" in box.fold().status()["summary"]


# ── interlocks ──────────────────────────────────────────────────────────


async def test_an_unfinished_fold_in_blocks_the_switch_both_ways(box: Box) -> None:
    await box.kill_at("rewritten:shared")
    reason = fi.blocking_reason()
    assert reason and "fold-in" in reason
    switch = es.EmbedderSwitch(
        box.client(f"http://127.0.0.1:{NORMAL_PORT}"),
        target=Embedder("local", "hindsight-ai/hs-embed-1"),
        configured=BGE,
        evidence=box.host.evidence,
        isolated_port=lambda: None,
        unit_text=lambda: SHIPPED_UNIT,
        hs_dir=box.hs,
        timeouts=FAST,
    )
    plan = await switch.preflight()
    assert "fold_in" in _codes(plan)
    with pytest.raises(es.SwitchRefused, match="fold-in"):
        await switch.clear_gate()


async def test_an_unfinished_switch_blocks_the_fold_in(
    box: Box, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(es, "blocking_reason", lambda: "an embedder switch did not finish")
    plan = await box.fold().preflight()
    assert "embedder_switch" in _codes(plan)


async def test_the_switch_keeps_its_own_on_disk_names(box: Box) -> None:
    assert es.NAMES.journal == "memory-embedder-switch.json"
    assert es.NAMES.lock == "memory-embedder-switch.lock"
    assert es.NAMES.drop_in == "zzzz-hal0-embedder-switch.conf"
    for key in ("journal", "lock", "archive_prefix", "drop_in", "mirror", "gate_reason"):
        assert getattr(fi.NAMES, key) != getattr(es.NAMES, key), key


async def test_the_switch_does_not_read_the_fold_ins_journal_through_the_gate(box: Box) -> None:
    await box.kill_at("rewritten:shared")
    assert es.interrupted_switch() is None
    assert es.blocking_reason() is None
    assert fi.interrupted_fold_in() is not None


async def test_an_unfinished_fold_in_blocks_update_engine_upgrade_and_seeding(
    box: Box,
) -> None:
    from hal0.memory import bank_seed
    from hal0.updater import updater

    await box.kill_at("rewritten:shared")
    with pytest.raises(updater.UpdateBlockedByFoldIn):
        updater._raise_if_embedder_switch()
    assert "fold-in" in (bank_seed.switch_in_progress() or "")


def test_the_gate_message_names_the_fold_in() -> None:
    error = write_gate.MemoryWritesPaused({"reason": write_gate.REASON_FOLD_IN})
    assert "hal0 memory migrate fold-in --status" in str(error)
