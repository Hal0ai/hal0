// ─── schema-driven settings form (R5 data seam · REWORK §K) ──────────────────
//
// The buffer / dirty-tracking / coerce / deep-merge-patch / manual-restart
// confirm-gate machinery that AdvancedPage grew inline, extracted once so any
// schema-driven page (Advanced, Backend & GPU) submits a *typed intent* —
// "here are the dotted keys I edit" — instead of re-implementing the edit
// loop. Reads its field schema + reload class from the one settings client,
// so page copy and effect classification stay server/registry-sourced.
//
// Contract: give it the settings client and the list of dotted keys the page
// edits. It returns the buffer state, per-key dirty/validity, and a `submit`
// that gates on any dirty manual-restart key (surfacing them via
// `confirmKeys` so the page renders its ConfirmDialog) before writing.
import { useMemo, useState } from 'react'
import { _advCoerce, _getIn, _deepMergePatch } from '../shared/SchemaRow.jsx'

/**
 * @param {object} client - the object returned by useSettingsClient()
 * @param {string[]} keys - dotted config keys this form owns
 */
export function useSettingsForm(client, keys) {
  const { live, registry } = client
  const [buf, setBuf] = useState({})
  const [confirmKeys, setConfirmKeys] = useState(null)

  const set = (dotKey, value) => setBuf((b) => ({ ...b, [dotKey]: value }))
  const reset = () => setBuf({})

  // Field schema per key, memoised on the schema identity.
  const fields = useMemo(() => {
    const out = {}
    for (const k of keys) out[k] = client.field(k)
    // client.field closes over the (cache-forever) schema; the key list is
    // stable per page. Memo on the schema payload + joined keys so this only
    // recomputes when the schema actually arrives.
    return out
  }, [client.schema.data, keys.join('|')])

  // A key is dirty when its coerced buffer value differs from the live one.
  // An invalid buffer counts as dirty so Save stays visible-but-disabled.
  const dirtyKeys = Object.keys(buf).filter((k) => {
    const { ok, value } = _advCoerce(fields[k], buf[k])
    if (!ok) return true
    const cur = _getIn(live, k)
    return value !== (cur === undefined ? (fields[k]?.default ?? null) : cur)
  })
  const invalidKeys = dirtyKeys.filter((k) => !_advCoerce(fields[k], buf[k]).ok)
  const canSave = dirtyKeys.length > 0 && invalidKeys.length === 0 && !client.update.isPending

  const buildPatch = () => {
    let patch = {}
    for (const k of dirtyKeys) {
      const { value } = _advCoerce(fields[k], buf[k])
      patch = _deepMergePatch(
        patch,
        k.split('.').reverse().reduce((acc, part) => ({ [part]: acc }), value),
      )
    }
    return patch
  }

  // Restart hint derived from the reload-class source over the keys we write.
  const restartKeys = () =>
    dirtyKeys.filter((k) => {
      const cls = client.reloadClass(k)?.apply_class
      return cls && cls !== 'immediate'
    })

  // Write now (no gate). Returns the react-query promise so the caller can
  // toast on resolve/reject.
  const commit = async () => {
    const patch = buildPatch()
    const needsRestart = restartKeys().length > 0
    await client.save(patch)
    reset()
    return { needsRestart }
  }

  // Gated submit: if any dirty key is manual-restart, stash the set and let
  // the page confirm; otherwise commit immediately.
  const submit = async () => {
    const manual = dirtyKeys.filter((k) => client.reloadClass(k)?.apply_class === 'manual-restart')
    if (manual.length > 0) {
      setConfirmKeys(manual)
      return { deferred: true }
    }
    return commit()
  }

  const clearConfirm = () => setConfirmKeys(null)

  return {
    buf,
    set,
    reset,
    fields,
    dirtyKeys,
    invalidKeys,
    canSave,
    confirmKeys,
    clearConfirm,
    submit,
    commit,
    registry,
  }
}
