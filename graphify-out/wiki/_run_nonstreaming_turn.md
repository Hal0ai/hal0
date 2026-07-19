# _run_nonstreaming_turn

> 23 nodes · cohesion 0.14

## Key Concepts

- **_run_nonstreaming_turn()** (10 connections) — `src/hal0/cli/chat_commands.py`
- **chat_commands.py** (9 connections) — `src/hal0/cli/chat_commands.py`
- **chat_command()** (8 connections) — `src/hal0/cli/chat_commands.py`
- **_run_streaming_turn()** (8 connections) — `src/hal0/cli/chat_commands.py`
- **.build_body()** (6 connections) — `src/hal0/cli/chat_commands.py`
- **.finish_assistant_turn()** (5 connections) — `src/hal0/cli/chat_commands.py`
- **_iter_stream_events()** (5 connections) — `src/hal0/cli/chat_commands.py`
- **_post_completions()** (5 connections) — `src/hal0/cli/chat_commands.py`
- **Client** (5 connections)
- **_handle_think_command()** (4 connections) — `src/hal0/cli/chat_commands.py`
- **.clear()** (3 connections) — `src/hal0/cli/chat_commands.py`
- **.set_think()** (3 connections) — `src/hal0/cli/chat_commands.py`
- **Any** (3 connections)
- **``hal0 chat`` — terminal REPL over the local ``/v1/chat/completions`` (§21.14).** (1 connections) — `src/hal0/cli/chat_commands.py`
- **Split reasoning out of a completed reply; fold only the stripped         visible** (1 connections) — `src/hal0/cli/chat_commands.py`
- **One non-streaming ``POST /v1/chat/completions`` call → parsed JSON.** (1 connections) — `src/hal0/cli/chat_commands.py`
- **Consume one OpenAI-style SSE chat-completion stream.      Mirrors the ``data: ..** (1 connections) — `src/hal0/cli/chat_commands.py`
- **Stream one turn's tokens live, then fold the stripped reply into history.** (1 connections) — `src/hal0/cli/chat_commands.py`
- **``--no-stream``: wait for the whole completion, then print it at once.** (1 connections) — `src/hal0/cli/chat_commands.py`
- **Terminal chat REPL over ``/v1/chat/completions``.      In-REPL commands:      \b** (1 connections) — `src/hal0/cli/chat_commands.py`
- **``/clear`` — drop the whole conversation history.** (1 connections) — `src/hal0/cli/chat_commands.py`
- **Apply ``/think <mode>``; returns False on an unrecognised mode.** (1 connections) — `src/hal0/cli/chat_commands.py`
- **Append the user's turn to history and build the request body.          ``think_m** (1 connections) — `src/hal0/cli/chat_commands.py`

## Relationships

- [ChatSession](ChatSession.md) (9 shared connections)
- [run_tool_loop](run_tool_loop.md) (3 shared connections)
- [Typer](Typer.md) (1 shared connections)
- [apply_thinking_policy](apply_thinking_policy.md) (1 shared connections)

## Source Files

- `src/hal0/cli/chat_commands.py`

## Audit Trail

- EXTRACTED: 80 (95%)
- INFERRED: 4 (5%)
- AMBIGUOUS: 0 (0%)

---

*Part of the graphify knowledge wiki. See [index](index.md) to navigate.*