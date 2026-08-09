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
from hal0.config.migrations.v2 import PROFILE_CATALOG_SCHEMA_VERSION
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

    Inert unless a manifest actually sets the field — the default is ``""``
    (no floor), which is what every published manifest carries today.
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


# Best-effort prerelease recognizer used ONLY by the packaging-less fallback
# below. ``packaging`` is now a hard runtime dependency (pyproject.toml), so
# this path should only ever fire on a venv that predates that fix — the old
# updater code downloads and runs the *new* tarball's installer, but the
# version *comparison* that decides whether to offer an update at all runs in
# the OLD, already-installed code. A box stuck on this fallback needs one
# manual/self-update to get off it permanently.
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
    """packaging-less sort key: (release_tuple_or_digit_tuple, pre_rank, pre_num).

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
    or when ``packaging`` itself is unavailable (see its docstring for why that
    fallback needs its own prerelease handling, not just a raw tuple compare).
    """
    try:
        from packaging.version import InvalidVersion, Version
    except Exception:  # packaging absent — keep the best-effort key compare
        return _naive_version_key(candidate) > _naive_version_key(current)
    try:
        return Version(candidate) > Version(current)
    except InvalidVersion:
        return _naive_version_key(candidate) > _naive_version_key(current)


def _enforce_upgrade_floor(
    manifest: ReleaseManifest,
    *,
    current: str | None = None,
    job_id: str | None = None,
) -> None:
    """Refuse to stage a release whose ``upgrade_from`` floor this box is below.

    ``upgrade_from`` is a PEP 440 specifier set (e.g. ``">=0.9.8"``). An empty
    value — every published manifest today — means no floor and this is a no-op.

    Fail-open on anything unparseable: a malformed specifier, a non-PEP-440
    installed version, or a missing ``packaging`` must never block an otherwise
    fully-verified update. The floor is a supportability statement, not a
    security control (cosign is the security control), so a broken statement
    degrades to "no statement".

    Raises:
        UpdateUpgradePathUnsupported: the installed version is outside the floor.
    """
    spec = (manifest.upgrade_from or "").strip()
    if not spec:
        return
    installed = current or hal0.__version__
    try:
        from packaging.specifiers import InvalidSpecifier, SpecifierSet
        from packaging.version import InvalidVersion, Version
    except Exception:  # pragma: no cover — packaging is a hard dep in practice
        return
    try:
        allowed = SpecifierSet(spec)
        # prereleases=True: 1.0.0-alpha.2 is a real, supported baseline, and a
        # bare ">=0.9.8" would otherwise exclude every prerelease.
        ok = allowed.contains(Version(installed), prereleases=True)
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
                "re-install with installer/install.sh"
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
    ceiling: int | None = None,
) -> tuple[int, int]:
    """Run forward config migrations if the release demands a newer schema.

    Reads ``hal0.toml``'s ``meta.schema_version``, walks
    ``hal0.config.migrations.run_migrations`` up to
    ``max(min_data_version, latest_version())``, and atomically writes
    the migrated TOML back.

    Args:
        min_data_version: the release manifest's floor.
        job_id: optional breadcrumb for structured-log tracing.
        ceiling: hard cap on the target version for THIS run. A schema version
            is a claim about what has already been converged, and v2 claims the
            one-shot profile-catalog reset has run
            (:mod:`hal0.config.migrations.v2`). When an operator declines that
            reset, stamping v2 anyway would silently consume the one-shot, so
            :meth:`Updater.commit` caps the target below it. Never clamps below
            the on-disk version — that would be a downgrade, which
            ``run_migrations`` rejects outright.

    Returns ``(source_version, target_version)`` for breadcrumb logging.
    Skips entirely when the running schema is already ≥ target.
    """
    target = max(min_data_version or 1, latest_version())
    if ceiling is not None and ceiling < target:
        log.info("updater.migrations_capped", job_id=job_id, uncapped=target, ceiling=ceiling)
        target = ceiling
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

    # ``exclude_none=True`` matches ``save_hal0_config``: None has no TOML
    # representation and ``tomli_w`` raises TypeError on it (#1652).
    # SecurityConfig.require_auth / trust_forwarded_for both default to
    # None, so every config hit this before exclude_none was added here —
    # masked only by version arithmetic (the only migration is v2, and the
    # profile-catalog gate keeps source==target or ceiling==source, so this
    # writer was never actually reached). Pydantic re-supplies the default
    # on load, so dropping None on write is safe for any field whose
    # default is None, same as save_hal0_config.
    raw = cfg.model_dump(mode="python", exclude_none=True)
    new_raw, new_version = run_migrations(raw, target_version=target)
    write_toml_atomic(toml_path, new_raw)
    log.info(
        "updater.migrations_applied",
        job_id=job_id,
        source=source,
        target=new_version,
    )
    return (source, new_version)


def run_post_activation_migrations(
    min_data_version: int = 1,
    *,
    job_id: str | None = None,
    ceiling: int | None = None,
) -> tuple[int, int]:
    """Run every post-activation migration pass hal0 ships, in order.

    The single sequence both :meth:`Updater.commit` (self-update) and
    ``install.sh``'s repair/upgrade-in-place re-run path call, so the two
    upgrade paths converge on the same on-disk state (GH #1475). Before
    this existed, install.sh's venv-python block called only two of these
    five passes directly (``ensure_seed_profiles``,
    ``clear_stale_mtp_overrides``) — a box upgraded by re-running
    install.sh kept a stale ``meta.schema_version``, stale runner-image
    pins, and unsanitised ``defaults.extra_args``, while a box upgraded
    via ``hal0 update`` did not. There is no other caller of the
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
    schema migration (first) is NOT swallowed — a hard failure there means
    the running code and the on-disk schema have diverged, which the
    caller must treat as fatal (``commit()`` nukes the staged tree and
    aborts; install.sh's ``set -euo pipefail`` aborts the script on a
    non-zero exit). The four data-cleanup passes after it are each
    independently best-effort, exactly as before: one pass's exception is
    logged and never blocks the others or the caller's activation.

    ``ceiling`` hard-caps the migration target for this run — see
    :func:`_maybe_run_config_migrations` for why (the one-shot v1.0
    profile-catalog reset gate). ``None`` (the default, and what
    install.sh's caller always passes) applies no cap.

    Returns the ``(source, target)`` schema-version tuple for breadcrumb
    logging, the same shape ``commit()`` already returned.
    """
    migration_info = _maybe_run_config_migrations(min_data_version, job_id=job_id, ceiling=ceiling)

    try:
        ensure_seed_profiles(job_id=job_id)
    except Exception as exc:
        log.warning("updater.seed_profiles_prune_failed", job_id=job_id, error=str(exc))
        # Non-fatal: a lingering materialised seed is harmless (the overlay
        # overwrites it on load); don't abort the update.

    try:
        clear_stale_mtp_overrides(job_id=job_id)
    except Exception as exc:
        log.warning("updater.mtp_migration_failed", job_id=job_id, error=str(exc))
        # Non-fatal: the affected slot simply fails to load until the
        # operator flips MTP to Auto/Off in the drawer.

    try:
        retag_stale_slot_images(job_id=job_id)
    except Exception as exc:
        log.warning("updater.image_retag_failed", job_id=job_id, error=str(exc))
        # Non-fatal: a stale pin just keeps the previous runner image.

    try:
        sanitize_model_extra_args(job_id=job_id)
    except Exception as exc:
        log.warning("updater.extra_args_sanitize_failed", job_id=job_id, error=str(exc))
        # Non-fatal: an affected model keeps failing to launch until the
        # operator removes the managed flag in the model drawer.

    return migration_info


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


# ── v1.0 profile-catalog reset (one-shot, schema-version gated) ────────────────
#
# Distinct from ensure_seed_profiles() above, deliberately. That function is the
# *conservative* migration: it prunes materialised seeds and RESCUES anything
# divergent under `<name>-custom`. Existing callers and
# tests/updater/test_seed_profiles_migration.py depend on that prune-and-rescue
# contract, so it is left exactly as it is.
#
# This is the *destructive* one. v1.0 made the profile catalog tuning-only, and
# a pre-v1.0 profiles.toml can hold shapes the v1.0 loader no longer accepts
# (hardware fields, stale seed materialisations, operator rows authored against
# the old schema). Converging such a box means deleting the file — the built-in
# catalog is virtual (load_profiles_config overlays SEED_PROFILES on every load;
# save_profiles_config strips seed-named keys before writing), so the reseed is
# free and needs no code here.
#
# Which contract applies is decided purely by the schema-version gate:
#   meta.schema_version <  2  → this reset applies (pre-v1.0 box, converge once)
#   meta.schema_version >= 2  → only ensure_seed_profiles() applies from here on
#
# Paranoia budget, because a gate bug here is shipped data loss:
#   * an ABSENT hal0.toml means the gate can be neither read nor recorded, so
#     the reset refuses outright rather than firing on every update;
#   * every run that deletes writes a NEW timestamped backup first — unlike the
#     write-once `.pre-virtual-seeds.bak` above, which goes stale;
#   * a DECLINED reset does not stamp, and commit() clamps the schema-migration
#     runner below v2 for that run so the one-shot is not silently consumed.


def _raw_hal0_toml() -> dict[str, Any] | None:
    """Read ``hal0.toml`` as a raw dict, or ``None`` when absent/unreadable.

    Deliberately raw: a pre-v1.0 ``hal0.toml`` may fail today's validators, and
    the profile-catalog gate must still be readable on exactly those boxes.
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


def profile_reset_status() -> dict[str, Any]:
    """Report whether the one-shot v1.0 profile-catalog reset is still due.

    Pure read — touches nothing. Called from :meth:`Updater.prepare` (so the
    CLI can prompt with real numbers before anything is activated), from
    :func:`reset_profile_catalog` as its own gate, and from install.sh.

    Returns a dict with:
        ``due``: the reset has not run on this box yet.
        ``reason``: why it is not due, when it is not.
        ``schema_version``: ``hal0.toml``'s ``meta.schema_version`` (1 default).
        ``path`` / ``exists``: the profiles.toml this would delete.
        ``custom_profiles``: operator-authored (non-seed) profile names — the
            only thing an operator can actually lose, and therefore the only
            thing worth prompting about.
        ``unreadable``: profiles.toml exists but does not parse. This is the
            shape that makes ``ensure_seed_profiles`` raise ``ConfigParseError``
            and (before the install.sh containment fix) aborted whole installs.
        ``needs_consent``: True when the delete would destroy operator content.
    """
    from hal0.config.loader import _read_toml
    from hal0.config.migrations.v2 import PROFILE_CATALOG_SCHEMA_VERSION
    from hal0.config.schema import SEED_PROFILES

    target = paths.profiles_toml()
    raw_cfg = _raw_hal0_toml()
    version = _raw_schema_version(raw_cfg)
    exists = target.exists()

    custom: list[str] = []
    unreadable = False
    if exists:
        try:
            raw = _read_toml(target)
        except Exception:
            unreadable = True
        else:
            table = raw.get("profile")
            if isinstance(table, dict):
                custom = sorted(k for k in table if k not in SEED_PROFILES)

    if raw_cfg is None:
        # No hal0.toml → the gate cannot be recorded, so a reset here would
        # re-fire on every single update. Refuse; report why.
        due, reason = False, "no_config"
    elif version >= PROFILE_CATALOG_SCHEMA_VERSION:
        due, reason = False, "already_reset"
    else:
        due, reason = True, ""

    return {
        "due": due,
        "reason": reason,
        "schema_version": version,
        "target_schema_version": PROFILE_CATALOG_SCHEMA_VERSION,
        "path": str(target),
        "exists": exists,
        "custom_profiles": custom,
        "unreadable": unreadable,
        # An unreadable file has no recoverable operator content to weigh (and
        # is actively breaking this box), so it converges without a prompt —
        # the timestamped backup still preserves the original bytes.
        "needs_consent": bool(custom) and not unreadable,
    }


def _backup_profiles_toml(*, job_id: str | None = None) -> str | None:
    """Copy profiles.toml to ``/var/lib/hal0/backups/profiles-<UTC>.toml``.

    Timestamped on every run. The older ``profiles.toml.pre-virtual-seeds.bak``
    convention (:func:`ensure_seed_profiles`) is deliberately write-once, so it
    captures only the first migration a box ever ran and silently goes stale;
    this one never clobbers and never lies about what it holds.

    The stamp has one-second resolution, so a second reset inside the same
    second would land on the same name — suffix until the name is free rather
    than overwrite (same idiom ``ensure_seed_profiles`` uses for ``-custom{n}``).
    A backup that silently replaces the backup it was meant to sit beside is the
    write-once flaw wearing a different hat.
    """
    import shutil
    from datetime import UTC, datetime

    src = paths.profiles_toml()
    if not src.exists():
        return None
    backup_root = paths.var_lib() / "backups"
    stamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
    dest = backup_root / f"profiles-{stamp}.toml"
    n = 2
    while dest.exists():
        dest = backup_root / f"profiles-{stamp}-{n}.toml"
        n += 1
    try:
        backup_root.mkdir(parents=True, exist_ok=True)
        shutil.copy2(src, dest)
    except OSError as exc:
        log.warning("updater.profile_reset_backup_failed", job_id=job_id, error=str(exc))
        return None
    log.info("updater.profile_reset_backup", job_id=job_id, path=str(dest))
    return str(dest)


def _stamp_profile_catalog_version(*, job_id: str | None = None) -> bool:
    """Advance ``hal0.toml``'s ``meta.schema_version`` to the reset watermark.

    Raw round-trip on purpose (see :func:`_raw_hal0_toml`): the stamp has to
    land on exactly the boxes whose config the current validators would reject,
    and preserving unknown keys is strictly safer than dropping them.
    """
    from hal0.config.migrations.v2 import PROFILE_CATALOG_SCHEMA_VERSION

    raw = _raw_hal0_toml()
    if raw is None:
        log.warning("updater.profile_reset_stamp_skipped", job_id=job_id, reason="hal0.toml absent")
        return False
    try:
        new_raw, version = run_migrations(raw, target_version=PROFILE_CATALOG_SCHEMA_VERSION)
        write_toml_atomic(paths.hal0_toml(), new_raw)
    except Exception as exc:
        log.warning("updater.profile_reset_stamp_failed", job_id=job_id, error=str(exc))
        return False
    log.info("updater.profile_reset_stamped", job_id=job_id, schema_version=version)
    return True


def reset_profile_catalog(
    *,
    approved: bool | None = None,
    job_id: str | None = None,
) -> dict[str, Any]:
    """One-shot destructive reset of ``/etc/hal0/profiles.toml`` (v1.0 convergence).

    Deletes the on-disk profile catalog after a timestamped backup, then stamps
    ``meta.schema_version = 2`` so it never fires again. The built-in catalog is
    virtual, so the reseed happens for free on the next
    :func:`~hal0.config.loader.load_profiles_config`.

    Slots referencing a deleted operator profile fall back through the existing
    ``providers.container._resolve_profile_or_base`` path — no new fallback is
    introduced here, and nothing under ``providers/`` is touched.

    Args:
        approved: the operator's answer, when one was needed. ``True`` means
            "delete my custom profiles". ``None``/``False`` mean no consent was
            obtained — which is what a headless run (no TTY, no ``--yes``)
            passes. Ignored when there is nothing to consent to.
        job_id: optional breadcrumb for structured-log tracing.

    Returns:
        The :func:`profile_reset_status` dict plus ``performed`` (did we
        delete/stamp), ``outcome`` (machine-readable), ``backup`` (path or
        ``None``) and ``stamped``.
    """
    status = profile_reset_status()

    if not status["due"]:
        log.info("updater.profile_reset_skipped", job_id=job_id, reason=status["reason"])
        return {
            **status,
            "performed": False,
            "outcome": status["reason"],
            "backup": None,
            "stamped": False,
        }

    if status["needs_consent"] and approved is not True:
        # Headless default is SKIP, never wipe: an unattended update must not
        # destroy operator-authored profiles. The gate stays un-stamped so the
        # next interactive `hal0 update` (or `sudo bash install.sh`) re-offers
        # it, and the convergence report tells the operator it is outstanding.
        log.warning(
            "updater.profile_reset_declined",
            job_id=job_id,
            custom_profiles=status["custom_profiles"],
            note=(
                "profile-catalog reset not applied — operator consent absent "
                "(declined, or headless). Re-run interactively or with --yes."
            ),
        )
        return {
            **status,
            "performed": False,
            "outcome": "declined",
            "backup": None,
            "stamped": False,
        }

    backup = _backup_profiles_toml(job_id=job_id)
    if status["exists"]:
        try:
            paths.profiles_toml().unlink()
        except OSError as exc:
            log.warning("updater.profile_reset_failed", job_id=job_id, error=str(exc))
            return {
                **status,
                "performed": False,
                "outcome": "error",
                "backup": backup,
                "stamped": False,
                "error": str(exc),
            }

    stamped = _stamp_profile_catalog_version(job_id=job_id)
    log.warning(
        "updater.profile_reset_applied",
        job_id=job_id,
        removed=status["exists"],
        custom_profiles=status["custom_profiles"],
        unreadable=status["unreadable"],
        backup=backup,
        stamped=stamped,
    )
    return {**status, "performed": True, "outcome": "reset", "backup": backup, "stamped": stamped}


def _slot_display_name(config_dir: Any, stem: str) -> str:
    """Resolve a slot TOML stem to the slot's ``name``, or the stem itself.

    Both keying layouts are live in the wild: ``<name>.toml`` (pre-rework, and
    what CT150 runs) and ``<id>.toml`` (post ``migrate_slot_id_keying``). Only
    the second needs resolving, and only for display.
    """
    import tomllib

    try:
        raw = tomllib.loads((config_dir / f"{stem}.toml").read_text(encoding="utf-8"))
    except Exception:
        return stem
    table = raw.get("slot") if isinstance(raw.get("slot"), dict) else raw
    name = table.get("name") if isinstance(table, dict) else None
    return str(name) if isinstance(name, str) and name.strip() else stem


def sweep_slot_enabled_keys(*, job_id: str | None = None) -> list[str]:
    """Run the ``SlotConfig.enabled`` removal sweep and report which slots moved.

    The identical sweep already runs at hal0-api boot
    (``hal0.api._boot_slot_reconcile``) where it logs only to
    ``journalctl -u hal0-api`` — invisible in an install/update transcript.
    Calling it here puts the same idempotent pass in front of the operator who
    is actually watching the upgrade.

    Returns the slot names that were rewritten (empty when already swept).

    ``migrate_slot_dir`` reports each file's *stem*, which on an id-keyed box
    (``1.toml`` carrying ``id = 1``, post ``migrate_slot_id_keying``) is ``"1"``
    — a filename, not a slot. An operator reading an install transcript needs
    the slot name, so the stem is resolved back through the rewritten TOML's
    ``name`` field, falling back to the stem when there is none.
    """
    from hal0.config.migrations.slot_enabled_removal import migrate_slot_dir

    try:
        config_dir = paths.slots_config_dir()
        swept = [_slot_display_name(config_dir, stem) for stem in migrate_slot_dir(config_dir)]
    except Exception as exc:
        log.warning("updater.slot_enabled_sweep_failed", job_id=job_id, error=str(exc))
        return []
    if swept:
        log.warning("updater.slot_enabled_swept", job_id=job_id, slots=swept, count=len(swept))
    else:
        log.info("updater.slot_enabled_sweep_noop", job_id=job_id)
    return swept


#: The three spec-hw-slot-ownership / spec-flags-ownership folds that are
#: deploy-window gated and operator-run. Keyed by report name → (module,
#: remediation command).
_OWNERSHIP_MIGRATIONS: tuple[tuple[str, str, str], ...] = (
    ("flags", "hal0.config.migrations.slot_flags_fold", "hal0 slot migrate-flags --apply"),
    ("caps", "hal0.config.migrations.model_owned_caps", "hal0 slot migrate-caps --apply"),
    ("hw", "hal0.config.migrations.hw_slot_ownership", "hal0 slot migrate-hw --apply"),
)


def detect_pending_ownership_migrations(*, job_id: str | None = None) -> dict[str, Any]:
    """Detect — never apply — the ownership folds an old box still needs.

    Why detect instead of auto-run (the deliberate call, spec'd in the commit
    message for this change):

    1. ``hal0 slot migrate-hw --apply`` REFUSES to run while ``hal0-api`` or any
       ``hal0-slot@*`` unit is active (``cli/slot_commands.py``), because
       rewriting slot TOMLs under a live runtime split-brains it.
       :meth:`Updater.commit` executes *inside* hal0-api, and install.sh's
       migration block runs while the old hal0-api is still serving. Both
       auto-run sites are exactly the state the migration refuses.
    2. ``slot_flags_fold`` legitimately REFUSES a model when two slots bound to
       it carry divergent tunes. There is no operator in an auto-run to resolve
       that, so an auto-run would be half-applied by design.
    3. ``hw_slot_ownership`` NULLs registry-DB columns. ``hal0 update rollback``
       restores the code tree, not the DB — irreversible on a path with no undo.

    What is NOT acceptable is leaving the box silently half-converged, so this
    runs each fold's *planner* (``dry_run=True``, filesystem- and DB-write-free)
    and reports precisely what is outstanding, with the command that fixes it.

    Returns ``{"pending": [keys], "detail": {key: {...}}, "commands": [...]}``.
    """
    import importlib

    detail: dict[str, Any] = {}
    for key, module_name, command in _OWNERSHIP_MIGRATIONS:
        entry: dict[str, Any] = {"command": command, "lines": [], "error": None}
        try:
            module = importlib.import_module(module_name)
            raw_lines = module.run_migration(deploy_window=False, dry_run=True) or []
            # slot_flags_fold reports its no-op skips as "skip model ..." lines;
            # those mean CONVERGED, not pending. Every other line is real work.
            entry["lines"] = [str(line) for line in raw_lines if not str(line).startswith("skip ")]
        except Exception as exc:
            # slot_flags_fold raises RuntimeError on divergent-share refusals
            # even in dry-run — that is the single most important thing to
            # surface, not swallow.
            entry["error"] = str(exc)
        detail[key] = entry

    pending = [k for k, v in detail.items() if v["lines"] or v["error"]]
    result = {
        "pending": pending,
        "detail": detail,
        "commands": [detail[k]["command"] for k in pending],
    }
    if pending:
        log.warning(
            "updater.ownership_migrations_pending",
            job_id=job_id,
            pending=pending,
            commands=result["commands"],
        )
    else:
        log.info("updater.ownership_migrations_converged", job_id=job_id)
    return result


def convergence_report(*, job_id: str | None = None) -> dict[str, Any]:
    """Read-only snapshot of how far this box is from the v1.0 on-disk shape.

    Applies nothing. Used by ``installer/install.sh`` to print the same
    convergence verdict ``hal0 update`` prints, so both entry points tell an
    operator the same story about the same box.
    """
    profile = profile_reset_status()
    ownership = detect_pending_ownership_migrations(job_id=job_id)
    return {
        "profile_reset": profile,
        "ownership_migrations": ownership,
        "converged": not ownership["pending"] and not profile["due"],
    }


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


def sanitize_model_extra_args(*, job_id: str | None = None, registry: Any = None) -> int:
    """Strip hal0-managed flags from model ``defaults.extra_args`` (upgrade migration).

    ``defaults.extra_args`` rides the untrusted ``model_extra_args`` argv
    segment, which hard-rejects the §21.7 managed flags at every launch
    (``slot.managed_arg_denied``). Rows stamped before the screens existed —
    notably via the unscreened profile stamp of the since-removed
    ``POST /api/models/{id}/duplicate``, back when 8 seed profiles carried
    ``-c <N>`` — are therefore
    bricked: they can never load until the flag is hand-removed. Strip exactly
    the denylisted tokens (+ values) from every model's ``extra_args`` and log
    loudly per row. Matching is token-exact via the argv alias table, so
    ``--model_path``/``--threads-batch`` are never touched; slot-hardware
    flags (``--threads``/``-dev``) are left alone too — they are functional at
    launch, so stripping them would change working rows.

    Args:
        job_id: Optional breadcrumb for structured-log tracing.
        registry: Model-registry override for tests (anything with ``list()``
            and ``update(model_id, updates)``). ``None`` uses the real
            :class:`~hal0.registry.store.ModelRegistry`.

    Returns:
        Number of model rows sanitized.
    """
    import shlex

    from hal0.slots.argv import strip_managed_flags

    if registry is None:
        from hal0.registry.store import ModelRegistry

        registry = ModelRegistry()

    sanitized = 0
    for model in registry.list():
        defaults = getattr(model, "defaults", None)
        extra = getattr(defaults, "extra_args", None) if defaults is not None else None
        if not extra or not extra.strip():
            continue
        try:
            tokens = shlex.split(extra)
        except ValueError:
            continue  # malformed quoting — leave for the edit-path screens
        clean, removed = strip_managed_flags(tokens)
        if not removed:
            continue
        defaults_dict = defaults.model_dump(mode="python")
        defaults_dict["extra_args"] = " ".join(shlex.quote(tok) for tok in clean)
        try:
            registry.update(model.id, {"defaults": defaults_dict})
        except Exception as exc:
            log.warning(
                "updater.extra_args_sanitize_write_failed",
                job_id=job_id,
                model=model.id,
                error=str(exc),
            )
            continue
        sanitized += 1
        log.warning(
            "updater.model_extra_args_sanitized",
            job_id=job_id,
            model=model.id,
            removed=removed,
            note=(
                "defaults.extra_args carried hal0-managed flag(s) the launcher "
                "hard-rejects (slot.managed_arg_denied); stripped so the model "
                "can load again. Context/port/host come from the slot config."
            ),
        )
    return sanitized


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

    Returns ``{version, install_dir, cache_dir, notes, profile_reset}``.
    ``profile_reset`` is a read-only :func:`profile_reset_status` snapshot so
    the CLI can prompt about the one-shot profile-catalog reset with real
    numbers *before* anything is activated (commit runs inside hal0-api,
    where there is no TTY to prompt on).

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

    # Step 3: download tarball + Sigstore bundle (survives cert expiry, #1159).
    cache = _cache_dir(target_version)
    cache.mkdir(parents=True, exist_ok=True)
    tarball_path = cache / f"hal0-{target_version}.tar.gz"
    bundle_path = cache / f"hal0-{target_version}.tar.gz.bundle"
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

    # Step 6: extract. `_extract_tarball` quarantines a prior hal0
    # extraction at the same path (see its docstring) so a retry
    # after a half-failed apply isn't permanently wedged.
    install_dir = _versioned_install_dir(target_version)
    await asyncio.to_thread(_extract_tarball, tarball_path, install_dir, job_id=job_id)

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
        "profile_reset": await asyncio.to_thread(profile_reset_status),
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
    try:
        for path in (root, *root.rglob("*")):
            os.chown(path, entry.pw_uid, entry.pw_gid)
    except OSError as exc:
        log.warning("updater.cache_chown_failed", job_id=job_id, path=str(root), error=str(exc))


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

        Returns ``{version, install_dir, cache_dir, notes, profile_reset}``.
        ``profile_reset`` is a read-only :func:`profile_reset_status` snapshot so
        the CLI can prompt about the one-shot profile-catalog reset with real
        numbers *before* anything is activated (commit runs inside hal0-api,
        where there is no TTY to prompt on).

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

    async def commit(self, version: str, *, reset_profiles: bool | None = None) -> dict[str, Any]:
        """Activate a previously :meth:`prepare`d ``version`` (§9 steps 7-9+).

        Runs forward config migrations, prunes materialised seed profiles, clears
        stale mtp overrides, atomic-swaps the ``current`` symlink, re-pips the
        tree into the running venv, and re-renders slot units. Requires that
        :meth:`prepare` already staged this version (its ``install_dir`` and
        cached manifest must exist) — otherwise raises ``UpdateError``.

        Slot units are NOT restarted and ``hal0-api`` is not bounced here; the
        route layer (``routes/updater._run_commit_job``) try-restarts hal0-api
        fail-soft after a successful commit. Returns the same breadcrumb dict
        shape as the old single-step apply, plus ``convergence`` — see
        :func:`convergence_report`.

        Args:
            version: the version ``prepare()`` staged.
            reset_profiles: the operator's answer to the one-shot
                profile-catalog reset prompt (see :func:`reset_profile_catalog`).
                ``None`` — the default, and what an unattended
                ``POST /api/updates/commit`` or ``apply()`` passes — means "no
                consent obtained", so a box with operator-authored profiles is
                left alone and reported as outstanding.
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

        # Step 6b: the one-shot v1.0 profile-catalog reset, BEFORE the schema
        # migrations below. Ordering is load-bearing, not stylistic: the reset's
        # gate IS meta.schema_version, and step 7 would stamp it forward, so
        # running the migrations first would make the gate unreadable on the
        # very box it exists for. The reset owns its own stamp; when it does not
        # fire (declined / no consent) the migration target is capped below the
        # watermark so the one-shot is not silently consumed.
        try:
            profile_reset = await asyncio.to_thread(
                reset_profile_catalog, approved=reset_profiles, job_id=self.job_id
            )
        except Exception as exc:
            log.warning("updater.profile_reset_error", job_id=self.job_id, error=str(exc))
            profile_reset = {
                "performed": False,
                "outcome": "error",
                "error": str(exc),
                "due": True,
                "stamped": False,
            }
            # Non-fatal by design: a failed catalog reset leaves the pre-v1.0
            # file in place (the overlay still serves the built-in seeds), and
            # the un-stamped gate re-offers it next update.

        # Step 7: config migrations. Cap the target below the profile-catalog
        # watermark ONLY while that reset is genuinely outstanding — an already
        # converged box (or a future v3 migration) must not be held back.
        migration_info: tuple[int, int]
        reset_outstanding = bool(profile_reset.get("due")) and not profile_reset.get("stamped")
        ceiling = PROFILE_CATALOG_SCHEMA_VERSION - 1 if reset_outstanding else None
        try:
            migration_info = await asyncio.to_thread(
                run_post_activation_migrations,
                manifest.min_data_version,
                job_id=self.job_id,
                ceiling=ceiling,
            )
        except Hal0Error as exc:
            # Don't leave the new tree orphaned on a migration failure —
            # nuke the half-installed dir so a retry starts fresh. Routed
            # through the seam: /usr/lib/hal0 is root-only (#1464).
            with contextlib.suppress(Exception):
                seam.discard(release_dir_name(target_version))
            raise UpdateError(
                f"config migration failed during update: {exc.message}",
                details={**exc.details, "version": target_version},
            ) from exc

        # spec-hw-slot-ownership §6 / spec-flags-ownership §5 one-shot folds are
        # still NOT auto-run here — see detect_pending_ownership_migrations()
        # for the three concrete reasons (the folds refuse to run under a live
        # runtime, and commit() IS the live runtime). What HAS changed is that
        # they are no longer silently skipped: step 7f below runs each fold's
        # write-free planner and the result rides out on the job so an operator
        # is told, by name and with the exact command, what is still outstanding.

        # Step 7c2: sweep the removed SlotConfig.enabled key. The identical
        # idempotent pass already runs at hal0-api boot, where it logs only to
        # journalctl and is invisible in an upgrade transcript. Running it here
        # puts the result in the commit job → `hal0 update` output.
        try:
            enabled_swept = await asyncio.to_thread(sweep_slot_enabled_keys, job_id=self.job_id)
        except Exception as exc:
            log.warning("updater.slot_enabled_sweep_error", job_id=self.job_id, error=str(exc))
            enabled_swept = []

        # Step 7f: write-free detection of the deploy-window ownership folds this
        # box still owes. Runs before the swap so the report describes the state
        # the operator is actually being handed.
        try:
            pending_ownership = await asyncio.to_thread(
                detect_pending_ownership_migrations, job_id=self.job_id
            )
        except Exception as exc:
            log.warning("updater.ownership_detect_failed", job_id=self.job_id, error=str(exc))
            pending_ownership = {"pending": [], "detail": {}, "commands": [], "error": str(exc)}

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

        # A commit that swapped the tree but left the box on the pre-v1.0 slot /
        # model shape is not "done" — surface exactly what is outstanding so the
        # CLI can refuse to call it a clean success.
        convergence = {
            "profile_reset": profile_reset,
            "slot_enabled_swept": enabled_swept,
            "ownership_migrations": pending_ownership,
            "converged": (
                not pending_ownership.get("pending")
                and profile_reset.get("outcome") in {"reset", "already_reset", "no_config"}
            ),
        }
        log.info(
            "updater.convergence",
            job_id=self.job_id,
            converged=convergence["converged"],
            profile_reset=profile_reset.get("outcome"),
            slot_enabled_swept=len(enabled_swept),
            ownership_pending=pending_ownership.get("pending"),
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

    async def apply(
        self, version: str | None = None, *, reset_profiles: bool | None = None
    ) -> dict[str, Any]:
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
        return await self.commit(str(prepared["version"]), reset_profiles=reset_profiles)

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
        # Steps 8 + 8b via the same privileged primitive commit() uses: swap the
        # symlink back and re-pip the prior tree so the next hal0-api restart
        # actually serves the rolled-back version. On a pip failure activate
        # swaps *forward* again, so `current` and site-packages stay consistent.
        activated = await seam.activate(prior_path.name)
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
    "UpdatePrivilegeError",
    "UpdateRollbackUnavailable",
    "UpdateSwapError",
    "UpdateUpgradePathUnsupported",
    "UpdateVerifyError",
    "Updater",
    "activate_release",
    "assert_release_dir_name",
    "assert_trusted_release_dir",
    "convergence_report",
    "detect_pending_ownership_migrations",
    "discard_release",
    "ensure_seed_profiles",
    "fetch_release_manifest",
    "profile_reset_status",
    "refresh_privileged_wrappers",
    "release_dir_name",
    "releases_url",
    "reset_profile_catalog",
    "run_post_activation_migrations",
    "sanitize_model_extra_args",
    "stage_release",
    "sweep_slot_enabled_keys",
    "validate_manifest_for_channel",
]
