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

#: The ``mental_models`` columns hindsight-api 1.2.0 declares NOT NULL on
#: Postgres: alembic ``n9i0j1k2l3m4`` (name, source_query, content,
#: last_refreshed_at, created_at; the table was ``pinned_reflections``) and
#: ``v7q8r9s0t1u2`` (max_tokens, trigger). Written from the engine's
#: migrations, not from the fold-in's own list.
MM_NOT_NULL = frozenset(
    {"name", "source_query", "content", "last_refreshed_at", "created_at", "max_tokens", "trigger"}
)

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
        #: Banks whose next import fails after its mental models, before its pages
        #: (hindsight-api 1.2.0 writes them with no transaction around them).
        self.fail_pages: set[str] = set()
        #: Banks whose next import fails at its first mental model, as a NOT NULL
        #: violation does, after its documents landed.
        self.fail_models: set[str] = set()
        #: Banks whose import stays ``processing`` until :meth:`finish`.
        self.hold_import: set[str] = set()
        #: Per bank: queued retains (``pending``), counted and listed.
        self.queue: dict[str, int] = {}
        #: Facts the export leaves out (they belong to no document).
        self.orphans: dict[str, int] = {}
        self.imports: list[tuple[str, dict[str, Any]]] = []
        #: Per bank: manifest fields the export adds (attachment_count, ...).
        self.manifest_extra: dict[str, dict[str, Any]] = {}
        #: Per bank: the next import lands this many facts more (or, negative,
        #: fewer) than its archive carries, every id present all the same.
        self.skew_import: dict[str, int] = {}
        #: Per bank: mental_model_history rows the export carries.
        self.model_history: dict[str, list[dict[str, Any]]] = {}

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
                        **self.manifest_extra.get(bank, {}),
                    }
                ),
            )
            if bank in self.model_history:
                zf.writestr("mental_model_history.json", json.dumps(self.model_history[bank]))
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
        skew = self.skew_import.pop(bank, 0)
        if skew and landed:
            facts = target["docs"][sorted(landed)[0]]["facts"]
            if skew > 0:
                facts.extend(fact(f"extra {i}") for i in range(skew))
            else:
                del facts[skew:]
        for o in obs:
            if all(s["document_id"] in landed for s in o["sources"]):
                target["obs"].append(o)
        # hindsight-api writes model rows verbatim with no transaction around
        # them, after the documents: a NULL in a NOT NULL column fails the
        # insert and leaves the documents landed.
        for m in models:
            nulls = sorted(k for k in MM_NOT_NULL if k in m and m[k] is None)
            if bank in self.fail_models:
                self.fail_models.discard(bank)
                nulls = ["last_refreshed_at"]
            if nulls:
                return {
                    "status": "failed",
                    "error_message": "NotNullViolationError: null value in column "
                    f'"{nulls[0]}" of relation "mental_models" violates not-null constraint',
                }
        for m in models:
            target["models"].setdefault(m["id"], m)
        if bank in self.fail_pages and pages:
            self.fail_pages.discard(bank)
            return {"status": "failed", "error_message": "died before the pages"}
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
                {"bank_id": b, "last_document_at": "doc-" + "|".join(sorted(v["docs"]))}
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
            return httpx.Response(200, json={"roots": [dict(p) for p in b["pages"]]})
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
            if params.get("type") == "observation":
                obs = [
                    {"id": f"o{i}", "text": o["text"], "tags": o["tags"]}
                    for i, o in enumerate(b["obs"])
                ]
                return self._page("items", obs, params)
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
        self.unit_text = SHIPPED_UNIT
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
            "overview": {
                "id": "overview",
                "name": "O",
                "tags": [],
                "content": "x",
                "last_refreshed_at": "2026-11-01T10:00:00+00:00",
                "last_memory_seen_at": "2026-11-01T10:00:00+00:00",
                "created_at": "2026-10-01T10:00:00+00:00",
            }
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
            unit_text=lambda: self.unit_text,
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


def _assert_on_the_old_layout(box: Box) -> None:
    """A run that did not cut over: writes open on the old banks, nothing blocking."""
    assert box.layout().layout == bank_layout.LAYOUT_LEGACY
    assert write_gate.read_gate() is None
    assert fi.load_journal() is None
    assert fi.blocking_reason() is None
    assert box.host.dropin_port is None
    assert box.engine.port == NORMAL_PORT
    assert box.host.api_restarts == 0


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


async def test_an_engine_listening_beyond_loopback_warns_and_needs_a_flag_to_apply(
    box: Box,
) -> None:
    """Third review, item 4: a remote client on the engine's port is invisible otherwise."""
    plan = await box.fold().preflight()
    listen = plan["engine_listen"]
    assert listen["host"] == "127.0.0.1" and listen["exposed"] is False
    assert listen["source"].startswith("--host in the unit's ExecStart")
    assert not any("loopback" in w for w in plan["warnings"])
    box.unit_text = SHIPPED_UNIT.replace("--host 127.0.0.1", "--host 0.0.0.0")
    dry = await box.fold().preflight()
    assert "engine_exposed" not in _codes(dry)
    (warning,) = [w for w in dry["warnings"] if "not on loopback only" in w]
    assert "http://<box>:9177" in warning and "nothing reads them" in warning
    assert "--bridge-key-file" in warning and "--allow-exposed-engine" in warning
    applied = await box.fold().preflight(apply=True)
    assert "engine_exposed" in _codes(applied)
    with pytest.raises(fi.FoldInRefused, match="not on loopback only"):
        await box.fold().run()
    assert box.engine.imports == [] and write_gate.read_gate() is None
    allowed = await box.fold(allow_exposed_engine=True).preflight(apply=True)
    assert allowed["refusals"] == []
    assert (await box.fold(allow_exposed_engine=True).run())["status"] == "folded"


@pytest.mark.parametrize(
    ("command", "environment", "exposed"),
    [
        ("--port 9177", "", True),
        ("--port 9177", "Environment=HINDSIGHT_API_HOST=127.0.0.1\n", False),
        ("--port 9177", 'Environment="HINDSIGHT_API_HOST=0.0.0.0" OTHER=1\n', True),
        ("--host=localhost --port 9177", "Environment=HINDSIGHT_API_HOST=0.0.0.0\n", False),
        ("--host ::1 --port 9177", "", False),
        ("--host 10.0.0.5 --port 9177", "", True),
    ],
)
def test_the_listen_address_is_read_as_the_engine_reads_it(
    command: str, environment: str, exposed: bool
) -> None:
    unit = (
        "# /etc/systemd/system/hindsight-api.service\n[Service]\n"
        + environment
        + f"ExecStart=/opt/hs/bin/hindsight-api {command}\n"
    )
    host, _where = em.listen_host(fi.NAMES, unit)
    assert host is not None
    assert (not em.is_loopback_host(host)) is exposed


@pytest.mark.parametrize(
    ("command", "host"),
    [
        ("--host 127.0.0.1 --port 9177 --host 0.0.0.0", "0.0.0.0"),
        ("--host 0.0.0.0 --port 9177 --host=127.0.0.1", "127.0.0.1"),
        ("--host=10.0.0.5 --host 127.0.0.1", "127.0.0.1"),
    ],
)
def test_a_host_given_twice_binds_the_last_as_argparse_does(command: str, host: str) -> None:
    """Fourth review, item 7."""
    unit = (
        "# /etc/systemd/system/hindsight-api.service\n[Service]\n"
        f"ExecStart=/opt/hs/bin/hindsight-api {command}\n"
    )
    assert em.listen_host(fi.NAMES, unit)[0] == host


def test_the_listen_address_reads_the_units_environment_file(tmp_path: Path) -> None:
    """Fourth review, item 8: an EnvironmentFile beats Environment=, and unreadable is exposed."""
    env = tmp_path / "engine.env"
    env.write_text("# a comment\nOTHER=1\nHINDSIGHT_API_HOST='0.0.0.0'\n", encoding="utf-8")

    def unit(lines: str, command: str = "--port 9177") -> str:
        return (
            "# /etc/systemd/system/hindsight-api.service\n[Service]\n"
            + lines
            + f"ExecStart=/opt/hs/bin/hindsight-api {command}\n"
        )

    # The file overrides Environment=, even when Environment= comes after it.
    host, where = em.listen_host(
        fi.NAMES, unit(f"EnvironmentFile={env}\nEnvironment=HINDSIGHT_API_HOST=127.0.0.1\n")
    )
    assert host == "0.0.0.0" and str(env) in where
    # A file that does not set it leaves Environment= in charge.
    env.write_text("OTHER=1\n", encoding="utf-8")
    host, _ = em.listen_host(
        fi.NAMES, unit(f"EnvironmentFile={env}\nEnvironment=HINDSIGHT_API_HOST=127.0.0.1\n")
    )
    assert host == "127.0.0.1"
    # An explicit --host on the command line wins over both.
    env.write_text("HINDSIGHT_API_HOST=0.0.0.0\n", encoding="utf-8")
    host, _ = em.listen_host(fi.NAMES, unit(f"EnvironmentFile={env}\n", "--host 127.0.0.1"))
    assert host == "127.0.0.1"
    # A missing optional file is skipped, as systemd skips it.
    host, _ = em.listen_host(
        fi.NAMES,
        unit(f"EnvironmentFile=-{tmp_path}/absent.env\nEnvironment=HINDSIGHT_API_HOST=::1\n"),
    )
    assert host == "::1"
    # A file that cannot be read makes the host unknown, which is exposed.
    for lines in (f"EnvironmentFile={tmp_path}/absent.env\n", f"EnvironmentFile={tmp_path}\n"):
        host, where = em.listen_host(fi.NAMES, unit(lines))
        assert host == em.UNKNOWN_HOST and "cannot be read" in where
        assert not em.is_loopback_host(host)


async def test_an_unreadable_environment_file_refuses_apply_without_the_flag(
    box: Box, tmp_path: Path
) -> None:
    """Fourth review, item 8: an engine whose host cannot be read is treated as exposed."""
    unreadable = tmp_path / "secret.env"
    unreadable.mkdir()
    box.unit_text = SHIPPED_UNIT.replace("--host 127.0.0.1 ", "").replace(
        "EnvironmentFile=-/etc/hal0/hindsight-llm.env",
        f"EnvironmentFile={unreadable}",
    )
    dry = await box.fold().preflight()
    assert dry["engine_listen"]["exposed"] is True
    assert any("cannot be read" in w for w in dry["warnings"])
    assert "engine_exposed" in _codes(await box.fold().preflight(apply=True))
    allowed = await box.fold(allow_exposed_engine=True).preflight(apply=True)
    assert "engine_exposed" not in _codes(allowed)


async def test_refuses_apply_without_root_and_a_target_that_is_the_old_bank(box: Box) -> None:
    box.privileged = False
    assert "not_privileged" in _codes(await box.fold().preflight(apply=True))
    box.privileged = True
    box.cfg = memory_config(bank="shared")
    assert "target_is_old_bank" in _codes(await box.fold().preflight())


async def test_an_upgraded_boxs_bank_shared_gets_the_exact_edit_and_stays_the_platform_bank(
    box: Box,
) -> None:
    # CT143: every upgraded box carries [memory] bank = "shared". The run
    # refuses, names the edit, and the dry run still shows shared as the
    # platform bank to fold, not as a target.
    box.cfg = memory_config(bank="shared")
    plan = await box.fold().preflight()
    (message,) = [r["message"] for r in plan["refusals"] if r["code"] == "target_is_old_bank"]
    assert '[memory] bank = "hal0:shared"' in message
    assert "hal0.toml" in message
    assert "layout record" in message
    rows = {row["bank"]: row for row in plan["banks"]}
    assert rows["shared"]["class"] == "platform"
    assert rows["shared"]["action"] == "fold"


def test_the_old_bank_edit_is_safe_while_the_layout_is_old() -> None:
    # The message's promise: with the old layout recorded, the edited value is
    # never read as the platform bank; the record's bank is.
    from hal0.memory import bank_layout

    layout = bank_layout.BankLayout(
        layout=bank_layout.LAYOUT_LEGACY, legacy_platform_bank="shared", legacy_unified=True
    )
    cfg = SimpleNamespace(bank="hal0:shared", model_fields_set={"bank"})
    assert bank_layout.legacy_platform_bank(layout, cfg) == "shared"


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


async def test_a_bank_whose_ids_are_taken_leaves_the_run_with_nothing_imported(box: Box) -> None:
    box.engine.banks["hal0:shared"] = empty_bank()
    box.engine.banks["hal0:shared"]["docs"]["n1"] = doc("n1", fact("someone else's"))
    result = await box.fold().run()
    assert result["status"] == "incomplete"
    assert result["cut_over"] is False
    # The pre-check found the clash before any import: no bank was imported,
    # so no bank is left half-sent and the next run starts clean.
    assert box.engine.imports == []
    assert result["folded"] == []
    assert sorted(result["left"]) == sorted(FOLDABLE_BANKS)
    notes = {b["bank"]: b["note"] for b in result["banks"]}
    assert "already holds 1 of its document id(s)" in notes["shared"]
    assert "not imported" in notes["pi-coder"] and "(shared)" in notes["pi-coder"]
    _assert_on_the_old_layout(box)
    ledger = fi.load_ledger()
    assert {row["state"] for row in ledger["banks"].values()} == {"left"}
    assert all("sent_files" not in row for row in ledger["banks"].values())
    # The clashing id is in the 0600 sidecar the note names, not in the note.
    sidecar = Path(result["archive_dir"]) / fi.LEFT_IDS_NAME
    assert str(sidecar) in notes["shared"] and "n1" not in notes["shared"].split(". ")[0]
    assert sidecar.stat().st_mode & 0o777 == 0o600
    assert json.loads(sidecar.read_text())["shared"]["documents"] == ["n1"]
    # Once the target's copy is gone, every bank folds and the store cuts over.
    del box.engine.banks["hal0:shared"]["docs"]["n1"]
    assert (await box.fold().run())["status"] == "folded"
    _assert_folded(box)


async def test_a_bank_the_export_cannot_carry_whole_is_left(box: Box) -> None:
    box.engine.orphans["pi-coder"] = 1
    result = await box.fold().run()
    assert sorted(result["left"]) == sorted(FOLDABLE_BANKS)
    assert box.engine.imports == []
    assert "belongs to no document" in next(
        b["note"] for b in result["banks"] if b["bank"] == "pi-coder"
    )


async def test_a_failed_import_leaves_the_bank_and_a_retry_completes_it(box: Box) -> None:
    # The coding bank's import dies after its first document landed.
    box.engine.fail_import.add("hal0:shared")
    result = await box.fold().run()
    assert result["left"] == ["coding-agent::web"]
    assert "worker died" in next(b["note"] for b in result["banks"] if b["step"] == "left")
    _assert_on_the_old_layout(box)
    ledger = fi.load_ledger()
    assert list(ledger["banks"]["coding-agent::web"]["sent_files"]) == ["shared"]
    before, mark = len(box.engine.imports), len(box.events)
    retry = await box.fold(only=["coding-agent::web"]).run()
    assert retry["status"] == "folded"
    assert "coding-agent::web" in retry["folded"]
    _assert_folded(box)
    # The retry sent only what was missing of the left bank; the banks folded
    # before were exported and checked again, and nothing of them was sent.
    sent = box.engine.imports[before:]
    assert [t for t, _ in sent] == ["hal0:shared"]
    assert [d["id"] for d in sent[0][1]["documents"]] == ["web::shared::git:123"]
    rechecked = {b["bank"]: b for b in retry["banks"] if b["refold"]}
    assert set(rechecked) == {"shared", "pi-coder", "private__brain"}
    assert all(b["step"] == "folded" for b in rechecked.values())
    exported = [ev for ev in box.events[mark:] if ev.startswith("export:")]
    assert set(exported) >= {"export:shared", "export:pi-coder", "export:private__brain"}
    record = bank_layout.load_fold_in_record()
    assert record["left"] == []
    assert {b["bank"] for b in record["folded"]} == {
        "shared",
        "coding-agent::web",
        "pi-coder",
        "private__brain",
    }
    assert box.host.api_restarts == 1


async def test_a_retry_refuses_a_bank_already_folded(box: Box) -> None:
    box.engine.fail_import.add("hal0:shared")
    await box.fold().run()
    plan = await box.fold(only=["pi-coder"]).preflight()
    assert "not_foldable" in _codes(plan)
    await box.fold(only=["coding-agent::web"]).run()
    plan = await box.fold(only=["pi-coder"]).preflight()
    assert "already_two_bank" in _codes(plan)


# ── no cutover while any bank is left (review item 2) ───────────────────


async def test_a_left_bank_keeps_the_old_layout_then_a_later_run_folds_it_and_cuts_over(
    box: Box,
) -> None:
    e = box.engine
    e.fail_import.add("hal0:shared")
    first = await box.fold().run()
    assert first["status"] == "incomplete" and first["left"] == ["coding-agent::web"]
    _assert_on_the_old_layout(box)
    # Writes resumed on the old layout: the old banks take new memory.
    e.banks["shared"]["docs"]["n2"] = doc("n2", fact("written after", "agent:hermes"))
    e.banks["coding-agent::web"]["docs"]["conversation:b"] = doc(
        "conversation:b", fact("later", "agent:claude-code")
    )
    before = len(e.imports)
    second = await box.fold().run()
    assert second["status"] == "folded"
    sent = e.imports[before:]
    by_target = [(t, sorted(d["id"] for d in a["documents"])) for t, a in sent]
    # The changed platform bank sends only its new document; its private part
    # is all there already, so nothing is sent for it. The left bank sends what
    # is missing. pi-coder and private__brain are unchanged: nothing.
    assert by_target == [
        ("hal0:shared", ["n2"]),
        ("hal0:shared", ["web::hal0-legacy-claude-code::conversation:b", "web::shared::git:123"]),
    ]
    shared = e.banks["hal0:shared"]["docs"]
    assert {"n2", "web::hal0-legacy-claude-code::conversation:b"} <= set(shared)
    assert box.layout().two_bank
    assert box.host.api_restarts == 1
    assert write_gate.read_gate() is None


async def test_an_observation_the_old_bank_built_since_does_not_hold_a_refold_back(
    box: Box,
) -> None:
    e = box.engine
    first = await box.fold(only=["shared"]).run()
    assert first["status"] == "incomplete" and first["folded"] == ["shared"]
    assert [o["text"] for o in e.banks["hal0:shared"]["obs"]] == ["nature"]
    # Consolidation in the old bank, after writes resumed, built a new
    # observation over a document already folded, and a new document arrived.
    e.banks["shared"]["obs"].append(
        {"text": "built since", "tags": [], "sources": [{"document_id": "n1", "fact_index": 1}]}
    )
    e.banks["shared"]["docs"]["n2"] = doc("n2", fact("new", "agent:hermes"))
    result = await box.fold().run()
    assert result["status"] == "folded"
    shared = next(b for b in result["banks"] if b["bank"] == "shared")
    assert shared["report"]["observations"]["not_carried_built_since"] == 1
    assert "n2" in e.banks["hal0:shared"]["docs"]


async def test_a_bank_named_alone_does_not_cut_over_while_others_are_pending(box: Box) -> None:
    result = await box.fold(only=["pi-coder"]).run()
    assert result["status"] == "incomplete"
    assert result["left"] == []
    assert sorted(result["pending"]) == ["coding-agent::web", "private__brain", "shared"]
    _assert_on_the_old_layout(box)
    plan = await box.fold(only=["pi-coder"]).preflight()
    assert "not_foldable" in _codes(plan)
    done = await box.fold().run()
    assert done["status"] == "folded"
    _assert_folded(box)


FOLDABLE_BANKS = ["shared", "coding-agent::web", "pi-coder", "private__brain"]


async def test_an_unclassified_bank_holds_the_cutover_back_even_when_not_named(box: Box) -> None:
    # The second review's case: hal0-mono holds a fact, and --bank names only
    # the four foldable banks. Every named bank folds, and still no cutover.
    box.engine.banks["hal0-mono"] = empty_bank()
    box.engine.banks["hal0-mono"]["docs"] = {"m1": doc("m1", fact("devops note"))}
    plan = await box.fold(only=FOLDABLE_BANKS).preflight(apply=True)
    assert plan["refusals"] == []
    assert plan["cuts_over"] is False
    assert [b["bank"] for b in plan["blocking"]] == ["hal0-mono"]
    result = await box.fold(only=FOLDABLE_BANKS).run()
    assert result["status"] == "incomplete"
    assert result["cut_over"] is False
    assert sorted(result["folded"]) == sorted(FOLDABLE_BANKS)
    (blocker,) = result["blocking"]
    assert blocker["bank"] == "hal0-mono"
    assert "--map hal0-mono=HARNESS" in blocker["fix"] and "--skip hal0-mono" in blocker["fix"]
    _assert_on_the_old_layout(box)
    # Acknowledged with --skip, the next run cuts over and leaves it where it is.
    done = await box.fold(skips=["hal0-mono"]).run()
    assert done["status"] == "folded"
    assert "m1" in box.engine.banks["hal0-mono"]["docs"]
    assert box.layout().two_bank


async def test_the_retired_agents_bank_holds_the_cutover_back_until_empty_or_skipped(
    box: Box,
) -> None:
    box.engine.banks["agents"]["docs"] = {"a1": doc("a1", fact("old agent memory"))}
    result = await box.fold().run()
    assert result["status"] == "incomplete"
    assert [b["bank"] for b in result["blocking"]] == ["agents"]
    assert "hal0 memory migrate platform" in result["blocking"][0]["fix"]
    _assert_on_the_old_layout(box)
    box.engine.banks["agents"]["docs"] = {}
    assert (await box.fold().run())["status"] == "folded"


async def test_a_map_is_remembered_and_a_later_run_reuses_it(box: Box) -> None:
    """Third review, item 2: the retry must not drop the --map the run used."""
    e = box.engine
    e.banks["hal0-mono"] = empty_bank()
    e.banks["hal0-mono"]["docs"] = {"m1": doc("m1", fact("devops note", "project:web"))}
    first = await box.fold(maps={"hal0-mono": "claude-code"}, only=["hal0-mono"]).run()
    assert first["status"] == "incomplete" and first["folded"] == ["hal0-mono"]
    assert "--map hal0-mono=claude-code" in first["retry"]
    assert fi.load_ledger()["maps"] == {"hal0-mono": "claude-code"}
    # A later run without the flag still knows the bank.
    plan = await box.fold().preflight()
    assert plan["maps"] == {"hal0-mono": "claude-code"}
    assert plan["refusals"] == [] and plan["blocking"] == []
    # A changed --map, or a --skip, for the folded bank is refused.
    assert "map_frozen" in _codes(await box.fold(maps={"hal0-mono": "codex"}).preflight())
    assert "skip_folded" in _codes(await box.fold(skips=["hal0-mono"]).preflight())
    done = await box.fold().run()
    assert done["status"] == "folded"
    assert box.layout().two_bank


async def test_a_skip_is_remembered_and_named_in_the_retry(box: Box) -> None:
    e = box.engine
    e.banks["hal0-other"] = empty_bank()
    e.banks["hal0-other"]["docs"] = {"x1": doc("x1", fact("kept out"))}
    first = await box.fold(skips=["hal0-other"], only=["shared"]).run()
    assert first["status"] == "incomplete"
    assert first["retry"].endswith("--skip hal0-other")
    assert fi.load_ledger()["skips"] == ["hal0-other"]
    plan = await box.fold().preflight()
    assert plan["skips"] == ["hal0-other"] and plan["blocking"] == []
    done = await box.fold().run()
    assert done["status"] == "folded"
    assert "x1" in e.banks["hal0-other"]["docs"]
    # Third review, item 3: the cutover names what it leaves unread.
    assert done["skipped"] == [
        {"bank": "hal0-other", "class": "unclassified", "facts": 1, "documents": 1}
    ]
    assert "nothing reads them after the cutover" in done["skipped_note"]


async def test_a_first_map_for_a_bank_folded_without_one_is_refused(box: Box) -> None:
    """Fourth review, item 5: the freeze covers every folded bank, not only mapped ones."""
    first = await box.fold(only=["pi-coder"]).run()
    assert first["folded"] == ["pi-coder"]
    plan = await box.fold(maps={"pi-coder": "codex"}).preflight()
    (message,) = [r["message"] for r in plan["refusals"] if r["code"] == "map_frozen"]
    assert "without --map" in message and "Run without --map for pi-coder" in message
    # A bank only partly sent is frozen as well.
    box.engine.fail_import.add("hal0:shared")
    left = await box.fold(only=["coding-agent::web"]).run()
    web = fi.load_ledger()["banks"]["coding-agent::web"]
    assert left["left"] == ["coding-agent::web"] and web["state"] == fi.LEDGER_LEFT
    assert web.get("sent_files")
    frozen = await box.fold(maps={"coding-agent::web": "codex"}).preflight()
    assert "map_frozen" in _codes(frozen)
    # A bank no run has sent anything of may still be mapped.
    box.engine.banks["hal0-mono"] = empty_bank()
    plan = await box.fold(maps={"hal0-mono": "claude-code"}).preflight()
    assert "map_frozen" not in _codes(plan)


async def test_a_recorded_skip_for_a_bank_that_is_gone_is_dropped_from_the_ledger(
    box: Box,
) -> None:
    """Fourth review, item 6: a bank that comes back after its skip was dropped starts afresh."""
    e = box.engine
    e.banks["hal0-other"] = empty_bank()
    e.banks["hal0-other"]["docs"] = {"x1": doc("x1", fact("kept out"))}
    await box.fold(skips=["hal0-other"], only=["shared"]).run()
    assert fi.load_ledger()["skips"] == ["hal0-other"]
    del e.banks["hal0-other"]
    again = await box.fold(only=["pi-coder"]).run()
    assert again["status"] == "incomplete"
    assert fi.load_ledger()["skips"] == []
    # It comes back: unclassified again, so it needs --skip or --map anew.
    e.banks["hal0-other"] = empty_bank()
    e.banks["hal0-other"]["docs"] = {"x2": doc("x2", fact("back again"))}
    plan = await box.fold().preflight()
    assert plan["skips"] == [] and "unclassified_bank" in _codes(plan)


async def test_forget_skip_drops_a_recorded_skip(box: Box) -> None:
    """Fourth review, item 6: --forget-skip BANK."""
    e = box.engine
    e.banks["hal0-other"] = empty_bank()
    await box.fold(skips=["hal0-other"], only=["shared"]).run()
    assert fi.load_ledger()["skips"] == ["hal0-other"]
    plan = await box.fold(forget_skips=["hal0-other"]).preflight()
    assert plan["skips"] == [] and plan["forgotten_skips"] == ["hal0-other"]
    # The empty unclassified bank is unaccounted for again.
    assert "unclassified_bank" in _codes(plan)
    assert fi.load_ledger()["skips"] == ["hal0-other"], "a dry run writes nothing"
    both = await box.fold(skips=["hal0-other"], forget_skips=["hal0-other"]).preflight()
    assert "forget_skip_conflict" in _codes(both)
    unknown = await box.fold(forget_skips=["never-skipped"]).preflight()
    assert any("--forget-skip never-skipped" in w for w in unknown["warnings"])
    # A run that ends writes the ledger without it.
    e.banks["hal0-other"]["docs"] = {}
    del e.banks["hal0-other"]
    await box.fold(forget_skips=["hal0-other"], only=["pi-coder"]).run()
    assert fi.load_ledger()["skips"] == []


async def test_skip_is_only_for_a_bank_the_fold_in_does_not_move(box: Box) -> None:
    plan = await box.fold(skips=["pi-coder"]).preflight()
    assert "skip_not_allowed" in _codes(plan)
    plan = await box.fold(skips=["nope"]).preflight()
    assert "no_such_bank" in _codes(plan)
    box.engine.banks["hal0-mono"] = empty_bank()
    plan = await box.fold(skips=["hal0-mono"]).preflight()
    assert "unclassified_bank" not in _codes(plan)
    assert plan["blocking"] == [] and plan["cuts_over"] is True


async def test_a_folded_document_changed_in_the_old_bank_refuses_until_its_copy_is_deleted(
    box: Box,
) -> None:
    e = box.engine
    e.fail_import.add("hal0:shared")
    await box.fold().run()
    # A document folded into hal0:shared is appended to in the old bank.
    e.banks["pi-coder"]["docs"]["p1"]["facts"].append(fact("appended", "project:web"))
    second = await box.fold().run()
    assert second["status"] == "incomplete"
    note = next(b["note"] for b in second["banks"] if b["bank"] == "pi-coder")
    assert "changed in the old bank" in note and "web::pi-coder::p1" not in note
    sidecar = json.loads((Path(second["archive_dir"]) / fi.LEFT_IDS_NAME).read_text())
    assert sidecar["pi-coder"]["changed"] == ["web::pi-coder::p1"]
    assert len(e.banks["hal0:shared"]["docs"]["web::pi-coder::p1"]["facts"]) == 1
    _assert_on_the_old_layout(box)
    # The operator deletes the stale copy; the next run sends the document as it is now.
    del e.banks["hal0:shared"]["docs"]["web::pi-coder::p1"]
    third = await box.fold().run()
    assert third["status"] == "folded"
    assert len(e.banks["hal0:shared"]["docs"]["web::pi-coder::p1"]["facts"]) == 2


async def test_a_folded_document_retagged_private_refuses_the_cutover(box: Box) -> None:
    """Third review, item 1: a re-tag moves neither the counts nor the last write time."""
    e = box.engine
    first = await box.fold(only=["shared"]).run()
    assert first["folded"] == ["shared"] and "n1" in e.banks["hal0:shared"]["docs"]
    # PATCH /banks/shared/documents/n1 {"tags": [..., "visibility:private"]}:
    # the engine replaces the document's tags and every fact's under it.
    n1 = e.banks["shared"]["docs"]["n1"]
    n1["tags"] = [*n1["tags"], "visibility:private"]
    for f in n1["facts"]:
        f["tags"] = [*f["tags"], "visibility:private"]
    sent = len(e.imports)
    second = await box.fold().run()
    assert second["status"] == "incomplete" and second["cut_over"] is False
    assert e.imports[sent:] == []
    note = next(b["note"] for b in second["banks"] if b["bank"] == "shared")
    assert "no longer go to hal0:shared" in note and "n1" not in note.split(". ")[0]
    sidecar = json.loads((Path(second["archive_dir"]) / fi.LEFT_IDS_NAME).read_text())
    assert sidecar["shared"]["gone"] == ["n1"]
    _assert_on_the_old_layout(box)
    # Once the operator deletes the shared copy, the document folds private.
    del e.banks["hal0:shared"]["docs"]["n1"]
    third = await box.fold().run()
    assert third["status"] == "folded"
    assert "n1" not in e.banks["hal0:shared"]["docs"]
    assert "private:hermes:n1" in e.banks["hal0:private"]["docs"]
    assert "visibility:private" not in e.all_tags("hal0:shared")
    assert all(not d.startswith("private:") for d in e.banks["hal0:shared"]["docs"])


async def test_a_folded_mental_model_retagged_private_refuses(box: Box) -> None:
    e = box.engine
    await box.fold(only=["shared"]).run()
    assert "overview" in e.banks["hal0:shared"]["models"]
    e.banks["shared"]["models"]["overview"]["tags"] = ["visibility:private"]
    second = await box.fold().run()
    assert second["status"] == "incomplete" and second["cut_over"] is False
    note = next(b["note"] for b in second["banks"] if b["bank"] == "shared")
    assert "1 mental model(s)" in note and "no longer go to hal0:shared" in note
    sidecar = json.loads((Path(second["archive_dir"]) / fi.LEFT_IDS_NAME).read_text())
    assert sidecar["shared"]["gone_mental_models"] == ["overview"]
    _assert_on_the_old_layout(box)


async def test_a_folded_mental_model_refreshed_in_the_old_bank_keeps_the_target_copy(
    box: Box,
) -> None:
    """Fourth review, item 1: a content-only refresh converges, with a warning and a count."""
    e = box.engine
    await box.fold(only=["coding-agent::web"]).run()
    model = e.banks["coding-agent::web"]["models"]["overview"]
    model["content"] = "refreshed since"
    model["history"] = [{"content": "y"}]
    said: list[str] = []
    second = await box.fold(progress=said.append).run()
    assert second["status"] == "folded" and second["cut_over"] is True
    report = next(b["report"] for b in second["banks"] if b["bank"] == "coding-agent::web")
    assert report["model_content_refreshed_kept_target"] == 1
    assert any("refreshed in the old bank" in m and "kept as they are" in m for m in said)
    # The older content stays: the target refreshes it from its own memory.
    assert e.banks["hal0:shared"]["models"]["web-overview"]["content"] == "y"


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("tags", ["topic:build"]),
        ("trigger", {"tag_groups": [{"tags": ["topic:build"], "match": "any"}]}),
        ("name", "Renamed"),
        ("source_query", "what changed?"),
    ],
)
async def test_a_folded_mental_model_whose_spec_changed_refuses(
    box: Box, field: str, value: Any
) -> None:
    e = box.engine
    await box.fold(only=["coding-agent::web"]).run()
    e.banks["coding-agent::web"]["models"]["overview"][field] = value
    second = await box.fold().run()
    assert second["status"] == "incomplete"
    note = next(b["note"] for b in second["banks"] if b["bank"] == "coding-agent::web")
    assert "0 document(s) and 1 mental model(s)" in note and "changed in the old bank" in note
    left_ids = str(Path(second["archive_dir"]) / fi.LEFT_IDS_NAME)
    assert f"The full list of ids is in {left_ids}" in note
    assert "DELETE /v1/default/banks/hal0:shared/mental-models/<id>" in note
    assert "There is no option to keep a copy" in note
    sidecar = json.loads(Path(left_ids).read_text())
    assert sidecar["coding-agent::web"]["changed_mental_models"] == ["web-overview"]
    assert e.banks["hal0:shared"]["models"]["web-overview"]["content"] == "y"
    _assert_on_the_old_layout(box)
    # The stale copy deleted, the model is sent as it is now.
    del e.banks["hal0:shared"]["models"]["web-overview"]
    done = await box.fold().run()
    assert done["status"] == "folded"
    assert e.banks["hal0:shared"]["models"]["web-overview"][field] == (
        [*value, "project:web"] if field == "tags" else value
    )


async def test_a_model_whose_content_is_now_cleared_refuses(box: Box) -> None:
    """A copy generated before the bank held private facts may draw on them: not kept."""
    e = box.engine
    await box.fold(only=["coding-agent::web"]).run()
    assert e.banks["hal0:shared"]["models"]["web-overview"]["content"] == "y"
    e.banks["coding-agent::web"]["docs"]["secret"] = doc(
        "secret", fact("mine alone", "visibility:private", "agent:claude-code")
    )
    second = await box.fold().run()
    assert second["status"] == "incomplete"
    sidecar = json.loads((Path(second["archive_dir"]) / fi.LEFT_IDS_NAME).read_text())
    assert sidecar["coding-agent::web"]["changed_mental_models"] == ["web-overview"]


@pytest.mark.parametrize("legacy", ["none", "combined"])
async def test_a_model_recorded_by_an_earlier_build_gets_its_own_message(
    box: Box, legacy: str
) -> None:
    """Fourth review, item 3: an .ids.json without the split digest is not called "changed"."""
    first = await box.fold(only=["coding-agent::web"]).run()
    for path in Path(first["archive_dir"]).glob("*.ids.json"):
        data = json.loads(path.read_text())
        data.pop("model_spec_digests", None)
        data.pop("model_content_digests", None)
        if legacy == "none":
            data.pop("model_digests", None)
        path.write_text(json.dumps(data))
    if legacy == "combined":
        # Unchanged since: the combined digest still matches, so it is the fold-in's own copy.
        again = await box.fold().run()
        assert again["status"] == "folded"
        return
    second = await box.fold().run()
    assert second["status"] == "incomplete"
    note = next(b["note"] for b in second["banks"] if b["bank"] == "coding-agent::web")
    assert "recorded by an earlier build" in note and "Delete the copies and re-send" in note
    assert "changed in the old bank" not in note
    assert str(Path(second["archive_dir"]) / fi.LEFT_IDS_NAME) in note


async def test_a_combined_digest_from_an_earlier_build_that_moved_refuses(box: Box) -> None:
    e = box.engine
    first = await box.fold(only=["coding-agent::web"]).run()
    for path in Path(first["archive_dir"]).glob("*.ids.json"):
        data = json.loads(path.read_text())
        data.pop("model_spec_digests", None)
        data.pop("model_content_digests", None)
        path.write_text(json.dumps(data))
    e.banks["coding-agent::web"]["models"]["overview"]["content"] = "refreshed since"
    second = await box.fold().run()
    note = next(b["note"] for b in second["banks"] if b["bank"] == "coding-agent::web")
    assert second["status"] == "incomplete" and "recorded by an earlier build" in note


async def test_a_folded_documents_text_edit_refuses_rather_than_keeping_it_stale(
    box: Box,
) -> None:
    e = box.engine
    await box.fold(only=["pi-coder"]).run()
    e.banks["pi-coder"]["docs"]["p1"]["original_text"] = "edited"
    second = await box.fold().run()
    assert second["status"] == "incomplete"
    note = next(b["note"] for b in second["banks"] if b["bank"] == "pi-coder")
    assert "changed in the old bank" in note
    sidecar = json.loads((Path(second["archive_dir"]) / fi.LEFT_IDS_NAME).read_text())
    assert sidecar["pi-coder"]["changed"] == ["web::pi-coder::p1"]
    _assert_on_the_old_layout(box)
    del e.banks["hal0:shared"]["docs"]["web::pi-coder::p1"]
    assert (await box.fold().run())["status"] == "folded"
    assert e.banks["hal0:shared"]["docs"]["web::pi-coder::p1"]["original_text"] == "edited"


async def test_a_folded_document_deleted_from_the_old_bank_refuses(box: Box) -> None:
    e = box.engine
    e.fail_import.add("hal0:shared")
    await box.fold().run()
    e.banks["pi-coder"]["docs"]["p2"] = doc("p2", fact("new", "project:web"))
    del e.banks["pi-coder"]["docs"]["p1"]
    second = await box.fold().run()
    note = next(b["note"] for b in second["banks"] if b["bank"] == "pi-coder")
    assert "no longer in the old bank" in note
    assert second["status"] == "incomplete"


async def test_a_platform_bank_with_private_facts_and_a_model_folds_end_to_end(box: Box) -> None:
    """CT143: a cleared model must not send NULL into a NOT NULL column.

    The platform bank holds a private fact, so its model is carried with its
    generated content cleared; the fake engine enforces the NOT NULL columns
    hindsight-api declares on mental_models.
    """
    e = box.engine
    source = e.banks["shared"]["models"]["overview"]
    assert source["last_refreshed_at"] and "private:hermes:s1" in e.banks["shared"]["docs"]
    result = await box.fold().run()
    assert result["status"] == "folded" and result["cut_over"] is True
    model = e.banks["hal0:shared"]["models"]["overview"]
    assert model["content"] == ""
    assert model["last_refreshed_at"] == fi.NEVER_REFRESHED_AT
    assert model["last_memory_seen_at"] is None
    assert not any(model.get(k) is None for k in MM_NOT_NULL if k in model)
    assert "n1" in e.banks["hal0:shared"]["docs"]
    assert "private:hermes:s1" in e.banks["hal0:private"]["docs"]


async def test_a_model_the_rewrite_would_null_is_refused_before_anything_is_sent(
    box: Box, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The guard behind the clearing: a NULL in a NOT NULL column leaves the bank unsent."""
    monkeypatch.setitem(fi._CLEARED_MODEL_VALUES, "last_refreshed_at", None)
    result = await box.fold().run()
    assert result["status"] == "incomplete" and box.engine.imports == []
    note = next(b["note"] for b in result["banks"] if b["bank"] == "shared")
    assert "failed their safety check" in note
    sidecar = json.loads((Path(result["archive_dir"]) / fi.LEFT_IDS_NAME).read_text())
    assert any("last_refreshed_at would be NULL" in p for p in sidecar["shared"]["problems"])


async def test_a_part_half_landed_by_a_failed_model_insert_is_completed_by_the_next_run(
    box: Box,
) -> None:
    """The engine runs no transaction around models: documents in, models not."""
    e = box.engine
    e.fail_models.add("hal0:shared")
    first = await box.fold(only=["shared"]).run()
    assert first["left"] == ["shared"] and first["cut_over"] is False
    assert "n1" in e.banks["hal0:shared"]["docs"]
    assert "overview" not in e.banks["hal0:shared"]["models"]
    row = fi.load_ledger()["banks"]["shared"]
    assert row["state"] == fi.LEDGER_LEFT and row["sent_files"]["shared"]
    _assert_on_the_old_layout(box)
    sent = len(e.imports)
    second = await box.fold().run()
    assert second["status"] == "folded" and second["cut_over"] is True
    assert "overview" in e.banks["hal0:shared"]["models"]
    # The resend carried what was missing: the model, not the document again.
    resent = [archive for bank, archive in e.imports[sent:] if bank == "hal0:shared"]
    assert resent
    assert all(d["id"] != "n1" for archive in resent for d in archive["documents"])
    assert any(m["id"] == "overview" for archive in resent for m in archive["mental_models"])


async def test_a_folded_bank_deleted_from_the_engine_refuses_the_cutover(box: Box) -> None:
    """Fourth review, item 2: a deleted old bank's copies must not stay readable unseen."""
    e = box.engine
    first = await box.fold(only=["pi-coder"]).run()
    assert first["folded"] == ["pi-coder"]
    assert "web::pi-coder::p1" in e.banks["hal0:shared"]["docs"]
    del e.banks["pi-coder"]
    dry = await box.fold().preflight()
    assert dry["gone_banks"] == ["pi-coder"]
    assert any("no longer on the engine (pi-coder)" in w for w in dry["warnings"])
    second = await box.fold().run()
    assert second["status"] == "incomplete" and second["cut_over"] is False
    (blocker,) = [b for b in second["blocking"] if b["bank"] == "pi-coder"]
    assert "deleted from the engine" in blocker["reason"]
    assert "no longer in the old bank" in blocker["reason"]
    assert ".ids.json" in blocker["fix"]
    left_ids = Path(second["archive_dir"]) / fi.LEFT_IDS_NAME
    assert str(left_ids) in blocker["reason"]
    assert json.loads(left_ids.read_text())["pi-coder"]["gone"] == ["web::pi-coder::p1"]
    _assert_on_the_old_layout(box)
    # Once the operator deletes the copy, nothing of the bank is left behind.
    del e.banks["hal0:shared"]["docs"]["web::pi-coder::p1"]
    third = await box.fold().run()
    assert third["status"] == "folded" and third["cut_over"] is True


async def test_status_names_the_archive_directories_a_later_run_still_needs(box: Box) -> None:
    box.engine.fail_import.add("hal0:shared")
    first = await box.fold().run()
    assert box.fold().status()["archives_needed"] == [first["archive_dir"]]
    await box.fold().run()
    assert box.fold().status()["archives_needed"] == []


async def test_a_missing_record_of_what_was_sent_refuses_the_bank(box: Box) -> None:
    box.engine.fail_import.add("hal0:shared")
    await box.fold().run()
    for path in fi.load_ledger()["banks"]["coding-agent::web"]["sent_files"]["shared"]:
        Path(path).unlink()
    result = await box.fold().run()
    note = next(b["note"] for b in result["banks"] if b["bank"] == "coding-agent::web")
    assert "cannot be read" in note
    assert result["status"] == "incomplete"


@pytest.mark.parametrize("point", ["before_release", "released"])
async def test_a_run_killed_while_releasing_completes_with_resume(box: Box, point: str) -> None:
    box.engine.banks["hal0:shared"] = empty_bank()
    box.engine.banks["hal0:shared"]["docs"]["n1"] = doc("n1", fact("someone else's"))
    await box.kill_at(point)
    assert write_gate.read_gate() is not None
    result = await box.fold().resume()
    assert result["status"] == "incomplete"
    _assert_on_the_old_layout(box)


# ── the pre-check (second review, items 2, 4, 6, 7) ────────────────────


async def test_the_ledger_and_the_notes_hold_no_memory_id(box: Box) -> None:
    e = box.engine
    # A private document with two owners (left by the rewrite), and a coding
    # document whose id the target already holds (left by the clash check).
    e.banks["shared"]["docs"]["private:hermes:s2"] = doc(
        "private:hermes:s2",
        fact("a", "agent:hermes", "visibility:private"),
        fact("b", "agent:brain", "visibility:private"),
    )
    e.banks["hal0:shared"] = empty_bank()
    taken = "web::shared::git:123"
    e.banks["hal0:shared"]["docs"][taken] = doc(taken, fact("someone else's"))
    result = await box.fold().run()
    assert result["status"] == "incomplete" and e.imports == []
    # The run id is random hex and the notes name left-ids.json by its path;
    # either may spell "b1", so both are masked before the search.
    text = (
        fi.ledger_path()
        .read_text()
        .replace(result["archive_dir"], "<archive dir>")
        .replace(result["run_id"], "<run>")
    )
    for memory_id in ("private:", "s2", "git:123", "conversation:a", "n1", "p1", "b1"):
        assert memory_id not in text, memory_id
    sidecar = json.loads((Path(result["archive_dir"]) / fi.LEFT_IDS_NAME).read_text())
    assert sidecar["shared"]["mixed_owner_documents"][0]["document"] == "private:hermes:s2"
    assert sidecar["coding-agent::web"]["documents"] == [taken]


async def test_a_bank_with_attachments_or_invalidated_facts_is_left_before_any_import(
    box: Box,
) -> None:
    box.engine.manifest_extra["pi-coder"] = {"attachment_count": 2}
    box.engine.manifest_extra["private__brain"] = {"invalidated_memory_count": 1}
    box.engine.model_history["coding-agent::web"] = [{"id": 1}, {"id": 2}]
    result = await box.fold().run()
    assert box.engine.imports == []
    notes = {b["bank"]: b["note"] for b in result["banks"]}
    assert "2 attachment(s), which a merge import does not carry" in notes["pi-coder"]
    assert "1 invalidated fact(s)" in notes["private__brain"]
    box.engine.manifest_extra.clear()
    done = await box.fold().run()
    assert done["status"] == "folded"
    web = next(b for b in done["banks"] if b["bank"] == "coding-agent::web")
    assert web["report"]["mental_model_history_not_carried"] == 2


async def test_two_banks_with_one_document_id_are_both_left_and_named(box: Box) -> None:
    e = box.engine
    e.banks["project__docs"] = empty_bank()
    e.banks["project__docs"]["docs"] = {"n1": doc("n1", fact("same id, other bank"))}
    result = await box.fold().run()
    assert e.imports == []
    notes = {b["bank"]: b["note"] for b in result["banks"]}
    assert "also in project__docs" in notes["shared"]
    assert "also in shared" in notes["project__docs"]
    sidecar = json.loads((Path(result["archive_dir"]) / fi.LEFT_IDS_NAME).read_text())
    assert sidecar["shared"]["ids"] == {"hal0:shared": {"documents": ["n1"]}}


async def test_an_id_an_earlier_run_sent_for_another_bank_is_named_in_the_clash(
    box: Box,
) -> None:
    e = box.engine
    first = await box.fold(only=["shared"]).run()
    assert first["folded"] == ["shared"]
    sent = len(e.imports)
    e.banks["project__docs"] = empty_bank()
    e.banks["project__docs"]["docs"] = {"n1": doc("n1", fact("same id, other bank"))}
    result = await box.fold().run()
    note = next(b["note"] for b in result["banks"] if b["bank"] == "project__docs")
    assert "sent there by an earlier run of shared" in note
    assert len(e.imports) == sent and result["status"] == "incomplete"


@pytest.mark.parametrize("skew", [1, -1])
async def test_an_import_that_lands_more_or_fewer_facts_than_sent_leaves_the_bank(
    box: Box, skew: int
) -> None:
    # Every recorded id is in the target; only the exact growth check sees it.
    box.engine.skew_import["hal0:shared"] = skew
    result = await box.fold().run()
    assert result["status"] == "incomplete"
    assert result["left"] == ["shared"]
    note = next(b["note"] for b in result["banks"] if b["bank"] == "shared")
    assert "hal0:shared grew by" in note
    _assert_on_the_old_layout(box)


async def test_a_refold_rechecks_models_of_a_part_that_sent_no_documents(box: Box) -> None:
    e = box.engine
    e.banks["private__brain"]["docs"] = {}
    e.banks["private__brain"]["models"] = {
        "habits": {"id": "habits", "name": "Habits", "tags": [], "content": "mine"}
    }
    await box.fold(only=["private__brain"]).run()
    assert "brain-habits" in e.banks["hal0:private"]["models"]
    del e.banks["hal0:private"]["models"]["brain-habits"]
    result = await box.fold().run()
    assert result["status"] == "folded"
    brain = next(b for b in result["banks"] if b["bank"] == "private__brain")
    assert "unchanged" not in (brain["note"] or "")
    assert "brain-habits" in e.banks["hal0:private"]["models"]


# ── review items 3, 5, 6, 10 ─────────────────────────────────────────────


async def test_an_observation_lost_on_a_resend_leaves_the_bank(box: Box) -> None:
    e = box.engine
    e.banks["coding-agent::web"]["obs"] = [
        {
            "text": "vite",
            "tags": [],
            "sources": [{"document_id": "conversation:a", "fact_index": 0}],
        }
    ]
    # The first document lands, the import dies before its observation.
    e.fail_import.add("hal0:shared")
    await box.fold().run()
    assert "vite" not in [o["text"] for o in e.banks["hal0:shared"]["obs"]]
    retry = await box.fold(only=["coding-agent::web"]).run()
    assert retry["status"] == "incomplete"
    note = next(b["note"] for b in retry["banks"] if b["bank"] == "coding-agent::web")
    assert "observation(s) rest on 1 document(s) already in hal0:shared" in note
    assert "conversation:a" not in note
    sidecar = json.loads((Path(retry["archive_dir"]) / fi.LEFT_IDS_NAME).read_text())
    assert sidecar["coding-agent::web"]["documents"] == [
        "web::hal0-legacy-claude-code::conversation:a"
    ]
    _assert_on_the_old_layout(box)


async def test_owners_are_frozen_for_every_later_run_of_a_bank(box: Box) -> None:
    box.engine.fail_import.add("hal0:shared")
    await box.fold(owners={"pi": "pi-coder"}).run()
    frozen = fi.load_ledger()["banks"]["coding-agent::web"]
    assert frozen["owners"] == {"pi": "pi-coder"}
    assert frozen["known_clients"] == []
    plan = await box.fold(owners={"claude-code": "laptop"}).preflight()
    assert "owners_frozen" in _codes(plan)
    # A client named after the harness appears between runs: the frozen list
    # still files the bank's documents under the ids the first run gave them.
    box.clients = {"claude-code"}
    result = await box.fold().run()
    assert result["status"] == "folded"
    assert "web::hal0-legacy-claude-code::conversation:a" in box.engine.banks["hal0:shared"]["docs"]
    assert "web::claude-code::conversation:a" not in box.engine.banks["hal0:shared"]["docs"]


async def test_resume_refuses_a_changed_owner_map(box: Box) -> None:
    await box.kill_at("rewritten:shared")
    with pytest.raises(fi.FoldInRefused, match="keeps the owners"):
        await box.fold(owners={"claude-code": "laptop"}).resume()
    await box.fold().resume()
    _assert_folded(box)


async def test_a_private_banks_mental_model_goes_private_for_its_agent(box: Box) -> None:
    box.engine.banks["private__brain"]["models"] = {
        "habits": {"id": "habits", "name": "Habits", "tags": [], "content": "mine"}
    }
    await box.fold().run()
    models = box.engine.banks["hal0:private"]["models"]
    assert set(models) == {"brain-habits"}
    assert {"agent:brain", "visibility:private"} <= set(models["brain-habits"]["tags"])
    assert "brain-habits" not in box.engine.banks["hal0:shared"]["models"]


async def test_a_page_that_did_not_land_with_its_model_is_sent_again(box: Box) -> None:
    e = box.engine
    e.banks["coding-agent::web"]["pages"] = [
        {"id": "kf", "kind": "folder", "name": "Web", "parent_id": None},
        {
            "id": "kp",
            "kind": "page",
            "name": "Overview",
            "parent_id": "kf",
            "mental_model_id": "overview",
        },
    ]
    e.fail_pages.add("hal0:shared")
    first = await box.fold().run()
    assert first["left"] == ["coding-agent::web"]
    assert "web-overview" in e.banks["hal0:shared"]["models"]
    assert e.banks["hal0:shared"]["pages"] == []
    before = len(e.imports)
    second = await box.fold(only=["coding-agent::web"]).run()
    assert second["status"] == "folded"
    (resend,) = [a for t, a in e.imports[before:] if t == "hal0:shared"]
    assert resend["documents"] == []
    assert [p["id"] for p in resend["knowledge_pages"]] == ["kf", "kp"]
    pages = e.banks["hal0:shared"]["pages"]
    assert [p["mental_model_id"] for p in pages if p["kind"] == "page"] == ["web-overview"]


async def test_the_routing_check_on_disk_refuses_a_tampered_archive(
    box: Box, monkeypatch: pytest.MonkeyPatch
) -> None:
    journal = await box.kill_at("rewritten:shared")
    entry = next(b for b in journal.banks if b.bank == "shared")
    # Rebuild the platform bank's parts with the private split broken, as a
    # bug in the rewrite would, and put them where the run will read them.
    archive_dir = Path(journal.archive_dir)
    source = (archive_dir / entry.archive).read_bytes()
    with monkeypatch.context() as m:
        m.setattr(fi, "_document_private", lambda _plan, _doc: False)
        m.setattr(fi, "routing_problems", lambda *_a, **_k: [])
        m.setattr(fi, "shared_archive_problems", lambda _d: [])
        m.setattr(fi, "private_archive_problems", lambda _d: [])
        bad = fi.rewrite_archive(source, box.fold()._rewrite_plan(entry, journal))
    shared_part = entry.part("shared")
    assert shared_part is not None
    (archive_dir / shared_part.archive).write_bytes(bad.shared)
    (archive_dir / (entry.archive[: -len(".zip")] + ".provenance.json")).write_text(
        json.dumps(bad.provenance)
    )
    result = await box.fold().resume()
    note = next(b["note"] for b in result["banks"] if b["bank"] == "shared")
    assert "do not keep its private memory on the private side" in note
    # Caught by the pre-check, so no bank of the run was imported at all.
    assert box.engine.imports == []
    assert not any(
        "private:hermes:s1" in json.dumps(a) or "my secret" in json.dumps(a)
        for t, a in box.engine.imports
        if t == "hal0:shared"
    )


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


# ── a pause left behind (second review, item 3) ────────────────────────


@pytest.mark.parametrize("cut_over", [True, False])
async def test_a_kill_between_retiring_the_journal_and_lifting_the_gate_is_cleared(
    box: Box, cut_over: bool
) -> None:
    if not cut_over:
        box.engine.orphans["pi-coder"] = 1

    def hook(reached: str) -> None:
        if reached == "retired":
            raise Killed

    with pytest.raises(Killed):
        await box.fold(hook=hook).run()
    # The journal is retired and the pause is still set: stuck, before the fix.
    assert fi.load_journal() is None
    assert write_gate.read_gate()["reason"] == write_gate.REASON_FOLD_IN
    assert "gate_set" in _codes(await box.fold().preflight())
    assert "--clear-gate" in (fi.blocking_reason() or "")
    box.privileged = False
    with pytest.raises(fi.FoldInRefused, match="root"):
        box.fold().clear_gate()
    box.privileged = True
    cleared = await box.fold().resume()
    assert cleared["status"] == "gate_cleared" and cleared["cut_over"] is cut_over
    assert write_gate.read_gate() is None
    assert fi.blocking_reason() is None
    assert box.layout().two_bank is cut_over


async def test_clear_gate_refuses_a_pause_no_finished_run_accounts_for(box: Box) -> None:
    with pytest.raises(fi.FoldInRefused, match="not paused by a fold-in"):
        box.fold().clear_gate()
    await box.kill_at("rewritten:shared")
    with pytest.raises(fi.FoldInRefused, match="--resume"):
        box.fold().clear_gate()
    await box.fold().resume()
    write_gate.engage_gate(reason=write_gate.REASON_FOLD_IN, run_id="someone-else")
    with pytest.raises(fi.FoldInRefused, match="the pause stays"):
        box.fold().clear_gate()
    assert write_gate.read_gate() is not None


# ── status ──────────────────────────────────────────────────────────────


async def test_status_reads_the_journal_and_the_record(box: Box) -> None:
    assert box.fold().status()["summary"] == "no fold-in is in progress"
    await box.kill_at("rewritten:shared")
    status = box.fold().status()
    assert status["journal"]["phase"] == fi.PHASE_FOLDING
    assert status["gate"]["reason"] == write_gate.REASON_FOLD_IN
    await box.fold().resume()
    assert "4 old bank(s) folded in" in box.fold().status()["summary"]


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
