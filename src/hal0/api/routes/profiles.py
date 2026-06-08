"""Profile catalog endpoint.

Mounted at ``GET /api/profiles`` — returns the full profile catalog from
``/etc/hal0/profiles.toml``, falling back to the built-in seed profiles on a
fresh install.

Each profile entry includes a ``resolved_flags`` field that inlines the MTP
bundle expansion so callers (and tests) can observe the exact flag string that
would be passed to llama-server without having to call
``resolve_profile_flags()`` separately.
"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter

from hal0.config.loader import load_profiles_config
from hal0.config.schema import resolve_profile_flags

router = APIRouter()


@router.get("")
def list_profiles() -> list[dict[str, Any]]:
    """Return every profile in the catalog as a JSON array.

    Each item shape::

        {
            "name":           "moe-rocmfp4",
            "image":          "ghcr.io/hal0ai/...:rocm-7.2.4-rocmfp4-server",
            "flags":          "-fa on ...",
            "mtp":            false,
            "resolved_flags": "-fa on ..."   # flags + MTP bundle when mtp=true
        }

    Raises:
        500 (ConfigParseError): if profiles.toml is present but malformed.
    """
    cfg = load_profiles_config()
    return [
        {
            "name": name,
            "image": p.image,
            "flags": p.flags,
            "mtp": p.mtp,
            "resolved_flags": resolve_profile_flags(p),
        }
        for name, p in cfg.profile.items()
    ]


__all__ = ["router"]
