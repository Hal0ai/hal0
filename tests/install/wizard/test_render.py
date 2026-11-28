from __future__ import annotations

import re
from dataclasses import replace

import pytest

from hal0.install.wizard.catalog import Catalog
from hal0.install.wizard.document import Override
from hal0.install.wizard.facts import Facts
from hal0.install.wizard.flow import State, press, start, view
from hal0.install.wizard.flow_model import View
from hal0.install.wizard.render import render, strip_ansi

ANSI = re.compile(r"\x1b\[[0-9;]*[A-Za-z]")


def run(state: State, *keys: str) -> State:
    for k in keys:
        state = press(state, k)
    return state


def to_jobs(catalog: Catalog, facts: Facts, **kw: object) -> State:
    return run(start(catalog, facts, **kw), "DOWN", "DOWN", "ENTER")  # type: ignore[arg-type]


@pytest.fixture
def type_view(catalog: Catalog, usable_facts: Facts) -> View:
    return view(start(catalog, usable_facts))


@pytest.fixture
def jobs_view(catalog: Catalog, usable_facts: Facts) -> View:
    return view(to_jobs(catalog, usable_facts))


@pytest.fixture
def fixed_jobs_view(catalog: Catalog, usable_facts: Facts) -> View:
    over = [Override("apps.hermes.enabled", False, "--no-hermes")]
    return view(to_jobs(catalog, usable_facts, over=over))


@pytest.fixture
def folder_error_view(catalog: Catalog, two_disk_facts: Facts) -> View:
    s = run(start(catalog, two_disk_facts), "ENTER", "DOWN", "DOWN", "ENTER")
    return view(run(s, *"~/models", "ENTER"))


@pytest.fixture
def all_views(
    catalog: Catalog,
    ladder_catalog: Catalog,
    usable_facts: Facts,
    two_disk_facts: Facts,
    consent_facts: Facts,
    stop_facts: Facts,
    npu_facts: Facts,
    folder_error_view: View,
    fixed_jobs_view: View,
) -> list[View]:
    views = [view(start(catalog, consent_facts)), view(start(catalog, stop_facts))]
    pool = replace(usable_facts, gpu_pool_gb=32.0)
    for cat, facts in ((catalog, usable_facts), (ladder_catalog, pool), (catalog, npu_facts)):
        s = to_jobs(cat, facts)
        for _ in range(6):
            views.append(view(s))
            views.append(view(press(s, "?")))
            s = press(s, "ENTER")
    quick = run(start(catalog, two_disk_facts), "ENTER")
    views += [view(start(catalog, usable_facts)), view(quick), view(press(quick, "ENTER"))]
    views += [view(run(quick, "ENTER", "s")), folder_error_view, fixed_jobs_view]
    return views


@pytest.mark.parametrize("width", [20, 39, 80, 200])
@pytest.mark.parametrize("plain", [False, True])
def test_no_drawn_row_is_wider_than_the_terminal(
    all_views: list[View], width: int, plain: bool
) -> None:
    for v in all_views:
        for line in render(v, width=width, plain=plain, color=not plain):
            assert len(strip_ansi(line)) <= width


def test_cursor_form_marks_the_highlighted_row_and_ticks(jobs_view: View) -> None:
    lines = [strip_ansi(x) for x in render(jobs_view, width=80, plain=False, color=True)]
    assert any(x.startswith(" > [x] Chat in your browser") for x in lines)
    assert any(x.startswith("   [-] Use the NPU") and "no NPU was found" in x for x in lines)


def test_fixed_row_shows_its_source(fixed_jobs_view: View) -> None:
    lines = render(fixed_jobs_view, width=80, plain=True, color=False)
    assert any("set by --no-hermes" in x for x in lines)


def test_plain_form_numbers_rows_and_has_no_escape_codes(jobs_view: View) -> None:
    lines = render(jobs_view, width=80, plain=True, color=False)
    assert any(x.startswith("  1) [x] Chat in your browser") for x in lines)
    assert not any(ANSI.search(x) for x in lines)


def test_plain_form_has_no_escape_codes_even_when_colour_is_asked_for(jobs_view: View) -> None:
    assert not any(ANSI.search(x) for x in render(jobs_view, width=80, plain=True, color=True))


def test_color_off_has_no_escape_codes(jobs_view: View) -> None:
    assert not any(ANSI.search(x) for x in render(jobs_view, width=80, plain=False, color=False))


def test_color_on_uses_the_accent_and_dim(jobs_view: View) -> None:
    out = "\n".join(render(jobs_view, width=80, plain=False, color=True))
    assert "\x1b[38;5;214m" in out
    assert "\x1b[2m" in out
    assert "\x1b[0m" in out


def test_title_line_carries_the_position_at_the_right(type_view: View) -> None:
    first = strip_ansi(render(type_view, width=60, plain=False, color=False)[0])
    assert first.startswith(" hal0 setup")
    assert first.rstrip().endswith("1 of 3")


def test_error_and_text_field_are_drawn(folder_error_view: View) -> None:
    out = "\n".join(render(folder_error_view, width=80, plain=False, color=False))
    assert "must start with /" in out
    assert "models_" in out  # cursor mark after the typed text


def test_hint_line_is_last(type_view: View) -> None:
    assert "Enter" in render(type_view, width=80, plain=False, color=False)[-1]


def test_info_rows_are_unnumbered_in_the_plain_form(catalog: Catalog, usable_facts: Facts) -> None:
    review = view(run(start(catalog, usable_facts), "ENTER", "ENTER"))
    lines = render(review, width=80, plain=True, color=False)
    assert not any(re.match(r"\s+\d+\) ", x) for x in lines)
    assert any(x.startswith("     Installing") for x in lines)


def test_plain_form_counts_only_rows_that_are_not_info(type_view: View) -> None:
    lines = render(type_view, width=80, plain=True, color=False)
    numbered = [x for x in lines if re.match(r"\s+\d+\) ", x)]
    assert [x.split(")")[0].strip() for x in numbered] == ["1", "2", "3"]


def test_a_long_help_text_is_fully_present_across_the_lines(
    catalog: Catalog, usable_facts: Facts
) -> None:
    s = run(to_jobs(catalog, usable_facts), "ENTER", "ENTER", "ENTER", "ENTER", "?")
    v = view(s)
    help_text = catalog.questions["terminal_tool"].help
    assert help_text in " ".join(v.intro)
    lines = render(v, width=60, plain=False, color=False)
    assert all(len(x) <= 60 for x in lines)
    joined = " ".join(x.strip() for x in lines)
    assert " ".join(help_text.split()) in joined


def test_a_note_that_does_not_fit_moves_under_the_label(jobs_view: View) -> None:
    lines = [strip_ansi(x) for x in render(jobs_view, width=30, plain=False, color=False)]
    assert all(len(x) <= 29 for x in lines)
    index = next(i for i, x in enumerate(lines) if "Use the NPU" in x)
    assert "no NPU was found" not in lines[index]
    assert "no NPU was found" in " ".join(x.strip() for x in lines[index:])
