"""The profile-centric conversion plan (ADR-0029, phase 1: plan only).

The fixture install mirrors the shapes found on a production box (names and
paths anonymised): a slot whose ``[server].extra_args`` never reached the
launch, a divergent profile overlay, a slot with no profile, slots naming
profiles that do not exist, an unhonoured context ceiling, an FLM thinking
default, a name-keyed slot file next to id-keyed ones, a TTS slot whose model
has no registry row, and stack rows carrying tuning keys.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest
import tomli_w
from typer.testing import CliRunner

from hal0.config import paths
from hal0.config.migrations import profile_centric as pc
from tests.config.profile_centric_fixtures import (
    old_model,
    production_install,
    write_db,
)


def _write(path: Path, data: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(tomli_w.dumps(data).encode())


def _db(models: list[dict[str, Any]]) -> None:
    write_db(models)


_model = old_model


def _slot(stem: str, data: dict[str, Any]) -> None:
    _write(paths.slots_config_dir() / f"{stem}.toml", data)


@pytest.fixture
def install() -> None:
    production_install()


def _ctx(argv: tuple[str, ...]) -> int:
    return int(argv[argv.index("--ctx-size") + 1])


def _by_name(plan: pc.ConversionPlan) -> dict[str, pc.SlotPlan]:
    return {s.name: s for s in plan.slots}


def test_plan_preserves_what_launches(install: None) -> None:
    plan = pc.plan_conversion()
    slots = _by_name(plan)
    assert not plan.blocked

    agent = slots["agent"]
    assert agent.status == "convert" and agent.generated
    assert agent.profile_after == "thinking-big-chat"
    content = plan.profiles["thinking-big-chat"]
    assert content["ctx_size"] == 96000
    assert content["vision"] == "off"  # --no-mmproj beat --mmproj
    assert "-ctk" not in content["flags"]  # inert today: dropped, never applied
    assert "--jinja" not in content["flags"]
    assert {(i.key, i.value) for i in agent.inert} == {
        ("extra_args", "-ctk q8_0 -ctv q8_0"),
        ("parallel", 1),
    }
    assert {"parallel", "backend", "[model].context_size", "[server].extra_args"} <= set(
        agent.drop_keys
    )
    assert "--metrics" not in content["flags"] and "--threads" not in content["flags"]

    brain = slots["brain"]
    assert brain.profile_after == "brain-small-chat"
    flags = plan.profiles["brain-small-chat"]["flags"]
    assert "--top-k 40" in flags and "--reasoning off" in flags  # model tune under the overlay

    assert slots["code"].profile_after == "chat-ctx96000"
    embed = slots["embed"]
    assert any("'embedding' profile" in c for c in embed.launch_changes)
    assert any("--jinja" in c for c in embed.launch_changes)
    # the new embedding seed has ctx 4096; today the slot launches 8192 (the
    # fallback for a model with no native window), so its profile keeps that
    assert embed.generated and "ctx_size" not in plan.profiles[embed.profile_after]
    assert _ctx(embed.argv_after) == 8192

    flm = slots["flm"]
    assert flm.profile_after == "flm-npu-chat"
    assert plan.profiles["flm-npu-chat"] == {
        "ctx_size": 8192,  # FLM's window is always explicit after conversion
        "enable_thinking": True,
        "runner": "flm",
        "cloned_from": "flm",
        "intent": "Converted: slot flm, model npu-chat",
    }

    utility = slots["utility"]
    assert utility.profile_after == "chat" and not utility.generated
    assert [(i.key, i.value) for i in utility.inert] == [("context_size", 98304)]

    # The fixture's custom `coding` differs from the new `coding` seed.
    assert plan.renames == {"coding": "coding-custom"}
    assert slots["coder"].profile_after == "coding-custom"
    assert slots["clean"].status == "unchanged"
    for s in plan.slots:
        if s.argv_today and s.status == "convert":
            assert s.argv_after, s.name

    tts = slots["tts"]
    assert tts.profile_after == "qwen3-tts"
    assert ("context_size", 16000) in [(i.key, i.value) for i in tts.inert]

    models = {m.model_id: m for m in plan.models}
    assert models["unbound"].generated_profile == "chat-unbound"
    assert plan.profiles["chat-unbound"]["ctx_size"] == 98304
    assert "mtp-only" not in models  # "has MTP heads" stays a model fact

    assert set(plan.profile_rewrites) >= {"thinking", "(profiles.toml)"}
    rows = {r["slot"]: r["change"] for st in plan.stacks for r in st.rows}
    assert rows["agent"].startswith("drop vision, mtp (inert today: mtp=True)")
    assert "server_extra_args='-b 64'" in rows["brain"]
    assert "-b 64" not in json.dumps(plan.profiles)


def _converted_install() -> None:
    _write(
        paths.profiles_toml(),
        {
            "profile": {
                "agent-tune": {"flags": "-fa auto -b 2048", "ctx_size": 96000, "vision": "off"}
            }
        },
    )
    _slot(
        "1",
        {
            "id": 1,
            "name": "agent",
            "type": "llm",
            "device": "gpu-rocm",
            "profile": "agent-tune",
            "port": 8082,
            "model": {"default": "big-chat"},
        },
    )
    _db([_model("big-chat", metadata={"context_length": 262144}, tags=["mtp"])])


def test_converted_install_plans_nothing_without_any_state_file() -> None:
    """Shape is read from content: no state file, still nothing to convert."""
    _converted_install()
    assert not (paths.var_lib() / pc.STATE_FILE).exists()
    plan = pc.plan_conversion()
    assert not plan.changed, plan.lines()
    assert plan.lines()[-1] == "nothing to convert"
    assert _by_name(plan)["agent"].notes == ["already in the new shape"]


def test_state_file_is_never_consulted() -> None:
    """A stale "complete" record does not hide an old-shape slot file."""
    _converted_install()
    state = paths.var_lib() / pc.STATE_FILE
    state.parent.mkdir(parents=True, exist_ok=True)
    state.write_text(json.dumps({"complete": True, "slots": ["1.toml"]}))
    _slot(
        "2",
        {
            "id": 2,
            "name": "late",
            "type": "llm",
            "device": "gpu-rocm",
            "profile": "chat",
            "port": 8083,
            "workers": 1,
            "model": {"default": "big-chat", "context_size": 50000},
        },
    )
    plan = pc.plan_conversion()
    late = _by_name(plan)["late"]
    assert late.status == "convert" and late.profile_after != "chat"
    assert _ctx(late.argv_after) == 50000
    assert _by_name(plan)["agent"].status == "unchanged"


def test_lost_state_after_conversion_regenerates_nothing() -> None:
    """New-shape profiles (mtp="off", ctx_size, enable_thinking) are never
    re-read through the frozen resolver."""
    _write(
        paths.profiles_toml(),
        {
            "profile": {
                "thinking-big": {
                    "flags": "-fa auto --reasoning on",
                    "ctx_size": 96000,
                    "enable_thinking": True,
                    "mtp": "off",
                    "cloned_from": "chat",
                }
            }
        },
    )
    _slot(
        "1",
        {
            "id": 1,
            "name": "agent",
            "type": "llm",
            "device": "gpu-rocm",
            "profile": "thinking-big",
            "port": 8082,
            "model": {"default": "big-chat"},
        },
    )
    _db([_model("big-chat", metadata={"context_length": 262144}, tags=["mtp"])])
    plan = pc.plan_conversion()
    assert not plan.changed, plan.lines()
    assert not plan.profiles and not plan.profile_rewrites


def test_mixed_shapes_block() -> None:
    _write(
        paths.profiles_toml(),
        {"profile": {"half": {"flags": "-fa auto", "quant": "Q4", "ctx_size": 8192}}},
    )
    _slot(
        "1",
        {
            "id": 1,
            "name": "agent",
            "type": "llm",
            "device": "gpu-rocm",
            "profile": "half",
            "port": 8082,
            "workers": 1,
            "model": {"default": "big-chat"},
        },
    )
    _db([_model("big-chat", metadata={"context_length": 262144})])
    plan = pc.plan_conversion()
    assert plan.blocked_profiles["half"] and "mixes" in plan.blocked_profiles["half"][0]
    assert _by_name(plan)["agent"].status == "blocked"
    assert plan.blockers == ["1.toml", "profile:half"]


def test_seed_rename_never_overwrites_an_existing_custom() -> None:
    """`agent` moves aside for the new seed; `agent-custom` is already taken."""
    _write(
        paths.profiles_toml(),
        {
            "profile": {
                "agent": {"flags": "-fa auto -b 64", "mtp": False},
                "agent-custom": {"flags": "-fa auto -b 32", "mtp": False},
            }
        },
    )
    plan = pc.plan_conversion()
    assert plan.renames == {"agent": "agent-custom-2"}
    assert "-b 32" in str(plan.profiles.get("agent-custom", {}).get("flags", "")) or (
        "agent-custom" not in plan.profiles
    )
    assert "-b 64" in plan.profiles["agent-custom-2"]["flags"]


def test_blocked_profile_no_slot_uses_is_reported() -> None:
    _write(
        paths.profiles_toml(),
        {"profile": {"orphan": {"flags": "-fa auto --mmproj /p.gguf", "mtp": False}}},
    )
    plan = pc.plan_conversion()
    assert "orphan" in plan.blocked_profiles
    assert plan.blockers == ["profile:orphan"]
    assert any(line.startswith("profile orphan: BLOCKED") for line in plan.lines())


def test_old_shape_profile_with_nothing_to_report_is_still_rewritten() -> None:
    """``mtp = false`` / ``quant = ""`` say nothing, but the entry must reach
    the new shape or the new loader refuses it."""
    _write(
        paths.profiles_toml(),
        {"profile": {"quiet": {"flags": "-fa auto -b 64", "mtp": False, "quant": ""}}},
    )
    _write(
        paths.stacks_toml(),
        {"stack": {"st": {"slots": [], "profiles": {"emb": {"flags": "-b 2", "mtp": False}}}}},
    )
    plan = pc.plan_conversion()
    assert plan.changed
    assert plan.profile_rewrites["quiet"] == ["rewritten in the new shape"]
    (st,) = plan.stacks
    assert st.profile_rewrites["emb"] == ["rewritten in the new shape"]


def test_stack_row_naming_a_missing_profile_is_a_warning_not_a_change() -> None:
    """Broken before and after with nothing to write: a rerun plans nothing."""
    _write(
        paths.stacks_toml(),
        {"stack": {"st": {"slots": [{"slot": "agent", "profile": "gone-profile"}]}}},
    )
    plan = pc.plan_conversion()
    assert not plan.changed, plan.lines()
    assert any("'gone-profile' does not exist" in w for w in plan.warnings)


def test_stale_seed_stack_on_disk_is_not_planned_or_blocking() -> None:
    """Seed stacks are virtual: the loader takes the code version, so a stale
    on-disk copy needs no conversion and cannot block the migration, even with
    an old-shape embedded profile that would otherwise fail."""
    _write(
        paths.stacks_toml(),
        {
            "stack": {
                "forge": {
                    "slots": [{"slot": "agent", "profile": "rocm", "mtp": True}],
                    "profiles": {"emb": {"flags": "--mmproj /p"}},
                },
                "st": {"slots": [{"slot": "agent", "mtp": True}]},
            }
        },
    )
    plan = pc.plan_conversion()
    assert [st.stack for st in plan.stacks] == ["st"]
    assert plan.blockers == []


def test_stack_errors_count_as_blocked() -> None:
    _write(
        paths.stacks_toml(),
        {"stack": {"st": {"slots": [], "profiles": {"emb": {"flags": "--mmproj /p"}}}}},
    )
    plan = pc.plan_conversion()
    assert plan.blockers == ["stack:st"]
    assert plan.to_dict()["blocked"] == ["stack:st"]


@pytest.mark.parametrize("profile", [None, "chat", "deleted-profile"])
def test_clean_slot_is_left_alone(profile: str | None) -> None:
    """No profile (the type default), a seed, or a name nothing defines: a
    slot with no old-shape content is never re-pointed on boot."""
    slot: dict[str, Any] = {
        "id": 1,
        "name": "plain",
        "type": "llm",
        "device": "gpu-rocm",
        "port": 8082,
        "model": {"default": "big-chat"},
    }
    if profile:
        slot["profile"] = profile
    _slot("1", slot)
    _db([_model("big-chat", metadata={"context_length": 262144})])
    plan = pc.plan_conversion()
    assert not plan.changed, plan.lines()


def test_old_slot_keys_next_to_a_converted_profile_block() -> None:
    _converted_install()
    _slot(
        "1",
        {
            "id": 1,
            "name": "agent",
            "type": "llm",
            "device": "gpu-rocm",
            "profile": "agent-tune",
            "port": 8082,
            "workers": 1,
            "model": {"default": "big-chat", "context_size": 50000},
        },
    )
    plan = pc.plan_conversion()
    agent = _by_name(plan)["agent"]
    assert agent.status == "blocked" and "already converted" in str(agent.error)


def test_model_tuning_behind_a_converted_slot_is_kept_as_its_own_profile() -> None:
    _converted_install()
    paths.db_path().unlink()
    _db(
        [
            _model(
                "big-chat",
                metadata={"context_length": 262144},
                tags=["mtp"],
                defaults={"extra_args": "--temp 0.3"},
            )
        ]
    )
    plan = pc.plan_conversion()
    assert _by_name(plan)["agent"].status == "unchanged"
    (mp,) = plan.models
    assert mp.clear == {"extra_args": "--temp 0.3"}
    assert "--temp 0.3" in plan.profiles[str(mp.generated_profile)]["flags"]


def test_every_old_slot_is_evaluated() -> None:
    """An untuned slot on a seed whose meaning changes is caught."""
    _slot(
        "5",
        {
            "id": 5,
            "name": "embed",
            "type": "embedding",
            "device": "gpu-rocm",
            "profile": "embedding",
            "port": 8089,
            "workers": 1,
            "model": {"default": "e"},
        },
    )
    _db([_model("e", metadata={"context_length": 32768})])
    plan = pc.plan_conversion()
    embed = _by_name(plan)["embed"]
    # old: 32768 (native, dense-capped); the new seed says 4096 -> keep 32768
    assert embed.generated and _ctx(embed.argv_after) == 32768
    assert any("--jinja" in c for c in embed.launch_changes)


def test_unparseable_slot_is_blocked_and_others_still_plan(install: None) -> None:
    (paths.slots_config_dir() / "99.toml").write_text("name = [unterminated\n")
    plan = pc.plan_conversion()
    blocked = [s.file for s in plan.blocked]
    assert blocked == ["99.toml"]
    assert _by_name(plan)["agent"].status == "convert"


def test_thread_count_in_tune_moves_to_slot() -> None:
    _slot(
        "1",
        {
            "id": 1,
            "name": "a",
            "type": "llm",
            "device": "gpu-rocm",
            "profile": "chat",
            "port": 8082,
            "model": {"default": "m"},
        },
    )
    _db([_model("m", defaults={"extra_args": "--threads 8 -b 64"})])
    plan = pc.plan_conversion()
    slot = _by_name(plan)["a"]
    assert slot.set_keys == {"threads": 8}
    assert "--threads" not in plan.profiles[slot.profile_after]["flags"]


def test_profile_names_are_deterministic_and_bounded() -> None:
    content = {"flags": "-b 1"}
    assert (
        pc.slug_name("thinking", "qwen3.8-27b", content=content, taken={}) == "thinking-qwen3-8-27b"
    )
    long = pc.slug_name("embedding", "qwen3-embedding-0-6b-q8-0", content=content, taken={})
    assert len(long) <= 32 and long == pc.slug_name(
        "embedding", "qwen3-embedding-0-6b-q8-0", content=content, taken={}
    )
    clash = pc.slug_name("a", "b", content=content, taken={"a-b": {"flags": "other"}})
    assert clash != "a-b" and clash.startswith("a-b-")
    assert pc.slug_name("a", "b", content=content, taken={"a-b": content}) == "a-b"


def test_identical_tunes_share_one_profile() -> None:
    for i, port in ((1, 8081), (2, 8082)):
        _slot(
            str(i),
            {
                "id": i,
                "name": f"s{i}",
                "type": "llm",
                "device": "gpu-rocm",
                "profile": "chat",
                "port": port,
                "model": {"default": "m", "context_size": 4096},
            },
        )
    _db([_model("m", metadata={"context_length": 32768})])
    plan = pc.plan_conversion()
    after = {s.profile_after for s in plan.slots}
    assert len(after) == 1 and len(plan.profiles) == 1


def test_generated_tune_reuses_an_existing_operator_profile() -> None:
    _write(paths.profiles_toml(), {"profile": {"mine": {"flags": "-fa auto -b 99", "mtp": False}}})
    _slot(
        "1",
        {
            "id": 1,
            "name": "a",
            "type": "llm",
            "device": "gpu-rocm",
            "profile": "chat",
            "port": 8082,
            "model": {"default": "m"},
        },
    )
    _db([_model("m", defaults={"profile": "chat", "extra_args": "-fa auto -b 99"})])
    plan = pc.plan_conversion()
    assert _by_name(plan)["a"].profile_after == "mine"
    assert plan.profiles == {}


def test_cli_preview_writes_nothing(install: None) -> None:
    from hal0.cli.main import app

    before = {p: p.read_bytes() for p in paths.etc().rglob("*") if p.is_file()}
    result = CliRunner().invoke(app, ["profile", "migrate"])
    assert result.exit_code == 0, result.output
    assert "thinking-big-chat" in result.output
    assert "inert today, dropped" in result.output
    after = {p: p.read_bytes() for p in paths.etc().rglob("*") if p.is_file()}
    assert before == after

    result = CliRunner().invoke(app, ["profile", "migrate", "--json"])
    assert result.exit_code == 0, result.output
    assert "thinking-big-chat" in json.loads(result.output)["profiles"]


def test_target_seeds_are_in_the_new_shape() -> None:
    allowed = {
        "flags",
        "ctx_size",
        "enable_thinking",
        "vision",
        "mtp",
        "runner",
        "intent",
        "cloned_from",
    }
    for name, seed in pc.target_seeds().items():
        assert set(seed) <= allowed, name
        conv = pc.convert_flags(seed.get("flags", ""), source="profile")
        assert not conv.notes and not conv.errors, (name, conv.notes, conv.errors)
        if "--model_path" in seed.get("flags", ""):
            assert name in {"kokoro", "moonshine", "qwen3-tts"}, name
    assert pc.target_seeds()["agent"]["ctx_size"] >= 64000
