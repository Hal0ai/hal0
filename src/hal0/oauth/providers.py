"""OAuth provider registry (`/etc/hal0/oauth-providers.toml`).

Ported from ODS's `extensions/services/hermes/oauth-providers.json`
(Osmantic/ODS, Apache-2.0; permission to copy granted by its author) — see
`src/hal0/config/data/oauth_providers.toml` for the shipped provider list
and the attribution note.

The registry is TOML because it is human config (COMMON.md rule 2): the
operator can hand-add a provider or point `client_id` at their own OAuth
app. It never holds a secret value — `client_id` is not treated as
sensitive (it appears in the browser's address bar during the consent
redirect regardless), but `client_secret` is a real credential and is
never written here; see :mod:`hal0.oauth.store` for where it lives.
"""

from __future__ import annotations

import tomllib
from dataclasses import dataclass, field
from importlib.resources import files

import structlog

from hal0.config import paths

log = structlog.get_logger(__name__)

#: Shipped default registry lives under hal0.config.data alongside the
#: other bundled TOML (seed_profiles.toml, family_defaults.toml, ...) —
#: read via importlib.resources so it works from an editable checkout AND
#: an installed wheel identically (mirrors hal0.config.seeds._read_toml).
_DEFAULT_PACKAGE = "hal0.config.data"
_DEFAULT_FILENAME = "oauth_providers.toml"


class ProviderRegistryError(ValueError):
    """Raised when the provider registry TOML is malformed."""


@dataclass(frozen=True)
class OAuthProvider:
    """One entry from `oauth-providers.toml`."""

    id: str
    name: str
    skill_id: str
    authorize_url: str
    token_url: str
    revoke_url: str = ""
    scopes: list[str] = field(default_factory=list)
    pkce: bool = True
    client_id: str = ""
    requires_client_secret: bool = False
    notes: str = ""

    @classmethod
    def from_dict(cls, data: dict) -> OAuthProvider:
        for required in ("id", "name", "skill_id", "authorize_url", "token_url"):
            if not isinstance(data.get(required), str) or not data[required].strip():
                raise ProviderRegistryError(f"provider entry missing required field {required!r}")
        scopes = data.get("scopes") or []
        if not isinstance(scopes, list) or not all(isinstance(s, str) for s in scopes):
            raise ProviderRegistryError(
                f"provider {data.get('id')!r}: scopes must be a list of strings"
            )
        return cls(
            id=data["id"].strip(),
            name=data["name"].strip(),
            skill_id=data["skill_id"].strip(),
            authorize_url=data["authorize_url"].strip(),
            token_url=data["token_url"].strip(),
            revoke_url=str(data.get("revoke_url") or "").strip(),
            scopes=list(scopes),
            pkce=bool(data.get("pkce", True)),
            client_id=str(data.get("client_id") or "").strip(),
            requires_client_secret=bool(data.get("requires_client_secret", False)),
            notes=str(data.get("notes") or "").strip(),
        )


def registry_path():
    """Return `/etc/hal0/oauth-providers.toml` (HAL0_HOME-relative under tests)."""
    return paths.etc() / "oauth-providers.toml"


def _shipped_default_text() -> str:
    return files(_DEFAULT_PACKAGE).joinpath(_DEFAULT_FILENAME).read_text(encoding="utf-8")


def _seed_default(target) -> None:
    """Copy the shipped default registry to ``target`` (0644, atomic).

    Only ever runs once — on every subsequent load ``target`` already
    exists and this is skipped, so hand edits are never clobbered.
    """
    target.parent.mkdir(parents=True, exist_ok=True)
    body = _shipped_default_text()
    tmp = target.with_suffix(".toml.tmp")
    tmp.write_text(body, encoding="utf-8")
    tmp.chmod(0o644)
    tmp.replace(target)


def load_providers(path=None) -> list[OAuthProvider]:
    """Load the provider registry, seeding the shipped default if missing.

    Malformed entries are skipped (not fatal) so one bad hand-edit doesn't
    take down the whole registry — mirrors `personas.list_personas`'s
    tolerant-load discipline.
    """
    target = path if path is not None else registry_path()
    if not target.exists():
        _seed_default(target)
    with open(target, "rb") as f:
        raw = tomllib.load(f)
    entries = raw.get("providers")
    if not isinstance(entries, list):
        raise ProviderRegistryError(f"{target}: 'providers' must be a list of tables")
    out: list[OAuthProvider] = []
    for entry in entries:
        if not isinstance(entry, dict):
            continue
        try:
            out.append(OAuthProvider.from_dict(entry))
        except ProviderRegistryError as exc:
            log.warning("oauth.registry.skip_malformed", path=str(target), error=str(exc))
            continue
    return out


def get_provider(provider_id: str, *, path=None) -> OAuthProvider | None:
    """Return the named provider, or None if it isn't registered."""
    for provider in load_providers(path=path):
        if provider.id == provider_id:
            return provider
    return None
