# test_brain_read_only.py

> 25 nodes · cohesion 0.12

## Key Concepts

- **test_brain_read_only.py** (14 connections) — `tests/brain/test_brain_read_only.py`
- **_fake_request()** (12 connections) — `tests/brain/test_brain_read_only.py`
- **_RecordingClient** (10 connections) — `tests/brain/test_brain_read_only.py`
- **test_read_only_overrides_persona_auto_approve()** (8 connections) — `tests/brain/test_brain_read_only.py`
- **test_read_only_refuses_every_mutation()** (4 connections) — `tests/brain/test_brain_read_only.py`
- **test_read_only_refuses_gated_before_enqueue()** (4 connections) — `tests/brain/test_brain_read_only.py`
- **Any** (3 connections)
- **test_read_only_allows_board_read()** (3 connections) — `tests/brain/test_brain_read_only.py`
- **test_read_only_false_allows_board_mutation()** (3 connections) — `tests/brain/test_brain_read_only.py`
- **test_schema_default_documented_and_enforceable()** (3 connections) — `tests/brain/test_brain_read_only.py`
- **Path** (2 connections)
- **.request_json()** (2 connections) — `tests/brain/test_brain_read_only.py`
- **test_is_read_tool_fails_closed_on_unknown()** (2 connections) — `tests/brain/test_brain_read_only.py`
- **test_read_only_allows_admin_autonomous_read()** (2 connections) — `tests/brain/test_brain_read_only.py`
- **Read-only-default posture for the hal0-brain steward chat (KB-2/3 §4).  The ``[b** (1 connections) — `tests/brain/test_brain_read_only.py`
- **A gated tool is refused by read-only BEFORE it can enqueue — the     approval qu** (1 connections) — `tests/brain/test_brain_read_only.py`
- **Even a persona that auto-approves model_pull cannot beat read-only.** (1 connections) — `tests/brain/test_brain_read_only.py`
- **An unrecognised tool is NOT a read — read-only refuses it.** (1 connections) — `tests/brain/test_brain_read_only.py`
- **A hermes_kanban stand-in: records every request_json call, returns {}.** (1 connections) — `tests/brain/test_brain_read_only.py`
- **A Request stand-in carrying exactly what ``_dispatch_tool`` reads.** (1 connections) — `tests/brain/test_brain_read_only.py`
- **The steward SHIPS read-only (KB-2/3): a bare config refuses mutating     and adm** (1 connections) — `tests/brain/test_brain_read_only.py`
- **An admin autonomous-read (profile_list) is read-safe under read-only.** (1 connections) — `tests/brain/test_brain_read_only.py`
- **.__init__()** (1 connections) — `tests/brain/test_brain_read_only.py`
- **test_is_read_tool_false_for_mutations()** (1 connections) — `tests/brain/test_brain_read_only.py`
- **test_is_read_tool_true_for_reads()** (1 connections) — `tests/brain/test_brain_read_only.py`

## Relationships

- [ApprovalQueue](ApprovalQueue.md) (4 shared connections)
- [Persona](Persona.md) (3 shared connections)
- [BrainChatConfig](BrainChatConfig.md) (2 shared connections)
- [test_board_chat_admin_tools.py](test_board_chat_admin_tools.py.md) (2 shared connections)
- [types.py](types.py.md) (1 shared connections)
- [load_hal0_config](load_hal0_config.md) (1 shared connections)

## Source Files

- `tests/brain/test_brain_read_only.py`

## Audit Trail

- EXTRACTED: 74 (89%)
- INFERRED: 9 (11%)
- AMBIGUOUS: 0 (0%)

---

*Part of the graphify knowledge wiki. See [index](index.md) to navigate.*