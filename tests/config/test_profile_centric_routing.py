"""Profile-centric planner: every flag lands somewhere, or the slot blocks.

Each test builds a small install, plans the conversion, and checks that every
difference between what the slot launches today and what it launches after
is either a reported launch change or a blocked slot, never silent.
"""

from __future__ import annotations

import json
import sqlite3
from pathlib import Path
from typing import Any

import tomli_w
from typer.testing import CliRunner

from hal0.config import paths
from hal0.config.migrations import profile_centric as pc
from hal0.db.repository import model_to_row
from hal0.registry.model import Model, ModelDefaults


def _write(path: Path, data: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(tomli_w.dumps(data).encode())


def _db(models: list[Model], holding: list[dict[str, Any]] | None = None) -> None:
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
    if holding is not None:
        conn.execute(
            f"CREATE TABLE {pc.HOLDING_TABLE} (id, profile, extra_args, n_gpu_layers, "
            "context_size, rope_freq_base, preferred_runner, enable_thinking, vision)"
        )
        for h in holding:
            keys = list(h)
            conn.execute(
                f"INSERT INTO {pc.HOLDING_TABLE} ({', '.join(keys)}) "
                f"VALUES ({', '.join('?' for _ in keys)})",
                [h[k] for k in keys],
            )
    conn.commit()
    conn.close()


def _model(model_id: str = "m", **kw: Any) -> Model:
    defaults = kw.pop("defaults", None)
    return Model(
        id=model_id,
        path=kw.pop("path", f"/models/{model_id}.gguf"),
        defaults=ModelDefaults(**defaults) if defaults else None,
        **kw,
    )


def _slot(stem: str = "1", **kw: Any) -> None:
    data: dict[str, Any] = {
        "id": int(stem) if stem.isdigit() else None,
        "name": kw.pop("name", "s"),
        "type": "llm",
        "device": "gpu-rocm",
        "port": 8081,
        "model": {"default": "m"},
    }
    data.update(kw)
    _write(
        paths.slots_config_dir() / f"{stem}.toml", {k: v for k, v in data.items() if v is not None}
    )


def _one(plan: pc.ConversionPlan) -> pc.SlotPlan:
    assert len(plan.slots) == 1, plan.lines()
    return plan.slots[0]


# ── launch changes that are reported, never silent ───────────────────────────


def test_mtp_draft_device_follows_the_slot_device() -> None:
    _slot(profile="chat", device="gpu-vulkan")
    _db([_model(tags=["mtp"])])
    sp = _one(pc.plan_conversion())
    assert sp.status == "convert"
    assert any("MTP draft device" in c and "Vulkan0" in c for c in sp.launch_changes)


def test_forced_mtp_on_a_runner_without_mtp_is_reported_as_stopping() -> None:
    _slot(profile="cpu-chat", device="cpu")
    _db([_model(defaults={"mtp": True})])
    sp = _one(pc.plan_conversion())
    assert any("MTP drafting stops" in c for c in sp.launch_changes)
    assert not any("draft device" in c for c in sp.launch_changes)


def test_explicit_rocm_spec_draft_device_override_is_reported() -> None:
    _slot(profile="chat")
    _db(
        [
            _model(
                tags=["mtp"],
                defaults={"profile": "chat", "extra_args": "--spec-draft-device Vulkan0"},
            )
        ]
    )
    sp = _one(pc.plan_conversion())
    assert any("Vulkan0 -> ('ROCm0',)" in c or "Vulkan0" in c for c in sp.launch_changes)


def test_explicit_mtp_false_moves_to_the_profile() -> None:
    _slot(profile="chat")
    _db([_model(tags=["mtp"], defaults={"mtp": False})])
    plan = pc.plan_conversion()
    sp = _one(plan)
    assert plan.profiles[sp.profile_after]["mtp"] == "off"
    assert {m.model_id: m.clear for m in plan.models}["m"] == {"mtp": False}
    assert not sp.launch_changes


def test_fit_vetoed_profile_flags_do_not_start_applying() -> None:
    """Today a cpu-only custom profile is not applied on a GPU slot."""
    _write(
        paths.profiles_toml(),
        {"profile": {"cpu-only": {"flags": "-b 64 --temp 9", "device_class": "cpu", "mtp": False}}},
    )
    _slot(profile="cpu-only")
    _db([_model(defaults={"profile": "chat", "extra_args": "-b 32"})])
    plan = pc.plan_conversion()
    sp = _one(plan)
    assert sp.status == "convert" and sp.generated
    flags = plan.profiles[sp.profile_after]["flags"]
    assert "--temp 9" not in flags and "-b 32" in flags


# ── flags with no profile home ───────────────────────────────────────────────


def test_model_fact_flag_in_the_tune_blocks_the_slot() -> None:
    _slot(profile="chat")
    _db([_model(defaults={"extra_args": "--chat-template-file /x.jinja -b 8"})])
    plan = pc.plan_conversion()
    sp = _one(plan)
    assert sp.status == "blocked" and "--chat-template-file" in (sp.error or "")


def test_mmproj_auto_and_no_mmproj_auto_are_routed() -> None:
    _slot(profile="chat")
    _db([_model(mmproj="/m.mmproj", defaults={"extra_args": "--no-mmproj-auto -b 8"})])
    plan = pc.plan_conversion()
    sp = _one(plan)
    assert sp.status == "convert"
    content = plan.profiles[sp.profile_after]
    assert content["vision"] == "off" and "mmproj" not in content["flags"]


def test_non_mtp_spec_draft_device_blocks() -> None:
    _slot(profile="chat")
    _db([_model(defaults={"extra_args": "--spec-type draft-simple --spec-draft-device CUDA0"})])
    sp = _one(pc.plan_conversion())
    assert sp.status == "blocked" and "--spec-draft-device" in (sp.error or "")


def test_profile_rewrites_route_every_forbidden_flag() -> None:
    _write(
        paths.profiles_toml(),
        {
            "profile": {
                "mine": {
                    "flags": "--jinja -fa auto --reasoning-format=none -ngl 99 -c 4096 "
                    "--no-mmproj --mmproj-auto --spec-draft-device ROCm0 --temp 0.3",
                    "mtp": False,
                    "quant": "",
                    "device_class": "gpu",
                }
            }
        },
    )
    plan = pc.plan_conversion()
    notes = " | ".join(plan.profile_rewrites["mine"])
    for word in (
        "--reasoning-format",
        "-ngl",
        "-c",
        "--jinja",
        "vision",
        "--mmproj-auto",
        "--spec-draft-device",
        "device_class",
    ):
        assert word in notes, word


def test_custom_profile_with_a_model_fact_flag_blocks_its_slots() -> None:
    _write(
        paths.profiles_toml(),
        {"profile": {"tmpl": {"flags": "--chat-template chatml -b 8", "mtp": False}}},
    )
    _slot(profile="tmpl", model={"default": ""})
    plan = pc.plan_conversion()
    assert _one(plan).status == "blocked"


# ── seeds, customs and demoted seeds ─────────────────────────────────────────


def test_custom_named_like_a_new_seed_is_renamed() -> None:
    _write(paths.profiles_toml(), {"profile": {"agent": {"flags": "-b 1 --temp 3", "mtp": False}}})
    _slot(profile="agent")
    _db([_model()])
    plan = pc.plan_conversion()
    assert plan.renames == {"agent": "agent-custom"}
    sp = _one(plan)
    # The slot keeps its own tune under the renamed custom profile.
    assert sp.profile_after == "agent-custom"
    assert "--temp 3" in plan.profiles["agent-custom"]["flags"]
    assert "-b 1" in " ".join(sp.argv_after)


def test_custom_identical_to_a_new_seed_just_becomes_the_seed() -> None:
    seed = pc.target_seeds()["coding"]
    _write(
        paths.profiles_toml(),
        {
            "profile": {
                "coding": {"flags": "--jinja " + seed["flags"] + " --no-mmproj", "mtp": False}
            }
        },
    )
    plan = pc.plan_conversion()
    assert plan.renames == {}
    assert "identical to the new seed" in plan.profile_rewrites["coding"][0]


def test_fresh_install_demoted_reference_is_adopted_in_the_new_shape() -> None:
    _slot(profile="dense")
    _db([_model()])
    plan = pc.plan_conversion()
    assert "dense" in plan.profiles
    assert "--jinja" not in plan.profiles["dense"]["flags"]
    assert plan.profiles["dense"]["vision"] == "off"


def test_settled_deleted_demoted_name_is_not_resurrected() -> None:
    _write(
        paths.profiles_toml(),
        {"legacy_seeds_migrated": True, "legacy_seeds_adopted": ["dense"], "profile": {}},
    )
    _slot(profile="dense")
    _db([_model()])
    plan = pc.plan_conversion()
    assert "dense" not in plan.profiles
    assert _one(plan).profile_after != "dense"


def test_markers_stay_while_any_slot_is_blocked() -> None:
    _write(
        paths.profiles_toml(),
        {"legacy_seeds_migrated": True, "legacy_seeds_adopted": [], "profile": {}},
    )
    _slot(profile="chat")
    _db([_model(defaults={"extra_args": "--mmproj /x"})])
    plan = pc.plan_conversion()
    assert plan.blocked and "(profiles.toml)" not in plan.profile_rewrites
    assert any("markers" in w for w in plan.warnings)


# ── engines ──────────────────────────────────────────────────────────────────


def test_flm_ctx_is_always_explicit() -> None:
    _slot(device="npu", profile="flm", model={"default": "npu-m"})
    _db([_model("npu-m")])
    plan = pc.plan_conversion()
    sp = _one(plan)
    assert plan.profiles[sp.profile_after]["ctx_size"] == 8192


def test_tts_slot_model_path_checks_run_even_without_tuning() -> None:
    _slot(type="tts", profile="kokoro", model={"default": "kok"})
    _db([_model("kok", path="/elsewhere")])
    sp = _one(pc.plan_conversion())
    assert sp.status == "blocked" and "--model_path" in (sp.error or "")


def test_tts_slot_without_model_row_registers_it() -> None:
    _slot(type="tts", profile="qwen3-tts", model={"default": "q3"})
    _db([_model("other")])
    plan = pc.plan_conversion()
    assert any(m.model_id == "q3" and m.register_path for m in plan.models)


def test_profile_less_kokoro_slot_is_reported_as_failing_today() -> None:
    _slot(type="tts", model={"default": ""})
    plan = pc.plan_conversion()
    sp = _one(plan)
    assert any("fails to launch today" in c for c in sp.launch_changes)


# ── files, stacks, models ────────────────────────────────────────────────────


def test_only_the_file_the_loader_uses_is_planned() -> None:
    _slot("1", name="agent", profile="chat")
    _slot("agent", name="agent", profile="chat")
    _db([_model()])
    plan = pc.plan_conversion()
    assert [s.file for s in plan.slots] == ["1.toml"]
    assert plan.shadowed == ["agent.toml"]


def test_holding_table_supplies_model_tuning() -> None:
    _slot(profile="chat")
    _db(
        [_model(metadata={"context_length": 262144})],
        holding=[{"id": "m", "context_size": 50000, "enable_thinking": 1}],
    )
    plan = pc.plan_conversion()
    sp = _one(plan)
    content = plan.profiles[sp.profile_after]
    assert content["ctx_size"] == 50000 and content["enable_thinking"] is True


def test_stack_rows_and_embedded_profiles_are_planned() -> None:
    _slot(profile="vulkan-gone", model={"default": "m", "context_size": 96000})
    _db([_model(metadata={"context_length": 262144})])
    _write(
        paths.stacks_toml(),
        {
            "stack": {
                "st": {
                    "slots": [
                        {
                            "slot": "s",
                            "model": "m",
                            "profile": "vulkan-gone",
                            "vision": False,
                            "mtp": True,
                        },
                        {"slot": "x", "profile": "moe"},
                    ],
                    "profiles": {"emb": {"flags": "--jinja -b 2 --mmproj /p", "mtp": False}},
                }
            }
        },
    )
    plan = pc.plan_conversion()
    (st,) = plan.stacks
    rows = {r["slot"]: r["change"] for r in st.rows}
    assert "the converted slot's" in rows["s"]
    assert "drop vision, mtp" in rows["s"]
    # `moe` only resolved through demoted-seed adoption; it is adopted as a
    # custom profile, so the row keeps its name and the stack still applies.
    assert "x" not in rows and "moe" in plan.profiles
    assert st.errors and "--mmproj" in st.errors[0]


def test_cli_exits_2_when_blocked_and_json_reports_it() -> None:
    from hal0.cli.main import app

    _slot(profile="chat")
    _db([_model(defaults={"extra_args": "--mmproj /x"})])
    result = CliRunner().invoke(app, ["profile", "migrate", "--json"])
    assert result.exit_code == 2
    data = json.loads(result.output)
    assert data["blocked"] == ["1.toml"] and data["changed"] is True
