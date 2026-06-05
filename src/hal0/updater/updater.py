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
  8. Record the prior symlink target in ``/var/lib/hal0/hal0.previous`` for
     rollback. Slot units are NOT touched. The ``hal0-api.service`` restart
     is the route layer's job (``routes/updater._run_apply_job``), not this
     function - apply() only swaps the tree.

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
from hal0.config.loader import load_hal0_config, write_toml_atomic
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


def _previous_record() -> Path:
    """Return ``/var/lib/hal0/hal0.previous`` — the rollback breadcrumb."""
    return paths.var_lib() / "hal0.previous"


def _cache_dir(version: str) -> Path:
    """Return ``/var/lib/hal0/cache/<version>/`` — the per-release download cache."""
    return paths.var_lib() / "cache" / version


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
        update_available = (
            bool(latest)
            and not revoked
            and _version_tuple(latest) > _version_tuple(hal0.__version__)
        )
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

    async def apply(self, version: str | None = None) -> dict[str, Any]:
        """Download, verify, install, and activate ``version`` (or latest).

        Implements the full §9 update sequence:

          1. Fetch + schema-validate the release manifest.
          2. Confirm the target version (caller-pinned or manifest.version).
          3. Download tarball + signature to ``/var/lib/hal0/cache/<version>/``.
          4. SHA-256 verify against the manifest digest.
          5. Cosign verify-blob against the GH Actions OIDC identity.
          6. Extract to ``/usr/lib/hal0-<version>/`` (refuse non-empty).
          7. Run forward config migrations if ``min_data_version`` advanced.
          8. Atomic-swap the ``/usr/lib/hal0/current`` symlink.
          9. Record the prior target in ``/var/lib/hal0/hal0.previous``.

        Slot units are NOT restarted; the ``hal0-api.service`` restart is
        the route layer's responsibility (``routes/updater._run_apply_job``
        try-restarts it fail-soft after a successful apply).

        Returns a breadcrumb dict the route layer can attach to the job
        record (``version``, ``previous``, ``installed_at``,
        ``cosign_skipped``).

        Raises:
            UpdateError + subclasses on any step failure. Partial-state
            artifacts (tempfiles, half-extracted dirs) are cleaned up.
        """
        # Step 1: fetch + validate manifest.
        log.info("updater.apply_start", job_id=self.job_id, channel=self.channel, pinned=version)
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

    # ── rollback ───────────────────────────────────────────────────────────────

    async def rollback(self) -> dict[str, Any]:
        """Revert to the previously installed version.

        Reads ``/var/lib/hal0/hal0.previous`` for the prior symlink target,
        atomic-swaps the ``current`` symlink back, and emits a WARN if the
        running ``meta.schema_version`` is now ahead of the previous tree
        — forward-only migrations are acceptable for v1 (PLAN §9 + Team D
        brief). The route layer can surface the warning in the job result.

        Raises:
            UpdateRollbackUnavailable: No previous record on disk.
            UpdateSwapError: The symlink swap itself failed.
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
    "fetch_release_manifest",
    "releases_url",
]
