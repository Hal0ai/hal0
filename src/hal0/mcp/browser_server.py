"""
hal0 Browser MCP Server — performant, persistent browser automation.

**EXPERIMENTAL / NOT WIRED IN.** This is a standalone prototype. It is
never imported by :mod:`hal0.api.mcp_mount` (which only mounts
``hal0-admin`` and ``hal0-memory``), has no systemd unit, and is not on
any install/upgrade path — see diagnosis #10. ``playwright`` is
consequently *not* a declared dependency in ``pyproject.toml``; the
import is deferred into :meth:`BrowserPool._ensure_browser` so merely
importing this module (e.g. from a test that globs ``hal0.mcp.*``)
doesn't hard-fail on a missing package. Running it for real still
requires ``pip install playwright && playwright install chromium``.
Kept around as a prototype rather than deleted — wire it into
``mount_mcp_servers`` (behind an opt-in flag) or delete it outright
before it's treated as a supported surface.

Provides a small set of composable browser primitives via MCP. The
browser is launched once at startup and reused across all tool calls
for zero cold-start latency. Chromium headless shell is used for
maximum speed and minimal resource footprint.

Tools:
  browser_navigate   — Go to a URL, return page title + snapshot
  browser_screenshot — Capture viewport or full-page screenshot
  browser_click      — Click an element by selector or text
  browser_type       — Type into an input field
  browser_extract    — Extract text, HTML, or structured content
  browser_evaluate   — Execute arbitrary JavaScript
  browser_snapshot   — Accessibility snapshot (fast structured view)

Architecture:
  - Single persistent Chromium instance (launched on first use)
  - Connection pool: concurrent pages with 60s idle timeout
  - Auto-recovery: browser crashes are detected and re-launched
  - FastMCP transport: SSE over HTTP, same pattern as hal0-admin

Usage:
  HAL0_BROWSER_HEADLESS=0 .venv/bin/python src/hal0/mcp/browser_server.py
  # Registers on port 9178 by default (HAL0_BROWSER_PORT).
"""

from __future__ import annotations

import asyncio
import base64
import contextlib
import logging
import os
import time
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any

from mcp.server.fastmcp import FastMCP

if TYPE_CHECKING:
    # Type-only — `from __future__ import annotations` (PEP 563) means
    # these never need to resolve at runtime, so they don't force the
    # (undeclared, optional) playwright dependency onto plain imports
    # of this module. The real import is deferred into
    # BrowserPool._ensure_browser, the only place it's actually used.
    from playwright.async_api import Browser, BrowserContext, Page

logger = logging.getLogger("hal0.browser")

# ── Config ───────────────────────────────────────────────────────────────

PORT = int(os.environ.get("HAL0_BROWSER_PORT", "9178"))
HEADLESS = os.environ.get("HAL0_BROWSER_HEADLESS", "1") != "0"
PAGE_IDLE_TIMEOUT_S = int(os.environ.get("HAL0_BROWSER_PAGE_IDLE_S", "60"))
MAX_CONCURRENT_PAGES = int(os.environ.get("HAL0_BROWSER_PAGE_MAX", "4"))

# ── Browser pool ──────────────────────────────────────────────────────────


@dataclass
class BrowserPool:
    """Persistent browser with page lifecycle management."""

    _browser: Browser | None = field(default=None, init=False)
    _context: BrowserContext | None = field(default=None, init=False)
    _pages: dict[str, tuple[Page, float]] = field(default_factory=dict, init=False)
    _lock: asyncio.Lock = field(default_factory=asyncio.Lock, init=False)

    async def _ensure_browser(self) -> Browser:
        if self._browser is None or not self._browser.is_connected():
            try:
                from playwright.async_api import async_playwright
            except ImportError as exc:  # pragma: no cover — exercised only if run standalone
                raise ImportError(
                    "hal0.mcp.browser_server requires the (optional, undeclared) "
                    "'playwright' package. Install via 'pip install playwright && "
                    "playwright install chromium' — this server is experimental and "
                    "not wired into any hal0 install path (diagnosis #10)."
                ) from exc
            logger.info("Launching Chromium (headless=%s)", HEADLESS)
            pw = await async_playwright().start()
            self._browser = await pw.chromium.launch(
                headless=HEADLESS,
                channel="chromium",
                args=[
                    "--disable-gpu",
                    "--disable-dev-shm-usage",
                    "--no-sandbox",
                    "--disable-setuid-sandbox",
                    "--single-process",  # lighter memory footprint
                ],
            )
            self._context = await self._browser.new_context(
                viewport={"width": 1280, "height": 800},
                user_agent=(
                    "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
                    "(KHTML, like Gecko) Chrome/149.0.0.0 Safari/537.36"
                ),
            )
            # Start idle page reaper.
            self._reaper_task = asyncio.create_task(self._reap_idle_pages())
        return self._browser

    async def get_page(self, page_id: str = "default") -> Page:
        async with self._lock:
            await self._ensure_browser()
            entry = self._pages.get(page_id)
            if entry is not None:
                page, _ = entry
                # Check if page is still alive.
                try:
                    await page.evaluate("1")
                    self._pages[page_id] = (page, time.monotonic())
                    return page
                except Exception:
                    # Page is dead, remove and recreate.
                    self._pages.pop(page_id, None)

            if len(self._pages) >= MAX_CONCURRENT_PAGES:
                # Evict the oldest page.
                oldest_id = min(self._pages, key=lambda k: self._pages[k][1])
                oldest_page, _ = self._pages.pop(oldest_id)
                with contextlib.suppress(Exception):
                    await oldest_page.close()

            page = await self._context.new_page()
            self._pages[page_id] = (page, time.monotonic())
            return page

    async def _reap_idle_pages(self) -> None:
        """Background task: close pages idle longer than PAGE_IDLE_TIMEOUT_S."""
        while True:
            await asyncio.sleep(15)
            now = time.monotonic()
            async with self._lock:
                stale = [
                    pid
                    for pid, (_, last_used) in self._pages.items()
                    if now - last_used > PAGE_IDLE_TIMEOUT_S
                ]
                for pid in stale:
                    page, _ = self._pages.pop(pid)
                    with contextlib.suppress(Exception):
                        await page.close()

    async def close_page(self, page_id: str) -> None:
        async with self._lock:
            entry = self._pages.pop(page_id, None)
            if entry is not None:
                page, _ = entry
                with contextlib.suppress(Exception):
                    await page.close()

    async def shutdown(self) -> None:
        logger.info("Shutting down browser pool")
        async with self._lock:
            for page, _ in self._pages.values():
                with contextlib.suppress(Exception):
                    await page.close()
            self._pages.clear()
            if self._context:
                with contextlib.suppress(Exception):
                    await self._context.close()
            if self._browser:
                with contextlib.suppress(Exception):
                    await self._browser.close()
            self._browser = None
            self._context = None


# ── Global state ──────────────────────────────────────────────────────────

pool = BrowserPool()
mcp = FastMCP(
    name="hal0-browser",
    host="127.0.0.1",
    port=PORT,
    instructions="Persistent Chromium browser automation for hal0 agents. "
    "Provides composable browser primitives: navigate, screenshot, click, "
    "type, extract, evaluate, snapshot.",
)

# ── Helper: normalize selector ────────────────────────────────────────────


def _selector(by: str, value: str) -> str:
    """Build a Playwright selector from a strategy + value pair."""
    strategies: dict[str, str] = {
        "css": value,
        "text": f'text="{value}"',
        "xpath": value,
        "id": f"#{value}",
        "role": f'[role="{value}"]',
        "testid": f'[data-testid="{value}"]',
        "placeholder": f'[placeholder="{value}"]',
    }
    return strategies.get(by, value)


# ── Tools ─────────────────────────────────────────────────────────────────


@mcp.tool()
async def browser_navigate(url: str, page_id: str = "default") -> dict[str, Any]:
    """Navigate to a URL. Returns page title, URL, and a text snapshot of the visible content."""
    page = await pool.get_page(page_id)
    try:
        response = await page.goto(url, wait_until="domcontentloaded", timeout=30_000)
        title = await page.title()
        text = await page.evaluate("() => document.body?.innerText?.substring(0, 8000) || ''")
        return {
            "ok": response is not None and response.ok,
            "status": response.status if response else None,
            "url": page.url,
            "title": title,
            "text": text,
        }
    except Exception as exc:
        return {"ok": False, "error": str(exc), "url": url}


@mcp.tool()
async def browser_screenshot(
    page_id: str = "default",
    full_page: bool = False,
    selector: str | None = None,
) -> dict[str, Any]:
    """Take a screenshot. Returns base64-encoded PNG.

    Set full_page=True for the entire scrollable page.
    Set selector to capture only a specific element (CSS selector)."""
    page = await pool.get_page(page_id)
    try:
        opts: dict[str, Any] = {"full_page": full_page, "type": "png"}
        if selector:
            elem = await page.query_selector(selector)
            if elem is None:
                return {"ok": False, "error": f"selector not found: {selector}"}
            data = await elem.screenshot(**opts)
        else:
            data = await page.screenshot(**opts)
        return {
            "ok": True,
            "image_base64": base64.b64encode(data).decode(),
            "mime": "image/png",
        }
    except Exception as exc:
        return {"ok": False, "error": str(exc)}


@mcp.tool()
async def browser_click(
    selector: str,
    by: str = "css",
    page_id: str = "default",
) -> dict[str, Any]:
    """Click an element. Use 'by' to specify selector strategy: css, text, id, role, testid, xpath, placeholder."""
    page = await pool.get_page(page_id)
    try:
        sel = _selector(by, selector)
        await page.click(sel, timeout=10_000)
        title = await page.title()
        url = page.url
        return {"ok": True, "clicked": selector, "url": url, "title": title}
    except Exception as exc:
        return {"ok": False, "error": str(exc), "selector": selector}


@mcp.tool()
async def browser_type(
    selector: str,
    text: str,
    by: str = "css",
    clear_first: bool = True,
    press_enter: bool = False,
    page_id: str = "default",
) -> dict[str, Any]:
    """Type text into an input field. Set clear_first=True to clear existing content. Set press_enter=True to submit."""
    page = await pool.get_page(page_id)
    try:
        sel = _selector(by, selector)
        await page.wait_for_selector(sel, timeout=10_000)
        if clear_first:
            await page.fill(sel, "")
        await page.type(sel, text)
        if press_enter:
            await page.press(sel, "Enter")
        return {"ok": True, "typed": text, "selector": selector}
    except Exception as exc:
        return {"ok": False, "error": str(exc), "selector": selector}


@mcp.tool()
async def browser_extract(
    selector: str | None = None,
    what: str = "text",
    page_id: str = "default",
) -> dict[str, Any]:
    """Extract content from the page. 'what' can be: text, html, links, or form (inputs + buttons). Leave selector empty for full page."""
    page = await pool.get_page(page_id)
    try:
        target = page
        if selector:
            elem = await page.query_selector(selector)
            if elem is None:
                return {"ok": False, "error": f"selector not found: {selector}"}
            target = elem

        if what == "text":
            content = await target.evaluate("el => el.innerText?.substring(0, 20000) || ''")
        elif what == "html":
            content = await target.evaluate("el => el.outerHTML?.substring(0, 50000) || ''")
        elif what == "links":
            links = await target.evaluate("""() => {
                const as = Array.from(document.querySelectorAll('a[href]'));
                return as.slice(0, 200).map(a => ({
                    text: a.innerText?.trim()?.substring(0,120) || '',
                    href: a.href
                }));
            }""")
            return {"ok": True, "links": links}
        elif what == "form":
            inputs = await target.evaluate("""() => {
                const fields = Array.from(document.querySelectorAll('input, textarea, select, button'));
                return fields.slice(0, 100).map(el => ({
                    tag: el.tagName.toLowerCase(),
                    type: el.type || '',
                    name: el.name || '',
                    id: el.id || '',
                    placeholder: el.placeholder || '',
                    value: el.value?.substring(0, 200) || '',
                    text: (el.innerText || el.textContent || '').trim().substring(0, 200)
                }));
            }""")
            return {"ok": True, "form_fields": inputs}
        else:
            return {"ok": False, "error": f"unknown what={what}. Use: text, html, links, form"}

        return {"ok": True, what: content}
    except Exception as exc:
        return {"ok": False, "error": str(exc)}


@mcp.tool()
async def browser_evaluate(
    script: str,
    page_id: str = "default",
) -> dict[str, Any]:
    """Execute arbitrary JavaScript in the page context. Returns the JSON-serializable result."""
    page = await pool.get_page(page_id)
    try:
        result = await page.evaluate(f"() => {{ {script} }}")
        return {"ok": True, "result": result}
    except Exception as exc:
        return {"ok": False, "error": str(exc)}


@mcp.tool()
async def browser_snapshot(page_id: str = "default") -> dict[str, Any]:
    """Get an accessibility snapshot — a fast structured view of the page. Best for understanding page structure without full DOM parsing."""
    page = await pool.get_page(page_id)
    try:
        snapshot = await page.accessibility.snapshot()
        if snapshot is None:
            return {"ok": True, "snapshot": None, "note": "page has no accessibility tree"}
        # Trim to a reasonable size for LLM context.
        return {"ok": True, "snapshot": _trim_snapshot(snapshot, max_depth=6, max_children=30)}
    except Exception as exc:
        return {"ok": False, "error": str(exc)}


@mcp.tool()
async def browser_close_page(page_id: str = "default") -> dict[str, Any]:
    """Close a browser page/tab. Use this when done with a page to free resources."""
    await pool.close_page(page_id)
    return {"ok": True, "closed": page_id}


def _trim_snapshot(
    node: dict[str, Any], max_depth: int, max_children: int, depth: int = 0
) -> dict[str, Any] | None:
    """Recursively trim an accessibility snapshot to manageable size."""
    if depth > max_depth:
        return None
    trimmed: dict[str, Any] = {
        "role": node.get("role", "unknown"),
        "name": (node.get("name") or "")[:200],
    }
    children = node.get("children")
    if children and depth < max_depth:
        trimmed["children"] = [
            child
            for child in (
                _trim_snapshot(c, max_depth, max_children, depth + 1)
                for c in children[:max_children]
            )
            if child is not None
        ]
    return trimmed


# ── Entry point ───────────────────────────────────────────────────────────

if __name__ == "__main__":
    logging.basicConfig(
        level=logging.INFO, format="%(asctime)s [%(name)s] %(levelname)s: %(message)s"
    )
    logger.info("Starting hal0-browser on http://127.0.0.1:%d/mcp", PORT)
    mcp.run(transport="streamable-http")
