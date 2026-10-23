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
from pathlib import PurePosixPath
from typing import Any

import httpx

from hal0.registry.fileset import SHARD_RE

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


def load_shard_shas(registry: Any, model_id: str) -> dict[str, str]:
    """``{rel: sha256}`` of a model's installed split-GGUF parts (#2761).

    Read from the ``model_file`` rows the pull writes (one per part,
    ``role = shard``). Empty for a single-file model, a non-SQLite registry,
    or any read failure: the check then falls back to the first part alone.
    """
    db = getattr(registry, "db_path", None)
    if db is None:
        return {}
    try:
        from hal0.db import repository
        from hal0.db.connection import connect

        with connect(db) as conn:
            rows = repository.list_model_files(conn, model_id)
    except Exception:
        log.debug("update_check.shard_rows_failed", extra={"model_id": model_id}, exc_info=True)
        return {}
    return {
        r["rel"]: str(r["sha256"]).lower()
        for r in rows
        if r.get("role") == "shard" and r.get("rel") and r.get("sha256")
    }


def _evaluate_shards(
    filename: str,
    files: dict[str, str],
    shard_shas: dict[str, str],
) -> tuple[str | None, list[str]]:
    """Compare every part of a split GGUF; returns ``(reason, rels)``.

    ``reason`` is ``shard_changed`` (``rels`` = parts whose remote sha differs
    from the recorded one), ``shard_count_changed`` (the installed layout is
    gone upstream and the same stem now has a different ``-of-N``; ``rels`` =
    the installed parts), or ``None``. A layout that is still present is never
    a count change: two layouts side by side are a valid repo shape. Parts
    with no recorded local sha or no remote LFS entry are skipped, so they
    never raise a phantom update.
    """
    path = PurePosixPath(filename)
    m = SHARD_RE.match(path.name)
    if m is None or not shard_shas:
        return None, []
    tot = int(m.group("tot"))
    stem, ext = m.group("stem"), m.group("ext")
    changed: list[str] = []
    for i in range(1, tot + 1):
        rel = str(path.with_name(f"{stem}-{i:05d}-of-{tot:05d}.{ext}"))
        local = shard_shas.get(rel)
        remote = files.get(rel)
        if local and remote and local != remote:
            changed.append(rel)
    if changed:
        return "shard_changed", changed
    if filename in files:
        return None, []
    for rel in files:
        rp = PurePosixPath(rel)
        rm = SHARD_RE.match(rp.name)
        if (
            rm is not None
            and rp.parent == path.parent
            and rm.group("stem") == stem
            and rm.group("ext").lower() == ext.lower()
            and int(rm.group("tot")) != tot
        ):
            return "shard_count_changed", sorted(shard_shas)
    return None, []


def shard_update_pending(verdict: dict[str, Any], current: dict[str, str]) -> bool:
    """Re-check a cached shard verdict against the CURRENT installed rows.

    ``current`` is :func:`load_shard_shas` now. A ``shard_changed`` verdict
    holds only while an installed part still differs from the remote sha the
    check recorded; a ``shard_count_changed`` one only while the installed
    part set is the one the check saw. Applying an update changes the rows, so
    the badge clears itself without waiting for the check TTL.
    """
    reason = verdict.get("reason")
    if reason == "shard_changed":
        remote = verdict.get("remote_shard_shas")
        if not isinstance(remote, dict):
            return False
        return any(
            isinstance(sha, str) and (cur := current.get(rel)) is not None and cur != sha
            for rel, sha in remote.items()
        )
    if reason == "shard_count_changed":
        return bool(current) and sorted(current) == verdict.get("changed_parts")
    return False


def evaluate_model_update(
    model: Any,
    repo_files: dict[str, dict[str, str] | None],
    shard_shas: dict[str, str] | None = None,
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
    shard_reason, shard_rels = _evaluate_shards(filename, files, shard_shas or {})
    if shard_reason is not None:
        # A later part (or the part count) moved upstream even if part 1 did not.
        verdict["update_available"] = True
        verdict["reason"] = shard_reason
        verdict["changed_parts"] = shard_rels
        verdict["remote_sha256"] = files.get(filename)
        # Remote sha per changed part, so list_all can re-check the cached
        # verdict against the installed rows instead of replaying it.
        verdict["remote_shard_shas"] = {r: files[r] for r in shard_rels if r in files}
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
