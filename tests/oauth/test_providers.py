"""Tests for the `/etc/hal0/oauth-providers.toml` registry loader."""

from __future__ import annotations

from pathlib import Path

import pytest

from hal0.oauth.providers import (
    OAuthProvider,
    ProviderRegistryError,
    get_provider,
    load_providers,
)


def test_load_providers_seeds_shipped_default_on_first_read(tmp_path: Path) -> None:
    target = tmp_path / "oauth-providers.toml"
    assert not target.exists()

    providers = load_providers(path=target)

    assert target.exists()
    ids = {p.id for p in providers}
    assert {"google", "spotify", "github"} <= ids


def test_seeded_file_is_never_overwritten_after_hand_edit(tmp_path: Path) -> None:
    target = tmp_path / "oauth-providers.toml"
    load_providers(path=target)  # seeds it
    original = target.read_text(encoding="utf-8")
    edited = (
        original
        + '\n[[providers]]\nid = "custom"\nname = "Custom"\nskill_id = "custom"\nauthorize_url = "https://example.com/auth"\ntoken_url = "https://example.com/token"\n'
    )
    target.write_text(edited, encoding="utf-8")

    providers = load_providers(path=target)

    assert any(p.id == "custom" for p in providers)


def test_get_provider_returns_none_for_unknown_id(tmp_path: Path) -> None:
    target = tmp_path / "oauth-providers.toml"
    assert get_provider("nonexistent", path=target) is None


def test_get_provider_finds_seeded_entry(tmp_path: Path) -> None:
    target = tmp_path / "oauth-providers.toml"
    google = get_provider("google", path=target)
    assert google is not None
    assert google.name == "Google Workspace"
    assert google.pkce is True
    assert google.requires_client_secret is True


def test_malformed_entry_is_skipped_not_fatal(tmp_path: Path) -> None:
    target = tmp_path / "oauth-providers.toml"
    target.write_text(
        'schema_version = "hal0.oauth-providers.v1"\n'
        "[[providers]]\n"
        'id = "broken"\n'  # missing required fields
        "\n"
        "[[providers]]\n"
        'id = "ok"\n'
        'name = "OK Provider"\n'
        'skill_id = "ok"\n'
        'authorize_url = "https://example.com/auth"\n'
        'token_url = "https://example.com/token"\n',
        encoding="utf-8",
    )

    providers = load_providers(path=target)

    assert [p.id for p in providers] == ["ok"]


def test_missing_providers_key_raises() -> None:
    with pytest.raises(ProviderRegistryError):
        OAuthProvider.from_dict({"id": "x"})


def test_from_dict_rejects_non_list_scopes() -> None:
    with pytest.raises(ProviderRegistryError):
        OAuthProvider.from_dict(
            {
                "id": "x",
                "name": "X",
                "skill_id": "x",
                "authorize_url": "https://example.com/a",
                "token_url": "https://example.com/t",
                "scopes": "not-a-list",
            }
        )
