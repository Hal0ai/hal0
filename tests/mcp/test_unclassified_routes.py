"""Gap-1 CI report: routes the autogen scaffolds but no human has classified.

Under route-map autogen every live route lands in the generated scaffolding.
A route with no ``TOOL_NAME_ALIASES`` entry is HIDDEN from ``tools/list`` (never
an MCP tool) and NOT fatal — deny-by-default. This test surfaces that set so a
human classifies a new admin route deliberately instead of it silently becoming
agent-reachable (or silently vanishing).

It is informational (prints the list) and asserts the SAFETY property — every
unclassified route is hidden — rather than a hard count threshold.
"""

from __future__ import annotations

import asyncio

from hal0.api import create_app
from hal0.mcp import admin
from hal0.mcp.approval_queue import ApprovalQueue


def test_unclassified_routes_are_hidden_and_reported(capsys) -> None:
    create_app()  # installs the live route map

    unclassified = list(admin._UNCLASSIFIED_ROUTES)

    # Every reported route_id is genuinely unclassified (no alias) — so it is
    # not reconstructed into _REST_MAP and cannot surface as a tool.
    assert all(rid not in admin.TOOL_NAME_ALIASES for rid in unclassified)

    # None of them leaked into the exposed tool surface.
    exposed = set(admin.TOOL_DESCRIPTIONS)
    aliased_tools = {t for names in admin.TOOL_NAME_ALIASES.values() for t in names}
    assert aliased_tools <= exposed
    # A generated-but-unclassified route contributes zero tool names.
    reconstructed = set(admin._REST_MAP)
    for rid in unclassified:
        # route_id -> would-be tool names: none, because it is not aliased.
        assert rid not in admin.TOOL_NAME_ALIASES
    assert reconstructed == aliased_tools

    with capsys.disabled():
        print(
            f"\n[gap-1] unclassified admin routes: {len(unclassified)} "
            f"(hidden from tools/list, deny-by-default)"
        )
        for rid in unclassified[:20]:
            print(f"    {rid}")
        if len(unclassified) > 20:
            print(f"    … and {len(unclassified) - 20} more")


def test_tools_list_exposes_exactly_the_classified_catalog() -> None:
    """tools/list-identical proof: the autogen surfaces EXACTLY the hand-authored
    catalog (no add / drop / rename), with schemas from the reconstructed map."""
    create_app()
    server = admin.build_server(approval_queue=ApprovalQueue(), base_url="http://t")
    tools = asyncio.new_event_loop().run_until_complete(server.list_tools())

    names = sorted(t.name for t in tools)
    assert names == sorted(admin.TOOL_DESCRIPTIONS)

    # Advertised nested ``args`` schema equals the shared tool_param_schema.
    by_name = {t.name: t for t in tools}
    for name in admin.TOOL_DESCRIPTIONS:
        advertised = by_name[name].inputSchema["properties"]["args"]
        assert advertised == admin.tool_param_schema(name), name
