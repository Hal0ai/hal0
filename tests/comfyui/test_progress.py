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

    # Nothing has stepped yet. ComfyUI's queue is FIFO, so the oldest render runs first.
    first = progress.current()
    assert first is not None
    assert first["prompt_id"] == "p-old"

    progress.update("p-old", step=3, total=20, node="12")

    assert progress.current() == {
        "model": "sdxl-lightning",
        "step": 3,
        "total": 20,
        "node": "12",
        "elapsed_s": 7.3,
        "prompt_id": "p-old",
    }

    # A later frame replaces the earlier one.
    progress.update("p-old", step=4, total=20, node="12")
    current = progress.current()
    assert current is not None
    assert (current["step"], current["total"]) == (4, 20)

    # When it ends, the render that was waiting is reported, with no step yet.
    progress.finish("p-old")
    waiting = progress.current()
    assert waiting is not None
    assert waiting["prompt_id"] == "p-new"
    assert (waiting["step"], waiting["total"], waiting["node"]) == (None, None, None)
    assert waiting["elapsed_s"] == 2.3

    # Once it steps, it is the one reported.
    progress.update("p-new", step=1, total=8, node="9")
    stepping = progress.current()
    assert stepping is not None
    assert (stepping["prompt_id"], stepping["step"], stepping["total"]) == ("p-new", 1, 8)


def test_current_prefers_the_render_that_is_stepping(clock: _Clock) -> None:
    """The older render is the one running; the newer one is queued behind it."""
    progress.start("running", "sdxl-lightning")
    clock.now += 1
    progress.start("queued", "sd-1.5-pruned-emaonly")
    progress.update("running", step=5, total=8, node="3")

    current = progress.current()

    assert current is not None
    assert current["prompt_id"] == "running"
    assert (current["step"], current["total"]) == (5, 8)


def test_current_with_no_progress_yet_is_the_oldest_render(clock: _Clock) -> None:
    progress.start("first", "m1")
    clock.now += 1
    progress.start("second", "m2")
    clock.now += 1
    progress.start("third", "m3")

    current = progress.current()

    assert current is not None
    assert current["prompt_id"] == "first"


def test_current_follows_the_most_recent_update(clock: _Clock) -> None:
    """When more than one render has stepped, the one that last received a frame is reported."""
    progress.start("a", "m1")
    progress.start("b", "m2")
    progress.update("a", step=8, total=8, node="3")
    progress.update("b", step=1, total=8, node="3")

    current = progress.current()
    assert current is not None
    assert current["prompt_id"] == "b"

    progress.update("a", step=9, total=9, node="3")
    current = progress.current()
    assert current is not None
    assert current["prompt_id"] == "a"


def test_an_update_to_an_unknown_render_does_not_change_which_is_reported() -> None:
    progress.start("a", "m1")
    progress.start("b", "m2")
    progress.update("a", step=1, total=8, node="3")

    progress.update("gone", step=2, total=8, node="3")

    current = progress.current()
    assert current is not None
    assert current["prompt_id"] == "a"


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


def test_restarting_a_prompt_id_sends_it_to_the_back_of_the_queue(clock: _Clock) -> None:
    progress.start("a", "m1")
    progress.start("b", "m2")
    progress.update("a", step=3, total=8, node="3")
    clock.now += 1
    progress.start("a", "m1")

    # The restarted entry has no step yet and is now younger than "b".
    current = progress.current()
    assert current is not None
    assert current["prompt_id"] == "b"

    progress.update("a", step=1, total=8, node="3")
    current = progress.current()
    assert current is not None
    assert (current["prompt_id"], current["step"], current["elapsed_s"]) == ("a", 1, 0.0)
