# _StepCtx

> 29 nodes

## Key Concepts

- **_StepCtx** (21 connections) — `src/hal0/agents/hermes_provision.py`
- **PhaseResult** (20 connections) — `src/hal0/agents/hermes_provision.py`
- **_phase_self_report()** (9 connections) — `src/hal0/agents/hermes_provision.py`
- **_phase_brain_profile_mcp_wire()** (7 connections) — `src/hal0/agents/hermes_provision.py`
- **_default_mcp_servers()** (6 connections) — `src/hal0/agents/hermes_provision.py`
- **_deep_merge()** (6 connections) — `src/hal0/agents/hermes_provision.py`
- **_phase_mcp_wire()** (6 connections) — `src/hal0/agents/hermes_provision.py`
- **_phase_gateway_secrets_wire()** (6 connections) — `src/hal0/agents/hermes_provision.py`
- **_phase_home_init()** (5 connections) — `src/hal0/agents/hermes_provision.py`
- **_phase_env_probe()** (5 connections) — `src/hal0/agents/hermes_provision.py`
- **_load_agent_allowlist()** (5 connections) — `src/hal0/agents/hermes_provision.py`
- **_phase_namespace_register()** (5 connections) — `src/hal0/agents/hermes_provision.py`
- **_phase_brain_profile_seed()** (5 connections) — `src/hal0/agents/hermes_provision.py`
- **_build_brain_profile_mcp_servers()** (4 connections) — `src/hal0/agents/hermes_provision.py`
- **.output_of()** (4 connections) — `src/hal0/agents/hermes_provision.py`
- **The return of one install-step body.      ``details`` is a free-form dict the st** (1 connections) — `src/hal0/agents/hermes_provision.py`
- **Make the ``$HERMES_HOME`` layout canonical — ``mkdir`` the standard tree.      C** (1 connections) — `src/hal0/agents/hermes_provision.py`
- **Capture a host-environment snapshot for context_link to render from.      Writes** (1 connections) — `src/hal0/agents/hermes_provision.py`
- **Builtin MCP server inventory (matches PR-1's allowlist + auto-register).      Ph** (1 connections) — `src/hal0/agents/hermes_provision.py`
- **Recursive dict merge — overlay wins; nested dicts merge.** (1 connections) — `src/hal0/agents/hermes_provision.py`
- **Read ``[mcp.servers.*]`` blocks from the per-agent allow-list.      Returns ``No** (1 connections) — `src/hal0/agents/hermes_provision.py`
- **Verify the two hal0-bundled MCP servers respond + record their tool list.      W** (1 connections) — `src/hal0/agents/hermes_provision.py`
- **Write the Hermes identity card to the `agents` memory dataset.      Idempotency:** (1 connections) — `src/hal0/agents/hermes_provision.py`
- **Register the hal0-brain profile as a first-class agent identity.      Writes the** (1 connections) — `src/hal0/agents/hermes_provision.py`
- **The hal0-owned MCP servers the hal0-brain profile is wired to.      Mirrors the** (1 connections) — `src/hal0/agents/hermes_provision.py`
- *... and 4 more nodes in this community*

## Relationships

- [hermes_provision.py](hermes_provision.py.md) (22 shared connections)
- [Path](Path.md) (16 shared connections)
- [Any](Any.md) (12 shared connections)
- [_phase_preflight](_phase_preflight.md) (2 shared connections)
- [install_hermes](install_hermes.md) (2 shared connections)
- [write_gateway_secrets_dropin](write_gateway_secrets_dropin.md) (1 shared connections)
- [ensure_gateway_api_server_key](ensure_gateway_api_server_key.md) (1 shared connections)

## Source Files

- `src/hal0/agents/hermes_provision.py`

## Audit Trail

- EXTRACTED: 119 (93%)
- INFERRED: 9 (7%)
- AMBIGUOUS: 0 (0%)

---

*Part of the graphify knowledge wiki. See [index](index.md) to navigate.*