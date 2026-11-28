from __future__ import annotations

import json
from dataclasses import replace
from pathlib import Path

import pytest

from hal0.install.wizard.catalog import Catalog, ModelRow, Option, load_catalog
from hal0.install.wizard.document import Override, effective
from hal0.install.wizard.facts import Facts, load_facts
from hal0.install.wizard.flow import (
    Outcome,
    Screen,
    State,
    chosen,
    outcome,
    press,
    secret,
    sequence,
    start,
    view,
)


def run(state: State, *keys: str) -> State:
    for k in keys:
        state = press(state, k)
    return state


def custom(catalog: Catalog, facts: Facts, **kw: object) -> State:
    return run(start(catalog, facts, **kw), "DOWN", "DOWN", "ENTER")  # type: ignore[arg-type]


def open_field(state: State, field: str) -> State:
    """Move the highlight to the row called `field` and press Space."""
    index = [r.id for r in view(state).rows].index(field)
    delta = index - view(state).cursor
    return run(state, *(["DOWN" if delta > 0 else "UP"] * abs(delta)), "SPACE")


def test_customize_is_five_screens_then_the_same_review(
    catalog: Catalog, usable_facts: Facts
) -> None:
    assert sequence("custom", usable_facts) == (
        Screen.TYPE,
        Screen.JOBS,
        Screen.MODELS,
        Screen.STORAGE,
        Screen.NETWORK,
        Screen.ASSISTANT,
        Screen.REVIEW,
    )
    assert view(custom(catalog, usable_facts)).position == "Customize 1 of 5"
    end = run(custom(catalog, usable_facts), "ENTER", "ENTER", "ENTER", "ENTER")
    assert view(end).position == "Customize 5 of 5"
    review = press(end, "ENTER")
    assert (view(review).screen, view(review).position) == (Screen.REVIEW, "Review")
    assert view(start(catalog, usable_facts)).position == "1 of 3"


def test_jobs_start_from_the_recommended_preset(catalog: Catalog, usable_facts: Facts) -> None:
    rows = {r.id: r for r in view(custom(catalog, usable_facts)).rows}
    assert (rows["openwebui"].value, rows["images"].value) == (True, False)


def test_unmet_need_greys_the_row_with_its_reason_and_it_cannot_be_ticked(
    catalog: Catalog, usable_facts: Facts
) -> None:
    s = custom(catalog, usable_facts)  # no NPU on this box
    npu = next(r for r in view(s).rows if r.id == "npu")
    assert (npu.state, npu.note) == ("unavailable", "no NPU was found")
    assert "npu.opt_in" not in chosen(run(s, "DOWN", "DOWN", "DOWN", "DOWN", "SPACE"))


def test_unknown_fact_leaves_the_row_open_with_a_note(catalog: Catalog) -> None:
    s = custom(catalog, load_facts(None, disks=[], ram_gb=None))
    row = next(r for r in view(s).rows if r.id == "openwebui")
    assert row.state == "open"
    assert row.note == "could not be checked on this box"


def test_npu_row_is_conditional_when_a_device_is_found(catalog: Catalog, npu_facts: Facts) -> None:
    npu = next(r for r in view(custom(catalog, npu_facts)).rows if r.id == "npu")
    assert (npu.state, npu.value, npu.note) == (
        "open",
        True,
        "found; set up if it passes its check",
    )


def test_unprivileged_container_says_where_the_npu_comes_from(
    catalog: Catalog, lxc_unprivileged_no_npu_facts: Facts
) -> None:
    rows = view(custom(catalog, lxc_unprivileged_no_npu_facts)).rows
    npu = next(r for r in rows if r.id == "npu")
    assert npu.note == (
        "no NPU was found; in a Proxmox container the host has to pass it through first"
    )


def test_flag_fixes_its_row_and_the_rest_stay_open(catalog: Catalog, usable_facts: Facts) -> None:
    over = [Override("apps.hermes.enabled", False, "--no-hermes")]
    rows = {r.id: r for r in view(custom(catalog, usable_facts, over=over)).rows}
    assert (rows["hermes"].state, rows["hermes"].note, rows["memory"].state) == (
        "fixed",
        "set by --no-hermes",
        "open",
    )


def test_ticking_images_sets_its_key_to_the_catalog_on_value(
    catalog: Catalog, usable_facts: Facts
) -> None:
    s = run(custom(catalog, usable_facts), "DOWN", "DOWN", "DOWN", "SPACE")
    assert chosen(s)["gen.mode"] == "scaffold_and_download"


def test_model_row_greyed_when_pool_known_and_too_small(
    ladder_catalog: Catalog, small_pool_facts: Facts
) -> None:
    rows = view(run(custom(ladder_catalog, small_pool_facts), "ENTER")).rows
    big = next(r for r in rows if r.id == "big-1")
    assert big.state == "unavailable"
    assert "needs 24 GB of GPU memory; this box has 8" in big.note


def test_model_row_open_with_requirement_when_pool_unknown_and_review_warns(
    ladder_catalog: Catalog, unknown_pool_facts: Facts
) -> None:
    s = run(custom(ladder_catalog, unknown_pool_facts), "ENTER")
    big = next(r for r in view(s).rows if r.id == "big-1")
    assert big.state == "open"
    assert big.note == "needs 24 GB of GPU memory; could not be checked on this box"
    # pick big-1 (two down), blank token, storage, network, assistant
    review = view(run(s, "DOWN", "DOWN", "ENTER", "ENTER", "ENTER", "ENTER", "ENTER", "ENTER"))
    assert any(r.id == "warning" and "big-1" in r.detail for r in review.rows)


def test_token_is_asked_only_for_a_model_that_needs_an_account(
    ladder_catalog: Catalog, big_pool_facts: Facts
) -> None:
    small = run(custom(ladder_catalog, big_pool_facts), "ENTER", "DOWN", "ENTER")
    assert view(small).screen is Screen.STORAGE
    big = run(custom(ladder_catalog, big_pool_facts), "ENTER", "DOWN", "DOWN", "ENTER")
    assert view(big).editing is not None
    assert "HuggingFace" in view(big).title
    typed = run(big, *"hf_abc", "ENTER")
    assert secret(typed) == "hf_abc"
    assert "hf_abc" not in json.dumps(chosen(typed))
    assert view(typed).screen is Screen.STORAGE


def test_the_token_is_never_shown_while_typing(
    ladder_catalog: Catalog, big_pool_facts: Facts
) -> None:
    s = run(custom(ladder_catalog, big_pool_facts), "ENTER", "DOWN", "DOWN", "ENTER", *"hf_abc")
    assert view(s).editing == ""
    assert "hf_abc" not in repr(s)
    assert "hf_abc" not in json.dumps(chosen(s))


def test_a_blank_token_skips_and_goes_on(ladder_catalog: Catalog, big_pool_facts: Facts) -> None:
    s = run(custom(ladder_catalog, big_pool_facts), "ENTER", "DOWN", "DOWN", "ENTER", "ENTER")
    assert view(s).screen is Screen.STORAGE
    assert secret(s) is None


def test_empty_ladder_says_so_and_asks_nothing(catalog: Catalog, usable_facts: Facts) -> None:
    s = custom(catalog, usable_facts)
    models = run(s, "ENTER")
    v = view(models)
    assert v.rows[0].kind == "info"
    assert "added after install" in v.rows[0].detail
    assert view(press(models, "ENTER")).screen is Screen.STORAGE
    assert "models.starter" not in chosen(press(models, "ENTER"))


_IMAGES_ON: dict[str, object] = {"gen": {"mode": "scaffold_and_download"}}


def _with_image_row(catalog: Catalog) -> Catalog:
    row = ModelRow("image", "img-1", "Image", "cpu", None, None, None, False)
    return replace(catalog, model_ladder=(*catalog.model_ladder, row))


def test_the_image_step_needs_an_image_row(ladder_catalog: Catalog, big_pool_facts: Facts) -> None:
    s = run(custom(ladder_catalog, big_pool_facts), "DOWN", "DOWN", "DOWN", "SPACE", "ENTER")
    assert view(run(s, "ENTER")).screen is Screen.STORAGE


def test_the_image_step_needs_the_images_job(
    ladder_catalog: Catalog, big_pool_facts: Facts
) -> None:
    s = run(custom(_with_image_row(ladder_catalog), big_pool_facts), "ENTER", "ENTER")
    assert view(s).screen is Screen.STORAGE


def test_image_step_picks_into_gen_models(ladder_catalog: Catalog, big_pool_facts: Facts) -> None:
    catalog = _with_image_row(ladder_catalog)
    s = run(custom(catalog, big_pool_facts), "DOWN", "DOWN", "DOWN", "SPACE", "ENTER", "ENTER")
    assert view(s).screen is Screen.MODELS
    assert [r.id for r in view(s).rows] == ["recommended", "img-1"]
    done = run(s, "DOWN", "ENTER")
    assert view(done).screen is Screen.STORAGE
    assert chosen(done)["gen.models"] == ["img-1"]


def test_storage_shows_the_npu_folder_only_with_an_npu(
    catalog: Catalog, usable_facts: Facts, npu_facts: Facts
) -> None:
    def ids(f: Facts) -> list[str]:
        return [r.id for r in view(run(custom(catalog, f), "ENTER", "ENTER")).rows]

    assert "flm_store" not in ids(usable_facts)
    assert "flm_store" in ids(npu_facts)


def test_a_typed_storage_folder_is_recorded_and_stays_on_the_screen(
    catalog: Catalog, usable_facts: Facts
) -> None:
    s = run(custom(catalog, usable_facts), "ENTER", "ENTER", "SPACE", *"/srv/m", "ENTER")
    assert view(s).screen is Screen.STORAGE
    assert chosen(s)["model_store.path"] == "/srv/m"


@pytest.mark.parametrize(
    ("field", "typed", "error"),
    [
        ("port", "0", "between 1 and 65535"),
        ("port", "http", "between 1 and 65535"),
        ("hostname", "my box", "letters, digits and hyphens"),
        ("hostname", "-bad", "letters, digits and hyphens"),
    ],
)
def test_network_fields_are_validated(
    catalog: Catalog, usable_facts: Facts, field: str, typed: str, error: str
) -> None:
    s = run(custom(catalog, usable_facts), "ENTER", "ENTER", "ENTER")  # on NETWORK
    s = open_field(s, field)
    s = run(s, *typed, "ENTER")
    assert view(s).error is not None
    assert error in view(s).error  # type: ignore[operator]
    assert view(s).screen is Screen.NETWORK


def test_network_values_are_recorded_in_their_shapes(catalog: Catalog, usable_facts: Facts) -> None:
    s = run(custom(catalog, usable_facts), "ENTER", "ENTER", "ENTER")
    s = run(open_field(s, "port"), *"9000", "ENTER")
    s = run(open_field(s, "hostname"), *"my-box.lan", "ENTER")
    s = run(open_field(s, "allowed_origins"), *"https://a.test, https://b.test", "ENTER")
    answers = chosen(s)
    assert answers["network.port"] == 9000
    assert answers["network.hostname"] == "my-box.lan"
    assert answers["network.allowed_origins"] == ["https://a.test", "https://b.test"]


def test_the_listen_address_is_not_a_question_and_the_network_screen_has_no_row_for_it(
    catalog: Catalog, usable_facts: Facts
) -> None:
    assert "bind_host" not in catalog.questions
    s = run(custom(catalog, usable_facts), "ENTER", "ENTER", "ENTER")
    assert view(s).screen is Screen.NETWORK
    assert [r.id for r in view(s).rows] == ["hostname", "port", "allowed_origins"]


def test_a_customize_run_never_writes_a_listen_address(
    catalog: Catalog, usable_facts: Facts
) -> None:
    s = run(custom(catalog, usable_facts), "ENTER", "ENTER", "ENTER")
    s = run(open_field(s, "port"), *"9000", "ENTER")
    s = run(s, "ENTER", "ENTER")
    assert view(s).screen is Screen.REVIEW
    assert not [k for k in chosen(s) if "bind_host" in k]


def test_a_question_with_options_in_a_multi_row_screen_is_one_row_that_steps_on_space(
    catalog: Catalog, usable_facts: Facts
) -> None:
    memory = catalog.questions["memory_model"]
    two = replace(
        memory,
        options=(*memory.options, Option("dedicated", "Its own model", "memory gets a model")),
    )
    two_catalog = replace(catalog, questions={**catalog.questions, "memory_model": two})
    s = run(custom(two_catalog, usable_facts), "ENTER", "ENTER", "ENTER", "ENTER")
    s = open_field(s, "memory_model")
    assert view(s).editing is None
    assert chosen(s)["models.memory"] == "dedicated"
    row = next(r for r in view(s).rows if r.id == "memory_model")
    assert row.detail == "Its own model"
    assert chosen(press(s, "SPACE"))["models.memory"] == "shared"


_ABSENT_IDS = {
    "starter_model": {"auto", "small-1", "big-1"},
    "image_model": {"recommended", "img-1"},
    "hf_token": set(),
}


@pytest.mark.parametrize(
    "qid",
    [
        "model_store",
        "flm_store",
        "hostname",
        "port",
        "allowed_origins",
        "terminal_tool",
        "memory_model",
        "starter_model",
        "image_model",
        "hf_token",
    ],
)
def test_a_customize_row_whose_question_is_gone_is_skipped_never_an_error(
    ladder_catalog: Catalog, npu_facts: Facts, qid: str
) -> None:
    catalog = _with_image_row(ladder_catalog)
    questions = {k: v for k, v in catalog.questions.items() if k != qid}
    s = custom(replace(catalog, questions=questions), npu_facts, given=_IMAGES_ON)
    seen: set[str] = set()
    for _ in range(12):
        v = view(s)
        view(press(s, "?"))
        if v.screen is Screen.REVIEW:
            break
        seen |= {r.id for r in v.rows}
        s = press(s, "ENTER")
    assert view(s).screen is Screen.REVIEW
    assert not seen & _ABSENT_IDS.get(qid, {qid})


def test_terminal_tool_defaults_off_and_review_prints_it_when_on(
    catalog: Catalog, usable_facts: Facts
) -> None:
    s = run(custom(catalog, usable_facts), "ENTER", "ENTER", "ENTER", "ENTER")  # to ASSISTANT
    row = next(r for r in view(s).rows if r.id == "terminal_tool")
    assert row.value is False
    assert catalog.questions["terminal_tool"].help in " ".join(view(press(s, "?")).intro)
    on = run(s, "SPACE", "ENTER")
    review = next(r for r in view(on).rows if r.id == "terminal_tool")
    assert review.detail == "ON: the assistant can run commands as a root-equivalent user"


def test_the_terminal_tool_row_is_unavailable_when_the_assistant_is_off(
    catalog: Catalog, usable_facts: Facts
) -> None:
    over = [Override("apps.hermes.enabled", False, "--no-hermes")]
    s = run(custom(catalog, usable_facts, over=over), "ENTER", "ENTER", "ENTER", "ENTER")
    row = next(r for r in view(s).rows if r.id == "terminal_tool")
    assert row.state == "unavailable"
    assert row.note == "An assistant that carries out tasks (Hermes) is not being installed"
    assert "apps.hermes.terminal_tool" not in chosen(press(s, "SPACE"))


def test_question_mark_on_a_job_shows_that_jobs_help(catalog: Catalog, usable_facts: Facts) -> None:
    s = custom(catalog, usable_facts)
    owui = next(c for c in catalog.components if c.id == "openwebui")
    assert owui.help in " ".join(view(press(s, "?")).intro)


def test_customize_with_nothing_changed_adds_only_the_type(
    catalog: Catalog, usable_facts: Facts
) -> None:
    s = run(custom(catalog, usable_facts), *["ENTER"] * 5)
    assert view(s).screen is Screen.REVIEW
    assert chosen(s) == {"install.type": "custom"}


def test_back_from_review_returns_to_the_last_customize_screen(
    catalog: Catalog, usable_facts: Facts
) -> None:
    s = run(custom(catalog, usable_facts), *["ENTER"] * 5)
    assert view(press(s, "b")).screen is Screen.ASSISTANT


def _models(catalog: Catalog, facts: Facts, **kw: object) -> State:
    return run(custom(catalog, facts, **kw), "ENTER")


def test_models_list_is_auto_then_the_ladder_then_none(
    ladder_catalog: Catalog, big_pool_facts: Facts
) -> None:
    rows = view(_models(ladder_catalog, big_pool_facts)).rows
    assert [r.id for r in rows] == ["auto", "small-1", "big-1", "none"]
    options = {o.value: o for o in ladder_catalog.questions["starter_model"].options}
    assert rows[0].label == options["auto"].label
    assert rows[-1].label == options["none"].label


def test_the_auto_row_names_the_expected_pick_or_says_it_is_not_known_yet(
    ladder_catalog: Catalog, big_pool_facts: Facts, small_pool_facts: Facts
) -> None:
    fits = view(_models(ladder_catalog, big_pool_facts)).rows[0]
    assert fits.detail == "Small (2 GB)"
    unknown = replace(small_pool_facts, gpu_pool_gb=None, ram_gb=None)
    none_fit = view(_models(ladder_catalog, unknown)).rows[0]
    assert none_fit.detail == "chosen after hal0 has checked this box"


def test_one_unchanged_enter_leaves_the_starter_out_of_the_answers(
    ladder_catalog: Catalog, big_pool_facts: Facts
) -> None:
    s = _models(ladder_catalog, big_pool_facts)
    assert view(s).cursor == 0
    assert "models.starter" not in chosen(press(s, "ENTER"))


def test_a_saved_model_starts_the_cursor_on_its_row(
    ladder_catalog: Catalog, big_pool_facts: Facts
) -> None:
    saved: dict[str, object] = {"models": {"starter": "small-1"}}
    assert view(_models(ladder_catalog, big_pool_facts, saved=saved)).cursor == 1
    unknown: dict[str, object] = {"models": {"starter": "gone-9"}}
    assert view(_models(ladder_catalog, big_pool_facts, saved=unknown)).cursor == 0


def test_a_fixed_model_puts_the_cursor_on_the_fixed_row(
    ladder_catalog: Catalog, big_pool_facts: Facts
) -> None:
    over = [Override("models.starter", "big-1", "HAL0_AGENT_MODEL")]
    v = view(_models(ladder_catalog, big_pool_facts, over=over))
    assert v.rows[v.cursor].id == "big-1"
    assert v.rows[v.cursor].note == "set by HAL0_AGENT_MODEL"


def test_enter_on_an_unavailable_row_sets_the_error_and_stays(
    ladder_catalog: Catalog, small_pool_facts: Facts
) -> None:
    s = run(_models(ladder_catalog, small_pool_facts), "DOWN", "DOWN", "ENTER")
    assert view(s).screen is Screen.MODELS
    assert view(s).error == "needs 24 GB of GPU memory; this box has 8"


def _typed_token(catalog: Catalog, facts: Facts) -> State:
    s = run(_models(catalog, facts), "DOWN", "DOWN", "ENTER", *"hf_x", "ENTER")
    assert secret(s) == "hf_x"
    return s


def test_a_blank_token_clears_an_earlier_one(
    ladder_catalog: Catalog, big_pool_facts: Facts
) -> None:
    s = _typed_token(ladder_catalog, big_pool_facts)
    again = run(s, "b", "ENTER")  # cursor is back on big-1: its field reopens
    assert view(again).editing == ""
    assert secret(run(again, "ENTER")) is None


def test_a_model_without_an_account_clears_an_earlier_token(
    ladder_catalog: Catalog, big_pool_facts: Facts
) -> None:
    models = run(_typed_token(ladder_catalog, big_pool_facts), "b")
    assert view(models).screen is Screen.MODELS
    assert view(models).rows[view(models).cursor].id == "big-1"
    free = run(models, "UP", "ENTER")
    assert view(free).screen is Screen.STORAGE
    assert secret(free) is None


def test_no_token_question_means_no_token_field(
    ladder_catalog: Catalog, big_pool_facts: Facts
) -> None:
    questions = {k: v for k, v in ladder_catalog.questions.items() if k != "hf_token"}
    s = run(_models(replace(ladder_catalog, questions=questions), big_pool_facts), "DOWN", "DOWN")
    assert view(press(s, "ENTER")).screen is Screen.STORAGE


def test_the_storage_row_shows_the_path_and_free_space(
    catalog: Catalog, usable_facts: Facts
) -> None:
    row = view(run(custom(catalog, usable_facts), "ENTER", "ENTER")).rows[0]
    assert row.detail == "/var/lib/hal0/models (100 GB free)"


def test_an_unavailable_job_the_preset_turns_on_keeps_its_value(
    catalog: Catalog, no_python_facts: Facts
) -> None:
    row = next(r for r in view(custom(catalog, no_python_facts)).rows if r.id == "memory")
    assert (row.state, row.value) == ("unavailable", True)


def test_turning_the_assistant_off_drops_the_terminal_tool_answer(
    catalog: Catalog, usable_facts: Facts
) -> None:
    on = run(custom(catalog, usable_facts), "ENTER", "ENTER", "ENTER", "ENTER", "SPACE")
    assert chosen(on)["apps.hermes.terminal_tool"] is True
    off = run(on, "b", "b", "b", "b", "DOWN", "SPACE", *["ENTER"] * 5)
    assert view(off).screen is Screen.REVIEW
    assert "apps.hermes.terminal_tool" not in chosen(off)
    rows = {r.id: r for r in view(off).rows}
    assert rows["terminal_tool"].detail == "off"


def test_the_empty_ladder_hint_does_not_offer_a_choice(
    catalog: Catalog, usable_facts: Facts
) -> None:
    assert "choose" not in view(_models(catalog, usable_facts)).hint


def _image_catalog(ladder_catalog: Catalog, image_needs_account: bool) -> Catalog:
    row = ModelRow("image", "img-1", "Image", "cpu", None, None, None, image_needs_account)
    return replace(ladder_catalog, model_ladder=(*ladder_catalog.model_ladder, row))


def _at_image_step(catalog: Catalog, facts: Facts) -> State:
    """Images job on; big-1 (needs an account) picked on the chat step."""
    s = run(custom(catalog, facts), "DOWN", "DOWN", "DOWN", "SPACE", "ENTER")
    return run(s, "DOWN", "DOWN", "ENTER")


def test_the_image_step_keeps_a_token_typed_for_the_chat_model(
    ladder_catalog: Catalog, big_pool_facts: Facts
) -> None:
    catalog = _image_catalog(ladder_catalog, False)
    s = run(_at_image_step(catalog, big_pool_facts), *"hf_x", "ENTER")
    assert [r.id for r in view(s).rows] == ["recommended", "img-1"]
    done = press(s, "ENTER")
    assert view(done).screen is Screen.STORAGE
    assert secret(done) == "hf_x"


def test_an_image_model_with_an_account_does_not_ask_again_when_a_token_is_held(
    ladder_catalog: Catalog, big_pool_facts: Facts
) -> None:
    catalog = _image_catalog(ladder_catalog, True)
    s = run(_at_image_step(catalog, big_pool_facts), *"hf_x", "ENTER")
    done = run(s, "DOWN", "ENTER")
    assert view(done).screen is Screen.STORAGE
    assert view(done).editing is None
    assert secret(done) == "hf_x"


def test_an_image_model_with_an_account_asks_when_no_token_is_held(
    ladder_catalog: Catalog, big_pool_facts: Facts
) -> None:
    catalog = _image_catalog(ladder_catalog, True)
    s = run(custom(catalog, big_pool_facts), "DOWN", "DOWN", "DOWN", "SPACE", "ENTER", "ENTER")
    assert [r.id for r in view(s).rows] == ["recommended", "img-1"]
    asked = run(s, "DOWN", "ENTER")
    assert view(asked).editing == ""
    done = run(asked, *"hf_y", "ENTER")
    assert view(done).screen is Screen.STORAGE
    assert secret(done) == "hf_y"
    blank = run(asked, "ENTER")
    assert view(blank).screen is Screen.STORAGE
    assert secret(blank) is None


def test_an_information_only_models_screen_has_no_cursor_keys_in_its_hint(
    catalog: Catalog, usable_facts: Facts
) -> None:
    v = view(run(custom(catalog, usable_facts), "ENTER"))
    assert all(r.kind == "info" for r in v.rows)
    assert v.hint == "Enter go on, b back, ? explain, q quit"


def _two_image_catalog(ladder_catalog: Catalog) -> Catalog:
    rows = (
        ModelRow("image", "img-1", "Image one", "cpu", None, None, None, False),
        ModelRow("image", "img-2", "Image two", "cpu", None, None, None, False),
    )
    return replace(ladder_catalog, model_ladder=(*ladder_catalog.model_ladder, *rows))


def _image_step(catalog: Catalog, facts: Facts, given: dict[str, object] | None = None) -> State:
    s = run(custom(catalog, facts, given=given or _IMAGES_ON), "ENTER", "ENTER")
    assert view(s).screen is Screen.MODELS
    return s


def test_the_image_list_is_the_recommended_row_then_the_image_rows_in_file_order(
    ladder_catalog: Catalog, big_pool_facts: Facts
) -> None:
    catalog = _two_image_catalog(ladder_catalog)
    v = view(_image_step(catalog, big_pool_facts))
    assert [r.id for r in v.rows] == ["recommended", "img-1", "img-2"]
    option = next(o for o in catalog.questions["image_model"].options if o.value == "recommended")
    assert (v.rows[0].label, v.rows[0].detail) == (option.label, option.help)
    assert v.rows[1].label == "Image one"


@pytest.mark.parametrize(
    ("models", "row"),
    [
        ([], 0),
        (["img-2"], 2),
        (["img-1", "img-2"], 0),
        (["gone-9"], 0),
    ],
)
def test_the_image_cursor_starts_on_the_row_of_the_current_value(
    ladder_catalog: Catalog, big_pool_facts: Facts, models: list[str], row: int
) -> None:
    catalog = _two_image_catalog(ladder_catalog)
    given: dict[str, object] = {"gen": {"mode": "scaffold_and_download", "models": models}}
    assert view(_image_step(catalog, big_pool_facts, given=given)).cursor == row


def test_one_unchanged_enter_on_the_image_step_changes_nothing(
    ladder_catalog: Catalog, big_pool_facts: Facts
) -> None:
    catalog = _two_image_catalog(ladder_catalog)
    s = _image_step(catalog, big_pool_facts)
    assert "gen.models" not in chosen(press(s, "ENTER"))
    given: dict[str, object] = {"gen": {"mode": "scaffold_and_download", "models": ["img-1"]}}
    held = _image_step(catalog, big_pool_facts, given=given)
    assert view(held).cursor == 1
    assert "gen.models" not in chosen(press(held, "ENTER"))


def test_enter_on_the_image_step_writes_the_highlighted_value_when_it_differs(
    ladder_catalog: Catalog, big_pool_facts: Facts
) -> None:
    catalog = _two_image_catalog(ladder_catalog)
    picked = run(_image_step(catalog, big_pool_facts), "DOWN", "DOWN", "ENTER")
    assert chosen(picked)["gen.models"] == ["img-2"]
    given: dict[str, object] = {"gen": {"mode": "scaffold_and_download", "models": ["img-1"]}}
    back = run(_image_step(catalog, big_pool_facts, given=given), "UP", "ENTER")
    assert chosen(back)["gen.models"] == []


def test_a_fixed_recommended_set_marks_the_recommended_row(
    ladder_catalog: Catalog, big_pool_facts: Facts
) -> None:
    catalog = _two_image_catalog(ladder_catalog)
    over = [
        Override("gen.mode", "scaffold_and_download", "HAL0_IMAGE_MODELS"),
        Override("gen.models", [], "HAL0_IMAGE_MODELS"),
    ]
    s = run(custom(catalog, big_pool_facts, over=over), "ENTER", "ENTER")
    v = view(s)
    assert [r.id for r in v.rows] == ["recommended", "img-1", "img-2"]
    assert (v.rows[0].state, v.rows[0].note) == ("fixed", "set by HAL0_IMAGE_MODELS")
    assert v.rows[1].note == ""


# ------------------------------------------ what auto means when no model fits

_AUTO_DETAIL = {
    "too_small": "no larger chat model fits this box; the small assistant model will be used",
    "unknown": "chosen after hal0 has checked this box",
}


def test_the_auto_row_when_a_row_fits_names_the_pick_and_its_size(
    ladder_catalog: Catalog, usable_facts: Facts
) -> None:
    facts = replace(usable_facts, gpu_pool_gb=32.0)
    assert view(_models(ladder_catalog, facts)).rows[0].detail == "Small (2 GB)"


def test_the_auto_row_when_the_box_is_known_not_to_run_any_row(
    ladder_catalog: Catalog, usable_facts: Facts
) -> None:
    facts = replace(usable_facts, gpu_pool_gb=8.0, ram_gb=None)
    assert view(_models(ladder_catalog, facts)).rows[0].detail == _AUTO_DETAIL["too_small"]


def test_the_auto_row_with_no_usable_gpu_and_no_row_that_fits(
    ladder_catalog: Catalog, consent_facts: Facts
) -> None:
    facts = replace(consent_facts, ram_gb=None)
    over = [Override("hardware.allow_cpu_only", True, "HAL0_ALLOW_CPU_ONLY")]
    rows = view(_models(ladder_catalog, facts, over=over)).rows
    assert rows[0].detail == _AUTO_DETAIL["too_small"]


def test_the_auto_row_when_the_memory_is_not_known(
    ladder_catalog: Catalog, usable_facts: Facts
) -> None:
    facts = replace(usable_facts, gpu_pool_gb=None, ram_gb=None)
    assert view(_models(ladder_catalog, facts)).rows[0].detail == _AUTO_DETAIL["unknown"]


def test_a_brain_row_is_in_the_ladder_but_offered_on_no_screen(
    catalog_path: Path, tmp_path: Path, big_pool_facts: Facts
) -> None:
    rows = [
        {"role": "starter", "model": "s-1", "label": "S", "download_gb": 5, "min_gpu_pool_gb": 8},
        {"role": "brain", "model": "b-1", "label": "B", "download_gb": 20, "min_gpu_pool_gb": 32},
        {"role": "image", "model": "i-1", "label": "I", "download_gb": 9},
    ]
    models = tmp_path / "m.json"
    models.write_text(json.dumps({"version": 1, "model_ladder": rows}))
    catalog = load_catalog(catalog_path, models)
    assert any(r.model == "b-1" for r in catalog.model_ladder)
    s = custom(catalog, big_pool_facts, given=_IMAGES_ON)
    offered: set[str] = set()
    for _ in range(12):
        v = view(s)
        offered |= {r.id for r in v.rows}
        if v.screen is Screen.REVIEW:
            break
        s = press(s, "ENTER")
    assert {"s-1", "i-1"} <= offered
    assert "b-1" not in offered


def _document(catalog: Catalog, s: State) -> dict[str, object]:
    return effective(
        catalog, chosen=chosen(s), saved=None, given=None, over=[], hal0_version="v", now="t"
    )


def _untick_chat_and_assistant(catalog: Catalog, facts: Facts) -> State:
    s = run(custom(catalog, facts), "SPACE", "DOWN", "SPACE")
    rows = {r.id: r.value for r in view(s).rows}
    assert rows["openwebui"] is False and rows["hermes"] is False
    return press(s, "b")


@pytest.mark.parametrize(("moves", "plain"), [(["UP", "UP"], 0), (["UP"], 1)])
def test_changing_the_type_after_a_customize_detour_drops_the_customize_choices(
    catalog: Catalog, usable_facts: Facts, moves: list[str], plain: int
) -> None:
    s = _untick_chat_and_assistant(catalog, usable_facts)
    assert view(s).screen is Screen.TYPE
    s = run(s, *moves, "ENTER", "ENTER", "ENTER")
    direct = run(start(catalog, usable_facts), *(["DOWN"] * plain), "ENTER", "ENTER", "ENTER")
    assert outcome(s) is Outcome.DONE and outcome(direct) is Outcome.DONE
    assert chosen(s) == chosen(direct)
    assert _document(catalog, s) == _document(catalog, direct)


def test_picking_customize_again_keeps_the_customize_choices(
    catalog: Catalog, usable_facts: Facts
) -> None:
    s = press(_untick_chat_and_assistant(catalog, usable_facts), "ENTER")
    assert view(s).screen is Screen.JOBS
    rows = {r.id: r.value for r in view(s).rows}
    assert rows["openwebui"] is False and rows["hermes"] is False
    assert chosen(s)["apps.openwebui.enabled"] is False


def test_changing_the_type_keeps_the_folder_the_consent_and_the_copy_and_drops_the_token(
    ladder_catalog: Catalog, consent_facts: Facts, tmp_path: Path
) -> None:
    facts = replace(consent_facts, gpu_pool_gb=96.0)
    s = run(start(ladder_catalog, facts), "UP", "ENTER", "DOWN", "DOWN", "ENTER")
    s = run(s, "ENTER", "DOWN", "DOWN", "ENTER", *"hf_abc", "ENTER")  # jobs, big-1 and a token
    assert secret(s) == "hf_abc"
    folder = str(tmp_path / "models")
    s = run(open_field(s, "model_store"), *folder, "ENTER", "ENTER", "ENTER", "ENTER")
    copy = str(tmp_path / "copy.json")
    s = run(s, "s", *copy, "ENTER")
    assert view(s).screen is Screen.REVIEW
    s = run(s, *["b"] * 6)
    assert view(s).screen is Screen.TYPE
    s = run(s, "UP", "ENTER")  # Minimal
    assert chosen(s) == {
        "hardware.allow_cpu_only": True,
        "install.type": "minimal",
        "model_store.path": folder,
        "_save_copy_to": copy,
    }
    assert secret(s) is None
