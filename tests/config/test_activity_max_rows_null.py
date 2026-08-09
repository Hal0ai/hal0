"""#1665: ``[activity] max_rows = null`` ("disables the row cap") cannot persist.

``max_rows: int | None = Field(default=50_000, ge=100)`` carries the comment
"None disables the row cap". ``save_hal0_config`` uses ``exclude_none=True``
(TOML has no null), so writing ``None`` drops the key entirely and the next
load restores the ``50_000`` default -- the documented unlimited mode was
unreachable through any persisted config. ``PUT /api/settings`` accepted
``{"activity": {"max_rows": null}}``, returned 200 with ``max_rows=null`` in
the echoed config, and silently reverted on the next reload/restart.

Same save/load re-default class as #1644 (``[brain_chat] tool_model``): the
internal "unlimited" value (``None``) has no lossless TOML spelling, so it
must be given one at the serialization boundary rather than relying on
``exclude_none`` to round-trip it.
"""

from __future__ import annotations

from pathlib import Path

import pytest
from pydantic import ValidationError

from hal0.config.loader import load_hal0_config, save_hal0_config
from hal0.config.schema import ActivityConfig, Hal0Config


def test_the_default_is_still_the_anchor() -> None:
    assert ActivityConfig().max_rows == 50_000


def test_none_means_unlimited_in_memory() -> None:
    assert ActivityConfig(max_rows=None).max_rows is None


def test_a_real_value_is_kept() -> None:
    assert ActivityConfig(max_rows=1000).max_rows == 1000


def test_below_the_floor_is_still_rejected() -> None:
    with pytest.raises(ValidationError):
        ActivityConfig(max_rows=50)


def test_max_rows_null_survives_a_save_load_round_trip(tmp_path: Path) -> None:
    """The regression itself (#1665)."""
    toml_path = tmp_path / "hal0.toml"
    save_hal0_config(Hal0Config(activity={"max_rows": None}), toml_path)
    reloaded = load_hal0_config(toml_path)
    assert reloaded.activity.max_rows is None, (
        "max_rows=None did not survive a save/load round trip: "
        f"got {reloaded.activity.max_rows!r}"
    )


def test_zero_is_accepted_on_disk_as_the_unlimited_spelling(tmp_path: Path) -> None:
    """0 is below the ge=100 floor, so it was never a valid persisted row
    cap -- free to repurpose as the on-disk "unlimited" sentinel, the same
    way "off"/"none"/"disabled" are reserved spellings for tool_model."""
    toml_path = tmp_path / "hal0.toml"
    toml_path.write_text("[activity]\nmax_rows = 0\n", encoding="utf-8")
    assert load_hal0_config(toml_path).activity.max_rows is None


def test_a_real_value_still_round_trips(tmp_path: Path) -> None:
    toml_path = tmp_path / "hal0.toml"
    save_hal0_config(Hal0Config(activity={"max_rows": 1234}), toml_path)
    assert load_hal0_config(toml_path).activity.max_rows == 1234


def test_the_api_echo_still_shows_null_not_the_disk_sentinel() -> None:
    """The dump used for API responses (no exclude_none) must keep showing
    the real None, not leak the 0 on-disk encoding into the client."""
    cfg = ActivityConfig(max_rows=None)
    assert cfg.model_dump()["max_rows"] is None
