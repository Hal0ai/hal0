"""Per-model rows on [[upstream]] entries: schema, validators, TOML round trip."""

from __future__ import annotations

import tomllib

import pytest
from pydantic import ValidationError

from hal0.config import paths
from hal0.config.loader import load_upstreams_config
from hal0.config.schema import UpstreamEntry, UpstreamModelEntry, UpstreamModelFilters
from hal0.upstreams.catalog import UpstreamModel
from hal0.upstreams.registry import UpstreamRegistry


def _entry(**kw: object) -> UpstreamEntry:
    base: dict[str, object] = {"name": "mm", "url": "https://api.example.test/v1"}
    base.update(kw)
    return UpstreamEntry(**base)  # type: ignore[arg-type]


def _curated_entry() -> UpstreamEntry:
    return _entry(
        catalog_id="minimax",
        curated=True,
        budget_monthly_usd=12.5,
        models=[
            UpstreamModelEntry(id="m-a", context=200000, price_in=0.3, price_out=1.2),
            UpstreamModelEntry(id="m-b", enabled=False, type="embed", price_unit="Mtok"),
        ],
    )


def test_models_round_trip_toml(tmp_hal0_home: str) -> None:
    reg = UpstreamRegistry()
    created = reg.create_persistent(_curated_entry())
    assert created.curated is True
    assert created.catalog_id == "minimax"
    assert created.budget_monthly_usd == 12.5
    assert created.models == (
        UpstreamModel("m-a", context=200000, price_in=0.3, price_out=1.2),
        UpstreamModel("m-b", enabled=False, type="embed"),
    )

    raw = tomllib.loads((paths.etc() / "upstreams.toml").read_text())
    assert [m["id"] for m in raw["upstream"][0]["models"]] == ["m-a", "m-b"]

    reloaded = load_upstreams_config().upstream[0]
    assert reloaded == _curated_entry()


def test_unrelated_patch_keeps_catalog_fields(tmp_hal0_home: str) -> None:
    reg = UpstreamRegistry()
    reg.create_persistent(_curated_entry())
    merged = reg.apply_persistent_patch("mm", {"advertise_models": False})
    assert merged.curated is True and len(merged.models) == 2
    on_disk = load_upstreams_config().upstream[0]
    assert on_disk.curated is True
    assert on_disk.catalog_id == "minimax"
    assert on_disk.budget_monthly_usd == 12.5
    assert [m.id for m in on_disk.models] == ["m-a", "m-b"]


def test_patch_replaces_models(tmp_hal0_home: str) -> None:
    reg = UpstreamRegistry()
    reg.create_persistent(_curated_entry())
    merged = reg.apply_persistent_patch(
        "mm", {"models": (UpstreamModel("z", price_in=1.0),), "budget_monthly_usd": 3.0}
    )
    assert merged.models == (UpstreamModel("z", price_in=1.0),)
    on_disk = load_upstreams_config().upstream[0]
    assert [m.id for m in on_disk.models] == ["z"]
    assert on_disk.budget_monthly_usd == 3.0


def test_duplicate_model_id_rejected() -> None:
    with pytest.raises(ValidationError, match="dup-id"):
        _entry(models=[UpstreamModelEntry(id="dup-id"), UpstreamModelEntry(id="dup-id")])


def test_bad_type_rejected() -> None:
    with pytest.raises(ValidationError):
        UpstreamModelEntry(id="x", type="video")


def test_unknown_model_key_rejected() -> None:
    with pytest.raises(ValidationError):
        UpstreamModelEntry(id="x", bogus=1)  # type: ignore[call-arg]


def test_negative_budget_rejected() -> None:
    with pytest.raises(ValidationError):
        _entry(budget_monthly_usd=-1)


def test_curated_with_filters_rejected() -> None:
    with pytest.raises(ValidationError, match="model_filters"):
        _entry(curated=True, model_filters=UpstreamModelFilters(include=["a/*"]))


def test_curated_with_empty_filters_allowed() -> None:
    assert _entry(curated=True, model_filters=UpstreamModelFilters()).curated is True


def test_legacy_row_loads_uncurated(tmp_hal0_home: str) -> None:
    etc = paths.etc()
    etc.mkdir(parents=True, exist_ok=True)
    (etc / "upstreams.toml").write_text(
        '[[upstream]]\nname = "or"\nurl = "https://x.test/v1"\n'
        '[upstream.model_filters]\ninclude = ["anthropic/*"]\n'
    )
    e = load_upstreams_config().upstream[0]
    assert e.curated is False and e.models == []
    assert e.model_filters is not None and e.model_filters.include == ["anthropic/*"]
