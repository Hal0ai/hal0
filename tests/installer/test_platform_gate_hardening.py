"""Platform-gate hardening — WS-B (#1098): early hal0 user + disk-on-store.

Two more gaps closed alongside bootstrap-prereq parity
(``test_bootstrap_prereq_parity.py``):

1. The ``hal0`` system user/group (and its render/video group membership)
   used to be created very late — after directories, the source copy,
   hal0.toml patching, and the systemd-unit render had already mutated the
   host (handoffs/installer-setup-plan-2026-07-05.md, decision Q1). It is
   now created immediately after pre-flight, before the first mutating
   step ("Filesystem layout"'s ``mkdir -p``).
2. The disk-space pre-flight only ever measured ``VAR_DIR``, never the
   models store the operator actually picked via ``--models-dir`` /
   ``HAL0_MODELS_DIR`` — a separate mount in the "big NVMe/NAS for
   weights" case. It now also measures ``MODELS_DIR`` (warn-only, per the
   Q4 posture: no model is chosen yet at install time, so an undersized
   store shouldn't hard-block the rest of the platform gate).

These are static-text/ordering assertions against ``installer/install.sh``
(the same technique ``tests/systemd/test_hf_token_secrets.py`` uses) —
actually creating a system user or exhausting a mount needs root / a
disposable filesystem, which the black-box harness
(``tests/harness/installer-test.sh``) exercises instead.
"""

from __future__ import annotations

import re
import subprocess
from pathlib import Path

import pytest

_REPO_ROOT = Path(__file__).resolve().parents[2]
_INSTALL_SH = _REPO_ROOT / "installer" / "install.sh"


@pytest.fixture(scope="module")
def install_sh_text() -> str:
    assert _INSTALL_SH.exists(), f"missing {_INSTALL_SH}"
    return _INSTALL_SH.read_text(encoding="utf-8")


# ── early hal0 user ─────────────────────────────────────────────────────────


class TestEarlyHal0User:
    def test_group_and_user_creation_present(self, install_sh_text: str) -> None:
        assert "groupadd --system hal0" in install_sh_text
        assert re.search(r"useradd --system --gid hal0", install_sh_text)

    def test_render_video_group_membership_present(self, install_sh_text: str) -> None:
        assert re.search(r"for _g in render video", install_sh_text)
        assert "usermod -aG" in install_sh_text

    def test_user_and_group_created_exactly_once(self, install_sh_text: str) -> None:
        # A prior version of this block ran twice (once — the real one —
        # having been left behind after the move). Guard the merge.
        assert install_sh_text.count("groupadd --system hal0") == 1
        assert install_sh_text.count("useradd --system --gid hal0") == 1

    def test_user_creation_precedes_filesystem_layout_step(self, install_sh_text: str) -> None:
        # "Filesystem layout" is the first ui_step that mutates the host
        # (mkdir -p PREFIX/ETC_DIR/MODELS_DIR/...). The hal0 user must exist
        # before that, not after.
        useradd_idx = install_sh_text.index("useradd --system --gid hal0")
        mkdir_step_idx = install_sh_text.index('ui_step "Filesystem layout"')
        assert useradd_idx < mkdir_step_idx, (
            "hal0 user must be created before the 'Filesystem layout' mutation step"
        )

    def test_user_creation_precedes_source_copy_and_toml_patch(self, install_sh_text: str) -> None:
        # Belt-and-suspenders on top of the ui_step check: also strictly
        # before the rsync/tar source copy and the hal0.toml [models] patch,
        # both of which are mutation the user must predate per Q1.
        useradd_idx = install_sh_text.index("useradd --system --gid hal0")
        rsync_idx = install_sh_text.index("Copying source to")
        toml_idx = install_sh_text.index('HAL0_TOML="${ETC_DIR}/hal0.toml"')
        assert useradd_idx < rsync_idx
        assert useradd_idx < toml_idx

    def test_user_creation_precedes_preflight_gpu_group_dependent_followups(
        self, install_sh_text: str
    ) -> None:
        # The FLM cache dir is chowned to `1000:hal0` — it depends on the
        # hal0 group existing. It must come after the early user/group block.
        useradd_idx = install_sh_text.index("useradd --system --gid hal0")
        flm_chown_idx = install_sh_text.index("chown 1000:hal0")
        assert useradd_idx < flm_chown_idx

    def test_dev_mode_still_skips_user_creation(self, install_sh_text: str) -> None:
        assert "dev mode — skipping hal0 system user creation" in install_sh_text

    def test_followup_block_does_not_recreate_user(self, install_sh_text: str) -> None:
        # The later (device/dir-dependent) block must not re-run getent/
        # groupadd/useradd — that work already happened earlier.
        m = re.search(
            r"── hal0 system user: device/dir-dependent follow-up.*?\nif.*?\nelse\n(.*?)\nfi\n",
            install_sh_text,
            re.DOTALL,
        )
        assert m is not None, "device/dir-dependent follow-up block not found"
        followup = m.group(1)
        # (Comments in this block legitimately reference "useradd above" as
        # documentation of the split — check for the actual invocations,
        # not the bare substring.)
        assert "groupadd --system hal0" not in followup
        assert "useradd --system --gid hal0" not in followup


# ── disk-on-store ────────────────────────────────────────────────────────────


class TestDiskOnStore:
    def test_models_dir_is_probed(self, install_sh_text: str) -> None:
        assert re.search(
            r'preflight_disk\s+"\$\{HAL0_MODELS_DISK_MIN_GB:-\d+\}"\s+"\$\{MODELS_DIR\}"',
            install_sh_text,
        )

    def test_var_dir_probe_is_unchanged(self, install_sh_text: str) -> None:
        # The original VAR_DIR probe (system reqs: venv, container images,
        # config) must still be there — this is additive, not a swap.
        assert 'preflight_disk 20 "${VAR_DIR}"' in install_sh_text

    def test_models_dir_probe_is_warn_only_not_fatal(self, install_sh_text: str) -> None:
        # Unlike the VAR_DIR probe (`|| pf_rc=$?`, aggregated into the hard
        # abort below), the models-store probe must not feed pf_rc — a
        # small/undersized store at install time (before any model is
        # picked) is a warning, not a hard stop.
        m = re.search(
            r'preflight_disk\s+"\$\{HAL0_MODELS_DISK_MIN_GB:-\d+\}"\s+"\$\{MODELS_DIR\}"\s*\\\n\s*\|\|\s*(\w+)',
            install_sh_text,
        )
        assert m is not None
        assert m.group(1) == "warn", "the model-store disk check must warn, not set pf_rc / die"

    def test_models_dir_probe_happens_within_preflight_step(self, install_sh_text: str) -> None:
        preflight_step_idx = install_sh_text.index('ui_step "Pre-flight checks"')
        filesystem_step_idx = install_sh_text.index('ui_step "Filesystem layout"')
        models_probe_idx = install_sh_text.index('preflight_disk "${HAL0_MODELS_DISK_MIN_GB:-20}"')
        assert preflight_step_idx < models_probe_idx < filesystem_step_idx


def test_bash_syntax_check() -> None:
    proc = subprocess.run(
        ["bash", "-n", str(_INSTALL_SH)], capture_output=True, text=True, check=False
    )
    assert proc.returncode == 0, proc.stderr


class TestShellcheckClean:
    def test_no_new_shellcheck_errors(self, install_sh_text: str) -> None:
        import shutil

        if shutil.which("shellcheck") is None:
            pytest.skip("shellcheck not installed")
        proc = subprocess.run(
            ["shellcheck", "--severity=error", str(_INSTALL_SH)],
            capture_output=True,
            text=True,
            check=False,
        )
        assert proc.returncode == 0, f"shellcheck reported errors:\n{proc.stdout}\n{proc.stderr}"
