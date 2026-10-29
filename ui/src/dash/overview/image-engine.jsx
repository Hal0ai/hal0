// hal0 Overview v2 — Image engine: ComfyUI at a glance (mode, engine, queue, the render in
// flight, a held render's wait note, the weight inventory). Window-global (OvImageEngine).
// Renders nothing while /api/comfyui/status has no answer (404 from an older backend or a box
// without an img slot): the card is absent, not an error.

import { useArbiter, useComfyui } from '@/api/hooks/useComfyui'
import { OFF_BOX_NOTE } from '../image/editor-reach'
import { useEditorAccess } from '../image/useEditorAccess'
import { imageModel } from './image-engine-pure'

const { useMemo } = React

export function OvImageEngine({ onGo }) {
  const status = useComfyui()
  const arbiter = useArbiter()
  const access = useEditorAccess()
  const m = useMemo(() => imageModel(status.data, arbiter.data), [status.data, arbiter.data])
  if (!m) return null

  const editorUrl = access.onBox ? access.url : null

  return (
    <OvCard
      title="Image engine"
      note="ComfyUI · :8188"
      foot={
        <>
          <button type="button" className="ov-lnk" onClick={() => onGo('slots/image')}>
            Generate →
          </button>
          <span style={{ flex: 1 }} />
          {editorUrl ? (
            <button type="button" className="ov-lnk" onClick={() => window.open(editorUrl, '_blank', 'noopener')}>
              Open editor ↗
            </button>
          ) : (
            <button type="button" className="ov-lnk" disabled title={OFF_BOX_NOTE} style={{ cursor: 'not-allowed', opacity: 0.5 }}>
              Open editor ↗
            </button>
          )}
        </>
      }
    >
      <div className="ov-ie-row">
        <OvPill tone={m.modeTone}>
          <span className="ov-d" />
          {m.modeText}
        </OvPill>
        <span>
          engine <b>{m.engine}</b>
        </span>
        <span>{m.queueLine}</span>
      </div>
      {m.progress && (
        <div className="ov-prog">
          <div className="ov-ie-row">
            <b>{m.progress.model}</b>
            {m.progress.step && <span>{m.progress.step}</span>}
            {m.progress.node && <span>{m.progress.node}</span>}
            <span>{m.progress.elapsed}</span>
          </div>
          {m.progress.pct != null && (
            <div className="ov-track" role="progressbar" aria-label="Render progress" aria-valuemin={0} aria-valuemax={100} aria-valuenow={m.progress.pct}>
              <i style={{ width: `${m.progress.pct}%` }} />
            </div>
          )}
        </div>
      )}
      {m.pendingNote && (
        <div className="ov-prog">
          <span style={{ color: 'var(--warn)' }}>{m.pendingNote}</span>
          <div className="ov-track">
            <i style={{ width: '0%' }} />
          </div>
        </div>
      )}
      {m.inventory.length > 0 && (
        <div className="ov-inv">
          {m.inventory.map((t) => (
            <div key={t.k}>
              <b>{t.v}</b>
              <span>{t.k}</span>
            </div>
          ))}
        </div>
      )}
    </OvCard>
  )
}

Object.assign(window, { OvImageEngine })
