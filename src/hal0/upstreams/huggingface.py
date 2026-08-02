"""Unified HuggingFace Hub HTTP client.

Consolidates what used to be two independent, ad-hoc HF clients:

* ``api/routes/models.py``'s repo tree + metadata fetch, powering the
  Add-by-HF "Inspect" modal (:func:`fetch_repo`).
* ``api/routes/hf.py``'s free-text search, powering the dashboard's
  "Search HF" panel (:func:`search_models`).

Both hit ``https://huggingface.co/api/models...`` with the same
``HF_TOKEN``/``HUGGING_FACE_HUB_TOKEN`` bearer lookup and the same
never-500-the-caller failure policy — they'd just drifted into separate
modules with their own copies of the header-injection and client-setup
boilerplate. This module owns that shared transport plumbing plus the
per-endpoint response normalizer.

Route-level concerns (TTL caches, request validation, HTTP status mapping
to the dashboard envelope) deliberately stay with the callers — this
module is the transport + normalizer boundary only, not a route.
"""

from __future__ import annotations

import asyncio
import os
from typing import Any

import httpx
import structlog

from hal0.errors import Hal0Error, NotFound

logger = structlog.get_logger(__name__)

_HF_MODELS_URL = "https://huggingface.co/api/models"

# ── Search (dashboard "Search HF" panel) knobs ──────────────────────────
# Hard cap on rows returned to the dashboard. Keeps the wire payload
# bounded so a wide search doesn't drag the renderer down; HF accepts
# ``limit=`` up to 100 so we ask for a little more than we surface in
# case HF swaps ordering (e.g. trending) between requests.
HF_SEARCH_RESULT_CAP = 20
_HF_SEARCH_UPSTREAM_LIMIT = 30
_HF_SEARCH_TIMEOUT_S = 5.0

# ── Repo inspect (Add-by-HF "Inspect" modal) knobs ──────────────────────
_INSPECT_TIMEOUT_SECONDS = 8.0
_INSPECT_GGUF_SUFFIX = ".gguf"
# mmproj sidecars are often uploaded with a bare ``.mmproj`` extension
# (e.g. ``mmproj-Foo-F32.mmproj``) rather than ``…mmproj….gguf``.  The local
# directory scanner (:func:`hal0.registry.discover._is_mmproj_sidecar`) matches
# those by name regardless of extension, so the HF inspect path has to admit
# them too — otherwise the Add-by-HF modal's vision picker shows "no mmproj
# files in repo" and a vision pull silently ships without a projector.
_INSPECT_MMPROJ_SUFFIX = ".mmproj"
_INSPECT_FLM_TOKENIZERS = ("tokenizer.json", "tokenizer.model")


class HFUpstreamError(Hal0Error):
    """502 — fetching huggingface.co failed (network, 5xx, or unparseable)."""

    code = "hf.unreachable"
    status = 502


def _hf_headers() -> dict[str, str]:
    """Shared request headers, forwarding ``HF_TOKEN``/``HUGGING_FACE_HUB_TOKEN`` when set."""
    headers: dict[str, str] = {"Accept": "application/json"}
    hf_token = os.environ.get("HF_TOKEN") or os.environ.get("HUGGING_FACE_HUB_TOKEN")
    if hf_token:
        headers["Authorization"] = f"Bearer {hf_token}"
    return headers


def normalise_repo_slug(value: str) -> str:
    """Reduce a HF repo input to ``org/name``.

    Accepts the canonical ``org/name`` slug and a full
    ``https://huggingface.co/org/name[/...]`` URL — both are surfaced
    in the dashboard's Add-by-HF modal. Trims trailing slashes and
    drops the ``/tree/<rev>`` / ``/blob/<rev>/...`` suffixes that the
    HF UI tends to copy along with the slug.
    """
    raw = (value or "").strip()
    if not raw:
        return ""
    # Strip protocol + host so we can normalise URL + slug uniformly.
    for prefix in ("https://huggingface.co/", "http://huggingface.co/", "huggingface.co/"):
        if raw.startswith(prefix):
            raw = raw[len(prefix) :]
            break
    raw = raw.strip("/")
    # Drop /tree/<rev> or /blob/<rev>/<path> if the user pasted a deep link.
    parts = raw.split("/")
    repo = f"{parts[0]}/{parts[1]}" if len(parts) >= 2 else raw
    return repo


# ── Search ───────────────────────────────────────────────────────────────


def _normalise_search_row(entry: Any) -> dict[str, Any] | None:
    """Project an HF models-list row onto the dashboard's flat shape.

    HF occasionally returns nulls or non-dict entries between real rows
    (the public list endpoint has a few soft spots when the index is
    being rebuilt); we drop those rather than 500 the caller. Numeric
    counters default to 0; ``gated`` is surfaced verbatim — HF uses
    ``false`` for open repos and a string ("manual", "auto", or
    sometimes a model id) for gated ones.
    """
    if not isinstance(entry, dict):
        return None
    model_id = entry.get("id")
    if not isinstance(model_id, str) or not model_id.strip():
        return None
    downloads = entry.get("downloads") or 0
    likes = entry.get("likes") or 0
    gated = entry.get("gated", False)
    pipeline_tag = entry.get("pipeline_tag") or ""
    library = entry.get("library_name") or ""
    last_modified = entry.get("last_modified") or ""
    return {
        "id": model_id,
        "downloads": int(downloads) if isinstance(downloads, (int, float)) else 0,
        "likes": int(likes) if isinstance(likes, (int, float)) else 0,
        "gated": gated,
        "pipeline_tag": str(pipeline_tag),
        "library": str(library),
        "last_modified": str(last_modified),
    }


async def search_models(
    q: str, type_filter: str, limit: int = _HF_SEARCH_UPSTREAM_LIMIT
) -> list[dict[str, Any]]:
    """Hit HF's public models list and project it onto the row shape.

    Caller is responsible for capping the *requested* row count; this
    returns whatever HF gave us after the ``limit=`` hint, hard-capped
    at :data:`HF_SEARCH_RESULT_CAP`. Returns ``[]`` on every failure
    path with a single structlog line so operators can trace the cause
    without a 500 cluttering the dashboard's toast queue.
    """
    params: dict[str, str | int] = {
        "search": q,
        "limit": limit,
        "full": "false",
    }
    if type_filter:
        params["pipeline_tag"] = type_filter

    try:
        async with httpx.AsyncClient(
            timeout=httpx.Timeout(_HF_SEARCH_TIMEOUT_S),
            follow_redirects=True,
            headers=_hf_headers(),
        ) as client:
            resp = await client.get(_HF_MODELS_URL, params=params)
    except (httpx.TimeoutException, httpx.HTTPError) as exc:
        logger.warning(
            "hf_search_unreachable",
            q=q,
            type=type_filter,
            error=exc.__class__.__name__,
            detail=str(exc),
        )
        return []

    if resp.status_code >= 400:
        logger.warning(
            "hf_search_upstream_error",
            q=q,
            type=type_filter,
            status=resp.status_code,
        )
        return []

    try:
        payload = resp.json()
    except ValueError:
        logger.warning("hf_search_bad_json", q=q, type=type_filter)
        return []

    if not isinstance(payload, list):
        logger.warning("hf_search_unexpected_shape", q=q, type=type_filter)
        return []

    out: list[dict[str, Any]] = []
    for entry in payload:
        row = _normalise_search_row(entry)
        if row is None:
            continue
        out.append(row)
        if len(out) >= HF_SEARCH_RESULT_CAP:
            break
    return out


# ── Repo inspect (tree + metadata) ──────────────────────────────────────


def _looks_like_flm_repo(rel_basenames: set[str]) -> bool:
    """True when an HF repo tree has the FastFlowLM (NPU) model shape.

    FLM models aren't a single GGUF — they're a HF-Transformers-shaped
    directory (``config.json`` + a tokenizer + NPU-quant weights, e.g.
    ``model.q4nx``), so the ``.gguf``/``.mmproj`` variant filter skips every
    file and the repo inspects as "no variants".

    Detected by dir shape rather than a brittle weight-extension allowlist:
    ``config.json`` + tokenizer + at least one NPU-quant weight blob matched
    by the FastFlowLM ``…nx`` quant family (``model.q4nx`` and future levels).
    Requiring the ``nx`` blob is what keeps a plain GGUF/safetensors repo —
    which shares ``config.json``/``tokenizer`` — from being misread as FLM.
    """
    has_config = "config.json" in rel_basenames
    has_tokenizer = any(t in rel_basenames for t in _INSPECT_FLM_TOKENIZERS)
    has_npu_weight = any("." in n and n.endswith("nx") for n in rel_basenames)
    return has_config and has_tokenizer and has_npu_weight


def _extract_readme_excerpt(card_data: Any, limit: int = 400) -> str:
    """Pull a short README excerpt from the HF model API payload.

    HF returns the model card body under different shapes depending on
    the endpoint. ``cardData`` carries YAML frontmatter; the actual
    README body comes back under ``description`` or ``card``. Use
    whatever is present and truncate hard so the modal stays light.
    """
    candidates: list[str] = []
    if isinstance(card_data, dict):
        for key in ("description", "card", "readme"):
            v = card_data.get(key)
            if isinstance(v, str) and v.strip():
                candidates.append(v.strip())
    if not candidates:
        return ""
    text = candidates[0]
    if len(text) <= limit:
        return text
    return text[:limit].rstrip() + "…"


def _format_size(size_bytes: int | None) -> str:
    """Format bytes as a short human label used in the variant dropdown."""
    if not isinstance(size_bytes, int) or size_bytes <= 0:
        return "—"
    if size_bytes < 1024:
        return f"{size_bytes} B"
    if size_bytes < 1024**2:
        return f"{size_bytes / 1024:.1f} KB"
    if size_bytes < 1024**3:
        return f"{size_bytes / 1024**2:.1f} MB"
    return f"{size_bytes / 1024**3:.2f} GB"


async def fetch_repo(repo: str) -> dict[str, Any]:
    """Fetch HF model metadata + tree listing for ``repo``.

    Returns ``{"variants", "tags", "metadata"}`` — see
    :func:`hal0.api.routes.models.inspect_model` for the full response
    shape this feeds. Raises a typed :class:`hal0.errors.Hal0Error`
    subclass on transport failure or 404 so the route maps it to the
    dashboard envelope.
    """
    headers = _hf_headers()

    meta_url = f"{_HF_MODELS_URL}/{repo}"
    # ``recursive=true`` matters: repos routinely nest the mmproj sidecar in a
    # subdirectory (e.g. ``vision/Foo.mmproj``) and quants under per-quant
    # folders. The non-recursive listing only returns root entries, so those
    # files never reached the variant list and the vision picker claimed the
    # repo ships no mmproj (same param :mod:`hal0.registry.update_check` uses).
    tree_url = f"{_HF_MODELS_URL}/{repo}/tree/main?recursive=true"
    try:
        async with httpx.AsyncClient(
            timeout=httpx.Timeout(_INSPECT_TIMEOUT_SECONDS),
            follow_redirects=True,
            headers=headers,
        ) as client:
            meta_res, tree_res = await asyncio.gather(
                client.get(meta_url),
                client.get(tree_url),
            )
    except (httpx.TimeoutException, httpx.HTTPError) as exc:
        raise HFUpstreamError(
            f"failed to reach huggingface.co for {repo!r}: {exc.__class__.__name__}",
            code="hf.unreachable",
            details={"repo": repo, "error": str(exc)},
        ) from exc

    if meta_res.status_code == 404:
        raise NotFound(
            f"hugging face repo {repo!r} not found",
            code="hf.repo_not_found",
            details={"repo": repo},
        )
    if meta_res.status_code >= 400:
        raise HFUpstreamError(
            f"hugging face metadata fetch returned {meta_res.status_code}",
            code="hf.upstream_error",
            details={"repo": repo, "status": meta_res.status_code},
        )
    if tree_res.status_code >= 400:
        # A missing tree (private repo, gated, etc.) is recoverable —
        # we still surface tags + metadata, just with no variants.
        tree_payload: list[Any] = []
    else:
        try:
            tree_payload = tree_res.json() or []
        except ValueError:
            tree_payload = []

    try:
        meta_payload = meta_res.json() or {}
    except ValueError:
        meta_payload = {}

    variants: list[dict[str, Any]] = []
    rel_basenames: set[str] = set()
    flm_total_bytes = 0
    for entry in tree_payload:
        if not isinstance(entry, dict):
            continue
        rel = entry.get("path") or entry.get("rfilename")
        if not isinstance(rel, str):
            continue
        rel_low = rel.lower()
        rel_basenames.add(rel_low.rsplit("/", 1)[-1])
        # HF's tree API reports the *pointer file* size in ``size`` for
        # LFS objects; the real bytes live under ``lfs.size``. Prefer
        # the LFS size when present so the modal shows the real
        # download size, not the 100-byte pointer.
        size_raw: Any = None
        lfs = entry.get("lfs")
        if isinstance(lfs, dict):
            size_raw = lfs.get("size")
        if size_raw is None:
            size_raw = entry.get("size")
        try:
            size_bytes = int(size_raw) if size_raw is not None else 0
        except (TypeError, ValueError):
            size_bytes = 0
        flm_total_bytes += size_bytes
        is_gguf = rel_low.endswith(_INSPECT_GGUF_SUFFIX)
        # A ``.mmproj`` sidecar is pullable even though it isn't a GGUF quant —
        # the modal filters it out of the main variant dropdown (by the
        # "mmproj" token) and into the vision picker. Guard on the token too so
        # an unrelated ``.mmproj`` never slips into the main list.
        is_mmproj = "mmproj" in rel_low and rel_low.endswith(_INSPECT_MMPROJ_SUFFIX)
        if not (is_gguf or is_mmproj):
            continue
        # Use the GGUF filename as the canonical variant id — that's
        # also what the pull endpoint resolves against (hf_filename).
        kind_label = "mmproj sidecar" if (is_mmproj and not is_gguf) else "single file"
        variants.append(
            {
                "id": rel,
                "size_bytes": size_bytes,
                "size": _format_size(size_bytes),
                "info": _format_size(size_bytes) + " · " + kind_label,
            }
        )
    variants.sort(key=lambda v: v.get("size_bytes") or 0)
    # FLM/NPU repos carry no GGUF variant but a config.json + tokenizer +
    # ``…nx`` NPU-weight shape. Surface one whole-repo variant flagged
    # ``flm`` so the dashboard routes the pull through the FLM path
    # (``flm pull``) instead of the GGUF hf-download path, rather than
    # showing the repo as having nothing to pull.
    if not variants and _looks_like_flm_repo(rel_basenames):
        variants.append(
            {
                "id": repo,
                "size_bytes": flm_total_bytes,
                "size": _format_size(flm_total_bytes),
                "info": _format_size(flm_total_bytes) + " · FLM (NPU) — served via `flm pull`",
                "flm": True,
            }
        )

    tags_raw = meta_payload.get("tags") or []
    tags = [t for t in tags_raw if isinstance(t, str)]

    license_label = ""
    card = meta_payload.get("cardData")
    if isinstance(card, dict):
        lic = card.get("license")
        if isinstance(lic, str):
            license_label = lic
    if not license_label:
        # Fallback: HF exposes the top-level "license" sometimes.
        lic = meta_payload.get("license")
        if isinstance(lic, str):
            license_label = lic

    return {
        "variants": variants,
        "tags": tags,
        "metadata": {
            "license": license_label,
            "readme_excerpt": _extract_readme_excerpt(card),
        },
    }
