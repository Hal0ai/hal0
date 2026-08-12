"""``SlotManager.iter_configs_detailed`` — report what the read skipped.

``iter_configs`` swallows a per-slot ``SlotConfigError`` and continues, so
its return value cannot distinguish "that slot is gone" from "that slot's
TOML momentarily wouldn't parse". Callers that REPLACE derived state from
the enumeration need that distinction: the ``/v1/models`` composite
catalogue replaces ``model_cache["hal0"]`` wholesale (#1837), and doing
that with a partial read silently un-advertises a healthy slot's model.

These cases run against the REAL manager over real on-disk TOMLs — the
api-side guard is unit-tested with a double in
``tests/api/test_upstream_dedup.py``, and this is what proves the double's
shape is one the production code path actually produces.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from hal0.slots.manager import SlotManager


def _write(root: Path, name: str, body: str) -> None:
    root.mkdir(parents=True, exist_ok=True)
    (root / f"{name}.toml").write_text(body, encoding="utf-8")


@pytest.mark.asyncio
async def test_detailed_reports_a_slot_whose_toml_does_not_parse(
    tmp_hal0_home: str,
) -> None:
    """A malformed TOML is skipped from the configs AND named in the
    skipped list — the healthy sibling still comes back."""
    root = Path(tmp_hal0_home) / "etc" / "hal0" / "slots"
    _write(
        root,
        "healthy",
        'name = "healthy"\nport = 8191\nprovider = "llama-server"\n[model]\ndefault = "m-a"\n',
    )
    _write(root, "broken", 'name = "broken"\nport = = = 8192\n[model\n')

    sm = SlotManager()
    cfgs, skipped = await sm.iter_configs_detailed()

    names = {c.get("name") for c in cfgs}
    assert "healthy" in names
    assert "broken" not in names
    assert skipped == ["broken"], f"skipped slots not reported: {skipped!r}"


@pytest.mark.asyncio
async def test_detailed_reports_nothing_skipped_for_a_clean_catalogue(
    tmp_hal0_home: str,
) -> None:
    """The common case: every configured slot parses, so nothing is
    reported skipped and the caller may safely replace derived state."""
    root = Path(tmp_hal0_home) / "etc" / "hal0" / "slots"
    _write(
        root,
        "healthy",
        'name = "healthy"\nport = 8191\nprovider = "llama-server"\n[model]\ndefault = "m-a"\n',
    )

    sm = SlotManager()
    cfgs, skipped = await sm.iter_configs_detailed()

    assert skipped == []
    assert "healthy" in {c.get("name") for c in cfgs}


@pytest.mark.asyncio
async def test_iter_configs_still_returns_a_bare_list(tmp_hal0_home: str) -> None:
    """The existing ``iter_configs`` contract is unchanged — it delegates
    to the detailed form and drops the skip report."""
    root = Path(tmp_hal0_home) / "etc" / "hal0" / "slots"
    _write(
        root,
        "healthy",
        'name = "healthy"\nport = 8191\nprovider = "llama-server"\n[model]\ndefault = "m-a"\n',
    )

    sm = SlotManager()
    cfgs = await sm.iter_configs()

    assert isinstance(cfgs, list)
    assert "healthy" in {c.get("name") for c in cfgs}
