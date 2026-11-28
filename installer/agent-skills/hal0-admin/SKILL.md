---
name: hal0-admin
description: >
  Teach agents how to use the hal0-Admin MCP for slot management, model/capability
  control, hardware probes, system status, and companion services (ComfyUI, Hermes, etc.).
  Trigger: "check system status", "list slots", "show hardware", "admin mcp",
  "hal0 admin", "what slots are running", "load a slot", "start a model",
  "system info", "service health", "slot metrics".

---

# Hal0-Admin MCP — Usage Guide

The hal0-Admin MCP gives control over the hal0 inference platform: slots, models, hardware,
benchmarks, services, and MCP server management. The tool list is long and grows between
releases; this guide covers the common tasks. Ask the server for the rest.

## MCP Endpoint

Local (on hal0): `http://127.0.0.1:8080/mcp/admin/mcp` (MCP over the `http` transport, POST only; the
path ends in `/mcp`; the generated `MCP-CLIENTS.md` says not to use `streamable-http` or SSE).
When hal0's network auth is on (`hal0 auth status` says whether), the call needs the admin key as
`Authorization: Bearer <key>`; `/mcp/admin` accepts the admin key only. From another machine use
that host's address and the same path.

## Tool names and arguments

Hermes sees the tools under the namespace `mcp__hal0_admin__` (server `hal0-admin`). They are
deferred tools: find one with `tool_search`, then run it with `tool_call` using the exact name
`tool_search` returns, for example `mcp__hal0_admin__slot_list`. `hal0_admin:<tool>` is not a
callable name. Another MCP client names the tools by its own rules; use the name its tool
listing shows.

Every tool takes **one `args` object**, so a call looks like
`mcp__hal0_admin__slot_status` with `{"args": {"name": "<slot>"}}`. A tool with no required field
can be called with no `args`. The tool listing gives each tool's fields; this guide shows only the
names that matter. Slot tools take the slot name as `name`.

## Quick Reference — Common Tasks

Names below are the tool names; add the `mcp__hal0_admin__` prefix to call them.

### System Overview
```
system_info        → hardware, feature flags, per-runner state ("what can this box run")
doctor_report      → health, URLs, system, memory, services, capabilities (one read)
health_system      → deep health: disk, slot manager, event bus, MCP mount
env_report         → host snapshot: container, CPU, RAM, GPU, NPU, network, tooling
power_stats        → GPU power/temp/clock, CPU temp
hardware_probe     → live probe: backends, memory, accelerators
```

### Slots — Status & Metrics
```
slot_list          → every slot (local + remote), compact roster
slot_status {name} → one slot's lifecycle state + metadata
slot_metrics       → per-slot tok/s, latency, queue depth
slot_capacity      → GPU/NPU memory capacity and per-slot allocation
slot_stats         → per-slot runtime metrics aggregated across upstreams (fleet-wide)
slot_resolved {name} → the resolved llama-server command, per-flag provenance
```

### Slots — Control
These change what is serving. **Ask and wait for a yes** before `slot_unload` and `model_swap`.
Tell the person before `slot_load` and `slot_edit`, and wait for a yes if the slot is loaded or in use.
```
slot_load {name}               → load a slot (optional model_id to assign first)   [no approval]
slot_unload {name}             → unload gracefully                                   [no approval]
slot_edit {name, ...}          → placement fields: model, profile, port, device, pinned, ...  [no approval]
model_swap {name, model_id}    → swap a slot to another model (restarts its container)  [no approval]
slot_restart {name}            → restart the slot's unit                             [gated]
slot_create {name, model, ...} → write a new slot config (then slot_load to start)   [gated]
slot_delete {name}             → delete a slot                                       [gated]
```
**Never unload or swap the slot that is serving you.** Your own model is the one hal0 Hermes is
configured with, normally `hal0/agent`, which is served by the slot named `agent`. Confirm with
`slot_status {name: "agent"}` and the model your session reports. If you cannot tell which slot
serves you, ask the person before touching any slot that is loaded.

A gated tool returns `pending_approval` at once and nothing waits: tell the person, then re-check
with a read tool (`slot_status`, `slot_list`) after they approve. Prefer `slot_load` over
`slot_restart` for a slot in `error` state or after a config change: load regenerates the unit.

### Models
```
model_list           → local registry + upstream models (large: pass fields, filters, limit)
model_show {model_id} → full metadata for one model
model_catalogue      → the curated remote catalogue: what COULD be pulled
model_inspect {hf_repo} → peek at a HuggingFace repo without registering anything
model_pull {model_id, hf_repo, hf_filename, ...} → download into the model store (async, gated)
model_pull_status    → one pull job's progress; check once, do not poll in a loop
model_scan_preview   → dry run of a model scan
model_scan           → register newly found model files (adds registry rows)
hf_search            → free-text search of the HuggingFace Hub (q, type, limit)
```

### Services & ComfyUI
```
service_list         → companion services (ComfyUI, Hermes, Hindsight, OpenWebUI) with probe status
service_health       → comfyui / hermes / openwebui health summary
service_action {service_id, action} → start|stop|restart|enable|disable, only what the row allows  [gated]
comfyui_status       → engine + queue + arbiter mode
comfyui_workflow_launch {name} → launch a workflow template  [gated]
comfyui_models       → image-model catalog with install state
```

## Current state of this box

Do not rely on any list in this guide for what is installed or running. Call `slot_list` for
the slots, `model_list` (with `fields`) for the models, `upstream_list` for remote providers,
`service_list` for companion services, and `hardware_probe` or `env_report` for the hardware.

## Rules for agents

1. **Read-only first**: always probe state before mutating.
2. **Gated operations need the person's approval**: `slot_create`, `slot_delete`, `slot_restart`,
   `model_pull`, `model_delete`, `service_action` and others return `pending_approval` at once.
   Nothing waits: tell the person, then re-check with a read after they approve.
3. **Use `slot_list`** before load/unload to see the current state.
4. **`model_swap`** changes the model on a slot and restarts its container; it is one call instead
   of `slot_unload` plus `slot_load`, not a way around a load time. Ask and wait for a yes first,
   and never on the slot that is serving you.
5. **Check `slot_metrics`** after loading to confirm a healthy start.
6. **Never edit TOML files** directly (for example `/etc/hal0/capabilities.toml` or a slot file); use the tools.
7. **Prefer `doctor_report`** for a one-shot overview instead of chaining several reads.
