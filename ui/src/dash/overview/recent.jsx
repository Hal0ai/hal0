// hal0 Overview v2 — Recent: the activity trail as a digest that folds slot
// state flaps into one row. Window-global (OvRecent). Footer opens the dock's
// events pane, the same `hal0:open-events` event the agents page uses.

import { useActivityRecent } from '@/api/hooks/useActivity'
import { digest } from './recent-digest-pure'

const { useMemo } = React

export function OvRecent() {
  const { data: records } = useActivityRecent(200)
  const rows = useMemo(() => digest(records ?? []), [records])

  return (
    <OvCard
      title="Recent"
      note="digest · flaps folded"
      flush
      foot={
        <>
          every event, live, is in the dock below
          <span style={{ flex: 1 }} />
          <button type="button" className="ov-lnk" onClick={() => window.dispatchEvent(new CustomEvent('hal0:open-events'))}>
            Open events ↓
          </button>
        </>
      }
    >
      {rows.length === 0 ? (
        <div className="ov-empty">no recent activity</div>
      ) : (
        rows.map((r) => (
          <div key={r.key} className="ov-act">
            <span className="ov-ts">{r.ts}</span>
            <span className={'ov-cat' + (r.tone !== 'info' ? ' ' + r.tone : '')}>{r.category}</span>
            {/* html is built by digest(): every dynamic part is escaped; only <b> is markup */}
            <span className="ov-msg" dangerouslySetInnerHTML={{ __html: r.html }} />
          </div>
        ))
      )}
    </OvCard>
  )
}

Object.assign(window, { OvRecent })
