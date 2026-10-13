// GENERAL ▸ Network (#2382), rendered by NetworkPage.jsx — the browser origins hal0 accepts.
//
// Edits hal0.toml [network] through POST /api/settings/network (the locked
// config transaction every Settings write uses), never api.env: the
// installer rewrites its managed HAL0_ALLOWED_ORIGINS block on every run,
// which is why a hand-added origin there used to vanish. Origins go as a
// diff the server applies to the list on disk, so this page never writes
// back a stale copy. The origin gates read [network] live
// (api/agents/_auth.allowed_origins), so a save applies on the next
// request. The CLI equivalent is `hal0 network`.
import { useEffect, useState } from 'react'
import { useSettingsClient } from '../../data/settingsClient.js'
import { useSettingsNetworkEdit } from '@/api/hooks/useSettings'
import {
  networkEditBody,
  networkFormDirty,
  networkFormFromSettings,
  networkSaveError,
} from './networkOrigins.js'

const inputStyle = {
  fontFamily: 'var(--jbm)', fontSize: 11, background: 'var(--bg-2)', color: 'var(--fg)',
  border: '1px solid var(--line)', borderRadius: 4, padding: '4px 6px', width: '100%',
  boxSizing: 'border-box',
}

export function NetworkPanel() {
  const { settings } = useSettingsClient()
  const edit = useSettingsNetworkEdit()
  const live = settings.data || null
  const [form, setForm] = useState(() => networkFormFromSettings(live))
  const [error, setError] = useState(null)

  // Re-seed from the server whenever the live config changes (initial load,
  // a save, another tab's save) — the form never holds a stale baseline.
  useEffect(() => {
    setForm(networkFormFromSettings(live))
  }, [live])

  const dirty = live ? networkFormDirty(form, live) : false

  const onSave = async () => {
    setError(null)
    const body = networkEditBody(form, live)
    if (!body) return
    try {
      await edit.mutateAsync(body)
      window.__hal0Toast && window.__hal0Toast('Network origins saved — applies on the next request', 'ok')
    } catch (e) {
      setError(networkSaveError(e))
    }
  }

  return (
    <>
      <h3 style={{ margin: '0 0 6px', fontSize: 13 }}>Browser origins</h3>
      <div className="s-panel" data-testid="network-panel" style={{ marginBottom: 16 }}>
        <div className="s-row" style={{ display: 'block', padding: '13px 14px' }}>
          <label htmlFor="network-public-url" style={{ fontSize: 13, color: 'var(--fg)' }}>Public URL</label>
          <div className="mono" style={{ fontSize: 10.5, color: 'var(--fg-5)', margin: '2px 0 6px' }}>
            The URL users reach hal0 at through a reverse proxy. Its origin is allowed. Leave empty for none.
          </div>
          <input
            id="network-public-url"
            data-testid="network-public-url"
            value={form.publicUrl}
            placeholder="https://hal0.example.com"
            onChange={(e) => setForm((f) => ({ ...f, publicUrl: e.target.value }))}
            style={inputStyle}
          />
        </div>
        <div className="s-row" style={{ display: 'block', padding: '13px 14px' }}>
          <label htmlFor="network-extra-origins" style={{ fontSize: 13, color: 'var(--fg)' }}>Extra origins</label>
          <div className="mono" style={{ fontSize: 10.5, color: 'var(--fg-5)', margin: '2px 0 6px' }}>
            One per line, as scheme://host[:port] with no path, e.g. http://box.lan:8080. Added on top of the installer&apos;s list.
          </div>
          <textarea
            id="network-extra-origins"
            data-testid="network-extra-origins"
            rows={3}
            value={form.origins}
            placeholder="http://box.lan:8080"
            onChange={(e) => setForm((f) => ({ ...f, origins: e.target.value }))}
            style={{ ...inputStyle, resize: 'vertical' }}
          />
        </div>
        {error && (
          <div data-testid="network-error" className="mono err" style={{ fontSize: 10.5, padding: '0 14px 12px', lineHeight: 1.55 }}>
            {error}
          </div>
        )}
        <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', padding: '0 14px 12px' }}>
          <span className="mono" style={{ fontSize: 11, color: 'var(--fg-4)' }}>
            Stored at <span style={{ color: 'var(--fg-3)' }}>/etc/hal0/hal0.toml</span> · [network] · applies on the next request
          </span>
          <span style={{ display: 'inline-flex', gap: 8 }}>
            <button
              className="btn ghost sm"
              disabled={!dirty || edit.isPending}
              onClick={() => { setError(null); setForm(networkFormFromSettings(live)) }}
            >
              Reset
            </button>
            <button
              className="btn sm"
              data-testid="network-save"
              disabled={!dirty || edit.isPending}
              onClick={onSave}
            >
              {edit.isPending ? 'Saving…' : 'Save'}
            </button>
          </span>
        </div>
      </div>
    </>
  )
}
