// hal0 Overview v2 — Memory card: the Hindsight engine at a glance (bank count, the chosen bank's
// nodes / links / observations, pending operations, last consolidation). Window-global (OvMemory).
// Renders nothing while memory is gated off (`useMemoryEnabled()` false).

import { useMemoryEnabled } from '@/api/hooks/useMemory'
import { useMemoryEngine, useMemoryBanks, useBankStats } from '@/api/hooks/useHindsight'
import { bankFigures, chosenBank } from './memory-pure'

const { useMemo } = React

const MEM_BANK_LS_KEY = 'hal0.mem.bank'

function storedBank() {
  try {
    return localStorage.getItem(MEM_BANK_LS_KEY) || null
  } catch {
    return null
  }
}

// Thin gate: a box with memory off never mounts the body, so it never polls /api/memory/*.
export function OvMemory({ onGo }) {
  const enabled = useMemoryEnabled()
  return enabled ? <OvMemoryBody onGo={onGo} /> : null
}

function OvMemoryBody({ onGo }) {
  const engine = useMemoryEngine()
  const banksQ = useMemoryBanks()
  const bankIds = useMemo(() => (banksQ.data?.banks ?? []).map((b) => b.bank_id), [banksQ.data])
  const bank = chosenBank(bankIds, storedBank(), (typeof window !== 'undefined' && window.location.hash) || '')
  const stats = useBankStats(bank)

  const e = engine.data
  const up = e?.reachable === true
  const total = typeof e?.banks_total === 'number' ? e.banks_total : bankIds.length > 0 ? bankIds.length : null
  const f = bankFigures(stats.data)
  const notes = []
  if (f.pending != null) notes.push(`${f.pending} ${f.pending === 1 ? 'operation' : 'operations'} pending`)
  if (f.consolidated) notes.push(`consolidated ${f.consolidated}`)

  return (
    <OvCard
      title="Memory"
      foot={
        <>
          <button type="button" className="ov-lnk" onClick={() => onGo('memory')}>
            Open memory →
          </button>
        </>
      }
    >
      <div className="ov-ie-row">
        <OvPill tone={up ? 'ok' : e ? 'err' : 'dim'}>
          <span className="ov-d" />
          Hindsight
        </OvPill>
        <span>
          {total != null ? `${total} banks` : '— banks'}
          {bank && (
            <>
              {' · bank '}
              <b>{bank}</b>
            </>
          )}
        </span>
      </div>
      <div className="ov-kv">
        <div>
          <b>{f.nodes}</b>
          <span>nodes</span>
        </div>
        <div>
          <b>{f.links}</b>
          <span>links</span>
        </div>
        <div>
          <b>{f.obs}</b>
          <span>observations</span>
        </div>
      </div>
      {notes.length > 0 && <div className="ov-card-n">{notes.join(' · ')}</div>}
    </OvCard>
  )
}

Object.assign(window, { OvMemory })
