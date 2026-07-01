"""Tests for :func:`hal0.normalize.messages.normalize_system_messages`.

Covers the three constraints the Qwen3.6-35B-A3B chat template enforces
(``System message must be at the beginning``): single-system position,
absence of mid-array systems, and absence of stacked systems. Each of these
was a 500 from llama-server before normalising.
"""

from hal0.normalize.messages import normalize_system_messages

# ── trivial / no-op paths ──────────────────────────────────────────────────


def test_non_list_passthrough():
    # Non-list inputs aren't message arrays at all — return as-is so callers
    # can match ``is`` to detect the no-op.
    assert normalize_system_messages(None) is None
    assert normalize_system_messages({"role": "user"}) == {"role": "user"}
    assert normalize_system_messages("not a list") == "not a list"


def test_empty_list_passthrough():
    assert normalize_system_messages([]) == []
    empty = []
    assert normalize_system_messages(empty) is empty  # no copy on no-op


def test_no_system_messages_no_allocation():
    # Hot path: the SPA's history filter strips system messages, so user→agent
    # requests must not pay a copy.
    msgs = [{"role": "user", "content": "hi"}, {"role": "assistant", "content": "hello"}]
    out = normalize_system_messages(msgs)
    assert out is msgs
    assert out == msgs


# ── the original repro: system positioned mid-array ───────────────────────


def test_system_after_user_is_hoisted():
    # Was HTTP 500 on the live dashboard before the fix.
    msgs = [{"role": "user", "content": "Hi"}, {"role": "system", "content": "ops"}]
    out = normalize_system_messages(msgs)
    assert out != msgs  # new list
    assert out[0] == {"role": "system", "content": "ops"}
    assert out[1] == {"role": "user", "content": "Hi"}


def test_already_canonical_no_copy():
    msgs = [{"role": "system", "content": "ops"}, {"role": "user", "content": "Hi"}]
    out = normalize_system_messages(msgs)
    assert out == msgs
    # The function allocates a new list whenever ANY system was found —
    # document that contract rather than over-engineer an identity-preserving
    # no-op for an edge case that costs O(1) anyway.


# ── the multi-system case (collapse path) ─────────────────────────────────


def test_multi_system_collapses_into_single():
    # Stacked systems also 500 the Qwen3 template (verified live). The
    # collapse emits a single leading system whose content joins the inputs
    # with blank lines — matching the OpenAI / Anthropic convention.
    msgs = [
        {"role": "user", "content": "u1"},
        {"role": "system", "content": "s1"},
        {"role": "assistant", "content": "a1"},
        {"role": "system", "content": "s2"},
    ]
    out = normalize_system_messages(msgs)
    assert len([m for m in out if isinstance(m, dict) and m.get("role") == "system"]) == 1
    assert out[0] == {"role": "system", "content": "s1\n\ns2"}
    assert out[1] == {"role": "user", "content": "u1"}
    assert out[2] == {"role": "assistant", "content": "a1"}


def test_three_or_more_systems_still_collapse():
    msgs = [
        {"role": "system", "content": "a"},
        {"role": "system", "content": "b"},
        {"role": "system", "content": "c"},
        {"role": "user", "content": "hi"},
    ]
    out = normalize_system_messages(msgs)
    assert len(out) == 2  # one system + one user
    assert out[0] == {"role": "system", "content": "a\n\nb\n\nc"}
    assert out[1] == {"role": "user", "content": "hi"}


# ── robustness ────────────────────────────────────────────────────────────


def test_junk_entries_preserved_as_others():
    # Non-dict entries and entries with role=system but missing 'content'
    # still trigger hoist; content defaults to "" so join() never raises.
    msgs = [
        {"role": "system", "content": "x"},
        "string entry",
        42,
        {"role": "user", "content": "u"},
    ]
    out = normalize_system_messages(msgs)
    assert out[0] == {"role": "system", "content": "x"}
    assert out[1:] == ["string entry", 42, {"role": "user", "content": "u"}]


def test_system_entry_without_content_key_still_hoists():
    # role='system' with no ``content`` key still hoists; content defaults
    # to "" so join() doesn't raise on the multi-system path.
    msgs = [
        {"role": "system", "content": "first"},
        {"role": "user", "content": "u"},
        {"role": "system"},  # no content
    ]
    out = normalize_system_messages(msgs)
    # The two systems collapse into one; missing content contributes "".
    assert out[0] == {"role": "system", "content": "first\n\n"}
    assert out[1] == {"role": "user", "content": "u"}


def test_user_only_passthrough_with_extras():
    # Non-message body extras (tool calls, tool results, names) on the
    # user/assistant entries should survive intact.
    msgs = [
        {
            "role": "assistant",
            "content": "",
            "tool_calls": [{"id": "1", "type": "function", "function": {"name": "n"}}],
        },
        {"role": "tool", "tool_call_id": "1", "content": "{}"},
        {"role": "user", "content": "u"},
    ]
    out = normalize_system_messages(msgs)
    assert out == msgs  # no system → exact pass-through
