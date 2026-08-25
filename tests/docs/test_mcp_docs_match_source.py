"""``docs/reference/mcp-tools.mdx`` and ``docs/guides/connect-mcp.mdx`` must
match the MCP tool catalog and mount contract in source (#1902).

Two shipped-docs defects, both found by ``rc-validate`` v1.0.0-rc.6:

1. ``connect-mcp.mdx``'s "### Connect" section told a client to point at the
   bare mount URL (``http://localhost:8080/mcp/admin``). That 405s — FastMCP's
   Streamable-HTTP sub-app expects ``POST`` on ``/mcp/admin/mcp``, not the
   mount root (see ``hal0.api.routes.mcp.mcp_url`` and #1796, which pinned
   this exact rule for the REST introspection surface). The curl example
   further down the same doc already gets this right; the "Connect" prose
   above it did not.
2. ``mcp-tools.mdx`` claims to enumerate "every MCP tool" but its tables
   named a stale subset — 89 of 180 admin tools and 21 of 26 memory tools
   were undocumented, most of them added after the page was first written.
   The ``AUTONOMOUS_READ_TOOLS`` / ``AUTONOMOUS_WRITE_TOOLS`` / ``GATED_TOOLS``
   frozensets in ``hal0.mcp.admin``, and the ``@server.tool`` registrations in
   ``hal0.mcp.memory``, are ground truth — this test diffs the doc's table
   rows against them by name set (not count, which a docs author could pad
   to pass without naming the actual missing tools).
"""

from __future__ import annotations

import re
from pathlib import Path

from hal0.mcp.admin import (
    AUTONOMOUS_READ_TOOLS,
    AUTONOMOUS_WRITE_TOOLS,
    GATED_TOOLS,
)

_REPO_ROOT = Path(__file__).resolve().parents[2]
_CONNECT_MD = _REPO_ROOT / "docs" / "guides" / "connect-mcp.mdx"
_TOOLS_MD = _REPO_ROOT / "docs" / "reference" / "mcp-tools.mdx"

# Every @server.tool(name="...") registration in hal0.mcp.memory (the
# module doesn't export its own frozenset of tool names, so this test
# mirrors the same source-of-truth pattern as admin's tools).
_MEMORY_SERVER_TOOL_RE = re.compile(r'@server\.tool\(\s*name="([a-z_0-9]+)"')


def _memory_tool_names() -> frozenset[str]:
    from hal0.mcp import memory as memory_mod

    src = Path(memory_mod.__file__).read_text(encoding="utf-8")
    names = _MEMORY_SERVER_TOOL_RE.findall(src)
    assert names, (
        "found no @server.tool registrations in hal0.mcp.memory — extraction regex is stale"
    )
    return frozenset(names)


def _connect_text() -> str:
    assert _CONNECT_MD.exists(), f"missing {_CONNECT_MD}"
    return _CONNECT_MD.read_text(encoding="utf-8")


def _tools_text() -> str:
    assert _TOOLS_MD.exists(), f"missing {_TOOLS_MD}"
    return _TOOLS_MD.read_text(encoding="utf-8")


def _connect_section(text: str) -> str:
    start = text.index("### Connect")
    rest = text[start:]
    nxt = rest.find("\n### ", 1)
    return rest if nxt == -1 else rest[:nxt]


def _section(text: str, start_marker: str, *end_markers: str) -> str:
    """Slice ``text`` from ``start_marker`` to the first end marker found after it."""
    start = text.index(start_marker)
    rest = text[start:]
    cut = len(rest)
    for marker in end_markers:
        pos = rest.find(marker, len(start_marker))
        if pos != -1:
            cut = min(cut, pos)
    return rest[:cut]


def _table_row_names(section: str) -> set[str]:
    """Backticked tool names from the *first cell* of each markdown table row.

    First-cell-only matters: delegate rows list several names in one cell
    (all wanted), while Returns/route cells contain backticked payload shapes
    and cross-references to other tools (not wanted).
    """
    names: set[str] = set()
    for line in section.splitlines():
        stripped = line.strip()
        if not stripped.startswith("|") or stripped.startswith(("|---", "| Tool", "| Method")):
            continue
        first_cell = stripped.split("|")[1]
        names |= set(re.findall(r"`([a-z][a-z_0-9]*)`", first_cell))
    return names


def _assert_tier_matches(tier: str, doc: set[str], src: frozenset[str] | set[str]) -> None:
    missing = sorted(set(src) - doc)
    stale = sorted(doc - set(src))
    assert not missing and not stale, (
        f"mcp-tools.mdx {tier} table has drifted from source: "
        f"missing {missing or 'nothing'}, stale/misplaced rows {stale or 'none'} — "
        "a tool must appear in exactly the table matching its frozenset tier"
    )


def test_connect_section_has_no_bare_mount_urls() -> None:
    """A client POSTing to the bare mount root gets a 405 (#1796); the
    Connect section must send them straight to the working ``/mcp`` path."""
    section = _connect_section(_connect_text())
    for bare in ("localhost:8080/mcp/admin\n", "localhost:8080/mcp/memory\n"):
        assert bare not in section, (
            f"Connect section still gives the bare mount URL {bare.strip()!r} — "
            "it 405s on loopback (#1796); the doc must point at "
            f"{bare.strip()}/mcp instead"
        )


def test_connect_section_urls_end_in_mcp() -> None:
    section = _connect_section(_connect_text())
    urls = re.findall(r"https?://\S+/mcp/(?:admin|memory)\S*", section)
    assert urls, "Connect section names no /mcp/admin or /mcp/memory URL to check"
    for url in urls:
        assert url.rstrip("`").endswith("/mcp"), (
            f"Connect section URL {url!r} does not end in '/mcp' — the mount root 405s (#1796)"
        )


def test_admin_tool_tables_match_frozensets_per_tier() -> None:
    """Set equality per gating tier, not "name appears somewhere on the page".

    This is the ratchet #1902 asked to promote: it fails on a deleted or gutted
    section, on tier drift (a Gated tool row moved to an Autonomous table), and
    on stale rows for tools that no longer exist in source.
    """
    admin = _section(_tools_text(), "## `hal0-admin`", "\n## `hal0-memory`")
    tiers = {
        "Autonomous — read": AUTONOMOUS_READ_TOOLS,
        "Autonomous — write": AUTONOMOUS_WRITE_TOOLS,
        "Gated": GATED_TOOLS,
    }
    for heading, src in tiers.items():
        table = _section(admin, f"### {heading}", "\n### ")
        _assert_tier_matches(f"admin '{heading}'", _table_row_names(table), src)


def test_memory_tool_tables_match_registrations_per_tier() -> None:
    """The three hal0-memory sub-tables must name exactly the registered
    ``@server.tool``s, each in the table matching its admin-frozenset tier."""
    memory_tools = _memory_tool_names()
    mem = _section(_tools_text(), "## `hal0-memory`", "\n## ")
    tiers = {
        "Autonomous — read": memory_tools & AUTONOMOUS_READ_TOOLS,
        "Autonomous — write": memory_tools & AUTONOMOUS_WRITE_TOOLS,
        "Gated": memory_tools & GATED_TOOLS,
    }
    documented: set[str] = set()
    for heading, src in tiers.items():
        table = _section(mem, f"**{heading}**", "\n**", "\nValidation errors")
        rows = _table_row_names(table)
        _assert_tier_matches(f"memory '{heading}'", rows, src)
        documented |= rows
    unclassified = sorted(memory_tools - documented)
    assert not unclassified, (
        f"hal0.mcp.memory registers tool(s) with no admin-frozenset tier and no "
        f"doc row: {unclassified} — classify them in hal0.mcp.admin and document them"
    )
