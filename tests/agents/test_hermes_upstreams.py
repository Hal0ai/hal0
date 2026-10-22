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
    for name in ("HERMES_HOME", "API_SERVER_HOST", "API_SERVER_PORT", "API_SERVER_MODEL_NAME"):
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


def _probe(status: dict[str, int] | None = None, *, gateway: int = 200):
    """``gateway`` answers the default ``/health``; ``status`` the ``/p/<p>/health`` probes."""
    table = {"coder": 200, "nokey": 200, **(status or {})}

    def probe(url: str) -> int:
        if "/p/" not in url:
            return gateway
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


async def test_reconcile_owned_models_are_the_derived_ids(
    home: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    reg = UpstreamRegistry()
    _patch_models(monkeypatch, reg, {"hermes.coder": ["coder"]})
    await reconcile_hermes_upstreams(reg, home=home, probe=_probe(), environ={})
    assert reg.get("hermes.coder").owned_models == ("coder",)  # type: ignore[union-attr]
    assert reg.get("hermes").owned_models == ("hermes-agent",)  # type: ignore[union-attr]


async def test_reconcile_empty_fetch_keeps_default_owned(
    home: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    reg = UpstreamRegistry()
    _patch_models(monkeypatch, reg, {})
    await reconcile_hermes_upstreams(reg, home=home, probe=_probe(), environ={})
    assert reg.get("hermes.coder").owned_models == ("coder",)  # type: ignore[union-attr]
    assert reg.get("hermes").owned_models == ("hermes-agent",)  # type: ignore[union-attr]


async def test_reconcile_real_fetch_authenticates_from_os_environ(
    home: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    import os

    for k in [k for k in os.environ if k.startswith("HAL0_HERMES_API_KEY__")]:
        monkeypatch.delenv(k)
    seen: list[str] = []

    class _Resp:
        def __init__(self, status: int, body: dict) -> None:
            self.status_code = status
            self._body = body

        def json(self) -> dict:
            return self._body

    class _Client:
        async def get(self, url: str, headers: dict | None = None, **_: object) -> _Resp:
            auth = (headers or {}).get("Authorization")
            seen.append(f"{url} {auth}")
            if "/p/coder/" in url and auth == f"Bearer {CODER_KEY}":
                return _Resp(200, {"data": [{"id": "coder"}]})
            return _Resp(401, {})

    reg = UpstreamRegistry()
    monkeypatch.setattr(reg, "_get_client", lambda: _Client())
    try:
        await reconcile_hermes_upstreams(reg, home=home, probe=_probe())
        assert os.environ[env_key_name("coder")] == CODER_KEY
        # The registry authenticates from os.environ, so the entry reconcile
        # wrote fetches its live ids with the profile's own key.
        assert await reg.fetch_models("hermes.coder") == ["coder"]
        assert f"Bearer {CODER_KEY}" in seen[-1]
    finally:
        for p in ("default", "coder"):
            os.environ.pop(env_key_name(p), None)


async def test_reconcile_live_ids_do_not_become_owned(
    home: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Ruling 9: a ``model_routes`` alias Hermes advertises never confers ownership."""
    reg = UpstreamRegistry()
    _patch_models(
        monkeypatch, reg, {"hermes": ["other", "hermes-agent"], "hermes.coder": ["coder", "x"]}
    )
    await reconcile_hermes_upstreams(reg, home=home, probe=_probe(), environ={})
    assert reg.get("hermes").owned_models == ("hermes-agent",)  # type: ignore[union-attr]
    assert reg.get("hermes.coder").owned_models == ("coder",)  # type: ignore[union-attr]


async def test_reconcile_gateway_down_registers_every_keyed_profile(
    home: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Ruling 8: liveness never decides ownership; a down gateway keeps every keyed id owned."""
    reg = UpstreamRegistry()
    _patch_models(monkeypatch, reg, {})
    env: dict[str, str] = {}
    down = _probe({"coder": 0, "nokey": 0}, gateway=0)
    report = await reconcile_hermes_upstreams(reg, home=home, probe=down, environ=env)
    assert {u.name for u in reg.list()} == {"hermes", "hermes.coder"}
    assert report["registered"] == ["hermes", "hermes.coder"]
    assert report["removed"] == []
    assert reg.get("hermes").owned_models == ("hermes-agent",)  # type: ignore[union-attr]
    assert reg.get("hermes.coder").owned_models == ("coder",)  # type: ignore[union-attr]
    assert all(u.pinned for u in reg.list())
    assert env[env_key_name("coder")] == CODER_KEY


async def test_reconcile_gateway_down_removes_nothing(
    home: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    reg = UpstreamRegistry()
    _patch_models(monkeypatch, reg, {})
    env: dict[str, str] = {}
    await reconcile_hermes_upstreams(reg, home=home, probe=_probe(), environ=env)
    report = await reconcile_hermes_upstreams(
        reg, home=home, probe=_probe({"coder": 404}, gateway=0), environ=env
    )
    assert report["removed"] == []
    assert reg.get("hermes.coder") is not None
    assert env[env_key_name("coder")] == CODER_KEY


async def test_reconcile_keeps_profile_whose_health_is_5xx(
    home: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    reg = UpstreamRegistry()
    _patch_models(monkeypatch, reg, {})
    env: dict[str, str] = {}
    await reconcile_hermes_upstreams(reg, home=home, probe=_probe(), environ=env)
    report = await reconcile_hermes_upstreams(
        reg, home=home, probe=_probe({"coder": 503}), environ=env
    )
    assert report["removed"] == []
    assert "hermes.coder" in report["registered"]
    assert reg.get("hermes.coder") is not None


async def test_reconcile_refreshes_a_rotated_key(
    home: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    reg = UpstreamRegistry()
    _patch_models(monkeypatch, reg, {})
    env: dict[str, str] = {}
    await reconcile_hermes_upstreams(reg, home=home, probe=_probe(), environ=env)
    rotated = "rotatedkey_0123456789abcdefghij"
    (home / "profiles" / "coder" / ".env").write_text(f"API_SERVER_KEY={rotated}\n")
    report = await reconcile_hermes_upstreams(reg, home=home, probe=_probe(), environ=env)
    assert env[env_key_name("coder")] == rotated
    assert "hermes.coder" in report["registered"]
    assert reg.get("hermes.coder").pinned is True  # type: ignore[union-attr]


async def test_reconcile_removes_entry_when_key_vanishes(
    home: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    reg = UpstreamRegistry()
    _patch_models(monkeypatch, reg, {})
    env: dict[str, str] = {}
    await reconcile_hermes_upstreams(reg, home=home, probe=_probe(), environ=env)
    (home / "profiles" / "coder" / ".env").write_text("OTHER=1\n")
    report = await reconcile_hermes_upstreams(reg, home=home, probe=_probe(), environ=env)
    assert report["removed"] == ["hermes.coder"]
    assert report["profiles_without_key"] == ["coder", "nokey"]
    assert reg.get("hermes.coder") is None
    assert env_key_name("coder") not in env


async def test_reconcile_warns_once_per_taken_name(
    home: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from structlog.testing import capture_logs

    reg = UpstreamRegistry()
    _patch_models(monkeypatch, reg, {})
    reg.add(Upstream(name="hermes", kind="remote", url="http://elsewhere/v1"))
    with capture_logs() as logs:
        for _ in range(3):
            await reconcile_hermes_upstreams(reg, home=home, probe=_probe(), environ={})
    taken = [e for e in logs if e["event"] == "hermes_upstreams.name_taken"]
    assert len(taken) == 1
    assert taken[0]["name"] == "hermes"


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


async def test_reconcile_default_owns_configured_model_name(
    home: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    (home / "config.yaml").write_text(
        "gateway:\n  platforms:\n    api_server:\n      model_name: my-agent\n"
    )
    reg = UpstreamRegistry()
    _patch_models(monkeypatch, reg, {})
    await reconcile_hermes_upstreams(reg, home=home, probe=_probe(), environ={})
    assert reg.get("hermes").owned_models == ("my-agent",)  # type: ignore[union-attr]
    assert reg.get("hermes.coder").owned_models == ("coder",)  # type: ignore[union-attr]


async def test_reconcile_skips_profile_whose_env_name_collides(
    home: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    for name, key in (
        ("my-profile", CODER_KEY),
        ("my_profile", "zyxwvutsrqponmlkjihgfedcba987654"),
    ):
        d = home / "profiles" / name
        d.mkdir()
        (d / ".env").write_text(f"API_SERVER_KEY={key}\n")
    reg = UpstreamRegistry()
    _patch_models(monkeypatch, reg, {})
    env: dict[str, str] = {}
    report = await reconcile_hermes_upstreams(
        reg, home=home, probe=_probe({"my-profile": 200, "my_profile": 200}), environ=env
    )
    # Sorted order: "my-profile" claims the name first; "my_profile" is skipped.
    assert reg.get("hermes.my-profile") is not None
    assert reg.get("hermes.my_profile") is None
    assert env[env_key_name("my-profile")] == CODER_KEY
    assert report["env_name_collisions"] == ["my_profile"]
    assert "hermes.my_profile" not in report["registered"]
