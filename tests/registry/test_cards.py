"""registry.cards — HF model card (README) fetch + disk cache."""

from __future__ import annotations

import httpx
import pytest

from hal0.registry.cards import (
    CardNoSource,
    CardUnavailable,
    card_path,
    card_url,
    get_card,
    read_cached_card,
)

CARD = "# Qwen3\n\nA fine model.\n"


def _client(handler) -> httpx.AsyncClient:
    return httpx.AsyncClient(transport=httpx.MockTransport(handler), follow_redirects=True)


def _ok_handler(body: str = CARD, status: int = 200, calls: list | None = None):
    def handler(request: httpx.Request) -> httpx.Response:
        if calls is not None:
            calls.append(str(request.url))
        return httpx.Response(status, text=body)

    return handler


def test_card_url_is_raw_main():
    assert card_url("org/repo") == "https://huggingface.co/org/repo/raw/main/README.md"


async def test_fetch_persists_and_second_read_is_cached(tmp_hal0_home):
    calls: list[str] = []
    async with _client(_ok_handler(calls=calls)) as client:
        out = await get_card("m1", "org/repo", client=client)
        assert out["markdown"] == CARD
        assert out["cached"] is False
        assert card_path("m1").read_text() == CARD

        again = await get_card("m1", "org/repo", client=client)
    assert again["cached"] is True
    assert again["markdown"] == CARD
    assert len(calls) == 1  # second read never hit HF


async def test_refresh_refetches(tmp_hal0_home):
    calls: list[str] = []
    async with _client(_ok_handler(calls=calls)) as client:
        await get_card("m1", "org/repo", client=client)
        out = await get_card("m1", "org/repo", refresh=True, client=client)
    assert len(calls) == 2
    assert out["cached"] is False


async def test_refresh_falls_back_to_stale_cache_when_hf_down(tmp_hal0_home):
    async with _client(_ok_handler()) as client:
        await get_card("m1", "org/repo", client=client)

    def down(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("boom", request=request)

    async with _client(down) as client:
        out = await get_card("m1", "org/repo", refresh=True, client=client)
    assert out["markdown"] == CARD
    assert out["stale"] is True


async def test_no_cache_and_hf_down_raises(tmp_hal0_home):
    def down(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("boom", request=request)

    async with _client(down) as client:
        with pytest.raises(CardUnavailable):
            await get_card("m1", "org/repo", client=client)


async def test_missing_readme_raises_not_found(tmp_hal0_home):
    async with _client(_ok_handler(status=404)) as client:
        with pytest.raises(CardUnavailable) as exc:
            await get_card("m1", "org/repo", client=client)
    assert exc.value.code == "model.card_not_found"


async def test_no_hf_repo_raises_no_source(tmp_hal0_home):
    with pytest.raises(CardNoSource):
        await get_card("m1", "")


async def test_oversized_card_is_truncated(tmp_hal0_home):
    big = "x" * (600 * 1024)
    async with _client(_ok_handler(body=big)) as client:
        out = await get_card("m1", "org/repo", client=client)
    assert len(out["markdown"]) < len(big)
    assert "truncated by hal0" in out["markdown"]


async def test_read_cached_card_absent(tmp_hal0_home):
    assert read_cached_card("nope") is None
