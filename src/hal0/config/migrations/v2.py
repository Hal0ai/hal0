"""v2 — the v1.0 profile-catalog reset watermark.

v1.0 made the profile catalog **tuning-only**: a profile carries model launch
tune and nothing else.  Every pre-v1.0 install carries an on-disk
``/etc/hal0/profiles.toml`` written under the old rules — entries that may
carry hardware fields, materialised copies of what are now virtual seeds, and
operator profiles authored against a shape the v1.0 loader no longer accepts.
The convergence step for those boxes is to **delete the file** (the built-in
catalog is overlaid from code on every load, so the reseed is free) after
backing it up.

That reset is destructive, so it must fire **exactly once** per box.  This
migration is the watermark that makes "exactly once" true:

* ``meta.schema_version < 2`` means "this box has not been through the v1.0
  profile-catalog reset yet".
* ``meta.schema_version >= 2`` means "already converged — never touch
  ``profiles.toml`` again".

The transform on ``hal0.toml`` itself is deliberately the identity.  The work
this version represents happens **out of band**, on a different file, in
:func:`hal0.updater.updater.reset_profile_catalog` — because it needs a
timestamped backup and an operator prompt, neither of which belongs in a pure
dict transform.  The runner stamps ``meta.schema_version = 2`` after this
returns, which is the whole point: the stamp IS the one-shot gate.

Ordering contract (enforced in :meth:`hal0.updater.updater.Updater.commit`):
``reset_profile_catalog`` runs **before** the schema-migration runner and owns
the stamp.  When an operator declines the reset the stamp must NOT advance, so
``commit()`` clamps the migration runner's target below 2 for that run
(``_maybe_run_config_migrations(..., ceiling=...)``).  Otherwise the runner
would stamp v2 on a box that never had its catalog reset and the one-shot would
be silently consumed.
"""

from __future__ import annotations

from typing import Any

from hal0.config.migrations import register

#: ``meta.schema_version`` at or above which the v1.0 profile-catalog reset has
#: already happened.  Imported by the updater so the gate and the watermark can
#: never drift apart.
PROFILE_CATALOG_SCHEMA_VERSION = 2


@register(2)
def migrate_v1_to_v2(data: dict[str, Any]) -> dict[str, Any]:
    """Identity on ``hal0.toml`` — the version bump is the payload.

    See the module docstring: v2 records that the v1.0 profile-catalog reset
    has been applied to this box.  No key in ``hal0.toml`` changes.

    Args:
        data: The raw ``hal0.toml`` dict at v1.

    Returns:
        ``data`` unchanged.  The runner deep-copied it already and stamps
        ``meta.schema_version = 2`` after this returns.
    """
    return data


__all__ = ["PROFILE_CATALOG_SCHEMA_VERSION", "migrate_v1_to_v2"]
