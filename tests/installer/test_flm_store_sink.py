"""The FLM model directory is prepared through `nofollow`, never by path name.

``HAL0_FLM_MODELS_DIR`` may come from an answers document, and the hal0 account
may control part of the path, so root must not chown, chmod or mkdir it by name.
The block is sliced out of installer/install.sh by text markers and run under
real bash with ``nofollow``, ``chown``, ``chmod``, ``mkdir`` and ``info``/``warn``
stubbed to record their arguments (no root needed).
"""

from __future__ import annotations

import os
import subprocess
from pathlib import Path

INSTALL_SH = Path(__file__).resolve().parents[2] / "installer" / "install.sh"
TEXT = INSTALL_SH.read_text(encoding="utf-8")
_START = 'if [[ -n "${HAL0_FLM_MODELS_DIR:-}" ]]; then'
_END = "\n        fi\n"
_HELPERS_BEGIN = "# doc-path-helpers: begin"
_HELPERS_END = "# doc-path-helpers: end"


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
    root_uid: int | None = None,
    var_dir: str = "/nonexistent/var/lib/hal0",
) -> tuple[list[str], str]:
    """Run the block; return (recorded nofollow/chown/chmod/mkdir calls, stdout).

    ``root_uid`` is the uid the ancestor rule counts as root (the test seam
    HAL0_ANSWERS_TEST_ROOT_UID); it defaults to the test user so a directory
    made under ``tmp_path`` looks root-owned. Pass 0 to see a real refusal.
    """
    log = tmp_path / "calls.log"
    seam = os.getuid() if root_uid is None else root_uid
    script = f"""
set -euo pipefail
LOG="{log}"
VAR_DIR="{var_dir}"
export HAL0_ANSWERS_TEST_ROOT_UID="{seam}"
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
export HAL0_FLM_MODELS_DIR="{store}"
HAL0_ANSWERS_EXPORTED=({exported})
{_block()}
echo DONE
"""
    proc = subprocess.run(["bash", "-c", script], capture_output=True, text=True, check=False)
    assert proc.returncode == 0, proc.stderr
    calls = log.read_text(encoding="utf-8").splitlines() if log.exists() else []
    return calls, proc.stdout


def _no_path_name_commands(calls: list[str], store: str) -> None:
    for call in calls:
        if call.split()[0] in ("chown", "chmod", "mkdir"):
            raise AssertionError(f"path-name command used: {call}")


def test_missing_directory_is_created_through_nofollow(tmp_path: Path) -> None:
    store = str(tmp_path / "flm")
    calls, out = _run(tmp_path, store, exported="HAL0_FLM_MODELS_DIR")
    assert calls == [f"nofollow dir --create --owner 1000:hal0 --mode 2775 --expect adopt {store}"]
    assert "created FLM model directory" in out and "WARN" not in out


def test_missing_parents_are_made_root_owned_top_down(tmp_path: Path) -> None:
    store = str(tmp_path / "a" / "b" / "flm")
    calls, _ = _run(tmp_path, store)
    assert calls == [
        f"nofollow dir --create --owner root:root --mode 0755 --expect root {tmp_path}/a",
        f"nofollow dir --create --owner root:root --mode 0755 --expect root {tmp_path}/a/b",
        f"nofollow dir --create --owner 1000:hal0 --mode 2775 --expect adopt {store}",
    ]
    _no_path_name_commands(calls, store)


def test_existing_parents_are_not_re_moded(tmp_path: Path) -> None:
    store = str(tmp_path / "flm")
    calls, _ = _run(tmp_path, store)
    assert len(calls) == 1 and "--expect adopt" in calls[0]


def test_uid_1000_unusable_falls_back_to_hal0_hal0(tmp_path: Path) -> None:
    store = str(tmp_path / "flm")
    calls, out = _run(tmp_path, store, nofollow_fail="--owner 1000:hal0")
    assert calls == [
        f"nofollow dir --create --owner 1000:hal0 --mode 2775 --expect adopt {store}",
        f"nofollow dir --create --owner hal0:hal0 --mode 2775 --expect adopt {store}",
    ]
    assert "WARN" not in out


def test_existing_directory_from_a_document_keeps_owner_and_mode(tmp_path: Path) -> None:
    store = tmp_path / "flm"
    store.mkdir()
    calls, out = _run(tmp_path, str(store), exported="HAL0_MODELS_DIR HAL0_FLM_MODELS_DIR")
    assert calls == []
    assert out.count("INFO:") == 1 and "leaving its owner and mode as they are" in out


def test_existing_directory_from_the_environment_is_re_owned_through_nofollow(tmp_path: Path) -> None:
    store = tmp_path / "flm"
    store.mkdir()
    calls, _ = _run(tmp_path, str(store), exported="HAL0_MODELS_DIR")
    assert calls == [f"nofollow dir --owner 1000:hal0 --mode 2775 --expect adopt {store}"]
    _no_path_name_commands(calls, str(store))


def test_existing_directory_from_the_environment_falls_back_to_hal0_hal0(tmp_path: Path) -> None:
    store = tmp_path / "flm"
    store.mkdir()
    calls, _ = _run(tmp_path, str(store), nofollow_fail="--owner 1000:hal0")
    assert calls[-1] == f"nofollow dir --owner hal0:hal0 --mode 2775 --expect adopt {store}"


def test_a_symlink_at_the_path_is_skipped_for_a_document_and_left_to_nofollow_otherwise(tmp_path: Path) -> None:
    link = tmp_path / "flm"
    link.symlink_to("/etc")
    calls, out = _run(tmp_path, str(link), exported="HAL0_FLM_MODELS_DIR")
    assert calls == []  # from a document: untouched, nothing followed it, one warning
    assert out.count("WARN:") == 1 and "is a symbolic link" in out and "DONE" in out
    calls, _ = _run(tmp_path, str(link))
    assert calls == [f"nofollow dir --owner 1000:hal0 --mode 2775 --expect adopt {link}"]


def test_a_refusal_warns_and_does_not_fail(tmp_path: Path) -> None:
    store = str(tmp_path / "flm")
    _, out = _run(tmp_path, store, nofollow_fail="dir ")
    assert out.count("WARN:") == 1 and "DONE" in out


def test_the_comment_no_longer_calls_the_path_trusted() -> None:
    assert "Operator override from root's own environment" not in _block()
    assert "is NOT always a trusted path" in _block()


# ── a document's path is created only under a root-owned, non-writable parent ──
#
# Tests cannot make root-owned directories, so the ancestor rule's notion of
# "root" is the seam HAL0_ANSWERS_TEST_ROOT_UID: 0 (the default, which this
# user is not) shows a refusal, this user's uid shows the "root-owned" case.


def _refused(calls: list[str], out: str, store: str) -> None:
    assert calls == [], calls  # nothing created, nothing re-owned
    assert out.count("WARN:") == 1 and "DONE" in out
    assert "was not created" in out and "mkdir -p" in out
    assert store.replace(" ", "\\ ") in out


def _alice(tmp_path: Path) -> Path:
    home = tmp_path / "home" / "alice"
    (home / ".config").mkdir(parents=True)
    return home


def test_other_peoples_directories_are_not_made_group_writable(tmp_path: Path) -> None:
    # The three places a user's own login runs things from.
    home = _alice(tmp_path)
    for rel in (".config/systemd/user", ".local/bin", ".config/autostart"):
        store = str(home / rel)
        calls, out = _run(tmp_path, store, exported="HAL0_FLM_MODELS_DIR", root_uid=0)
        _refused(calls, out, store)


def test_a_group_writable_ancestor_is_refused(tmp_path: Path) -> None:
    parent = tmp_path / "shared"
    parent.mkdir()
    parent.chmod(0o775)
    store = str(parent / "sub" / "flm")
    calls, out = _run(tmp_path, store, exported="HAL0_FLM_MODELS_DIR")
    _refused(calls, out, store)


def test_a_sticky_world_writable_ancestor_is_refused(tmp_path: Path) -> None:
    parent = tmp_path / "sticky"
    parent.mkdir()
    parent.chmod(0o1777)
    store = str(parent / "flm")
    calls, out = _run(tmp_path, store, exported="HAL0_FLM_MODELS_DIR")
    _refused(calls, out, store)


def test_a_link_on_the_way_is_refused(tmp_path: Path) -> None:
    real = tmp_path / "real"
    real.mkdir()
    (tmp_path / "link").symlink_to(real)
    store = str(tmp_path / "link" / "flm")
    calls, out = _run(tmp_path, store, exported="HAL0_FLM_MODELS_DIR")
    _refused(calls, out, store)


def test_a_file_on_the_way_is_refused(tmp_path: Path) -> None:
    (tmp_path / "afile").write_text("x")
    store = str(tmp_path / "afile" / "flm")
    calls, out = _run(tmp_path, store, exported="HAL0_FLM_MODELS_DIR")
    _refused(calls, out, store)


def test_a_path_inside_the_default_store_is_created_whoever_owns_the_parent(tmp_path: Path) -> None:
    var_dir = tmp_path / "var" / "lib" / "hal0"
    var_dir.mkdir(parents=True)
    for rel in ("models", "models/flm"):
        store = str(var_dir / rel)
        calls, out = _run(
            tmp_path, store, exported="HAL0_FLM_MODELS_DIR", root_uid=0, var_dir=str(var_dir)
        )
        assert calls[-1] == f"nofollow dir --create --owner 1000:hal0 --mode 2775 --expect adopt {store}"
        assert "WARN" not in out
        (tmp_path / "calls.log").unlink()


def test_a_look_alike_of_the_default_store_is_not_the_default_store(tmp_path: Path) -> None:
    var_dir = tmp_path / "var"
    var_dir.mkdir()
    store = str(var_dir / "modelsx" / "flm")
    calls, out = _run(tmp_path, store, exported="HAL0_FLM_MODELS_DIR", root_uid=0, var_dir=str(var_dir))
    _refused(calls, out, store)


def test_an_ordinary_path_under_a_root_owned_ancestor_is_created(tmp_path: Path) -> None:
    store = str(tmp_path / "data" / "flm")
    calls, out = _run(tmp_path, store, exported="HAL0_FLM_MODELS_DIR")
    assert calls == [
        f"nofollow dir --create --owner root:root --mode 0755 --expect root {tmp_path}/data",
        f"nofollow dir --create --owner 1000:hal0 --mode 2775 --expect adopt {store}",
    ]
    assert "WARN" not in out


def test_the_rule_looks_at_the_deepest_existing_ancestor_only(tmp_path: Path) -> None:
    # tmp_path is "root-owned" and 0755 by the seam; the missing levels below it are fine.
    store = str(tmp_path / "a" / "b" / "c" / "flm")
    calls, _ = _run(tmp_path, store, exported="HAL0_FLM_MODELS_DIR")
    assert len(calls) == 4 and "--expect adopt" in calls[-1]


def test_a_value_from_the_environment_is_not_subject_to_the_rule(tmp_path: Path) -> None:
    store = str(_alice(tmp_path) / ".config" / "flm")
    calls, out = _run(tmp_path, store, root_uid=0)  # not exported by a document
    assert calls and "--expect adopt" in calls[-1] and "WARN" not in out


def test_an_existing_directory_from_a_document_is_left_alone_whoever_owns_it(tmp_path: Path) -> None:
    store = _alice(tmp_path) / ".config" / "systemd" / "user"
    store.mkdir(parents=True)
    calls, out = _run(tmp_path, str(store), exported="HAL0_FLM_MODELS_DIR", root_uid=0)
    assert calls == [] and "leaving its owner and mode as they are" in out


def test_the_seam_is_a_test_only_name_that_a_document_cannot_set() -> None:
    reader = INSTALL_SH.parent / "lib" / "answers_reader.py"
    text = reader.read_text(encoding="utf-8")
    allowed = text[text.index("ALLOWED_NAMES = ("): text.index("MAX_BYTES")]
    assert "HAL0_ANSWERS_TEST_ROOT_UID" not in allowed
    assert "HAL0_ANSWERS_TEST_ROOT_UID" in _helpers()


def test_no_sink_here_re_owns_whatever_it_finds_at_the_name(tmp_path: Path) -> None:
    # `--expect any` re-owns a root-owned directory the hal0 account renamed into
    # the operator's path; `adopt` takes only what hal0 owns or an empty new one.
    assert "--expect any" not in _block()


def test_a_refused_folder_from_a_document_never_stops_the_install(tmp_path: Path) -> None:
    # the NPU is optional: unlike the model store, no `die` here (the harness has
    # no die(); calling one would end the run with status 127)
    store = str(tmp_path / "shared" / "flm")
    (tmp_path / "shared").mkdir()
    (tmp_path / "shared").chmod(0o775)
    _, out = _run(tmp_path, store, exported="HAL0_FLM_MODELS_DIR")
    assert out.count("WARN:") == 1 and "DONE" in out
    assert "die" not in _block()
