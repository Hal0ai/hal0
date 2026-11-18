// Where an address from before the Providers tab now lives, read from a whole hash.
// `parseRoute` (main.jsx) calls this the way it calls `legacyMemoryRedirect`; the rule
// itself is `legacyModelsRedirect` in models/providers-pure.ts.
//
// HAL0-SUNSET: v1.9.0 — remove with legacyModelsRedirect.

import { legacyModelsRedirect } from './models/providers-pure'

/** `#models/upstream` → `#models/providers`; null when the hash needs no redirect. */
export function modelsRedirectForHash(hash: string): string | null {
  const raw = hash.replace(/^#/, '')
  const at = raw.indexOf('?')
  const path = at === -1 ? raw : raw.slice(0, at)
  const query = new URLSearchParams(at === -1 ? '' : raw.slice(at + 1))
  const [route, ...rest] = path.split('/')
  const moved = legacyModelsRedirect(route, rest.join('/') || undefined, query)
  return moved ? `#${moved}` : null
}
