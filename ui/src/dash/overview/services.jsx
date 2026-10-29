// hal0 Overview v2 — Services: the companion services as rows (the four, in order, then any others) (state dot, name + detail,
// what OpenWebUI is wired to, an open arrow). Window-global (OvServices). A row click goes to the
// Services page; the arrow opens the service's own URL. ComfyUI's arrow is the editor access's,
// never the row's `url`: ComfyUI listens on the box's loopback (image/editor-reach.ts has the
// rule), so the arrow opens from a page on the box and is disabled anywhere else.

import { useServices } from '@/api/hooks/useServices'
import { servicesForWidget } from '../services-widget-pure'
import { OFF_BOX_NOTE } from '../image/editor-reach'
import { useEditorAccess } from '../image/useEditorAccess'

// The four companions lead, in this order; any other service follows (servicesForWidget orders and caps).
const SERVICE_ORDER = ['openwebui', 'comfyui', 'hermes', 'n8n']
const WIRED_KEYS = ['chat', 'voice', 'documents', 'images', 'web_search']

export function OvServices({ onGo }) {
  const svc = useServices()
  const editor = useEditorAccess()
  const rows = servicesForWidget(svc, SERVICE_ORDER, 8)
  const upCount = rows.filter((s) => s.up).length

  return (
    <OvCard
      title="Services"
      note={svc.pending || svc.unavailable || rows.length === 0 ? null : `${upCount} / ${rows.length} up`}
      right={
        <button type="button" className="ov-lnk" onClick={() => onGo('services')}>
          Manage →
        </button>
      }
      flush
    >
      {svc.unavailable ? (
        <div className="ov-empty">services unavailable</div>
      ) : svc.pending ? (
        <div className="ov-empty">source pending</div>
      ) : rows.length === 0 ? (
        <div className="ov-empty">no companion services</div>
      ) : (
        rows.map((s) => {
          const disabled = s.unit_state?.unit_file_state === 'disabled' || (!s.up && !s.managed)
          const isComfy = s.id === 'comfyui'
          const openUrl = isComfy ? editor.url : s.url
          const openable = s.up && (isComfy || s.url)
          const wired = WIRED_KEYS.filter((k) => s.wired?.[k] === true)
          return (
            <div
              key={s.id}
              className="ov-svc"
              role="link"
              tabIndex={0}
              onClick={() => onGo('services')}
              onKeyDown={(e) => {
                if (e.key === 'Enter' && e.target === e.currentTarget) onGo('services')
              }}
              title={s.name}
              style={{ cursor: 'pointer' }}
            >
              <span className={'ov-dot ' + (s.up ? 'up' : disabled ? '' : 'err')} />
              <div>
                <div className="ov-nm" style={disabled ? { color: 'var(--fg-3)' } : undefined}>
                  {s.id}
                </div>
                <div className="ov-det">{disabled ? 'disabled' : s.detail || '—'}</div>
              </div>
              <div className="ov-wired">
                {wired.map((w) => (
                  <span key={w} className="ov-chip">
                    {w}
                  </span>
                ))}
              </div>
              {openable ? (
                isComfy && !editor.onBox ? (
                  <span
                    className="ov-open"
                    data-testid="ov-svc-open-comfyui"
                    title={OFF_BOX_NOTE}
                    aria-disabled="true"
                    style={{ cursor: 'not-allowed', opacity: 0.5 }}
                    onClick={(e) => e.stopPropagation()}
                  >
                    ↗
                  </span>
                ) : (
                  <button
                    type="button"
                    className="ov-open"
                    data-testid={`ov-svc-open-${s.id}`}
                    title={`Open ${s.name}`}
                    onClick={(e) => {
                      e.stopPropagation()
                      window.open(openUrl, '_blank', 'noopener')
                    }}
                  >
                    ↗
                  </button>
                )
              ) : (
                <span />
              )}
            </div>
          )
        })
      )}
    </OvCard>
  )
}

Object.assign(window, { OvServices })
