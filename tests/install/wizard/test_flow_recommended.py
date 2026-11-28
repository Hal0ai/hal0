from __future__ import annotations

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
    assert press(s, "ENTER") is not s
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
    assert rows["model"].detail == "Small (picked for this box)"
    given: dict[str, object] = {"models": {"starter": "big-1"}}
    chosen_rows = view(run(start(ladder_catalog, facts, given=given), "ENTER", "ENTER")).rows
    rows = {r.id: r for r in chosen_rows}
    assert rows["total"].detail == "17 GB, about 23 min on a 100 Mbit/s line"
    assert rows["model"].detail == "Big"


def test_auto_takes_the_first_row_that_fits(ladder_catalog: Catalog, usable_facts: Facts) -> None:
    facts = Facts(**{**usable_facts.__dict__, "gpu_pool_gb": None, "ram_gb": 16.0})
    rows = {r.id: r for r in view(run(start(ladder_catalog, facts), "ENTER", "ENTER")).rows}
    assert rows["downloads"].detail == "Small (2 GB)"


def test_auto_with_nothing_that_fits_says_so(ladder_catalog: Catalog, usable_facts: Facts) -> None:
    facts = Facts(**{**usable_facts.__dict__, "gpu_pool_gb": None, "ram_gb": None})
    rows = {r.id: r for r in view(run(start(ladder_catalog, facts), "ENTER", "ENTER")).rows}
    assert rows["downloads"].detail == "a chat model sized for this box"
    assert "total" not in rows


def test_save_copy_path_is_exposed_but_never_reaches_the_document(
    catalog: Catalog, usable_facts: Facts
) -> None:
    s = run(start(catalog, usable_facts), "ENTER", "ENTER", "s", *"/tmp/a.json", "ENTER")
    assert view(s).screen is Screen.REVIEW
    assert chosen(s)["_save_copy_to"] == "/tmp/a.json"
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


def test_the_review_says_where_a_copy_will_be_saved(catalog: Catalog, usable_facts: Facts) -> None:
    s = run(start(catalog, usable_facts), "ENTER", "ENTER")
    assert view(s).intro == ()
    s = run(s, "s", *"/tmp/a.json", "ENTER")
    assert view(s).intro == ("Saving a copy of these answers to /tmp/a.json.",)
    assert view(press(s, "b")).screen is Screen.FOLDER
