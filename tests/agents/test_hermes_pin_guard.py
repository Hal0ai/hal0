"""Pin guard: bumping the vetted Hermes ref requires re-verifying ADR-0035."""

from __future__ import annotations

from pathlib import Path

from hal0.agents import hermes_provision

ADR = Path(__file__).resolve().parents[2] / "docs/adr/0035-hermes-openai-api-as-v1-upstreams.md"
PREFIX = "Verified against hermes-agent "


def test_adr_0035_verified_ref_matches_vetted_pin() -> None:
    lines = [ln for ln in ADR.read_text(encoding="utf-8").splitlines() if ln.startswith(PREFIX)]
    assert lines, f"{ADR.name} has no line starting with {PREFIX!r}"
    verified = "\n".join(lines)
    missing = sorted(r for r in hermes_provision.VETTED_HERMES_REFS if r not in verified)
    assert not missing, (
        f"VETTED_HERMES_REFS has refs the ADR-0035 'Verified against' line does not name: "
        f"{missing}. Re-verify the Hermes API citations in the ADR, then update the line."
    )
