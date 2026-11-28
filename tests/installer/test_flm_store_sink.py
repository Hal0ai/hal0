"""The FLM model directory is prepared through `nofollow`, never by path name.

``HAL0_FLM_MODELS_DIR`` may come from an answers document, and the hal0 account
may control part of the path, so root must not chown, chmod or mkdir it by name.
The block is sliced out of installer/install.sh by text markers and run under
real bash with ``nofollow``, ``chown``, ``chmod``, ``mkdir`` and ``info``/``warn``
stubbed to record their arguments (no root needed).
"""

from __future__ import annotations

import subprocess
from pathlib import Path

INSTALL_SH = Path(__file__).resolve().parents[2] / "installer" / "install.sh"
TEXT = INSTALL_SH.read_text(encoding="utf-8")
_START = 'if [[ -n "${HAL0_FLM_MODELS_DIR:-}" ]]; then'
_END = "\n        fi\n"


def _block() -> str:
    head = TEXT.index(_START)
    return TEXT[head : TEXT.index(_END, head) + len(_END)]


def _run(tmp_path: Path, store: str, *, exported: str = "", nofollow_fail: str = "") -> tuple[list[str], str]:
    """Run the block; return (recorded nofollow/chown/chmod/mkdir calls, stdout)."""
    log = tmp_path / "calls.log"
    script = f"""
set -euo pipefail
LOG="{log}"
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
    assert calls == [f"nofollow dir --owner 1000:hal0 --mode 2775 --expect any {store}"]
    _no_path_name_commands(calls, str(store))


def test_existing_directory_from_the_environment_falls_back_to_hal0_hal0(tmp_path: Path) -> None:
    store = tmp_path / "flm"
    store.mkdir()
    calls, _ = _run(tmp_path, str(store), nofollow_fail="--owner 1000:hal0")
    assert calls[-1] == f"nofollow dir --owner hal0:hal0 --mode 2775 --expect any {store}"


def test_a_symlink_at_the_path_counts_as_existing_and_is_left_to_nofollow(tmp_path: Path) -> None:
    link = tmp_path / "flm"
    link.symlink_to("/etc")
    calls, _ = _run(tmp_path, str(link), exported="HAL0_FLM_MODELS_DIR")
    assert calls == []  # from a document and "exists": untouched, and nothing followed it
    calls, _ = _run(tmp_path, str(link))
    assert calls == [f"nofollow dir --owner 1000:hal0 --mode 2775 --expect any {link}"]


def test_a_refusal_warns_and_does_not_fail(tmp_path: Path) -> None:
    store = str(tmp_path / "flm")
    _, out = _run(tmp_path, store, nofollow_fail="dir ")
    assert out.count("WARN:") == 1 and "DONE" in out


def test_the_comment_no_longer_calls_the_path_trusted() -> None:
    assert "Operator override from root's own environment" not in _block()
    assert "is NOT always a trusted path" in _block()
