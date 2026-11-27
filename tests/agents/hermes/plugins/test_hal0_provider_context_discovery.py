"""hal0-provider must not hide the hal0 gateway from Hermes's live context discovery (#3835).

Hermes resolves a model's context window in ``agent.model_metadata.
get_model_context_length``. For a custom endpoint, the step that reads the
live ``/models`` metadata is gated on the base URL *not* belonging to a known
provider (pinned ``caafe538``, ``agent/model_metadata.py:2286``)::

    if _is_custom_endpoint(base_url) and not _is_known_provider_base_url(base_url) ...

"Known" is decided by ``_infer_provider_from_url``, which matches the URL's
host against ``_URL_TO_PROVIDER`` (the host or a subdomain of it at v0.23.3; a
substring of the netloc before, which the model below keeps because it claims
strictly more). At import time that table is
auto-extended with every registered provider profile's ``get_hostname()``
(``model_metadata.py:466-471``), and the pinned ``ProviderProfile.get_hostname``
derives a hostname from ``base_url`` when the ``hostname`` field is empty
(``providers/base.py:331-342``). v0.23.3 also asks ``providers.profile_for_host``
first (``model_metadata.py:481-483``), which matches a profile's declared
``hostnames`` / ``metadata_hostnames``; the hal0 profile declares none.

The hal0 profile used to declare ``hostname="127.0.0.1"``. That claimed every
loopback URL as the provider ``hal0``: the hal0 gateway itself, but also an
Ollama or llama.cpp server on another loopback port. With provider ``custom``
(how hal0 provisions Hermes), the profile's own hooks are never consulted
(``get_provider_profile(provider)`` at ``model_metadata.py:2258`` looks up
``custom``), so nothing recovered the window and ``hal0/agent`` fell to the
256,000 fallback against a 65,536 serving window.

CI has no Hermes, so the pinned classification and the step-2 lookup are
modelled below from the pinned source. They run against the REAL hal0
profile, whose base class (vendored in ``profile.py``) mirrors the pinned
``get_hostname`` derivation.
"""

from __future__ import annotations

from typing import Any
from urllib.parse import urlparse

import pytest

from hal0.agents.hermes.plugins.provider_hal0.profile import (
    DEFAULT_AUX_MODEL,
    DEFAULT_BASE_URL,
    Hal0ProviderProfile,
)
from tests.fixtures.hermes.contracts.provider_profile import (
    ProviderProfile as FrozenProviderProfile,
)

# Pinned ``agent/model_metadata.py:265`` DEFAULT_FALLBACK_CONTEXT (CONTEXT_PROBE_TIERS[0]).
_FALLBACK_CONTEXT = 256_000


def _url_to_provider(profiles: list[Any]) -> dict[str, str]:
    """Pinned ``model_metadata.py:466-471`` auto-extension (static rows omitted:
    none of them is a loopback host)."""
    table: dict[str, str] = {}
    for pp in profiles:
        host = pp.get_hostname()
        if host and host not in table:
            table[host] = pp.name
    return table


def _infer_provider_from_url(base_url: str, table: dict[str, str]) -> str | None:
    """``model_metadata.py:476-486``, modelled as the substring match v0.22.4 used."""
    parsed = urlparse(base_url)
    host = parsed.netloc.lower() or parsed.path.lower()
    for url_part, provider in table.items():
        if url_part in host:
            return provider
    return None


def _resolve_endpoint_context_length(model: str, metadata: dict[str, dict[str, Any]]) -> int | None:
    """Pinned ``model_metadata.py:1088-1097`` (exact id, single entry, substring)."""
    matched = metadata.get(model)
    if not matched and len(metadata) == 1:
        matched = next(iter(metadata.values()))
    elif not matched and model:
        matched = next(
            (entry for key, entry in metadata.items() if model in key or key in model), None
        )
    ctx = matched.get("context_length") if matched else None
    return ctx if isinstance(ctx, int) else None


def _resolve_context_length(
    model: str, base_url: str, profiles: list[Any], metadata: dict[str, dict[str, Any]]
) -> int:
    """The path ``get_model_context_length`` takes for ``provider="custom"``
    against a loopback endpoint that is neither Ollama, LM Studio, llama.cpp nor
    vLLM (hal0 answers ``/props`` and ``/version`` with its SPA): step 2 when
    the URL is not a known provider's, otherwise steps 5-9, which have nothing
    for ``hal0/agent`` and end at the 256K fallback."""
    known = _infer_provider_from_url(base_url, _url_to_provider(profiles)) is not None
    if not known:
        ctx = _resolve_endpoint_context_length(model, metadata)
        if ctx is not None:
            return ctx
    return _FALLBACK_CONTEXT


# hal0's ``/v1/models`` row for the agent slot (alias = slot name), as the
# issue's box reported it.
_HAL0_MODELS = {
    "agent": {"context_length": 65_536},
    "utility": {"context_length": 32_768},
}


def test_pinned_get_hostname_derives_from_base_url() -> None:
    # Guard on the contract fixture: an empty ``hostname`` is not "no claim".
    p = FrozenProviderProfile(name="x", base_url="http://127.0.0.1:8080/v1")
    assert p.get_hostname() == "127.0.0.1"


def test_vendored_base_mirrors_pinned_get_hostname() -> None:
    # The profile's vendored base must behave like the pin, or the tests below
    # would pass in hal0's venv and fail inside Hermes.
    from hal0.agents.hermes.plugins.provider_hal0.profile import ProviderProfile

    p = ProviderProfile(name="x", base_url="http://127.0.0.1:8080/v1")
    assert p.get_hostname() == "127.0.0.1"


def test_hal0_profile_claims_no_hostname() -> None:
    assert Hal0ProviderProfile().get_hostname() == ""


@pytest.mark.parametrize(
    "base_url",
    [
        DEFAULT_BASE_URL,
        "http://localhost:8080/v1",
        "http://127.0.0.1:11434/v1",  # an Ollama on the same box
        "http://10.0.0.9:9000/v1",  # HAL0_PROVIDER_BASE pointed at a LAN host
    ],
)
def test_hal0_profile_does_not_make_an_endpoint_a_known_provider(
    base_url: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("HAL0_PROVIDER_BASE", base_url)
    table = _url_to_provider([Hal0ProviderProfile()])
    assert _infer_provider_from_url(base_url, table) is None


def test_hermes_resolves_hal0_agent_to_the_serving_window() -> None:
    # The value Hermes ends up with for ``hal0/agent`` on the shipped wiring
    # (model.default=hal0/agent, provider=custom, base_url=127.0.0.1:8080/v1).
    ctx = _resolve_context_length(
        DEFAULT_AUX_MODEL, DEFAULT_BASE_URL, [Hal0ProviderProfile()], _HAL0_MODELS
    )
    assert ctx == 65_536


def test_a_loopback_hostname_claim_reproduces_the_256k_fallback() -> None:
    # The pre-fix declaration, kept as the repro so the model above is known
    # to be able to fail.
    claimed = FrozenProviderProfile(name="hal0", hostname="127.0.0.1")
    ctx = _resolve_context_length(DEFAULT_AUX_MODEL, DEFAULT_BASE_URL, [claimed], _HAL0_MODELS)
    assert ctx == _FALLBACK_CONTEXT
