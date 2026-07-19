# route_to_chat.py

> 16 nodes · cohesion 0.19

## Key Concepts

- **route_to_chat.py** (9 connections) — `src/hal0/omni_router/route_to_chat.py`
- **Any** (8 connections)
- **_build_delegation_messages()** (5 connections) — `src/hal0/omni_router/route_to_chat.py`
- **build_delegation_messages_for_slot()** (5 connections) — `src/hal0/omni_router/route_to_chat.py`
- **_is_chat_slot()** (4 connections) — `src/hal0/omni_router/route_to_chat.py`
- **_system_prompt_of()** (4 connections) — `src/hal0/omni_router/route_to_chat.py`
- **validate_delegation_slots()** (4 connections) — `src/hal0/omni_router/route_to_chat.py`
- **_model_of()** (3 connections) — `src/hal0/omni_router/route_to_chat.py`
- **_slot_by_name()** (3 connections) — `src/hal0/omni_router/route_to_chat.py`
- **``route_to_chat`` dispatch — plan §7.4.  One-shot delegation: the calling LLM ha** (1 connections) — `src/hal0/omni_router/route_to_chat.py`
- **Build the messages array for a delegation chat request.      Per plan §7.4 step** (1 connections) — `src/hal0/omni_router/route_to_chat.py`
- **Build delegation messages from a typed ``LoadedSlot`` view.** (1 connections) — `src/hal0/omni_router/route_to_chat.py`
- **Run route_to_chat guardrails against typed ``LoadedSlot`` views.** (1 connections) — `src/hal0/omni_router/route_to_chat.py`
- **Chat slots are ``type=llm`` and enabled. NPU exclusivity is     enforced separat** (1 connections) — `src/hal0/omni_router/route_to_chat.py`
- **Pull the slot's default model name out of its config dict.      Mirrors :func:`h** (1 connections) — `src/hal0/omni_router/route_to_chat.py`
- **Pull a slot's configured system_prompt — empty string if absent.      The ``syst** (1 connections) — `src/hal0/omni_router/route_to_chat.py`

## Relationships

- [make_slot](make_slot.md) (4 shared connections)
- [tools_by_name](tools_by_name.md) (2 shared connections)

## Source Files

- `src/hal0/omni_router/route_to_chat.py`

## Audit Trail

- EXTRACTED: 50 (96%)
- INFERRED: 2 (4%)
- AMBIGUOUS: 0 (0%)

---

*Part of the graphify knowledge wiki. See [index](index.md) to navigate.*