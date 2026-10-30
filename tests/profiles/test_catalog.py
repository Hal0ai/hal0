from __future__ import annotations

from pathlib import Path

import pytest

from hal0.config.loader import load_profiles_config, save_profiles_config
from hal0.config.schema import ProfileConfig
from hal0.errors import BadRequest, Conflict, NotFound, UnprocessableEntity
from hal0.profiles import UNSET, ProfileCatalog, ProfilePatch, _runtime_family


def test_resolve_seed_profile_includes_runtime_facts(tmp_hal0_home: str) -> None:
    profile = ProfileCatalog().resolve("flm")

    assert profile.seed is True
    assert profile.runtime_family == "flm"
    assert profile.supported_slot_types == ("llm", "embedding", "transcription")


def test_resolve_qwen3tts_seed_is_gpu_tts_family(tmp_hal0_home: str) -> None:
    profile = ProfileCatalog().resolve("qwen3-tts")

    assert profile.seed is True
    assert profile.runtime_family == "qwen3tts"
    # Qwen3-TTS is a TTS-only runtime family; seeded profiles are device-agnostic.
    assert profile.supported_slot_types == ("tts",)
    assert profile.rtf == 0.48


def test_resolved_profile_items_carry_the_profile_centric_fields(tmp_hal0_home: str) -> None:
    """ADR-0029: items expose the tune; device_class/backend/quant are gone."""
    item = ProfileCatalog().resolve("agent").to_dict()
    assert item["ctx_size"] == 65536
    assert item["vision"] == "off"
    assert item["speculation"] == "auto"
    assert item["enable_thinking"] is None
    assert item["resolved_flags"] == item["flags"]
    assert not {"device_class", "backend", "quant"} & set(item)


def test_shipped_slot_seeds_name_seed_profiles(tmp_hal0_home: str) -> None:
    """Slots are built around the seeds hal0 ships (ADR-0029 decision 4)."""
    by_name = {p.name: p for p in ProfileCatalog().list()}
    for name in ("agent", "utility", "coding", "brain", "embedding", "reranking", "flm"):
        assert by_name[name].seed is True


def test_create_update_delete_profile(tmp_hal0_home: str) -> None:
    catalog = ProfileCatalog()

    created = catalog.create(
        "my-rocm",
        ProfileConfig(flags="-fa on", ctx_size=32768, enable_thinking=False, speculation="off"),
    )
    assert created.seed is False
    assert created.runtime_family == "llama-server"
    # A profile's flags are exactly what the operator wrote: the MTP draft
    # bundle is composed at launch from the model + runner, never stored here.
    assert created.resolved_flags == "-fa on"
    assert created.ctx_size == 32768
    assert created.enable_thinking is False
    assert created.speculation == "off"

    updated = catalog.update("my-rocm", ProfilePatch(flags="-fa off", speculation="auto"))
    assert updated.flags == "-fa off"
    assert updated.resolved_flags == "-fa off"
    assert updated.speculation == "auto"
    # Unpatched tune fields keep their stored value.
    assert updated.ctx_size == 32768
    assert updated.enable_thinking is False

    catalog.delete("my-rocm")
    assert all(profile.name != "my-rocm" for profile in catalog.list())


def test_update_ctx_size_and_enable_thinking_distinguish_unset_from_none(
    tmp_hal0_home: str,
) -> None:
    """UNSET leaves a stored value alone; None clears it."""
    catalog = ProfileCatalog()
    catalog.create("tuned", ProfileConfig(ctx_size=16384, enable_thinking=True, vision="off"))

    left = catalog.update("tuned", ProfilePatch(intent="renamed"))
    assert (left.ctx_size, left.enable_thinking, left.vision) == (16384, True, "off")
    assert ProfilePatch().ctx_size is UNSET

    changed = catalog.update("tuned", ProfilePatch(ctx_size=8192, vision="auto"))
    assert (changed.ctx_size, changed.enable_thinking, changed.vision) == (8192, True, "auto")

    cleared = catalog.update("tuned", ProfilePatch(ctx_size=None, enable_thinking=None))
    assert cleared.ctx_size is None
    assert cleared.enable_thinking is None
    assert catalog.resolve("tuned").ctx_size is None


@pytest.mark.parametrize(
    ("flags", "code"),
    [
        ("--jinja -fa on", "profiles.model_fact_flag_denied"),
        ("--no-mmproj", "profiles.model_fact_flag_denied"),
        ("--mmproj /x/mmproj.gguf", "profiles.model_fact_flag_denied"),
        ("--chat-template-file t.jinja", "profiles.model_fact_flag_denied"),
        ("-fa on --spec-draft-device ROCm0", "profiles.model_fact_flag_denied"),
        ("--reasoning-format none", "slot.reasoning_format_none_denied"),
        ("-fa on --ctx-size 4096", "slot.managed_arg_denied"),
        ("-ngl 99", "slot.hardware_flag_denied"),
    ],
)
def test_create_screens_every_flag_a_profile_may_not_carry(
    tmp_hal0_home: str, flags: str, code: str
) -> None:
    """Model-fact, managed, hardware and --spec-draft-device flags never persist."""
    catalog = ProfileCatalog()
    with pytest.raises(BadRequest) as exc:
        catalog.create("screened", ProfileConfig(flags=flags))
    assert exc.value.code == code
    assert all(profile.name != "screened" for profile in catalog.list())


# ── #1730: malformed shell quoting must never reach disk ─────────────────
#
# #1639 added a launch-time guard (providers.container, code
# slot.profile_flags_malformed) so a divergent profile with unmatched quotes
# no longer 500s the launch/preview path — but that only fires for a profile
# that ALREADY made it onto disk. The actual write seam
# (screen_profile_flags, called by both ProfileCatalog.create/update and the
# API routes) used to swallow the shlex.split ValueError and return, letting
# bad quoting sail straight through to persistence.


def test_create_rejects_unmatched_quoting(tmp_hal0_home: str) -> None:
    catalog = ProfileCatalog()
    with pytest.raises(UnprocessableEntity) as exc_info:
        catalog.create("bad-quotes", ProfileConfig(flags='-fa on "unterminated'))
    assert exc_info.value.code == "profiles.flags_malformed"
    # Never persisted — a re-list must not show the rejected profile.
    assert all(profile.name != "bad-quotes" for profile in catalog.list())


def test_update_rejects_unmatched_quoting(tmp_hal0_home: str) -> None:
    catalog = ProfileCatalog()
    catalog.create("good-profile", ProfileConfig(flags="-fa on"))
    with pytest.raises(UnprocessableEntity) as exc_info:
        catalog.update("good-profile", ProfilePatch(flags="-fa on 'unterminated"))
    assert exc_info.value.code == "profiles.flags_malformed"
    # The prior valid flags must survive an update rejected on save.
    assert catalog.resolve("good-profile").flags == "-fa on"


def test_update_rechecks_flags_even_when_a_stored_profile_is_malformed(
    tmp_hal0_home: str,
) -> None:
    """There is no grandfathering (ADR-0029): the converter fixes stored flags
    once, so a save that sends flags is always screened."""
    cfg = load_profiles_config()
    cfg.profile["stored-bad"] = ProfileConfig(flags='-fa on "unterminated')
    save_profiles_config(cfg)
    catalog = ProfileCatalog()
    with pytest.raises(UnprocessableEntity) as exc_info:
        catalog.update("stored-bad", ProfilePatch(flags='-fa on "unterminated'))
    assert exc_info.value.code == "profiles.flags_malformed"
    # A patch that leaves flags alone still goes through.
    assert catalog.update("stored-bad", ProfilePatch(intent="fixed later")).intent == "fixed later"


def test_delete_profile_in_use_raises_conflict(tmp_hal0_home: str) -> None:
    root = Path(tmp_hal0_home) / "etc" / "hal0" / "slots"
    root.mkdir(parents=True, exist_ok=True)
    (root / "chat.toml").write_text(
        "\n".join(
            [
                "[slot]",
                'name = "chat"',
                "port = 8081",
                'profile = "my-rocm"',
                "",
            ]
        ),
        encoding="utf-8",
    )
    catalog = ProfileCatalog()
    catalog.create("my-rocm", ProfileConfig())

    with pytest.raises(Conflict) as exc:
        catalog.delete("my-rocm")

    assert exc.value.code == "profiles.in_use"
    assert exc.value.details["slots"] == ["chat"]


def _slots_dir(tmp_hal0_home: str) -> Path:
    root = Path(tmp_hal0_home) / "etc" / "hal0" / "slots"
    root.mkdir(parents=True, exist_ok=True)
    return root


def test_delete_profile_blocked_by_unconverted_slot(tmp_hal0_home: str) -> None:
    """A slot refused with ``slot.unconverted`` still names its profile (#2574)."""
    root = _slots_dir(tmp_hal0_home)
    # Legacy nested layout: tuning key under [slot] fails the typed load.
    (root / "chat.toml").write_text(
        '[slot]\nname = "chat"\nport = 8081\nprofile = "my-rocm"\nctx_size = 4096\n',
        encoding="utf-8",
    )
    # Flat layout, id-keyed stem: the display name comes from inside the file.
    (root / "143.toml").write_text(
        'id = 143\nname = "brain"\nport = 8082\nprofile = "my-rocm"\nparallel = 2\n',
        encoding="utf-8",
    )
    catalog = ProfileCatalog()
    catalog.create("my-rocm", ProfileConfig())

    assert sorted(catalog.slots_using("my-rocm")) == ["brain", "chat"]
    by_name = {p.name: p for p in catalog.list()}
    assert sorted(by_name["my-rocm"].used_by) == ["brain", "chat"]

    with pytest.raises(Conflict) as exc:
        catalog.delete("my-rocm")

    assert exc.value.code == "profiles.in_use"
    assert sorted(exc.value.details["slots"]) == ["brain", "chat"]
    assert "my-rocm" in load_profiles_config().profile


def test_delete_profile_fails_closed_on_unparseable_slot(tmp_hal0_home: str) -> None:
    """A slot that is not even TOML blocks the delete, but not the listing (#2574)."""
    root = _slots_dir(tmp_hal0_home)
    (root / "good.toml").write_text(
        '[slot]\nname = "good"\nport = 8081\nprofile = "chat"\n', encoding="utf-8"
    )
    (root / "broken.toml").write_text("[slot\nname = \n", encoding="utf-8")
    catalog = ProfileCatalog()
    catalog.create("my-rocm", ProfileConfig())

    by_name = {p.name: p for p in catalog.list()}
    assert by_name["chat"].used_by == ("good",)
    assert by_name["my-rocm"].used_by == ()

    with pytest.raises(Conflict) as exc:
        catalog.delete("my-rocm")

    assert exc.value.code == "profiles.slot_unreadable"
    assert exc.value.details["slots"] == ["broken"]
    assert "broken" in str(exc.value)
    assert "my-rocm" in load_profiles_config().profile

    # Once the slot is repaired (or removed) the delete goes through.
    (root / "broken.toml").unlink()
    catalog.delete("my-rocm")
    assert "my-rocm" not in load_profiles_config().profile


def test_cloned_from_persists_and_round_trips(tmp_hal0_home: str) -> None:
    catalog = ProfileCatalog()

    created = catalog.create(
        "vulkan-custom",
        ProfileConfig(flags="-fa on", cloned_from="vulkan"),
    )
    assert created.cloned_from == "vulkan"
    assert created.to_dict()["cloned_from"] == "vulkan"

    # Survives the profiles.toml round trip on a fresh catalog instance.
    reloaded = ProfileCatalog().resolve("vulkan-custom")
    assert reloaded.cloned_from == "vulkan"


def test_cloned_from_defaults_to_none_and_survives_update(tmp_hal0_home: str) -> None:
    catalog = ProfileCatalog()

    plain = catalog.create("my-rocm", ProfileConfig())
    assert plain.cloned_from is None

    catalog.create("my-copy", ProfileConfig(cloned_from="my-rocm"))
    updated = catalog.update("my-copy", ProfilePatch(flags="-fa off"))
    assert updated.cloned_from == "my-rocm"


# ── profiles overhaul: bench / intent / used_by ─────────────────────────


def test_seed_bench_metrics_exposed(tmp_hal0_home: str) -> None:
    by_name = {p.name: p for p in ProfileCatalog().list()}
    assert by_name["chat"].tps == 52.8
    # TTS is synth — reported as a real-time factor, not tok/s.
    assert by_name["kokoro"].tps is None
    assert by_name["kokoro"].rtf == 0.18


def test_seed_intent_exposed(tmp_hal0_home: str) -> None:
    by_name = {p.name: p for p in ProfileCatalog().list()}
    assert by_name["chat"].intent == "Generic chat (fallback for unknown models)"
    assert "64K ctx" in by_name["agent"].intent


def test_custom_profile_has_no_bench_and_round_trips_intent(
    tmp_hal0_home: str,
) -> None:
    catalog = ProfileCatalog()
    created = catalog.create(
        "my-tuned",
        ProfileConfig(intent="My workload", ctx_size=24576),
    )
    assert created.intent == "My workload"
    assert created.tps is None
    assert created.rtf is None

    reloaded = ProfileCatalog().resolve("my-tuned")
    assert reloaded.intent == "My workload"
    assert reloaded.ctx_size == 24576


def test_used_by_lists_bound_slots(tmp_hal0_home: str) -> None:
    root = Path(tmp_hal0_home) / "etc" / "hal0" / "slots"
    root.mkdir(parents=True, exist_ok=True)
    for slot in ("primary", "agent"):
        (root / f"{slot}.toml").write_text(
            "\n".join(["[slot]", f'name = "{slot}"', "port = 8081", 'profile = "chat"', ""]),
            encoding="utf-8",
        )
    by_name = {p.name: p for p in ProfileCatalog().list()}
    assert sorted(by_name["chat"].used_by) == ["agent", "primary"]
    assert by_name["embedding"].used_by == ()
    assert by_name["chat"].to_dict()["used_by"] == ["agent", "primary"]


def test_used_by_lists_bound_slots_id_keyed(tmp_hal0_home: str) -> None:
    # P3-runtime-db inc4: an id-keyed slot TOML (stem is the digit id, name
    # lives inside the file) must still be reported by its real display name,
    # not the digit stem — the "list_slots CLI/portable callers" id-awareness
    # fix deferred from inc1.
    root = Path(tmp_hal0_home) / "etc" / "hal0" / "slots"
    root.mkdir(parents=True, exist_ok=True)
    (root / "143.toml").write_text(
        "\n".join(["[slot]", "id = 143", 'name = "brain"', "port = 8081", 'profile = "chat"', ""]),
        encoding="utf-8",
    )
    by_name = {p.name: p for p in ProfileCatalog().list()}
    assert by_name["chat"].used_by == ("brain",)
    assert by_name["chat"].to_dict()["used_by"] == ["brain"]


def test_create_profile_with_runner(tmp_hal0_home: str) -> None:
    cat = ProfileCatalog()
    prof = cat.create("pf-setup", ProfileConfig(flags="-fa on", runner="cuda"))
    assert prof.runner == "cuda"
    assert cat.resolve("pf-setup").to_dict()["runner"] == "cuda"


@pytest.mark.parametrize("retired", ["vulkanfpx", "rocmfpx", "strix", "promptforge"])
def test_create_profile_with_a_retired_runner_key_is_rejected(
    tmp_hal0_home: str, retired: str
) -> None:
    """The runner registry has no aliases (ADR-0028): a retired key is an
    unknown runtime, not something folded onto ``standard`` at save. The
    one-shot ``hal0 runner-images migrate`` converts stored profiles instead."""
    cat = ProfileCatalog()
    with pytest.raises(UnprocessableEntity) as exc:
        cat.create("legacy", ProfileConfig(flags="", runner=retired))
    assert exc.value.code == "profiles.unknown_runner"


def test_create_profile_unknown_runner_rejected(tmp_hal0_home: str) -> None:
    cat = ProfileCatalog()
    with pytest.raises(UnprocessableEntity) as exc:
        cat.create("bad", ProfileConfig(flags="", runner="nope"))
    assert exc.value.code == "profiles.unknown_runner"


def test_profile_without_runner_unchanged(tmp_hal0_home: str) -> None:
    cat = ProfileCatalog()
    prof = cat.create("plain", ProfileConfig(flags="-fa on"))
    assert prof.runner is None


def test_create_with_blank_runner_is_auto(tmp_hal0_home: str) -> None:
    """'' means "no runtime", never a key to look up — so a create carrying the
    drawer's Auto option lands as Auto instead of 422'ing on an empty key."""
    prof = ProfileCatalog().create("blankrun", ProfileConfig(flags="", runner=""))
    assert prof.runner is None


# ── update runner semantics: None = unchanged, "" = clear (#2186),
#    unchanged value grandfathered (#2183) ─────────────────────────────────


def _stage_runner_profile(name: str, runner: str) -> None:
    """Put a profile carrying *runner* on disk, bypassing the write screen.

    The only way to stage a stored key the registry no longer offers — the
    #2183 case, where a runner was removed/renamed by a build (or the box was
    downgraded) after the profile was authored.
    """
    cfg = load_profiles_config()
    cfg.profile[name] = ProfileConfig(flags="-fa on", runner=runner, intent="before")
    save_profiles_config(cfg)


def test_update_without_runner_leaves_the_stored_runtime(tmp_hal0_home: str) -> None:
    cat = ProfileCatalog()
    cat.create("keeper", ProfileConfig(flags="", runner="cuda"))
    assert cat.update("keeper", ProfilePatch(intent="renamed")).runner == "cuda"


def test_update_blank_runner_clears_to_auto(tmp_hal0_home: str) -> None:
    """#2186 — '' is the clear sentinel; None still means leave-unchanged."""
    cat = ProfileCatalog()
    cat.create("clearme", ProfileConfig(flags="", runner="cuda"))
    updated = cat.update("clearme", ProfilePatch(runner=""))
    assert updated.runner is None
    assert cat.resolve("clearme").runner is None


def test_update_changing_the_runner_still_screens(tmp_hal0_home: str) -> None:
    cat = ProfileCatalog()
    cat.create("switcher", ProfileConfig(flags="", runner="cuda"))
    with pytest.raises(UnprocessableEntity) as exc:
        cat.update("switcher", ProfilePatch(runner="nope"))
    assert exc.value.code == "profiles.unknown_runner"


def test_update_changing_to_a_retired_runner_key_is_rejected(tmp_hal0_home: str) -> None:
    cat = ProfileCatalog()
    cat.create("folder", ProfileConfig(flags=""))
    with pytest.raises(UnprocessableEntity) as exc:
        cat.update("folder", ProfilePatch(runner="vulkanfpx"))
    assert exc.value.code == "profiles.unknown_runner"


def test_update_resubmitting_an_out_of_vocab_runner_is_grandfathered(
    tmp_hal0_home: str,
) -> None:
    """#2183 — the drawer resends the stored runner verbatim on every save, so
    screening an UNCHANGED value made a profile whose runtime left the registry
    un-editable in every field."""
    _stage_runner_profile("ghosted", "ghost-runner")
    updated = ProfileCatalog().update(
        "ghosted", ProfilePatch(intent="renamed", runner="ghost-runner")
    )
    assert updated.intent == "renamed"
    assert updated.runner == "ghost-runner"


def test_update_cannot_swap_one_unknown_runner_for_another(tmp_hal0_home: str) -> None:
    """The exemption is byte-identical resubmission only, not an amnesty."""
    _stage_runner_profile("ghosted", "ghost-runner")
    with pytest.raises(UnprocessableEntity) as exc:
        ProfileCatalog().update("ghosted", ProfilePatch(runner="other-ghost"))
    assert exc.value.code == "profiles.unknown_runner"


def test_update_can_clear_an_out_of_vocab_runner(tmp_hal0_home: str) -> None:
    """Dropping a runtime is always safe, so the clear sentinel bypasses the
    screen — that is the in-product way off a stale key."""
    _stage_runner_profile("ghosted", "ghost-runner")
    assert ProfileCatalog().update("ghosted", ProfilePatch(runner="")).runner is None


# ── runtime_family precedence: runner > seed slug ───────────────────────────


def test_runtime_family_prefers_runner_key() -> None:
    profile = ProfileConfig(flags="", runner="kokoro")
    assert _runtime_family("my-custom", profile) == "kokoro"


def test_runtime_family_runner_beats_the_seed_slug() -> None:
    # a kokoro-NAMED profile pointing at the comfyui runner is comfyui-family
    profile = ProfileConfig(flags="", runner="comfyui")
    assert _runtime_family("kokoro", profile) == "comfyui"


def test_runtime_family_unknown_runner_falls_back_to_the_slug() -> None:
    profile = ProfileConfig(flags="", runner="ghost-runner")
    assert _runtime_family("kokoro", profile) == "kokoro"


def test_runtime_family_no_runner_follows_the_slug() -> None:
    cases = [
        ("flm", "flm"),
        ("qwen3-tts", "qwen3tts"),
        ("kokoro", "kokoro"),
        ("moonshine", "moonshine"),
        ("comfyui", "comfyui"),
        ("anything", "llama-server"),
    ]
    for name, expected in cases:
        assert _runtime_family(name, ProfileConfig(flags="")) == expected


# ── seeds are the only source of the canonical names ────────────────────────


def test_canonical_seeds_resolve_without_adoption(tmp_path: Path) -> None:
    """agent/coding/utility are promoted seeds: they resolve on a fresh install
    with no profiles.toml and nothing is persisted by resolving them."""
    p = tmp_path / "profiles.toml"
    catalog = ProfileCatalog(path=p)

    for name in ("agent", "coding", "utility"):
        resolved = catalog.resolve(name)
        assert resolved.seed is True
    assert not p.exists()


def test_seeds_are_immutable_and_undeletable(tmp_path: Path) -> None:
    catalog = ProfileCatalog(path=tmp_path / "profiles.toml")
    with pytest.raises(Conflict) as exc:
        catalog.update("coding", ProfilePatch(intent="mine now"))
    assert exc.value.code == "profiles.seed_immutable"
    with pytest.raises(Conflict) as exc:
        catalog.delete("agent")
    assert exc.value.code == "profiles.seed_immutable"


def test_demoted_names_are_not_resurrected(tmp_path: Path) -> None:
    """The retired demoted-seed names (``dense``, ``moe``) are plain unknowns now."""
    catalog = ProfileCatalog(path=tmp_path / "profiles.toml")
    for name in ("dense", "moe", "thinking"):
        with pytest.raises(NotFound):
            catalog.resolve(name)


def test_resolve_still_raises_for_an_unknown_name(tmp_path: Path) -> None:
    catalog = ProfileCatalog(path=tmp_path / "profiles.toml")
    with pytest.raises(NotFound):
        catalog.resolve("no-such-profile")


# ── quarantined entries (#2653) ───────────────────────────────────────────────


def _write_bad_profile(name: str = "broken") -> Path:
    from hal0.config import paths

    p = paths.profiles_toml()
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(f'[profile.{name}]\nnot_a_field = "surprise"\n', encoding="utf-8")
    return p


def test_list_can_include_quarantined_entries_as_read_only_rows(tmp_hal0_home: str) -> None:
    _write_bad_profile()
    catalog = ProfileCatalog()

    # The default stays valid-only: internal callers use it as the set of
    # names a slot may bind to.
    assert "broken" not in {p.name for p in catalog.list()}

    rows = {r["name"]: r for r in (p.to_dict() for p in catalog.list(include_quarantined=True))}
    assert rows["broken"]["quarantined"] is True
    assert rows["broken"]["error"]
    assert rows["broken"]["seed"] is False
    assert rows["agent"].get("quarantined") is not True


def test_delete_removes_a_quarantined_entry_from_disk(tmp_hal0_home: str) -> None:
    path = _write_bad_profile()
    ProfileCatalog().delete("broken")

    assert "broken" not in path.read_text(encoding="utf-8")
    assert "broken" not in {p.name for p in ProfileCatalog().list(include_quarantined=True)}


def test_delete_of_a_quarantined_entry_still_refuses_when_a_slot_names_it(
    tmp_hal0_home: str,
) -> None:
    _write_bad_profile()
    (_slots_dir(tmp_hal0_home) / "chat.toml").write_text(
        'name = "chat"\nport = 8081\nprofile = "broken"\n', encoding="utf-8"
    )
    with pytest.raises(Conflict) as exc:
        ProfileCatalog().delete("broken")
    assert exc.value.code == "profiles.in_use"


def test_update_of_a_quarantined_entry_names_the_working_remedies(tmp_hal0_home: str) -> None:
    _write_bad_profile()
    with pytest.raises(UnprocessableEntity) as exc:
        ProfileCatalog().update("broken", ProfilePatch(flags="-fa on"))
    assert exc.value.code == "profiles.invalid"
    assert "recreate it under the same name or delete it" in str(exc.value)


def test_resolve_message_names_the_working_remedies(tmp_hal0_home: str) -> None:
    _write_bad_profile()
    with pytest.raises(UnprocessableEntity) as exc:
        ProfileCatalog().resolve("broken")
    assert "recreate it under the same name or delete it" in str(exc.value)
    assert "hal0 profile edit" not in str(exc.value)


def test_recreating_a_quarantined_name_replaces_the_held_entry(tmp_hal0_home: str) -> None:
    path = _write_bad_profile()
    ProfileCatalog().create("broken", ProfileConfig(flags="-fa on"))

    assert "not_a_field" not in path.read_text(encoding="utf-8")
    assert ProfileCatalog().resolve("broken").flags == "-fa on"


def test_patch_reasoning_fields_and_clear(tmp_hal0_home: str) -> None:
    catalog = ProfileCatalog()
    catalog.create("p", ProfileConfig(flags="-fa on"))
    catalog.update("p", ProfilePatch(reasoning_effort="low", reasoning_budget=512))
    got = catalog.resolve("p")
    assert (got.reasoning_effort, got.reasoning_budget) == ("low", 512)

    left = catalog.update("p", ProfilePatch(intent="x"))
    assert (left.reasoning_effort, left.reasoning_budget) == ("low", 512)

    catalog.update("p", ProfilePatch(reasoning_budget=None))
    assert catalog.resolve("p").to_dict()["reasoning_budget"] == -1
    assert catalog.resolve("p").to_dict()["reasoning_effort"] == "low"
