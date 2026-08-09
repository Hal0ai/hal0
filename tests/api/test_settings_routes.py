"""Tests for /api/settings — typed read/write of hal0.toml.

Uses ``tmp_hal0_home`` so writes land in a tmp dir, not /etc.
"""

from __future__ import annotations

import asyncio
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from hal0.api import create_app
from hal0.api.routes import settings as settings_routes
from hal0.config.loader import HAL0_TOML_LOCK


@pytest.fixture
def isolated_client(tmp_hal0_home: str) -> Iterator[TestClient]:
    """A TestClient whose lifespan resolves paths under tmp_hal0_home.

    The shared ``client`` fixture in conftest.py builds the app *before*
    the per-test monkeypatch sets HAL0_HOME; we instantiate inside the
    fixture instead so writes land in the tmp dir.
    """
    app: FastAPI = create_app()
    with TestClient(app) as c:
        yield c


def test_get_settings_returns_default_config_when_no_toml(isolated_client: TestClient) -> None:
    """GET /api/settings on a fresh install returns the all-defaults shape."""
    r = isolated_client.get("/api/settings")
    assert r.status_code == 200, r.text
    body = r.json()
    # Shape sanity: each top-level table is present.
    assert "meta" in body
    assert "slots" in body
    assert "dispatcher" in body
    assert "telemetry" in body
    # Schema defaults.
    assert body["meta"]["schema_version"] == 1
    assert body["telemetry"]["enabled"] is False
    assert body["telemetry"]["channel"] == "stable"


def test_put_settings_reloads_disk_instead_of_a_stale_cache(
    isolated_client: TestClient, tmp_hal0_home: str
) -> None:
    """#1717 review: routes/memory.py's PUT /graph writes its section
    straight to disk without touching ``app.state.hal0_config``. A settings
    PUT that resumes after waiting on the shared HAL0_TOML_LOCK must reload
    from disk rather than trusting that (now stale) cache — otherwise it
    merges its own unrelated patch into the pre-graph-write snapshot and
    overwrites the section the other route just saved."""
    # Simulate exactly that: hal0.toml on disk now has a graph section the
    # app's in-memory ``hal0_config`` (still whatever create_app()'s
    # lifespan loaded at startup) knows nothing about.
    cfg_dir = Path(tmp_hal0_home) / "etc" / "hal0"
    cfg_dir.mkdir(parents=True, exist_ok=True)
    (cfg_dir / "hal0.toml").write_text(
        '[memory.graph]\nenabled = true\nextraction_slot = "agent"\n',
        encoding="utf-8",
    )

    # An unrelated PUT — must not clobber the graph section above.
    r = isolated_client.put("/api/settings", json={"telemetry": {"enabled": True}})
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["telemetry"]["enabled"] is True
    assert body["memory"]["graph"]["enabled"] is True
    assert body["memory"]["graph"]["extraction_slot"] == "agent"

    # And the on-disk file reflects both, not just the settings PUT's patch.
    toml_path = cfg_dir / "hal0.toml"
    import tomllib

    parsed = tomllib.loads(toml_path.read_text(encoding="utf-8"))
    assert parsed["telemetry"]["enabled"] is True
    assert parsed["memory"]["graph"]["extraction_slot"] == "agent"


def test_put_settings_partial_update_persists_to_disk(
    isolated_client: TestClient, tmp_hal0_home: str
) -> None:
    """PUT /api/settings deep-merges and writes hal0.toml atomically."""
    r = isolated_client.put(
        "/api/settings",
        json={"telemetry": {"enabled": True}, "dispatcher": {"prefetch_timeout_s": 12.0}},
    )
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["telemetry"]["enabled"] is True
    assert body["dispatcher"]["prefetch_timeout_s"] == 12.0
    # Channel default survives the merge — not clobbered by the partial PUT.
    assert body["telemetry"]["channel"] == "stable"

    # On-disk file exists and parses back to the same shape.
    toml_path = Path(tmp_hal0_home) / "etc" / "hal0" / "hal0.toml"
    assert toml_path.exists(), f"expected hal0.toml at {toml_path}"
    raw = toml_path.read_bytes()
    # Sanity: not empty, parseable.
    import tomllib

    parsed = tomllib.loads(raw.decode("utf-8"))
    assert parsed["telemetry"]["enabled"] is True


def test_put_settings_validation_error_envelope(isolated_client: TestClient) -> None:
    """Schema-failing payload returns config.invalid with per-field details."""
    r = isolated_client.put(
        "/api/settings",
        json={"telemetry": {"channel": "nonsense"}},
    )
    assert r.status_code == 400, r.text
    body = r.json()
    assert body["error"]["code"] == "config.invalid"
    # Per-field details: at least the offending path must be present.
    details = body["error"]["details"]
    assert any("channel" in k for k in details), f"no channel path in details: {details}"


def test_put_settings_non_json_body_returns_envelope(isolated_client: TestClient) -> None:
    """Non-JSON body fails with the typed envelope, not a stack trace."""
    r = isolated_client.put(
        "/api/settings",
        content=b"not json",
        headers={"content-type": "application/json"},
    )
    assert r.status_code in (400, 500)
    assert "error" in r.json()


def test_reload_settings_re_reads_disk(isolated_client: TestClient, tmp_hal0_home: str) -> None:
    """POST /api/settings/reload re-reads hal0.toml from disk."""
    # Hand-write a TOML to disk, then ask the route to reload.
    cfg_dir = Path(tmp_hal0_home) / "etc" / "hal0"
    cfg_dir.mkdir(parents=True, exist_ok=True)
    (cfg_dir / "hal0.toml").write_text(
        '[telemetry]\nenabled = true\nchannel = "nightly"\n',
        encoding="utf-8",
    )
    r = isolated_client.post("/api/settings/reload")
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["telemetry"]["enabled"] is True
    assert body["telemetry"]["channel"] == "nightly"


def test_settings_schema_returns_json_schema(isolated_client: TestClient) -> None:
    """GET /api/settings/schema returns the pydantic JSON schema."""
    r = isolated_client.get("/api/settings/schema")
    assert r.status_code == 200, r.text
    body = r.json()
    # pydantic v2 puts top-level model under "properties"
    assert "properties" in body
    assert "telemetry" in body["properties"]


def test_reload_after_bad_toml_returns_parse_error_envelope(
    isolated_client: TestClient, tmp_hal0_home: str
) -> None:
    """Malformed TOML on disk surfaces as the typed config.parse_error envelope."""
    cfg_dir = Path(tmp_hal0_home) / "etc" / "hal0"
    cfg_dir.mkdir(parents=True, exist_ok=True)
    (cfg_dir / "hal0.toml").write_text("this is not = valid = toml\n", encoding="utf-8")
    r = isolated_client.post("/api/settings/reload")
    assert r.status_code in (400, 500)
    body = r.json()
    assert body["error"]["code"].startswith("config."), body


class _FakeAppState:
    pass


class _FakeApp:
    def __init__(self) -> None:
        self.state = _FakeAppState()


class _FakeRequest:
    """Minimal stand-in for a Starlette ``Request`` — just enough surface for
    ``update_settings`` (``.json()`` + ``.app.state``) without a real ASGI
    app or TestClient, so the lock-sharing test can drive the coroutine
    directly instead of going through HTTP transport plumbing."""

    def __init__(self, body: dict[str, Any]) -> None:
        self._body = body
        self.app = _FakeApp()

    async def json(self) -> dict[str, Any]:
        return self._body


async def test_update_settings_waits_for_shared_hal0_toml_lock(tmp_hal0_home: str) -> None:
    """#1682 review: routes/memory.py's ``PUT /graph`` can hold ``HAL0_TOML_LOCK``
    across a multi-second extraction-slot propagation. Without a lock SHARED
    across both routes, an unrelated settings save landing mid-flight would
    persist a config snapshot taken before that propagation started,
    clobbering the graph section it just wrote. Simulate the graph route
    holding the lock and assert the settings route genuinely waits rather
    than racing straight past it."""
    await HAL0_TOML_LOCK.acquire()
    try:
        task = asyncio.ensure_future(
            settings_routes.update_settings(_FakeRequest({"telemetry": {"enabled": True}}))
        )
        await asyncio.sleep(0.05)
        assert not task.done(), "update_settings must block on the shared lock"
    finally:
        HAL0_TOML_LOCK.release()

    result = await task
    assert result["telemetry"]["enabled"] is True
