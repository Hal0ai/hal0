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
  $'hermes\tHermes agent\tvenv + config + gateway\ton\t'
  $'pi\tPi agent\tterminal coding agent (CLI/TUI)\toff\t'
  $'hindsight\tHindsight memory\thindsight-api unit\ton\tno usable Python 3.11-3.14'
  $'brain\tBrain model\t~1-3 GB pull\ton\t'
)
declare -A result=()
ui_component_picker rows result
for id in openwebui comfyui hermes pi hindsight brain; do
  printf '%s=%s\n' "$id" "${{result[$id]}}"
done
"""


@pytest.fixture
def run_picker(tmp_path):
    def _run(keys: str, plain: bool) -> dict:
        keyfile = tmp_path / "keys"
        keyfile.write_bytes(keys.encode())
        # _ui_init_colors forces UI_PLAIN=1 off a non-tty stdout, so the
        # cursor-path case resets it explicitly after sourcing.
        plain_line = "UI_PLAIN=1; HAL0_PLAIN=1" if plain else "UI_PLAIN=0"
        script = tmp_path / "drive.sh"
        script.write_text(SCRIPT.format(ui_sh=UI_SH, plain_line=plain_line))
        env = {
            "PATH": "/usr/bin:/bin",
            "UI_TTY_IN": str(keyfile),
            "UI_TTY_OUT": "/dev/null",
        }
        proc = subprocess.run(
            ["bash", str(script)], env=env, capture_output=True, text=True, timeout=30
        )
        assert proc.returncode == 0, proc.stderr
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
