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
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Any

import httpx

from hal0.registry.runner_image import RunnerImage
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
    resp = await client.get(
        f"https://ghcr.io/v2/{repo}/tags/list",
        headers={"Authorization": f"Bearer {token}"},
        timeout=_HTTP_TIMEOUT_S,
    )
    resp.raise_for_status()
    payload = resp.json()
    tags = payload.get("tags")
    return [t for t in tags if isinstance(t, str)] if isinstance(tags, list) else []


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
) -> tuple[str | None, int | None]:
    """Return ``(digest, size_bytes)`` for one tag; either may be None.

    Unlike a bare HEAD (whose ``content-length`` is the size of the manifest
    *document*, a couple of KB), this GETs the manifest body and sums the
    config + layer blob sizes — the actual compressed image size, matching
    what GHCR's package UI reports. Multi-arch indexes are followed one
    level down to the linux/amd64 image manifest. The digest reported is
    the top-level one (what ``podman pull repo@digest`` would resolve).
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
                    size = _manifest_content_size(child.json())
                except ValueError:
                    size = None
    if size is None:
        length = resp.headers.get("content-length")
        size = int(length) if length and length.isdigit() else None
    return digest, size


async def probe_ghcr_package(
    repo: str,
    *,
    client: httpx.AsyncClient,
    tag: str | None = None,
    image_id: str | None = None,
) -> RunnerImage:
    """Anonymously resolve one GHCR package's latest tag + digest + size.

    ``tag`` pins a specific tag (from an ``images.json`` entry); when
    omitted, the newest-looking tag from ``tags/list`` is used, falling
    back to ``latest``. ``image_id`` sets the catalogue row id (the
    ``images.json`` short name, e.g. ``cpu``); defaults to the repo path
    for rows with no manifest entry.
    """
    token = await _ghcr_anon_token(repo, client=client)
    resolved_tag = tag
    if resolved_tag is None:
        tags = await _ghcr_list_tags(repo, token=token, client=client)
        resolved_tag = "latest" if "latest" in tags else (tags[-1] if tags else "latest")
    digest, size = await _ghcr_manifest_info(repo, resolved_tag, token=token, client=client)
    return RunnerImage(
        id=image_id or repo,
        image=f"ghcr.io/{repo}",
        tag=resolved_tag,
        digest=digest,
        size_bytes=size,
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
            result.images.append(store.upsert(image))

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
    "probe_ghcr_package",
    "sync_runner_images",
]
