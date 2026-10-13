"""The origin gates honour hal0.toml ``[network]`` (#2382).

``allowed_origins()`` used to return only ``HAL0_ALLOWED_ORIGINS`` (or the
default). That env var lives in the installer-managed api.env block, so a
reverse-proxy origin added there by hand vanished on the next installer run.
Now the allowlist is the env list plus the origin of ``[network].public_url``
plus ``[network].extra_origins``, read live off hal0.toml. These tests cover
the merge, its reload semantics, and both gates (REST middleware and the
chat-proxy WebSocket) accepting a configured origin.
"""

from __future__ import annotations

import os
from collections.abc import Iterator
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from starlette.websockets import WebSocketDisconnect

from hal0.api import auth as auth_mod
from hal0.api.agents import _auth

PROXY = "https://hal0.example.com"
EXTRA = "https://box.example.net"


def _write_network(home: Path, body: str) -> Path:
    etc = home / "etc" / "hal0"
    etc.mkdir(parents=True, exist_ok=True)
    target = etc / "hal0.toml"
    target.write_text(body, encoding="utf-8")
    return target


def _bump_mtime(path: Path) -> None:
    """Guarantee a new mtime even on a coarse-grained filesystem clock."""
    st = path.stat()
    os.utime(path, ns=(st.st_atime_ns, st.st_mtime_ns + 1_000_000_000))


@pytest.fixture
def home(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    h = tmp_path / "home"
    (h / "etc" / "hal0").mkdir(parents=True)
    monkeypatch.setenv("HAL0_HOME", str(h))
    monkeypatch.setenv("HAL0_ALLOWED_ORIGINS", "http://hal0.local,http://192.0.2.20:8080")
    return h


# ── runtime merge ─────────────────────────────────────────────────────────


def test_no_network_section_keeps_env_list(home: Path) -> None:
    """A box upgraded with no [network] table: the allowlist is unchanged."""
    _write_network(home, "[security]\nrequire_auth = false\n")
    assert _auth.allowed_origins() == ("http://hal0.local", "http://192.0.2.20:8080")


def test_no_config_file_keeps_env_list(home: Path) -> None:
    assert _auth.allowed_origins() == ("http://hal0.local", "http://192.0.2.20:8080")


def test_merge_appends_public_url_origin_and_extra_origins(home: Path) -> None:
    _write_network(
        home,
        "[network]\n"
        'public_url = "https://hal0.example.com/"\n'
        'extra_origins = ["http://box.lan:8080", "https://box.example.net"]\n',
    )
    assert _auth.allowed_origins() == (
        "http://hal0.local",
        "http://192.0.2.20:8080",
        PROXY,
        "http://box.lan:8080",
        EXTRA,
    )


def test_public_url_with_non_default_port_and_path(home: Path) -> None:
    _write_network(home, '[network]\npublic_url = "https://hal0.example.com:8443/hal0/"\n')
    origins = _auth.allowed_origins()
    assert origins[-1] == "https://hal0.example.com:8443"
    assert not any(o.endswith("/hal0") for o in origins)


def test_merge_is_deduplicated(home: Path) -> None:
    """An origin in both the env list and [network] appears once, env order first."""
    _write_network(
        home,
        "[network]\n"
        'public_url = "http://hal0.local"\n'
        'extra_origins = ["http://192.0.2.20:8080", "https://hal0.local", "http://hal0.local"]\n',
    )
    origins = _auth.allowed_origins()
    assert origins == ("http://hal0.local", "http://192.0.2.20:8080", "https://hal0.local")
    assert len(origins) == len(set(origins))


def test_merge_on_top_of_default_when_env_unset(
    home: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.delenv("HAL0_ALLOWED_ORIGINS")
    _write_network(home, f'[network]\nextra_origins = ["{EXTRA}"]\n')
    origins = _auth.allowed_origins()
    assert origins[: len(_auth.DEFAULT_ALLOWED_ORIGINS)] == _auth.DEFAULT_ALLOWED_ORIGINS
    assert origins[-1] == EXTRA


def test_change_applies_on_next_call_without_restart(home: Path) -> None:
    """Cached on (path, mtime): a rewrite (Settings save, CLI, hand edit) is seen
    on the next request; an unchanged file is not re-parsed."""
    target = _write_network(home, f'[network]\nextra_origins = ["{EXTRA}"]\n')
    assert EXTRA in _auth.allowed_origins()

    target.write_text('[network]\nextra_origins = ["http://other.lan:8080"]\n', encoding="utf-8")
    _bump_mtime(target)
    origins = _auth.allowed_origins()
    assert origins == ("http://hal0.local", "http://192.0.2.20:8080", "http://other.lan:8080")


def test_unchanged_file_is_served_from_cache(home: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    _write_network(home, f'[network]\nextra_origins = ["{EXTRA}"]\n')
    assert EXTRA in _auth.allowed_origins()

    import hal0.config.loader as loader

    def _boom(*_a: object, **_k: object) -> None:
        raise AssertionError("re-parsed hal0.toml although its mtime did not change")

    monkeypatch.setattr(loader, "load_hal0_config", _boom)
    assert EXTRA in _auth.allowed_origins()


def test_replaced_file_with_same_mtime_and_size_is_reread(home: Path) -> None:
    """The cache key carries the inode: an atomic replace (every hal0 writer
    renames a temp file over hal0.toml) that lands with the same mtime and
    size still revokes the removed origin on the next request."""
    other = "https://box.example.org"  # same length as EXTRA
    assert len(other) == len(EXTRA)
    target = _write_network(home, f'[network]\nextra_origins = ["{EXTRA}"]\n')
    assert _auth.allowed_origins().count(EXTRA) == 1
    st = target.stat()

    replacement = target.with_name("hal0.toml.new")
    replacement.write_text(f'[network]\nextra_origins = ["{other}"]\n', encoding="utf-8")
    os.utime(replacement, ns=(st.st_atime_ns, st.st_mtime_ns))
    os.replace(replacement, target)
    assert (target.stat().st_mtime_ns, target.stat().st_size) == (st.st_mtime_ns, st.st_size)

    origins = _auth.allowed_origins()
    assert origins.count(EXTRA) == 0
    assert origins.count(other) == 1


def test_broken_config_degrades_to_env_list(home: Path) -> None:
    """A malformed hal0.toml never makes the gate raise; the env list still applies."""
    _write_network(home, '[network]\nextra_origins = ["not an origin"]\n')
    assert _auth.allowed_origins() == ("http://hal0.local", "http://192.0.2.20:8080")


# ── REST gate (hal0.api.auth._origin_allowed) ─────────────────────────────


def _scope(origin: str, host: str = "192.0.2.20:8080") -> dict[str, object]:
    return {
        "type": "http",
        "method": "POST",
        "scheme": "http",
        "path": "/api/settings",
        "headers": [(b"origin", origin.encode()), (b"host", host.encode())],
    }


def test_rest_gate_accepts_configured_extra_origin(home: Path) -> None:
    _write_network(home, f'[network]\nextra_origins = ["{EXTRA}"]\n')
    assert auth_mod._origin_allowed(_scope(EXTRA)) is True


def test_rest_gate_accepts_public_url_origin(home: Path) -> None:
    _write_network(home, '[network]\npublic_url = "https://hal0.example.com:8443/"\n')
    assert auth_mod._origin_allowed(_scope("https://hal0.example.com:8443")) is True


def test_rest_gate_still_rejects_unlisted_origin(home: Path) -> None:
    _write_network(home, f'[network]\nextra_origins = ["{EXTRA}"]\n')
    assert auth_mod._origin_allowed(_scope("https://evil.example.com")) is False


@pytest.fixture
def hardened_app(
    home: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> Iterator[TestClient]:
    """Auth enforced, admin key set, so the Origin gate is live on every route."""
    from hal0.api import create_app

    monkeypatch.setenv("HAL0_AGENT_SECRET_PATH", str(tmp_path / "secret.bin"))
    monkeypatch.setenv("HAL0_REQUIRE_AUTH", "1")
    monkeypatch.setenv("HAL0_ADMIN_KEY", "admin-xyz")
    with TestClient(create_app()) as c:
        yield c


def _put(client: TestClient, body: dict[str, object], origin: str | None = None):  # type: ignore[no-untyped-def]
    headers = {"Authorization": "Bearer admin-xyz", "Host": "192.0.2.20:8080"}
    if origin is not None:
        headers["Origin"] = origin
    return client.put("/api/settings", headers=headers, json=body)


def test_settings_save_allows_origin_on_next_request(hardened_app: TestClient) -> None:
    """End to end: the origin is refused, an admin saves it to [network] through
    PUT /api/settings, and the very next request from it passes. No restart."""
    refused = _put(hardened_app, {}, origin=EXTRA)
    assert refused.status_code == 403
    assert refused.json()["error"]["code"] == "auth.origin_forbidden"

    saved = _put(hardened_app, {"network": {"extra_origins": [EXTRA + "/"]}})
    assert saved.status_code == 200, saved.text
    assert saved.json()["network"]["extra_origins"] == [EXTRA]

    allowed = _put(hardened_app, {}, origin=EXTRA)
    assert allowed.status_code == 200, allowed.text


def test_settings_save_rejects_malformed_origin(hardened_app: TestClient) -> None:
    resp = _put(hardened_app, {"network": {"extra_origins": ["https://box.example.net/app"]}})
    assert resp.status_code == 400, resp.text
    err = resp.json()["error"]
    assert err["code"] == "config.invalid"
    assert "no path" in err["details"]["network.extra_origins"]


def _edit(client: TestClient, body: dict[str, object]):  # type: ignore[no-untyped-def]
    headers = {"Authorization": "Bearer admin-xyz", "Host": "192.0.2.20:8080"}
    return client.post("/api/settings/network", headers=headers, json=body)


def _changed_paths(resp) -> list[str]:  # type: ignore[no-untyped-def]
    return [c["path"] for c in resp.json()["_hal0"]["changeset"]["changes"]]


def test_network_edit_applies_to_the_list_on_disk(home: Path, hardened_app: TestClient) -> None:
    """POST /api/settings/network edits the list read inside the config
    transaction, so a change made elsewhere since the client last read it
    (here a hand edit the running API never saw) is kept, not overwritten."""
    assert _put(hardened_app, {"network": {"extra_origins": [EXTRA]}}).status_code == 200
    _write_network(home, f'[network]\nextra_origins = ["{EXTRA}", "http://box.lan:8080"]\n')

    added = _edit(hardened_app, {"add_origins": ["HTTPS://Proxy.Example.com:443/"]})
    assert added.status_code == 200, added.text
    assert added.json()["network"]["extra_origins"] == [
        EXTRA,
        "http://box.lan:8080",
        "https://proxy.example.com",
    ]
    assert _changed_paths(added) == ["network.extra_origins"]

    removed = _edit(hardened_app, {"remove_origins": [EXTRA + "/"]})
    assert removed.status_code == 200, removed.text
    assert removed.json()["network"]["extra_origins"] == [
        "http://box.lan:8080",
        "https://proxy.example.com",
    ]
    assert _auth.allowed_origins().count(EXTRA) == 0


def test_network_edit_noop_reports_no_change(hardened_app: TestClient) -> None:
    assert _edit(hardened_app, {"add_origins": [EXTRA]}).status_code == 200
    again = _edit(hardened_app, {"add_origins": [EXTRA], "remove_origins": ["http://gone.lan"]})
    assert again.status_code == 200, again.text
    assert again.json()["network"]["extra_origins"] == [EXTRA]
    assert _changed_paths(again) == []


def test_network_edit_sets_and_clears_public_url(hardened_app: TestClient) -> None:
    resp = _edit(hardened_app, {"public_url": "https://Hal0.Example.com/"})
    assert resp.status_code == 200, resp.text
    assert resp.json()["network"]["public_url"] == PROXY
    assert _auth.allowed_origins().count(PROXY) == 1

    cleared = _edit(hardened_app, {"public_url": None})
    assert cleared.status_code == 200, cleared.text
    assert cleared.json()["network"]["public_url"] is None
    assert _auth.allowed_origins().count(PROXY) == 0


@pytest.mark.parametrize(
    "bad",
    [
        "https://box.example.net/app",
        "javascript://box.example.net",
        "ws://box.example.net",
        "https://*.example.net",
        "https://user@box.example.net",
        "null",
        "",
    ],
)
def test_network_edit_refuses_a_bad_origin_and_writes_nothing(
    hardened_app: TestClient, bad: str
) -> None:
    """One bad entry refuses the whole edit: the good entry beside it is not added."""
    resp = _edit(hardened_app, {"add_origins": ["http://good.lan:8080", bad]})
    assert resp.status_code == 400, resp.text
    assert resp.json()["error"]["code"] == "config.invalid"
    assert "network.extra_origins" in resp.json()["error"]["details"]
    assert _auth.allowed_origins().count("http://good.lan:8080") == 0


@pytest.mark.parametrize(
    "body",
    [
        {"extra_origins": [EXTRA]},  # whole-list writes belong to PUT /api/settings
        {"add_origins": EXTRA},
        {"add_origins": [1]},
        {"public_url": 5},
    ],
)
def test_network_edit_refuses_malformed_bodies(hardened_app: TestClient, body: dict) -> None:  # type: ignore[type-arg]
    resp = _edit(hardened_app, body)
    assert resp.status_code == 400, resp.text
    assert _auth.allowed_origins().count(EXTRA) == 0


def test_network_edit_refuses_a_bad_public_url(hardened_app: TestClient) -> None:
    resp = _edit(hardened_app, {"public_url": "ftp://hal0.example.com", "add_origins": [EXTRA]})
    assert resp.status_code == 400, resp.text
    assert resp.json()["error"]["code"] == "config.invalid"
    assert _auth.allowed_origins().count(EXTRA) == 0


def test_network_edit_requires_admin(hardened_app: TestClient) -> None:
    resp = hardened_app.post(
        "/api/settings/network",
        headers={"Host": "192.0.2.20:8080"},
        json={"add_origins": [EXTRA]},
    )
    assert resp.status_code in (401, 403), resp.text
    assert _auth.allowed_origins().count(EXTRA) == 0


def test_websocket_upgrade_accepts_configured_origin(home: Path, hardened_app: TestClient) -> None:
    """The REST middleware's WS branch: a configured origin is not refused 4403."""
    _write_network(home, f'[network]\nextra_origins = ["{EXTRA}"]\n')
    with hardened_app.websocket_connect(
        "/api/board/events",
        headers={"origin": EXTRA, "authorization": "Bearer admin-xyz"},
    ):
        pass


# ── chat-proxy WS gate (check_ws_origin_and_cookie) ───────────────────────


@pytest.fixture
def chat_client(
    home: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> Iterator[TestClient]:
    from starlette.websockets import WebSocket

    from hal0.api import create_app
    from hal0.api.agents import chat_proxy

    async def _stub_proxy_ws(browser_ws: WebSocket, **_: object) -> None:
        await browser_ws.send_text("ok")
        await browser_ws.close()

    monkeypatch.setattr(chat_proxy, "_proxy_ws", _stub_proxy_ws)
    monkeypatch.setenv("HAL0_AGENT_SECRET_PATH", str(tmp_path / "secret.bin"))
    monkeypatch.setenv("HAL0_ALLOWED_ORIGINS", "http://127.0.0.1:8080")
    with TestClient(create_app()) as c:
        yield c


def _chat_ws_close_code(client: TestClient, origin: str) -> int | None:
    resp = client.get("/api/agents/hermes/session/handshake")
    assert resp.status_code == 200, resp.text
    client.cookies.set(_auth.SESSION_COOKIE_NAME, resp.cookies[_auth.SESSION_COOKIE_NAME])
    try:
        with client.websocket_connect(
            "/api/agents/hermes/events", headers={"origin": origin, "host": "192.0.2.20:8080"}
        ) as ws:
            assert ws.receive_text() == "ok"
    except WebSocketDisconnect as exc:
        return exc.code
    return None


def test_chat_ws_gate_accepts_configured_extra_origin(home: Path, chat_client: TestClient) -> None:
    _write_network(home, f'[network]\nextra_origins = ["{EXTRA}"]\n')
    assert _chat_ws_close_code(chat_client, EXTRA) is None


def test_chat_ws_gate_accepts_public_url_origin(home: Path, chat_client: TestClient) -> None:
    _write_network(home, f'[network]\npublic_url = "{PROXY}/"\n')
    assert _chat_ws_close_code(chat_client, PROXY) is None


def test_chat_ws_gate_still_rejects_unlisted_origin(home: Path, chat_client: TestClient) -> None:
    _write_network(home, f'[network]\nextra_origins = ["{EXTRA}"]\n')
    assert _chat_ws_close_code(chat_client, "https://evil.example.com") == 4403
