"""hal0.mcp.browser_server — smoke coverage for the pure, browser-free surface.

``browser_server.py`` imports Playwright at module scope (to launch a real
persistent Chromium instance) and the real ``mcp`` SDK's ``FastMCP``. Neither
is guaranteed in a clean CI environment, so this whole module is guarded with
``importorskip`` — it skips cleanly wherever playwright/chromium isn't
installed, same as a dev box without the browser extra.

Where the deps ARE present, this exercises:

* ``_selector`` — pure by-strategy selector string builder (no browser).
* ``_trim_snapshot`` — pure recursive accessibility-tree trimmer (no browser).
* Tool registration — that every ``@mcp.tool()`` primitive is present in
  ``mcp.list_tools()`` (registration/wiring only; no tool is ever called,
  since every one of them requires a live ``Page``).
"""

from __future__ import annotations

import pytest

_playwright = pytest.importorskip("playwright", reason="playwright/chromium not installed")
_fastmcp = pytest.importorskip("mcp.server.fastmcp", reason="mcp SDK not installed")

from hal0.mcp import browser_server as bs  # noqa: E402

# ── _selector ──────────────────────────────────────────────────────────────


def test_selector_css_is_passthrough() -> None:
    assert bs._selector("css", ".foo") == ".foo"


def test_selector_text_wraps_in_text_equals() -> None:
    assert bs._selector("text", "Submit") == 'text="Submit"'


def test_selector_xpath_is_passthrough() -> None:
    assert bs._selector("xpath", "//button") == "//button"


def test_selector_id_prefixes_hash() -> None:
    assert bs._selector("id", "myid") == "#myid"


def test_selector_role_builds_attribute_selector() -> None:
    assert bs._selector("role", "button") == '[role="button"]'


def test_selector_testid_builds_data_testid_selector() -> None:
    assert bs._selector("testid", "submit-btn") == '[data-testid="submit-btn"]'


def test_selector_placeholder_builds_attribute_selector() -> None:
    assert bs._selector("placeholder", "Email") == '[placeholder="Email"]'


def test_selector_unknown_strategy_falls_back_to_raw_value() -> None:
    assert bs._selector("bogus-strategy", "whatever") == "whatever"


# ── _trim_snapshot ─────────────────────────────────────────────────────────


def test_trim_snapshot_basic_shape() -> None:
    node = {"role": "button", "name": "Submit", "children": []}
    out = bs._trim_snapshot(node, max_depth=6, max_children=30)
    assert out == {"role": "button", "name": "Submit"}


def test_trim_snapshot_defaults_missing_role_and_name() -> None:
    out = bs._trim_snapshot({}, max_depth=6, max_children=30)
    assert out["role"] == "unknown"
    assert out["name"] == ""


def test_trim_snapshot_truncates_name_to_200_chars() -> None:
    node = {"role": "text", "name": "x" * 500}
    out = bs._trim_snapshot(node, max_depth=6, max_children=30)
    assert len(out["name"]) == 200


def test_trim_snapshot_returns_none_past_max_depth() -> None:
    assert bs._trim_snapshot({"role": "a"}, max_depth=0, max_children=5, depth=1) is None


def test_trim_snapshot_recurses_into_children() -> None:
    node = {
        "role": "list",
        "name": "",
        "children": [
            {"role": "item", "name": "one"},
            {"role": "item", "name": "two"},
        ],
    }
    out = bs._trim_snapshot(node, max_depth=6, max_children=30)
    assert [c["role"] for c in out["children"]] == ["item", "item"]


def test_trim_snapshot_caps_children_at_max_children() -> None:
    node = {
        "role": "list",
        "name": "",
        "children": [{"role": "item", "name": str(i)} for i in range(50)],
    }
    out = bs._trim_snapshot(node, max_depth=6, max_children=5)
    assert len(out["children"]) == 5


def test_trim_snapshot_omits_children_key_at_max_depth() -> None:
    node = {"role": "list", "name": "", "children": [{"role": "item", "name": "x"}]}
    out = bs._trim_snapshot(node, max_depth=0, max_children=30)
    assert "children" not in out


def test_trim_snapshot_recurses_multiple_levels_within_max_depth() -> None:
    node = {
        "role": "root",
        "name": "",
        "children": [
            {"role": "leaf", "name": "deep", "children": [{"role": "leaf2", "name": "deeper"}]}
        ],
    }
    out = bs._trim_snapshot(node, max_depth=2, max_children=30)
    assert out["children"][0]["name"] == "deep"
    assert out["children"][0]["children"][0]["name"] == "deeper"


# ── Tool registration (wiring only, no browser calls) ─────────────────────


def test_mcp_server_is_named_hal0_browser() -> None:
    assert bs.mcp.name == "hal0-browser"


async def test_all_browser_tools_are_registered() -> None:
    tools = await bs.mcp.list_tools()
    names = {t.name for t in tools}
    assert names == {
        "browser_navigate",
        "browser_screenshot",
        "browser_click",
        "browser_type",
        "browser_extract",
        "browser_evaluate",
        "browser_snapshot",
        "browser_close_page",
    }
