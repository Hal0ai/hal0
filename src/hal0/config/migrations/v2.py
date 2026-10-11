"""v2 — identity step; once the watermark of the retired v1.0 profile reset.

``meta.schema_version = 2`` used to record that a box had been through the
one-shot v1.0 profile-catalog reset, which deleted a pre-v1.0
``/etc/hal0/profiles.toml``. That reset is gone: upgrades start at 1.3.0, and a
1.3.0 box runs on whatever ``profiles.toml`` it has. A box an operator kept at
``schema_version = 1`` by declining the reset is stamped 2 by the next
migration run and keeps its file.

The step stays registered so the migration chain has no gap below a future v3.
The transform is the identity; the runner stamps the version after it returns.
"""

from __future__ import annotations

from typing import Any

from hal0.config.migrations import register

#: The schema version this step produces. A 1.3.x updater's post-swap child
#: script still imports it (through ``hal0.updater.updater``).
PROFILE_CATALOG_SCHEMA_VERSION = 2


@register(2)
def migrate_v1_to_v2(data: dict[str, Any]) -> dict[str, Any]:
    """Identity on ``hal0.toml``; the runner stamps ``schema_version = 2``."""
    return data


__all__ = ["PROFILE_CATALOG_SCHEMA_VERSION", "migrate_v1_to_v2"]
