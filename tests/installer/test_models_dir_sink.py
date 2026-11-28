"""A model store from an answers document: the rules about the disk live in the sink.

The reader checks the path's text only. The "Filesystem layout" step then
walks the path from / down, looking at each component itself (lstat): no
component may be a symbolic link, wherever it sits; outside the default store
every existing ancestor must be a directory only root can change; an existing
store must be root's with no group or other write bit, or the hal0 account's.
Missing folders are made through `nofollow`: root:root 0755 outside the default
store, hal0:hal0 2775 (as the default store itself) inside it. Any refusal
stops the install with one line.

The block and its helpers are sliced out of installer/install.sh by text
markers and run under real bash. Tests cannot run as root, so `stat` and `id`
are stubbed (owners and modes come from a table, everything else defaults to
root and 0755) and `nofollow`, `mkdir` and `die` record what they were asked.
The folders and links themselves are real, under tmp_path.
"""

from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

INSTALL_SH = Path(__file__).resolve().parents[2] / "installer" / "install.sh"
TEXT = INSTALL_SH.read_text(encoding="utf-8")
HAL0_UID = "990"
NOFOLLOW_ROOT = "nofollow dir --create --owner root:root --mode 0755 --expect root"
NOFOLLOW_HAL0 = "nofollow dir --create --owner hal0:hal0 --mode 2775 --expect adopt"
NOFOLLOW_DEV = "nofollow dir --create --mode 0755 --expect adopt"
EXISTING = (
    "it already exists and is not a folder only root can change, nor one the hal0 account owns"
)


def _slice(start: str, end: str) -> str:
    head = TEXT.index(start)
    return TEXT[head : TEXT.index(end, head)]


def _run(
    tmp_path: Path,
    models_dir: str,
    *,
    from_doc: int = 1,
    owners: dict[str, str] | None = None,
    fail: int = 0,
    var_dir: str = "/v",
    hal0_exists: bool = True,
    dev_mode: int = 0,
    expect_rc: int = 0,
) -> tuple[list[str], str]:
    """Run the step. ``owners`` maps a path to the "UID MODE" its stubbed `stat -L` reports."""
    log = tmp_path / "calls.log"
    table = " ".join(f'["{path}"]="{value}"' for path, value in (owners or {}).items())
    hal0 = f"echo {HAL0_UID}" if hal0_exists else "return 1"
    script = f"""
set -euo pipefail
LOG="{log}"
PREFIX=/p ETC_DIR=/e VAR_DIR="{var_dir}" UNIT_DIR=/u DEV_MODE={dev_mode}
MODELS_DIR="{models_dir}"
MODELS_DIR_FROM_DOC={from_doc}
declare -A OWN=({table})
{_slice("# doc-path-helpers: begin", "# doc-path-helpers: end")}
ui_step() {{ :; }}
info() {{ echo "INFO: $*"; }}
warn() {{ echo "WARN: $*"; }}
die() {{ echo "DIE: $*"; exit 1; }}
nofollow() {{ echo "nofollow $*" >>"$LOG"; return {fail}; }}
mkdir() {{ echo "mkdir $*" >>"$LOG"; }}
id() {{
    if [[ "$*" == "-u" ]]; then echo 0
    elif [[ "$*" == "-u hal0" ]]; then {hal0}
    else return 1; fi
}}
stat() {{
    # lstat only: the type comes from the real entry, owner and mode from OWN
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
{_slice('ui_step "Filesystem layout"', 'info "directories under')}
echo DONE
"""
    proc = subprocess.run(["bash", "-c", script], capture_output=True, text=True, check=False)
    assert proc.returncode == expect_rc, proc.stdout + proc.stderr
    calls = log.read_text(encoding="utf-8").splitlines() if log.exists() else []
    return calls, proc.stdout


def _nofollow(calls: list[str]) -> list[str]:
    return [c for c in calls if c.startswith("nofollow")]


def _mkdir_p(calls: list[str]) -> str:
    (line,) = [c for c in calls if c.startswith("mkdir -p")]
    return line


def _died(calls: list[str], out: str, store: str, why: str, *, after_mkdir: bool = False) -> None:
    """One line naming the store and why, pointing at --models-dir; nothing made at all.

    ``after_mkdir``: a store inside the default store is checked once the
    default store's own folders exist, so the product's `mkdir -p` has run.
    """
    assert out.count("DIE:") == 1 and "DONE" not in out and "WARN" not in out
    assert len(out.strip().splitlines()) == 1
    assert f"model store {store} from the answers document cannot be used" in out
    assert why in out, out
    assert "--models-dir=PATH" in out and "your own choice" in out
    assert _nofollow(calls) == [], calls
    made = [c for c in calls if c.startswith("mkdir")]
    if after_mkdir:
        assert len(made) == 1, calls  # the product's own folders, the default store with them
    else:
        assert made == [], calls  # not even the other folders


def _link_died(calls: list[str], out: str, store: str, link: str, **kwargs: bool) -> None:
    _died(calls, out, store, f"a link is on the path ({link} is a symbolic link)", **kwargs)
    assert "reached through a link" in out and "--models-dir=PATH" in out


# ── accepted ────────────────────────────────────────────────────────────────


def test_missing_folders_under_root_only_ancestors_are_made_in_one_nofollow_call(
    tmp_path: Path,
) -> None:
    store = f"{tmp_path}/a/b/models"
    calls, out = _run(tmp_path, store)
    assert _nofollow(calls) == [f"{NOFOLLOW_ROOT} {tmp_path}/a {tmp_path}/a/b {store}"]
    assert store not in _mkdir_p(calls)  # never by path name
    assert "DONE" in out and "WARN" not in out and "DIE" not in out
    # every existing ancestor, from / down, was looked at through `stat -L`
    looked = [c.split()[-1] for c in calls if c.startswith("stat")]
    parts = [p for p in str(tmp_path).split("/") if p]
    assert looked == ["/"] + ["/" + "/".join(parts[: i + 1]) for i in range(len(parts))]


def test_existing_components_are_not_re_moded(tmp_path: Path) -> None:
    (tmp_path / "a").mkdir()
    store = f"{tmp_path}/a/models"
    calls, _ = _run(tmp_path, store)
    assert _nofollow(calls) == [f"{NOFOLLOW_ROOT} {store}"]


@pytest.mark.parametrize(
    "owner", ["0 755", "0 700", "0 1755", f"{HAL0_UID} 2775", f"{HAL0_UID} 777"]
)
def test_an_existing_store_owned_by_root_alone_or_by_hal0_needs_no_creation(
    tmp_path: Path, owner: str
) -> None:
    (tmp_path / "models").mkdir()
    store = f"{tmp_path}/models"
    calls, out = _run(tmp_path, store, owners={store: owner})
    assert _nofollow(calls) == [] and "DONE" in out
    assert store not in _mkdir_p(calls)


def test_the_store_and_its_parents_are_looked_at_without_following_links(tmp_path: Path) -> None:
    (tmp_path / "a").mkdir()
    calls, _ = _run(tmp_path, f"{tmp_path}/a/m")
    looked = [c for c in calls if c.startswith("stat")]
    assert looked and all(c.startswith("stat -c %f %u -- ") for c in looked), looked


def test_a_path_inside_the_default_store_is_made_through_nofollow_like_the_default_store(
    tmp_path: Path,
) -> None:
    # the default store is hal0:hal0 2775 (the adopt loop below); folders a document
    # adds inside it get the same, through `nofollow`, never `mkdir -p` by name
    var_dir = f"{tmp_path}/var"
    (tmp_path / "var" / "models").mkdir(parents=True)  # the step's own `mkdir -p` made it
    store = f"{var_dir}/models/deeper/x"
    calls, out = _run(
        tmp_path,
        store,
        var_dir=var_dir,
        owners={
            str(tmp_path): "4242 777",  # its parents' owners do not matter
            f"{var_dir}/models": f"{HAL0_UID} 2775",
        },
    )
    assert "DIE" not in out and "DONE" in out, out
    assert _nofollow(calls) == [f"{NOFOLLOW_HAL0} {var_dir}/models/deeper {store}"]
    mkdir_p = _mkdir_p(calls).split()
    assert f"{var_dir}/models" in mkdir_p and store not in mkdir_p
    assert calls.index(_mkdir_p(calls)) < calls.index(_nofollow(calls)[0])


def test_the_default_store_itself_from_a_document_needs_no_creation(tmp_path: Path) -> None:
    var_dir = f"{tmp_path}/var"
    (tmp_path / "var" / "models").mkdir(parents=True)
    calls, out = _run(
        tmp_path,
        f"{var_dir}/models",
        var_dir=var_dir,
        owners={f"{var_dir}/models": f"{HAL0_UID} 2775"},
    )
    assert "DONE" in out and _nofollow(calls) == []
    assert f"{var_dir}/models" in _mkdir_p(calls).split()


def test_inside_the_default_store_without_a_hal0_account_folders_are_made_plain(
    tmp_path: Path,
) -> None:
    var_dir = f"{tmp_path}/var"
    (tmp_path / "var" / "models").mkdir(parents=True)
    store = f"{var_dir}/models/x"
    for kwargs in ({"hal0_exists": False}, {"dev_mode": 1}):
        calls, out = _run(tmp_path, store, var_dir=var_dir, **kwargs)
        assert "DONE" in out, out
        assert _nofollow(calls) == [f"{NOFOLLOW_DEV} {store}"]
        (tmp_path / "calls.log").unlink()


def test_a_link_inside_the_default_store_stops_the_install(tmp_path: Path) -> None:
    # the hal0 account owns the default store: a link it planted there is refused
    var_dir = f"{tmp_path}/var"
    (tmp_path / "var" / "models").mkdir(parents=True)
    (tmp_path / "elsewhere").mkdir()
    (tmp_path / "var" / "models" / "deeper").symlink_to(tmp_path / "elsewhere")
    store = f"{var_dir}/models/deeper/x"
    calls, out = _run(tmp_path, store, var_dir=var_dir, expect_rc=1)
    _link_died(calls, out, store, f"{var_dir}/models/deeper", after_mkdir=True)


def test_a_default_store_that_is_a_link_stops_a_documents_store_inside_it(tmp_path: Path) -> None:
    var_dir = f"{tmp_path}/var"
    (tmp_path / "var").mkdir()
    (tmp_path / "big").mkdir()
    (tmp_path / "var" / "models").symlink_to(tmp_path / "big")
    for store in (f"{var_dir}/models", f"{var_dir}/models/x"):
        calls, out = _run(tmp_path, store, var_dir=var_dir, expect_rc=1)
        _link_died(calls, out, store, f"{var_dir}/models", after_mkdir=True)
        (tmp_path / "calls.log").unlink()


def test_an_existing_store_inside_the_default_store_gets_the_existing_folder_rule(
    tmp_path: Path,
) -> None:
    var_dir = f"{tmp_path}/var"
    (tmp_path / "var" / "models" / "x").mkdir(parents=True)
    store = f"{var_dir}/models/x"
    calls, out = _run(tmp_path, store, var_dir=var_dir, owners={store: "0 1777"}, expect_rc=1)
    _died(calls, out, store, EXISTING, after_mkdir=True)


def test_a_look_alike_of_the_default_store_gets_the_full_rule(tmp_path: Path) -> None:
    var_dir = f"{tmp_path}/var"
    store = f"{var_dir}/modelsx/m"
    calls, out = _run(
        tmp_path, store, var_dir=var_dir, owners={str(tmp_path): "4242 755"}, expect_rc=1
    )
    _died(calls, out, store, f"{tmp_path} is not owned by root")


# ── refused: each one is fatal, one line, nothing made ──────────────────────


def test_an_ancestor_owned_by_someone_else_stops_the_install(tmp_path: Path) -> None:
    (tmp_path / "home" / "alice").mkdir(parents=True)
    store = f"{tmp_path}/home/alice/models"
    calls, out = _run(tmp_path, store, owners={f"{tmp_path}/home/alice": "1000 755"}, expect_rc=1)
    _died(calls, out, store, f"{tmp_path}/home/alice is not owned by root")


def test_an_ancestor_owned_by_the_hal0_account_stops_the_install(tmp_path: Path) -> None:
    (tmp_path / "lib" / "hal0").mkdir(parents=True)
    store = f"{tmp_path}/lib/hal0/other"
    calls, out = _run(
        tmp_path, store, owners={f"{tmp_path}/lib/hal0": f"{HAL0_UID} 2775"}, expect_rc=1
    )
    _died(calls, out, store, f"{tmp_path}/lib/hal0 is not owned by root")


@pytest.mark.parametrize("mode", ["775", "757", "1777", "2775", "777"])
def test_a_group_or_other_writable_ancestor_stops_the_install(tmp_path: Path, mode: str) -> None:
    (tmp_path / "shared").mkdir()
    store = f"{tmp_path}/shared/models"
    calls, out = _run(tmp_path, store, owners={f"{tmp_path}/shared": f"0 {mode}"}, expect_rc=1)
    _died(calls, out, store, f"{tmp_path}/shared can be changed by a group or by other users")


def test_a_writable_root_folder_stops_the_install(tmp_path: Path) -> None:
    calls, out = _run(tmp_path, f"{tmp_path}/m", owners={"/": "0 777"}, expect_rc=1)
    _died(calls, out, f"{tmp_path}/m", "/ is not a folder only root can change")


def test_an_ancestor_further_up_counts_too_not_only_the_deepest(tmp_path: Path) -> None:
    (tmp_path / "a" / "b").mkdir(parents=True)
    store = f"{tmp_path}/a/b/models"
    calls, out = _run(tmp_path, store, owners={f"{tmp_path}/a": "1000 755"}, expect_rc=1)
    _died(calls, out, store, f"{tmp_path}/a is not owned by root")


@pytest.mark.parametrize("owner", ["1000 755", "4242 700", "1000 2775"])
def test_an_existing_store_owned_by_another_account_stops_the_install(
    tmp_path: Path, owner: str
) -> None:
    (tmp_path / "models").mkdir()
    store = f"{tmp_path}/models"
    calls, out = _run(tmp_path, store, owners={store: owner}, expect_rc=1)
    _died(calls, out, store, EXISTING)


@pytest.mark.parametrize("mode", ["1777", "2775", "775", "757", "777", "3777"])
def test_an_existing_root_store_a_group_or_others_can_write_stops_the_install(
    tmp_path: Path, mode: str
) -> None:
    # /var/tmp, /var/crash (1777), /var/mail, /var/local (2775): root's later
    # downloads write by name below the store, where anyone could plant a link
    (tmp_path / "shared").mkdir()
    store = f"{tmp_path}/shared"
    calls, out = _run(tmp_path, store, owners={store: f"0 {mode}"}, expect_rc=1)
    _died(calls, out, store, EXISTING)


def test_without_a_hal0_account_only_root_may_own_an_existing_store(tmp_path: Path) -> None:
    (tmp_path / "models").mkdir()
    store = f"{tmp_path}/models"
    calls, out = _run(
        tmp_path, store, owners={store: f"{HAL0_UID} 755"}, hal0_exists=False, expect_rc=1
    )
    _died(calls, out, store, EXISTING)


def test_a_symlink_component_in_a_directory_someone_else_owns_stops_the_install(
    tmp_path: Path,
) -> None:
    (tmp_path / "home" / "alice").mkdir(parents=True)
    (tmp_path / "elsewhere").mkdir()
    (tmp_path / "home" / "alice" / "link").symlink_to(tmp_path / "elsewhere")
    store = f"{tmp_path}/home/alice/link/models"
    calls, out = _run(tmp_path, store, owners={f"{tmp_path}/home/alice": "1000 700"}, expect_rc=1)
    _died(calls, out, store, f"{tmp_path}/home/alice is not owned by root")


def test_a_symlink_leading_to_a_folder_someone_else_owns_stops_the_install(tmp_path: Path) -> None:
    (tmp_path / "alice").mkdir()
    (tmp_path / "data").symlink_to(tmp_path / "alice")
    store = f"{tmp_path}/data/models"
    calls, out = _run(tmp_path, store, owners={f"{tmp_path}/alice": "1000 755"}, expect_rc=1)
    _link_died(calls, out, store, f"{tmp_path}/data")


def test_a_link_in_a_root_only_folder_to_a_root_only_folder_still_stops_the_install(
    tmp_path: Path,
) -> None:
    # like /home -> /var/home: every link is refused, wherever it sits and leads
    (tmp_path / "var" / "home").mkdir(parents=True)
    (tmp_path / "home").symlink_to(tmp_path / "var" / "home")
    store = f"{tmp_path}/home/me/models"
    calls, out = _run(tmp_path, store, expect_rc=1)
    _link_died(calls, out, store, f"{tmp_path}/home")


def test_a_store_that_is_itself_a_link_stops_the_install(tmp_path: Path) -> None:
    (tmp_path / "real").mkdir()
    (tmp_path / "models").symlink_to(tmp_path / "real")
    store = f"{tmp_path}/models"
    calls, out = _run(tmp_path, store, expect_rc=1)
    _link_died(calls, out, store, store)


def test_a_link_in_the_middle_stops_the_install(tmp_path: Path) -> None:
    (tmp_path / "real" / "b").mkdir(parents=True)
    (tmp_path / "a").symlink_to(tmp_path / "real")
    store = f"{tmp_path}/a/b/models"
    calls, out = _run(tmp_path, store, expect_rc=1)
    _link_died(calls, out, store, f"{tmp_path}/a")


def test_the_reviews_data_models_link_into_the_default_store_stops_the_install(
    tmp_path: Path,
) -> None:
    # root's convenience link /data/models -> /var/lib/hal0/models: the hal0
    # account owns where it leads, so a document may not name it
    var_dir = f"{tmp_path}/var/lib/hal0"
    (tmp_path / "var" / "lib" / "hal0" / "models").mkdir(parents=True)
    (tmp_path / "data").mkdir()
    (tmp_path / "data" / "models").symlink_to(f"{var_dir}/models")
    store = f"{tmp_path}/data/models"
    calls, out = _run(
        tmp_path,
        store,
        var_dir=var_dir,
        owners={var_dir: f"{HAL0_UID} 2775", f"{var_dir}/models": f"{HAL0_UID} 2775"},
        expect_rc=1,
    )
    _link_died(calls, out, store, store)


def test_a_dangling_link_stops_the_install(tmp_path: Path) -> None:
    (tmp_path / "models").symlink_to(tmp_path / "nowhere")
    for store in (f"{tmp_path}/models", f"{tmp_path}/models/deeper"):
        calls, out = _run(tmp_path, store, expect_rc=1)
        _link_died(calls, out, store, f"{tmp_path}/models")
        (tmp_path / "calls.log").unlink(missing_ok=True)


def test_a_file_on_the_way_stops_the_install(tmp_path: Path) -> None:
    (tmp_path / "afile").write_text("x")
    for store in (f"{tmp_path}/afile", f"{tmp_path}/afile/models"):
        calls, out = _run(tmp_path, store, expect_rc=1)
        _died(calls, out, store, f"{tmp_path}/afile is not a folder")
        (tmp_path / "calls.log").unlink(missing_ok=True)


def test_a_folder_that_cannot_be_inspected_stops_the_install(tmp_path: Path) -> None:
    calls, out = _run(tmp_path, f"{tmp_path}/m", owners={str(tmp_path): ""}, expect_rc=1)
    _died(calls, out, f"{tmp_path}/m", f"{tmp_path} cannot be inspected")


def test_a_refused_creation_stops_the_install(tmp_path: Path) -> None:
    store = f"{tmp_path}/a/models"
    calls, out = _run(tmp_path, store, fail=3, expect_rc=1)
    assert _nofollow(calls) == [f"{NOFOLLOW_ROOT} {tmp_path}/a {store}"]  # one call, refused
    assert out.count("DIE:") == 1 and f"{tmp_path}/a could not be created safely" in out
    assert not any(c.startswith("mkdir") for c in calls)


# ── a store that did not come from a document: as before ────────────────────


def test_a_store_that_did_not_come_from_a_document_is_made_as_before(tmp_path: Path) -> None:
    (tmp_path / "link").symlink_to(tmp_path)
    for store in (f"{tmp_path}/a/models", f"{tmp_path}/link/m"):
        calls, out = _run(tmp_path, store, from_doc=0, owners={str(tmp_path): "1000 777"})
        assert "DIE" not in out and "DONE" in out
        assert store in _mkdir_p(calls) and _nofollow(calls) == []
        assert not any(c.startswith("stat") for c in calls)
        (tmp_path / "calls.log").unlink()


def test_the_old_helpers_and_the_root_uid_seam_are_gone() -> None:
    for gone in (
        "hal0_doc_dir_parent_ok",
        "hal0_mkdir_walk_nofollow",
        "_hal0_root_uid",
        "HAL0_ANSWERS_TEST_ROOT_UID",
        "HAL0_INSTALL_TEST_ROOT_UID",
    ):
        assert gone not in TEXT, gone


def test_the_flag_is_set_only_when_the_document_won() -> None:
    block = _slice("# answers-models-dir: begin", "# answers-models-dir: end")
    assert block.count("MODELS_DIR_FROM_DOC=1") == 1
    assert block.index("MODELS_DIR_FROM_DOC=1") > block.index('MODELS_DIR="${HAL0_MODELS_DIR}"')
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
    assert (
        _seam(exported=["HAL0_MODELS_DIR"], flag=1) == "FROM_DOC=0 DIR=/default"
    )  # --models-dir won
    assert (
        _seam(exported=["HAL0_NONINTERACTIVE"], flag=0) == "FROM_DOC=0 DIR=/default"
    )  # inherited env
