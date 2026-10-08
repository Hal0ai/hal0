"""``NO_PROXY``/``no_proxy`` in the Hermes driver env (#2330).

Hermes's MCP client is an ``httpx.AsyncClient`` with the default
``trust_env=True``, so with ``HTTP_PROXY`` set it routes a loopback MCP url
through the proxy unless ``NO_PROXY`` names the host — and the record's
``[secrets]`` headers go to the proxy in clear text. hal0 owns the env file the
``hal0-agent@hermes`` unit loads (``EnvironmentFile=-/etc/hal0/agents/%i.env``),
so :func:`hal0.agents.hermes_provision._write_driver_env` writes both spellings
there: localhost, 127.0.0.1, ::1, every loopback host an exposed record uses,
and whatever the operator already had.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from hal0.agents import hermes_provision as hp
from hal0.mcp import installed


def _env_lines(body: str) -> dict[str, str]:
    out: dict[str, str] = {}
    for line in body.splitlines():
        if line and not line.startswith("#") and "=" in line:
            key, value = line.split("=", 1)
            out[key] = value
    return out


@pytest.fixture
def driver_env(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    target = tmp_path / "agents" / "hermes.env"
    monkeypatch.setattr(hp, "DRIVER_ENV_PATH", target)
    monkeypatch.setattr(hp.os, "geteuid", lambda: 0)
    for name in ("HAL0_ADMIN_KEY", "HAL0_CLIENT_KEY", "NO_PROXY", "no_proxy"):
        monkeypatch.delenv(name, raising=False)
    return target


def _install(server_id: str, url: str, **overrides: object) -> None:
    fields: dict[str, object] = {
        "id": server_id,
        "name": server_id,
        "spec": f"https://{server_id}.hal0.example.com/manifest.json",
        "transport": "streamable-http",
        "url": url,
        "enabled": True,
    }
    fields.update(overrides)
    installed.install(installed.InstalledServer(**fields))


def _no_proxy_entries(target: Path) -> tuple[list[str], list[str]]:
    env = _env_lines(target.read_text())
    return env["NO_PROXY"].split(","), env["no_proxy"].split(",")


def test_driver_env_sets_both_no_proxy_spellings_to_loopback(
    driver_env: Path, tmp_hal0_home: str
) -> None:
    hp._write_driver_env()
    upper, lower = _no_proxy_entries(driver_env)
    # Both spellings, same value: Python's proxy lookup prefers the lowercase
    # one, so an inherited `no_proxy` would otherwise shadow a written NO_PROXY.
    assert upper == lower
    assert upper == ["localhost", "127.0.0.1", "::1"]


def test_driver_env_covers_loopback_hosts_of_exposed_records(
    driver_env: Path, tmp_hal0_home: str
) -> None:
    _install(
        "local-a",
        "http://127.0.0.2:9000/mcp",
        exposure=installed.ExposureConfig(hermes=True),
    )
    _install(
        "local-b",
        "http://127.0.0.3:9000/mcp",
        exposure=installed.ExposureConfig(brain=True),
    )
    # Not exposed, disabled, or not loopback: none of these needs an entry.
    _install("local-c", "http://127.0.0.4:9000/mcp")
    _install(
        "local-d",
        "http://127.0.0.5:9000/mcp",
        enabled=False,
        exposure=installed.ExposureConfig(hermes=True),
    )
    _install(
        "remote",
        "https://mcp.hal0.example.com/mcp",
        exposure=installed.ExposureConfig(hermes=True),
    )

    hp._write_driver_env()
    upper, lower = _no_proxy_entries(driver_env)
    assert upper == lower
    assert upper == ["localhost", "127.0.0.1", "::1", "127.0.0.2", "127.0.0.3"]


def test_driver_env_keeps_the_operators_proxy_exclusions(
    driver_env: Path, tmp_hal0_home: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The file's value wins over the unit's inherited one, so it must carry
    the operator's own entries rather than replace them."""
    monkeypatch.setenv("NO_PROXY", ".hal0.example.com,10.0.0.0")
    monkeypatch.setenv("no_proxy", "intranet,localhost")
    hp._write_driver_env()
    upper, lower = _no_proxy_entries(driver_env)
    assert upper == lower
    assert upper == [".hal0.example.com", "10.0.0.0", "intranet", "localhost", "127.0.0.1", "::1"]


def test_driver_env_keeps_a_value_already_in_the_file(driver_env: Path, tmp_hal0_home: str) -> None:
    driver_env.parent.mkdir(parents=True)
    driver_env.write_text("HAL0_API_URL=http://127.0.0.1:8080\nNO_PROXY=192.0.2.10\n")
    hp._write_driver_env()
    upper, lower = _no_proxy_entries(driver_env)
    assert upper == lower
    assert upper == ["192.0.2.10", "localhost", "127.0.0.1", "::1"]


def test_driver_env_no_proxy_is_stable_across_rewrites(
    driver_env: Path, tmp_hal0_home: str
) -> None:
    _install(
        "local-a",
        "http://127.0.0.2:9000/mcp",
        exposure=installed.ExposureConfig(hermes=True),
    )
    _, wrote = hp._write_driver_env()
    assert wrote is True
    _, wrote = hp._write_driver_env()
    assert wrote is False  # merging the file's own value back in is a no-op


def test_driver_env_keeps_the_exclusions_in_hal0_apis_env_file(
    driver_env: Path, tmp_hal0_home: str
) -> None:
    """api.env is where the docs send an operator's own NO_PROXY: readable by
    the hal0 user, so a reprovision run from a shell without it keeps it too."""
    from hal0.config import paths

    paths.api_env().parent.mkdir(parents=True, exist_ok=True)
    paths.api_env().write_text("HAL0_PORT=8080\nno_proxy=.hal0.example.com\n")
    hp._write_driver_env()
    upper, lower = _no_proxy_entries(driver_env)
    assert upper == lower
    assert upper == [".hal0.example.com", "localhost", "127.0.0.1", "::1"]
