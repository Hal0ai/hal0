/**
 * mock-data — Playwright-side mirror of `ui/src/dash/data.jsx` HAL0_DATA.
 *
 * Why a duplicate? `data.jsx` is a Vite-transformed module that publishes
 * HAL0_DATA onto `window` and isn't importable directly under plain
 * Playwright/Node. Rather than wire a Vite shim into the test runner we
 * keep a shape-faithful subset here. Slice contract: every top-level key
 * that an apiMock route returns MUST stay structurally equivalent to its
 * HAL0_DATA twin until Phase B1 swaps the in-bundle mock for real API
 * responses.
 *
 * Phase B (current): the React dashboard is HAL0_DATA-driven — no /api/*
 * fetch happens until Phase B1 wires hooks. These constants exist so:
 *   1. apiMock fixture has stable defaults to fulfil with when B1 lands.
 *   2. Specs that simulate B1 endpoints can override per-test without
 *      re-deriving the shape from data.jsx.
 */

export const MOCK_DATA = {
  // Flat /api/hardware response shape (mirrors hal0.api.routes.hardware
  // _flatten_for_ui). useHardware.normalizeHardware reads these flat keys;
  // a few legacy display keys (name/ram) are kept as fallbacks for any
  // consumer still reading the pre-B1 shape.
  host: {
    hostname: 'hal0',
    uptime_s: 123_456, // → "1d 10:17"
    kernel: 'Linux version 7.0.6-2-pve',
    distro: 'Debian GNU/Linux 13 (trixie)',
    platform: 'lxc',
    platform_label: 'Linux container (LXC)',
    cpu_model: 'AMD RYZEN AI MAX+ 395 w/ Radeon 8060S',
    cpu_cores: 16,
    cpu_threads: 16,
    ram_mb: 96_000,
    ram_total_mb: 96_000,
    ram_available_mb: 94_577,
    unified_memory_mb: 131_072,
    gtt_total_mb: 107_520,
    memory_kind: 'unified',
    gpu_name: 'AMD Radeon 8060S (Strix Halo)',
    gpu_vendor: 'amd',
    gpus: [
      {
        vendor: 'amd',
        name: 'AMD Radeon 8060S (Strix Halo)',
        vram_mb: 107_520,
        driver: 'amdgpu',
        compute_capable: false,
        vulkan_capable: true,
      },
    ],
    npu: { present: true, vendor: 'amd', name: 'AMD NPU (XDNA)', driver: 'amdxdna' },
    npu_present: true,
    npu_name: 'AMD NPU (XDNA)',
    // Legacy display-shape fallbacks (pre-B1 consumers).
    name: 'hal0',
    ram: { total: 93.8, free: 92.4, used: 1.4 },
  },

  /** Subset of slots the v3 dash seeds — enough to drive `/slots` group
   *  rendering (chat / embed / voice / img + NPU rollup).
   *
   *  `mem_mb` is the BE-METRICS contract field (real per-slot resident
   *  model + KV memory) the memory-map now attributes per slot; `type`
   *  + `group` drive grouped rendering + the endpoint-widget modality
   *  breakdown. Kept on every mock slot so apiMock-driven specs exercise
   *  the same code paths as the in-bundle seed. */
  slots: [
    {
      name: 'primary', type: 'llm', device: 'gpu-rocm',
      device_class: 'gpu', backend: 'rocm',
      model: 'qwen3.6-27b-mtp-q4_k_m', model_id: 'qwen3.6-27b-mtp',
      group: 'chat', state: 'serving', port: 8092, isDefault: true,
      runtime: 'container', profile: 'rocm',
      container_status: 'running', container_health: true,
      mem_mb: 18_400,
      metrics: { toks: 45, ttft: 220, ctx: 8192, kv: null, mem: 18.8 },
    },
    {
      name: 'agent', type: 'llm', device: 'npu',
      device_class: 'npu', backend: null,
      model: 'gemma3:1b', model_id: 'gemma3-1b-npu',
      group: 'npu', state: 'ready', port: 8093, isDefault: true,
      runtime: 'container', profile: 'flm',
      container_status: 'running', container_health: true,
      mem_mb: 1_100,
      metrics: { toks: 40, ttft: 280, ctx: 4096, kv: 66, mem: 1.0 },
    },
    {
      name: 'coder', type: 'llm', device: 'gpu-rocm',
      device_class: 'gpu', backend: 'rocm',
      model: 'qwen3-coder-30b-a3b', model_id: 'qwen3-coder-30b',
      group: 'chat', state: 'idle', port: 8094,
      runtime: 'container', profile: 'rocm',
      container_status: 'running', container_health: true,
      mem_mb: 17_900,
      metrics: { toks: 0, ttft: null, ctx: 32768, kv: null, mem: 18.6 },
    },
    {
      name: 'embed', type: 'embedding', device: 'gpu-rocm',
      device_class: 'gpu', backend: 'rocm',
      model: 'nomic-embed-text-v1.5', model_id: 'nomic-v1.5',
      group: 'embed', state: 'ready', port: 8095, isDefault: true,
      runtime: 'container', profile: 'rocm',
      container_status: 'running', container_health: true,
      mem_mb: 540,
      metrics: { rpm: 124, lat: 18, dim: 768, mem: 0.35 },
    },
    // Container runtime slot — added for #657 container-card coverage.
    // Models the primary chat slot running via ContainerProvider (podman
    // systemd unit with an ROCmFP4 image). State mirrors what
    // _container_state_enrichment() returns: container_status=running,
    // container_health=true → slot state "ready".
    {
      name: 'primary-container', type: 'llm', device: 'gpu-rocm',
      device_class: 'gpu', backend: 'rocm',
      model: 'qwen3.6-35b-a3b-q4_k_m', model_id: 'qwen3.6-35b-a3b',
      group: 'chat', state: 'ready', port: 8096,
      runtime: 'container',
      profile: 'rocm-mtp',
      image: 'ghcr.io/hal0ai/amd-strix-halo-toolboxes:rocm-7.2.4-rocmfp4-server',
      image_status: 'present',
      container_status: 'running',
      container_health: true,
      mem_mb: 22_400,
      bench_toks_per_sec: 52.8,
    },
    // C7 spec target: GPU container slot with a profile that has device_class=gpu.
    // Uses rocm-mtp which is in the mock profiles list.
    {
      name: 'chat', type: 'llm', device: 'gpu-rocm',
      device_class: 'gpu', backend: 'rocm',
      model: 'qwen3.6-35b-a3b-q4_k_m', model_id: 'qwen3.6-35b-a3b',
      group: 'chat', state: 'ready', port: 8099,
      runtime: 'container',
      profile: 'rocm-mtp',
      image: 'ghcr.io/hal0ai/amd-strix-halo-toolboxes:rocm-7.2.4-rocmfp4-server',
      image_status: 'present',
      container_status: 'running',
      container_health: true,
      mem_mb: 22_400,
      bench_toks_per_sec: 52.8,
    },
    // C7 spec target: TTS container slot with the tts profile (device_class=cpu — fixed text).
    {
      name: 'tts', type: 'tts', device: 'cpu',
      device_class: 'cpu', backend: null,
      model: 'kokoro-v1', model_id: 'kokoro-v1',
      group: 'voice', state: 'ready', port: 8100,
      runtime: 'container',
      profile: 'tts',
      image: 'ghcr.io/hal0ai/hal0-toolbox-kokoro:v1',
      image_status: 'present',
      container_status: 'running',
      container_health: true,
      mem_mb: 800,
    },
    // Container-runtime NPU slot (Phase A — npu trio toggles via TOML config).
    // asr=true, embed=false: exercises split-toggle state in npu-container spec.
    {
      name: 'npu', type: 'llm', device: 'npu',
      device_class: 'npu', backend: null,
      model: 'qwen3-0.6b-npu', model_id: 'qwen3-0.6b-npu',
      group: 'npu', state: 'ready', port: 8098,
      runtime: 'container',
      profile: 'flm',
      image: 'ghcr.io/hal0ai/amd-strix-halo-toolboxes:flm-npu-server',
      image_status: 'present',
      container_status: 'running',
      container_health: true,
      mem_mb: 1_200,
      npu: { asr: true, embed: false },
    },
    // Container slot in starting state (health probe not yet passing).
    {
      name: 'coder-container', type: 'llm', device: 'gpu-rocm',
      device_class: 'gpu', backend: 'rocm',
      model: 'qwen3-coder-30b-a3b', model_id: 'qwen3-coder-30b',
      group: 'chat', state: 'starting', port: 8097,
      runtime: 'container',
      profile: 'rocm-mtp',
      image: 'ghcr.io/hal0ai/amd-strix-halo-toolboxes:rocm-7.2.4-rocmfp4-server',
      image_status: 'present',
      container_status: 'starting',
      container_health: false,
      mem_mb: 0,
    },
  ],

  /** Subset of /api/models rows the swap popover + create-slot modal
   *  now consume (PR fix(dash): slot swap popover reads live /api/models).
   *  Shape matches the registry serializer: capabilities + backends drive
   *  the JSX-side normalizer's type + device derivation. */
  models: [
    {
      id: 'qwen3.6-27b-q5kxl',
      name: 'Qwen3.6-27B UD-Q5_K_XL',
      path: '/mnt/ai-models/qwen3.6-27b-q5kxl/Qwen3.6-27B-UD-Q5_K_XL.gguf',
      size_bytes: 19_000_000_000,
      capabilities: ['chat'],
      backends: ['vulkan', 'rocm', 'cpu'],
      hf_repo: 'unsloth/Qwen3.6-27B-GGUF',
      installed: true,
      ns: 'pulled',
    },
    {
      id: 'qwen3-coder-next-q4kxl',
      name: 'Qwen3-Coder-Next UD-Q4_K_XL',
      path: '/mnt/ai-models/qwen3-coder-next-q4kxl/Qwen3-Coder-Next-UD-Q4_K_XL.gguf',
      size_bytes: 19_000_000_000,
      capabilities: ['chat', 'coding'],
      backends: ['vulkan', 'rocm', 'cpu'],
      hf_repo: 'unsloth/Qwen3-Coder-Next-GGUF',
      installed: true,
      ns: 'pulled',
    },
    {
      id: 'nomic-embed-text-v1.5-q8',
      name: 'nomic-embed-text-v1.5 Q8',
      path: '/mnt/ai-models/nomic-embed-text-v1.5-q8/nomic-embed-text-v1.5.Q8_0.gguf',
      size_bytes: 350_000_000,
      capabilities: ['embed'],
      backends: ['vulkan', 'rocm', 'cpu'],
      hf_repo: 'nomic-ai/nomic-embed-text-v1.5-GGUF',
      installed: true,
      ns: 'blessed',
    },
    {
      id: 'bge-reranker-v2-m3-q4_k_m',
      name: 'BGE Reranker v2 M3 (Q4_K_M)',
      path: '/mnt/ai-models/local/bge-reranker-v2-m3-q4_k_m/bge-reranker-v2-m3-Q4_K_M.gguf',
      size_bytes: 438_376_864,
      capabilities: ['rerank'],
      backends: ['vulkan', 'rocm', 'cpu'],
      hf_repo: 'gpustack/bge-reranker-v2-m3-GGUF',
      installed: true,
      ns: 'pulled',
    },
  ],

  backends: [
    { id: 'llamacpp:rocm', name: 'llamacpp:rocm', ver: 'v1.0 (b9253)', state: 'installed', recommended: true },
    { id: 'llamacpp:vulkan', name: 'llamacpp:vulkan', ver: 'v1.0 (b9253)', state: 'installed' },
    { id: 'flm:npu', name: 'flm:npu', ver: 'v0.9.42 (deb)', state: 'installed', recommended: true, note: 'manual deb' },
  ],

  approvals: [] as any[],

  profiles: [
    {
      name: 'rocm',
      image: 'ghcr.io/hal0ai/amd-strix-halo-toolboxes:rocm-7.2.4-rocmfp4-server',
      flags: '--flash-attn on -ngl 999',
      mtp: false,
      resolved_flags: '--flash-attn on -ngl 999',
      device_class: 'gpu',
      backend: 'rocm',
      seed: true,
    },
    {
      name: 'rocm-mtp',
      image: 'ghcr.io/hal0ai/amd-strix-halo-toolboxes:rocm-7.2.4-rocmfp4-server',
      flags: '--flash-attn on -ngl 999',
      mtp: true,
      resolved_flags: '--flash-attn on -ngl 999 --draft-model /mnt/ai-models/mtp/llama-3b.gguf',
      device_class: 'gpu',
      backend: 'rocm',
      seed: true,
    },
    {
      name: 'vulkan',
      image: 'ghcr.io/hal0ai/amd-strix-halo-toolboxes:vulkan-radv-server',
      flags: '--flash-attn on -ngl 999',
      mtp: false,
      resolved_flags: '--flash-attn on -ngl 999',
      device_class: 'gpu',
      backend: 'vulkan',
      seed: true,
    },
    // Phase A seed — NPU container slot (FLM toolbox).
    {
      name: 'flm',
      image: 'ghcr.io/hal0ai/hal0-toolbox-flm:v1',
      flags: '',
      mtp: false,
      resolved_flags: '',
      device_class: 'npu',
      backend: null,
      seed: true,
    },
    // Phase B5 seed — TTS container slot (kokoro-onnx, CPU-only).
    {
      name: 'tts',
      image: 'ghcr.io/hal0ai/hal0-toolbox-kokoro:v1',
      flags: '--model_path /mnt/ai-models/local/kokoro-v1/kokoro-onnx',
      mtp: false,
      resolved_flags: '--model_path /mnt/ai-models/local/kokoro-v1/kokoro-onnx',
      device_class: 'cpu',
      backend: null,
      seed: true,
    },
    // ComfyUI image-generation seed (Phase D).
    {
      name: 'comfyui',
      image: 'ghcr.io/hal0ai/hal0-toolbox-comfyui:v1',
      flags: '',
      mtp: false,
      resolved_flags: '',
      device_class: 'img',
      backend: null,
      seed: true,
    },
  ],
}

export type MockData = typeof MOCK_DATA
