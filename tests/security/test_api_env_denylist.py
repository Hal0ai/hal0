"""Names the shared ``api.env`` writer refuses beyond the #2936 loader set.

``/etc/hal0/api.env`` is hal0-api's ``EnvironmentFile=`` and every write also
lands in the live ``os.environ``, so each name below reaches hal0-api and
every child it starts. None of them is a credential; each one changes how
those processes do TLS, find their configuration or route their traffic.
They are refused on set by the secrets routes, the provider credentials
route and the writer itself, in any letter case, and stay deletable so an
entry written before the guard can be removed.
"""

from __future__ import annotations

import os
from collections.abc import Iterator
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from hal0.api._env_store import (
    delete_env_value,
    is_process_control_env,
    list_env_keys,
    upsert_env_value,
)
from hal0.upstreams.registry import Upstream

#: name -> the issue that refused it.
DENIED: dict[str, int] = {
    "SSLKEYLOGFILE": 2997,
}


@pytest.fixture(autouse=True)
def _restore_environ() -> Iterator[None]:
    snapshot = dict(os.environ)
    yield
    for key in set(os.environ) - set(snapshot):
        del os.environ[key]
    for key, value in snapshot.items():
        os.environ[key] = value


def _api_env(home: str) -> Path:
    return Path(home) / "etc" / "hal0" / "api.env"


def _cases(name: str) -> list[str]:
    return [name, name.lower()]


ALL_CASES = [variant for name in DENIED for variant in _cases(name)]


@pytest.mark.parametrize("name", ALL_CASES)
def test_classified_as_process_control(name: str) -> None:
    assert is_process_control_env(name)


@pytest.mark.parametrize("name", ALL_CASES)
def test_writer_refuses(tmp_path: Path, name: str) -> None:
    target = tmp_path / "api.env"
    environ: dict[str, str] = {}
    with pytest.raises(ValueError, match="controls how processes run"):
        upsert_env_value(target, name, "/tmp/x", environ=environ)
    assert not target.exists()
    assert environ == {}


@pytest.mark.parametrize("name", ALL_CASES)
def test_writer_still_deletes_a_planted_entry(tmp_path: Path, name: str) -> None:
    target = tmp_path / "api.env"
    target.write_text(f'{name}="/tmp/x"\nHF_TOKEN="hf_x"\n', encoding="utf-8")
    environ = {name: "/tmp/x"}

    assert delete_env_value(target, name, environ=environ) is True

    assert list_env_keys(target) == ["HF_TOKEN"]
    assert name not in environ


@pytest.mark.parametrize("name", list(DENIED))
@pytest.mark.parametrize("method", ["post", "put"])
def test_secrets_route_refuses(
    client: TestClient, tmp_hal0_home: str, name: str, method: str
) -> None:
    api_env = _api_env(tmp_hal0_home)
    before = api_env.read_bytes() if api_env.exists() else None
    live_before = os.environ.get(name)

    r = getattr(client, method)(f"/api/secrets/{name}", json={"value": "/tmp/x"})

    assert r.status_code == 403, r.text
    err = r.json()["error"]
    assert err["code"] == "secret.protected"
    assert err["details"] == {"name": name, "protected": True, "reason": "process_control"}
    assert (api_env.read_bytes() if api_env.exists() else None) == before
    assert os.environ.get(name) == live_before


@pytest.mark.parametrize("name", [n.lower() for n in DENIED])
def test_secrets_route_refuses_lower_case(client: TestClient, name: str) -> None:
    """The route's name grammar is upper case only, so a lower-case spelling never lands."""
    r = client.put(f"/api/secrets/{name}", json={"value": "/tmp/x"})
    assert r.status_code == 400, r.text
    assert r.json()["error"]["code"] == "secret.name_invalid"
    assert os.environ.get(name) != "/tmp/x"


@pytest.mark.parametrize("name", list(DENIED))
def test_secrets_route_still_deletes_a_planted_entry(
    client: TestClient, tmp_hal0_home: str, name: str
) -> None:
    api_env = _api_env(tmp_hal0_home)
    api_env.parent.mkdir(parents=True, exist_ok=True)
    api_env.write_text(f'{name}="/tmp/x"\nHF_TOKEN="hf_x"\n', encoding="utf-8")

    r = client.delete(f"/api/secrets/{name}")

    assert r.status_code == 204, r.text
    assert list_env_keys(api_env) == ["HF_TOKEN"]


@pytest.mark.parametrize("name", list(DENIED))
def test_credentials_route_refuses(client: TestClient, tmp_hal0_home: str, name: str) -> None:
    reg = client.app.state.upstreams
    reg.add(
        Upstream(
            name="sneaky",
            kind="remote",
            url="https://example.invalid/v1",
            auth_style="bearer",
            auth_value_env=name,
        )
    )
    api_env = _api_env(tmp_hal0_home)
    before = api_env.read_bytes() if api_env.exists() else None
    live_before = os.environ.get(name)

    r = client.post("/api/providers/sneaky/credentials", json={"key": name, "value": "/tmp/x"})

    assert r.status_code == 400, r.text
    assert r.json()["error"]["details"]["reason"] == "process_control"
    assert (api_env.read_bytes() if api_env.exists() else None) == before
    assert os.environ.get(name) == live_before


@pytest.mark.parametrize("name", list(DENIED))
def test_upstream_auth_value_env_refused(client: TestClient, name: str) -> None:
    r = client.post(
        "/api/upstreams",
        json={"name": "sneaky", "url": "https://example.invalid/v1", "auth_value_env": name},
    )
    assert r.status_code == 400, r.text
    assert r.json()["error"]["details"]["reason"] == "process_control"
