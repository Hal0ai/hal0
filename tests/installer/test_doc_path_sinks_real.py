"""The two document-path sinks against the real `nofollow` helper, no stubs.

Tests cannot run as root. src/hal0/security/nofollow.py trusts directories
owned by root or by the uid running it (``_trusted_uids``), and install.sh's
sinks do the same (``_hal0_trusted_owner``), so here our own uid stands in for
root: a folder we own with no group or other write bit is "root-only", and a
group-writable folder (0775) stands in for one the hal0 account controls. The
layout is built in a fresh folder under the home directory, because every
folder above it must itself be "root-only" for the helper to trust it (the
system temp folder is 1777). The test is skipped when the home directory does
not qualify.

The only thing replaced is ``--owner``: it is dropped before the real helper
runs, because the hal0 group may not exist here, and a non-root helper never
re-owns anyway.
"""

from __future__ import annotations

import os
import shutil
import stat
import subprocess
import sys
import tempfile
from collections.abc import Iterator
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[2]
TEXT = (REPO / "installer" / "install.sh").read_text(encoding="utf-8")


def _helpers() -> str:
    head = TEXT.index("# doc-path-helpers: begin")
    return TEXT[head : TEXT.index("# doc-path-helpers: end", head)]


def _trusted_chain(path: Path) -> bool:
    me = os.geteuid()
    for p in (path, *path.parents):
        st = os.lstat(p)
        if stat.S_ISLNK(st.st_mode) or not stat.S_ISDIR(st.st_mode):
            return False
        if st.st_uid not in (0, me) or st.st_mode & 0o022:
            return False
    return True


@pytest.fixture
def base() -> Iterator[Path]:
    home = Path.home()
    if os.geteuid() == 0 or not home.is_dir() or not _trusted_chain(home):
        pytest.skip("needs a non-root user whose home and its parents only they or root can change")
    made = Path(tempfile.mkdtemp(prefix=".hal0-sink-test-", dir=home))
    try:
        yield made
    finally:
        shutil.rmtree(made, ignore_errors=True)


def _dir(path: Path, mode: int = 0o755) -> Path:
    path.mkdir(parents=True, exist_ok=True)
    path.chmod(mode)
    return path


def _run(body: str, *, plant: str = "", chosen: str = "") -> subprocess.CompletedProcess[str]:
    """Run ``body`` with the real helpers; ``plant`` runs once, right before the first `nofollow`."""
    script = f"""
set -euo pipefail
PY="{sys.executable}"
REPO_ROOT="{REPO}"
DEV_MODE=0
PLANTED=0
info() {{ echo "INFO: $*"; }}
warn() {{ echo "WARN: $*"; }}
id() {{ if [[ "$*" == "-u hal0" ]]; then return 1; fi; command id "$@"; }}
nofollow() {{
    local -a a=()
    while (( $# )); do
        if [[ "$1" == --owner ]]; then shift 2; continue; fi
        a+=("$1"); shift
    done
    if [[ "${{PLANTED}}" -eq 0 ]]; then PLANTED=1; {plant or ":"}; fi
    "${{PY}}" -I "${{REPO_ROOT}}/src/hal0/security/nofollow.py" "${{a[@]}}"
}}
{_helpers()}
source "{REPO}/installer/lib/answers.sh"
_HAL0_WIZARD_CHOSEN_STORE="{chosen}"
{body}
"""
    return subprocess.run(["bash", "-c", script], capture_output=True, text=True, check=False)


def _review_layout(base: Path) -> tuple[Path, Path, Path]:
    """etc/systemd/system (root-only), var/lib/hal0 (hal0's), data/models -> var/lib/hal0/models."""
    system = _dir(base / "etc" / "systemd" / "system")
    var_dir = _dir(base / "var" / "lib" / "hal0", 0o775)
    _dir(var_dir / "models", 0o2775)
    _dir(base / "data")
    (base / "data" / "models").symlink_to(var_dir / "models")
    return system, var_dir, base / "data" / "models"


def test_the_reviews_data_models_attack_is_refused_by_both_sinks(base: Path) -> None:
    system, var_dir, data_models = _review_layout(base)
    # 1. the store sink refuses the document's store: a link is on the path
    proc = _run(
        f'hal0_doc_store_prepare "{data_models}" "{var_dir}/models" && echo ACCEPTED'
        ' || echo "REFUSED: ${_HAL0_STORE_WHY}"'
    )
    assert f"REFUSED: a link is on the path ({data_models} is a symbolic link)" in proc.stdout, (
        proc.stdout + proc.stderr
    )
    # 2. the hal0 account swaps its models folder for a link to the systemd folder
    (var_dir / "models").rmdir()
    (var_dir / "models").symlink_to(system)
    # 3. the NPU sink, run on its own, refuses too, and nothing lands in etc/systemd/system
    flm = data_models / "sshd.service.d"
    proc = _run(f'hal0_doc_flm_prepare "{flm}" "{data_models}" "{var_dir}/models"; echo DONE')
    assert "DONE" in proc.stdout, proc.stdout + proc.stderr
    assert proc.stdout.count("WARN:") == 1 and "is not one you chose" in proc.stdout
    assert not (system / "sshd.service.d").exists()
    assert sorted(system.iterdir()) == []
    # 4. even had the person chosen data/models on the screens, a link in front
    # of the NPU folder is refused: the kernel would follow its target through
    # the hal0 account's folder (the helper alone does not stop that)
    proc = _run(
        f'hal0_doc_flm_prepare "{flm}" "{data_models}" "{var_dir}/models"; echo DONE',
        chosen=str(data_models),
    )
    assert "DONE" in proc.stdout and proc.stdout.count("WARN:") == 1, proc.stdout + proc.stderr
    assert f"a link is on the path ({data_models} is a symbolic link)" in proc.stdout
    assert sorted(system.iterdir()) == []


def test_the_npu_folder_inside_the_default_store_is_created(base: Path) -> None:
    var_dir = _dir(base / "var" / "lib" / "hal0", 0o775)
    store = _dir(var_dir / "models", 0o2775)
    flm = store / "npu" / "flm"
    proc = _run(f'hal0_doc_flm_prepare "{flm}" "{store}" "{store}"; echo DONE')
    assert "created FLM model directory" in proc.stdout and "WARN" not in proc.stdout, (
        proc.stdout + proc.stderr
    )
    assert stat.S_IMODE(os.lstat(flm).st_mode) == 0o2775
    assert stat.S_IMODE(os.lstat(flm.parent).st_mode) == 0o755


def test_a_link_planted_in_the_default_store_after_the_check_is_refused_by_the_helper(
    base: Path,
) -> None:
    # the window between the shell's walk and the helper: the hal0 account plants
    # npu -> etc/systemd/system; the helper opens it O_NOFOLLOW (its folder is not
    # root-only) and refuses, so nothing is created in the systemd folder
    system = _dir(base / "etc" / "systemd" / "system")
    var_dir = _dir(base / "var" / "lib" / "hal0", 0o775)
    store = _dir(var_dir / "models", 0o2775)
    flm = store / "npu" / "flm"
    proc = _run(
        f'hal0_doc_flm_prepare "{flm}" "{store}" "{store}"; echo DONE',
        plant=f'ln -s "{system}" "{store}/npu"',
    )
    assert "DONE" in proc.stdout and proc.stdout.count("WARN:") == 1, proc.stdout + proc.stderr
    assert sorted(system.iterdir()) == []


def test_the_npu_folder_under_root_only_real_folders_needs_the_operators_choice(base: Path) -> None:
    store = _dir(base / "data" / "store")
    flm = store / "npu" / "flm"
    body = f'hal0_doc_flm_prepare "{flm}" "{store}" "{base}/var/lib/hal0/models"; echo DONE'
    proc = _run(body)
    assert proc.stdout.count("WARN:") == 1 and "is not one you chose" in proc.stdout
    assert not flm.exists() and not flm.parent.exists()
    proc = _run(body, chosen=str(store))
    assert "created FLM model directory" in proc.stdout and "WARN" not in proc.stdout, (
        proc.stdout + proc.stderr
    )
    assert stat.S_IMODE(os.lstat(flm).st_mode) == 0o2775
    assert stat.S_IMODE(os.lstat(flm.parent).st_mode) == 0o755


def test_the_npu_folder_under_a_writable_folder_is_skipped(base: Path) -> None:
    store = _dir(base / "data" / "store", 0o775)
    flm = store / "flm"
    proc = _run(f'hal0_doc_flm_prepare "{flm}" "{store}" "{base}/var/lib/hal0/models"; echo DONE')
    assert proc.stdout.count("WARN:") == 1 and "DONE" in proc.stdout
    assert "is not one you chose" in proc.stdout
    assert not flm.exists()


def test_a_link_between_root_only_real_folders_is_refused(base: Path) -> None:
    real = _dir(base / "real")
    (base / "data").symlink_to(real)
    proc = _run(
        f'hal0_doc_store_prepare "{base}/data/m" "{base}/v/models" || echo "REFUSED: ${{_HAL0_STORE_WHY}}"'
    )
    assert f"a link is on the path ({base}/data is a symbolic link)" in proc.stdout, (
        proc.stdout + proc.stderr
    )
    proc = _run(f'hal0_doc_flm_prepare "{base}/data/flm" "{base}/data" "{base}/v/models"')
    assert "is not one you chose" in proc.stdout, proc.stdout + proc.stderr
    assert sorted(real.iterdir()) == []


@pytest.mark.parametrize("mode", [0o1777, 0o2775, 0o775])
def test_an_existing_store_others_can_write_is_refused(base: Path, mode: int) -> None:
    shared = _dir(base / "shared", mode)
    proc = _run(
        f'hal0_doc_store_prepare "{shared}" "{base}/v/models" && echo ACCEPTED'
        ' || echo "REFUSED: ${_HAL0_STORE_WHY}"'
    )
    assert "REFUSED: it already exists and is not a folder only root can change" in proc.stdout, (
        proc.stdout + proc.stderr
    )


def test_an_existing_root_only_store_is_accepted_and_missing_ones_are_made(base: Path) -> None:
    _dir(base / "ok")
    proc = _run(f'hal0_doc_store_prepare "{base}/ok" "{base}/v/models" && echo ACCEPTED')
    assert "ACCEPTED" in proc.stdout, proc.stdout + proc.stderr
    proc = _run(f'hal0_doc_store_prepare "{base}/new/models" "{base}/v/models" && echo ACCEPTED')
    assert "ACCEPTED" in proc.stdout, proc.stdout + proc.stderr
    assert stat.S_IMODE(os.lstat(base / "new" / "models").st_mode) == 0o755


def test_a_store_inside_the_default_store_is_made_through_the_helper(base: Path) -> None:
    var_dir = _dir(base / "var" / "lib" / "hal0", 0o775)
    store = _dir(var_dir / "models", 0o2775)
    proc = _run(f'hal0_doc_store_prepare "{store}/a/b" "{store}" && echo ACCEPTED')
    assert "ACCEPTED" in proc.stdout, proc.stdout + proc.stderr
    assert stat.S_IMODE(os.lstat(store / "a" / "b").st_mode) == 0o755  # no hal0 account here
    # the hal0 account plants h/c -> etc after the walk, in a folder of its own
    # (2775 stands for hal0:hal0 2775): the helper refuses it
    system = _dir(base / "etc")
    _dir(store / "h", 0o2775)
    proc = _run(
        f'hal0_doc_store_prepare "{store}/h/c/d" "{store}" && echo ACCEPTED'
        ' || echo "REFUSED: ${_HAL0_STORE_WHY}"',
        plant=f'ln -s "{system}" "{store}/h/c"',
    )
    assert "REFUSED" in proc.stdout and "could not be created safely" in proc.stdout, (
        proc.stdout + proc.stderr
    )
    assert sorted(system.iterdir()) == []
