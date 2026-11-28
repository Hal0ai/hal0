from __future__ import annotations

from dataclasses import replace
from pathlib import Path

import pytest

from hal0.install.wizard.catalog import Catalog
from hal0.install.wizard.document import Override, effective
from hal0.install.wizard.facts import Facts, load_facts
from hal0.install.wizard.flow import (
    Outcome,
    Row,
    Screen,
    State,
    chosen,
    outcome,
    press,
    sequence,
    start,
    validate_folder,
    view,
)


def run(state: State, *keys: str) -> State:
    for k in keys:
        state = press(state, k)
    return state


def test_recommended_is_three_screens(catalog: Catalog, usable_facts: Facts) -> None:
    assert sequence("recommended", usable_facts) == (Screen.TYPE, Screen.FOLDER, Screen.REVIEW)
    assert sequence("minimal", usable_facts) == (Screen.TYPE, Screen.FOLDER, Screen.REVIEW)


def test_enter_on_every_screen_equals_a_headless_run(catalog: Catalog, usable_facts: Facts) -> None:
    s = run(start(catalog, usable_facts), "ENTER", "ENTER", "ENTER")
    assert outcome(s) is Outcome.DONE
    a = effective(
        catalog, chosen=chosen(s), saved=None, given=None, over=[], hal0_version="v", now="t"
    )
    b = effective(catalog, chosen={}, saved=None, given=None, over=[], hal0_version="v", now="t")
    assert a == b
    assert chosen(s) == {"install.type": "recommended", "model_store.path": "/var/lib/hal0/models"}


def test_positions_read_as_the_design_shows(catalog: Catalog, usable_facts: Facts) -> None:
    s = start(catalog, usable_facts)
    assert view(s).position == "1 of 3"
    assert view(run(s, "ENTER")).position == "2 of 3"


def test_type_screen_lists_what_was_detected_and_says_not_detected_otherwise(
    catalog: Catalog,
) -> None:
    v = view(start(catalog, load_facts(None, disks=[], ram_gb=None)))
    assert any("not detected" in line for line in v.intro)


def test_back_and_quit(catalog: Catalog, usable_facts: Facts) -> None:
    s = run(start(catalog, usable_facts), "ENTER", "b")
    assert view(s).screen is Screen.TYPE
    assert outcome(press(s, "q")) is Outcome.QUIT
    assert outcome(press(s, "EOF")) is Outcome.QUIT
    assert view(press(start(catalog, usable_facts), "b")).screen is Screen.TYPE


def test_folder_lists_disks_largest_free_first_with_the_default_preselected(
    catalog: Catalog, two_disk_facts: Facts
) -> None:
    v = view(run(start(catalog, two_disk_facts), "ENTER"))
    assert v.rows[0].value == "/var/lib/hal0/models"
    assert v.rows[1].value == "/data/hal0/models"
    assert v.rows[-1].id == "other"
    assert v.cursor == 0
    assert v.rows[0].detail == "100 GB free"
    assert v.rows[1].detail == "1500 GB free"


@pytest.mark.parametrize(
    "typed, error",
    [
        ("models", "must start with /"),
        ("~/models", "must start with /"),
        ("/data/\x07models", "control character"),
        ("", "is required"),
        ("/data/50%models", "must not contain %"),
        ("/data/%2e", "must not contain %"),
    ],
)
def test_bad_folder_is_refused_on_the_same_screen(typed: str, error: str) -> None:
    assert error in str(validate_folder(typed)[1])


def test_bad_typed_folder_keeps_the_field_open(catalog: Catalog, two_disk_facts: Facts) -> None:
    s = run(start(catalog, two_disk_facts), "ENTER", "DOWN", "DOWN", "ENTER")
    assert view(s).editing == ""
    s = run(s, "b", "q", "ENTER")
    assert outcome(s) is Outcome.RUNNING
    v = view(s)
    assert v.screen is Screen.FOLDER
    assert v.editing == "bq"
    assert v.error is not None
    assert "must start with /" in v.error


def test_typed_folder_is_recorded_and_goes_on(catalog: Catalog, two_disk_facts: Facts) -> None:
    s = run(start(catalog, two_disk_facts), "ENTER", "DOWN", "DOWN", "ENTER")
    s = run(s, *"/srv/x", "ENTER")
    assert view(s).screen is Screen.REVIEW
    assert chosen(s)["model_store.path"] == "/srv/x"


def test_folder_under_a_file_is_refused(tmp_path: Path) -> None:
    f = tmp_path / "plain"
    f.write_text("x")
    assert "is not a folder" in str(validate_folder(str(f / "models"))[1])


def test_surrounding_spaces_are_stripped() -> None:
    assert validate_folder("  /data/models  ") == ("/data/models", None)


def test_empty_enter_closes_a_field_with_nothing_changed(
    catalog: Catalog, two_disk_facts: Facts
) -> None:
    s = run(start(catalog, two_disk_facts), "ENTER", "DOWN", "DOWN", "ENTER")
    assert view(s).editing == ""
    s = press(s, "ENTER")
    v = view(s)
    assert v.editing is None
    assert v.screen is Screen.FOLDER
    assert "model_store.path" not in chosen(s)


def test_cpu_only_consent_is_the_opening_screen(catalog: Catalog, consent_facts: Facts) -> None:
    s = start(catalog, consent_facts)
    assert view(s).screen is Screen.GPU
    assert view(s).cursor == 1
    assert outcome(press(s, "ENTER")) is Outcome.STOPPED
    yes = run(s, "UP", "ENTER")
    assert view(yes).screen is Screen.TYPE
    assert chosen(yes)["hardware.allow_cpu_only"] is True


def test_gpu_stop_cannot_be_consented_away(catalog: Catalog, stop_facts: Facts) -> None:
    s = start(catalog, stop_facts)
    assert view(s).screen is Screen.GPU
    assert all(r.kind == "info" for r in view(s).rows)
    assert outcome(press(s, "ENTER")) is Outcome.STOPPED


def test_cpu_only_fixed_by_variable_skips_the_screen(
    catalog: Catalog, consent_facts: Facts
) -> None:
    over = [Override("hardware.allow_cpu_only", True, "HAL0_ALLOW_CPU_ONLY")]
    s = start(catalog, consent_facts, over=over)
    assert view(s).screen is Screen.TYPE
    assert view(s).position == "1 of 3"


def test_a_fixed_folder_row_says_who_set_it(catalog: Catalog, usable_facts: Facts) -> None:
    over = [Override("model_store.path", "/mnt/m", "--models-dir")]
    s = run(start(catalog, usable_facts, over=over), "ENTER")
    row = view(s).rows[0]
    assert row.value == "/mnt/m"
    assert row.state == "fixed"
    assert row.note == "set by --models-dir"
    s = press(s, "ENTER")
    assert view(s).screen is Screen.REVIEW
    assert "model_store.path" not in chosen(s)


def test_a_fixed_type_row_is_kept_and_enter_goes_on(catalog: Catalog, usable_facts: Facts) -> None:
    over = [Override("install.type", "minimal", "--bare")]
    s = start(catalog, usable_facts, over=over)
    assert view(s).rows[view(s).cursor].value == "minimal"
    assert view(press(s, "ENTER")).screen is Screen.FOLDER
    assert "install.type" not in chosen(press(s, "ENTER"))


def test_review_states_admin_key_terminal_tool_and_address(
    catalog: Catalog, usable_facts: Facts
) -> None:
    rows = {r.id: r for r in view(run(start(catalog, usable_facts), "ENTER", "ENTER")).rows}
    assert rows["admin_key"].detail == "created; shown once when the install ends"
    assert rows["terminal_tool"].detail == "off"
    assert rows["reachable"].detail == "http://hal0.local:8080 from this network"
    assert rows["downloads"].detail == "nothing yet; models are added after install"
    assert "total" not in rows


@pytest.mark.parametrize("bind", ["127.0.0.1", "0.0.0.0"])
def test_the_review_always_says_the_box_is_reachable_from_the_network(
    catalog: Catalog, usable_facts: Facts, bind: str
) -> None:
    given: dict[str, object] = {"network": {"bind_host": bind}}
    s = run(start(catalog, usable_facts, given=given), "ENTER", "ENTER")
    rows = {r.id: r for r in view(s).rows}
    assert rows["reachable"].detail == "http://hal0.local:8080 from this network"


def test_review_on_a_box_without_podman_does_not_promise_chat(
    catalog: Catalog, usable_facts: Facts
) -> None:
    facts = Facts(**{**usable_facts.__dict__, "podman_runs": False})
    rows = {r.id: r for r in view(run(start(catalog, facts), "ENTER", "ENTER")).rows}
    assert "Chat in your browser" not in rows["install"].detail


def test_question_mark_shows_the_help_of_the_highlighted_line(
    catalog: Catalog, usable_facts: Facts
) -> None:
    v = view(press(start(catalog, usable_facts), "?"))
    assert catalog.questions["setup_type"].help in " ".join(v.intro)
    assert catalog.questions["setup_type"].help not in " ".join(
        view(run(start(catalog, usable_facts), "?", "?")).intro
    )


def test_low_space_note_on_a_disk_smaller_than_the_download(
    ladder_catalog: Catalog, two_disk_facts: Facts
) -> None:
    facts = Facts(**{**two_disk_facts.__dict__, "gpu_pool_gb": 32.0})
    from hal0.install.wizard.facts import Disk

    facts = Facts(
        **{**facts.__dict__, "disks": (Disk("/", 10.0, 50.0), Disk("/data", 1500.0, 2000.0))}
    )
    given: dict[str, object] = {"models": {"starter": "big-1"}}
    v = view(run(start(ladder_catalog, facts, given=given), "ENTER"))
    assert v.intro[-1] == "This setup will download about 17 GB."
    assert v.rows[0].note == "less than the 17 GB this setup downloads"
    assert v.rows[1].note == ""


def test_a_ladder_with_sizes_gives_the_download_line_total_and_minutes(
    ladder_catalog: Catalog, usable_facts: Facts
) -> None:
    facts = Facts(**{**usable_facts.__dict__, "gpu_pool_gb": 32.0})
    rows = {r.id: r for r in view(run(start(ladder_catalog, facts), "ENTER", "ENTER")).rows}
    assert rows["downloads"].detail == "Small (2 GB)"
    assert rows["total"].detail == "2 GB, about 3 min on a 100 Mbit/s line"
    assert rows["model"].detail == "Small, picked for this box"
    given: dict[str, object] = {"models": {"starter": "big-1"}}
    chosen_rows = view(run(start(ladder_catalog, facts, given=given), "ENTER", "ENTER")).rows
    rows = {r.id: r for r in chosen_rows}
    assert rows["total"].detail == "17 GB, about 23 min on a 100 Mbit/s line"
    assert rows["model"].detail == "Big"


def test_auto_takes_the_first_row_that_fits(ladder_catalog: Catalog, usable_facts: Facts) -> None:
    facts = Facts(**{**usable_facts.__dict__, "gpu_pool_gb": None, "ram_gb": 16.0})
    rows = {r.id: r for r in view(run(start(ladder_catalog, facts), "ENTER", "ENTER")).rows}
    assert rows["downloads"].detail == "Small (2 GB)"


_TOO_SMALL = "no larger chat model fits this box; the small assistant model will be used"
_FACTS_FOR = {
    "fits": {"gpu_pool_gb": 32.0},
    "too_small": {"gpu_pool_gb": 8.0, "ram_gb": None},
    "unknown": {"gpu_pool_gb": None, "ram_gb": None},
}
_REVIEW = {
    "fits": ("Small (2 GB)", "Small, picked for this box"),
    "too_small": ("no chat model to download", _TOO_SMALL),
    "unknown": (
        "a chat model; its size is shown before it downloads",
        "picked for this box once hal0 has checked it",
    ),
}


@pytest.mark.parametrize("case", ["fits", "too_small", "unknown"])
def test_the_review_says_what_auto_means_for_each_way_a_pick_can_turn_out(
    ladder_catalog: Catalog, usable_facts: Facts, case: str
) -> None:
    facts = replace(usable_facts, **_FACTS_FOR[case])
    rows = {r.id: r for r in _review(ladder_catalog, facts)}
    downloads, model = _REVIEW[case]
    assert rows["downloads"].detail == downloads
    assert rows["model"].detail == model
    assert ("total" in rows) is (case == "fits")


def test_no_usable_graphics_card_and_no_row_that_fits_is_too_small(
    ladder_catalog: Catalog, consent_facts: Facts
) -> None:
    facts = replace(consent_facts, ram_gb=None)
    over = [Override("hardware.allow_cpu_only", True, "HAL0_ALLOW_CPU_ONLY")]
    rows = {
        r.id: r
        for r in view(run(start(ladder_catalog, facts, over=over), "ENTER", "ENTER", "ENTER")).rows
    }
    assert rows["model"].detail == _TOO_SMALL
    assert rows["downloads"].detail == "no chat model to download"


def test_too_small_leaves_the_other_downloads_and_their_total_alone(
    ladder_catalog: Catalog, usable_facts: Facts
) -> None:
    sized = tuple(
        replace(c, download_gb=3.0) if c.id == "openwebui" else c for c in ladder_catalog.components
    )
    catalog = replace(ladder_catalog, components=sized)
    facts = replace(usable_facts, **_FACTS_FOR["too_small"])
    rows = {r.id: r for r in _review(catalog, facts)}
    assert rows["downloads"].detail == "Chat in your browser (Open WebUI) (3 GB)"
    assert rows["total"].detail.startswith("3 GB")
    assert rows["model"].detail == _TOO_SMALL


def test_save_copy_path_is_exposed_but_never_reaches_the_document(
    catalog: Catalog, usable_facts: Facts, tmp_path: Path
) -> None:
    copy = str(tmp_path / "a.json")
    s = run(start(catalog, usable_facts), "ENTER", "ENTER", "s", *copy, "ENTER")
    assert view(s).screen is Screen.REVIEW
    assert chosen(s)["_save_copy_to"] == copy
    s = run(s, "s", *"rel", "ENTER")
    assert view(s).error is not None


def _review(catalog: Catalog, facts: Facts) -> tuple[Row, ...]:
    return view(run(start(catalog, facts), "ENTER", "ENTER")).rows


def test_a_job_the_box_cannot_run_is_left_out_and_warned_about(
    catalog: Catalog, no_python_facts: Facts
) -> None:
    rows = _review(catalog, no_python_facts)
    install = next(r for r in rows if r.id == "install")
    assert "Memory for your assistants" not in install.detail
    warnings = [r.detail for r in rows if r.id == "warning"]
    assert warnings == [
        "Memory for your assistants (Hindsight): needs Python 3.11 to 3.14, which was not found"
    ]


def test_a_box_with_no_npu_says_nothing_about_the_npu(
    catalog: Catalog, usable_facts: Facts
) -> None:
    rows = _review(catalog, usable_facts)
    install = next(r for r in rows if r.id == "install")
    assert "Use the NPU" not in install.detail
    assert all(r.id != "npu" for r in rows)
    assert all("NPU" not in r.detail for r in rows if r.id == "warning")


def test_a_box_with_an_npu_lists_it_once(catalog: Catalog, usable_facts: Facts) -> None:
    facts = Facts(**{**usable_facts.__dict__, "npu_device": True, "npu_flm_build": True})
    rows = _review(catalog, facts)
    install = next(r for r in rows if r.id == "install")
    assert "Use the NPU" not in install.detail
    assert [r.id for r in rows].count("npu") == 1


def test_the_review_says_where_a_copy_will_be_saved(
    catalog: Catalog, usable_facts: Facts, tmp_path: Path
) -> None:
    copy = str(tmp_path / "a.json")
    s = run(start(catalog, usable_facts), "ENTER", "ENTER")
    assert view(s).intro == ()
    s = run(s, "s", *copy, "ENTER")
    assert view(s).intro == (f"Saving a copy of these answers to {copy}.",)
    assert view(press(s, "b")).screen is Screen.FOLDER


def test_a_space_typed_into_a_folder_field_is_kept(catalog: Catalog, two_disk_facts: Facts) -> None:
    s = run(start(catalog, two_disk_facts), "ENTER", "DOWN", "DOWN", "ENTER")
    s = run(s, *"/mnt/my", "SPACE", *"models", "ENTER")
    assert view(s).screen is Screen.REVIEW
    assert chosen(s)["model_store.path"] == "/mnt/my models"


def test_up_and_down_stay_on_a_fixed_value(catalog: Catalog, usable_facts: Facts) -> None:
    over = [Override("install.type", "minimal", "--bare")]
    s = start(catalog, usable_facts, over=over)
    assert view(run(s, "DOWN", "DOWN")).cursor == view(s).cursor
    assert view(run(s, "UP")).cursor == view(s).cursor


def test_hints_are_per_screen(catalog: Catalog, usable_facts: Facts, stop_facts: Facts) -> None:
    s = start(catalog, usable_facts)
    assert view(s).hint == "Up/Down move, Enter choose, ? explain, q quit"
    assert view(press(s, "ENTER")).hint == "Up/Down move, Enter choose, b back, ? explain, q quit"
    review = run(s, "ENTER", "ENTER")
    assert view(review).hint == "Enter start the install, b back, s save a copy, q quit"
    assert view(press(review, "s")).hint == "Enter confirm, Enter on an empty line cancels"
    assert view(start(catalog, stop_facts)).hint == "Enter or q to leave"


def test_help_on_the_gpu_stop_screen_offers_nothing(catalog: Catalog, stop_facts: Facts) -> None:
    s = start(catalog, stop_facts)
    assert view(press(s, "?")).intro == view(s).intro


def test_the_preset_folder_stays_in_the_list_after_picking_another_disk(
    catalog: Catalog, two_disk_facts: Facts
) -> None:
    s = run(start(catalog, two_disk_facts), "ENTER", "DOWN", "ENTER", "b")
    values = [r.value for r in view(s).rows]
    assert values[0] == "/data/hal0/models"
    assert "/var/lib/hal0/models" in values


def _model_line(catalog: Catalog, facts: Facts, starter: str | None) -> str:
    given: dict[str, object] = {} if starter is None else {"models": {"starter": starter}}
    rows = view(run(start(catalog, facts, given=given), "ENTER", "ENTER")).rows
    return next(r for r in rows if r.id == "model").detail


def test_review_model_line_for_none(ladder_catalog: Catalog, usable_facts: Facts) -> None:
    assert _model_line(ladder_catalog, usable_facts, "none") == "none"


def test_review_model_line_for_auto_with_an_empty_ladder(
    catalog: Catalog, usable_facts: Facts
) -> None:
    assert _model_line(catalog, usable_facts, "auto") == "added after install"


def test_review_model_line_for_auto_with_a_row_that_fits(
    ladder_catalog: Catalog, usable_facts: Facts
) -> None:
    facts = Facts(**{**usable_facts.__dict__, "gpu_pool_gb": 32.0})
    assert _model_line(ladder_catalog, facts, "auto") == "Small, picked for this box"


def test_review_model_line_for_auto_with_no_row_that_fits(
    ladder_catalog: Catalog, usable_facts: Facts
) -> None:
    facts = Facts(**{**usable_facts.__dict__, "gpu_pool_gb": None, "ram_gb": None})
    assert _model_line(ladder_catalog, facts, "auto") == (
        "picked for this box once hal0 has checked it"
    )


def test_review_model_line_for_a_model_id(ladder_catalog: Catalog, usable_facts: Facts) -> None:
    assert _model_line(ladder_catalog, usable_facts, "big-1") == "Big"


@pytest.mark.parametrize(
    ("present", "runs", "word"),
    [
        (False, None, "will be installed"),
        (True, True, "installed"),
        (True, False, "installed but not working"),
        (None, None, "not detected"),
    ],
)
def test_the_box_line_says_what_podman_will_do(
    catalog: Catalog, usable_facts: Facts, present: bool | None, runs: bool | None, word: str
) -> None:
    facts = replace(usable_facts, podman_present=present, podman_runs=runs)
    lines = view(start(catalog, facts)).intro
    assert f"  Podman: {word}" in lines
    assert not any("running" in line for line in lines)


def test_a_box_without_podman_still_offers_web_chat(catalog: Catalog, usable_facts: Facts) -> None:
    facts = replace(usable_facts, podman_present=False, podman_runs=None)
    jobs = view(run(start(catalog, facts), "DOWN", "DOWN", "ENTER"))
    assert jobs.screen is Screen.JOBS
    chat = next(r for r in jobs.rows if r.id == "openwebui")
    assert chat.state == "open" and chat.value is True
    review = {r.id: r for r in view(run(start(catalog, facts), "ENTER", "ENTER")).rows}
    assert "Chat in your browser" in review["install"].detail


@pytest.mark.parametrize(
    ("value", "text"),
    [
        (135.04, "135"),
        (46.9, "47"),
        (10.0, "10"),
        (9.96, "10"),
        (2.0, "2"),
        (2.5, "2.5"),
        (2.04, "2"),
        (0.5, "0.5"),
    ],
)
def test_sizes_read_plainly(value: float, text: str) -> None:
    from hal0.install.wizard.flow_model import fmt_gb

    assert fmt_gb(value) == text


def _save(catalog: Catalog, facts: Facts, text: str) -> State:
    return run(start(catalog, facts), "ENTER", "ENTER", "s", *text, "ENTER")


def test_a_copy_can_be_saved_over_an_earlier_copy(
    catalog: Catalog, usable_facts: Facts, tmp_path: Path
) -> None:
    earlier = tmp_path / "copy.json"
    earlier.write_text("{}")
    s = _save(catalog, usable_facts, str(earlier))
    assert view(s).error is None
    assert view(s).editing is None
    assert chosen(s)["_save_copy_to"] == str(earlier)


def test_a_copy_cannot_be_saved_over_a_folder(
    catalog: Catalog, usable_facts: Facts, tmp_path: Path
) -> None:
    s = _save(catalog, usable_facts, str(tmp_path))
    assert view(s).error == f"{tmp_path} is a folder"
    assert view(s).editing is not None


def test_a_copy_cannot_be_saved_through_a_symlink(
    catalog: Catalog, usable_facts: Facts, tmp_path: Path
) -> None:
    target = tmp_path / "real.json"
    target.write_text("{}")
    link = tmp_path / "link.json"
    link.symlink_to(target)
    s = _save(catalog, usable_facts, str(link))
    assert view(s).error == f"{link} is a symlink"
    dangling = tmp_path / "dangling.json"
    dangling.symlink_to(tmp_path / "nowhere")
    assert view(_save(catalog, usable_facts, str(dangling))).error == f"{dangling} is a symlink"


def test_a_copy_needs_an_existing_folder_to_go_in(
    catalog: Catalog, usable_facts: Facts, tmp_path: Path
) -> None:
    gone = tmp_path / "no" / "such" / "copy.json"
    message = "the folder for this file does not exist"
    assert view(_save(catalog, usable_facts, str(gone))).error == message
    plain = tmp_path / "plain"
    plain.write_text("x")
    assert view(_save(catalog, usable_facts, str(plain / "copy.json"))).error == message


def test_a_copy_path_may_contain_a_percent_sign(
    catalog: Catalog, usable_facts: Facts, tmp_path: Path
) -> None:
    copy = str(tmp_path / "50%.json")
    assert chosen(_save(catalog, usable_facts, copy))["_save_copy_to"] == copy
