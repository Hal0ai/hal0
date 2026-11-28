# MCP tool quirks — hal0-admin / hal0-memory

Facts about the tool surface that differ from what a tool's name suggests. Names are shown bare
(`slot_list`); your client calls them `mcp__hal0_admin__slot_list` (see the `hal0-admin` skill).

## Arguments

- **Every hal0-admin tool takes one `args` object**: `{"args": {"name": "agent"}}`. Each tool's
  schema in the tool listing names its fields. A tool with no required field can be called with no `args`.
- `slot_status`, `slot_load` and the other slot tools take the **slot name** as `name`.
  `model_swap` takes `name` (the slot, not the model) and `model_id`.

## Large answers

- `slot_list` returns a compact roster. It keeps `tune_effective` but leaves out the rest of the
  tuning detail and the resolved command; call `slot_status` or `slot_resolved` for those.
- `model_list` unfiltered is large: pass `fields` (for example `id,type`) and filters (`installed`,
  `type`, `capability`, `device`, `q`, `limit`, `offset`).
- `capability_list` unfiltered is large: pass `section` (`backends`, `catalogs`, `selections`)
  and/or `summary=true`.

## Gated tools

Some tools do not run when called: they return `pending_approval` at once and nothing waits. Tell
the person, then re-check with a read tool after they approve.

- `logs_tail`, `slot_logs`, `comfyui_logs` and `mcp_server_logs` are gated, because journal text
  can carry secrets. Ask the person to approve, or run `hal0 doctor logs --unit <unit>` with the
  terminal tool (`journalctl` is denied to you, and the units are system units, so there is no
  `--user`). A person with root can run `journalctl -u <unit>`.
- Gated writes include `slot_create`, `slot_delete`, `slot_restart`, `model_pull`, `model_delete`,
  `capability_set`, `config_write`, `provider_credential_write`, `service_action`.
- Autonomous writes (no approval) include `slot_load`, `slot_unload`, `slot_edit`, `model_swap`,
  `model_scan`, `memory_add`. `slot_load` with `unload_npu_holder=true` is gated.
- The authoritative lists are `AUTONOMOUS_READ_TOOLS`, `AUTONOMOUS_WRITE_TOOLS` and `GATED_TOOLS`
  in `src/hal0/mcp/admin.py`.

## Tool-specific

- `model_pull` returns a job id at once; check `model_pull_status` once and report. Do not poll in a loop.
- `slot_restart` can time out on a slot in `error` state; prefer `slot_load`, which regenerates the unit.
- `provider_credential_write` stores a model upstream provider's API key. It does not write messaging-platform tokens.
- `provider_list` is empty when no remote provider is configured; that is normal.
- `memory_add` is shared by default. Pass `private=true` (a boolean field) to file one note under your
  own identity; never name a private dataset yourself (`dataset="private:<agent>"` is refused).
