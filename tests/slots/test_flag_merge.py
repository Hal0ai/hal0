"""Unit tests for hal0.slots.flag_merge.merge_flags.

Covers the Phase 1 A3 contract:
  * Empty / None inputs collapse to ''.
  * One-sided inputs round-trip trimmed.
  * Slot flag wins over a colliding model-default ``--flag value`` pair.
  * Append-list flags (``--lora`` / ``--draft-model`` / ``--override-kv``)
    skip dedup so both occurrences end up in the merged string.
  * Malformed (unbalanced-quote) input falls back to a dumb concat and
    emits a structured warning on the module logger.
"""

from __future__ import annotations

import logging

import pytest

from hal0.slots.flag_merge import merge_flags

# ─── Empty inputs ─────────────────────────────────────────────────────────────


def test_none_and_none_returns_empty() -> None:
    assert merge_flags(None, None) == ""


def test_empty_strings_return_empty() -> None:
    assert merge_flags("", "") == ""
    assert merge_flags("   ", "\t") == ""


def test_none_model_only_slot() -> None:
    assert merge_flags(None, "--threads 4") == "--threads 4"


def test_only_model_none_slot() -> None:
    assert merge_flags("--threads 4", None) == "--threads 4"


def test_one_sided_input_is_trimmed() -> None:
    assert merge_flags("  --threads 4  ", None) == "--threads 4"
    assert merge_flags(None, "  --threads 4  ") == "--threads 4"


# ─── Dedup: slot flag wins ────────────────────────────────────────────────────


def test_slot_overrides_model_threads() -> None:
    """Slot's --threads 8 replaces model's --threads 4; --rope yarn survives."""
    out = merge_flags("--threads 4 --rope yarn", "--threads 8")
    assert out == "--rope yarn --threads 8"


def test_slot_flag_without_value_strips_model_flag_with_value() -> None:
    """A slot --flash-attn (no value) still strips the model's --flash-attn on."""
    out = merge_flags("--flash-attn on --threads 4", "--flash-attn")
    # Model's --flash-attn value pair is gone; slot's --flash-attn stays.
    assert "--flash-attn" in out
    assert "on" not in out.split()
    assert "--threads" in out and "4" in out


def test_slot_keeps_order_after_model_remainder() -> None:
    """Model first (cleaned), slot appended — no surprises in ordering."""
    out = merge_flags("--a 1 --b 2 --c 3", "--b 99")
    parts = out.split()
    # --a and --c remain in original order; --b ends up at the end.
    assert parts.index("--a") < parts.index("--c") < parts.index("--b")
    assert parts[-2:] == ["--b", "99"]


# ─── Append-list flags ────────────────────────────────────────────────────────


def test_lora_is_appended_not_deduped() -> None:
    """Both --lora occurrences survive; model's lora comes first."""
    out = merge_flags("--lora a.gguf", "--lora b.gguf")
    parts = out.split()
    assert parts.count("--lora") == 2
    assert parts.index("a.gguf") < parts.index("b.gguf")


def test_draft_model_is_appended_not_deduped() -> None:
    out = merge_flags("--draft-model d1.gguf", "--draft-model d2.gguf")
    assert out.split().count("--draft-model") == 2


def test_override_kv_is_appended_not_deduped() -> None:
    out = merge_flags(
        "--override-kv tokenizer.ggml.bos_token_id=int:1",
        "--override-kv llama.rope.scaling.type=str:linear",
    )
    assert out.split().count("--override-kv") == 2


# ─── Malformed input ──────────────────────────────────────────────────────────


def test_unbalanced_quote_falls_back_to_dumb_concat(
    caplog: pytest.LogCaptureFixture,
) -> None:
    bad_model = '--threads 4 --prompt "oops'
    slot = "--threads 8"
    caplog.set_level(logging.WARNING, logger="hal0.slots.argv")
    out = merge_flags(bad_model, slot)
    # Dumb concat preserves both sides verbatim (trimmed).
    assert bad_model.strip() in out
    assert slot in out
    # Structured warning captured.
    warn_records = [
        r for r in caplog.records if r.levelno == logging.WARNING and "flag_merge" in r.message
    ]
    assert warn_records, "expected a structured warning for malformed input"
    rec = warn_records[0]
    assert getattr(rec, "event", "").startswith("flag_merge.malformed_input")


def test_unbalanced_quote_on_one_side_only(
    caplog: pytest.LogCaptureFixture,
) -> None:
    """Malformed input on only the model side still warns + returns concat."""
    caplog.set_level(logging.WARNING, logger="hal0.slots.argv")
    out = merge_flags('--prompt "oops', None)
    # Falls back to the trimmed model defaults via the dumb-concat path.
    assert "--prompt" in out
    warn_records = [r for r in caplog.records if r.levelno == logging.WARNING]
    assert warn_records


# ─── Edge cases ───────────────────────────────────────────────────────────────


def test_quoted_value_survives_round_trip() -> None:
    """A quoted value (with internal spaces) keeps its grouping in the output.

    Note: the merged string is a *space-joined* token sequence; the launcher
    re-tokenises it with shlex.split, so what matters is that the value
    survives as one token after re-tokenisation.
    """
    import shlex as _shlex

    out = merge_flags(None, '--chat-template "you are helpful"')
    tokens = _shlex.split(out)
    assert tokens[0] == "--chat-template"
    assert tokens[1] == "you are helpful"


def test_boolean_flag_without_value() -> None:
    """A bare ``--metrics`` (no value) merges cleanly."""
    out = merge_flags("--metrics", "--verbose")
    parts = out.split()
    assert "--metrics" in parts
    assert "--verbose" in parts


def test_slot_can_remove_value_by_overriding_with_bare_flag() -> None:
    """Slot's bare --threads (no value) wins; model's value pair disappears."""
    out = merge_flags("--threads 4", "--threads")
    assert out.split() == ["--threads"]


# ─── Short-flag handling (folded into hal0.slots.argv) ────────────────────────
# The retired flag_merge tokenizer only understood ``--long`` flags — a
# ``-b 8192`` slot override was mis-parsed as a stray positional and could not
# dedup against the model's ``-b``. The argv-backed merge fixes this.


def test_short_flag_dedups_last_wins() -> None:
    """A short flag on both sides collapses to one, slot value last-wins."""
    assert merge_flags("-b 512", "-b 8192") == "-b 8192"


def test_short_flag_dedups_against_long_alias() -> None:
    """``-b`` and ``--batch-size`` share a canonical key, so the slot's -b
    overrides the model's --batch-size (impossible with the old --only lexer)."""
    assert merge_flags("--batch-size 512", "-b 8192") == "-b 8192"


def test_short_flag_negative_value_is_not_a_flag() -> None:
    """``-ngl -1`` keeps -1 as the value (not mis-read as a flag)."""
    assert merge_flags("", "-ngl -1") == "-ngl -1"


def test_mixed_short_and_long_flags_merge() -> None:
    out = merge_flags("-b 512 --threads 8", "-b 8192 -t 16")
    tokens = out.split()
    # -b collapsed to one, slot's 8192 wins
    assert tokens.count("-b") == 1
    assert tokens[tokens.index("-b") + 1] == "8192"
    # --threads (model, long) vs -t (slot, short) share a key → -t wins
    assert "--threads" not in tokens
    assert tokens[tokens.index("-t") + 1] == "16"
