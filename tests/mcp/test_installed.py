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
