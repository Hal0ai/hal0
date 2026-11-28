"""hal0_apply_answers: run the reader, check names, export all or nothing."""

from __future__ import annotations

import base64
import json
import re
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
ANSWERS_SH = ROOT / "installer" / "lib" / "answers.sh"
READER = ROOT / "installer" / "lib" / "answers_reader.py"


def _bash(script: str, tmp_path: Path, reader: Path | None = None) -> subprocess.CompletedProcess[str]:
    override = f'_HAL0_ANSWERS_READER="{reader}"' if reader else ""
    full = f"""
set -u
PY="{sys.executable}"
source "{ANSWERS_SH}"
{override}
{script}
"""
    return subprocess.run(
        ["bash", "-c", full], capture_output=True, text=True, check=False, cwd=tmp_path
    )


def _doc(tmp_path: Path, doc: dict) -> Path:
    path = tmp_path / "answers.json"
    path.write_text(json.dumps(doc), encoding="utf-8")
    return path


SAVED = "#saved " + base64.b64encode(b'{"version": 1}\n').decode()


def _fake(tmp_path: Path, *out: str) -> Path:
    """A stand-in reader that prints exactly the lines OUT and exits 0."""
    fake = tmp_path / "fake_reader.py"
    fake.write_text("import sys\nsys.stdout.write(" + repr("".join(f"{x}\n" for x in out)) + ")\n",
                    encoding="utf-8")
    return fake


def test_exports_listed_names(tmp_path: Path) -> None:
    doc = _doc(tmp_path, {"version": 1, "memory": {"enabled": False}, "network": {"port": 9000}})
    proc = _bash(
        f'hal0_apply_answers "{doc}" file; echo "rc=$?"; '
        'echo "$HAL0_NONINTERACTIVE $HAL0_SKIP_HINDSIGHT $HAL0_PORT $HAL0_ANSWERS_APPLIED"; '
        'bash -c \'echo "child:$HAL0_PORT"\'',
        tmp_path,
    )
    assert proc.stdout.splitlines() == ["rc=0", "1 1 9000 1", "child:9000"], proc.stderr


def test_name_off_the_list_aborts(tmp_path: Path) -> None:
    fake = tmp_path / "fake_reader.py"
    fake.write_text(
        "print('HAL0_PORT=1234')\nprint('PATH=/tmp/x')\n", encoding="utf-8"
    )
    proc = _bash(
        'before="$PATH"; hal0_apply_answers /x/doc.json file; echo "rc=$?"; '
        '[[ "$PATH" == "$before" ]] && echo path-same; echo "port=${HAL0_PORT:-unset}"',
        tmp_path,
        reader=fake,
    )
    assert proc.stdout.splitlines() == ["rc=1", "path-same", "port=unset"], proc.stderr
    assert "not allowed" in proc.stderr


def test_terminal_name_refused_for_file_origin(tmp_path: Path) -> None:
    fake = tmp_path / "fake_reader.py"
    fake.write_text("print('HAL0_HERMES_TERMINAL=1')\n", encoding="utf-8")
    proc = _bash(
        'hal0_apply_answers /x/doc.json file; echo "rc=$?"; echo "${HAL0_HERMES_TERMINAL:-unset}"',
        tmp_path,
        reader=fake,
    )
    assert proc.stdout.splitlines() == ["rc=1", "unset"]


def test_value_with_spaces_and_equals_survives(tmp_path: Path) -> None:
    path = "/mnt/my models/a=b"
    doc = _doc(tmp_path, {"version": 1, "model_store": {"path": path, "flm_path": f"{path}/x="}})
    proc = _bash(
        f'hal0_apply_answers "{doc}" file; printf "[%s]\\n[%s]\\n" "$HAL0_MODELS_DIR" '
        '"$HAL0_FLM_MODELS_DIR"',
        tmp_path,
    )
    assert proc.stdout.splitlines() == [f"[{path}]", f"[{path}/x=]"], proc.stderr


def test_shell_syntax_in_value_is_data(tmp_path: Path) -> None:
    # Even if a reader let such a value through, bash only ever exports it.
    marker = tmp_path / "pwned"
    value = f"https://x/$(touch {marker})`touch {marker}`;\"'*"
    fake = _fake(tmp_path, f"HAL0_PUBLIC_URL={value}", SAVED)
    proc = _bash(
        'hal0_apply_answers /x/doc.json file; printf "%s" "$HAL0_PUBLIC_URL"',
        tmp_path,
        reader=fake,
    )
    assert proc.stdout == value
    assert not marker.exists()


def test_reader_rejection_exports_nothing_and_passes_messages(tmp_path: Path) -> None:
    doc = _doc(tmp_path, {"version": 1, "memory": {"enabled": False}, "network": {"port": 0}})
    proc = _bash(
        f'hal0_apply_answers "{doc}" file; echo "rc=$?"; '
        'echo "${HAL0_NONINTERACTIVE:-unset}/${HAL0_SKIP_HINDSIGHT:-unset}"',
        tmp_path,
    )
    assert proc.stdout.splitlines() == ["rc=2", "unset/unset"]
    assert "network.port" in proc.stderr
    assert proc.stderr.rstrip().endswith("Stopped: the answers document was not accepted.")


def test_bad_origin_and_missing_python(tmp_path: Path) -> None:
    doc = _doc(tmp_path, {"version": 1})
    proc = _bash(f'hal0_apply_answers "{doc}" bogus; echo "rc=$?"', tmp_path)
    assert proc.stdout.strip() == "rc=1"
    # No usable ${PY} and no python3 on PATH at all: nothing can read the document.
    proc = _bash(f'PY=""; PATH=/nonexistent; hal0_apply_answers "{doc}" file; echo "rc=$?"', tmp_path)
    assert proc.stdout.strip() == "rc=1"
    assert "no Python 3.10 or newer" in proc.stderr


def test_exported_names_are_recorded_only_on_success(tmp_path: Path) -> None:
    doc = _doc(tmp_path, {"version": 1, "model_store": {"path": "/srv/m"}})
    proc = _bash(
        f'hal0_apply_answers "{doc}" file; echo "${{HAL0_ANSWERS_EXPORTED[*]}}"', tmp_path
    )
    assert proc.stdout.split() == ["HAL0_NONINTERACTIVE", "HAL0_MODELS_DIR"], proc.stderr
    bad = _doc(tmp_path, {"version": 2})
    proc = _bash(f'hal0_apply_answers "{bad}" file; echo "n=${{#HAL0_ANSWERS_EXPORTED[@]}}"', tmp_path)
    assert proc.stdout.strip() == "n=0"


def test_bind_host_not_accepted_from_a_reader(tmp_path: Path) -> None:
    fake = tmp_path / "fake_reader.py"
    fake.write_text("print('HAL0_BIND_HOST=127.0.0.1')\n", encoding="utf-8")
    proc = _bash('hal0_apply_answers /x/d.json file; echo "rc=$?"', tmp_path, reader=fake)
    assert proc.stdout.strip() == "rc=1"


def test_collect_answers_runs_apply_for_a_file(tmp_path: Path) -> None:
    doc = _doc(tmp_path, {"version": 1, "apps": {"hermes": {"terminal_tool": True}}})
    proc = _bash(
        f'HAL0_ANSWERS_FILE="{doc}"; hal0_collect_answers; echo "rc=$?"; '
        'echo "$HAL0_ANSWERS_APPLIED ${HAL0_HERMES_TERMINAL:-unset}"',
        tmp_path,
    )
    assert proc.stdout.splitlines() == ["rc=0", "1 unset"], proc.stderr


def test_allow_lists_match() -> None:
    sh = ANSWERS_SH.read_text(encoding="utf-8")
    block = re.search(r"_HAL0_ANSWER_NAMES=\(\n(.*?)\n\)", sh, re.S)
    assert block, "allow-list block not found in answers.sh"
    sh_names = block.group(1).split()
    assert sh_names, "allow-list in answers.sh parsed empty"
    py = READER.read_text(encoding="utf-8")
    tup = re.search(r"ALLOWED_NAMES = \(\n(.*?)\n\)", py, re.S)
    assert tup, "ALLOWED_NAMES not found in answers_reader.py"
    py_names = re.findall(r'"(HAL0_[A-Z0-9_]+)"', tup.group(1))
    assert py_names, "ALLOWED_NAMES in answers_reader.py parsed empty"
    assert sh_names == py_names
    assert len(set(sh_names)) == len(sh_names)
    assert "HAL0_BIND_HOST" not in sh_names


# ── interpreter choice: ${PY}, else any python3 on PATH that is 3.10+ ───────


def _fake_python3(tmp_path: Path, version: str) -> Path:
    """A `python3` on PATH reporting VERSION; it logs each run and runs the real one."""
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir(exist_ok=True)
    stub = bin_dir / "python3"
    major, minor = version.split(".")
    stub.write_text(
        "#!/bin/bash\n"
        f'echo "$*" >>"{tmp_path}/python3.calls"\n'
        'if [[ "$2" == "-c" ]]; then\n'
        f'    [[ "$3" == *"(3, 10)"* ]] && exit $(( {major} > 3 || ({major} == 3 && {minor} >= 10) ? 0 : 1 ))\n'
        "    exit 0\n"
        "fi\n"
        f'exec "{sys.executable}" "$@"\n',
        encoding="utf-8",
    )
    stub.chmod(0o755)
    return bin_dir


def _bash_path(script: str, tmp_path: Path, bin_dir: Path, py: str) -> subprocess.CompletedProcess[str]:
    full = f"""
set -u
PY="{py}"
PATH="{bin_dir}:$PATH"
source "{ANSWERS_SH}"
{script}
"""
    return subprocess.run(["bash", "-c", full], capture_output=True, text=True, check=False, cwd=tmp_path)


def test_unusable_py_falls_back_to_a_python3_on_path(tmp_path: Path) -> None:
    doc = _doc(tmp_path, {"version": 1, "network": {"port": 9000}})
    bin_dir = _fake_python3(tmp_path, "3.11")
    proc = _bash_path(f'hal0_apply_answers "{doc}" file; echo "rc=$? $HAL0_PORT"', tmp_path, bin_dir,
                      "/nonexistent/python")
    assert proc.stdout.strip() == "rc=0 9000", proc.stderr
    calls = (tmp_path / "python3.calls").read_text(encoding="utf-8")
    assert "answers_reader.py" in calls  # the reader ran with the stub


def test_python3_older_than_3_10_is_not_used(tmp_path: Path) -> None:
    doc = _doc(tmp_path, {"version": 1})
    bin_dir = _fake_python3(tmp_path, "3.9")
    proc = _bash_path(f'hal0_apply_answers "{doc}" file; echo "rc=$?"', tmp_path, bin_dir, "/nonexistent/python")
    assert proc.stdout.strip() == "rc=1" and "no Python 3.10 or newer" in proc.stderr
    assert "answers_reader.py" not in (tmp_path / "python3.calls").read_text(encoding="utf-8")


def test_a_working_py_is_preferred_over_path(tmp_path: Path) -> None:
    doc = _doc(tmp_path, {"version": 1})
    bin_dir = _fake_python3(tmp_path, "3.11")
    proc = _bash_path(f'hal0_apply_answers "{doc}" file; echo "rc=$?"', tmp_path, bin_dir, sys.executable)
    assert proc.stdout.strip() == "rc=0"
    assert not (tmp_path / "python3.calls").exists()


def test_hal0_answers_python_prints_the_choice(tmp_path: Path) -> None:
    bin_dir = _fake_python3(tmp_path, "3.12")
    proc = _bash_path("hal0_answers_python; echo rc=$?", tmp_path, bin_dir, "/nonexistent/python")
    assert proc.stdout.splitlines() == [f"{bin_dir}/python3", "rc=0"]
    proc = _bash_path("PATH=/nonexistent; hal0_answers_python; echo rc=$?", tmp_path, bin_dir, "")
    assert proc.stdout.strip() == "rc=1"


# ── the default store is passed in, not assumed (round 3) ───────────────────


def _argv_reader(tmp_path: Path) -> Path:
    fake = tmp_path / "argv_reader.py"
    fake.write_text(
        "import sys\n"
        f"open({str(tmp_path / 'argv.txt')!r}, 'a').write(repr(sys.argv[1:]) + '\\n')\n"
        "print('HAL0_PORT=1234')\n"
        f"print({SAVED!r})\n",
        encoding="utf-8",
    )
    return fake


def test_the_reader_runs_once_with_the_default_store_from_var_dir(tmp_path: Path) -> None:
    fake = _argv_reader(tmp_path)
    proc = _bash(
        f'VAR_DIR="{tmp_path}/dev/var"; hal0_apply_answers /x/doc.json file; echo "rc=$?"',
        tmp_path,
        reader=fake,
    )
    assert proc.stdout.splitlines() == ["rc=0"], proc.stderr
    calls = (tmp_path / "argv.txt").read_text(encoding="utf-8").splitlines()
    assert len(calls) == 1  # one read, one run: the saved form comes from the same run
    store = f"{tmp_path}/dev/var/models"
    assert eval(calls[0]) == ["--origin", "file", "--default-store", store, "--", "/x/doc.json"]


def test_without_var_dir_the_reader_keeps_its_own_default(tmp_path: Path) -> None:
    fake = _argv_reader(tmp_path)
    proc = _bash('unset VAR_DIR; hal0_apply_answers /x/doc.json file; echo "rc=$?"', tmp_path,
                 reader=fake)
    assert proc.stdout.splitlines() == ["rc=0"], proc.stderr
    calls = (tmp_path / "argv.txt").read_text(encoding="utf-8").splitlines()
    assert all("--default-store" not in call for call in calls)



# ── one read, one run: the `#saved` line ────────────────────────────────────


_CHECK = ('hal0_apply_answers /x/doc.json file; echo "rc=$?"; '
          'echo "port=${HAL0_PORT:-unset} n=${#HAL0_ANSWERS_EXPORTED[@]} saved=[${HAL0_ANSWERS_SAVED}]"')


def test_the_saved_line_is_kept_and_not_exported(tmp_path: Path) -> None:
    fake = _fake(tmp_path, "HAL0_PORT=1234", SAVED)
    proc = _bash(_CHECK + "; bash -c 'echo \"child:[${HAL0_ANSWERS_SAVED:-}]\"'", tmp_path, reader=fake)
    payload = SAVED[len("#saved "):]
    assert proc.stdout.splitlines() == ["rc=0", f"port=1234 n=1 saved=[{payload}]", "child:[]"], proc.stderr


@pytest.mark.parametrize("out", [
    ("HAL0_PORT=1234",),                          # no saved form at all
    ("HAL0_PORT=1234", SAVED, SAVED),             # a second #saved line
    ("HAL0_PORT=1234", SAVED, "HAL0_SKIP_PI=1"),  # anything after it
    (SAVED, "HAL0_PORT=1234"),
    ("HAL0_PORT=1234", "#saved "),                # empty payload
    ("HAL0_PORT=1234", "#saved not*base64!"),     # not base64 characters
    ("HAL0_PORT=1234", "#saved QUJD="),           # base64 characters that do not decode
    ("HAL0_PORT=1234", "#savedQUJD"),             # not the prefix: a malformed line
    ("HAL0_PORT=1234", "", SAVED),                # an empty line
    ("HAL0_PORT=1234", "# comment", SAVED),
])
def test_a_bad_saved_protocol_fails_the_apply_and_exports_nothing(tmp_path: Path,
                                                                 out: tuple[str, ...]) -> None:
    proc = _bash(_CHECK, tmp_path, reader=_fake(tmp_path, *out))
    assert proc.stdout.splitlines() == ["rc=1", "port=unset n=0 saved=[]"], (out, proc.stderr)
    assert proc.stderr.count("error:") == 1


def test_a_real_document_carries_its_saved_form(tmp_path: Path) -> None:
    doc = _doc(tmp_path, {"version": 1, "network": {"port": 9000},
                          "huggingface": {"token": "hf_secret"}})
    proc = _bash(f'hal0_apply_answers "{doc}" file; printf "%s" "$HAL0_ANSWERS_SAVED"', tmp_path)
    saved = json.loads(base64.b64decode(proc.stdout, validate=True))
    assert saved == {"version": 1, "network": {"port": 9000}}


# ── shell-side backstops behind the reader ──────────────────────────────────


@pytest.mark.parametrize("name", ["HAL0_PORT", "HAL0_MODELS_DIR", "HAL0_NONINTERACTIVE"])
def test_a_name_printed_twice_fails_the_apply_and_exports_nothing(tmp_path: Path, name: str) -> None:
    value = "/mnt/a" if name == "HAL0_MODELS_DIR" else "1"
    fake = _fake(tmp_path, "HAL0_SKIP_PI=1", f"{name}={value}", f"{name}={value}", SAVED)
    proc = _bash(_CHECK + '; echo "pi=${HAL0_SKIP_PI:-unset}"', tmp_path, reader=fake)
    assert proc.stdout.splitlines()[:2] == ["rc=1", "port=unset n=0 saved=[]"], proc.stderr
    assert proc.stdout.splitlines()[2] == "pi=unset"
    assert f"printed {name} twice" in proc.stderr and proc.stderr.count("error:") == 1


@pytest.mark.parametrize("name", ["HAL0_MODELS_DIR", "HAL0_FLM_MODELS_DIR"])
@pytest.mark.parametrize("bad", ["relative/x", "", "/mnt/café", "/mnt/$(id)", "/mnt/a`b`",
                                 "/mnt/a;b", "/mnt/a'b", '/mnt/a"b', "/mnt/a\\b", "/mnt/a*",
                                 "/mnt/[ab]", "/mnt/a\tb", "/mnt/a\x1b[31m", "/mnt/a%b",
                                 "/mnt/a#b", "/mnt/a!b", "/mnt/١"])
def test_a_folder_path_outside_the_plain_ascii_set_fails_the_apply(tmp_path: Path, name: str,
                                                                    bad: str) -> None:
    marker = tmp_path / "pwned"
    fake = _fake(tmp_path, "HAL0_PORT=1234", f"{name}={bad}", SAVED)
    proc = _bash(_CHECK + f'; echo "dir=[${{{name}:-unset}}]"', tmp_path, reader=fake)
    assert proc.stdout.splitlines() == ["rc=1", "port=unset n=0 saved=[]", "dir=[unset]"], proc.stderr
    assert "not a plain absolute path" in proc.stderr
    assert bad not in proc.stderr or bad == ""  # the value itself is not echoed
    assert not marker.exists()


def test_an_invalid_byte_in_a_folder_path_fails_the_apply(tmp_path: Path) -> None:
    fake = tmp_path / "fake_reader.py"
    fake.write_text(
        "import sys\n"
        "sys.stdout.buffer.write(b'HAL0_MODELS_DIR=/mnt/\\xff\\xfe\\n' + "
        f"{(SAVED + chr(10)).encode()!r})\n",
        encoding="utf-8",
    )
    for locale in ("C", "C.UTF-8"):
        proc = _bash(f"LC_ALL={locale}; " + _CHECK, tmp_path, reader=fake)
        assert proc.stdout.splitlines() == ["rc=1", "port=unset n=0 saved=[]"], (locale, proc.stderr)


@pytest.mark.parametrize("good", ["/mnt/my models/a=b", "/a-b_c.d+e@f,g:h~i", "/var/lib/hal0/models"])
def test_plain_folder_paths_pass_the_backstop(tmp_path: Path, good: str) -> None:
    fake = _fake(tmp_path, f"HAL0_MODELS_DIR={good}", f"HAL0_FLM_MODELS_DIR={good}/flm", SAVED)
    proc = _bash('hal0_apply_answers /x/doc.json file; echo "rc=$?"; '
                 'printf "[%s][%s]\\n" "$HAL0_MODELS_DIR" "$HAL0_FLM_MODELS_DIR"', tmp_path, reader=fake)
    assert proc.stdout.splitlines() == ["rc=0", f"[{good}][{good}/flm]"], proc.stderr


def test_other_values_are_not_held_to_the_path_set(tmp_path: Path) -> None:
    fake = _fake(tmp_path, "HAL0_ALLOWED_ORIGINS=https://a.example.com,http://b:8080", SAVED)
    proc = _bash('hal0_apply_answers /x/doc.json file; echo "rc=$? $HAL0_ALLOWED_ORIGINS"', tmp_path,
                 reader=fake)
    assert proc.stdout.strip() == "rc=0 https://a.example.com,http://b:8080", proc.stderr
