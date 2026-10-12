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
import sqlite3
from pathlib import Path
from typing import Any

import pytest
import tomli_w
from typer.testing import CliRunner

from hal0.config import paths
from hal0.config.migrations import profile_centric as pc
from hal0.db.repository import model_to_row
from hal0.registry.model import Model, ModelDefaults

THINKING = (
    "--jinja -fa auto -b 2048 -ub 512 --reasoning on --reasoning-format deepseek "
    "--temp 1.0 --top-p 0.95 --no-mmproj"
)
CODING_TUNE = "--jinja -fa auto -b 2048 -ub 512 --reasoning off --temp 0.7 --top-k 40 --no-mmproj"


def _write(path: Path, data: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(tomli_w.dumps(data).encode())


def _db(models: list[Model]) -> None:
    rows = [model_to_row(m) for m in models]
    target = paths.db_path()
    target.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(target)
    cols = list(rows[0])
    conn.execute(f"CREATE TABLE model ({', '.join(cols)})")
    for row in rows:
        conn.execute(
            f"INSERT INTO model ({', '.join(cols)}) VALUES ({', '.join('?' for _ in cols)})",
            [row[c] for c in cols],
        )
    conn.commit()
    conn.close()


def _model(model_id: str, **kw: Any) -> Model:
    defaults = kw.pop("defaults", None)
    return Model(
        id=model_id,
        path=kw.pop("path", f"/models/{model_id}/model.gguf"),
        defaults=ModelDefaults(**defaults) if defaults else None,
        **kw,
    )


def _slot(stem: str, data: dict[str, Any]) -> None:
    _write(paths.slots_config_dir() / f"{stem}.toml", data)


@pytest.fixture
def install() -> None:
    _write(
        paths.profiles_toml(),
        {
            "legacy_seeds_migrated": True,
            "legacy_seeds_adopted": ["coding", "thinking"],
            "profile": {
                "thinking": {"flags": THINKING, "mtp": False, "quant": ""},
                "coding": {"flags": CODING_TUNE, "mtp": False, "quant": ""},
            },
        },
    )
    _slot(
        "1",
        {
            "id": 1,
            "name": "agent",
            "type": "llm",
            "device": "gpu-vulkan",
            "backend": "vulkan",
            "profile": "thinking",
            "port": 8082,
            "parallel": 1,
            "threads": 16,
            "model": {"default": "big-chat", "context_size": 96000},
            "server": {"extra_args": "-ctk q8_0 -ctv q8_0"},
        },
    )
    _slot(
        "2",
        {
            "id": 2,
            "name": "brain",
            "type": "llm",
            "device": "gpu-vulkan",
            "profile": "brain",
            "port": 8087,
            "chat_template": "auto",
            "model": {"default": "small-chat", "context_size": 66000},
        },
    )
    _slot(
        "3",
        {
            "id": 3,
            "name": "code",
            "type": "llm",
            "device": "gpu-rocm",
            "profile": "rocm-dense-gone",
            "port": 8085,
            "model": {"default": "", "context_size": 96000},
        },
    )
    _slot(
        "5",
        {
            "id": 5,
            "name": "embed",
            "type": "embedding",
            "device": "gpu-rocm",
            "port": 8089,
            "model": {"default": "embedder", "context_size": 8192},
        },
    )
    _slot(
        "8",
        {
            "id": 8,
            "name": "flm",
            "type": "llm",
            "device": "npu",
            "profile": "flm",
            "port": 8088,
            "model": {"default": "npu-chat"},
            "npu": {"chat": True, "asr": True, "embed": False},
        },
    )
    _slot(
        "15",
        {
            "id": 15,
            "name": "utility",
            "type": "llm",
            "device": "gpu-vulkan",
            "profile": "chat",
            "port": 8081,
            "model": {"default": "no-native", "context_size": 98304},
        },
    )
    _slot(
        "14",
        {
            "id": 14,
            "name": "tts",
            "type": "tts",
            "device": "gpu-rocm",
            "profile": "qwen3-tts",
            "port": 8084,
            "model": {"default": "qwen3-tts", "context_size": 16000},
        },
    )
    _slot(
        "coder",
        {
            "name": "coder",
            "type": "llm",
            "device": "gpu-vulkan",
            "profile": "coding",
            "port": 8083,
            "model": {"context_size": 32768},
        },
    )
    _slot(
        "16",
        {
            "id": 16,
            "name": "clean",
            "type": "llm",
            "device": "gpu-rocm",
            "profile": "chat",
            "port": 8090,
            "model": {"default": "untuned"},
        },
    )
    _write(
        paths.stacks_toml(),
        {
            "stack": {
                "s1": {
                    "slots": [
                        {
                            "slot": "agent",
                            "model": "big-chat",
                            "profile": "chat",
                            "vision": False,
                            "mtp": True,
                        },
                        {"slot": "brain", "profile": "brain", "server_extra_args": "-b 64"},
                    ]
                }
            }
        },
    )
    _db(
        [
            _model(
                "big-chat", mmproj="/models/big-chat/mm.gguf", metadata={"context_length": 262144}
            ),
            _model(
                "small-chat",
                metadata={"context_length": 131072},
                defaults={"profile": "coding", "extra_args": CODING_TUNE},
            ),
            _model("embedder"),
            _model("npu-chat", defaults={"enable_thinking": True}),
            _model("no-native"),
            _model("untuned", metadata={"context_length": 32768}),
            _model("unbound", defaults={"context_size": 98304}),
            _model("mtp-only", tags=["mtp"], defaults={"mtp": True}),
        ]
    )


def _by_name(plan: pc.ConversionPlan) -> dict[str, pc.SlotPlan]:
    return {s.name: s for s in plan.slots}


def test_plan_preserves_what_launches(install: None) -> None:
    plan = pc.plan_conversion()
    slots = _by_name(plan)
    assert plan.policy == "preserve_launch"
    assert not plan.blocked

    agent = slots["agent"]
    assert agent.status == "convert" and agent.generated
    assert agent.profile_after == "thinking-big-chat"
    content = plan.profiles["thinking-big-chat"]
    assert content["ctx_size"] == 96000
    assert content["vision"] == "off"  # --no-mmproj beat --mmproj
    assert "-ctk" not in content["flags"]  # inert, not applied
    assert "--jinja" not in content["flags"]
    assert {(i.key, i.applied) for i in agent.inert} == {("extra_args", False), ("parallel", False)}
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
    assert embed.launch_changes and "embedding" in embed.launch_changes[0]
    # ceiling 8192 == the 8192 fallback of a model with no native window: the
    # seed reproduces it, so no profile is generated.
    assert embed.profile_after == "embedding" and not embed.generated

    assert slots["flm"].profile_after == "flm-npu-chat"
    assert plan.profiles["flm-npu-chat"]["enable_thinking"] is True
    assert plan.profiles["flm-npu-chat"]["runner"] == "flm"

    utility = slots["utility"]
    assert utility.profile_after == "chat" and not utility.generated
    assert [(i.key, i.value) for i in utility.inert] == [("context_size", 98304)]

    assert slots["coder"].profile_after == "coding"  # ceiling == dense cap
    assert slots["clean"].status == "unchanged"

    tts = slots["tts"]
    assert any(m.model_id == "qwen3-tts" and m.register_path for m in plan.models)
    assert ("context_size", 16000) in [(i.key, i.value) for i in tts.inert]

    models = {m.model_id: m for m in plan.models}
    assert models["unbound"].generated_profile == "chat-unbound"
    assert plan.profiles["chat-unbound"]["ctx_size"] == 98304
    assert "mtp-only" not in models  # mtp is a model fact

    assert set(plan.profile_rewrites) >= {"thinking", "coding", "(profiles.toml)"}
    rows = {r.slot: r for r in plan.stacks}
    assert rows["agent"].drop_keys == ["vision", "mtp"]
    assert [(i.key, i.applied) for i in rows["brain"].inert] == [("server_extra_args", False)]


def test_apply_intent_switch_folds_inert_values(install: None) -> None:
    plan = pc.plan_conversion(policy="apply_intent")
    slots = _by_name(plan)
    agent = slots["agent"]
    flags = plan.profiles[agent.profile_after]["flags"]
    assert "-ctk q8_0 -ctv q8_0" in flags and "--parallel 1" in flags
    assert all(i.applied for i in agent.inert)
    utility = slots["utility"]
    assert utility.generated
    assert plan.profiles[utility.profile_after]["ctx_size"] == 98304
    rows = {r.slot: r for r in plan.stacks}
    assert rows["brain"].profile_after is not None
    assert "-b 64" in plan.profiles[rows["brain"].profile_after]["flags"]


def test_inert_switch_is_the_only_policy_reader() -> None:
    items = [
        pc.InertValue("slot", "extra_args", "-b 1"),
        pc.InertValue("slot", "chat_template", "x"),
    ]
    adds, marked = pc.inert_additions("preserve_launch", items)
    assert adds == pc.InertAdditions() and not any(i.applied for i in marked)
    adds, marked = pc.inert_additions("apply_intent", items)
    assert adds.flags == ("-b", "1")
    assert [i.applied for i in marked] == [True, False]
    with pytest.raises(ValueError):
        pc.inert_additions("whatever", items)  # type: ignore[arg-type]
    assert pc.DEFAULT_INERT_POLICY == "preserve_launch"


def test_converted_install_plans_nothing() -> None:
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
    plan = pc.plan_conversion()
    assert not plan.changed, plan.lines()
    assert plan.lines()[-1] == "nothing to convert"


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
    assert "inert today, not applied" in result.output
    after = {p: p.read_bytes() for p in paths.etc().rglob("*") if p.is_file()}
    assert before == after

    result = CliRunner().invoke(app, ["profile", "migrate", "--json", "--inert", "apply_intent"])
    assert result.exit_code == 0, result.output
    assert json.loads(result.output)["policy"] == "apply_intent"

    result = CliRunner().invoke(app, ["profile", "migrate", "--inert", "bogus"])
    assert result.exit_code != 0
