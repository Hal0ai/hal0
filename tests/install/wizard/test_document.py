from __future__ import annotations

import json
import stat
from dataclasses import replace
from pathlib import Path

import pytest

from hal0.install.wizard.catalog import Catalog, ModelRow
from hal0.install.wizard.document import (
    DocumentError,
    Override,
    effective,
    get,
    load_document,
    overrides,
    preset,
    put,
    write_document,
    write_private_text,
)

NOW = "2026-11-28T00:00:00Z"


def test_recommended_preset_is_the_catalog_preset(catalog: Catalog) -> None:
    doc = preset(catalog, "recommended")
    assert get(doc, "apps.hermes.enabled") is True
    assert get(doc, "access.admin_key") == "generate"
    assert get(doc, "model_store.path") == "/var/lib/hal0/models"


def test_custom_starts_from_recommended(catalog: Catalog) -> None:
    assert get(preset(catalog, "custom"), "install.type") == "custom"
    assert get(preset(catalog, "custom"), "memory.enabled") is True


def test_unknown_preset_is_refused(catalog: Catalog) -> None:
    with pytest.raises(DocumentError, match="bogus"):
        preset(catalog, "bogus")


def test_preset_is_a_copy(catalog: Catalog) -> None:
    doc = preset(catalog, "recommended")
    put(doc, "gen.models", ["x"])
    put(doc, "network.port", 1)
    assert get(preset(catalog, "recommended"), "gen.models") == []


@pytest.mark.parametrize(
    ("flags", "env", "key", "value", "source"),
    [
        (["--no-pi"], {}, "apps.pi.enabled", False, "--no-pi"),
        ([], {"HAL0_SKIP_HERMES": "1"}, "apps.hermes.enabled", False, "HAL0_SKIP_HERMES"),
        ([], {"HAL0_SKIP_HINDSIGHT": "1"}, "memory.enabled", False, "HAL0_SKIP_HINDSIGHT"),
        (
            [],
            {"HAL0_SKIP_OPENWEBUI": "1"},
            "apps.openwebui.enabled",
            False,
            "HAL0_SKIP_OPENWEBUI",
        ),
        ([], {"HAL0_IMAGE_MODELS": "none"}, "gen.mode", "off", "HAL0_IMAGE_MODELS"),
        (
            [],
            {"HAL0_IMAGE_MODELS": "recommended"},
            "gen.mode",
            "scaffold_and_download",
            "HAL0_IMAGE_MODELS",
        ),
        ([], {"HAL0_ALLOW_CPU_ONLY": "1"}, "hardware.allow_cpu_only", True, "HAL0_ALLOW_CPU_ONLY"),
        ([], {"HAL0_PORT": "9090"}, "network.port", 9090, "HAL0_PORT"),
        (["--models-dir=/data/m"], {}, "model_store.path", "/data/m", "--models-dir"),
        (
            [],
            {"HAL0_HERMES_TERMINAL": "1"},
            "apps.hermes.terminal_tool",
            True,
            "HAL0_HERMES_TERMINAL",
        ),
        (["--bare"], {}, "install.type", "minimal", "--bare"),
        (["--bare-inference"], {}, "install.type", "minimal", "--bare-inference"),
        (["--no-owui"], {}, "apps.openwebui.enabled", False, "--no-owui"),
        (["--no-comfyui"], {}, "gen.mode", "off", "--no-comfyui"),
        ([], {"HAL0_IMAGE_MODELS": "0"}, "gen.mode", "off", "HAL0_IMAGE_MODELS"),
        (
            [],
            {"HAL0_IMAGE_MODELS": "a, b c,d"},
            "gen.models",
            ["a", "b", "c", "d"],
            "HAL0_IMAGE_MODELS",
        ),
        (["--no-hermes"], {}, "apps.hermes.enabled", False, "--no-hermes"),
        ([], {"HAL0_SKIP_PI": "1"}, "apps.pi.enabled", False, "HAL0_SKIP_PI"),
        ([], {"HAL0_INSTALL_PI": "1"}, "apps.pi.enabled", True, "HAL0_INSTALL_PI"),
        (["--no-agents"], {}, "apps.hermes.enabled", False, "--no-agents"),
        (["--no-agents"], {}, "apps.pi.enabled", False, "--no-agents"),
        (["--no-memory"], {}, "memory.enabled", False, "--no-memory"),
        (["--no-brain-model"], {}, "models.brain", "none", "--no-brain-model"),
        ([], {"HAL0_SKIP_BRAIN_MODEL": "1"}, "models.brain", "none", "HAL0_SKIP_BRAIN_MODEL"),
        ([], {"HAL0_BRAIN_MODEL": "m1"}, "models.brain", "m1", "HAL0_BRAIN_MODEL"),
        ([], {"HAL0_PULL_AGENT_MODEL": "0"}, "models.starter", "none", "HAL0_PULL_AGENT_MODEL"),
        ([], {"HAL0_PULL_AGENT_MODEL": "1"}, "models.starter", "auto", "HAL0_PULL_AGENT_MODEL"),
        ([], {"HAL0_AGENT_MODEL": "m2"}, "models.starter", "m2", "HAL0_AGENT_MODEL"),
        ([], {"HAL0_MODELS_DIR": "/d"}, "model_store.path", "/d", "HAL0_MODELS_DIR"),
        ([], {"HAL0_FLM_MODELS_DIR": "/f"}, "model_store.flm_path", "/f", "HAL0_FLM_MODELS_DIR"),
        (
            [],
            {"HAL0_PUBLIC_URL": "https://h.example"},
            "network.public_url",
            "https://h.example",
            "HAL0_PUBLIC_URL",
        ),
        (
            [],
            {"HAL0_HERMES_TERMINAL": "0"},
            "apps.hermes.terminal_tool",
            False,
            "HAL0_HERMES_TERMINAL",
        ),
    ],
)
def test_flags_and_variables_become_overrides(
    flags: list[str], env: dict[str, str], key: str, value: object, source: str
) -> None:
    assert Override(key, value, source) in overrides(flags, env)


def test_recommended_image_models_mean_the_installer_picks_the_set() -> None:
    found = overrides([], {"HAL0_IMAGE_MODELS": "recommended"})
    assert Override("gen.mode", "scaffold_and_download", "HAL0_IMAGE_MODELS") in found
    assert Override("gen.models", [], "HAL0_IMAGE_MODELS") in found
    assert not [o for o in found if o.value == ["recommended"]]


@pytest.mark.parametrize("value", ["none", "0"])
def test_no_image_models_turn_images_off_without_a_list(value: str) -> None:
    found = overrides([], {"HAL0_IMAGE_MODELS": value})
    assert found == (Override("gen.mode", "off", "HAL0_IMAGE_MODELS"),)


def test_a_listen_address_in_a_saved_or_given_document_passes_through(catalog: Catalog) -> None:
    doc = effective(
        catalog,
        chosen={},
        saved={"network": {"bind_host": "127.0.0.1"}},
        given=None,
        over=[],
        hal0_version="v",
        now=NOW,
    )
    assert get(doc, "network.bind_host") == "127.0.0.1"
    doc = effective(
        catalog,
        chosen={},
        saved=None,
        given={"network": {"bind_host": "0.0.0.0"}},
        over=[],
        hal0_version="v",
        now=NOW,
    )
    assert get(doc, "network.bind_host") == "0.0.0.0"
    assert get(preset(catalog, "recommended"), "network.bind_host") is None


def test_a_skip_variable_not_set_to_one_is_ignored() -> None:
    assert overrides([], {"HAL0_SKIP_HERMES": "0", "HAL0_SKIP_PI": ""}) == ()


def test_known_flags_without_an_override_are_accepted() -> None:
    flags = ["--dev", "--no-start", "--summary-json=/tmp/s.json", "--help", "-h"]
    assert overrides(flags, {}) == ()


def test_unknown_flag_is_refused() -> None:
    with pytest.raises(DocumentError, match="--no-tls"):
        overrides(["--no-tls"], {})


def test_flag_value_problems_are_refused() -> None:
    with pytest.raises(DocumentError, match="--models-dir"):
        overrides(["--models-dir"], {})
    with pytest.raises(DocumentError, match="--models-dir"):
        overrides(["--models-dir="], {})
    with pytest.raises(DocumentError, match="--bare"):
        overrides(["--bare=1"], {})


@pytest.mark.parametrize("port", ["abc", "0", "65536", "-1", "9 0", "1.5", "٣"])
def test_a_bad_port_is_refused(port: str) -> None:
    with pytest.raises(DocumentError, match="HAL0_PORT"):
        overrides([], {"HAL0_PORT": port})


def test_empty_variables_produce_nothing() -> None:
    assert overrides([], {"HAL0_PORT": "", "HAL0_MODELS_DIR": "", "HAL0_IMAGE_MODELS": ""}) == ()


def test_skip_wins_over_install_pi() -> None:
    found = overrides([], {"HAL0_SKIP_PI": "1", "HAL0_INSTALL_PI": "1"})
    assert Override("apps.pi.enabled", False, "HAL0_SKIP_PI") in found
    assert not [o for o in found if o.value is True]


def test_a_flag_beats_a_variable_for_the_same_key(catalog: Catalog) -> None:
    over = overrides(["--models-dir=/flag"], {"HAL0_MODELS_DIR": "/var"})
    doc = effective(
        catalog, chosen={}, saved=None, given=None, over=over, hal0_version="1.9.0", now=NOW
    )
    assert get(doc, "model_store.path") == "/flag"


def test_precedence_is_override_then_chosen_then_given_then_saved_then_preset(
    catalog: Catalog,
) -> None:
    doc = effective(
        catalog,
        chosen={"network.port": 7000},
        saved={"network": {"port": 5000, "hostname": "old"}},
        given={"network": {"port": 6000}},
        over=[Override("memory.enabled", False, "--no-memory")],
        hal0_version="1.9.0",
        now=NOW,
    )
    assert get(doc, "network.port") == 7000
    assert get(doc, "network.hostname") == "old"
    assert get(doc, "memory.enabled") is False
    assert get(doc, "apps.hermes.enabled") is True


def test_a_present_key_is_never_overridden_by_the_preset(catalog: Catalog) -> None:
    doc = effective(
        catalog,
        chosen={},
        saved=None,
        given={"install": {"type": "minimal"}, "apps": {"hermes": {"enabled": True}}},
        over=[],
        hal0_version="1.9.0",
        now="t",
    )
    assert get(doc, "apps.hermes.enabled") is True
    assert get(doc, "apps.openwebui.enabled") is False


def test_install_type_comes_from_the_highest_layer(catalog: Catalog) -> None:
    doc = effective(
        catalog,
        chosen={"install.type": "custom"},
        saved={"install": {"type": "minimal"}},
        given=None,
        over=[],
        hal0_version="1.9.0",
        now="t",
    )
    assert get(doc, "install.type") == "custom"
    assert get(doc, "memory.enabled") is True
    doc = effective(
        catalog,
        chosen={"install.type": "custom"},
        saved=None,
        given=None,
        over=[Override("install.type", "minimal", "--bare")],
        hal0_version="1.9.0",
        now="t",
    )
    assert get(doc, "install.type") == "minimal"
    assert get(doc, "memory.enabled") is False


def test_an_unknown_install_type_from_saved_falls_back_to_recommended(catalog: Catalog) -> None:
    doc = effective(
        catalog,
        chosen={},
        saved={"install": {"type": "bogus"}, "network": {"port": 9000}},
        given=None,
        over=[],
        hal0_version="1.9.0",
        now="t",
    )
    assert get(doc, "install.type") == "bogus"
    assert get(doc, "network.port") == 9000
    assert get(doc, "apps.hermes.enabled") is True
    doc = effective(
        catalog,
        chosen={},
        saved={"install": {"type": 3}},
        given=None,
        over=[],
        hal0_version="1.9.0",
        now="t",
    )
    assert get(doc, "apps.hermes.enabled") is True


def test_an_unknown_install_type_from_anywhere_else_is_refused(catalog: Catalog) -> None:
    for kwargs in (
        {"given": {"install": {"type": "bogus"}}},
        {"chosen": {"install.type": "bogus"}},
        {"over": [Override("install.type", "bogus", "--bare")]},
        {"chosen": {"install.type": 3}},
    ):
        args: dict[str, object] = {"chosen": {}, "saved": None, "given": None, "over": []}
        args.update(kwargs)
        with pytest.raises(DocumentError, match=r"bogus|install\.type"):
            effective(catalog, hal0_version="1.9.0", now="t", **args)  # type: ignore[arg-type]


@pytest.mark.parametrize(
    ("layer", "over", "key", "value"),
    [
        (
            {"given": {"apps": {"hermes": {"terminal_tool": {"on": 1}}}}},
            [Override("apps.hermes.terminal_tool", False, "HAL0_HERMES_TERMINAL")],
            "apps.hermes.terminal_tool",
            False,
        ),
        (
            {"saved": {"apps": {"hermes": {"enabled": {"x": 1}}}}},
            [Override("apps.hermes.enabled", False, "--no-hermes")],
            "apps.hermes.enabled",
            False,
        ),
        (
            {"saved": {"network": {"port": {}}}},
            [Override("network.port", 9090, "HAL0_PORT")],
            "network.port",
            9090,
        ),
    ],
)
def test_an_override_replaces_an_object_valued_leaf(
    catalog: Catalog,
    layer: dict[str, dict[str, object]],
    over: list[Override],
    key: str,
    value: object,
) -> None:
    doc = effective(
        catalog,
        chosen={},
        saved=layer.get("saved"),
        given=layer.get("given"),
        over=over,
        hal0_version="1.9.0",
        now="t",
    )
    assert get(doc, key) == value


def test_a_choice_replaces_an_object_valued_leaf(catalog: Catalog) -> None:
    doc = effective(
        catalog,
        chosen={"network.port": 7000},
        saved={"network": {"port": {"x": 1}}},
        given=None,
        over=[],
        hal0_version="1.9.0",
        now="t",
    )
    assert get(doc, "network.port") == 7000


def test_terminal_tool_is_never_taken_from_a_saved_document(catalog: Catalog) -> None:
    doc = effective(
        catalog,
        chosen={},
        saved={"apps": {"hermes": {"terminal_tool": True}}},
        given=None,
        over=[],
        hal0_version="1.9.0",
        now="t",
    )
    assert get(doc, "apps.hermes.terminal_tool") is False


def test_terminal_tool_can_still_be_chosen_or_overridden(catalog: Catalog) -> None:
    doc = effective(
        catalog,
        chosen={"apps.hermes.terminal_tool": True},
        saved=None,
        given=None,
        over=[],
        hal0_version="1.9.0",
        now="t",
    )
    assert get(doc, "apps.hermes.terminal_tool") is True


def test_effective_does_not_mutate_its_inputs(catalog: Catalog) -> None:
    saved = {"apps": {"hermes": {"terminal_tool": True}}}
    given = {"gen": {"models": ["a"]}}
    effective(
        catalog,
        chosen={},
        saved=saved,
        given=given,
        over=[],
        hal0_version="1.9.0",
        now="t",
    )
    assert saved == {"apps": {"hermes": {"terminal_tool": True}}}
    assert given == {"gen": {"models": ["a"]}}


@pytest.mark.parametrize("state", ["upgrade", "unfinished"])
def test_an_upgrade_never_gains_an_admin_key_from_the_preset(catalog: Catalog, state: str) -> None:
    doc = effective(
        catalog,
        chosen={},
        saved={"network": {"port": 9000}},
        given=None,
        over=[],
        hal0_version="1.9.0",
        now="t",
        install_state=state,
    )
    assert get(doc, "access.admin_key") is None
    kept = effective(
        catalog,
        chosen={},
        saved={"access": {"admin_key": "generate"}},
        given=None,
        over=[],
        hal0_version="1.9.0",
        now="t",
        install_state=state,
    )
    assert get(kept, "access.admin_key") == "generate"


def test_a_fresh_install_gets_the_preset_admin_key(catalog: Catalog) -> None:
    doc = effective(
        catalog, chosen={}, saved=None, given=None, over=[], hal0_version="1.9.0", now="t"
    )
    assert get(doc, "access.admin_key") == "generate"


def test_underscore_chosen_keys_are_not_answers(catalog: Catalog) -> None:
    doc = effective(
        catalog,
        chosen={"_hf_token": "secret", "network.port": 1},
        saved=None,
        given=None,
        over=[],
        hal0_version="1.9.0",
        now="t",
    )
    assert "_hf_token" not in json.dumps(doc)
    assert "secret" not in json.dumps(doc)
    assert get(doc, "network.port") == 1


def test_a_higher_layer_replaces_a_non_object_in_a_lower_one(catalog: Catalog) -> None:
    doc = effective(
        catalog,
        chosen={},
        saved={"network": "oops"},
        given={"network": {"port": 6000}},
        over=[],
        hal0_version="1.9.0",
        now="t",
    )
    assert doc["network"] == {"port": 6000}
    doc = effective(
        catalog,
        chosen={"network.port": 7000},
        saved={"network": "oops"},
        given=None,
        over=[Override("apps.hermes.enabled.x", 1, "t")],
        hal0_version="1.9.0",
        now="t",
    )
    assert doc["network"] == {"port": 7000}
    assert get(doc, "apps.hermes.enabled.x") == 1


def test_a_lower_non_object_never_replaces_a_higher_object(catalog: Catalog) -> None:
    doc = effective(
        catalog,
        chosen={},
        saved={"apps": "text"},
        given=None,
        over=[],
        hal0_version="1.9.0",
        now="t",
    )
    assert get(doc, "apps.hermes.enabled") is True


def test_a_layer_never_replaces_a_whole_sub_object_with_a_partial_one(catalog: Catalog) -> None:
    doc = effective(
        catalog,
        chosen={},
        saved=None,
        given={"apps": {"pi": {"enabled": True}}},
        over=[],
        hal0_version="1.9.0",
        now="t",
    )
    assert get(doc, "apps.pi.enabled") is True
    assert get(doc, "apps.hermes.enabled") is True


def test_a_layer_that_is_not_an_object_is_refused(catalog: Catalog) -> None:
    with pytest.raises(DocumentError, match="saved"):
        effective(
            catalog,
            chosen={},
            saved=[1],  # type: ignore[arg-type]
            given=None,
            over=[],
            hal0_version="1.9.0",
            now="t",
        )


def test_bad_dotted_keys_are_refused(catalog: Catalog) -> None:
    with pytest.raises(DocumentError, match="key"):
        put({}, "a..b", 1)
    with pytest.raises(DocumentError, match="key"):
        put({}, "", 1)
    with pytest.raises(DocumentError, match="key"):
        effective(
            catalog,
            chosen={"a..b": 1},
            saved=None,
            given=None,
            over=[],
            hal0_version="1.9.0",
            now="t",
        )


def test_get_and_put_walk_dotted_keys() -> None:
    doc: dict[str, object] = {}
    put(doc, "a.b.c", 1)
    assert doc == {"a": {"b": {"c": 1}}}
    assert get(doc, "a.b.c") == 1
    assert get(doc, "a.x.c") is None
    assert get(doc, "a.b.c.d") is None


def test_document_records_when_and_by_which_version(catalog: Catalog) -> None:
    doc = effective(
        catalog, chosen={}, saved=None, given=None, over=[], hal0_version="1.9.0", now=NOW
    )
    assert doc["version"] == 1
    assert doc["meta"] == {"saved_at": NOW, "saved_by": "1.9.0"}


def test_write_is_private_and_atomic_and_refuses_a_symlink(tmp_path: Path) -> None:
    target = tmp_path / "answers.json"
    write_document({"version": 1}, target)
    assert stat.S_IMODE(target.stat().st_mode) == 0o600
    assert json.loads(target.read_text()) == {"version": 1}
    link = tmp_path / "link.json"
    link.symlink_to(tmp_path / "elsewhere")
    with pytest.raises(DocumentError, match="symlink"):
        write_document({"version": 1}, link)
    assert not (tmp_path / "elsewhere").exists()


def test_write_refuses_a_symlink_to_an_existing_file(tmp_path: Path) -> None:
    real = tmp_path / "real.json"
    real.write_text("keep")
    link = tmp_path / "link.json"
    link.symlink_to(real)
    with pytest.raises(DocumentError, match="symlink"):
        write_document({"version": 1}, link)
    assert real.read_text() == "keep"


def test_write_format_and_replacement(tmp_path: Path) -> None:
    target = tmp_path / "answers.json"
    target.write_text("old")
    target.chmod(0o644)
    write_document({"b": 1, "a": {"z": 1, "y": 2}}, target)
    assert target.read_text() == json.dumps({"a": {"y": 2, "z": 1}, "b": 1}, indent=2) + "\n"
    assert stat.S_IMODE(target.stat().st_mode) == 0o600
    assert [p.name for p in tmp_path.iterdir()] == ["answers.json"]


def test_a_failed_write_leaves_no_temporary_file(tmp_path: Path) -> None:
    target = tmp_path / "dir"
    target.mkdir()
    with pytest.raises(DocumentError, match="dir"):
        write_document({"version": 1}, target)
    assert [p.name for p in tmp_path.iterdir()] == ["dir"]


def test_write_refuses_what_it_cannot_serialise(tmp_path: Path) -> None:
    target = tmp_path / "answers.json"
    with pytest.raises(DocumentError):
        write_document({"x": object()}, target)
    with pytest.raises(DocumentError):
        write_document({"x": float("nan")}, target)
    assert list(tmp_path.iterdir()) == []


def test_write_into_a_missing_directory_is_a_document_error(tmp_path: Path) -> None:
    with pytest.raises(DocumentError, match="nope"):
        write_document({"version": 1}, tmp_path / "nope" / "a.json")


def test_load_keeps_unknown_keys_and_refuses_non_objects(tmp_path: Path) -> None:
    p = tmp_path / "a.json"
    p.write_text('{"version": 1, "future": {"x": 1}}')
    assert load_document(p)["future"] == {"x": 1}
    p.write_text("[1]")
    with pytest.raises(DocumentError):
        load_document(p)


def test_load_tolerates_another_version(tmp_path: Path) -> None:
    p = tmp_path / "a.json"
    p.write_text('{"version": 7}')
    assert load_document(p) == {"version": 7}


def test_load_refusals_are_one_line_and_name_the_path(tmp_path: Path) -> None:
    p = tmp_path / "a.json"
    with pytest.raises(DocumentError, match=r"a\.json") as missing:
        load_document(p)
    p.write_text("{nope")
    with pytest.raises(DocumentError, match=r"a\.json") as bad:
        load_document(p)
    p.write_bytes(b"\xff\xfe\x00")
    with pytest.raises(DocumentError, match=r"a\.json") as binary:
        load_document(p)
    with pytest.raises(DocumentError, match=r"cannot be read") as directory:
        load_document(tmp_path)
    for err in (missing, bad, binary, directory):
        assert "\n" not in str(err.value)


def test_a_deeply_nested_file_is_a_document_error(tmp_path: Path) -> None:
    p = tmp_path / "a.json"
    p.write_text("[" * 200000)
    with pytest.raises(DocumentError, match=r"a\.json"):
        load_document(p)


def test_a_write_cut_short_by_an_interrupt_leaves_no_temporary_file(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    def interrupted(fd: int) -> None:
        raise KeyboardInterrupt

    monkeypatch.setattr("hal0.install.wizard.document.os.fsync", interrupted)
    with pytest.raises(KeyboardInterrupt):
        write_private_text("x", tmp_path / "a.json")
    assert list(tmp_path.iterdir()) == []


# ------------------------------------------------- an old saved model choice


def _starter_catalog(catalog: Catalog, *models: str) -> Catalog:
    rows = tuple(ModelRow("starter", m, m.title(), "cpu", 1.0, None, None, False) for m in models)
    return replace(catalog, model_ladder=rows)


def _starter(catalog: Catalog, **layers: object) -> object:
    layers = {"chosen": {}, "saved": None, "given": None, "over": [], **layers}
    doc = effective(catalog, hal0_version="v", now=NOW, **layers)  # type: ignore[arg-type]
    return get(doc, "models.starter")


def test_a_saved_model_that_is_no_longer_offered_is_dropped(catalog: Catalog) -> None:
    ladder = _starter_catalog(catalog, "new-1")
    assert _starter(ladder, saved={"models": {"starter": "old-1"}}) == "auto"


def test_a_saved_model_that_is_still_offered_is_kept(catalog: Catalog) -> None:
    ladder = _starter_catalog(catalog, "new-1", "new-2")
    assert _starter(ladder, saved={"models": {"starter": "new-2"}}) == "new-2"


@pytest.mark.parametrize("value", ["auto", "none"])
def test_a_saved_auto_or_none_is_kept(catalog: Catalog, value: str) -> None:
    ladder = _starter_catalog(catalog, "new-1")
    assert _starter(ladder, saved={"models": {"starter": value}}) == value


def test_a_saved_model_is_kept_when_there_is_no_ladder(catalog: Catalog) -> None:
    assert _starter(catalog, saved={"models": {"starter": "old-1"}}) == "old-1"


def test_a_given_model_that_is_not_offered_passes_through(catalog: Catalog) -> None:
    ladder = _starter_catalog(catalog, "new-1")
    saved = {"models": {"starter": "old-1"}}
    assert _starter(ladder, given={"models": {"starter": "mine-1"}}) == "mine-1"
    assert _starter(ladder, saved=saved, given={"models": {"starter": "mine-1"}}) == "mine-1"


def test_a_chosen_model_that_is_not_offered_passes_through(catalog: Catalog) -> None:
    ladder = _starter_catalog(catalog, "new-1")
    saved = {"models": {"starter": "old-1"}}
    assert _starter(ladder, saved=saved, chosen={"models.starter": "mine-1"}) == "mine-1"


def test_an_overridden_model_that_is_not_offered_passes_through(catalog: Catalog) -> None:
    ladder = _starter_catalog(catalog, "new-1")
    saved = {"models": {"starter": "old-1"}}
    over = [Override("models.starter", "mine-1", "HAL0_AGENT_MODEL")]
    assert _starter(ladder, saved=saved, over=over) == "mine-1"


# ------------------------------------------- the NPU folder must sit inside the store


def _with_flm(
    catalog: Catalog, store: str, flm: str, *, saved: dict[str, object] | None = None
) -> dict[str, object]:
    env = {"HAL0_MODELS_DIR": store, "HAL0_FLM_MODELS_DIR": flm}
    return effective(
        catalog,
        chosen={},
        saved=saved,
        given=None,
        over=overrides([], env),
        hal0_version="v",
        now=NOW,
    )


def test_an_npu_folder_inside_the_store_is_written(catalog: Catalog) -> None:
    doc = _with_flm(catalog, "/a/models", "/a/models/flm")
    assert get(doc, "model_store.flm_path") == "/a/models/flm"


@pytest.mark.parametrize(
    "flm",
    ["/elsewhere/flm", "/a/models", "/a/models/", "/a/models-x", "/a/models/../x", "flm", "/"],
)
def test_an_npu_folder_not_strictly_inside_the_store_is_left_out(
    catalog: Catalog, flm: str
) -> None:
    doc = _with_flm(catalog, "/a/models", flm)
    assert get(doc, "model_store.flm_path") is None
    assert get(doc, "model_store.path") == "/a/models"


def test_a_lower_layer_npu_folder_does_not_come_back(catalog: Catalog) -> None:
    saved: dict[str, object] = {"model_store": {"flm_path": "/a/models/old"}}
    doc = _with_flm(catalog, "/a/models", "/elsewhere/flm", saved=saved)
    assert get(doc, "model_store.flm_path") is None


def test_an_outside_npu_folder_is_still_an_override() -> None:
    (found,) = [
        o
        for o in overrides([], {"HAL0_FLM_MODELS_DIR": "/elsewhere/flm"})
        if o.key == "model_store.flm_path"
    ]
    assert (found.value, found.source) == ("/elsewhere/flm", "HAL0_FLM_MODELS_DIR")


def test_an_npu_variable_written_with_a_doubled_leading_slash_counts_as_inside(
    catalog: Catalog,
) -> None:
    doc = _with_flm(catalog, "/a/models", "//a/models/flm")
    assert get(doc, "model_store.flm_path") == "//a/models/flm"
