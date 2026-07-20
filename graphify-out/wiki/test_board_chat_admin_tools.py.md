# test_board_chat_admin_tools.py

> 32 nodes · cohesion 0.12

## Key Concepts

- **test_board_chat_admin_tools.py** (18 connections) — `tests/board/test_board_chat_admin_tools.py`
- **PersonaApproval** (15 connections) — `src/hal0/agents/personas.py`
- **_fake_request()** (15 connections) — `tests/board/test_board_chat_admin_tools.py`
- **_brain_persona_root()** (12 connections) — `tests/board/test_board_chat_admin_tools.py`
- **Path** (8 connections)
- **test_persona_loosening_skips_approval_from_sidebar()** (8 connections) — `tests/board/test_board_chat_admin_tools.py`
- **test_persona_cannot_loosen_destructive_floor()** (6 connections) — `tests/board/test_board_chat_admin_tools.py`
- **test_brain_policy_loads_from_persona_toml()** (5 connections) — `tests/board/test_board_chat_admin_tools.py`
- **test_dispatch_tool_refuses_disallowed_local_tool()** (5 connections) — `tests/board/test_board_chat_admin_tools.py`
- **test_dispatch_tool_routes_admin_names_through_mcp_core()** (5 connections) — `tests/board/test_board_chat_admin_tools.py`
- **test_surfaced_schemas_filtered_by_tools_allowed()** (5 connections) — `tests/board/test_board_chat_admin_tools.py`
- **test_gated_tool_returns_pending_approval_from_chat_path()** (4 connections) — `tests/board/test_board_chat_admin_tools.py`
- **test_admin_dispatch_degrades_without_approval_queue()** (3 connections) — `tests/board/test_board_chat_admin_tools.py`
- **test_brain_policy_none_when_persona_missing()** (3 connections) — `tests/board/test_board_chat_admin_tools.py`
- **test_unknown_tool_still_errors()** (3 connections) — `tests/board/test_board_chat_admin_tools.py`
- **Any** (2 connections)
- **MonkeyPatch** (2 connections)
- **test_admin_schemas_do_not_collide_with_local_tools()** (2 connections) — `tests/board/test_board_chat_admin_tools.py`
- **test_admin_schemas_require_path_args()** (2 connections) — `tests/board/test_board_chat_admin_tools.py`
- **Subset of the ``[persona.approval]`` TOML table.      ``default_policy`` is one** (1 connections) — `src/hal0/agents/personas.py`
- **The sidebar Brain's admin-MCP tool surface (board_chat ↔ hal0.mcp.admin).  The s** (1 connections) — `tests/board/test_board_chat_admin_tools.py`
- **An admin-catalog name that no local resolver claims lands in     admin.dispatch** (1 connections) — `tests/board/test_board_chat_admin_tools.py`
- **model_pull from the sidebar enqueues on the SAME ApprovalQueue the     /mcp/admi** (1 connections) — `tests/board/test_board_chat_admin_tools.py`
- **No lifespan wiring (approval_queue=None) → typed error, no crash.** (1 connections) — `tests/board/test_board_chat_admin_tools.py`
- **tools_allowed narrows BOTH the local and admin schema lists.** (1 connections) — `tests/board/test_board_chat_admin_tools.py`
- *... and 7 more nodes in this community*

## Relationships

- [Persona](Persona.md) (7 shared connections)
- [ApprovalQueue](ApprovalQueue.md) (6 shared connections)
- [test_board_chat_tool_use_e2e.py](test_board_chat_tool_use_e2e.py.md) (3 shared connections)
- [test_brain_read_only.py](test_brain_read_only.py.md) (2 shared connections)
- [types.py](types.py.md) (1 shared connections)
- [BrainChatConfig](BrainChatConfig.md) (1 shared connections)

## Source Files

- `src/hal0/agents/personas.py`
- `tests/board/test_board_chat_admin_tools.py`

## Audit Trail

- EXTRACTED: 114 (84%)
- INFERRED: 22 (16%)
- AMBIGUOUS: 0 (0%)

---

*Part of the graphify knowledge wiki. See [index](index.md) to navigate.*