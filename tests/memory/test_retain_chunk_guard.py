"""#3215: warn when ``extraction_max_tokens`` does not clear a bank's retain chunk size."""

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
from hal0.config.schema import MemoryGraphConfig
from hal0.memory.extraction_env import ExtractionLimits
from hal0.memory.retain_chunk_guard import audit_bank_chunks, largest_retain_chunk

PLUGIN_BANK = "coding-agent::hal0-mono"


def _cfg(chunk: int = 3000, **strategies: int) -> dict[str, Any]:
    """A GET /banks/{id}/config body: resolved config plus bank overrides."""
    strat = {n: {"retain_chunk_size": c} for n, c in strategies.items()}
    return {
        "bank_id": "x",
        "config": {"retain_chunk_size": chunk, "retain_strategies": strat or None},
        "overrides": {"retain_strategies": strat} if strat else {},
    }


def test_default_cap_clears_the_plugin_chunk() -> None:
    assert MemoryGraphConfig().extraction_max_tokens == 16384
    assert ExtractionLimits().max_tokens == 16384
    assert MemoryGraphConfig().extraction_max_tokens > 12000


def test_largest_chunk_reads_config_strategies_and_overrides() -> None:
    assert largest_retain_chunk(_cfg(3000)) == 3000
    assert largest_retain_chunk(_cfg(3000, gitlog=12000, survey=8000)) == 12000
    only_override = {
        "config": {},
        "overrides": {"retain_strategies": {"a": {"retain_chunk_size": 9000}}},
    }
    assert largest_retain_chunk(only_override) == 9000
    assert largest_retain_chunk({"config": {"retain_chunk_size": True}}) is None
    assert largest_retain_chunk("nope") is None


@pytest.mark.asyncio
async def test_audit_flags_only_banks_the_cap_does_not_clear() -> None:
    bodies = {
        "shared": _cfg(3000),
        PLUGIN_BANK: _cfg(3000, gitlog=12000),
        "edge": _cfg(4096),
    }
    paths: list[str] = []

    async def request_json(method: str, path: str) -> Any:
        paths.append(path)
        return bodies[path.split("/banks/")[1].rsplit("/config", 1)[0].replace("%3A", ":")]

    audit = await audit_bank_chunks(request_json, list(bodies), 4096)

    # strictly above: a cap equal to the chunk size is refused by the engine
    assert [(c.bank_id, c.chunk_size) for c in audit.conflicts] == [
        (PLUGIN_BANK, 12000),
        ("edge", 4096),
    ]
    assert audit.verdict == "warn" and audit.checked == 3
    assert "coding-agent%3A%3Ahal0-mono" in " ".join(paths)  # bank id is URL-quoted
    assert "12000" in (audit.message() or "")
    assert (await audit_bank_chunks(request_json, list(bodies), 16384)).verdict == "ok"


@pytest.mark.asyncio
async def test_unreadable_banks_are_skipped_not_passed() -> None:
    async def boom(method: str, path: str) -> Any:
        raise RuntimeError("engine down")

    audit = await audit_bank_chunks(boom, ["shared"], 4096)

    assert audit.verdict == "unknown" and audit.skipped == 1 and audit.message() is None


# ── routes ────────────────────────────────────────────────────────────────


class _Provider:
    def __init__(self, bodies: dict[str, Any] | None) -> None:
        self.bodies = bodies
        client = MagicMock()
        client.request_json = self._request_json
        self.hindsight_client = client if bodies is not None else None

    def graph_status(self) -> dict[str, Any]:
        return {"enabled": False, "extraction_slot": "utility"}

    def set_graph_enabled(self, enabled: bool, extraction_slot: str | None = None) -> None:
        pass

    async def _request_json(self, method: str, path: str) -> Any:
        assert self.bodies is not None
        if path.startswith("/v1/default/banks?"):
            return {"banks": [{"bank_id": b} for b in self.bodies], "total": len(self.bodies)}
        return self.bodies[path.split("/banks/")[1].rsplit("/config", 1)[0].replace("%3A", ":")]


def _client(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, provider: _Provider, toml: dict[str, Any]
) -> Iterator[TestClient]:
    home = tmp_path / "home"
    (home / "etc" / "hal0").mkdir(parents=True)
    (home / "var-lib").mkdir(parents=True)
    (home / "etc" / "hal0" / "hal0.toml").write_text(
        tomli_w.dumps({"meta": {"schema_version": 1}, **toml})
    )
    monkeypatch.setenv("HAL0_HOME", str(home))
    app = FastAPI()
    error_codes.install(app)
    app.include_router(memory_routes.router, prefix="/api/memory", tags=["memory"])
    app.state.memory_provider = provider
    app.state.slot_manager = MagicMock(iter_configs=AsyncMock(return_value=[]))
    with (
        TestClient(app) as c,
        patch("hal0.memory.extraction_env.apply_extraction_slot") as apply_mock,
    ):
        apply_mock.return_value = {"written": True, "restarted": True, "error": None}
        yield c


BANKS = {"shared": _cfg(3000), PLUGIN_BANK: _cfg(3000, gitlog=12000)}
OLD_TOML = {"memory": {"graph": {"extraction_max_tokens": 4096}}}


@pytest.fixture
def pinned_box(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Iterator[TestClient]:
    yield from _client(tmp_path, monkeypatch, _Provider(BANKS), OLD_TOML)


def test_explicit_old_setting_is_kept_and_the_check_warns(pinned_box: TestClient) -> None:
    body = pinned_box.get("/api/memory/graph/chunk-check").json()["chunk_check"]

    assert body["verdict"] == "warn" and body["max_tokens"] == 4096
    assert body["conflicts"] == [{"bank_id": PLUGIN_BANK, "chunk_size": 12000}]


def test_put_graph_warns_about_an_uncleared_bank(pinned_box: TestClient) -> None:
    r = pinned_box.put("/api/memory/graph", json={"llm_timeout_s": 600})

    assert r.status_code == 200, r.text
    [warning] = r.json()["warnings"]
    assert PLUGIN_BANK in warning and "12000" in warning


def test_put_graph_with_a_clearing_cap_does_not_warn(pinned_box: TestClient) -> None:
    r = pinned_box.put("/api/memory/graph", json={"extraction_max_tokens": 16384})

    assert r.status_code == 200, r.text
    assert "warnings" not in r.json()


def test_absent_key_resolves_to_the_new_default_and_passes(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    (client,) = list(_client(tmp_path, monkeypatch, _Provider(BANKS), {}))[:1]
    body = client.get("/api/memory/graph/chunk-check").json()["chunk_check"]

    assert body["max_tokens"] == 16384 and body["verdict"] == "ok"


def test_unreachable_engine_skips_the_check(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    (client,) = list(_client(tmp_path, monkeypatch, _Provider(None), OLD_TOML))[:1]

    assert client.get("/api/memory/graph/chunk-check").json() == {"chunk_check": None}
    r = client.put("/api/memory/graph", json={"llm_timeout_s": 600})
    assert r.status_code == 200 and "warnings" not in r.json()
