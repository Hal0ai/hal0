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
  host: {
    name: 'halo-strix.local',
    uptime: '14d 02:11',
    cpu: 'AMD Ryzen AI Max+ PRO 395',
    cores: '16c · 32t',
    gpu: 'Radeon Graphics (gfx1151, Strix Halo)',
    ram: { total: 128, free: 74, used: 54 },
    npu: { present: true, columns: 8, ctx: 1 },
  },

  lemond: {
    status: 'up',
    version: 'v10.6.0',
    loaded: 3,
    budget: 4,
    throughput: 12.4,
    queued: 0,
    coresident: true,
  },

  /** Subset of slots the v3 dash seeds — enough to drive `/slots` group
   *  rendering (chat / embed / voice / img + NPU rollup). */
  slots: [
    {
      name: 'primary', type: 'llm', device: 'gpu-rocm',
      model: 'qwen3.6-27b-mtp-q4_k_m', model_id: 'qwen3.6-27b-mtp',
      group: 'chat', state: 'serving', port: 8092, isDefault: true,
    },
    {
      name: 'agent', type: 'llm', device: 'npu',
      model: 'gemma3:1b', model_id: 'gemma3-1b-npu',
      group: 'npu', state: 'ready', port: 8093, isDefault: true,
    },
    {
      name: 'coder', type: 'llm', device: 'gpu-rocm',
      model: 'qwen3-coder-30b-a3b', model_id: 'qwen3-coder-30b',
      group: 'chat', state: 'idle', port: 8094,
    },
    {
      name: 'embed', type: 'embedding', device: 'gpu-rocm',
      model: 'nomic-embed-text-v1.5', model_id: 'nomic-v1.5',
      group: 'embed', state: 'ready', port: 8095, isDefault: true,
    },
  ],

  backends: [
    { id: 'llamacpp:rocm', name: 'llamacpp:rocm', ver: 'v1.0 (b9253)', state: 'installed', recommended: true },
    { id: 'llamacpp:vulkan', name: 'llamacpp:vulkan', ver: 'v1.0 (b9253)', state: 'installed' },
    { id: 'flm:npu', name: 'flm:npu', ver: 'v0.9.42 (deb)', state: 'installed', recommended: true, note: 'manual deb' },
  ],

  approvals: [] as any[],
}

export type MockData = typeof MOCK_DATA
