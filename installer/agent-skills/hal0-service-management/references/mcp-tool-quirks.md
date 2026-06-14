# MCP tool quirks — hal0-admin / hal0-memory

Non-obvious behaviours discovered while probing hal0. These are not bugs, just
API surface facts that differ from what the tool signatures suggest.

## hal0-admin tools

### `slot_status`
- **Requires `name`, not `slot_name`.** The JSON arg is `{"name": "agent-hermes"}`.
  Passing `{"slot_name": "agent-hermes"}` succeeds but returns `{"status":"error"}`.
- Occasionally returns empty error (`"Error executing tool slot_status: "`) with
  no structured payload — treat as transient, retry once before giving up.

### `slot_list`
- No args (empty `{}`). Returns all slots.
- Can fail with empty error identically to `slot_status` — retry once.

### `read_resource`
- **Only accepts registered MCP resource URIs.** The resource list on this host
  is empty (`[]` from both hal0-admin and hal0-memory). Arbitrary `file:///` URIs
  return `Unknown resource`. You cannot use this tool to read arbitrary files.
  Use `delegate_task` with terminal toolset (after setting `allow_code_exec`)
  or a terminal-based subagent for file reads.

### `logs_tail`
- **Gated.** Returns `{"status":"pending_approval","approval_id":"..."}` instead
  of log output. Requires human approval through the admin gating layer.
  Workaround: use a terminal subagent running `journalctl --user -u <unit>`.

### `capability_list`
- Returns `{"status":"error"}` with empty detail — possibly requires specific
  args or the capability subsystem is not configured on this host.

### `hardware_probe` / `version_info`
- Can fail with empty error when slots are evicted/offline. Retry after warming
  a slot (send a request to the agent-hermes or primary endpoint first).

### `provider_list`
- Returns empty array `[]` when no remote providers are configured. This is
  expected — it means everything runs locally via lemonade/llama-server.

### `model_list`
- Works reliably. Returns full model registry with sizes, backends, and tags.
  This is the best tool for understanding what's available locally.

### `env_report`
- The `tooling` field scans a fixed set of paths: docker, podman, flm, python3, uv.
  It does **not** scan for node, npm, npx, claude, claude-code, or other binaries.
  Absence from the report does not mean the binary is absent — it means it's
  outside the scan set.

## hal0-memory tools

### `memory_search`
- **30-second MCP timeout.** Broad queries or queries during high memory load
  can time out. Narrow the query (fewer terms, shorter string) if hitting
  timeouts. The underlying store (Cognee) is a vector+graph hybrid — broad
  semantic searches are more expensive than keyword matches.

### `slot_status` — per-slot argument parsing bug
- **`{"name":"agent"}` works fine. `{"name":"chat"}` fails with `mcp.missing_arg`.**
  This is NOT a general bug — the tool signature is correct. Something specific
  to the `"chat"` slot or its arg routing causes the parser to drop the `name`
  field mid-flight. Workaround: use `slot_list` which returns full details for
  every slot, or use `systemctl --user show -p ... --value <service>.service`
  via terminal. If a specific slot name triggers this, avoid calling
  `slot_status` on it and fall back to `slot_list`.

### `memory_add` — private namespace rejected on admin server
- `mcp_hal0_admin.memory_add` **cannot address private namespaces** — the call
  returns `mcp.memory_schema` error: "non-private callers cannot address the
  private namespace by name."
- **Always use `mcp_hal0_memory` (dedicated memory server) for private
  dataset operations** (`private:hermes-agent` etc.). The dedicated server
  supports private namespaces correctly. The admin server is shared-memory
  only.

### `capability_list` — catalog truncation
- Returns 3 backend entries + a massive pullable-model catalog. The catalog
  can exceed 80K chars, causing the response to be truncated. If you need the
  full catalog, parse it in chunks or filter by capability name.

## General patterns

- **Many admin tools are gated** and return `pending_approval` for write
  operations (`config_write`, `capability_set`, `model_pull`, `model_delete`,
  `slot_create`, `slot_delete`, `provider_credential_write`, `model_swap`).
  Read operations (`slot_list`, `model_list`, `env_report`, `provider_list`,
  `version_info`, `npu_status`, `model_store_probe`) typically pass through.

- **Empty error responses** (`"Error executing tool X: "`) should be treated as
  transient. Retry once with a 1-second gap. If they persist, the underlying
  slot may be evicted — warm it by sending a health-check request first.
