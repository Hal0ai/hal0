"""Portable profiles — export/import (mirrors stacks/portable.py, simpler).

Export serializes a single profile (image + flag bundle + display facts; never
secrets, never host paths) into a self-contained ``.hal0profile.json`` envelope
with a content checksum. Import validates the envelope and creates the profile.
Pure functions — the caller stamps ``exported_at``, so there is no clock or
hidden global here. Profiles carry no models/slots, so there is no embedding or
model resolution (unlike stacks).
"""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any

from pydantic import BaseModel

from hal0 import __version__
from hal0.config.schema import PROFILE_SCHEMA_VERSION_CURRENT, ProfileConfig
from hal0.errors import BadRequest

ENVELOPE_KIND = "hal0.profile"


def _checksum(body: dict[str, Any]) -> str:
    """sha256 over the canonical profile body — deterministic, order-independent."""
    payload = json.dumps(body, sort_keys=True, default=str)
    return "sha256:" + hashlib.sha256(payload.encode("utf-8")).hexdigest()


def export_envelope(name: str, profile: ProfileConfig, *, exported_at: str) -> dict[str, Any]:
    """Build the ``.hal0profile.json`` envelope dict for ``profile``.

    ``exported_at`` is caller-supplied (no clock here). The checksum covers the
    profile body only — re-exporting the same profile yields the same checksum
    regardless of ``exported_at``.
    """
    body = profile.model_dump(mode="json", exclude_none=True)
    return {
        "kind": ENVELOPE_KIND,
        "schema_version": PROFILE_SCHEMA_VERSION_CURRENT,
        "hal0_version": __version__,
        "exported_at": exported_at,
        "name": name,
        "checksum": _checksum(body),
        "profile": body,
    }


# ── import ───────────────────────────────────────────────────────────────────


class ProfileEnvelope(BaseModel):
    """Parsed ``.hal0profile.json`` wire shape. ``extra="ignore"`` keeps a newer
    producer's extra envelope keys from breaking import; the inner ProfileConfig
    still forbids unknown fields."""

    model_config = {"extra": "ignore"}

    kind: str
    schema_version: int = PROFILE_SCHEMA_VERSION_CURRENT
    hal0_version: str = ""
    exported_at: str = ""
    name: str = ""
    checksum: str = ""
    profile: ProfileConfig


def parse_envelope(data: Any) -> ProfileEnvelope:
    """Validate the wire shape. Raises BadRequest on a non-envelope/invalid input."""
    if not isinstance(data, dict) or data.get("kind") != ENVELOPE_KIND:
        raise BadRequest(
            "not a hal0.profile envelope",
            code="profiles.bad_envelope",
            details={"kind": (data.get("kind") if isinstance(data, dict) else None)},
        )
    try:
        return ProfileEnvelope.model_validate(data)
    except Exception as exc:
        raise BadRequest(
            f"invalid profile envelope: {exc}",
            code="profiles.bad_envelope",
            details={"reason": str(exc)},
        ) from exc


def verify_checksum(envelope: dict[str, Any]) -> bool:
    """True when the envelope's checksum matches its profile body."""
    body = envelope.get("profile")
    if not isinstance(body, dict):
        return False
    return envelope.get("checksum") == _checksum(body)


def import_profile(
    data: Any,
    name: str,
    catalog: Any,
    *,
    profile_path: Path | None = None,
    force: bool = False,
) -> Any:
    """Validate the envelope and create the profile under ``name``.

    ``catalog`` is a ProfileCatalog (duck-typed: needs ``create(name, ProfileConfig)``).
    Raises BadRequest for a bad/too-new envelope or a checksum that does not
    match the body; the catalog raises Conflict (``profiles.exists``) on a
    duplicate name. Returns the created ResolvedProfile.
    """
    env = parse_envelope(data)
    if env.schema_version > PROFILE_SCHEMA_VERSION_CURRENT:
        raise BadRequest(
            f"profile schema v{env.schema_version} is newer than supported "
            f"v{PROFILE_SCHEMA_VERSION_CURRENT}",
            code="profiles.envelope_too_new",
            details={"got": env.schema_version, "supported": PROFILE_SCHEMA_VERSION_CURRENT},
        )
    # #1416 — the commit path verifies the checksum, not just ``dry_run``.
    #
    # ``POST /api/profiles/import`` reported ``checksum_ok`` on a dry run and
    # then imported without ever checking it again, so a tampered or truncated
    # envelope committed silently and the dry-run report was advisory theatre.
    # The checksum is the only integrity signal a portable profile carries.
    #
    # A MISSING/empty checksum is still accepted: ``ProfileEnvelope.checksum``
    # defaults to ``""`` and hand-authored envelopes legitimately omit it.
    # Only a checksum that is PRESENT and WRONG is rejected — that is
    # unambiguously corruption, never an authoring style. ``force`` is the
    # documented escape hatch for a deliberately hand-edited envelope; it
    # waives this check ONLY — the flag screen below (via ``catalog.create``)
    # still applies unconditionally.
    if not force and env.checksum and isinstance(data, dict) and not verify_checksum(data):
        raise BadRequest(
            "profile envelope checksum does not match its body — the file is "
            "corrupt or was modified after export",
            code="profiles.checksum_mismatch",
            details={
                "expected": env.checksum,
                "actual": _checksum(data.get("profile") or {}),
            },
        )
    # (forward-compat seam: older schema_version would migrate here; only v1 exists.)
    return catalog.create(name, env.profile)
