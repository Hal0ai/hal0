// Settings ▸ Data ▸ Memory — engine limits that live under [memory.lanes].
//
// Schema-driven rows (like the reranker panel): the generic settings PUT
// writes hal0.toml and its apply plan re-renders the engine drop-in. Today:
// the reflect / knowledge-page wall clock (#3759) and the
// consolidation batch size (#3762).
import { useState } from 'react'
import { useSettingsClient } from '../../data/settingsClient.js'
import { AdvRow, _schemaField, _getIn, _deepMergePatch, _advCoerce } from '../../shared/SchemaRow.jsx'

export const ENGINE_LIMIT_ROWS = [
  { key: 'memory.lanes.reflect.wall_timeout_s', label: 'Reflect and page refresh time limit (s)' },
  { key: 'memory.lanes.consolidation.batch_size', label: 'Memories per consolidation call' },
]

export function EngineLimitsPanel({ registry }) {
  const { settings, update, schema: schemaQuery } = useSettingsClient({ schema: true })
  const schema = schemaQuery?.data || null
  const live = settings.data || null

  const fields = {}
  for (const r of ENGINE_LIMIT_ROWS) fields[r.key] = _schemaField(schema, r.key)
  // An API that predates a key has no schema entry for it: leave the row out
  // rather than offer a control whose save the server would ignore.
  const rows = ENGINE_LIMIT_ROWS.filter(r => fields[r.key])

  const [buf, setBuf] = useState({})
  const onChange = (dotKey, value) => setBuf(b => ({ ...b, [dotKey]: value }))

  const dirtyKeys = Object.keys(buf).filter(k => {
    const { ok, value } = _advCoerce(fields[k], buf[k])
    if (!ok) return true
    const cur = _getIn(live, k)
    return value !== (cur === undefined ? (fields[k]?.default ?? null) : cur)
  })
  const invalidKeys = dirtyKeys.filter(k => !_advCoerce(fields[k], buf[k]).ok)
  const canSave = dirtyKeys.length > 0 && invalidKeys.length === 0 && !update.isPending

  const doSave = async () => {
    let patch = {}
    for (const k of dirtyKeys) {
      const { value } = _advCoerce(fields[k], buf[k])
      patch = _deepMergePatch(patch, k.split('.').reverse().reduce((acc, part) => ({ [part]: acc }), value))
    }
    try {
      await update.mutateAsync(patch)
      setBuf({})
      window.__hal0Toast && window.__hal0Toast('Engine limits saved', 'ok')
    } catch (e) {
      window.__hal0Toast && window.__hal0Toast(`Save failed — ${e?.message || 'see logs'}`, 'err')
    }
  }

  if (rows.length === 0) return null
  return (
    <div className="s-panel" style={{ marginBottom: 12 }} data-testid="mem-engine-limits">
      <div className="s-row" style={{ paddingBottom: 4, borderBottom: '1px solid var(--line)' }}>
        <div className="k">
          <span>Engine limits</span>
          <FieldInfoIcon description="hal0.toml [memory.lanes] · limits hal0 renders into the memory engine's drop-in" />
        </div>
      </div>
      {rows.map(r => (
        <AdvRow
          key={r.key}
          dotKey={r.key}
          field={fields[r.key]}
          live={_getIn(live, r.key)}
          buf={buf[r.key]}
          onChange={onChange}
          registry={registry}
          label={r.label}
        />
      ))}
      <div style={{ display: 'flex', justifyContent: 'flex-end', gap: 8, padding: '8px 12px 4px' }}>
        {dirtyKeys.length > 0 && (
          <button className="btn ghost sm" onClick={() => setBuf({})}>Reset</button>
        )}
        <button className="btn sm" data-testid="mem-engine-limits-save" disabled={!canSave} onClick={doSave}>
          {update.isPending ? 'Saving…' : 'Save engine limits'}
        </button>
      </div>
    </div>
  )
}
