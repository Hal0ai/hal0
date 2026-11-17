"""Resolving a requested model id to one curated provider."""

from __future__ import annotations

from dataclasses import replace

import pytest

from hal0.errors import Hal0Error
from hal0.upstreams.catalog import (
    ModelAmbiguous,
    ModelNotEnabled,
    NotEnabled,
    Resolved,
    UpstreamModel,
    resolve_requested_model,
)
from hal0.upstreams.registry import Upstream


def _up(name: str, *models: UpstreamModel, **kw) -> Upstream:
    kw.setdefault("curated", True)
    return Upstream(name=name, kind="remote", url=f"https://{name}.test/v1", models=models, **kw)


def test_error_types_carry_codes() -> None:
    assert issubclass(ModelNotEnabled, Hal0Error)
    assert (ModelNotEnabled.code, ModelNotEnabled.status) == ("upstream.model_not_enabled", 404)
    assert (ModelAmbiguous.code, ModelAmbiguous.status) == ("upstream.model_ambiguous", 400)


def test_canonical_ref_resolves() -> None:
    mm = _up("mm", UpstreamModel("MiniMax-M2.5"))
    assert resolve_requested_model("mm/MiniMax-M2.5", [mm]) == Resolved(mm, "MiniMax-M2.5")


def test_canonical_ref_with_inner_slash() -> None:
    orr = _up("openrouter", UpstreamModel("qwen/qwen3-coder"))
    assert resolve_requested_model("openrouter/qwen/qwen3-coder", [orr]) == Resolved(
        orr, "qwen/qwen3-coder"
    )


def test_bare_id_with_slash_not_a_provider() -> None:
    orr = _up("openrouter", UpstreamModel("qwen/qwen3-coder"))
    assert resolve_requested_model("qwen/qwen3-coder", [orr]) == Resolved(orr, "qwen/qwen3-coder")


def test_bare_id_unique() -> None:
    a = _up("a", UpstreamModel("only-a"))
    b = _up("b", UpstreamModel("only-b"))
    assert resolve_requested_model("only-b", [a, b]) == Resolved(b, "only-b")


@pytest.mark.parametrize("order", [0, 1])
def test_bare_id_ambiguous_names_candidates(order: int) -> None:
    a = _up("a", UpstreamModel("shared"))
    b = _up("b", UpstreamModel("shared"))
    ups = [a, b] if order == 0 else [b, a]
    with pytest.raises(ModelAmbiguous) as e:
        resolve_requested_model("shared", ups)
    assert e.value.details["candidates"] == ["a/shared", "b/shared"]
    assert e.value.details["model"] == "shared"
    assert "a/shared" in e.value.message and "b/shared" in e.value.message


def test_disabled_canonical_is_pending_canonical() -> None:
    mm = _up("mm", UpstreamModel("m", enabled=False))
    result = resolve_requested_model("mm/m", [mm])
    assert isinstance(result, NotEnabled)
    assert result.canonical is True
    assert isinstance(result.error, ModelNotEnabled)
    assert result.error.details == {"provider": "mm", "model": "m"}
    assert "is not enabled on provider 'mm'" in result.error.message
    assert "hal0 upstream enable mm m" in result.error.message


def test_disabled_bare_is_pending_bare() -> None:
    mm = _up("mm", UpstreamModel("m", enabled=False))
    result = resolve_requested_model("m", [mm])
    assert isinstance(result, NotEnabled)
    assert result.canonical is False
    assert result.error.details == {"provider": "mm", "model": "m"}


def test_disabled_bare_names_the_alphabetically_first_provider() -> None:
    b = _up("b", UpstreamModel("m", enabled=False))
    a = _up("a", UpstreamModel("m", enabled=False))
    result = resolve_requested_model("m", [b, a])
    assert isinstance(result, NotEnabled)
    assert result.error.details["provider"] == "a"


def test_unlisted_model_on_named_provider_is_pending_canonical() -> None:
    mm = _up("mm", UpstreamModel("m"))
    result = resolve_requested_model("mm/nope", [mm])
    assert isinstance(result, NotEnabled)
    assert result.canonical is True
    assert result.error.details == {"provider": "mm", "model": "nope"}
    assert "is not enabled, or is not offered, on provider 'mm'" in result.error.message
    assert "hal0 upstream enable mm nope" in result.error.message


def test_not_enabled_message_never_names_a_providers_tab() -> None:
    mm = _up("mm", UpstreamModel("m", enabled=False))
    for ref in ("mm/m", "m", "mm/nope"):
        result = resolve_requested_model(ref, [mm])
        assert isinstance(result, NotEnabled)
        assert "Providers" not in result.error.message
        assert "dashboard" in result.error.message


def test_unlisted_ref_resolves_as_a_bare_id_elsewhere() -> None:
    # A provider named "openai" does not own "openai/gpt-5" when only
    # openrouter lists that string.
    openai = _up("openai", UpstreamModel("gpt-4o"))
    orr = _up("openrouter", UpstreamModel("openai/gpt-5"))
    assert resolve_requested_model("openai/gpt-5", [openai, orr]) == Resolved(orr, "openai/gpt-5")


def test_listed_ref_beats_a_bare_id_elsewhere() -> None:
    openai = _up("openai", UpstreamModel("gpt-5"))
    orr = _up("openrouter", UpstreamModel("openai/gpt-5"))
    assert resolve_requested_model("openai/gpt-5", [openai, orr]) == Resolved(openai, "gpt-5")


def test_whole_string_enabled_elsewhere_beats_a_disabled_ref() -> None:
    # The whole-string bare-id attempt runs before a pending "not enabled".
    openai = _up("openai", UpstreamModel("gpt-5", enabled=False))
    orr = _up("openrouter", UpstreamModel("openai/gpt-5"))
    assert resolve_requested_model("openai/gpt-5", [openai, orr]) == Resolved(orr, "openai/gpt-5")


def test_disabled_ref_with_whole_string_ambiguous_raises_at_once() -> None:
    openai = _up("openai", UpstreamModel("gpt-5", enabled=False))
    a = _up("a", UpstreamModel("openai/gpt-5"))
    b = _up("b", UpstreamModel("openai/gpt-5"))
    with pytest.raises(ModelAmbiguous):
        resolve_requested_model("openai/gpt-5", [openai, a, b])


def test_bare_id_with_no_row_anywhere_is_none() -> None:
    mm = _up("mm", UpstreamModel("m", enabled=False))
    assert resolve_requested_model("other", [mm]) is None


def test_unknown_everywhere_returns_none() -> None:
    mm = _up("mm", UpstreamModel("m"))
    assert resolve_requested_model("nope", [mm]) is None
    assert resolve_requested_model("zz/nope", [mm]) is None
    assert resolve_requested_model("anything", []) is None


def test_uncurated_disabled_slot_and_agent_managed_are_ignored() -> None:
    m = UpstreamModel("m")
    base = _up("base", m)
    ups = [
        replace(base, name="unc", curated=False),
        replace(base, name="off", enabled=False),
        replace(base, name="slot", kind="slot", slot_name="s1"),
        replace(base, name="agent", managed_by="hermes"),
    ]
    assert resolve_requested_model("m", ups) is None
    for name in ("unc", "off", "slot", "agent"):
        assert resolve_requested_model(f"{name}/m", ups) is None


def test_non_participating_prefix_falls_to_bare_id() -> None:
    # "off" exists but does not take part, so "off/m" is a bare id on a live provider.
    off = _up("off", UpstreamModel("m"), enabled=False)
    live = _up("live", UpstreamModel("off/m"))
    assert resolve_requested_model("off/m", [off, live]) == Resolved(live, "off/m")
