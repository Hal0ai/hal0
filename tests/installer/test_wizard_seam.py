"""The installer runs the setup screens (the wizard) at the answers seam.

The wizard itself lives in another branch. Here a stub stands in for it: a
script that records its arguments, writes the outputs a test asks for and
exits with the code a test asks for. It is found through the test seams
HAL0_WIZARD_ENTRY and HAL0_WIZARD_CATALOG, which the installer honours only
when it does not run as root (tests never do).

The functions run under real bash: installer/lib/answers.sh is sourced, and the
parts of installer/install.sh that take part (the flag loop, the folder
helpers, the seam, the Python-first region) are sliced out by text markers.
The contract between the two sides is the authority for the command line, the
exit codes and the three side files.
"""

from __future__ import annotations

import json
import os
import stat
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
ANSWERS_SH = ROOT / "installer" / "lib" / "answers.sh"
INSTALL_SH = ROOT / "installer" / "install.sh"
TEXT = INSTALL_SH.read_text(encoding="utf-8")
TOKEN = "hf_StubToken_0123-xyz"

STUB = r'''
import json, os, sys
argv = sys.argv[1:]
def opt(name):
    return argv[argv.index(name) + 1] if name in argv[: argv.index("--")] else None
out, secret, chosen = opt("--out"), opt("--secret-out"), opt("--chosen-folder-out")
record = {
    "argv": argv,
    "isolated": bool(sys.flags.isolated),
    "chosen_existed": bool(chosen) and os.path.lexists(chosen),
    "dir_mode": oct(os.stat(os.path.dirname(out)).st_mode & 0o7777),
    "stdin_is_null": os.path.samestat(os.fstat(0), os.stat("/dev/null")),
    "token_in_env": any("hf_StubToken" in v for k, v in os.environ.items()
                         if not k.startswith("STUB_")),
}
saved = opt("--saved")
if saved:
    record["saved_text"] = open(saved, encoding="utf-8").read()
with open(os.environ["STUB_LOG"], "a", encoding="utf-8") as fh:
    fh.write(json.dumps(record) + "\n")
def write(path, text):
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(fd, "w", encoding="utf-8") as fh:
        fh.write(text)
if "STUB_DOC" in os.environ:
    write(out, os.environ["STUB_DOC"])
if "STUB_SECRET" in os.environ and secret:
    write(secret, os.environ["STUB_SECRET"])
if "STUB_CHOSEN" in os.environ and chosen:
    write(chosen, os.environ["STUB_CHOSEN"])
sys.stderr.write(os.environ.get("STUB_STDERR", ""))
sys.exit(int(os.environ.get("STUB_RC", "0")))
'''


def _slice(start: str, end: str) -> str:
    head = TEXT.index(start)
    return TEXT[head : TEXT.index(end, head)]


def _helpers() -> str:
    return _slice("# doc-path-helpers: begin", "# doc-path-helpers: end")


@pytest.fixture
def stub(tmp_path: Path) -> dict[str, str]:
    entry = tmp_path / "wizard" / "__main__.py"
    entry.parent.mkdir()
    entry.write_text(STUB, encoding="utf-8")
    catalog = tmp_path / "wizard" / "setup-catalog.json"
    catalog.write_text("{}", encoding="utf-8")
    return {
        "HAL0_WIZARD_ENTRY": str(entry),
        "HAL0_WIZARD_CATALOG": str(catalog),
        "STUB_LOG": str(tmp_path / "stub.log"),
    }


def _records(tmp_path: Path) -> list[dict]:
    log = tmp_path / "stub.log"
    if not log.exists():
        return []
    return [json.loads(line) for line in log.read_text(encoding="utf-8").splitlines()]


def _saved(tmp_path: Path, doc: dict, *, mode: int = 0o600, dir_mode: int = 0o700) -> Path:
    secrets = tmp_path / "var" / "secrets"
    secrets.mkdir(parents=True, exist_ok=True)
    path = secrets / "install-answers.json"
    path.write_text(json.dumps(doc), encoding="utf-8")
    path.chmod(mode)
    secrets.chmod(dir_mode)
    return path


def _collect(
    tmp_path: Path,
    env: dict[str, str] | None = None,
    *,
    args: tuple[str, ...] = (),
    reconfigure: bool = False,
    after: str = "",
) -> subprocess.CompletedProcess[str]:
    """Run hal0_collect_answers with the helpers of install.sh; print the state after it."""
    script = f"""
set -euo pipefail
IFS=$'\\n\\t'
PY="{sys.executable}"
REPO_ROOT="{ROOT}"
DEV_MODE=1
PREFIX="{tmp_path}/prefix"
VAR_DIR="{tmp_path}/var"
VERSION=9.9.9
source "{ANSWERS_SH}"
{_helpers()}
info() {{ printf 'INFO: %s\\n' "$*"; }}
warn() {{ printf 'WARN: %s\\n' "$*" >&2; }}
err() {{ printf 'ERR: %s\\n' "$*" >&2; }}
_HAL0_INSTALL_ARGS=({" ".join(json.dumps(a) for a in args)})
_HAL0_RECONFIGURE={1 if reconfigure else 0}
rc=0
hal0_collect_answers || rc=$?
echo "RC=${{rc}}"
echo "APPLIED=${{HAL0_ANSWERS_APPLIED}} RAN=${{HAL0_WIZARD_RAN:-0}} ORIGIN=${{HAL0_ANSWERS_ORIGIN:-}}"
echo "EXPORTED=${{HAL0_ANSWERS_EXPORTED[*]+${{HAL0_ANSWERS_EXPORTED[*]}}}}"
echo "TERMINAL=${{HAL0_HERMES_TERMINAL:-}}"
echo "MODELS=${{HAL0_MODELS_DIR:-}}"
echo "CHOSEN=${{_HAL0_WIZARD_CHOSEN_STORE:-}} SAVED_STORE=${{_HAL0_SAVED_STORE:-}}"
echo "TOKEN_SET=${{_HAL0_WIZARD_HF_TOKEN:+yes}}"
if bash -c 'env' | grep -v '^STUB_' | grep -q hf_StubToken; then echo "TOKEN_IN_CHILD=yes"; else echo "TOKEN_IN_CHILD=no"; fi
echo "LEFT=$(ls -A "{tmp_path}/prefix/run/hal0-install" 2>/dev/null | tr '\\n' ' ')"
{after}
"""
    return subprocess.run(
        ["bash", "-c", script],
        capture_output=True,
        text=True,
        timeout=60,
        env={"PATH": os.environ["PATH"], "HOME": str(tmp_path), **(env or {})},
        check=False,
        cwd=tmp_path,
    )


def _state(proc: subprocess.CompletedProcess[str]) -> dict[str, str]:
    out: dict[str, str] = {}
    for line in proc.stdout.splitlines():
        for part in line.split(" "):
            key, sep, value = part.partition("=")
            if sep and key.isupper() and key not in out:
                out[key] = value
    return out


DOC = json.dumps({"version": 1, "install": {"type": "recommended"}})


# ── wizard absent: nothing changes ──────────────────────────────────────────


def test_not_run_when_wizard_absent(tmp_path: Path, stub: dict[str, str]) -> None:
    env = {"STUB_LOG": stub["STUB_LOG"]}
    proc = _collect(tmp_path, env)
    assert proc.returncode == 0, proc.stderr
    assert _state(proc)["RC"] == "0" and _state(proc)["APPLIED"] == "0"
    assert _records(tmp_path) == []
    # a given document takes today's path: origin file, no wizard
    doc = tmp_path / "given.json"
    doc.write_text(json.dumps({"version": 1, "memory": {"enabled": False}}), encoding="utf-8")
    proc = _collect(tmp_path, {**env, "HAL0_ANSWERS_FILE": str(doc)})
    state = _state(proc)
    assert state["RC"] == "0" and state["APPLIED"] == "1" and state["RAN"] == "0"
    assert "HAL0_SKIP_HINDSIGHT" in proc.stdout
    assert _records(tmp_path) == []


def test_only_one_of_the_two_files_is_not_the_wizard(tmp_path: Path, stub: dict[str, str]) -> None:
    for missing in ("HAL0_WIZARD_ENTRY", "HAL0_WIZARD_CATALOG"):
        env = {**stub, missing: str(tmp_path / "nowhere"), "STUB_DOC": DOC}
        proc = _collect(tmp_path, env)
        assert _state(proc)["APPLIED"] == "0", proc.stdout + proc.stderr
    assert _records(tmp_path) == []


def test_the_test_seams_are_not_honoured_as_root() -> None:
    text = ANSWERS_SH.read_text(encoding="utf-8")
    assert text.count("HAL0_WIZARD_ENTRY") >= 1 and text.count("HAL0_WIZARD_CATALOG") >= 1
    fn = text[text.index("_hal0_wizard_paths() {") :]
    fn = fn[: fn.index("\n}\n")]
    assert '"${EUID}" -ne 0' in fn
    assert fn.index('"${EUID}" -ne 0') < fn.index('"${HAL0_WIZARD_ENTRY:-}"')
    assert fn.index('"${EUID}" -ne 0') < fn.index('"${HAL0_WIZARD_CATALOG:-}"')


# ── the command line ────────────────────────────────────────────────────────


def test_command_line_matches_contract(tmp_path: Path, stub: dict[str, str]) -> None:
    facts = tmp_path / "facts.json"
    facts.write_text("{}", encoding="utf-8")
    given = tmp_path / "given.json"
    given.write_text(DOC, encoding="utf-8")
    _saved(tmp_path, {"version": 1})
    args = ("--no-owui", "--models-dir=/srv/m", f"--answers={given}", "--reconfigure", "--bare",
            "--summary-json=/tmp/s.json", "--no-start")
    env = {**stub, "STUB_DOC": DOC, "HAL0_FACTS_FILE": str(facts), "HAL0_ANSWERS_FILE": str(given),
           "HAL0_NONINTERACTIVE": "1"}
    proc = _collect(tmp_path, env, args=args, reconfigure=True)
    assert _state(proc)["RC"] == "0", proc.stdout + proc.stderr
    (rec,) = _records(tmp_path)
    argv = rec["argv"]
    work = Path(argv[argv.index("--out") + 1]).parent
    assert work.parent == tmp_path / "prefix" / "run" / "hal0-install"
    assert argv == [
        "--catalog", stub["HAL0_WIZARD_CATALOG"],
        "--models", f"{ROOT}/installer/manifests/setup-models.json",
        "--facts", str(facts),
        "--out", f"{work}/answers.json",
        "--secret-out", f"{work}/hf-token.env",
        "--chosen-folder-out", f"{work}/chosen-folder",
        "--hal0-version", "9.9.9",
        "--saved", f"{work}/saved.json",
        "--answers", str(given),
        "--headless",
        "--reconfigure",
        "--",
        "--no-owui", "--models-dir=/srv/m", "--bare", "--summary-json=/tmp/s.json", "--no-start",
    ]
    assert rec["isolated"] is True
    assert rec["dir_mode"] == "0o700"
    assert rec["chosen_existed"] is False
    assert rec["stdin_is_null"] is True


def test_headless_whenever_a_document_is_given(tmp_path: Path, stub: dict[str, str]) -> None:
    given = tmp_path / "given.json"
    given.write_text(DOC, encoding="utf-8")
    proc = _collect(tmp_path, {**stub, "STUB_DOC": DOC, "HAL0_ANSWERS_FILE": str(given)})
    assert _state(proc)["RC"] == "0", proc.stderr
    argv = _records(tmp_path)[0]["argv"]
    assert "--headless" in argv and argv.index("--headless") < argv.index("--")


def test_no_headless_and_no_reconfigure_by_default(tmp_path: Path, stub: dict[str, str]) -> None:
    proc = _collect(tmp_path, {**stub, "STUB_DOC": DOC})
    assert _state(proc)["RC"] == "0", proc.stderr
    argv = _records(tmp_path)[0]["argv"]
    head = argv[: argv.index("--")]
    assert "--headless" not in head and "--reconfigure" not in head and "--answers" not in head
    assert argv[argv.index("--") + 1 :] == []


def test_reconfigure_flag_is_forwarded_before_the_separator(tmp_path: Path,
                                                            stub: dict[str, str]) -> None:
    proc = _collect(tmp_path, {**stub, "STUB_DOC": DOC}, args=("--reconfigure", "--no-pi"),
                    reconfigure=True)
    assert _state(proc)["RC"] == "0", proc.stderr
    argv = _records(tmp_path)[0]["argv"]
    sep = argv.index("--")
    assert argv.index("--reconfigure") < sep
    assert argv[sep + 1 :] == ["--no-pi"]


def test_saved_and_facts_passed_only_when_present(tmp_path: Path, stub: dict[str, str]) -> None:
    env = {**stub, "STUB_DOC": DOC, "HAL0_FACTS_FILE": str(tmp_path / "no-facts.json")}
    proc = _collect(tmp_path, env)
    assert _state(proc)["RC"] == "0", proc.stderr
    argv = _records(tmp_path)[-1]["argv"]
    assert "--facts" not in argv and "--saved" not in argv
    assert "WARN" not in proc.stderr
    # a saved copy that root alone could not have written is not passed (one warning)
    for mode, dir_mode in ((0o644, 0o700), (0o600, 0o770), (0o600, 0o702)):
        _saved(tmp_path, {"version": 1}, mode=mode, dir_mode=dir_mode)
        proc = _collect(tmp_path, env)
        assert _state(proc)["RC"] == "0", proc.stderr
        assert "--saved" not in _records(tmp_path)[-1]["argv"], (mode, dir_mode)
        assert proc.stderr.count("WARN") == 1, proc.stderr
        assert "saved answers" in proc.stderr
    # a link in its place is refused too
    path = tmp_path / "var" / "secrets" / "install-answers.json"
    (tmp_path / "var" / "secrets").chmod(0o700)
    real = tmp_path / "elsewhere.json"
    real.write_text('{"version": 1}', encoding="utf-8")
    real.chmod(0o600)
    path.unlink()
    path.symlink_to(real)
    proc = _collect(tmp_path, env)
    assert "--saved" not in _records(tmp_path)[-1]["argv"]
    assert proc.stderr.count("WARN") == 1


def test_a_good_saved_copy_is_handed_over_as_a_copy(tmp_path: Path, stub: dict[str, str]) -> None:
    _saved(tmp_path, {"version": 1, "memory": {"enabled": False}})
    proc = _collect(tmp_path, {**stub, "STUB_DOC": DOC})
    assert _state(proc)["RC"] == "0", proc.stderr
    rec = _records(tmp_path)[0]
    argv = rec["argv"]
    copy = Path(argv[argv.index("--saved") + 1])
    assert copy.parent == Path(argv[argv.index("--out") + 1]).parent
    assert json.loads(rec["saved_text"]) == {"version": 1, "memory": {"enabled": False}}
    assert _state(proc)["LEFT"] == ""


# ── exit 0 ──────────────────────────────────────────────────────────────────


def test_exit_0_applies_with_wizard_origin(tmp_path: Path, stub: dict[str, str]) -> None:
    doc = json.dumps({"version": 1, "apps": {"hermes": {"enabled": True, "terminal_tool": True}},
                      "memory": {"enabled": False}})
    proc = _collect(tmp_path, {**stub, "STUB_DOC": doc})
    state = _state(proc)
    assert state["RC"] == "0", proc.stdout + proc.stderr
    assert state["APPLIED"] == "1" and state["RAN"] == "1" and state["ORIGIN"] == "wizard"
    assert state["TERMINAL"] == "1"
    assert "HAL0_SKIP_HINDSIGHT" in proc.stdout and "HAL0_HERMES_TERMINAL" in proc.stdout
    assert state["LEFT"] == ""


def test_terminal_tool_from_a_given_document_stays_off(tmp_path: Path,
                                                       stub: dict[str, str]) -> None:
    # A given document is run headless, so nobody chose on a screen: the
    # document the wizard wrote is applied with origin `file`.
    doc = json.dumps({"version": 1, "apps": {"hermes": {"enabled": True, "terminal_tool": True}}})
    given = tmp_path / "given.json"
    given.write_text(doc, encoding="utf-8")
    proc = _collect(tmp_path, {**stub, "STUB_DOC": doc, "HAL0_ANSWERS_FILE": str(given)})
    state = _state(proc)
    assert state["RC"] == "0", proc.stdout + proc.stderr
    assert state["APPLIED"] == "1" and state["RAN"] == "1" and state["ORIGIN"] == "file"
    assert state["TERMINAL"] == ""


def test_exit_0_without_a_document_stops(tmp_path: Path, stub: dict[str, str]) -> None:
    proc = _collect(tmp_path, stub)
    state = _state(proc)
    assert state["RC"] == "1" and state["APPLIED"] == "0"
    assert state["LEFT"] == ""


def test_a_document_the_reader_refuses_stops(tmp_path: Path, stub: dict[str, str]) -> None:
    proc = _collect(tmp_path, {**stub, "STUB_DOC": '{"version": 2}'})
    state = _state(proc)
    assert state["RC"] not in ("0", "10", "130") and state["APPLIED"] == "0"
    assert state["LEFT"] == ""


# ── exit codes, through the seam in install.sh ──────────────────────────────


def _seam_run(tmp_path: Path, env: dict[str, str]) -> subprocess.CompletedProcess[str]:
    seam = _slice("# answers-seam: begin", "# answers-seam: end")
    script = f"""
set -euo pipefail
IFS=$'\\n\\t'
PY="{sys.executable}"
REPO_ROOT="{ROOT}"
DEV_MODE=1
PREFIX="{tmp_path}/prefix"
VAR_DIR="{tmp_path}/var"
CURRENT_STEP="Pre-flight checks"
source "{ROOT}/installer/lib/ui.sh"
source "{ROOT}/installer/lib/failure-report.sh"
source "{ANSWERS_SH}"
hal0_write_failure_report() {{ echo "REPORT WRITTEN" >> "{tmp_path}/report.log"; echo "{tmp_path}/r.txt"; }}
trap 'hal0_report_on_exit "$?"' EXIT
trap 'echo "ERR TRAP" >> "{tmp_path}/report.log"; _HAL0_REPORT_WRITTEN=1; exit 1' ERR
_HAL0_INSTALL_ARGS=()
_HAL0_RECONFIGURE=0
{seam}
echo "PAST THE SEAM"
"""
    return subprocess.run(["bash", "-c", script], capture_output=True, text=True, timeout=60,
                          env={"PATH": os.environ["PATH"], "HAL0_PLAIN": "1", **env},
                          check=False, cwd=tmp_path)


@pytest.mark.parametrize(
    "code,installer_code,report",
    [(2, 1, True), (10, 10, False), (11, 1, True), (70, 1, True), (130, 130, False),
     (3, 1, True)],
)
def test_exit_code_mapping(tmp_path: Path, stub: dict[str, str], code: int, installer_code: int,
                           report: bool) -> None:
    # the stub writes a document even so: a signal just after the write can do that
    env = {**stub, "STUB_DOC": DOC, "STUB_RC": str(code), "STUB_SECRET": f"HF_TOKEN={TOKEN}\n",
           "STUB_STDERR": "hal0 setup: stub says no\n"}
    proc = _seam_run(tmp_path, env)
    assert proc.returncode == installer_code, proc.stdout + proc.stderr
    assert "PAST THE SEAM" not in proc.stdout
    log = (tmp_path / "report.log").read_text() if (tmp_path / "report.log").exists() else ""
    assert ("REPORT WRITTEN" in log) is report, log
    assert "ERR TRAP" not in log
    run = tmp_path / "prefix" / "run" / "hal0-install"
    assert list(run.iterdir()) == []
    assert ("Nothing was changed." in proc.stdout) is (code == 10)
    assert "hal0 setup: stub says no" in proc.stderr
    assert TOKEN not in proc.stdout + proc.stderr


def test_exit_0_goes_past_the_seam(tmp_path: Path, stub: dict[str, str]) -> None:
    proc = _seam_run(tmp_path, {**stub, "STUB_DOC": DOC})
    assert proc.returncode == 0, proc.stdout + proc.stderr
    assert "PAST THE SEAM" in proc.stdout
    assert not (tmp_path / "report.log").exists()


def test_without_the_wizard_a_bad_document_still_goes_through_the_err_trap(
        tmp_path: Path) -> None:
    bad = tmp_path / "bad.json"
    bad.write_text('{"version": 7}', encoding="utf-8")
    proc = _seam_run(tmp_path, {"HAL0_ANSWERS_FILE": str(bad)})
    assert proc.returncode == 1
    assert "ERR TRAP" in (tmp_path / "report.log").read_text()


# ── the secret file ─────────────────────────────────────────────────────────


def test_secret_file_read_and_removed(tmp_path: Path, stub: dict[str, str]) -> None:
    proc = _collect(tmp_path, {**stub, "STUB_DOC": DOC, "STUB_SECRET": f"HF_TOKEN={TOKEN}\n"},
                    after='[[ "${_HAL0_WIZARD_HF_TOKEN}" == "' + TOKEN + '" ]] && echo MATCH')
    state = _state(proc)
    assert state["RC"] == "0" and state["TOKEN_SET"] == "yes", proc.stdout + proc.stderr
    assert "MATCH" in proc.stdout
    assert state["TOKEN_IN_CHILD"] == "no"
    assert state["LEFT"] == ""
    assert "WARN" not in proc.stderr


@pytest.mark.parametrize("text", [
    f"OTHER={TOKEN}\n", f"HF_TOKEN={TOKEN} x\n", f"HF_TOKEN={TOKEN}\nHF_TOKEN=b\n",
    "HF_TOKEN=\n", f"HF_TOKEN=$({TOKEN})\n", f"HF_TOKEN={TOKEN}\x1b[2J\n", f" HF_TOKEN={TOKEN}\n",
])
def test_secret_file_with_other_name_ignored(tmp_path: Path, stub: dict[str, str],
                                             text: str) -> None:
    proc = _collect(tmp_path, {**stub, "STUB_DOC": DOC, "STUB_SECRET": text})
    state = _state(proc)
    assert state["RC"] == "0" and state["TOKEN_SET"] == "", proc.stdout + proc.stderr
    assert proc.stderr.count("WARN") == 1
    assert TOKEN not in proc.stdout + proc.stderr
    assert state["LEFT"] == ""


def test_secret_never_printed(tmp_path: Path, stub: dict[str, str]) -> None:
    for rc in ("0", "2", "130"):
        proc = _collect(tmp_path, {**stub, "STUB_DOC": DOC, "STUB_RC": rc,
                                   "STUB_SECRET": f"HF_TOKEN={TOKEN}\n"})
        assert TOKEN not in proc.stdout and TOKEN not in proc.stderr
        assert _state(proc)["TOKEN_IN_CHILD"] == "no"
    # nor does the wizard itself see one in its environment
    assert not any(r["token_in_env"] for r in _records(tmp_path))


def test_the_token_reaches_the_installers_token_variable() -> None:
    head = TEXT.index("# ── Operator input: model store + HuggingFace token")
    block = TEXT[head : TEXT.index("_md_answer=", head)]
    assert 'HF_TOKEN_VAL="${_HAL0_WIZARD_HF_TOKEN}"' in block
    assert "unset _HAL0_WIZARD_HF_TOKEN" in block


# ── the work directory ──────────────────────────────────────────────────────


def test_work_directory_removed_on_every_path(tmp_path: Path, stub: dict[str, str]) -> None:
    cases = [
        {"STUB_DOC": DOC},
        {"STUB_DOC": DOC, "STUB_RC": "2"},
        {"STUB_DOC": DOC, "STUB_RC": "10"},
        {"STUB_DOC": DOC, "STUB_RC": "130"},
        {"STUB_RC": "0"},
        {"STUB_DOC": '{"version": 2}'},
        {"STUB_DOC": DOC, "STUB_CHOSEN": "/srv/x\n", "STUB_SECRET": "junk"},
    ]
    for extra in cases:
        proc = _collect(tmp_path, {**stub, **extra})
        assert _state(proc)["LEFT"] == "", (extra, proc.stdout, proc.stderr)
        run = tmp_path / "prefix" / "run" / "hal0-install"
        assert list(run.iterdir()) == []


def test_a_work_base_others_can_write_is_refused(tmp_path: Path, stub: dict[str, str]) -> None:
    run = tmp_path / "prefix" / "run" / "hal0-install"
    run.mkdir(parents=True)
    run.chmod(0o777)
    proc = _collect(tmp_path, {**stub, "STUB_DOC": DOC})
    assert _state(proc)["RC"] == "1" and _records(tmp_path) == []
    run.chmod(0o755)


# ── Python first ────────────────────────────────────────────────────────────


def _python_region(tmp_path: Path, *, usable: int, wizard: bool, have: str = "",
                   answers: str = "") -> list[str]:
    region = _slice("# file-origin: begin", "# file-origin: end")
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir(exist_ok=True)
    for name in have.split():
        p = bin_dir / name
        p.write_text("#!/bin/bash\nexit 0\n", encoding="utf-8")
        p.chmod(0o755)
    log = tmp_path / "calls.log"
    log.unlink(missing_ok=True)
    present = "return 0" if wizard else "return 1"
    script = f"""
set -u
PATH="{bin_dir}"
LOG="{log}"
PY=python3
{answers}
source "{ANSWERS_SH}"
hal0_wizard_present() {{ {present}; }}
hal0_answers_python() {{ return 1; }}
info() {{ echo "info: $*" >> "$LOG"; }}
_hal0_resolve_main_python_or_die() {{ echo "resolve $*" >> "$LOG"; }}
_main_python_usable={usable}
{region}
echo "usable=$_main_python_usable" >> "$LOG"
"""
    subprocess.run(["/bin/bash", "-c", script], capture_output=True, text=True, check=False)
    return log.read_text().splitlines()


def test_python_installed_first_when_missing(tmp_path: Path) -> None:
    calls = _python_region(tmp_path, usable=0, wizard=True)
    msg = "info: Installing Python, which hal0 needs before it can ask anything."
    assert calls == [msg, "resolve install-first", "usable=1"]
    # an interpreter that is new enough exists: resolved quietly, no message
    calls = _python_region(tmp_path, usable=0, wizard=True, have="python3.13")
    assert calls == ["resolve install-first", "usable=1"]
    # the default one is usable: nothing
    assert _python_region(tmp_path, usable=1, wizard=True) == ["usable=1"]
    # without the wizard the older rule stays: only for a given file
    assert _python_region(tmp_path, usable=0, wizard=False) == ["usable=0"]
    calls = _python_region(tmp_path, usable=0, wizard=False, answers="HAL0_ANSWERS_FILE=/x.json")
    assert calls[0] == "info: Installing Python, which is needed to read your answers file."


def test_the_wizard_refuses_an_old_python(tmp_path: Path, stub: dict[str, str]) -> None:
    fake = tmp_path / "oldpy"
    fake.write_text(f"#!/bin/bash\n[[ \"$*\" == *version_info* ]] && exit 1\nexec {sys.executable} \"$@\"\n",
                    encoding="utf-8")
    fake.chmod(0o755)
    script_env = {**stub, "STUB_DOC": DOC}
    proc = subprocess.run(
        ["bash", "-c", f"""
set -u
PY="{fake}" REPO_ROOT="{ROOT}" DEV_MODE=1 PREFIX="{tmp_path}/p2" VAR_DIR="{tmp_path}/v2"
source "{ANSWERS_SH}"
err() {{ echo "ERR: $*" >&2; }}
warn() {{ echo "WARN: $*" >&2; }}
_HAL0_INSTALL_ARGS=()
rc=0; hal0_run_wizard || rc=$?; echo "RC=$rc"
"""],
        capture_output=True, text=True, check=False,
        env={"PATH": os.environ["PATH"], **script_env},
    )
    assert "RC=1" in proc.stdout and "3.12" in proc.stderr, proc.stdout + proc.stderr
    assert _records(tmp_path) == []


# ── the CPU-only prompt ─────────────────────────────────────────────────────


def test_cpu_only_prompt_skipped_after_the_wizard() -> None:
    head = TEXT.index('if [[ "${DEV_MODE}" -eq 0 ]]; then\n    # gpu_rc was captured')
    block = TEXT[head : TEXT.index("# ── Operator input", head)]
    prompts = [ln for ln in block.splitlines() if "_confirm_cpu_only" in ln]
    assert len(prompts) == 2
    assert all('"${HAL0_WIZARD_RAN:-0}" != "1"' in ln for ln in prompts), prompts
