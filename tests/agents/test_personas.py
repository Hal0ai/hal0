"""Unit tests for :mod:`hal0.agents.personas` (PR-3, v0.3).

Persona TOML is the hal0-side concept layered on top of Hermes's
``system_prompt_prelude`` + tool gating + memory namespacing. These
tests pin the round-trip + malformed-handling + seed contracts every
downstream surface (config_write Phase 7, the activate API in PR-4,
the dashboard chooser in PR-10) depends on.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from hal0.agents import personas as P


def test_persona_dataclass_round_trips_through_toml(tmp_path: Path) -> None:
    """A persona round-trips through save → load with no data loss."""
    persona = P.Persona(
        id="example",
        display_name="Example",
        summary="A test persona.",
        system_prompt="You are a test persona. Be terse.",
        tools_allowed=("memory.*", "search.*"),
        memory_namespace="private:example",
        approval=P.PersonaApproval(
            default_policy="auto-approve",
            auto_approve=("memory.read.*",),
            require_approval=("files.write.*",),
        ),
        preferred_upstream="hal0",
        preferred_model="qwen3-coder-q4kxl",
    )
    P.save_persona(persona, root=tmp_path)
    loaded = P.load_persona("example", root=tmp_path)
    assert loaded == persona


def test_load_persona_missing_file_raises_filenotfound(tmp_path: Path) -> None:
    with pytest.raises(FileNotFoundError):
        P.load_persona("nope", root=tmp_path)


def test_malformed_toml_raises_persona_error(tmp_path: Path) -> None:
    (tmp_path / "bad.toml").write_text(
        "[persona]\nid = 'bad'\ndisplay_name = unterminated string",
        encoding="utf-8",
    )
    with pytest.raises(P.PersonaError) as exc_info:
        P.load_persona("bad", root=tmp_path)
    assert "malformed TOML" in str(exc_info.value)


def test_filename_id_mismatch_raises_persona_error(tmp_path: Path) -> None:
    """The filename stem must match ``[persona].id`` to prevent silent renames."""
    (tmp_path / "actual.toml").write_text(
        '[persona]\nid = "different"\ndisplay_name = "Different"\n',
        encoding="utf-8",
    )
    with pytest.raises(P.PersonaError) as exc_info:
        P.load_persona("actual", root=tmp_path)
    assert "doesn't match filename" in str(exc_info.value)


def test_missing_id_raises_persona_error(tmp_path: Path) -> None:
    (tmp_path / "noid.toml").write_text("[persona]\ndisplay_name = 'foo'\n", encoding="utf-8")
    with pytest.raises(P.PersonaError):
        P.load_persona("noid", root=tmp_path)


def test_invalid_default_policy_raises_persona_error(tmp_path: Path) -> None:
    (tmp_path / "bad-policy.toml").write_text(
        '[persona]\nid = "bad-policy"\ndisplay_name = "Bad"\n'
        '[persona.approval]\ndefault_policy = "yolo"\n',
        encoding="utf-8",
    )
    with pytest.raises(P.PersonaError) as exc_info:
        P.load_persona("bad-policy", root=tmp_path)
    assert "default_policy" in str(exc_info.value)


def test_list_personas_skips_malformed_files_with_warning(tmp_path: Path, caplog) -> None:
    """One bad persona must not hide the others — log + continue."""
    good = P.Persona(id="good", display_name="Good")
    P.save_persona(good, root=tmp_path)
    (tmp_path / "broken.toml").write_text("not = valid persona toml at all", encoding="utf-8")
    items = P.list_personas(root=tmp_path)
    # Only the parseable one comes back; broken is logged not raised.
    assert [p.id for p in items] == ["good"]


def test_list_personas_empty_when_root_missing(tmp_path: Path) -> None:
    assert P.list_personas(root=tmp_path / "no-such-dir") == []


def test_active_pointer_round_trips(tmp_path: Path) -> None:
    p = P.Persona(id="example", display_name="Example")
    P.save_persona(p, root=tmp_path)
    P.set_active("example", root=tmp_path)
    assert P.get_active(root=tmp_path) == "example"


def test_get_active_returns_none_when_pointer_missing(tmp_path: Path) -> None:
    assert P.get_active(root=tmp_path) is None


def test_get_active_strips_whitespace(tmp_path: Path) -> None:
    (tmp_path / "active.txt").write_text("  whitespaced  \n", encoding="utf-8")
    assert P.get_active(root=tmp_path) == "whitespaced"


def test_set_active_refuses_missing_persona(tmp_path: Path) -> None:
    with pytest.raises(FileNotFoundError):
        P.set_active("ghost", root=tmp_path)


def test_seed_default_personas_writes_two_files_and_pointer(tmp_path: Path) -> None:
    written = P.seed_default_personas(agent_id="hermes-agent", root=tmp_path)
    assert {p.id for p in written} == {"hermes", "coder"}
    assert (tmp_path / "hermes.toml").exists()
    assert (tmp_path / "coder.toml").exists()
    assert (tmp_path / P.ACTIVE_POINTER).read_text().strip() == "hermes"


def test_seed_default_personas_idempotent_no_overwrite(tmp_path: Path) -> None:
    """Re-running seed without --repair leaves operator edits alone."""
    P.seed_default_personas(agent_id="hermes-agent", root=tmp_path)
    # Operator edits the hermes persona
    hand_edit = (
        '[persona]\nid = "hermes"\ndisplay_name = "Custom"\n'
        '[persona.prompt]\nsystem = "operator-edited"\n'
    )
    (tmp_path / "hermes.toml").write_text(hand_edit, encoding="utf-8")
    written = P.seed_default_personas(agent_id="hermes-agent", root=tmp_path)
    assert written == []  # nothing re-written
    loaded = P.load_persona("hermes", root=tmp_path)
    assert loaded.display_name == "Custom"


def test_seed_default_personas_overwrite_restores_defaults(tmp_path: Path) -> None:
    """``--repair`` (overwrite=True) re-writes the canonical seed."""
    P.seed_default_personas(agent_id="hermes-agent", root=tmp_path)
    (tmp_path / "hermes.toml").write_text(
        '[persona]\nid = "hermes"\ndisplay_name = "Custom"\n',
        encoding="utf-8",
    )
    written = P.seed_default_personas(agent_id="hermes-agent", root=tmp_path, overwrite=True)
    assert {p.id for p in written} == {"hermes", "coder"}
    loaded = P.load_persona("hermes", root=tmp_path)
    assert loaded.display_name == "Hermes"


def test_seed_preserves_operator_active_choice(tmp_path: Path) -> None:
    """Operator-chosen active persona survives re-seeding."""
    P.seed_default_personas(agent_id="hermes-agent", root=tmp_path)
    P.set_active("coder", root=tmp_path)
    P.seed_default_personas(agent_id="hermes-agent", root=tmp_path)
    assert P.get_active(root=tmp_path) == "coder"


def test_seed_recovers_dangling_active_pointer(tmp_path: Path) -> None:
    """If active.txt names a missing persona, reseed resets to hermes."""
    (tmp_path / P.ACTIVE_POINTER).write_text("ghost\n", encoding="utf-8")
    P.seed_default_personas(agent_id="hermes-agent", root=tmp_path)
    assert P.get_active(root=tmp_path) == "hermes"


def test_build_prompt_addendum_includes_mcp_servers() -> None:
    persona = P._seed_hermes("hermes-agent")
    servers = [
        {"name": "hal0-memory", "usage_hint": "persistent context"},
        {"name": "hal0-admin", "usage_hint": "platform state"},
    ]
    block = P.build_prompt_addendum(persona, mcp_servers=servers)
    assert "hal0-memory: persistent context" in block
    assert "hal0-admin: platform state" in block
    assert "Approval policy (active persona 'hermes'):" in block
    assert persona.system_prompt.split(".")[0] in block


def test_build_prompt_addendum_lists_approval_lists() -> None:
    persona = P.Persona(
        id="strict",
        display_name="Strict",
        approval=P.PersonaApproval(
            default_policy="never",
            auto_approve=("memory.read.*",),
            require_approval=("files.*", "shell.*"),
        ),
    )
    block = P.build_prompt_addendum(persona)
    assert "memory.read.*" in block
    assert "files.*, shell.*" in block
    assert "Default policy: never" in block


def test_activate_writes_active_and_returns_payload(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """``activate`` writes active.txt + returns a payload usable as an API
    response. The reload nudge is best-effort; we stub it to assert no
    propagation on failure."""
    P.seed_default_personas(agent_id="hermes-agent", root=tmp_path)
    monkeypatch.setattr(P, "hermes_reload", lambda **_: (False, "unreachable"))
    result = P.activate("coder", root=tmp_path)
    assert result["persona_id"] == "coder"
    assert result["display_name"] == "Coder"
    assert P.get_active(root=tmp_path) == "coder"
    assert result["hot_reload"]["ok"] is False
    assert result["hot_reload"]["error"] == "unreachable"


def test_activate_propagates_filenotfound_for_missing_persona(tmp_path: Path) -> None:
    with pytest.raises(FileNotFoundError):
        P.activate("ghost", root=tmp_path)
