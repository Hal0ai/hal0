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
