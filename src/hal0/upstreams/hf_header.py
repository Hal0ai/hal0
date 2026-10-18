"""Read a GGUF header straight from the Hub by HTTP range (ADR-0031 §2).

A fit check needs two headers (target + drafter), tens of MB against a
download of tens of GB. The leading bytes are fetched in growing ranges until
:func:`hal0.registry.gguf_header.read_gguf_header_bytes` parses the KV block
or the byte cap is reached.
"""

from __future__ import annotations

import os
from typing import Any

import httpx

from hal0.registry.gguf_header import read_gguf_header_bytes
from hal0.registry.pull import hf_download_url

_TIMEOUT_S = 30.0


def _auth_headers(token: str | None) -> dict[str, str]:
    tok = token or os.environ.get("HF_TOKEN") or os.environ.get("HUGGING_FACE_HUB_TOKEN")
    return {"Authorization": f"Bearer {tok}"} if tok else {}


async def fetch_gguf_header(
    repo: str,
    filename: str,
    *,
    revision: str = "main",
    token: str | None = None,
    client: httpx.AsyncClient | None = None,
    step_bytes: int = 4 * 2**20,
    max_bytes: int = 16 * 2**20,
) -> dict[str, Any] | None:
    """Parsed GGUF header of ``repo``/``filename``, or ``None`` if unreadable.

    ``None`` on any status other than 200/206 (a 416 included), a transport
    error, or when ``max_bytes`` is read without the KV block completing.
    """
    url = hf_download_url(repo, filename, revision)
    headers = _auth_headers(token)
    owned = client is None
    http = client or httpx.AsyncClient(timeout=httpx.Timeout(_TIMEOUT_S), follow_redirects=True)
    try:
        n = step_bytes
        while True:
            n = min(n, max_bytes)
            try:
                async with http.stream(
                    "GET",
                    url,
                    headers={**headers, "Range": f"bytes=0-{n - 1}"},
                    follow_redirects=True,
                ) as resp:
                    if resp.status_code not in (200, 206):
                        return None
                    # A 200 means the server ignored the range and is sending the
                    # whole file: read up to the cap once, never re-request.
                    ranged = resp.status_code == 206
                    want = n if ranged else max_bytes
                    body = bytearray()
                    async for chunk in resp.aiter_bytes():
                        body += chunk
                        if len(body) >= want:
                            break
            except httpx.HTTPError:
                return None
            header = read_gguf_header_bytes(bytes(body[:want]), require_complete=True)
            if header is not None:
                return header
            # Unparsed and no more to ask for: unranged, short file, or at the cap.
            if not ranged or len(body) < n or n >= max_bytes:
                return None
            n += step_bytes
    finally:
        if owned:
            await http.aclose()
