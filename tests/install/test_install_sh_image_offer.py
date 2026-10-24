"""The install.sh block that offers starter image models.

Static checks on the script text, in the style of ``test_comfyui_scripts_shipped.py`` and the
agent-anchor checks in ``tests/installer/test_install_single_entry_point.py``. The behaviour
behind each line is covered by ``test_image_models.py``; what these pin is the shape that
keeps an install safe:

* a headless install never blocks and never downloads (the prompt sits behind ``_interactive``);
* the default answer is skip, and an unattended opt-in is an explicit environment variable;
* bash never invents a size, it prints what ``--plan`` computed;
* a failed or refused install warns and the install continues.

The last section runs the block itself under bash, with a stub ``python`` standing in for
``hal0.install.image_models``, so the control flow is exercised and not just grepped.
"""

from __future__ import annotations

import re
import subprocess
from pathlib import Path

import pytest

REPO = Path(__file__).parent.parent.parent
INSTALL_SH = REPO / "installer" / "install.sh"

_START = "starter image models"
_END = "# ── NPU prerequisites"


@pytest.fixture(scope="module")
def install_sh_text() -> str:
    return INSTALL_SH.read_text(encoding="utf-8")


@pytest.fixture(scope="module")
def image_block(install_sh_text: str) -> str:
    mention = install_sh_text.index(_START)
    start = install_sh_text.rindex("\n", 0, mention) + 1  # the header comment's own line
    end = install_sh_text.index(_END, mention)
    return install_sh_text[start:end]


@pytest.fixture(scope="module")
def code(image_block: str) -> str:
    """The block without its comment lines."""
    return "\n".join(
        line
        for line in image_block.splitlines()
        if line.strip() and not line.lstrip().startswith("#")
    )


def test_the_block_follows_the_agent_model_offer(install_sh_text: str) -> None:
    agent = install_sh_text.index("agent anchor model — OPT-IN")
    images = install_sh_text.index(_START)
    npu = install_sh_text.index(_END)
    assert agent < images < npu


def test_install_sh_image_block_is_gated(image_block: str) -> None:
    for needle in ("_interactive", "HAL0_IMAGE_MODELS", "hal0.install.image_models --plan"):
        assert needle in image_block, f"the image block never mentions {needle!r}"
    assert not re.search(r"\d+(\.\d+)?\s*GB", image_block), (
        "the image block hardcodes a GB figure; the sizes come from `--plan`"
    )
    assert (
        "HAL0_SKIP_COMFYUI" not in image_block
    )  # retired in Plan 1; HAL0_IMAGE_MODELS=none replaces it


def test_dev_mode_and_skip_setup_offer_nothing(code: str) -> None:
    assert re.search(r'if \[\[ "\$\{DEV_MODE\}" -eq 1 \]\]; then', code)
    assert 'HAL0_SKIP_SETUP:-0}" == "1"' in code


def test_no_start_skips_the_whole_step(code: str) -> None:
    """The step starts the img slot (and pulls its runtime), which ``--no-start`` forbids."""
    assert re.search(r'elif \[\[ "\$\{NO_START\}" -eq 1 \]\]; then', code)
    # Before the plan is computed: nothing downloads and nothing is selected under --no-start.
    assert code.index('"${NO_START}" -eq 1') < code.index("hal0.install.image_models --plan")


@pytest.mark.parametrize("value", ["none", "0"])
def test_image_models_none_or_zero_skips_the_step(code: str, value: str) -> None:
    assert f'"${{HAL0_IMAGE_MODELS:-}}" == "{value}"' in code


def test_the_prompt_sits_behind_the_interactive_gate(code: str) -> None:
    gate = code.index("elif _interactive; then")
    prompt = code.index("_tty_read _img_answer")
    assert gate < prompt, "the image prompt is not inside the _interactive branch"


def test_the_prompt_defaults_to_skip(code: str) -> None:
    m = re.search(r'_tty_read _img_answer "([^"]*)" "([^"]*)"', code)
    assert m, "the image prompt is gone or changed shape"
    assert m.group(1) == "Image models to download (numbers, 'recommended', or Enter to skip)"
    assert m.group(2) == "", "bare Enter must mean skip"


def test_a_headless_install_says_how_to_opt_in(code: str) -> None:
    assert (
        "Non-interactive install — no image model downloaded "
        "(set HAL0_IMAGE_MODELS=recommended to opt in)."
    ) in code
    # The headless message is the final branch of the offer: nothing is downloaded there.
    assert code.index("Non-interactive install") > code.index("elif _interactive; then")


def test_an_env_value_resolves_through_the_python_grammar(code: str) -> None:
    """HAL0_IMAGE_MODELS=recommended (or ids) goes through `--resolve`, never bash's own parsing."""
    assert "hal0.install.image_models --resolve" in code
    assert '"${HAL0_IMAGE_MODELS}"' in code


def test_the_menu_marks_the_recommended_line(code: str) -> None:
    assert "[recommended]" in code
    assert "--plan" in code


def test_install_sh_is_syntactically_valid() -> None:
    result = subprocess.run(["bash", "-n", str(INSTALL_SH)], capture_output=True, text=True)
    assert result.returncode == 0, result.stderr


# ── running the block ────────────────────────────────────────────────────────

_PLAN = (
    "sdxl-lightning\tSDXL Lightning — 7.6 GB download, ~10s per image\t1\n"
    "sdxl-turbo\tSDXL Turbo — 6.9 GB download, ~3s per image\t0\n"
)

# Stands in for `python -m hal0.install.image_models`. --resolve knows `recommended`,
# `none`, numbers 1-2 and `sdxl-turbo`; anything else is the exit-2 path.
_FAKE_PYTHON = """#!/usr/bin/env bash
echo "$*" >> "$FAKE_DIR/python.log"
if [[ "$*" == *"--plan"* ]]; then
    cat "$FAKE_DIR/plan"
elif [[ "$*" == *"--resolve"* ]]; then
    case "${@: -1}" in
        recommended|1) echo "sdxl-lightning" ;;
        2|sdxl-turbo) echo "sdxl-turbo" ;;
        1,2) echo "sdxl-lightning sdxl-turbo" ;;
        none|"") ;;
        boom) echo "could not resolve 'boom' (RuntimeError: no hardware)" >&2; exit 1 ;;
        *) echo "'${@: -1}' is not a listed model. Answer 'none' to skip." >&2; exit 2 ;;
    esac
else
    echo "installing $* with HF_TOKEN=${HF_TOKEN:-}" >> "$FAKE_DIR/installs.log"
    exit "${FAKE_INSTALL_RC:-0}"
fi
"""

_HARNESS = """set -euo pipefail
VENV_DIR="$FAKE_DIR"
HF_TOKEN_VAL="hf_secret"
info() { printf 'INFO %s\\n' "$*"; }
warn() { printf 'WARN %s\\n' "$*"; }
_interactive() { [[ "${FAKE_INTERACTIVE:-0}" == "1" ]]; }
_tty_read() {
    local -n _out="$1"
    local line=""
    IFS= read -r line <&3 || line=""
    echo "PROMPT $2" >> "$FAKE_DIR/prompts.log"
    _out="$line"
}
exec 3< "$FAKE_DIR/answers"
DEV_MODE="${DEV_MODE:-0}"
NO_START="${NO_START:-0}"
"""


def _run_block(
    tmp_path: Path,
    block: str,
    *,
    env: dict[str, str] | None = None,
    plan: str = _PLAN,
    answers: tuple[str, ...] = (),
) -> tuple[subprocess.CompletedProcess[str], list[str], list[str]]:
    """Run the image block; returns (process, installs, python invocations)."""
    (tmp_path / "bin").mkdir()
    fake = tmp_path / "bin" / "python"
    fake.write_text(_FAKE_PYTHON)
    fake.chmod(0o755)
    (tmp_path / "plan").write_text(plan)
    (tmp_path / "answers").write_text("".join(f"{a}\n" for a in answers))
    script = tmp_path / "block.sh"
    script.write_text(_HARNESS + block)
    run_env = {"PATH": "/usr/bin:/bin", "FAKE_DIR": str(tmp_path), **(env or {})}
    proc = subprocess.run(
        ["bash", str(script)], capture_output=True, text=True, env=run_env, timeout=30
    )

    def lines(name: str) -> list[str]:
        path = tmp_path / name
        return path.read_text().splitlines() if path.exists() else []

    return proc, lines("installs.log"), lines("python.log")


def _installing(*ids: str) -> str:
    """The line the stub logs for `python -m hal0.install.image_models <ids>` with the token."""
    return f"installing -m hal0.install.image_models {' '.join(ids)} with HF_TOKEN=hf_secret"


def test_headless_without_the_variable_downloads_nothing(tmp_path: Path, image_block: str) -> None:
    proc, installs, _ = _run_block(tmp_path, image_block)

    assert proc.returncode == 0, proc.stderr
    assert (
        "INFO Non-interactive install — no image model downloaded "
        "(set HAL0_IMAGE_MODELS=recommended to opt in)."
    ) in proc.stdout
    assert installs == []
    assert not (tmp_path / "prompts.log").exists()


def test_the_variable_opts_in_without_a_prompt(tmp_path: Path, image_block: str) -> None:
    proc, installs, _ = _run_block(tmp_path, image_block, env={"HAL0_IMAGE_MODELS": "recommended"})

    assert proc.returncode == 0, proc.stderr
    assert installs == [_installing("sdxl-lightning")]
    assert not (tmp_path / "prompts.log").exists()


def test_the_variable_takes_ids_too(tmp_path: Path, image_block: str) -> None:
    _, installs, _ = _run_block(tmp_path, image_block, env={"HAL0_IMAGE_MODELS": "1,2"})
    assert installs == [_installing("sdxl-lightning", "sdxl-turbo")]


@pytest.mark.parametrize(
    "env",
    [
        {"HAL0_IMAGE_MODELS": "none"},
        {"HAL0_IMAGE_MODELS": "0"},
        {"HAL0_SKIP_SETUP": "1", "HAL0_IMAGE_MODELS": "recommended"},
        {"DEV_MODE": "1", "HAL0_IMAGE_MODELS": "recommended"},
    ],
)
def test_the_opt_outs_skip_before_the_plan_is_even_computed(
    tmp_path: Path, image_block: str, env: dict[str, str]
) -> None:
    proc, installs, calls = _run_block(tmp_path, image_block, env=env)

    assert proc.returncode == 0, proc.stderr
    assert installs == []
    assert calls == []


@pytest.mark.parametrize("interactive", ["0", "1"])
def test_no_start_skips_the_step_and_says_how_to_install_later(
    tmp_path: Path, image_block: str, interactive: str
) -> None:
    """``--no-start`` sets everything up without starting units: no download, no prompt, no slot."""
    proc, installs, calls = _run_block(
        tmp_path,
        image_block,
        env={"NO_START": "1", "HAL0_IMAGE_MODELS": "recommended", "FAKE_INTERACTIVE": interactive},
    )

    assert proc.returncode == 0, proc.stderr
    assert installs == []
    assert calls == []
    assert not (tmp_path / "prompts.log").exists()
    assert proc.stdout.count("INFO ") == 1
    assert "--no-start" in proc.stdout
    assert "hal0 comfyui models install <id>" in proc.stdout


def test_an_unresolvable_variable_warns_and_downloads_nothing(
    tmp_path: Path, image_block: str
) -> None:
    proc, installs, _ = _run_block(tmp_path, image_block, env={"HAL0_IMAGE_MODELS": "bogus"})

    assert proc.returncode == 0, proc.stderr
    assert "WARN HAL0_IMAGE_MODELS=bogus did not resolve" in proc.stdout
    assert installs == []


def test_a_host_without_a_lane_prints_the_reason_and_offers_nothing(
    tmp_path: Path, image_block: str
) -> None:
    proc, installs, _ = _run_block(
        tmp_path,
        image_block,
        plan="none\tno ROCm lane on this host (ComfyUI needs /dev/kfd and a render node)\n",
        env={"HAL0_IMAGE_MODELS": "recommended", "FAKE_INTERACTIVE": "1"},
    )

    assert proc.returncode == 0, proc.stderr
    assert "INFO Starter image models are not offered: no ROCm lane on this host" in proc.stdout
    assert installs == []
    assert not (tmp_path / "prompts.log").exists()


def test_an_empty_plan_is_no_offer_not_a_failure(tmp_path: Path, image_block: str) -> None:
    proc, installs, _ = _run_block(tmp_path, image_block, plan="")

    assert proc.returncode == 0, proc.stderr
    assert "not offered" in proc.stdout
    assert installs == []


def test_the_menu_numbers_the_plan_and_marks_the_recommended_line(
    tmp_path: Path, image_block: str
) -> None:
    proc, installs, _ = _run_block(
        tmp_path, image_block, env={"FAKE_INTERACTIVE": "1"}, answers=("",)
    )

    assert proc.returncode == 0, proc.stderr
    assert (
        "INFO   1) sdxl-lightning: SDXL Lightning — 7.6 GB download, ~10s per image [recommended]"
        in proc.stdout
    )
    assert "INFO   2) sdxl-turbo: SDXL Turbo — 6.9 GB download, ~3s per image" in proc.stdout
    assert (
        "sdxl-turbo: SDXL Turbo — 6.9 GB download, ~3s per image [recommended]" not in proc.stdout
    )
    assert installs == []  # Enter skips


def test_an_interactive_answer_is_asked_again_until_it_resolves(
    tmp_path: Path, image_block: str
) -> None:
    proc, installs, _ = _run_block(
        tmp_path, image_block, env={"FAKE_INTERACTIVE": "1"}, answers=("bogus", "1,2")
    )

    assert proc.returncode == 0, proc.stderr
    assert "WARN Not a valid choice" in proc.stdout
    assert "is not a listed model" in proc.stderr  # --resolve's own message reaches the operator
    prompts = (tmp_path / "prompts.log").read_text().splitlines()
    assert (
        prompts
        == ["PROMPT Image models to download (numbers, 'recommended', or Enter to skip)"] * 2
    )
    assert installs == [_installing("sdxl-lightning", "sdxl-turbo")]


def test_an_internal_failure_is_not_asked_again(tmp_path: Path, image_block: str) -> None:
    """`--resolve` exits 1 for an internal failure: another prompt cannot help, so skip the step."""
    proc, installs, _ = _run_block(
        tmp_path, image_block, env={"FAKE_INTERACTIVE": "1"}, answers=("boom", "1")
    )

    assert proc.returncode == 0, proc.stderr
    assert "WARN The image model step failed" in proc.stdout
    assert "Not a valid choice" not in proc.stdout
    assert (tmp_path / "prompts.log").read_text().count("PROMPT") == 1  # asked once, not again
    assert installs == []


def test_the_variable_wins_over_the_prompt(tmp_path: Path, image_block: str) -> None:
    _, installs, _ = _run_block(
        tmp_path,
        image_block,
        env={"FAKE_INTERACTIVE": "1", "HAL0_IMAGE_MODELS": "sdxl-turbo"},
        answers=("1",),
    )

    assert installs == [_installing("sdxl-turbo")]
    assert not (tmp_path / "prompts.log").exists()


def test_a_failed_download_warns_and_the_install_goes_on(tmp_path: Path, image_block: str) -> None:
    proc, installs, _ = _run_block(
        tmp_path,
        image_block + "\ninfo 'the install continues'\n",
        env={"HAL0_IMAGE_MODELS": "recommended", "FAKE_INSTALL_RC": "1"},
    )

    assert proc.returncode == 0, proc.stderr  # set -e did not abort
    assert installs  # it did try
    assert "WARN an image model download did not complete" in proc.stdout
    assert "INFO the install continues" in proc.stdout
