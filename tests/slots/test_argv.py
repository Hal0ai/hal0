"""Unit + golden-parity tests for hal0.slots.argv.normalize_argv.

The golden fixture is the *live* ``agent`` slot's resolved command (captured
from ``slot_list`` on CT105), which carries the real duplicate-flag soup
(``-b`` x2, ``-ctk`` x2, ``--jinja`` x2, ``--threads`` long + ``-t`` short).
The parity property: normalising it preserves every flag's effective (last)
value and drops only the earlier duplicates — so the slot launches identically.
"""

from __future__ import annotations

import pytest

from hal0.errors import BadRequest
from hal0.slots.argv import (
    MANAGED_ARGS_DENYLIST,
    SLOT_HARDWARE_FLAGS,
    _deny_slot_hardware_flags,
    normalize_argv,
    resolve_argv,
)

# The flag portion of the live `agent` slot resolved_command (post `--port`).
# Verbatim from `mcp__hal0-admin__slot_list` — includes the profile MTP bundle
# AND the slot extra_args repeating many of the same flags.
AGENT_LIVE = [
    "--host",
    "0.0.0.0",
    "--port",
    "8101",
    "--model",
    "qwen3.6-35b-a3b-crown-halo-mtp-dynamic",
    "--alias",
    "qwen3.6-35b-a3b-crown-halo-mtp-dynamic",
    "--ctx-size",
    "164000",
    "-fa",
    "on",
    "-ctk",
    "q4_0",
    "-ctv",
    "q4_0",
    "-b",
    "8192",
    "-ub",
    "2048",
    "--parallel",
    "1",
    "--threads",
    "16",
    "--threads-batch",
    "32",
    "--no-mmap",
    "--poll",
    "100",
    "--poll-batch",
    "1",
    "--jinja",
    "--spec-type",
    "draft-mtp",
    "--spec-draft-device",
    "ROCm0",
    "--spec-draft-ngl",
    "all",
    "--spec-draft-n-max",
    "4",
    "--spec-draft-n-min",
    "0",
    "--spec-draft-p-min",
    "0.0",
    "--spec-draft-p-split",
    "0.10",
    "--spec-draft-type-k",
    "f16",
    "--spec-draft-type-v",
    "f16",
    "--spec-draft-threads",
    "16",
    "--spec-draft-threads-batch",
    "32",
    "--spec-draft-poll",
    "1",
    "--spec-draft-poll-batch",
    "1",
    # ── slot [server].extra_args begins here — repeats much of the above ──
    "-ngl",
    "999",
    "-dev",
    "ROCm0",
    "-sm",
    "row",
    "-b",
    "8192",
    "-ub",
    "2048",
    "-t",
    "16",
    "-tb",
    "32",
    "-ctk",
    "q4_0",
    "-ctv",
    "q4_0",
    "--spec-draft-device",
    "ROCm0",
    "--spec-draft-ngl",
    "all",
    "--spec-draft-type-k",
    "f16",
    "--spec-draft-type-v",
    "f16",
    "--spec-draft-threads",
    "16",
    "--spec-draft-threads-batch",
    "32",
    "--spec-draft-n-max",
    "4",
    "--spec-draft-n-min",
    "0",
    "--spec-draft-p-min",
    "0.0",
    "--spec-draft-p-split",
    "0.10",
    "--poll",
    "100",
    "--poll-batch",
    "1",
    "--spec-draft-poll",
    "1",
    "--spec-draft-poll-batch",
    "1",
    "--temp",
    "0",
    "--min-p",
    "0.0",
    "--top-p",
    "0.9",
    "--top-k",
    "20",
    "--repeat-penalty",
    "1.0",
    "--seed",
    "123",
    "--cache-ram",
    "0",
    "--parallel",
    "1",
    "--image-min-tokens",
    "1024",
    "--metrics",
    "--jinja",
    "--reasoning-format",
    "deepseek",
    "--reasoning-budget",
    "0",
]


def _value_after(tokens: list[str], flag: str) -> str | None:
    """Last value following ``flag`` in ``tokens`` (the effective value)."""
    val = None
    for i, t in enumerate(tokens):
        if t == flag and i + 1 < len(tokens):
            val = tokens[i + 1]
    return val


# ── golden parity on the live agent slot ──────────────────────────────────────


def test_agent_live_dedups_but_preserves_effective_values() -> None:
    res = normalize_argv(AGENT_LIVE)
    out = res.argv

    # 1) duplicates were actually removed
    assert res.removed > 0
    assert len(out) < len(AGENT_LIVE)

    # 2) each dedupable flag now appears exactly once
    for flag in ("-b", "-ub", "-ctk", "-ctv", "--jinja", "--parallel", "--poll"):
        assert out.count(flag) <= 1, f"{flag} still duplicated: {out.count(flag)}"

    # 3) effective (last) value preserved for representative scalar flags
    assert _value_after(out, "-b") == "8192"
    assert _value_after(out, "-ctk") == "q4_0"
    assert _value_after(out, "--spec-draft-type-k") == "f16"
    assert _value_after(out, "--reasoning-budget") == "0"
    assert _value_after(out, "--temp") == "0"

    # 4) structural prefix intact
    assert out[:4] == ["--host", "0.0.0.0", "--port", "8101"]
    assert _value_after(out, "--model") == "qwen3.6-35b-a3b-crown-halo-mtp-dynamic"
    assert _value_after(out, "--ctx-size") == "164000"

    # 5) bool flag survives exactly once
    assert out.count("--jinja") == 1
    assert out.count("--metrics") == 1


def test_alias_dedups_short_against_long() -> None:
    # --threads (long, from profile) and -t (short, from extra_args) share a key.
    res = normalize_argv(["--threads", "16", "-t", "16"])
    assert res.argv == ["-t", "16"]  # last occurrence wins, short spelling kept
    assert res.removed == 1


def test_normalize_is_idempotent() -> None:
    once = normalize_argv(AGENT_LIVE).argv
    twice = normalize_argv(once)
    assert twice.argv == once
    assert twice.removed == 0


# ── focused unit cases ────────────────────────────────────────────────────────


def test_last_value_wins_on_conflict() -> None:
    res = normalize_argv(["-b", "512", "-b", "8192"])
    assert res.argv == ["-b", "8192"]
    assert res.removed == 1


def test_bool_flags_collapse_to_one() -> None:
    res = normalize_argv(["--jinja", "--metrics", "--jinja"])
    assert res.argv == ["--metrics", "--jinja"]  # --jinja kept at its last spot
    assert res.removed == 1


def test_append_flags_are_never_deduped() -> None:
    res = normalize_argv(["--lora", "a.gguf", "--lora", "b.gguf"])
    assert res.argv == ["--lora", "a.gguf", "--lora", "b.gguf"]
    assert res.removed == 0


def test_negative_number_is_a_value_not_a_flag() -> None:
    res = normalize_argv(["-ngl", "-1"])
    assert res.argv == ["-ngl", "-1"]
    assert _value_after(res.argv, "-ngl") == "-1"


def test_bare_positionals_preserved() -> None:
    res = normalize_argv(["--model", "/m.gguf", "extra-positional"])
    assert "extra-positional" in res.argv


def test_empty_is_noop() -> None:
    res = normalize_argv([])
    assert res.argv == []
    assert res.removed == 0
    assert res.winners == {}


# ── resolve_argv: provenance over labelled segments ───────────────────────────


def test_resolve_argv_attributes_winning_source() -> None:
    res = resolve_argv(
        [
            ("base", ["--host", "0.0.0.0"]),
            ("profile", ["-b", "512", "--jinja"]),
            ("extra_args", ["-b", "8192"]),  # overrides the profile's -b
        ]
    )
    assert res.argv == ["--host", "0.0.0.0", "--jinja", "-b", "8192"]
    prov = {p.flag: p for p in res.provenance}
    # -b was set last by extra_args -> that segment is credited, value preserved
    assert prov["-b"].source == "extra_args"
    assert prov["-b"].value == "8192"
    # --jinja only came from the profile
    assert prov["--jinja"].source == "profile"
    assert prov["--jinja"].value is None
    # --host from base
    assert prov["--host"].source == "base"
    assert res.removed == 1


def test_resolve_argv_equivalent_argv_to_normalize() -> None:
    # Same tokens, segmented vs flat, produce the same deduped argv.
    flat = ["--host", "0.0.0.0", "-b", "512", "--jinja", "-b", "8192"]
    seg = resolve_argv([("base", flat[:2]), ("profile", flat[2:5]), ("extra_args", flat[5:])])
    assert seg.argv == normalize_argv(flat).argv


def test_resolve_argv_omits_append_flags_from_provenance() -> None:
    res = resolve_argv([("profile", ["--lora", "a", "--lora", "b"]), ("extra_args", ["--jinja"])])
    flags = {p.flag for p in res.provenance}
    assert "--lora" not in flags  # append flags aren't deduped -> no single "winner"
    assert "--jinja" in flags


# ── §21.7: managed-args denylist ──────────────────────────────────────────────
#
# ``[server].extra_args`` is free-form and is the LAST segment ``container.py``
# passes to ``resolve_argv`` — these pin that it can't smuggle a flag hal0
# itself owns (model path, listen address/port, context size, GPU-layer
# override, advertised alias) past the merge.

_BASE_SEGMENTS = [
    ("base", ["--host", "0.0.0.0", "--port", "8101", "--model", "/models/m.gguf"]),
    ("profile", ["-fa", "on", "--threads", "16"]),
]


@pytest.mark.parametrize(
    "denied_tokens",
    [
        ["--model", "/etc/passwd"],
        ["--port", "9999"],
        ["--host", "0.0.0.0"],
        ["--ctx-size", "8192"],
        ["-c", "8192"],
        ["-ngl", "0"],
        ["--n-gpu-layers", "0"],
        ["--alias", "not-the-real-model"],
    ],
)
def test_resolve_argv_rejects_managed_flag_in_extra_args(denied_tokens: list[str]) -> None:
    segments = [*_BASE_SEGMENTS, ("extra_args", ["--flash-attn", "on", *denied_tokens])]
    with pytest.raises(BadRequest) as exc_info:
        resolve_argv(segments)
    assert exc_info.value.code == "slot.managed_arg_denied"
    assert denied_tokens[0] in exc_info.value.message


def test_resolve_argv_rejects_multiple_managed_flags_in_one_extra_args() -> None:
    segments = [*_BASE_SEGMENTS, ("extra_args", ["--model", "/tmp/evil.gguf", "--port", "1"])]
    with pytest.raises(BadRequest) as exc_info:
        resolve_argv(segments)
    assert exc_info.value.details["flags"] == ["--model", "--port"]


def test_resolve_argv_allows_clean_extra_args() -> None:
    """A slot's real extra_args (bench tuning, no managed flags) passes through."""
    segments = [*_BASE_SEGMENTS, ("extra_args", ["--flash-attn", "on", "--threads", "8"])]
    res = resolve_argv(segments)
    # extra_args wins the -fa/--flash-attn and --threads collisions (last-wins,
    # winning spelling kept) — no managed flag present, so nothing is raised.
    assert res.argv == [
        "--host",
        "0.0.0.0",
        "--port",
        "8101",
        "--model",
        "/models/m.gguf",
        "--flash-attn",
        "on",
        "--threads",
        "8",
    ]


def test_resolve_argv_only_screens_untrusted_labels() -> None:
    """A managed flag in a non-``extra_args`` (trusted) segment is not screened.

    ``base``/``profile``/``model_defaults``/``slot_overrides`` are hal0-computed,
    not caller-supplied — only labels in ``UNTRUSTED_SEGMENT_LABELS`` are
    screened, so legitimate managed-layer flags (e.g. ``-ngl`` set from
    ``[model].n_gpu_layers`` in the ``slot_overrides`` segment) never trip it.
    """
    segments = [*_BASE_SEGMENTS, ("slot_overrides", ["-ngl", "40"])]
    res = resolve_argv(segments)  # must not raise
    assert "-ngl" in res.argv


def test_resolve_argv_screens_model_extra_args_segment() -> None:
    """A model's free-form ``defaults.extra_args`` (the ``model_extra_args``
    segment) is caller-supplied, so a managed flag smuggled through it must be
    rejected at launch — the gap where an ``extra_args`` with ``--port`` reached
    the container only because the model-defaults segment wasn't screened."""
    segments = [*_BASE_SEGMENTS, ("model_extra_args", ["--flash-attn", "on", "--port", "9999"])]
    with pytest.raises(BadRequest) as exc_info:
        resolve_argv(segments)
    assert exc_info.value.code == "slot.managed_arg_denied"
    assert "--port" in exc_info.value.message


def test_resolve_argv_does_not_screen_trusted_model_defaults_ngl() -> None:
    """The ``-ngl`` hal0 computes from the schema field ``defaults.n_gpu_layers``
    rides the trusted ``model_defaults`` segment and must NOT be rejected, even
    though ``--n-gpu-layers`` is a managed flag."""
    segments = [*_BASE_SEGMENTS, ("model_defaults", ["-ngl", "20"])]
    res = resolve_argv(segments)  # must not raise
    assert "-ngl" in res.argv


def test_managed_args_denylist_covers_expected_flags() -> None:
    expected = frozenset({"--model", "--ctx-size", "--host", "--port", "--n-gpu-layers", "--alias"})
    assert expected == MANAGED_ARGS_DENYLIST


def test_slot_hardware_flags_covers_grid_flags_both_spellings() -> None:
    """spec-hw-slot-ownership §5 partition set: the grid-owned hardware flags
    (device/-dev, NGL/-ngl, threads/--threads) in BOTH long and short form."""
    expected = frozenset({"--n-gpu-layers", "-ngl", "--device", "-dev", "--threads", "-t"})
    assert expected == SLOT_HARDWARE_FLAGS


# ── slot-hardware partition guard (spec-hw-slot-ownership §5) ─────────────────


@pytest.mark.parametrize(
    "denied_tokens",
    [
        ["-ngl", "99"],
        ["--n-gpu-layers", "99"],
        ["-dev", "CUDA0"],
        ["--device", "ROCm0"],
        ["-t", "8"],
        ["--threads", "8"],
    ],
)
def test_deny_slot_hardware_flags_rejects_both_spellings(denied_tokens: list[str]) -> None:
    """A model/profile freeform-flag save that carries a grid-owned hardware flag
    (either spelling) is hard-rejected with the "belongs on the slot" envelope."""
    tokens = ["--flash-attn", "on", *denied_tokens]
    with pytest.raises(BadRequest) as exc_info:
        _deny_slot_hardware_flags(tokens, segment="model defaults.extra_args")
    assert exc_info.value.code == "slot.hardware_flag_denied"
    assert denied_tokens[0] in exc_info.value.message
    assert "slot" in exc_info.value.message.lower()
    assert exc_info.value.details["flags"] == [denied_tokens[0]]


def test_deny_slot_hardware_flags_reports_every_offender() -> None:
    tokens = ["-ngl", "40", "--threads", "8", "-fa", "on"]
    with pytest.raises(BadRequest) as exc_info:
        _deny_slot_hardware_flags(tokens, segment="profile flags")
    assert exc_info.value.details["flags"] == ["-ngl", "--threads"]


def test_deny_slot_hardware_flags_allows_clean_tune() -> None:
    """A real device-agnostic tune (batch/flash-attn/KV-quant/rope) has no
    hardware flags, so the guard is a no-op."""
    tokens = ["-b", "2048", "-ub", "512", "-fa", "on", "-ctk", "q8_0", "--no-mmap"]
    _deny_slot_hardware_flags(tokens, segment="model defaults.extra_args")  # must not raise
