"""HF update detection for pulled models.

A registry row pulled from HuggingFace records the sha256 of the bytes it
streamed (``metadata.sha256`` — for LFS-backed files this equals the LFS
object id HF advertises). HF's tree API exposes the CURRENT LFS oid per
file, so "an update is available" reduces to: the stored sha no longer
matches the repo's sha for the same ``hf_repo``/``hf_filename``.

This module owns the two halves of that comparison:

* :func:`fetch_remote_lfs_shas` — one tree fetch per unique repo,
  fail-soft (an unreachable/gated repo yields ``None``, never raises).
* :func:`evaluate_model_update` — the pure per-model verdict.

The route layer (``api/routes/models.py``) composes them behind
``GET /api/models/updates/check`` and caches the result on app state so
the 30s ``/api/models`` poll never touches huggingface.co.
"""

from __future__ import annotations

import asyncio
import logging
import os
from typing import Any

import httpx

log = logging.getLogger(__name__)

_TREE_TIMEOUT_S = 15.0
# Parallel tree fetches per check run. HF rate-limits anonymous clients
# aggressively; four concurrent GETs keeps a 30-repo registry check quick
# without tripping 429s.
_FETCH_CONCURRENCY = 4


def _tree_url(repo: str) -> str:
    # ``recursive=true`` because hf_filename may live in a subdirectory
    # (e.g. multi-part GGUF layouts store variants under ``<quant>/``).
    return f"https://huggingface.co/api/models/{repo}/tree/main?recursive=true"


async def fetch_remote_lfs_shas(
    repos: set[str],
    *,
    hf_token: str | None = None,
    client: httpx.AsyncClient | None = None,
) -> dict[str, dict[str, str] | None]:
    """Fetch the current LFS sha256 per file for each repo.

    Returns ``{repo: {path: sha256}}``; a repo whose tree fetch failed
    (network, 4xx, gated) maps to ``None`` so callers can distinguish
    "couldn't check" from "file gone". Non-LFS entries are omitted —
    they carry only a git blob sha1, which is not comparable to the
    sha256 the pull recorded.
    """
    headers = {"Accept": "application/json", "User-Agent": "hal0/update-check"}
    token = hf_token or os.environ.get("HF_TOKEN") or os.environ.get("HUGGING_FACE_HUB_TOKEN")
    if token:
        headers["Authorization"] = f"Bearer {token}"

    owns_client = client is None
    if client is None:
        client = httpx.AsyncClient(
            timeout=httpx.Timeout(_TREE_TIMEOUT_S),
            follow_redirects=True,
        )

    sem = asyncio.Semaphore(_FETCH_CONCURRENCY)
    results: dict[str, dict[str, str] | None] = {}

    async def _one(repo: str) -> None:
        async with sem:
            try:
                resp = await client.get(_tree_url(repo), headers=headers)
                if resp.status_code >= 400:
                    results[repo] = None
                    return
                payload = resp.json()
            except Exception as exc:
                log.debug("update_check.tree_fetch_failed", extra={"repo": repo, "error": str(exc)})
                results[repo] = None
                return
            files: dict[str, str] = {}
            for entry in payload if isinstance(payload, list) else []:
                if not isinstance(entry, dict):
                    continue
                rel = entry.get("path")
                lfs = entry.get("lfs")
                if not (isinstance(rel, str) and rel and isinstance(lfs, dict)):
                    continue
                oid = lfs.get("oid")
                if isinstance(oid, str) and oid:
                    files[rel] = oid.removeprefix("sha256:").lower()
            results[repo] = files

    try:
        await asyncio.gather(*(_one(r) for r in {r for r in repos if r}))
    finally:
        if owns_client:
            await client.aclose()
    return results


def evaluate_model_update(
    model: Any,
    repo_files: dict[str, dict[str, str] | None],
) -> dict[str, Any] | None:
    """Pure per-model update verdict against pre-fetched repo trees.

    Returns ``None`` for a model with no HF coordinates (not updatable
    from HF at all — hand-registered or scan-discovered rows). Otherwise
    a verdict dict; ``update_available`` is only ever True when BOTH a
    local sha and a remote sha exist and disagree, so a hand-registered
    row (no recorded sha) or a non-LFS file never shows a phantom update.
    """
    repo = (getattr(model, "hf_repo", "") or "").strip()
    filename = (getattr(model, "hf_filename", "") or "").strip()
    if not repo or not filename:
        return None
    meta = getattr(model, "metadata", None)
    local_sha = meta.get("sha256") if isinstance(meta, dict) else None
    if not isinstance(local_sha, str) or not local_sha:
        local_sha = None

    verdict: dict[str, Any] = {
        "hf_repo": repo,
        "hf_filename": filename,
        "local_sha256": local_sha,
        "remote_sha256": None,
        "update_available": False,
        "reason": None,
    }
    files = repo_files.get(repo)
    if files is None:
        verdict["reason"] = "repo_unreachable"
        return verdict
    remote_sha = files.get(filename)
    if remote_sha is None:
        # Exact repo-relative path missed — fall back to a UNIQUE basename
        # match. A row whose stored hf_filename dropped the upstream subdir
        # prefix (hand-registered or path-added rows) would otherwise
        # silently never flag even though the file is right there under
        # ``<quant>/<file>``. Guarded on a single distinct sha so an
        # ambiguous basename (the same filename under two quant dirs) is
        # left unresolved rather than compared against the wrong variant.
        base = filename.rsplit("/", 1)[-1]
        candidates = {sha for path, sha in files.items() if path.rsplit("/", 1)[-1] == base}
        if len(candidates) == 1:
            remote_sha = next(iter(candidates))
    if remote_sha is None:
        # File renamed/removed upstream, or a non-LFS object (no sha256).
        verdict["reason"] = "file_missing_or_not_lfs"
        return verdict
    verdict["remote_sha256"] = remote_sha
    if local_sha is None:
        verdict["reason"] = "no_local_sha256"
        return verdict
    verdict["update_available"] = remote_sha != local_sha.lower()
    return verdict
