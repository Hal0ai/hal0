// #2382 — the pure half of the Settings ▸ Network panel (NetworkPanel.jsx).
//
// hal0.toml [network] holds the browser origins the REST and WebSocket origin
// gates accept on top of the installer-managed HAL0_ALLOWED_ORIGINS:
// `public_url` (a reverse proxy's URL; its origin joins the allowlist) and
// `extra_origins` (a list of scheme://host[:port] entries). The server
// validates and normalises both (hal0.config.schema.NetworkConfig); this file
// only maps between the form and the POST /api/settings/network body.

/** Form state from the live `[network]` section (missing section = defaults). */
export function networkFormFromSettings(settings) {
  const net = (settings && settings.network) || {}
  const origins = Array.isArray(net.extra_origins) ? net.extra_origins : []
  return { publicUrl: net.public_url || '', origins: origins.join('\n') }
}

/** Textarea → origin list: one per line or comma-separated, blanks dropped. */
export function parseOriginLines(text) {
  return String(text || '')
    .split(/[\n,]/)
    .map((s) => s.trim())
    .filter(Boolean)
}

/**
 * The POST /api/settings/network body for a save, or null when nothing changed.
 *
 * Origins go as a diff against what the form was seeded from (`settings`):
 * the server applies the removals and additions to the list it reads from
 * disk under the config lock, so an origin someone else added or removed
 * since this page loaded is left as they set it. Sending the whole list
 * would write the page's stale copy back. An empty public URL clears it.
 */
export function networkEditBody(form, settings) {
  const live = networkFormFromSettings(settings)
  const body = {}
  const url = String(form.publicUrl || '').trim()
  if (url !== live.publicUrl) body.public_url = url === '' ? null : url
  const wanted = parseOriginLines(form.origins)
  const had = parseOriginLines(live.origins)
  const add = wanted.filter((o) => !had.includes(o))
  const remove = had.filter((o) => !wanted.includes(o))
  if (add.length) body.add_origins = add
  if (remove.length) body.remove_origins = remove
  return Object.keys(body).length ? body : null
}

/** True when two form states hold the same public URL and origin list. */
export function networkFormsEqual(a, b) {
  return (
    String(a.publicUrl || '').trim() === String(b.publicUrl || '').trim() &&
    parseOriginLines(a.origins).join('\n') === parseOriginLines(b.origins).join('\n')
  )
}

/** True when the form differs from what the server last reported. */
export function networkFormDirty(form, settings) {
  return !networkFormsEqual(form, networkFormFromSettings(settings))
}

/** A save failure → one readable line, preferring the per-field reasons. */
export function networkSaveError(err) {
  const details = err && err.details
  if (details && typeof details === 'object') {
    const lines = Object.entries(details)
      .filter(([k]) => k.startsWith('network'))
      .map(([, v]) => String(v).replace(/^Value error, /, ''))
    if (lines.length) return lines.join('; ')
  }
  return (err && err.message) || 'Save failed'
}
