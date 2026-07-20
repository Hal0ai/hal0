"""Catalog consistency guard tests (spec §4.2/§4.4).

``_validate_overlay`` checks the app-independent hand-authored overlays
(classification frozensets, TOOL_NAME_ALIASES, descriptions, annotations,
param hints); ``_validate_catalog`` adds the installed-route-map checks. Both
must reject the drift shapes that would otherwise detach a tool from its route
or silently change the exposed surface.
"""

from __future__ import annotations

import pytest

from hal0.mcp import admin


def test_shipped_catalog_is_coherent() -> None:
    admin._validate_overlay()
    admin._validate_catalog()  # map installed by the conftest fixture


def test_descriptions_track_classification() -> None:
    catalog = admin.AUTONOMOUS_READ_TOOLS | admin.AUTONOMOUS_WRITE_TOOLS | admin.GATED_TOOLS
    assert set(admin.TOOL_DESCRIPTIONS) == catalog


def test_aliases_cover_exactly_the_routed_catalog() -> None:
    routed = admin._routed_catalog()
    aliased = {t for names in admin.TOOL_NAME_ALIASES.values() for t in names}
    assert routed == aliased


def test_overlay_rejects_alias_for_non_routed_tool(monkeypatch: pytest.MonkeyPatch) -> None:
    bad = dict(admin.TOOL_NAME_ALIASES)
    bad["GET:/api/ghost"] = ("ghost_tool",)
    monkeypatch.setattr(admin, "TOOL_NAME_ALIASES", bad)
    with pytest.raises(RuntimeError, match="ghost_tool"):
        admin._validate_overlay()


def test_overlay_rejects_routed_tool_missing_an_alias(monkeypatch: pytest.MonkeyPatch) -> None:
    trimmed = {k: v for k, v in admin.TOOL_NAME_ALIASES.items() if "slot_list" not in v}
    monkeypatch.setattr(admin, "TOOL_NAME_ALIASES", trimmed)
    with pytest.raises(RuntimeError, match="slot_list"):
        admin._validate_overlay()


def test_validate_catalog_rejects_classified_route_with_no_live_route(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A TOOL_NAME_ALIASES route_id absent from the generated map fails —
    replaces the old route-sync 'classified but missing from _REST_MAP'."""
    bad = dict(admin.TOOL_NAME_ALIASES)
    bad["GET:/api/does-not-exist"] = ("phantom_read",)
    # phantom_read must also be classified + described for the overlay to pass
    # up to the route check.
    monkeypatch.setattr(admin, "TOOL_NAME_ALIASES", bad)
    monkeypatch.setattr(
        admin, "AUTONOMOUS_READ_TOOLS", admin.AUTONOMOUS_READ_TOOLS | {"phantom_read"}
    )
    monkeypatch.setitem(admin.TOOL_DESCRIPTIONS, "phantom_read", "x")
    monkeypatch.setitem(
        admin._ANNOTATIONS,
        "phantom_read",
        next(iter(admin._ANNOTATIONS.values())),
    )
    with pytest.raises(RuntimeError, match="no live route"):
        admin._validate_catalog()
