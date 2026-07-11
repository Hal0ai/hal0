"""OpenWebUI container-image pin: the pure-text seam.

The OpenWebUI companion runs as a podman container whose image is pinned by
sha256 *manifest-list* digest (see ``packaging/systemd/hal0-openwebui.service``
and ``installer/install.sh``). Unlike the toolbox images, OWUI is *not* listed
in ``manifest.json`` — its digest is hand-pinned in those two files and bumped
per release (#79).

This module is the single place that knows how to read and rewrite that pin.
It is deliberately pure — no network, no subprocess, no privileged writes — so
it is trivially testable and can be reused by:

* ``hal0 update owui`` (repin the *installed* unit on a running box), and
* ``scripts/check-owui-digest.sh`` / the pin-consistency test (assert the
  repo-source pin sites agree).

The upstream digest resolution + the podman/systemctl side effects live in the
CLI (:mod:`hal0.cli.update_commands`); this module never touches the wire.
"""

from __future__ import annotations

import os
import re
from pathlib import Path

#: Canonical registry ref for the companion image (no tag / no digest).
OPENWEBUI_IMAGE_REPO = "ghcr.io/open-webui/open-webui"

#: ghcr.io repository path (the ``<owner>/<name>`` half), for the OCI probe.
OPENWEBUI_GHCR_REPO = "open-webui/open-webui"

#: Tag we resolve against when checking for a newer image. OWUI publishes the
#: multi-arch container under ``:main`` (the tag its own docs recommend for the
#: self-hosted container). Overridable via ``hal0 update owui --tag``.
OPENWEBUI_DEFAULT_TAG = "main"

#: systemd unit that runs the container.
OPENWEBUI_UNIT_NAME = "hal0-openwebui.service"

#: Matches the pinned ref in the unit / install.sh, capturing the digest.
#: e.g. ``ghcr.io/open-webui/open-webui@sha256:7f1b0a…`` → ``sha256:7f1b0a…``.
_PINNED_REF_RE = re.compile(r"open-webui@(sha256:[0-9a-f]{64})")

#: A bare or prefixed sha256 digest, for normalising ``--target`` input.
_SHA256_RE = re.compile(r"^(?:sha256:)?([0-9a-f]{64})$")


def installed_unit_path() -> Path:
    """Resolve the *installed* OpenWebUI unit path.

    ``HAL0_HOME``-aware to mirror the installer's dev layout
    (``$HAL0_HOME/etc/systemd/system/…`` under ``install.sh --dev``), falling
    back to the system location. Kept in lock-step with how ``install.sh``
    computes ``UNIT_DIR``.
    """
    home = os.environ.get("HAL0_HOME", "").strip()
    if home:
        return Path(home) / "etc" / "systemd" / "system" / OPENWEBUI_UNIT_NAME
    return Path("/etc/systemd/system") / OPENWEBUI_UNIT_NAME


def is_sha256_digest(value: str) -> bool:
    """True when ``value`` is a ``sha256:<64-hex>`` digest."""
    return bool(re.fullmatch(r"sha256:[0-9a-f]{64}", value))


def normalize_digest(value: str) -> str | None:
    """Normalise user-supplied ``--target`` to a canonical ``sha256:<hex>``.

    Accepts a bare 64-hex string or a ``sha256:``-prefixed one (any case for
    the hex). Returns ``None`` when the input isn't a plausible digest so the
    caller can reject it with a clear error rather than pinning garbage.
    """
    match = _SHA256_RE.match(value.strip().lower())
    if not match:
        return None
    return f"sha256:{match.group(1)}"


def pinned_ref(digest: str) -> str:
    """Return the pull-ready ``<repo>@<digest>`` reference."""
    return f"{OPENWEBUI_IMAGE_REPO}@{digest}"


def find_owui_digests(text: str) -> list[str]:
    """Return every OpenWebUI image digest referenced in ``text``.

    Used by the pin-consistency check to prove that all pin sites in a file
    agree (the unit references the digest twice — ExecStartPre pull + ExecStart
    run — and install.sh once).
    """
    return _PINNED_REF_RE.findall(text)


def parse_pinned_digest(text: str) -> str | None:
    """Return the single digest pinned in ``text``, or ``None``.

    Returns ``None`` when no pin is present *or* when the pins disagree — an
    inconsistent unit is not a digest we should act on; the caller surfaces
    that as an error.
    """
    digests = find_owui_digests(text)
    if not digests:
        return None
    first = digests[0]
    if any(d != first for d in digests):
        return None
    return first


def repin_unit_text(text: str, new_digest: str) -> tuple[str, int]:
    """Rewrite every pinned OWUI digest in ``text`` to ``new_digest``.

    Returns ``(new_text, replacements)``. The digest must already be a
    canonical ``sha256:<hex>`` (validate with :func:`is_sha256_digest` first).
    Raises :class:`ValueError` on a malformed digest so a bad value can never
    reach the unit file.
    """
    if not is_sha256_digest(new_digest):
        raise ValueError(f"not a sha256 digest: {new_digest!r}")
    new_text, count = _PINNED_REF_RE.subn(f"open-webui@{new_digest}", text)
    return new_text, count


__all__ = [
    "OPENWEBUI_DEFAULT_TAG",
    "OPENWEBUI_GHCR_REPO",
    "OPENWEBUI_IMAGE_REPO",
    "OPENWEBUI_UNIT_NAME",
    "find_owui_digests",
    "installed_unit_path",
    "is_sha256_digest",
    "normalize_digest",
    "parse_pinned_digest",
    "pinned_ref",
    "repin_unit_text",
]
