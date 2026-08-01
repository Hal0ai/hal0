"""hal0.updater — Atomic self-update with cosign-verified releases.

Implements `hal0 update [--check] [--rollback]` on the stable channel.

The updater fetches the per-channel release manifest, verifies its cosign
signature, extracts to a versioned directory, runs any pending config
migrations, and atomically swaps the /usr/lib/hal0/current symlink.
Running slots are NOT restarted; only hal0-api is bounced (by the route
layer) after a successful apply.

Rollback swaps the symlink back to the retained previous version.

See PLAN.md §9 (update mechanism) and §17 risk #2 (cosign edge cases).
The schema for release manifests lives at ``docs/internal/release-manifest.md``.

Key exports:
    Updater — check / apply / rollback methods.
    ReleaseInfo — typed result of Updater.check.
    ReleaseManifest — pydantic schema for the on-disk manifest.
    UpdateError + subclasses — typed errors with system.update_* codes.
"""

from __future__ import annotations

from hal0.updater.updater import (
    ReleaseInfo,
    ReleaseManifest,
    UpdateCosignFailed,
    UpdateCosignMissing,
    UpdateDownloadError,
    UpdateError,
    UpdateExtractError,
    UpdateManifestInvalid,
    Updater,
    UpdateRollbackUnavailable,
    UpdateSwapError,
    UpdateUpgradePathUnsupported,
    UpdateVerifyError,
    convergence_report,
    detect_pending_ownership_migrations,
    fetch_release_manifest,
    profile_reset_status,
    releases_url,
    reset_profile_catalog,
    sweep_slot_enabled_keys,
    validate_manifest_for_channel,
    validate_release_version,
)

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
    "UpdateUpgradePathUnsupported",
    "UpdateVerifyError",
    "Updater",
    "convergence_report",
    "detect_pending_ownership_migrations",
    "fetch_release_manifest",
    "profile_reset_status",
    "releases_url",
    "reset_profile_catalog",
    "sweep_slot_enabled_keys",
    "validate_manifest_for_channel",
    "validate_release_version",
]
