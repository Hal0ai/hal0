from __future__ import annotations

import json
from dataclasses import replace

import pytest

from hal0.install.wizard.catalog import Catalog, ModelRow
from hal0.install.wizard.document import Override
from hal0.install.wizard.facts import Facts, load_facts
from hal0.install.wizard.flow import (
    Screen,
    State,
    chosen,
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
    # pick big-1, blank token, storage, network, assistant
    review = view(run(s, "DOWN", "ENTER", "ENTER", "ENTER", "ENTER", "ENTER"))
    assert any(r.id == "warning" and "big-1" in r.detail for r in review.rows)


def test_token_is_asked_only_for_a_model_that_needs_an_account(
    ladder_catalog: Catalog, big_pool_facts: Facts
) -> None:
    small = run(custom(ladder_catalog, big_pool_facts), "ENTER", "ENTER")
    assert view(small).screen is Screen.STORAGE
    big = run(custom(ladder_catalog, big_pool_facts), "ENTER", "DOWN", "ENTER")
    assert view(big).editing is not None
    assert "HuggingFace" in view(big).title
    typed = run(big, *"hf_abc", "ENTER")
    assert secret(typed) == "hf_abc"
    assert "hf_abc" not in json.dumps(chosen(typed))
    assert view(typed).screen is Screen.STORAGE


def test_the_token_is_never_shown_while_typing(
    ladder_catalog: Catalog, big_pool_facts: Facts
) -> None:
    s = run(custom(ladder_catalog, big_pool_facts), "ENTER", "DOWN", "ENTER", *"hf_abc")
    assert view(s).editing == ""
    assert "hf_abc" not in repr(s)
    assert "hf_abc" not in json.dumps(chosen(s))


def test_a_blank_token_skips_and_goes_on(ladder_catalog: Catalog, big_pool_facts: Facts) -> None:
    s = run(custom(ladder_catalog, big_pool_facts), "ENTER", "DOWN", "ENTER", "ENTER")
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
    assert [r.id for r in view(s).rows] == ["img-1"]
    done = press(s, "ENTER")
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


def test_space_cycles_the_listen_address_and_opens_no_field(
    catalog: Catalog, usable_facts: Facts
) -> None:
    s = open_field(run(custom(catalog, usable_facts), "ENTER", "ENTER", "ENTER"), "bind_host")
    assert view(s).editing is None
    assert chosen(s)["network.bind_host"] == "127.0.0.1"
    row = next(r for r in view(s).rows if r.id == "bind_host")
    assert row.detail == "This machine only"
    assert chosen(press(s, "SPACE"))["network.bind_host"] == "0.0.0.0"


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
