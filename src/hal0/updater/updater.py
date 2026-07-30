"""Self-update mechanism for hal0.

Updater handles the full update lifecycle:
  1. Check ``{HAL0_RELEASES_URL}`` (or ``https://releases.hal0.dev/{channel}.json``)
     for a newer version.
  2. Download tarball + cosign signature to ``/var/lib/hal0/cache/<version>/``.
  3. Verify the SHA-256 digest against the release manifest.
  4. ``cosign verify-blob`` against the exact GitHub Actions OIDC identity
     derived from the authenticated release kind and version.
  5. Extract to ``/usr/lib/hal0-<version>/`` (refuses non-empty paths).
  6. Run pending config migrations (``hal0.config.migrations.run_migrations``)
     when ``min_data_version`` advances the schema.
  7. Atomically swap the ``/usr/lib/hal0/current`` symlink using the
     POSIX ``symlink(tmp) + os.replace(tmp, current)`` pattern.
  8. Re-pip the swapped-in tree into the running venv (non-editable prod
     only, #495) — the venv imports hal0 from its own site-packages, so the
     symlink swap alone changes nothing until the code is reinstalled. A
     failed re-pip rolls the symlink back so ``current`` and the venv stay
     consistent. Skipped for editable/dev installs.
  9. Record the prior symlink target in ``/var/lib/hal0/hal0.previous`` for
     rollback. Slot units are NOT touched. The ``hal0-api.service`` restart
     is the route layer's job (``routes/updater._run_apply_job``), not this
     function — apply() swaps the tree and refreshes the venv.

Rollback reads ``/var/lib/hal0/hal0.previous``, atomic-swaps the
``current`` symlink back, and warns (without erroring) if the
``meta.schema_version`` on disk would be downgraded — forward-only
migrations are acceptable for v1.

See PLAN.md §9 (update mechanism), §17 risk #2 (cosign release pipeline
edge cases), and §5 Phase 5 milestone.
"""

from __future__ import annotations

import asyncio
import contextlib
import dataclasses
import hashlib
import json
import os
import re
import shutil
import subprocess
import sys
import tarfile
import tempfile
import time
from pathlib import Path
from typing import Any, Literal
from urllib.parse import urlparse

import structlog
from pydantic import BaseModel, Field, field_validator, model_validator

import hal0
from hal0.config import paths
from hal0.config.loader import (
    ConfigParseError,
    load_hal0_config,
    save_profiles_config,
    write_toml_atomic,
)
from hal0.config.migrations import latest_version, run_migrations
from hal0.errors import Hal0Error
from hal0.release.policy import ReleaseKind, ReleasePolicy, ReleaseTagError

log = structlog.get_logger(__name__)


# Read/install compatibility for nightly manifests published before the current
# 14-digit timestamp policy. This does not make older date-only tags publishable
# through ReleasePolicy; it only preserves existing manifest and staged-release reads.
_LEGACY_NIGHTLY_VERSION_RE = re.compile(r"^[0-9]+\.[0-9]+\.[0-9]+-nightly\.[0-9]{8}$")


# Client-pinned trust roots for authenticating release manifests. Admission is
# selected only from the locally requested channel, before any JSON is decoded.
# Stable and preview admit only their respective immutable tag grammars. Nightly
# is signed by the reusable release workflow as invoked from main, so its sole
# admitted identity is release.yml@refs/heads/main. After authenticated parsing,
# every manifest is bound to one exact release identity before artifact fields
# are consumed.
_MANIFEST_IDENTITY_PREFIX = (
    r"^https://github\.com/(Hal0ai|hal0ai)/hal0/"
    r"\.github/workflows/release\.yml@"
)
_MANIFEST_SIGNER_ISSUER = "https://token.actions.githubusercontent.com"
_STABLE_MANIFEST_ADMISSION_IDENTITY = _MANIFEST_IDENTITY_PREFIX + r"refs/tags/v\d+\.\d+\.\d+$"
_PREVIEW_MANIFEST_ADMISSION_IDENTITY = (
    _MANIFEST_IDENTITY_PREFIX + r"refs/tags/v\d+\.\d+\.\d+(-(alpha|beta|rc)\.(0|[1-9]\d*))?$"
)
_NIGHTLY_MANIFEST_IDENTITY = _MANIFEST_IDENTITY_PREFIX + r"refs/heads/main$"


def manifest_admission_identity(channel: str) -> str:
    """Return the first-verification identity for a trusted requested channel."""
    identities = {
        "stable": _STABLE_MANIFEST_ADMISSION_IDENTITY,
        "preview": _PREVIEW_MANIFEST_ADMISSION_IDENTITY,
        "nightly": _NIGHTLY_MANIFEST_IDENTITY,
    }
    try:
        return identities[channel]
    except KeyError as exc:
        raise ValueError(f"unknown requested channel {channel!r}") from exc


def exact_manifest_identity(release_kind: str, version: str) -> str:
    """Derive the sole accepted signer identity from authenticated release policy."""
    if release_kind == "nightly" and _LEGACY_NIGHTLY_VERSION_RE.fullmatch(version):
        return _NIGHTLY_MANIFEST_IDENTITY
    try:
        policy = ReleasePolicy.from_tag(f"v{version}")
    except ReleaseTagError as exc:
        raise ValueError(f"unsupported release version: {version!r}") from exc
    if policy.version != version or policy.kind != release_kind:
        raise ValueError(
            f"release kind {release_kind!r} does not match version policy {policy.kind!r}"
        )
    if policy.kind == "nightly":
        return _NIGHTLY_MANIFEST_IDENTITY
    escaped_tag = f"v{version}".replace(".", r"\.")
    return _MANIFEST_IDENTITY_PREFIX + f"refs/tags/{escaped_tag}$"


# ── Typed errors (system.update_*) ─────────────────────────────────────────────


class UpdateError(Hal0Error):
    """Generic updater envelope error."""

    code = "system.update_error"
    status = 500


class UpdateManifestInvalid(UpdateError):
    """Release manifest is missing required fields or has the wrong shape."""

    code = "system.update_manifest_invalid"
    status = 400


class UpdateDownloadError(UpdateError):
    """Tarball or signature could not be fetched."""

    code = "system.update_download_failed"
    status = 502


class UpdateVerifyError(UpdateError):
    """SHA-256 digest of the downloaded tarball did not match the manifest."""

    code = "system.update_verify_failed"
    status = 400


class UpdateCosignMissing(UpdateError):
    """The ``cosign`` binary is not installed on this host.

    Surfaced as a typed error with install hints — the updater does NOT
    fall back to unsigned acceptance under any circumstance.
    """

    code = "system.update_cosign_missing"
    status = 500


class UpdateCosignFailed(UpdateError):
    """``cosign verify-blob`` returned non-zero — signature is not trusted."""

    code = "system.update_cosign_failed"
    status = 400


class UpdateExtractError(UpdateError):
    """Tarball extraction failed (e.g. target dir not empty, IO error)."""

    code = "system.update_extract_failed"
    status = 500


class UpdateSwapError(UpdateError):
    """Atomic symlink swap failed."""

    code = "system.update_swap_failed"
    status = 500


class UpdateRollbackUnavailable(UpdateError):
    """No previous-version record exists — nothing to roll back to."""

    code = "system.update_rollback_unavailable"
    status = 400


# ── Release-manifest schema (pydantic) ─────────────────────────────────────────


def validate_release_version(version: str) -> str:
    """Return an exact supported updater version or raise ``ValueError``.

    ReleasePolicy remains the source of truth for every currently publishable
    version. The updater additionally accepts the documented, path-safe date-only
    nightly read/install form ``X.Y.Z-nightly.YYYYMMDD`` for already-issued
    manifests and staged releases.
    """
    if not isinstance(version, str):
        raise ValueError(f"release version must be a string, got {type(version).__name__}")
    try:
        policy = ReleasePolicy.from_tag(f"v{version}")
    except ReleaseTagError as exc:
        if _LEGACY_NIGHTLY_VERSION_RE.fullmatch(version):
            return version
        raise ValueError(f"unsupported release version: {version!r}") from exc
    if policy.version != version:
        raise ValueError(f"noncanonical release version: {version!r}")
    return policy.version


def _require_release_version(version: str, *, field: str) -> str:
    """Translate release-policy rejection into the updater's typed 400 error."""
    try:
        return validate_release_version(version)
    except ValueError as exc:
        raise UpdateManifestInvalid(
            f"{field} must be an exact supported release version",
            details={field: version, "error": str(exc)},
        ) from exc


class ReleaseManifest(BaseModel):
    """Schema-validated release-manifest payload.

    Mirrors the on-disk JSON shape documented in ``docs/internal/release-manifest.md``
    (``_schema = "hal0.releases.v1"``). Malformed manifests are rejected at
    fetch time so apply() never operates on a half-shaped payload.

    Extra fields are preserved (``extra = "allow"``) so future additions
    (release notes, etc.) round-trip without breaking older clients.
    """

    model_config = {"populate_by_name": True, "extra": "allow"}

    schema_id: Literal["hal0.releases.v1"] = Field(alias="_schema")
    version: str = Field(..., description="Release version, e.g. '0.1.1'.")
    channel: ReleaseKind = Field(default="stable", description="Release channel.")
    release_kind: ReleaseKind = Field(
        default="stable",
        description="Kind of release: stable, nightly, or preview.",
    )
    prerelease_stage: Literal["alpha", "beta", "rc"] | None = Field(
        default=None,
        description="Prerelease stage for preview releases (alpha/beta/rc).",
    )
    rollback_policy: Literal["safe", "backup-required", "blocked"] = Field(
        default="safe",
        description="Rollback policy for this release.",
    )
    upgrade_from: str = Field(
        default="",
        description="Version constraint for supported upgrade paths, e.g. '>=0.9.8'.",
    )
    operator_migrations: list[str] = Field(
        default_factory=list,
        description="Operator-visible migration steps required for this release.",
    )
    url: str = Field(..., description="Tarball download URL (https or file).")
    bundle_url: str = Field(
        ...,
        description=(
            "Sigstore bundle URL (cosign keyless OIDC). The bundle embeds the "
            "Fulcio certificate, the signature, and the Rekor transparency-log "
            "inclusion proof + Signed Entry Timestamp (SET). The SET is the "
            "trusted timestamp that lets ``cosign verify-blob --bundle`` succeed "
            "after the short-lived Fulcio cert has expired (see #1159)."
        ),
    )
    digest_sha256: str = Field(..., description="Hex sha256 of the tarball bytes.")
    signer_identity: str = Field(
        ...,
        description=(
            "Exact generated GitHub Actions OIDC subject regex for this release. "
            "Clients derive and enforce the same value from release_kind + version."
        ),
    )
    signer_issuer: str = Field(
        default="https://token.actions.githubusercontent.com",
        description="OIDC issuer URL.",
    )
    min_data_version: int = Field(
        default=1,
        ge=1,
        description="Minimum config schema version required after this update.",
    )
    revoked: bool = Field(
        default=False,
        description=(
            "True if this release has been yanked/withdrawn. A revoked latest "
            "is not recommended by ``hal0 update --check`` (see "
            "``docs/internal/release-manifest.md`` → 'Yanking a release'). "
            "Older manifests without this field parse as not-revoked."
        ),
    )
    revoked_reason: str = Field(
        default="",
        description="Human-readable explanation shown when ``revoked`` is true.",
    )
    released_at: str | None = Field(default=None, description="ISO-8601 release timestamp.")
    notes_url: str | None = Field(default=None, description="Release notes URL.")
    manifest_url: str | None = Field(default=None, description="Self-reference URL.")
    toolbox_images: dict[str, dict[str, Any]] = Field(
        default_factory=dict,
        description="Mirror of manifest.json's toolbox_images block.",
    )

    @field_validator("version")
    @classmethod
    def _version_is_supported(cls, v: str) -> str:
        return validate_release_version(v)

    @field_validator("digest_sha256")
    @classmethod
    def _digest_is_hex(cls, v: str) -> str:
        s = (v or "").strip().lower()
        if s.startswith("sha256:"):
            s = s.split(":", 1)[1]
        if not re.fullmatch(r"[0-9a-f]{64}", s):
            raise ValueError(f"digest_sha256 must be a 64-char hex string, got {v!r}")
        return s

    @model_validator(mode="after")
    def _validate_release_policy(self) -> ReleaseManifest:
        """Cross-field validation for preview/release-kind consistency.

        Rules:
        - preview requires prerelease_stage in (alpha, beta, rc) and channel="preview".
        - stable requires no prerelease_stage and may target stable or preview.
        - nightly requires no prerelease_stage and channel="nightly".
        - non-empty operator_migrations require rollback_policy in
          ("backup-required", "blocked").
        """
        if self.release_kind == "preview":
            if self.prerelease_stage is None:
                raise ValueError(
                    "preview release_kind requires a prerelease_stage (alpha, beta, or rc)"
                )
            if self.channel != "preview":
                raise ValueError(
                    f"preview release_kind requires channel='preview', got {self.channel!r}"
                )
        if self.release_kind == "stable":
            if self.prerelease_stage is not None:
                raise ValueError(
                    "stable release_kind must not have a "
                    f"prerelease_stage, got {self.prerelease_stage!r}"
                )
            if self.channel not in ("stable", "preview"):
                raise ValueError(
                    "stable release_kind requires channel='stable' or 'preview', "
                    f"got {self.channel!r}"
                )
        if self.release_kind == "nightly":
            if self.prerelease_stage is not None:
                raise ValueError(
                    "nightly release_kind must not have a "
                    f"prerelease_stage, got {self.prerelease_stage!r}"
                )
            if self.channel != "nightly":
                raise ValueError(
                    f"nightly release_kind requires channel='nightly', got {self.channel!r}"
                )
        if self.operator_migrations and self.rollback_policy not in (
            "backup-required",
            "blocked",
        ):
            raise ValueError(
                "non-empty operator_migrations requires rollback_policy "
                "to be 'backup-required' or 'blocked', "
                f"got {self.rollback_policy!r}"
            )
        return self


_ACCEPTED_RELEASE_KINDS: dict[str, frozenset[ReleaseKind]] = {
    "stable": frozenset({"stable"}),
    "preview": frozenset({"preview", "stable"}),
    "nightly": frozenset({"nightly"}),
}


def validate_manifest_for_channel(
    manifest: ReleaseManifest, requested_channel: str
) -> ReleaseManifest:
    """Return ``manifest`` when it is safe to consume for the requested channel."""
    if requested_channel not in _ACCEPTED_RELEASE_KINDS:
        raise ValueError(f"unknown requested channel {requested_channel!r}")
    if manifest.channel != requested_channel:
        raise ValueError(
            f"manifest channel {manifest.channel!r} does not match "
            f"requested channel {requested_channel!r}"
        )
    accepted_kinds = _ACCEPTED_RELEASE_KINDS[requested_channel]
    if manifest.release_kind not in accepted_kinds:
        raise ValueError(
            f"release kind {manifest.release_kind!r} is not accepted for "
            f"requested channel {requested_channel!r}"
        )
    return manifest


@dataclasses.dataclass(frozen=True)
class ReleaseInfo:
    """Typed result of ``Updater.check()``.

    Returned to the CLI / route layer so both surfaces agree on the
    available release shape without re-parsing the raw manifest.
    """

    current: str
    latest: str | None
    channel: str
    update_available: bool
    manifest_url: str
    digest_sha256: str | None
    signer_identity: str | None
    min_data_version: int | None
    notes_url: str | None = None
    revoked: bool = False
    revoked_reason: str = ""
    raw_manifest: dict[str, Any] = dataclasses.field(default_factory=dict)


# ── URL helpers + raw fetch ────────────────────────────────────────────────────


def releases_url(channel: str = "stable") -> str:
    """Return the release-manifest URL for ``channel``.

    Resolution:
      - ``HAL0_RELEASES_URL`` env var wins (tests + dev installs point at
        a local file or fake HTTP endpoint); the channel is appended as a
        ``?channel=`` parameter when the override is set and looks
        URL-shaped (http/https), so the test service can shard per channel.
      - Otherwise ``https://releases.hal0.dev/{channel}.json`` — the
        canonical per-channel layout from PLAN §9.
    """
    override = os.environ.get("HAL0_RELEASES_URL", "").strip()
    if override:
        # Preserve historical test behaviour: file:// or bare paths use the
        # override verbatim (a single static JSON file under the tmp dir).
        parsed = urlparse(override)
        if parsed.scheme in ("http", "https") and channel and channel != "stable":
            sep = "&" if "?" in override else "?"
            return f"{override}{sep}channel={channel}"
        return override
    # Production default: per-channel manifest at releases.hal0.dev.
    channel = (channel or "stable").strip() or "stable"
    return f"https://releases.hal0.dev/{channel}.json"


def _release_manifest_bundle_url(manifest_url: str) -> str:
    """Return the sibling Sigstore bundle URL for a channel manifest."""
    parsed = urlparse(manifest_url)
    if parsed.scheme in ("http", "https", "file"):
        return parsed._replace(path=f"{parsed.path}.bundle").geturl()
    return f"{manifest_url}.bundle"


async def _fetch_release_manifest_bytes(channel: str = "stable") -> bytes:
    """Fetch the exact manifest bytes so their sibling bundle can verify them."""
    url = releases_url(channel)
    parsed = urlparse(url)
    if parsed.scheme in ("", "file"):
        path = parsed.path if parsed.scheme == "file" else url
        try:
            return Path(path).read_bytes()
        except OSError as exc:
            raise OSError(f"could not read release manifest at {path}: {exc}") from exc

    import httpx

    try:
        async with httpx.AsyncClient(timeout=10.0) as client:
            resp = await client.get(url)
    except httpx.HTTPError as exc:
        raise OSError(f"release manifest fetch failed for {url}: {exc}") from exc
    if resp.status_code != 200:
        raise OSError(f"release manifest fetch returned HTTP {resp.status_code} from {url}")
    return resp.content


def _decode_release_manifest(raw_bytes: bytes, url: str) -> dict[str, Any]:
    """Decode exact fetched bytes as a JSON object."""
    try:
        raw = json.loads(raw_bytes)
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ValueError(f"release manifest at {url} is not valid JSON: {exc}") from exc
    if not isinstance(raw, dict):
        raise ValueError(f"release manifest at {url} must be a JSON object")
    return raw


async def fetch_release_manifest(channel: str = "stable") -> dict[str, Any]:
    """Fetch and parse the release manifest for ``channel``.

    Returns the parsed JSON dict. Supports both ``http(s)://`` URLs (via
    httpx) and ``file://`` URLs / bare paths (for tests). Raises
    ``OSError`` on transport failures and ``ValueError`` on bad JSON so
    callers can produce typed envelopes.
    """
    url = releases_url(channel)
    return _decode_release_manifest(await _fetch_release_manifest_bytes(channel), url)


def _parse_manifest(raw: dict[str, Any]) -> ReleaseManifest:
    """Validate ``raw`` against ReleaseManifest, raising UpdateManifestInvalid."""
    try:
        return ReleaseManifest.model_validate(raw)
    except Exception as exc:  # pydantic ValidationError + anything malformed
        raise UpdateManifestInvalid(
            f"release manifest failed schema validation: {exc}",
            details={"error": str(exc)},
        ) from exc


def _version_tuple(v: str) -> tuple[int, ...]:
    """Parse a dotted version string into a sortable tuple.

    Mirrors the route layer's helper so a manifest version like
    ``"0.1.0-rc1"`` still orders correctly against ``__version__``.
    """
    parts: list[int] = []
    for piece in (v or "").split("."):
        try:
            parts.append(int(piece))
        except ValueError:
            num = "".join(c for c in piece if c.isdigit())
            parts.append(int(num) if num else 0)
    return tuple(parts) or (0,)


def _is_newer(candidate: str, current: str) -> bool:
    """Return True if PEP 440 version ``candidate`` is strictly newer than ``current``.

    Uses ``packaging.version.Version`` so pip-normalised forms (``0.8.0b3``) order
    correctly against tag-derived manifest forms (``0.8.1-beta.1``). The naive
    ``_version_tuple`` digit-parser conflated the beta number with the patch
    component (``0.8.0b3`` → ``(0, 8, 3)``), so every box on a ``0.8.0bN`` beta saw
    ``0.8.1-beta.1`` as "not newer" and ``hal0 update`` reported nothing to apply.
    Falls back to the tuple compare only when a string is not valid PEP 440.
    """
    try:
        from packaging.version import InvalidVersion, Version
    except Exception:  # packaging absent — keep the best-effort tuple compare
        return _version_tuple(candidate) > _version_tuple(current)
    try:
        return Version(candidate) > Version(current)
    except InvalidVersion:
        return _version_tuple(candidate) > _version_tuple(current)


# ── Atomic helpers ─────────────────────────────────────────────────────────────


def _atomic_symlink_swap(new_target: Path, link_path: Path) -> Path | None:
    """Atomically point ``link_path`` at ``new_target``.

    Returns the path the symlink previously pointed at (resolved relative
    to the link's parent), or ``None`` if there was no prior link.

    Uses ``os.symlink(new_target, tmp)`` + ``os.replace(tmp, link_path)``
    — the POSIX pattern for atomic symlink updates. ``os.replace`` is
    atomic across the rename even when the destination exists, which
    ``os.symlink`` is not (it would EEXIST).
    """
    link_path = Path(link_path)
    link_path.parent.mkdir(parents=True, exist_ok=True)

    prior: Path | None = None
    if link_path.is_symlink():
        try:
            prior = Path(os.readlink(link_path))
        except OSError:
            prior = None

    # Make a unique tmp name in the same directory so the rename is on the
    # same filesystem (otherwise os.replace is not atomic).
    tmp_path = link_path.with_name(f".{link_path.name}.swap-{os.getpid()}-{int(time.time_ns())}")
    # Defensive: if a leftover swap file exists from a prior crash, unlink.
    with contextlib.suppress(FileNotFoundError):
        os.unlink(tmp_path)

    os.symlink(str(new_target), str(tmp_path))
    try:
        os.replace(str(tmp_path), str(link_path))
    except OSError:
        with contextlib.suppress(FileNotFoundError):
            os.unlink(tmp_path)
        raise
    return prior


def _write_atomic_text(path: Path, content: str) -> None:
    """Tempfile + fsync + os.replace for short text payloads (hal0.previous)."""
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp_str = tempfile.mkstemp(prefix=f".{path.name}.", suffix=".tmp", dir=path.parent)
    tmp_path = Path(tmp_str)
    try:
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as f:
                f.write(content)
                f.flush()
                os.fsync(f.fileno())
        except BaseException:
            with contextlib.suppress(OSError):
                os.close(fd)
            raise
        os.replace(tmp_path, path)
        tmp_path = None  # type: ignore[assignment]
    finally:
        if tmp_path is not None:
            with contextlib.suppress(OSError):
                tmp_path.unlink(missing_ok=True)


# ── Download + verify ──────────────────────────────────────────────────────────


async def _download(url: str, dest: Path) -> None:
    """Download ``url`` to ``dest`` atomically (tempfile + os.replace).

    Supports ``http(s)://`` (httpx) and ``file://`` / bare paths (tests +
    LXC smoke against a local synthetic release). Raises
    ``UpdateDownloadError`` on transport failure.
    """
    dest = Path(dest)
    dest.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp_str = tempfile.mkstemp(prefix=f".{dest.name}.", suffix=".part", dir=dest.parent)
    tmp_path = Path(tmp_str)
    try:
        os.close(fd)
        parsed = urlparse(url)
        if parsed.scheme in ("", "file"):
            src = parsed.path if parsed.scheme == "file" else url
            try:
                shutil.copyfile(src, tmp_path)
            except OSError as exc:
                raise UpdateDownloadError(
                    f"could not copy release artifact from {src}: {exc}",
                    details={"url": url, "error": str(exc)},
                ) from exc
        else:
            import httpx

            try:
                async with (
                    httpx.AsyncClient(timeout=60.0, follow_redirects=True) as client,
                    client.stream("GET", url) as resp,
                ):
                    if resp.status_code != 200:
                        raise UpdateDownloadError(
                            f"download returned HTTP {resp.status_code}",
                            details={"url": url, "status": resp.status_code},
                        )
                    with open(tmp_path, "wb") as out:
                        async for chunk in resp.aiter_bytes(chunk_size=64 * 1024):
                            out.write(chunk)
            except httpx.HTTPError as exc:
                raise UpdateDownloadError(
                    f"download failed: {exc}",
                    details={"url": url, "error": str(exc)},
                ) from exc

        os.replace(tmp_path, dest)
        tmp_path = None  # type: ignore[assignment]
    finally:
        if tmp_path is not None:
            with contextlib.suppress(OSError):
                tmp_path.unlink(missing_ok=True)


def _sha256_file(path: Path) -> str:
    """Hex sha256 of a file's contents (streamed)."""
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()


def _verify_cosign(
    tarball: Path,
    bundle: Path,
    *,
    identity_regexp: str,
    issuer: str,
    job_id: str | None = None,
) -> None:
    """Invoke ``cosign verify-blob`` against the GitHub Actions OIDC identity.

    Raises:
        UpdateCosignMissing: ``cosign`` not on PATH.
        UpdateCosignFailed: signature invalid or identity mismatch.

    Keyless signing uses a short-lived (~10 min) Fulcio certificate. To
    verify a signature after that cert expires — i.e. every real install,
    which happens hours/days after the release was signed — cosign needs a
    trusted timestamp proving the signature was made while the cert was
    valid. That timestamp is the Rekor Signed Entry Timestamp (SET), which
    travels inside the Sigstore ``bundle`` (fetched from ``manifest.bundle_url``).
    ``--certificate-identity-regexp`` is checked against the cert SAN carried
    in the bundle. Verification is always mandatory — there is no bypass.
    """
    cosign = shutil.which("cosign")
    if not cosign:
        raise UpdateCosignMissing(
            "cosign is not installed; install from https://docs.sigstore.dev/cosign/installation/",
            details={
                "install_hint_arch": "pacman -S cosign  # or: paru -S cosign-bin",
                "install_hint_deb": "see https://docs.sigstore.dev/cosign/installation/",
            },
        )

    cmd = [
        cosign,
        "verify-blob",
        "--bundle",
        str(bundle),
        "--certificate-identity-regexp",
        identity_regexp,
        "--certificate-oidc-issuer",
        issuer,
        str(tarball),
    ]
    log.info("updater.cosign_verify_start", job_id=job_id, cmd=" ".join(cmd[:3]))
    try:
        proc = subprocess.run(
            cmd,
            check=False,
            capture_output=True,
            text=True,
            timeout=60.0,
        )
    except (OSError, subprocess.SubprocessError) as exc:
        raise UpdateCosignFailed(
            f"cosign invocation failed: {exc}",
            details={"error": str(exc)},
        ) from exc

    if proc.returncode != 0:
        raise UpdateCosignFailed(
            "cosign verify-blob rejected the signature",
            details={
                "returncode": proc.returncode,
                "stdout": proc.stdout[-2000:],
                "stderr": proc.stderr[-2000:],
                "identity_regexp": identity_regexp,
                "issuer": issuer,
            },
        )
    log.info("updater.cosign_verify_ok", job_id=job_id)


async def _fetch_verified_release_manifest(
    channel: str, *, job_id: str | None = None
) -> tuple[dict[str, Any], ReleaseManifest, str]:
    """Fetch and authenticate exact manifest bytes before parsing any JSON.

    The first identity depends only on the trusted requested channel. Once the
    admitted bytes pass schema and channel policy, they are rebound to the exact
    identity derived from authenticated ``release_kind`` + ``version``. Temporary
    files live outside the updater cache, so rejection cannot stage update state.
    """
    try:
        admission_identity = manifest_admission_identity(channel)
    except ValueError as exc:
        raise UpdateManifestInvalid(
            f"release manifest channel is invalid: {exc}",
            details={"channel": channel, "error": str(exc)},
        ) from exc

    url = releases_url(channel)
    try:
        raw_bytes = await _fetch_release_manifest_bytes(channel)
    except OSError as exc:
        raise UpdateError(
            f"could not fetch release manifest: {exc}",
            details={"channel": channel, "url": url, "error": str(exc)},
        ) from exc

    bundle_url = _release_manifest_bundle_url(url)
    with tempfile.TemporaryDirectory(prefix="hal0-manifest-") as work:
        manifest_path = Path(work) / "manifest.json"
        bundle_path = Path(work) / "manifest.json.bundle"
        manifest_path.write_bytes(raw_bytes)
        await _download(bundle_url, bundle_path)
        await asyncio.to_thread(
            _verify_cosign,
            manifest_path,
            bundle_path,
            identity_regexp=admission_identity,
            issuer=_MANIFEST_SIGNER_ISSUER,
            job_id=job_id,
        )

        try:
            raw = _decode_release_manifest(raw_bytes, url)
        except ValueError as exc:
            raise UpdateError(
                f"release manifest is not valid JSON: {exc}",
                details={"channel": channel, "url": url, "error": str(exc)},
            ) from exc
        manifest = _parse_manifest(raw)
        try:
            validate_manifest_for_channel(manifest, channel)
            exact_identity = exact_manifest_identity(manifest.release_kind, manifest.version)
        except ValueError as exc:
            raise UpdateManifestInvalid(
                f"release manifest is not accepted for channel {channel!r}: {exc}",
                details={"channel": channel, "error": str(exc)},
            ) from exc
        if manifest.signer_identity != exact_identity:
            raise UpdateManifestInvalid(
                "release manifest signer_identity does not match exact release identity",
                details={
                    "channel": channel,
                    "expected_signer_identity": exact_identity,
                    "manifest_signer_identity": manifest.signer_identity,
                },
            )
        if manifest.signer_issuer != _MANIFEST_SIGNER_ISSUER:
            raise UpdateManifestInvalid(
                "release manifest signer_issuer does not match the pinned issuer",
                details={
                    "channel": channel,
                    "expected_signer_issuer": _MANIFEST_SIGNER_ISSUER,
                    "manifest_signer_issuer": manifest.signer_issuer,
                },
            )

        if exact_identity != admission_identity:
            await asyncio.to_thread(
                _verify_cosign,
                manifest_path,
                bundle_path,
                identity_regexp=exact_identity,
                issuer=_MANIFEST_SIGNER_ISSUER,
                job_id=job_id,
            )

    return raw, manifest, url


# ── Extraction + migration helpers ─────────────────────────────────────────────


def _looks_like_hal0_install(path: Path) -> bool:
    """Heuristic: does ``path`` look like a prior hal0 tarball extraction?

    A safe quarantine candidate has either a top-level ``VERSION`` file
    or a ``pyproject.toml`` whose ``name`` is ``hal0ai`` (or the legacy
    pre-rename ``hal0``). We deliberately refuse to touch unrelated
    non-empty directories.
    """
    if (path / "VERSION").is_file():
        return True
    pp = path / "pyproject.toml"
    if pp.is_file():
        try:
            head = pp.read_text(encoding="utf-8", errors="replace")[:512]
        except OSError:
            return False
        return any(
            marker in head
            for marker in ('name = "hal0ai"', "name = 'hal0ai'", 'name = "hal0"', "name = 'hal0'")
        )
    return False


def _extract_tarball(tarball: Path, dest: Path, *, job_id: str | None = None) -> None:
    """Extract ``tarball`` to ``dest``.

    The tarball is expected to contain a top-level directory matching
    ``hal0-<version>/``; we strip that prefix to land files directly under
    ``dest`` (which the caller names ``/usr/lib/hal0-<version>/``).

    If ``dest`` already exists and looks like a prior hal0 extraction
    (has ``VERSION`` or a hal0 ``pyproject.toml``), it is renamed aside
    to ``dest.with_suffix(.stale-<unix-ts>)`` so a retry after a half-
    failed apply isn't permanently wedged. Unrelated non-empty dirs are
    still refused — we will not silently destroy whatever the operator
    parked there.

    Raises ``UpdateExtractError`` on filesystem issues, malformed
    tarballs, or unsafe paths.
    """
    dest = Path(dest)
    if dest.exists():
        if not dest.is_dir():
            raise UpdateExtractError(
                f"refusing to extract: {dest} exists and is not a directory",
                details={"path": str(dest)},
            )
        if any(dest.iterdir()):
            if not _looks_like_hal0_install(dest):
                raise UpdateExtractError(
                    f"refusing to extract over non-empty directory {dest}",
                    details={"path": str(dest)},
                )
            stale = dest.with_name(f"{dest.name}.stale-{int(time.time())}")
            try:
                dest.rename(stale)
            except OSError as exc:
                raise UpdateExtractError(
                    f"could not quarantine stale install dir {dest}: {exc}",
                    details={"path": str(dest), "quarantine": str(stale), "error": str(exc)},
                ) from exc
            log.warning(
                "updater.extract_quarantined_stale",
                job_id=job_id,
                original=str(dest),
                quarantine=str(stale),
            )
    dest.mkdir(parents=True, exist_ok=True)

    log.info("updater.extract_start", job_id=job_id, tarball=str(tarball), dest=str(dest))
    strip_prefix: str | None = None
    try:
        with tarfile.open(tarball, "r:*") as tf:
            members = tf.getmembers()
            if not members:
                raise UpdateExtractError(
                    "release tarball is empty",
                    details={"tarball": str(tarball)},
                )

            # Refuse absolute paths and parent-dir escapes (tar slip).
            for m in members:
                p = Path(m.name)
                if p.is_absolute() or ".." in p.parts:
                    raise UpdateExtractError(
                        f"unsafe path in tarball: {m.name!r}",
                        details={"tarball": str(tarball), "member": m.name},
                    )

            # Determine top-level prefix (first path component shared by all entries).
            top_levels = {Path(m.name).parts[0] for m in members if m.name and m.name != "."}
            if len(top_levels) == 1:
                strip_prefix = next(iter(top_levels))

            # Python 3.12+ supports filter='data' which blocks unsafe members.
            # We already vetted paths above, but pass it through for defence-in-depth.
            try:
                tf.extractall(path=dest, filter="data")  # type: ignore[arg-type]
            except TypeError:
                # Older Python without the filter kwarg — already vetted.
                tf.extractall(path=dest)
    except UpdateExtractError:
        raise
    except (tarfile.TarError, OSError) as exc:
        raise UpdateExtractError(
            f"failed to extract release tarball: {exc}",
            details={"tarball": str(tarball), "dest": str(dest), "error": str(exc)},
        ) from exc

    # If extractall landed everything under dest/<prefix>/..., flatten it.
    if strip_prefix:
        inner = dest / strip_prefix
        if inner.is_dir():
            for entry in list(inner.iterdir()):
                target = dest / entry.name
                if target.exists():
                    continue
                shutil.move(str(entry), str(target))
            with contextlib.suppress(OSError):
                inner.rmdir()

    log.info("updater.extract_ok", job_id=job_id, dest=str(dest))


def _maybe_run_config_migrations(
    min_data_version: int,
    *,
    job_id: str | None = None,
) -> tuple[int, int]:
    """Run forward config migrations if the release demands a newer schema.

    Reads ``hal0.toml``'s ``meta.schema_version``, walks
    ``hal0.config.migrations.run_migrations`` up to
    ``max(min_data_version, latest_version())``, and atomically writes
    the migrated TOML back.

    Returns ``(source_version, target_version)`` for breadcrumb logging.
    Skips entirely when the running schema is already ≥ target.
    """
    target = max(min_data_version or 1, latest_version())
    toml_path = paths.hal0_toml()

    if not toml_path.exists():
        log.info(
            "updater.migrations_skipped",
            job_id=job_id,
            reason="hal0.toml absent",
            target=target,
        )
        return (target, target)

    cfg = load_hal0_config(toml_path)
    source = int(getattr(cfg.meta, "schema_version", 1) or 1)
    if source >= target:
        log.info(
            "updater.migrations_noop",
            job_id=job_id,
            source=source,
            target=target,
        )
        return (source, source)

    raw = cfg.model_dump(mode="python")
    new_raw, new_version = run_migrations(raw, target_version=target)
    write_toml_atomic(toml_path, new_raw)
    log.info(
        "updater.migrations_applied",
        job_id=job_id,
        source=source,
        target=new_version,
    )
    return (source, new_version)


# ── Filesystem layout (paths-aware) ────────────────────────────────────────────


def _usr_lib_root() -> Path:
    """Return the parent of the ``current`` symlink.

    Production:  ``/usr/lib/hal0/``  (so ``current`` lives at ``/usr/lib/hal0/current``)
    HAL0_HOME:   ``$HAL0_HOME/usr-lib/hal0/``

    ``paths.usr_lib()`` returns ``.../hal0/current`` so the parent of that
    is the install root we need.
    """
    return paths.usr_lib().parent


def _versioned_install_dir(version: str) -> Path:
    """Return ``<usr_lib>/hal0-<version>/`` — where this release's tree lives."""
    root = _usr_lib_root()
    # ``current`` lives at ``<root>/current``; siblings are ``<root>/hal0-<version>/``.
    return root / f"hal0-{version}"


def _current_symlink() -> Path:
    """Return ``<usr_lib>/current`` — the atomic-swap target."""
    return _usr_lib_root() / "current"


def _editable_install_path() -> str | None:
    """Return the source-tree path if hal0 is a pip *editable* install, else None.

    Authoritative detection via PEP 610 installer metadata: pip records an
    editable install's source tree in the package's ``direct_url.json`` with
    ``dir_info.editable`` true. This is reliable even when the editable
    checkout is itself a git clone — the ``__file__``-outside-``sys.prefix``
    heuristic (below) misclassifies that as a git-tracked FHS install and lets
    ``hal0 update`` silently no-op while reporting success (audit 4.1). The
    recorded ``file://`` URL is returned as a plain path so a refusal can name
    exactly where the editable tree lives.
    """
    try:
        from importlib.metadata import distribution

        raw = distribution("hal0").read_text("direct_url.json")
    except Exception:
        return None
    if not raw:
        return None
    try:
        data = json.loads(raw)
    except ValueError:
        return None
    dir_info = data.get("dir_info")
    if not isinstance(dir_info, dict) or not dir_info.get("editable"):
        return None
    url = str(data.get("url") or "")
    parsed = urlparse(url)
    if parsed.scheme == "file":
        return parsed.path or url
    return url or None


def _is_editable_install() -> bool:
    """True when hal0 runs from an editable/dev checkout, not the FHS venv.

    Metadata (PEP 610 ``direct_url.json``) is authoritative — it catches an
    editable install cloned from git, which the ``__file__`` heuristic below
    would wave through as a git-tracked FHS install (audit 4.1). The heuristic
    is the fallback when installer metadata is absent (e.g. a bare source
    checkout on ``sys.path``).

    Returns ``False`` for git-tracked installs under the FHS layout
    (``/usr/lib/hal0/hal0-<version>/``) — those are a maintainer-only
    dev layout, not a pip-managed editable install.
    """
    if _editable_install_path() is not None:
        return True

    import hal0

    try:
        Path(hal0.__file__).resolve().relative_to(Path(sys.prefix).resolve())
        return False
    except ValueError:
        pass
    # Git-tracked FHS installs: code lives in a versioned dir under
    # /usr/lib/hal0/ — not truly editable (maintainer-only dev layout).
    return not _is_git_install()


def _raise_if_editable_install() -> None:
    """Hard-refuse an update when hal0 runs from an editable/dev install.

    ``apply()`` / ``commit()`` manipulate the FHS layout (the
    ``/usr/lib/hal0/current`` symlink + the shared venv's site-packages),
    none of which exist in an editable checkout — so proceeding would extract
    a tree that is never imported and report a phantom success (audit 4.1).
    The single chokepoint for that refusal; detection is metadata-driven so an
    editable install cloned from git is caught too.
    """
    if not _is_editable_install():
        return
    import hal0

    path = _editable_install_path() or str(Path(hal0.__file__).resolve().parent)
    raise UpdateError(
        f"hal0 is installed in editable mode from {path}. "
        "Install from release wheel with `pip install hal0`.",
        details={
            "editable_path": path,
            "hint": "for a dev checkout run 'git pull && pip install -e .' to update",
        },
    )


def _is_git_install() -> bool:
    """True when hal0 is installed from a git clone under the FHS layout.

    Detects versioned directories like ``/usr/lib/hal0/hal0-0.9.4/`` that
    contain a ``.git`` directory (or are a git worktree) — a maintainer-only
    dev layout, distinct from a pip editable install.
    """
    import hal0

    here = Path(hal0.__file__).resolve()
    return any((parent / ".git").is_dir() for parent in here.parents)


def _reinstall_into_venv(install_dir: Path, *, job_id: str | None = None) -> None:
    """``pip install --force-reinstall <install_dir>`` into the running venv.

    apply() swaps the ``current`` symlink but the venv imports hal0 from its own
    site-packages, so the swap alone changes nothing until the code is
    reinstalled. Installs the release's full dependency set (deliberately NO
    ``--no-deps``, #12): with ``--no-deps`` a release that adds or bumps a
    ``[project.dependencies]`` entry silently never installs it — pip reports
    success and the gap only surfaces as a deferred ``ImportError`` the first
    time the new code path runs. Raises ``UpdateError`` on a non-zero pip exit.
    """
    cmd = [
        sys.executable,
        "-m",
        "pip",
        "install",
        "--force-reinstall",
        str(install_dir),
    ]
    log.info("updater.reinstall_start", job_id=job_id, install_dir=str(install_dir))
    proc = subprocess.run(cmd, capture_output=True, text=True)
    if proc.returncode != 0:
        raise UpdateError(
            f"pip reinstall of {install_dir} failed (rc={proc.returncode})",
            details={
                "install_dir": str(install_dir),
                "returncode": proc.returncode,
                "stderr": proc.stderr[-2000:],
            },
        )
    log.info("updater.reinstall_ok", job_id=job_id, install_dir=str(install_dir))


def _previous_record() -> Path:
    """Return ``/var/lib/hal0/hal0.previous`` — the rollback breadcrumb."""
    return paths.var_lib() / "hal0.previous"


def _cache_dir(version: str) -> Path:
    """Return ``/var/lib/hal0/cache/<version>/`` — the per-release download cache."""
    return paths.var_lib() / "cache" / version


def _manifest_cache_path(version: str) -> Path:
    """Where :meth:`Updater.prepare` stashes the verified manifest for commit()."""
    return _cache_dir(version) / "manifest.json"


def _load_cached_manifest(version: str) -> dict[str, Any]:
    """Read the manifest cached by :meth:`Updater.prepare`.

    ``commit()`` needs ``min_data_version`` without re-fetching (a re-fetch could
    resolve a *newer* release than the one that was prepared+verified). Raises
    ``UpdateError`` if the cache is missing — i.e. commit was called without a
    prior prepare for this version.
    """
    path = _manifest_cache_path(version)
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        raise UpdateError(
            f"no staged manifest for {version} — call prepare() before commit()",
            details={"version": version, "path": str(path), "error": str(exc)},
        ) from exc


#: Cap release-notes markdown so a hostile/oversized tree can't blow up the job
#: record or the CLI render. Notes are informational, not a transport.
_MAX_NOTES_BYTES = 64 * 1024


def _read_release_notes(install_dir: Path) -> dict[str, Any]:
    """Read release notes shipped INSIDE the (cosign-verified) release tree.

    ``RELEASE_NOTES.md`` (markdown) and an optional ``release.json``
    (``{highlights, breaking, migrations}``) at the tree root. Both are optional
    — older releases without them yield empty notes (graceful). Because they live
    in the extracted tarball, they are covered by the sha256 + cosign
    verification, unlike a manifest ``notes_url`` fetched over plain TLS — so what
    the operator reviews before commit is exactly what was signed.
    """
    markdown = ""
    md_path = install_dir / "RELEASE_NOTES.md"
    if md_path.is_file():
        with contextlib.suppress(OSError):
            markdown = md_path.read_text(encoding="utf-8", errors="replace")[:_MAX_NOTES_BYTES]

    highlights: list[str] = []
    breaking: list[str] = []
    migrations: list[str] = []
    json_path = install_dir / "release.json"
    if json_path.is_file():
        with contextlib.suppress(OSError, ValueError):
            data = json.loads(json_path.read_text(encoding="utf-8"))
            if isinstance(data, dict):
                highlights = [str(x) for x in (data.get("highlights") or [])][:50]
                breaking = [str(x) for x in (data.get("breaking") or [])][:50]
                migrations = [str(x) for x in (data.get("migrations") or [])][:50]
    return {
        "markdown": markdown,
        "highlights": highlights,
        "breaking": breaking,
        "migrations": migrations,
    }


# ── Seed-profile merge helpers ─────────────────────────────────────────────────


def ensure_seed_profiles(*, job_id: str | None = None) -> int:
    """Prune materialised seed profiles from /etc/hal0/profiles.toml.

    Seeds are **virtual** — overlaid from code (:data:`SEED_PROFILES`) on every
    :func:`load_profiles_config` and never persisted (:func:`save_profiles_config`
    strips them).  Older installers wrote every seed inline, which froze stale
    seed definitions on upgrade (a re-tuned seed never reached an existing
    install).  This migration rewrites the on-disk catalog to operator
    (non-seed) profiles only, so the shipped seed definition is authoritative
    again on the next load.  If the file is absent nothing is written (the
    overlay serves the seeds in-memory).

    Data-safety (this rewrite is the one destructive step of the virtual-seed
    design, so it hedges):

    * The pre-prune file is copied to ``profiles.toml.pre-virtual-seeds.bak``
      once (never overwritten on re-runs), so nothing is unrecoverable.
    * A seed-named entry whose content DIFFERS from the code seed is **rescued
      to ``<name>-custom``**, not deleted.  Two real cases: an operator
      hand-edited a seed table in the TOML (previously honoured by the #838
      additive merge), or an operator created a profile under a name that only
      became a seed in a later release (e.g. ``embed``/``rerank``).  Slots
      keep referencing ``<name>`` (now the code seed) — the rescue preserves
      the operator's content under the new name and the log says so loudly.
    * A seed-named entry byte-identical to the code seed is just a stale
      materialisation — pruned without rescue.

    Args:
        job_id: Optional breadcrumb for structured-log tracing.

    Returns:
        Number of materialised seed entries handled (pruned + rescued).
    """
    import shutil

    from hal0.config.loader import _read_toml
    from hal0.config.schema import SEED_PROFILES, ProfileConfig, ProfilesConfig

    target = paths.profiles_toml()
    if not target.exists():
        # Fresh install — no on-disk file yet; load_profiles_config already
        # returns the seeds in-memory on every call. No write needed.
        log.info("updater.seed_profiles_noop", job_id=job_id, reason="profiles.toml absent")
        return 0

    # Load the raw on-disk catalog directly so we can detect exactly which
    # seed keys are materialised before deciding whether to write anything.
    raw = _read_toml(target)
    try:
        cfg = ProfilesConfig.model_validate(raw)
    except Exception as exc:
        raise ConfigParseError(
            f"failed to validate profiles.toml during seed merge at {target}: {exc}",
            details={"path": str(target), "reason": str(exc)},
        ) from exc

    stale = [key for key in cfg.profile if key in SEED_PROFILES]
    if not stale:
        log.info("updater.seed_profiles_noop", job_id=job_id, reason="no materialised seeds")
        return 0

    # One-time backup before the only destructive rewrite of this migration.
    backup = target.with_name(target.name + ".pre-virtual-seeds.bak")
    if not backup.exists():
        try:
            shutil.copy2(target, backup)
            log.info("updater.seed_profiles_backup", job_id=job_id, path=str(backup))
        except OSError as exc:
            log.warning("updater.seed_profiles_backup_failed", job_id=job_id, error=str(exc))

    pruned: list[str] = []
    rescued: dict[str, str] = {}
    for key in stale:
        entry = cfg.profile[key]
        seed = ProfileConfig.model_validate(SEED_PROFILES[key])
        if entry == seed:
            # Byte-identical stale materialisation — safe to drop.
            pruned.append(key)
            continue
        # Divergent content: operator-authored (hand-edit, or a name that only
        # later became a seed). Rescue under <name>-custom (suffix until free).
        rescue = f"{key}-custom"
        n = 2
        while rescue in cfg.profile or rescue in SEED_PROFILES:
            rescue = f"{key}-custom{n}"
            n += 1
        cfg.profile[rescue] = entry
        rescued[key] = rescue

    save_profiles_config(cfg, path=target)  # strips all seed-named keys
    log.info(
        "updater.seed_profiles_pruned",
        job_id=job_id,
        pruned=pruned,
        rescued=rescued,
        count=len(stale),
        backup=str(backup),
    )
    if rescued:
        log.warning(
            "updater.seed_profiles_rescued_customs",
            job_id=job_id,
            rescued=rescued,
            note=(
                "profiles with seed names but non-seed content were renamed; "
                "slots referencing the original name now use the built-in seed — "
                "repoint them at the -custom profile if the old behaviour is wanted"
            ),
        )
    return len(stale)


def clear_stale_mtp_overrides(*, job_id: str | None = None, registry: Any = None) -> int:
    """Clear crash-only ``mtp = true`` slot overrides (upgrade migration).

    An explicit slot ``mtp = true`` is honored literally at launch — it is the
    escape hatch for MTP-capable models the eligibility heuristics miss. But a
    force-on pointing at a model with NO MTP layers makes llama-server exit at
    load ("context type MTP requested but model doesn't contain MTP layers").
    Such overrides are typically debris from the pre-separation binary MTP
    pill or an old stack apply, left behind by a later model swap and masked
    for months by stale baked units.

    For every slot with ``mtp = true`` whose bound model is registry-resolvable
    and NOT MTP-eligible, drop the override (→ AUTO) and log loudly. Slots are
    left untouched when the model can't be resolved (can't judge, stay
    conservative), when the override is ``false``/absent (harmless), or when
    the model is eligible (deliberate force-on — the escape hatch stands).

    Args:
        job_id: Optional breadcrumb for structured-log tracing.
        registry: Model-registry override for tests (anything with
            ``get(model_id)`` returning an object with ``model_dump()``).
            ``None`` uses the real :class:`~hal0.registry.store.ModelRegistry`.

    Returns:
        Number of slot overrides cleared.
    """
    import tomllib

    from hal0.config.loader import write_toml_atomic
    from hal0.config.paths import slots_config_dir
    from hal0.model_meta import model_is_mtp_eligible

    if registry is None:
        from hal0.registry.store import ModelRegistry

        registry = ModelRegistry()

    # Raw-TOML surgery, deliberately schema-free: slot TOMLs exist in two
    # shapes on real boxes (flat manager-written keys vs the nested ``[slot]``
    # table of the installer seeds), and a migration must not depend on the
    # current SlotConfig validating either. Only the ``mtp`` key is touched,
    # and a file is rewritten only when it actually changes.
    cleared = 0
    slots_dir = slots_config_dir()
    for toml_path in sorted(slots_dir.glob("*.toml")) if slots_dir.is_dir() else []:
        slot_name = toml_path.stem
        try:
            raw = tomllib.loads(toml_path.read_text(encoding="utf-8"))
        except Exception as exc:
            log.warning("updater.mtp_migration_slot_unreadable", slot=slot_name, error=str(exc))
            continue
        # ``mtp`` lives top-level (flat shape) or under [slot] (nested shape).
        holder = raw
        if not isinstance(holder.get("mtp"), bool) and isinstance(raw.get("slot"), dict):
            holder = raw["slot"]
        if holder.get("mtp") is not True:
            continue
        model_table = raw.get("model")
        model_id = model_table.get("default", "") if isinstance(model_table, dict) else ""
        if not model_id:
            continue
        try:
            model = registry.get(model_id)
            info = model.model_dump() if hasattr(model, "model_dump") else dict(model)
        except Exception:
            # Unresolvable model — can't judge eligibility; leave the override.
            continue
        info.setdefault("_model_key", model_id)
        if model_is_mtp_eligible(info):
            continue
        del holder["mtp"]  # absent = AUTO (TOML has no null)
        try:
            write_toml_atomic(toml_path, raw)
        except Exception as exc:
            log.warning("updater.mtp_migration_write_failed", slot=slot_name, error=str(exc))
            continue
        cleared += 1
        log.warning(
            "updater.mtp_force_on_cleared",
            job_id=job_id,
            slot=slot_name,
            model=model_id,
            note=(
                "slot forced MTP on but the model has no MTP heads (would crash "
                "llama-server at load); override cleared to AUTO. If the model "
                "really ships MTP layers, tag it 'mtp' in the registry or re-force "
                "MTP in the slot drawer."
            ),
        )
    return cleared


def retag_stale_slot_images(*, job_id: str | None = None) -> int:
    """Retag slot ``image`` pins that are stale FORMER DEFAULTS (upgrade migration).

    Slot creation historically materialised the then-current default runner
    image into the slot TOML (``image = "..."``, top-level or under
    ``[slot]``). Those pins freeze the default at creation time, so a release
    that bumps :data:`~hal0.config.schema.DEFAULT_ROCMFPX_IMAGE` never reaches
    existing slots. For every slot whose pin is in
    :data:`~hal0.runners.STALE_RUNNER_IMAGE_REFS` — exactly equal to a
    known former default, so demonstrably NOT a deliberate operator pin —
    rewrite it to the current default and log loudly. Any other value is an
    intentional per-slot override (debug build, A/B test) and is never
    touched: the escape hatch stands.

    The same policy covers CUSTOM (non-seed-named) profiles in
    ``profiles.toml``: a custom profile is typically cloned from a seed, so
    its ``image`` field carries the same materialised-default debris. Only
    the ``image`` value is rewritten — the operator's flags/name are theirs.
    (Seed-NAMED profiles are ensure_seed_profiles' job, not ours.)

    Runs before :func:`rerender_slot_units` in the update flow, so the
    re-rendered units carry the new ref in the same pass; nothing is bounced —
    the new image applies (and is pulled by podman if absent) on each slot's
    next start.

    Returns:
        Number of pins retagged (slot TOMLs + custom profile entries).
    """
    import tomllib

    from hal0.config.loader import _read_toml, write_toml_atomic
    from hal0.config.paths import profiles_toml, slots_config_dir
    from hal0.runners import STALE_RUNNER_IMAGE_REFS, resolve_runner_image, runner_for_backend

    def resolve_default_image(backend: str | None, device_class: str | None = None) -> str:
        """Local alias — §7.1b / ML-4 HW-gated default via the runner registry."""
        return resolve_runner_image(runner_for_backend(backend, device_class))

    def _cfg_str(cfg: dict, key: str) -> str:
        """Read a string field top-level or nested under ``[slot]``."""
        v = cfg.get(key)
        if isinstance(v, str):
            return v
        slot = cfg.get("slot")
        if isinstance(slot, dict) and isinstance(slot.get(key), str):
            return slot[key]
        return ""

    def _backend_of(cfg: dict) -> str:
        be = _cfg_str(cfg, "backend")
        if be:
            return be
        dev = _cfg_str(cfg, "device")  # "gpu-rocm" / "gpu-vulkan" / "cpu" / "npu"
        return dev.split("-", 1)[1] if dev.startswith("gpu-") else ""

    def _device_class_of(cfg: dict) -> str:
        dev = _cfg_str(cfg, "device") or _cfg_str(cfg, "device_class")
        if dev == "cpu":
            return "cpu"
        return "gpu" if dev.startswith("gpu") else dev

    retagged = 0
    slots_dir = slots_config_dir()
    for toml_path in sorted(slots_dir.glob("*.toml")) if slots_dir.is_dir() else []:
        slot_name = toml_path.stem
        try:
            raw = tomllib.loads(toml_path.read_text(encoding="utf-8"))
        except Exception as exc:
            log.warning("updater.image_retag_slot_unreadable", slot=slot_name, error=str(exc))
            continue
        # An image REF is a top-level or [slot]-nested STRING. The ``[image]``
        # TOML table (image-gen settings, #599) shares the key and must be
        # ignored — same trap as providers.container._resolve_image_ref.
        holder: dict | None = None
        if isinstance(raw.get("image"), str):
            holder = raw
        elif isinstance(raw.get("slot"), dict) and isinstance(raw["slot"].get("image"), str):
            holder = raw["slot"]
        if holder is None or holder["image"] not in STALE_RUNNER_IMAGE_REFS:
            continue
        old_ref = holder["image"]
        # HW-gated target: rocmfpx on a Strix GPU lane, the lean toolbox
        # elsewhere. When the host/lane default already equals the pin (e.g. a
        # non-Strix box on the vulkan toolbox), it's a no-op — leave it be.
        new_ref = resolve_default_image(_backend_of(raw), _device_class_of(raw))
        if new_ref == old_ref:
            continue
        holder["image"] = new_ref
        try:
            write_toml_atomic(toml_path, raw)
        except Exception as exc:
            log.warning("updater.image_retag_write_failed", slot=slot_name, error=str(exc))
            continue
        retagged += 1
        log.warning(
            "updater.slot_image_retagged",
            job_id=job_id,
            slot=slot_name,
            old=old_ref,
            new=new_ref,
            note=(
                "slot image pin matched a stale former default runner; rolled to "
                "the current HW-gated default (applies on the slot's next start)"
            ),
        )

    # Custom (non-seed-named) profiles: same stale-former-default policy,
    # image field only.
    prof_path = profiles_toml()
    if prof_path.exists():
        try:
            raw = _read_toml(prof_path)
        except Exception as exc:
            log.warning("updater.image_retag_profiles_unreadable", error=str(exc))
            return retagged
        changed = False
        for name, entry in (raw.get("profile") or {}).items():
            if not isinstance(entry, dict):
                continue
            if entry.get("image") in STALE_RUNNER_IMAGE_REFS:
                old_ref = entry["image"]
                new_ref = resolve_default_image(entry.get("backend"), entry.get("device_class"))
                if new_ref == old_ref:
                    continue
                entry["image"] = new_ref
                changed = True
                retagged += 1
                log.warning(
                    "updater.profile_image_retagged",
                    job_id=job_id,
                    profile=name,
                    old=old_ref,
                    new=new_ref,
                    note=(
                        "custom profile image matched a stale former default "
                        "runner; rolled to the current HW-gated default (flags untouched)"
                    ),
                )
        if changed:
            try:
                write_toml_atomic(prof_path, raw)
            except Exception as exc:
                log.warning("updater.image_retag_profiles_write_failed", error=str(exc))
    return retagged


def rerender_slot_units(*, job_id: str | None = None) -> int:
    """Re-render every existing container slot unit through current code.

    A slot's systemd unit bakes the launch argv at load time. Updating hal0
    changes the code that WOULD render but not the file that DID — so
    ``systemctl restart``, crash-restarts, and reboots keep running pre-update
    flags until an operator remembers a hal0-level reload (field finding,
    CT105). This sweep rewrites each on-disk unit via the same plan path a
    load uses and batches one ``daemon-reload`` — it never enables, starts, or
    restarts anything, so serving is not bounced; the fresh argv applies on
    the next start from ANY path. The dashboard's drift indicator covers the
    remaining "process still running old argv" window.

    Per-slot failures (unresolvable model/profile, malformed TOML) log and
    skip — a bad slot must never wedge an update.

    Returns the number of unit files rewritten.
    """
    import tomllib

    from hal0.config.paths import slots_config_dir
    from hal0.providers.container import ContainerProvider, _best_effort_model_info

    provider = ContainerProvider()
    rewritten = 0
    slots_dir = slots_config_dir()
    for toml_path in sorted(slots_dir.glob("*.toml")) if slots_dir.is_dir() else []:
        slot_name = toml_path.stem
        try:
            raw = tomllib.loads(toml_path.read_text(encoding="utf-8"))
            # Slot TOMLs exist flat (manager-written) or with a [slot] table
            # (installer seeds); hoist the nested shape to the flat one the
            # provider consumes. [model]/[server] tables pass through as-is.
            cfg: dict[str, Any] = {**raw, **(raw.get("slot") or {})}
            cfg.pop("slot", None)
            cfg.setdefault("name", slot_name)
            # Same registry-backed, never-raising resolver the preview path
            # uses — a registry miss degrades to a minimal path dict.
            model_info = _best_effort_model_info(cfg, None)
            if provider.rerender_unit_sync(cfg, model_info):
                rewritten += 1
                log.info("updater.unit_rerendered", job_id=job_id, slot=slot_name)
        except Exception as exc:
            log.warning(
                "updater.unit_rerender_skipped",
                job_id=job_id,
                slot=slot_name,
                error=str(exc),
            )
    if rewritten:
        try:
            provider.daemon_reload()
        except Exception as exc:
            log.warning("updater.unit_rerender_daemon_reload_failed", job_id=job_id, error=str(exc))
        log.info("updater.unit_rerender_complete", job_id=job_id, rewritten=rewritten)
    return rewritten


# ── Updater class ──────────────────────────────────────────────────────────────


class Updater:
    """Atomic self-update with cosign-verified releases and one-step rollback.

    All methods are async; call from asyncio context or via asyncio.run().
    The class is a stable seam — the API route layer calls these methods,
    so Team C's route surface stays unchanged.
    """

    def __init__(self, channel: str = "stable", job_id: str | None = None) -> None:
        """Initialise the updater.

        Args:
            channel: Release channel — "stable" (default).
            job_id: Optional background-job id used to thread structured
                log breadcrumbs through to the status endpoint.
        """
        self.channel = channel
        self.job_id = job_id

    # ── check ──────────────────────────────────────────────────────────────────

    async def check(self, channel: str | None = None) -> ReleaseInfo:
        """Check for a newer version on the configured release channel.

        Fetches the release manifest, validates it against the
        ``ReleaseManifest`` schema, and compares against ``hal0.__version__``.

        Returns a ``ReleaseInfo`` dataclass; the route layer constructs the
        wire JSON from this so the CLI + API surface stay in lock-step.

        Raises:
            UpdateError: Manifest could not be fetched or parsed.
            UpdateManifestInvalid: Manifest is missing required fields.
        """
        ch = channel or self.channel
        raw, manifest, url = await _fetch_verified_release_manifest(ch, job_id=self.job_id)

        latest = manifest.version
        revoked = manifest.revoked
        revoked_reason = manifest.revoked_reason
        # A revoked (yanked/withdrawn) latest is never recommended — the
        # operator should not be nudged toward a release we've pulled. The
        # version is still surfaced (revoked + reason) so the dashboard can
        # explain why no update is offered. See docs/internal/release-manifest.md.
        update_available = bool(latest) and not revoked and _is_newer(latest, hal0.__version__)
        if revoked and bool(latest):
            log.warning(
                "updater.latest_revoked",
                version=latest,
                channel=ch,
                reason=revoked_reason,
            )
        return ReleaseInfo(
            current=hal0.__version__,
            latest=latest or None,
            channel=ch,
            update_available=update_available,
            manifest_url=url,
            digest_sha256=manifest.digest_sha256,
            signer_identity=manifest.signer_identity,
            min_data_version=manifest.min_data_version,
            notes_url=manifest.notes_url,
            revoked=revoked,
            revoked_reason=revoked_reason,
            raw_manifest=raw,
        )

    # ── apply ──────────────────────────────────────────────────────────────────

    async def prepare(self, version: str | None = None) -> dict[str, Any]:
        """Download, verify, and STAGE ``version`` (or latest) — without activating.

        Runs §9 steps **1-6** only:

          1. Fetch + schema-validate the release manifest.
          2. Confirm the target version (caller-pinned or manifest.version).
          3. Download tarball + signature to ``/var/lib/hal0/cache/<version>/``.
          4. SHA-256 verify against the manifest digest.
          5. Cosign verify-blob against the GH Actions OIDC identity.
          6. Extract to ``/usr/lib/hal0-<version>/`` (refuse non-empty).

        Nothing about the running system changes: the ``current`` symlink, the
        venv, and ``/etc/hal0`` are untouched, so an abandoned prepare is
        discarded by deleting the staged tree. The verified manifest is cached
        (``<cache>/manifest.json``) so :meth:`commit` reads ``min_data_version``
        without a re-fetch, and release notes are read from the *verified* tree
        so what the operator reviews before commit is exactly what was signed.

        Returns ``{version, install_dir, cache_dir, notes}``.

        Raises:
            UpdateError + subclasses on any step failure. Partial-state
            artifacts (tempfiles, half-extracted dirs) are cleaned up.
        """
        # Guard: hard-refuse on editable/dev installs.  apply() manipulates
        # the FHS layout (/usr/lib/hal0/current symlink + venv site-packages)
        # which does not exist in an editable checkout.  Continuing would
        # silently extract a tarball that is never actually loaded.
        _raise_if_editable_install()

        # Step 1: fetch, validate, and authenticate the manifest itself.
        log.info("updater.prepare_start", job_id=self.job_id, channel=self.channel, pinned=version)
        raw, manifest, _ = await _fetch_verified_release_manifest(self.channel, job_id=self.job_id)

        # Step 2: treat the optional version as an optimistic exact pin. It is
        # never a historical resolver or a caller-controlled staging label:
        # authenticated manifest.version remains the sole target authority.
        requested_version = (
            _require_release_version(version, field="requested_version")
            if version is not None
            else None
        )
        target_version = _require_release_version(manifest.version, field="manifest_version")
        if requested_version is not None and requested_version != target_version:
            raise UpdateManifestInvalid(
                "requested version does not match authenticated channel manifest",
                details={
                    "channel": self.channel,
                    "requested_version": requested_version,
                    "manifest_version": target_version,
                },
            )

        # Residual: concurrent prepares for the same version share cache/install
        # paths and are not serialized. A lock redesign is intentionally out of
        # scope; signed immutable same-version assets limit current release risk
        # because both prepares authenticate the same publisher-pinned bytes.

        # Step 3: download tarball + Sigstore bundle (survives cert expiry, #1159).
        cache = _cache_dir(target_version)
        cache.mkdir(parents=True, exist_ok=True)
        tarball_path = cache / f"hal0-{target_version}.tar.gz"
        bundle_path = cache / f"hal0-{target_version}.tar.gz.bundle"
        log.info(
            "updater.download_start",
            job_id=self.job_id,
            version=target_version,
            url=manifest.url,
        )
        await _download(manifest.url, tarball_path)
        await _download(manifest.bundle_url, bundle_path)
        log.info(
            "updater.download_ok",
            job_id=self.job_id,
            tarball=str(tarball_path),
            bundle=str(bundle_path),
        )

        # Step 4: sha256 verify.
        got_digest = _sha256_file(tarball_path)
        if got_digest != manifest.digest_sha256:
            raise UpdateVerifyError(
                f"sha256 digest mismatch (expected {manifest.digest_sha256}, got {got_digest})",
                details={
                    "expected": manifest.digest_sha256,
                    "got": got_digest,
                    "tarball": str(tarball_path),
                },
            )
        log.info("updater.sha256_ok", job_id=self.job_id, digest=got_digest)

        # Step 5: verify against the identity derived from authenticated release
        # policy, never a manifest-selected broader expression.
        expected_identity = exact_manifest_identity(manifest.release_kind, manifest.version)
        await asyncio.to_thread(
            _verify_cosign,
            tarball_path,
            bundle_path,
            identity_regexp=expected_identity,
            issuer=_MANIFEST_SIGNER_ISSUER,
            job_id=self.job_id,
        )

        # Step 6: extract. `_extract_tarball` quarantines a prior hal0
        # extraction at the same path (see its docstring) so a retry
        # after a half-failed apply isn't permanently wedged.
        install_dir = _versioned_install_dir(target_version)
        await asyncio.to_thread(_extract_tarball, tarball_path, install_dir, job_id=self.job_id)

        # Cache the verified manifest so commit() reads min_data_version without a
        # re-fetch (which could resolve a *newer* release than the one just
        # verified), then read release notes from the *verified* tree — so what an
        # operator reviews before commit is exactly what cosign signed.
        _write_atomic_text(_manifest_cache_path(target_version), json.dumps(raw))
        notes = _read_release_notes(install_dir)
        log.info("updater.prepare_ok", job_id=self.job_id, version=target_version)
        return {
            "version": target_version,
            "install_dir": str(install_dir),
            "cache_dir": str(cache),
            "notes": notes,
        }

    async def commit(self, version: str) -> dict[str, Any]:
        """Activate a previously :meth:`prepare`d ``version`` (§9 steps 7-9+).

        Runs forward config migrations, prunes materialised seed profiles, clears
        stale mtp overrides, atomic-swaps the ``current`` symlink, re-pips the
        tree into the running venv, and re-renders slot units. Requires that
        :meth:`prepare` already staged this version (its ``install_dir`` and
        cached manifest must exist) — otherwise raises ``UpdateError``.

        Slot units are NOT restarted and ``hal0-api`` is not bounced here; the
        route layer (``routes/updater._run_commit_job``) try-restarts hal0-api
        fail-soft after a successful commit. Returns the same breadcrumb dict
        shape as the old single-step apply.
        """
        _raise_if_editable_install()
        target_version = _require_release_version(version, field="commit_version")
        install_dir = _versioned_install_dir(target_version)
        cache = _cache_dir(target_version)
        if not install_dir.exists():
            raise UpdateError(
                f"nothing staged for {target_version} — call prepare() before commit()",
                details={"version": target_version, "install_dir": str(install_dir)},
            )
        # Manifest cached by prepare(); needed for min_data_version below. Recheck
        # its authenticated target binding before any migration or activation.
        manifest = _parse_manifest(_load_cached_manifest(target_version))
        if manifest.version != target_version:
            raise UpdateManifestInvalid(
                "cached manifest version does not match prepared target",
                details={
                    "channel": self.channel,
                    "target_version": target_version,
                    "manifest_version": manifest.version,
                },
            )
        try:
            validate_manifest_for_channel(manifest, self.channel)
        except ValueError as exc:
            raise UpdateManifestInvalid(
                f"cached manifest is not accepted for channel {self.channel!r}: {exc}",
                details={"channel": self.channel, "error": str(exc)},
            ) from exc
        log.info("updater.commit_start", job_id=self.job_id, version=target_version)

        # Step 7: config migrations.
        migration_info: tuple[int, int]
        try:
            migration_info = await asyncio.to_thread(
                _maybe_run_config_migrations,
                manifest.min_data_version,
                job_id=self.job_id,
            )
        except Hal0Error as exc:
            # Don't leave the new tree orphaned on a migration failure —
            # nuke the half-installed dir so a retry starts fresh.
            with contextlib.suppress(OSError):
                shutil.rmtree(install_dir)
            raise UpdateError(
                f"config migration failed during update: {exc.message}",
                details={**exc.details, "version": target_version},
            ) from exc

        # Step 7b: virtual-seed migration — prune any materialised seed profiles
        # from /etc/hal0/profiles.toml (older installs wrote them inline, which
        # froze stale definitions) so the code overlay wins. Operator edits are
        # untouched. Runs after migrations so the schema is stable first.
        try:
            await asyncio.to_thread(ensure_seed_profiles, job_id=self.job_id)
        except Exception as exc:
            log.warning(
                "updater.seed_profiles_prune_failed",
                job_id=self.job_id,
                error=str(exc),
            )
            # Non-fatal: a lingering materialised seed is harmless (the overlay
            # overwrites it on load); don't abort the update.

        # Step 7c: clear crash-only mtp=true slot overrides (a force-on pointing
        # at a model with no MTP heads makes llama-server exit at load once the
        # unit re-renders under post-separation code). Loud per-slot log; the
        # eligible / unresolvable cases are left untouched.
        try:
            await asyncio.to_thread(clear_stale_mtp_overrides, job_id=self.job_id)
        except Exception as exc:
            log.warning(
                "updater.mtp_migration_failed",
                job_id=self.job_id,
                error=str(exc),
            )
            # Non-fatal: the affected slot simply fails to load until the
            # operator flips MTP to Auto/Off in the drawer.

        # spec-hw-slot-ownership §6 one-shot folds are NOT auto-run on update:
        # they are deploy-window gated + dry-run by default and live in the
        # standalone hal0.config.migrations.hw_slot_ownership module, invoked
        # manually via `hal0 slot migrate-hw` (mirrors slot_flags_fold). Auto-
        # running an irreversible hardware re-partition on every update is unsafe.

        # Step 7d: roll stale former-default runner-image pins to the current
        # DEFAULT_ROCMFPX_IMAGE (runs before the unit re-render below so the
        # rewritten units carry the new ref; applies on next slot start).
        try:
            await asyncio.to_thread(retag_stale_slot_images, job_id=self.job_id)
        except Exception as exc:
            log.warning(
                "updater.image_retag_failed",
                job_id=self.job_id,
                error=str(exc),
            )
            # Non-fatal: a stale pin just keeps the previous runner image.

        # Step 8 + 9: atomic symlink swap + record previous.
        link = _current_symlink()
        try:
            prior = _atomic_symlink_swap(install_dir, link)
        except OSError as exc:
            # Roll back the extracted tree so /usr/lib stays clean.
            with contextlib.suppress(OSError):
                shutil.rmtree(install_dir)
            raise UpdateSwapError(
                f"atomic symlink swap failed: {exc}",
                details={"link": str(link), "target": str(install_dir), "error": str(exc)},
            ) from exc

        # Step 8b: re-install the swapped-in code into the running venv.
        # apply() only swaps the `current` symlink; the venv imports hal0
        # from its own site-packages (a normal, non-editable install), so a
        # symlink swap alone would NOT change the running version. Re-pip the
        # freshly-swapped tree so the next hal0-api restart actually runs the
        # new code (#495). Editable/dev installs are exempt — there is no FHS
        # venv to refresh and apply() is unsupported there.
        if not _is_editable_install():
            try:
                await asyncio.to_thread(_reinstall_into_venv, install_dir, job_id=self.job_id)
            except UpdateError:
                # Re-pip failed: roll the symlink back (when there is a prior
                # target) so `current` and the venv's installed code stay
                # consistent — both = prior.
                if prior is not None:
                    with contextlib.suppress(OSError):
                        _atomic_symlink_swap(prior, link)
                raise

        # Step 8c: re-render existing slot units through the NEW code so any
        # subsequent start (systemctl restart, crash-restart, reboot) uses
        # current argv — never bounces serving (write + daemon-reload only).
        # Must run AFTER the 8b venv reinstall and in a FRESH interpreter:
        # this (still-old) process would render pre-update argv, defeating the
        # point; a subprocess of the re-pipped venv executes the new module.
        if not _is_editable_install():
            try:
                proc = await asyncio.to_thread(
                    subprocess.run,
                    [
                        sys.executable,
                        "-c",
                        "from hal0.updater.updater import rerender_slot_units; "
                        "print(rerender_slot_units())",
                    ],
                    capture_output=True,
                    text=True,
                    timeout=300,
                )
                if proc.returncode == 0:
                    log.info(
                        "updater.unit_rerender_done",
                        job_id=self.job_id,
                        rewritten=(proc.stdout or "").strip(),
                    )
                else:
                    log.warning(
                        "updater.unit_rerender_failed",
                        job_id=self.job_id,
                        rc=proc.returncode,
                        stderr=(proc.stderr or "")[-500:],
                    )
            except Exception as exc:
                log.warning(
                    "updater.unit_rerender_failed",
                    job_id=self.job_id,
                    error=str(exc),
                )
                # Non-fatal: stale units keep old flags until a hal0-level slot
                # restart; the dashboard drift indicator surfaces them.

        if prior is not None:
            _write_atomic_text(_previous_record(), str(prior))
        log.info(
            "updater.swap_ok",
            job_id=self.job_id,
            version=target_version,
            link=str(link),
            previous=str(prior) if prior else None,
        )

        return {
            "version": target_version,
            "previous": str(prior) if prior else None,
            "install_dir": str(install_dir),
            "cache_dir": str(cache),
            "migrations": {"from": migration_info[0], "to": migration_info[1]},
            "installed_at": time.time(),
        }

    async def apply(self, version: str | None = None) -> dict[str, Any]:
        """Prepare + commit in one call — the back-compat single-step update.

        Equivalent to the pre-split ``apply()``: stages the release
        (:meth:`prepare`) and immediately activates it (:meth:`commit`). Callers
        that want to show release notes / gate on confirmation between the two
        phases call :meth:`prepare` then :meth:`commit` directly instead.
        """
        # Single chokepoint: hard-refuse on editable/dev installs before any
        # download/extract work (prepare/commit re-check, so the two-phase
        # route path is covered too).
        _raise_if_editable_install()
        prepared = await self.prepare(version)
        return await self.commit(str(prepared["version"]))

    # ── rollback ───────────────────────────────────────────────────────────────

    async def rollback(self) -> dict[str, Any]:
        """Revert to the previously installed version.

        Reads ``/var/lib/hal0/hal0.previous`` for the prior symlink target,
        atomic-swaps the ``current`` symlink back, and emits a WARN if the
        running ``meta.schema_version`` is now ahead of the previous tree
        — forward-only migrations are acceptable for v1 (PLAN §9 + Team D
        brief). The route layer can surface the warning in the job result.

        Step 8b (non-editable installs only): after the symlink swap, re-pips
        the prior tree into the running venv so that the next ``hal0-api``
        restart actually serves the rolled-back version. This mirrors the
        same step in ``apply()`` (#495 / #980). On pip failure the symlink is
        swapped *forward* again (back to ``current_target``) so the symlink
        and the venv-installed code stay consistent — same failure-recovery
        pattern as ``apply()``.

        Raises:
            UpdateRollbackUnavailable: No previous record on disk.
            UpdateSwapError: The symlink swap itself failed.
            UpdateError: Re-pip of the prior tree failed (non-editable only).
        """
        record = _previous_record()
        if not record.exists():
            raise UpdateRollbackUnavailable(
                "no previous-version record at /var/lib/hal0/hal0.previous; nothing to roll back",
                details={"record": str(record)},
            )

        prior_str = record.read_text(encoding="utf-8").strip()
        if not prior_str:
            raise UpdateRollbackUnavailable(
                "previous-version record is empty",
                details={"record": str(record)},
            )

        link = _current_symlink()
        # Resolve relative previous targets against the symlink's parent so
        # rollback works when previous was recorded as a relative path.
        prior_path = Path(prior_str)
        if not prior_path.is_absolute():
            prior_path = (link.parent / prior_path).resolve()

        if not prior_path.exists():
            raise UpdateRollbackUnavailable(
                f"previous install dir is gone: {prior_path}",
                details={"previous": str(prior_path)},
            )

        log.info("updater.rollback_start", job_id=self.job_id, previous=str(prior_path))
        try:
            current_target = _atomic_symlink_swap(prior_path, link)
        except OSError as exc:
            raise UpdateSwapError(
                f"rollback symlink swap failed: {exc}",
                details={"link": str(link), "target": str(prior_path), "error": str(exc)},
            ) from exc

        # Step 8b: re-install the prior tree into the running venv so the
        # next hal0-api restart serves the rolled-back version. Editable/dev
        # installs are exempt (no FHS venv to refresh; mirrors apply()).
        if not _is_editable_install():
            try:
                await asyncio.to_thread(_reinstall_into_venv, prior_path, job_id=self.job_id)
            except UpdateError:
                # Re-pip failed: swap the symlink forward again so `current`
                # and the venv's installed code stay consistent — both remain
                # pointing at current_target (the version we just rolled away
                # from), rather than leaving the symlink at prior_path while
                # site-packages still has the newer code.
                if current_target is not None:
                    with contextlib.suppress(OSError):
                        _atomic_symlink_swap(current_target, link)
                raise

        # Re-record what we just swapped away from so a double-rollback
        # bounces between the two installs (matches haloai semantics).
        if current_target is not None:
            _write_atomic_text(record, str(current_target))
        else:
            with contextlib.suppress(OSError):
                record.unlink()

        # Forward-only migration warning. The schema on disk reflects the
        # latest version we ever migrated to; if it's ahead of v1 the
        # previous tree may not know about new fields. We tolerate this
        # for v1 and let the new (older) hal0-api parse what it can.
        warning: str | None = None
        try:
            cfg = load_hal0_config()
            on_disk = int(getattr(cfg.meta, "schema_version", 1) or 1)
            if on_disk > latest_version():
                warning = (
                    f"meta.schema_version on disk is {on_disk}; the previous install may not "
                    "understand all fields. Forward-only migrations: skipping migration revert."
                )
                log.warning(
                    "updater.rollback_schema_ahead",
                    job_id=self.job_id,
                    on_disk=on_disk,
                    supported=latest_version(),
                )
        except Hal0Error:
            warning = None

        log.info("updater.rollback_ok", job_id=self.job_id, restored=str(prior_path))
        return {
            "rolled_back_to": str(prior_path),
            "previous_now": str(current_target) if current_target else None,
            "schema_warning": warning,
        }


__all__ = [
    "ReleaseInfo",
    "ReleaseManifest",
    "UpdateCosignFailed",
    "UpdateCosignMissing",
    "UpdateDownloadError",
    "UpdateError",
    "UpdateExtractError",
    "UpdateManifestInvalid",
    "UpdateRollbackUnavailable",
    "UpdateSwapError",
    "UpdateVerifyError",
    "Updater",
    "ensure_seed_profiles",
    "fetch_release_manifest",
    "releases_url",
    "validate_manifest_for_channel",
]
