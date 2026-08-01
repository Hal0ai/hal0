"""Issue #1513: an upstream cannot advertise auth it does not perform.

`google_query` was declared in the schema enum, accepted by the validator,
shipped in the provider catalog as Google AI Studio's auth style, offered as
an MCP/CLI prefill — and implemented nowhere. `auth_headers` returned `{}`
for it with the comment *"Google keys ride as ?key=… on the URL — emit no
header here"*, and no code anywhere appends that query parameter. The
dispatcher builds its outbound URL by plain concatenation and injects only
the header map, so every Google AI Studio call left hal0 with no
`Authorization`, no `x-api-key` and no `?key=` — while the dashboard showed
a green "key set" chip, because `auth_key_present` is backed by env-var
presence rather than by whether the key can be presented.

That is the worst shape a security bug takes: the UI asserts a protection
that does not exist, so nobody investigates.

**The fix is not to implement `?key=`.** Google's OpenAI-compatible endpoint
— the one in the catalog, `/v1beta/openai` — authenticates with a normal
bearer header; Google's own REST example is
`-H "Authorization: Bearer $GEMINI_API_KEY"`. `google_query` was modelled on
the *native* `generativelanguage` API, which is a different surface. So the
catalog entry becomes `bearer` (a style that is implemented, tested, and
shared with every other cloud provider) and `google_query` is retired
outright. Threading a secret into a URL would have been strictly worse:
query strings land in access logs, `Referer` headers and error envelopes,
so it would have traded a broken provider for a credential-leak surface.

The second half is the invariant behind the lying chip: a style that
*requires* a credential must fail loudly when it does not have one, instead
of dispatching unauthenticated and letting the remote answer 401.
"""

from __future__ import annotations

import pytest

from hal0.config.schema import _VALID_AUTH_STYLES
from hal0.upstreams.integrations import _CATALOG, validate_catalog
from hal0.upstreams.registry import Upstream, UpstreamAuthUnconfigured, UpstreamRegistry


def _upstream(**kw) -> Upstream:
    base = {
        "name": "probe",
        "kind": "remote",
        "url": "https://example.invalid/v1",
        "auth_style": "bearer",
        "auth_value_env": "PROBE_KEY",
    }
    base.update(kw)
    return Upstream(**base)  # type: ignore[arg-type]


@pytest.fixture
def registry() -> UpstreamRegistry:
    return UpstreamRegistry()


# ── 1. google_query is retired everywhere it was declared ───────────────────


def test_google_query_is_not_a_valid_auth_style() -> None:
    """The schema comment claimed "the full set implemented by
    UpstreamRegistry.auth_headers()" while listing one that wasn't."""
    assert "google_query" not in _VALID_AUTH_STYLES


def test_every_declared_auth_style_is_actually_implemented() -> None:
    """The assertion the schema comment was making. Stated as a check so the
    next style added to the enum has to come with a branch."""
    registry_styles = {"bearer", "anthropic", "header", "none"}
    assert registry_styles == _VALID_AUTH_STYLES


def test_no_catalog_entry_uses_an_unimplemented_style() -> None:
    for cid, entry in _CATALOG.items():
        assert entry["auth"] in _VALID_AUTH_STYLES, f"{cid} → {entry['auth']!r}"


def test_catalog_still_validates() -> None:
    assert validate_catalog() == []


def test_google_query_is_no_longer_offered_as_a_choice() -> None:
    """The CLI help advertised it in two places. A style the validator now
    rejects must not still be printed as a selectable option — the retirement
    comments and the alias map that keeps old configs booting are expected to
    keep naming it."""
    from pathlib import Path

    root = Path(__file__).resolve().parents[2]
    cli = (root / "src/hal0/cli/upstream_commands.py").read_text(encoding="utf-8")
    assert "google_query" not in cli


def test_google_query_is_not_a_selectable_value_anywhere() -> None:
    """The three places that enumerate valid styles agree."""
    from hal0.upstreams import integrations

    assert "google_query" not in _VALID_AUTH_STYLES
    assert all(e["auth"] != "google_query" for e in integrations._CATALOG.values())
    assert "google_query" not in (integrations.__doc__ or "").split("Auth styles:")[1].split(
        "Every style"
    )[0]


# ── 2. Google AI Studio authenticates ───────────────────────────────────────


def test_google_ai_studio_uses_bearer() -> None:
    entry = _CATALOG["google_ai_studio"]
    assert entry["auth"] == "bearer"
    assert entry["auth_header_name"] == "Authorization"


def test_google_ai_studio_still_points_at_the_openai_compat_surface() -> None:
    """The bearer choice is only correct for `/v1beta/openai`; the native
    API is a different surface with different auth."""
    assert _CATALOG["google_ai_studio"]["base_url"].endswith("/v1beta/openai")


def test_google_ai_studio_emits_a_bearer_header(
    registry: UpstreamRegistry, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The #1513 repro: this returned `{}` and the call went out naked."""
    monkeypatch.setenv("GEMINI_KEY", "secret-key")
    entry = _CATALOG["google_ai_studio"]
    u = _upstream(
        name="gemini",
        url=entry["base_url"],
        auth_style=entry["auth"],
        auth_value_env="GEMINI_KEY",
    )
    assert registry.auth_headers(u) == {"Authorization": "Bearer secret-key"}


# ── 3. a configured google_query survives the upgrade, authenticating ───────


def test_existing_google_query_config_is_coerced_to_bearer(tmp_path) -> None:
    """A box already carrying `auth_style = "google_query"` must not hard-fail
    config load on upgrade — and must stop dispatching unauthenticated. It is
    coerced to the style that actually works against that base_url."""
    from hal0.config.schema import coerce_auth_style

    assert coerce_auth_style("google_query") == "bearer"
    assert coerce_auth_style("bearer") == "bearer"
    assert coerce_auth_style("none") == "none"


def test_a_google_query_entry_still_validates_and_becomes_bearer() -> None:
    """The upgrade path: dropping the value from the enum without an alias
    would turn every existing Gemini upstream into a config-load failure."""
    from hal0.config.schema import UpstreamEntry

    entry = UpstreamEntry(
        name="gemini",
        kind="remote",
        url="https://generativelanguage.googleapis.com/v1beta/openai",
        auth_style="google_query",
        auth_value_env="GEMINI_KEY",
    )
    assert entry.auth_style == "bearer"


def test_a_genuinely_unknown_auth_style_is_still_rejected() -> None:
    """The alias map must not become a way to smuggle anything through."""
    import pydantic

    from hal0.config.schema import UpstreamEntry

    with pytest.raises(pydantic.ValidationError):
        UpstreamEntry(name="x", kind="remote", url="https://x.invalid", auth_style="magic")


def test_a_coerced_entry_authenticates_end_to_end(
    registry: UpstreamRegistry, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Coercion is only worth anything if the resulting upstream presents a
    credential — the whole defect was one that dispatched naked."""
    from hal0.config.schema import UpstreamEntry

    monkeypatch.setenv("GEMINI_KEY", "k")
    entry = UpstreamEntry(
        name="gemini",
        kind="remote",
        url="https://generativelanguage.googleapis.com/v1beta/openai",
        auth_style="google_query",
        auth_value_env="GEMINI_KEY",
    )
    u = _upstream(
        name=entry.name,
        url=entry.url,
        auth_style=entry.auth_style,
        auth_value_env=entry.auth_value_env,
    )
    assert registry.auth_headers(u) == {"Authorization": "Bearer k"}


# ── 4. a credential-requiring style fails loudly without a credential ───────


@pytest.mark.parametrize("style", ["bearer", "anthropic", "header"])
def test_missing_credential_raises_instead_of_dispatching_naked(
    registry: UpstreamRegistry, monkeypatch: pytest.MonkeyPatch, style: str
) -> None:
    """The invariant the "key set" chip was asserting. `auth_headers` used to
    return `{}` (or bare `anthropic-version`) for a missing key, so the call
    went out unauthenticated and the remote — not hal0 — reported the
    problem, as a 401 the operator had to go read."""
    monkeypatch.delenv("PROBE_KEY", raising=False)
    u = _upstream(auth_style=style, auth_header="X-Api-Key" if style == "header" else None)
    with pytest.raises(UpstreamAuthUnconfigured) as exc:
        registry.auth_headers(u)
    assert "PROBE_KEY" in str(exc.value)
    assert "probe" in str(exc.value)


def test_the_error_never_carries_the_key_itself(
    registry: UpstreamRegistry, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("PROBE_KEY", "   ")
    with pytest.raises(UpstreamAuthUnconfigured) as exc:
        registry.auth_headers(_upstream())
    assert "   " not in str(exc.value).replace("PROBE_KEY", "")


def test_blank_credential_counts_as_missing(
    registry: UpstreamRegistry, monkeypatch: pytest.MonkeyPatch
) -> None:
    """`KEY=` in api.env is the shape an operator leaves behind after
    clearing a secret; it must not read as configured."""
    monkeypatch.setenv("PROBE_KEY", "")
    with pytest.raises(UpstreamAuthUnconfigured):
        registry.auth_headers(_upstream())


def test_style_none_needs_no_credential(registry: UpstreamRegistry) -> None:
    assert registry.auth_headers(_upstream(auth_style="none", auth_value_env=None)) == {}


def test_an_upstream_with_no_env_declared_needs_no_credential(
    registry: UpstreamRegistry,
) -> None:
    """Local slot upstreams declare no auth_value_env; they must not start
    raising."""
    assert registry.auth_headers(_upstream(auth_style="none", auth_value_env=None)) == {}
    assert registry.auth_headers(_upstream(kind="slot", auth_value_env=None, auth_style="none")) == {}


# ── 5. the failure surfaces cleanly, not as a 500 ───────────────────────────


@pytest.mark.asyncio
async def test_cold_prefetch_degrades_instead_of_exploding(
    registry: UpstreamRegistry, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Prefetch is best-effort; an unconfigured upstream must drop out of the
    model list, not take the whole fan-out down."""
    monkeypatch.delenv("PROBE_KEY", raising=False)
    registry._upstreams["probe"] = _upstream()
    assert await registry.fetch_models("probe") == []


@pytest.mark.asyncio
async def test_test_reports_the_missing_env_var_without_raising(
    registry: UpstreamRegistry, monkeypatch: pytest.MonkeyPatch
) -> None:
    """`test()` already pre-checked the env var and returned a clean message;
    that path must keep working rather than becoming an exception."""
    monkeypatch.delenv("PROBE_KEY", raising=False)
    registry._upstreams["probe"] = _upstream()
    result = await registry.test("probe")
    assert result["ok"] is False
    assert "PROBE_KEY" in result["error"]
