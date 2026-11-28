"""The setup catalog loader reads the wording file and refuses a bad one."""

from __future__ import annotations

import json
import re
from collections.abc import Callable
from pathlib import Path
from typing import Any

import pytest

from hal0.install.wizard.catalog import Catalog, CatalogError, load_catalog


def test_shipped_catalog_loads(catalog: Catalog) -> None:
    assert set(catalog.presets) == {"recommended", "minimal"}
    assert [c.id for c in catalog.components] == [
        "openwebui",
        "hermes",
        "memory",
        "images",
        "npu",
    ]
    assert catalog.questions["model_store"].answer_key == "model_store.path"
    assert catalog.model_ladder == ()


def test_options_load_only_where_the_catalog_gives_them(catalog: Catalog) -> None:
    assert [o.value for o in catalog.questions["setup_type"].options] == [
        "recommended",
        "minimal",
        "custom",
    ]
    assert catalog.questions["port"].options == ()


def test_every_preset_key_is_a_dotted_name(catalog: Catalog) -> None:
    for preset in catalog.presets.values():
        assert all(re.fullmatch(r"[a-z_]+(\.[a-z_]+)+", k) for k in preset)


def _bad_version(d: dict[str, Any]) -> None:
    d.update(version=2)


def _unknown_need(d: dict[str, Any]) -> None:
    d["components"][0].update(needs=["warp_drive"])


def _duplicate_question(d: dict[str, Any]) -> None:
    d["questions"].append(dict(d["questions"][0]))


def _no_recommended(d: dict[str, Any]) -> None:
    d["presets"].pop("recommended")


def _negative_download(d: dict[str, Any]) -> None:
    d["components"][0].update(download_gb=-1)


@pytest.mark.parametrize(
    ("mutate", "message"),
    [
        (_bad_version, "version 2"),
        (_unknown_need, "warp_drive"),
        (_duplicate_question, "cpu_only"),
        (_no_recommended, "recommended"),
        (_negative_download, "download_gb"),
    ],
)
def test_bad_catalog_is_refused_with_the_offending_name(
    catalog_path: Path,
    tmp_path: Path,
    mutate: Callable[[dict[str, Any]], None],
    message: str,
) -> None:
    data = json.loads(catalog_path.read_text())
    mutate(data)
    p = tmp_path / "c.json"
    p.write_text(json.dumps(data))
    with pytest.raises(CatalogError, match=message) as err:
        load_catalog(p)
    assert str(p) in str(err.value)
    assert "\n" not in str(err.value)


@pytest.mark.parametrize("text", ["", "not json", "[]"])
def test_unreadable_catalog_is_a_catalog_error(tmp_path: Path, text: str) -> None:
    p = tmp_path / "c.json"
    p.write_text(text)
    with pytest.raises(CatalogError):
        load_catalog(p)


def test_missing_catalog_is_a_catalog_error(tmp_path: Path) -> None:
    with pytest.raises(CatalogError, match="not found"):
        load_catalog(tmp_path / "absent.json")


def test_models_file_supplies_the_ladder(
    ladder_catalog: Catalog, catalog_path: Path, tmp_path: Path
) -> None:
    assert [(r.model, r.download_gb, r.needs_account) for r in ladder_catalog.model_ladder] == [
        ("small-1", 2.0, False),
        ("big-1", 17.0, True),
    ]
    assert ladder_catalog.model_ladder[1].min_gpu_pool_gb == 24
    models = tmp_path / "m.json"
    models.write_text(
        json.dumps(
            {"version": 1, "model_ladder": [{"role": "starter", "model": "x", "label": "X"}]}
        )
    )
    row = load_catalog(catalog_path, models).model_ladder[0]
    assert row.download_gb is None
    assert row.min_ram_gb is None
    assert row.lane == ""
    assert row.needs_account is False


def test_absent_models_file_means_no_ladder(catalog_path: Path, tmp_path: Path) -> None:
    assert load_catalog(catalog_path, tmp_path / "nope.json").model_ladder == ()


@pytest.mark.parametrize("text", ["not json", "[]", '{"version": 2, "model_ladder": []}'])
def test_bad_models_file_is_a_catalog_error(catalog_path: Path, tmp_path: Path, text: str) -> None:
    models = tmp_path / "m.json"
    models.write_text(text)
    with pytest.raises(CatalogError, match=re.escape(str(models))):
        load_catalog(catalog_path, models)


def test_the_image_question_offers_the_recommended_set(catalog: Catalog) -> None:
    options = catalog.questions["image_model"].options
    assert [(o.value, o.label, o.help) for o in options] == [
        (
            "recommended",
            "The recommended set",
            "the image models that suit this box",
        )
    ]


def test_extra_keys_on_a_model_row_are_ignored(catalog_path: Path, tmp_path: Path) -> None:
    models = tmp_path / "m.json"
    row = {"role": "starter", "model": "x", "label": "X", "recommended": True, "family": "q"}
    models.write_text(json.dumps({"version": 1, "model_ladder": [row]}))
    loaded = load_catalog(catalog_path, models).model_ladder
    assert [(r.model, r.label) for r in loaded] == [("x", "X")]


def test_the_generated_models_file_shape_loads(catalog_path: Path, tmp_path: Path) -> None:
    starter = {
        "role": "starter",
        "model": "s-1",
        "label": "S",
        "download_gb": 5.5,
        "min_gpu_pool_gb": 8,
    }
    brain = {
        "role": "brain",
        "model": "b-1",
        "label": "B",
        "download_gb": 20,
        "min_gpu_pool_gb": 32,
    }
    image = {"role": "image", "model": "i-1", "label": "I", "download_gb": 9}
    models = tmp_path / "m.json"
    models.write_text(
        json.dumps(
            {
                "version": 1,
                "generated_from": ["a.toml", "b.toml"],
                "model_ladder": [starter, brain, image],
            }
        )
    )
    catalog = load_catalog(catalog_path, models)
    assert [r.role for r in catalog.model_ladder] == ["starter", "brain", "image"]
    for row in catalog.model_ladder:
        assert row.min_ram_gb is None
        assert row.lane == ""
        assert row.needs_account is False
    assert any(r.role == "brain" for r in catalog.model_ladder)


def _models_with(tmp_path: Path, text: str) -> Path:
    models = tmp_path / "m.json"
    models.write_text(text)
    return models


@pytest.mark.parametrize("bad", ["NaN", "Infinity", "-Infinity", "1e999"])
def test_a_model_row_number_that_is_not_finite_is_refused(
    catalog_path: Path, tmp_path: Path, bad: str
) -> None:
    row = '{"role": "starter", "model": "x", "label": "X", "min_ram_gb": ' + bad + "}"
    models = _models_with(tmp_path, '{"version": 1, "model_ladder": [' + row + "]}")
    with pytest.raises(CatalogError, match="min_ram_gb") as err:
        load_catalog(catalog_path, models)
    assert str(models) in str(err.value)


@pytest.mark.parametrize("bad", ["NaN", "Infinity", "1e999"])
def test_a_catalog_number_that_is_not_finite_is_refused(
    catalog_path: Path, tmp_path: Path, bad: str
) -> None:
    text = catalog_path.read_text().replace('"download_gb": null', f'"download_gb": {bad}', 1)
    p = tmp_path / "c.json"
    p.write_text(text)
    with pytest.raises(CatalogError, match="download_gb"):
        load_catalog(p)


def test_a_preset_value_that_is_not_finite_is_refused(catalog_path: Path, tmp_path: Path) -> None:
    data = json.loads(catalog_path.read_text())
    data["presets"]["minimal"]["network.port"] = float("nan")
    p = tmp_path / "c.json"
    p.write_text(json.dumps(data))
    with pytest.raises(CatalogError, match=r"network\.port"):
        load_catalog(p)


@pytest.mark.parametrize("qid", ["setup_type", "cpu_only", "model_store"])
def test_a_catalog_without_a_question_the_screens_need_is_refused(
    catalog_path: Path, tmp_path: Path, qid: str
) -> None:
    data = json.loads(catalog_path.read_text())
    data["questions"] = [q for q in data["questions"] if q["id"] != qid]
    p = tmp_path / "c.json"
    p.write_text(json.dumps(data))
    with pytest.raises(CatalogError, match=f"question '{qid}' is missing") as err:
        load_catalog(p)
    assert str(p) in str(err.value)
