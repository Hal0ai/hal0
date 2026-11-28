"""The dashboard's import catalog (``hal0.profiles.addon_catalog``).

No test here opens a socket: the published index is a dict of URL -> bytes
handed to ``load_catalog`` as its ``fetch``.
"""

from __future__ import annotations

import copy
import json
from collections.abc import Iterator
from typing import Any

import pytest

from hal0.profiles import addon_catalog
from hal0.profiles.addon_catalog import CatalogError, load_catalog, release_too_old
from hal0.profiles.addons import ADDON_PROFILES, build_envelopes, build_index
from hal0.profiles.portable import _checksum

URL = "https://catalog.example/profiles/index.json"
BASE = "https://catalog.example/profiles/"


@pytest.fixture(autouse=True)
def _isolated(monkeypatch: pytest.MonkeyPatch) -> Iterator[None]:
    monkeypatch.setenv(addon_catalog.INDEX_URL_ENV, URL)
    addon_catalog.clear_cache()
    yield
    addon_catalog.clear_cache()


class Site:
    """A published catalog: the index and its envelope files, by URL."""

    def __init__(self) -> None:
        self.envelopes: dict[str, dict[str, Any]] = copy.deepcopy(build_envelopes())
        self.index: dict[str, Any] = build_index(self.envelopes)
        self.raw: dict[str, bytes] = {}
        self.down = False
        self.calls: list[str] = []

    def row(self, name: str) -> dict[str, Any]:
        return next(r for r in self.index["addons"] if r["name"] == name)

    async def fetch(self, url: str) -> bytes:
        self.calls.append(url)
        if self.down:
            raise CatalogError(f"could not reach {url}: ConnectError")
        if url in self.raw:
            return self.raw[url]
        if url == URL:
            return json.dumps(self.index).encode()
        name = url.removeprefix(BASE).removesuffix(".hal0profile.json")
        if url.startswith(BASE) and name in self.envelopes:
            return json.dumps(self.envelopes[name]).encode()
        raise CatalogError(f"{url} answered HTTP 404")


async def _load(site: Site, **kwargs: Any) -> dict[str, Any]:
    kwargs.setdefault("installed", set())
    kwargs.setdefault("running_version", "1.9.0")
    return await load_catalog(fetch=site.fetch, **kwargs)


def _by_name(catalog: dict[str, Any]) -> dict[str, dict[str, Any]]:
    return {row["name"]: row for row in catalog["addons"]}


async def test_published_index_becomes_importable_rows() -> None:
    site = Site()
    catalog = await _load(site)

    assert catalog["source"] == "remote"
    assert catalog["url"] == URL
    assert catalog["error"] is None
    rows = _by_name(catalog)
    assert sorted(rows) == sorted(ADDON_PROFILES)
    dense = rows["dense"]
    assert dense["blocked"] is None
    assert dense["envelope"] == site.envelopes["dense"]
    assert dense["profile"]["ctx_size"] == 131072
    assert dense["runtime_family"] == "llama-server"
    assert dense["installed"] is False
    assert dense["title"] == "Generic dense workload"


async def test_unreachable_index_falls_back_to_the_bundled_addons() -> None:
    site = Site()
    site.down = True
    catalog = await _load(site)

    assert catalog["source"] == "bundled"
    assert catalog["url"] == URL
    assert "could not reach" in catalog["error"]
    rows = _by_name(catalog)
    assert sorted(rows) == sorted(ADDON_PROFILES)
    assert all(row["blocked"] is None and row["envelope"] for row in rows.values())


async def test_fetch_switched_off_serves_bundled_without_a_request(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv(addon_catalog.INDEX_URL_ENV, "off")
    site = Site()
    catalog = await _load(site)

    assert site.calls == []
    assert catalog["source"] == "bundled"
    assert catalog["url"] is None
    assert catalog["error"] is None


async def test_fetched_catalog_is_cached_until_refresh() -> None:
    site = Site()
    await _load(site)
    first = len(site.calls)
    await _load(site)
    assert len(site.calls) == first

    await _load(site, refresh=True)
    assert len(site.calls) == 2 * first


async def test_failure_is_cached_briefly_then_retried(monkeypatch: pytest.MonkeyPatch) -> None:
    site = Site()
    site.down = True
    clock = [1000.0]
    monkeypatch.setattr(addon_catalog.time, "monotonic", lambda: clock[0])

    assert (await _load(site))["source"] == "bundled"
    calls = len(site.calls)
    site.down = False
    assert (await _load(site))["source"] == "bundled"
    assert len(site.calls) == calls

    clock[0] += addon_catalog.FAILURE_TTL_S + 1
    assert (await _load(site))["source"] == "remote"


async def test_installed_name_is_marked() -> None:
    rows = _by_name(await _load(Site(), installed={"dense", "agent"}))
    assert rows["dense"]["installed"] is True
    assert rows["moe"]["installed"] is False


async def test_tampered_envelope_is_listed_but_not_offered() -> None:
    site = Site()
    site.envelopes["dense"]["profile"]["flags"] = "-fa auto --temp 2"
    row = _by_name(await _load(site))["dense"]

    assert row["blocked"]["code"] == "profiles.checksum_mismatch"
    assert row["envelope"] is None


async def test_envelope_that_differs_from_the_index_checksum_is_blocked() -> None:
    """Internally consistent file, but not the one the index was published with."""
    site = Site()
    body = site.envelopes["dense"]["profile"]
    body["ctx_size"] = 65536
    site.envelopes["dense"]["checksum"] = _checksum(body)
    row = _by_name(await _load(site))["dense"]

    assert row["blocked"]["code"] == "profiles.checksum_mismatch"
    assert row["envelope"] is None


async def test_envelope_carrying_a_refused_flag_is_blocked() -> None:
    site = Site()
    body = site.envelopes["moe"]["profile"]
    body["flags"] = "-ngl 99 -fa auto"
    site.envelopes["moe"]["checksum"] = _checksum(body)
    site.row("moe")["checksum"] = site.envelopes["moe"]["checksum"]
    row = _by_name(await _load(site))["moe"]

    assert row["blocked"]["code"] == "slot.hardware_flag_denied"
    assert row["envelope"] is None
    assert row["profile"]["flags"] == "-ngl 99 -fa auto"


async def test_row_needing_a_newer_release_is_blocked() -> None:
    site = Site()
    site.row("thinking")["min_hal0_version"] = "9.0.0"
    row = _by_name(await _load(site, running_version="1.9.0"))["thinking"]

    assert row["blocked"]["code"] == "addons.needs_newer_hal0"
    assert "9.0.0" in row["blocked"]["message"]
    assert row["envelope"] is None


async def test_missing_envelope_file_blocks_only_that_row() -> None:
    site = Site()
    del site.envelopes["moe"]
    rows = _by_name(await _load(site))

    assert rows["moe"]["blocked"]["code"] == "addons.envelope_unavailable"
    assert rows["dense"]["blocked"] is None


async def test_envelope_named_differently_from_its_row_is_blocked() -> None:
    site = Site()
    site.envelopes["moe"]["name"] = "something-else"
    row = _by_name(await _load(site))["moe"]
    assert row["blocked"]["code"] == "addons.name_mismatch"


@pytest.mark.parametrize(
    "file",
    [
        "https://evil.example/dense.hal0profile.json",
        "//evil.example/dense.hal0profile.json",
        "../dense.hal0profile.json",
        "sub/dense.hal0profile.json",
        "dense.json",
    ],
)
async def test_index_cannot_point_the_box_at_another_path_or_origin(file: str) -> None:
    site = Site()
    site.row("dense")["file"] = file
    row = _by_name(await _load(site))["dense"]

    assert row["blocked"]["code"] == "addons.envelope_unavailable"
    assert all(call.startswith(BASE) for call in site.calls)
    assert BASE + "dense.hal0profile.json" not in site.calls


@pytest.mark.parametrize(
    "index",
    [
        b"not json",
        b'{"kind": "something.else", "schema_version": 1, "addons": []}',
        b'{"kind": "hal0.addon-index", "schema_version": 2, "addons": []}',
        b'{"kind": "hal0.addon-index", "schema_version": 1}',
        b"x" * (addon_catalog.MAX_INDEX_BYTES + 1),
    ],
)
async def test_unusable_index_falls_back_with_the_reason(index: bytes) -> None:
    site = Site()
    site.raw[URL] = index
    catalog = await _load(site)

    assert catalog["source"] == "bundled"
    assert catalog["error"]


async def test_publisher_extras_pass_through_typed_and_unknown_keys_drop() -> None:
    site = Site()
    site.row("dense").update(
        {
            "category": "chat",
            "summary": "A dense tune.",
            "version": 3,
            "first_party": True,
            "author": 7,
            "tracking": "x",
        }
    )
    row = _by_name(await _load(site))["dense"]

    assert row["category"] == "chat"
    assert row["summary"] == "A dense tune."
    assert row["version"] == 3
    assert row["first_party"] is True
    assert "author" not in row
    assert "tracking" not in row


async def test_non_https_index_url_is_refused_without_a_request(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv(addon_catalog.INDEX_URL_ENV, "http://catalog.example/index.json")
    catalog = await load_catalog(installed=set())

    assert catalog["source"] == "bundled"
    assert "non-HTTPS" in catalog["error"]


@pytest.mark.parametrize(
    ("running", "minimum", "expected"),
    [
        ("1.8.3", "1.5.0", False),
        ("1.4.9", "1.5.0", True),
        ("1.5.0", "1.5.0", False),
        ("1.5", "1.5.0", False),
        ("1.9.0-nightly.20261128", "1.9.0", False),
        ("1.8.2rc1", "1.8.3", True),
        ("0.0.0+source", "1.5.0", False),
        ("garbage", "1.5.0", False),
        ("1.8.3", "", False),
    ],
)
def test_release_too_old(running: str, minimum: str, expected: bool) -> None:
    assert release_too_old(running, minimum) is expected
