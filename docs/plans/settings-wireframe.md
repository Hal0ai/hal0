# hal0 Settings Menu — Wireframe / IA

Design decisions:
1. **Web/app dashboard settings UI** (left-rail nav + content pane), not a TUI — but every control is backed by a real config key so this tree *is* the `hal0 config` / `/internal/config` schema. One source of truth, three surfaces (UI, CLI, API).
2. **Server-level vs deferred split is visual** — ⟳ = hot-applies, ⏻ = needs model reload/restart (from Lemonade's `/internal/set` split).
3. **Host-mutating tuning (Strix Halo kernel knobs) lives in a gated "Danger Zone" page** with an explicit Apply+Reboot flow.

Source tags on each control: `[L]`emonade / `[O]`DS / `[h]`al0-existing / `[L+O]` both.

---

```
┌──────────────────────────────────────────────────────────────────────────┐
│  hal0  ▸ Settings                              ● healthy   gfx1151  ⏻ admin │
├────────────────────┬───────────────────────────────────────────────────────┤
│ SERVER             │                                                         │
│   General          │   ← content pane renders selected page →                │
│   Security & Access│                                                         │
│   Network & Disc.  │   Legend:  ⟳ hot-applies   ⏻ needs reload/restart       │
│                    │            [●─] on  [─○] off   ▼ dropdown   ⛔ admin-only │
│ MODELS             │            ●ok ⚠warn ✗fail   🔒 host-mutating           │
│   Library & Down.  │                                                         │
│   Loaded Models    │                                                         │
│   Model Defaults   │                                                         │
│                    │                                                         │
│ INFERENCE          │                                                         │
│   Backend & GPU    │                                                         │
│   Hardware Tuning🔒 │                                                         │
│   Performance      │                                                         │
│   Memory Manager   │                                                         │
│                    │                                                         │
│ ROUTING (BRAIN)    │                                                         │
│   Mode & Fallback  │                                                         │
│   Providers        │                                                         │
│   Agent Profiles   │                                                         │
│                    │                                                         │
│ OBSERVABILITY      │                                                         │
│   Telemetry        │                                                         │
│   Health & Stats   │                                                         │
│   Logs             │                                                         │
│                    │                                                         │
│ DATA & MEMORY      │                                                         │
│   Honcho Memory    │                                                         │
│   Storage & Cache  │                                                         │
│   Offline Mode     │                                                         │
│                    │                                                         │
│ DIAGNOSTICS        │                                                         │
│   Doctor           │                                                         │
│   Support Bundle   │                                                         │
│   Updates & Versns │                                                         │
│                    │                                                         │
│ INTEGRATIONS       │                                                         │
│   API Compat       │                                                         │
│   Client Setup     │                                                         │
└────────────────────┴───────────────────────────────────────────────────────┘
```

### SERVER ▸ General
```
┌─ General ──────────────────────────────────────────────────────────────────┐
│  Device name      [ hal0 ...............]  ⟳   → hal0.local  [h] HAL0_DEVICE  │
│  Bind address     [ 127.0.0.1 ▼ ]        ⏻   [h] BIND_ADDRESS               │
│                     options: 127.0.0.1 (loopback) · 0.0.0.0 (LAN) · custom   │
│                     ⚠ 0.0.0.0 requires an API key (see Security)             │
│  API port         [ 8080 ]  ⏻   Admin/agent port [ 7710 ]  ⏻  [O] ports.json │
│  API base path    ( /v1 ) ( /api/v1 both )   ⟳   [L] multi-prefix support    │
│  Log level        [ info ▼ ] ⟳  trace·debug·info·warning·error·fatal  [L]    │
│  Global timeout   [ 600 ]s ⟳   Model-load wait [ 1200 ]s ⏻  [L] big FP4 load │
│  ─────────────────────────────────────────────────────────────────────────  │
│  Config file  ~/.hal0/hal0.env        ( Export )  ( Reset to defaults )      │
│               [L] /internal/config · /internal/config/defaults              │
└──────────────────────────────────────────────────────────────────────────────┘
```

### SERVER ▸ Security & Access  ⛔
```
┌─ Security & Access ─────────────────────────────────────────────────────────┐
│  Authentication   [●─] Require API key on /v1     ⏻   [L+O]                   │
│                     ⚠ currently DISABLED — /v1 open on 10.0.1.x              │
│  Client key       sk-hal0-••••••••4f2a   ( Reveal ) ( Rotate ) [L] HAL0_API_KEY│
│  Admin key        sk-hal0-••••••••9b13   ( Reveal ) ( Rotate ) [L] *_ADMIN_KEY │
│                     gates /internal/*, doctor, config writes                 │
│  Placeholder key  [●─] accept any non-empty key when auth off  [L+O] drop-in │
│  ─────────────────────────────────────────────────────────────────────────  │
│  Network exposure policy                        [O] network-exposure-policy  │
│   ┌────────────────┬───────────────┬───────────────┬─────────────────────┐  │
│   │ service        │ lan_exposure  │ auth_required │ status              │  │
│   │ hal0 /v1       │ [ none ▼ ]    │ [●─]          │ ● loopback          │  │
│   │ dashboard      │ [ none ▼ ]    │ [●─]          │ ● loopback          │  │
│   │ langfuse       │ [ none ▼ ]    │ [─○]          │ ● internal          │  │
│   └────────────────┴───────────────┴───────────────┴─────────────────────┘  │
│   CI contract test: ✗ fail build if /v1 gains a 0.0.0.0 port  [O]           │
│  ─────────────────────────────────────────────────────────────────────────  │
│  Image pinning    llama.cpp @ [ sha256:2f9c… ▼ ]  ⏻  [O] no :latest          │
│  Run as non-root  [●─]   Mem cap [ 96 ]G  CPU cap [ auto ]   [O]             │
│  gitleaks pre-commit  ● installed        SHA256-verify downloads [●─]  [O]   │
└──────────────────────────────────────────────────────────────────────────────┘
```

### SERVER ▸ Network & Discovery
```
┌─ Network & Discovery ───────────────────────────────────────────────────────┐
│  mDNS advertise   [●─] publish hal0.local        ⟳   [L+O] hal0-mdns         │
│    Poll interval  [ 30 ]s   SRV record only when LAN-bound  ● (loopback→off) │
│    Records:  hal0.local (A) · hal0-llama._http._tcp → 8080 (SRV)            │
│  Tailscale        [─○] host-net remote access    ⏻   [O]                     │
│    Auth key  [ tskey-•••• ]   Hostname [ hal0 ]   Tags [ tag:hal0 ]         │
│    ● not connected      ( Connect )                                          │
│  LAN firewall hint:  ufw allow from 10.0.1.0/24 to any port 8080   [O]       │
│  Connected clients (read-only)                                               │
│    ● thinMint 10.0.1.x   OpenWhispr · chat-ui     last seen 2m ago           │
└──────────────────────────────────────────────────────────────────────────────┘
```

### MODELS ▸ Library & Downloads
```
┌─ Library & Downloads ───────────────────────────────────────────────────────┐
│  Source  [ huggingface ▼ ]  Endpoint [ default ]        [L] --source        │
│  Pull    [ org/repo:Q4_K_M .....................]  ( Fetch variants )        │
│    ┌ variants (top 5 by popularity) ──────────────────────────[L] /variants ┐│
│    │ ○ Q4_K_M  4.1GB  ●suggested   ○ Q5_K_M 4.8GB   ○ Q8_0 8.5GB   + mmproj ││
│    └──────────────────────────────────────────────────────────────────────┘│
│  ─── Downloads (background jobs) ─────────────────────────[L] /v1/downloads ─│
│   Qwen3-Coder-30B-Q4   ███████░░░ 68%  4.2/6.1GB   ( pause )( cancel )       │
│   nomic-embed-v1.5     ● complete                                            │
│  ─── Extra models dir ───────────────────────────────────[L] extra_models_ ─│
│   [ /mnt/models .....................]  ● 7 GGUF found → extra.*   ( Scan )  │
│  ─── Catalog ────────────────────────────────────────[L+O] model-library.json│
│   id · family · quant · size · vram_req · ctx · specialty · sha256 · license │
│   [ + Register custom model ]   ( Import bundle )  ( Export )                │
└──────────────────────────────────────────────────────────────────────────────┘
```

### MODELS ▸ Loaded Models
```
┌─ Loaded Models ─────────────────────────────────────────[L] /v1/health ─────┐
│  Slots: LLM 2/2 · embed 1/1 · rerank 0/1        max_loaded_models [ per-type]│
│  ┌ model ───────────────┬ type ┬ dev ┬ ctx ┬ pin ┬ idle ┬ actions ────────┐ │
│  │ Qwen3-Coder-30B FP4  │ llm  │ROCm │32768│ [📌] │ 12s  │ (unload)(opts)  │ │
│  │ Llama-3-8B-GGUF Q4   │ llm  │ROCm │ 8192│ [ ] │ 4m   │ (unload)(opts)  │ │
│  │ nomic-embed-v1.5     │embed │ROCm │ 2048│ [ ] │ 1m   │ (unload)        │ │
│  └──────────────────────┴──────┴─────┴─────┴─────┴──────┴─────────────────┘ │
│  📌 pinned = never evicted (brain planner stays hot)   [L] /internal/pin     │
│  ( Load model… )   TTFT 0.28s · 47 tok/s  [L] /v1/stats                      │
└──────────────────────────────────────────────────────────────────────────────┘
```

### MODELS ▸ Model Defaults  (per-model overrides live in the opts modal)
```
┌─ Model Defaults ──────────────────────────────[L] recipe_options.json ──────┐
│  Default context   [ auto ▼ ]  ⏻  auto = fit VRAM/GTT from GGUF arch  [L]    │
│    presets:  8K · 16K · 32K · auto                                           │
│  Default quant     [ Q4_K_M ▼ ]   mark UD-Q4 distinct  [O]                   │
│  Per-arch defaults [●─] apply architecture_defaults.json  ⏻  [L]            │
│  Load-on-startup   [●─] warm default model before first request  [O]        │
│  ── Per-model opts modal (opens from Loaded/Catalog) ──                      │
│   ctx_size [____]  backend [rocm ▼]  llamacpp_args [__________]  ⏻           │
│   [●─] save_options → persist to recipe_options.json  [L]                    │
└──────────────────────────────────────────────────────────────────────────────┘
```

### INFERENCE ▸ Backend & GPU
```
┌─ Backend & GPU ─────────────────────────────────────────────────────────────┐
│  Engine        llama.cpp (llama-server)             [h]                       │
│  Backend       [ rocm ▼ ]  ⏻   rocm·vulkan·cpu     [L] recipe:backend        │
│  ROCm channel  [ stable ▼ ]  Build pin [ gfx1151 @ b8460 ▼ ] ⏻ [L] rocm_bin  │
│  gfx guard     ● system_info reports gfx1151        ⛔ refuse start if missing│
│                  [L+O] blocks all-'?' garbage-output failure mode            │
│  Image         hal0-llama.cpp-gfx1151-<sha>   ( verify archs )  [O]          │
│  ROCM_PATH     [ /opt/rocm ]   reuse host runtime if compatible  [L]         │
│  ── Detected hardware (read-only) ──────────────────[O] capability-profile ──│
│   AMD Strix Halo · gfx1151 · ROCm 6.x · 128GB unified · FP4 ✔               │
│   /dev/kfd ● · /dev/dri/renderD128 ● · video/render GID ●                    │
└──────────────────────────────────────────────────────────────────────────────┘
```

### INFERENCE ▸ Hardware Tuning  🔒 host-mutating, ⛔ admin
```
┌─ Hardware Tuning  🔒 (reboot required) ─────────────────[O] system-tuning ───┐
│  ⚠ These write host kernel/GRUB/modprobe/sysctl config. Review before apply. │
│                                              current │ recommended │ status  │
│  amdgpu gttsize                          [ 120000 ]  │  120000     │ ● set   │
│    ⓘ 120GB GPU GTT · needs BIOS UMA min (512MB-1GB)                          │
│  GRUB amd_iommu                          [ off ▼ ]   │  off        │ ● set   │
│    ⓘ +2-6% · iommu=pt is NOT equivalent                                      │
│  tuned profile                    [ accelerator-perf ]│ accel-perf │ ⚠ unset │
│    ⓘ +5-8% prompt processing                                                 │
│  ppfeaturemask                           [ 0xffffffff]│ 0xffffffff  │ ● set   │
│  amdgpu gpu_recovery                      [●─]         │  on         │ ● set   │
│  ttm pages_limit / page_pool             [ auto ]     │ 120G/60G    │ ⚠ part  │
│  vm.swappiness                           [ 10 ]       │  10         │ ● set   │
│  ─────────────────────────────────────────────────────────────────────────  │
│  BIOS UMA Frame Buffer: minimum   ⚠ verify manually in firmware              │
│  ( Preview diff )   ( Apply + update-initramfs )   ( Schedule reboot )       │
└──────────────────────────────────────────────────────────────────────────────┘
```

### INFERENCE ▸ Performance
```
┌─ Performance ───────────────────────────────────────────────────────────────┐
│  Parallel slots   [- 8 +] ⏻   KV ≈ slots × ctx   tier-hint: Strix→8-12 [O]  │
│  GPU layers       [ 99 ]  ⏻   all-on-GPU                       [O]           │
│  Enforce eager    [●─] managed default (shared-mem APU)  ⏻    [L] APU-safe   │
│  Prefix caching   [●─]                                         [L]           │
│  Context strategy  tight default (8-16K), widen per-request    [L+O]         │
│  Speculative (MTP) [─○] {method:mtp, num_tokens:1}  ⏻          [L] structured│
│  ROCm kernel cache [●─] persist between runs                   [L]           │
│  ⓘ first FP4/ROCmFP4 cold load ~12min (kernel JIT) — see Model-load wait     │
└──────────────────────────────────────────────────────────────────────────────┘
```

### INFERENCE ▸ Memory Manager
```
┌─ Memory Manager ───────────────────────────────────────[L] multi-model ─────┐
│  Max loaded / type   LLM [2]  embed [1]  rerank [1]   (-1 = ∞)  ⟳            │
│  Auto-evict          [●─] on VRAM pressure    ⟳                              │
│    Threshold         [────●──] 90%   polls rocm-smi/sysfs                    │
│  Idle degradation    [●─] two-stage                                          │
│    Soft (clear KV)   [ 60 ]s      Hard (unload weights) [ 300 ]s   ⟳         │
│  Eviction weight     score = idle/(load×weight) · protect slow experts       │
│  Orphan protection   [●─] process-group kill on child timeout  (VRAM leak)   │
└──────────────────────────────────────────────────────────────────────────────┘
```

### ROUTING (BRAIN) ▸ Mode & Fallback
```
┌─ Mode & Fallback ──────────────────────────────────[L+O] HAL0_MODE ─────────┐
│  Mode   ( ● local )  ( ○ cloud )  ( ○ hybrid )   ⏻                           │
│  Fallback ladder (hybrid)                          [O] hybrid.yaml           │
│    primary  local(llama-server)  →  on timeout/error  →  [ cloud ▼ ]         │
│    retries [ 2 ]   strategy [ simple-shuffle ▼ ]                             │
│    'default' alias pinned → local (never falls to cloud accidentally)        │
│  Unified namespace  models shown as <provider>.<model> in /v1/models  [L]    │
│  Per-mode required keys checked at boot (fail-fast)   [O]                     │
└──────────────────────────────────────────────────────────────────────────────┘
```

### ROUTING (BRAIN) ▸ Providers
```
┌─ Providers ─────────────────────────────────────────────────────────────────┐
│  ┌ name ──────┬ base_url ───────────────────┬ path ────┬ key ──┬ status ───┐ │
│  │ local      │ http://127.0.0.1:8080       │ /v1      │  —    │ ● ready   │ │
│  │ cloud      │ https://api.anthropic.com   │ /v1      │ ••••  │ ● ready   │ │
│  │ minimax    │ https://api.minimax.io      │ /anthro. │ ••••  │ ● ready   │ │
│  └────────────┴─────────────────────────────┴──────────┴───────┴───────────┘ │
│  [ + Add provider ]    per-provider: path-normalize · enable_thinking:false  │
│                        · timeout 900s · error classification  [L+O]          │
└──────────────────────────────────────────────────────────────────────────────┘
```

### ROUTING (BRAIN) ▸ Agent Profiles
```
┌─ Agent Profiles ──────────────────────────────[O] hal0.profiles.v1 ─────────┐
│  ┌ profile ─────────┬ model ───────────┬ fallback ──┬ tools ──────┬ valid ─┐ │
│  │ code-assistant   │ Qwen3-Coder-30B  │ kimi-k2    │ read,exec…  │ ● 2/2  │ │
│  │ research         │ Qwen3-30B-A3B    │ claude     │ web,read    │ ● 2/2  │ │
│  │ writing          │ Qwen3-30B-A3B    │ kimi-k2    │ read,edit   │ ⚠ 1/2  │ │
│  └──────────────────┴──────────────────┴────────────┴─────────────┴────────┘ │
│  Edit profile ▸  system_prompt · tools allowlist · routing_rules(regex)      │
│                  exec safe/dangerous cmd lists · validation prompts          │
│  labels drive routing: tool-calling·vision·reasoning·mtp·coding·embed  [L+O] │
└──────────────────────────────────────────────────────────────────────────────┘
```

### OBSERVABILITY ▸ Telemetry
```
┌─ Telemetry ─────────────────────────────────────────[L] + langfuse[h] ──────┐
│  Enabled          [●─]   runtime toggle (no restart)  ⟳                       │
│  Exporter         OTLP → [ http://localhost:4318/v1/traces ]  langfuse       │
│  Semantics        [●─] openinference.*   [●─] gen_ai.*  (both in one payload) │
│  Redaction        inputs [─○]  outputs [─○]  thinking [●─]  (keep metadata)  │
│  Computed metrics [●─] TTFT + tok/s at router layer                          │
│  Batch            size [100]  timeout [1.0]s  retries [0]   ( Flush now )     │
└──────────────────────────────────────────────────────────────────────────────┘
```

### OBSERVABILITY ▸ Health & Stats · Logs
```
┌─ Health & Stats ──────────────────────────[L] /v1/system-stats,/metrics ─────┐
│  GPU  [██████░░] 74%   VRAM 96/128 GB   CPU 22%   NPU —                       │
│  Endpoints:  /live ● · /v1/health ● · /metrics ● (Prometheus, root-only)     │
│  Last request  TTFT 0.28s · 47 tok/s · in 1.2k · out 340   [L] /v1/stats     │
├─ Logs ──────────────────────────────────────────────[L] WS /logs/stream ─────┤
│  level [ info ▼ ]  service [ all ▼ ]   [ tail ▐]   ( download )              │
│  12:04:11 info  slot 0 model warmed  · 12:04:12 info  request 47tok/s …      │
└──────────────────────────────────────────────────────────────────────────────┘
```

### DATA & MEMORY ▸ Honcho · Storage · Offline
```
┌─ Honcho Memory ────────────────────────────────────────[h] ─────────────────┐
│  Endpoint [ http://127.0.0.1:8000 ]  peer [ alexander ]  ● connected         │
│  Workspace / sessions dirs   data/hal0/{workspace,sessions}   [O]            │
├─ Storage & Cache ────────────────────────────────────[L] models_dir ─────────┤
│  models_dir [ auto (HF_HUB_CACHE) ▼ ]   Data dir [ ~/.hal0 ]                 │
│  Disk: models 41GB · cache 8GB · logs 200MB   ( cold-storage idle models )[O]│
├─ Offline Mode ───────────────────────────────────────[O] --offline ──────────┤
│  [─○] Air-gapped   drops telemetry/update pings · bundled LLM+embed GGUF     │
│  web-search → local Qdrant RAG fallback                                      │
└──────────────────────────────────────────────────────────────────────────────┘
```

### DIAGNOSTICS ▸ Doctor · Support Bundle · Updates
```
┌─ Doctor ────────────────────────────────────────────[O] hal0 doctor ─────────┐
│  ( Run doctor )   last: 12:01  exit 0                                         │
│  ● Inference   llama-server responding · model loaded                        │
│  ● GPU         gfx1151 · ROCm libs present · /dev/kfd ok                     │
│  ⚠ Tuning      tuned profile not set → ( apply accelerator-performance )     │
│  ● Observ.     langfuse reachable · honcho reachable                         │
│  diagnoses use stable IDs (HAL0-GFX-TARGET-UNSUPPORTED…) + autofix hints     │
├─ Support Bundle ─────────────────────────────────────[O] ────────────────────┤
│  ( Generate )  redacted · command-status TSV · rocm-smi/rocminfo captures    │
├─ Updates & Versions ─────────────────────────────[L+O] KNOWN-GOOD-VERSIONS ──┤
│  hal0 v0.1.0 (sha …)   llama.cpp b8460   ROCm 6.x   ● known-good              │
│  ( check model updates )  digest-pinned refs · no silent bumps               │
└──────────────────────────────────────────────────────────────────────────────┘
```

### INTEGRATIONS ▸ API Compat · Client Setup
```
┌─ API Compatibility ─────────────────────────────────────────────────────────┐
│  OpenAI /v1        [●─] chat·completions·embeddings·models   [h]             │
│  Anthropic /v1/messages [●─]  → unlocks Claude Code          [L+O]           │
│  Ollama /api/*     [─○] :11434 (tags·chat·ps·embed) for Open WebUI  [L]      │
│  Realtime WS /v1/realtime [─○]  streaming STT → OpenWhispr    [L] ★          │
│  Rerank /v1/reranking [●─]   Tokenize /v1/tokenize [●─]       [L]            │
│  /v1/models: text-only (hide image) · labels · max_ctx · show_all  [L+O]     │
├─ Client Setup (copy-paste) ──────────────────────────────────────────────────┤
│  base_url http://hal0.local:8080/v1   key "not-needed"       [L+O]           │
│  ( hal0 launch claude )  ( setup Continue )  ( setup Cursor )  ( Open WebUI ) │
└──────────────────────────────────────────────────────────────────────────────┘
```

---

## Design rationale
- **Nav order = urgency + frequency.** Security is high (your `/v1` is open today); Hardware Tuning is gated deep (rare + reboot-y).
- **⟳/⏻ badges are load-bearing** — they tell the user (and the API) which writes are hot vs need reload, from Lemonade's server-level/deferred split. That's what makes a live settings UI safe over a running inference server.
- **One schema.** This tree = `hal0 config` keys = `/internal/config` payload = `.env` + `recipe_options.json`. UI is just a renderer; CLI/API come free (ODS "single source of truth" contract).
- **★ Realtime WS** flagged: the one item that directly upgrades the existing OpenWhispr setup (streaming vs per-chunk STT).

## MVP (v0.1) subset vs Full
- **v0.1 must-have pages:** General · Security & Access · Backend & GPU · Hardware Tuning · Loaded Models · Library & Downloads · Doctor · Health & Stats.
- **Defer to v0.2+:** Agent Profiles · Providers/fallback · Offline Mode · Tailscale · Support Bundle · full Integrations.
