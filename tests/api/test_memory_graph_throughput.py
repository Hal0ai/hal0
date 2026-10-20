"""#2353: ``/api/memory/graph`` warns when the extraction slot is too slow.

Operator decision on the issue: warn, never refuse; one bounded probe. The
probe runs on ``PUT /api/memory/graph`` when the gate is switched on or the
extraction slot changes, and on ``POST /api/memory/graph/throughput`` (what
``hal0 doctor all`` and the installer call). ``GET`` only reads the cached
estimate, which is what ``hal0 memory status`` shows.

The slot is a fake llama-server (``httpx.MockTransport``) at a slow and a fast
rate, driven through the real :func:`measure_extraction_throughput`.
"""

from __future__ import annotations

from collections.abc import Iterator
from pathlib import Path
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
import tomli_w
from fastapi import FastAPI
from fastapi.testclient import TestClient

from hal0.api.middleware import error_codes
from hal0.api.routes import memory as memory_routes
from hal0.memory import extraction_env
from tests.memory.test_extraction_throughput import FAST, SLOW, FakeSlot

_real_measure = extraction_env.measure_extraction_throughput


class StubWrapper:
    def __init__(self, *, enabled: bool = False, extraction_slot: str = "utility") -> None:
        self.enabled = enabled
        self.extraction_slot = extraction_slot

    def graph_status(self) -> dict[str, Any]:
        return {
            "enabled": self.enabled,
            "extraction_slot": self.extraction_slot,
            "in_flight": 0,
            "builds_ok": 0,
            "errors": 0,
            "last_built_at": None,
            "last_error": None,
        }

    def set_graph_enabled(self, enabled: bool, extraction_slot: str | None = None) -> None:
        self.enabled = enabled
        if extraction_slot is not None:
            self.extraction_slot = extraction_slot


def _slot_manager() -> MagicMock:
    mgr = MagicMock()
    mgr.iter_configs = AsyncMock(
        return_value=[
            {"name": "agent", "type": "llm", "enabled": True, "model_id": "m-a", "port": 8081},
            {"name": "utility", "type": "llm", "enabled": True, "model_id": "m-u", "port": 8082},
        ]
    )
    return mgr


@pytest.fixture
def hal0_home(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    home = tmp_path / "hal0-home"
    (home / "etc").mkdir(parents=True)
    (home / "var-lib").mkdir(parents=True)
    (home / "etc" / "hal0.toml").write_text(tomli_w.dumps({"meta": {"schema_version": 1}}))
    monkeypatch.setenv("HAL0_HOME", str(home))
    return home


@pytest.fixture
def stub() -> StubWrapper:
    return StubWrapper()


@pytest.fixture
def client(stub: StubWrapper, hal0_home: Path) -> Iterator[TestClient]:
    app = FastAPI()
    error_codes.install(app)
    app.include_router(memory_routes.router, prefix="/api/memory", tags=["memory"])
    app.state.memory_provider = stub
    app.state.slot_manager = _slot_manager()
    with (
        TestClient(app) as c,
        patch("hal0.memory.extraction_env.apply_extraction_slot") as apply_mock,
    ):
        apply_mock.return_value = {"written": True, "restarted": True, "error": None}
        yield c


def _fake_slots(monkeypatch: pytest.MonkeyPatch, **rates_by_port: dict[str, float]) -> list[int]:
    """Route the measurement to a fake slot per port; return the ports probed."""
    probed: list[int] = []

    async def _measure(slot: str, port: int, **kw: Any) -> Any:
        probed.append(port)
        fake = FakeSlot(**rates_by_port[f"p{port}"])
        return await _real_measure(slot, port, transport=fake.transport, **kw)

    monkeypatch.setattr(extraction_env, "measure_extraction_throughput", _measure)
    return probed


def test_enable_on_a_slow_slot_warns_but_still_enables(
    client: TestClient, stub: StubWrapper, monkeypatch: pytest.MonkeyPatch
) -> None:
    probed = _fake_slots(monkeypatch, p8082=SLOW)

    r = client.put("/api/memory/graph", json={"enabled": True})

    assert r.status_code == 200, r.text
    body = r.json()
    assert body["enabled"] is True and stub.enabled is True
    assert probed == [8082]
    assert body["throughput"]["verdict"] == "too_slow"
    assert body["throughput"]["prompt_tps"] == pytest.approx(12.0)
    [warning] = body["warnings"]
    assert "12.0 prompt tok/s" in warning
    assert "llm_timeout_s = 300 s" in warning


def test_enable_on_a_fast_slot_does_not_warn(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    _fake_slots(monkeypatch, p8082=FAST)

    body = client.put("/api/memory/graph", json={"enabled": True}).json()

    assert body["throughput"]["verdict"] == "ok"
    assert "warnings" not in body


def test_slot_change_measures_the_new_slot(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    probed = _fake_slots(monkeypatch, p8081=SLOW, p8082=FAST)

    body = client.put(
        "/api/memory/graph", json={"enabled": True, "extraction_slot": "agent"}
    ).json()

    assert probed == [8081]
    assert body["throughput"]["slot"] == "agent"
    assert body["throughput"]["verdict"] == "too_slow"


def test_disable_never_probes(client: TestClient, monkeypatch: pytest.MonkeyPatch) -> None:
    probed = _fake_slots(monkeypatch, p8082=SLOW)

    body = client.put("/api/memory/graph", json={"enabled": False}).json()

    assert probed == []
    assert "throughput" not in body


def test_timeout_change_reprojects_the_cached_estimate_without_probing(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    probed = _fake_slots(monkeypatch, p8082={"prompt_tps": 100.0, "decode_tps": 20.0})
    first = client.put("/api/memory/graph", json={"enabled": True}).json()
    assert first["throughput"]["verdict"] == "ok"  # 35 + 204.8 s < 300 s

    body = client.put("/api/memory/graph", json={"llm_timeout_s": 120}).json()

    assert probed == [8082]  # the first enable only
    assert body["throughput"]["timeout_s"] == 120
    assert body["throughput"]["verdict"] == "too_slow"
    assert "llm_timeout_s = 120 s" in body["warnings"][0]


def test_get_throughput_reads_the_cache_and_never_probes(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    probed = _fake_slots(monkeypatch, p8082=SLOW)

    assert client.get("/api/memory/graph/throughput").json() == {"throughput": None}
    assert probed == []

    posted = client.post("/api/memory/graph/throughput").json()
    assert probed == [8082]
    assert posted["throughput"]["verdict"] == "too_slow"

    cached = client.get("/api/memory/graph/throughput").json()
    assert probed == [8082]
    assert cached["throughput"]["verdict"] == "too_slow"
    assert cached["throughput"]["prompt_tps"] == pytest.approx(12.0)

    status = client.get("/api/memory/graph/status").json()
    assert status["throughput"]["verdict"] == "too_slow"
    assert probed == [8082]


def test_cached_estimate_for_another_slot_is_not_reported(
    client: TestClient, stub: StubWrapper, monkeypatch: pytest.MonkeyPatch
) -> None:
    _fake_slots(monkeypatch, p8082=SLOW)
    client.post("/api/memory/graph/throughput")

    stub.extraction_slot = "agent"

    assert client.get("/api/memory/graph/throughput").json() == {"throughput": None}


def test_unknown_slot_port_is_an_unknown_estimate(
    client: TestClient, stub: StubWrapper, monkeypatch: pytest.MonkeyPatch
) -> None:
    stub.extraction_slot = "missing"

    body = client.post("/api/memory/graph/throughput").json()

    assert body["throughput"]["verdict"] == "unknown"
