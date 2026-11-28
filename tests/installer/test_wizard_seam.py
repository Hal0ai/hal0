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

import contextlib
import json
import os
import re
import signal
import subprocess
import sys
import time
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
ANSWERS_SH = ROOT / "installer" / "lib" / "answers.sh"
INSTALL_SH = ROOT / "installer" / "install.sh"
TEXT = INSTALL_SH.read_text(encoding="utf-8")
TOKEN = "hf_StubToken_0123-xyz"

STUB = r"""
import json, os, sys
argv = sys.argv[1:]
def opt(name):
    return argv[argv.index(name) + 1] if name in argv[: argv.index("--")] else None
out, secret, chosen = opt("--out"), opt("--secret-out"), opt("--chosen-folder-out")
shown = opt("--screens-shown-out")
record = {
    "argv": argv,
    "shown_out": shown,
    "shown_existed": bool(shown) and os.path.lexists(shown),
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
given = opt("--answers")
if given:
    record["answers_bytes"] = open(given, "rb").read().hex()
with open(os.environ["STUB_LOG"], "a", encoding="utf-8") as fh:
    fh.write(json.dumps(record) + "\n")
def write(path, name):
    if name + "_HEX" in os.environ:
        data = bytes.fromhex(os.environ[name + "_HEX"])
    elif name in os.environ:
        data = os.environ[name].encode("utf-8")
    else:
        return
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(fd, "wb") as fh:
        fh.write(data)
write(out, "STUB_DOC")
if secret:
    write(secret, "STUB_SECRET")
if chosen:
    write(chosen, "STUB_CHOSEN")
if shown:
    write(shown, "STUB_SHOWN")
if "STUB_SLEEP" in os.environ:
    import signal, time
    signal.signal(signal.SIGINT, signal.SIG_DFL)  # no handler of its own yet
    open(os.environ["STUB_SLEEP"], "w").close()
    time.sleep(30)
sys.stdout.write(os.environ.get("STUB_STDOUT", ""))
sys.stdout.flush()
sys.stderr.write(os.environ.get("STUB_STDERR", ""))
if "STUB_STDERR_HEX" in os.environ:
    sys.stderr.flush()
    sys.stderr.buffer.write(bytes.fromhex(os.environ["STUB_STDERR_HEX"]))
sys.exit(int(os.environ.get("STUB_RC", "0")))
"""


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


def _kill_group(pid: int) -> None:
    """SIGKILL a process group. The group can outlive its leader; an empty one is fine."""
    with contextlib.suppress(ProcessLookupError):
        os.killpg(pid, signal.SIGKILL)


def _run_group(
    argv: list[str], *, timeout: float = 60, **kwargs: object
) -> subprocess.CompletedProcess[str]:
    """subprocess.run in its own session; the whole process group is killed on every exit.

    subprocess.run kills only the direct child on timeout. A grandchild (the stub
    wizard under bash) that is blocked would survive as an orphan, and so would
    one that outlives bash after a normal end. The group is killed on a timeout
    (before it is raised again), on any other exception and on a normal end.
    """
    with subprocess.Popen(argv, start_new_session=True, **kwargs) as proc:  # type: ignore[call-overload]
        try:
            out, err = proc.communicate(timeout=timeout)
        except BaseException:
            _kill_group(proc.pid)
            # a pipe holder outside the group must not be waited on forever
            with contextlib.suppress(subprocess.TimeoutExpired):
                proc.communicate(timeout=5)
            raise
        _kill_group(proc.pid)
        return subprocess.CompletedProcess(argv, proc.returncode, out, err)


def _collect(
    tmp_path: Path,
    env: dict[str, str] | None = None,
    *,
    args: tuple[str, ...] = (),
    reconfigure: bool = False,
    after: str = "",
    timeout: float = 60,
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
    return _run_group(
        ["bash", "-c", script],
        timeout=timeout,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        env={"PATH": os.environ["PATH"], "HOME": str(tmp_path), **(env or {})},
        cwd=tmp_path,
    )


def _exported(proc: subprocess.CompletedProcess[str]) -> list[str]:
    """The names the document set, from the EXPORTED line of a _collect run."""
    # bash joins the array with IFS, which the script sets to a newline
    head = proc.stdout.index("EXPORTED=") + len("EXPORTED=")
    return proc.stdout[head : proc.stdout.index("\nTERMINAL=", head)].split()


def _state(proc: subprocess.CompletedProcess[str]) -> dict[str, str]:
    out: dict[str, str] = {}
    for line in proc.stdout.splitlines():
        for part in line.split(" "):
            key, sep, value = part.partition("=")
            if sep and key.isupper() and key not in out:
                out[key] = value
    return out


DOC = json.dumps({"version": 1, "install": {"type": "recommended"}})


# ── the tree has what the seam needs ────────────────────────────────────────

WIZARD_FILES = {
    "entry": Path("src/hal0/install/wizard/__main__.py"),
    "catalog": Path("installer/manifests/setup-catalog.json"),
    "models": Path("installer/manifests/setup-models.json"),
}


def _tree_with_the_three_files(tmp_path: Path) -> Path:
    tree = tmp_path / "tree"
    for rel in WIZARD_FILES.values():
        (tree / rel).parent.mkdir(parents=True, exist_ok=True)
        (tree / rel).write_text("{}", encoding="utf-8")
    return tree


def _is_present(tmp_path: Path, tree: Path) -> bool:
    proc = _run_group(
        [
            "bash",
            "-c",
            f'REPO_ROOT="{tree}"\nsource "{ANSWERS_SH}"\n'
            "if hal0_wizard_present; then echo PRESENT; else echo ABSENT; fi",
        ],
        timeout=30,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        env={"PATH": os.environ["PATH"]},
        cwd=tmp_path,
    )
    assert proc.returncode == 0, proc.stderr
    return proc.stdout.strip() == "PRESENT"


def test_a_tree_with_all_three_files_has_the_setup_screens(tmp_path: Path) -> None:
    assert _is_present(tmp_path, _tree_with_the_three_files(tmp_path))


@pytest.mark.parametrize("missing", sorted(WIZARD_FILES))
def test_a_tree_missing_any_one_of_the_three_files_has_no_setup_screens(
    tmp_path: Path, missing: str
) -> None:
    tree = _tree_with_the_three_files(tmp_path)
    (tree / WIZARD_FILES[missing]).unlink()
    assert not _is_present(tmp_path, tree)


def test_the_models_file_checked_is_the_one_the_seam_passes(tmp_path: Path) -> None:
    # the seam hands the wizard `--models ${_HAL0_WIZARD_MODELS_PATH}`; the check
    # must look at that same path
    proc = _run_group(
        [
            "bash",
            "-c",
            f'REPO_ROOT="{ROOT}"\nsource "{ANSWERS_SH}"\n_hal0_wizard_paths\n'
            'echo "${_HAL0_WIZARD_MODELS_PATH}"',
        ],
        timeout=30,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        env={"PATH": os.environ["PATH"]},
        cwd=tmp_path,
    )
    assert proc.stdout.strip() == str(ROOT / WIZARD_FILES["models"])
    assert (ROOT / WIZARD_FILES["models"]).is_file()


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
    args = (
        "--no-owui",
        "--models-dir=/srv/m",
        f"--answers={given}",
        "--reconfigure",
        "--bare",
        "--summary-json=/tmp/s.json",
        "--no-start",
    )
    env = {
        **stub,
        "STUB_DOC": DOC,
        "HAL0_FACTS_FILE": str(facts),
        "HAL0_ANSWERS_FILE": str(given),
        "HAL0_NONINTERACTIVE": "1",
    }
    proc = _collect(tmp_path, env, args=args, reconfigure=True)
    assert _state(proc)["RC"] == "0", proc.stdout + proc.stderr
    (rec,) = _records(tmp_path)
    argv = rec["argv"]
    work = Path(argv[argv.index("--out") + 1]).parent
    assert work.parent == tmp_path / "prefix" / "run" / "hal0-install"
    assert argv == [
        "--catalog",
        stub["HAL0_WIZARD_CATALOG"],
        "--models",
        f"{ROOT}/installer/manifests/setup-models.json",
        "--facts",
        str(facts),
        "--out",
        f"{work}/answers.json",
        "--secret-out",
        f"{work}/hf-token.env",
        "--chosen-folder-out",
        f"{work}/chosen-folder",
        "--screens-shown-out",
        f"{work}/screens-shown",
        "--hal0-version",
        "9.9.9",
        "--saved",
        f"{work}/saved.json",
        "--answers",
        f"{work}/given.json",
        "--headless",
        "--reconfigure",
        "--",
        "--no-owui",
        "--models-dir=/srv/m",
        "--bare",
        "--summary-json=/tmp/s.json",
        "--no-start",
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


def _given_refused(proc: subprocess.CompletedProcess[str], tmp_path: Path) -> list[str]:
    state = _state(proc)
    assert state["RC"] == "1" and state["APPLIED"] == "0", proc.stdout + proc.stderr
    assert _records(tmp_path) == [], "the wizard must not start"
    assert state["LEFT"] == ""
    lines = proc.stderr.strip().splitlines()
    assert lines[-1] == "Stopped: the answers document was not accepted.", lines
    return lines[:-1]


def test_a_given_fifo_is_refused_before_the_wizard_starts(
    tmp_path: Path, stub: dict[str, str]
) -> None:
    fifo = tmp_path / "given.json"
    os.mkfifo(fifo)
    proc = _collect(tmp_path, {**stub, "STUB_DOC": DOC, "HAL0_ANSWERS_FILE": str(fifo)})
    reasons = _given_refused(proc, tmp_path)
    assert len(reasons) == 1 and "not a regular file" in reasons[0], reasons


def test_a_given_oversized_file_is_refused_before_the_wizard_starts(
    tmp_path: Path, stub: dict[str, str]
) -> None:
    big = tmp_path / "given.json"
    big.write_bytes(b'{"version": 1, "x": "' + b"a" * (1 << 20) + b'"}')
    proc = _collect(tmp_path, {**stub, "STUB_DOC": DOC, "HAL0_ANSWERS_FILE": str(big)})
    reasons = _given_refused(proc, tmp_path)
    assert len(reasons) == 1 and "larger than 1 MiB" in reasons[0], reasons


def test_a_given_invalid_document_is_refused_by_the_reader_first(
    tmp_path: Path, stub: dict[str, str]
) -> None:
    bad = tmp_path / "given.json"
    bad.write_text('{"version": 1, "install": {"type": "bogus\\u001b[31m"}}', encoding="utf-8")
    proc = _collect(tmp_path, {**stub, "STUB_DOC": DOC, "HAL0_ANSWERS_FILE": str(bad)})
    reasons = _given_refused(proc, tmp_path)
    assert reasons and "\x1b" not in proc.stderr


def test_the_wizard_reads_a_checked_copy_of_the_given_file(
    tmp_path: Path, stub: dict[str, str]
) -> None:
    given = tmp_path / "given.json"
    body = b'{"version": 1, "memory": {"enabled": false}}\n'
    given.write_bytes(body)
    given.chmod(0o644)
    proc = _collect(tmp_path, {**stub, "STUB_DOC": DOC, "HAL0_ANSWERS_FILE": str(given)})
    assert _state(proc)["RC"] == "0", proc.stdout + proc.stderr
    (rec,) = _records(tmp_path)
    argv = rec["argv"]
    copy = Path(argv[argv.index("--answers") + 1])
    assert copy.parent == Path(argv[argv.index("--out") + 1]).parent
    assert copy != given
    assert bytes.fromhex(rec["answers_bytes"]) == body
    assert _state(proc)["LEFT"] == ""


# ── what the wizard prints ──────────────────────────────────────────────────


def test_the_wizards_output_is_filtered_before_the_log(
    tmp_path: Path, stub: dict[str, str]
) -> None:
    noise = "\x1b[31mred\x07\n" + "A" * 5000 + "\n" + "".join(f"line {i}\n" for i in range(100))
    env = {
        **stub,
        "STUB_RC": "2",
        "STUB_STDERR": noise,
        "STUB_STDOUT": "out\x1b]0;t\x07\n",
        "STUB_STDERR_HEX": (b"nul\x00byte \xff\xfe tail").hex(),
    }
    proc = _collect(tmp_path, env)
    assert _state(proc)["RC"] == "1"
    lines = proc.stderr.split("\n")
    assert lines[-1] == ""
    lines = lines[:-1]
    assert len(lines) == 20, lines
    assert all(len(ln) <= 300 for ln in lines)
    assert all(all(0x20 <= ord(c) < 0x7F for c in ln) for ln in lines)
    # the wizard's standard output reaches only the filter, never the installer's stdout
    assert "out" not in proc.stdout.split("RC=")[0]
    assert "out?]0;t?" in lines
    assert "?[31mred?" in lines and "A" * 300 in lines
    # the whole stays at 20 lines; a NUL and other bytes become '?'
    proc = _collect(
        tmp_path,
        {**stub, "STUB_RC": "2", "STUB_STDERR_HEX": (b"nul\x00byte \xff\xfe tail\n").hex()},
    )
    assert proc.stderr == "nul?byte ?? tail\n"


def test_no_headless_and_no_reconfigure_by_default(tmp_path: Path, stub: dict[str, str]) -> None:
    proc = _collect(tmp_path, {**stub, "STUB_DOC": DOC})
    assert _state(proc)["RC"] == "0", proc.stderr
    argv = _records(tmp_path)[0]["argv"]
    head = argv[: argv.index("--")]
    assert "--headless" not in head and "--reconfigure" not in head and "--answers" not in head
    assert argv[argv.index("--") + 1 :] == []


def test_reconfigure_flag_is_forwarded_before_the_separator(
    tmp_path: Path, stub: dict[str, str]
) -> None:
    proc = _collect(
        tmp_path, {**stub, "STUB_DOC": DOC}, args=("--reconfigure", "--no-pi"), reconfigure=True
    )
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


# ── unknown flags ───────────────────────────────────────────────────────────


def _flag_loop(tmp_path: Path, args: list[str], env: dict[str, str]) -> subprocess.CompletedProcess:
    block = _slice('SUMMARY_JSON_PATH=""', "# Banner first")
    script = f"""
set -euo pipefail
REPO_ROOT="{ROOT}"
source "{ANSWERS_SH}"
info() {{ printf 'INFO: %s\\n' "$*"; }}
warn() {{ printf 'WARN: %s\\n' "$*" >&2; }}
err() {{ printf 'ERR: %s\\n' "$*" >&2; }}
MODELS_DIR=""
DEV_MODE=0
NO_START=0
{block}
echo "LOOP DONE reconfigure=${{_HAL0_RECONFIGURE}} args=${{#_HAL0_INSTALL_ARGS[@]}}"
"""
    return _run_group(
        ["bash", "-c", script, "bash", *args],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        env={"PATH": os.environ["PATH"], **env},
    )


def test_unknown_flag_stops_when_wizard_present(tmp_path: Path, stub: dict[str, str]) -> None:
    for args in (["--no-hermse"], ["--no-owui", "--no-tls"], ["--box"], ["--answers", "/x"]):
        proc = _flag_loop(tmp_path, args, stub)
        assert proc.returncode == 1, args
        bad = next(a for a in args if a not in ("--no-owui",))
        assert proc.stderr.strip().splitlines() == [
            f"ERR: unknown flag: {bad} (run with --help for the list)"
        ], proc.stderr
        assert "LOOP DONE" not in proc.stdout
        assert "HAL0_SKIP_OPENWEBUI" not in proc.stdout


def test_unknown_flag_stops_before_help_runs(tmp_path: Path, stub: dict[str, str]) -> None:
    proc = _flag_loop(tmp_path, ["--help", "--bogus"], stub)
    assert proc.returncode == 1 and "Usage:" not in proc.stdout


def test_every_accepted_flag_passes_when_wizard_present(
    tmp_path: Path, stub: dict[str, str]
) -> None:
    args = [
        "--dev",
        "--no-start",
        "--models-dir=/srv/m",
        "--summary-json=/tmp/x.json",
        "--answers=/tmp/a.json",
        "--no-owui",
        "--no-comfyui",
        "--no-hermes",
        "--no-pi",
        "--no-agents",
        "--no-memory",
        "--no-brain-model",
        "--bare",
        "--bare-inference",
        "--reconfigure",
    ]
    proc = _flag_loop(tmp_path, args, stub)
    assert proc.returncode == 0, proc.stderr
    assert f"LOOP DONE reconfigure=1 args={len(args)}" in proc.stdout
    for flag in ("--help", "-h"):
        proc = _flag_loop(tmp_path, [flag], stub)
        assert proc.returncode == 0 and "--reconfigure" in proc.stdout


def test_reconfigure_is_documented_in_the_usage_text() -> None:
    usage = _slice("--help|-h)", "exit 0")
    assert "[--reconfigure]" in usage and "  --reconfigure " in usage


def test_usage_text_says_a_piped_run_can_show_the_screens() -> None:
    usage = " ".join(_slice("--help|-h)", "exit 0").split())
    assert "a piped (\\`curl … | bash\\`) or headless run takes the default." not in usage
    assert "setup screens" in usage and "terminal can be opened" in usage
    # and stays true for a tree without them
    assert "Without the setup screens" in usage


# ── exit 0 ──────────────────────────────────────────────────────────────────


def test_exit_0_applies_with_wizard_origin(tmp_path: Path, stub: dict[str, str]) -> None:
    doc = json.dumps(
        {
            "version": 1,
            "apps": {"hermes": {"enabled": True, "terminal_tool": True}},
            "memory": {"enabled": False},
        }
    )
    proc = _collect(tmp_path, {**stub, "STUB_DOC": doc})
    state = _state(proc)
    assert state["RC"] == "0", proc.stdout + proc.stderr
    assert state["APPLIED"] == "1" and state["RAN"] == "1" and state["ORIGIN"] == "wizard"
    assert state["TERMINAL"] == "1"
    assert "HAL0_SKIP_HINDSIGHT" in proc.stdout and "HAL0_HERMES_TERMINAL" in proc.stdout
    assert state["LEFT"] == ""


def test_terminal_tool_from_a_given_document_stays_off(
    tmp_path: Path, stub: dict[str, str]
) -> None:
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


# ── the terminal tool after a person confirmed the review screen ────────────

_TERMINAL_OFF = json.dumps({"version": 1, "apps": {"hermes": {"enabled": True}}})
_TERMINAL_ON = json.dumps(
    {"version": 1, "apps": {"hermes": {"enabled": True, "terminal_tool": True}}}
)


def test_the_marker_makes_the_reader_turn_the_terminal_tool_off(
    tmp_path: Path, stub: dict[str, str]
) -> None:
    proc = _collect(tmp_path, {**stub, "STUB_DOC": _TERMINAL_OFF, "STUB_SHOWN": "shown\n"})
    state = _state(proc)
    assert state["RC"] == "0", proc.stdout + proc.stderr
    assert state["ORIGIN"] == "wizard" and state["TERMINAL"] == "0"
    assert "HAL0_HERMES_TERMINAL" in _exported(proc)
    assert "WARN" not in proc.stderr
    assert state["LEFT"] == ""
    (record,) = _records(tmp_path)
    work = Path(record["shown_out"])
    assert work.name == "screens-shown"
    assert record["shown_existed"] is False
    assert not work.exists()


def test_the_marker_with_the_tool_ticked_keeps_it_on(tmp_path: Path, stub: dict[str, str]) -> None:
    proc = _collect(tmp_path, {**stub, "STUB_DOC": _TERMINAL_ON, "STUB_SHOWN": "shown\n"})
    state = _state(proc)
    assert state["RC"] == "0" and state["TERMINAL"] == "1", proc.stdout + proc.stderr


def test_no_marker_leaves_the_terminal_tool_alone(tmp_path: Path, stub: dict[str, str]) -> None:
    proc = _collect(tmp_path, {**stub, "STUB_DOC": _TERMINAL_OFF})
    state = _state(proc)
    assert state["RC"] == "0" and state["TERMINAL"] == "", proc.stdout + proc.stderr
    assert "HAL0_HERMES_TERMINAL" not in _exported(proc)
    assert "WARN" not in proc.stderr
    assert state["LEFT"] == ""
    (record,) = _records(tmp_path)
    assert record["shown_out"].endswith("/screens-shown")  # offered to the wizard, not written


@pytest.mark.parametrize(
    "raw",
    [
        b"other\n",
        b"Shown\n",
        b"shown",
        b"shown\n\n",
        b"shown\nshown\n",
        b"shown\nextra\n",
        b"sho\x00wn\n",
        b"shown\x00",
        b"shown\n\x00",
        b"shown \n",
        b"shown\r\n",
        b"\n",
        b"",
    ],
    ids=lambda raw: raw.hex() or "empty",
)
def test_a_malformed_marker_counts_as_no_screens_and_warns_once(
    tmp_path: Path, stub: dict[str, str], raw: bytes
) -> None:
    proc = _collect(tmp_path, {**stub, "STUB_DOC": _TERMINAL_OFF, "STUB_SHOWN_HEX": raw.hex()})
    state = _state(proc)
    assert state["RC"] == "0" and state["APPLIED"] == "1", proc.stdout + proc.stderr
    assert state["TERMINAL"] == ""
    assert proc.stderr.count("WARN") == 1 and "screens" in proc.stderr, proc.stderr
    assert state["LEFT"] == ""


@pytest.mark.parametrize("rc", ["10", "70", "2", "11", "130", "3"])
def test_a_marker_beside_a_failed_wizard_is_ignored(
    tmp_path: Path, stub: dict[str, str], rc: str
) -> None:
    proc = _collect(
        tmp_path,
        {**stub, "STUB_DOC": _TERMINAL_OFF, "STUB_SHOWN": "shown\n", "STUB_RC": rc},
    )
    state = _state(proc)
    assert state["RC"] != "0" and state["APPLIED"] == "0" and state["TERMINAL"] == ""
    assert state["LEFT"] == ""


def test_the_marker_is_ignored_without_screens(tmp_path: Path, stub: dict[str, str]) -> None:
    base = {**stub, "STUB_DOC": _TERMINAL_OFF, "STUB_SHOWN": "shown\n"}
    proc = _collect(tmp_path, {**base, "HAL0_NONINTERACTIVE": "1"})
    state = _state(proc)
    assert state["RC"] == "0" and state["TERMINAL"] == "", proc.stdout + proc.stderr
    assert "WARN" not in proc.stderr and state["LEFT"] == ""
    given = tmp_path / "given.json"
    given.write_text(_TERMINAL_OFF, encoding="utf-8")
    proc = _collect(tmp_path, {**base, "HAL0_ANSWERS_FILE": str(given)})
    state = _state(proc)
    assert state["RC"] == "0" and state["ORIGIN"] == "file" and state["TERMINAL"] == ""
    assert "WARN" not in proc.stderr and state["LEFT"] == ""


def test_the_marker_on_a_run_that_skips_hermes_still_says_off(
    tmp_path: Path, stub: dict[str, str]
) -> None:
    # The reader's rule holds on a run that does not provision Hermes. That the
    # installer then does nothing with the answer is pinned on the consumer side
    # (tests/installer/test_hermes_terminal_apply.py: HAL0_SKIP_HERMES=1 runs
    # nothing), not only here on the export.
    doc = json.dumps({"version": 1, "apps": {"hermes": {"enabled": False}}})
    proc = _collect(tmp_path, {**stub, "STUB_DOC": doc, "STUB_SHOWN": "shown\n"})
    state = _state(proc)
    assert state["RC"] == "0" and state["TERMINAL"] == "0", proc.stdout + proc.stderr
    assert "HAL0_SKIP_HERMES" in _exported(proc)


def test_the_documents_off_says_so_when_it_replaces_an_inherited_answer(
    tmp_path: Path, stub: dict[str, str]
) -> None:
    proc = _collect(
        tmp_path,
        {**stub, "STUB_DOC": _TERMINAL_OFF, "STUB_SHOWN": "shown\n", "HAL0_HERMES_TERMINAL": "1"},
    )
    state = _state(proc)
    assert state["RC"] == "0" and state["TERMINAL"] == "0", proc.stdout + proc.stderr
    lines = [ln for ln in proc.stdout.splitlines() if "replaces the HAL0_HERMES_TERMINAL" in ln]
    assert len(lines) == 1, proc.stdout
    assert (
        lines[0].startswith("INFO:") and "HAL0_HERMES_TERMINAL=1 from the environment" in lines[0]
    )


def test_no_replacement_line_without_an_inherited_answer(
    tmp_path: Path, stub: dict[str, str]
) -> None:
    proc = _collect(tmp_path, {**stub, "STUB_DOC": _TERMINAL_OFF, "STUB_SHOWN": "shown\n"})
    assert "replaces the HAL0_HERMES_TERMINAL" not in proc.stdout, proc.stdout


def test_hermes_terminal_has_two_readers_on_an_install() -> None:
    # `hal0 agent install hermes` (box without Hermes) and, on a box that
    # already has it, `hal0 agent reprovision hermes` after the apply block.
    # Both stay behind the HAL0_SKIP_HERMES check.
    call = '"${HAL0_BIN}" agent install hermes "${_hermes_install_args'
    assert TEXT.count(call) == 1
    head = TEXT.rindex(
        'if [[ -f "${AGENT_UNIT_DST}" && ! -x "/var/lib/hal0/venvs/hermes/bin/hermes" ]]; then',
        0,
        TEXT.index(call),
    )
    skip = TEXT.index('if [[ "${HAL0_SKIP_HERMES:-0}" -eq 1 ]]; then', head)
    assert head < skip < TEXT.index(call)
    assert TEXT.index(call) - skip < 6000
    apply_call = '"${HAL0_BIN}" agent reprovision hermes'
    assert TEXT.count(apply_call) == 1
    apply_head = TEXT.rindex('"${_hermes_was_provisioned}" -eq 1', 0, TEXT.index(apply_call))
    assert TEXT.index(call) < apply_head
    assert '"${HAL0_SKIP_HERMES:-0}" -ne 1' in TEXT[apply_head : TEXT.index(apply_call)]


def test_usage_text_does_not_say_an_unattended_run_never_enables_the_terminal_tool() -> None:
    usage = " ".join(_slice("--help|-h)", "exit 0").split())
    assert "non-interactive install never enables it" not in usage
    assert "HAL0_HERMES_TERMINAL=1" in usage
    assert "unattended" in usage


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
    return _run_group(
        ["bash", "-c", _seam_script(tmp_path)],
        timeout=60,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        env={"PATH": os.environ["PATH"], "HAL0_PLAIN": "1", **env},
        cwd=tmp_path,
    )


def _seam_script(tmp_path: Path, pre: str = "") -> str:
    seam = _slice("# answers-seam: begin", "# answers-seam: end")
    return f"""
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
{pre}
{seam}
echo "PAST THE SEAM"
"""


@pytest.mark.parametrize(
    "code,installer_code,report",
    [(2, 1, True), (10, 10, False), (11, 1, True), (70, 1, True), (130, 130, False), (3, 1, True)],
)
def test_exit_code_mapping(
    tmp_path: Path, stub: dict[str, str], code: int, installer_code: int, report: bool
) -> None:
    # the stub writes a document even so: a signal just after the write can do that
    env = {
        **stub,
        "STUB_DOC": DOC,
        "STUB_RC": str(code),
        "STUB_SECRET": f"HF_TOKEN={TOKEN}\n",
        "STUB_STDERR": "hal0 setup: stub says no\n",
    }
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


SLOW_READER = """
import os, signal, sys, time
signal.signal(signal.SIGINT, signal.SIG_DFL)
open(os.environ["STUB_SLEEP"], "w").close()
time.sleep(30)
"""


@pytest.mark.parametrize("stage", ["wizard", "reader"])
def test_an_interrupt_before_the_wizard_handles_it_is_a_quiet_130(
    tmp_path: Path, stub: dict[str, str], stage: str
) -> None:
    # Ctrl-C reaches the whole process group. The stub has no handler of its
    # own yet, so it dies of the signal, as the real wizard would before its
    # handler is installed; so does the reader checking a given file.
    marker = tmp_path / "sleeping"
    env = {
        "PATH": os.environ["PATH"],
        "HAL0_PLAIN": "1",
        **stub,
        "STUB_DOC": DOC,
        "STUB_SLEEP": str(marker),
    }
    pre = ""
    if stage == "reader":
        slow = tmp_path / "slow_reader.py"
        slow.write_text(SLOW_READER, encoding="utf-8")
        given = tmp_path / "given.json"
        given.write_text(DOC, encoding="utf-8")
        env["HAL0_ANSWERS_FILE"] = str(given)
        pre = f'_HAL0_ANSWERS_READER="{slow}"'
    proc = subprocess.Popen(
        ["bash", "-c", _seam_script(tmp_path, pre)],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        env=env,
        cwd=tmp_path,
        start_new_session=True,
    )
    try:
        deadline = time.monotonic() + 20
        while not marker.exists():
            assert proc.poll() is None and time.monotonic() < deadline, proc.communicate()
            time.sleep(0.05)
        os.killpg(proc.pid, signal.SIGINT)
        out, err = proc.communicate(timeout=20)
    finally:
        # the group can outlive bash, so kill it whether or not bash has exited
        _kill_group(proc.pid)
        with contextlib.suppress(subprocess.TimeoutExpired):
            proc.wait(timeout=5)
    assert proc.returncode == 130, (proc.returncode, out, err)
    assert "PAST THE SEAM" not in out
    log = (tmp_path / "report.log").read_text() if (tmp_path / "report.log").exists() else ""
    assert log == "", log
    assert list((tmp_path / "prefix" / "run" / "hal0-install").iterdir()) == []
    if stage == "reader":
        assert _records(tmp_path) == [], "the wizard must not start after an interrupt"


# A child of the seam that dies of the interrupt while bash has not yet seen it
# (Ctrl-C reaches the group one process at a time). The reader stand-in counts
# its calls and sends the signal to itself on the chosen one; any other call
# runs the real reader. The wrapper stands in for ${PY}: it does the same for
# the one python call that STUB_DIE names, and runs the real interpreter for
# the rest.
COUNTING_READER = """
import os, runpy, signal, sys
counter = os.environ["STUB_COUNTER"]
n = int(open(counter).read()) + 1 if os.path.exists(counter) else 1
open(counter, "w").write(str(n))
if n == int(os.environ.get("STUB_DIE_CALL", "0")):
    signal.signal(signal.SIGINT, signal.SIG_DFL)
    os.kill(os.getpid(), signal.SIGINT)
    signal.pause()
sys.argv = [os.environ["STUB_REAL_READER"], *sys.argv[1:]]
runpy.run_path(os.environ["STUB_REAL_READER"], run_name="__main__")
"""

PY_WRAPPER = """#!/bin/sh
case "$STUB_DIE:$3:$4" in
    pylookup:*"(3, 10)"*) kill -INT $$ ;;
    pyversion:*"(3, 12)"*) kill -INT $$ ;;
    copy:*:given) kill -INT $$ ;;
    savedcopy:*:saved) kill -INT $$ ;;
esac
exec "$STUB_REAL_PY" "$@"
"""

# name -> (reader call that dies, python step that dies, shell hook,
#          given document, saved answers present, wizard starts before the interrupt)
INTERRUPT_POINTS = {
    "reader-first-check": (1, "", "", True, False, False),
    "copy-step": (0, "copy", "", True, False, False),
    "reader-second-check": (2, "", "", True, False, False),
    "apply-after-wizard": (3, "", "", True, False, True),
    "work-directory": (0, "", "_hal0_wizard_lstat() { kill -INT $BASHPID; }", False, False, False),
    "saved-copy": (0, "savedcopy", "", False, True, False),
    "saved-store": (1, "", "", False, True, False),
    "python-lookup": (0, "pylookup", "", False, False, True),
    "python-version-check": (0, "pyversion", "", False, False, False),
}


@pytest.mark.parametrize("point", sorted(INTERRUPT_POINTS))
def test_a_child_that_dies_of_the_interrupt_before_bash_sees_it_is_a_quiet_130(
    tmp_path: Path, stub: dict[str, str], point: str
) -> None:
    die_call, die_py, hook, given_doc, saved, wizard_starts = INTERRUPT_POINTS[point]
    reader = tmp_path / "counting_reader.py"
    reader.write_text(COUNTING_READER, encoding="utf-8")
    wrapper = tmp_path / "py-wrapper.sh"
    wrapper.write_text(PY_WRAPPER, encoding="utf-8")
    wrapper.chmod(0o700)
    env = {
        "PATH": os.environ["PATH"],
        "HAL0_PLAIN": "1",
        **stub,
        "STUB_DOC": DOC,
        "STUB_COUNTER": str(tmp_path / "reader-calls"),
        "STUB_DIE_CALL": str(die_call),
        "STUB_DIE": die_py,
        "STUB_REAL_PY": sys.executable,
        "STUB_REAL_READER": str(ROOT / "installer" / "lib" / "answers_reader.py"),
    }
    if given_doc:
        given = tmp_path / "given.json"
        given.write_text(DOC, encoding="utf-8")
        env["HAL0_ANSWERS_FILE"] = str(given)
    if saved:
        _saved(tmp_path, json.loads(DOC))
    pre = f'PY="{wrapper}"\n_HAL0_ANSWERS_READER="{reader}"\n{hook}'
    proc = _run_group(
        ["bash", "-c", _seam_script(tmp_path, pre)],
        timeout=30,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        env=env,
        cwd=tmp_path,
    )
    assert proc.returncode == 130, (proc.returncode, proc.stdout, proc.stderr)
    assert "PAST THE SEAM" not in proc.stdout
    log = (tmp_path / "report.log").read_text() if (tmp_path / "report.log").exists() else ""
    assert log == "", log
    for text in ("not accepted", "could not make", "no Python", "not a file only root wrote"):
        assert text not in proc.stderr, proc.stderr
    assert "Failure report saved" not in proc.stdout + proc.stderr
    run = tmp_path / "prefix" / "run" / "hal0-install"
    assert not run.exists() or list(run.iterdir()) == []
    assert (_records(tmp_path) != []) is wizard_starts, (
        "the wizard must not start after an interrupt"
    )


# The Python-first step runs before the seam on every run, so an interrupt there
# ends the install the way an interrupt in the seam does: status 130, quietly,
# no failure report, and the install does not carry on. resolve_main_python is
# a stand-in with four behaviours; the real function and block are cut out of
# install.sh.
RESOLVE_STUBS = {
    # a child that dies of the interrupt before bash has seen it
    "child-dies": "kill -INT $BASHPID",
    # bash itself gets the interrupt while the child runs, and the child then
    # carries on and succeeds
    "shell-gets-it": 'kill -INT $$; sleep 0.3; echo "python3.12"',
    "fails": "return 1",
    "works": 'echo "python3.12"',
}


def _python_first_script(tmp_path: Path, how: str) -> str:
    fn_head = TEXT.index("_hal0_resolve_main_python_or_die() {")
    fn = TEXT[fn_head : TEXT.index("\n}\n", fn_head) + 3]
    region = _slice("# file-origin: begin", "# file-origin: end")
    return f"""
set -euo pipefail
IFS=$'\\n\\t'
PY="no-such-python-xyz"
CURRENT_STEP="Pre-flight checks"
source "{ROOT}/installer/lib/ui.sh"
source "{ROOT}/installer/lib/failure-report.sh"
source "{ANSWERS_SH}"
hal0_write_failure_report() {{ echo "REPORT WRITTEN" >> "{tmp_path}/report.log"; echo "{tmp_path}/r.txt"; }}
trap 'hal0_report_on_exit "$?"' EXIT
trap 'echo "ERR TRAP" >> "{tmp_path}/report.log"; _HAL0_REPORT_WRITTEN=1; exit 1' ERR
python_venv_hint() {{ echo hint; }}
pkg_install_cmd() {{ echo install-python; }}
resolve_main_python() {{ {RESOLVE_STUBS[how]}; }}
_main_python_usable=0
{fn}
{region}
echo "PAST THE STEP"
"""


@pytest.mark.parametrize("how", ["child-dies", "shell-gets-it"])
def test_an_interrupt_while_python_is_installed_first_is_a_quiet_130(
    tmp_path: Path, how: str
) -> None:
    proc = _run_group(
        ["bash", "-c", _python_first_script(tmp_path, how)],
        timeout=30,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        env={"PATH": os.environ["PATH"], "HAL0_PLAIN": "1"},
        cwd=tmp_path,
    )
    assert proc.returncode == 130, (proc.returncode, proc.stdout, proc.stderr)
    assert "PAST THE STEP" not in proc.stdout
    log = (tmp_path / "report.log").read_text() if (tmp_path / "report.log").exists() else ""
    assert log == "", log
    for text in ("not found", "below hal0", "using python3.12", "Failure report saved"):
        assert text not in proc.stdout + proc.stderr, proc.stdout + proc.stderr


def test_python_installed_first_still_dies_when_it_cannot_be_resolved(tmp_path: Path) -> None:
    proc = _run_group(
        ["bash", "-c", _python_first_script(tmp_path, "fails")],
        timeout=30,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        env={"PATH": os.environ["PATH"], "HAL0_PLAIN": "1"},
        cwd=tmp_path,
    )
    assert proc.returncode == 1, (proc.stdout, proc.stderr)
    assert "python interpreter 'no-such-python-xyz' not found" in proc.stderr
    assert "PAST THE STEP" not in proc.stdout
    assert "REPORT WRITTEN" in (tmp_path / "report.log").read_text()


def test_python_installed_first_goes_on_when_it_is_resolved(tmp_path: Path) -> None:
    proc = _run_group(
        ["bash", "-c", _python_first_script(tmp_path, "works")],
        timeout=30,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        env={"PATH": os.environ["PATH"], "HAL0_PLAIN": "1"},
        cwd=tmp_path,
    )
    assert proc.returncode == 0, (proc.stdout, proc.stderr)
    assert "PAST THE STEP" in proc.stdout and not (tmp_path / "report.log").exists()
    assert "using python3.12" in proc.stdout


BLOCKED_ON_FIFO = """
import os, sys
with open(sys.argv[2], "w") as fh:
    fh.write(str(os.getpid()))
open(sys.argv[1]).read()  # blocks on opening: nothing ever writes to the FIFO
"""


def _wait_for_pid(pidfile: Path, seconds: float = 5) -> int:
    deadline = time.monotonic() + seconds
    while time.monotonic() < deadline:
        text = pidfile.read_text(encoding="utf-8") if pidfile.exists() else ""
        if text:
            return int(text)
        time.sleep(0.05)
    raise AssertionError(f"the blocking child never wrote {pidfile}")


def _wait_gone(pid: int, seconds: float = 5) -> bool:
    deadline = time.monotonic() + seconds
    while time.monotonic() < deadline:
        try:
            os.kill(pid, 0)
        except ProcessLookupError:
            return True
        time.sleep(0.1)
    return False


def _blocker(tmp_path: Path) -> tuple[str, Path]:
    """The command line of a child that blocks opening a FIFO nobody writes to."""
    fifo = tmp_path / "blocker.fifo"
    os.mkfifo(fifo)
    pidfile = tmp_path / "blocker.pid"
    child = tmp_path / "blocked_on_fifo.py"
    child.write_text(BLOCKED_ON_FIFO, encoding="utf-8")
    return f'"{sys.executable}" "{child}" "{fifo}" "{pidfile}"', pidfile


def test_a_timed_out_run_leaves_no_process_behind(tmp_path: Path) -> None:
    command, pidfile = _blocker(tmp_path)
    # not the last command, so bash forks it instead of exec-ing it: a grandchild
    after = f"{command}; true"
    pid = 0
    gone = False
    try:
        with pytest.raises(subprocess.TimeoutExpired):
            _collect(tmp_path, args=(), after=after, timeout=6)
        pid = _wait_for_pid(pidfile)
        gone = _wait_gone(pid)
        assert gone, f"process {pid} survived the timed-out run"
    finally:
        if pid and not gone:
            with contextlib.suppress(ProcessLookupError):
                os.kill(pid, signal.SIGKILL)


def test_a_grandchild_outliving_its_parent_is_killed_when_the_run_ends(tmp_path: Path) -> None:
    # bash exits at once; the grandchild stays blocked, with its output sent
    # away so that no pipe holds the run open. The group outlives its leader.
    command, pidfile = _blocker(tmp_path)
    script = (
        f"{command} >/dev/null 2>&1 &\n"
        f'for _ in $(seq 100); do [ -s "{pidfile}" ] && break; sleep 0.05; done\n'
    )
    pid = 0
    gone = False
    try:
        proc = _run_group(
            ["bash", "-c", script],
            timeout=20,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            cwd=tmp_path,
        )
        assert proc.returncode == 0
        pid = _wait_for_pid(pidfile)
        gone = _wait_gone(pid)
        assert gone, f"process {pid} survived the end of the run"
    finally:
        if pid and not gone:
            with contextlib.suppress(ProcessLookupError):
                os.kill(pid, signal.SIGKILL)


def test_the_installer_sets_no_interrupt_trap_of_its_own() -> None:
    # hal0_run_wizard puts its own back with `trap - INT`; that is only right
    # while install.sh has none to restore.
    for line in TEXT.splitlines():
        stripped = line.strip()
        assert not (stripped.startswith("trap ") and stripped.endswith(" INT")), line


# ── the secret file ─────────────────────────────────────────────────────────


def test_secret_file_read_and_removed(tmp_path: Path, stub: dict[str, str]) -> None:
    proc = _collect(
        tmp_path,
        {**stub, "STUB_DOC": DOC, "STUB_SECRET": f"HF_TOKEN={TOKEN}\n"},
        after='[[ "${_HAL0_WIZARD_HF_TOKEN}" == "' + TOKEN + '" ]] && echo MATCH',
    )
    state = _state(proc)
    assert state["RC"] == "0" and state["TOKEN_SET"] == "yes", proc.stdout + proc.stderr
    assert "MATCH" in proc.stdout
    assert state["TOKEN_IN_CHILD"] == "no"
    assert state["LEFT"] == ""
    assert "WARN" not in proc.stderr


@pytest.mark.parametrize(
    "text",
    [
        f"OTHER={TOKEN}\n",
        f"HF_TOKEN={TOKEN} x\n",
        f"HF_TOKEN={TOKEN}\nHF_TOKEN=b\n",
        "HF_TOKEN=\n",
        f"HF_TOKEN=$({TOKEN})\n",
        f"HF_TOKEN={TOKEN}\x1b[2J\n",
        f" HF_TOKEN={TOKEN}\n",
        # a trailing '=' is refused, not dropped; so are a missing newline and a NUL
        f"HF_TOKEN={TOKEN}=\n",
        f"HF_TOKEN={TOKEN}==\n",
        f"HF_TOKEN={TOKEN}",
        "HF_TOKEN\n",
        f"HF_TOKEN={TOKEN[:6]}\x00{TOKEN[6:]}\n",
        f"HF_TOKEN={TOKEN}\n\x00",
        f"HF_TOKEN={TOKEN}\n\n",
    ],
)
def test_secret_file_with_other_name_ignored(
    tmp_path: Path, stub: dict[str, str], text: str
) -> None:
    proc = _collect(tmp_path, {**stub, "STUB_DOC": DOC, "STUB_SECRET_HEX": text.encode().hex()})
    state = _state(proc)
    assert state["RC"] == "0" and state["TOKEN_SET"] == "", proc.stdout + proc.stderr
    assert proc.stderr.count("WARN") == 1
    assert TOKEN not in proc.stdout + proc.stderr
    assert state["LEFT"] == ""


def test_secret_never_printed(tmp_path: Path, stub: dict[str, str]) -> None:
    for rc in ("0", "2", "130"):
        proc = _collect(
            tmp_path, {**stub, "STUB_DOC": DOC, "STUB_RC": rc, "STUB_SECRET": f"HF_TOKEN={TOKEN}\n"}
        )
        assert TOKEN not in proc.stdout and TOKEN not in proc.stderr
        assert _state(proc)["TOKEN_IN_CHILD"] == "no"
    # nor does the wizard itself see one in its environment
    assert not any(r["token_in_env"] for r in _records(tmp_path))


def test_the_token_reaches_the_installers_token_variable() -> None:
    head = TEXT.index("# ── Operator input: model store + HuggingFace token")
    block = TEXT[head : TEXT.index('info "Pull destination', head)]
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


# ── the folder the person chose, and the box's existing store ───────────────


def _store_doc(path: str, flm: str | None = None) -> str:
    store = {"path": path}
    if flm:
        store["flm_path"] = flm
    return json.dumps({"version": 1, "model_store": store})


CHECK = 'hal0_store_is_operator_choice "${HAL0_MODELS_DIR}" && echo OWN=yes || echo OWN=no'


def test_chosen_folder_honoured_only_when_equal(tmp_path: Path, stub: dict[str, str]) -> None:
    proc = _collect(
        tmp_path,
        {
            **stub,
            "STUB_DOC": _store_doc("/home/alice/models"),
            "STUB_CHOSEN": "/home/alice/models\n",
        },
        after=CHECK,
    )
    state = _state(proc)
    assert state["RC"] == "0", proc.stderr
    assert state["CHOSEN"] == "/home/alice/models" and state["OWN"] == "yes"
    assert state["LEFT"] == ""
    for line in (
        "/home/alice/other\n",
        "/home/alice/models/\n",
        "/home/alice/models \n",
        "/home/alice/modelsX\n",
        "",
    ):
        proc = _collect(
            tmp_path,
            {**stub, "STUB_DOC": _store_doc("/home/alice/models"), "STUB_CHOSEN": line},
            after=CHECK,
        )
        state = _state(proc)
        assert state["RC"] == "0" and state["CHOSEN"] == "" and state["OWN"] == "no", line
    # a file that is not exactly one line is refused (one warning), not read on
    # its first line; so is one with a NUL byte, which bash would drop
    for raw in (
        b"/home/alice/models\nextra\n",
        b"/home/alice/models\n\n",
        b"/home/alice/mo\x00dels\n",
        b"/home/alice/models\n\x00",
        b"/home/alice/models",
    ):
        proc = _collect(
            tmp_path,
            {**stub, "STUB_DOC": _store_doc("/home/alice/models"), "STUB_CHOSEN_HEX": raw.hex()},
            after=CHECK,
        )
        state = _state(proc)
        assert state["RC"] == "0" and state["CHOSEN"] == "" and state["OWN"] == "no", raw
        assert proc.stderr.count("WARN") == 1 and "chosen" in proc.stderr, proc.stderr
        assert state["LEFT"] == ""
    # and the doubled slash the reader folds: compared with the exported text
    proc = _collect(
        tmp_path,
        {
            **stub,
            "STUB_DOC": _store_doc("//home/alice/models"),
            "STUB_CHOSEN": "/home/alice/models\n",
        },
        after=CHECK,
    )
    assert _state(proc)["OWN"] == "yes"


def test_chosen_folder_ignored_when_headless_or_absent(
    tmp_path: Path, stub: dict[str, str]
) -> None:
    base = {**stub, "STUB_DOC": _store_doc("/home/alice/models")}
    proc = _collect(tmp_path, base, after=CHECK)
    assert _state(proc)["OWN"] == "no"
    for extra in ({"HAL0_NONINTERACTIVE": "1"},):
        proc = _collect(
            tmp_path, {**base, **extra, "STUB_CHOSEN": "/home/alice/models\n"}, after=CHECK
        )
        state = _state(proc)
        assert state["RC"] == "0" and state["CHOSEN"] == "" and state["OWN"] == "no"
    given = tmp_path / "given.json"
    given.write_text(_store_doc("/home/alice/models"), encoding="utf-8")
    proc = _collect(
        tmp_path,
        {**base, "HAL0_ANSWERS_FILE": str(given), "STUB_CHOSEN": "/home/alice/models\n"},
        after=CHECK,
    )
    assert _state(proc)["OWN"] == "no"
    # only on exit 0
    proc = _collect(
        tmp_path, {**base, "STUB_RC": "130", "STUB_CHOSEN": "/home/alice/models\n"}, after=CHECK
    )
    assert _state(proc)["CHOSEN"] == ""


def test_the_two_reasons_are_never_taken_from_the_environment(
    tmp_path: Path, stub: dict[str, str]
) -> None:
    env = {
        **stub,
        "STUB_DOC": _store_doc("/home/alice/models"),
        "_HAL0_WIZARD_CHOSEN_STORE": "/home/alice/models",
        "_HAL0_SAVED_STORE": "/home/alice/models",
    }
    proc = _collect(tmp_path, env, after=CHECK)
    state = _state(proc)
    assert state["RC"] == "0" and state["OWN"] == "no", proc.stdout + proc.stderr


def test_existing_store_from_saved_answers_is_exempt(tmp_path: Path, stub: dict[str, str]) -> None:
    _saved(tmp_path, json.loads(_store_doc("/home/alice/models")))
    proc = _collect(
        tmp_path,
        {**stub, "STUB_DOC": _store_doc("/home/alice/models"), "HAL0_NONINTERACTIVE": "1"},
        after=CHECK,
    )
    state = _state(proc)
    assert state["RC"] == "0", proc.stderr
    assert state["SAVED_STORE"] == "/home/alice/models" and state["OWN"] == "yes"
    # mismatch
    proc = _collect(tmp_path, {**stub, "STUB_DOC": _store_doc("/home/alice/other")}, after=CHECK)
    assert _state(proc)["OWN"] == "no"
    # planted: not root-only, so not used at all
    _saved(tmp_path, json.loads(_store_doc("/home/alice/models")), mode=0o644)
    proc = _collect(tmp_path, {**stub, "STUB_DOC": _store_doc("/home/alice/models")}, after=CHECK)
    state = _state(proc)
    assert state["SAVED_STORE"] == "" and state["OWN"] == "no"
    # missing
    (tmp_path / "var" / "secrets" / "install-answers.json").unlink()
    proc = _collect(tmp_path, {**stub, "STUB_DOC": _store_doc("/home/alice/models")}, after=CHECK)
    assert _state(proc)["OWN"] == "no"


def test_a_saved_copy_the_reader_refuses_gives_no_exemption(
    tmp_path: Path, stub: dict[str, str]
) -> None:
    _saved(tmp_path, {"version": 1, "model_store": {"path": "/etc/models"}})
    proc = _collect(
        tmp_path,
        {**stub, "STUB_DOC": _store_doc("/home/alice/models")},
        after="hal0_store_is_operator_choice /etc/models && echo OWN=yes || echo OWN=no",
    )
    assert _state(proc)["OWN"] == "no" and _state(proc)["SAVED_STORE"] == ""


def test_store_from_given_document_is_not_exempt(tmp_path: Path, stub: dict[str, str]) -> None:
    given = tmp_path / "given.json"
    given.write_text(_store_doc("/home/alice/models"), encoding="utf-8")
    proc = _collect(
        tmp_path,
        {**stub, "STUB_DOC": _store_doc("/home/alice/models"), "HAL0_ANSWERS_FILE": str(given)},
        after=CHECK,
    )
    state = _state(proc)
    assert state["RC"] == "0" and state["MODELS"] == "/home/alice/models"
    assert state["OWN"] == "no"


def test_the_store_sink_treats_the_operators_choice_like_models_dir(tmp_path: Path) -> None:
    block = _slice('ui_step "Filesystem layout"', 'info "directories under')
    store = "/home/alice/models"
    for chosen, expect in ((store, "MKDIR"), ("", "DIE")):
        script = f"""
set -euo pipefail
PREFIX=/p ETC_DIR=/e VAR_DIR=/v UNIT_DIR=/u DEV_MODE=0
MODELS_DIR="{store}"
MODELS_DIR_FROM_DOC=1
_HAL0_WIZARD_CHOSEN_STORE="{chosen}"
{_helpers()}
ui_step() {{ :; }}
info() {{ echo "INFO: $*"; }}
die() {{ echo "DIE: $*"; exit 1; }}
nofollow() {{ echo "NOFOLLOW $*"; return 3; }}
mkdir() {{ echo "MKDIR $*"; }}
stat() {{ printf '41ed 1000\\n'; }}
{block}
echo DONE
"""
        proc = _run_group(
            ["bash", "-c", script], stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True
        )
        assert expect in proc.stdout, proc.stdout + proc.stderr
        if expect == "MKDIR":
            assert "MKDIR -p" in proc.stdout and store in proc.stdout and "DONE" in proc.stdout


@pytest.mark.parametrize("reason", ["chosen", "saved"])
def test_a_store_inside_the_default_store_keeps_the_strict_rule(
    tmp_path: Path, reason: str
) -> None:
    block = _slice('ui_step "Filesystem layout"', 'info "directories under')
    var = tmp_path / "var"
    (var / "models").mkdir(parents=True)
    store = f"{var}/models/x"
    script = f"""
set -euo pipefail
PREFIX=/p ETC_DIR=/e VAR_DIR="{var}" UNIT_DIR=/u DEV_MODE=0
MODELS_DIR="{store}"
MODELS_DIR_FROM_DOC=1
_HAL0_WIZARD_CHOSEN_STORE="{store if reason == "chosen" else ""}"
_HAL0_SAVED_STORE="{store if reason == "saved" else ""}"
{_helpers()}
ui_step() {{ :; }}
info() {{ echo "INFO: $*"; }}
die() {{ echo "DIE: $*"; exit 1; }}
nofollow() {{ echo "NOFOLLOW $*"; return 0; }}
mkdir() {{ echo "MKDIR $*"; }}
for p in "{store}" "{store}/" "{var}/models" "{var}//models/./x/"; do
    hal0_store_is_operator_choice "$p" && echo "OWN $p=yes" || echo "OWN $p=no"
done
_HAL0_WIZARD_CHOSEN_STORE="{var}/models" hal0_store_is_operator_choice "{var}/models/" \\
    && echo "DEFAULT=yes" || echo "DEFAULT=no"
_HAL0_SAVED_STORE="{var}/models2" hal0_store_is_operator_choice "{var}/models2" \\
    && echo "BESIDE=yes" || echo "BESIDE=no"
{block}
echo DONE
"""
    proc = _run_group(
        ["bash", "-c", script], stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True
    )
    out = proc.stdout
    assert "DONE" in out, out + proc.stderr
    for p in (store, f"{store}/", f"{var}/models", f"{var}//models/./x/"):
        assert f"OWN {p}=no" in out, out
    assert "DEFAULT=no" in out and "BESIDE=yes" in out, out
    mkdirs = [ln for ln in out.splitlines() if ln.startswith("MKDIR")]
    assert mkdirs and all(store not in ln for ln in mkdirs), mkdirs
    assert f"{var}/models" in mkdirs[0]
    calls = [ln for ln in out.splitlines() if ln.startswith("NOFOLLOW")]
    assert (
        calls
        and calls[-1].startswith("NOFOLLOW dir --create")
        and calls[-1].endswith(f"--expect adopt {store}")
    ), calls
    assert "your own choice" not in out


# ── the NPU folder from a document ──────────────────────────────────────────


def _flm(
    tmp_path: Path, flm: str, store: str, default: str, *, chosen: str = "", saved: str = ""
) -> subprocess.CompletedProcess[str]:
    script = f"""
set -euo pipefail
DEV_MODE=0
{_helpers()}
source "{ANSWERS_SH}"
_HAL0_WIZARD_CHOSEN_STORE="{chosen}"
_HAL0_SAVED_STORE="{saved}"
info() {{ echo "INFO: $*"; }}
warn() {{ echo "WARN: $*"; }}
nofollow() {{ echo "NOFOLLOW $*"; }}
export HAL0_FLM_MODELS_DIR="{flm}"
HAL0_ANSWERS_EXPORTED=(HAL0_NONINTERACTIVE HAL0_MODELS_DIR HAL0_FLM_MODELS_DIR)
hal0_doc_flm_prepare "$HAL0_FLM_MODELS_DIR" "{store}" "{default}"
echo "FLM=${{HAL0_FLM_MODELS_DIR-unset}} EXPORTED=${{HAL0_ANSWERS_EXPORTED[*]}}"
bash -c 'echo "CHILD=${{HAL0_FLM_MODELS_DIR-unset}}"'
"""
    return _run_group(
        ["bash", "-c", script], stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True
    )


def test_npu_folder_from_document_only_inside_default_or_operator_store(tmp_path: Path) -> None:
    default = tmp_path / "var" / "models"
    default.mkdir(parents=True)
    (tmp_path / "data" / "store").mkdir(parents=True)
    store = f"{tmp_path}/data/store"
    # inside the default store: created
    proc = _flm(tmp_path, f"{default}/npu", str(default), str(default))
    assert "NOFOLLOW dir --create" in proc.stdout and "WARN" not in proc.stdout, proc.stdout
    # under root-only real folders outside the default store: no longer created (C3)
    proc = _flm(tmp_path, f"{store}/npu", store, str(default))
    assert "NOFOLLOW" not in proc.stdout and proc.stdout.count("WARN:") == 1, proc.stdout
    assert "set HAL0_FLM_MODELS_DIR" in proc.stdout
    # inside a store the person chose on screen, or the box's existing store: created
    for kw in ({"chosen": store}, {"saved": store}):
        proc = _flm(tmp_path, f"{store}/a/npu", store, str(default), **kw)
        lines = [ln for ln in proc.stdout.splitlines() if ln.startswith("NOFOLLOW")]
        assert lines == [
            f"NOFOLLOW dir --create --owner root:root --mode 0755 --expect root {store}/a",
            f"NOFOLLOW dir --create --owner 1000:hal0 --mode 2775 --expect adopt {store}/a/npu",
        ], proc.stdout
        assert "WARN" not in proc.stdout
    # a store that is someone's choice does not make another store's folder one
    proc = _flm(tmp_path, f"{store}/npu", store, str(default), chosen=f"{tmp_path}/data")
    assert "NOFOLLOW" not in proc.stdout and proc.stdout.count("WARN:") == 1


def test_refused_npu_folder_is_unset(tmp_path: Path) -> None:
    default = tmp_path / "var" / "models"
    default.mkdir(parents=True)
    (tmp_path / "data" / "store").mkdir(parents=True)
    store = f"{tmp_path}/data/store"
    for flm, st in ((f"{store}/npu", store), (f"{tmp_path}/elsewhere/npu", store)):
        proc = _flm(tmp_path, flm, st, str(default))
        assert "FLM=unset" in proc.stdout and "CHILD=unset" in proc.stdout, proc.stdout
        assert "EXPORTED=HAL0_NONINTERACTIVE HAL0_MODELS_DIR\n" in proc.stdout + "\n"
    # a link inside the default store: refused, unset
    (default / "lnk").symlink_to(tmp_path)
    proc = _flm(tmp_path, f"{default}/lnk/npu", str(default), str(default))
    assert "FLM=unset" in proc.stdout
    # accepted: kept
    proc = _flm(tmp_path, f"{default}/npu", str(default), str(default))
    assert f"FLM={default}/npu" in proc.stdout and "HAL0_FLM_MODELS_DIR" in proc.stdout


def test_no_npu_device_drops_a_documents_npu_folder() -> None:
    head = TEXT.index("    if [[ -e /dev/accel/accel0 ]]; then")
    block = TEXT[head : TEXT.index('ui_step "', head)]
    assert "hal0_answers_unset_doc_flm" in block


# ── Python first ────────────────────────────────────────────────────────────


def _python_region(tmp_path: Path, *, usable: int, have: str = "") -> list[str]:
    region = _slice("# file-origin: begin", "# file-origin: end")
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir(exist_ok=True)
    for name in have.split():
        p = bin_dir / name
        p.write_text("#!/bin/bash\nexit 0\n", encoding="utf-8")
        p.chmod(0o755)
    log = tmp_path / "calls.log"
    log.unlink(missing_ok=True)
    script = f"""
set -u
PATH="{bin_dir}"
LOG="{log}"
PY=python3
source "{ANSWERS_SH}"
info() {{ echo "info: $*" >> "$LOG"; }}
_hal0_resolve_main_python_or_die() {{ echo "resolve $*" >> "$LOG"; }}
_main_python_usable={usable}
{region}
echo "usable=$_main_python_usable" >> "$LOG"
"""
    _run_group(
        ["/bin/bash", "-c", script], stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True
    )
    return log.read_text().splitlines()


def test_python_installed_first_when_missing(tmp_path: Path) -> None:
    calls = _python_region(tmp_path, usable=0)
    msg = "info: Installing Python, which hal0 needs before it can ask anything."
    assert calls == [msg, "resolve install-first", "usable=1"]
    # an interpreter that is new enough exists: resolved quietly, no message
    calls = _python_region(tmp_path, usable=0, have="python3.13")
    assert calls == ["resolve install-first", "usable=1"]
    # the default one is usable: nothing
    assert _python_region(tmp_path, usable=1) == ["usable=1"]


def test_the_wizard_refuses_an_old_python(tmp_path: Path, stub: dict[str, str]) -> None:
    fake = tmp_path / "oldpy"
    fake.write_text(
        f'#!/bin/bash\n[[ "$*" == *version_info* ]] && exit 1\nexec {sys.executable} "$@"\n',
        encoding="utf-8",
    )
    fake.chmod(0o755)
    script_env = {**stub, "STUB_DOC": DOC}
    proc = _run_group(
        [
            "bash",
            "-c",
            f"""
set -u
PY="{fake}" REPO_ROOT="{ROOT}" DEV_MODE=1 PREFIX="{tmp_path}/p2" VAR_DIR="{tmp_path}/v2"
source "{ANSWERS_SH}"
err() {{ echo "ERR: $*" >&2; }}
warn() {{ echo "WARN: $*" >&2; }}
_HAL0_INSTALL_ARGS=()
rc=0; hal0_run_wizard || rc=$?; echo "RC=$rc"
""",
        ],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        env={"PATH": os.environ["PATH"], **script_env},
    )
    assert "RC=1" in proc.stdout and "3.12" in proc.stderr, proc.stdout + proc.stderr
    assert _records(tmp_path) == []


# ── the CPU-only prompt ─────────────────────────────────────────────────────


def test_cpu_only_prompt_is_gone_and_consent_is_the_variable() -> None:
    assert "_confirm_cpu_only" not in TEXT
    head = TEXT.index('if [[ "${DEV_MODE}" -eq 0 ]]; then\n    # gpu_rc was captured')
    block = TEXT[head : TEXT.index("# ── Operator input", head)]
    for code in ("NO_KFD", "NO_DEVICE"):
        branch = block[block.index(f"gpu_rc == HAL0_GPU_RC_{code}") :]
        branch = re.split(r"\n    (?:elif|fi)\b", branch, maxsplit=1)[0]
        assert '"${HAL0_ALLOW_CPU_ONLY:-0}" == "1"' in branch, code
        assert "elif" not in branch, code
        assert "/dev/tty" not in branch and "read " not in branch, code
        assert "exit 1" in branch.split("else", 1)[1], code


def _gpu_gate_script(code: str) -> str:
    head = TEXT.index('if [[ "${DEV_MODE}" -eq 0 ]]; then\n    # gpu_rc was captured')
    block = TEXT[head : TEXT.index("# ── Operator input", head)]
    return f"""
set -euo pipefail
DEV_MODE=0
HAL0_GPU_RC_BROKEN_GID=11
HAL0_GPU_RC_NO_KFD=12
HAL0_GPU_RC_KFD_GID=13
HAL0_GPU_RC_NO_DEVICE=14
gpu_rc=${{HAL0_GPU_RC_{code}}}
info() {{ echo "INFO: $*"; }}
warn() {{ echo "WARN: $*"; }}
err() {{ echo "ERR: $*"; }}
_cpu_only_image_gate() {{ return 0; }}
_cpu_only_remedy_lines() {{ echo "REMEDY-MARKER"; }}
hal0_lxc_kind() {{ echo none; }}
{block}
echo "CONTINUED"
"""


@pytest.mark.parametrize("code", ["NO_KFD", "NO_DEVICE"])
@pytest.mark.parametrize("consent", [None, "0", "yes"])
def test_gpu_refusal_exits_unless_consent_is_exactly_one(code: str, consent: str | None) -> None:
    env = {"PATH": os.environ["PATH"]}
    if consent is not None:
        env["HAL0_ALLOW_CPU_ONLY"] = consent
    proc = _run_group(
        ["bash", "-c", _gpu_gate_script(code)],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        env=env,
    )
    assert proc.returncode == 1, proc.stdout + proc.stderr
    assert "REMEDY-MARKER" in proc.stdout
    assert "CONTINUED" not in proc.stdout
    assert "WARN:" not in proc.stdout


@pytest.mark.parametrize("code", ["NO_KFD", "NO_DEVICE"])
def test_gpu_consent_continues_with_a_warning(code: str) -> None:
    proc = _run_group(
        ["bash", "-c", _gpu_gate_script(code)],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        env={"PATH": os.environ["PATH"], "HAL0_ALLOW_CPU_ONLY": "1"},
    )
    assert proc.returncode == 0, proc.stdout + proc.stderr
    assert "WARN:" in proc.stdout and "CPU-only" in proc.stdout
    assert "CONTINUED" in proc.stdout
    assert "REMEDY-MARKER" not in proc.stdout


# ── a folder the flag or the environment chose stays theirs ─────────────────


def _md(
    *,
    args: tuple[str, ...] = (),
    env_models: str | None = None,
    doc: str,
    ran: int,
    pre_flm: str = "",
    doc_flm: str = "",
) -> str:
    """The real flag loop of install.sh (from MODELS_DIR= to the banner), then the
    answers-models-dir block, with what the seam leaves behind in between."""
    loop = _slice('MODELS_DIR="${HAL0_MODELS_DIR:-}"', "# Banner first")
    block = _slice("# answers-models-dir: begin", "# answers-models-dir: end")
    exported = "HAL0_NONINTERACTIVE HAL0_MODELS_DIR" + (" HAL0_FLM_MODELS_DIR" if doc_flm else "")
    script = f"""
set -euo pipefail
REPO_ROOT="{ROOT}"
DEV_MODE=0
NO_START=0
source "{ANSWERS_SH}"
{_helpers()}
info() {{ echo "INFO: $*"; }}
warn() {{ echo "WARN: $*"; }}
err() {{ echo "ERR: $*"; }}
{loop}
echo "LOOP flag=${{MODELS_DIR_FLAG_GIVEN}} explicit=${{MODELS_DIR_EXPLICIT}}"
HAL0_ANSWERS_APPLIED=1
HAL0_WIZARD_RAN={ran}
HAL0_ANSWERS_SAVED=abc
_pre_seam_flm="{pre_flm}"
export HAL0_MODELS_DIR="{doc}"
{"export HAL0_FLM_MODELS_DIR=" + doc_flm if doc_flm else ""}
HAL0_ANSWERS_EXPORTED=({exported})
{block}
echo "FROM_DOC=${{MODELS_DIR_FROM_DOC}} DIR=${{MODELS_DIR}} SAVED=${{HAL0_ANSWERS_SAVED}} EXPORTED=${{HAL0_ANSWERS_EXPORTED[*]}}"
"""
    env = {"PATH": os.environ["PATH"]}
    if env_models is not None:
        env["HAL0_MODELS_DIR"] = env_models
    proc = _run_group(
        ["bash", "-c", script, "bash", *args],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        env=env,
    )
    assert proc.returncode == 0, proc.stdout + proc.stderr
    return proc.stdout.strip()


def test_models_dir_flag_alone_agreeing_with_the_document_keeps_the_saved_copy() -> None:
    out = _md(args=("--models-dir=/home/a/m",), doc="/home/a/m", ran=1)
    # what the script really produces for the flag alone
    assert "LOOP flag=1 explicit=0" in out
    assert "WARN" not in out, out
    assert out.endswith(
        "FROM_DOC=0 DIR=/home/a/m SAVED=abc EXPORTED=HAL0_NONINTERACTIVE HAL0_MODELS_DIR"
    ), out


def test_models_dir_flag_alone_differing_from_the_document_warns_and_keeps_none() -> None:
    out = _md(args=("--models-dir=/flag",), doc="/doc", ran=1)
    assert "LOOP flag=1 explicit=0" in out
    assert out.count("WARN: --models-dir was given") == 1, out
    assert "FROM_DOC=0 DIR=/flag SAVED= " in out
    # the same without the wizard
    out = _md(args=("--models-dir=/flag",), doc="/doc", ran=0)
    assert "WARN:" in out and "FROM_DOC=0 DIR=/flag SAVED= " in out


def test_a_folder_from_the_environment_stays_the_operators() -> None:
    # HAL0_MODELS_DIR is read by the wizard too
    out = _md(env_models="/home/a/m", doc="/home/a/m", ran=1)
    assert "LOOP flag=0 explicit=1" in out
    assert "FROM_DOC=0 DIR=/home/a/m SAVED=abc" in out and "WARN" not in out
    # without the wizard, today's rule: the document's value is the document's
    assert "FROM_DOC=1" in _md(env_models="/home/a/m", doc="/home/a/m", ran=0)
    # a different folder from the document is still the document's
    assert "FROM_DOC=1 DIR=/srv/m" in _md(env_models="/home/a/m", doc="/srv/m", ran=1)


def test_the_same_folder_is_compared_in_normal_form() -> None:
    # the wizard hands back the folder without a trailing slash or a `.`
    out = _md(env_models="/home/alice/models/", doc="/home/alice/models", ran=1)
    assert "FROM_DOC=0 DIR=/home/alice/models/ SAVED=abc" in out and "WARN" not in out
    out = _md(args=("--models-dir=/home//alice/./models/",), doc="/home/alice/models", ran=1)
    assert (
        "FROM_DOC=0 SAVED=abc" in out.replace("DIR=/home//alice/./models/ ", "")
        and "WARN" not in out
    ), out
    # a different folder in normal form is still different
    out = _md(env_models="/home/alice/models/", doc="/home/alice/models2", ran=1)
    assert "FROM_DOC=1 DIR=/home/alice/models2" in out
    out = _md(args=("--models-dir=/home/alice/models/x/..",), doc="/home/alice/models", ran=1)
    assert "WARN: --models-dir was given" in out
    # the NPU folder from the environment too
    out = _md(doc="/d", ran=1, pre_flm="/d//npu/", doc_flm="/d/npu")
    assert out.endswith("EXPORTED=HAL0_NONINTERACTIVE HAL0_MODELS_DIR"), out


def test_path_norm_is_text_only() -> None:
    script = f"""
{_helpers()}
for p in /home/alice/models/ //a//b/ /a/././b /a/./ /. / /a/. /a/.b/ /a/../b /a/b///; do
    printf '%s=%s\\n' "$p" "$(hal0_path_norm "$p")"
done
"""
    proc = _run_group(
        ["bash", "-c", script], stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True
    )
    got = dict(line.split("=", 1) for line in proc.stdout.splitlines())
    assert got == {
        "/home/alice/models/": "/home/alice/models",
        "//a//b/": "/a/b",
        "/a/././b": "/a/b",
        "/a/./": "/a",
        "/.": "/",
        "/": "/",
        "/a/.": "/a",
        "/a/.b/": "/a/.b",
        "/a/../b": "/a/../b",
        "/a/b///": "/a/b",
    }
    fn = _helpers()[_helpers().index("hal0_path_norm() {") :]
    fn = fn[: fn.index("\n}\n")]
    for call in ("stat", "readlink", "realpath", "cd ", "-e ", "-d "):
        assert call not in fn, call


def test_an_npu_folder_from_the_environment_stays_the_environments() -> None:
    out = _md(doc="/d", ran=1, pre_flm="/d/npu", doc_flm="/d/npu")
    assert out.endswith("EXPORTED=HAL0_NONINTERACTIVE HAL0_MODELS_DIR"), out
    out = _md(doc="/d", ran=1, pre_flm="", doc_flm="/d/npu")
    assert out.endswith("HAL0_FLM_MODELS_DIR"), out
