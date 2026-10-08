"""Unit tests for :mod:`hal0.mcp.installed` — #305 registry layer."""

from __future__ import annotations

import os

import pytest

from hal0.config.schema import ToolPolicy
from hal0.errors import BadRequest, Conflict, NotFound
from hal0.mcp import installed as registry
from hal0.mcp.installed import ExposureConfig


def _record(server_id: str = "filesystem", **overrides: object) -> registry.InstalledServer:
    defaults: dict[str, object] = {
        "id": server_id,
        "name": server_id,
        "description": "filesystem MCP",
        "spec": "uvx:mcp-server-filesystem",
        "transport": "stdio",
        "tools": 5,
    }
    defaults.update(overrides)
    return registry.InstalledServer(**defaults)


def test_list_installed_empty(tmp_hal0_home: str) -> None:
    assert registry.list_installed() == []


def test_install_and_list_round_trip(tmp_hal0_home: str) -> None:
    saved = registry.install(_record())
    assert saved.id == "filesystem"
    assert saved.installed_at  # auto-stamped

    rows = registry.list_installed()
    assert len(rows) == 1
    assert rows[0].id == "filesystem"
    assert rows[0].installed_at == saved.installed_at


def test_install_rejects_duplicate(tmp_hal0_home: str) -> None:
    registry.install(_record())
    with pytest.raises(Conflict) as exc:
        registry.install(_record())
    assert exc.value.code == "mcp.already_installed"


def test_install_rejects_bundled_id(tmp_hal0_home: str) -> None:
    with pytest.raises(Conflict) as exc:
        registry.install(_record("hal0-admin"))
    assert exc.value.code == "mcp.id_reserved"


def test_install_rejects_bad_id_charset(tmp_hal0_home: str) -> None:
    with pytest.raises(BadRequest) as exc:
        registry.install(_record("My Bad Id"))
    assert exc.value.code == "mcp.id_invalid"


def test_uninstall_round_trip(tmp_hal0_home: str) -> None:
    registry.install(_record())
    registry.uninstall("filesystem")
    assert registry.list_installed() == []


def test_uninstall_missing_raises_not_found(tmp_hal0_home: str) -> None:
    with pytest.raises(NotFound) as exc:
        registry.uninstall("filesystem")
    assert exc.value.code == "mcp.not_found"


def test_get_installed_missing_raises_not_found(tmp_hal0_home: str) -> None:
    with pytest.raises(NotFound):
        registry.get_installed("nope")


def test_patch_config_replaces_env(tmp_hal0_home: str) -> None:
    registry.install(_record(env={"OLD": "1"}))
    updated = registry.patch_config("filesystem", env={"NEW": "2"})
    assert updated.env == {"NEW": "2"}
    # Round-trip verifies disk write.
    reloaded = registry.get_installed("filesystem")
    assert reloaded.env == {"NEW": "2"}


def test_patch_config_coerces_env_values(tmp_hal0_home: str) -> None:
    registry.install(_record())
    updated = registry.patch_config("filesystem", env={"PORT": 8080, "FLAG": True})
    assert updated.env == {"PORT": "8080", "FLAG": "True"}


def test_patch_config_toggles_enabled(tmp_hal0_home: str) -> None:
    registry.install(_record(enabled=True))
    after = registry.patch_config("filesystem", enabled=False)
    assert after.enabled is False
    again = registry.patch_config("filesystem", enabled=True)
    assert again.enabled is True


def test_patch_config_noop_returns_record(tmp_hal0_home: str) -> None:
    registry.install(_record())
    record = registry.patch_config("filesystem")
    assert record.id == "filesystem"


def test_list_installed_tolerates_malformed_file(
    tmp_hal0_home: str,
) -> None:
    from pathlib import Path

    root = Path(tmp_hal0_home) / "etc" / "hal0" / "mcp-servers"
    root.mkdir(parents=True, exist_ok=True)
    (root / "broken.toml").write_text("this is not a [valid toml")
    registry.install(_record("good"))
    rows = registry.list_installed()
    assert [r.id for r in rows] == ["good"]


# ── Security hardening (#368 review) ────────────────────────────────────────


def test_install_writes_restrictive_permissions(tmp_hal0_home: str) -> None:
    """Registry TOMLs hold env blocks (API keys); they must be 0o600 + dir 0o700.

    Default umask (022) would otherwise leave both world-readable. We chmod
    explicitly after the atomic write — assert both modes round-trip.
    """
    from pathlib import Path

    registry.install(_record(env={"API_KEY": "secret-token"}))
    file_path = Path(tmp_hal0_home) / "etc" / "hal0" / "mcp-servers" / "filesystem.toml"
    dir_path = file_path.parent
    assert file_path.exists()
    file_mode = file_path.stat().st_mode & 0o777
    dir_mode = dir_path.stat().st_mode & 0o777
    assert file_mode == 0o600, f"expected 0o600, got {oct(file_mode)}"
    assert dir_mode == 0o700, f"expected 0o700, got {oct(dir_mode)}"


def test_uninstall_bundled_id_rejected_at_registry_layer(tmp_hal0_home: str) -> None:
    """Calling ``installed.uninstall("hal0-admin")`` rejects before disk lookup.

    Belt-and-braces: the route layer also rejects bundled ids (mcp.bundled,
    409); this asserts the registry's own validate-id guard catches the
    same case if a future call site bypasses the route check.
    """
    with pytest.raises(Conflict) as exc:
        registry.uninstall("hal0-admin")
    assert exc.value.code == "mcp.id_reserved"
    with pytest.raises(Conflict) as exc:
        registry.uninstall("hal0-memory")
    assert exc.value.code == "mcp.id_reserved"


def test_validate_id_rejects_path_traversal(tmp_hal0_home: str) -> None:
    """``id="../evil"`` must reject at the registry validator, not after stat.

    Even though Pydantic would allow it (no charset constraint on the
    field), the registry's :func:`_validate_id` rejects any non-[a-z0-9_-]
    char — that's what stops a write from landing outside the registry dir.
    """
    with pytest.raises(BadRequest) as exc:
        registry.install(_record("../evil"))
    assert exc.value.code == "mcp.id_invalid"
    with pytest.raises(BadRequest) as exc:
        registry.uninstall("../evil")
    assert exc.value.code == "mcp.id_invalid"


def test_patch_config_locked_rmw_applies(tmp_hal0_home: str) -> None:
    """#382: patch_config wraps its read-modify-write in an advisory lock.

    Functional guard that the locked RMW still applies env + enabled
    updates and the write lands on disk (the lock must not swallow the
    write or corrupt the record)."""
    registry.install(_record("filesystem", enabled=True))
    patched = registry.patch_config("filesystem", enabled=False, env={"FOO": "bar"})
    assert patched.enabled is False
    assert patched.env == {"FOO": "bar"}
    reloaded = registry.get_installed("filesystem")
    assert reloaded.enabled is False
    assert reloaded.env == {"FOO": "bar"}


# ── ADR-0015: schema extension (command/args/url/secrets/tools/exposure) ────


def test_new_fields_default_empty(tmp_hal0_home: str) -> None:
    """A fresh record has zero callable tools and zero exposure by default."""
    saved = registry.install(_record())
    assert saved.command == ""
    assert saved.args == []
    assert saved.url == ""
    assert saved.secrets == {}
    assert saved.tool_policy == ToolPolicy()
    assert saved.exposure == ExposureConfig()


def test_pre_adr0015_record_still_validates(tmp_hal0_home: str) -> None:
    """A pre-#305-extension on-disk shape (``tools`` as a bare int) still loads.

    Simulates an old record written before this PR: no [secrets]/[tools]/
    [exposure] tables, ``tools`` is the bare advertised-count int.
    """
    old_shape = {
        "id": "legacy",
        "name": "legacy",
        "spec": "npm:legacy-mcp",
        "transport": "stdio",
        "tools": 7,
        "enabled": True,
    }
    record = registry.InstalledServer.from_toml_dict(old_shape)
    assert record.tools == 7
    assert record.tool_policy == ToolPolicy()
    assert record.exposure == ExposureConfig()


def test_to_toml_dict_round_trips_tool_policy(tmp_hal0_home: str) -> None:
    saved = registry.install(
        _record(
            "github",
            tool_policy=ToolPolicy(allow=["search"], gated=["create_pr"], blocked=["delete_repo"]),
        )
    )
    reloaded = registry.get_installed("github")
    assert reloaded.tool_policy.allow == ["search"]
    assert reloaded.tool_policy.gated == ["create_pr"]
    assert reloaded.tool_policy.blocked == ["delete_repo"]
    # The int tool *count* (a separate field, see InstalledServer docstring)
    # survives the [tools]-table round-trip untouched.
    assert reloaded.tools == saved.tools == 5


def test_tool_policy_disjointness_enforced(tmp_hal0_home: str) -> None:
    """ToolPolicy's own validator rejects a tool on two tiers — reused, not re-implemented."""
    with pytest.raises(Exception, match="disjoint|overlap"):  # noqa: RUF043
        _record("github", tool_policy=ToolPolicy(allow=["x"], gated=["x"]))


def test_secrets_reference_must_be_env_shaped(tmp_hal0_home: str) -> None:
    with pytest.raises(Exception, match="secrets"):
        _record("github", secrets={"GITHUB_TOKEN": "not a valid key"})


def test_secrets_reference_valid_name_accepted(tmp_hal0_home: str) -> None:
    saved = registry.install(_record("github", secrets={"GITHUB_TOKEN": "GITHUB_MCP_TOKEN"}))
    assert saved.secrets == {"GITHUB_TOKEN": "GITHUB_MCP_TOKEN"}


def test_exposure_round_trips(tmp_hal0_home: str) -> None:
    registry.install(_record("github", exposure=ExposureConfig(hermes=True)))
    reloaded = registry.get_installed("github")
    assert reloaded.exposure.hermes is True
    assert reloaded.exposure.brain is False


def test_list_enabled_exposed_filters_correctly(tmp_hal0_home: str) -> None:
    registry.install(_record("exposed", exposure=ExposureConfig(hermes=True), enabled=True))
    registry.install(_record("disabled", exposure=ExposureConfig(hermes=True), enabled=False))
    registry.install(_record("not-exposed", exposure=ExposureConfig(hermes=False), enabled=True))

    hermes_ids = {r.id for r in registry.list_enabled_exposed(target="hermes")}
    assert hermes_ids == {"exposed"}


def test_patch_config_traversal_id_creates_no_file_outside_the_registry(
    tmp_hal0_home: str, tmp_path_factory: pytest.TempPathFactory
) -> None:
    """`patch_config` takes the record lock BEFORE `get_installed` validates.

    The lock file is opened `"w"` — a truncating create — so an id that walked
    out of the registry directory would let an API caller create (and, for a
    `<name>.toml` target, truncate) a file anywhere the daemon can write.
    `_registry_path` is the barrier; assert it holds from the entry point that
    reaches it first, using a traversal spelled relative to the REAL registry
    directory rather than a guessed number of `../`.
    """
    outside_dir = tmp_path_factory.mktemp("outside")
    victim = outside_dir / "victim.toml"
    victim.write_text("do not truncate me\n", encoding="utf-8")

    # ".../outside0/victim" — strip the .toml the registry appends itself.
    traversal = os.path.relpath(victim.with_suffix(""), registry._registry_dir())
    assert traversal.startswith(".."), traversal

    with pytest.raises(BadRequest) as exc:
        registry.patch_config(traversal, enabled=False)
    assert exc.value.code == "mcp.id_invalid"

    assert victim.read_text(encoding="utf-8") == "do not truncate me\n"
    assert not (outside_dir / "victim.toml.lock").exists()


# ── #2304: no [secrets]/[env] headers over plaintext http to a non-loopback host ──


def _http_record(url: str, **overrides: object) -> registry.InstalledServer:
    fields: dict[str, object] = {
        "spec": "https://github.example.com/manifest.json",
        "transport": "streamable-http",
        "url": url,
    }
    fields.update(overrides)
    return _record("github", **fields)


def test_https_url_with_secrets_accepted(tmp_hal0_home: str) -> None:
    saved = registry.install(
        _http_record(
            "https://github.example.com/mcp", secrets={"AUTHORIZATION": "GITHUB_MCP_TOKEN"}
        )
    )
    assert saved.secrets == {"AUTHORIZATION": "GITHUB_MCP_TOKEN"}


@pytest.mark.parametrize(
    "url",
    ["http://127.0.0.1:8765/mcp", "http://localhost:8765/mcp", "http://[::1]:8765/mcp"],
)
def test_loopback_http_with_secrets_accepted(tmp_hal0_home: str, url: str) -> None:
    saved = registry.install(_http_record(url, secrets={"AUTHORIZATION": "GITHUB_MCP_TOKEN"}))
    assert saved.url == url


@pytest.mark.parametrize("transport", ["streamable-http", "sse"])
def test_lan_http_with_secrets_refused(tmp_hal0_home: str, transport: str) -> None:
    with pytest.raises(ValueError, match=r"AUTHORIZATION") as exc:
        _http_record(
            "http://192.0.2.10:8765/mcp",
            transport=transport,
            secrets={"AUTHORIZATION": "GITHUB_MCP_TOKEN"},
        )
    assert "192.0.2.10" in str(exc.value)
    assert "https://" in str(exc.value)


def test_lan_http_with_env_literal_refused(tmp_hal0_home: str) -> None:
    """``build_headers`` forwards every ``[env]`` literal as a header too."""
    with pytest.raises(ValueError, match=r"X_API_KEY") as exc:
        _http_record("http://192.0.2.10:8765/mcp", env={"X_API_KEY": "literal"})
    assert "192.0.2.10" in str(exc.value)


def test_lan_http_without_secrets_accepted(tmp_hal0_home: str) -> None:
    """No header values to leak: plain http to a LAN host is the operator's call.

    An ``[env]`` key with an empty value (what ``POST /install`` writes for
    each ``env_required`` name) carries nothing, so it does not trip the gate.
    """
    saved = registry.install(_http_record("http://192.0.2.10:8765/mcp", env={"X_API_KEY": ""}))
    assert saved.url == "http://192.0.2.10:8765/mcp"


def test_allow_insecure_http_override_accepted(tmp_hal0_home: str) -> None:
    saved = registry.install(
        _http_record(
            "http://192.0.2.10:8765/mcp",
            secrets={"AUTHORIZATION": "GITHUB_MCP_TOKEN"},
            allow_insecure_http=True,
        )
    )
    reloaded = registry.get_installed("github")
    assert saved.allow_insecure_http is True
    assert reloaded.allow_insecure_http is True
    assert reloaded.secrets == {"AUTHORIZATION": "GITHUB_MCP_TOKEN"}


def test_patch_config_refuses_header_value_over_lan_http(tmp_hal0_home: str) -> None:
    """``patch_config`` builds via ``model_copy`` (no validators) — it must re-check."""
    registry.install(_http_record("http://192.0.2.10:8765/mcp"))
    with pytest.raises(BadRequest) as exc:
        registry.patch_config("github", secrets={"AUTHORIZATION": "GITHUB_MCP_TOKEN"})
    assert exc.value.code == "mcp.insecure_url"
    assert "AUTHORIZATION" in str(exc.value)
    assert "192.0.2.10" in str(exc.value)
    with pytest.raises(BadRequest) as exc:
        registry.patch_config("github", env={"X_API_KEY": "literal"})
    assert exc.value.code == "mcp.insecure_url"
    # Nothing was written: the on-disk record is unchanged.
    reloaded = registry.get_installed("github")
    assert reloaded.secrets == {}
    assert reloaded.env == {}


def test_hand_edited_lan_http_record_with_secrets_is_refused_on_load(
    tmp_hal0_home: str,
) -> None:
    """A TOML edited by hand never went through install/PATCH; loading refuses it."""
    path = registry._registry_path("github")
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        'id = "github"\nname = "github"\nspec = "https://github.example.com/manifest.json"\n'
        'transport = "streamable-http"\nurl = "http://192.0.2.10:8765/mcp"\n'
        '[secrets]\nAUTHORIZATION = "GITHUB_MCP_TOKEN"\n',
        encoding="utf-8",
    )
    assert registry.list_installed() == []
    with pytest.raises(BadRequest) as exc:
        registry.get_installed("github")
    assert exc.value.code == "mcp.record_malformed"
    assert "AUTHORIZATION" in exc.value.details["reason"]
    assert "192.0.2.10" in exc.value.details["reason"]


def test_refused_record_never_echoes_env_literal(tmp_hal0_home: str) -> None:
    """The refusal names keys and host, never the [env] literal it protects.

    pydantic's ``str(ValidationError)`` appends ``input_value={...}``; neither
    the ``bad_record`` journal line nor the 400's ``details.reason`` may carry
    it. The reason also tells the operator the way out (no API path exists
    for a record that will not load: edit the TOML or DELETE it).
    """
    from structlog.testing import capture_logs

    path = registry._registry_path("github")
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        'id = "github"\nname = "github"\nspec = "https://github.example.com/manifest.json"\n'
        'transport = "sse"\nurl = "http://192.0.2.10:8765/sse"\n'
        '[env]\nX_API_KEY = "SUPERSECRET-LITERAL"\n',
        encoding="utf-8",
    )
    with capture_logs() as logs:
        assert registry.list_installed() == []
    bad = [e for e in logs if e["event"] == "hal0.mcp.installed.bad_record"]
    assert len(bad) == 1
    logged = repr(bad[0])
    assert "SUPERSECRET-LITERAL" not in logged
    assert "X_API_KEY" in logged
    assert "192.0.2.10" in logged

    with pytest.raises(BadRequest) as exc:
        registry.get_installed("github")
    reason = exc.value.details["reason"]
    assert "SUPERSECRET-LITERAL" not in reason
    assert "SUPERSECRET-LITERAL" not in repr(exc.value.details)
    assert "SUPERSECRET-LITERAL" not in str(exc.value)
    assert "X_API_KEY" in reason
    assert "192.0.2.10" in reason
    assert str(path) in reason
    assert "allow_insecure_http = true" in reason
    assert "DELETE" in reason


def test_bad_record_warning_once_per_file_version(tmp_hal0_home: str) -> None:
    """``list_installed`` runs on every dashboard poll; warn once per edit, not per call."""
    from structlog.testing import capture_logs

    path = registry._registry_path("broken")
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text('id = "broken"\n', encoding="utf-8")  # missing required fields
    with capture_logs() as logs:
        registry.list_installed()
        registry.list_installed()
    assert len([e for e in logs if e["event"] == "hal0.mcp.installed.bad_record"]) == 1

    stat = path.stat()
    path.write_text('id = "broken"\nname = "broken"\n', encoding="utf-8")
    os.utime(path, ns=(stat.st_atime_ns, stat.st_mtime_ns + 1_000_000_000))
    with capture_logs() as logs:
        registry.list_installed()
    assert len([e for e in logs if e["event"] == "hal0.mcp.installed.bad_record"]) == 1


def test_uppercase_http_scheme_still_gated(tmp_hal0_home: str) -> None:
    with pytest.raises(ValueError, match=r"AUTHORIZATION"):
        _http_record("HTTP://192.0.2.10:8765/mcp", secrets={"AUTHORIZATION": "GITHUB_MCP_TOKEN"})
    saved = registry.install(
        _http_record(
            "HTTPS://github.example.com/mcp", secrets={"AUTHORIZATION": "GITHUB_MCP_TOKEN"}
        )
    )
    assert saved.url == "HTTPS://github.example.com/mcp"


@pytest.mark.parametrize(
    "key", ["Proxy-Authorization", "PROXY-AUTHORIZATION", "proxy-authorization"]
)
@pytest.mark.parametrize("url", ["https://github.example.com/mcp", "http://127.0.0.1:8765/mcp"])
def test_proxy_authorization_header_value_refused_for_any_url(
    tmp_hal0_home: str, key: str, url: str
) -> None:
    """urllib moves Proxy-Authorization onto the CONNECT request, which a plain
    http:// proxy carries in clear — https on the record does not protect it."""
    with pytest.raises(ValueError, match="CONNECT") as exc:
        _http_record(url, secrets={key: "GITHUB_MCP_TOKEN"})
    assert key in str(exc.value)
    with pytest.raises(ValueError, match="CONNECT"):
        _http_record(url, secrets={key: "GITHUB_MCP_TOKEN"}, allow_insecure_http=True)


def test_proxy_authorization_env_literal_refused_without_echoing_it(
    tmp_hal0_home: str,
) -> None:
    with pytest.raises(ValueError, match="Proxy-Authorization") as exc:
        _http_record(
            "https://github.example.com/mcp", env={"Proxy-Authorization": "Basic SUPERSECRET"}
        )
    assert "SUPERSECRET" not in str(exc.value)
    # An empty literal carries nothing and is not refused.
    _http_record("https://github.example.com/mcp", env={"Proxy-Authorization": ""})


def test_install_and_patch_refuse_proxy_authorization(tmp_hal0_home: str) -> None:
    base = _http_record("https://github.example.com/mcp")
    with pytest.raises(BadRequest) as exc:
        registry.install(base.model_copy(update={"secrets": {"Proxy-Authorization": "TOKEN"}}))
    assert exc.value.code == "mcp.proxy_header"
    assert exc.value.details["header_keys"] == ["Proxy-Authorization"]

    registry.install(base)
    with pytest.raises(BadRequest) as exc:
        registry.patch_config("github", env={"proxy-authorization": "Basic SUPERSECRET"})
    assert exc.value.code == "mcp.proxy_header"
    assert "SUPERSECRET" not in str(exc.value)
    assert "SUPERSECRET" not in repr(exc.value.details)
    assert registry.get_installed("github").env == {}
