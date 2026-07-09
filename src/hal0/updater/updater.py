"""Self-update mechanism for hal0.

Updater handles the full update lifecycle:
  1. Check ``{HAL0_RELEASES_URL}`` (or ``https://releases.hal0.dev/{channel}.json``)
     for a newer version.
  2. Download tarball + cosign signature to ``/var/lib/hal0/cache/<version>/``.
  3. Verify the SHA-256 digest against the release manifest.
  4. ``cosign verify-blob`` against the GitHub Actions OIDC identity
     declared in the manifest (``signer_identity`` / ``signer_issuer``).
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
from typing import Any
from urllib.parse import urlparse

import structlog
from pydantic import BaseModel, Field, field_validator

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

log = structlog.get_logger(__name__)


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
    fall back to unsigned acceptance. On dev (0.x) and pre-release
    builds, ``HAL0_UPDATE_SKIP_COSIGN=1`` bypasses verification for
    end-to-end smoke against unsigned tarballs; on stable v1+ tags the
    env var is silently ignored (see ``docs/internal/release-manifest.md``).
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


class ReleaseManifest(BaseModel):
    """Schema-validated release-manifest payload.

    Mirrors the on-disk JSON shape documented in ``docs/internal/release-manifest.md``
    (``_schema = "hal0.releases.v1"``). Malformed manifests are rejected at
    fetch time so apply() never operates on a half-shaped payload.

    Extra fields are preserved (``extra = "allow"``) so future additions
    (release notes, etc.) round-trip without breaking older clients.
    """

    model_config = {"populate_by_name": True, "extra": "allow"}

    schema_id: str = Field(default="hal0.releases.v1", alias="_schema")
    version: str = Field(..., description="Release version, e.g. '0.1.1'.")
    channel: str = Field(default="stable", description="stable | nightly")
    url: str = Field(..., description="Tarball download URL (https or file).")
    sig_url: str = Field(..., description="Detached cosign signature URL.")
    cert_url: str = Field(
        ...,
        description=(
            "Fulcio-issued certificate URL (cosign keyless OIDC). "
            "Required by ``cosign verify-blob --certificate`` in cosign 3.x; "
            "produced alongside the .sig by ``cosign sign-blob --output-certificate``."
        ),
    )
    digest_sha256: str = Field(..., description="Hex sha256 of the tarball bytes.")
    signer_identity: str = Field(
        ...,
        description=(
            "GitHub Actions OIDC subject. Used as a regex for "
            "``cosign verify-blob --certificate-identity-regexp``."
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

    @field_validator("digest_sha256")
    @classmethod
    def _digest_is_hex(cls, v: str) -> str:
        s = (v or "").strip().lower()
        if s.startswith("sha256:"):
            s = s.split(":", 1)[1]
        if not re.fullmatch(r"[0-9a-f]{64}", s):
            raise ValueError(f"digest_sha256 must be a 64-char hex string, got {v!r}")
        return s


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


async def fetch_release_manifest(channel: str = "stable") -> dict[str, Any]:
    """Fetch and parse the release manifest for ``channel``.

    Returns the parsed JSON dict. Supports both ``http(s)://`` URLs (via
    httpx) and ``file://`` URLs / bare paths (for tests). Raises
    ``OSError`` on transport failures and ``ValueError`` on bad JSON so
    callers can produce typed envelopes.
    """
    url = releases_url(channel)
    parsed = urlparse(url)
    if parsed.scheme in ("", "file"):
        path = parsed.path if parsed.scheme == "file" else url
        try:
            raw = Path(path).read_text(encoding="utf-8")
        except OSError as exc:
            raise OSError(f"could not read release manifest at {path}: {exc}") from exc
        try:
            return json.loads(raw)
        except json.JSONDecodeError as exc:
            raise ValueError(f"release manifest at {path} is not valid JSON: {exc}") from exc

    import httpx

    try:
        async with httpx.AsyncClient(timeout=10.0) as client:
            resp = await client.get(url)
    except httpx.HTTPError as exc:
        raise OSError(f"release manifest fetch failed for {url}: {exc}") from exc
    if resp.status_code != 200:
        raise OSError(f"release manifest fetch returned HTTP {resp.status_code} from {url}")
    try:
        return resp.json()
    except ValueError as exc:
        raise ValueError(f"release manifest at {url} is not valid JSON: {exc}") from exc


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


def _is_pre_release(version: str) -> bool:
    """True for dev placeholder (0.x) or any pre-release tag (contains a hyphen).

    Stable releases (e.g. ``1.0.0``, ``1.2.3``, ``2.0.0``) return False —
    on those, the cosign skip hatch is hard-disabled.
    """
    return version.startswith("0.") or "-" in version


def _cosign_skip() -> bool:
    """Return True if ``HAL0_UPDATE_SKIP_COSIGN=1`` is honored on this build.

    The env var is only respected on dev (0.x) and pre-release builds
    (anything with a ``-rc``/``-dev`` suffix). On stable v1+ tags the env
    var is silently ignored — verified releases are mandatory.
    """
    if os.environ.get("HAL0_UPDATE_SKIP_COSIGN", "").strip() != "1":
        return False
    from hal0 import __version__

    if not _is_pre_release(__version__):
        log.warning(
            "updater.cosign_skip_ignored_on_stable",
            version=__version__,
            reason="HAL0_UPDATE_SKIP_COSIGN is not honored on stable releases",
        )
        return False
    return True


def _verify_cosign(
    tarball: Path,
    signature: Path,
    certificate: Path,
    *,
    identity_regexp: str,
    issuer: str,
    job_id: str | None = None,
) -> None:
    """Invoke ``cosign verify-blob`` against the GitHub Actions OIDC identity.

    Raises:
        UpdateCosignMissing: ``cosign`` not on PATH.
        UpdateCosignFailed: signature invalid or identity mismatch.

    cosign 3.x requires the Fulcio-issued certificate (``--certificate``)
    alongside the signature for keyless verification; ``--certificate-
    identity-regexp`` is checked against the cert's SAN. The cert is
    fetched from ``manifest.cert_url`` and stored next to the .sig.

    The skip env-var (``HAL0_UPDATE_SKIP_COSIGN=1``) bypasses the entire
    check with a WARN log line — documented gap, must close before v1.
    """
    if _cosign_skip():
        log.warning(
            "updater.cosign_skipped",
            job_id=job_id,
            tarball=str(tarball),
            reason="HAL0_UPDATE_SKIP_COSIGN=1",
        )
        return

    cosign = shutil.which("cosign")
    if not cosign:
        from hal0 import __version__

        skip_hint = (
            "or set HAL0_UPDATE_SKIP_COSIGN=1 to bypass (pre-release builds only; ignored on stable)"
            if _is_pre_release(__version__)
            else "skip env-var is not honored on this stable build"
        )
        raise UpdateCosignMissing(
            f"cosign is not installed; install from https://docs.sigstore.dev/cosign/installation/ {skip_hint}",
            details={
                "install_hint_arch": "pacman -S cosign  # or: paru -S cosign-bin",
                "install_hint_deb": "see https://docs.sigstore.dev/cosign/installation/",
                "skip_env": ("HAL0_UPDATE_SKIP_COSIGN=1" if _is_pre_release(__version__) else None),
            },
        )

    cmd = [
        cosign,
        "verify-blob",
        "--signature",
        str(signature),
        "--certificate",
        str(certificate),
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


# ── Extraction + migration helpers ─────────────────────────────────────────────


def _looks_like_hal0_install(path: Path) -> bool:
    """Heuristic: does ``path`` look like a prior hal0 tarball extraction?

    A safe quarantine candidate has either a top-level ``VERSION`` file
    or a ``pyproject.toml`` whose ``name`` is ``hal0``. We deliberately
    refuse to touch unrelated non-empty directories.
    """
    if (path / "VERSION").is_file():
        return True
    pp = path / "pyproject.toml"
    if pp.is_file():
        try:
            head = pp.read_text(encoding="utf-8", errors="replace")[:512]
        except OSError:
            return False
        return 'name = "hal0"' in head or "name = 'hal0'" in head
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


def _is_editable_install() -> bool:
    """True when hal0 runs from an editable/dev checkout, not the FHS venv.

    Returns ``False`` for git-tracked installs under the FHS layout
    (``/usr/lib/hal0/hal0-<version>/``)— those are hosted by `prepare_git()`.
    """
    import hal0

    try:
        Path(hal0.__file__).resolve().relative_to(Path(sys.prefix).resolve())
        return False
    except ValueError:
        pass
    # Git-tracked FHS installs: code lives in a versioned dir under
    # /usr/lib/hal0/ — not truly editable. Let prepare_git handle them.
    return not _is_git_install()


def _is_git_install() -> bool:
    """True when hal0 is installed from a git clone under the FHS layout.

    Detects versioned directories like ``/usr/lib/hal0/hal0-0.9.4/`` that
    contain a ``.git`` directory (or are a git worktree). These installs
    can be updated via ``prepare_git()`` instead of downloading release
    tarballs.
    """
    import hal0

    here = Path(hal0.__file__).resolve()
    return any((parent / ".git").is_dir() for parent in here.parents)


def _reinstall_into_venv(install_dir: Path, *, job_id: str | None = None) -> None:
    """``pip install --no-deps --force-reinstall <install_dir>`` into the running venv.

    apply() swaps the ``current`` symlink but the venv imports hal0 from its own
    site-packages, so the swap alone changes nothing until the code is
    reinstalled. ``--no-deps`` keeps it fast and offline-safe (deps were
    resolved at install time); a release that changes deps needs a full
    reinstall. Raises ``UpdateError`` on a non-zero pip exit.
    """
    cmd = [
        sys.executable,
        "-m",
        "pip",
        "install",
        "--no-deps",
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
            channel: Release channel — "stable" (default) or "nightly".
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
        url = releases_url(ch)
        try:
            raw = await fetch_release_manifest(ch)
        except OSError as exc:
            raise UpdateError(
                f"could not fetch release manifest: {exc}",
                details={"channel": ch, "url": url, "error": str(exc)},
            ) from exc
        except ValueError as exc:
            raise UpdateError(
                f"release manifest is not valid JSON: {exc}",
                details={"channel": ch, "url": url, "error": str(exc)},
            ) from exc

        # Soft-validate: some routes (test fixture) ship a minimal manifest
        # with just {"version": "9.9.9"} — surface it without forcing a
        # full schema match. Strict validation happens inside ``apply()``.
        latest = ""
        revoked = False
        revoked_reason = ""
        if isinstance(raw, dict):
            latest = str(raw.get("version") or raw.get("latest_version") or "")
            revoked = bool(raw.get("revoked", False))
            revoked_reason = str(raw.get("revoked_reason") or "")
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
            digest_sha256=raw.get("digest_sha256") if isinstance(raw, dict) else None,
            signer_identity=raw.get("signer_identity") if isinstance(raw, dict) else None,
            min_data_version=raw.get("min_data_version") if isinstance(raw, dict) else None,
            notes_url=raw.get("notes_url") if isinstance(raw, dict) else None,
            revoked=revoked,
            revoked_reason=revoked_reason,
            raw_manifest=raw if isinstance(raw, dict) else {},
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

        Returns ``{version, install_dir, cache_dir, cosign_skipped, notes}``.

        Raises:
            UpdateError + subclasses on any step failure. Partial-state
            artifacts (tempfiles, half-extracted dirs) are cleaned up.
        """
        # Guard: hard-refuse on editable/dev installs.  apply() manipulates
        # the FHS layout (/usr/lib/hal0/current symlink + venv site-packages)
        # which does not exist in an editable checkout.  Continuing would
        # silently extract a tarball that is never actually loaded.
        # Re-run `git pull && pip install -e .` instead.
        if _is_editable_install():
            raise UpdateError(
                "update is not supported on an editable (dev) install — "
                "run 'git pull && pip install -e .' to update",
                details={"hint": "editable install detected via hal0.__file__ outside sys.prefix"},
            )

        # Step 1: fetch + validate manifest.
        log.info("updater.prepare_start", job_id=self.job_id, channel=self.channel, pinned=version)
        try:
            raw = await fetch_release_manifest(self.channel)
        except (OSError, ValueError) as exc:
            raise UpdateError(
                f"could not fetch release manifest: {exc}",
                details={"channel": self.channel, "error": str(exc)},
            ) from exc
        manifest = _parse_manifest(raw)

        # Step 2: confirm target version.
        target_version = (version or "").strip() or manifest.version
        if not target_version:
            raise UpdateManifestInvalid(
                "release manifest has no usable version",
                details={"channel": self.channel},
            )
        if version and version != manifest.version:
            log.info(
                "updater.version_pinned_mismatch",
                job_id=self.job_id,
                pinned=version,
                manifest=manifest.version,
            )

        # Step 3: download tarball + signature + cert.
        cache = _cache_dir(target_version)
        cache.mkdir(parents=True, exist_ok=True)
        tarball_path = cache / f"hal0-{target_version}.tar.gz"
        sig_path = cache / f"hal0-{target_version}.tar.gz.sig"
        cert_path = cache / f"hal0-{target_version}.tar.gz.crt"
        log.info(
            "updater.download_start",
            job_id=self.job_id,
            version=target_version,
            url=manifest.url,
        )
        await _download(manifest.url, tarball_path)
        await _download(manifest.sig_url, sig_path)
        await _download(manifest.cert_url, cert_path)
        log.info(
            "updater.download_ok",
            job_id=self.job_id,
            tarball=str(tarball_path),
            sig=str(sig_path),
            cert=str(cert_path),
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

        # Step 5: cosign verify-blob.
        await asyncio.to_thread(
            _verify_cosign,
            tarball_path,
            sig_path,
            cert_path,
            identity_regexp=manifest.signer_identity,
            issuer=manifest.signer_issuer,
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
            "cosign_skipped": _cosign_skip(),
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
        if _is_editable_install():
            raise UpdateError(
                "update is not supported on an editable (dev) install — "
                "run 'git pull && pip install -e .' to update",
                details={"hint": "editable install detected via hal0.__file__ outside sys.prefix"},
            )
        target_version = (version or "").strip()
        if not target_version:
            raise UpdateManifestInvalid(
                "commit requires an explicit prepared version",
                details={"channel": self.channel},
            )
        install_dir = _versioned_install_dir(target_version)
        cache = _cache_dir(target_version)
        if not install_dir.exists():
            raise UpdateError(
                f"nothing staged for {target_version} — call prepare() before commit()",
                details={"version": target_version, "install_dir": str(install_dir)},
            )
        # Manifest cached by prepare(); needed for min_data_version below.
        manifest = _parse_manifest(_load_cached_manifest(target_version))
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
            "cosign_skipped": _cosign_skip(),
            "installed_at": time.time(),
        }

    async def apply(self, version: str | None = None) -> dict[str, Any]:
        """Prepare + commit in one call — the back-compat single-step update.

        Equivalent to the pre-split ``apply()``: stages the release
        (:meth:`prepare`) and immediately activates it (:meth:`commit`). Callers
        that want to show release notes / gate on confirmation between the two
        phases call :meth:`prepare` then :meth:`commit` directly instead.
        """
        prepared = await self.prepare(version)
        return await self.commit(str(prepared["version"]))

    # ── git-based update ──────────────────────────────────────────────────────

    async def prepare_git(self, remote: str = "origin", branch: str = "main") -> dict[str, Any]:
        """Stage an update from a git remote instead of a release tarball.

        Clones (first time) or fetches the remote into
        ``/usr/lib/hal0/hal0-<latest-tag>/`` and returns the version to commit.
        Does NOT activate — call :meth:`commit_git` to swap + pip install.

        The update repo is a bare git clone at ``/var/lib/hal0/cache/repo.git``
        that is fetch-only.  Versioned install trees are sparse checkouts of
        the tag under ``/usr/lib/hal0/`` — the same layout the tarball path
        uses, so ``commit_git`` can reuse the same symlink-swap logic.
        """
        cache = paths.var_lib() / "cache"
        cache.mkdir(parents=True, exist_ok=True)
        repo_dir = cache / "repo.git"
        remote_url = "https://github.com/Hal0ai/hal0.git"

        if (repo_dir / "HEAD").is_file():
            log.info("updater.git_fetch", repo=str(repo_dir), remote=remote_url)
            await asyncio.to_thread(
                _git,
                "-C",
                str(repo_dir),
                "fetch",
                remote_url,
                "+refs/tags/*:refs/tags/*",
            )
        else:
            log.info("updater.git_clone_bare", repo=str(repo_dir), remote=remote_url)
            # Remove a leftover partial clone from a prior aborted run.
            with contextlib.suppress(OSError):
                shutil.rmtree(repo_dir)
            await asyncio.to_thread(
                _git,
                "clone",
                "--bare",
                remote_url,
                str(repo_dir),
            )

        # Find the latest stable tag (highest semver, ignoring -nightly/-beta).
        latest_tag = await asyncio.to_thread(_latest_stable_tag, repo_dir)
        if not latest_tag:
            raise UpdateError(
                "no stable release tags found in the git remote",
                details={"remote": remote_url},
            )
        log.info("updater.git_latest_tag", tag=latest_tag)

        # Check out the tag into the versioned install dir.
        target_version = latest_tag.lstrip("v")
        install_dir = _versioned_install_dir(target_version)
        if not install_dir.exists():
            await asyncio.to_thread(
                _git,
                "-C",
                str(repo_dir),
                "--work-tree",
                str(install_dir),
                "checkout",
                latest_tag,
                "--",
                ".",
            )
        log.info("updater.git_prepared", version=target_version, install_dir=str(install_dir))
        return {"version": target_version, "install_dir": str(install_dir)}

    async def commit_git(self, version: str) -> dict[str, Any]:
        """Activate a git-prepared version: pip install + symlink swap.

        Uses the same commit logic as :meth:`commit` (migrations, seed pruning,
        MTP override cleanup, symlink swap, venv re-pip, slot unit re-render)
        but reads ``min_data_version`` from the git tree's ``pyproject.toml``
        instead of a release manifest.
        """
        target_version = (version or "").strip()
        if not target_version:
            raise UpdateManifestInvalid("commit_git requires a prepared version")
        install_dir = _versioned_install_dir(target_version)
        if not install_dir.exists():
            raise UpdateError(
                f"nothing staged for {target_version} — call prepare_git() before commit_git()",
                details={"version": target_version, "install_dir": str(install_dir)},
            )

        log.info("updater.commit_git_start", job_id=self.job_id, version=target_version)

        # Derive min_data_version from the tree's pyproject.toml.
        min_data_version = _read_min_data_version(install_dir)

        # Step 7: config migrations.
        migration_info: tuple[int, int]
        try:
            migration_info = await asyncio.to_thread(
                _maybe_run_config_migrations,
                min_data_version,
                job_id=self.job_id,
            )
        except Hal0Error as exc:
            raise UpdateError(
                f"config migration failed during update: {exc.message}",
                details={**exc.details, "version": target_version},
            ) from exc

        # Step 7b: virtual-seed migration.
        try:
            await asyncio.to_thread(ensure_seed_profiles, job_id=self.job_id)
        except Exception as exc:
            log.warning("updater.seed_profiles_prune_failed", job_id=self.job_id, error=str(exc))

        # Step 7c: stale MTP overrides.
        try:
            await asyncio.to_thread(clear_stale_mtp_overrides, job_id=self.job_id)
        except Exception as exc:
            log.warning("updater.mtp_migration_failed", job_id=self.job_id, error=str(exc))

        # Step 8 + 9: atomic symlink swap + record previous.
        link = _current_symlink()
        try:
            prior = _atomic_symlink_swap(install_dir, link)
        except OSError as exc:
            raise UpdateSwapError(
                f"atomic symlink swap failed: {exc}",
                details={"link": str(link), "target": str(install_dir), "error": str(exc)},
            ) from exc

        # Re-pip into the shared venv.
        try:
            await asyncio.to_thread(_reinstall_into_venv, install_dir, job_id=self.job_id)
        except UpdateError:
            if prior is not None:
                with contextlib.suppress(OSError):
                    _atomic_symlink_swap(prior, link)
            raise

        # Re-render slot units through the new code.
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
            log.warning("updater.unit_rerender_failed", job_id=self.job_id, error=str(exc))

        if prior is not None:
            _write_atomic_text(_previous_record(), str(prior))
        log.info("updater.commit_git_ok", job_id=self.job_id, version=target_version)
        return {
            "version": target_version,
            "previous": str(prior) if prior else None,
            "install_dir": str(install_dir),
            "migrations": {"from": migration_info[0], "to": migration_info[1]},
            "installed_at": time.time(),
        }

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


def _git(*args: str) -> None:
    """Run a git command, raising UpdateError on failure."""
    proc = subprocess.run(["git", *args], capture_output=True, text=True)
    if proc.returncode != 0:
        raise UpdateError(
            f"git {' '.join(args[:3])}... failed (rc={proc.returncode})",
            details={"stderr": proc.stderr[-1000:]},
        )


def _latest_stable_tag(repo_dir: Path) -> str | None:
    """Return the highest semver tag (v0.x.y) skipping -nightly/-beta."""
    proc = subprocess.run(
        ["git", "-C", str(repo_dir), "tag", "--sort=-version:refname"],
        capture_output=True,
        text=True,
    )
    if proc.returncode != 0:
        return None
    for line in proc.stdout.strip().splitlines():
        tag = line.strip()
        if re.match(r"^v\d+\.\d+\.\d+(\.\d+)?$", tag):
            return tag
    return None


def _read_min_data_version(install_dir: Path) -> int:
    """Parse min_data_version from a git tree's pyproject.toml."""
    pp = install_dir / "pyproject.toml"
    if not pp.is_file():
        return 1
    try:
        import tomllib
    except ImportError:
        import tomli as tomllib  # type: ignore[no-redef]
    try:
        data = tomllib.loads(pp.read_text(encoding="utf-8"))
        return int(data.get("tool", {}).get("hal0", {}).get("min_data_version", 1) or 1)
    except Exception:
        return 1


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
]
