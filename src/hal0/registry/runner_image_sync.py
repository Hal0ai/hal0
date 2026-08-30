"""Runner-image discovery — GHCR anonymous probe + ``images.json`` merge.

Two independent fetches, merged per ``images.json`` entry — row id is the
entry's ``id`` short name, falling back to the GHCR repo path
(feat/runner-image-catalogue, corrected against the original handoff doc —
see PR description):

1. **GHCR discovery.** Anonymous-token tag/digest resolution against known
   packages (the same flow already proven out by
   ``hal0.cli.doctor_commands._ghcr_anon_token`` /
   ``_ghcr_manifest_digest`` for the toolbox-pull doctor probe). GHCR's
   package-LIST API needs an authenticated, org-scoped token — there is no
   anonymous "list every package under this org" call — so this module
   does not enumerate the org. Instead it probes the exact package repo
   paths named by ``images.json``'s ``image`` field (falling back to
   ``manifest_key`` when ``image`` is absent), which is a deliberate,
   documented deviation from the original handoff doc's assumption that
   anonymous org-wide listing would work.

2. **``images.json`` fetch.** ``raw.githubusercontent.com/Hal0ai/hal0-runner-images/main/images.json``
   (schema ``hal0.runner-images.v1``) — anonymous, CDN-backed, no auth. Gives
   display metadata (``ownership``/``publish``/``manifest_key``/``build``/
   ``notes``) per image. Fetched live at sync time, never baked into a
   release.

Fetch failures degrade gracefully at every layer: a malformed/unreachable
``images.json`` still lets already-known GHCR packages sync (tag/digest/size
only, no metadata); a single package's GHCR probe failing doesn't abort the
whole sync run.

Per-tag build provenance (h0/runner-provenance): alongside each tag's
manifest probe, the image CONFIG BLOB (manifest → ``config.digest`` → blob
GET) is fetched and its OCI labels (``org.opencontainers.image.source`` /
``.revision``, ``dev.hal0.runner.patches``) are folded into
``RunnerImageTag.provenance`` — so an operator can tell the upstream
llama.cpp Vulkan/ROCm build apart from the ROCmFPX fork one. A failed blob
fetch degrades that tag to ``provenance=None``, never fails the sync.
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any

import httpx

from hal0.registry.runner_image import RunnerImage, RunnerImageTag
from hal0.registry.runner_image_store import RunnerImageStore

log = logging.getLogger(__name__)

IMAGES_JSON_URL = "https://raw.githubusercontent.com/Hal0ai/hal0-runner-images/main/images.json"
_EXPECTED_SCHEMA = "hal0.runner-images.v1"

_GHCR_TOKEN_URL = "https://ghcr.io/token"
_OCI_MANIFEST_ACCEPT = (
    "application/vnd.oci.image.index.v1+json,"
    "application/vnd.oci.image.manifest.v1+json,"
    "application/vnd.docker.distribution.manifest.list.v2+json,"
    "application/vnd.docker.distribution.manifest.v2+json"
)
_HTTP_TIMEOUT_S = 15.0

#: OCI labels the runner-image builds stamp into the image CONFIG blob —
#: the llama.cpp build provenance an operator needs to tell the upstream
#: Vulkan/ROCm build apart from the ROCmFPX one (verified live on the
#: published ghcr.io/hal0ai packages).
_LABEL_SOURCE = "org.opencontainers.image.source"
_LABEL_REVISION = "org.opencontainers.image.revision"
_LABEL_PATCHES = "dev.hal0.runner.patches"


@dataclass
class SyncResult:
    """Outcome of one sync run — surfaced by the ``POST /sync`` route."""

    images: list[RunnerImage] = field(default_factory=list)
    images_json_ok: bool = False
    images_json_error: str | None = None
    probe_errors: dict[str, str] = field(default_factory=dict)


def _strip_registry_host(image_ref: str) -> str:
    """``ghcr.io/hal0ai/hal0-toolbox-cpu`` -> ``hal0ai/hal0-toolbox-cpu``.

    Splits on the first ``/`` and compares the whole leading segment for
    exact equality with ``ghcr.io`` (not a ``str.startswith``/substring
    check) — CodeQL's "incomplete URL substring sanitization" query
    flags prefix/substring probes against host-like strings because they
    can be spoofed by a crafted string that merely starts with the
    expected text (e.g. ``ghcr.io.evil.example/...``). An exact segment
    match has no such bypass.
    """
    ref = image_ref.strip()
    host, sep, rest = ref.partition("/")
    if sep and host == "ghcr.io":
        return rest
    return ref


#: ``v?1.2.3``-shaped tags — the "semver" bucket of :func:`sort_tags_newest_first`.
_SEMVER_TAG_RE = re.compile(r"^v?\d+(\.\d+)+$")

#: Cosign signature/attestation objects — ``cosign sign``/``cosign attest``
#: pushes these into the SAME GHCR repo as the image itself, as an ordinary
#: tag shaped ``sha256-<64 lowercase hex>`` (the signed digest, ``-``
#: instead of ``:``), optionally suffixed ``.sig`` (signature) or ``.att``
#: (attestation/provenance). They resolve and pull like any other tag, but
#: they are never a "version" of the image — just noise in a tag picker or
#: a "newer than headline" comparison.
_COSIGN_ARTIFACT_TAG_RE = re.compile(r"^sha256-[0-9a-f]{64}(\.sig|\.att)?$")

#: Per-commit CI tags — GitHub Actions pushes ``sha-<commit sha>`` (short,
#: 7 hex chars, or full, 40) on every build of the CI-built toolbox
#: families (hal0-toolbox-vulkan/rocm). Real, pullable images, but one per
#: commit floods a family's tag list and should never win a "newer" slot
#: either — that shape is what produced the observed .sig/CI-tag flood on
#: ghcr.io/hal0ai/hal0-toolbox-vulkan and -rocm.
_CI_COMMIT_TAG_RE = re.compile(r"^sha-[0-9a-f]{7,40}$")


def is_noise_tag(tag: str) -> bool:
    """True for cosign signature/attestation artifacts and per-commit CI
    tags (see :data:`_COSIGN_ARTIFACT_TAG_RE` / :data:`_CI_COMMIT_TAG_RE`)
    — real GHCR tags that must never reach a family's ``available_tags``
    (tag picker) or a "newer than headline" comparison.
    """
    return bool(_COSIGN_ARTIFACT_TAG_RE.match(tag) or _CI_COMMIT_TAG_RE.match(tag))


def sort_tags_newest_first(tags: list[str]) -> list[str]:
    """Order GHCR ``tags/list`` output newest-first (catalogue-v2 contract).

    Three buckets, concatenated in this order:

    1. all-digit tags (the CI date/short-SHA-free shape, e.g. ``0824``) —
       numeric descending, so the freshest date-tag leads;
    2. semver-shaped tags (``v1.10.0`` / ``1.2.3``) — version-tuple
       descending (NOT lexicographic: ``v1.10.0`` beats ``v1.2.0``);
    3. everything else (``latest``, branch names, bare SHAs) — original
       registry order, untouched, as the stable last resort.

    Pure and exported: the sync path uses it for ``available_tags`` and the
    unpinned headline tag, and tests/UI helpers can call it directly.
    """
    numeric: list[str] = []
    semver: list[str] = []
    rest: list[str] = []
    for tag in tags:
        if tag.isdigit():
            numeric.append(tag)
        elif _SEMVER_TAG_RE.match(tag):
            semver.append(tag)
        else:
            rest.append(tag)
    numeric.sort(key=int, reverse=True)
    semver.sort(key=lambda t: tuple(int(p) for p in t.lstrip("v").split(".")), reverse=True)
    return numeric + semver + rest


async def fetch_images_json(client: httpx.AsyncClient) -> tuple[list[dict[str, Any]], str | None]:
    """Fetch + validate ``images.json``.

    Returns ``(entries, error)`` — ``entries`` is ``[]`` and ``error`` is a
    human-readable string on any failure (network, non-200, bad JSON, wrong
    schema, non-list ``images``); the caller degrades to GHCR-only rows
    rather than raising.
    """
    try:
        resp = await client.get(IMAGES_JSON_URL, timeout=_HTTP_TIMEOUT_S)
        resp.raise_for_status()
        payload = resp.json()
    except (httpx.HTTPError, ValueError) as exc:
        return [], f"images.json fetch failed: {exc}"
    if not isinstance(payload, dict):
        return [], "images.json is not a JSON object"
    schema = payload.get("schema")
    if schema != _EXPECTED_SCHEMA:
        log.warning("runner_images.unexpected_schema schema=%r", schema)
    entries = payload.get("images")
    if not isinstance(entries, list):
        return [], "images.json has no 'images' array"
    return [e for e in entries if isinstance(e, dict)], None


async def _ghcr_anon_token(repo: str, *, client: httpx.AsyncClient) -> str:
    resp = await client.get(
        _GHCR_TOKEN_URL,
        params={"scope": f"repository:{repo}:pull"},
        timeout=_HTTP_TIMEOUT_S,
    )
    resp.raise_for_status()
    payload = resp.json()
    token = payload.get("token") or payload.get("access_token")
    if not isinstance(token, str) or not token:
        raise RuntimeError(f"ghcr.io token endpoint returned no token for {repo}")
    return token


async def _ghcr_list_tags(repo: str, *, token: str, client: httpx.AsyncClient) -> list[str]:
    """Fetch ``tags/list`` for ``repo``, filtered of :func:`is_noise_tag` shapes.

    Filtering here — the single fetch point every consumer (sync-time
    ``available_tags``, the unpinned headline fallback, the API response,
    the JSX tag picker/"newer" chip) is downstream of — is deliberate:
    cosign signature/CI-commit tags never enter the catalogue at all,
    rather than being filtered again by each reader.
    """
    resp = await client.get(
        f"https://ghcr.io/v2/{repo}/tags/list",
        headers={"Authorization": f"Bearer {token}"},
        timeout=_HTTP_TIMEOUT_S,
    )
    resp.raise_for_status()
    payload = resp.json()
    tags = payload.get("tags")
    raw = [t for t in tags if isinstance(t, str)] if isinstance(tags, list) else []
    return [t for t in raw if not is_noise_tag(t)]


def provenance_from_labels(labels: Any) -> dict[str, Any] | None:
    """Build-provenance dict from an image config blob's ``config.Labels``.

    Pure and exported (tested directly, same discipline as
    :func:`sort_tags_newest_first`). Returns
    ``{"source_repo", "revision", "patch_count"}`` with each field ``None``
    when its label is absent; ``patch_count`` is the count of non-empty
    entries in the comma-split ``dev.hal0.runner.patches`` value (``0`` for
    a present-but-empty label — pristine upstream). Returns ``None`` when
    ``labels`` isn't a dict or carries none of the three labels, so a
    labelless image reads exactly like an unprobed one.
    """
    if not isinstance(labels, dict):
        return None
    source = labels.get(_LABEL_SOURCE)
    revision = labels.get(_LABEL_REVISION)
    patches = labels.get(_LABEL_PATCHES)
    if not any(isinstance(v, str) for v in (source, revision, patches)):
        return None
    patch_count: int | None = None
    if isinstance(patches, str):
        patch_count = len([p for p in patches.split(",") if p.strip()])
    return {
        "source_repo": source if isinstance(source, str) and source else None,
        "revision": revision if isinstance(revision, str) and revision else None,
        "patch_count": patch_count,
    }


def _manifest_config_digest(payload: Any) -> str | None:
    """``config.digest`` of one image manifest (never of an index)."""
    if not isinstance(payload, dict):
        return None
    config = payload.get("config")
    if isinstance(config, dict) and isinstance(config.get("digest"), str):
        return config["digest"]
    return None


def _manifest_content_size(payload: Any) -> int | None:
    """Sum ``config.size`` + every ``layers[].size`` of one image manifest."""
    if not isinstance(payload, dict):
        return None
    layers = payload.get("layers")
    if not isinstance(layers, list):
        return None
    total = 0
    config = payload.get("config")
    if isinstance(config, dict) and isinstance(config.get("size"), int):
        total += config["size"]
    for layer in layers:
        if isinstance(layer, dict) and isinstance(layer.get("size"), int):
            total += layer["size"]
    return total or None


def _pick_index_manifest(payload: dict[str, Any]) -> str | None:
    """Pick the linux/amd64 image-manifest digest out of an OCI index.

    Attestation manifests (buildkit provenance, platform ``unknown``) are
    skipped; falls back to the first non-attestation entry when no
    linux/amd64 platform is declared.
    """
    manifests = payload.get("manifests")
    if not isinstance(manifests, list):
        return None
    fallback: str | None = None
    for entry in manifests:
        if not isinstance(entry, dict):
            continue
        digest = entry.get("digest")
        if not isinstance(digest, str):
            continue
        platform_raw = entry.get("platform")
        platform: dict[str, Any] = platform_raw if isinstance(platform_raw, dict) else {}
        if platform.get("os") == "unknown" or platform.get("architecture") == "unknown":
            continue  # buildkit attestation, not a runnable image
        if fallback is None:
            fallback = digest
        if platform.get("os") == "linux" and platform.get("architecture") == "amd64":
            return digest
    return fallback


async def _ghcr_manifest_info(
    repo: str, ref: str, *, token: str, client: httpx.AsyncClient
) -> tuple[str | None, int | None, str | None]:
    """Return ``(digest, size_bytes, config_digest)`` for one tag; any may
    be None.

    Unlike a bare HEAD (whose ``content-length`` is the size of the manifest
    *document*, a couple of KB), this GETs the manifest body and sums the
    config + layer blob sizes — the actual compressed image size, matching
    what GHCR's package UI reports. Multi-arch indexes are followed one
    level down to the linux/amd64 image manifest. The digest reported is
    the top-level one (what ``podman pull repo@digest`` would resolve);
    ``config_digest`` is the IMAGE manifest's ``config.digest`` (the child's
    for an index) — what the provenance blob probe fetches.
    """
    resp = await client.get(
        f"https://ghcr.io/v2/{repo}/manifests/{ref}",
        headers={"Authorization": f"Bearer {token}", "Accept": _OCI_MANIFEST_ACCEPT},
        timeout=_HTTP_TIMEOUT_S,
    )
    if resp.status_code >= 400:
        raise RuntimeError(f"HTTP {resp.status_code} probing {repo}:{ref}")
    digest = resp.headers.get("docker-content-digest")
    try:
        payload = resp.json()
    except ValueError:
        payload = None

    size = _manifest_content_size(payload)
    config_digest = _manifest_config_digest(payload)
    if size is None and isinstance(payload, dict):
        child_digest = _pick_index_manifest(payload)
        if child_digest:
            child = await client.get(
                f"https://ghcr.io/v2/{repo}/manifests/{child_digest}",
                headers={"Authorization": f"Bearer {token}", "Accept": _OCI_MANIFEST_ACCEPT},
                timeout=_HTTP_TIMEOUT_S,
            )
            if child.status_code < 400:
                try:
                    child_payload = child.json()
                except ValueError:
                    child_payload = None
                size = _manifest_content_size(child_payload)
                config_digest = _manifest_config_digest(child_payload)
    if size is None:
        length = resp.headers.get("content-length")
        size = int(length) if length and length.isdigit() else None
    return digest, size, config_digest


async def _ghcr_tag_provenance(
    repo: str, config_digest: str | None, *, token: str, client: httpx.AsyncClient
) -> dict[str, Any] | None:
    """Fetch one tag's config blob and extract its provenance labels.

    Fail-soft by contract: any failure (no config digest resolved, HTTP
    error, non-JSON blob) degrades to ``None`` — provenance never fails a
    tag row, let alone the sync run.
    """
    if not config_digest:
        return None
    try:
        resp = await client.get(
            f"https://ghcr.io/v2/{repo}/blobs/{config_digest}",
            headers={"Authorization": f"Bearer {token}"},
            timeout=_HTTP_TIMEOUT_S,
        )
        if resp.status_code >= 400:
            raise RuntimeError(f"HTTP {resp.status_code}")
        payload = resp.json()
    except (httpx.HTTPError, RuntimeError, ValueError) as exc:
        log.warning(
            "runner_images.config_blob_probe_failed repo=%s digest=%s error=%s",
            repo,
            config_digest,
            exc,
        )
        return None
    if not isinstance(payload, dict):
        return None
    config = payload.get("config")
    labels = config.get("Labels") if isinstance(config, dict) else None
    return provenance_from_labels(labels)


async def probe_ghcr_package(
    repo: str,
    *,
    client: httpx.AsyncClient,
    tag: str | None = None,
    image_id: str | None = None,
) -> RunnerImage:
    """Anonymously resolve one GHCR package's tags + digest + size.

    ``tag`` pins the headline tag (from an ``images.json`` entry); when
    omitted, the newest tag per :func:`sort_tags_newest_first` is used,
    falling back to ``latest``. Either way ``tags/list`` is always probed
    so the row carries the full ``available_tags`` list (catalogue v2 tag
    tracking); a failing ``tags/list`` degrades to ``available_tags=[]``
    without failing the row. ``image_id`` sets the catalogue row id (the
    ``images.json`` short name, e.g. ``cpu``); defaults to the repo path
    for rows with no manifest entry.
    """
    token = await _ghcr_anon_token(repo, client=client)
    try:
        tags = await _ghcr_list_tags(repo, token=token, client=client)
    except (httpx.HTTPError, ValueError) as exc:
        log.warning("runner_images.tags_list_failed repo=%s error=%s", repo, exc)
        tags = []
    available = sort_tags_newest_first(tags)
    resolved_tag = tag if tag is not None else (available[0] if available else "latest")
    digest, size, config_digest = await _ghcr_manifest_info(
        repo, resolved_tag, token=token, client=client
    )
    # Build provenance from the config blob's OCI labels — but never for a
    # noise-tag headline (an images.json pin should never name one, but the
    # per-tag blob GET is real registry traffic worth gating anyway;
    # `available` is already noise-filtered at _ghcr_list_tags).
    provenance = (
        None
        if is_noise_tag(resolved_tag)
        else await _ghcr_tag_provenance(repo, config_digest, token=token, client=client)
    )

    # Resolve a digest for every catalogued tag (catalogue v3).
    now = datetime.now(UTC).isoformat()
    tag_rows: list[RunnerImageTag] = []

    # Build tag rows from available tags (newest-first).
    for t in available:
        if t == resolved_tag:
            # Reuse the headline manifest result.
            tag_rows.append(
                RunnerImageTag(
                    tag=t, digest=digest, size_bytes=size, last_seen=now, provenance=provenance
                )
            )
            continue
        try:
            t_digest, t_size, t_config = await _ghcr_manifest_info(
                repo, t, token=token, client=client
            )
        except (httpx.HTTPError, RuntimeError, ValueError) as exc:
            log.warning("runner_images.tag_probe_failed repo=%s tag=%s error=%s", repo, t, exc)
            t_digest, t_size, t_config = None, None, None
        t_prov = await _ghcr_tag_provenance(repo, t_config, token=token, client=client)
        tag_rows.append(
            RunnerImageTag(
                tag=t, digest=t_digest, size_bytes=t_size, last_seen=now, provenance=t_prov
            )
        )

    # If resolved_tag (pinned or fallback) is not in available, prepend it.
    if resolved_tag not in available:
        tag_rows.insert(
            0,
            RunnerImageTag(
                tag=resolved_tag,
                digest=digest,
                size_bytes=size,
                last_seen=now,
                provenance=provenance,
            ),
        )

    return RunnerImage(
        id=image_id or repo,
        image=f"ghcr.io/{repo}",
        tag=resolved_tag,
        digest=digest,
        size_bytes=size,
        available_tags=available,
        tags=tag_rows,
    )


def _match_key(entry: dict[str, Any]) -> str | None:
    """The GHCR repo path an images.json entry describes, or None if unusable."""
    image = entry.get("image")
    if isinstance(image, str) and image.strip():
        return _strip_registry_host(image)
    manifest_key = entry.get("manifest_key")
    if isinstance(manifest_key, str) and manifest_key.strip():
        return manifest_key.strip()
    return None


def _apply_manifest_metadata(image: RunnerImage, entry: dict[str, Any]) -> RunnerImage:
    """Overlay images.json display metadata onto a GHCR-discovered row."""
    update: dict[str, Any] = {}
    for field_name in ("manifest_key", "ownership", "publish", "notes"):
        val = entry.get(field_name)
        if isinstance(val, str) and val.strip():
            update[field_name] = val.strip()
    build = entry.get("build")
    if isinstance(build, dict):
        update["build"] = build
    tag = entry.get("tag")
    if isinstance(tag, str) and tag.strip():
        update["tag"] = tag.strip()
    return image.model_copy(update=update) if update else image


async def sync_runner_images(
    store: RunnerImageStore, *, client: httpx.AsyncClient | None = None
) -> SyncResult:
    """Run one discovery pass and persist the merged rows.

    Source of which GHCR packages to probe: ``images.json``'s entries
    (see module docstring — anonymous org-wide GHCR listing isn't
    available). Row id is the entry's ``id`` short name (``cpu``,
    ``rocmfpx-hy3``, …) so two entries sharing one GHCR repo path under
    different tags stay distinct rows; entries with no ``id`` fall back
    to the repo path. A malformed/unreachable ``images.json`` still lets
    already-catalogued packages keep their existing rows; on a *successful*
    manifest fetch, catalogue rows whose id no longer appears in
    ``images.json`` are pruned (unless locally downloaded) so removed or
    renamed entries don't linger as stale duplicates. A failed per-package
    probe never deletes anything.
    """
    owns_client = client is None
    client = client or httpx.AsyncClient(follow_redirects=True)
    result = SyncResult()
    try:
        entries, err = await fetch_images_json(client)
        result.images_json_ok = err is None
        result.images_json_error = err

        by_id: dict[str, tuple[str, dict[str, Any]]] = {}
        for entry in entries:
            repo = _match_key(entry)
            if not repo:
                continue
            entry_id = entry.get("id")
            image_id = entry_id.strip() if isinstance(entry_id, str) and entry_id.strip() else repo
            by_id[image_id] = (repo, entry)

        for image_id, (repo, entry) in by_id.items():
            tag = entry.get("tag") if isinstance(entry.get("tag"), str) else None
            try:
                image = await probe_ghcr_package(repo, client=client, tag=tag, image_id=image_id)
            except (httpx.HTTPError, RuntimeError, ValueError) as exc:
                result.probe_errors[image_id] = str(exc)
                log.warning(
                    "runner_images.probe_failed id=%s repo=%s error=%s", image_id, repo, exc
                )
                continue
            image = _apply_manifest_metadata(image, entry)
            # `store.upsert` reads the tag table for the row it returns
            # BEFORE `set_tags` below writes this sync's freshly-probed
            # tags — reading upsert's return value straight would carry the
            # PREVIOUS sync's tags into the SyncResult (empty on a first
            # sync), so the /sync response — and its families payload —
            # would lag by one sync. Persist the fresh tags first, then
            # stamp them onto the row this call reports.
            stored = store.upsert(image)
            store.set_tags(image.id, image.tags)
            result.images.append(stored.model_copy(update={"tags": image.tags}))

        if result.images_json_ok:
            pruned = store.prune_absent(set(by_id))
            if pruned:
                log.info("runner_images.pruned_stale_rows count=%d", pruned)
    finally:
        if owns_client:
            await client.aclose()
    return result


__all__ = [
    "IMAGES_JSON_URL",
    "SyncResult",
    "fetch_images_json",
    "is_noise_tag",
    "probe_ghcr_package",
    "provenance_from_labels",
    "sort_tags_newest_first",
    "sync_runner_images",
]
