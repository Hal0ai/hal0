"""``hal0.bridge.output.run_list_item``: the one projection behind both run lists (ADR-0043)."""

from __future__ import annotations

from typing import Any

from hal0.bridge.output import OUTPUT_PREVIEW_CHARS, run_list_item

SECRET = "hal0bk_cc.s3cr3tvalue0123456789"


def _view(**over: Any) -> dict[str, Any]:
    view: dict[str, Any] = {
        "run_id": "run_1",
        "agent": "hermes/coder",
        "profile": "coder",
        "client": "cc",
        "status": "completed",
        "created_at": 1.0,
        "updated_at": 2.0,
        "input_preview": "ping",
        "output": "pong",
        "error": None,
        "usage": None,
        "pending": None,
        "last_seq": 3,
    }
    view.update(over)
    return view


def test_a_list_item_swaps_output_for_a_preview_and_keeps_the_rest() -> None:
    view = _view(error="boom")
    item = run_list_item(view)
    assert "output" not in item
    assert item["output_preview"] == "pong"
    assert {k: v for k, v in item.items() if k != "output_preview"} == {
        k: v for k, v in view.items() if k != "output"
    }


def test_the_preview_is_the_first_200_characters() -> None:
    assert OUTPUT_PREVIEW_CHARS == 200
    item = run_list_item(_view(output="x" * 30_000))
    assert item["output_preview"] == "x" * 200


def test_the_preview_is_masked_before_it_is_cut() -> None:
    item = run_list_item(_view(output=f"Authorization: Bearer {SECRET} " + "y" * 500))
    assert SECRET not in item["output_preview"]
    assert item["output_preview"].startswith("Authorization: Bearer")
    assert len(item["output_preview"]) <= 200


def test_a_run_without_output_previews_none_and_the_view_is_untouched() -> None:
    view = _view(output=None)
    item = run_list_item(view)
    assert item["output_preview"] is None and "output" not in item
    assert view["output"] is None  # the caller's view is not mutated
    assert "output_preview" not in view


def test_a_live_run_previews_a_token_cut_short_masked_to_the_end() -> None:
    """A run that has not finished may have the rest of a token still unstored."""
    for status in ("running", "queued", None):
        item = run_list_item(_view(status=status, output="Here is ghp_A1b2C3"))
        assert item["output_preview"] == "Here is ***REDACTED***"
    # no known prefix running to the end: the redactor's output as it is
    assert (
        run_list_item(_view(status="running", output="ghp_A1b2C3 is done"))["output_preview"]
        == "ghp_A1b2C3 is done"
    )


def test_a_finished_run_keeps_the_whole_token_rule() -> None:
    """A finished output is complete: a short ``ghp_`` word is not a token cut short."""
    item = run_list_item(_view(status="completed", output="Here is ghp_A1b2C3"))
    assert item["output_preview"] == "Here is ghp_A1b2C3"
    whole = "ghp_" + "A1b2C3" * 4
    assert (
        run_list_item(_view(status="completed", output=f"Here is {whole}"))["output_preview"]
        == "Here is ***REDACTED***"
    )
