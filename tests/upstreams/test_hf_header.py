"""Hub GGUF header reader: ranged GETs that grow until the KV block parses."""

from __future__ import annotations

from pathlib import Path

import httpx
import pytest

from hal0.upstreams.hf_header import fetch_gguf_header
from tests.registry.gguf_fixtures import write_gguf

_MB = 2**20


def _padded_header(tmp_path: Path, *, kv_end_at: int, total: int) -> bytes:
    """A real GGUF whose long string KV pushes the KV block end to ``kv_end_at``."""
    probe = write_gguf(tmp_path / "probe.gguf", "llama", strings={"x.pad": ""}).read_bytes()
    pad = kv_end_at - len(probe)
    raw = write_gguf(tmp_path / "f.gguf", "llama", strings={"x.pad": "a" * pad}).read_bytes()
    assert len(raw) == kv_end_at
    return raw + b"\0" * (total - len(raw))


def _range_handler(data: bytes, seen: list[httpx.Request], *, honour_range: bool = True):
    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        spec = request.headers["range"].removeprefix("bytes=")
        lo, hi = (int(p) for p in spec.split("-"))
        if not honour_range:
            return httpx.Response(200, content=data)
        return httpx.Response(206, content=data[lo : hi + 1])

    return handler


async def _fetch(handler, **kw):
    async with httpx.AsyncClient(
        transport=httpx.MockTransport(handler), follow_redirects=True
    ) as c:
        return await fetch_gguf_header("org/repo", "m.gguf", client=c, **kw)


@pytest.mark.asyncio
async def test_fetch_header_grows_range_until_parse(tmp_path: Path) -> None:
    data = _padded_header(tmp_path, kv_end_at=5 * _MB, total=6 * _MB)
    seen: list[httpx.Request] = []
    header = await _fetch(_range_handler(data, seen))
    assert header is not None and header["general.architecture"] == "llama"
    assert [r.headers["range"] for r in seen] == ["bytes=0-4194303", "bytes=0-8388607"]
    assert str(seen[0].url) == "https://huggingface.co/org/repo/resolve/main/m.gguf"


@pytest.mark.asyncio
async def test_fetch_header_stops_at_cap_returns_none(tmp_path: Path) -> None:
    data = _padded_header(tmp_path, kv_end_at=5 * _MB, total=6 * _MB)
    seen: list[httpx.Request] = []
    header = await _fetch(_range_handler(data, seen), step_bytes=_MB, max_bytes=2 * _MB)
    assert header is None
    assert [r.headers["range"] for r in seen] == ["bytes=0-1048575", "bytes=0-2097151"]


@pytest.mark.asyncio
async def test_fetch_header_403_returns_none() -> None:
    header = await _fetch(lambda r: httpx.Response(403))
    assert header is None


@pytest.mark.asyncio
async def test_fetch_header_416_returns_none() -> None:
    assert await _fetch(lambda r: httpx.Response(416)) is None


@pytest.mark.asyncio
async def test_fetch_header_network_error_returns_none() -> None:
    def boom(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("down")

    assert await _fetch(boom) is None


@pytest.mark.asyncio
async def test_fetch_header_ignored_range_uses_first_n_bytes(tmp_path: Path) -> None:
    data = _padded_header(tmp_path, kv_end_at=5 * _MB, total=6 * _MB)
    seen: list[httpx.Request] = []
    header = await _fetch(_range_handler(data, seen, honour_range=False))
    assert header is not None
    assert len(seen) == 1  # a 200 is never retried with a larger range


@pytest.mark.asyncio
async def test_fetch_header_sends_bearer(tmp_path: Path) -> None:
    data = write_gguf(tmp_path / "s.gguf", "llama").read_bytes()
    seen: list[httpx.Request] = []
    await _fetch(_range_handler(data, seen), token="hf_secret")
    assert seen[0].headers["authorization"] == "Bearer hf_secret"


@pytest.mark.asyncio
async def test_fetch_header_env_token(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("HF_TOKEN", "hf_env")
    data = write_gguf(tmp_path / "s.gguf", "llama").read_bytes()
    seen: list[httpx.Request] = []
    await _fetch(_range_handler(data, seen))
    assert seen[0].headers["authorization"] == "Bearer hf_env"


@pytest.mark.asyncio
async def test_fetch_header_follows_redirect(tmp_path: Path) -> None:
    data = write_gguf(tmp_path / "s.gguf", "llama").read_bytes()

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.host == "huggingface.co":
            return httpx.Response(302, headers={"location": "https://cdn.example/blob"})
        return httpx.Response(206, content=data)

    assert (await _fetch(handler)) is not None


@pytest.mark.asyncio
async def test_fetch_header_ignored_range_reads_at_most_cap() -> None:
    """A 200 for a huge file must not be buffered: pull <= max_bytes + one chunk."""
    chunk = 64 * 1024
    pulled = 0

    async def body():
        nonlocal pulled
        for _ in range(100_000):  # ~6 GB if fully consumed
            pulled += chunk
            yield b"\0" * chunk

    reqs = 0

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal reqs
        reqs += 1
        return httpx.Response(200, content=body())

    header = await _fetch(handler, step_bytes=_MB, max_bytes=2 * _MB)
    assert header is None
    assert reqs == 1
    assert pulled <= 2 * _MB + chunk


@pytest.mark.asyncio
async def test_fetch_header_short_file_stops_after_one_request(tmp_path: Path) -> None:
    data = write_gguf(tmp_path / "s.gguf", "llama").read_bytes()[:-6]  # whole file, cut KV
    seen: list[httpx.Request] = []
    assert await _fetch(_range_handler(data, seen)) is None
    assert len(seen) == 1
