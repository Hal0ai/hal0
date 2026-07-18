"""File-SET pulling — recursive HF enumeration, shard grouping, deterministic
mmproj pairing (ML-2, plan §7.1c / seam S11).

Today's pull engine (:mod:`hal0.registry.pull`) downloads exactly one file
(plus an optional hand-picked mmproj sidecar) at a hardcoded ``main``
revision, and :mod:`hal0.registry.discover` DELETES any file matching a
GGUF/safetensors shard pattern on sight — a multi-shard model is invisible
to auto-scan. This module is the pure-logic planning layer that fixes both:
it enumerates a full HF repo tree (recursive + paginated), classifies every
file by role, groups shards into one model unit instead of dropping them,
and picks a deterministic mmproj pairing instead of directory-scan roulette.

``SHARD_RE`` here is the single source shared with
:mod:`hal0.registry.discover` (seam S11) — discover imports it directly so
the two call sites can never define the pattern twice and drift.
"""

from __future__ import annotations

import contextlib
import logging
import os
import re
from dataclasses import dataclass, field
from pathlib import PurePosixPath
from typing import Any

import httpx

from hal0.errors import Hal0Error
from hal0.registry.detect import quant_from_filename

log = logging.getLogger(__name__)

_HF_MODELS_URL = "https://huggingface.co/api/models"
_TREE_TIMEOUT_S = 15.0

#: The positive twin of the historic ``discover._SHARD_RE`` — same pattern,
#: opposite verdict (group, not drop). Matched against the FULL filename
#: (with extension), unlike discover's old ``p.stem``-only match — discover
#: is updated to match on the same shape (see ``registry/discover.py``).
SHARD_RE = re.compile(
    r"^(?P<stem>.+)-(?P<idx>\d{5})-of-(?P<tot>\d{5})\.(?P<ext>gguf|safetensors)$",
    re.IGNORECASE,
)

_TOKENIZER_BASENAMES = frozenset({"tokenizer.json", "tokenizer.model", "tokenizer_config.json"})
_CONFIG_BASENAMES = frozenset({"config.json", "generation_config.json"})
_MODEL_EXTENSIONS = frozenset({".gguf", ".safetensors"})

#: Precision ranking for deterministic mmproj tiebreak (plan §7.1c a2):
#: "the largest-precision mmproj (F32 > F16 > Q8…)". Higher = preferred.
#: Anything not listed (unknown / no quant token) ranks lowest.
_MMPROJ_PRECISION_RANK: dict[str, int] = {
    "F32": 100,
    "F16": 90,
    "BF16": 85,
    "Q8_0": 70,
    "Q6_K": 60,
    "Q5_K_M": 55,
    "Q5_K_S": 54,
    "Q5_0": 50,
    "Q5_1": 51,
    "Q4_K_M": 45,
    "Q4_K_S": 44,
    "Q4_0": 40,
    "Q4_1": 41,
    "Q3_K_M": 30,
    "Q3_K_S": 29,
    "Q3_K_L": 31,
    "Q2_K": 20,
}


class FilesetError(Hal0Error):
    """Base error for file-set planning."""

    code = "fileset.error"
    status = 500


class HFUpstreamError(FilesetError):
    """502 — the HF tree/revision fetch failed (network, 5xx, unparseable)."""

    code = "fileset.hf_unreachable"
    status = 502


class FilesetEmpty(FilesetError):
    """The repo tree carries no model-role file to plan around."""

    code = "fileset.empty"
    status = 422


class FilesetVariantNotFound(FilesetError):
    """``requested_variant`` doesn't match any file in the repo tree."""

    code = "fileset.variant_not_found"
    status = 404


# ── raw tree entry (one row of the HF tree API, normalised) ──────────────


@dataclass
class RawTreeEntry:
    """One normalised row of an HF repo tree listing."""

    path: str
    size: int = 0
    lfs_oid: str | None = None
    lfs_size: int | None = None
    entry_type: str = "file"


# ── planned file-set (the ML-2 → ML-3 handoff shape) ──────────────────────


@dataclass
class FileSetEntry:
    """One file of a planned file-set, ready for ``model_file`` insertion."""

    rel: str
    size_bytes: int = 0
    lfs_sha256: str | None = None
    role: str = "config"  # model | shard | mmproj | tokenizer | config
    shard_index: int | None = None


@dataclass
class FileSetPlan:
    """The full set of files one model pull should download + register."""

    repo: str
    revision: str
    entry_rel: str
    files: list[FileSetEntry] = field(default_factory=list)
    mmproj_rel: str | None = None
    total_bytes: int = 0
    runner_hint: str | None = None
    #: Why this mmproj (if any) was picked — surfaced to the UI so a
    #: deterministic-but-surprising pairing is explainable (plan §7.1c a2).
    mmproj_tiebreak_reason: str | None = None


# ── HF tree enumeration (recursive + paginated) ───────────────────────────


def _hf_headers(token: str | None) -> dict[str, str]:
    headers = {"Accept": "application/json", "User-Agent": "hal0/fileset"}
    tok = token or os.environ.get("HF_TOKEN") or os.environ.get("HUGGING_FACE_HUB_TOKEN")
    if tok:
        headers["Authorization"] = f"Bearer {tok}"
    return headers


_LINK_NEXT_RE = re.compile(r'<([^>]+)>;\s*rel="next"')


def _next_link(headers: httpx.Headers) -> str | None:
    """Parse a ``Link: <url>; rel="next"`` header, or ``None`` when absent."""
    raw = headers.get("link")
    if not raw:
        return None
    m = _LINK_NEXT_RE.search(raw)
    return m.group(1) if m else None


def _row_to_entry(row: dict[str, Any]) -> RawTreeEntry | None:
    rel = row.get("path") or row.get("rfilename")
    if not isinstance(rel, str) or not rel:
        return None
    entry_type = row.get("type") or "file"
    size_raw = row.get("size")
    try:
        size = int(size_raw) if size_raw is not None else 0
    except (TypeError, ValueError):
        size = 0
    lfs = row.get("lfs")
    lfs_oid: str | None = None
    lfs_size: int | None = None
    if isinstance(lfs, dict):
        oid = lfs.get("oid")
        if isinstance(oid, str) and oid:
            lfs_oid = oid.removeprefix("sha256:").lower()
        raw_lfs_size = lfs.get("size")
        try:
            lfs_size = int(raw_lfs_size) if raw_lfs_size is not None else None
        except (TypeError, ValueError):
            lfs_size = None
    return RawTreeEntry(
        path=rel, size=size, lfs_oid=lfs_oid, lfs_size=lfs_size, entry_type=entry_type
    )


async def enumerate_repo(
    repo: str,
    *,
    revision: str = "main",
    token: str | None = None,
    client: httpx.AsyncClient | None = None,
) -> list[RawTreeEntry]:
    """Recursively + paginated-ly list every file in an HF repo tree.

    Fixes the historic non-recursive/unpaginated ``fetch_repo`` walk (misses
    subdirectory quant variants + repos with >~1000 entries). Follows
    ``Link: rel="next"`` until exhausted. Fail-soft is the CALLER's choice —
    this raises :class:`HFUpstreamError` on any transport failure so a
    planning caller can decide whether "couldn't enumerate" is fatal.
    """
    owns_client = client is None
    if client is None:
        client = httpx.AsyncClient(timeout=httpx.Timeout(_TREE_TIMEOUT_S), follow_redirects=True)

    headers = _hf_headers(token)
    url: str | None = f"{_HF_MODELS_URL}/{repo}/tree/{revision}?recursive=true"
    entries: list[RawTreeEntry] = []
    try:
        while url:
            try:
                resp = await client.get(url, headers=headers)
            except (httpx.TimeoutException, httpx.HTTPError) as exc:
                raise HFUpstreamError(
                    f"failed to enumerate {repo!r}@{revision}: {exc.__class__.__name__}",
                    details={"repo": repo, "revision": revision, "error": str(exc)},
                ) from exc
            if resp.status_code >= 400:
                raise HFUpstreamError(
                    f"hugging face tree fetch returned {resp.status_code} for {repo!r}@{revision}",
                    details={"repo": repo, "revision": revision, "status": resp.status_code},
                )
            try:
                payload = resp.json()
            except ValueError as exc:
                raise HFUpstreamError(
                    f"hugging face tree response for {repo!r} was not JSON",
                    details={"repo": repo, "revision": revision},
                ) from exc
            for row in payload if isinstance(payload, list) else []:
                if not isinstance(row, dict):
                    continue
                entry = _row_to_entry(row)
                if entry is not None and entry.entry_type in ("file", "blob"):
                    entries.append(entry)
            url = _next_link(resp.headers)
    finally:
        if owns_client:
            await client.aclose()
    return entries


async def resolve_revision(
    repo: str,
    ref: str = "main",
    *,
    token: str | None = None,
    client: httpx.AsyncClient | None = None,
) -> str:
    """Resolve ``ref`` (usually ``"main"``) to a pinned commit sha.

    Pinning the sha means a mid-pull upstream re-tag can't stitch mismatched
    shards together (plan §7.1c). Primary: HF's revision-resolve endpoint.
    Fallback: the ``X-Repo-Commit`` header on a HEAD-shaped tree request.
    Raises :class:`HFUpstreamError` if neither resolves — callers that only
    need "some stable identifier" may catch this and fall back to ``ref``
    itself, but that reopens the floating-tag race, so it is not silently
    swallowed here.
    """
    owns_client = client is None
    if client is None:
        client = httpx.AsyncClient(timeout=httpx.Timeout(_TREE_TIMEOUT_S), follow_redirects=True)
    headers = _hf_headers(token)
    try:
        try:
            resp = await client.get(f"{_HF_MODELS_URL}/{repo}/revision/{ref}", headers=headers)
        except (httpx.TimeoutException, httpx.HTTPError) as exc:
            raise HFUpstreamError(
                f"failed to resolve revision for {repo!r}@{ref}: {exc.__class__.__name__}",
                details={"repo": repo, "ref": ref, "error": str(exc)},
            ) from exc
        if resp.status_code < 400:
            with contextlib.suppress(ValueError):
                payload = resp.json()
                sha = payload.get("sha") if isinstance(payload, dict) else None
                if isinstance(sha, str) and sha:
                    return sha
        # Fallback: X-Repo-Commit header off a plain tree request.
        try:
            tree_resp = await client.get(f"{_HF_MODELS_URL}/{repo}/tree/{ref}", headers=headers)
        except (httpx.TimeoutException, httpx.HTTPError) as exc:
            raise HFUpstreamError(
                f"failed to resolve revision for {repo!r}@{ref}: {exc.__class__.__name__}",
                details={"repo": repo, "ref": ref, "error": str(exc)},
            ) from exc
        commit = tree_resp.headers.get("x-repo-commit")
        if commit:
            return commit
        raise HFUpstreamError(
            f"could not resolve a commit sha for {repo!r}@{ref}",
            details={"repo": repo, "ref": ref},
        )
    finally:
        if owns_client:
            await client.aclose()


# ── classification ─────────────────────────────────────────────────────────


def role_of(rel: str) -> str:
    """Classify one repo-relative path into a ``model_file.role`` value.

    ``model`` | ``shard`` | ``mmproj`` | ``tokenizer`` | ``config``. Reuses
    the same "mmproj" name-token rule discovery already applies
    (:func:`hal0.registry.discover._is_mmproj_sidecar`), so a repo file and
    a locally-scanned file classify identically.
    """
    name = PurePosixPath(rel).name
    lowered = name.lower()
    if "mmproj" in lowered:
        return "mmproj"
    if SHARD_RE.match(name):
        return "shard"
    ext = PurePosixPath(rel).suffix.lower()
    if ext in _MODEL_EXTENSIONS:
        return "model"
    if lowered in _TOKENIZER_BASENAMES:
        return "tokenizer"
    if lowered in _CONFIG_BASENAMES or ext == ".jinja":
        return "config"
    return "config"


def _entry_bytes(e: RawTreeEntry) -> int:
    return e.lfs_size if e.lfs_size is not None else e.size


def _dirname(rel: str) -> str:
    return str(PurePosixPath(rel).parent)


def _precision_rank(rel: str) -> int:
    q = quant_from_filename(PurePosixPath(rel).name) or ""
    return _MMPROJ_PRECISION_RANK.get(q.upper(), 0)


def _infer_runner_hint(files: list[FileSetEntry]) -> str | None:
    """Best-effort ``preferred_runner`` hint from the planned file shape.

    Forward reference for ML-4's runner registry (not built yet — plan
    §7.1b / seam S11): a GGUF-shaped set hints ``llama-server``; a
    safetensors + tokenizer + config shape (no GGUF) hints ``flm`` (the
    FastFlowLM/NPU transformers-style layout). Anything ambiguous is
    ``None`` — ML-4 fills the column in later, this never guesses wrong on
    purpose.
    """
    exts = {PurePosixPath(f.rel).suffix.lower() for f in files if f.role in ("model", "shard")}
    if ".gguf" in exts:
        return "llama-server"
    roles = {f.role for f in files}
    if ".safetensors" in exts and {"tokenizer", "config"} <= roles:
        return "flm"
    return None


def plan_fileset(
    entries: list[RawTreeEntry],
    *,
    repo: str,
    revision: str,
    requested_variant: str | None = None,
) -> FileSetPlan:
    """Plan the download set for one model pull from a repo tree listing.

    1. Classify every entry via :func:`role_of`.
    2. Group ``shard``-role entries by ``(dir, stem, tot)`` — one "unit" per
       shard set (shard_index=1 is the entry point), replacing the historic
       drop-on-sight behaviour.
    3. Pick ONE unit as the model to install: ``requested_variant`` (an
       exact ``rel`` or bare filename) restricts the choice; otherwise the
       largest unit by total bytes wins (deterministic, no directory-walk
       order dependence).
    4. Pair a deterministic mmproj (quant-affinity first, else largest
       precision, ties broken lexicographically — plan §7.1c a2) from the
       SAME directory as the chosen unit (falling back to any mmproj in the
       tree if none share a directory).
    5. Carry tokenizer/config files from the same directory too (the FLM /
       HF-transformers multi-file shape needs them; GGUF-only repos won't
       have any).

    Raises :class:`FilesetEmpty` when no model-role file exists, or
    :class:`FilesetVariantNotFound` when ``requested_variant`` matches
    nothing.
    """
    shard_groups: dict[tuple[str, str, str], dict[int, RawTreeEntry]] = {}
    model_files: list[RawTreeEntry] = []
    mmproj_files: list[RawTreeEntry] = []
    tokenizer_files: list[RawTreeEntry] = []
    config_files: list[RawTreeEntry] = []

    for e in entries:
        role = role_of(e.path)
        name = PurePosixPath(e.path).name
        if role == "shard":
            m = SHARD_RE.match(name)
            assert m is not None  # role_of only returns "shard" on a match
            key = (_dirname(e.path), m.group("stem"), m.group("tot"))
            shard_groups.setdefault(key, {})[int(m.group("idx"))] = e
        elif role == "model":
            model_files.append(e)
        elif role == "mmproj":
            mmproj_files.append(e)
        elif role == "tokenizer":
            tokenizer_files.append(e)
        else:
            config_files.append(e)

    units: list[dict[str, Any]] = []
    for (dirname, _stem, _tot), idx_map in shard_groups.items():
        ordered = [idx_map[i] for i in sorted(idx_map)]
        units.append({"dirname": dirname, "kind": "shard", "entries": ordered})
    for e in model_files:
        units.append({"dirname": _dirname(e.path), "kind": "single", "entries": [e]})

    if not units:
        raise FilesetEmpty(
            f"no model-role file found in {repo!r}@{revision}",
            details={"repo": repo, "revision": revision},
        )

    if requested_variant:
        req_name = PurePosixPath(requested_variant).name
        chosen = next(
            (
                u
                for u in units
                if u["entries"][0].path == requested_variant
                or PurePosixPath(u["entries"][0].path).name == req_name
            ),
            None,
        )
        if chosen is None:
            raise FilesetVariantNotFound(
                f"{requested_variant!r} not found in {repo!r}@{revision}",
                details={
                    "repo": repo,
                    "revision": revision,
                    "requested_variant": requested_variant,
                },
            )
    else:
        chosen = max(units, key=lambda u: sum(_entry_bytes(e) for e in u["entries"]))

    chosen_dir = chosen["dirname"]
    entry_rel = chosen["entries"][0].path
    is_shard = chosen["kind"] == "shard"

    files: list[FileSetEntry] = [
        FileSetEntry(
            rel=e.path,
            size_bytes=_entry_bytes(e),
            lfs_sha256=e.lfs_oid,
            role="shard" if is_shard else "model",
            shard_index=(idx + 1) if is_shard else None,
        )
        for idx, e in enumerate(chosen["entries"])
    ]

    # ── deterministic mmproj pairing (a2) ─────────────────────────────────
    mmproj_rel: str | None = None
    mmproj_reason: str | None = None
    same_dir_mm = [e for e in mmproj_files if _dirname(e.path) == chosen_dir]
    mm_candidates = same_dir_mm or mmproj_files
    if mm_candidates:
        entry_quant = quant_from_filename(PurePosixPath(entry_rel).name)
        affinity = (
            [
                e
                for e in mm_candidates
                if entry_quant and quant_from_filename(PurePosixPath(e.path).name) == entry_quant
            ]
            if entry_quant
            else []
        )
        if affinity:
            picked = sorted(affinity, key=lambda e: e.path)[0]
            mmproj_reason = "quant_affinity"
        else:
            best_rank = max(_precision_rank(e.path) for e in mm_candidates)
            top = sorted(
                (e for e in mm_candidates if _precision_rank(e.path) == best_rank),
                key=lambda e: e.path,
            )
            picked = top[0]
            mmproj_reason = "largest_precision" if len(mm_candidates) > 1 else "only_candidate"
        mmproj_rel = picked.path
        files.append(
            FileSetEntry(
                rel=picked.path,
                size_bytes=_entry_bytes(picked),
                lfs_sha256=picked.lfs_oid,
                role="mmproj",
                shard_index=None,
            )
        )

    # ── same-dir tokenizer/config carry (FLM / transformers-shaped repos) ─
    for e in tokenizer_files:
        if _dirname(e.path) == chosen_dir:
            files.append(
                FileSetEntry(
                    rel=e.path,
                    size_bytes=_entry_bytes(e),
                    lfs_sha256=e.lfs_oid,
                    role="tokenizer",
                    shard_index=None,
                )
            )
    for e in config_files:
        if _dirname(e.path) == chosen_dir:
            files.append(
                FileSetEntry(
                    rel=e.path,
                    size_bytes=_entry_bytes(e),
                    lfs_sha256=e.lfs_oid,
                    role="config",
                    shard_index=None,
                )
            )

    total_bytes = sum(f.size_bytes for f in files)
    return FileSetPlan(
        repo=repo,
        revision=revision,
        entry_rel=entry_rel,
        files=files,
        mmproj_rel=mmproj_rel,
        total_bytes=total_bytes,
        runner_hint=_infer_runner_hint(files),
        mmproj_tiebreak_reason=mmproj_reason,
    )


__all__ = [
    "SHARD_RE",
    "FileSetEntry",
    "FileSetPlan",
    "FilesetEmpty",
    "FilesetError",
    "FilesetVariantNotFound",
    "HFUpstreamError",
    "RawTreeEntry",
    "enumerate_repo",
    "plan_fileset",
    "resolve_revision",
    "role_of",
]
