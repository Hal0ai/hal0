"""hal0.comfyui.progress: process-local step progress for hal0-submitted renders."""

from __future__ import annotations

from collections.abc import Iterator

import pytest

from hal0.comfyui import progress


class _Clock:
    """A settable stand-in for the tracker's monotonic clock."""

    def __init__(self) -> None:
        self.now = 1000.0

    def __call__(self) -> float:
        return self.now


@pytest.fixture(autouse=True)
def clock(monkeypatch: pytest.MonkeyPatch) -> Iterator[_Clock]:
    progress._reset()
    fake = _Clock()
    monkeypatch.setattr(progress, "_now", fake)
    yield fake
    progress._reset()


def test_nothing_is_rendering_by_default() -> None:
    assert progress.current() is None


def test_progress_tracks_latest_render(clock: _Clock) -> None:
    progress.start("p-old", "sdxl-lightning")
    clock.now += 5
    progress.start("p-new", "sd-1.5-pruned-emaonly")
    clock.now += 2.26

    progress.update("p-new", step=3, total=20, node="12")
    progress.update("p-old", step=7, total=8, node="9")

    # The newest live render is reported, with its own step, never the older one's.
    assert progress.current() == {
        "model": "sd-1.5-pruned-emaonly",
        "step": 3,
        "total": 20,
        "node": "12",
        "elapsed_s": 2.3,
        "prompt_id": "p-new",
    }

    # A later frame replaces the earlier one.
    progress.update("p-new", step=4, total=20, node="12")
    current = progress.current()
    assert current is not None
    assert (current["step"], current["total"]) == (4, 20)

    # When the newest render ends, the one still running is reported.
    progress.finish("p-new")
    older = progress.current()
    assert older is not None
    assert older["prompt_id"] == "p-old"
    assert (older["step"], older["total"], older["node"]) == (7, 8, "9")
    assert older["elapsed_s"] == 7.3


def test_a_render_with_no_frame_yet_has_no_step(clock: _Clock) -> None:
    """Between submit and the first progress frame the render is live but has no step to show."""
    progress.start("p1", "sdxl-lightning")

    assert progress.current() == {
        "model": "sdxl-lightning",
        "step": None,
        "total": None,
        "node": None,
        "elapsed_s": 0.0,
        "prompt_id": "p1",
    }


def test_elapsed_comes_from_the_start_and_is_rounded_to_one_decimal(clock: _Clock) -> None:
    progress.start("p1", "m")
    clock.now += 12.349
    first = progress.current()
    assert first is not None
    assert first["elapsed_s"] == 12.3

    # Updating a render does not restart its clock.
    progress.update("p1", step=1, total=4, node=None)
    clock.now += 0.1
    again = progress.current()
    assert again is not None
    assert again["elapsed_s"] == 12.4


def test_progress_clears_after_render() -> None:
    progress.start("p1", "m")
    progress.update("p1", step=1, total=4, node="3")
    assert progress.current() is not None

    progress.finish("p1")

    assert progress.current() is None


def test_finishing_an_unknown_render_is_a_no_op() -> None:
    progress.start("p1", "m")

    progress.finish("never-started")
    progress.finish("never-started")

    current = progress.current()
    assert current is not None
    assert current["prompt_id"] == "p1"


def test_an_update_for_an_unknown_render_is_dropped() -> None:
    """A late frame for a render that already finished must not resurrect it."""
    progress.start("p1", "m")
    progress.finish("p1")

    progress.update("p1", step=2, total=4, node="3")

    assert progress.current() is None


def test_a_missing_node_keeps_the_last_known_one() -> None:
    progress.start("p1", "m")
    progress.update("p1", step=1, total=4, node="3")

    progress.update("p1", step=2, total=4, node=None)

    current = progress.current()
    assert current is not None
    assert current["node"] == "3"


def test_restarting_a_prompt_id_makes_it_the_newest(clock: _Clock) -> None:
    progress.start("a", "m1")
    progress.start("b", "m2")
    clock.now += 1
    progress.start("a", "m1")

    current = progress.current()
    assert current is not None
    assert current["prompt_id"] == "a"
    assert current["elapsed_s"] == 0.0
