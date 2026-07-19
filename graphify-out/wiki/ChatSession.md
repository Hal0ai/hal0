# ChatSession

> 23 nodes · cohesion 0.15

## Key Concepts

- **ChatSession** (22 connections) — `src/hal0/cli/chat_commands.py`
- **test_chat_commands.py** (16 connections) — `tests/cli/test_chat_commands.py`
- **_FakeClient** (5 connections) — `tests/cli/test_chat_commands.py`
- **test_run_nonstreaming_turn_strips_reasoning_before_history()** (4 connections) — `tests/cli/test_chat_commands.py`
- **test_run_streaming_turn_accumulates_deltas_and_strips_history()** (4 connections) — `tests/cli/test_chat_commands.py`
- **MonkeyPatch** (3 connections)
- **test_build_body_default_think_mode_leaves_body_untouched()** (3 connections) — `tests/cli/test_chat_commands.py`
- **test_history_never_grows_reasoning_across_multiple_turns()** (3 connections) — `tests/cli/test_chat_commands.py`
- **test_build_body_appends_user_turn_to_history()** (2 connections) — `tests/cli/test_chat_commands.py`
- **test_build_body_think_off_forces_enable_thinking_false()** (2 connections) — `tests/cli/test_chat_commands.py`
- **test_build_body_think_on_forces_enable_thinking_true()** (2 connections) — `tests/cli/test_chat_commands.py`
- **test_finish_assistant_turn_combines_explicit_and_inline_reasoning()** (2 connections) — `tests/cli/test_chat_commands.py`
- **test_finish_assistant_turn_no_reasoning_is_a_no_op_split()** (2 connections) — `tests/cli/test_chat_commands.py`
- **test_finish_assistant_turn_strips_inline_think_tags_from_history()** (2 connections) — `tests/cli/test_chat_commands.py`
- **test_repl_think_and_clear_commands()** (2 connections) — `tests/cli/test_chat_commands.py`
- **test_set_think_accepts_valid_modes_only()** (2 connections) — `tests/cli/test_chat_commands.py`
- **In-memory REPL state: the running ``messages`` history + knobs.      Kept as a p** (1 connections) — `src/hal0/cli/chat_commands.py`
- **Tests for ``hal0 chat`` (§21.14) — the terminal REPL over /v1/chat/completions.** (1 connections) — `tests/cli/test_chat_commands.py`
- **The context-bloat guarantee: after N turns with reasoning on, history     holds** (1 connections) — `tests/cli/test_chat_commands.py`
- **Stand-in for httpx.Client — the turn helpers only need a placeholder     object** (1 connections) — `tests/cli/test_chat_commands.py`
- **think_mode='default' must NOT inject chat_template_kwargs — the     server's own** (1 connections) — `tests/cli/test_chat_commands.py`
- **test_chat_registered_on_the_root_app()** (1 connections) — `tests/cli/test_chat_commands.py`
- **test_repl_exits_cleanly_on_eof()** (1 connections) — `tests/cli/test_chat_commands.py`

## Relationships

- [_run_nonstreaming_turn](_run_nonstreaming_turn.md) (9 shared connections)

## Source Files

- `src/hal0/cli/chat_commands.py`
- `tests/cli/test_chat_commands.py`

## Audit Trail

- EXTRACTED: 59 (71%)
- INFERRED: 24 (29%)
- AMBIGUOUS: 0 (0%)

---

*Part of the graphify knowledge wiki. See [index](index.md) to navigate.*