"""``hermes_upstreams``: one pinned remote upstream per served Hermes profile."""

from __future__ import annotations

from pathlib import Path

import pytest

from hal0.agents import hermes_upstreams
from hal0.agents.hermes_api import env_key_name
from hal0.agents.hermes_upstreams import build_hermes_upstream, reconcile_hermes_upstreams
from hal0.upstreams.registry import Upstream, UpstreamRegistry

DEFAULT_KEY = "defaultkey_abcdefghijklmnop0123"
CODER_KEY = "abcdefghijklmnopqrstuvwxyz012345"


@pytest.fixture(autouse=True)
def _clean(monkeypatch: pytest.MonkeyPatch) -> None:
    for name in ("HERMES_HOME", "API_SERVER_HOST", "API_SERVER_PORT"):
        monkeypatch.delenv(name, raising=False)
    hermes_upstreams._WARNED.clear()


@pytest.fixture
def home(tmp_path: Path) -> Path:
    h = tmp_path / "hermes"
    (h / "profiles" / "coder").mkdir(parents=True)
    (h / "profiles" / "nokey").mkdir()
    (h / ".env").write_text(f"API_SERVER_KEY={DEFAULT_KEY}\n")
    (h / "profiles" / "coder" / ".env").write_text(f"API_SERVER_KEY={CODER_KEY}\n")
    return h


def _probe(status: dict[str, int] | None = None):
    table = {"coder": 200, "nokey": 200, **(status or {})}

    def probe(url: str) -> int:
        return table.get(url.split("/p/")[1].split("/")[0], 404)

    return probe


def _patch_models(monkeypatch: pytest.MonkeyPatch, registry: UpstreamRegistry, by_name):
    async def fetch(name: str) -> list[str]:
        return by_name.get(name, [])

    monkeypatch.setattr(registry, "fetch_models", fetch)


async def test_reconcile_registers_default_and_named(
    home: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    reg = UpstreamRegistry()
    _patch_models(monkeypatch, reg, {"hermes": ["hermes-agent"], "hermes.coder": ["coder"]})
    env: dict[str, str] = {}
    report = await reconcile_hermes_upstreams(reg, home=home, probe=_probe(), environ=env)
    assert {u.name for u in reg.list()} == {"hermes", "hermes.coder"}
    assert reg.get("hermes").url.endswith("/v1")  # type: ignore[union-attr]
    assert reg.get("hermes.coder").url.endswith("/p/coder/v1")  # type: ignore[union-attr]
    assert env[env_key_name("default")] == DEFAULT_KEY
    assert env[env_key_name("coder")] == CODER_KEY
    assert report["registered"] == ["hermes", "hermes.coder"]


async def test_reconcile_skips_profile_without_key(
    home: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    reg = UpstreamRegistry()
    _patch_models(monkeypatch, reg, {})
    env: dict[str, str] = {}
    report = await reconcile_hermes_upstreams(reg, home=home, probe=_probe(), environ=env)
    assert reg.get("hermes.nokey") is None
    assert report["profiles_without_key"] == ["nokey"]
    assert env_key_name("nokey") not in env


async def test_reconcile_removes_unserved_managed_entry(
    home: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    reg = UpstreamRegistry()
    _patch_models(monkeypatch, reg, {})
    env: dict[str, str] = {}
    await reconcile_hermes_upstreams(reg, home=home, probe=_probe(), environ=env)
    assert reg.get("hermes.coder") is not None
    report = await reconcile_hermes_upstreams(
        reg, home=home, probe=_probe({"coder": 404}), environ=env
    )
    assert reg.get("hermes.coder") is None
    assert env_key_name("coder") not in env
    assert report["removed"] == ["hermes.coder"]
    assert reg.get("hermes") is not None


async def test_reconcile_skips_toml_authored_name(
    home: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    reg = UpstreamRegistry()
    _patch_models(monkeypatch, reg, {})
    authored = Upstream(name="hermes", kind="remote", url="http://elsewhere/v1")
    reg.add(authored)
    env: dict[str, str] = {}
    report = await reconcile_hermes_upstreams(reg, home=home, probe=_probe(), environ=env)
    assert reg.get("hermes") is authored
    assert report["skipped"] == ["hermes"]
    assert "hermes" not in report["registered"]


async def test_reconcile_sets_owned_models_from_live_models(
    home: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    reg = UpstreamRegistry()
    _patch_models(monkeypatch, reg, {"hermes.coder": ["coder"]})
    await reconcile_hermes_upstreams(reg, home=home, probe=_probe(), environ={})
    assert reg.get("hermes.coder").owned_models == ("coder",)  # type: ignore[union-attr]
    assert reg.get("hermes").owned_models == ("hermes-agent",)  # type: ignore[union-attr]


async def test_reconcile_owned_models_merges_live_ids(
    home: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    reg = UpstreamRegistry()
    _patch_models(monkeypatch, reg, {"hermes": ["other", "hermes-agent"]})
    await reconcile_hermes_upstreams(reg, home=home, probe=_probe(), environ={})
    assert reg.get("hermes").owned_models == ("hermes-agent", "other")  # type: ignore[union-attr]


def test_build_hermes_upstream_fields() -> None:
    u = build_hermes_upstream("default", host="127.0.0.1", port=8642, owned=("hermes-agent",))
    assert u.name == "hermes"
    assert u.url == "http://127.0.0.1:8642/v1"
    c = build_hermes_upstream("coder", host="h", port=9, owned=("coder",))
    assert c.name == "hermes.coder"
    assert c.url == "http://h:9/p/coder/v1"
    assert c.kind == "remote"
    assert c.auth_style == "bearer"
    assert c.auth_value_env == env_key_name("coder")
    assert c.timeout_seconds == 600
    assert c.health_path == "/health"
    assert c.advertise_models is True
    assert c.hal0_peer is False
    assert c.managed_by == "agent:hermes"
    assert c.pinned is True
    assert c.owned_models == ("coder",)
    assert c.stream_idle_timeout_s == 600
    assert c.stream_total_timeout_s == 1800
