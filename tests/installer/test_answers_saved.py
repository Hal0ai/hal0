"""hal0_save_answers: keep the applied document, minus secrets and the terminal grant."""

from __future__ import annotations

import base64
import json
import os
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
ANSWERS_SH = ROOT / "installer" / "lib" / "answers.sh"
READER = ROOT / "installer" / "lib" / "answers_reader.py"

DOC = {
    "version": 1,
    "model_store": {"path": "/mnt/models"},
    "apps": {"hermes": {"enabled": True, "terminal_tool": True}, "pi": {"enabled": False}},
    "memory": {"enabled": False},
    "network": {"port": 9000, "bind_host": "0.0.0.0"},
    "huggingface": {"token": "hf_secret_value"},
    "slots": [{"capability": "chat", "name": "a", "port": 9000}],
}


def _bash(script: str, tmp_path: Path, nofollow_rc: int = 0,
          dir_rc: int = 0) -> subprocess.CompletedProcess[str]:
    """Source answers.sh with `nofollow` and `warn` stubbed.

    `nofollow write` logs its argv to nofollow.args and its body to nofollow.body;
    `nofollow dir` logs its argv to nofollow.dir.args.
    """
    full = f"""
set -u
PY="{sys.executable}"
VAR_DIR="{tmp_path}/var/lib/hal0"
warn() {{ echo "WARN: $*" >&2; }}
nofollow() {{
    if [[ "$1" == dir ]]; then printf '%s\\n' "$*" >>"{tmp_path}/nofollow.dir.args"; return {dir_rc}; fi
    printf '%s\\n' "$*" >"{tmp_path}/nofollow.args"; cat >"{tmp_path}/nofollow.body"; return {nofollow_rc}
}}
source "{ANSWERS_SH}"
{script}
"""
    return subprocess.run(["bash", "-c", full], capture_output=True, text=True, check=False, cwd=tmp_path)


def _doc(tmp_path: Path, doc: dict) -> Path:
    path = tmp_path / "answers.json"
    path.write_text(json.dumps(doc), encoding="utf-8")
    return path


def test_saved_document_drops_terminal_tool_and_token(tmp_path: Path) -> None:
    doc = _doc(tmp_path, DOC)
    proc = _bash(f'HAL0_ANSWERS_FILE="{doc}"; hal0_collect_answers; hal0_save_answers; echo "rc=$?"', tmp_path)
    assert "rc=0" in proc.stdout, proc.stderr
    body = (tmp_path / "nofollow.body").read_text(encoding="utf-8")
    assert "hf_secret_value" not in body
    saved = json.loads(body)
    assert "huggingface" not in saved
    assert saved["apps"]["hermes"] == {"enabled": True}
    assert saved["apps"]["pi"] == {"enabled": False}
    assert saved["slots"] == [{"capability": "chat", "name": "a", "port": 9000}]
    assert saved["network"]["bind_host"] == "0.0.0.0"
    assert list(saved) == [k for k in DOC if k != "huggingface"]
    assert body.endswith("}\n") and body.startswith('{\n  "version"')


def test_saved_document_mode_and_owner_request(tmp_path: Path) -> None:
    doc = _doc(tmp_path, DOC)
    _bash(f'HAL0_ANSWERS_FILE="{doc}"; hal0_collect_answers; hal0_save_answers', tmp_path)
    args = (tmp_path / "nofollow.args").read_text(encoding="utf-8").split()
    assert args[:1] == ["write"]
    assert args[args.index("--owner") + 1] == "root:root"
    assert args[args.index("--mode") + 1] == "0600"
    assert "--require-trusted-dir" in args
    assert args[-1] == f"{tmp_path}/var/lib/hal0/secrets/install-answers.json"


def test_nothing_saved_without_a_document(tmp_path: Path) -> None:
    proc = _bash("hal0_collect_answers; hal0_save_answers; echo rc=$?", tmp_path)
    assert "rc=0" in proc.stdout, proc.stderr
    assert not (tmp_path / "nofollow.args").exists()
    assert "WARN" not in proc.stderr


def test_saved_document_reloads_with_file_origin(tmp_path: Path) -> None:
    doc = _doc(tmp_path, DOC)
    _bash(f'HAL0_ANSWERS_FILE="{doc}"; hal0_collect_answers; hal0_save_answers', tmp_path)
    saved = tmp_path / "saved.json"
    saved.write_text((tmp_path / "nofollow.body").read_text(encoding="utf-8"), encoding="utf-8")

    def lines(path: Path, origin: str) -> list[str]:
        proc = subprocess.run(
            [sys.executable, "-I", str(READER), "--origin", origin, "--", str(path)],
            capture_output=True, text=True, check=False,
        )
        assert proc.returncode == 0, proc.stderr
        return proc.stdout.splitlines()

    original = lines(doc, "wizard")
    assert "HAL0_HERMES_TERMINAL=1" in original
    expected = [x for x in original if not x.startswith("HAL0_HERMES_TERMINAL=")]
    assert lines(saved, "file") == expected
    assert lines(saved, "wizard") == expected  # even a wizard-origin replay cannot grant it


def test_failures_never_fail_the_install(tmp_path: Path) -> None:
    doc = _doc(tmp_path, DOC)
    proc = _bash(
        f'HAL0_ANSWERS_FILE="{doc}"; hal0_collect_answers; hal0_save_answers; echo rc=$?',
        tmp_path,
        nofollow_rc=1,
    )
    assert "rc=0" in proc.stdout
    assert proc.stderr.count("WARN") == 1
    assert "earlier saved copy was left as it was" in proc.stderr


def test_saved_form_is_taken_at_apply_time(tmp_path: Path) -> None:
    doc = _doc(tmp_path, DOC)
    proc = _bash(
        f'HAL0_ANSWERS_FILE="{doc}"; hal0_collect_answers; rm -f "{doc}"; hal0_save_answers; echo rc=$?',
        tmp_path,
    )
    assert "rc=0" in proc.stdout and "WARN" not in proc.stderr, proc.stderr
    assert json.loads((tmp_path / "nofollow.body").read_text(encoding="utf-8"))["version"] == 1


def test_a_reader_without_a_saved_form_fails_the_apply_and_saves_nothing(tmp_path: Path) -> None:
    fake = _fake_reader(tmp_path, "print('HAL0_PORT=9000')\n")
    proc = _bash(
        f'_HAL0_ANSWERS_READER="{fake}"; hal0_apply_answers /x/doc.json file; echo rc=$?; '
        'hal0_save_answers; echo "saved=[$HAL0_ANSWERS_SAVED] port=${HAL0_PORT:-unset}"',
        tmp_path,
    )
    assert proc.stdout.splitlines() == ["rc=1", "saved=[] port=unset"], proc.stderr
    assert not (tmp_path / "nofollow.args").exists()


def test_warnings_are_printed_once(tmp_path: Path) -> None:
    doc = _doc(tmp_path, DOC)
    proc = _bash(f'HAL0_ANSWERS_FILE="{doc}"; hal0_collect_answers; hal0_save_answers', tmp_path)
    assert proc.stderr.count("huggingface settings are not read") == 1, proc.stderr


# ── where the saved copy goes: root's own directory, never /etc/hal0 ────────


def test_saved_file_path_is_exported_by_collect_answers(tmp_path: Path) -> None:
    doc = _doc(tmp_path, DOC)
    for answers in (f'HAL0_ANSWERS_FILE="{doc}"; ', ""):
        proc = _bash(f'{answers}hal0_collect_answers; echo "[$HAL0_SAVED_ANSWERS_FILE]"; '
                     'bash -c \'echo "child:$HAL0_SAVED_ANSWERS_FILE"\'', tmp_path)
        want = f"{tmp_path}/var/lib/hal0/secrets/install-answers.json"
        assert proc.stdout.splitlines() == [f"[{want}]", f"child:{want}"], proc.stderr


def test_saved_file_path_is_not_taken_from_the_environment(tmp_path: Path) -> None:
    proc = _bash('HAL0_SAVED_ANSWERS_FILE=/etc/passwd; export HAL0_SAVED_ANSWERS_FILE; '
                 'hal0_collect_answers; echo "$HAL0_SAVED_ANSWERS_FILE"', tmp_path)
    assert proc.stdout.strip() == f"{tmp_path}/var/lib/hal0/secrets/install-answers.json"


def test_the_root_only_directory_is_ensured_before_the_write(tmp_path: Path) -> None:
    doc = _doc(tmp_path, DOC)
    _bash(f'HAL0_ANSWERS_FILE="{doc}"; hal0_collect_answers; hal0_save_answers', tmp_path)
    dir_args = (tmp_path / "nofollow.dir.args").read_text(encoding="utf-8").split()
    assert dir_args[:1] == ["dir"] and "--create" in dir_args
    assert dir_args[dir_args.index("--owner") + 1] == "root:root"
    assert dir_args[dir_args.index("--mode") + 1] == "0700"
    assert dir_args[dir_args.index("--expect") + 1] == "root"
    assert dir_args[-1] == f"{tmp_path}/var/lib/hal0/secrets"
    assert "--require-trusted-dir" in (tmp_path / "nofollow.args").read_text(encoding="utf-8")
    assert "etc/hal0" not in (tmp_path / "nofollow.args").read_text(encoding="utf-8")


def test_a_directory_that_is_not_root_owned_means_no_write_and_one_warning(tmp_path: Path) -> None:
    doc = _doc(tmp_path, DOC)
    proc = _bash(f'HAL0_ANSWERS_FILE="{doc}"; hal0_collect_answers; hal0_save_answers; echo rc=$?',
                 tmp_path, dir_rc=3)
    assert "rc=0" in proc.stdout and proc.stderr.count("WARN") == 1
    assert "not a root-owned directory" in proc.stderr
    assert not (tmp_path / "nofollow.args").exists()


def test_the_flag_that_demands_a_trusted_directory_is_kept() -> None:
    assert "--require-trusted-dir" in ANSWERS_SH.read_text(encoding="utf-8")


# ── variables holding document text are never inherited as exported ─────────


def test_inherited_exported_variables_do_not_leak_the_document(tmp_path: Path) -> None:
    doc = _doc(tmp_path, DOC)
    full = f"""
set -u
PY="{sys.executable}"
VAR_DIR="{tmp_path}/var/lib/hal0"
warn() {{ echo "WARN: $*" >&2; }}
source "{ANSWERS_SH}"
HAL0_ANSWERS_FILE="{doc}"
hal0_collect_answers
echo "rc=$?"
env | grep -c '^HAL0_ANSWERS_SAVED=' || true
env | grep -c '^HAL0_ANSWERS_APPLIED=' || true
bash -c 'echo "child:[${{HAL0_ANSWERS_SAVED:-}}]"'
"""
    proc = subprocess.run(
        ["bash", "-c", full], capture_output=True, text=True, check=False, cwd=tmp_path,
        env={"PATH": os.environ["PATH"], "HAL0_ANSWERS_SAVED": "inherited-secret",
             "HAL0_ANSWERS_APPLIED": "7"},
    )
    assert proc.stdout.splitlines() == ["rc=0", "0", "0", "child:[]"], proc.stderr
    assert "hf_secret_value" not in proc.stdout


# ── the saved form is the apply run's `#saved` line, decoded ────────────────


def _fake_reader(tmp_path: Path, body: str) -> Path:
    fake = tmp_path / "fake_reader.py"
    fake.write_text(body, encoding="utf-8")
    return fake


def test_the_saved_file_is_exactly_the_decoded_payload(tmp_path: Path) -> None:
    payload = '{\n  "version": 1,\n  "note": "caf\u00e9 = \\\\ \\"q\\""\n}\n'.encode("utf-8")
    line = "#saved " + base64.b64encode(payload).decode()
    fake = _fake_reader(
        tmp_path,
        "import sys\n"
        "sys.stderr.write('DeprecationWarning: something noisy\\n')\n"
        f"print('HAL0_PORT=9000')\nprint({line!r})\n",
    )
    proc = _bash(
        f'_HAL0_ANSWERS_READER="{fake}"; HAL0_ANSWERS_FILE=/x/doc.json; hal0_collect_answers; '
        'echo rc=$?; hal0_save_answers',
        tmp_path,
    )
    assert "rc=0" in proc.stdout, proc.stderr
    assert (tmp_path / "nofollow.body").read_bytes() == payload
    assert "WARN" not in proc.stderr


def test_the_real_reader_saved_line_and_the_saved_file_agree(tmp_path: Path) -> None:
    doc = _doc(tmp_path, DOC)
    proc = _bash(
        f'HAL0_ANSWERS_FILE="{doc}"; hal0_collect_answers; '
        f'printf "%s" "$HAL0_ANSWERS_SAVED" >"{tmp_path}/b64"; hal0_save_answers',
        tmp_path,
    )
    assert proc.returncode == 0, proc.stderr
    payload = base64.b64decode((tmp_path / "b64").read_text(encoding="ascii"), validate=True)
    assert (tmp_path / "nofollow.body").read_bytes() == payload
    emitted = subprocess.run([sys.executable, "-I", str(READER), "--emit-saved", str(doc)],
                             capture_output=True, check=False)
    assert emitted.returncode == 0 and emitted.stdout == payload


def test_a_rejected_document_leaves_nothing_to_save(tmp_path: Path) -> None:
    fake = _fake_reader(
        tmp_path,
        "import sys\nsys.stdout.write('{\"partial\": true}\\n')\nsys.stderr.write('error: bad\\n')\nsys.exit(2)\n",
    )
    proc = _bash(
        f'_HAL0_ANSWERS_READER="{fake}"; hal0_apply_answers /x/doc.json file; echo rc=$?; '
        'HAL0_ANSWERS_APPLIED=1; hal0_save_answers; echo "saved=[$HAL0_ANSWERS_SAVED]"',
        tmp_path,
    )
    assert proc.stdout.splitlines() == ["rc=2", "saved=[]"]
    assert "partial" not in proc.stderr
    assert not (tmp_path / "nofollow.args").exists()


def test_locals_named_like_inherited_exports_do_not_carry_the_document(tmp_path: Path) -> None:
    # `local out` keeps the export attribute of an environment variable `out`,
    # so the saved form would reach every child process started afterwards. The
    # spy stands in for `base64`, which the apply run starts after the locals
    # hold the document's values and its saved form.
    doc = _doc(tmp_path, DOC)
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    spy = bin_dir / "base64"
    spy.write_text(f'#!/bin/bash\nenv >"{tmp_path}/child.env"\n', encoding="utf-8")
    spy.chmod(0o755)
    full = f"""
set -u
PY="{sys.executable}"
VAR_DIR="{tmp_path}/var/lib/hal0"
PATH="{bin_dir}:$PATH"
source "{ANSWERS_SH}"
HAL0_ANSWERS_FILE="{doc}"
hal0_collect_answers
"""
    env = {"PATH": os.environ["PATH"], "output": "x", "line": "x", "name": "x",
           "value": "x", "names": "x", "values": "x", "saved": "x"}
    subprocess.run(["bash", "-c", full], capture_output=True, text=True, check=False, cwd=tmp_path, env=env)
    dump = (tmp_path / "child.env").read_text(encoding="utf-8")
    assert '"version"' not in dump and "HAL0_NONINTERACTIVE" not in dump.replace("HAL0_NONINTERACTIVE=1", "")
    assert "#saved" not in dump and "ewog" not in dump  # nor the base64 of '{\n'
    for name in ("output", "line", "name", "value", "names", "values", "saved"):
        assert f"\n{name}=x\n" in "\n" + dump  # the inherited value, not ours


def test_saved_copy_keeps_install_and_meta(tmp_path: Path) -> None:
    doc = _doc(tmp_path, {
        "version": 1,
        "install": {"type": "custom"},
        "meta": {"saved_at": "2026-11-28T09:30:00Z", "saved_by": "1.9.0"},
        "models": {"memory": "shared"},
        "npu": {"opt_in": "auto"},
        "huggingface": {"token_env": "HF_TOKEN"},
    })
    proc = _bash(f'HAL0_ANSWERS_FILE="{doc}"; hal0_collect_answers; hal0_save_answers; echo "rc=$?"', tmp_path)
    assert "rc=0" in proc.stdout, proc.stderr
    saved = json.loads((tmp_path / "nofollow.body").read_text(encoding="utf-8"))
    assert saved == {
        "version": 1,
        "install": {"type": "custom"},
        "meta": {"saved_at": "2026-11-28T09:30:00Z", "saved_by": "1.9.0"},
        "models": {"memory": "shared"},
        "npu": {"opt_in": "auto"},
    }
