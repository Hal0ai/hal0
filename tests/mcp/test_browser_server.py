"""Regression coverage for :mod:`hal0.mcp.browser_server` (diagnosis #10).

This server is an orphaned prototype — never mounted by
:func:`hal0.api.mcp_mount.mount_mcp_servers`, no install path reaches it.
``playwright`` is consequently undeclared in ``pyproject.toml``. These
tests pin the two behaviors the fix guarantees:

* Merely importing the module must not require ``playwright`` to be
  installed (the top-level ``import playwright`` used to hard-fail any
  import of this module, e.g. from a test that globs ``hal0.mcp.*``).
* Actually using the server (launching a browser) still requires
  ``playwright`` and raises a clear, actionable ``ImportError`` when it's
  missing — rather than the raw ``ModuleNotFoundError`` traceback that
  used to blow up import-time.
"""

from __future__ import annotations

import sys

import pytest


def test_module_imports_without_playwright_installed() -> None:
    """Import must succeed even when ``playwright`` isn't installed.

    Force a fresh import so a prior successful import (e.g. from an
    earlier test file, or a dev environment with playwright present)
    doesn't mask a regression here.
    """
    sys.modules.pop("hal0.mcp.browser_server", None)
    module = __import__("hal0.mcp.browser_server", fromlist=["BrowserPool"])
    assert hasattr(module, "BrowserPool")
    assert hasattr(module, "mcp")


def test_module_docstring_flags_experimental_not_wired() -> None:
    from hal0.mcp import browser_server

    assert browser_server.__doc__ is not None
    assert "EXPERIMENTAL" in browser_server.__doc__
    assert "NOT WIRED IN" in browser_server.__doc__


@pytest.mark.asyncio
async def test_ensure_browser_raises_clear_error_without_playwright(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """If playwright truly isn't importable, launching must fail with a
    clear, actionable ImportError rather than an opaque traceback."""
    from hal0.mcp import browser_server

    if _playwright_installed():
        pytest.skip("playwright is installed in this environment")

    pool = browser_server.BrowserPool()
    with pytest.raises(ImportError, match="playwright"):
        await pool._ensure_browser()


def _playwright_installed() -> bool:
    try:
        import playwright.async_api  # noqa: F401

        return True
    except ImportError:
        return False
