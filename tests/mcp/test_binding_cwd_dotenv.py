"""FastMCP settings never read a ``.env`` from the process working directory (#2907).

``mcp.server.fastmcp.server.Settings`` is a pydantic-settings model with
``env_file=".env"``, so every ``FastMCP(...)`` construction opened ``.env`` in
whatever directory the process happened to sit in. Under ``hal0 agent
reprovision hermes --repair`` run from ``/root`` as the ``hal0`` user that
raised ``PermissionError: '.env'`` inside ``create_app()`` and failed the whole
MCP mount. Importing :mod:`hal0.mcp._binding` turns the dotenv read off.
"""

from __future__ import annotations

import os
from pathlib import Path

import pytest
from mcp.server.fastmcp import FastMCP


def test_fastmcp_constructs_from_an_unreadable_cwd(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The repro: cwd inherited from root's ``/root``, unreadable to this user."""
    if os.geteuid() == 0:
        pytest.skip("root can read any directory")
    import hal0.mcp._binding  # noqa: F401

    locked = tmp_path / "locked"
    locked.mkdir()
    (locked / ".env").write_text("FASTMCP_PORT=1\n", encoding="utf-8")
    monkeypatch.chdir(locked)
    locked.chmod(0)
    try:
        FastMCP("probe")
    finally:
        locked.chmod(0o700)
