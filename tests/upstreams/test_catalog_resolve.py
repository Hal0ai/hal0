"""Resolving a requested model id to one curated provider."""

from __future__ import annotations

from dataclasses import replace

import pytest

from hal0.errors import Hal0Error
from hal0.upstreams.catalog import (
    ModelAmbiguous,
    ModelNotEnabled,
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


def test_disabled_canonical_raises_not_enabled() -> None:
    mm = _up("mm", UpstreamModel("m", enabled=False))
    with pytest.raises(ModelNotEnabled) as e:
        resolve_requested_model("mm/m", [mm])
    assert e.value.details == {"provider": "mm", "model": "m"}
    assert "mm" in e.value.message and "m" in e.value.message


def test_disabled_bare_raises_not_enabled() -> None:
    mm = _up("mm", UpstreamModel("m", enabled=False))
    with pytest.raises(ModelNotEnabled) as e:
        resolve_requested_model("m", [mm])
    assert e.value.details["model"] == "m"
    assert e.value.details["provider"] == "mm"


def test_unknown_model_on_known_provider_raises_not_enabled() -> None:
    mm = _up("mm", UpstreamModel("m"))
    with pytest.raises(ModelNotEnabled) as e:
        resolve_requested_model("mm/nope", [mm])
    assert e.value.details == {"provider": "mm", "model": "nope"}


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
