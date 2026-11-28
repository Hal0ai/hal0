"""hal0_save_answers: keep the applied document, minus secrets and the terminal grant."""

from __future__ import annotations

import json
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
    "slots": [{"name": "a"}],
}


def _bash(script: str, tmp_path: Path, nofollow_rc: int = 0) -> subprocess.CompletedProcess[str]:
    """Source answers.sh with `nofollow` and `warn` stubbed; the stub logs its argv and body."""
    full = f"""
set -u
PY="{sys.executable}"
ETC_DIR="{tmp_path}/etc/hal0"
warn() {{ echo "WARN: $*" >&2; }}
nofollow() {{ printf '%s\\n' "$*" >"{tmp_path}/nofollow.args"; cat >"{tmp_path}/nofollow.body"; return {nofollow_rc}; }}
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
    assert saved["slots"] == [{"name": "a"}]
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
    assert args[-1] == f"{tmp_path}/etc/hal0/install-answers.json"


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


def test_prepare_failure_warns_once_and_saves_nothing(tmp_path: Path) -> None:
    doc = _doc(tmp_path, DOC)
    proc = _bash(
        f'_hal0_prepare_saved_answers "/nonexistent/x.json"; HAL0_ANSWERS_APPLIED=1; '
        f'hal0_save_answers; echo rc=$?; echo "saved=[$HAL0_ANSWERS_SAVED]"; : "{doc}"',
        tmp_path,
    )
    assert "rc=0" in proc.stdout and "saved=[]" in proc.stdout
    assert proc.stderr.count("WARN") == 1 and "cannot read" in proc.stderr
    assert not (tmp_path / "nofollow.args").exists()


def test_warnings_are_printed_once(tmp_path: Path) -> None:
    doc = _doc(tmp_path, DOC)
    proc = _bash(f'HAL0_ANSWERS_FILE="{doc}"; hal0_collect_answers; hal0_save_answers', tmp_path)
    assert proc.stderr.count("huggingface settings are not read") == 1, proc.stderr
