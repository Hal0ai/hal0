"""The FLM model directory is prepared through `nofollow`, never by path name.

``HAL0_FLM_MODELS_DIR`` may come from root's environment or from an answers
document, and the hal0 account may control part of the path, so root must not
chown, chmod or mkdir it by name. A document's folder must lie inside the model
store (the reader required it; the sink checks it again by text) and is only
ever created, never re-owned. It is created only inside the default store
(after the sink itself walks the path from /: no component may be a symbolic
link), or inside a store that is the operator's own choice (chosen on the setup
screens, or the box's existing store), the way a folder from the environment
is made. Anywhere else: one warning, nothing made, and the variable unset. The block and its helpers are sliced out of
installer/install.sh by text markers and run under real bash with
``nofollow``, ``chown``, ``chmod``, ``mkdir``, ``stat``, ``id`` and
``info``/``warn`` stubbed to record their arguments (no root needed); ``stat``
reports the real entry's type with an owner and mode from a table (root 0755
unless the test says otherwise).
"""

from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

INSTALL_SH = Path(__file__).resolve().parents[2] / "installer" / "install.sh"
ANSWERS_SH = INSTALL_SH.parent / "lib" / "answers.sh"
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
    var_dir: str = "/nonexistent/var/lib/hal0",
    owners: dict[str, str] | None = None,
    chosen: str = "",
) -> tuple[list[str], str]:
    """Run the block; return (recorded nofollow/chown/chmod/mkdir/stat calls, stdout).

    ``models_dir`` is MODELS_DIR, the model store; by default the folder that
    holds ``store``, so a document's NPU folder is inside it.
    """
    log = tmp_path / "calls.log"
    models = models_dir if models_dir is not None else str(Path(store).parent)
    table = " ".join(f'["{path}"]="{value}"' for path, value in (owners or {}).items())
    script = f"""
set -euo pipefail
LOG="{log}"
VAR_DIR="{var_dir}"
MODELS_DIR="{models}"
declare -A OWN=({table})
{_helpers()}
source "{ANSWERS_SH}"
_HAL0_WIZARD_CHOSEN_STORE="{chosen}"
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
stat() {{
    echo "stat $*" >>"$LOG"
    local p="${{@: -1}}" v t
    [[ "$*" == "-c %f %u -- $p" ]] || return 9
    v="${{OWN[$p]-0 755}}"
    [[ -n "$v" ]] || return 1
    if [[ -L "$p" ]]; then t=8#120000
    elif [[ -d "$p" ]]; then t=8#040000
    elif [[ -e "$p" ]]; then t=8#100000
    else return 1; fi
    printf '%x %s\n' $(( t | 8#${{v#* }} )) "${{v%% *}}"
}}
id() {{
    if [[ "$*" == "-u" ]]; then echo 0
    elif [[ "$*" == "-u hal0" ]]; then echo 990
    else return 1; fi
}}
export HAL0_FLM_MODELS_DIR="{store}"
HAL0_ANSWERS_EXPORTED=({exported})
{_block()}
echo "FLM=${{HAL0_FLM_MODELS_DIR-unset}}"
echo DONE
"""
    proc = subprocess.run(["bash", "-c", script], capture_output=True, text=True, check=False)
    assert proc.returncode == 0, proc.stderr
    calls = log.read_text(encoding="utf-8").splitlines() if log.exists() else []
    return calls, proc.stdout


def _nofollow(calls: list[str]) -> list[str]:
    return [c for c in calls if c.startswith("nofollow")]


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


def test_existing_directory_from_the_environment_is_re_owned_through_nofollow(
    tmp_path: Path,
) -> None:
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
    calls, out = _run(
        tmp_path, store, exported=DOC, models_dir=f"{tmp_path}/models", chosen=f"{tmp_path}/models"
    )
    assert _nofollow(calls) == [
        f"{PARENT} {tmp_path}/models/npu {tmp_path}/models/npu/a",
        f"{LEAF_1000} {store}",
    ]
    assert "created FLM model directory" in out and "WARN" not in out
    _no_path_name_commands(calls)


def test_existing_folders_between_the_store_and_the_npu_folder_are_not_re_moded(
    tmp_path: Path,
) -> None:
    (tmp_path / "models" / "npu").mkdir(parents=True)
    store = f"{tmp_path}/models/npu/flm"
    calls, _ = _run(
        tmp_path, store, exported=DOC, models_dir=f"{tmp_path}/models/", chosen=f"{tmp_path}/models"
    )
    assert _nofollow(calls) == [f"{LEAF_1000} {store}"]


def test_a_document_folder_falls_back_to_hal0_hal0(tmp_path: Path) -> None:
    store = f"{tmp_path}/flm"
    calls, out = _run(
        tmp_path, store, exported=DOC, nofollow_fail="--owner 1000:hal0", chosen=str(tmp_path)
    )
    assert _nofollow(calls) == [f"{LEAF_1000} {store}", f"{LEAF_HAL0} {store}"]
    assert "WARN" not in out


@pytest.mark.parametrize(
    "models_dir", ["{t}/other", "{t}/flm", "{t}/fl", "/", "", "{t}/flm/x", "relative"]
)
def test_a_document_folder_outside_the_store_is_skipped_with_one_warning(
    tmp_path: Path, models_dir: str
) -> None:
    store = f"{tmp_path}/flm"
    calls, out = _run(tmp_path, store, exported=DOC, models_dir=models_dir.format(t=tmp_path))
    assert calls == []
    assert out.count("WARN:") == 1 and "is not inside the model store" in out
    assert "set HAL0_FLM_MODELS_DIR when running the installer" in out and "DONE" in out
    assert "FLM=unset" in out


def test_an_existing_document_folder_is_left_exactly_as_it_is(tmp_path: Path) -> None:
    for owner_dir in ("flm", "home/alice/.config/systemd/user"):
        (tmp_path / owner_dir).mkdir(parents=True)
        store = f"{tmp_path}/{owner_dir}"
        calls, out = _run(tmp_path, store, exported=DOC, chosen=str(Path(store).parent))
        assert _nofollow(calls) == [] and out.count("INFO:") == 1
        assert "leaving its owner and mode as they are" in out
        assert f"FLM={store}" in out


def _not_chosen(calls: list[str], out: str, flm: str) -> None:
    """Outside the default store, in a store nobody chose: nothing looked at or made."""
    assert _nofollow(calls) == [], calls
    _no_path_name_commands(calls)
    assert out.count("WARN:") == 1 and "DONE" in out, out
    assert f"FLM model directory {flm} from the answers document was not created" in out
    assert "is not one you chose on the setup screens or one this box already uses" in out
    assert "set HAL0_FLM_MODELS_DIR" in out and "FLM=unset" in out


def _link_skipped(calls: list[str], out: str, flm: str, link: str) -> None:
    assert _nofollow(calls) == [], calls
    _no_path_name_commands(calls)
    assert out.count("WARN:") == 1 and "DONE" in out, out
    assert f"FLM model directory {flm} from the answers document was not created" in out
    assert f"a link is on the path ({link} is a symbolic link)" in out
    assert "reached through a link" in out and "set HAL0_FLM_MODELS_DIR" in out


def test_a_document_folder_that_is_a_link_is_left_alone(tmp_path: Path) -> None:
    for target in ("/etc", f"{tmp_path}/nowhere"):
        link = tmp_path / "flm"
        link.symlink_to(target)
        calls, out = _run(tmp_path, str(link), exported=DOC)
        _not_chosen(calls, out, str(link))
        (tmp_path / "calls.log").unlink(missing_ok=True)
        # in the operator's own store, a link on the way is refused all the same
        calls, out = _run(tmp_path, str(link), exported=DOC, chosen=str(tmp_path))
        _link_skipped(calls, out, str(link), str(link))
        assert "FLM=unset" in out
        link.unlink()
        (tmp_path / "calls.log").unlink(missing_ok=True)


def test_a_link_in_the_middle_skips_the_folder(tmp_path: Path) -> None:
    (tmp_path / "models" / "real").mkdir(parents=True)
    (tmp_path / "models" / "mid").symlink_to(tmp_path / "models" / "real")
    flm = f"{tmp_path}/models/mid/flm"
    calls, out = _run(tmp_path, flm, exported=DOC, models_dir=f"{tmp_path}/models")
    _not_chosen(calls, out, flm)


def test_a_link_in_a_root_only_folder_to_a_root_only_folder_still_skips_it(tmp_path: Path) -> None:
    # the link and where it leads are root's alone; any link is refused all the same
    (tmp_path / "real").mkdir()
    (tmp_path / "models").symlink_to(tmp_path / "real")
    flm = f"{tmp_path}/models/flm"
    calls, out = _run(tmp_path, flm, exported=DOC, models_dir=f"{tmp_path}/models")
    _not_chosen(calls, out, flm)


def test_a_link_above_the_store_skips_the_folder(tmp_path: Path) -> None:
    (tmp_path / "var" / "home" / "models").mkdir(parents=True)
    (tmp_path / "home").symlink_to(tmp_path / "var" / "home")
    flm = f"{tmp_path}/home/models/flm"
    calls, out = _run(tmp_path, flm, exported=DOC, models_dir=f"{tmp_path}/home/models")
    _not_chosen(calls, out, flm)


def test_the_reviews_data_models_attack_creates_nothing(tmp_path: Path) -> None:
    # root's link /data/models -> /var/lib/hal0/models; the hal0 account has
    # replaced /var/lib/hal0/models with a link to /etc/systemd/system. The NPU
    # sink does not count on the store's sink having refused /data/models.
    var_dir = f"{tmp_path}/var/lib/hal0"
    (tmp_path / "var" / "lib" / "hal0").mkdir(parents=True)
    (tmp_path / "etc" / "systemd" / "system").mkdir(parents=True)
    (tmp_path / "var" / "lib" / "hal0" / "models").symlink_to(
        tmp_path / "etc" / "systemd" / "system"
    )
    (tmp_path / "data").mkdir()
    (tmp_path / "data" / "models").symlink_to(f"{var_dir}/models")
    flm = f"{tmp_path}/data/models/sshd.service.d"
    calls, out = _run(
        tmp_path,
        flm,
        exported=DOC,
        models_dir=f"{tmp_path}/data/models",
        var_dir=var_dir,
        owners={var_dir: "990 2775"},
    )
    _not_chosen(calls, out, flm)
    assert not (tmp_path / "etc" / "systemd" / "system" / "sshd.service.d").exists()


def test_a_folder_inside_the_default_store_is_created(tmp_path: Path) -> None:
    # the default store is the hal0 account's own tree: its owner does not matter,
    # because `nofollow` refuses a link planted below a folder root does not own
    var_dir = f"{tmp_path}/var/lib/hal0"
    (tmp_path / "var" / "lib" / "hal0" / "models").mkdir(parents=True)
    flm = f"{var_dir}/models/npu/flm"
    calls, out = _run(
        tmp_path,
        flm,
        exported=DOC,
        models_dir=f"{var_dir}/models",
        var_dir=var_dir,
        owners={var_dir: "990 2775", f"{var_dir}/models": "990 2775"},
    )
    assert _nofollow(calls) == [f"{PARENT} {var_dir}/models/npu", f"{LEAF_1000} {flm}"]
    assert "created FLM model directory" in out and "WARN" not in out


def test_a_link_inside_the_default_store_skips_the_folder(tmp_path: Path) -> None:
    var_dir = f"{tmp_path}/var/lib/hal0"
    (tmp_path / "var" / "lib" / "hal0").mkdir(parents=True)
    (tmp_path / "etc").mkdir()
    (tmp_path / "var" / "lib" / "hal0" / "models").symlink_to(tmp_path / "etc")
    flm = f"{var_dir}/models/flm"
    calls, out = _run(
        tmp_path,
        flm,
        exported=DOC,
        models_dir=f"{var_dir}/models",
        var_dir=var_dir,
        owners={var_dir: "990 2775"},
    )
    _link_skipped(calls, out, flm, f"{var_dir}/models")


def test_a_folder_under_root_only_real_folders_is_no_longer_created(tmp_path: Path) -> None:
    # finding C3: a document alone must not have root make a 1000:hal0 2775
    # folder in any root-only folder it names
    (tmp_path / "data" / "store").mkdir(parents=True)
    flm = f"{tmp_path}/data/store/flm"
    calls, out = _run(tmp_path, flm, exported=DOC, models_dir=f"{tmp_path}/data/store")
    _not_chosen(calls, out, flm)


@pytest.mark.parametrize(
    "owners",
    [
        {"{t}/data": "1000 755"},
        {"{t}/data/store": "990 2775"},
        {"{t}/data": "0 1777"},
        {"/": "0 777"},
    ],
)
def test_outside_the_default_store_ownership_does_not_matter_any_more(
    tmp_path: Path, owners: dict
) -> None:
    (tmp_path / "data" / "store").mkdir(parents=True)
    flm = f"{tmp_path}/data/store/npu/flm"
    calls, out = _run(
        tmp_path,
        flm,
        exported=DOC,
        models_dir=f"{tmp_path}/data/store",
        owners={k.format(t=tmp_path): v for k, v in owners.items()},
    )
    _not_chosen(calls, out, flm)


def test_an_uninspectable_folder_in_the_operators_store_skips_the_folder(tmp_path: Path) -> None:
    (tmp_path / "store" / "a").mkdir(parents=True)
    flm = f"{tmp_path}/store/a/flm"
    calls, out = _run(
        tmp_path,
        flm,
        exported=DOC,
        models_dir=f"{tmp_path}/store",
        owners={f"{tmp_path}/store": ""},
        chosen=f"{tmp_path}/store",
    )
    assert _nofollow(calls) == [] and out.count("WARN:") == 1
    assert f"{tmp_path}/store cannot be inspected" in out and "FLM=unset" in out


def test_a_refused_parent_skips_the_folder_with_one_warning(tmp_path: Path) -> None:
    store = f"{tmp_path}/a/flm"
    calls, out = _run(
        tmp_path,
        store,
        exported=DOC,
        models_dir=str(tmp_path),
        nofollow_fail="--expect root",
        chosen=str(tmp_path),
    )
    assert _nofollow(calls) == [f"{PARENT} {tmp_path}/a"]  # the folder itself is not attempted
    assert out.count("WARN:") == 1 and "could not create" in out and "DONE" in out
    assert "FLM=unset" in out


def test_a_refused_folder_warns_and_never_stops_the_install(tmp_path: Path) -> None:
    # the NPU is optional: unlike the model store, no `die` here (the harness has
    # no die(); calling one would end the run with status 127)
    store = f"{tmp_path}/flm"
    calls, out = _run(tmp_path, store, exported=DOC, nofollow_fail="dir ", chosen=str(tmp_path))
    assert _nofollow(calls) == [f"{LEAF_1000} {store}", f"{LEAF_HAL0} {store}"]
    assert out.count("WARN:") == 1 and "DONE" in out and "FLM=unset" in out
    assert "die" not in _block()
    flm_helper = _helpers()[_helpers().index("hal0_doc_flm_prepare() {") :]
    assert "die " not in flm_helper


def test_a_document_folder_is_only_looked_at_and_never_re_owned(tmp_path: Path) -> None:
    (tmp_path / "flm").mkdir()
    for store in (f"{tmp_path}/flm", f"{tmp_path}/new"):
        calls, _ = _run(tmp_path, store, exported=DOC, chosen=str(tmp_path))
        _no_path_name_commands(calls)
        # lstat only: nothing is followed while looking
        assert all(c.startswith("stat -c %f %u -- ") for c in calls if c.startswith("stat")), calls
        assert all("--create" in c for c in _nofollow(calls)), calls
        (tmp_path / "calls.log").unlink(missing_ok=True)


def test_the_check_runs_right_before_the_creation() -> None:
    # nothing but the walk sits between the text check and the first `nofollow`
    helper = _helpers()[_helpers().index("hal0_doc_flm_prepare() {") :]
    walk = helper.index("_hal0_doc_walk ")
    assert walk < helper.index("nofollow dir")
    assert '"${VAR_DIR}/models"' in _block()


def test_no_sink_here_re_owns_whatever_it_finds_at_the_name() -> None:
    # `--expect any` re-owns a root-owned directory the hal0 account renamed into
    # the operator's path; `adopt` takes only what hal0 owns or an empty new one.
    assert "--expect any" not in _block()
    assert "--expect any" not in _helpers()


def test_no_root_uid_seam_is_left() -> None:
    assert "HAL0_ANSWERS_TEST_ROOT_UID" not in TEXT
    assert "HAL0_INSTALL_TEST_ROOT_UID" not in TEXT
