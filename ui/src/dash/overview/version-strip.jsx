// hal0 Overview v2 — version strip: the page's plain-text footer (hal0 version · channel, FLM,
// auto-check, update state, host summary). Window-global (OvVersionStrip); a plain div, not a card.
// The version reads `…` until the update state lands, like the topbar.

import { useUpdateState } from '@/api/hooks/useUpdates'
import { useHardware } from '@/api/hooks/useHardware'
import { useMemoryMapModel } from '../memory-map'
import { hostSummary } from './version-strip-pure'

function OvVersionStrip() {
  const upd = useUpdateState().data
  const hw = useHardware().data
  const mm = useMemoryMapModel()

  const hal0 = upd?.hal0
  const available = hal0?.available

  return (
    <div className="ov-verstrip">
      <span>
        hal0 <b>{hal0?.current ?? '…'}</b>
        {hal0?.channel ? ` · ${hal0.channel}` : ''}
      </span>
      <span>
        FLM <b>{upd?.flm?.current ?? '—'}</b>
      </span>
      {typeof upd?.autoCheck === 'boolean' && <span>auto-check {upd.autoCheck ? 'on' : 'off'}</span>}
      {/* `available` null = checked, nothing newer; undefined = unknown, so claim nothing. */}
      {hal0?.current && typeof available === 'string' && available && (
        <span>
          <b style={{ color: 'var(--accent)' }}>{available} available</b>
        </span>
      )}
      {hal0?.current && available === null && <span>up to date</span>}
      <span style={{ flex: 1 }} />
      {hw && <span>{hostSummary(hw, mm.pool?.totalGb ?? 0)}</span>}
    </div>
  )
}

Object.assign(window, { OvVersionStrip })
