"""Self-update mechanism for hal0.

Updater handles the full update lifecycle:
  1. Check ``{HAL0_RELEASES_URL}`` (or ``https://releases.hal0.dev/{channel}.json``)
     for a newer version.
  2. Download tarball + cosign signature into a root-only ``0700`` staging
     directory under the install root (#1738 — never the service-writable
     ``/var/lib/hal0/cache/<version>/``, which only holds the verified
     manifest ``commit()`` re-reads).
  3. Verify the SHA-256 digest against the release manifest, and re-derive it
     from the same open file object the tarball is extracted from.
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
from collections.abc import Iterator
from pathlib import Path
from typing import Any, BinaryIO, Literal
from urllib.parse import urlparse

import structlog
from packaging.specifiers import InvalidSpecifier, SpecifierSet
from packaging.version import InvalidVersion, Version
from pydantic import BaseModel, Field, field_validator, model_validator

import hal0
from hal0.config import paths
from hal0.config.loader import (
    hal0_config_file_lock,
    load_hal0_config,
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


class UpdateUpgradePathUnsupported(UpdateError):
    """The installed version is below the release's declared ``upgrade_from`` floor.

    ``ReleaseManifest.upgrade_from`` has existed since the schema was written but
    was never read, so there was no floor at all on how old a box could be before
    ``prepare()`` would attempt to converge it. Enforcing it makes "we support
    upgrades from >= X" a machine-checked claim instead of a docs claim.

    Release manifests carry the floor that ``manifest.json``'s ``upgrade_from``
    declares; ``release.yml`` copies it into every channel manifest. An empty
    value means no floor.
    """

    code = "system.update_upgrade_path_unsupported"
    status = 400


class UpdateRollbackUnavailable(UpdateError):
    """No previous-version record exists — nothing to roll back to."""

    code = "system.update_rollback_unavailable"
    status = 400


class UpdatePrivilegeError(UpdateError):
    """The privileged half of the update cannot be reached (#1464).

    ``hal0-api`` runs as the unprivileged ``hal0`` service account while
    ``/usr/lib/hal0`` is root-owned and never service-writable
    (:mod:`hal0.install.perms`). Every activation step therefore routes through
    the ``hal0-update`` sudo seam. This error is raised by the *preflight*, at
    the top of :meth:`Updater.prepare`/:meth:`Updater.commit`/
    :meth:`Updater.rollback`, when neither route is usable — so an operator gets
    an actionable message immediately instead of a raw ``Permission denied``
    after a full download + sha256 + cosign pass.
    """

    code = "system.update_privilege_denied"
    status = 500


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
    runners: dict[str, dict[str, Any]] = Field(
        default_factory=dict,
        description="Mirror of manifest.json's runners pin table.",
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
    #: The active ``HAL0_RELEASES_URL`` value when the manifest source was
    #: overridden, else ``None``. When set, ``channel`` was NOT consulted to
    #: build ``manifest_url`` — reporting surfaces must disclose that (#2128).
    releases_url_override: str | None = None


# ── URL helpers + raw fetch ────────────────────────────────────────────────────


def releases_url_override() -> str | None:
    """Return the active ``HAL0_RELEASES_URL`` override, or ``None``.

    Structural companion to :func:`releases_url` (#2128): when this returns a
    value, channel resolution is bypassed entirely — the configured channel
    plays no part in which manifest is consulted. Every surface that reports
    a channel next to a resolved target (CLI ``--check``, ``/api/updates/*``,
    dashboard) uses this to disclose the override; it never changes which URL
    is fetched.
    """
    override = os.environ.get("HAL0_RELEASES_URL", "").strip()
    return override or None


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
    override = releases_url_override()
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
        async with httpx.AsyncClient(timeout=10.0, follow_redirects=True) as client:
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


# Best-effort prerelease recognizer used ONLY for version strings that are not
# valid PEP 440 (tag-derived forms such as ``1.0.0-rc.1`` or nightly timestamp
# tags), where ``packaging.version.Version`` raises ``InvalidVersion``.
_PRERELEASE_LABEL_RE = re.compile(
    r"^(?P<release>\d+(?:\.\d+)*)[-._]?(?P<label>a|alpha|b|beta|c|rc|pre|preview)[-._]?(?P<num>\d*)$",
    re.IGNORECASE,
)
_PRERELEASE_RANK = {
    "a": 0,
    "alpha": 0,
    "b": 1,
    "beta": 1,
    "c": 2,
    "rc": 2,
    "pre": 2,
    "preview": 2,
}


def _naive_version_key(v: str) -> tuple[Any, int, int]:
    """Sort key for non-PEP-440 version strings: (release_tuple_or_digit_tuple, pre_rank, pre_num).

    Recognises ``<release><a|alpha|b|beta|c|rc|pre|preview><N>`` (both the
    pip-normalised form, e.g. ``1.0.0rc1``, and the tag-derived form, e.g.
    ``1.0.0-rc.1``) so a prerelease sorts BEFORE its own final release —
    without this, ``_version_tuple("1.0.0rc1")`` reads the ``rc1`` suffix as
    an extra ``.1`` patch component and a box on ``1.0.0rc1`` never sees
    ``1.0.0`` (or even ``1.0.1``) GA as "newer". Final releases get the
    sentinel rank 99 so they sort above every prerelease of the same release
    tuple. Anything that doesn't match (nightly timestamp tags, malformed
    strings) keeps the original digit-tuple behavior via ``_version_tuple``,
    tagged as a "final" so it still compares correctly against a prerelease
    of the same release number.
    """
    match = _PRERELEASE_LABEL_RE.match((v or "").strip())
    if match:
        release = tuple(int(p) for p in match.group("release").split("."))
        rank = _PRERELEASE_RANK[match.group("label").lower()]
        num = int(match.group("num") or 0)
        return (release, rank, num)
    return (_version_tuple(v), 99, 0)


def _is_newer(candidate: str, current: str) -> bool:
    """Return True if PEP 440 version ``candidate`` is strictly newer than ``current``.

    Uses ``packaging.version.Version`` so pip-normalised forms (``0.8.0b3``) order
    correctly against tag-derived manifest forms (``0.8.1-beta.1``). The naive
    ``_version_tuple`` digit-parser conflated the beta number with the patch
    component (``0.8.0b3`` → ``(0, 8, 3)``), so every box on a ``0.8.0bN`` beta saw
    ``0.8.1-beta.1`` as "not newer" and ``hal0 update`` reported nothing to apply.
    Falls back to ``_naive_version_key`` only when a string is not valid PEP 440
    (see its docstring for why that fallback needs its own prerelease handling,
    not just a raw tuple compare).
    """
    try:
        return Version(candidate) > Version(current)
    except InvalidVersion:
        return _naive_version_key(candidate) > _naive_version_key(current)


_NIGHTLY_INSTALLED_VERSION_RE = re.compile(
    r"^(?P<base>[0-9]+\.[0-9]+\.[0-9]+)-nightly\.(?P<stamp>[0-9]+)$"
)


def floor_comparable_version(installed: str) -> str:
    """Map a nightly ``X.Y.Z-nightly.<stamp>`` onto PEP 440 ``X.Y.Z.dev<stamp>``.

    A nightly is cut from main before its base version ships, so it orders
    below ``X.Y.Z``. Every other string is returned unchanged. installer/install.sh
    applies the same mapping in its own floor check.
    """
    m = _NIGHTLY_INSTALLED_VERSION_RE.fullmatch(installed.strip())
    if m is None:
        return installed
    return f"{m.group('base')}.dev{m.group('stamp')}"


def _enforce_upgrade_floor(
    manifest: ReleaseManifest,
    *,
    current: str | None = None,
    job_id: str | None = None,
) -> None:
    """Refuse to stage a release whose ``upgrade_from`` floor this box is below.

    ``upgrade_from`` is a PEP 440 specifier set (e.g. ``">=1.3.0"``). An empty
    value means no floor and this is a no-op.

    A nightly install reports ``X.Y.Z-nightly.<stamp>``, which is not PEP 440.
    It is compared as ``X.Y.Z.dev<stamp>`` (:func:`floor_comparable_version`), so
    a nightly cut before the floor's release is held to the floor instead of
    failing open.

    Fail-open on anything unparseable: a malformed specifier or a non-PEP-440
    installed version must never block an otherwise
    fully-verified update. The floor is a supportability statement, not a
    security control (cosign is the security control), so a broken statement
    degrades to "no statement".

    Raises:
        UpdateUpgradePathUnsupported: the installed version is outside the floor.
    """
    spec = (manifest.upgrade_from or "").strip()
    if not spec:
        return
    installed = current or installed_version()
    try:
        allowed = SpecifierSet(spec)
        # prereleases=True: an rc or nightly of a release above the floor is a
        # supported baseline, and a bare ">=" would exclude every prerelease.
        ok = allowed.contains(Version(floor_comparable_version(installed)), prereleases=True)
    except (InvalidSpecifier, InvalidVersion) as exc:
        log.warning(
            "updater.upgrade_floor_unparseable",
            job_id=job_id,
            upgrade_from=spec,
            installed=installed,
            error=str(exc),
        )
        return
    if ok:
        log.info("updater.upgrade_floor_ok", job_id=job_id, upgrade_from=spec, installed=installed)
        return
    raise UpdateUpgradePathUnsupported(
        f"hal0 {manifest.version} supports upgrades from {spec}; this box is on {installed}",
        details={
            "installed_version": installed,
            "target_version": manifest.version,
            "upgrade_from": spec,
            "hint": (
                "upgrade to a release inside the supported window first, or "
                "reinstall; see 'Upgrading from an older release' in "
                "docs/guides/update-and-rollback.mdx"
            ),
        },
    )


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


#: Written into a destination directory for the duration of an extraction and
#: removed on success. Its presence means "a stage started here and did not
#: finish" — the only marker that is reliable for *every* interruption shape,
#: including the ones that leave no recognisable file layout behind at all.
_STAGING_SENTINEL = ".hal0-staging"

#: Quarantined trees (``<install-dir>.stale-<unix-ts>``) are kept so an
#: operator can recover from a half-failed apply, but they were never cleaned
#: up — one full install tree leaked per retry, forever. Keep the newest few
#: regardless of age, and reap anything older than this.
_QUARANTINE_KEEP = 3
_QUARANTINE_MAX_AGE_S = 30 * 86400

#: Orphaned ``.stage-<version>-XXXXXX`` staging dirs (see
#: :func:`_root_only_staging_dir`) are removed in a ``finally``, which does not
#: run on SIGKILL / OOM / power-cut. A killed stage therefore leaks a fresh
#: ``mkdtemp`` dir holding a full release tarball under the root filesystem,
#: forever. Reap any older than this. The floor is comfortably above the
#: privileged stage timeout (``_STAGE_TIMEOUT`` = 1800s) so a live concurrent
#: stage's own dir — always freshly created — can never be swept.
_STAGING_MAX_AGE_S = 3600


def _looks_like_hal0_install(path: Path) -> bool:
    """Heuristic: does ``path`` look like a prior hal0 tarball extraction?

    A safe quarantine candidate is one of:

    * a leftover :data:`_STAGING_SENTINEL` — an extraction started here and
      never completed, whatever it managed to write;
    * a top-level ``VERSION`` file;
    * an un-flattened ``hal0-<version>/`` prefix directory containing a
      ``VERSION`` — the exact shape ``tf.extractall`` leaves when it is
      interrupted before :func:`_extract_tarball` flattens the prefix away;
    * a ``pyproject.toml`` whose ``name`` is ``hal0ai`` (or the legacy
      pre-rename ``hal0``).

    We deliberately refuse to touch unrelated non-empty directories: an
    operator's own files at this path must never be silently moved aside.

    The pyproject check reads the **whole** file. It used to read only
    ``[:512]``, which never matched hal0's own manifest — ``name = "hal0ai"``
    starts at byte 512 exactly — so the fallback was dead code, masked only
    by release tarballs happening to ship a ``VERSION``. An installer rsync
    tree has no ``VERSION``, so the fallback is the only thing that can
    recognise it, and it has to actually run.
    """
    if (path / _STAGING_SENTINEL).exists():
        return True
    if (path / "VERSION").is_file():
        return True
    for child in _iterdir_safe(path):
        if child.is_dir() and child.name.startswith("hal0-") and (child / "VERSION").is_file():
            return True
    pp = path / "pyproject.toml"
    if pp.is_file():
        try:
            body = pp.read_text(encoding="utf-8", errors="replace")
        except OSError:
            return False
        return any(
            marker in body
            for marker in ('name = "hal0ai"', "name = 'hal0ai'", 'name = "hal0"', "name = 'hal0'")
        )
    return False


def _iterdir_safe(path: Path) -> list[Path]:
    """``path.iterdir()`` as a list, empty on any filesystem error."""
    try:
        return list(path.iterdir())
    except OSError:
        return []


def _reap_stale_quarantines(
    install_dir: Path,
    *,
    keep: int = _QUARANTINE_KEEP,
    max_age_s: int = _QUARANTINE_MAX_AGE_S,
    job_id: str | None = None,
) -> None:
    """Delete old ``<install_dir>.stale-<unix-ts>`` quarantine trees.

    Quarantining is a self-heal (see :func:`_extract_tarball`) but nothing
    ever removed the result, so a box that retried a failed apply a few times
    accumulated whole install trees under ``/usr/lib/hal0`` indefinitely. The
    newest ``keep`` are retained — recovery material for the failure that just
    happened — and everything else, plus anything older than ``max_age_s``, is
    removed. Best-effort and logged: failing to tidy is never a reason to
    fail an update.
    """
    prefix = f"{install_dir.name}.stale-"
    candidates: list[tuple[int, Path]] = []
    for entry in _iterdir_safe(install_dir.parent):
        if not entry.name.startswith(prefix) or not entry.is_dir():
            continue
        suffix = entry.name[len(prefix) :]
        if not suffix.isdigit():
            continue
        candidates.append((int(suffix), entry))
    if not candidates:
        return
    candidates.sort(key=lambda item: item[0], reverse=True)
    cutoff = int(time.time()) - max_age_s
    for index, (stamp, entry) in enumerate(candidates):
        # Two independent bounds, because either alone leaks: an age-only
        # rule keeps every quarantine from a same-day retry storm, and a
        # count-only rule keeps three full install trees forever.
        if index < keep and stamp > cutoff:
            continue
        try:
            shutil.rmtree(entry)
        except OSError as exc:
            log.warning(
                "updater.quarantine_reap_failed", job_id=job_id, path=str(entry), error=str(exc)
            )
            continue
        log.info("updater.quarantine_reaped", job_id=job_id, path=str(entry), stamp=stamp)


def _reap_stale_staging_dirs(
    *,
    max_age_s: int = _STAGING_MAX_AGE_S,
    job_id: str | None = None,
) -> None:
    """Delete orphaned ``.stage-<version>-XXXXXX`` dirs under the install root.

    :func:`_root_only_staging_dir` tears its dir down in a ``finally``, so a
    stage killed by SIGKILL / OOM / power-cut leaks the whole ``mkdtemp`` tree —
    a full release tarball — under ``/usr/lib/hal0`` with nothing to sweep it.
    The ``.stale-`` quarantine reaper doesn't match this prefix, so it grew
    unbounded, one dir per killed stage. Reap any ``.stage-`` dir older than
    ``max_age_s`` by mtime; the floor sits above ``_STAGE_TIMEOUT`` so a live
    concurrent stage's freshly-created dir is always spared. Best-effort and
    logged: failing to tidy is never a reason to fail an update.
    """
    root = _usr_lib_root()
    cutoff = time.time() - max_age_s
    for entry in _iterdir_safe(root):
        if not entry.name.startswith(".stage-") or not entry.is_dir():
            continue
        try:
            mtime = entry.stat().st_mtime
        except OSError:
            continue
        if mtime > cutoff:
            continue
        try:
            shutil.rmtree(entry)
        except OSError as exc:
            log.warning(
                "updater.staging_reap_failed", job_id=job_id, path=str(entry), error=str(exc)
            )
            continue
        log.info("updater.staging_reaped", job_id=job_id, path=str(entry))


@contextlib.contextmanager
def _root_only_staging_dir(version: str, *, job_id: str | None = None) -> Iterator[Path]:
    """Yield a fresh 0700 staging dir under the **root-owned** install root.

    #1738: the release artifact used to be downloaded into
    ``/var/lib/hal0/cache/<version>/`` and then re-opened *by path* four
    times — download, sha256, ``cosign verify-blob`` (a subprocess), and
    finally ``tarfile.open``. ``/var/lib/hal0`` is ``hal0:hal0`` ``0o2775``
    with no sticky bit, and :func:`_restore_service_ownership` hands the
    cache version dir back to the service account after every successful
    stage — so the unprivileged account could substitute its own tarball in
    the window between cosign exiting and root extracting, and root would
    pip-install it. That defeats the entire "root re-fetches and re-verifies
    the bytes itself" argument the privileged seam rests on.

    ``mkdtemp`` creates the directory ``0700``, atomically and owned by the
    creating euid — root on the seam path. Nothing but root can traverse it,
    so no other principal can even name, let alone replace, the artifact
    between verification and extraction. The dot-prefixed name can never
    collide with a release-dir token (those forbid a leading ``.``; see the
    token regex above), and the tree is removed unconditionally on the way
    out so a failed stage leaks nothing.
    """
    root = _usr_lib_root()
    root.mkdir(parents=True, exist_ok=True)
    staging = Path(tempfile.mkdtemp(prefix=f".stage-{version}-", dir=root))
    try:
        os.chmod(staging, 0o700)
        yield staging
    finally:
        try:
            shutil.rmtree(staging)
        except OSError as exc:
            log.warning(
                "updater.staging_cleanup_failed", job_id=job_id, path=str(staging), error=str(exc)
            )


def _extract_tarball(
    tarball: Path,
    dest: Path,
    *,
    job_id: str | None = None,
    expected_digest: str | None = None,
) -> None:
    """Extract ``tarball`` to ``dest``.

    The tarball is expected to contain a top-level directory matching
    ``hal0-<version>/``; we strip that prefix to land files directly under
    ``dest`` (which the caller names ``/usr/lib/hal0-<version>/``).

    ``expected_digest`` (#1738) is the authenticated ``manifest.digest_sha256``.
    When given, the file is opened **once** and that single file object is used
    for both the digest and the extraction — the bytes that are hashed are, by
    construction, the bytes that are unpacked. Any substitution between
    ``cosign verify-blob`` returning and this hash pass (a rename, a replace,
    or an in-place rewrite of the artifact) fails the check and aborts the
    stage before a single member is written. A rewrite of the pinned inode
    *after* the hash pass is not re-checked — the fd pins the inode, not its
    bytes — so the guarantee against that is the root-only staging isolation
    (:func:`_root_only_staging_dir`), which keeps any unprivileged principal
    from reaching the artifact in the first place. This digest check is the
    inner, non-negotiable half; the isolation is the load-bearing one.

    If ``dest`` already exists and looks like a prior hal0 extraction
    (see :func:`_looks_like_hal0_install`), it is renamed aside
    to ``dest.with_name(<name>.stale-<unix-ts>)`` so a retry after a half-
    failed apply isn't permanently wedged. Unrelated non-empty dirs are
    still refused — we will not silently destroy whatever the operator
    parked there.

    Raises ``UpdateExtractError`` on filesystem issues, malformed
    tarballs, or unsafe paths, and ``UpdateVerifyError`` when
    ``expected_digest`` does not match the bytes about to be extracted.
    """
    dest = Path(dest)
    # Open the artifact exactly once, and re-derive its digest from *that*
    # handle, before anything on disk is disturbed — a mismatch must abort
    # without quarantining the operator's existing install. The handle is
    # what ``tarfile`` reads from below, so there is no second name lookup
    # between the check and the use. On any raise before the ExitStack takes
    # ownership, the handle is dropped and closed with the last reference.
    handle = _open_digest_verified(tarball, expected_digest)
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
        elif os.geteuid() == 0 and ((_st := dest.stat()).st_uid != 0 or _st.st_mode & 0o022):
            # Codex follow-up: an *empty* pre-existing dest was previously
            # reused untouched aside from a later chmod — but chmod cannot
            # revoke the owning user's own access, and reusing-then-chmod
            # still leaves a (short) window where a pre-existing writable
            # inode stays writable. Two ways an inode here can be untrusted:
            # wrong owner (chmod can't fix that — root reusing it and
            # chmod-ing to 0700 leaves the ORIGINAL owner's rwx bits
            # unchanged), or root-owned-but-group/other-writable (e.g. a
            # directory a failed stage left behind under the old, pre-#1723
            # permissive-umask behavior — reusing it at all trusts an inode
            # that was, until this exact chmod call, writable by whoever
            # could reach it). Only meaningful at euid 0 — the privileged
            # seam path with a trust boundary to defend; a dev/CI/HAL0_HOME
            # run has none. Quarantine it exactly like a suspicious
            # non-empty dir, so the fresh `mkdir(mode=0o700)` below always
            # creates (and therefore owns, atomically-secured) `dest`
            # itself rather than trusting whatever created it before.
            stale = dest.with_name(f"{dest.name}.stale-{int(time.time())}")
            try:
                dest.rename(stale)
            except OSError as exc:
                raise UpdateExtractError(
                    f"could not quarantine untrusted empty install dir {dest}: {exc}",
                    details={"path": str(dest), "quarantine": str(stale), "error": str(exc)},
                ) from exc
            log.warning(
                "updater.extract_quarantined_untrusted_empty",
                job_id=job_id,
                original=str(dest),
                quarantine=str(stale),
            )
    # #1723 / Codex follow-up: lock `dest` to owner-only *before* anything is
    # extracted into it. A directory's own children are unreachable to
    # non-owners as long as the directory itself denies traversal (x) — so
    # an owner-only top-level `dest` closes the whole plant-a-file-during-
    # extraction race by construction, without needing to touch every path
    # underneath or mutate the process-wide umask (which would race other
    # concurrent `asyncio.to_thread` extractions sharing the same process).
    #
    # ``mode=0o700`` on the ``mkdir`` call itself makes the *fresh-create*
    # case atomic — the OS never exposes a wider mode than requested, even
    # for an instant, because umask can only clear bits from ``mode``, never
    # add them (a separate follow-up ``mkdir()``-then-``chmod()`` pair would
    # leave a real, schedulable window between the two syscalls under a
    # permissive caller umask). The explicit ``chmod`` right after still
    # runs unconditionally to cover the *reuse* case — ``exist_ok=True``
    # silently keeps an already-existing empty ``dest`` (e.g. left behind by
    # an interrupted earlier stage attempt) exactly as it was, mode
    # included, so ``mode=`` on ``mkdir`` never applies to it.
    dest.mkdir(parents=True, exist_ok=True, mode=0o700)
    dest.chmod(0o700)

    # Mark the destination as mid-stage for the whole extraction window. If we
    # are killed anywhere below, the next attempt sees this sentinel, treats
    # the tree as a prior (incomplete) hal0 extraction and quarantines it —
    # instead of hitting "refusing to extract over non-empty directory" on
    # every retry forever, which is unrecoverable for the unprivileged daemon
    # because the wedged tree is root-owned.
    sentinel = dest / _STAGING_SENTINEL
    with contextlib.suppress(OSError):
        sentinel.write_text("", encoding="utf-8")

    log.info("updater.extract_start", job_id=job_id, tarball=str(tarball), dest=str(dest))
    strip_prefix: str | None = None
    try:
        with contextlib.ExitStack() as stack:
            stack.enter_context(contextlib.closing(handle))
            tf = stack.enter_context(tarfile.open(fileobj=handle, mode="r:*"))
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
            tf.extractall(path=dest, filter="data")
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

    # Extraction (and its flatten) completed — drop the mid-stage marker so
    # this tree reads as a finished install from here on.
    with contextlib.suppress(OSError):
        sentinel.unlink(missing_ok=True)

    # Now that extraction is complete and `dest` is no longer racing anyone,
    # widen it back to its final, correct mode: normalize the whole tree
    # (not just the top level) so assert_trusted_release_dir's precondition
    # holds, and every subdirectory tarfile created — which inherited
    # whatever mode its tar member or the caller's umask gave it — is
    # go-w-clean too, not just readable-only-by-owner forever.
    _normalize_staged_tree(dest, job_id=job_id)

    # Tidy older quarantines now that a good tree exists at `dest` — the
    # recovery material for *this* failure is kept, ancient copies are not.
    _reap_stale_quarantines(dest, job_id=job_id)
    # Sweep any staging dirs orphaned by a SIGKILLed stage (the current one is
    # still live and too fresh to match the age floor).
    _reap_stale_staging_dirs(job_id=job_id)

    log.info("updater.extract_ok", job_id=job_id, dest=str(dest))


def _open_digest_verified(tarball: Path, expected_digest: str | None) -> BinaryIO:
    """Open ``tarball`` once and confirm its bytes match ``expected_digest``.

    Returns the still-open, rewound binary handle. When ``expected_digest``
    is ``None`` (callers outside the privileged release path) the file is
    simply opened.

    This is the #1738 fix's inner guarantee: hashing and extracting share a
    single file object, so nothing that happens to the *path* between
    ``cosign verify-blob`` and ``tarfile.open`` — a rename or an
    ``os.replace`` of a different inode — can change what gets unpacked.
    Holding the fd pins the *inode*, not its bytes: a rewrite of the pinned
    inode after this hash pass would not be re-checked, so the load-bearing
    guarantee against that is the root-only staging isolation
    (:func:`_root_only_staging_dir`), which keeps any other principal from
    reaching the artifact at all. This check is the non-negotiable inner half,
    not a standalone one.
    """
    try:
        # SIM115 off: returning the *still-open* handle is the entire point —
        # the caller closes it through an ExitStack once tarfile is done with
        # it, so hashing and extraction cannot be split across two opens.
        handle = open(tarball, "rb")  # noqa: SIM115
    except OSError as exc:
        raise UpdateExtractError(
            f"could not open release tarball: {exc}",
            details={"tarball": str(tarball), "error": str(exc)},
        ) from exc
    if expected_digest is None:
        return handle
    try:
        h = hashlib.sha256()
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            h.update(chunk)
        got = h.hexdigest()
        handle.seek(0)
    except OSError as exc:
        handle.close()
        raise UpdateExtractError(
            f"could not read release tarball: {exc}",
            details={"tarball": str(tarball), "error": str(exc)},
        ) from exc
    if got != expected_digest:
        handle.close()
        raise UpdateVerifyError(
            "release tarball digest changed after verification — refusing to extract "
            f"(expected {expected_digest}, got {got})",
            details={"expected": expected_digest, "got": got, "tarball": str(tarball)},
        )
    return handle


def _normalize_staged_tree(dest: Path, *, job_id: str | None = None) -> None:
    """Set the staged tree's final mode/ownership after extraction.

    Defends the precondition ``assert_trusted_release_dir`` enforces at
    activate time: the staged tree and its parent must be uid-0-owned and
    not group/other writable. ``assert_trusted_release_dir`` only inspects
    the top-level directory today, but this normalizes every entry
    ``tf.extractall`` wrote — defense in depth so a future recursive gate,
    or a subdirectory an operator inspects by hand, is never the surprise.

    Directories are explicitly set to ``0755``, not merely masked — the
    caller locked ``dest`` to ``0700`` for the extraction window (see
    ``_extract_tarball``), and a mask (``mode & ~0o022``) can only ever
    remove bits, so it would leave the real, activated install root-only
    and unreadable by the service account that has to run it. Files keep
    whatever mode the tarball's own member metadata gave them (tarfile
    ``chmod``s each member explicitly, independent of umask) with only
    group/other write bits stripped — there is no equivalent "restore
    executable bit" ambiguity for files the way there is for the
    deliberately-locked-down directory.

    Ownership is reset to root:root only when running as euid 0 (the
    privileged seam path, where root just extracted the tree and files are
    already root-owned by construction — this chown is belt-and-suspenders,
    not load-bearing for that path). Mirrors :func:`_restore_service_ownership`
    just below: best-effort and logged, never raised — a process can be euid
    0 in a container without CAP_CHOWN (or in a test that stubs ``geteuid``
    without stubbing the filesystem), and failing loudly there would turn an
    inert edge case into a hard stage failure. The *mode* normalization
    below is what actually fixes #1723 and is not best-effort: unlike
    ownership, a plain-file owner can always chmod their own files, so a
    failure there is a real, actionable problem worth surfacing.
    """
    if os.geteuid() == 0:
        try:
            os.chown(dest, 0, 0)
            for path in dest.rglob("*"):
                os.chown(path, 0, 0, follow_symlinks=False)
        except OSError as exc:
            log.warning(
                "updater.extract_chown_failed", job_id=job_id, path=str(dest), error=str(exc)
            )

    try:
        entries = (dest, *dest.rglob("*"))
        for path in entries:
            if path.is_symlink():
                continue
            mode = path.stat().st_mode
            normalized = 0o755 if path.is_dir() else mode & ~0o022
            if normalized != mode:
                path.chmod(normalized)
    except OSError as exc:
        raise UpdateExtractError(
            f"failed to normalize staged release tree permissions: {exc}",
            details={"path": str(dest), "error": str(exc)},
        ) from exc


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

    Args:
        min_data_version: the release manifest's floor.
        job_id: optional breadcrumb for structured-log tracing.

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

    # Cross-process serialization (#1721). This runs either on an
    # ``asyncio.to_thread`` inside the live API process (Updater.commit) or as
    # a bare ``hal0`` process from ``install.sh`` — and install.sh runs this
    # step BEFORE it restarts hal0-api, i.e. against a daemon that may be
    # serving a settings PUT right now. Same ``hal0.toml.lock`` the API's
    # ``hal0_config_txn`` takes, held across this read-modify-write.
    with hal0_config_file_lock(toml_path):
        return _run_config_migrations_locked(toml_path, target, job_id=job_id)


def _run_config_migrations_locked(
    toml_path: Path,
    target: int,
    *,
    job_id: str | None = None,
) -> tuple[int, int]:
    """Body of :func:`_maybe_run_config_migrations`, run under the config lock.

    A raw round-trip, not a load through ``Hal0Config``: dumping the validated
    model would write every default into ``hal0.toml`` and pin it there, and
    unknown keys would be dropped. The migrations are dict transforms, so they
    need nothing the validator adds.
    """
    from hal0.config.loader import _read_toml

    raw = _read_toml(toml_path)
    source = _raw_schema_version(raw)
    if source >= target:
        log.info(
            "updater.migrations_noop",
            job_id=job_id,
            source=source,
            target=target,
        )
        return (source, source)

    new_raw, new_version = run_migrations(raw, target_version=target)
    write_toml_atomic(toml_path, new_raw)
    log.info(
        "updater.migrations_applied",
        job_id=job_id,
        source=source,
        target=new_version,
    )
    return (source, new_version)


#: What an operator runs by hand when the automatic repair could not (no
#: network during the update, a resolver conflict). Logged with the failure so
#: the remedy travels with the diagnosis — the omission #2102 is partly about.
HERMES_VENV_REMEDY = "hal0 agent reprovision hermes --repair"

#: Where the managed hermes venv (link) lives. The same value as
#: ``hal0.agents.hermes_layout.HERMES_VENV_PATH``, spelled out here so core
#: modules that import the updater pull in no hermes module (golden path #15);
#: ``tests/updater/test_hermes_venv_repair.py`` pins the two together. Only a
#: real repair imports the builder, lazily, inside the branch that has already
#: proven the box is broken.
HERMES_VENV_PATH = Path("/var/lib/hal0/venvs/hermes")


#: Where the hermes home (and so ``plugins/``) lives; same spelled-out value as
#: ``HermesProvisionState.hermes_home`` for the same import-weight reason as
#: :data:`HERMES_VENV_PATH`.
HERMES_HOME_PATH = Path("/var/lib/hal0/.hermes")


def mirror_hermes_plugins(
    *,
    job_id: str | None = None,
    hermes_home: Path | None = None,
    plugin_src_root: Path | None = None,
    install: bool = True,
) -> bool:
    """Converge the mirrored Hermes plugin trees after an update (#2884).

    ``hal0 update`` never provisions, and the installer skips provisioning when
    the hermes venv already exists, so an in-place upgrade never re-ran the
    plugin mirror: a manifest changed by a release (``kind: provider`` ->
    ``kind: model-provider``) survived every nightly. This runs the
    provisioner's own ``_mirror_plugins`` (hash-skips identical trees) from the
    shipped ``installer/agents/hermes/plugins`` of the freshly activated tree.

    ``install=False`` (the boot-time safety net) does nothing. A box without a
    hermes home is skipped. Every failure is a quiet ``False``: a plugin copy
    that cannot land is never a reason to fail an update; ``hal0 doctor``
    reports the drift. Returns ``True`` only when a tree was rewritten.
    """
    if not install:
        return False
    home = hermes_home or HERMES_HOME_PATH
    if not home.is_dir():
        log.debug("updater.hermes_home_absent", job_id=job_id, hermes_home=str(home))
        return False

    from hal0.agents import hermes_provision as hermes

    src_root = plugin_src_root or (
        hermes.REPO_ROOT_FOR_INSTALLER / "installer" / "agents" / "hermes" / "plugins"
    )
    changed, _details, failure = hermes._mirror_plugins(src_root, home)
    if failure is not None:
        log.warning("updater.hermes_plugin_mirror_failed", job_id=job_id, reason=failure.reason)
        return False
    if changed:
        log.info("updater.hermes_plugins_mirrored", job_id=job_id, hermes_home=str(home))
    return changed


def repair_hermes_mcp_client(
    *,
    job_id: str | None = None,
    venv: Path | None = None,
    install: bool = True,
    restart_gateway: bool = True,
) -> bool:
    """Rebuild a hermes release whose MCP client cannot import (#2102).

    Heals the venv half of "the agent has no memory tools": a venv that
    drifted off the vetted pin — no ``mcp`` package at all, the
    residual of #2021 — survives every upgrade untouched, because nothing
    outside provisioning ever re-converges the venv and ``hal0 update`` never
    provisions. Measured on ct150 during the rc.12 lane: the #2090 header
    repair landed correctly and the live client still failed at 7181 ms with
    ``mcp.client.streamable_http is not available``.

    The diagnosis is the provisioner's own import assertion
    (``_probe_mcp_client`` with no URL — no server round-trip, just "can this
    interpreter import the client stack"), and the cure is the provisioner's
    own release builder (a fresh release of the live ref, activated with a
    flip-back on failure). Both are reused rather than reimplemented, so a
    repaired box lands on exactly the state a fresh install produces.

    ``restart_gateway=False`` (tests) activates without touching any unit.

    ``install=False`` diagnoses without touching the venv. The boot-time safety
    net passes it: :func:`check_outstanding_migrations` runs on every
    ``hal0-api`` start, crash-restarts included, and a pip run in that path
    would put an unbounded network operation in the startup of a box that is
    already unhealthy. There it logs the fault and the remedy instead, and the
    next real update repairs it.

    Verification is a re-probe, never pip's exit code: the #2021 lesson is that
    an install step reporting success over an unusable artefact is precisely
    how this class hides. Returns ``True`` only when the client imports
    afterwards.

    Every failure is a quiet ``False``. An absent venv (no hermes on this box),
    a pip that cannot resolve, a repair that does not take — none of them is a
    reason to fail an update, and each leaves the box no worse than it was.
    """
    venv = venv or HERMES_VENV_PATH
    venv_python = venv / "bin" / "python"
    if not venv_python.exists():
        log.debug("updater.hermes_venv_absent", job_id=job_id, venv=str(venv))
        return False

    # Lazy: see HERMES_VENV_PATH. Only a box that reaches this line pays for it.
    from hal0.agents import hermes_provision as hermes

    try:
        before = hermes._probe_mcp_client(venv_python, None, agent_id="hermes")
    except Exception as exc:
        log.warning("updater.hermes_mcp_client_probe_failed", job_id=job_id, error=str(exc))
        return False
    if before.get("ok"):
        return False

    log.warning(
        "updater.hermes_mcp_client_broken",
        job_id=job_id,
        stage=before.get("stage"),
        error=before.get("error"),
        remedy=HERMES_VENV_REMEDY,
        repairing=install,
    )
    if not install:
        return False

    # The cure is a fresh release of the live ref through the one builder
    # (ADR-0032 decision 6): never a write into the live release. The update
    # never waits for the builder lock; a held lock is retried by the next
    # `hal0 update`. Activation stops and starts only the units that were
    # running before the switch, so a gateway the operator stopped or disabled
    # is never started (#2822/#2823), and as the unprivileged hal0 user it goes
    # through the hal0-systemctl seam rather than a bare systemctl (#2828).
    from hal0.agents import hermes_release as rel
    from hal0.components.hermes_arm import override_build_ref

    # The cure rebuilds the commit the box runs: a `--target` override, not the
    # pin (an unvetted override is refused rather than built over).
    override_ref, override_error = override_build_ref()
    if override_error is not None:
        log.warning("updater.hermes_venv_repair_refused", job_id=job_id, error=override_error)
        return False
    outcome = rel.ensure_release(
        caller="repair",
        venv=venv,
        ref=override_ref,
        force=True,
        reason="mcp client import failed",
        wait=False,
        units=None if restart_gateway else rel.NoUnits(),
    )
    if outcome.status == "busy":
        holder = outcome.holder or {}
        log.warning(
            "components.hermes_busy",
            job_id=job_id,
            holder_pid=holder.get("pid"),
            holder_caller=holder.get("caller"),
            holder_since=holder.get("started_at"),
        )
        return False
    if not outcome.ok:
        log.warning(
            "updater.hermes_venv_repair_failed",
            job_id=job_id,
            status=outcome.status,
            error=outcome.error,
            remedy=outcome.remedy or HERMES_VENV_REMEDY,
        )
        return False

    try:
        after = hermes._probe_mcp_client(venv_python, None, agent_id="hermes")
    except Exception as exc:
        log.warning("updater.hermes_mcp_client_probe_failed", job_id=job_id, error=str(exc))
        return False
    if not after.get("ok"):
        log.warning(
            "updater.hermes_venv_repair_ineffective",
            job_id=job_id,
            stage=after.get("stage"),
            error=after.get("error"),
            remedy=HERMES_VENV_REMEDY,
        )
        return False

    log.info(
        "updater.hermes_venv_repaired",
        job_id=job_id,
        venv=str(venv),
        release=outcome.release_id,
        warnings=outcome.warnings or None,
    )
    return True


class PostActivationResult(tuple):
    """``(source, target)`` schema versions, plus the conversion blockers.

    Still a 2-tuple, so every caller that unpacks ``source, target =`` (the
    install.sh re-run path) or indexes ``result[0]`` / ``result[1]`` (the
    post-swap child script, an ABI between releases) keeps working.
    ``conversion_blockers`` lists what the profile-centric conversion
    (ADR-0029) could not convert: ``{"target": ..., "reason": ...}``.
    ``unassigned_models`` lists every slot whose model the runner-system
    conversion unassigned (ADR-0028 §7, #2694):
    ``{"slot", "model", "quant", "remedy"}``. Not a blocker: the conversion
    did what it must, but the operator has to be told, and how to fix it.
    ``model_bundles`` lists the engine bundles the upgrade fetched or could
    not fetch (#2739, ADR-0030): ``{"model", "outcome", "path", "remedy",
    "error"?}``. Not a blocker either: a failed fetch leaves the slot's spawn
    preflight naming the same remedy.
    """

    conversion_blockers: list[dict[str, str]]
    unassigned_models: list[dict[str, str]]
    model_bundles: list[dict[str, str]]

    def __new__(
        cls,
        source: int,
        target: int,
        conversion_blockers: list[dict[str, str]] | None = None,
        unassigned_models: list[dict[str, str]] | None = None,
        model_bundles: list[dict[str, str]] | None = None,
    ) -> PostActivationResult:
        obj = super().__new__(cls, (source, target))
        obj.conversion_blockers = list(conversion_blockers or [])
        obj.model_bundles = list(model_bundles or [])
        obj.unassigned_models = list(unassigned_models or [])
        return obj


def conversion_blockers(report: Any) -> list[dict[str, str]]:
    """What a profile-centric conversion run left for the operator.

    One ``{"target", "reason"}`` per entry of ``plan.blockers``: a blocked
    slot (its file, with :attr:`SlotPlan.error`), a profile
    (``profile:<name>``) or a stack (``stack:<name>``) with its reasons.
    """
    plan = getattr(report, "plan", None)
    if plan is None:
        return []
    slot_errors = {s.file: s.error for s in getattr(plan, "blocked", [])}
    profile_reasons = getattr(plan, "blocked_profiles", {}) or {}
    stack_errors = {st.stack: st.errors for st in getattr(plan, "stacks", []) if st.errors}
    out: list[dict[str, str]] = []
    for target in plan.blockers:
        if target.startswith("profile:"):
            reasons = profile_reasons.get(target.removeprefix("profile:")) or []
            reason = "; ".join(str(r) for r in reasons)
        elif target.startswith("stack:"):
            reason = "; ".join(str(e) for e in stack_errors.get(target.removeprefix("stack:"), []))
        else:
            reason = str(slot_errors.get(target) or "")
        out.append({"target": str(target), "reason": reason or "cannot be converted as it is"})
    return out


def run_post_activation_migrations(
    min_data_version: int = 1,
    *,
    job_id: str | None = None,
    skip_catalogue_sync: bool = False,
    repair_hermes_venv: bool = True,
    upgrade_memory_engine_venv: bool = True,
    converge_companions: bool = True,
    honour_restore_marker: bool = False,
    rerender_after_runner_conversion: bool = False,
    fetch_model_bundles: bool = True,
) -> PostActivationResult:
    """Run every post-activation migration pass hal0 ships, in order.

    The single sequence both :meth:`Updater.commit` (self-update) and
    ``install.sh``'s repair/upgrade-in-place re-run path call, so the two
    upgrade paths converge on the same on-disk state (GH #1475). There is
    no other caller of the
    ``hal0.toml`` schema-migration chain outside this function and ``hal0
    config migrate`` (``cli/config_commands.py``).

    ``min_data_version`` defaults to ``1`` — the same floor
    :func:`_maybe_run_config_migrations` applies internally
    (``max(min_data_version or 1, latest_version())``), so a caller with
    no release manifest to read a real value from (install.sh) still
    migrates all the way to whatever schema version the running code
    knows about. ``Updater.commit()`` passes the release manifest's
    ``min_data_version`` explicitly.

    Synchronous by design — a shell caller (install.sh) invokes this
    directly with no event loop; ``commit()`` wraps the whole call in one
    ``asyncio.to_thread`` hop instead of one per pass.

    Error handling mirrors ``commit()``'s original inline sequence: the
    runner-system conversion (first, :mod:`hal0.config.migrations.runner_system`)
    and the schema migration (second) are NOT swallowed. The conversion restores
    its own backup before raising, so a failure leaves config as it was; the
    post-swap caller reports it and ``hal0 update`` does not call the run
    converged. A hard failure in the schema migration means
    the running code and the on-disk schema have diverged, which the
    caller must treat as fatal (``commit()`` nukes the staged tree and
    aborts; install.sh's ``set -euo pipefail`` aborts the script on a
    non-zero exit). The data-cleanup passes after it, starting with the
    ``registry.toml`` retirement
    (:func:`hal0.registry.toml_retirement.retire_registry_toml`), are each
    independently best-effort: one pass's exception is logged and never
    blocks the others or the caller's activation.

    ``upgrade_memory_engine_venv`` gates the hindsight engine-venv
    convergence pass (:func:`hal0.memory.engine_upgrade.upgrade_memory_engine`).
    It runs LAST: it is the slowest pass by an order of magnitude (a pip
    build of the engine's ML wheel set) and it stops/starts the
    ``hindsight-api`` companion service — every cheap config-convergence
    pass should land even when it wedges, and it has no ordering dependency
    on any of them (the engine venv is disjoint state).
    :func:`check_outstanding_migrations` passes ``False`` — the engine pass
    is the ``skip_catalogue_sync``/``repair_hermes_venv`` argument taken
    further still: a boot-time pip is an unbounded network operation, the
    stop/start is a surprise memory outage on every crash-restart race, and
    the first start of a newer engine triggers its ONE-WAY database
    migration — none of which belongs outside an operator-visible update.
    At boot the pass only logs ``updater.memory_engine_stale`` with the
    remedy.

    ``skip_catalogue_sync`` omits the runner-image catalogue sync the
    ``runner-images`` component arm runs on an update (a network fetch of the
    runner-images repo's ``images.json``). Boot runs its own non-blocking
    sync from ``hal0-api`` (``_boot_background_tasks``), so
    :func:`check_outstanding_migrations` passes ``True`` here; an actual
    update passes the default ``False``.

    ``rerender_after_runner_conversion`` re-renders the slot units when the
    runner-system conversion changed something, so a running or restarted slot
    picks up the converted image and runner. :func:`check_outstanding_migrations`
    passes ``True``; an update re-renders every unit after this call anyway.

    ``fetch_model_bundles`` lets the kokoro bundle pass
    (:func:`hal0.config.migrations.kokoro_bundle.ensure_kokoro_bundle`) pull
    ``kokoro-v1`` once when kokoro is in use and the store holds no loadable
    bundle (#2739). :func:`check_outstanding_migrations` passes ``False``: the
    boot-time net never downloads, it only logs the remedy.

    ``honour_restore_marker`` makes the runner-system conversion stand down
    after an operator restored its backup (a downgrade).
    :func:`check_outstanding_migrations`, the API's boot-time safety net,
    passes ``True``; an actual update passes the default ``False`` and
    converts again.

    ``converge_companions`` gates whether the component catalog
    (:func:`hal0.components.runner.converge_components`, the final pass
    below) is even allowed to mutate. It maps straight to that call's
    ``apply`` kwarg: ``True`` (the default, what an actual update passes)
    lets each arm converge; ``False`` (what :func:`check_outstanding_migrations`
    passes) makes every arm diagnose-only, matching the boot-time posture
    the other three flags (``skip_catalogue_sync``, ``repair_hermes_venv``,
    ``upgrade_memory_engine_venv``) already hold at boot. Without this flag
    the openwebui arm — which has no dedicated "install" toggle of its own —
    would still mutate at boot; adding an explicit kwarg here instead of
    inferring "diagnose-only" from the other three being off keeps that
    behaviour legible rather than implicit.

    Returns the ``(source, target)`` schema-version tuple for breadcrumb
    logging, the same shape ``commit()`` already returned, as a
    :class:`PostActivationResult` whose ``conversion_blockers`` lists every
    slot, profile or stack the profile-centric conversion left blocked
    (an empty list when the install converged), and whose
    ``unassigned_models`` lists every slot the runner-system conversion
    unassigned a model from.
    """
    # The runner-system conversion (ADR-0028) runs FIRST and is fatal: it
    # rewrites raw TOML before anything validates config, because a removed
    # runner key in ``[slots].default_images`` fails config load. It backs up
    # before writing and restores every write before raising, so a failure
    # here leaves the box exactly as it was and the caller reports it.
    from hal0.config.migrations.runner_system import migrate_runner_system

    conversion = migrate_runner_system(job_id=job_id, honour_restore_marker=honour_restore_marker)
    # The profile-centric conversion (ADR-0029) runs next, on the converted
    # runner keys, and is fatal the same way: it moves every tune onto a
    # profile, verifies each converted slot's launch, and rolls back every
    # write before raising.
    from hal0.config.migrations.profile_centric_apply import migrate_profile_centric

    profiles = migrate_profile_centric(job_id=job_id, honour_restore_marker=honour_restore_marker)
    if rerender_after_runner_conversion and (conversion.backup or profiles.backup):
        try:
            rerender_slot_units(job_id=job_id)
        except Exception as exc:
            log.warning("updater.runner_conversion_rerender_failed", job_id=job_id, error=str(exc))

    migration_info = _maybe_run_config_migrations(min_data_version, job_id=job_id)

    # Retire a leftover registry.toml: back it up, rename it, never import
    # it (every box at the 1.3.0 floor already did its first-boot import).
    # Non-fatal: nothing reads the file, so a box that cannot retire it
    # (a root-owned 0600 file, say) logs a warning and `hal0 doctor all`
    # reports it. retire_registry_toml never raises; the guard is defensive.
    try:
        from hal0.registry.toml_retirement import retire_registry_toml

        retired = retire_registry_toml(job_id=job_id)
        if retired.get("outcome") == "failed":
            log.warning(
                "updater.registry_toml_retire_failed", job_id=job_id, error=retired.get("reason")
            )
    except Exception as exc:
        log.warning("updater.registry_toml_retire_failed", job_id=job_id, error=str(exc))

    try:
        repair_hermes_mcp_client(job_id=job_id, install=repair_hermes_venv)
    except Exception as exc:
        log.warning("updater.hermes_venv_repair_pass_failed", job_id=job_id, error=str(exc))
        # Non-fatal, same posture as the header repair above: the box keeps
        # the venv it had and the logged remedy names the manual repair.

    try:
        mirror_hermes_plugins(job_id=job_id, install=repair_hermes_venv)
    except Exception as exc:
        log.warning("updater.hermes_plugin_mirror_pass_failed", job_id=job_id, error=str(exc))
        # Non-fatal: the stale tree stays and `hal0 doctor` flags it.

    try:
        relabel_stale_vulkan_slots(job_id=job_id)
    except Exception as exc:
        log.warning("updater.vulkan_migration_failed", job_id=job_id, error=str(exc))
        # Non-fatal: an affected slot keeps refusing to load (the #1923
        # preflight guard) until the operator manually relabels its device.

    try:
        strip_retired_image_keys(job_id=job_id)
    except Exception as exc:
        log.warning("updater.image_keys_strip_failed", job_id=job_id, error=str(exc))
        # Non-fatal: the img slot still loads (the slot manager reads raw TOML),
        # but a file that still carries the key fails hal0 doctor's slot
        # validation until the operator deletes the line.

    # ── Kokoro store bundle (#2739, ADR-0030): from 1.4.0 the tts slot
    # launches only from the pulled kokoro-v1 bundle, so an upgraded box that
    # uses kokoro gets it fetched once here instead of losing speech until a
    # manual pull. Never fatal: a failed fetch is reported with its remedy.
    model_bundles: list[dict[str, str]] = []
    try:
        from hal0.config.migrations.kokoro_bundle import ensure_kokoro_bundle

        bundle_report = ensure_kokoro_bundle(job_id=job_id, fetch=fetch_model_bundles)
        if bundle_report is not None:
            model_bundles.append(bundle_report)
    except Exception as exc:
        log.warning("updater.kokoro_bundle_pull_failed", job_id=job_id, error=str(exc))

    try:
        from hal0.agents.bundled_skills import prune_retired_skills

        prune_retired_skills(ship_root=None)
    except Exception as exc:
        log.warning("updater.retired_skills_prune_failed", job_id=job_id, error=str(exc))
        # Non-fatal: a retired skill stays offered until the next install.sh.

    # ── Component convergence (spec 2026-08-30): the catalog runs LAST for
    # the same reason the engine pass did — hindsight (inside it) is the
    # slowest pass and stops a companion service; every cheap pass above
    # must land even when a component wedges. Flags carry the callers'
    # existing semantics: boot (check_outstanding_migrations) passes
    # engine/hermes-install/catalogue-sync off, an actual update passes them on.
    try:
        from hal0.components.runner import converge_components

        converge_components(
            job_id=job_id,
            apply=converge_companions,
            catalogue_sync=not skip_catalogue_sync,
            engine=upgrade_memory_engine_venv,
            hermes_install=repair_hermes_venv,
        )
    except Exception as exc:
        log.warning("updater.components_converge_failed", job_id=job_id, error=str(exc))
        # Non-fatal: each arm reports operational failures as status dicts
        # (and logs its own event with a remedy); reaching here means a
        # genuine bug, and the box keeps its current working components
        # either way — never a reason to fail an update.

    return PostActivationResult(
        migration_info[0],
        migration_info[1],
        conversion_blockers(profiles),
        # #2694: the runner-system report used to be dropped here except its
        # ``backup``, so a slot that lost its model was told only to the journal.
        list(getattr(conversion, "unassigned", None) or []),
        model_bundles,
    )


def check_outstanding_migrations(*, job_id: str | None = None) -> tuple[int, int] | None:
    """Boot-time safety net for #1960: heal a box that missed its migrations.

    Two boxes need this, not one: (a) any box that upgraded through the
    pre-#1960 in-process/pre-swap call never got the incoming release's
    migrations at all, permanently — there is no later ``commit()`` call
    that will ever re-run them; (b) even post-fix,
    :func:`_run_post_activation_migrations_after_swap` can itself fail (disk
    full, a bug in one specific pass), and ``commit()`` deliberately does not
    retry it inline — see that function's caller for why. Calling this once
    at every ``hal0-api`` boot heals both: every pass
    ``run_post_activation_migrations`` runs here is documented as an
    idempotent "stale? fix it : no-op" sweep (see each pass's own
    docstring), so a converged box pays for one ``hal0.toml`` read plus a
    handful of near-instant no-ops.

    ``skip_catalogue_sync=True``: the API boot runs its own background
    catalogue sync, so this safety net never blocks startup on a network
    fetch.

    Never raises — a startup safety net that could itself fail startup would
    be worse than the staleness it exists to fix. Returns the ``(source,
    target)`` schema tuple on success, ``None`` on any failure (already
    logged as a warning; this is a background sweep, not a user-initiated
    update, so it does not warrant the ERROR level
    :meth:`Updater.commit` uses for the same failure).
    """
    try:
        return run_post_activation_migrations(
            job_id=job_id,
            skip_catalogue_sync=True,
            honour_restore_marker=True,
            rerender_after_runner_conversion=True,
            repair_hermes_venv=False,
            upgrade_memory_engine_venv=False,
            converge_companions=False,
            fetch_model_bundles=False,
        )
    except Exception as exc:
        log.warning("updater.boot_migration_check_failed", job_id=job_id, error=str(exc))
        return None


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


def installed_version() -> str:
    """The release version actually running — ``current/VERSION`` when present,
    else ``hal0.__version__``.

    A nightly wheel deliberately carries the BASE version in its package
    metadata: PEP 440 has no vocabulary for the ``v<base>-nightly.<stamp>``
    tag shape, so pip metadata can't hold it (that is why release.yml's
    version gate only base-matches nightly tags). ``hal0.__version__`` is
    therefore blind to WHICH nightly is installed — a box already on the
    newest nightly compared ``1.1.0 < 1.1.0-nightly.<stamp>`` and reported
    "update available" forever, re-staging the same release on every
    ``hal0 update`` (live on ct105 + ct150, 2026-09-01).

    The release tarball ships its exact version in the top-level ``VERSION``
    file and activation swaps ``current`` onto that tree, so
    ``current/VERSION`` IS the installed identity. The wheel version remains
    the fallback for source/dev installs with no release dir. Fail-soft: any
    unreadable/empty VERSION degrades to the old behavior, never an error.
    """
    try:
        v = (_current_symlink() / "VERSION").read_text(encoding="utf-8").strip()
    except OSError:
        return hal0.__version__
    return v or hal0.__version__


# ── Release-directory tokens (the only thing that crosses the privileged seam) ──
#
# #1464: the unprivileged API never hands the root helper a *path*. It hands a
# bare directory BASENAME, matched against this regex on both sides. No '/', no
# leading '.', bounded length — so a validated token can only ever name a direct
# child of ``_usr_lib_root()`` and can never traverse out of it.
RELEASE_DIR_RE = re.compile(r"^hal0-[A-Za-z0-9][A-Za-z0-9._+-]{0,63}$")


def release_dir_name(version: str) -> str:
    """Return the ``hal0-<version>`` directory basename for ``version``."""
    return assert_release_dir_name(f"hal0-{version}")


def assert_release_dir_name(name: str) -> str:
    """Return ``name`` if it is a legal release-directory basename, else raise.

    Raises :class:`ValueError` — callers translate that into their own typed
    error. Mirrors ``validate_dir_name`` in ``installer/wrappers/hal0-update``
    EXACTLY; the wrapper's copy is a fail-fast convenience, this one (running as
    root inside the helper) is the security boundary.
    """
    if not isinstance(name, str) or not RELEASE_DIR_RE.match(name):
        raise ValueError(f"bad release directory name: {name!r}")
    return name


def assert_trusted_release_dir(path: Path, *, euid: int | None = None) -> None:
    """Refuse to activate a tree the ``hal0`` service account could have written.

    Called from the ROOT boundary only (``hal0.updater.privileged.main``), which
    is the one caller acting on behalf of an unprivileged party. ``activate``
    ends in ``pip install <path>``, which executes the tree's build backend **as
    root** — so if a compromised hal0-api could write that tree, the seam would
    be a root-code-execution hole rather than a narrow grant. The trust test is
    the classic one: the directory and its parent must be owned by uid 0 and not
    group/other writable, so only root can have produced their contents.

    No-op below euid 0: an operator running ``sudo hal0 update`` staged the tree
    themselves, and a dev/CI/``HAL0_HOME`` run has no privilege boundary to
    protect. ``euid`` is injectable so this is testable without root (and so the
    predicate does not depend on a process-global ``os.geteuid`` that other
    suites monkeypatch).

    Residual, stated plainly: the check is top-level, not recursive. On the seam
    path root staged the tree itself into a root-owned ``<usr_lib>``, so a
    service-writable subdirectory is not reachable.
    """
    if (os.geteuid() if euid is None else euid) != 0:
        return
    for candidate in (path.parent, path):
        try:
            st = candidate.lstat()
        except OSError as exc:
            raise UpdateError(
                f"cannot stat {candidate}: {exc}",
                details={"path": str(candidate), "error": str(exc)},
            ) from exc
        if st.st_uid != 0 or st.st_mode & 0o022:
            raise UpdatePrivilegeError(
                f"refusing to activate {path}: {candidate} is not root-owned "
                f"(uid={st.st_uid}, mode={st.st_mode & 0o7777:04o}) — a tree the "
                "service account can write must never be pip-installed as root",
                details={
                    "path": str(path),
                    "offending_path": str(candidate),
                    "uid": st.st_uid,
                    "mode": f"{st.st_mode & 0o7777:04o}",
                    "hint": f"sudo chown -R root:root {candidate} && sudo chmod go-w {candidate}",
                },
            )


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


def _raw_hal0_toml() -> dict[str, Any] | None:
    """Read ``hal0.toml`` as a raw dict, or ``None`` when absent/unreadable.

    Deliberately raw: :meth:`Updater.commit` reads the schema version from it
    after a failed migration, when the file may not pass today's validators.
    """
    from hal0.config.loader import _read_toml

    path = paths.hal0_toml()
    if not path.exists():
        return None
    try:
        return _read_toml(path)
    except Exception:
        return None


def _raw_schema_version(raw: dict[str, Any] | None) -> int:
    """``meta.schema_version`` off a raw config dict, defaulting to 1."""
    if not isinstance(raw, dict):
        return 1
    meta = raw.get("meta")
    if not isinstance(meta, dict):
        return 1
    try:
        return int(meta.get("schema_version", 1) or 1)
    except (TypeError, ValueError):
        return 1


def converge_kfd_group(*, job_id: str | None = None) -> str:
    """Align ``/dev/kfd``'s group with the render node's. PRIVILEGED CALLERS ONLY.

    NOT part of :func:`run_post_activation_migrations`, deliberately. That chain
    runs in an ``asyncio.to_thread`` inside ``hal0-api`` — ``User=hal0`` — and
    BEFORE ``seam.activate()``. Calling it from there was wrong twice over: the
    ``chown`` hit EPERM as an unprivileged user and reported ``failed`` for
    exactly the boxes it was meant to converge, and on the FIRST update
    delivering this code the running daemon is still executing the previous
    release, where this function does not exist at all.

    Convergence now happens root-side in :func:`refresh_gpu_perms_unit`, which
    runs during ``activate`` (euid 0) and starts the freshly-installed unit once
    so a box converges without waiting for a reboot. ``install.sh`` covers the
    fresh-install path, also as root.

    Kept as a callable seam for those privileged entry points and for tests.

    #1953. A plain LXC passthrough leaves the compute node ``root:root 0660``
    while ``/dev/dri/renderD128`` lands ``root:render 0660``. Slot containers
    run rootful so ROCm works fine, but every hal0-user probe reports the GPU
    unusable — and the operator is told to re-forward a device that is already
    forwarded. Converging the two nodes makes both identities agree.

    Runs BEFORE :func:`relabel_stale_vulkan_slots` deliberately: that pass
    branches on the compute node's state to decide ``gpu-rocm`` vs ``cpu``, so
    it must see the converged answer rather than race the repair.

    Best-effort by construction. On an unprivileged LXC the node's ownership is
    host-mapped and ``chown`` raises ``EPERM``; the remedy there is a ``gid=``
    on the host's ``dev`` entry, which the guard's own error text now spells
    out. Never raises, never blocks an update.
    """
    from hal0.providers._gpu import converge_kfd_device_group, host_is_amd_gpu

    if not host_is_amd_gpu():
        return "skipped"
    status, detail = converge_kfd_device_group()
    if status == "changed":
        log.warning("updater.kfd_group_converged", job_id=job_id, detail=detail)
    elif status == "failed":
        log.warning("updater.kfd_group_converge_refused", job_id=job_id, detail=detail)
    else:
        log.info("updater.kfd_group_noop", job_id=job_id, status=status, detail=detail)
    return status


def _vulkan_lane_is_loadable(holder: dict) -> bool:
    """Would ``load_sync`` accept this ``gpu-vulkan`` llama slot as configured?

    #1948 re-enabled the Vulkan LLM lane behind an image gate, which turns
    this migration's premise into a question rather than a constant. Under
    #1923 every ``gpu-vulkan`` llama slot was unloadable by definition, so
    relabeling all of them was strictly a rescue. Now a slot whose resolved
    runner image is Vulkan-validated loads fine — relabeling THAT one would be
    a reverse migration of a working configuration, and on a kfd-less box it
    would push a functioning GPU slot onto the CPU.

    Delegates to :func:`hal0.providers._gpu.vulkan_lane_serves`, the one
    predicate the derivation ladders, the bench harness and the installer
    preflight also use — so no two of them can reach different conclusions
    about the same box.

    Fails CLOSED on any error: an unresolvable image is not an
    established-safe one, and the relabel is the conservative outcome.
    """
    from hal0.providers._gpu import vulkan_lane_serves

    return vulkan_lane_serves(holder)


def relabel_stale_vulkan_slots(
    *,
    job_id: str | None = None,
    rocm_lane: bool | None = None,
    amd_host: bool | None = None,
) -> int:
    """Relabel retired ``device = "gpu-vulkan"`` slot TOMLs (upgrade migration).

    #1888/PR #1923 retired the Vulkan LLM lane and switched ``load_sync`` to
    hard-refuse loading a GPU slot when the ROCm compute node (``/dev/kfd``)
    is not visible (:func:`hal0.providers._gpu.require_kfd_for_gpu_slot`). PR
    #1923 relabeled the SEED slot TOMLs the installer ships
    (``installer/etc-hal0/slots/*.toml``) from ``gpu-vulkan`` to ``gpu-rocm``,
    but deliberately did NOT touch slot TOMLs already materialised on an
    existing install — see #1924. This is that follow-up: every EXISTING
    on-disk slot TOML carrying the retired label is relabeled here, so an
    updated install doesn't refuse to load slots it was serving fine
    yesterday.

    AMD-only, exactly matching :func:`~hal0.providers._gpu.require_kfd_for_gpu_slot`'s
    own scope for a ``gpu-vulkan`` device: that guard only fires when the
    amdgpu kernel driver is bound on THIS host
    (:func:`hal0.providers._gpu.host_is_amd_gpu`). An Intel iGPU or an NVIDIA
    card without CDI has no ``/dev/kfd`` by design, is never gated, and keeps
    working post-#1923 exactly as it did before — relabeling its
    ``gpu-vulkan`` slots here (which an unscoped ``kfd_present()`` check
    would do, since none of those hosts has ``/dev/kfd`` either) would
    silently downgrade a perfectly working GPU slot to CPU for no reason. On
    a non-AMD host this function is therefore a full no-op: nothing is
    inspected or written.

    On an AMD host, for every slot TOML with a literal ``device =
    "gpu-vulkan"`` (top-level or nested under ``[slot]``, the same two-shape
    handling the slot loader accepts) that is ALSO llama.cpp-backed
    (see runtime scope below), relabel ``device`` to:

    * ``"gpu-rocm"`` — when :func:`hal0.providers._gpu.rocm_lane_present`
      reports the ROCm lane usable: the compute node ``/dev/kfd`` AND a
      ``/dev/dri/renderD*`` render node, both of which a ROCm slot opens
      (#2356 — kfd alone moved slots onto a device they could not open). This
      is the same target PR #1923 gave the seed TOMLs, and the slot keeps
      running on the GPU.
    * ``"cpu"`` — when it is not. This is a genuine, operator-visible
      behavior change (the slot drops off the GPU entirely and gets much
      slower) — the #1867 rails require every mutation to be logged
      explicitly, so this is logged as its own WARNING-level breadcrumb,
      never folded silently into the routine "migrated" line.

    Runtime scope — llama.cpp-backed slots ONLY: ``device = "gpu-vulkan"``
    is not exclusively an llama.cpp label. ``capabilities/catalog.py``
    deliberately keeps it for the non-llama runtimes (Kokoro TTS,
    whisper.cpp/Moonshine STT, ComfyUI), where it means "the picker's GPU
    row", not "this image runs Vulkan" (#1941) — #1923's Vulkan-lane
    retirement and its ``require_kfd_for_gpu_slot`` guard are about
    llama.cpp's Standard runner specifically, not those.
    Relabeling a non-llama slot here would be actively wrong on both kfd
    axes: ``gpu-rocm`` is a label its image can't honor (and
    ``gpu_visibility_env`` would silently swap
    ``GGML_VK_VISIBLE_DEVICES`` for ``HIP_VISIBLE_DEVICES``, dropping any
    ``gpu_index`` pin), and ``cpu`` would strip ``/dev/dri`` from a slot that
    was working fine. So every candidate slot is resolved through
    :func:`hal0.providers.container._spec_provider_for` — the same
    authoritative runtime-family discriminator ``load_sync`` itself uses —
    and skipped ENTIRELY (no relabel, no warning, no log line, on either kfd
    axis) unless it resolves to ``None`` (the default llama-server GPU
    provider). The kfd-absent non-llama case (``require_kfd_for_gpu_slot``
    itself over-firing for non-llama runtimes) was a separate bug, fixed in
    the guard itself (#1941): it now takes a ``runtime_lane`` derived from
    the resolved provider's own ``gpu_runtime_needs_rocm`` declaration, so a
    Kokoro/Moonshine ``gpu-vulkan`` slot is neither relabeled here nor
    refused there — while a ComfyUI or Qwen3-TTS slot, whose image really is
    ROCm, keeps its refusal. Note the two scopes are deliberately NOT the
    same predicate: this migration skips every non-llama slot (relabeling
    one would break its device passthrough regardless of backend), the guard
    keys on what the image needs at launch.

    Only the ``device`` key is ever changed in VALUE — narrow scope per the
    #1867 rails (no other field or slot is touched, and a slot whose
    ``device`` is not literally ``"gpu-vulkan"``, or that isn't
    llama.cpp-backed, is skipped entirely). Note that "narrow scope" is
    about which *keys* get new values, not byte-for-byte file preservation:
    like every other pass in this module, the write goes through
    :func:`~hal0.config.loader.write_toml_atomic`, which reserializes the
    whole parsed document via ``tomli_w`` — so any comments in a touched
    slot's TOML are dropped on that rewrite, as they are for every other
    pass here.
    Image scope — #1948, and the reason this pass is no longer a blanket
    rescue: the Vulkan LLM lane is supported again, on a runner image whose
    Vulkan backend passed validation. Under #1923 every ``gpu-vulkan`` llama
    slot was unloadable by definition, so relabeling all of them was strictly
    a rescue. Now a slot whose resolved image serves the lane
    (:func:`_vulkan_lane_is_loadable`) is SKIPPED — relabeling it would be a
    reverse migration of a working configuration, and on a kfd-less box it
    would push a functioning GPU slot onto the CPU. Slots on an unvalidated
    image still refuse to load, so those are still rescued exactly as before.

    Note what this does NOT do: it never relabels ``gpu-rocm`` BACK to
    ``gpu-vulkan``. A box that already ran this migration keeps the device it
    was given; the re-enabled lane is offered to new slots, not forced onto
    old ones.

    Idempotent: once a slot's ``device`` reads ``"gpu-rocm"`` or ``"cpu"`` it
    no longer matches the ``"gpu-vulkan"`` guard, so a second run touches
    nothing and logs nothing
    new.

    Args:
        job_id: Optional breadcrumb for structured-log tracing.
        rocm_lane: Override for
            :func:`hal0.providers._gpu.rocm_lane_present` — test seam.
            ``None`` (the default) probes the real host's ``/dev/kfd`` and
            render node.
        amd_host: Override for :func:`hal0.providers._gpu.host_is_amd_gpu` —
            test seam, same shape as
            :func:`~hal0.providers._gpu.require_kfd_for_gpu_slot`'s own
            parameter. ``None`` (the default) probes the real host.

    Returns:
        Number of slot TOMLs relabeled.
    """
    import tomllib

    from hal0.config.loader import write_toml_atomic
    from hal0.config.paths import slots_config_dir
    from hal0.providers._gpu import host_is_amd_gpu as _probe_host_is_amd_gpu
    from hal0.providers._gpu import rocm_lane_present as _probe_rocm_lane

    # Private import, deliberate: this must be the EXACT discriminator
    # load_sync uses to pick a provider, not a re-implementation of it — do
    # not "clean this up" into a public wrapper or a local re-derivation,
    # that would risk drifting from the real dispatch logic it mirrors.
    from hal0.providers.container import _spec_provider_for

    is_amd = _probe_host_is_amd_gpu() if amd_host is None else amd_host
    if not is_amd:
        # Not the hardware #1923's guard characterised or gates — a
        # gpu-vulkan slot here was never broken by it. Leave it alone.
        return 0

    have_rocm = _probe_rocm_lane() if rocm_lane is None else rocm_lane

    relabeled = 0
    slots_dir = slots_config_dir()
    for toml_path in sorted(slots_dir.glob("*.toml")) if slots_dir.is_dir() else []:
        slot_name = toml_path.stem
        try:
            raw = tomllib.loads(toml_path.read_text(encoding="utf-8"))
        except Exception as exc:
            log.warning("updater.vulkan_migration_slot_unreadable", slot=slot_name, error=str(exc))
            continue
        holder: dict | None = None
        if raw.get("device") == "gpu-vulkan":
            holder = raw
        elif isinstance(raw.get("slot"), dict) and raw["slot"].get("device") == "gpu-vulkan":
            holder = raw["slot"]
        if holder is None:
            continue
        try:
            provider = _spec_provider_for(holder)
        except Exception as exc:
            log.warning(
                "updater.vulkan_migration_slot_runtime_unresolved",
                slot=slot_name,
                error=str(exc),
            )
            continue
        if provider is not None:
            # A non-llama runtime (Kokoro/Moonshine/ComfyUI/FLM/Qwen3TTS)
            # genuinely runs a Vulkan image on this device string — #1923's
            # guard is scoped to llama.cpp's Standard runner, not
            # these. Leave completely untouched (see docstring; #1941 tracks
            # the separate kfd-absent over-firing issue for this class).
            continue
        if _vulkan_lane_is_loadable(holder):
            # #1948: this slot's resolved runner image serves the Vulkan lane
            # correctly, so it is NOT a stale label — it loads. Relabeling it
            # would be a REVERSE migration of a deliberate, working choice,
            # and on a kfd-less box it would drag the slot to CPU for no
            # reason at all. Leave it exactly as the operator set it.
            log.info(
                "updater.vulkan_migration_slot_lane_supported",
                job_id=job_id,
                slot=slot_name,
                note="gpu-vulkan slot runs a Vulkan-validated runner image — not migrated",
            )
            continue
        new_device = "gpu-rocm" if have_rocm else "cpu"
        holder["device"] = new_device
        try:
            write_toml_atomic(toml_path, raw)
        except Exception as exc:
            log.warning("updater.vulkan_migration_write_failed", slot=slot_name, error=str(exc))
            continue
        relabeled += 1
        if have_rocm:
            log.warning(
                "updater.slot_vulkan_relabeled_rocm",
                job_id=job_id,
                slot=slot_name,
                old="gpu-vulkan",
                new=new_device,
                note=(
                    "this slot's runner image is not validated for the Vulkan lane "
                    "(#1888); relabeled to gpu-rocm — /dev/kfd and a render node are "
                    "present, slot keeps running on GPU"
                ),
            )
        else:
            log.warning(
                "updater.slot_vulkan_relabeled_cpu_fallback",
                job_id=job_id,
                slot=slot_name,
                old="gpu-vulkan",
                new=new_device,
                note=(
                    "BEHAVIOR CHANGE: this slot's runner image is not validated for the "
                    "Vulkan lane (#1888) and this host has no ROCm lane (/dev/kfd, the "
                    "ROCm compute node, together with a /dev/dri/renderD* render node) "
                    "— relabeled to cpu. This slot no longer runs on the GPU and will "
                    "be significantly slower until either both nodes are forwarded and "
                    "the slot is re-pointed at gpu-rocm, or the slot is repinned to a "
                    "Vulkan-validated runner image."
                ),
            )
    return relabeled


_IMAGE_TABLE_RE = re.compile(r"^\s*\[\s*image\s*\]\s*(#.*)?$")
_ANY_TABLE_RE = re.compile(r"^\s*\[")
_IDLE_RESTORE_KEY_RE = re.compile(r"^\s*idle_restore_minutes\s*=")


def strip_retired_image_keys(*, job_id: str | None = None) -> int:
    """Take ``idle_restore_minutes`` out of every slot TOML's ``[image]`` table.

    ADR-0038: image mode ends when generation stops, so nothing reads the key
    and ``ImageGenConfig`` refuses it. The line is removed in place, so the
    file's comments survive. A shape the line edit cannot handle (an inline
    ``image = {…}`` table, a dotted ``image.idle_restore_minutes`` key) is
    re-serialised without the key, which drops its comments. Idempotent. A file
    that cannot be read or written is logged and skipped. Returns the number of
    files changed.
    """
    import tomllib

    import tomli_w

    from hal0.config.migrations.runner_system import _replace_file
    from hal0.config.paths import slots_config_dir

    changed = 0
    slots_dir = slots_config_dir()
    for toml_path in sorted(slots_dir.glob("*.toml")) if slots_dir.is_dir() else []:
        slot_name = toml_path.stem
        try:
            text = toml_path.read_text(encoding="utf-8")
            raw = tomllib.loads(text)
        except (OSError, tomllib.TOMLDecodeError, UnicodeDecodeError) as exc:
            log.warning("updater.image_keys_slot_unreadable", slot=slot_name, error=str(exc))
            continue
        image = raw.get("image")
        if not isinstance(image, dict) or "idle_restore_minutes" not in image:
            continue
        expected = {
            **raw,
            "image": {k: v for k, v in image.items() if k != "idle_restore_minutes"},
        }

        in_image = False
        dropped = False
        kept: list[str] = []
        for line in text.splitlines(keepends=True):
            if _IMAGE_TABLE_RE.match(line):
                in_image = True
            elif _ANY_TABLE_RE.match(line):
                in_image = False
            elif in_image and not dropped and _IDLE_RESTORE_KEY_RE.match(line):
                dropped = True
                continue
            kept.append(line)
        edited = "".join(kept)

        comments_kept = False
        if edited != text:
            try:
                comments_kept = tomllib.loads(edited) == expected
            except tomllib.TOMLDecodeError:
                comments_kept = False
        try:
            data = edited if comments_kept else tomli_w.dumps(expected)
            _replace_file(toml_path, data.encode("utf-8"))
        except Exception as exc:
            log.warning("updater.image_keys_write_failed", slot=slot_name, error=str(exc))
            continue
        changed += 1
        log.info(
            "updater.image_idle_restore_stripped",
            job_id=job_id,
            slot=slot_name,
            value=image["idle_restore_minutes"],
            comments_kept=comments_kept,
        )
    return changed


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


# ── Privileged primitives (#1464) ──────────────────────────────────────────────
#
# The three operations below are the ENTIRE set of update steps that need root
# on a shipped box: they write ``/usr/lib/hal0`` (root:root 0755, never
# service-writable) or the root-owned venv. Everything else the updater does —
# config migrations, seed-profile pruning, the mtp/extra-args/image sweeps, the
# rollback breadcrumb, the slot-unit re-render — writes ``/etc/hal0``,
# ``/var/lib/hal0`` or goes through the existing ``hal0-systemctl`` seam, all of
# which the ``hal0`` service account already owns. Keeping those OUT of the root
# helper is deliberate: running them as root would silently re-own hal0's own
# config and SQLite files and break the next unprivileged write.
#
# They are plain module-level functions (not Updater methods) because they run
# in TWO processes: in-process on a dev/CI/root install, and inside
# ``python -m hal0.updater.privileged`` as root when hal0-api routes through the
# ``hal0-update`` sudo seam. :class:`hal0.updater.privileged.UpdateSeam` picks.


async def stage_release(
    channel: str,
    version: str | None = None,
    *,
    job_id: str | None = None,
) -> dict[str, Any]:
    """§9 steps 1-6: fetch + authenticate + download + verify + extract.

    The whole of this runs on the PRIVILEGED side on a shipped box, deliberately
    including the manifest fetch, the sha256 check and ``cosign verify-blob``.
    Splitting it — letting the unprivileged API download and verify, then asking
    root to install the result — would make the grant a root-code-execution hole:
    a compromised hal0-api would simply skip the verification and hand root an
    attacker-supplied tree. Root re-derives the target version and re-verifies
    the bytes itself, so the only thing the caller controls is *which channel*
    and an optional exact-match version pin.

    Returns ``{version, install_dir, cache_dir, notes}``.

    Raises:
        UpdateUpgradePathUnsupported when this box is below the release's
        declared ``upgrade_from`` floor.
    """
    # Step 1: fetch, validate, and authenticate the manifest itself.
    log.info("updater.prepare_start", job_id=job_id, channel=channel, pinned=version)
    raw, manifest, _ = await _fetch_verified_release_manifest(channel, job_id=job_id)

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
                "channel": channel,
                "requested_version": requested_version,
                "manifest_version": target_version,
            },
        )

    # Step 2b: honour the manifest's declared upgrade floor. Cheap, and it
    # runs before the download so an unsupported box is not made to pull a
    # tarball it will never be allowed to activate.
    _enforce_upgrade_floor(manifest, job_id=job_id)

    # Residual: concurrent prepares for the same version share cache/install
    # paths and are not serialized. A lock redesign is intentionally out of
    # scope; signed immutable same-version assets limit current release risk
    # because both prepares authenticate the same publisher-pinned bytes.

    # Steps 3-6 all happen inside a root-only staging directory (#1738). The
    # artifact is downloaded, digested, cosign-verified and extracted without
    # ever existing at a path a non-root principal can reach — the cache dir
    # under /var/lib/hal0 is service-owned and must never hold bytes root is
    # about to trust. The directory (and the artifact in it) is destroyed on
    # the way out, success or failure.
    cache = _cache_dir(target_version)
    cache.mkdir(parents=True, exist_ok=True)
    install_dir = _versioned_install_dir(target_version)
    with _root_only_staging_dir(target_version, job_id=job_id) as staging:
        # Step 3: download tarball + Sigstore bundle (survives cert expiry, #1159).
        tarball_path = staging / f"hal0-{target_version}.tar.gz"
        bundle_path = staging / f"hal0-{target_version}.tar.gz.bundle"
        log.info("updater.download_start", job_id=job_id, version=target_version, url=manifest.url)
        await _download(manifest.url, tarball_path)
        await _download(manifest.bundle_url, bundle_path)
        log.info(
            "updater.download_ok",
            job_id=job_id,
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
        log.info("updater.sha256_ok", job_id=job_id, digest=got_digest)

        # Step 5: verify against the identity derived from authenticated release
        # policy, never a manifest-selected broader expression.
        expected_identity = exact_manifest_identity(manifest.release_kind, manifest.version)
        await asyncio.to_thread(
            _verify_cosign,
            tarball_path,
            bundle_path,
            identity_regexp=expected_identity,
            issuer=_MANIFEST_SIGNER_ISSUER,
            job_id=job_id,
        )

        # Step 6: extract. The authenticated digest is re-derived from the very
        # file object the tarball is read out of, so the bytes cosign accepted
        # in step 5 are provably the bytes that land on disk. `_extract_tarball`
        # also quarantines a prior hal0 extraction at the same path (see its
        # docstring) so a retry after a half-failed apply isn't permanently
        # wedged.
        await asyncio.to_thread(
            _extract_tarball,
            tarball_path,
            install_dir,
            job_id=job_id,
            expected_digest=manifest.digest_sha256,
        )

    # Cache the verified manifest so commit() reads min_data_version without a
    # re-fetch (which could resolve a *newer* release than the one just
    # verified), then read release notes from the *verified* tree — so what an
    # operator reviews before commit is exactly what cosign signed.
    _write_atomic_text(_manifest_cache_path(target_version), json.dumps(raw))
    notes = _read_release_notes(install_dir)
    # The cache tree lives under the service-owned /var/lib/hal0. When this ran
    # as root (the seam path) the freshly-created files are root-owned, which
    # would break the next unprivileged retry — hand them back to the service
    # account. Best-effort: a dev/CI box has no hal0 user and needs no chown.
    await asyncio.to_thread(_restore_service_ownership, cache, job_id=job_id)
    log.info("updater.prepare_ok", job_id=job_id, version=target_version)
    return {
        "version": target_version,
        "install_dir": str(install_dir),
        "cache_dir": str(cache),
        "notes": notes,
    }


def _restore_service_ownership(root: Path, *, job_id: str | None = None) -> None:
    """chown ``root`` (recursively) back to the ``hal0`` service account.

    Only meaningful when we are euid 0 AND the ``hal0`` user exists — i.e. the
    real seam path on a shipped box. Everywhere else this is a no-op, so dev,
    CI and ``HAL0_HOME`` runs are untouched. Failures are logged, never raised:
    a cache dir with the wrong owner is a retry annoyance, not a failed update.
    """
    if os.geteuid() != 0:
        return
    try:
        import pwd

        entry = pwd.getpwnam("hal0")
    except (ImportError, KeyError):
        return
    # #2975: the cache sits in hal0-owned /var/lib/hal0. A pathname chown of
    # every rglob() hit followed any symlink the service account planted
    # there and handed its target (say /etc/shadow) to hal0.
    from hal0.security import nofollow

    try:
        nofollow.chown_tree(root, entry.pw_uid, entry.pw_gid)
    except OSError as exc:
        log.warning("updater.cache_chown_failed", job_id=job_id, path=str(root), error=str(exc))


#: Where the enabled unit lives. A module-level constant (not inlined in the
#: function) so tests can redirect it the same way ``seam_dirs`` redirects
#: :data:`hal0.system.seam_check.SEAM_BIN_DIR` — the real path needs root to
#: write and must never be touched by a unit test.
GPU_PERMS_UNIT_DST = Path("/etc/systemd/system/hal0-gpu-perms.service")

#: The FHS default venv path baked into the bundled unit (matches
#: ``installer/systemd/hal0-gpu-perms.service``'s ``ExecStart`` and
#: ``install.sh``'s own ``VENV_DIR`` default — installer/install.sh:184).
_DEFAULT_VENV_DIR = "/usr/lib/hal0/venv"


def refresh_gpu_perms_unit(target: Path, *, job_id: str | None = None) -> str:
    """Install/refresh ``hal0-gpu-perms.service`` from the activated release (#1953).

    Same #1689 shape as :func:`refresh_privileged_wrappers`: ``install.sh`` was
    about to become the only installer of this unit, so a box that only ever
    runs ``hal0 update`` would never receive it and its ``/dev/kfd`` group would
    silently revert on the next reboot with nothing to re-apply it.

    Privileged-side ONLY — a no-op unless ``os.geteuid() == 0``, matching
    :func:`refresh_privileged_wrappers`. The unprivileged daemon must never
    write ``/etc/systemd/system``.

    Best-effort: a release tree without the unit source is skipped, and a
    failed ``systemctl enable`` is logged rather than raised. A missing
    permissions tidy-up must never fail an otherwise-successful activate.

    #1982: a non-default ``HAL0_PREFIX`` moves the venv, and the bundled unit
    hardcodes the FHS ``ExecStart`` interpreter path — copying it verbatim
    clobbers the rewrite ``install.sh`` performs at install time
    (installer/install.sh:1630-1645) and the unit dies ``203/EXEC`` on the
    next activation. This process is itself running from the ACTUAL venv
    (``sys.prefix`` — the same signal :func:`_is_editable_install` already
    trusts for "where does this venv live"), so the same string rewrite
    ``install.sh`` does is applied here before the unit is written, keeping
    every subsequent update self-healing instead of re-breaking the box.

    Returns one of ``"installed"``, ``"unchanged"``, ``"skipped"``, ``"failed"``.
    """
    if os.geteuid() != 0:
        return "skipped"
    src = target / "installer" / "systemd" / "hal0-gpu-perms.service"
    if not src.is_file():
        log.info("updater.gpu_perms_unit_absent", job_id=job_id, src=str(src))
        return "skipped"
    dst = GPU_PERMS_UNIT_DST
    try:
        desired = src.read_text(encoding="utf-8")
        venv_dir = sys.prefix
        if venv_dir != _DEFAULT_VENV_DIR:
            desired = desired.replace(_DEFAULT_VENV_DIR, venv_dir)
        # NOT an early return on identical content: enable/start still re-run
        # below so a previously-failed enable self-heals on the next activate.
        up_to_date = dst.is_file() and dst.read_text(encoding="utf-8") == desired
        if not up_to_date:
            dst.write_text(desired, encoding="utf-8")
            os.chmod(dst, 0o644)
        subprocess.run(["systemctl", "daemon-reload"], check=False, capture_output=True)
        # Enable is CHECKED. Treating the file write alone as success let a
        # transient systemd failure latch permanently: the next update saw
        # matching content, returned "unchanged" early, and never retried the
        # enable — so the box silently lost the boot-time convergence this
        # delivery path exists to provide.
        enabled = subprocess.run(
            ["systemctl", "enable", "hal0-gpu-perms.service"],
            check=False,
            capture_output=True,
        )
        if enabled.returncode != 0:
            log.warning(
                "updater.gpu_perms_unit_enable_failed",
                job_id=job_id,
                returncode=enabled.returncode,
                stderr=(enabled.stderr or b"").decode(errors="replace").strip()[:400],
            )
            return "failed"
        # Converge NOW, not just at next boot. This call site runs at euid 0
        # during activate — unlike the migration chain, which runs as `hal0`
        # and before activation — so an updated box does not have to reboot
        # before its compute node becomes usable by the service user.
        started = subprocess.run(
            ["systemctl", "start", "hal0-gpu-perms.service"],
            check=False,
            capture_output=True,
        )
        if started.returncode != 0:
            log.warning(
                "updater.gpu_perms_unit_start_failed",
                job_id=job_id,
                returncode=started.returncode,
                detail="unit installed and enabled; convergence deferred to next boot",
            )
    except OSError as exc:
        log.warning("updater.gpu_perms_unit_failed", job_id=job_id, error=str(exc))
        return "failed"
    log.info("updater.gpu_perms_unit_installed", job_id=job_id, path=str(dst))
    return "unchanged" if up_to_date else "installed"


#: Where systemd unit files live; a module constant so tests redirect it.
SYSTEMD_UNIT_DIR = Path("/etc/systemd/system")

#: ``/etc/sudoers.d`` grants hal0 once installed and no longer ships. Every
#: grant hal0 ever wrote came from ``packaging/sudoers/``; these are the two
#: that left it. hal0-slotctl (v0.8.0-beta.1 to beta.3) let the hal0 user write
#: unit text into a root-owned slot unit, which is root. hal0-comfyui let it
#: restart ``hal0-slot@img.service`` and was dropped in #984.
_RETIRED_SUDOERS: tuple[str, ...] = ("hal0-comfyui", "hal0-slotctl")

#: ``/usr/lib/hal0/bin`` wrappers hal0 no longer ships.
_RETIRED_WRAPPERS: tuple[str, ...] = ("hal0-slotctl",)


def remove_retired_artifacts(*, job_id: str | None = None) -> list[str]:
    """Delete root-owned files older releases installed and nothing replaces.

    The one post-activation sweep for retired system artefacts. It runs as root
    from :func:`activate_release` on every update, and ``install.sh`` calls it
    too, so a box that has only ever been updated converges the same way as one
    that re-ran the installer. Before this, ``install.sh`` was the only place
    that removed them, so an update-only box kept them.

    * The ``hal0-slotctl`` wrapper in ``/usr/lib/hal0/bin`` and the
      ``hal0-slotctl`` and ``hal0-comfyui`` grants in ``/etc/sudoers.d``.
    * The pre-quadlet static ``hal0-slot@.service`` template and any
      ``hal0-slot@*.service.d`` drop-ins. A static unit in
      ``/etc/systemd/system`` shadows the quadlet generator's unit of the same
      name, which breaks every slot.

    Privileged-side ONLY, like :func:`refresh_privileged_wrappers`: a no-op
    unless ``os.geteuid() == 0``. Best-effort: a path that cannot be removed is
    logged and skipped, never raised.

    Returns the paths removed.
    """
    if os.geteuid() != 0:
        return []

    from hal0.system.seam_check import SEAM_BIN_DIR, SUDOERS_DIR

    candidates: list[Path] = [SEAM_BIN_DIR / name for name in _RETIRED_WRAPPERS]
    candidates += [SUDOERS_DIR / name for name in _RETIRED_SUDOERS]
    units = [SYSTEMD_UNIT_DIR / "hal0-slot@.service"]
    units += sorted(SYSTEMD_UNIT_DIR.glob("hal0-slot@*.service.d"))
    candidates += units

    removed: list[str] = []
    for path in candidates:
        try:
            if path.is_dir() and not path.is_symlink():
                shutil.rmtree(path)
            elif path.exists() or path.is_symlink():
                path.unlink()
            else:
                continue
        except OSError as exc:
            log.warning(
                "updater.retired_artifact_remove_failed",
                job_id=job_id,
                path=str(path),
                error=str(exc),
            )
            continue
        removed.append(str(path))

    if any(str(u) in removed for u in units):
        subprocess.run(["systemctl", "daemon-reload"], check=False, capture_output=True)
    if removed:
        log.warning("updater.retired_artifacts_removed", job_id=job_id, paths=removed)
    return removed


def refresh_privileged_wrappers(target: Path, *, job_id: str | None = None) -> dict[str, Any]:
    """Re-install the privileged sudo wrappers from the just-activated release (#1689).

    ``install.sh`` was the ONLY installer of ``${LIB_DIR}/bin/hal0-*`` — self-
    update never touched them, so a box upgraded through ``hal0 update`` kept
    running every NEW hal0 release against the OLD wrapper. Any seam verb
    added after the box's last ``install.sh`` run (``stop-agent``/#453, the
    ``svc-<verb>`` companion-service family/#1590, ``write-hindsight-
    dropin``/#1641) was rejected by the stale wrapper with
    ``hal0-systemctl: bad cmd: <verb>`` / exit 64 on any box that only ever
    updated, never re-ran the installer.

    Privileged-side ONLY: a no-op unless ``os.geteuid() == 0`` — the same
    guard :func:`_restore_service_ownership` uses just above. The
    unprivileged daemon (``hal0-api`` running ``User=hal0``) must never write
    ``${LIB_DIR}/bin`` or ``/etc/sudoers.d`` itself; it only ever reaches this
    function by way of the root-side seam
    (:func:`hal0.updater.privileged.main`'s ``activate`` verb, run under
    ``sudo -n hal0-update activate``), which is what makes the euid-0 check
    sufficient rather than a bare "are we routed" flag — an operator's direct
    ``sudo hal0 update`` and a root dev/CI run reach the SAME euid-0 branch,
    with no seam in between, and refresh correctly too.

    Trust: by the time :func:`activate_release` calls this, ``target`` has
    already passed ``assert_trusted_release_dir`` (root-owned) and cosign
    verification, and ``pip install`` has already executed that tree's build
    backend as root — copying its ``installer/wrappers/*`` here is strictly
    LESS privileged than that, so this adds no new trust assumption.

    Best-effort, per seam: :data:`hal0.system.seam_check.SEAMS` is the
    canonical wrapper inventory (kept in lock-step with ``install.sh``'s
    per-seam blocks — #1465's lesson). A release tree missing an optional
    wrapper source is skipped, not fatal: a stale wrapper is a correctness
    gap the seam already surfaces loudly (#1682's remediation line) or that
    ``hal0 doctor`` catches, never a reason to fail an otherwise-successful
    activate.

    The matching ``/etc/sudoers.d/<seam>`` drop-in is reinstalled ONLY when
    its content actually changed, and ONLY after an independent
    ``visudo -cf`` pass on the release tree's copy — a malformed drop-in must
    never reach ``/etc/sudoers.d``, corrupted release tree or not.
    """
    if os.geteuid() != 0:
        return {"refreshed": [], "sudoers_refreshed": [], "errors": {}}

    from hal0.system.seam_check import SEAM_BIN_DIR, SEAMS, SUDOERS_DIR

    refreshed: list[str] = []
    sudoers_refreshed: list[str] = []
    errors: dict[str, str] = {}

    for seam in SEAMS:
        src = target / "installer" / "wrappers" / seam.name
        if src.is_file():
            dest = SEAM_BIN_DIR / seam.name
            try:
                SEAM_BIN_DIR.mkdir(parents=True, exist_ok=True)
                subprocess.run(  # nosec B603 — fixed argv, no caller input
                    ["install", "-m", "0755", "-o", "root", "-g", "root", str(src), str(dest)],
                    check=True,
                    capture_output=True,
                    text=True,
                    timeout=30,
                )
                refreshed.append(seam.name)
            except subprocess.CalledProcessError as exc:
                msg = (exc.stderr or str(exc)).strip()[:300]
                errors[seam.name] = msg
                log.warning(
                    "updater.wrapper_refresh_failed", job_id=job_id, seam=seam.name, error=msg
                )
            except (OSError, subprocess.SubprocessError) as exc:
                errors[seam.name] = str(exc)
                log.warning(
                    "updater.wrapper_refresh_failed",
                    job_id=job_id,
                    seam=seam.name,
                    error=str(exc),
                )

        sudoers_src = target / "packaging" / "sudoers" / seam.name
        if not sudoers_src.is_file():
            continue
        sudoers_dest = SUDOERS_DIR / seam.name
        try:
            new_content = sudoers_src.read_text(encoding="utf-8")
            old_content = (
                sudoers_dest.read_text(encoding="utf-8") if sudoers_dest.exists() else None
            )
            if new_content == old_content:
                continue
            check = subprocess.run(  # nosec B603 — fixed argv, no caller input
                ["visudo", "-cf", str(sudoers_src)],
                capture_output=True,
                text=True,
                timeout=10,
            )
            if check.returncode != 0:
                log.warning(
                    "updater.sudoers_refresh_rejected",
                    job_id=job_id,
                    seam=seam.name,
                    stderr=(check.stderr or "").strip()[:300],
                )
                continue
            SUDOERS_DIR.mkdir(parents=True, exist_ok=True)
            subprocess.run(  # nosec B603 — fixed argv, no caller input
                [
                    "install",
                    "-m",
                    "0440",
                    "-o",
                    "root",
                    "-g",
                    "root",
                    str(sudoers_src),
                    str(sudoers_dest),
                ],
                check=True,
                capture_output=True,
                text=True,
                timeout=30,
            )
            sudoers_refreshed.append(seam.name)
        except (OSError, subprocess.SubprocessError) as exc:
            msg = (
                (exc.stderr or str(exc)).strip()[:300]
                if isinstance(exc, subprocess.CalledProcessError)
                else str(exc)
            )
            log.warning("updater.sudoers_refresh_failed", job_id=job_id, seam=seam.name, error=msg)

    log.info(
        "updater.wrappers_refreshed",
        job_id=job_id,
        refreshed=refreshed,
        sudoers_refreshed=sudoers_refreshed,
        errors=errors or None,
    )
    return {"refreshed": refreshed, "sudoers_refreshed": sudoers_refreshed, "errors": errors}


#: Where ``install.sh`` symlinks the CLI onto PATH (installer/install.sh:1078,
#: env-overridable there via ``HAL0_PATH_LINK``). A module constant for the
#: same testability reason as :data:`GPU_PERMS_UNIT_DST` — tests redirect it
#: rather than touching the real ``/usr/local/bin``.
HAL0_PATH_LINK_DST = Path(os.environ.get("HAL0_PATH_LINK", "/usr/local/bin/hal0"))


def refresh_path_links(target: Path, *, job_id: str | None = None) -> dict[str, str]:
    """Re-assert the ``/usr/local/bin/hal0`` (+ ``hal0-agent``) PATH links (#1844/#2019).

    Same #1689 shape as :func:`refresh_privileged_wrappers`: ``install.sh`` was
    the ONLY writer of these symlinks (installer/install.sh:1077-1099) —
    ``hal0 update`` never refreshed them, so a box upgraded exclusively
    through the updater kept ``/usr/local/bin/hal0`` pointed at whatever venv
    shim existed at the LAST ``install.sh`` run. A rebuilt or relocated venv
    (or a `scripts/deploy.sh` dev-deploy that reprovisions one, #2019) then
    left the PATH entry stale or dangling with nothing to re-assert it.

    Privileged-side ONLY, same guard as its siblings: a no-op unless
    ``os.geteuid() == 0`` — the unprivileged daemon must never write
    ``/usr/local/bin``.

    ``target`` names the release tree only for log context; the link SOURCE
    is always THIS process's actual venv (``sys.prefix`` — the same signal
    :func:`refresh_gpu_perms_unit` trusts), because the CLI shim lives in the
    venv's ``bin/``, not the release tree, and by the time this runs
    :func:`activate_release` has already re-pip'd ``target`` into that venv
    (a no-op re-pip for an editable install).

    Best-effort per link, using the same atomic swap :func:`activate_release`
    uses for the release symlink itself (:func:`_atomic_symlink_swap`) so a
    concurrent ``hal0`` invocation never observes a half-written link. A
    missing ``hal0-agent`` shim (older release, or a venv still provisioning)
    is skipped, not fatal — the same "never wedge an otherwise-successful
    activate" policy as :func:`refresh_privileged_wrappers`.

    Returns ``{"hal0": "linked"|"missing"|"failed", "hal0-agent": ...}``
    (empty dict when not root).
    """
    if os.geteuid() != 0:
        return {}

    venv_bin = Path(sys.prefix) / "bin"
    results: dict[str, str] = {}
    for src_name, dst in (
        ("hal0", HAL0_PATH_LINK_DST),
        ("hal0-agent", HAL0_PATH_LINK_DST.with_name("hal0-agent")),
    ):
        src = venv_bin / src_name
        if not src.is_file():
            results[src_name] = "missing"
            log.warning(
                "updater.path_link_source_missing", job_id=job_id, name=src_name, src=str(src)
            )
            continue
        try:
            _atomic_symlink_swap(src, dst)
            results[src_name] = "linked"
        except OSError as exc:
            results[src_name] = "failed"
            log.warning(
                "updater.path_link_refresh_failed", job_id=job_id, name=src_name, error=str(exc)
            )
    log.info("updater.path_links_refreshed", job_id=job_id, target=str(target), results=results)
    return results


def activate_release(dir_name: str, *, job_id: str | None = None) -> dict[str, Any]:
    """§9 step 8 + 8b: swap ``current`` to ``<usr_lib>/<dir_name>`` and re-pip it.

    THE single activation primitive — :meth:`Updater.commit` and
    :meth:`Updater.rollback` both go through it, so "swap the symlink, refresh
    the venv, and undo the swap if pip fails" has exactly one implementation.
    ``dir_name`` is a bare basename (never a path). The root boundary
    (:func:`hal0.updater.privileged.main`) additionally asserts the named tree
    is root-owned before calling this — see :func:`assert_trusted_release_dir`.

    Returns ``{"previous": <prior target or None>, "target": <activated path>}``.
    """
    try:
        name = assert_release_dir_name(dir_name)
    except ValueError as exc:
        raise UpdateError(
            f"refusing to activate: {exc}",
            details={"dir_name": dir_name, "error": str(exc)},
        ) from exc

    target = _usr_lib_root() / name
    if not target.is_dir() or target.is_symlink():
        raise UpdateError(
            f"nothing to activate at {target} — not a directory",
            details={"dir_name": name, "path": str(target)},
        )
    # NOTE: the "must be root-owned" trust test is NOT here. It belongs to the
    # root boundary — hal0.updater.privileged.main — because that is the only
    # caller acting on behalf of an unprivileged party. An operator running
    # `sudo hal0 update` staged the tree themselves, and a dev/CI/HAL0_HOME run
    # has no privilege boundary to protect at all.

    link = _current_symlink()
    try:
        prior = _atomic_symlink_swap(target, link)
    except OSError as exc:
        raise UpdateSwapError(
            f"atomic symlink swap failed: {exc}",
            details={"link": str(link), "target": str(target), "error": str(exc)},
        ) from exc

    # Re-install the swapped-in code into the running venv. The symlink swap
    # alone changes nothing — the venv imports hal0 from its own site-packages
    # (#495). On failure roll the symlink back so `current` and the installed
    # code never disagree.
    if not _is_editable_install():
        try:
            _reinstall_into_venv(target, job_id=job_id)
        except UpdateError:
            if prior is not None:
                with contextlib.suppress(OSError):
                    _atomic_symlink_swap(prior, link)
            raise

    # #1689: re-install the privileged sudo wrappers from the tree we just
    # activated — best-effort, never fatal to an otherwise-successful
    # activate (a wrapper that fails to refresh keeps the OLD one in place,
    # same class of degradation as before this fix, not a new failure mode).
    wrappers = refresh_privileged_wrappers(target, job_id=job_id)
    path_links = refresh_path_links(target, job_id=job_id)
    gpu_perms_unit = refresh_gpu_perms_unit(target, job_id=job_id)
    retired = remove_retired_artifacts(job_id=job_id)

    log.info(
        "updater.activate_ok",
        job_id=job_id,
        target=str(target),
        previous=str(prior) if prior else None,
    )
    return {
        "previous": str(prior) if prior else None,
        "target": str(target),
        "wrappers_refreshed": wrappers["refreshed"],
        "path_links_refreshed": path_links,
        "gpu_perms_unit": gpu_perms_unit,
        "retired_artifacts_removed": retired,
    }


def discard_release(dir_name: str, *, job_id: str | None = None) -> None:
    """Remove a staged ``<usr_lib>/<dir_name>`` tree (rm -rf semantics).

    The failure-cleanup primitive: ``commit`` calls it when a config migration
    aborts the update so a half-installed tree is never left behind. Idempotent
    — a missing tree is success, matching the ``shutil.rmtree`` + suppress this
    replaced.
    """
    try:
        name = assert_release_dir_name(dir_name)
    except ValueError as exc:
        raise UpdateError(
            f"refusing to discard: {exc}",
            details={"dir_name": dir_name, "error": str(exc)},
        ) from exc
    target = _usr_lib_root() / name
    if target.is_symlink() or not target.exists():
        return
    with contextlib.suppress(OSError):
        shutil.rmtree(target)
    log.info("updater.discard_ok", job_id=job_id, target=str(target))


async def _rerender_units_after_swap(job_id: str | None) -> None:
    """Re-render every slot unit through a FRESH interpreter of the
    just-activated code, so any subsequent start (``systemctl restart``,
    crash-restart, reboot) uses that version's argv — never bounces
    serving (write + daemon-reload only).

    Shared by :meth:`Updater.commit` (step 8c) and :meth:`Updater.rollback`
    (GH #1475: rollback used to stop after the symlink swap + venv re-pip,
    leaving units carrying argv rendered by the version just rolled AWAY
    from). Must run in a subprocess, not this process: the caller's own
    interpreter is still running the PRE-swap code (Python doesn't hot-swap
    already-imported modules), so calling :func:`rerender_slot_units`
    in-process here would render outgoing argv, defeating the point — a
    subprocess of the just-repipped venv executes the NEW module. Non-fatal
    by design: a failure here leaves stale units with the old flags until
    the next hal0-level slot restart; the dashboard drift indicator
    surfaces them either way.
    """
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
                job_id=job_id,
                rewritten=(proc.stdout or "").strip(),
            )
        else:
            log.warning(
                "updater.unit_rerender_failed",
                job_id=job_id,
                rc=proc.returncode,
                stderr=(proc.stderr or "")[-500:],
            )
    except Exception as exc:
        log.warning(
            "updater.unit_rerender_failed",
            job_id=job_id,
            error=str(exc),
        )


#: Sentinel key on the single JSON stdout line the migration subprocess
#: emits. Mirrors :data:`hal0.updater.privileged._RESULT_KEY`'s exact
#: contract: structured logs go to STDERR (see the script built below) so
#: stdout carries nothing but this envelope, scanned from the end.
_MIGRATION_RESULT_KEY = "hal0_migration_result"

#: The non-fatal per-pass failure events ``run_post_activation_migrations``
#: can log without raising (#1960 B2). A pass that swallows its own
#: exception still exits the subprocess with rc=0 — by design, see that
#: function's docstring — so it would otherwise be invisible outside
#: journalctl. ``_run_post_activation_migrations_after_swap`` scans the
#: relayed child log for these exact event names so a swallowed failure
#: still surfaces in commit()'s convergence report.
_NON_FATAL_MIGRATION_FAILURE_EVENTS: tuple[str, ...] = (
    "updater.registry_toml_retire_failed",
    "updater.vulkan_migration_failed",
    "updater.image_keys_strip_failed",
    "updater.image_keys_write_failed",
    "updater.image_keys_slot_unreadable",
    "updater.retired_skills_prune_failed",
    "updater.memory_engine_upgrade_failed",
    "updater.components_converge_failed",
    "updater.kokoro_bundle_pull_failed",
    "components.hermes_gateway_regen_failed",
)


async def _run_post_activation_migrations_after_swap(
    min_data_version: int,
    *,
    job_id: str | None,
) -> dict[str, Any]:
    """Run :func:`run_post_activation_migrations` in a subprocess of the
    just-repipped venv, AFTER the ``current`` symlink swap (#1960).

    Mirrors :func:`_rerender_units_after_swap`'s exact reasoning: this
    process is still running the PRE-swap code — Python does not hot-swap
    already-imported modules — so calling ``run_post_activation_migrations``
    in-process at this point would run the OUTGOING version's migration set,
    never the incoming release's. That was the bug: a migration shipped in
    release N+1 never ran on the N → N+1 upgrade that shipped it, because the
    old call site (this function's replacement) ran BEFORE the swap. A
    subprocess of the just-repipped venv imports the NEW module instead.

    The child script is an ABI between this (outgoing) release and the
    incoming one: it calls ``run_post_activation_migrations(min_data_version,
    job_id=...)``, so every later release must keep that call shape.

    The child's structured log is pinned to stderr (mirrors
    :func:`hal0.updater.privileged._pin_logs_to_stderr` exactly) so stdout
    carries only the JSON result envelope; the parent relays that stderr
    into its own journal via :func:`hal0.updater.privileged._relay_child_log`
    (#1960 B2) — without this, every breadcrumb the six passes emit
    (``updater.slot_vulkan_relabeled_rocm``, ``migrations_applied``, …) dies
    in ``capture_output`` and #1935's journal-breadcrumb contract silently
    regresses for this one call site (the release-validation kit's
    post-upgrade check greps the journal for exactly these lines). The
    relayed text is also scanned for :data:`_NON_FATAL_MIGRATION_FAILURE_EVENTS`
    so a pass that swallowed its own exception into a warning (rc=0, by
    design) still surfaces in the returned ``pass_warnings`` list.

    Unlike ``_rerender_units_after_swap``, this raises :class:`UpdateError`
    on any hard failure — subprocess launch failure (``OSError``), a
    timeout, a non-zero exit, or a clean exit with no parsable result are
    ALL converted to the same typed error, so :meth:`Updater.commit`'s
    non-fatal-but-loud containment holds for every failure shape the
    subprocess can produce, not just a non-zero return code.

    Returns ``{"from": int, "to": int, "pass_warnings": list[str],
    "conversion_blockers": list[dict], "unassigned_models": list[dict],
    "model_bundles": list[dict]}``
    (each list is empty when the incoming release predates it).
    """
    payload = json.dumps(
        {
            "min_data_version": min_data_version,
            "job_id": job_id,
        }
    )
    envelope_line = (
        "sys.stdout.write(json.dumps({"
        + repr(_MIGRATION_RESULT_KEY)
        + ": {'from': result[0], 'to': result[1],"
        " 'conversion_blockers': list(getattr(result, 'conversion_blockers', None) or []),"
        " 'unassigned_models': list(getattr(result, 'unassigned_models', None) or []),"
        " 'model_bundles': list(getattr(result, 'model_bundles', None) or [])}})"
        " + chr(10))\n"
    )
    script = (
        "import json, sys\n"
        "import structlog\n"
        # Mirrors hal0.updater.privileged._pin_logs_to_stderr exactly: send
        # structured logs to stderr so stdout carries only the result line.
        "structlog.configure(logger_factory=structlog.PrintLoggerFactory(file=sys.stderr))\n"
        "from hal0.updater.updater import run_post_activation_migrations\n"
        "args = json.loads(sys.argv[1])\n"
        "try:\n"
        "    result = run_post_activation_migrations(args['min_data_version'], job_id=args['job_id'])\n"
        "except Exception as exc:\n"
        "    print('hal0-migrate: ' + str(exc), file=sys.stderr)\n"
        "    raise SystemExit(1)\n" + envelope_line
    )
    try:
        proc = await asyncio.to_thread(
            subprocess.run,
            [sys.executable, "-c", script, payload],
            capture_output=True,
            text=True,
            # 2700s, not the old 300s: the memory-engine convergence pass may
            # pip-build the hindsight venv (transformers/sentence-transformers
            # wheel churn — gigabytes). Its own pip runs are capped at 2400s
            # (hal0.memory.engine_upgrade._PIP_TIMEOUT_S) so this envelope
            # always has headroom to emit the JSON result; fast passes still
            # finish fast — this is a runaway cap, not a wait.
            timeout=2700,
        )
    except subprocess.TimeoutExpired as exc:
        # Same containment class as _rerender_units_after_swap and the
        # privileged seam's own _invoke — subprocess.run itself can raise
        # rather than return a CompletedProcess, and the old code only
        # handled the "returns a non-zero CompletedProcess" shape (#1960 B1).
        raise UpdateError(
            "post-activation migrations timed out after 2700s in the newly activated tree",
            details={"job_id": job_id, "timeout": 2700},
        ) from exc
    except OSError as exc:
        raise UpdateError(
            f"post-activation migrations subprocess could not be launched: {exc}",
            details={"job_id": job_id},
        ) from exc

    stderr = proc.stderr or ""
    if proc.returncode == 0:
        from hal0.updater.privileged import _relay_child_log

        # Relay only on success, mirroring UpdateSeam._invoke exactly: a
        # failure's stderr goes into the raised error's `details` below
        # instead (a diagnostic breadcrumb, not the audit trail this
        # replay closes).
        _relay_child_log(stderr, verb="post_swap_migrations", job_id=job_id)
        pass_warnings = [event for event in _NON_FATAL_MIGRATION_FAILURE_EVENTS if event in stderr]
        # Scan stdout from the END, exactly like privileged._parse_result —
        # an unexpected banner on stdout cannot displace the answer.
        for line in reversed((proc.stdout or "").splitlines()):
            line = line.strip()
            if not line.startswith("{"):
                continue
            try:
                envelope = json.loads(line)
            except ValueError:
                continue
            if not isinstance(envelope, dict) or _MIGRATION_RESULT_KEY not in envelope:
                continue
            result = envelope[_MIGRATION_RESULT_KEY]
            if isinstance(result, dict) and "from" in result and "to" in result:
                return {
                    "from": int(result["from"]),
                    "to": int(result["to"]),
                    "pass_warnings": pass_warnings,
                    "conversion_blockers": list(result.get("conversion_blockers") or []),
                    "unassigned_models": list(result.get("unassigned_models") or []),
                    "model_bundles": list(result.get("model_bundles") or []),
                }
        raise UpdateError(
            "post-activation migrations subprocess exited 0 but produced no result marker",
            details={"job_id": job_id, "stdout": (proc.stdout or "")[-2000:]},
        )
    # Last non-empty line is almost always the exception's own message (a
    # traceback's final line, or the "hal0-migrate: ..." line the child
    # prints itself) — surfaced in the message itself (not just `details`)
    # so it reaches the operator-facing convergence report in
    # cli/update_commands.py, not only the journal.
    reason = next((line.strip() for line in reversed(stderr.splitlines()) if line.strip()), "")
    raise UpdateError(
        f"post-activation migrations failed in the newly activated tree (rc={proc.returncode})"
        + (f": {reason}" if reason else ""),
        details={
            "job_id": job_id,
            "returncode": proc.returncode,
            "stderr": stderr[-2000:],
        },
    )


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

    def _seam(self) -> Any:
        """Return the privileged-update seam (#1464).

        Imported lazily: :mod:`hal0.updater.privileged` imports *this* module at
        the top level (it wraps the primitives above), so a top-level import
        here would be circular. The seam is constructed per call — it is
        stateless and default-constructed = production behaviour, exactly like
        :class:`hal0.system.seam.SystemCtlSeam`.
        """
        from hal0.updater.privileged import UpdateSeam

        return UpdateSeam(job_id=self.job_id)

    # ── check ──────────────────────────────────────────────────────────────────

    async def check(self, channel: str | None = None) -> ReleaseInfo:
        """Check for a newer version on the configured release channel.

        Fetches the release manifest, validates it against the
        ``ReleaseManifest`` schema, and compares against
        :func:`installed_version` (``current/VERSION``-first — see its
        docstring for the nightly wheel-metadata story).

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
        current_version = installed_version()
        update_available = bool(latest) and not revoked and _is_newer(latest, current_version)
        if revoked and bool(latest):
            log.warning(
                "updater.latest_revoked",
                version=latest,
                channel=ch,
                reason=revoked_reason,
            )
        return ReleaseInfo(
            current=current_version,
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
            releases_url_override=releases_url_override(),
        )

    # ── apply ──────────────────────────────────────────────────────────────────

    async def prepare(self, version: str | None = None) -> dict[str, Any]:
        """Download, verify, and STAGE ``version`` (or latest) — without activating.

        Runs §9 steps **1-6** only:

          1. Fetch + schema-validate the release manifest.
          2. Confirm the target version (caller-pinned or manifest.version).
          3. Download tarball + signature into a root-only staging dir under
             the install root (#1738 — never the hal0-writable cache).
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
            UpdateUpgradePathUnsupported when this box is below the release's
            declared ``upgrade_from`` floor.
        """
        # Guard: hard-refuse on editable/dev installs.  apply() manipulates
        # the FHS layout (/usr/lib/hal0/current symlink + venv site-packages)
        # which does not exist in an editable checkout.  Continuing would
        # silently extract a tarball that is never actually loaded.
        _raise_if_editable_install()
        # #1464: prove we can reach the root-only tree (directly or through the
        # hal0-update seam) BEFORE burning a download + sha256 + cosign pass.
        seam = self._seam()
        seam.assert_privileges()
        return await seam.stage(self.channel, version)

    async def commit(self, version: str) -> dict[str, Any]:
        """Activate a previously :meth:`prepare`d ``version`` (§9 steps 7-9+).

        Atomic-swaps the ``current`` symlink, re-pips the tree into the running
        venv, runs the post-activation migrations under the new code, and
        re-renders slot units. Requires that
        :meth:`prepare` already staged this version (its ``install_dir`` and
        cached manifest must exist) — otherwise raises ``UpdateError``.

        Slot units are NOT restarted and ``hal0-api`` is not bounced here; the
        route layer (``routes/updater._run_commit_job``) try-restarts hal0-api
        fail-soft after a successful commit. Returns the same breadcrumb dict
        shape as the old single-step apply, plus ``convergence`` (what the
        post-activation conversions changed).

        Args:
            version: the version ``prepare()`` staged.
        """
        _raise_if_editable_install()
        seam = self._seam()
        seam.assert_privileges()
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

        # Step 7's CALL runs AFTER seam.activate() (#1960) — see
        # _run_post_activation_migrations_after_swap for why an in-process call
        # here executed the OUTGOING version's migrations, never the incoming
        # release's.

        # Step 8 + 8b: atomic symlink swap + venv re-pip, as ONE privileged
        # operation (`activate_release`). The venv imports hal0 from its own
        # site-packages, so a symlink swap alone would NOT change the running
        # version (#495); activate rolls the symlink back itself if the re-pip
        # fails, so `current` and the installed code never disagree.
        link = _current_symlink()
        try:
            activated = await seam.activate(release_dir_name(target_version))
        except UpdateSwapError:
            # Roll back the extracted tree so /usr/lib stays clean.
            with contextlib.suppress(Exception):
                seam.discard(release_dir_name(target_version))
            raise
        prior_str = activated.get("previous")
        prior = Path(prior_str) if prior_str else None

        # Step 7 (moved here, #1960): post-activation migrations now run in a
        # subprocess of the just-repipped venv, AFTER the swap above — see
        # _run_post_activation_migrations_after_swap. Must run BEFORE the
        # unit re-render below so slot units render from already-migrated
        # config (e.g. a relabelled device, sanitised extra_args). commit()
        # already hard-refuses editable installs at its own top
        # (_raise_if_editable_install), so — unlike _rerender_units_after_swap,
        # which is shared with rollback() and therefore still guards on it —
        # no editable-install branch is needed here.
        migrations_error: str | None = None
        pass_warnings: list[str] = []
        blockers: list[dict[str, str]] = []
        unassigned: list[dict[str, str]] = []
        model_bundles: list[dict[str, str]] = []
        try:
            migration_result = await _run_post_activation_migrations_after_swap(
                manifest.min_data_version,
                job_id=self.job_id,
            )
            migration_info = (migration_result["from"], migration_result["to"])
            pass_warnings = list(migration_result.get("pass_warnings") or [])
            blockers = list(migration_result.get("conversion_blockers") or [])
            unassigned = list(migration_result.get("unassigned_models") or [])
            model_bundles = list(migration_result.get("model_bundles") or [])
        except Exception as exc:
            # LOUD, not silent (#1960's failure-mode decision): the tree is
            # already active by this point — the symlink swap and venv re-pip
            # above already succeeded — so raising here and reporting the
            # whole commit() as "failed" would be misleading; the release IS
            # running. What actually failed is narrower: on-disk data (slot
            # TOMLs, hal0.toml schema, registry rows) may still reflect the
            # OUTGOING release's shape — real, user-visible state divergence,
            # so this logs at ERROR (louder than _rerender_units_after_swap's
            # own WARNING for its lower-stakes non-fatal failure) and is
            # threaded into `convergence` below so `hal0 update` refuses to
            # call the run a clean success (see cli/update_commands.py's
            # _print_convergence). hal0-api's boot-time
            # check_outstanding_migrations retries this automatically at the
            # next restart, so the failure is self-healing, not permanent.
            #
            # Bare `except Exception`, not `except UpdateError` (#1960 B1):
            # the helper converts every failure shape it knows about to
            # UpdateError, but subprocess.run can raise things outside that
            # (a bug in the helper's own conversion, a future refactor that
            # misses a case) — the same containment class
            # _rerender_units_after_swap uses for its own subprocess call.
            # This is the one place in commit() a bare except is correct: by
            # this point the release is already active, so NOTHING may
            # escape and abort the function — every later step (unit
            # re-render, the previous-version breadcrumb, the response
            # itself) still has to run.
            migrations_error = str(exc)
            fallback_version = _raw_schema_version(_raw_hal0_toml())
            migration_info = (fallback_version, fallback_version)
            log.error(
                "updater.post_swap_migration_failed",
                job_id=self.job_id,
                version=target_version,
                error=migrations_error,
            )

        # Step 8c: re-render existing slot units through the NEW code. Must
        # run AFTER the 8b venv reinstall (see _rerender_units_after_swap).
        if not _is_editable_install():
            await _rerender_units_after_swap(self.job_id)

        if prior is not None:
            _write_atomic_text(_previous_record(), str(prior))
        log.info(
            "updater.swap_ok",
            job_id=self.job_id,
            version=target_version,
            link=str(link),
            previous=str(prior) if prior else None,
        )

        # `pass_warnings` (#1960 B2) does NOT factor into `ok`/`converged`:
        # those five passes are independently best-effort BY DESIGN (see
        # run_post_activation_migrations's own docstring — an affected slot
        # or model just keeps its previous state until the operator acts),
        # exactly as true before this PR as after it. What changed is only
        # that a swallowed pass failure used to be invisible outside
        # journalctl; it is now visible here too, without reclassifying a
        # long-standing non-fatal outcome as a hard one.
        convergence = {
            "post_activation_migrations": {
                "ok": migrations_error is None,
                "from": migration_info[0],
                "to": migration_info[1],
                "error": migrations_error,
                "pass_warnings": pass_warnings,
                # ADR-0029: slots/profiles/stacks the profile-centric
                # conversion left for the operator. The release is applied,
                # but the install is not converged until they are fixed.
                "conversion_blockers": blockers,
                # ADR-0028 §7 / #2694: slots the runner-system conversion
                # unassigned a model from. Reported with a remedy, but not a
                # blocker: the conversion did what it must.
                "unassigned_models": unassigned,
                # #2739: engine bundles the upgrade fetched (kokoro-v1), or
                # could not fetch, with the remedy. Not a blocker.
                "model_bundles": model_bundles,
            },
            "converged": migrations_error is None and not blockers,
        }
        log.info(
            "updater.convergence",
            job_id=self.job_id,
            converged=convergence["converged"],
            post_activation_migrations_ok=migrations_error is None,
            post_activation_migrations_pass_warnings=pass_warnings,
            conversion_blockers=[b["target"] for b in blockers],
            unassigned_models=[u.get("slot") for u in unassigned if isinstance(u, dict)],
            model_bundles=[
                f"{b.get('model')}:{b.get('outcome')}" for b in model_bundles if isinstance(b, dict)
            ],
        )

        return {
            "version": target_version,
            "previous": str(prior) if prior else None,
            "install_dir": str(install_dir),
            "cache_dir": str(cache),
            "migrations": {"from": migration_info[0], "to": migration_info[1]},
            "installed_at": time.time(),
            "convergence": convergence,
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

        Step 8c (non-editable installs only, GH #1475): re-renders every
        slot unit through the rolled-back code, mirroring ``commit()``'s own
        step 8c — otherwise the on-disk units keep carrying argv rendered by
        the version this rollback just left.

        Raises:
            UpdateRollbackUnavailable: No previous record on disk.
            UpdateSwapError: The symlink swap itself failed.
            UpdateError: Re-pip of the prior tree failed (non-editable only).
        """
        seam = self._seam()
        seam.assert_privileges()
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

        # The recorded target must be a direct child of the install root — the
        # seam only ever takes a bare `hal0-<version>` basename, never a path
        # (#1464). A breadcrumb pointing anywhere else is not rollable.
        if prior_path.parent != _usr_lib_root():
            raise UpdateRollbackUnavailable(
                f"previous-version record points outside the install root: {prior_path}",
                details={"previous": str(prior_path), "install_root": str(_usr_lib_root())},
            )

        log.info("updater.rollback_start", job_id=self.job_id, previous=str(prior_path))
        # ADR-0029: a release before the profile-centric conversion cannot read
        # the converted config. Undo the conversion first (files, model
        # columns, migration 012's bookkeeping) or refuse with the manual steps.
        profile_restore = _restore_profile_conversion_for(prior_path, job_id=self.job_id)
        # ADR-0045: a release before per-slot [tune] refuses a slot file that
        # carries the table. Strip it (after the profile restore, which may put
        # slot files back) and leave a .bak-<stamp> copy of each file touched.
        tune_backups: dict[str, Path] = {}
        tune_stripped = _strip_slot_tune_for(
            prior_path, job_id=self.job_id, backups_out=tune_backups
        )
        # Steps 8 + 8b via the same privileged primitive commit() uses: swap the
        # symlink back and re-pip the prior tree so the next hal0-api restart
        # actually serves the rolled-back version. On a pip failure activate
        # swaps *forward* again, so `current` and site-packages stay consistent.
        try:
            activated = await seam.activate(prior_path.name)
        except Exception:
            # Still on this release: its slot files get their [tune] back. This
            # comes first, so a re-conversion below sees the files as they were.
            _restore_slot_tune_backups(tune_backups, job_id=self.job_id)
            if profile_restore:
                # Still on this release: convert again so it keeps running.
                from hal0.config.migrations.profile_centric_apply import (
                    migrate_profile_centric,
                )

                with contextlib.suppress(Exception):
                    migrate_profile_centric(job_id=self.job_id)
            raise
        current_str = activated.get("previous")
        current_target = Path(current_str) if current_str else None

        # Step 8c: re-render existing slot units through the rolled-back
        # code, mirroring commit()'s same step (GH #1475) — otherwise the
        # on-disk units keep carrying argv rendered by the version this
        # rollback just left, and the next start hands the rolled-back
        # binary flags it may not accept.
        if not _is_editable_install():
            await _rerender_units_after_swap(self.job_id)

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
            "profile_conversion_restored": profile_restore,
            "slot_tune_stripped": tune_stripped,
        }


#: A release tree that ships the profile-centric conversion (ADR-0029).
_PROFILE_CENTRIC_MARKER = Path("src/hal0/config/migrations/profile_centric_apply.py")


def _restore_profile_conversion_for(prior_path: Path, *, job_id: str | None) -> list[str]:
    """Undo the profile-centric conversion before rolling back to ``prior_path``
    when that release predates it. Returns what was restored (empty when the
    target understands the converted config, or nothing was converted).

    Raises :class:`UpdateRollbackUnavailable` with the manual steps when the
    conversion backups cannot be restored.
    """
    if (prior_path / _PROFILE_CENTRIC_MARKER).is_file():
        return []
    from hal0.config.migrations.profile_centric_apply import (
        ProfileConversionError,
        conversion_backups,
        restore_for_rollback,
    )

    try:
        return restore_for_rollback(job_id=job_id)
    except ProfileConversionError as exc:
        backups = conversion_backups()
        raise UpdateRollbackUnavailable(
            f"cannot roll back to {prior_path.name}: it predates the profile-centric "
            f"config this install was converted to, and the conversion could not be "
            f"undone ({exc.message}). To roll back by hand: `sudo systemctl stop hal0-api`; "
            "for each backup, newest first, run "
            "`sudo -u hal0 hal0 profile migrate --restore <backup>`; "
            "`sudo systemctl start hal0-api`; then run `hal0 update --rollback` again.",
            details={"previous": str(prior_path), "backups": backups, "error": exc.message},
        ) from exc


#: A release tree that understands a slot's ``[tune]`` table (ADR-0045): the
#: resolver module that reads it. Older trees do not ship this file.
_SLOT_TUNE_MARKER = Path("src/hal0/slots/tune.py")


def _restore_slot_tune_backups(backups: dict[str, Path], *, job_id: str | None) -> list[str]:
    """Put each slot file in ``backups`` ({file: its ``.bak-<stamp>``}) back as it was.

    Writes in place (the file keeps its owner and mode) and removes the backup
    once its file is restored; a backup that cannot be restored is kept and
    logged. Returns the files restored. Never raises: it runs on a failure path.
    """
    restored: list[str] = []
    for file, backup in backups.items():
        try:
            Path(file).write_bytes(backup.read_bytes())
            backup.unlink()
        except OSError as exc:
            log.error(
                "updater.slot_tune_restore_failed",
                job_id=job_id,
                path=file,
                backup=str(backup),
                error=str(exc),
            )
            continue
        restored.append(file)
    return restored


def _strip_slot_tune_for(
    prior_path: Path,
    *,
    job_id: str | None,
    backups_out: dict[str, Path] | None = None,
) -> list[str]:
    """Remove ``[tune]`` from every slot file before rolling back to ``prior_path``
    when that release predates per-slot tune overrides.

    A slot file without a ``[tune]`` table is not touched (not even rewritten).
    Each file that has one is first copied to ``<file>.bak-<UTC stamp>``, then
    rewritten without the table. Returns the slot files changed; empty when the
    target understands ``[tune]`` or no slot carries one.

    ``backups_out`` receives ``{file: backup}`` for each file changed, so a
    caller whose later step fails can undo this with
    :func:`_restore_slot_tune_backups`. If a write fails part-way, the files
    already changed are restored before the error propagates.
    """
    if (prior_path / _SLOT_TUNE_MARKER).is_file():
        return []
    import tomllib

    import tomli_w

    slots_dir = paths.slots_config_dir()
    if not slots_dir.is_dir():
        return []
    stamp = time.strftime("%Y%m%dT%H%M%SZ", time.gmtime())
    backups: dict[str, Path] = backups_out if backups_out is not None else {}
    changed: list[str] = []
    try:
        for path in sorted(slots_dir.glob("*.toml")):
            try:
                raw = path.read_bytes()
                data = tomllib.loads(raw.decode("utf-8"))
            except (OSError, UnicodeDecodeError, tomllib.TOMLDecodeError):
                log.warning("updater.slot_tune_strip_unreadable", job_id=job_id, path=str(path))
                continue
            if "tune" not in data:
                continue
            backup = path.with_name(f"{path.name}.bak-{stamp}")
            shutil.copy2(path, backup)
            backups[str(path)] = backup
            data.pop("tune")
            path.write_text(tomli_w.dumps(data), encoding="utf-8")
            changed.append(str(path))
            log.info(
                "updater.slot_tune_stripped", job_id=job_id, path=str(path), backup=str(backup)
            )
    except Exception:
        _restore_slot_tune_backups(backups, job_id=job_id)
        raise
    return changed


__all__ = [
    "ReleaseInfo",
    "ReleaseManifest",
    "UpdateCosignFailed",
    "UpdateCosignMissing",
    "UpdateDownloadError",
    "UpdateError",
    "UpdateExtractError",
    "UpdateManifestInvalid",
    "UpdatePrivilegeError",
    "UpdateRollbackUnavailable",
    "UpdateSwapError",
    "UpdateUpgradePathUnsupported",
    "UpdateVerifyError",
    "Updater",
    "activate_release",
    "assert_release_dir_name",
    "assert_trusted_release_dir",
    "check_outstanding_migrations",
    "discard_release",
    "fetch_release_manifest",
    "refresh_gpu_perms_unit",
    "refresh_path_links",
    "refresh_privileged_wrappers",
    "release_dir_name",
    "releases_url",
    "remove_retired_artifacts",
    "run_post_activation_migrations",
    "stage_release",
    "validate_manifest_for_channel",
]
