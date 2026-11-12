"""``hal0 memory embedder switch`` (#3518, ADR-0058 decision 2).

The engine is an ``httpx.MockTransport`` behind the real
``HindsightRestClient``; it keeps banks in memory and answers the routes the
switch uses the way hindsight-api 1.2.0 does (shapes read from its OpenAPI).
Its restore keeps knowledge-node and directive ids exactly when the real one
does (``engine/transfer/importer.py``): into the archive's own bank id, with
no other bank holding them (#3597). The host side (drop-in write and restart,
the engine's live environment) is injected, and every step lands in one
ordered event list.

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

#: The unit the installer ships, as ``systemctl cat`` prints it.
SHIPPED_UNIT_PATH = Path(__file__).resolve().parents[2] / "installer/systemd/hindsight-api.service"
SHIPPED_UNIT = "# /etc/systemd/system/hindsight-api.service\n" + SHIPPED_UNIT_PATH.read_text(
    encoding="utf-8"
)
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
    delete_s=5.0,
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
        self.api_version = "1.2.0"
        #: An exception type raised for a request to the normal port while the
        #: engine is elsewhere: something there accepts the connection.
        self.normal_port_raises: type[httpx.HTTPError] | None = None
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
        #: Banks whose restore does not finish at once: it stays ``processing``
        #: for ``stuck_polls`` reads of its operation (``None``: until a test
        #: calls :meth:`finish`), then writes its bank.
        self.stuck_restore: set[str] = set()
        self.stuck_polls: int | None = None
        #: Banks whose restore writes the right counts but the wrong content.
        self.lossy_restore: set[str] = set()
        self.lossy_everywhere: set[str] = set()
        #: Engine restarts so far; the process holding the port changes with each.
        self.restarts = 0
        self.writes: list[tuple[str, str]] = []

    # ── what a test does to the engine behind the switch's back ──

    def m(self, bank: str) -> dict[str, Any]:
        return self.meta.setdefault(
            bank,
            {
                "rev": 0,
                "mm": "",
                "dir": "",
                "config": {},
                "ops": [],
                "profile": 0,
                "ids": bank,
                "text": bank,
            },
        )

    def ids(self, bank: str) -> dict[str, list[str]]:
        """The knowledge-node and directive ids the engine shows for ``bank``."""
        token, c = self.m(bank)["ids"], self.banks[bank]
        return {
            "knowledge": [f"kp-{i}-{token}" for i in range(c["mental_models"])],
            "directives": [f"d-{i}-{token}" for i in range(c["directives"])],
        }

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

    def finish(self, op_id: str, status: str = "completed") -> None:
        """A running restore's worker reaches its end and writes its bank.

        Its last status write is suppressed when the row says ``cancelled``,
        as hindsight-api 1.2.0 guards every worker status write.
        """
        op = self.ops[op_id]
        op.pop("land")()
        op.pop("polls_left", None)
        if op["status"] != "cancelled":
            op["status"] = status

    def restart(self) -> None:
        """The unit restarts: a cancelled restore's worker dies with it."""
        self.restarts += 1
        for op in self.ops.values():
            if op.get("status") == "cancelled" and "land" in op:
                op.pop("land")
                op.pop("polls_left", None)

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
            # What the rows carry verbatim: definitions, directive text, config, and ids.
            m = self.m(bank)
            zf.writestr(
                "fake_rows.json",
                json.dumps(
                    {
                        **{k: m[k] for k in ("mm", "dir", "config", "ids", "text")},
                        "mm_content": m.get("mm_content", ""),
                        "mm_refreshed": m.get("mm_refreshed"),
                        "no_text": m.get("no_text", False),
                    }
                ),
            )
        return buf.getvalue()

    @staticmethod
    def _unzip(body: bytes) -> tuple[str, dict[str, Any], dict[str, int]]:
        """Source bank, verbatim rows and counts of the archive inside a multipart upload."""
        with zipfile.ZipFile(io.BytesIO(body)) as zf:
            manifest = json.loads(zf.read("manifest.json"))
            models = len(json.loads(zf.read("mental_models.json")))
            rows = json.loads(zf.read("fake_rows.json"))
        return (
            manifest["source_bank_id"],
            rows,
            {
                "facts": manifest["fact_count"],
                "observations": manifest["observation_count"],
                "mental_models": models,
                "directives": manifest["directive_count"],
                "documents": manifest["document_count"],
            },
        )

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
        if (
            self.normal_port_raises is not None
            and request.url.port == NORMAL_PORT
            and self.port != NORMAL_PORT
        ):
            raise self.normal_port_raises("accepted, then nothing", request=request)
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
            return httpx.Response(
                200, json={"api_version": self.api_version, "features": self.features}
            )
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
            op_id = rest.rsplit("/", 1)[-1]
            op = self.ops.get(op_id)
            if method == "DELETE":
                # Like hindsight-api 1.2.0: the row says cancelled at once, but
                # a restore never checks it, so its worker goes on writing.
                self.events.append(f"cancel:{bank}")
                if op is not None and op["status"] in ("pending", "processing"):
                    op["status"] = "cancelled"
                return httpx.Response(200, json={"success": True})
            if op is not None and op.get("held") and self.hold_restore is not None:
                await self.hold_restore.wait()
            if op is not None and "land" in op:
                self.events.append(f"poll-running:{bank}")
                if op["polls_left"] is not None:
                    op["polls_left"] -= 1
                    if op["polls_left"] <= 0:
                        self.finish(op_id)
            public = {k: v for k, v in (op or {}).items() if k not in ("land", "polls_left")}
            return httpx.Response(200, json=public or {"status": "not_found"})
        if rest == "" and method == "PUT":
            self.events.append(f"create:{bank}")
            self.banks.setdefault(bank, _counts(0, docs=0))
            return httpx.Response(200, json={"bank_id": bank})
        if rest == "/transfer/import" and method == "POST":
            assert params["mode"] == "restore"
            target = params["target_bank_id"]
            source, rows, counts = self._unzip(request.content)
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
            # ``lossy_restore`` loses content only into the bank's own id
            # (what a restore through the scratch bank alone gets wrong);
            # ``lossy_everywhere`` into the rebuilt copy too.
            lossy = source in self.lossy_everywhere or (
                source in self.lossy_restore and target == source
            )

            def land() -> None:
                self.banks[target] = counts
                # Knowledge-node ids are unique across the schema and directive
                # ids are dropped for another bank id, so they survive only a
                # restore into the archive's own id that nothing else holds.
                held = {meta["ids"] for b, meta in self.meta.items() if b != target}
                keep = target == source and rows["ids"] not in held
                restored = self.m(target)
                restored.update({k: rows[k] for k in ("mm", "dir", "config", "mm_refreshed")})
                restored["mm_content"] = "lost" if lossy else rows["mm_content"]
                restored["text"] = f"lost-{rows['text']}" if lossy else rows["text"]
                restored["ids"] = rows["ids"] if keep else f"new-{op_id}"
                restored["no_text"] = rows.get("no_text", False)
                restored["hash_epoch"] = op_id

            stuck = source in self.stuck_restore
            self.ops[op_id] = {
                "status": "processing" if stuck else "completed",
                "held": self.hold_restore is not None,
            }
            self.m(bank)["ops"].insert(0, op_id)
            if stuck:
                self.ops[op_id].update(land=land, polls_left=self.stuck_polls)
            else:
                land()
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
        if rest == "/documents":
            meta = self.m(bank)
            no_text = meta.get("no_text", False)
            rows = [
                {
                    "id": f"doc-{i}",
                    "bank_id": bank,
                    # Recomputed by a restore from the text it stores: with no
                    # text stored, the original keeps the hash of a text it
                    # does not hold and the restored row hashes "".
                    "content_hash": f"h-{i}-{meta['text']}-{meta.get('hash_epoch', 0)}"
                    if no_text
                    else f"h-{i}-{meta['text']}",
                    # LENGTH(original_text), 0 when none is stored.
                    "text_length": 0 if no_text else len(f"document {i} of {meta['text']}"),
                }
                for i in range(self.banks[bank]["documents"])
            ]
            return self._page("items", rows, params)
        if rest == "/memories/list":
            c, text = self.banks[bank], self.m(bank)["text"]
            world = c["facts"] // 2
            n = world if params["type"] == "world" else c["facts"] - world
            rows = [
                {
                    "id": f"{params['type']}-{i}-{self.m(bank)['rev']}",
                    "text": f"fact {i} of {text}",
                    "fact_type": params["type"],
                    "document_id": "doc-0",
                    "tags": ["b", "a"],
                }
                for i in range(n)
            ]
            return self._page("items", rows, params)
        if rest == "/directives":
            assert params.get("active_only") == "false", "inactive directives count too"
            rows = [
                {"id": did, "bank_id": bank, "content": self.m(bank)["dir"]}
                for did in self.ids(bank)["directives"]
            ]
            return self._page("items", rows, params)
        if rest == "/knowledge-base/tree":
            folder = f"kf-{self.m(bank)['ids']}"
            pages = [
                {"id": pid, "kind": "page", "name": f"page {pid[:4]}", "parent_id": folder}
                for pid in self.ids(bank)["knowledge"]
            ]
            roots = [{"id": folder, "kind": "folder", "name": "F", "children": pages}]
            return httpx.Response(200, json={"roots": roots if pages else []})
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
        #: The port in the isolation drop-in, as written; read at the next restart.
        self.dropin_port: int | None = None
        #: A drop-in that sorts after hal0's and replaces ExecStart: isolation does not take.
        self.exec_start_pinned = False
        self.isolate_error: str | None = None
        #: When set, a restart to the engine's normal address fails with this.
        self.release_restart_error: str | None = None
        #: ``systemctl cat`` of the engine's unit.
        self.unit: str | None = SHIPPED_UNIT
        #: port -> the process that holds it instead of the engine.
        self.foreign: dict[int, str] = {}
        #: port -> what the listener check says, overriding everything else.
        self.listener_says: dict[int, dict[str, Any]] = {}
        self.unit_stopped = False
        self.stop_error: str | None = None
        #: When false, ``systemctl show -p InvocationID`` answers ``invocation_says``.
        self.invocation_known = True
        self.invocation_says: str | None = None

    def listener(self, port: int) -> dict[str, Any]:
        if port in self.listener_says:
            return self.listener_says[port]
        if port in self.foreign:
            return {"owner": "other", "holder": self.foreign[port], "pid": 4242}
        if self.engine.reachable and self.engine.port == port:
            pid = 100 + self.engine.restarts
            return {"owner": "unit", "holder": f"pid {pid} (hindsight-api)", "pid": pid}
        return {"owner": "none", "holder": None, "pid": None}

    def unit_text(self) -> str | None:
        return self.unit

    def invocation(self) -> str | None:
        """systemd's InvocationID: new on every start of the unit."""
        return f"inv-{self.engine.restarts}" if self.invocation_known else self.invocation_says

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

    def stop_unit(self) -> dict[str, Any]:
        self.events.append("stop-unit")
        if self.stop_error:
            return {"error": self.stop_error}
        self.unit_stopped = True
        self.engine.reachable = False
        return {}

    def apply_dropin(self, embedder: Embedder) -> dict[str, Any]:
        self.events.append(f"engine:{embedder.model}")
        if embedder.model in self.dropin_error:
            return {"error": self.dropin_error[embedder.model]}
        if self.dropin_port is None and self.release_restart_error:
            return {"error": self.release_restart_error}
        self.live = self.override or embedder
        self.engine.restart()
        self.unit_stopped = False
        self.engine.reachable = True
        self.engine.healthy = embedder.model not in self.dead_on
        private = self.dropin_port is not None and not self.exec_start_pinned
        self.engine.port = self.dropin_port if private else NORMAL_PORT
        return {"error": None, "written": True, "restarted": True}

    def evidence(self) -> dict[str, Any]:
        pair = self.live._asdict()
        return {"live": pair, "on_disk": pair, "on_disk_source": "unit"}


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
        kw.setdefault("listener", self.host.listener)
        kw.setdefault("unit_text", self.host.unit_text)
        kw.setdefault("unit_invocation", self.host.invocation)
        return es.EmbedderSwitch(
            self.client(f"http://127.0.0.1:{NORMAL_PORT}"),
            target=target,
            configured=HS,
            apply_dropin=self.host.apply_dropin,
            isolate=self.host.isolate,
            release=self.host.release,
            stop_unit=self.host.stop_unit,
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
    # What a run costs: three engine restarts, an export and two restores per
    # bank, and (189 facts + 50 observations) embedded twice at the measured
    # 60 per second.
    assert plan["estimate"] == {"restarts_s": 75, "per_bank_s": 9, "embedding_s": 8}
    assert plan["estimate_s"] == 92
    # It says which ids the switch keeps and which it cannot (#3597).
    assert "knowledge-base folder and page ids (kf-..., kp-...)" in plan["ids"]["kept"]
    assert "directive ids" in plan["ids"]["kept"]
    assert plan["ids"]["changed"] == ["fact ids: the engine gives every restored fact a new id"]
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
    scratch = es.scratch_bank_id(result["run_id"])
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
        # Then each original is deleted and its archive restored again, into
        # its own id (the one restore that keeps its ids), and the rebuilt
        # copy goes only once that matches.
        "delete:alpha",
        f"create:{scratch}",
        f"restore:alpha->alpha via {scratch}",
        f"delete:{temps['alpha']}",
        *(
            e
            for b in ORIGINALS[1:]
            for e in (f"delete:{b}", f"restore:{b}->{b} via {scratch}", f"delete:{temps[b]}")
        ),
        f"delete:{scratch}",
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
    assert [b["step"] for b in done["banks"]] == [es.STEP_PLACED] * 3


async def test_every_knowledge_page_and_directive_keeps_its_id(box: Box) -> None:
    """#3597: agents, the coding-agents plugin and dashboard links hold these ids."""
    before = {b: box.engine.ids(b) for b in ORIGINALS}
    assert before["alpha"]["knowledge"] and before["Hal0::Beta"]["directives"]

    result = await box.switch().run()

    assert {b: box.engine.ids(b) for b in ORIGINALS} == before
    assert result["status"] == "switched" and not box.temps()


async def test_the_rebuilt_copy_does_not_keep_the_ids_which_is_why_it_is_not_kept(
    box: Box,
) -> None:
    """The engine mints new knowledge-node and directive ids for a copy beside its original."""
    journal = await box.kill_at("before_swap")
    alpha, beta = journal.banks[0], journal.banks[1]
    copy, original = box.engine.ids(alpha.temp), box.engine.ids(alpha.bank)
    assert set(copy["knowledge"]).isdisjoint(original["knowledge"])
    copy, original = box.engine.ids(beta.temp), box.engine.ids(beta.bank)
    assert set(copy["directives"]).isdisjoint(original["directives"])


async def test_a_bank_that_comes_back_with_other_ids_is_not_accepted(box: Box) -> None:
    """An engine that remapped ids on the restore into the own id: stop, keep the copy."""
    real = box.engine.handler

    async def remapping(request: httpx.Request) -> httpx.Response:
        resp = await real(request)
        target = dict(request.url.params).get("target_bank_id")
        if request.url.path.endswith("/transfer/import") and target == "Hal0::Beta":
            box.engine.m(target)["ids"] = "remapped"
        return resp

    box.engine.handler = remapping  # type: ignore[method-assign]

    with pytest.raises(es.SwitchError) as err:
        await box.switch().run()

    assert err.value.resumable
    assert "differs from the original as it was archived: directives" in err.value.message
    journal = es.load_journal()
    assert journal is not None
    beta = journal.banks[1]
    assert beta.step == es.STEP_PLACING and beta.temp in box.engine.banks, "the copy is kept"
    assert write_gate.read_gate() is not None, "writes stay paused"


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


async def test_rollback_waits_for_a_running_restore_and_never_cancels_it(box: Box) -> None:
    """A cancel would mark the restore ended while its worker still writes its bank."""
    box.engine.stuck_restore.add("alpha")
    box.engine.stuck_polls = 10  # outlives the restore's own budget, ends in the rollback's

    with pytest.raises(es.SwitchError) as err:
        await box.switch().run()

    assert "did not finish within" in err.value.message
    assert not any(e.startswith("cancel:") for e in box.events), "a restore is never cancelled"
    polls = [i for i, e in enumerate(box.events) if e == "poll-running:alpha"]
    assert len(polls) == 10
    temp = next(e.split(":", 1)[1] for e in box.events if e.startswith("delete:alpha.switch-"))
    assert box.events.index(f"delete:{temp}") > polls[-1], "swept only once it had ended"
    _assert_rolled_back(box)


async def test_a_restore_that_does_not_end_keeps_the_pause_and_resume_waits_again(
    box: Box,
) -> None:
    box.engine.stuck_restore.add("alpha")

    with pytest.raises(es.SwitchError) as err:
        await box.switch().run()

    assert err.value.resumable and "is still processing after" in err.value.message
    assert "does not cancel a restore" in err.value.message
    assert "--resume` again" in err.value.message
    assert not any(e.startswith(("cancel:", "delete:")) for e in box.events)
    assert write_gate.read_gate() is not None
    journal = es.load_journal()
    assert journal is not None and journal.phase == es.PHASE_ROLLBACK_FAILED
    op = journal.banks[0].restore_op
    assert op is not None and op["target"] == journal.banks[0].temp

    # The worker gets to its end; the next resume finds it ended and finishes the rollback.
    box.engine.finish(op["id"])
    result = await box.switch(None).resume()

    assert result["status"] == "rolled_back"
    _assert_rolled_back(box)


async def test_a_rollback_that_cannot_finish_keeps_writes_paused_until_resume(box: Box) -> None:
    box.engine.restore_delta["alpha"] = {"facts": -1}
    box.host.dropin_error[BGE.model] = "systemctl restart hindsight-api.service failed"

    with pytest.raises(es.SwitchError) as err:
        await box.switch().run()

    assert err.value.resumable and "the rollback failed too" in err.value.message
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
        es.STEP_PLACED,
        es.STEP_RESTORED,
        es.STEP_RESTORED,
    ]

    # The operator stops the writer and resumes: Beta is exported and rebuilt
    # again from what it holds now, then the swap carries on.
    before = len(box.events)
    result = await box.switch(None).resume()

    beta = journal.banks[1].temp
    scratch = es.scratch_bank_id(journal.run_id)
    assert box.events[before:][:6] == [
        f"delete:{beta}",
        "export:Hal0::Beta",
        f"restore:Hal0::Beta->{beta}",
        "delete:Hal0::Beta",
        f"restore:Hal0::Beta->Hal0::Beta via {scratch}",
        f"delete:{beta}",
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
    real_restore = es.EmbedderSwitch._restore

    async def restore(
        self: es.EmbedderSwitch, journal: es.Journal, entry: es.BankEntry, target: str, **kw: Any
    ) -> dict[str, Any]:
        if target == "alpha":
            seen["at_place"] = es.load_journal().banks[0].step  # type: ignore[union-attr]
        return await real_restore(self, journal, entry, target, **kw)

    switch._restore = restore.__get__(switch)  # type: ignore[method-assign]

    await switch.run()

    assert seen["at_delete"] == es.STEP_DELETING and seen["mirror_at_delete"] == es.STEP_DELETING
    assert seen["deleted:alpha"] == es.STEP_DELETED
    assert seen["at_place"] == es.STEP_PLACING
    assert seen["placed:alpha"] == es.STEP_PLACED


SWAP_POINTS = [
    "before_swap",
    "before_delete:Hal0::Beta",
    "deleted:Hal0::Beta",
    "before_place:Hal0::Beta",
    "placed:Hal0::Beta",
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


async def _stuck_placing_beta(box: Box) -> es.Journal:
    """A run whose restore of Hal0::Beta into its own id is still running when it stops."""
    box.engine.stuck_restore.add("Hal0::Beta")
    real_restore = box.engine.on_restore
    calls: list[str] = []

    def count(source: str) -> None:
        calls.append(source)
        if source == "Hal0::Beta" and calls.count(source) == 1:
            # The rebuilt copy goes through; the restore into the own id hangs.
            box.engine.stuck_restore.discard(source)
        elif source == "Hal0::Beta":
            box.engine.stuck_restore.add(source)
        if real_restore is not None:
            real_restore(source)

    box.engine.on_restore = count

    with pytest.raises(es.SwitchError) as err:
        await box.switch().run()

    assert err.value.resumable and err.value.bank == "Hal0::Beta"
    box.engine.stuck_restore.clear()
    box.engine.on_restore = real_restore
    journal = es.load_journal()
    assert journal is not None
    return journal


async def test_a_failure_in_the_middle_of_the_swap_is_finished_by_resume(box: Box) -> None:
    journal = await _stuck_placing_beta(box)
    assert journal.phase == es.PHASE_SWAPPING
    assert {b.bank: b.step for b in journal.banks} == {
        "alpha": es.STEP_PLACED,
        "Hal0::Beta": es.STEP_PLACING,
        "gamma": es.STEP_RESTORED,
    }
    beta, gamma = journal.banks[1], journal.banks[2]
    assert beta.restore_op is not None and beta.restore_op["target"] == "Hal0::Beta"
    assert beta.temp in box.engine.banks, "the rebuilt copy is kept while its bank is missing"
    assert write_gate.read_gate() is not None and "gate:off" not in box.events
    assert box.host.live == HS
    assert box.engine.port == PRIVATE_PORT, "the engine stays private while the run is resumable"
    assert "delete:gamma" not in box.events, "the swap stops at the first failure"
    assert not any(e.startswith("cancel:") for e in box.events)

    # The restore the run gave up on gets to its end and writes Hal0::Beta.
    box.engine.finish(beta.restore_op["id"])
    before = len(box.events)
    result = await box.switch(None).resume()

    scratch = es.scratch_bank_id(journal.run_id)
    assert box.events[before:] == [
        f"delete:{beta.temp}",
        "delete:gamma",
        f"restore:gamma->gamma via {scratch}",
        f"delete:{gamma.temp}",
        f"delete:{scratch}",
        "release",
        f"engine:{HS.model}",
        "gate:off",
    ]
    assert box.engine.port == NORMAL_PORT
    assert result["status"] == "switched"
    _assert_switched(box)


async def test_resume_waits_for_a_restore_into_the_own_id_that_still_runs(box: Box) -> None:
    """The race the old cancel made: a second restore into the id the first still writes."""
    journal = await _stuck_placing_beta(box)
    op = journal.banks[1].restore_op
    assert op is not None
    before = len(box.events)

    with pytest.raises(es.SwitchError) as err:
        await box.switch(None).resume()

    assert err.value.resumable and err.value.bank == "Hal0::Beta"
    assert f"operation {op['id']}) is still processing" in err.value.message
    assert not any(e.startswith(("cancel:", "delete:", "restore:")) for e in box.events[before:]), (
        "nothing is cancelled, deleted or restored while it runs"
    )
    assert es.load_journal().banks[1].restore_op == op  # type: ignore[union-attr]

    box.engine.finish(op["id"])
    result = await box.switch(None).resume()

    assert result["status"] == "switched"
    restores = [e for e in box.events if e.startswith("restore:Hal0::Beta->Hal0::Beta via ")]
    assert len(restores) == 1, "the restore that was running is the one kept"
    _assert_switched(box)


async def test_a_restore_someone_else_cancelled_is_trusted_ended_only_after_a_restart(
    box: Box,
) -> None:
    journal = await _stuck_placing_beta(box)
    op = journal.banks[1].restore_op
    assert op is not None
    scratch = es.scratch_bank_id(journal.run_id)
    # An operator cancels it by hand: the row says cancelled, the worker goes on.
    async with box.http(f"http://127.0.0.1:{PRIVATE_PORT}") as http:
        await http.delete(f"/v1/default/banks/{scratch}/operations/{op['id']}")
    before = len(box.events)

    with pytest.raises(es.SwitchError) as err:
        await box.switch(None).resume()

    assert "recorded as cancelled" in err.value.message
    assert "systemctl restart hindsight-api.service" in err.value.message
    assert not any(e.startswith(("delete:", "restore:")) for e in box.events[before:])

    box.host.apply_dropin(HS)  # the operator restarts the engine: that worker is gone
    result = await box.switch(None).resume()

    assert result["status"] == "switched"
    assert "restore:Hal0::Beta->Hal0::Beta via " + scratch in box.events[before:]
    _assert_switched(box)


async def test_a_half_finished_restore_into_the_own_id_is_done_again(box: Box) -> None:
    """Killed while the bank was being restored into its own id: what is there is not trusted."""
    journal = await box.kill_at("before_place:alpha")
    alpha = journal.banks[0]
    box.engine.create("alpha", _counts(30))  # the engine had written part of it
    box.engine.m("alpha")["ids"] = alpha.bank
    journal.banks[0].step = es.STEP_PLACING
    box.switch()._save(journal)
    before = len(box.events)

    await box.switch(None).resume()

    scratch = es.scratch_bank_id(journal.run_id)
    assert box.events[before:][:3] == [
        "delete:alpha",
        f"restore:alpha->alpha via {scratch}",
        f"delete:{alpha.temp}",
    ]
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
    assert not any(e.startswith(("restore:", "delete:")) for e in box.events[before:])
    assert write_gate.read_gate() is not None


async def test_the_engine_is_checked_again_before_every_restore(box: Box) -> None:
    def drift(source: str) -> None:
        if source == "alpha" and not seen:
            seen.append(source)
            box.host.live = BGE  # moved under the run after the first restore

    seen: list[str] = []

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
    # The engine records the restore against the URL bank, which must exist,
    # while the target must not: the URL is a scratch bank.
    assert after[:2] == [f"create:{scratch}", f"restore:alpha->alpha via {scratch}"]
    assert f"delete:{scratch}" in after, "the scratch bank does not outlive the run"
    _assert_switched(box)


async def test_a_bank_journalled_as_placed_but_missing_is_restored_again(box: Box) -> None:
    """An engine rollback to an older snapshot undid a placement the journal recorded."""
    journal = await box.kill_at("before_finish")
    alpha = journal.banks[0]
    assert alpha.step == es.STEP_PLACED
    box.engine.drop("alpha")
    before = len(box.events)

    await box.switch(None).resume()

    assert f"restore:alpha->alpha via {es.scratch_bank_id(journal.run_id)}" in box.events[before:]
    _assert_switched(box)


async def test_a_bank_that_is_already_final_is_not_verified_again_on_resume(box: Box) -> None:
    """Placed, under its own id, no temporary id left: checked for existence only.

    It was verified when it was placed. Checking it again on every resume
    would make a resume fail the same way for ever once anything about the
    bank moved, with the gate set and no way forward."""
    await box.kill_at("before_finish")
    box.engine.banks["alpha"]["facts"] -= 7  # whatever happened, it is not this run's to judge

    result = await box.switch(None).resume()

    assert result["status"] == "switched"
    assert write_gate.read_gate() is None and es.interrupted_switch() is None
    _assert_public(box)


async def test_an_original_id_found_alone_after_a_delete_is_checked_not_assumed(box: Box) -> None:
    """Step ``deleted``, rebuilt bank gone, original id present: whose is it?"""
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


async def test_a_placement_that_finished_before_the_journal_said_so_is_accepted(
    box: Box,
) -> None:
    journal = await box.kill_at("placed:alpha")
    journal.banks[0].step = es.STEP_PLACING  # killed before the save that said so
    box.switch()._save(journal)
    before = len(box.events)

    await box.switch(None).resume()

    assert not any(e.endswith("->alpha") or e == "delete:alpha" for e in box.events[before:])
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
    assert not any(e.startswith(("delete:", "restore:")) for e in box.events[before:])
    assert journal.banks[0].temp in box.engine.banks
    assert write_gate.read_gate() is not None


async def test_resume_puts_back_a_pause_that_was_removed_by_hand(box: Box) -> None:
    await box.kill_at("deleted:Hal0::Beta")
    write_gate.gate_path().unlink()
    before = len(box.events)

    await box.switch(None).resume()

    assert box.events[before] == "gate:on", "paused again before the first delete or restore"
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
    journal = await box.kill_at("placed:alpha")
    es.journal_path().unlink()

    found = es.locate_journal()
    assert found["journal"] is not None and found["journal"].run_id == journal.run_id
    assert found["journal"].banks[0].step == es.STEP_PLACED
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
    """Entries at ``deleted`` and ``placed`` when every copy of the journal is gone."""
    journal = await _lose_every_copy(box, "deleted:Hal0::Beta")
    es.journal_path().unlink()
    beta, gamma = journal.banks[1].temp, journal.banks[2].temp
    before = len(box.events)

    with pytest.raises(es.SwitchRefused) as err:
        await box.switch(None).resume()

    message = err.value.message
    assert "but no journal of a switch was found" in message
    assert "Banks on the engine: alpha, gamma." in message
    scratch = es.scratch_bank_id(journal.run_id)
    assert f"temporary ids from a switch: {beta}, {gamma}, {scratch}." in message
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
        "stopped_by_switch": False,
        "port_holder": None,
    }
    assert f"isolated on 127.0.0.1:{PRIVATE_PORT}" in status["summary"]
    roles = {b["bank"]: b["role"] for b in status["banks"]}
    assert roles[journal.banks[1].temp] == "temporary (rebuilt copy of Hal0::Beta)"
    assert roles["alpha"] == "original or rebuilt"
    plan = {r["bank"]: r["action"] for r in status["resume"]}
    assert plan == {
        "alpha": es.ACTION_FINAL,
        "Hal0::Beta": es.ACTION_PLACE,
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
        # The delete may have been sent: never deleted again, never exported again.
        (es.STEP_DELETING, True, True, es.ACTION_DELETE_SENT),
        (es.STEP_DELETING, True, False, es.ACTION_DELETE_SENT),
        (es.STEP_RESTORED, True, False, es.ACTION_REBUILD),
        # The original is gone: its archive puts it back under its own id.
        (es.STEP_DELETING, False, True, es.ACTION_PLACE),
        (es.STEP_DELETED, False, True, es.ACTION_PLACE),
        (es.STEP_RESTORED, False, False, es.ACTION_PLACE),
        (es.STEP_DELETED, False, False, es.ACTION_PLACE),
        # ``placing`` is saved before the restore is sent: a bank under the own
        # id is this run's, whole or not.
        (es.STEP_PLACING, True, True, es.ACTION_CHECK_PLACED),
        (es.STEP_PLACING, True, False, es.ACTION_CHECK_PLACED),
        (es.STEP_PLACING, False, True, es.ACTION_PLACE),
        (es.STEP_PLACING, False, False, es.ACTION_PLACE),
        (es.STEP_DELETED, True, False, es.ACTION_VERIFY),
        (es.STEP_DELETED, True, True, es.ACTION_RECREATED),
        (es.STEP_PLACED, True, True, es.ACTION_DROP_COPY),
        (es.STEP_PLACED, True, False, es.ACTION_FINAL),
        # A journal ahead of the engine (the engine went back to an older snapshot).
        (es.STEP_PLACED, False, True, es.ACTION_PLACE),
        (es.STEP_PLACED, False, False, es.ACTION_PLACE),
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
    assert set(where.values()) == {PRIVATE_PORT}, "private from the bank list to the last restore"
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
    assert "could not read the engine's unit" in out["error"] and not path.exists()

    def empty(argv: list[str], **kw: Any) -> subprocess.CompletedProcess[str]:
        return subprocess.CompletedProcess(argv, 0, "[Service]\nExecStart=\n", "")

    out = es.write_isolation_dropin(45123, "x", runner=empty, path=path)
    assert "could not find an ExecStart" in out["error"] and not path.exists()

    def shell(argv: list[str], **kw: Any) -> subprocess.CompletedProcess[str]:
        return subprocess.CompletedProcess(argv, 0, SHIPPED_UNIT + SHELL_DROPIN, "")

    out = es.write_isolation_dropin(45123, "x", runner=shell, path=path)
    assert "through a shell" in out["error"] and not path.exists()


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


# ── isolation proofs: the normal address, the private port, the unit ────────

SHIPPED_EXEC = "/var/lib/hal0/memory/hindsight/.venv/bin/hindsight-api --host 127.0.0.1 --port 9177"
HS_BIN = "/var/lib/hal0/memory/hindsight/.venv/bin/hindsight-api"
SHELL_DROPIN = f"""
# /etc/systemd/system/hindsight-api.service.d/wrap.conf
[Service]
ExecStart=
ExecStart=/bin/sh -c 'exec {HS_BIN} --host 0.0.0.0 --port 9177'
"""
LATER_DROPIN = """
# /etc/systemd/system/hindsight-api.service.d/zzzz-zlocal.conf
[Service]
ExecStart=
ExecStart=/opt/engine/bin/hindsight-api --host 0.0.0.0 --port 9177
"""


@pytest.mark.parametrize(
    ("raised", "answers"),
    [
        (None, False),  # the fake engine refuses: httpx.ConnectError
        (httpx.ReadTimeout, True),
        (httpx.ConnectTimeout, True),
        (httpx.RemoteProtocolError, True),
        (httpx.ReadError, True),
    ],
)
async def test_only_a_refused_connection_means_nothing_answers_at_the_normal_address(
    box: Box, raised: type[httpx.HTTPError] | None, answers: bool
) -> None:
    box.engine.port = PRIVATE_PORT
    box.engine.normal_port_raises = raised

    assert await box.switch()._public_answers() is answers


async def test_a_slow_listener_at_the_normal_address_stops_the_run_before_any_export(
    box: Box,
) -> None:
    """A connection accepted at the normal address and then timing out is not "isolated"."""
    box.engine.normal_port_raises = httpx.ReadTimeout

    with pytest.raises(es.SwitchError) as err:
        await box.switch().run()

    assert "still answers at its normal address" in err.value.message
    assert not any(e.startswith(("export:", "restore:")) for e in box.events)
    assert f"engine:{HS.model}" not in box.events
    box.engine.normal_port_raises = None
    _assert_rolled_back(box)


async def test_resume_does_not_take_a_slow_normal_address_for_an_isolated_engine(
    box: Box,
) -> None:
    await box.kill_at("deleted:alpha")
    box.engine.normal_port_raises = httpx.ReadTimeout
    seen = len(box.events)

    with pytest.raises(es.SwitchError) as err:
        await box.switch(None).resume()

    assert "still answers at its normal address" in err.value.message
    assert not any(e.startswith(("rename:", "restore:")) for e in box.events[seen:])
    assert write_gate.read_gate() is not None

    box.engine.normal_port_raises = None
    await box.switch(None).resume()
    _assert_switched(box)


async def test_a_foreign_process_on_the_private_port_fails_fast_and_is_named(box: Box) -> None:
    box.host.foreign[PRIVATE_PORT] = "pid 4242 (python3)"
    real = box.engine.handler
    private_asks: list[str] = []

    async def counting(request: httpx.Request) -> httpx.Response:
        if request.url.port == PRIVATE_PORT:
            private_asks.append(request.url.path)
        return await real(request)

    box.engine.handler = counting  # type: ignore[method-assign]
    looked = box.host.listener

    def listener(port: int) -> dict[str, Any]:
        private_asks.append("listener")
        return looked(port)

    with pytest.raises(es.SwitchError) as err:
        await box.switch(listener=listener).run()

    assert "127.0.0.1:45123" in err.value.message
    assert "held by pid 4242 (python3)" in err.value.message
    # The first thing after the restart is the look at who holds the port,
    # and it stops there: /health on that port is first asked by the rollback.
    assert private_asks[0] == "listener"
    assert not any(e.startswith("export:") for e in box.events)
    _assert_rolled_back(box)


async def test_a_health_answer_from_a_process_that_is_not_the_units_is_not_trusted(
    box: Box,
) -> None:
    """``/health`` answers JSON 200 on the private port, but no process of the unit listens there."""
    box.host.listener_says[PRIVATE_PORT] = {
        "owner": "unknown",
        "holder": "a process this account cannot see (run as root)",
    }

    with pytest.raises(es.SwitchError) as err:
        await box.switch().run()

    # The rollback does not take that answer for the engine either: it stays
    # paused and resumable until the owner can be confirmed.
    assert "could not be matched to a process of hindsight-api.service" in err.value.message
    assert "rollback" in err.value.message and err.value.resumable
    assert not any(e.startswith("export:") for e in box.events)
    assert write_gate.read_gate() is not None

    box.host.listener_says.clear()  # run as root: the owner can be read
    await box.switch(None).resume()
    _assert_rolled_back(box)


async def test_the_engine_on_the_private_port_must_report_the_version_preflight_saw(
    box: Box,
) -> None:
    real = box.engine.handler

    async def other_version(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/version" and request.url.port == PRIVATE_PORT:
            return httpx.Response(200, json={"api_version": "0.9.0", "features": {}})
        return await real(request)

    box.engine.handler = other_version  # type: ignore[method-assign]

    with pytest.raises(es.SwitchError) as err:
        await box.switch().run()

    assert "reports api_version '0.9.0', not '1.2.0'" in err.value.message
    assert not any(e.startswith("export:") for e in box.events)
    _assert_rolled_back(box)


def _fake_proc(tmp_path: Path, port: int, holders: dict[int, list[str]]) -> Path:
    """A ``/proc`` with one socket listening on ``port`` (inode 777) and the given fds."""
    proc = tmp_path / "proc"
    (proc / "net").mkdir(parents=True)
    header = "  sl  local_address rem_address   st tx_queue rx_queue tr tm->when retrnsmt   uid  timeout inode\n"
    listen = f"   0: 0100007F:{port:04X} 00000000:0000 0A 00000000:00000000 00:00000000 00000000     0        0 777 1 0\n"
    other = "   1: 0100007F:2385 0100007F:B0E2 01 00000000:00000000 00:00000000 00000000     0        0 778 1 0\n"
    (proc / "net" / "tcp").write_text(header + (listen if holders is not None else "") + other)
    (proc / "net" / "tcp6").write_text(header)
    for pid, links in holders.items():
        fd = proc / str(pid) / "fd"
        fd.mkdir(parents=True)
        (proc / str(pid) / "comm").write_text(f"proc{pid}\n")
        for i, link in enumerate(links):
            os.symlink(link, fd / str(i))
    return proc


def test_the_listener_check_reads_the_socket_owner_from_proc(tmp_path: Path) -> None:
    proc = _fake_proc(tmp_path, 45123, {100: ["socket:[777]"], 200: ["socket:[1]", "/dev/null"]})
    assert es.loopback_listener(45123, unit_pids=lambda: {100}, proc=proc)["owner"] == "unit"

    held = es.loopback_listener(45123, unit_pids=lambda: {200}, proc=proc)
    assert held == {"owner": "other", "holder": "pid 100 (proc100)", "pid": 100}

    assert es.loopback_listener(45124, unit_pids=lambda: {100}, proc=proc)["owner"] == "none"

    hidden = _fake_proc(tmp_path / "hidden", 45123, {})
    assert es.loopback_listener(45123, unit_pids=lambda: set(), proc=hidden)["owner"] == "unknown"


def test_the_units_processes_are_its_main_pid_and_its_control_group(tmp_path: Path) -> None:
    group = tmp_path / "system.slice" / "hindsight-api.service"
    group.mkdir(parents=True)
    (group / "cgroup.procs").write_text("100\n101\n")

    def runner(argv: list[str], **kw: Any) -> subprocess.CompletedProcess[str]:
        assert argv[:2] == ["systemctl", "show"] and argv[-1] == "hindsight-api.service"
        out = "MainPID=100\nControlGroup=/system.slice/hindsight-api.service\n"
        return subprocess.CompletedProcess(argv, 0, out, "")

    assert es._unit_pids(runner, cgroup_root=tmp_path) == {100, 101}


def test_the_shipped_units_command_is_rewritten_word_for_word() -> None:
    line = es.effective_exec_start(SHIPPED_UNIT)
    assert line == SHIPPED_EXEC
    assert es.private_exec_start(line, 45123) == SHIPPED_EXEC.replace("9177", "45123")
    assert es.unit_refusals(SHIPPED_UNIT, NORMAL_PORT) == []
    # An operator's LAN bind (UNIT + LAN_DROPIN, above) is rewritable too.
    assert es.unit_refusals(UNIT + LAN_DROPIN, NORMAL_PORT) == []


def test_a_quoted_argument_is_kept_and_a_quoted_address_is_replaced() -> None:
    line = (
        f'{HS_BIN} --log-config "/etc/hal0/log conf.json" --host "0.0.0.0" '
        "--port='9177' --workers 2"
    )
    assert es.private_exec_start(line, 45123) == (
        f'{HS_BIN} --log-config "/etc/hal0/log conf.json" --host 127.0.0.1 --port=45123 --workers 2'
    )


def test_a_kept_prefix_survives_the_rewrite() -> None:
    assert es.private_exec_start(f"-{HS_BIN} --port 9177", 45123) == (
        f"-{HS_BIN} --port 45123 --host 127.0.0.1"
    )


@pytest.mark.parametrize(
    ("line", "why"),
    [
        (f"/bin/sh -c 'exec {HS_BIN} --host 0.0.0.0 --port 9177'", "through a shell"),
        (f"/usr/bin/bash -c '{HS_BIN} --port 9177'", "through a shell"),
        (f"@{HS_BIN} hindsight-api --port 9177", "prefix '@'"),
        (f"+{HS_BIN} --port 9177", "prefix '+'"),
        (f"!!{HS_BIN} --port 9177", "prefix '!!'"),
        (f"{HS_BIN} --host 0.0.0.0 --port $PORT", "% specifier or $ variable"),
        (f"{HS_BIN} --port=${{HINDSIGHT_PORT}}", "% specifier or $ variable"),
        (f"{HS_BIN} --host %H --port 9177", "% specifier or $ variable"),
        (f'{HS_BIN} --log-config "a\\"b" --port 9177', "backslash"),
        (f"{HS_BIN} --log-config 'open --port 9177", "unterminated quote"),
        (f"{HS_BIN} --port", "has no value"),
    ],
)
def test_a_command_the_switch_cannot_rewrite_safely_is_refused(line: str, why: str) -> None:
    with pytest.raises(es.UnsupportedExecStart, match=re.escape(why)):
        es.private_exec_start(line, 45123)
    message = es.exec_start_refusal(line, "/etc/systemd/system/hindsight-api.service.d/x.conf")
    assert message is not None
    assert line in message and "x.conf" in message and "run the switch again" in message


async def test_a_shell_wrapped_exec_start_is_refused_before_anything_changes(box: Box) -> None:
    box.host.unit = SHIPPED_UNIT + SHELL_DROPIN

    plan = await box.switch().preflight()
    (refusal,) = [r for r in plan["refusals"] if r["code"] == "exec_start_unsupported"]
    assert "wrap.conf" in refusal["message"] and "through a shell" in refusal["message"]

    with pytest.raises(es.SwitchRefused):
        await box.switch().run()
    assert box.events == [] and box.engine.writes == []
    assert write_gate.read_gate() is None and box.host.dropin_port is None
    assert not es.journal_path().exists()


async def test_a_later_sorting_exec_start_drop_in_is_named_in_the_refusal(box: Box) -> None:
    box.host.unit = SHIPPED_UNIT + LATER_DROPIN

    plan = await box.switch().preflight()

    (refusal,) = [r for r in plan["refusals"] if r["code"] == "exec_start_overridden"]
    assert "/etc/systemd/system/hindsight-api.service.d/zzzz-zlocal.conf" in refusal["message"]
    assert "/opt/engine/bin/hindsight-api --host 0.0.0.0 --port 9177" in refusal["message"]
    assert es.ISOLATION_DROP_IN_NAME in refusal["message"]
    with pytest.raises(es.SwitchRefused):
        await box.switch().run()
    assert box.events == []


async def test_a_drop_in_that_appears_after_the_preflight_is_named_when_isolation_fails(
    box: Box,
) -> None:
    def hook(point: str) -> None:
        if point == "paused":
            box.host.unit = SHIPPED_UNIT + LATER_DROPIN
            box.host.exec_start_pinned = True

    with pytest.raises(es.SwitchError) as err:
        await box.switch(hook=hook).run()

    assert "zzzz-zlocal.conf" in err.value.message
    assert "/opt/engine/bin/hindsight-api --host 0.0.0.0 --port 9177" in err.value.message
    assert not any(e.startswith("export:") for e in box.events)
    _assert_rolled_back(box)


PROBE_DROPIN = """
# /etc/systemd/system/hindsight-api.service.d/probe.conf
[Service]
ExecStartPre=/bin/true
ExecStartPost=/usr/bin/curl -fsS http://127.0.0.1:9177/health
ExecReload=/bin/kill -HUP $MAINPID
"""


async def test_a_start_command_that_probes_the_normal_port_is_refused_by_name(box: Box) -> None:
    box.host.unit = SHIPPED_UNIT + PROBE_DROPIN

    plan = await box.switch().preflight()

    (refusal,) = [r for r in plan["refusals"] if r["code"] == "exec_probe_normal_port"]
    assert "probe.conf" in refusal["message"]
    assert "ExecStartPost=`/usr/bin/curl -fsS http://127.0.0.1:9177/health`" in refusal["message"]


def test_only_side_commands_naming_the_normal_port_count() -> None:
    cleared = (
        PROBE_DROPIN
        + "\n# /etc/systemd/system/hindsight-api.service.d/undo.conf\n[Service]\nExecStartPost=\n"
    )
    assert es.side_commands_on_port(SHIPPED_UNIT + cleared, NORMAL_PORT) == []
    near = "\n# /x/n.conf\n[Service]\nExecCondition=/usr/bin/test -e /run/91770\nExecStartPre=nc -z localhost 9177\n"
    assert es.side_commands_on_port(SHIPPED_UNIT + near, NORMAL_PORT) == [
        ("/x/n.conf", "ExecStartPre", "nc -z localhost 9177")
    ]


async def test_status_says_the_engine_is_back_while_the_gate_is_still_set(box: Box) -> None:
    """The finish's last restart failed after the drop-in was removed."""
    real = box.host.apply_dropin

    def released_then_failed(embedder: Embedder) -> dict[str, Any]:
        out = real(embedder)
        if box.host.dropin_port is None and box.host.live == HS:
            return {"error": "systemctl restart hindsight-api.service failed"}
        return out

    box.host.apply_dropin = released_then_failed  # type: ignore[method-assign]
    with pytest.raises(es.SwitchError) as err:
        await box.switch().run()
    assert err.value.resumable
    assert box.engine.port == NORMAL_PORT and write_gate.read_gate() is not None

    status = await box.switch(None).status()

    assert status["engine"]["released_early"] is True
    assert "already gone" in status["summary"] and "normal address" in status["summary"]
    assert "memory writes through hal0 stay paused" in status["summary"]
    assert "--resume isolates the engine again" in status["summary"]
    assert es.interrupted_switch()["isolated"] is True

    box.host.apply_dropin = real  # type: ignore[method-assign]
    seen = len(box.events)
    await box.switch(None).resume()
    assert "isolate" in box.events[seen:], "resume isolates the engine again first"
    _assert_switched(box)


# ── a foreign process on the private port: stop, report, message the real phase ──


async def test_resume_stops_the_engine_when_a_foreign_process_holds_the_port(box: Box) -> None:
    await box.kill_at("deleted:alpha")
    box.host.foreign[PRIVATE_PORT] = "pid 4242 (python3)"
    box.engine.reachable = False  # the unit would crash-loop on the taken port
    box.host.unit_stopped = False
    box.host.dropin_port = PRIVATE_PORT

    with pytest.raises(es.SwitchError) as err:
        await box.switch(None).resume()

    msg = err.value.message
    assert err.value.resumable
    assert "held by pid 4242 (python3)" in msg and "was stopped" in msg
    assert "--resume" in msg and "Free the port" in msg
    assert box.events[-1] == "stop-unit" and box.host.unit_stopped
    assert write_gate.read_gate() is not None, "the gate stays set"
    journal = es.load_journal()
    assert journal is not None and journal.engine_stopped is True


async def test_a_unit_that_cannot_be_stopped_says_so(box: Box) -> None:
    await box.kill_at("deleted:alpha")
    box.host.foreign[PRIVATE_PORT] = "pid 4242 (python3)"
    box.host.stop_error = "Access denied"

    with pytest.raises(es.SwitchError) as err:
        await box.switch(None).resume()

    assert "could not be stopped (Access denied)" in err.value.message
    assert "systemctl stop hindsight-api.service" in err.value.message
    journal = es.load_journal()
    assert journal is not None and journal.engine_stopped is False


async def test_status_reports_a_port_held_by_another_process_not_its_html(box: Box) -> None:
    await box.kill_at("deleted:alpha")
    box.host.foreign[PRIVATE_PORT] = "pid 4242 (python3)"
    box.host.dropin_port = PRIVATE_PORT
    real = box.engine.handler

    async def html(request: httpx.Request) -> httpx.Response:
        return httpx.Response(404, text="<!DOCTYPE HTML><html><body>Error 404</body></html>")

    box.engine.handler = html  # type: ignore[method-assign]
    try:
        status = await box.switch(None).status()
    finally:
        box.engine.handler = real  # type: ignore[method-assign]

    engine = status["engine"]
    assert engine["reachable"] is False
    assert engine["error"] == (
        f"port {PRIVATE_PORT} held by pid 4242 (python3), not by hindsight-api.service"
    )
    assert "DOCTYPE" not in str(status) and "<" not in engine["error"]


async def test_status_after_the_stop_says_the_switch_stopped_the_engine(box: Box) -> None:
    await box.kill_at("deleted:alpha")
    box.host.foreign[PRIVATE_PORT] = "pid 4242 (python3)"
    with pytest.raises(es.SwitchError):
        await box.switch(None).resume()
    box.host.foreign.clear()  # the port is free again, the unit is still stopped

    status = await box.switch(None).status()

    assert status["engine"]["stopped_by_switch"] is True
    assert status["engine"]["reachable"] is False

    await box.switch(None).resume()  # --resume starts it again and finishes
    _assert_switched(box)


def _foreign_answers_health(box: Box, asked: list[str]) -> None:
    """A foreign holder on the private port that answers 2xx on /health and nothing else."""
    real = box.engine.handler

    async def handler(request: httpx.Request) -> httpx.Response:
        if request.url.port == PRIVATE_PORT and box.host.foreign.get(PRIVATE_PORT):
            if request.url.path == "/health":
                return httpx.Response(200, json={"status": "healthy"})
            asked.append(request.url.path)
            return httpx.Response(503, json={"detail": "not the engine"})
        return await real(request)

    box.engine.handler = handler  # type: ignore[method-assign]


async def test_rollback_does_not_trust_a_foreign_holder_that_answers_health(box: Box) -> None:
    asked: list[str] = []
    _foreign_answers_health(box, asked)
    box.host.foreign[PRIVATE_PORT] = "pid 4242 (python3)"
    stop = box.host.stop_unit

    def stop_then_keep_answering() -> dict[str, Any]:
        out = stop()
        box.engine.reachable = True  # the engine is down; the foreign process is what answers
        return out

    box.host.stop_unit = stop_then_keep_answering  # type: ignore[method-assign]

    with pytest.raises(es.SwitchError) as err:
        await box.switch().run()

    assert "rollback_failed" not in err.value.message
    assert "Nothing was exported; the run is undone" in err.value.message
    assert asked == [], "the foreign holder is never asked for banks"
    box.engine.reachable = True
    box.engine.port = NORMAL_PORT
    box.host.foreign.clear()
    _assert_rolled_back(box)


async def test_after_the_flip_a_foreign_holder_is_never_queried_for_banks(box: Box) -> None:
    asked: list[str] = []
    _foreign_answers_health(box, asked)

    def hook(reached: str) -> None:
        if reached == "flipped":
            box.host.foreign[PRIVATE_PORT] = "pid 4242 (python3)"
            raise es.SwitchError("something went wrong after the flip")

    with pytest.raises(es.SwitchError) as err:
        await box.switch(hook=hook).run()

    assert "rollback" in err.value.message and err.value.resumable
    assert asked == [], "the foreign holder is never asked for banks"
    journal = es.load_journal()
    assert journal is not None and journal.phase == es.PHASE_ROLLBACK_FAILED
    assert write_gate.read_gate() is not None


async def test_a_foreign_port_during_the_flip_does_not_claim_nothing_was_exported(
    box: Box,
) -> None:
    def hook(reached: str) -> None:
        if reached == "before_flip":
            box.host.foreign[PRIVATE_PORT] = "pid 4242 (python3)"

    stop = box.host.stop_unit

    def stop_and_free() -> dict[str, Any]:
        out = stop()
        box.host.foreign.clear()  # the holder lets go before the rollback looks
        return out

    box.host.stop_unit = stop_and_free  # type: ignore[method-assign]

    with pytest.raises(es.SwitchError) as err:
        await box.switch(hook=hook).run()

    assert "Nothing was exported" not in err.value.message
    assert "held by pid 4242 (python3)" in err.value.message


async def _stopped_by_the_switch_then_started_outside(box: Box) -> es.Journal:
    await box.kill_at("deleted:alpha")
    box.host.foreign[PRIVATE_PORT] = "pid 4242 (python3)"
    with pytest.raises(es.SwitchError):
        await box.switch(None).resume()
    box.host.foreign.clear()
    # Someone starts the unit by hand; its drop-in still names the private port.
    box.host.unit_stopped = False
    box.engine.reachable = True
    box.engine.port = PRIVATE_PORT
    journal = es.load_journal()
    assert journal is not None and journal.engine_stopped is True
    return journal


async def test_status_reports_an_engine_started_outside_the_switch_as_running(box: Box) -> None:
    await _stopped_by_the_switch_then_started_outside(box)

    status = await box.switch(None).status()

    engine = status["engine"]
    assert engine["reachable"] is True and engine["running_again"] is True
    assert engine["stopped_by_switch"] is False
    assert {"Hal0::Beta", "gamma"} <= {b["bank"] for b in status["banks"]}
    assert all(b["counts"] for b in status["banks"])


async def test_ensure_engine_clears_a_stale_stopped_flag_when_the_unit_is_back(box: Box) -> None:
    journal = await _stopped_by_the_switch_then_started_outside(box)

    await box.switch(None)._ensure_engine(
        HS, port=PRIVATE_PORT, run_id=journal.run_id, journal=journal
    )

    assert journal.engine_stopped is False
    again = es.load_journal()
    assert again is not None and again.engine_stopped is False


async def test_status_says_when_the_port_owner_could_not_be_confirmed(box: Box) -> None:
    await box.kill_at("deleted:alpha")
    box.host.listener_says[PRIVATE_PORT] = {"owner": "unknown", "holder": "unreadable"}

    status = await box.switch(None).status()

    assert status["engine"]["owner_unconfirmed"] is True
    assert status["engine"]["reachable"] is True


def test_http_detail_is_short_single_line_and_markup_safe() -> None:
    assert es.http_detail("<!DOCTYPE HTML><html>x</html>") == "(a non-JSON page, not shown)"
    out = es.http_detail("line one\nline [two] <b>" + "x" * 500)
    assert "\n" not in out and not set("<>[]") & set(out) and len(out) <= 120


async def test_a_failed_rollback_names_its_phase_and_the_journalled_port(box: Box) -> None:
    box.engine.restore_delta["alpha"] = {"facts": -1}
    box.host.dropin_error[BGE.model] = "systemctl restart hindsight-api.service failed"

    with pytest.raises(es.SwitchError) as err:
        await box.switch().run()

    msg = err.value.message
    assert "rollback_failed" in msg and "the write pause is still set" in msg
    assert f"127.0.0.1:{PRIVATE_PORT}" in msg and "--resume" in msg
    assert "run the switch again" not in msg and "the run is undone" not in msg


# ── #3602 review: journal version, a delete that may have been sent, lossy restores ──


async def test_a_journal_from_the_rename_based_switch_is_refused_not_guessed_at(
    box: Box,
) -> None:
    """Version 1 steps (``renaming``, ``renamed``) fell through to a rebuild that deleted the copy."""
    journal = await box.kill_at("before_delete:alpha")
    data = journal.to_dict()
    data["version"] = 1
    data["banks"][0]["step"] = "renaming"
    text = json.dumps(data)
    es.journal_path().write_text(text)
    (Path(journal.archive_dir) / es.mirror_name()).write_text(text)
    before = len(box.events)

    with pytest.raises(es.SwitchError) as err:
        await box.switch(None).resume()

    assert "journal version 1" in err.value.message
    assert f"its archives are in {journal.archive_dir}" in err.value.message
    assert "Nothing was changed" in err.value.message
    assert box.events[before:] == [], "no delete, no export, no restore"
    assert es.interrupted_switch()["phase"] == "unreadable"  # type: ignore[index]
    assert es.blocking_reason() is not None


@pytest.mark.parametrize(
    ("field", "value", "said"),
    [
        ("step", "renamed", "unknown step 'renamed'"),
        ("phase", "renaming", "unknown phase 'renaming'"),
    ],
)
async def test_a_journal_with_a_step_or_phase_this_code_does_not_know_is_refused(
    box: Box, field: str, value: str, said: str
) -> None:
    journal = await box.kill_at("before_delete:alpha")
    data = journal.to_dict()
    if field == "step":
        data["banks"][0]["step"] = value
    else:
        data["phase"] = value
    text = json.dumps(data)
    es.journal_path().write_text(text)
    (Path(journal.archive_dir) / es.mirror_name()).write_text(text)
    before = len(box.events)

    with pytest.raises(es.SwitchError) as err:
        await box.switch(None).resume()

    assert said in err.value.message and journal.archive_dir in err.value.message
    assert box.events[before:] == []


async def test_a_bank_still_there_after_a_delete_that_may_have_been_sent_is_left_alone(
    box: Box,
) -> None:
    journal = await box.kill_at("before_delete:alpha")
    journal.banks[0].step = es.STEP_DELETING  # saved, then killed before the delete answered
    box.switch()._save(journal)
    before = len(box.events)

    with pytest.raises(es.SwitchError) as err:
        await box.switch(None).resume()

    assert err.value.resumable and err.value.bank == "alpha"
    assert "may have removed part of it" in err.value.message
    assert f"{journal.archive_dir}/{journal.banks[0].archive}" in err.value.message
    assert not any(e.startswith(("delete:", "export:", "restore:")) for e in box.events[before:])
    status = await box.switch(None).status()
    alpha = next(r for r in status["resume"] if r["bank"] == "alpha")
    assert alpha["action"] == es.ACTION_DELETE_SENT and "curl -X DELETE" in alpha["operator"]

    # The operator deletes what is left: the archive puts it back under its own id.
    box.engine.drop("alpha")
    result = await box.switch(None).resume()

    assert result["status"] == "switched"
    _assert_switched(box)


async def test_a_kill_after_the_delete_answered_is_finished_by_resume(box: Box) -> None:
    journal = await box.kill_at("deleted:alpha")
    assert journal.banks[0].step == es.STEP_DELETED and "alpha" not in box.engine.banks

    result = await box.switch(None).resume()

    assert result["status"] == "switched"
    _assert_switched(box)


async def test_a_restore_into_the_own_id_with_the_right_counts_but_lost_content_is_caught(
    box: Box,
) -> None:
    box.engine.m("alpha")["mm_content"] = "what alpha knows"
    box.engine.m("alpha")["mm_refreshed"] = "2026-11-01"
    box.engine.lossy_restore.add("alpha")

    with pytest.raises(es.SwitchError) as err:
        await box.switch().run()

    assert err.value.resumable and err.value.bank == "alpha"
    message = err.value.message
    assert "restored into its own id, does not match the original" in message
    assert "documents" in message and "facts" in message and "mental-model content" in message
    journal = es.load_journal()
    assert journal is not None and journal.banks[0].temp in box.engine.banks, "copy kept"
    assert journal.banks[0].contents is not None


async def test_the_content_check_names_the_ids_that_differ() -> None:
    before = {
        "documents": {"doc-a": 10, "doc-b": 0, "doc-c": 7},
        "facts": {"doc-a": "f1", "doc-c": "f2"},
        "model_content": {
            "mm-0": {"refreshed": "t1", "content": "c1"},
            "mm-1": {"refreshed": "t1", "content": "c1"},
        },
    }
    same = json.loads(json.dumps(before))
    assert es._contents_moved(before, same) == []
    # Refreshed by the engine after the restore: regenerated, not lost.
    refreshed = json.loads(json.dumps(before))
    refreshed["model_content"]["mm-0"] = {"refreshed": "t2", "content": "c2"}
    assert es._contents_moved(before, refreshed) == []
    lost = json.loads(json.dumps(before))
    lost["model_content"]["mm-1"]["content"] = "c0"
    del lost["documents"]["doc-b"]
    lost["documents"]["doc-z"] = 1
    lost["documents"]["doc-c"] = 3
    lost["facts"]["doc-a"] = "f0"
    assert es._contents_moved(before, lost) == [
        "documents (missing doc-b; not in the original doc-z; text length differs for doc-c)",
        "facts (differ for document doc-a)",
        "mental-model content (differs for mm-1)",
    ]
    many = {"documents": {f"d{i}": i for i in range(5)}}
    assert es._contents_moved(many, {"documents": {}}) == [
        "documents (missing d0, d1, d2 and 2 more)"
    ]
    # A form this build does not read is a difference, never assumed equal.
    assert es._contents_moved({"documents": "d1"}, {"documents": {}}) == [
        "documents (recorded in a form this build cannot compare)"
    ]


async def test_a_restore_that_loses_content_is_caught_before_any_original_is_deleted(
    box: Box,
) -> None:
    """The rebuilt copy keeps the original's document and model ids: it is checked by content."""
    box.engine.m("alpha")["mm_content"] = "what alpha knows"
    box.engine.m("alpha")["mm_refreshed"] = "2026-11-01"
    box.engine.lossy_everywhere.add("alpha")

    with pytest.raises(es.SwitchError) as err:
        await box.switch().run()

    assert not err.value.resumable, "rolled back: nothing had been deleted"
    message = err.value.message
    assert "alpha was not touched" in message
    assert "documents (text length differs for doc-0, doc-1, doc-2 and 3 more)" in message
    assert "facts (differ for document doc-0)" in message
    assert "mental-model content (differs for mm-0, mm-1)" in message
    _assert_rolled_back(box)


async def test_a_resume_that_rebuilds_a_lossy_copy_says_to_stop_resuming(box: Box) -> None:
    journal = await box.kill_at("before_swap")
    alpha = journal.banks[0]
    box.engine.m(alpha.temp)["text"] = "tampered"  # the copy no longer matches
    box.engine.lossy_everywhere.add("alpha")  # and every rebuild loses the same content
    before = len(box.events)

    with pytest.raises(es.SwitchError) as err:
        await box.switch(None).resume()

    assert err.value.resumable and err.value.bank == "alpha"
    assert "if --resume reports it again, stop running it" in err.value.message
    assert "delete:alpha" not in box.events[before:], "the original is never deleted"
    assert "alpha" in box.engine.banks


async def test_a_bank_that_stores_no_document_text_switches_cleanly(box: Box) -> None:
    """store_document_text off: original_text NULL, content_hash of a text it does not hold.

    A restore recomputes content_hash from the text it stores, so the hashes
    differ by design; the check compares ids and stored-text length instead.
    """
    box.engine.m("gamma")["no_text"] = True
    async with box.http(f"http://127.0.0.1:{NORMAL_PORT}") as http:
        old = (await http.get("/v1/default/banks/gamma/documents")).json()["items"]

    result = await box.switch().run()

    assert result["status"] == "switched"
    _assert_switched(box)
    async with box.http(f"http://127.0.0.1:{NORMAL_PORT}") as http:
        new = (await http.get("/v1/default/banks/gamma/documents")).json()["items"]
    assert [d["id"] for d in new] == [d["id"] for d in old]
    assert all(d["text_length"] == 0 for d in new)
    assert [d["content_hash"] for d in new] != [d["content_hash"] for d in old], (
        "the engine's hash moved, and the switch did not take that for a loss"
    )


async def test_restores_into_the_own_id_that_fail_the_same_way_twice_stop_the_retries(
    box: Box,
) -> None:
    box.engine.lossy_restore.add("alpha")  # wrong only into the own id

    with pytest.raises(es.SwitchError) as first:
        await box.switch().run()
    assert "restores it again once; if it fails the same way" in first.value.message

    with pytest.raises(es.SwitchError) as second:
        await box.switch(None).resume()
    assert "failed its check the same way each time" in second.value.message
    assert "stop running --resume" in second.value.message
    journal = es.load_journal()
    assert journal is not None and journal.banks[0].place_repeats == 2
    before = len(box.events)

    with pytest.raises(es.SwitchError) as third:
        await box.switch(None).resume()

    assert "stop running --resume" in third.value.message
    status = await box.switch(None).status()
    alpha = next(b for b in status["resume"] if b["bank"] == "alpha")
    assert "stop running --resume" in alpha["operator"]
    assert not any(e.startswith(("delete:", "restore:")) for e in box.events[before:]), (
        "nothing is deleted or restored a third time"
    )
    assert journal.banks[0].temp in box.engine.banks, "the verified copy is kept"


async def test_the_restore_op_is_saved_cleared_as_soon_as_the_restore_ends(box: Box) -> None:
    switch = box.switch()
    real_quiet = switch._quiet_state
    seen: list[Any] = []

    async def quiet(bank: str) -> dict[str, Any]:
        if es.is_switch_temp(bank) and not seen:
            journal = es.load_journal()
            assert journal is not None
            seen.append(journal.banks[0].restore_op)
            raise Killed
        return await real_quiet(bank)

    switch._quiet_state = quiet  # type: ignore[method-assign]
    switch._rollback = lambda *_a: (_ for _ in ()).throw(Killed)  # type: ignore[method-assign]
    with pytest.raises(Killed):
        await switch.run()

    assert seen == [None], "on disk before the bank is read"


async def test_a_cancelled_restore_recorded_without_a_process_is_ended_by_a_restart(
    box: Box,
) -> None:
    """No engine pid was recorded (no unit process seen); the unit's InvocationID is enough."""
    journal = await _stuck_placing_beta(box)
    op = journal.banks[1].restore_op
    assert op is not None and op["engine_invocation"]
    op["engine_pid"] = None
    box.switch()._save(journal)
    scratch = es.scratch_bank_id(journal.run_id)
    async with box.http(f"http://127.0.0.1:{PRIVATE_PORT}") as http:
        await http.delete(f"/v1/default/banks/{scratch}/operations/{op['id']}")

    with pytest.raises(es.SwitchError) as err:
        await box.switch(None).resume()
    assert "recorded as cancelled" in err.value.message
    assert "systemctl stop" not in err.value.message, "a restart can be told apart"

    box.host.apply_dropin(HS)  # the operator restarts the engine
    result = await box.switch(None).resume()

    assert result["status"] == "switched"
    _assert_switched(box)


async def test_a_cancelled_restore_with_nothing_recorded_gives_a_way_out_that_works(
    box: Box,
) -> None:
    journal = await _stuck_placing_beta(box)
    op = journal.banks[1].restore_op
    assert op is not None
    op["engine_pid"] = op["engine_invocation"] = None  # an older record, or nothing seen
    box.switch()._save(journal)
    scratch = es.scratch_bank_id(journal.run_id)
    async with box.http(f"http://127.0.0.1:{PRIVATE_PORT}") as http:
        await http.delete(f"/v1/default/banks/{scratch}/operations/{op['id']}")
    before = len(box.events)

    with pytest.raises(es.SwitchError) as err:
        await box.switch(None).resume()

    assert "systemctl stop hindsight-api.service" in err.value.message
    assert not any(e.startswith(("delete:", "restore:")) for e in box.events[before:])

    box.host.stop_unit()  # what the message says to do
    result = await box.switch(None).resume()

    assert result["status"] == "switched"
    _assert_switched(box)


async def test_allow_pending_skips_the_content_checks_it_cannot_make(box: Box) -> None:
    journal = await box.kill_at("before_swap", allow_pending=True)
    assert all(entry.contents is None for entry in journal.banks)
    assert "content checks are skipped" in es.IDS_ACROSS_SWITCH["note"]
