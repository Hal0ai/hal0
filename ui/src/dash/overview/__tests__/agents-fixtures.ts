// Shared fixtures for the Agents panel tests.
import type { AgentOverview } from '@/api/hooks/useAgents'
import type { BridgeRun } from '@/api/hooks/useBridge'

export const OV_HERMES = {
  name: 'hermes',
  kind: 'daemon',
  installed: true,
  phase: 'serving',
  units: { agent: 'active', gateway: 'active' },
  api_server: { healthy: true, profiles: ['default', 'coder', 'hal0-brain', 'homelab', 'research'] },
  platforms: ['telegram', 'webhook', 'api'],
  version: { live: { describe: 'v0.22.4', python_minor: '3.14' } },
  approvals: { mode: 'smart' },
} as unknown as AgentOverview

export const OV_PI = {
  name: 'pi',
  kind: 'cli',
  installed: true,
  phase: 'installed',
  version: { installed: { pi: '0.9.1', adapter: '2' } },
} as unknown as AgentOverview

export const RUN = {
  run_id: 'r1',
  agent: 'hermes/coder',
  profile: 'coder',
  client: 'claude-code',
  status: 'running',
  created_at: 1000,
  updated_at: 1000,
  input_preview: 'Fix the flaky test\nthen push',
  output_preview: null,
  error: null,
  usage: null,
  pending: null,
  last_seq: 0,
} as BridgeRun
