"""bridge.db and its WAL sidecars are declared owner-only in the perms table (ADR-0043).

``bridge.db`` holds client key hashes and run text, so unlike the 0644
``hal0.db`` it must stay 0600. SQLite creates the ``-wal``/``-shm`` siblings
with the main file's mode, but the perms table still names all three so the
audit and repair paths agree with what the store sets.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from hal0.config import paths
from hal0.install import perms


@pytest.mark.parametrize("flipped", [True, False])
@pytest.mark.parametrize("suffix", ["", "-wal", "-shm"])
def test_bridge_db_rows_are_owner_only(tmp_hal0_home: str, flipped: bool, suffix: str) -> None:
    user = "hal0" if flipped else "root"
    rows = {r.target: r for r in perms.ownership_table(service_user=user)}
    target = Path(f"{paths.bridge_db()}{suffix}")
    assert target.parent == paths.var_lib()
    row = rows[target]
    assert row.mode == 0o600
    assert row.optional is True
    assert row.owner == "hal0"  # state_owner is hal0 on both the root-era and flipped tables
    assert row.group == "hal0"
    assert row.glob is None


def test_bridge_db_path_is_beside_hal0_db(tmp_hal0_home: str) -> None:
    assert paths.bridge_db() == paths.var_lib() / "bridge.db"
    assert paths.bridge_db().parent == paths.db_path().parent
