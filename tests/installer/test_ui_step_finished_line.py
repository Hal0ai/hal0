"""The finished line ``ui_step`` and ``ui_step_finalize`` print when a step closes out."""

from __future__ import annotations

import shlex
import subprocess
from collections.abc import Callable
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
UI_SH = REPO_ROOT / "installer" / "lib" / "ui.sh"


@pytest.fixture
def run_ui(tmp_path: Path) -> Callable[..., str]:
    def _run(body: str, *, plain: int) -> str:
        # Standard output here is a pipe, so ui.sh would pick plain mode by itself;
        # the cursor form is forced by setting the colours after sourcing.
        cursor_setup = (
            ""
            if plain
            else "UI_PLAIN=0; BOLD=$'\\033[1m'; AMBER=$'\\033[38;5;214m'; "
            "DIM=$'\\033[2m'; RST=$'\\033[0m'; UI_GLYPH_BOX_H='-'"
        )
        script = tmp_path / "run.sh"
        script.write_text(
            "\n".join(
                [
                    "set -euo pipefail",
                    "trap 'echo ERR-TRAP >&2' ERR",
                    f"source {shlex.quote(str(UI_SH))}",
                    f"UI_PLAIN={plain}",
                    cursor_setup,
                    "UI_STEP_TOTAL=",
                    body,
                ]
            )
            + "\n",
            encoding="utf-8",
        )
        proc = subprocess.run(["bash", str(script)], capture_output=True, text=True, check=False)
        assert proc.returncode == 0, proc.stderr
        assert "ERR-TRAP" not in proc.stderr
        return proc.stdout

    return _run


def test_a_finished_line_follows_each_step_but_the_first(run_ui: Callable[..., str]) -> None:
    out = run_ui(
        'ui_step "One"; SECONDS=$((SECONDS+72)); ui_step "Two"; '
        "SECONDS=$((SECONDS+8)); ui_step_finalize",
        plain=1,
    )
    lines = [x for x in out.splitlines() if x.strip()]
    assert lines == ["== (1/?) One", "   done in 1m 12s", "== (2/?) Two", "   done in 8s"]


def test_zero_seconds_prints_nothing(run_ui: Callable[..., str]) -> None:
    out = run_ui('ui_step "One"; ui_step "Two"; ui_step_finalize', plain=1)
    assert "done in" not in out


def test_plain_form_has_no_escape_codes(run_ui: Callable[..., str]) -> None:
    out = run_ui('ui_step "One"; SECONDS=$((SECONDS+5)); ui_step_finalize', plain=1)
    assert "done in 5s" in out
    assert "\x1b" not in out


def test_cursor_form_dims_the_line(run_ui: Callable[..., str]) -> None:
    out = run_ui('ui_step "One"; SECONDS=$((SECONDS+5)); ui_step_finalize', plain=0)
    assert "   \x1b[2mdone in 5s\x1b[0m" in out


def test_twelve_minutes_exactly(run_ui: Callable[..., str]) -> None:
    out = run_ui('ui_step "One"; SECONDS=$((SECONDS+720)); ui_step_finalize', plain=1)
    assert "   done in 12m 0s" in out


def test_durations_are_still_recorded(run_ui: Callable[..., str]) -> None:
    out = run_ui(
        'ui_step "One"; SECONDS=$((SECONDS+5)); ui_step_finalize; '
        'echo "D=${UI_STEP_DURATIONS[One]}"',
        plain=1,
    )
    assert "D=5" in out
