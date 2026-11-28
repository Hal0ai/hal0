/**
 * Full pages: every top-level route and tab at desktop width.
 * The route list comes from ui/src/dash/main.jsx (ROUTES), slots-route.ts
 * (SLOTS_RESERVED_TABS), models.jsx (MODEL_TABS), memory-area/MemoryArea.jsx
 * (TABS), agents/agent-view.jsx (AGENT_TABS) and settings/SettingsNav.jsx.
 */
import { DESKTOP, open, shoot, test } from './shot'

export const PAGES: Array<[string, string, { width: number; height: number }?]> = [
  // The hero: wide enough that the Serving now table and the Agents card fit.
  ['dashboard-overview', '#dashboard', { width: 1680, height: 1050 }],
  ['slots-inference', '#slots'],
  ['image-gen-comfyui', '#slots/image'],
  ['slots-npu', '#slots/npu'],
  ['endpoints-tab', '#slots/endpoints'],
  ['slots-runner-images', '#slots/runner-images'],
  ['stacks-tab', '#slots/stacks'],
  ['models-registry', '#models'],
  ['models-image', '#models/image'],
  ['models-providers', '#models/providers'],
  ['models-flm', '#models/flm'],
  ['models-profiles', '#models/profiles'],
  ['benchmarks', '#benchmarks'],
  ['agents-overview', '#agent'],
  ['mcp-tab', '#agent/mcp'],
  ['memory-health', '#memory/health'],
  ['memory-bank', '#memory/facts'],
  ['memory-knowledge', '#memory/knowledge'],
  ['memory-operations', '#memory/operations'],
  ['memory-agents', '#memory/agents'],
  ['services-page', '#services'],
  ['logs-activity', '#logs'],
  ['operator-board', '#board'],
  ['settings-page', '#settings'],
  ['settings-security', '#settings/security'],
  ['settings-doctor', '#settings/doctor'],
  ['settings-loaded-models', '#settings/slots'],
  ['settings-capabilities', '#settings/capabilities'],
  ['settings-hardware', '#settings/hardware'],
  ['settings-storage', '#settings/storage'],
  ['settings-memory', '#settings/memory'],
  ['settings-updates', '#settings/updates'],
  ['settings-accounts', '#settings/accounts'],
  ['settings-agent-chat', '#settings/agents'],
]

const only = process.env.SHOT_ONLY ? new RegExp(process.env.SHOT_ONLY) : null
for (const [name, hash, viewport] of PAGES) {
  if (only && !only.test(name)) continue
  test(name, async ({ page }) => {
    await open(page, hash, viewport ?? DESKTOP)
    await shoot(page, name)
  })
}
