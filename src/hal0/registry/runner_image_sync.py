"""Runner-image catalogue sync — ``images.json`` v2 merged with a GHCR probe.

The runner-images repo publishes ``images.json`` (schema
``hal0.runner-images.v2``): one entry per runner, ``id`` = the hal0 runner
key, plus ``title``, ``summary``, ``image`` (registry repo), ``tag`` and
``digest`` (the gated pin), ``backends``, ``lanes_gated``, ``family`` and
``tier``. A sync writes exactly one catalogue row per entry whose ``id`` is a
runner in :data:`hal0.runners.RUNNER_IMAGES` and prunes every other row.

Each row is then enriched by an anonymous GHCR probe of its repo: the
headline tag's size, the newest tags with their digests, and each tag's build
provenance (OCI labels ``org.opencontainers.image.source`` / ``.revision`` and
``dev.hal0.runner.patches`` from the image config blob). A failed probe keeps
the catalogue facts and records the error; it never drops the row.

The sync runs at ``hal0-api`` boot (background, non-blocking), during
``hal0 update`` (the ``runner-images`` component arm, best-effort), and on
demand (``POST /api/runner-images/sync``, ``hal0 runner-images sync``).
"""

from __future__ import annotations

import asyncio
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
_EXPECTED_SCHEMA = "hal0.runner-images.v2"

#: Newest tags probed per repo beyond the headline. Tags are immutable and
#: the catalogue already names the gated one, so this only feeds the tag
#: picker; the cap keeps a large upstream repo (ggml-org/llama.cpp carries
#: thousands of tags) to a bounded number of registry calls.
_MAX_EXTRA_TAGS = 10
#: At most this many GHCR requests in flight per runner (extra-tag probes),
#: and at most this many runners probed at once.
_PROBE_CONCURRENCY = 4
#: The whole GHCR probe phase of one sync gets this long. A runner still
#: being probed then keeps its last good registry facts, so a slow registry
#: cannot hold `hal0 update`'s component convergence for many minutes.
SYNC_PROBE_DEADLINE_S = 120.0

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
    #: Catalogue entries ignored: ``id`` -> reason (unknown runner, bad shape).
    skipped: dict[str, str] = field(default_factory=dict)
    #: True when the prune of absent rows was skipped (empty catalogue, or an
    #: entry could not be read), so a partial answer never deletes a row.
    prune_skipped: bool = False


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

#: Runner release tags (ADR-0028): an upstream release (``b11552``,
#: ``0.9.44``, ``20251121``) plus the runner-images build suffix
#: ``-r<recipe revision>``, and ggml-org's ``server-cuda-b<N>``.
_RUNNER_RELEASE_TAG_RE = re.compile(
    r"^(?:b(?P<build>\d+)|v?(?P<version>\d+(?:\.\d+)*))-r[0-9a-f]{7,40}$"
    r"|^server-cuda-b(?P<cuda>\d+)$"
)


def runner_release_key(tag: str) -> tuple[int, ...] | None:
    """The sort key of a runner release tag, or ``None`` for any other tag."""
    match = _RUNNER_RELEASE_TAG_RE.match(tag)
    if match is None:
        return None
    number = match.group("build") or match.group("cuda")
    if number is not None:
        return (int(number),)
    return tuple(int(p) for p in match.group("version").split("."))


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

    Four buckets, concatenated in this order:

    0. runner release tags (``b11552-re14329d``, ``0.9.44-re14329d``,
       ``server-cuda-b11541``; see :func:`runner_release_key`) — release
       descending, builds of one release in registry order;
    1. all-digit tags (the CI date/short-SHA-free shape, e.g. ``0824``) —
       numeric descending, so the freshest date-tag leads;
    2. semver-shaped tags (``v1.10.0`` / ``1.2.3``) — version-tuple
       descending (NOT lexicographic: ``v1.10.0`` beats ``v1.2.0``);
    3. everything else (``latest``, branch names, bare SHAs) — original
       registry order, untouched, as the stable last resort.

    Pure and exported: the sync path uses it for ``available_tags`` and the
    unpinned headline tag, and tests/UI helpers can call it directly.
    """
    runner: list[str] = []
    numeric: list[str] = []
    semver: list[str] = []
    rest: list[str] = []
    for tag in tags:
        if runner_release_key(tag) is not None:
            runner.append(tag)
        elif tag.isdigit():
            numeric.append(tag)
        elif _SEMVER_TAG_RE.match(tag):
            semver.append(tag)
        else:
            rest.append(tag)
    # Stable: two builds of the same release keep the registry's order.
    runner.sort(key=lambda t: runner_release_key(t) or (), reverse=True)
    numeric.sort(key=int, reverse=True)
    semver.sort(key=lambda t: tuple(int(p) for p in t.lstrip("v").split(".")), reverse=True)
    return runner + numeric + semver + rest


async def fetch_images_json(client: httpx.AsyncClient) -> tuple[list[Any], str | None]:
    """Fetch + validate ``images.json``.

    Returns ``(entries, error)`` — ``entries`` is ``[]`` and ``error`` is a
    human-readable string on any failure (network, non-200, bad JSON, a schema
    other than ``hal0.runner-images.v2``, non-list ``images``). A failed fetch
    never prunes anything.
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
        return [], f"images.json schema {schema!r} is not {_EXPECTED_SCHEMA!r}"
    entries = payload.get("images")
    if not isinstance(entries, list):
        return [], "images.json has no 'images' array"
    # Non-object entries are passed through: the sync counts them as
    # unreadable, which keeps a partial catalogue from pruning rows.
    return list(entries), None


def _str_list(value: Any) -> list[str]:
    if not isinstance(value, list):
        return []
    return [v.strip() for v in value if isinstance(v, str) and v.strip()]


def _opt_str(value: Any) -> str | None:
    return value.strip() if isinstance(value, str) and value.strip() else None


def catalogue_row(entry: dict[str, Any]) -> tuple[RunnerImage | None, str | None]:
    """One ``images.json`` v2 entry → ``(row, None)`` or ``(None, reason)``.

    The entry must name a registered runner (``id``), a registry repo
    (``image``, no tag) and a ``tag``. Pure; the GHCR probe enriches the row
    afterwards.
    """
    from hal0.runners import RUNNER_IMAGES

    key = _opt_str(entry.get("id"))
    if key is None:
        return None, "entry has no id"
    if key not in RUNNER_IMAGES:
        return None, f"{key!r} is not a runner this hal0 ships"
    image = _opt_str(entry.get("image"))
    tag = _opt_str(entry.get("tag"))
    if image is None or tag is None:
        return None, "entry needs both image and tag"
    if "@" in image or ":" in image.rsplit("/", 1)[-1]:
        return None, "image must be a bare repo (tag and digest are separate fields)"
    return (
        RunnerImage(
            id=key,
            image=image,
            tag=tag,
            digest=_opt_str(entry.get("digest")),
            title=_opt_str(entry.get("title")),
            summary=_opt_str(entry.get("summary")),
            family=_opt_str(entry.get("family")),
            tier=_opt_str(entry.get("tier")),
            backends=_str_list(entry.get("backends")),
            lanes_gated=_str_list(entry.get("lanes_gated")),
        ),
        None,
    )


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


async def probe_ghcr_package(row: RunnerImage, *, client: httpx.AsyncClient) -> RunnerImage:
    """Enrich one catalogue row from GHCR: size, newest tags, provenance.

    The headline ``row.tag`` is always probed; up to :data:`_MAX_EXTRA_TAGS`
    of the newest other tags are probed too. The catalogue's ``digest`` stays
    authoritative; a probe that sees a different digest for the headline tag
    logs a warning and keeps the catalogue value.
    """
    repo = _strip_registry_host(row.image)
    token = await _ghcr_anon_token(repo, client=client)
    try:
        tags = await _ghcr_list_tags(repo, token=token, client=client)
    except (httpx.HTTPError, ValueError) as exc:
        log.warning("runner_images.tags_list_failed repo=%s error=%s", repo, exc)
        tags = []
    available = sort_tags_newest_first(tags)
    digest, size, config_digest = await _ghcr_manifest_info(
        repo, row.tag, token=token, client=client
    )
    if row.digest and digest and digest != row.digest:
        log.warning(
            "runner_images.digest_mismatch id=%s tag=%s catalogue=%s registry=%s",
            row.id,
            row.tag,
            row.digest,
            digest,
        )
    now = datetime.now(UTC).isoformat()
    tag_rows = [
        RunnerImageTag(
            tag=row.tag,
            digest=row.digest or digest,
            size_bytes=size,
            last_seen=now,
            provenance=await _ghcr_tag_provenance(repo, config_digest, token=token, client=client),
        )
    ]
    limit = asyncio.Semaphore(_PROBE_CONCURRENCY)

    async def _probe_tag(t: str) -> RunnerImageTag:
        async with limit:
            try:
                t_digest, t_size, t_config = await _ghcr_manifest_info(
                    repo, t, token=token, client=client
                )
            except (httpx.HTTPError, RuntimeError, ValueError) as exc:
                log.warning("runner_images.tag_probe_failed repo=%s tag=%s error=%s", repo, t, exc)
                t_digest, t_size, t_config = None, None, None
            t_prov = await _ghcr_tag_provenance(repo, t_config, token=token, client=client)
        return RunnerImageTag(
            tag=t, digest=t_digest, size_bytes=t_size, last_seen=now, provenance=t_prov
        )

    extra = [t for t in available if t != row.tag][:_MAX_EXTRA_TAGS]
    tag_rows.extend(await asyncio.gather(*(_probe_tag(t) for t in extra)))
    return row.model_copy(
        update={
            "digest": row.digest or digest,
            "size_bytes": size,
            "available_tags": available,
            "tags": tag_rows,
        }
    )


async def sync_runner_images(
    store: RunnerImageStore, *, client: httpx.AsyncClient | None = None
) -> SyncResult:
    """Run one catalogue sync and persist the rows.

    A failed ``images.json`` fetch changes nothing. A successful one writes a
    row per valid entry (GHCR-enriched when the probe answers) and prunes
    every row the catalogue no longer lists, downloaded or not, unless the
    catalogue came back empty or with an entry it could not read (then nothing
    is pruned and :attr:`SyncResult.prune_skipped` is set). GHCR probes run a
    few at a time under :data:`SYNC_PROBE_DEADLINE_S`; a runner the deadline
    cuts off keeps its last good registry facts.
    """
    owns_client = client is None
    client = client or httpx.AsyncClient(follow_redirects=True)
    result = SyncResult()
    try:
        entries, err = await fetch_images_json(client)
        result.images_json_ok = err is None
        result.images_json_error = err
        if err is not None:
            log.warning("runner_images.catalogue_fetch_failed error=%s", err)
            return result

        from hal0.runners import RUNNER_IMAGES

        rows: dict[str, RunnerImage] = {}
        malformed = 0
        for position, entry in enumerate(entries):
            if not isinstance(entry, dict):
                result.skipped[f"entry {position}"] = "entry is not an object"
                malformed += 1
                continue
            row, reason = catalogue_row(entry)
            if row is None:
                entry_id = entry.get("id")
                result.skipped[str(entry_id or "?")] = reason or "invalid"
                # A runner a newer catalogue lists and this hal0 does not ship
                # is expected; only an entry this code cannot read makes the
                # answer partial.
                if not (isinstance(entry_id, str) and entry_id.strip()) or (
                    entry_id.strip() in RUNNER_IMAGES
                ):
                    malformed += 1
                continue
            rows[row.id] = row

        probed = await _probe_all(rows, client=client)
        for key, row in rows.items():
            outcome = probed.get(key)
            if not isinstance(outcome, RunnerImage):
                exc = outcome if outcome is not None else TimeoutError("probe deadline")
                result.probe_errors[key] = str(exc) or type(exc).__name__
                log.warning(
                    "runner_images.probe_failed id=%s repo=%s error=%s", key, row.image, exc
                )
                # Refresh the catalogue facts only; keep the registry facts the
                # last good probe recorded (size, tag list, per-tag digests).
                previous = store.get(key)
                if previous is not None and previous.image == row.image:
                    row = row.model_copy(
                        update={
                            "size_bytes": previous.size_bytes,
                            "available_tags": previous.available_tags,
                        }
                    )
                stored = store.upsert(row)
                if previous is not None and previous.image != row.image:
                    # The runner moved repositories: the old repo's per-tag
                    # digests and provenance describe nothing in the new one.
                    store.set_tags(key, [])
                    stored = stored.model_copy(update={"tags": []})
                result.images.append(stored)
                continue
            row = outcome
            # Persist the fresh tags first, then stamp them onto the row this
            # call reports (upsert's read-back would carry the previous sync's
            # tags otherwise).
            stored = store.upsert(row)
            store.set_tags(row.id, row.tags)
            result.images.append(stored.model_copy(update={"tags": row.tags}))

        # Prune only on a complete answer: an empty catalogue or one with an
        # entry this code could not read (a newer schema field, a typo
        # upstream) must not delete rows, downloaded ones included. An unknown
        # runner id from a newer catalogue does not block it.
        if rows and not malformed:
            pruned = store.prune_absent(set(rows))
            if pruned:
                log.info("runner_images.pruned_rows count=%d", pruned)
        else:
            result.prune_skipped = True
            log.warning(
                "runner_images.prune_skipped rows=%d skipped=%d", len(rows), len(result.skipped)
            )
    finally:
        if owns_client:
            await client.aclose()
    return result


async def _probe_all(
    rows: dict[str, RunnerImage], *, client: httpx.AsyncClient
) -> dict[str, RunnerImage | BaseException | None]:
    """Probe every row, a few at a time, under :data:`SYNC_PROBE_DEADLINE_S`.

    Returns ``key -> enriched row``, ``key -> the exception``, or
    ``key -> None`` when the deadline cut the probe off.
    """
    limit = asyncio.Semaphore(_PROBE_CONCURRENCY)

    async def _one(row: RunnerImage) -> RunnerImage:
        async with limit:
            return await probe_ghcr_package(row, client=client)

    tasks = {key: asyncio.ensure_future(_one(row)) for key, row in rows.items()}
    if not tasks:
        return {}
    _done, pending = await asyncio.wait(tasks.values(), timeout=SYNC_PROBE_DEADLINE_S)
    for task in pending:
        task.cancel()
    out: dict[str, RunnerImage | BaseException | None] = {}
    for key, task in tasks.items():
        if task in pending:
            out[key] = None
            continue
        exc = task.exception()
        if exc is not None and not isinstance(exc, (httpx.HTTPError, RuntimeError, ValueError)):
            raise exc
        out[key] = exc if exc is not None else task.result()
    if pending:
        await asyncio.gather(*pending, return_exceptions=True)
        log.warning("runner_images.probe_deadline runners=%d", len(pending))
    return out


def sync_catalogue_blocking(store: RunnerImageStore | None = None) -> SyncResult:
    """Run :func:`sync_runner_images` from synchronous code (no running loop).

    Used by the ``runner-images`` component arm during ``hal0 update``.
    """
    import asyncio

    return asyncio.run(sync_runner_images(store or RunnerImageStore()))


__all__ = [
    "IMAGES_JSON_URL",
    "SyncResult",
    "catalogue_row",
    "fetch_images_json",
    "is_noise_tag",
    "probe_ghcr_package",
    "provenance_from_labels",
    "sort_tags_newest_first",
    "sync_catalogue_blocking",
    "sync_runner_images",
]
