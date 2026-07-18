I now have a complete picture. Here is the implementation-ready spec.

---

# P3-brain — Implementation-Ready Spec: hal0-brain as a first-class module

**Repo:** `/home/mint/hal0` @ `rework/descar` (verified). **Scope:** promote hal0-brain out of its Hermes costume into `src/hal0/brain/`, provisioned by API lifespan, on the shared `toolloop/engine.py` (§7.6), with a readiness gate → read-only degrade. **Standing constraint verified against plan §16.1:** the core brain must work with zero Hermes dependency (today `board_chat` already has zero Hermes-runtime dep — it only borrows the persona store path and identity prefix; this spec severs those two ties).

## 0. Verified current state (file:line ground truth)

**The loop / tools / dispatch — `src/hal0/api/routes/board_chat.py` (1327 lines):**
- `_chat_stream` (1110) — the SSE tool loop; `run_board_chat` (1295) entrypoint.
- Constants: `BRAIN_SLOT_MODEL="hal0/brain"` / `PRIMARY_SLOT_MODEL` alias (90-91); `BRAIN_PERSONA_ID="hal0-brain"` (97); `_SYSTEM_PROMPT` (103-159, hardcodes `namespace private:hermes__hal0-brain` at line 108); `_MAX_COMPLETION_TOKENS=4096` (83); approval consts (72-74).
- Profile/policy resolution: `_resolve_profile` (162) and `_brain_tool_policy` (433) both call `hal0.agents.personas.load_persona(BRAIN_PERSONA_ID, root=app.state.brain_persona_root)`.
- Tool surface: `_tool_schemas` (205, board+platform), `_admin_tool_names` (420), `_admin_tool_schemas` (458), `_surfaced_tool_schemas` (493), `_ADMIN_TOOL_EXCLUDES` (399 — note `memory_*` excluded, hardcoded comment `private:hermes__hal0-brain` at 410).
- Dispatch: `_dispatch_tool` (775), `_dispatch_admin_tool` (507, `client_id=BRAIN_PERSONA_ID`), `_dispatch_platform_tool` (646), `_resolve_read_tool` (539), `_resolve_platform_tool` (619), `_resolve_tool` (682), `_is_read_tool` (750).
- LLM: `_resolve_llm` (867) — injected `app.state.board_chat_llm` or a closure POSTing `{self_api_base_url}/v1/chat/completions`.
- Parsing (the toolcall-leak mitigations to move into `toolloop/engine.py`): `_extract_tool_calls` (904), `_parse_text_tool_calls` (976), `_split_thinking` (1068), `_assistant_thinking` (1086), regexes (941-945, 1065).
- Config accessor: `_brain_chat_config` (736).

**Route:** `src/hal0/api/routes/board.py:369` `@router.post("/chat")` → `board_chat()` → `run_board_chat` (378-380); board router mounted `prefix="/api/board"` at `src/hal0/api/__init__.py:1380`.

**Config:** `BrainChatConfig` at `src/hal0/config/schema.py:2971` (`enabled=True`, `read_only=False`, `model=""`, `tool_model=""`, `max_rounds=8 [1..100]`, `completion_timeout_s=300.0`); wired `Hal0Config.brain_chat` at 3029.

**Provisioning (Hermes phases to extract) — `src/hal0/agents/hermes_provision.py` (5314 lines):**
- `_phase_persona_seed` (2349) → `personas.seed_default_personas(...)` — seeds **both** `hermes` and `hal0-brain` personas.
- `_phase_brain_profile_seed` (3048) — registers `hermes__hal0-brain` identity card (`_build_brain_identity_card`, 3016) into the `agents` memory dataset via `ctx.io.mcp_memory_call`.
- `_phase_brain_profile_mcp_wire` (3211) — deep-merges `hal0-admin`+`hal0-memory` MCP servers + `memory.provider` into `~/.hermes/profiles/hal0-brain/config.yaml` (`_build_brain_profile_mcp_servers`, 3186; `_brain_profile_config_path`, 3181).
- Also relevant: `_phase_namespace_register` (2854, default agent), `_phase_self_report` (4575).
- `PHASES` list (5020): entries `persona_seed`(5037), `namespace_register`(5041), `brain_profile_seed`(5044), `brain_profile_mcp_wire`(5047), `self_report`(5068). `_validate_phase_graph` (5071) enforces ordering/needs — **removing entries requires updating any `needs=`/`needs_previous=` references.**

**Identity — `src/hal0/agents/personas.py`:**
- `PERSONAS_ROOT = Path("/var/lib/hal0/.hermes/personas")` (36) — **inside `.hermes`; must move to a hal0-owned path.**
- `BRAIN_PROFILE_AGENT_ID = "hermes__hal0-brain"` (46) — memory identity; bank `private:hermes__hal0-brain` → Hindsight `private__hermes__hal0-brain`.
- `_seed_hal0_brain` (id `hal0-brain`, `memory_namespace=f"private:{BRAIN_PROFILE_AGENT_ID}"`, `preferred_model="hal0/brain"`, tool policy) (448-...); `seed_default_personas` (516).
- **Rename impact:** `hermes__hal0-brain` / `BRAIN_PROFILE_AGENT_ID` appears **24×** across `personas.py`, `board_chat.py`, `hermes_provision.py` only (no UI dependency — UI keys off persona id `hal0-brain`, verified `endpoints.ts`/`CONTRACTS.md`).

**Slot seeds:** `installer/etc-hal0/slots/brain.toml` (`MiniCPM5-1B-Agentic-Tooluse`, `enabled=true`, port 8089; docstring recommends `tool_model="hal0/code"`); `installer/etc-hal0/slots/agent.toml` (**`enabled=false`, model-less**, port 8081). `STATIC_SEED_SLOTS` includes both (`src/hal0/install/static_seeds.py:33`).

**Lifespan already does half the work — `src/hal0/api/__init__.py`:** persona seed via `seed_default_personas(root=hermes_home/"personas")` at ~1075; static slot seed (`agent`/`brain`) at ~1090; `hermes_kanban` (1032), `approval_queue` (1591), `memory_dispatcher` (1642), `hal0_config` (965). **`self_api_base_url` is never set** (all readers fall back to `http://127.0.0.1:8080`). `board_chat_llm`, `brain_persona_root`, `platform_http` are test-injection-only.

**WARMING watchdog (pattern to mirror) — commit `e9639de1`, `src/hal0/slots/manager.py`:** `_WARMING_STALE_AFTER_S=900.0` (188); per-slot fail-watcher auto-recovers a wedged slot via unload→load (875-899). Readiness API to reuse: `is_ready_for_dispatch(name)` (612), `state(name)` (595), `DISPATCHABLE_STATES`, `load(slot_name)` (1107), `status(...)` (1355).

**The other tool loop (§7.6 unify) — `src/hal0/omni_router/router.py`:** `OmniRouter.run_loop` (97), `_extract_tool_calls` (228), builds `role=tool` (159). Duplicates board_chat's extraction/fallback — the reason `toolloop/engine.py` exists.

**UI (must stay unchanged):** `ui/src/api/endpoints.ts:409` `boardChat: '/api/board/chat'`; `useBoardChat` in `ui/src/api/hooks/useBoard.ts`; `ui/src/dash/board/agent-chat.jsx`; `ui/src/dash/board/board-hook-bridge.ts`; contract `ui/CONTRACTS.md:236`. SSE frame vocabulary the UI consumes: `token`/`thinking`/`tool_call`/`tool_result`/`approval_required`/`ping`/`done`/`error`.

**Tests today:** `tests/board/test_board_chat.py` (1055), `test_board_chat_admin_tools.py` (212), `test_board_chat_text_toolcalls.py` (74), `test_board_chat_tool_use_e2e.py` (438).

**Dependency on P2:** `src/hal0/toolloop/engine.py` does **not exist yet** (P2-toolloop lane building it). Brain consumes `run_tool_loop(llm_fn, tools, dispatch_fn, *, max_rounds, on_event)` per plan §7.6/`hal0-rework-plan.md:528`.

---

## 1. Target package: `src/hal0/brain/`

Create a first-class module. Proposed files (each with its migration source):

| New file | Responsibility | Moved/derived from |
|---|---|---|
| `src/hal0/brain/__init__.py` | Public exports: `run_brain_chat`, `ensure_brain_provisioned`, `BrainReadiness`, `BRAIN_AGENT_ID`, `BRAIN_PERSONA_ID`. | new |
| `src/hal0/brain/identity.py` | Identity constants + persona-store path. `BRAIN_PERSONA_ID="hal0-brain"`, **`BRAIN_AGENT_ID="hal0-brain"`** (drop `hermes__`), `BRAIN_MEMORY_NAMESPACE="private:hal0-brain"`, `PERSONAS_ROOT` moved out of `.hermes` (see §3). | `personas.py:36,46`; `board_chat.py:90,97` |
| `src/hal0/brain/service.py` | The SSE orchestrator: `run_brain_chat(request)` + `_chat_stream`, now driving `toolloop.engine.run_tool_loop` with an `on_event` adapter that emits the existing SSE frames (incl. the approval-pause loop, 1204-1275). Owns `_resolve_profile`, `_brain_chat_config`, `_resolve_llm`, `BrainChatConfig` model precedence (1144-1152). | `board_chat.py:1110-1304`, 162-179, 736-747, 867-894 |
| `src/hal0/brain/tools.py` | Tool schemas + dispatch: `_tool_schemas`, `_admin_tool_*`, `_surfaced_tool_schemas`, `_resolve_*`, `_dispatch_tool`/`_dispatch_admin_tool`/`_dispatch_platform_tool`, `_is_read_tool`, `ToolPolicy` load, read-only guardrail. | `board_chat.py:187-861, 1046-1048` |
| `src/hal0/brain/provision.py` | `ensure_brain_provisioned(app)` — lifespan-called idempotent provisioner (persona seed + identity card + MCP wire + slot warm + readiness). | extracted from `hermes_provision.py` phases 2349/3048/3211 |
| `src/hal0/brain/readiness.py` | `BrainReadiness` state + `ensure_tool_model_warm()` watchdog (§5). | new, mirrors `slots/manager.py` watchdog |
| `src/hal0/brain/persona_store.py` (optional split) | move `load_persona`/`seed`/`Persona*` here, or keep in `agents/personas.py` and re-export. | `personas.py` |

**Parsing/extraction (`_extract_tool_calls`, `_parse_text_tool_calls`, `_split_thinking`, regexes) does NOT move into `brain/` — it moves into `toolloop/engine.py`** (P2), and brain calls it. If P2 hasn't landed when P3 starts, brain temporarily keeps a thin local copy and swaps to the engine import on P2 merge (flagged as the one hard sequencing coupling — see §8 risks).

**Decouple from the board proxy:** the loop currently hard-requires `app.state.hermes_kanban` (`board_chat.py:1112-1116`) and errors "operator board backend not configured" when absent. In `brain/service.py`, make the kanban client **optional**: board tools degrade to `{"error": "operator board not configured"}` per-call; platform/admin/memory tools (the steward's actual remit) work with **zero board dependency**. This is what makes the brain a platform steward that survives with the board plugin absent — core-works-without-Hermes in practice.

## 2. Routing: `/api/brain/chat` primary + `/api/board/chat` alias (UI unchanged)

- Add a new router `src/hal0/api/routes/brain.py` with `@router.post("/chat")` → `hal0.brain.service.run_brain_chat`; mount `prefix="/api/brain"` in `src/hal0/api/__init__.py` (next to line 1380).
- Keep `src/hal0/api/routes/board.py:369` `@router.post("/chat")` as a **thin alias** delegating to `hal0.brain.service.run_brain_chat` (replace the `from hal0.api.routes.board_chat import run_board_chat` import at 378). `/api/board/chat` stays byte-for-byte behavior-identical so the UI (`endpoints.ts:409`, `useBoardChat`) needs **no change**.
- Update `endpoints.ts` optionally to add `brainChat: '/api/brain/chat'` as the new canonical (leave `boardChat` pointing at the alias) — **not required for this lane**, UI unchanged is the constraint.
- Delete `src/hal0/api/routes/board_chat.py` after the move, or reduce it to a back-compat shim re-exporting from `hal0.brain` (safer for external importers / the `__all__` at 1307-1327; tests import `hal0.api.routes.board_chat` symbols directly — see §7).

## 3. Provisioned by API lifespan, not Hermes phases

**Add `ensure_brain_provisioned(app)`** called from the lifespan in `src/hal0/api/__init__.py` (right after the existing persona/slot seed block ~1075-1097). It performs, all idempotent + warn-as-OK (never blocks startup, mirroring the existing `try/except` seed blocks):

1. **Persona seed** — already happens via `seed_default_personas` at ~1075; keep it, but point `root` at the **hal0-owned personas path** (§3a).
2. **Identity card** — port `_build_brain_identity_card` + the search→delete-stale→add logic from `_phase_brain_profile_seed` (3048-3163) into `brain/provision.py`, calling through `app.state.memory_dispatcher` (not `ctx.io.mcp_memory_call`). Use `BRAIN_AGENT_ID="hal0-brain"` and dataset/namespace `private:hal0-brain`.
3. **MCP wire** — the `_phase_brain_profile_mcp_wire` (3211) logic writes into a **Hermes profile `config.yaml`**. In the hal0-native world the brain reaches hal0-admin + hal0-memory **directly via `memory_dispatcher` + admin.dispatch** (already how `board_chat` does it — no Hermes profile involved). So this phase largely **evaporates**: the "wire" becomes ensuring `app.state.memory_dispatcher` is constructed with the brain's private namespace. Only if a Hermes *profile* still wants these servers (optional escalation seam, §16.1) does the YAML merge survive — move it behind the optional-Hermes path, not core provisioning.

**Then delete the Hermes phases:** remove the **brain half** of `_phase_persona_seed` (keep `hermes` persona seeding for the Hermes agent itself if Hermes is installed; the hal0-brain persona now seeds in lifespan), and delete `_phase_brain_profile_seed` + `_phase_brain_profile_mcp_wire` and their `PHASES` entries (5044, 5047). Update `_validate_phase_graph` consumers and any `needs=`. This is also the enabling deletion for §7.4's "slim the provisioner to ~200 lines."

**3a. Move persona store out of `.hermes`:** change `personas.PERSONAS_ROOT` from `/var/lib/hal0/.hermes/personas` to a hal0-owned path, e.g. `paths.var_lib()/"personas"` (or `/var/lib/hal0/brain/personas`). Update the lifespan seed call (`root=hermes_home/"personas"` at ~1077) and `board_chat`/`brain` persona-root resolution (`brain_persona_root` app.state). Provide a one-shot migration: if the old `.hermes/personas/hal0-brain.toml` exists and the new path doesn't, copy it (operator edits survive). The `hermes` persona can stay under `.hermes` (it's genuinely Hermes's) or also move — decide per §7.4, but hal0-brain must leave `.hermes`.

**3b. Identity rename `hermes__hal0-brain` → `hal0-brain`:** update `BRAIN_PROFILE_AGENT_ID` (personas.py:46), the 24 references, `_SYSTEM_PROMPT` line 108, `_ADMIN_TOOL_EXCLUDES` comment 410, `X-hal0-Agent` header in `_build_brain_profile_mcp_servers`, and the memory namespace `private:hal0-brain` (Hindsight bank `private__hal0-brain`). **Migration risk:** an existing box has memories under bank `private__hermes__hal0-brain`. Add a startup one-time bank-rename/alias (or a `memory_dispatcher` namespace alias map) so the steward's history isn't orphaned. If a clean cutover is acceptable on the new `halo` LXC (per MEMORY: rework deploys side-by-side, not in-place), document that the old bank is abandoned and skip the alias — **call this out for operator decision.**

## 4. Shared toolloop engine (§7.6)

`brain/service.py` drives `toolloop.engine.run_tool_loop(llm_fn, tools, dispatch_fn, *, max_rounds, on_event)`:
- `llm_fn` = `_resolve_llm(request)` (unchanged closure/injection).
- `tools` = `_surfaced_tool_schemas(request)` (persona-policy filtered).
- `dispatch_fn` = a closure over `_dispatch_tool(request, client, name, args, board=...)`.
- `on_event` = adapter yielding the SSE frames (`token`/`thinking`/`tool_call`/`tool_result`/`done`/`error`) so the wire contract is unchanged.
- The **approval-pause** block (`board_chat.py:1204-1275`) is brain-specific (it depends on `app.state.approval_queue`); it stays in the brain's `on_event`/dispatch wrapper, OR the engine grows an `on_pending_approval` hook. Prefer: engine emits a generic `tool_result` with `status=pending_approval`, brain's `on_event` runs the wait-loop + `approval_required`/`ping` frames. Keep the engine approval-aware but transport-agnostic (matches plan §16 "streaming + approval-aware").
- The text-toolcall fallback + `_split_thinking` now live in the engine (shared with OmniRouter), fixing the leak once. `_surfaced_tool_names` gating (985-992, "only accept names that are real tools") must be passed into the engine as the `known_names` set.

**Coordination note for P2:** the engine's `on_event` contract must carry `thinking` (explicit + inline), native-and-text tool calls, and per-call result framing — verify P2's signature covers all three or extend it. OmniRouter (`router.py:97`) is the second caller; unifying both is P2's job, brain just consumes.

## 5. Reliability: default `tool_model`, warm slot, readiness gate → read-only degrade

**5a. Default `tool_model="hal0/agent"`.** Change `BrainChatConfig.tool_model` default from `""` to `"hal0/agent"` (`schema.py:3003`). Rationale (verified): the `brain` slot ships `MiniCPM5-1B` which **can't emit native tool calls on the FPX runtime** (brain.toml docstring + MEMORY `hal0-brain-toolcall-leak`), so the steward always offers tools → tool turns must route to a capable slot. Plan §7.3 mandates `hal0/agent`; brain.toml currently *recommends* `hal0/code`. **Resolve the discrepancy:** `hal0/agent` is the always-on anchor every fallback chain ends in (agent.toml ADR-0023) — but it ships `enabled=false, model-less`. So the default is only reliable if §5b makes the agent slot real. Recommend default `hal0/agent` per §7.3 (single well-known anchor) and update brain.toml's recommendation comment to match.

**5b. Ensure the `tool_model` slot is warm when `brain_chat.enabled`.** The `agent` slot seed is `enabled=false` + model-less (agent.toml). In `ensure_brain_provisioned`: when `cfg.brain_chat.enabled` and `cfg.brain_chat.tool_model` resolves to a local `hal0/<slot>`, call `slot_manager.load(slot)` (or reconcile it enabled) so it's `DISPATCHABLE` before the first turn. Do NOT force-download a model on a model-less box — instead surface it via readiness (§5c). Use `slot_manager.is_ready_for_dispatch(slot)` (manager.py:612) as the gate.

**5c. Startup readiness gate → read-only degrade (mirror `e9639de1`).** Add `brain/readiness.py`:
- `BrainReadiness` computed at startup + refreshed on a lightweight watchdog: the brain is **fully-ready** iff its resolved `tool_model` slot `is_ready_for_dispatch`; **degraded/read-only** iff no tool-capable slot is warm (mirrors the "wedged WARMING → auto-recover" logic, but for the brain the recovery is "degrade the surface, don't 500").
- When degraded, `brain/tools.py` forces the effective `read_only=True` guardrail (reuse the existing `_is_read_tool` gate at `board_chat.py:801`) so the steward keeps answering/reading but refuses mutating/admin-write tools **instead of 500-ing mid-turn** when the tool_model can't be reached. Emit an `error`/system frame explaining "tool model warming — read-only until ready."
- Optionally attempt one auto-recover (`load`) of the tool_model slot per watchdog tick, bounded exactly like `_WARMING_STALE_AFTER_S=900` (manager.py:188), before declaring degraded — reuse the slot-manager's own watchdog rather than duplicating; brain readiness just *reads* slot state.

**5d. Brain listed as system infrastructure, not an installed agent.** Per §7.3, ensure the agents list (`src/hal0/api/routes/agents.py` / `agents/manager.py`) does **not** enumerate `hal0-brain` as an installed agent (it's the platform itself). The `list_agents` tool (board_chat.py:290) lists platform agents like `hermes`; verify brain is absent there. No code change likely needed (brain isn't in `manager.py`'s installed set today), but assert it in a test.

## 6. Files add / touch summary

**Add:** `src/hal0/brain/{__init__,identity,service,tools,provision,readiness}.py`; `src/hal0/api/routes/brain.py`; `tests/brain/…` (below).

**Touch:**
- `src/hal0/api/__init__.py` — mount `/api/brain` router; call `ensure_brain_provisioned(app)` in lifespan; point persona seed at hal0-owned root; construct `memory_dispatcher` with `private:hal0-brain`.
- `src/hal0/api/routes/board.py:369-380` — alias `/api/board/chat` → `hal0.brain.service.run_brain_chat`.
- `src/hal0/api/routes/board_chat.py` — delete or reduce to re-export shim.
- `src/hal0/config/schema.py:3003` — `tool_model` default `"hal0/agent"`.
- `src/hal0/agents/personas.py:36,46` — `PERSONAS_ROOT` off `.hermes`; `BRAIN_PROFILE_AGENT_ID`→`hal0-brain`; namespace `private:hal0-brain`.
- `src/hal0/agents/hermes_provision.py` — delete `_phase_brain_profile_seed` (3048), `_phase_brain_profile_mcp_wire` (3211), brain half of `_phase_persona_seed` (2349); remove `PHASES` entries (5044,5047) + fix `_validate_phase_graph`/`needs`; drop `_build_brain_identity_card`/`_build_brain_profile_mcp_servers`/`_brain_profile_config_path` (or move to `brain/provision.py`).
- `installer/etc-hal0/slots/brain.toml` + `agent.toml` — update tool_model recommendation comment to `hal0/agent`; consider seeding `agent` enabled (or document reconcile-on-provision).
- `ui/src/api/endpoints.ts` (optional) — add `brainChat` constant; UI behavior unchanged.

## 7. Tests

- **Move + rename** `tests/board/test_board_chat*.py` → `tests/brain/` (they import `hal0.api.routes.board_chat` symbols directly — either keep a re-export shim so they pass unchanged, or update imports to `hal0.brain.*`). Preserve all 4 files' coverage (loop, admin tools, text-toolcalls, e2e).
- **New `tests/brain/test_routing.py`** — `/api/brain/chat` and `/api/board/chat` alias return identical SSE for the same stub `board_chat_llm`; UI endpoint constant still resolves.
- **`test_provision.py`** — `ensure_brain_provisioned` seeds persona at hal0-owned root, writes identity card with `agent_id=hal0-brain` + namespace `private:hal0-brain`, is idempotent, warn-as-OK when `memory_dispatcher` absent; asserts the deleted Hermes phases no longer run (PHASES no longer contains brain_profile_*).
- **`test_readiness.py`** — tool_model slot ready → mutations allowed; tool_model slot cold/warming → brain degrades to read-only (mutating tool returns the read-only refusal envelope, reads pass); mirror `test_slot_manager` WARMING cases (`d0fe629c`).
- **`test_decouple_board.py`** — with `app.state.hermes_kanban=None`, platform/admin/memory tools still dispatch; board tools return a graceful per-call error (not a fatal stream error).
- **`test_identity_rename.py`** — no `hermes__` prefix remains in brain surface; `_SYSTEM_PROMPT`/excludes comment updated.
- **Hermes-optional guard:** a test that constructs the app with the Hermes agent uninstalled and asserts the brain chat still serves (core-works-without-Hermes, §16.1).
- **UI:** existing `ui/tests/e2e/specs/board-chat-*.spec.ts` must stay green against the alias (no UI change).

## 8. Risks / sequencing

1. **Hard dependency on P2-toolloop.** `toolloop/engine.py` doesn't exist yet. If P3 starts first, brain carries a temporary local copy of extraction/loop and swaps on P2 merge — a real merge-conflict surface in `_chat_stream`. Mitigation: land P2 first, or gate the engine import behind a small local fallback.
2. **Identity rename memory-orphan.** `private__hermes__hal0-brain` bank → `private__hal0-brain`. Existing boxes lose steward memory unless aliased/migrated. MEMORY says rework deploys side-by-side on a fresh `halo` LXC, so a clean cutover may be acceptable — **operator decision required.**
3. **`agent` slot ships disabled + model-less** (agent.toml) — the default `tool_model="hal0/agent"` is only reliable once §5b warms it; on a model-less box the readiness gate (§5c) must degrade gracefully, not spin trying to load nothing. Test the model-less path explicitly.
4. **`_validate_phase_graph` fail-fast** (`hermes_provision.py:5071`) runs at import time — deleting brain phases without fixing every `needs=`/`needs_previous=` reference breaks module import for the whole provisioner. Grep those before deleting.
5. **`board_chat.py` `__all__` + direct test imports** — 4 test files + external importers reference `hal0.api.routes.board_chat`. A re-export shim de-risks the move; a hard delete needs a coordinated test-import sweep.
6. **`hermes` persona still seeded by `_phase_persona_seed`** — don't delete the whole phase, only the hal0-brain half, or Hermes (if installed) loses its persona. Keep the split clean since §7.4 keeps Hermes.
7. **`self_api_base_url` unset in production** — brain's platform-tool self-HTTP and `_resolve_llm` rely on the `http://127.0.0.1:8080` default. Harmless today but brittle if the API ever binds elsewhere; consider setting `app.state.self_api_base_url` explicitly in lifespan as part of this lane.

This spec is buildable as-is once P2-toolloop's `run_tool_loop` signature is confirmed; every referenced symbol/line was verified against `rework/descar`.