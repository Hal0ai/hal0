"""Unit tests for :mod:`hal0.mcp.installed` — #305 registry layer."""

from __future__ import annotations

import pytest

from hal0.errors import BadRequest, Conflict, NotFound
from hal0.mcp import installed as registry


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
