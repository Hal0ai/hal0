"""The FLM model directory is prepared through `nofollow`, never by path name.

``HAL0_FLM_MODELS_DIR`` may come from root's environment or from an answers
document, and the hal0 account may control part of the path, so root must not
chown, chmod or mkdir it by name. A document's folder must lie inside the model
store (the reader required it; the sink checks it again by text) and is only
ever created, never re-owned. The block and its helpers are sliced out of
installer/install.sh by text markers and run under real bash with
``nofollow``, ``chown``, ``chmod``, ``mkdir``, ``stat`` and ``info``/``warn``
stubbed to record their arguments (no root needed).
"""

from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

INSTALL_SH = Path(__file__).resolve().parents[2] / "installer" / "install.sh"
TEXT = INSTALL_SH.read_text(encoding="utf-8")
_START = 'if [[ -n "${HAL0_FLM_MODELS_DIR:-}" ]]; then'
_END = "\n        fi\n"
_HELPERS_BEGIN = "# doc-path-helpers: begin"
_HELPERS_END = "# doc-path-helpers: end"

DOC = "HAL0_FLM_MODELS_DIR"
LEAF_1000 = "nofollow dir --create --owner 1000:hal0 --mode 2775 --expect adopt"
LEAF_HAL0 = "nofollow dir --create --owner hal0:hal0 --mode 2775 --expect adopt"
PARENT = "nofollow dir --create --owner root:root --mode 0755 --expect root"


def _helpers() -> str:
    head = TEXT.index(_HELPERS_BEGIN)
    return TEXT[head : TEXT.index(_HELPERS_END, head)]


def _block() -> str:
    head = TEXT.index(_START)
    return TEXT[head : TEXT.index(_END, head) + len(_END)]


def _run(
    tmp_path: Path,
    store: str,
    *,
    exported: str = "",
    nofollow_fail: str = "",
    models_dir: str | None = None,
) -> tuple[list[str], str]:
    """Run the block; return (recorded nofollow/chown/chmod/mkdir/stat calls, stdout).

    ``models_dir`` is MODELS_DIR, the model store; by default the folder that
    holds ``store``, so a document's NPU folder is inside it.
    """
    log = tmp_path / "calls.log"
    models = models_dir if models_dir is not None else str(Path(store).parent)
    script = f"""
set -euo pipefail
LOG="{log}"
VAR_DIR=/nonexistent/var/lib/hal0
MODELS_DIR="{models}"
{_helpers()}
info() {{ echo "INFO: $*"; }}
warn() {{ echo "WARN: $*"; }}
nofollow() {{
    echo "nofollow $*" >>"$LOG"
    [[ -n "{nofollow_fail}" && "$*" == *"{nofollow_fail}"* ]] && return 1
    return 0
}}
chown() {{ echo "chown $*" >>"$LOG"; }}
chmod() {{ echo "chmod $*" >>"$LOG"; }}
mkdir() {{ echo "mkdir $*" >>"$LOG"; }}
stat() {{ echo "stat $*" >>"$LOG"; return 1; }}
export HAL0_FLM_MODELS_DIR="{store}"
HAL0_ANSWERS_EXPORTED=({exported})
{_block()}
echo DONE
"""
    proc = subprocess.run(["bash", "-c", script], capture_output=True, text=True, check=False)
    assert proc.returncode == 0, proc.stderr
    calls = log.read_text(encoding="utf-8").splitlines() if log.exists() else []
    return calls, proc.stdout


def _no_path_name_commands(calls: list[str]) -> None:
    for call in calls:
        if call.split()[0] in ("chown", "chmod", "mkdir"):
            raise AssertionError(f"path-name command used: {call}")


# ── a value from root's environment: today's rules ──────────────────────────


def test_missing_directory_from_the_environment_is_created_through_nofollow(tmp_path: Path) -> None:
    store = str(tmp_path / "flm")
    calls, out = _run(tmp_path, store)
    assert calls == [f"{LEAF_1000} {store}"]
    assert "created FLM model directory" in out and "WARN" not in out


def test_missing_parents_are_made_root_owned_top_down(tmp_path: Path) -> None:
    store = str(tmp_path / "a" / "b" / "flm")
    calls, _ = _run(tmp_path, store)
    assert calls == [
        f"{PARENT} {tmp_path}/a",
        f"{PARENT} {tmp_path}/a/b",
        f"{LEAF_1000} {store}",
    ]
    _no_path_name_commands(calls)


def test_uid_1000_unusable_falls_back_to_hal0_hal0(tmp_path: Path) -> None:
    store = str(tmp_path / "flm")
    calls, out = _run(tmp_path, store, nofollow_fail="--owner 1000:hal0")
    assert calls == [f"{LEAF_1000} {store}", f"{LEAF_HAL0} {store}"]
    assert "WARN" not in out


def test_existing_directory_from_the_environment_is_re_owned_through_nofollow(tmp_path: Path) -> None:
    store = tmp_path / "flm"
    store.mkdir()
    calls, _ = _run(tmp_path, str(store), exported="HAL0_MODELS_DIR")
    assert calls == [f"nofollow dir --owner 1000:hal0 --mode 2775 --expect adopt {store}"]
    _no_path_name_commands(calls)


def test_existing_directory_from_the_environment_falls_back_to_hal0_hal0(tmp_path: Path) -> None:
    store = tmp_path / "flm"
    store.mkdir()
    calls, _ = _run(tmp_path, str(store), nofollow_fail="--owner 1000:hal0")
    assert calls[-1] == f"nofollow dir --owner hal0:hal0 --mode 2775 --expect adopt {store}"


def test_a_symlink_from_the_environment_is_left_to_nofollow(tmp_path: Path) -> None:
    link = tmp_path / "flm"
    link.symlink_to("/etc")
    calls, _ = _run(tmp_path, str(link))
    assert calls == [f"nofollow dir --owner 1000:hal0 --mode 2775 --expect adopt {link}"]


def test_a_value_from_the_environment_may_be_outside_the_store(tmp_path: Path) -> None:
    store = f"{tmp_path}/home/alice/.config/flm"
    calls, out = _run(tmp_path, store, models_dir="/somewhere/else")
    assert calls[-1] == f"{LEAF_1000} {store}" and "WARN" not in out


def test_a_refusal_warns_and_does_not_fail(tmp_path: Path) -> None:
    store = str(tmp_path / "flm")
    _, out = _run(tmp_path, store, nofollow_fail="dir ")
    assert out.count("WARN:") == 1 and "DONE" in out


def test_the_comment_no_longer_calls_the_path_trusted() -> None:
    assert "Operator override from root's own environment" not in _block()
    assert "is NOT always a trusted path" in _block()


# ── a document's NPU folder: only inside the model store, only ever created ──


def test_a_document_folder_inside_the_store_is_created_through_nofollow(tmp_path: Path) -> None:
    (tmp_path / "models").mkdir()
    store = f"{tmp_path}/models/npu/a/flm"
    calls, out = _run(tmp_path, store, exported=DOC, models_dir=f"{tmp_path}/models")
    assert calls == [
        f"{PARENT} {tmp_path}/models/npu {tmp_path}/models/npu/a",
        f"{LEAF_1000} {store}",
    ]
    assert "created FLM model directory" in out and "WARN" not in out


def test_existing_folders_between_the_store_and_the_npu_folder_are_not_re_moded(
        tmp_path: Path) -> None:
    (tmp_path / "models" / "npu").mkdir(parents=True)
    store = f"{tmp_path}/models/npu/flm"
    calls, _ = _run(tmp_path, store, exported=DOC, models_dir=f"{tmp_path}/models/")
    assert calls == [f"{LEAF_1000} {store}"]


def test_a_document_folder_falls_back_to_hal0_hal0(tmp_path: Path) -> None:
    store = f"{tmp_path}/flm"
    calls, out = _run(tmp_path, store, exported=DOC, nofollow_fail="--owner 1000:hal0")
    assert calls == [f"{LEAF_1000} {store}", f"{LEAF_HAL0} {store}"]
    assert "WARN" not in out


@pytest.mark.parametrize("models_dir", ["{t}/other", "{t}/flm", "{t}/fl", "/", "", "{t}/flm/x",
                                        "relative"])
def test_a_document_folder_outside_the_store_is_skipped_with_one_warning(tmp_path: Path,
                                                                         models_dir: str) -> None:
    store = f"{tmp_path}/flm"
    calls, out = _run(tmp_path, store, exported=DOC, models_dir=models_dir.format(t=tmp_path))
    assert calls == []
    assert out.count("WARN:") == 1 and "is not inside the model store" in out
    assert "set HAL0_FLM_MODELS_DIR when running the installer" in out and "DONE" in out


def test_an_existing_document_folder_is_left_exactly_as_it_is(tmp_path: Path) -> None:
    for owner_dir in ("flm", "home/alice/.config/systemd/user"):
        (tmp_path / owner_dir).mkdir(parents=True)
        store = f"{tmp_path}/{owner_dir}"
        calls, out = _run(tmp_path, store, exported=DOC)
        assert calls == [] and out.count("INFO:") == 1
        assert "leaving its owner and mode as they are" in out


def test_a_document_folder_that_is_a_link_is_left_alone(tmp_path: Path) -> None:
    for target in ("/etc", f"{tmp_path}/nowhere"):
        link = tmp_path / "flm"
        link.symlink_to(target)
        calls, out = _run(tmp_path, str(link), exported=DOC)
        assert calls == []
        assert out.count("WARN:") == 1 and "is a symbolic link" in out and "DONE" in out
        link.unlink()


def test_a_refused_parent_skips_the_folder_with_one_warning(tmp_path: Path) -> None:
    store = f"{tmp_path}/a/flm"
    calls, out = _run(tmp_path, store, exported=DOC, models_dir=str(tmp_path),
                      nofollow_fail="--expect root")
    assert calls == [f"{PARENT} {tmp_path}/a"]  # the folder itself is not attempted
    assert out.count("WARN:") == 1 and "could not create" in out and "DONE" in out


def test_a_refused_folder_warns_and_never_stops_the_install(tmp_path: Path) -> None:
    # the NPU is optional: unlike the model store, no `die` here (the harness has
    # no die(); calling one would end the run with status 127)
    store = f"{tmp_path}/flm"
    calls, out = _run(tmp_path, store, exported=DOC, nofollow_fail="dir ")
    assert calls == [f"{LEAF_1000} {store}", f"{LEAF_HAL0} {store}"]
    assert out.count("WARN:") == 1 and "DONE" in out
    assert "die" not in _block()
    flm_helper = _helpers()[_helpers().index("hal0_doc_flm_prepare() {"):]
    assert "die " not in flm_helper


def test_a_document_folder_never_looks_at_owners_or_re_owns_anything(tmp_path: Path) -> None:
    (tmp_path / "flm").mkdir()
    for store in (f"{tmp_path}/flm", f"{tmp_path}/new"):
        calls, _ = _run(tmp_path, store, exported=DOC)
        assert not any(c.split()[0] in ("stat", "chown", "chmod", "mkdir") for c in calls), calls
        assert all("--create" in c for c in calls if c.startswith("nofollow")), calls
        (tmp_path / "calls.log").unlink(missing_ok=True)


def test_no_sink_here_re_owns_whatever_it_finds_at_the_name() -> None:
    # `--expect any` re-owns a root-owned directory the hal0 account renamed into
    # the operator's path; `adopt` takes only what hal0 owns or an empty new one.
    assert "--expect any" not in _block()
    assert "--expect any" not in _helpers()


def test_no_root_uid_seam_is_left() -> None:
    assert "HAL0_ANSWERS_TEST_ROOT_UID" not in TEXT
    assert "HAL0_INSTALL_TEST_ROOT_UID" not in TEXT
