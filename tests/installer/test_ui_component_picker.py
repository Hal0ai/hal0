"""Behaviour of ``ui_component_picker`` in installer/lib/ui.sh.

Each test drives the real bash function: keystrokes come from a file via
``UI_TTY_IN`` and the picker's own output goes to ``/dev/null`` via
``UI_TTY_OUT``, so no pty is needed.
"""

import subprocess
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[2]
UI_SH = REPO / "installer" / "lib" / "ui.sh"

SCRIPT = r"""
set -eu
source "{ui_sh}"
{plain_line}
rows=(
  $'openwebui\tOpen WebUI\tchat UI, podman container\ton\t'
  $'comfyui\tComfyUI\timage/video generation, starter models\ton\t'
  $'hermes\tHermes agent\tvenv + config + gateway; brings the Operator Board and MCP wiring\ton\t'
  $'pi\tPi agent\tterminal coding agent (CLI/TUI)\toff\t'
  $'hindsight\tHindsight memory\thindsight-api unit, Memory tab, Hermes memory bank\ton\tno usable Python 3.11-3.14'
  $'brain\tBrain model\t~1-3 GB pull; steward chat and tools\ton\t'
)
declare -A result=()
ui_component_picker rows result
for id in openwebui comfyui hermes pi hindsight brain; do
  printf '%s=%s\n' "$id" "${{result[$id]}}"
done
echo stderr-still-works >&2
"""


@pytest.fixture
def run_picker(tmp_path):
    def _run(keys, plain, term="xterm", tty_out="/dev/null", cols=None) -> dict:
        keyfile = tmp_path / "keys"
        keyfile.write_bytes(keys.encode("latin-1"))
        # _ui_init_colors forces UI_PLAIN=1 off a non-tty stdout, so the
        # cursor-path case resets it explicitly after sourcing.
        plain_line = "UI_PLAIN=1; HAL0_PLAIN=1" if plain else "UI_PLAIN=0"
        script = tmp_path / "drive.sh"
        script.write_text(SCRIPT.format(ui_sh=UI_SH, plain_line=plain_line))
        env = {
            "PATH": "/usr/bin:/bin",
            "UI_TTY_IN": str(keyfile),
            "UI_TTY_OUT": str(tty_out),
        }
        if term is not None:
            env["TERM"] = term
        if cols is not None:
            env["UI_TTY_COLS"] = str(cols)
        proc = subprocess.run(
            ["bash", str(script)], env=env, capture_output=True, text=True, timeout=30
        )
        assert proc.returncode == 0, proc.stderr
        assert "stderr-still-works" in proc.stderr, "picker leaked a stderr redirect"
        return dict(line.split("=", 1) for line in proc.stdout.splitlines())

    return _run


def test_enter_accepts_defaults(run_picker):
    out = run_picker(keys="\n", plain=True)
    assert out == {"openwebui": "on", "comfyui": "on", "hermes": "on",
                   "pi": "off", "hindsight": "unavailable", "brain": "on"}


def test_bare_preset_turns_every_available_row_off(run_picker):
    out = run_picker(keys="b\n", plain=True)
    assert set(out.values()) == {"off", "unavailable"}


def test_plain_pick_toggles_by_number(run_picker):
    out = run_picker(keys="p\n4\n1\n\n", plain=True)   # pi on, openwebui off
    assert out["pi"] == "on" and out["openwebui"] == "off"


def test_plain_pick_cannot_toggle_unavailable_row(run_picker):
    out = run_picker(keys="p\n5\n\n", plain=True)
    assert out["hindsight"] == "unavailable"


def test_cursor_pick_space_and_enter(run_picker):
    out = run_picker(keys="p \r", plain=False)            # toggles row 1 off
    assert out["openwebui"] == "off" and out["hermes"] == "on"


def test_read_failure_accepts_current_state(run_picker):
    out = run_picker(keys="", plain=True)                 # EOF at the preset line
    assert out["pi"] == "off" and out["brain"] == "on"


def test_plain_leading_zero_and_padded_answers(run_picker):
    out = run_picker(keys="p \n08\n 04 \n\n", plain=True)   # 08 ignored, pi on
    assert out["pi"] == "on" and out["openwebui"] == "on"


DEFAULTS = {"openwebui": "on", "comfyui": "on", "hermes": "on",
            "pi": "off", "hindsight": "unavailable", "brain": "on"}


def test_cursor_j_k_move(run_picker):
    assert run_picker(keys="pj \r", plain=False)["comfyui"] == "off"
    out = run_picker(keys="pjk \r", plain=False)
    assert out["openwebui"] == "off" and out["comfyui"] == "on"


@pytest.mark.parametrize("down", ["\x1b[B", "\x1bOB"])
def test_cursor_arrow_down_csi_and_ss3(run_picker, down):
    out = run_picker(keys="p" + down + " \r", plain=False)
    assert out["comfyui"] == "off" and out["openwebui"] == "on"


@pytest.mark.parametrize("up", ["\x1b[A", "\x1bOA"])
def test_cursor_arrow_up(run_picker, up):
    out = run_picker(keys="pjj" + up + " \r", plain=False)
    assert out["comfyui"] == "off" and out["hermes"] == "on"


def test_cursor_modified_arrow_is_ignored(run_picker):
    # Ctrl-Down must not leak "B" (bare preset) or move the cursor.
    out = run_picker(keys="p\x1b[1;5B \r", plain=False)
    assert out["openwebui"] == "off" and out["comfyui"] == "on"
    assert out["hermes"] == "on" and out["brain"] == "on"


def test_cursor_a_after_b_restores_defaults(run_picker):
    assert run_picker(keys="pba\r", plain=False) == DEFAULTS


def test_cursor_space_on_unavailable_row(run_picker):
    out = run_picker(keys="pjjjj \r", plain=False)
    assert out == DEFAULTS


def test_cursor_eof_accepts_current_state(run_picker):
    out = run_picker(keys="p ", plain=False)           # EOF, no Enter
    assert out["openwebui"] == "off" and out["pi"] == "off"
    assert run_picker(keys="", plain=False) == DEFAULTS


def test_cursor_unknown_keys_at_preset_line_reprompt(run_picker):
    # x and an arrow must neither commit defaults nor leak into the checklist.
    out = run_picker(keys="x\x1b[B\x1b[1;5Ap \r", plain=False)
    assert out["openwebui"] == "off" and out["comfyui"] == "on"


def test_plain_unknown_word_reprompts_and_words_work(run_picker):
    out = run_picker(keys="zzz\np\n4\n\n", plain=True)
    assert out["pi"] == "on"
    assert set(run_picker(keys="bare\n", plain=True).values()) == {"off", "unavailable"}
    assert run_picker(keys="pick\n4\n\n", plain=True)["pi"] == "on"
    assert run_picker(keys="all\n", plain=True) == DEFAULTS
    assert run_picker(keys="defaults\n", plain=True) == DEFAULTS


@pytest.mark.parametrize("term", ["dumb", None])
def test_dumb_or_unset_term_takes_plain_path(run_picker, term):
    # Plain-path input (lines) only works if the picker did not go char-wise.
    out = run_picker(keys="p\n4\n\n", plain=False, term=term)
    assert out["pi"] == "on"


def test_rows_fit_terminal_width(run_picker, tmp_path):
    import re
    for plain in (True, False):
        out_file = tmp_path / "tty-out"
        run_picker(keys="p j\r\n", plain=plain, cols=60, tty_out=out_file)
        raw = out_file.read_bytes().decode()
        text = re.sub(r"\x1b\[[0-9;?]*[A-Za-z]", "", raw)
        lines = [ln.rsplit("\r", 1)[-1] for ln in text.split("\n")]
        assert any("Hermes agent" in ln for ln in lines)
        assert max(len(ln) for ln in lines) < 60, lines


DEFAULTS = {"openwebui": "on", "comfyui": "on", "hermes": "on",
            "pi": "off", "hindsight": "unavailable", "brain": "on"}


@pytest.mark.parametrize("plain", [True, False])
def test_lost_terminal_returns_defaults(run_picker, plain):
    # Every write to the picker's terminal fails; under set -e that must not
    # abort the installer, the picker falls through with the current state.
    out = run_picker(keys="", plain=plain, tty_out="/dev/full")
    assert out == DEFAULTS


def test_zero_width_winsize_falls_back_to_80(run_picker, tmp_path):
    import re
    out_file = tmp_path / "tty-out"
    run_picker(keys="\n", plain=True, cols=0, tty_out=out_file)
    text = re.sub(r"\x1b\[[0-9;?]*[A-Za-z]", "", out_file.read_bytes().decode())
    widest = max(len(ln) for ln in text.split("\n"))
    assert 60 < widest < 80, widest


def _bold_under_pty(term):
    import os
    import pty

    master, slave = pty.openpty()
    env = {"PATH": "/usr/bin:/bin"}
    if term is not None:
        env["TERM"] = term
    proc = subprocess.run(
        ["bash", "-c", f'source "{UI_SH}"; printf "[%s%s]" "$BOLD" "$DIM"'],
        env=env, stdout=slave, stderr=subprocess.DEVNULL, timeout=30,
    )
    os.close(slave)
    data = os.read(master, 4096).decode()
    os.close(master)
    assert proc.returncode == 0
    return data


@pytest.mark.parametrize("term", ["dumb", None])
def test_dumb_or_unset_term_emits_no_sgr(term):
    assert "\x1b[" not in _bold_under_pty(term)


def test_color_term_still_emits_sgr():
    assert "\x1b[1m" in _bold_under_pty("xterm")
