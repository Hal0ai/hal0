"""Profile addon catalog endpoint.

Mounted under /api/profile-addons (its own prefix: a profile may be named
``catalog``, so a GET beside ``/api/profiles/{name}`` would shadow it):

    GET ""  — the addons this box can import, checked and ready to install

Read-only. Installing a row is ``POST /api/profiles/import`` with the row's
``envelope``; that route owns the write, the audit record and every check.
"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter

from hal0.profiles import ProfileCatalog
from hal0.profiles.addon_catalog import load_catalog

router = APIRouter()


@router.get("")
async def list_profile_addons(refresh: bool = False) -> dict[str, Any]:
    """Return the import catalog.

    Shape::

        {
            "source":     "remote",            # remote | bundled
            "url":        "https://hal0.dev/profiles/index.json",  # null when the fetch is off
            "fetched_at": "2026-11-28T18:00:00Z",
            "error":      null,                # why the published index was not used
            "addons": [
                {
                    "name": "dense", "title": "...", "description": "...",
                    "runtime_family": "llama-server", "runner": null,
                    "runner_stripped": false,   # runner dropped on import: not on this box
                    "checksum": "sha256:...", "min_hal0_version": "1.5.0",
                    "installed": false,         # a profile of this name already exists
                    "profile":  {...},          # the profile body, for display
                    "envelope": {...},          # post to /api/profiles/import; null when blocked
                    "blocked":  null            # or {"code", "message"}: listed, not offered
                }
            ]
        }

    The published index is fetched at most once an hour; ``refresh=true``
    fetches it now. When it cannot be reached the addons this release ships
    are returned with ``source: "bundled"`` and the reason in ``error``. This
    route never fails because the index did.
    """
    installed = {p.name for p in ProfileCatalog().list()}
    return await load_catalog(installed=installed, refresh=refresh)
