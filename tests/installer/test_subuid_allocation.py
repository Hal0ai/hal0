"""Rootless-podman subuid/subgid allocation for the hal0 service user.

``useradd --system`` never allocates ``/etc/subuid`` + ``/etc/subgid``
ranges, so every install shipped a hal0 user whose rootless podman ran in a
single-uid namespace. Any image layer carrying files owned by a non-root
uid then died at unpack — ``potentially insufficient UIDs or GIDs available
in user namespace`` / ``lchown ...: invalid argument``, podman exit 125.
Slot units pull through ROOT podman and never hit it, which kept the gap
invisible until the dashboard's runner-image Pull button (which pulls as
the hal0 service user) shipped: first observed live on lxc105, where both
the comfyui and rocmfpx-combined dashboard pulls failed identically while
the same tags sat happily in the root store.

Static-text assertions against ``installer/install.sh``, same technique as
``test_platform_gate_hardening.py`` — actually exercising useradd/usermod
needs root, which the black-box harness owns.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

_REPO_ROOT = Path(__file__).resolve().parents[2]
_INSTALL_SH = _REPO_ROOT / "installer" / "install.sh"


@pytest.fixture(scope="module")
def install_sh_text() -> str:
    assert _INSTALL_SH.exists(), f"missing {_INSTALL_SH}"
    return _INSTALL_SH.read_text(encoding="utf-8")


class TestSubuidAllocation:
    def test_allocation_block_present(self, install_sh_text: str) -> None:
        assert "--add-subuids" in install_sh_text
        assert "--add-subgids" in install_sh_text

    def test_idempotence_guards_on_both_files(self, install_sh_text: str) -> None:
        """The block must be skipped only when BOTH files already carry a
        hal0 range — a subuid-only half-allocation still breaks unpack on the
        gid side."""
        assert re.search(r"grep -q '\^hal0:' /etc/subuid", install_sh_text)
        assert re.search(r"grep -q '\^hal0:' /etc/subgid", install_sh_text)

    def test_range_start_respects_existing_allocations(self, install_sh_text: str) -> None:
        """The chosen start must be computed from every range already present
        in both files, not hardcoded — overlapping another user's range makes
        two accounts share host uids, which is exactly what the namespace is
        supposed to prevent."""
        block = install_sh_text[install_sh_text.index("--add-subuids") - 2000 :]
        assert "/etc/subuid /etc/subgid" in block
        assert re.search(r"_sub_end > SUB_START", block)

    def test_fallback_append_matches_usermod_format(self, install_sh_text: str) -> None:
        """shadow-utils without --add-subuids: the direct append must write
        the exact ``user:start:count`` triple usermod would."""
        assert re.search(
            r'echo "hal0:\$\{SUB_START\}:\$\{SUB_COUNT\}" >> /etc/subuid',
            install_sh_text,
        )
        assert re.search(
            r'echo "hal0:\$\{SUB_START\}:\$\{SUB_COUNT\}" >> /etc/subgid',
            install_sh_text,
        )

    def test_storage_migrate_runs_for_existing_installs(self, install_sh_text: str) -> None:
        """An already-initialized single-uid podman store errors on the next
        operation after the mapping changes; the installer must run the
        one-time ``podman system migrate`` as hal0 (warn-only — a fresh
        install has no storage and must not fail the platform gate)."""
        assert re.search(r"runuser -u hal0 -- podman system migrate", install_sh_text)

    def test_allocation_happens_in_the_system_user_step(self, install_sh_text: str) -> None:
        """The range belongs with user creation (before any hal0-user podman
        touch), not bolted on after service start."""
        user_step = install_sh_text.index('ui_step "System user"')
        fs_step = install_sh_text.index('ui_step "Filesystem layout"')
        alloc = install_sh_text.index("--add-subuids")
        assert user_step < alloc < fs_step
