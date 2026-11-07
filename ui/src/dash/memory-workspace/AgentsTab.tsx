// hal0 v3 dashboard — Memory workspace: Agents tab (coding-agent install).
import React, { useState } from 'react'
import { useMemoryWorkspaceAgents } from '@/api/hooks/useMemoryWorkspace'
import { installCommandFor } from './memory-workspace-pure'
import { Card, EngineError } from './shared'

export const AgentsTab: React.FC<{ onOpenBank?: (bank: string) => void }> = ({ onOpenBank }) => {
  const agents = useMemoryWorkspaceAgents()
  const [harness, setHarness] = useState<string | null>(null)
  const [copied, setCopied] = useState(false)

  if (agents.isError) return <EngineError error={agents.error} what="agents" onRetry={() => agents.refetch()} />
  const harnesses = agents.data?.install.harnesses || []
  const active = harness || harnesses[0] || ''
  const command = active ? installCommandFor(agents.data?.install.command || '', active) : ''

  return (
    <div data-testid="memws-agents">
      <Card title="Coding-agent banks">
        <ul className="mono small">
          {(agents.data?.banks || []).map((b) => (
            <li key={b}>
              <button className="btn ghost sm" data-testid={`memws-agent-bank-${b}`} onClick={() => onOpenBank?.(b)}>
                {b}
              </button>
            </li>
          ))}
        </ul>
      </Card>
      <Card title="Install command">
        <div className="memws-chips">
          {harnesses.map((h) => (
            <button
              key={h}
              className={`btn ghost sm${active === h ? ' active' : ''}`}
              data-testid={`memws-harness-${h}`}
              onClick={() => setHarness(h)}
            >
              {h}
            </button>
          ))}
        </div>
        <pre className="mono small memws-pre" data-testid="memws-install-command">{command}</pre>
        <button
          className="btn sm"
          data-testid="memws-copy-command"
          onClick={() => {
            navigator.clipboard?.writeText(command)
            setCopied(true)
            setTimeout(() => setCopied(false), 1500)
          }}
        >
          {copied ? 'Copied!' : 'Copy'}
        </button>
      </Card>
    </div>
  )
}

export default AgentsTab
