"""Tests for the memory showcase surface — engine passthrough, lanes, agents, models.

Mirrors the harness in test_memory_admin_routes.py: an httpx.MockTransport
stands in for Hindsight, mounted behind a real HindsightRestClient so auth
headers / base-url plumbing stay exercised.

Run targeted:
    .venv/bin/python -m pytest tests/api/test_memory_lanes_routes.py -q
"""

from __future__ import annotations

from collections.abc import Iterator
from typing import Any

import httpx
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from hal0.api.middleware import error_codes
from hal0.api.routes import memory_lanes
from hal0.memory.hindsight_client import HindsightRestClient


class _Recorder:
    def __init__(self) -> None:
        self.requests: list[dict[str, Any]] = []
        self.responses: dict[tuple[str, str], httpx.Response] = {}

    def respond(self, method: str, path: str, status: int, body: Any) -> None:
        self.responses[(method, path)] = httpx.Response(status, json=body)

    async def handler(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(
            {
                "method": request.method,
                "path": request.url.path,
                "params": dict(request.url.params),
                "body": request.content.decode() if request.content else "",
            }
        )
        key = (request.method, request.url.path)
        if key in self.responses:
            return self.responses[key]
        if request.url.path == "/v1/default/banks":
            return httpx.Response(200, json={"banks": []})
        return httpx.Response(200, json={"echo": request.url.path})


class _HindsightStubProvider:
    def __init__(self, client: HindsightRestClient) -> None:
        self.hindsight_client = client


def _build_app(provider: Any, cfg: Any | None = None) -> FastAPI:
    app = FastAPI()
    error_codes.install(app)
    app.include_router(memory_lanes.router, prefix="/api/memory", tags=["memory"])
    app.state.memory_provider = provider
    return app


@pytest.fixture
def recorder() -> _Recorder:
    return _Recorder()


@pytest.fixture
def client(recorder: _Recorder) -> Iterator[TestClient]:
    transport = httpx.MockTransport(recorder.handler)
    http = httpx.AsyncClient(transport=transport, base_url="http://127.0.0.1:9177")
    rest = HindsightRestClient(http_client=http, api_key="hal0-local-noauth")
    app = _build_app(_HindsightStubProvider(rest))
    with TestClient(app) as c:
        yield c


# ── engine passthrough ───────────────────────────────────────────────────


def test_engine_banks_passthrough(client: TestClient, recorder: _Recorder) -> None:
    recorder.respond("GET", "/v1/default/banks", 200, {"banks": [{"bank_id": "shared"}]})
    r = client.get("/api/memory/engine/v1/default/banks")
    assert r.status_code == 200
    assert r.json() == {"banks": [{"bank_id": "shared"}]}


def test_engine_bank_subpath_passthrough(client: TestClient, recorder: _Recorder) -> None:
    recorder.respond(
        "GET", "/v1/default/banks/shared/stats", 200, {"bank_id": "shared", "total_nodes": 3}
    )
    r = client.get("/api/memory/engine/v1/default/banks/shared/stats")
    assert r.status_code == 200
    assert r.json()["total_nodes"] == 3


def test_engine_version_and_health_passthrough(client: TestClient, recorder: _Recorder) -> None:
    recorder.respond("GET", "/version", 200, {"version": "1.2.0"})
    recorder.respond("GET", "/health", 200, {"status": "ok"})
    assert client.get("/api/memory/engine/version").json() == {"version": "1.2.0"}
    assert client.get("/api/memory/engine/health").json() == {"status": "ok"}


def test_engine_bank_llm_request_stats_passthrough(client: TestClient, recorder: _Recorder) -> None:
    """The dashboard's "LLM requests by operation" card reads the bank-scoped
    stats route (#3494). Hindsight 1.2.0 exposes llm-request stats only under
    /v1/default/banks/{bank_id}/, and only fills each bucket's per-operation
    `series` when the request asks to split by operation — so the path must
    survive the allowlist AND the split_by query param must be forwarded."""
    recorder.respond(
        "GET",
        "/v1/default/banks/shared/llm-requests/stats",
        200,
        {
            "bank_id": "shared",
            "period": "7d",
            "trunc": "day",
            "start": "2026-11-01T00:00:00",
            "buckets": [
                {
                    "time": "2026-11-01T00:00:00",
                    "statuses": {"success": 2},
                    "total": 2,
                    "tokens": {"input": 0, "output": 0, "cached": 0, "thoughts": None, "total": 0},
                    "series": {"retain": {"calls": 2, "tokens": 30}},
                }
            ],
            "retention_days": 7,
        },
    )
    r = client.get(
        "/api/memory/engine/v1/default/banks/shared/llm-requests/stats?split_by=operation"
    )
    assert r.status_code == 200
    assert r.json()["buckets"][0]["series"]["retain"]["calls"] == 2
    upstream = recorder.requests[-1]
    assert upstream["path"] == "/v1/default/banks/shared/llm-requests/stats"
    assert upstream["params"] == {"split_by": "operation"}


def test_engine_post_body_forwarded(client: TestClient, recorder: _Recorder) -> None:
    recorder.respond("POST", "/v1/default/banks/shared/reflect", 200, {"ok": True})
    r = client.post("/api/memory/engine/v1/default/banks/shared/reflect", json={"q": "x"})
    assert r.status_code == 200
    import json as _json

    assert _json.loads(recorder.requests[-1]["body"]) == {"q": "x"}


def test_engine_disallowed_path_404(client: TestClient) -> None:
    r = client.get("/api/memory/engine/v1/default/admin/shutdown")
    assert r.status_code == 404


@pytest.mark.parametrize(
    "sub",
    [
        "v1/default/banks/shared/directives/dir-1",
        "v1/default/banks/shared/mental-models/mm-1",
    ],
)
def test_engine_item_delete_forwarded(client: TestClient, recorder: _Recorder, sub: str) -> None:
    r = client.delete(f"/api/memory/engine/{sub}")
    assert r.status_code == 200
    assert recorder.requests[-1]["method"] == "DELETE"
    assert recorder.requests[-1]["path"] == f"/{sub}"


@pytest.mark.parametrize(
    ("method", "sub"),
    [
        # #1024: a bank wipe must go through memory_admin's ?confirm= gate.
        ("DELETE", "v1/default/banks/shared"),
        ("DELETE", "v1/default/banks/shared/memories"),
        ("DELETE", "v1/default/banks/shared/documents/doc-1"),
        ("DELETE", "v1/default/banks/shared/config"),
        ("DELETE", "v1/default/banks/shared/directives"),
        ("POST", "v1/default/banks/shared/memories/delete"),
        ("POST", "v1/default/banks/shared/clear"),
        ("PUT", "v1/default/banks/shared/reset"),
    ],
)
def test_engine_destructive_bank_calls_refused(
    client: TestClient, recorder: _Recorder, method: str, sub: str
) -> None:
    before = len(recorder.requests)
    r = client.request(method, f"/api/memory/engine/{sub}")
    assert r.status_code == 403
    assert r.json()["error"]["code"] == "memory.engine_delete_not_allowed"
    assert len(recorder.requests) == before, "refused call must never reach the engine"


def test_engine_path_traversal_404(client: TestClient) -> None:
    r = client.get("/api/memory/engine/v1/default/banks/../../etc/passwd")
    assert r.status_code in (404, 400)


def test_engine_unreachable_503(recorder: _Recorder) -> None:
    async def fail(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("connection refused", request=request)

    http = httpx.AsyncClient(transport=httpx.MockTransport(fail), base_url="http://127.0.0.1:9177")
    rest = HindsightRestClient(http_client=http, api_key="hal0-local-noauth")
    app = _build_app(_HindsightStubProvider(rest))
    with TestClient(app) as c:
        r = c.get("/api/memory/engine/v1/default/banks")
    assert r.status_code == 503


def test_engine_no_provider_503() -> None:
    app = _build_app(None)
    with TestClient(app) as c:
        r = c.get("/api/memory/engine/version")
    assert r.status_code == 503


# ── lanes / agents / models ──────────────────────────────────────────────


def test_lanes_basic_shape(client: TestClient, recorder: _Recorder) -> None:
    recorder.respond("GET", "/v1/default/banks", 200, {"banks": [{"bank_id": "shared"}]})
    recorder.respond(
        "GET",
        "/v1/default/banks/shared/operations",
        200,
        {"operations": [{"status": "pending", "created_at": "2024-01-01T00:00:00Z"}]},
    )
    r = client.get("/api/memory/lanes")
    assert r.status_code == 200
    body = r.json()
    assert "decision" in body and "llm" in body and "banks" in body
    assert body["decision"]["reachable"] is False
    bank_row = next(b for b in body["banks"] if b["bank_id"] == "shared")
    assert bank_row["pending"] == 1
    assert body["llm"]["per_op"]["reflect"]["max_concurrent"] >= 1


def test_lanes_reports_effective_unit_values_and_drift(
    client: TestClient, recorder: _Recorder, monkeypatch
) -> None:
    """#3520: configured values stay where they were (backward compatible);
    ``effective`` carries what the hindsight-api unit really runs with and
    ``drift`` names each managed key where the two disagree."""
    from hal0.api.routes import memory_lanes as mod

    report = {
        "installed": True,
        "checked": True,
        "drift": [{"key": "HINDSIGHT_API_LLM_MAX_CONCURRENT", "expected": "1", "effective": "2"}],
        "legacy_drop_in": "/etc/systemd/system/hindsight-api.service.d/zz-h0-98-llm-global-cap.conf",
        "effective": {
            "HINDSIGHT_API_LLM_MODEL": "hal0/memory",
            "HINDSIGHT_API_LLM_MAX_CONCURRENT": "2",
            "HINDSIGHT_API_LLM_TIMEOUT": "3600",
            "HINDSIGHT_API_REFLECT_LLM_MAX_CONCURRENT": "1",
            "HINDSIGHT_API_REFLECT_LLM_TIMEOUT": "900",
        },
    }
    monkeypatch.setattr(mod, "_engine_env_report", lambda _mem: report)
    r = client.get("/api/memory/lanes")
    assert r.status_code == 200
    llm = r.json()["llm"]
    assert isinstance(llm["max_concurrent"], int)  # configured, unchanged shape
    assert "timeout" in llm and "per_op" in llm
    eff = llm["effective"]
    assert eff["max_concurrent"] == 2
    assert eff["timeout"] == 3600
    assert eff["model"] == "hal0/memory"
    assert eff["per_op"]["reflect"] == {"max_concurrent": 1, "model": "hal0/memory", "timeout": 900}
    assert eff["per_op"]["retain"]["timeout"] == 3600  # falls back to the global
    assert llm["drift"] == report["drift"]
    assert llm["legacy_drop_in"].endswith("zz-h0-98-llm-global-cap.conf")
    assert "HINDSIGHT_API_LLM_MAX_CONCURRENT=2" in llm["warning"]


def test_lanes_effective_is_null_when_unit_env_unreadable(
    client: TestClient, recorder: _Recorder, monkeypatch
) -> None:
    from hal0.api.routes import memory_lanes as mod

    monkeypatch.setattr(
        mod,
        "_engine_env_report",
        lambda _mem: {
            "installed": True,
            "checked": False,
            "drift": [],
            "legacy_drop_in": None,
            "effective": {},
        },
    )
    llm = client.get("/api/memory/lanes").json()["llm"]
    assert llm["effective"] is None
    assert llm["drift"] == [] and llm["warning"] is None


def test_lanes_survive_an_engine_env_failure(
    client: TestClient, recorder: _Recorder, monkeypatch
) -> None:
    from hal0.api.routes import memory_lanes as mod

    def _boom(_mem):
        raise RuntimeError("systemd exploded")

    monkeypatch.setattr(mod, "_engine_env_report", _boom)
    r = client.get("/api/memory/lanes")
    assert r.status_code == 200
    assert r.json()["llm"]["effective"] is None


def test_lanes_bank_operations_error_included(client: TestClient, recorder: _Recorder) -> None:
    recorder.respond("GET", "/v1/default/banks", 200, {"banks": [{"bank_id": "broken"}]})
    recorder.respond("GET", "/v1/default/banks/broken/operations", 500, {"detail": "boom"})
    r = client.get("/api/memory/lanes")
    assert r.status_code == 200
    body = r.json()
    bank_row = next(b for b in body["banks"] if b["bank_id"] == "broken")
    assert "error" in bank_row and isinstance(bank_row["error"], str)


def test_lanes_npu_absent_is_null(client: TestClient, recorder: _Recorder) -> None:
    recorder.respond("GET", "/v1/default/banks", 200, {"banks": []})
    r = client.get("/api/memory/lanes")
    assert r.status_code == 200
    assert r.json()["npu"] is None


def test_agents_filters_coding_agent_banks(client: TestClient, recorder: _Recorder) -> None:
    recorder.respond(
        "GET",
        "/v1/default/banks",
        200,
        {"banks": [{"bank_id": "shared"}, {"bank_id": "coding-agent::repo1"}]},
    )
    r = client.get("/api/memory/agents")
    assert r.status_code == 200
    body = r.json()
    assert body["banks"] == ["coding-agent::repo1"]
    assert body["install"]["harnesses"] == [
        "claude-code",
        "codex",
        "opencode",
        "pi",
        "cursor-cli",
        "copilot-cli",
        "qwen-code",
    ]
    assert "hindsight-coding-agents@0.8.5" in body["install"]["command"]
    assert body["console_url"] is None


def test_agents_console_url_disabled_by_default(client: TestClient, recorder: _Recorder) -> None:
    recorder.respond("GET", "/v1/default/banks", 200, {"banks": []})
    r = client.get("/api/memory/agents")
    assert r.status_code == 200
    assert r.json()["console_url"] is None


def test_agents_console_url_enabled(client: TestClient, recorder: _Recorder, monkeypatch) -> None:
    from hal0.api.routes import memory_lanes as mod

    class _Console:
        enabled = True
        port = 9999
        url = None

    class _MemCfg:
        public_url = None
        console = _Console()

    class _Cfg:
        memory = _MemCfg()

    monkeypatch.setattr(mod, "load_hal0_config", lambda: _Cfg())
    recorder.respond("GET", "/v1/default/banks", 200, {"banks": []})
    r = client.get("/api/memory/agents")
    assert r.status_code == 200
    body = r.json()
    assert body["console_url"] is not None
    assert body["console_url"].endswith(":9999")


def test_models_shape(client: TestClient) -> None:
    r = client.get("/api/memory/models")
    assert r.status_code == 200
    body = r.json()
    assert body["embeddings"]["provider"] == "local"
    assert body["decision"]["model"] == "hs-decide-1-flash"
    assert body["decision"]["reachable"] is False


# ── /api/memory/models: recorded against configured embedder (#3518) ─────────


def _store_and_dropin(tmp_path, monkeypatch, *, dropin_model: str | None = None) -> None:
    """An engine store under the sandboxed state root, and hal0's showcase drop-in path."""
    from hal0.memory import extraction_env as ee
    from hal0.memory.engine_upgrade import hindsight_dir

    (hindsight_dir() / ".pg0" / "instances" / "hindsight" / "data").mkdir(parents=True)
    dropin = tmp_path / "hindsight-api.service.d" / "showcase-lanes.conf"
    monkeypatch.setattr(ee, "SHOWCASE_DROP_IN_PATH", dropin)
    if dropin_model is not None:
        dropin.parent.mkdir(parents=True)
        dropin.write_text(
            f"[Service]\nEnvironment=HINDSIGHT_API_EMBEDDINGS_LOCAL_MODEL={dropin_model}\n"
        )


def test_models_reports_an_upgraded_store_as_needing_a_switch(
    client: TestClient, tmp_path, monkeypatch
) -> None:
    """Existing store, no drop-in, config at its hs-embed-1 default: the store
    was built with bge-small, and the route says so without touching disk."""
    from hal0.memory.embedder_record import embedder_record_path

    _store_and_dropin(tmp_path, monkeypatch)

    emb = client.get("/api/memory/models").json()["embeddings"]

    assert (emb["recorded_provider"], emb["recorded"]) == ("local", "BAAI/bge-small-en-v1.5")
    assert (emb["configured_provider"], emb["configured"]) == ("local", "hindsight-ai/hs-embed-1")
    # Unchanged: the configured values.
    assert (emb["provider"], emb["model"]) == ("local", "hindsight-ai/hs-embed-1")
    assert emb["conflict"] is None and emb["record_error"] is None
    assert emb["mixed"] is False
    assert emb["needs_switch"] is True
    assert emb["source"] == "engine-default"
    assert not embedder_record_path().exists()


def test_models_reports_a_matching_store_as_clean(
    client: TestClient, tmp_path, monkeypatch
) -> None:
    from hal0.api.routes import memory_lanes as mod

    class _Models:
        embeddings_provider = "local"
        embeddings_model = "BAAI/bge-small-en-v1.5"
        reranker_provider = "local"
        reranker_model = "hindsight-ai/hs-rerank-1"

    class _Decision:
        enabled = False
        provider = "off"
        model = "hs-decide-1-flash"
        port = 8090

    class _Mem:
        models = _Models()
        decision = _Decision()

    class _Cfg:
        memory = _Mem()

    _store_and_dropin(tmp_path, monkeypatch)
    monkeypatch.setattr(mod, "load_hal0_config", lambda: _Cfg())

    emb = client.get("/api/memory/models").json()["embeddings"]

    assert emb["recorded"] == emb["configured"] == "BAAI/bge-small-en-v1.5"
    assert emb["mixed"] is False and emb["needs_switch"] is False


def test_models_reports_a_mixed_store(client: TestClient, tmp_path, monkeypatch) -> None:
    """The drop-in already moved an existing store to hs-embed-1: recorded and
    configured agree, and the store still needs the switch."""
    _store_and_dropin(tmp_path, monkeypatch, dropin_model="hindsight-ai/hs-embed-1")

    emb = client.get("/api/memory/models").json()["embeddings"]

    assert emb["recorded"] == emb["configured"] == "hindsight-ai/hs-embed-1"
    assert emb["mixed"] is True
    assert emb["needs_switch"] is True
    assert emb["source"] == "showcase-drop-in"


def test_models_reports_a_provider_that_differs(client: TestClient, tmp_path, monkeypatch) -> None:
    """Same model name, a remote provider configured: still a different embedder."""
    from hal0.api.routes import memory_lanes as mod
    from hal0.config.schema import MemoryConfig, MemoryModelsConfig

    class _Cfg:
        memory = MemoryConfig(
            models=MemoryModelsConfig(
                embeddings_provider="openai", embeddings_model="BAAI/bge-small-en-v1.5"
            )
        )

    _store_and_dropin(tmp_path, monkeypatch)
    monkeypatch.setattr(mod, "load_hal0_config", lambda: _Cfg())

    emb = client.get("/api/memory/models").json()["embeddings"]

    assert (emb["recorded_provider"], emb["configured_provider"]) == ("local", "openai")
    assert emb["recorded"] == emb["configured"] == "BAAI/bge-small-en-v1.5"
    assert emb["needs_switch"] is True


def test_models_reports_where_the_engine_disagrees_with_the_record(
    client: TestClient, tmp_path, monkeypatch
) -> None:
    from hal0.memory import embedder_record as er

    _store_and_dropin(tmp_path, monkeypatch)
    er.save_embedder_record(
        er.EmbedderRecord(provider="local", model="BAAI/bge-small-en-v1.5", source="engine-default")
    )
    running = {"provider": "local", "model": "hindsight-ai/hs-embed-1"}
    monkeypatch.setattr(
        er,
        "engine_evidence",
        lambda **_kw: {"live": running, "on_disk": running, "on_disk_source": "unit"},
    )

    emb = client.get("/api/memory/models").json()["embeddings"]

    assert emb["recorded"] == "BAAI/bge-small-en-v1.5"
    assert emb["effective"] == {**running, "source": "process"}
    assert emb["conflict"] == {"live": running, "on_disk": running}
