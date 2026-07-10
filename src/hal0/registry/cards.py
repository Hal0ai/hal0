"""Model cards — download + local cache of HF README.md per registry model.

The dashboard's model detail pane wants the upstream model card (README)
for reference without leaving hal0. We fetch it once from
``https://huggingface.co/<repo>/raw/main/README.md`` (READMEs are plain
git blobs — ``raw`` works, no LFS dance) and persist it under
``<var_lib>/model-cards/<sanitised-id>.md`` so subsequent reads are
offline and instant. A ``refresh`` flag re-fetches; the cached copy is
the fallback when huggingface.co is unreachable.

Size is capped: a model card is documentation, and anything past the cap
is truncated with a marker rather than ballooning the state dir.
"""

from __future__ import annotations

import logging
import time
from pathlib import Path
from typing import Any

import httpx

from hal0.config import paths
from hal0.errors import Hal0Error
from hal0.registry.pull import _sanitise_id

log = logging.getLogger(__name__)

_CARD_TIMEOUT_S: float = 10.0
# Hard cap on stored/served markdown. HF cards are typically a few KB;
# 512 KiB tolerates image-heavy monsters without letting one repo fill
# the state dir.
_CARD_MAX_BYTES: int = 512 * 1024
_TRUNCATION_MARKER = "\n\n…\n*(model card truncated by hal0)*\n"


class CardUnavailable(Hal0Error):
    """No local cache and huggingface.co could not serve the card."""

    code = "model.card_unavailable"
    status = 502


class CardNoSource(Hal0Error):
    """The registry row has no hf_repo — nowhere to fetch a card from."""

    code = "model.card_no_source"
    status = 404


def cards_dir() -> Path:
    """Return ``<var_lib>/model-cards`` (HAL0_HOME-aware via config paths)."""
    return paths.var_lib() / "model-cards"


def card_path(model_id: str) -> Path:
    """On-disk cache location of ``model_id``'s card."""
    return cards_dir() / f"{_sanitise_id(model_id)}.md"


def read_cached_card(model_id: str) -> tuple[str, float] | None:
    """Return ``(markdown, fetched_at)`` from the disk cache, or None."""
    p = card_path(model_id)
    try:
        text = p.read_text(encoding="utf-8")
        return text, p.stat().st_mtime
    except OSError:
        return None


def _persist_card(model_id: str, markdown: str) -> None:
    """Best-effort write of the card cache — a failure never breaks the read."""
    p = card_path(model_id)
    try:
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(markdown, encoding="utf-8")
    except OSError as exc:
        log.warning("model.card_persist_failed model_id=%s error=%s", model_id, exc)


def card_url(hf_repo: str, revision: str = "main") -> str:
    """README location — ``raw`` (not ``resolve``): cards are plain git blobs."""
    return f"https://huggingface.co/{hf_repo.strip('/')}/raw/{revision}/README.md"


async def fetch_card(
    hf_repo: str,
    *,
    hf_token: str | None = None,
    client: httpx.AsyncClient | None = None,
) -> str:
    """Download the README for ``hf_repo``; raises :class:`CardUnavailable`.

    Streams the body and stops reading at the byte cap, so one
    image-base64-laden card can neither balloon the on-disk cache nor
    get fully buffered in memory first. The cap is byte-accurate (a
    CJK-heavy README truncates at the same stored size as an ASCII one);
    a multi-byte character split at the cut is dropped by the tolerant
    decode.
    """
    headers: dict[str, str] = {"User-Agent": "hal0/model-card"}
    if hf_token:
        headers["Authorization"] = f"Bearer {hf_token}"
    owns_client = client is None
    if client is None:
        client = httpx.AsyncClient(
            timeout=httpx.Timeout(_CARD_TIMEOUT_S),
            follow_redirects=True,
        )
    chunks: list[bytes] = []
    total = 0
    truncated = False
    try:
        async with client.stream("GET", card_url(hf_repo), headers=headers) as resp:
            if resp.status_code == 404:
                raise CardUnavailable(
                    f"hugging face repo {hf_repo!r} has no README.md at main",
                    code="model.card_not_found",
                    details={"repo": hf_repo},
                )
            if resp.status_code >= 400:
                raise CardUnavailable(
                    f"hugging face returned HTTP {resp.status_code} for {hf_repo!r} model card",
                    details={"repo": hf_repo, "status": resp.status_code},
                )
            async for chunk in resp.aiter_bytes():
                if not chunk:
                    continue
                if total >= _CARD_MAX_BYTES:
                    # Cap already reached and the stream has more — mark and
                    # stop pulling bytes off the wire.
                    truncated = True
                    break
                room = _CARD_MAX_BYTES - total
                if len(chunk) > room:
                    chunks.append(chunk[:room])
                    total = _CARD_MAX_BYTES
                    truncated = True
                    break
                chunks.append(chunk)
                total += len(chunk)
    except httpx.HTTPError as exc:
        raise CardUnavailable(
            f"failed to reach huggingface.co for {hf_repo!r} model card: {exc.__class__.__name__}",
            details={"repo": hf_repo, "error": str(exc)},
        ) from exc
    finally:
        if owns_client:
            await client.aclose()
    text = b"".join(chunks).decode("utf-8", errors="ignore")
    if truncated:
        text += _TRUNCATION_MARKER
    return text


async def get_card(
    model_id: str,
    hf_repo: str,
    *,
    refresh: bool = False,
    hf_token: str | None = None,
    client: httpx.AsyncClient | None = None,
) -> dict[str, Any]:
    """Return the card for ``model_id`` — disk cache first, HF on miss.

    ``refresh`` forces a re-fetch; if that fails but a cached copy
    exists, the cache is served with ``stale=True`` rather than erroring
    — the reference use-case prefers an old card over none.
    """
    if not (hf_repo or "").strip():
        raise CardNoSource(
            f"model {model_id!r} has no hf_repo — no model card source",
            details={"model_id": model_id},
        )
    cached = read_cached_card(model_id)
    if cached is not None and not refresh:
        markdown, fetched_at = cached
        return {
            "model_id": model_id,
            "hf_repo": hf_repo,
            "markdown": markdown,
            "cached": True,
            "stale": False,
            "fetched_at": fetched_at,
        }
    try:
        markdown = await fetch_card(hf_repo, hf_token=hf_token, client=client)
    except CardUnavailable:
        if cached is not None:
            markdown, fetched_at = cached
            return {
                "model_id": model_id,
                "hf_repo": hf_repo,
                "markdown": markdown,
                "cached": True,
                "stale": True,
                "fetched_at": fetched_at,
            }
        raise
    _persist_card(model_id, markdown)
    return {
        "model_id": model_id,
        "hf_repo": hf_repo,
        "markdown": markdown,
        "cached": False,
        "stale": False,
        "fetched_at": time.time(),
    }


def drop_cached_card(model_id: str) -> None:
    """Remove the cached card (model delete cascade). Best-effort."""
    import contextlib

    with contextlib.suppress(OSError):
        card_path(model_id).unlink(missing_ok=True)


__all__ = [
    "CardNoSource",
    "CardUnavailable",
    "card_path",
    "card_url",
    "cards_dir",
    "drop_cached_card",
    "fetch_card",
    "get_card",
    "read_cached_card",
]
