"""Permission hardening for stacks state.json + stacks.toml (spec §4).

Live incident (ct105, 2026-07-12 → 08-24): a root-run CLI wrote state.json via
mkstemp (0600 root:root), and root-created stacks.toml was 0600 too. The
hal0-user API then 500'd (``system.internal``) on every /api/stacks read until
a manual chgrp/chmod. These tests pin the two fixes: the atomic writer must
leave the file group-readable, and an unreadable file must surface as a typed
``StacksStateUnreadable`` instead of a raw ``PermissionError``.
"""

from __future__ import annotations

import os
import stat

import pytest

from hal0.stacks.state import StackStateRecord, read_stack_state, write_stack_state_atomic


def test_atomic_write_leaves_group_readable_state(tmp_path):
    """The atomic writer must chmod the mkstemp temp file before replace."""
    path = tmp_path / "state.json"
    write_stack_state_atomic(
        path,
        StackStateRecord(active_slug="s", content_hash="x", applied_at=1.0),
    )
    mode = stat.S_IMODE(os.stat(path).st_mode)
    assert mode & 0o060 == 0o060, f"group rw missing: {oct(mode)}"


def test_read_state_permission_error_is_typed(tmp_path):
    from hal0.stacks.state import StacksStateUnreadable

    path = tmp_path / "state.json"
    path.write_text("{}")
    path.chmod(0o000)
    if os.geteuid() == 0:
        pytest.skip("root reads through 0o000")
    with pytest.raises(StacksStateUnreadable) as exc:
        read_stack_state(path)
    assert "state.json" in str(exc.value)
    # The writer produces 0o664 (root CLI and hal0-user service both write) —
    # the operator hint must match the behavior, not a read-only 640.
    assert "chmod 664" in str(exc.value)


def test_load_stacks_config_permission_error_is_typed(tmp_path):
    from hal0.config.loader import load_stacks_config
    from hal0.stacks.state import StacksStateUnreadable

    p = tmp_path / "stacks.toml"
    p.write_text("")
    p.chmod(0o000)
    if os.geteuid() == 0:
        pytest.skip("root reads through 0o000")
    with pytest.raises(StacksStateUnreadable) as exc:
        load_stacks_config(path=p)
    assert "stacks.toml" in str(exc.value)
    assert "chmod 664" in str(exc.value)


def test_drift_status_degrades_when_state_unreadable(tmp_hal0_home, caplog):
    """drift_status is a cosmetic status read — its documented philosophy is
    that it must never raise. An unreadable state.json degrades to the same
    shape as "no stack applied", with a warning for operator visibility (the
    stacks list path still raises the typed error loudly)."""
    import logging
    from pathlib import Path

    from hal0.config import paths
    from hal0.stacks import StacksCatalog
    from hal0.stacks.apply import StackApplyEngine

    if os.geteuid() == 0:
        pytest.skip("root reads through 0o000")
    state_path = paths.stacks_state_path()
    state_path.parent.mkdir(parents=True, exist_ok=True)
    state_path.write_text("{}", encoding="utf-8")
    state_path.chmod(0o000)
    catalog = StacksCatalog(path=Path(tmp_hal0_home) / "etc" / "hal0" / "stacks.toml")
    with caplog.at_level(logging.WARNING, logger="hal0.stacks.apply"):
        assert StackApplyEngine().drift_status(catalog) == {"active": None, "status": "none"}
    assert any("stacks.state_unreadable_for_drift" in r.message for r in caplog.records)
