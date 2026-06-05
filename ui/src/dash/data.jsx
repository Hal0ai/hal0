// hal0 dashboard — mock data
// Sourced from the brief's wireframes + model strings from registry references.

const HAL0_DATA = {
  // Flat /api/hardware response shape (mirrors hal0.api.routes.hardware
  // _flatten_for_ui). useHardware.normalizeHardware reads these flat keys;
  // the legacy display keys (name/uptime/cpu/cores/gpu/ram) are kept so
  // direct HAL0_DATA.host.* readers (chrome.jsx, mcp-main.jsx, flow-modals,
  // firstrun) keep working without a hook.
  host: {
    hostname: "hal0",
    uptime_s: 1_216_771, // → "14d 02:00"
    kernel: "Linux version 7.0.6-2-pve",
    distro: "Debian GNU/Linux 13 (trixie)",
    platform: "lxc",
    platform_label: "Linux container (LXC)",
    cpu_model: "AMD Ryzen AI Max+ PRO 395",
    cpu_cores: 16,
    cpu_threads: 32,
    ram_mb: 131072,
    ram_total_mb: 131072,
    ram_available_mb: 75776,
    unified_memory_mb: 131072,
    gtt_total_mb: 81920,
    memory_kind: "unified", // Strix Halo UMA — drives memory-map "GPU pool (GTT)" label

    gpu_name: "AMD Radeon 8060S (gfx1151, Strix Halo)",
    gpu_vendor: "amd",
    gpus: [
      {
        vendor: "amd",
        name: "AMD Radeon 8060S (gfx1151, Strix Halo)",
        vram_mb: 81920,
        driver: "amdgpu",
        compute_capable: true,
        vulkan_capable: true,
      },
    ],
    npu: { present: true, vendor: "amd", name: "AMD NPU (XDNA2)", driver: "amdxdna" },
    npu_present: true,
    npu_name: "AMD NPU (XDNA2)",
    // Legacy display-shape keys (direct HAL0_DATA.host.* readers).
    name: "hal0",
    uptime: "14d 02:11",
    cpu: "AMD Ryzen AI Max+ PRO 395",
    cores: "16c · 32t",
    gpu: "AMD Radeon 8060S (gfx1151, Strix Halo)",
    ram: { total: 128, free: 74, used: 54 }, // GB
  },

  lemond: {
    status: "up",
    version: "v10.6.0",
    loaded: 3,
    budget: 4,
    throughput: 12.4, // MB/s
    queued: 0,
    coresident: true,
  },

  slots: [
    {
      // Synthetic composite /v1 upstream — surfaced by useEndpoints() in the
      // sidebar Runtime widget (hal0 row) and filtered OUT of the slot grid by
      // useSlots(). Mirrors slots.py → _synthesize_slots_from_upstreams.
      name: "hal0",
      _synthetic: true,
      _synthetic_reason: "Composite /v1 endpoint that fronts every chat model — not a lifecycle slot.",
      status: "serving",
      url: "http://127.0.0.1:8080/v1",
      advertised_models: 2,
    },
    {
      name: "primary",
      type: "llm",
      device: "gpu-rocm",
      model: "qwen3.6-27b-mtp-q4_k_m",
      model_id: "qwen3.6-27b-mtp",
      modelLong: "unsloth/Qwen3.6-27B-A3B-MTP-GGUF",
      group: "chat",
      state: "serving",
      isDefault: true,
      port: 8092,
      pid: 28471,
      metrics: { toks: 45, ttft: 220, ctx: 8192, kv: null, mem: 18.8 },
      spark: [3, 5, 7, 6, 8, 9, 10, 8, 9, 11, 12, 9, 10, 11, 13, 10],
    },
    {
      name: "agent",
      type: "llm",
      device: "npu",
      model: "gemma3:1b",
      model_id: "gemma3-1b-npu",
      modelLong: "google/gemma-3-1b-it-flm",
      group: "npu",
      state: "ready",
      isDefault: true,
      coresident: true,
      port: 8093,
      pid: 28482,
      metrics: { toks: 40, ttft: 280, ctx: 4096, kv: 66, mem: 1.0 },
      spark: [2, 3, 4, 3, 5, 4, 6, 5, 4, 6, 5, 7, 6, 5, 4, 5],
    },
    {
      name: "coder",
      type: "llm",
      device: "gpu-rocm",
      model: "qwen3-coder-30b-a3b",
      model_id: "qwen3-coder-30b",
      modelLong: "unsloth/Qwen3-Coder-30B-A3B-Instruct-GGUF",
      group: "chat",
      state: "idle",
      port: 8094,
      pid: 28491,
      metrics: { toks: 0, ttft: null, ctx: 32768, kv: null, mem: 18.6 },
      spark: [0, 0, 0, 1, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0],
    },
    {
      name: "embed",
      type: "embedding",
      device: "gpu-rocm",
      model: "nomic-embed-text-v1.5",
      model_id: "nomic-v1.5",
      modelLong: "nomic-ai/nomic-embed-text-v1.5-GGUF",
      group: "embed",
      state: "ready",
      isDefault: true,
      port: 8095,
      pid: 28498,
      metrics: { rpm: 124, lat: 18, dim: 768, mem: 0.35 },
      spark: [4, 5, 5, 6, 5, 7, 8, 6, 7, 8, 9, 7, 8, 9, 8, 7],
    },
    {
      name: "rerank",
      type: "reranking",
      device: "gpu-rocm",
      model: "bge-reranker-v2-m3",
      model_id: "bge-reranker-v2",
      modelLong: "BAAI/bge-reranker-v2-m3-q4_k_m",
      group: "embed",
      state: "idle",
      isDefault: true,
      port: 8096,
      pid: 28502,
      metrics: { rpm: 22, lat: 32, maxDocs: 200, mem: 0.4 },
      spark: [1, 0, 2, 1, 1, 0, 1, 2, 1, 0, 1, 1, 2, 1, 0, 1],
    },
    {
      name: "stt-npu",
      type: "transcription",
      device: "npu",
      model: "whisper-v3-turbo",
      model_id: "whisper-v3-turbo-npu",
      modelLong: "openai/whisper-large-v3-turbo-flm",
      group: "npu",
      state: "ready",
      isDefault: true,
      coresident: true,
      port: 8093,
      pid: 28482,
      metrics: { rpm: 8, xrt: 0.18, precision: "Q4_K_M", mem: 0.4 },
    },
    {
      name: "embed-npu",
      type: "embedding",
      device: "npu",
      model: "embed-gemma-300m",
      model_id: "embed-gemma-300m-npu",
      modelLong: "google/embed-gemma-300m-flm",
      group: "npu",
      state: "ready",
      coresident: true,
      port: 8093,
      pid: 28482,
      metrics: { rpm: 0, lat: null, dim: 768, mem: 0.35 },
    },
    {
      name: "tts",
      type: "tts",
      device: "cpu",
      model: "kokoro-v1",
      model_id: "kokoro-v1",
      modelLong: "hexgrad/Kokoro-82M",
      group: "voice",
      state: "ready",
      isDefault: true,
      cpuOnly: true,
      port: 8097,
      pid: 28510,
      metrics: { rpm: 4, secs: 47, voice: "af_heart", mem: 0.4 },
    },
    {
      name: "img",
      type: "image",
      device: "gpu-rocm",
      model: "sd-turbo",
      model_id: "sd-turbo",
      modelLong: "stabilityai/sd-turbo",
      group: "img",
      state: "idle",
      isDefault: true,
      port: 8098,
      pid: 28518,
      metrics: { rpm: 2, avg: 4.1, res: "512×512", mem: 1.2 },
    },
    {
      name: "warming-demo",
      type: "llm",
      device: "gpu-rocm",
      model: "qwen3-4b",
      model_id: "qwen3-4b",
      group: "chat",
      state: "warming",
      lemonade_state: "loading",
      port: 8099,
      metrics: { toks: 0, ttft: null, ctx: 8192, kv: null, mem: 4.0 },
    },
  ],

  bundles: [
    {
      id: "lite",
      name: "Lite",
      ram: 16,
      sizeGB: 1.2,
      desc: "Chat only — a small LLM on CPU/GPU.",
      contents: ["llama-3.2-1b-instruct", "—", "—", "—", "—", "—", "—"],
      includes: [
        { label: "chat (1.2B params)", active: true },
        { label: "embed", active: false },
        { label: "voice", active: false },
        { label: "image", active: false },
      ],
    },
    {
      id: "default",
      name: "Default",
      ram: 32,
      sizeGB: 8.4,
      desc: "Mainstream chat + embed + transcription + TTS.",
      includes: [
        { label: "chat (qwen3.5-9b)", active: true },
        { label: "embed (nomic-v1.5)", active: true },
        { label: "voice (whisper-base + kokoro)", active: true },
        { label: "image", active: false },
      ],
    },
    {
      id: "pro",
      name: "Pro",
      ram: 64,
      sizeGB: 38,
      desc: "Chat + coder + rerank + full A/V + image.",
      includes: [
        { label: "chat + coder (qwen3.6-27b, qwen3-coder-30b)", active: true },
        { label: "embed + rerank", active: true },
        { label: "voice", active: true },
        { label: "image (sd-turbo)", active: true },
      ],
    },
    {
      id: "max",
      name: "Max",
      ram: 100,
      sizeGB: 75,
      desc: "Pro + NPU trio + bigger models.",
      recommended: true,
      includes: [
        { label: "chat + coder + NPU agent", active: true },
        { label: "embed + rerank + embed-npu", active: true },
        { label: "voice (whisper-large + kokoro + stt-npu)", active: true },
        { label: "image (flux-2-klein)", active: true },
      ],
    },
  ],

  bundleDetails: {
    pro: {
      models: [
        { slot: "primary",  model: "Qwen3.6-27B-MTP-Q4_K_M",       size: "18.8 GB", tag: "chat default" },
        { slot: "coder",    model: "Qwen3-Coder-30B-A3B-Q4_K_M",   size: "18.6 GB", tag: "chat coder" },
        { slot: "embed",    model: "nomic-embed-text-v1.5",        size: "350 MB",  tag: "embed default" },
        { slot: "rerank",   model: "bge-reranker-v2-m3-q4_k_m",    size: "400 MB",  tag: "rerank default" },
        { slot: "stt",      model: "whisper-base",                  size: "150 MB",  tag: "stt default" },
        { slot: "tts",      model: "kokoro-v1",                     size: "400 MB",  tag: "tts default · cpu" },
        { slot: "img",      model: "sd-turbo",                       size: "1.2 GB",  tag: "img default" },
      ],
      npu: [
        { slot: "agent",     model: "gemma3:1b",            size: "1.0 GB", tag: "npu chat" },
        { slot: "stt-npu",   model: "whisper-v3-turbo",     size: "400 MB", tag: "coresident" },
        { slot: "embed-npu", model: "embed-gemma-300m",     size: "350 MB", tag: "coresident" },
      ],
    },
  },

  downloads: [
    { name: "Qwen3.6-27B-MTP-Q4_K_M.gguf",  repo: "unsloth/Qwen3.6-27B-A3B-MTP-GGUF", pct: 62, size: "18.8 GB", done: "11.4 GB", rate: "42 MB/s", eta: "2:34", state: "pulling" },
    { name: "Qwen3-Coder-30B-A3B.gguf",     repo: "unsloth/Qwen3-Coder-30B-A3B-Instruct-GGUF", pct: 11, size: "18.6 GB", done: "2.1 GB", rate: "—", eta: "queued", state: "queued" },
    { name: "user.Phi-4-Mini.gguf",         repo: "microsoft/Phi-4-mini-instruct-GGUF", pct: 47, size: "2.3 GB", done: "1.1 GB", rate: "—", eta: "—", state: "paused" },
    { name: "bge-reranker-v2-m3-q4_k_m.gguf", repo: "BAAI/bge-reranker-v2-m3", pct: 99, state: "verifying" },
    { name: "Llama-3.1-70B-Q4_K_M.gguf",    repo: "unsloth/Llama-3.1-70B-Instruct-GGUF", pct: 23, size: "39.6 GB", state: "error" },
    { name: "whisper-base.bin",              repo: "openai/whisper-base", pct: 100, state: "done" },
    { name: "kokoro-v1",                     repo: "hexgrad/Kokoro-82M", pct: 100, state: "done" },
    { name: "sd-turbo.safetensors",          repo: "stabilityai/sd-turbo", pct: 0, state: "queued" },
    { name: "nomic-embed-text-v1.5",         repo: "nomic-ai/nomic-embed-text-v1.5-GGUF", pct: 0, state: "queued" },
  ],

  models: [
    { id: "qwen3.6-27b-mtp", longName: "Qwen3.6-27B-MTP", repo: "unsloth/Qwen3.6-27B-A3B-MTP-GGUF:Q4_K_M", params: "27B", size: "18.8 GB", labels: ["chat", "tool-calling"], type: "llm", device: "rocm", ns: "blessed", installed: true, runtime: "llamacpp" },
    { id: "qwen3-coder-30b", longName: "Qwen3-Coder-30B-A3B", repo: "unsloth/Qwen3-Coder-30B-A3B-Instruct-GGUF:Q4_K_M", params: "30B", size: "18.6 GB", labels: ["chat", "coder", "tool-calling"], type: "llm", device: "rocm", ns: "blessed", installed: true, runtime: "llamacpp" },
    { id: "qwen3.5-9b", longName: "Qwen3.5-9B", repo: "Qwen/Qwen3.5-9B-Instruct-GGUF:Q4_K_M", params: "9B", size: "5.4 GB", labels: ["chat", "tool-calling", "vision"], type: "llm", device: "rocm", ns: "blessed", installed: false, runtime: "llamacpp" },
    { id: "gemma3-1b-npu", longName: "gemma3:1b (NPU)", repo: "google/gemma-3-1b-it-flm", params: "1B", size: "1.0 GB", labels: ["chat", "tool-calling"], type: "llm", device: "npu", ns: "blessed", installed: true, runtime: "flm" },
    { id: "llama-3.2-3b-npu", longName: "Llama-3.2-3B (NPU)", repo: "meta-llama/llama-3.2-3b-flm", params: "3B", size: "2.4 GB", labels: ["chat"], type: "llm", device: "npu", ns: "blessed", installed: false, runtime: "flm" },
    { id: "nomic-v1.5", longName: "nomic-embed-text-v1.5", repo: "nomic-ai/nomic-embed-text-v1.5-GGUF", params: "350M", size: "350 MB", labels: ["embeddings"], type: "embedding", device: "rocm", ns: "blessed", installed: true, runtime: "llamacpp" },
    { id: "bge-reranker-v2", longName: "bge-reranker-v2-m3", repo: "BAAI/bge-reranker-v2-m3-q4_k_m", params: "400M", size: "400 MB", labels: ["reranking"], type: "reranking", device: "rocm", ns: "blessed", installed: true, runtime: "llamacpp" },
    { id: "whisper-large-v3", longName: "whisper-large-v3", repo: "openai/whisper-large-v3", params: "1.5B", size: "3.0 GB", labels: ["transcription"], type: "transcription", device: "cpu", ns: "blessed", installed: false, runtime: "whispercpp" },
    { id: "whisper-base", longName: "whisper-base", repo: "openai/whisper-base", params: "74M", size: "150 MB", labels: ["transcription"], type: "transcription", device: "cpu", ns: "blessed", installed: true, runtime: "whispercpp" },
    { id: "kokoro-v1", longName: "kokoro-v1", repo: "hexgrad/Kokoro-82M", params: "82M", size: "400 MB", labels: ["tts"], type: "tts", device: "cpu", ns: "blessed", installed: true, runtime: "kokoro" },
    { id: "sd-turbo", longName: "sd-turbo", repo: "stabilityai/sd-turbo", params: "1.2B", size: "1.2 GB", labels: ["image"], type: "image", device: "rocm", ns: "blessed", installed: true, runtime: "sdcpp" },
    { id: "flux-2-klein-9b", longName: "Flux-2-Klein-9B", repo: "black-forest-labs/flux-2-klein-9b", params: "9B", size: "12 GB", labels: ["image", "edit"], type: "image", device: "rocm", ns: "blessed", installed: false, runtime: "sdcpp" },
    { id: "user.phi-4-mini", longName: "user.Phi-4-Mini", repo: "microsoft/Phi-4-mini-instruct-GGUF", params: "3.8B", size: "2.3 GB", labels: ["chat", "tool-calling"], type: "llm", device: "rocm", ns: "pulled", installed: true, runtime: "llamacpp" },
    { id: "user.bert-base", longName: "user.bert-base", repo: "google-bert/bert-base-uncased", params: "110M", size: "440 MB", labels: ["embeddings"], type: "embedding", device: "cpu", ns: "pulled", installed: true, runtime: "llamacpp" },
  ],

  recipe: {
    "qwen3.6-27b-mtp": {
      ctx_size: 8192,
      llamacpp_backend: "rocm",
      llamacpp_args: "--flash-attn on --threads 8 --parallel 1",
      n_gpu_layers: -1,
      temperature: 0.7,
    },
  },

  // ── journal block removed (#322 phase 3) ─────────────────────────
  // The dashboard now streams /api/journal/stream for the footer pane
  // and the Logs page; both render an empty state when the SSE has no
  // entries yet. No more "loaded model 'qwen3.6-27b-mtp'" mock prose
  // leaking into prod screenshots before SSE primes.

  omnirouter: [
    { name: "generate_image",   active: true,  target: "img (sd-turbo)" },
    { name: "edit_image",       active: false, target: "needs model with 'edit' label" },
    { name: "text_to_speech",   active: true,  target: "tts (kokoro-v1)" },
    { name: "transcribe_audio", active: true,  target: "stt-npu (whisper-v3-turbo)" },
    { name: "analyze_image",    active: false, target: "needs LLM with 'vision' label" },
    { name: "embed_text",       active: true,  target: "embed (nomic-v1.5)" },
    { name: "rerank_documents", active: true,  target: "rerank (bge-reranker-v2)" },
    { name: "route_to_chat",    active: true,  target: "agent, primary" },
  ],

  // v0.3 PR-8: HAL0_DATA.approvals removed. The dashboard reads
  // approvals from the live /api/agent/approvals queue (via
  // useAgentApprovalsCount in SidebarAgentBlock — PR-6) and renders
  // inline cards in the HermesChat composer (PR-10).

  backends: [
    { name: "llamacpp:rocm",   kind: "llamacpp", device: "rocm",  ver: "v1.0 (b9253)", state: "installed", recommended: true },
    { name: "llamacpp:vulkan", kind: "llamacpp", device: "vulkan", ver: "v1.0 (b9253)", state: "installed" },
    { name: "llamacpp:cpu",    kind: "llamacpp", device: "cpu",   ver: "v1.0 (b9253)", state: "installed" },
    { name: "flm:npu",         kind: "flm",      device: "npu",   ver: "v0.9.42 (deb)", state: "installed", recommended: true, note: "manual deb" },
    { name: "whispercpp",      kind: "whispercpp", device: "vulkan", ver: "v1.0 (vulkan)", state: "installed" },
    { name: "sdcpp",           kind: "sdcpp",    device: "rocm",  ver: "v1.0 (rocm)", state: "installed" },
    { name: "kokoro",          kind: "kokoro",   device: "cpu",   ver: "builtin · cpu", state: "installed" },
    { name: "ryzenai-server",  kind: "ryzenai",  device: "npu",   ver: "—", state: "unavailable", note: "Windows-only" },
  ],
};

// ─── Helpers ──────────────────────────────────────────────────────
// Unit-aware size parser: "4.9 GB" → 4.9 (always normalised to GB)
function parseSizeGB(str) {
  if (typeof str === "number") return str;
  if (!str) return 0;
  const m = String(str).trim().match(/^([\d.]+)\s*(GB|MB|KB|TB)?/i);
  if (!m) return 0;
  const n = parseFloat(m[1]);
  const unit = (m[2] || "GB").toUpperCase();
  if (unit === "TB") return n * 1024;
  if (unit === "GB") return n;
  if (unit === "MB") return n / 1024;
  if (unit === "KB") return n / (1024 * 1024);
  return n;
}

// Slot ↔ model match helper (uses structured model_id, falls back to id)
function slotsUsingModel(modelId) {
  return HAL0_DATA.slots.filter(s => s.model_id === modelId);
}

window.parseSizeGB = parseSizeGB;
window.slotsUsingModel = slotsUsingModel;

window.HAL0_DATA = HAL0_DATA;
