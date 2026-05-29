"""Round-trip tests — persona TOML preserves the budget block.

PR-3's :func:`save_persona` + :func:`load_persona` now carry the
``[persona.budget]`` sub-table through the dataclass without dropping
operator-set caps. These tests pin that contract so a future refactor
of the writer can't silently elide budgets.
"""

from __future__ import annotations

from pathlib import Path

from hal0.agents import personas as personas_mod
from hal0.agents.budget import Budget


def test_persona_with_budget_round_trips(tmp_path: Path) -> None:
    persona = personas_mod.Persona(
        id="example",
        display_name="Example",
        budget=Budget(
            daily_usd=2.50,
            monthly_usd=25.0,
            lifetime_usd=200.0,
            per_call_max_usd=0.10,
            hard_cap=True,
        ),
    )
    personas_mod.save_persona(persona, root=tmp_path)
    loaded = personas_mod.load_persona("example", root=tmp_path)
    assert loaded.budget == persona.budget


def test_persona_empty_budget_round_trips(tmp_path: Path) -> None:
    """Default Budget (no caps configured) survives save+load."""
    persona = personas_mod.Persona(id="ex", display_name="Ex")
    personas_mod.save_persona(persona, root=tmp_path)
    loaded = personas_mod.load_persona("ex", root=tmp_path)
    assert loaded.budget == Budget()
    assert loaded.budget.is_empty()


def test_persona_hard_cap_false_round_trips(tmp_path: Path) -> None:
    """hard_cap=False (warn-only mode) survives the round-trip."""
    persona = personas_mod.Persona(
        id="warn",
        display_name="Warn",
        budget=Budget(daily_usd=1.0, hard_cap=False),
    )
    personas_mod.save_persona(persona, root=tmp_path)
    loaded = personas_mod.load_persona("warn", root=tmp_path)
    assert loaded.budget.hard_cap is False
    assert loaded.budget.daily_usd == 1.0


def test_persona_explicit_zero_budget_round_trips(tmp_path: Path) -> None:
    """Explicit 0.0 (=block every paid call) survives — distinguished from None."""
    persona = personas_mod.Persona(
        id="fenced",
        display_name="Fenced",
        budget=Budget(daily_usd=0.0),
    )
    personas_mod.save_persona(persona, root=tmp_path)
    loaded = personas_mod.load_persona("fenced", root=tmp_path)
    assert loaded.budget.daily_usd == 0.0


def test_seed_personas_have_empty_budget_by_default(tmp_path: Path) -> None:
    """Default seeds ship an empty budget — operator opts in."""
    personas_mod.seed_default_personas(agent_id="hermes-agent", root=tmp_path)
    hermes = personas_mod.load_persona("hermes", root=tmp_path)
    coder = personas_mod.load_persona("coder", root=tmp_path)
    assert hermes.budget.is_empty()
    assert coder.budget.is_empty()
    # …but hard_cap defaults to True so a later operator edit doesn't have
    # to remember to flip it.
    assert hermes.budget.hard_cap is True
    assert coder.budget.hard_cap is True


def test_persona_with_budget_preserves_other_fields(tmp_path: Path) -> None:
    """Mutating budget doesn't lose system prompt / approval / tools state."""
    persona = personas_mod.Persona(
        id="full",
        display_name="Full",
        summary="A persona with everything set",
        system_prompt="You are Full. Be terse.",
        tools_allowed=("memory.*",),
        memory_namespace="private:full",
        budget=Budget(daily_usd=5.0),
    )
    personas_mod.save_persona(persona, root=tmp_path)
    loaded = personas_mod.load_persona("full", root=tmp_path)
    assert loaded == persona


def test_malformed_budget_in_toml_raises_persona_error(tmp_path: Path) -> None:
    """A bad budget field surfaces as PersonaError so load_persona's contract holds."""
    (tmp_path / "bad.toml").write_text(
        '[persona]\nid = "bad"\ndisplay_name = "Bad"\n[persona.budget]\ndaily_usd = -1.0\n',
        encoding="utf-8",
    )
    try:
        personas_mod.load_persona("bad", root=tmp_path)
    except personas_mod.PersonaError as exc:
        assert ">= 0" in str(exc)
    else:
        raise AssertionError("expected PersonaError")
