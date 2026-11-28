"""A model store that came from an answers document is created without following links.

The reader checks the path, but the hal0 account may control part of it, so
after that check it could swap a component for a symlink and have a root
`mkdir -p` make directories somewhere else. For a document's value the
"Filesystem layout" step walks the path top-down through `nofollow`. The block
and the helpers are sliced out of installer/install.sh by text markers and run
under real bash with `nofollow`, `mkdir`, `info` and `warn` stubbed.
"""

from __future__ import annotations

import os
import subprocess
from pathlib import Path

INSTALL_SH = Path(__file__).resolve().parents[2] / "installer" / "install.sh"
TEXT = INSTALL_SH.read_text(encoding="utf-8")


def _slice(start: str, end: str) -> str:
    head = TEXT.index(start)
    return TEXT[head : TEXT.index(end, head)]


def _run(tmp_path: Path, models_dir: str, *, from_doc: int, fail: int = 0,
         root_uid: int | None = None, var_dir: str = "/v",
         expect_rc: int = 0) -> tuple[list[str], str]:
    """Run the step. ``root_uid`` is the uid the safe-parent rule counts as root (the
    seam HAL0_ANSWERS_TEST_ROOT_UID); it defaults to the test user so a directory
    under tmp_path looks root-owned. A ``die`` ends the run with status 1."""
    log = tmp_path / "calls.log"
    seam = os.getuid() if root_uid is None else root_uid
    script = f"""
set -euo pipefail
LOG="{log}"
PREFIX=/p ETC_DIR=/e VAR_DIR="{var_dir}" UNIT_DIR=/u
export HAL0_ANSWERS_TEST_ROOT_UID="{seam}"
MODELS_DIR="{models_dir}"
MODELS_DIR_FROM_DOC={from_doc}
{_slice("# doc-path-helpers: begin", "# doc-path-helpers: end")}
ui_step() {{ :; }}
info() {{ echo "INFO: $*"; }}
warn() {{ echo "WARN: $*"; }}
die() {{ echo "DIE: $*"; exit 1; }}
nofollow() {{ echo "nofollow $*" >>"$LOG"; return {fail}; }}
mkdir() {{ echo "mkdir $*" >>"$LOG"; }}
{_slice('ui_step "Filesystem layout"', 'info "directories under')}
echo DONE
"""
    proc = subprocess.run(["bash", "-c", script], capture_output=True, text=True, check=False)
    assert proc.returncode == expect_rc, proc.stderr
    calls = log.read_text(encoding="utf-8").splitlines() if log.exists() else []
    return calls, proc.stdout


def _mkdir_p(calls: list[str]) -> str:
    (line,) = [c for c in calls if c.startswith("mkdir -p")]
    return line


def test_a_documents_store_is_made_top_down_through_nofollow(tmp_path: Path) -> None:
    store = str(tmp_path / "a" / "b" / "models")
    calls, out = _run(tmp_path, store, from_doc=1)
    nofollow = [c for c in calls if c.startswith("nofollow")]
    assert nofollow == [
        f"nofollow dir --create --owner root:root --mode 0755 --expect root {tmp_path}/a",
        f"nofollow dir --create --owner root:root --mode 0755 --expect root {tmp_path}/a/b",
        f"nofollow dir --create --owner root:root --mode 0755 --expect root {store}",
    ]
    assert store not in _mkdir_p(calls)  # never by path name
    assert "WARN" not in out and "DONE" in out


def test_existing_components_are_not_re_moded(tmp_path: Path) -> None:
    (tmp_path / "a").mkdir()
    store = str(tmp_path / "a" / "models")
    calls, _ = _run(tmp_path, store, from_doc=1)
    assert [c for c in calls if c.startswith("nofollow")] == [
        f"nofollow dir --create --owner root:root --mode 0755 --expect root {store}"
    ]


def test_an_existing_store_needs_no_creation(tmp_path: Path) -> None:
    store = tmp_path / "models"
    store.mkdir()
    calls, out = _run(tmp_path, str(store), from_doc=1)
    assert [c for c in calls if c.startswith("nofollow")] == []
    assert str(store) not in _mkdir_p(calls) and "WARN" not in out


def _died(calls: list[str], out: str, store: str, why: str) -> None:
    """One line, naming the path and saying why; nothing was made, nothing else ran."""
    assert out.count("DIE:") == 1 and "WARN" not in out and "DONE" not in out
    assert store in out and why in out
    assert not any(c.startswith("mkdir") for c in calls), calls  # not even the other folders


def test_a_symlink_component_stops_the_install(tmp_path: Path) -> None:
    (tmp_path / "real").mkdir()
    (tmp_path / "link").symlink_to(tmp_path / "real")
    store = str(tmp_path / "link" / "deeper" / "models")
    calls, out = _run(tmp_path, store, from_doc=1, expect_rc=1)
    _died(calls, out, store, "symbolic link")
    assert not (tmp_path / "real" / "deeper").exists()


def test_a_symlink_as_the_store_itself_stops_the_install(tmp_path: Path) -> None:
    (tmp_path / "real").mkdir()
    (tmp_path / "models").symlink_to(tmp_path / "real")
    store = str(tmp_path / "models")
    calls, out = _run(tmp_path, store, from_doc=1, expect_rc=1)
    _died(calls, out, store, "is a symbolic link")


def test_a_dangling_symlink_stops_the_install(tmp_path: Path) -> None:
    (tmp_path / "models").symlink_to(tmp_path / "nowhere")
    store = str(tmp_path / "models")
    calls, out = _run(tmp_path, store, from_doc=1, expect_rc=1)
    _died(calls, out, store, "is a symbolic link")


def test_a_failed_creation_stops_the_install(tmp_path: Path) -> None:
    store = str(tmp_path / "a" / "models")
    calls, out = _run(tmp_path, store, from_doc=1, fail=1, expect_rc=1)
    assert len([c for c in calls if c.startswith("nofollow")]) == 1  # stopped at the first failure
    _died(calls, out, store, "could not be made")


def test_a_missing_store_under_a_parent_that_is_not_root_alone_stops_the_install(
        tmp_path: Path) -> None:
    store = str(tmp_path / "shared" / "models")
    (tmp_path / "shared").mkdir()
    (tmp_path / "shared").chmod(0o775)  # group-writable: the rule refuses it
    calls, out = _run(tmp_path, store, from_doc=1, expect_rc=1)
    _died(calls, out, store, "writable by a group or by others")
    assert calls == []


def test_a_missing_store_under_a_user_owned_parent_stops_the_install(tmp_path: Path) -> None:
    store = str(tmp_path / "home" / "alice" / "models")
    (tmp_path / "home" / "alice").mkdir(parents=True)
    calls, out = _run(tmp_path, store, from_doc=1, root_uid=0 if os.getuid() else 1, expect_rc=1)
    _died(calls, out, store, "is not owned by root")
    assert calls == []


def test_a_missing_store_inside_the_default_store_needs_no_safe_parent(tmp_path: Path) -> None:
    var_dir = tmp_path / "var"
    var_dir.mkdir()
    store = str(var_dir / "models" / "deeper")
    calls, out = _run(tmp_path, store, from_doc=1, root_uid=0 if os.getuid() else 1,
                      var_dir=str(var_dir))
    assert "DIE" not in out and "DONE" in out
    assert any(c.startswith("nofollow dir --create") for c in calls)


def test_an_existing_store_under_a_user_owned_parent_is_fine(tmp_path: Path) -> None:
    store = tmp_path / "home" / "alice" / "models"
    store.mkdir(parents=True)
    calls, out = _run(tmp_path, str(store), from_doc=1, root_uid=0 if os.getuid() else 1)
    assert "DIE" not in out and "DONE" in out
    assert [c for c in calls if c.startswith("nofollow")] == []


def test_the_die_line_names_the_path_once_and_prints_nothing_else_of_it(tmp_path: Path) -> None:
    (tmp_path / "link").symlink_to(tmp_path)
    store = str(tmp_path / "link" / "m")
    _, out = _run(tmp_path, store, from_doc=1, expect_rc=1)
    assert len(out.strip().splitlines()) == 1


def test_a_store_that_did_not_come_from_a_document_is_never_fatal_here(tmp_path: Path) -> None:
    (tmp_path / "link").symlink_to(tmp_path)
    store = str(tmp_path / "link" / "m")
    calls, out = _run(tmp_path, store, from_doc=0, root_uid=0 if os.getuid() else 1)
    assert "DIE" not in out and "DONE" in out
    assert store in _mkdir_p(calls)


def test_a_path_that_did_not_come_from_a_document_behaves_as_before(tmp_path: Path) -> None:
    store = str(tmp_path / "a" / "models")
    calls, out = _run(tmp_path, store, from_doc=0)
    assert [c for c in calls if c.startswith("nofollow")] == []
    assert store in _mkdir_p(calls) and "WARN" not in out


def test_the_flag_is_set_only_when_the_document_won() -> None:
    block = _slice("# answers-models-dir: begin", "# answers-models-dir: end")
    assert block.count("MODELS_DIR_FROM_DOC=1") == 1
    assert block.index("MODELS_DIR_FROM_DOC=1") > block.index("MODELS_DIR=\"${HAL0_MODELS_DIR}\"")
    assert "MODELS_DIR_FROM_DOC=0" in TEXT[: TEXT.index("# answers-models-dir: begin")]


def _seam(*, exported: list[str], flag: int, inherited: str = "doc") -> str:
    names = " ".join(f'"{n}"' for n in exported)
    script = f"""
set -u
warn() {{ :; }}
HAL0_ANSWERS_APPLIED=1
HAL0_ANSWERS_EXPORTED=({names})
export HAL0_MODELS_DIR="/doc"
MODELS_DIR="/default"
MODELS_DIR_EXPLICIT=0
MODELS_DIR_FLAG_GIVEN={flag}
MODELS_DIR_FROM_DOC=0
{_slice("# answers-models-dir: begin", "# answers-models-dir: end")}
echo "FROM_DOC=${{MODELS_DIR_FROM_DOC}} DIR=${{MODELS_DIR}}"
"""
    proc = subprocess.run(["bash", "-c", script], capture_output=True, text=True, check=False)
    assert proc.returncode == 0, proc.stderr
    return proc.stdout.strip()


def test_the_flag_follows_who_chose_the_store() -> None:
    assert _seam(exported=["HAL0_MODELS_DIR"], flag=0) == "FROM_DOC=1 DIR=/doc"
    assert _seam(exported=["HAL0_MODELS_DIR"], flag=1) == "FROM_DOC=0 DIR=/default"  # --models-dir won
    assert _seam(exported=["HAL0_NONINTERACTIVE"], flag=0) == "FROM_DOC=0 DIR=/default"  # inherited env
