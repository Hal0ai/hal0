"""The dashboard's import catalog: the profile addons this box can install.

The rows come from the ``hal0.addon-index`` a site publishes (contract in
``docs/reference/profile-addons.mdx``); by default that is hal0.dev's. When the
index cannot be reached, the rows are the addons this release was built with
(:mod:`hal0.profiles.addons`), so a box with no route out still has a catalog.

Nothing here writes a profile. Every row is checked the way an import would
check it (envelope shape, checksum, flag screen, release floor) and a row that
fails is listed with the reason instead of being offered. Installing a row is
the ordinary ``POST /api/profiles/import`` with the row's envelope, which
checks all of it again.
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import re
import time
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any
from urllib.parse import urljoin, urlparse

from hal0 import __version__
from hal0.config.schema import PROFILE_SCHEMA_VERSION_CURRENT
from hal0.errors import Hal0Error
from hal0.profiles import runtime_family_of, screen_profile_flags
from hal0.profiles.addons import INDEX_KIND, INDEX_SCHEMA_VERSION, build_envelopes, build_index
from hal0.profiles.portable import envelope_runner_status, parse_envelope, verify_checksum

log = logging.getLogger(__name__)

#: Where the published index lives unless the operator points elsewhere.
DEFAULT_INDEX_URL = "https://hal0.dev/profiles/index.json"

#: Overrides :data:`DEFAULT_INDEX_URL`. ``off`` (or ``none``, ``0``, ``false``)
#: turns the outbound fetch off: the catalog is then the bundled addons only.
INDEX_URL_ENV = "HAL0_PROFILE_ADDONS_URL"

_OFF_VALUES = frozenset({"off", "none", "0", "false", "disabled"})

#: A fetched catalog is reused for this long; a failed fetch for much less, so
#: an offline box does not wait on the timeout at every page load and a box
#: that comes back online recovers within a minute.
CACHE_TTL_S = 3600.0
FAILURE_TTL_S = 60.0

#: A requested refresh is honoured at most this often. The route takes no
#: credential, so without a floor any client on the network could make the
#: box fetch as fast as it can ask.
MIN_REFRESH_INTERVAL_S = 30.0

FETCH_TIMEOUT_S = 5.0
MAX_INDEX_BYTES = 512 * 1024
MAX_ENVELOPE_BYTES = 64 * 1024
MAX_ROWS = 200
_FETCH_CONCURRENCY = 6

#: Extra index-row keys a publisher may add and the dashboard shows. Anything
#: else in a row is dropped, and each value must have the listed type.
_EXTRA_ROW_KEYS: dict[str, type] = {
    "category": str,
    "summary": str,
    "author": str,
    "first_party": bool,
    "version": int,
    "url": str,
}

#: An index row's ``file``: a bare envelope filename beside the index. A path,
#: a scheme or a host here would let an index point the box at another origin.
_FILE_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,120}\.hal0profile\.json$")

Fetch = Callable[[str], Awaitable[bytes]]


class CatalogError(Exception):
    """The published index could not be used; the message says why."""


@dataclass(frozen=True, slots=True)
class _Snapshot:
    """One fetched (or bundled) catalog: the index and its envelopes."""

    source: str  # "remote" | "bundled"
    url: str | None
    fetched_at: str
    index: dict[str, Any]
    #: Envelope JSON by index-row name; a string value is why it is missing.
    envelopes: dict[str, dict[str, Any] | str]
    error: str | None = None


#: Per index URL: when the entry expires, when it was fetched (both on the
#: monotonic clock), and the snapshot.
_cache: dict[str, tuple[float, float, _Snapshot]] = {}
_cache_lock = asyncio.Lock()


def index_url() -> str | None:
    """The index URL in force, or ``None`` when the outbound fetch is off."""
    raw = os.environ.get(INDEX_URL_ENV)
    if raw is None or not raw.strip():
        return DEFAULT_INDEX_URL
    value = raw.strip()
    if value.lower() in _OFF_VALUES:
        return None
    return value


def _now_iso() -> str:
    return datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")


async def _http_fetch(url: str) -> bytes:
    """GET ``url`` and return the body. HTTPS only; redirects are not followed."""
    import httpx

    from hal0.http_client import async_client

    if urlparse(url).scheme != "https":
        raise CatalogError(f"refusing a non-HTTPS catalog URL: {url}")
    try:
        async with async_client(timeout=FETCH_TIMEOUT_S, follow_redirects=False) as client:
            resp = await client.get(url, headers={"Accept": "application/json"})
    except httpx.HTTPError as exc:
        raise CatalogError(f"could not reach {url}: {exc.__class__.__name__}") from exc
    if resp.status_code != 200:
        raise CatalogError(f"{url} answered HTTP {resp.status_code}")
    return resp.content


def _load_json(raw: bytes, *, limit: int, what: str) -> Any:
    if len(raw) > limit:
        raise CatalogError(f"{what} is larger than {limit} bytes")
    try:
        return json.loads(raw.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise CatalogError(f"{what} is not valid JSON") from exc


def _check_index(index: Any) -> list[dict[str, Any]]:
    """The index's rows, or :class:`CatalogError` when it is not one we read."""
    if not isinstance(index, dict) or index.get("kind") != INDEX_KIND:
        raise CatalogError(f"not a {INDEX_KIND} document")
    version = index.get("schema_version")
    if not isinstance(version, int) or isinstance(version, bool):
        raise CatalogError("index has no integer schema_version")
    if version > INDEX_SCHEMA_VERSION:
        raise CatalogError(
            f"index schema v{version} is newer than this release reads (v{INDEX_SCHEMA_VERSION})"
        )
    rows = index.get("addons")
    if not isinstance(rows, list):
        raise CatalogError("index has no 'addons' list")
    if len(rows) > MAX_ROWS:
        raise CatalogError(f"index lists more than {MAX_ROWS} addons")
    return [row for row in rows if isinstance(row, dict)]


async def _fetch_remote(url: str, fetch: Fetch) -> _Snapshot:
    index = _load_json(await fetch(url), limit=MAX_INDEX_BYTES, what="the index")
    rows = _check_index(index)
    gate = asyncio.Semaphore(_FETCH_CONCURRENCY)

    async def one(row: dict[str, Any]) -> tuple[str, dict[str, Any] | str] | None:
        name = row.get("name")
        if not isinstance(name, str) or not name:
            return None
        file = row.get("file")
        if not isinstance(file, str) or not _FILE_RE.fullmatch(file):
            return name, "the index names no usable file for it"
        async with gate:
            try:
                raw = await fetch(urljoin(url, file))
                envelope = _load_json(raw, limit=MAX_ENVELOPE_BYTES, what="the profile file")
            except CatalogError as exc:
                return name, str(exc)
        if not isinstance(envelope, dict):
            return name, "the profile file is not a JSON object"
        return name, envelope

    fetched = await asyncio.gather(*(one(row) for row in rows))
    return _Snapshot(
        source="remote",
        url=url,
        fetched_at=_now_iso(),
        index=index,
        envelopes=dict(item for item in fetched if item is not None),
    )


def _bundled(*, url: str | None, error: str | None) -> _Snapshot:
    envelopes = build_envelopes()
    return _Snapshot(
        source="bundled",
        url=url,
        fetched_at=_now_iso(),
        index=build_index(envelopes),
        envelopes=dict(envelopes),
        error=error,
    )


async def _snapshot(*, refresh: bool, fetch: Fetch | None) -> _Snapshot:
    url = index_url()
    if url is None:
        return _bundled(url=None, error=None)
    async with _cache_lock:
        cached = _cache.get(url)
        now = time.monotonic()
        if cached is not None:
            expires, fetched, snap = cached
            if refresh and now - fetched < MIN_REFRESH_INTERVAL_S:
                return snap
            if not refresh and now < expires:
                return snap
        try:
            snap = await _fetch_remote(url, fetch or _http_fetch)
            ttl = CACHE_TTL_S
        except CatalogError as exc:
            log.warning(
                "profile addon index unavailable; serving the bundled addons",
                extra={"event": "profile.addon_index_unavailable", "url": url, "reason": str(exc)},
            )
            snap = _bundled(url=url, error=str(exc))
            ttl = FAILURE_TTL_S
        done = time.monotonic()
        _cache[url] = (done + ttl, done, snap)
        return snap


def clear_cache() -> None:
    """Forget every fetched catalog (tests, and a changed index URL)."""
    _cache.clear()


_RELEASE_RE = re.compile(r"^\s*v?(\d+(?:\.\d+)*)")


def _release_tuple(version: str) -> tuple[int, ...] | None:
    match = _RELEASE_RE.match(version or "")
    if match is None:
        return None
    return tuple(int(part) for part in match.group(1).split("."))


def release_too_old(running: str, minimum: str) -> bool:
    """True when ``running`` is older than ``minimum``.

    Only the leading dotted number counts, so a nightly or release-candidate
    suffix compares as its base release. A source checkout reports ``0.0.0``
    and an unreadable version tells us nothing; neither is called too old.
    """
    have = _release_tuple(running)
    need = _release_tuple(minimum)
    if have is None or need is None or not any(have):
        return False
    width = max(len(have), len(need))
    return have + (0,) * (width - len(have)) < need + (0,) * (width - len(need))


def _row(
    row: dict[str, Any],
    envelope: dict[str, Any] | str | None,
    *,
    installed: set[str],
    running_version: str,
) -> dict[str, Any]:
    """One catalog row as the dashboard reads it.

    ``blocked`` is ``None`` for a row this box can import, otherwise a
    ``{"code", "message"}`` pair saying why it is listed but not offered.
    """
    name = str(row.get("name", ""))
    out: dict[str, Any] = {
        "name": name,
        "title": row.get("title") if isinstance(row.get("title"), str) else "",
        "description": row.get("description") if isinstance(row.get("description"), str) else "",
        "runtime_family": None,
        "runner": None,
        "runner_stripped": False,
        "checksum": row.get("checksum") if isinstance(row.get("checksum"), str) else "",
        "min_hal0_version": (
            row.get("min_hal0_version") if isinstance(row.get("min_hal0_version"), str) else ""
        ),
        "installed": name in installed,
        "profile": None,
        "envelope": None,
        "blocked": None,
    }
    for key, kind in _EXTRA_ROW_KEYS.items():
        value = row.get(key)
        if isinstance(value, kind) and not (kind is int and isinstance(value, bool)):
            out[key] = value

    def blocked(code: str, message: str) -> dict[str, Any]:
        out["blocked"] = {"code": code, "message": message}
        return out

    if envelope is None:
        return blocked("addons.envelope_missing", "the catalog has no profile file for this row")
    if isinstance(envelope, str):
        return blocked("addons.envelope_unavailable", envelope)
    try:
        env = parse_envelope(envelope)
    except Hal0Error as exc:
        return blocked(exc.code, exc.message)
    if env.schema_version > PROFILE_SCHEMA_VERSION_CURRENT:
        return blocked(
            "profiles.envelope_too_new",
            f"profile schema v{env.schema_version} is newer than this release reads "
            f"(v{PROFILE_SCHEMA_VERSION_CURRENT}); update hal0 to import it",
        )
    if env.name != name:
        return blocked(
            "addons.name_mismatch",
            f"the profile file is named {env.name!r}, the catalog row {name!r}",
        )
    # Both checks: the file must be intact, and it must be the file the index
    # was published with. A publisher that edits one and not the other is
    # reported, never papered over.
    if not verify_checksum(envelope) or envelope.get("checksum") != out["checksum"]:
        return blocked(
            "profiles.checksum_mismatch",
            "the profile file does not match the checksum the catalog lists for it",
        )
    family = runtime_family_of(name, env.profile)
    kept, stripped = envelope_runner_status(env.profile)
    out["runtime_family"] = family
    out["runner"] = kept or env.profile.runner
    out["runner_stripped"] = stripped
    out["profile"] = envelope["profile"]
    try:
        screen_profile_flags(env.profile.flags, runtime_family=family)
    except Hal0Error as exc:
        return blocked(exc.code, exc.message)
    if release_too_old(running_version, out["min_hal0_version"]):
        return blocked(
            "addons.needs_newer_hal0",
            f"needs hal0 {out['min_hal0_version']} or newer; this box runs {running_version}",
        )
    out["envelope"] = envelope
    return out


async def load_catalog(
    *,
    installed: set[str],
    refresh: bool = False,
    fetch: Fetch | None = None,
    running_version: str = __version__,
) -> dict[str, Any]:
    """The import catalog for this box.

    ``installed`` is the set of profile names already on the box (seeds
    included): a row whose name is taken is marked, and importing it needs a
    different name. ``refresh`` skips the cache. ``fetch`` replaces the HTTP
    GET in tests.
    """
    snap = await _snapshot(refresh=refresh, fetch=fetch)
    rows = [
        _row(
            row,
            snap.envelopes.get(str(row.get("name", ""))),
            installed=installed,
            running_version=running_version,
        )
        for row in _check_index(snap.index)
        if isinstance(row.get("name"), str) and row.get("name")
    ]
    rows.sort(key=lambda r: r["name"])
    return {
        "source": snap.source,
        "url": snap.url,
        "fetched_at": snap.fetched_at,
        "error": snap.error,
        "addons": rows,
    }
