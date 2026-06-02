# ADR 0022 — Per-slot llama.cpp backend: load-path stickiness, actual-vs-declared display, and control

- **Status:** Proposed
- **Date:** 2026-06-02
- **Drivers:** The 80B chat slot `primary` declares `device = "gpu-vulkan"`
  in its TOML, yet ran under ROCm. Root cause: the gateway name-based
  lazy-load never told lemond which backend to use, so lemond fell back to
  its global default (`llamacpp.backend = "rocm"` in
  `/var/lib/hal0/lemonade/config.json`). The dashboard compounded the
  confusion by showing the *declared* device chip, never the *actual*
  runtime backend — a silent display lie.
- **Related:** ADR-0006 (`backend` → `device` migration), ADR-0008
  (Lemonade as the v0.2 unified runtime), ADR-0009 (FLM trio NPU packing).

## Context

### Two load paths, only one wired for backend

Lemonade (`lemond`, loopback `127.0.0.1:13305`) supervises one
`llama-server` child *per loaded model*. Two builds are installed:

```
/var/lib/hal0/lemonade/bin/llamacpp/rocm-stable/llama-server   (libggml-hip.so)
/var/lib/hal0/lemonade/bin/llamacpp/vulkan/llama-server        (libggml-vulkan.so)
```

Because each model is its own child process, different models can run
different backends concurrently (`max_loaded_models = 4`; whispercpp
already runs Vulkan while llamacpp's global default is ROCm — mixed
backends already coexist in production).

The backend for a given load is chosen by the `llamacpp_backend` field in
the `POST /v1/load` body. When that field is omitted, lemond uses the
global `llamacpp.backend` from its `config.json`. hal0 maps
`device → (recipe, llamacpp_backend)` in `device_to_backend()`
(`providers/lemonade.py:87`):

| device       | recipe | llamacpp_backend |
|--------------|--------|------------------|
| `gpu-rocm`   | None   | `rocm`           |
| `gpu-vulkan` | None   | `vulkan`         |
| `cpu`        | None   | `cpu`            |
| `npu`        | `flm`  | None             |
| empty/unknown| None   | None (→ lemond global default) |

**Explicit load** (`POST /api/slots/{name}/load` → `SlotManager.load` →
`_spawn_locked` → `LemonadeProvider.load(cfg)` → `client.load(...,
llamacpp_backend=...)`, `manager.py:1183`, `lemonade/client.py:207`)
**correctly** sends `llamacpp_backend` derived from the slot's `device`.

**Lazy load is the gap, and it does NOT go through hal0's `/v1/load` at
all.** When a chat request arrives by model name on `:8080`, the
dispatcher (`dispatcher/router.py:335`) resolves it (registry →
passthrough → prefetch → legacy `resolve_slot`, `proxy.py:58`) to a slot
upstream and then **forwards `/v1/chat/completions` verbatim to lemond's
port** (`router.py:535-850` / catch-all `lemonade_proxy.py:115`). lemond
sees an inference request for a not-yet-loaded model and **auto-loads it
itself, using the global config default** (rocm). hal0 never issued a
`/v1/load`, so it never had the chance to pass `llamacpp_backend`.

> Correction to investigation A: injecting `llamacpp_backend` into the
> `/v1/chat/completions` body is a dead end — lemond's chat endpoint does
> not accept that field. The fix must put an explicit, backend-aware
> `/v1/load` *in front of* the forward.

### The display lie

`/api/slots` enrichment (`slots.py:_lemonade_state_enrichment`, line 123)
lifts `backend_url` from `/v1/health.loaded[]` but never reports which
backend the child is actually running. The empirically-verified
`/v1/health` loaded-entry shape is:

```json
{ "model_name": "...", "backend_url": "http://127.0.0.1:<childport>/v1", ... }
```

There is **no `backend` field** in the health entry (investigation C's
Phase-1 assumption that `loaded_entry.get("backend")` exists is
unconfirmed and must not be relied on). The only trustworthy actual-backend
signal is the **child process binary path**: the child for `backend_url`'s
port runs either `.../rocm-stable/llama-server` or `.../vulkan/llama-server`.

### The control surface that half-exists

`POST /api/slots/{name}/backend` (`slots.py:957`) already exists and is
wired to a UI mutation (`useSlotBackend`, `useSlots.ts:327`). But it (a)
writes the **deprecated** `backend` field instead of `device`, (b) does
**not** validate against installed builds, and (c) does **not** reload —
`update_config` only rewrites the TOML (`manager.py:1334`), and the new
backend takes effect only on the *next* load.

## Decision

### Single source of truth

- **Declared backend** = the slot's `device` field (TOML), normalized
  through `device_to_backend()`. This is the single declared source of
  truth. The legacy `backend` field is a deprecated alias only.
- **Actual backend** = the build directory of the live child
  `llama-server` process, resolved by `resolve_actual_backend()` (new, in
  `providers/lemonade.py`): take the loaded entry's `backend_url`, extract
  its port, find the listening child PID, read its `/proc/<pid>/exe` (or
  `cmdline`), and classify the path: `…/vulkan/…` → `vulkan`,
  `…/rocm-stable/…` → `rocm`; CPU/FLM children classify by binary/recipe.
  Returns `None` when undeterminable (lemond down, model not loaded, race).

These two sources are deliberately different mechanisms (config file vs
live process) precisely so the dashboard can surface drift between intent
and reality.

### B1 — backend sticks on every load path

1. **Explicit load** already correct — no change beyond passing the
   resolved backend through (it does).
2. **Lazy load (the fix):** in `Dispatcher.dispatch`, when resolution
   lands on a *local slot* (`registry` slot, `legacy_slot:*`, or
   `passthrough` to a slot upstream) **and** that slot's model is not
   already present in lemond's `loaded[]`, perform a **pre-forward
   ensure-loaded** step through `SlotManager.load(slot_name)` *before*
   forwarding. Because `SlotManager.load` → `LemonadeProvider.load(cfg)`
   reads `cfg.device` and sends `llamacpp_backend`, the correct backend is
   guaranteed on the cold path. The forward then hits an already-correctly-
   loaded model.
   - This reuses the existing swap-window gate: `_check_slot_ready_for_dispatch`
     already raises `SlotLoading` (503 + `Retry-After`) for non-ready
     slots, so the natural implementation is: if the slot is OFFLINE/idle
     and its model isn't loaded, kick `SlotManager.load` (which is
     per-slot-locked and idempotent) and return `SlotLoading` so the
     client retries into the now-loading slot. No new concurrency surface.
3. **Defense in depth — also fix lemond's global default is NOT done.**
   We do not flip the global `llamacpp.backend`; per-model loads override
   it and other modalities (whispercpp=vulkan) rely on the per-recipe
   defaults. Backend stickiness is per-slot via the load body, never
   global.

### B2 — actual backend in the status payload

Extend `LemonadeProvider.status()` and `slots.py` enrichment to add a
single field. The status payload (per loaded slot) gains:

```jsonc
{
  "lemonade_state": "loaded",
  "backend_url": "http://127.0.0.1:14002/v1",
  "declared_backend": "vulkan",   // from device_to_backend(cfg.device)
  "actual_backend": "rocm",       // from resolve_actual_backend(); omitted if undeterminable
  "backend_mismatch": true        // computed: actual && declared && actual != declared
}
```

`declared_backend` is the normalized backend token (`rocm|vulkan|cpu|flm`),
not the `gpu-` device form, so the UI compares like-for-like.
`actual_backend` / `backend_mismatch` are **omitted** when the model is not
loaded or the backend can't be determined (never emit a misleading value).

### B3 — control endpoint (idempotent, validated, reloads)

Reuse the existing path `POST /api/slots/{name}/backend`, redefined:

- **Body:** `{ "backend": "rocm" | "vulkan" | "cpu" | "auto" }` (alias
  `device` accepted; `gpu-rocm`/`gpu-vulkan` normalized in). `"auto"`
  clears the per-slot preference (falls back to lemond global default).
- **Validation:** `rocm`/`vulkan` are rejected with `409
  backend.build_missing` if the corresponding build dir
  (`rocm-stable`/`vulkan`) is absent under
  `/var/lib/hal0/lemonade/bin/llamacpp/`. `cpu`/`auto` always valid;
  `flm`/`npu` rejected with `400 backend.not_selectable` (NPU is recipe-
  driven, not a llama.cpp backend).
- **Effect:** writes `device` to the TOML (via `update_config`, which also
  refreshes the mirrored `extra.backend`), then — if the slot is currently
  loaded — triggers `SlotManager.restart(name)` so the model reloads under
  the new backend. If not loaded, no reload (next load picks it up).
- **Idempotent:** if the requested backend already equals the declared
  device and (when loaded) the actual backend, the endpoint is a no-op
  reload-skip and returns `reloaded: false`.

### F1 — dashboard shows actual + mismatch warning

`slots.jsx` reads `actual_backend` / `backend_mismatch` and renders an
amber "≠ declared" badge plus a warning indicator + tooltip ("Declared
vulkan but running rocm — switch backend to reload"). Shown only when the
slot is loaded and `backend_mismatch` is true.

### F2 — per-slot backend selector

`slot-modals.jsx` EditSlotDrawer gets a read-only declared/actual strip, a
mismatch banner, and a ROCm/Vulkan/auto selector + Apply button wired to
`useSlotBackend()` with a pending state. Apply calls B3; on success it
invalidates the slots query so the new actual backend appears after reload.
The `cpu` and `npu` device slots disable the selector.

## Consequences

- **Positive:** declared backend is now authoritative on *every* load
  path; the dashboard can no longer lie about which backend is running;
  operators get a one-click, validated, reload-on-apply backend switch.
- **Cost:** the cold lazy-load path gains one extra `/v1/health` check and,
  on a true cold miss, a `SlotManager.load` + a client retry (the existing
  `SlotLoading` 503/Retry-After contract already handles this UX). Warm
  hits (model already loaded) pay only the health lookup, which is already
  cached for 0.5s (`client.py:_HEALTH_CACHE_TTL_S`).
- **Process introspection coupling:** `resolve_actual_backend()` reads
  `/proc/<pid>/exe`, coupling hal0 to running on the same host as lemond.
  This is already true (loopback-only daemon) and degrades gracefully
  (returns `None`, UI shows no actual badge) when introspection fails.
- **Risk:** a backend switch on a loaded slot incurs a model reload (VRAM
  churn, ~15-60s). The endpoint makes this explicit (`reloaded: true`) and
  is gated behind the writer scope, matching every other mutating slot
  route.
