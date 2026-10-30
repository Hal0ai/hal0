---
name: hal0-admin
description: >
  Teach agents how to use the hal0-Admin MCP for slot management, model/capability
  control, hardware probes, system status, and companion services (ComfyUI, Hermes, etc.).
  Trigger: "check system status", "list slots", "show hardware", "admin mcp",
  "hal0 admin", "what slots are running", "load a slot", "start a model",
  "system info", "service health", "slot metrics".

---

# Hal0-Admin MCP — Usage Guide (Pi Edition)

The hal0-Admin MCP (189 tools) gives full control over the hal0 inference platform:
slots, models, hardware, benchmarks, services, and MCP server management.

## MCP Endpoint

Local (on hal0): `http://127.0.0.1:8080/mcp/admin/mcp` (no auth)
Remote (Bearer token): `https://<hal0-host>/mcp/admin/mcp`

Tool namespace: `mcp__local_mcp` (when installed via `pi mcp add`)

## Quick Reference — Common Tasks

### System Overview
```
mcp__local_mcp_system_info({})        → hardware, backends, features
mcp__local_mcp_doctor_report({})      → health, URLs, memory, services (one read)
mcp__local_mcp_health_system({})      → deep health: disk, slot mgr, event bus
mcp__local_mcp_env_report({})         → full host snapshot
mcp__local_mcp_power_stats({})        → GPU power/temp/clocks
```

### Slots — Status & Metrics
```
mcp__local_mcp_slot_list({})          → all 21 slots (local + remote)
mcp__local_mcp_slot_status({name})    → one slot lifecycle + metadata
mcp__local_mcp_slot_metrics({})       → tok/s, latency, queue per slot
mcp__local_mcp_slot_capacity({})      → GPU/NPU memory allocation
mcp__local_mcp_slot_stats({})         → aggregated fleet metrics
```

### Slots — Control (gated/confirm first)
```
mcp__local_mcp_slot_load({name})          → start a slot
mcp__local_mcp_slot_unload({name})        → stop gracefully
mcp__local_mcp_slot_restart({name})       → restart systemd unit
mcp__local_mcp_slot_edit({name, ...})     → change model/profile/port/pinned
mcp__local_mcp_slot_create({name, model}) → create new slot config
mcp__local_mcp_slot_delete({name})        → remove a slot
mcp__local_mcp_model_swap({name, model_id}) → hot-swap model on running slot
```

### Models
```
mcp__local_mcp_model_list({})           → installed + upstream models
mcp__local_mcp_model_show({model_id})   → full metadata for one model
mcp__local_mcp_model_catalogue({})      → blessed remote models (what can pull)
mcp__local_mcp_model_inspect({hf_repo}) → peek at HF repo without downloading
mcp__local_mcp_model_pull({...})        → download from HF (async, gated)
mcp__local_mcp_model_pull_status({...}) → check download progress
mcp__local_mcp_model_scan({})           → discover new models on disk
mcp__local_mcp_hf_search({query})       → search HuggingFace Hub
```

### Services & ComfyUI
```
mcp__local_mcp_service_list({})            → ComfyUI/Hermes/Hindsight/OpenWebUI
mcp__local_mcp_service_health({})          → 3-service summary
mcp__local_mcp_service_action({id, verb})  → start/stop/restart a service
mcp__local_mcp_comfyui_status({})          → engine + queue + arbiter
mcp__local_mcp_comfyui_workflow_launch({}) → quick-launch a workflow template
mcp__local_mcp_comfyui_models({})          → image model catalog
```

## Current Slot Layout (hal0)

| Slot | State | Model | Type | Backend |
|------|-------|-------|------|---------|
| agent | ready | nemotron-3.5-bolt-74b | LLM | vulkan |
| code | idle | tiel-coder-35b | LLM | rocm |
| utility | serving | lfm2-5-2-6b | LLM | vulkan |
| brain | idle | lfm2-5-2-6b | LLM | vulkan (pinned) |
| ops | offline | Qwopus3.6-27B | LLM | vulkan |
| img | idle | sdxl-turbo | Image | rocm (ComfyUI) |
| tts | idle | qwen3-tts | TTS | rocm |
| embed | offline | qwen3-embedding-0.6b | Embedding | rocm |
| rerank | offline | bge-reranker-v2-m3 | Reranking | rocm |

Plus 6 remote upstreams: openrouter, minimax, hermes, hermes.coder, etc.

## Rules for Pi Agents

1. **Read-only first**: always probe state before mutating
2. **Confirm gated ops**: slot_create, slot_delete, model_pull, model_delete are gated
3. **Use `slot_list`** before load/unload to understand current state
4. **`model_swap` > `slot_unload`+`slot_load`**: hot-swap avoids cold-start
5. **Check `slot_metrics`** after loading to confirm healthy startup
6. **Never edit TOML files** directly — use the MCP tools
7. **Prefer `doctor_report`** for one-shot overview instead of chaining multiple reads
