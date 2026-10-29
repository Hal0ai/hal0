// Shared fixtures for the serving-now tests: the CT105 shape — 13 slots over four lanes, five loaded.
export const SLOTS: any[] = [
  { name: 'agent', device: 'gpu-vulkan', state: 'ready', type: 'llm', model: 'Qwen3.8-27B-MTP', port: 8101, isDefault: true, mem_mb: 22528, ctx_effective: 65536 },
  { name: 'utility', device: 'gpu-vulkan', state: 'ready', type: 'llm', model: 'Qwen3.8-35B-A3B', port: 8102, mem_mb: 20480, ctx_effective: 131072, metrics: { ctx: 10486 } },
  { name: 'brain', device: 'gpu-vulkan', state: 'ready', type: 'llm', model: 'Qwen3-4B', port: 8103, mem_mb: 4096, ctx_effective: 32768 },
  { name: 'ops', device: 'gpu-vulkan', state: 'offline', type: 'llm', model: 'Qwopus3.6-27B-Coder' },
  { name: 'img', device: 'gpu-rocm', state: 'ready', type: 'image', model: 'sdxl-turbo', port: 8188, mem_mb: 5836 },
  { name: 'tts', device: 'gpu-rocm', state: 'ready', type: 'tts', model: 'kokoro-82m', port: 8104, mem_mb: 600 },
  { name: 'rerank', device: 'gpu-rocm', state: 'offline', type: 'reranking', model: 'bge-reranker-v2-m3' },
  { name: 'code', device: 'gpu-rocm', state: 'offline', type: 'llm', model: 'qwen3.8-flash-next' },
  { name: 'embed', device: 'gpu-rocm', state: 'offline', type: 'embedding', model: 'qwen3-embedding-0-6b' },
  { name: 'flm-embed', device: 'npu', state: 'offline', type: 'embedding', model: 'embed-gemma:300m' },
  { name: 'flm-stt', device: 'npu', state: 'offline', type: 'transcription', model: 'whisper-v3:turbo' },
  { name: 'flm', device: 'npu', state: 'offline', type: 'llm', model: 'gemma4-it:e4b' },
  { name: 'stt', device: 'cpu', state: 'offline', type: 'transcription', model: 'moonshine-base-en' },
]

export const SYS: any = {
  ts: '2026-10-29T12:00:00Z',
  fleet: {},
  slots: [
    { slot_id: 'agent', kv_used: 21000, inflight: 0 },
    { slot_id: 'utility', kv_used: 83801, inflight: 0 },
  ],
}

export const SERVING_UTILITY: any = {
  name: 'utility',
  device: 'gpu-vulkan',
  state: 'serving',
  type: 'llm',
  model: 'Qwen3.8-35B-A3B',
  port: 8102,
  metrics: { toks: 58 },
  last_used_at: Date.now() / 1000,
}
