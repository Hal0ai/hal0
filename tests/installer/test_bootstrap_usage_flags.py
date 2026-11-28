"""The one-line install's usage text must only show flags the installer accepts."""

from __future__ import annotations

import re
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
BOOTSTRAP = ROOT / "installer" / "bootstrap.sh"
INSTALL_SH = ROOT / "installer" / "install.sh"

FLAG = re.compile(r"(?<![\w-])--[a-z][a-z-]*")


@pytest.fixture(name="bootstrap_text")
def _bootstrap_text() -> str:
    return BOOTSTRAP.read_text(encoding="utf-8")


@pytest.fixture(name="install_sh_text")
def _install_sh_text() -> str:
    return INSTALL_SH.read_text(encoding="utf-8")


def usage_comment(text: str) -> str:
    """The leading comment block: from line 1 to the first line that is not a comment or blank."""
    block: list[str] = []
    for line in text.splitlines():
        stripped = line.strip()
        if stripped and not stripped.startswith("#"):
            break
        block.append(line)
    return "\n".join(block)


def accepted_flags(text: str) -> set[str]:
    """Bare flag names from the case arm of the unknown-flag stop in install.sh."""
    start = text.index("if hal0_wizard_present")
    end = text.index(") ;;", start)
    arm = text[start:end]
    return set(FLAG.findall(arm))


def bootstrap_consumed_flags(text: str) -> set[str]:
    """Flags main() in bootstrap.sh removes from the arguments instead of passing on."""
    start = text.index("main() {")
    loop_end = text.index("set -- ", start)
    return set(re.findall(r'"\$\{arg\}" == "(--[a-z][a-z-]*)"', text[start:loop_end]))


def test_every_flag_in_the_usage_text_is_one_the_installer_accepts(
    bootstrap_text: str, install_sh_text: str
) -> None:
    accepted = accepted_flags(install_sh_text)
    assert accepted, "parsed no accepted flags from install.sh"
    assert {"--models-dir", "--answers"} <= accepted, accepted
    consumed = bootstrap_consumed_flags(bootstrap_text)
    assert "--box" in consumed, consumed
    used = set(FLAG.findall(usage_comment(bootstrap_text))) - consumed
    assert used and used <= accepted, used - accepted


def test_the_usage_text_shows_the_models_dir_example(bootstrap_text: str) -> None:
    line = "#   curl -fsSL https://hal0.dev/install.sh | sudo bash -s -- --models-dir=/data/models"
    assert line in usage_comment(bootstrap_text).splitlines()


def test_the_stdin_comment_no_longer_promises_a_fallback_to_defaults(
    bootstrap_text: str,
) -> None:
    assert "falls back to defaults" not in bootstrap_text
