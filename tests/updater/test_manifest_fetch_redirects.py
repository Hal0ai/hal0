"""Regression tests for GitHub-#1688: manifest/bundle fetch must follow 302s.

``HAL0_RELEASES_URL`` is documented as tolerating a GitHub release-asset URL
(the interim hosting until releases.hal0.dev exists — see
scripts/release-prototype/RELEASE_PIPELINE_NOTES.md). GitHub redirects asset
downloads to ``objects.githubusercontent.com`` with an HTTP 302, so every
client that fetches manifest or sibling-bundle bytes over http(s) must follow
redirects. These tests use an ``httpx.MockTransport`` so no real network call
is made.
"""

from __future__ import annotations

import functools
import json
from pathlib import Path
from typing import Any

import httpx
import pytest

import hal0.updater.updater as updater_module

pytestmark = pytest.mark.usefixtures("tmp_hal0_home")


def _patch_async_client(monkeypatch: pytest.MonkeyPatch, transport: httpx.MockTransport) -> None:
    """Force every ``httpx.AsyncClient(...)`` construction onto ``transport``.

    Both fetch paths do a function-local ``import httpx`` before constructing
    the client, but that binds the same module object already patched here,
    so this covers the manifest client, the bundle/tarball ``_download``
    client, or any future caller uniformly.
    """
    monkeypatch.setattr(
        httpx, "AsyncClient", functools.partial(httpx.AsyncClient, transport=transport)
    )


def _redirect_then_serve(final_url: str, body: bytes, *, content_type: str = "application/json"):
    """MockTransport handler: 302 the first hop, 200 + body on the real host."""

    def handler(request: httpx.Request) -> httpx.Response:
        if str(request.url) == final_url:
            return httpx.Response(200, content=body, headers={"content-type": content_type})
        return httpx.Response(302, headers={"location": final_url})

    return handler


def test_fetch_release_manifest_bytes_follows_redirect(monkeypatch: pytest.MonkeyPatch) -> None:
    """A 302 from the asset URL to objects.githubusercontent.com must be followed.

    Before the fix, ``_fetch_release_manifest_bytes`` builds its
    ``httpx.AsyncClient`` without ``follow_redirects=True`` and raises
    ``OSError: release manifest fetch returned HTTP 302`` instead of
    returning the manifest bytes — this is the exact failure from the issue.
    """
    manifest_bytes = json.dumps({"hello": "world"}).encode("utf-8")
    asset_url = (
        "https://github.com/Hal0ai/hal0/releases/download/v1.0.0-rc.2/preview.json?channel=preview"
    )
    redirect_target = "https://objects.githubusercontent.com/real-preview.json"
    transport = httpx.MockTransport(_redirect_then_serve(redirect_target, manifest_bytes))
    _patch_async_client(monkeypatch, transport)
    monkeypatch.setenv(
        "HAL0_RELEASES_URL",
        "https://github.com/Hal0ai/hal0/releases/download/v1.0.0-rc.2/preview.json",
    )

    assert updater_module.releases_url("preview") == asset_url

    import asyncio

    raw = asyncio.run(updater_module._fetch_release_manifest_bytes("preview"))
    assert raw == manifest_bytes


def test_fetch_verified_release_manifest_follows_redirects_for_manifest_and_bundle(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Both the manifest fetch and its sibling ``.bundle`` fetch must follow 302s.

    Also asserts the bytes handed to cosign for verification are exactly the
    bytes returned by the (redirected) manifest fetch — i.e. redirect-
    following must not change *what* gets verified.
    """
    payload: dict[str, Any] = {
        "_schema": "hal0.releases.v1",
        "version": "1.0.0-rc.1",
        "channel": "preview",
        "release_kind": "preview",
        "prerelease_stage": "rc",
        "url": "https://example.test/hal0.tar.gz",
        "bundle_url": "https://example.test/hal0.tar.gz.bundle",
        "digest_sha256": "0" * 64,
        "signer_identity": (
            r"^https://github\.com/(Hal0ai|hal0ai)/hal0/"
            r"\.github/workflows/release\.yml@refs/tags/v1\.0\.0-rc\.1$"
        ),
        "signer_issuer": "https://token.actions.githubusercontent.com",
    }
    manifest_bytes = json.dumps(payload).encode("utf-8")
    bundle_bytes = b"sigstore-bundle-placeholder\n"

    manifest_asset = "https://github.com/Hal0ai/hal0/releases/download/v1.0.0-rc.2/preview.json"
    manifest_asset_with_channel = f"{manifest_asset}?channel=preview"
    manifest_redirect = "https://objects.githubusercontent.com/real-preview.json"
    bundle_asset_with_channel = f"{manifest_asset}.bundle?channel=preview"
    bundle_redirect = "https://objects.githubusercontent.com/real-preview.json.bundle"

    def handler(request: httpx.Request) -> httpx.Response:
        url = str(request.url)
        if url == manifest_asset_with_channel:
            return httpx.Response(302, headers={"location": manifest_redirect})
        if url == manifest_redirect:
            return httpx.Response(200, content=manifest_bytes)
        if url == bundle_asset_with_channel:
            return httpx.Response(302, headers={"location": bundle_redirect})
        if url == bundle_redirect:
            return httpx.Response(200, content=bundle_bytes)
        return httpx.Response(404)

    transport = httpx.MockTransport(handler)
    _patch_async_client(monkeypatch, transport)
    monkeypatch.setenv("HAL0_RELEASES_URL", manifest_asset)

    captured: dict[str, bytes] = {}

    def fake_verify_cosign(tarball, bundle, *, identity_regexp, issuer, job_id=None):
        # ``tarball`` here is actually the manifest path for this call site.
        captured["manifest_bytes"] = Path(tarball).read_bytes()
        captured["bundle_bytes"] = Path(bundle).read_bytes()

    monkeypatch.setattr(updater_module, "_verify_cosign", fake_verify_cosign)

    import asyncio

    raw, _manifest, url = asyncio.run(updater_module._fetch_verified_release_manifest("preview"))

    assert url == manifest_asset_with_channel
    assert raw == payload
    assert captured["manifest_bytes"] == manifest_bytes
    assert captured["bundle_bytes"] == bundle_bytes
