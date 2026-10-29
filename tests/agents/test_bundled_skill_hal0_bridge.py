"""The shipped ``hal0-bridge`` skill: how Hermes answers a task another agent delegated (ADR-0043).

A remote coding agent reads Hermes's final message from a program or a model and
usually cannot ask again, so the skill fixes the reply shape: a fenced ``result``
block first, then what was done and what awaits approval, questions only through
the clarify tool, no trailing offers. These tests pin that the skill is valid for
Hermes (and for the bridge's own skill validator), is short enough to be read on
every load, is shipped by the installer, and is not on the retired list.
"""

from __future__ import annotations

import re
from pathlib import Path

import yaml

from hal0.agents.bundled_skills import RETIRED_BUNDLED_SKILLS
from hal0.bridge.skills import SkillLibrary

REPO = Path(__file__).resolve().parents[2]
SKILL_DIR = REPO / "installer" / "agent-skills" / "hal0-bridge"
SKILL = SKILL_DIR / "SKILL.md"
MAX_LINES = 60


def _frontmatter(text: str) -> dict[str, object]:
    parts = text.split("---")
    assert parts[0] == "", "SKILL.md must start with a frontmatter block"
    loaded = yaml.safe_load(parts[1])
    assert isinstance(loaded, dict)
    return loaded


def test_shipped_skill_frontmatter() -> None:
    fm = _frontmatter(SKILL.read_text(encoding="utf-8"))
    assert fm["name"] == "hal0-bridge" == SKILL_DIR.name
    assert fm["category"] == "homelab-ops"
    description = fm["description"]
    assert isinstance(description, str)
    assert description.startswith(
        "How to answer a task delegated by another agent through hal0's agent bridge"
    )
    assert "\n" not in description.strip()  # one sentence, one line


def test_skill_is_short() -> None:
    lines = SKILL.read_text(encoding="utf-8").splitlines()
    assert len(lines) <= MAX_LINES, f"{len(lines)} lines; the skill is read on every load"


def test_skill_leads_with_a_fenced_result_block() -> None:
    body = SKILL.read_text(encoding="utf-8").split("---", 2)[2]
    block = re.search(r"```result\n(.*?)\n```", body, re.DOTALL)
    assert block is not None, "no fenced ```result block"
    keys = [line.split(":", 1)[0] for line in block.group(1).splitlines()]
    assert keys == ["status", "summary", "artifacts", "needs"]
    statuses = re.search(r"status: (.*)", block.group(1))
    assert statuses is not None
    assert [s.strip() for s in statuses.group(1).split("|")] == [
        "ok",
        "partial",
        "failed",
        "needs-approval",
    ]
    # the block is the first fenced block: the skill does not teach another shape first
    assert body.index("```result") == body.index("```")


def test_skill_states_the_rules_the_caller_depends_on() -> None:
    text = SKILL.read_text(encoding="utf-8").lower()
    assert "clarify" in text  # questions go through the clarify tool
    assert "awaits approval" in text or "await approval" in text
    assert "offer" in text  # and the no-follow-up-offers rule


def test_skill_passes_the_bridges_own_validator(tmp_path: Path) -> None:
    library = SkillLibrary(shipped_root=tmp_path / "shipped", dropin_root=tmp_path / "dropin")
    library.validate("hal0-bridge", SKILL.read_text(encoding="utf-8"))


def test_skill_is_shipped_and_not_retired() -> None:
    assert "hal0-bridge" not in RETIRED_BUNDLED_SKILLS
    shipped = {p.name for p in (REPO / "installer" / "agent-skills").iterdir() if p.is_dir()}
    assert "hal0-bridge" in shipped
    # install.sh copies every directory under installer/agent-skills (no allowlist to extend),
    # and the provisioner links every immediate child of the shipped root.
    install = (REPO / "installer" / "install.sh").read_text(encoding="utf-8")
    assert 'cp -rf "${SKILLS_SRC}"/* "${SKILLS_SHIP}/"' in install
