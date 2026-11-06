// hal0 v3 dashboard — Memory workspace view (Hindsight 1.2 showcase).
//
// A real ES module (pattern: Benchmarks.tsx), not the window-globals .jsx
// shim #memory uses. Lives at its own top-level route (#memworkspace) so it
// can evolve independently of the AgentView/Memory tab gate. Data comes from
// useMemoryWorkspace.ts (lanes/agents/models + the allowlisted engine
// passthrough `/api/memory/engine/<upstream path>`, mirroring
// hindsight-1.2.0-openapi.json). Pure formatting/derivation lives in
// memory-workspace-pure.ts so vitest can pin it without a DOM. Each tab is
// its own component file under this directory; this file is just the shell
// (header, bank switcher, tab nav, console link) and routing.

import React, { useState } from 'react'
import { useMemoryEngineQuery } from '@/api/hooks/useMemoryWorkspace'
import { AgentsTab } from './AgentsTab'
import { DirectivesTab } from './DirectivesTab'
import { KnowledgeBaseTab } from './KnowledgeBaseTab'
import { MentalModelsTab } from './MentalModelsTab'
import { ModelsTab } from './ModelsTab'
import { OperationsTab } from './OperationsTab'
import { OverviewTab } from './OverviewTab'

const TABS = ['overview', 'kb', 'mental-models', 'directives', 'operations', 'agents', 'models'] as const
type Tab = (typeof TABS)[number]

const TAB_LABEL: Record<Tab, string> = {
  overview: 'Overview',
  kb: 'Knowledge base',
  'mental-models': 'Mental models',
  directives: 'Directives',
  operations: 'Operations',
  agents: 'Agents',
  models: 'Models',
}

export const tabFromParam = (param?: string | null): Tab =>
  (TABS as readonly string[]).includes(param || '') ? (param as Tab) : 'overview'

export const HINDSIGHT_CONSOLE_DEFAULT_PORT = 9999

/** Default Hindsight Control Plane URL: same host as the dashboard, fixed port. */
export function hindsightConsoleUrl(hostname: string): string {
  return `http://${hostname}:${HINDSIGHT_CONSOLE_DEFAULT_PORT}`
}

interface BankSummary {
  bank_id: string
  name?: string
  fact_count?: number
  [key: string]: unknown
}

function useBankList() {
  return useMemoryEngineQuery<{ banks: BankSummary[] }>('v1/default/banks', { refetchInterval: 15_000 })
}

export const MemoryWorkspaceView: React.FC<{ param?: string | null }> = ({ param = null }) => {
  const tab = tabFromParam(param)
  const bankList = useBankList()
  const banks = bankList.data?.banks || []
  const [bank, setBank] = useState<string | null>(null)
  const effectiveBank = bank || banks[0]?.bank_id || 'shared'
  const consoleUrl = hindsightConsoleUrl(typeof window !== 'undefined' ? window.location.hostname : 'localhost')

  const goTab = (t: Tab) => {
    window.location.hash = `#memworkspace/${t}`
  }

  const openBank = (b: string) => {
    setBank(b)
    goTab('overview')
  }

  return (
    <div className="view memws" data-testid="memws-view">
      <div className="memws-header">
        <h1>Memory</h1>
        <select
          className="memws-bank-select"
          data-testid="memws-bank-select"
          value={effectiveBank}
          onChange={(e) => setBank(e.target.value)}
        >
          {banks.map((b) => (
            <option key={b.bank_id} value={b.bank_id}>{b.bank_id}</option>
          ))}
        </select>
        <a className="btn ghost sm" href={consoleUrl} target="_blank" rel="noreferrer" data-testid="memws-console-link">
          Open console ↗
        </a>
      </div>
      <div className="memws-tabs" role="tablist">
        {TABS.map((t) => (
          <button
            key={t}
            role="tab"
            aria-selected={tab === t}
            className={`memws-tab${tab === t ? ' active' : ''}`}
            data-testid={`memws-tab-${t}`}
            onClick={() => goTab(t)}
          >
            {TAB_LABEL[t]}
          </button>
        ))}
      </div>
      <div className="memws-body">
        {tab === 'overview' && <OverviewTab bank={effectiveBank} />}
        {tab === 'kb' && <KnowledgeBaseTab bank={effectiveBank} />}
        {tab === 'mental-models' && <MentalModelsTab bank={effectiveBank} />}
        {tab === 'directives' && <DirectivesTab bank={effectiveBank} />}
        {tab === 'operations' && <OperationsTab bank={effectiveBank} />}
        {tab === 'agents' && <AgentsTab onOpenBank={openBank} />}
        {tab === 'models' && <ModelsTab bank={effectiveBank} />}
      </div>
    </div>
  )
}

export default MemoryWorkspaceView
