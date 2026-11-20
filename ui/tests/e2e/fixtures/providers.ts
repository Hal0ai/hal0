/**
 * Providers-tab test harness: a small stateful stand-in for the provider routes.
 *
 * Forced-mock serves the allow-listed GETs, but `apiMock` answers `/api/upstreams` with `[]` and
 * every unrouted `/api/*` with `{}` (both 2xx, so authoritative). A Providers spec therefore owns
 * the whole `/api/upstreams*` and `/api/providers/*` surface. The data comes from the same
 * builders and mutation fixtures the unit tests pin to `src/hal0/api/routes/providers.py`
 * (`src/api/mockFixtures.ts`); this file only keeps the changes a spec makes (a switched model, a
 * created provider, a removed one) so the next read sees them.
 *
 * Every request is logged in `calls` (method, path, parsed body) so a spec can assert what was
 * sent and that a key never rode in a URL.
 */
import type { Page, Route } from '@playwright/test'

import { MOCK_BUILDERS, MOCK_MUTATION_FIXTURES } from '../../../src/api/mockFixtures'

type Json = Record<string, any>

export type Call = { method: string; path: string; body: any }

export type Failure = { status: number; code?: string; message: string }

export type ProvidersCtl = {
  calls: Call[]
  /** Next PATCH of a model answers this failure instead of the fixture (cleared after use). */
  failPatch: Failure | null
  /** DELETE /api/upstreams/{name} answers this failure while set. */
  failDelete: Failure | null
  /** POST /api/providers/{name}/credentials answers this failure while set. */
  failCredentials: Failure | null
  /** Probe answers, first to last; the last repeats. Default: one success. */
  probes: Array<'ok' | 'denied'>
  /** PATCH /api/upstreams/{name} answers this failure while set (clears after one use). */
  failUpstreamPatch: Failure | null
  /** A refresh answers `stale: true` with no warning (the provider could not be reached). */
  refreshStale: boolean
  /** Hold the create (POST /api/upstreams) until `release()` is called. */
  holdCreate: boolean
  release: () => void
  callsTo: (method: string, pathRe: RegExp) => Call[]
}

const ID_RE = /^\/api\/(upstreams|providers)/

function clone<T>(v: T): T {
  return JSON.parse(JSON.stringify(v))
}

function envelope(route: Route, f: Failure) {
  return route.fulfill({
    status: f.status,
    contentType: 'application/json',
    body: JSON.stringify({ error: { code: f.code ?? 'system.unknown', message: f.message, details: null } }),
  })
}

function ok(route: Route, body: unknown, status = 200) {
  return route.fulfill({ status, contentType: 'application/json', body: JSON.stringify(body) })
}

/** Install the routes on `page` before `page.goto`. */
export async function installProviders(
  page: Page,
  opts: { switchedOff?: string[]; keyless?: boolean } = {},
): Promise<ProvidersCtl> {
  const upstreams: Json[] = clone((MOCK_BUILDERS.upstreams as any)('/api/upstreams', []))
  for (const u of upstreams) if (opts.switchedOff?.includes(u.name)) u.enabled = false
  // `keyless` adds "ct150-check": a per-model OpenRouter provider added without a key. The server
  // cannot fetch its list, so it answers `stale: true`, `fetched_at: null` and every row
  // `discovered: false` (routes/providers.py `_discovered`: no cache entry gives `([], None, True)`).
  const keylessRows = ['vendor/alpha', 'vendor/beta', 'vendor/gamma', 'vendor/delta'].map((id, i) => ({
    id,
    canonical_id: `ct150-check/${id}`,
    enabled: i < 2,
    type: 'chat',
    context: 128000,
    price_in: 0.3,
    price_out: 1.2,
    price_unit: 'Mtok',
    discovered: false,
    recommended: false,
    in_use_by: [],
  }))
  if (opts.keyless) {
    const proto = clone(upstreams.find((u) => u.name === 'openrouter')!)
    upstreams.push({
      ...proto,
      name: 'ct150-check',
      catalog_id: 'openrouter',
      auth_value_env: 'CT150_CHECK_API_KEY',
      auth_key_present: false,
      curated: true,
      models: [],
    })
  }
  const listings = new Map<string, Json>()
  if (opts.keyless) {
    listings.set('ct150-check', {
      name: 'ct150-check',
      curated: true,
      protected: false,
      fetched_at: null,
      stale: true,
      warning: null,
      models: keylessRows,
    })
  }
  for (const u of upstreams) {
    if (listings.has(u.name)) continue
    listings.set(u.name, clone((MOCK_BUILDERS.upstreamModels as any)('', ['', u.name])))
  }
  let probeCount = 0
  let gate: (() => void) | null = null

  const ctl: ProvidersCtl = {
    calls: [],
    failPatch: null,
    failDelete: null,
    failCredentials: null,
    failUpstreamPatch: null,
    refreshStale: false,
    probes: ['ok'],
    holdCreate: false,
    release: () => gate?.(),
    callsTo: (method, pathRe) => ctl.calls.filter((c) => c.method === method && pathRe.test(c.path)),
  }

  const recount = (name: string) => {
    const u = upstreams.find((x) => x.name === name)
    const l = listings.get(name)
    if (!u || !l) return
    u.enabled_count = l.curated ? l.models.filter((m: Json) => m.enabled).length : 0
    u.models = l.models.filter((m: Json) => m.enabled).map((m: Json) => m.id)
    u.models_total = l.curated ? l.models.length : 0
  }

  await page.route(/\/api\/(upstreams|providers)(\/|\?|$)/, async (route) => {
    const req = route.request()
    const url = new URL(req.url())
    const path = url.pathname
    if (!ID_RE.test(path)) return route.fallback()
    const method = req.method()
    let body: any = null
    try {
      body = req.postDataJSON()
    } catch {
      body = null
    }
    ctl.calls.push({ method, path, body })
    let m: RegExpMatchArray | null

    if (path === '/api/providers/catalog') return ok(route, (MOCK_BUILDERS.providersCatalog as any)('', []))

    if (path === '/api/providers/probe') {
      const kind = ctl.probes[Math.min(probeCount, ctl.probes.length - 1)]
      probeCount += 1
      return ok(route, MOCK_MUTATION_FIXTURES.providerProbe(kind === 'ok'))
    }

    if ((m = path.match(/^\/api\/providers\/([^/]+)\/credentials$/))) {
      if (ctl.failCredentials) return envelope(route, ctl.failCredentials)
      return ok(route, { ok: true, key: body?.key, name: decodeURIComponent(m[1]) })
    }

    if (path === '/api/upstreams' && method === 'GET') return ok(route, upstreams)

    if (path === '/api/upstreams' && method === 'POST') {
      if (ctl.holdCreate) await new Promise<void>((r) => (gate = r))
      const proto = clone(upstreams.find((u) => u.name === 'openrouter')!)
      const rows = (body.models ?? []).map((r: Json) => ({
        id: r.id,
        canonical_id: `${body.name}/${r.id}`,
        enabled: !!r.enabled,
        type: r.type ?? 'chat',
        context: r.context ?? null,
        price_in: r.price_in ?? null,
        price_out: r.price_out ?? null,
        price_unit: r.price_unit ?? 'Mtok',
        discovered: true,
        recommended: false,
        in_use_by: [],
      }))
      const created = {
        ...proto,
        name: body.name,
        url: body.url,
        catalog_id: body.catalog_id ?? null,
        auth_style: body.auth_style ?? 'bearer',
        auth_value_env: body.auth_value_env ?? '',
        auth_key_present: false,
        curated: true,
        models: [],
      }
      upstreams.push(created)
      listings.set(body.name, {
        name: body.name,
        curated: true,
        protected: false,
        fetched_at: 1763337600,
        stale: false,
        warning: null,
        models: rows,
      })
      recount(body.name)
      return ok(route, created, 201)
    }

    if ((m = path.match(/^\/api\/upstreams\/([^/]+)\/models$/)) && method === 'GET') {
      const l = listings.get(decodeURIComponent(m[1]))
      if (!l) return envelope(route, { status: 404, code: 'upstream.not_found', message: 'unknown upstream' })
      return ok(route, l)
    }

    if ((m = path.match(/^\/api\/upstreams\/([^/]+)\/models\/(.+)$/)) && method === 'PATCH') {
      const name = decodeURIComponent(m[1])
      const modelId = m[2].split('/').map(decodeURIComponent).join('/')
      if (ctl.failPatch) {
        const f = ctl.failPatch
        ctl.failPatch = null
        return envelope(route, f)
      }
      const answer = MOCK_MUTATION_FIXTURES.upstreamModelPatch(name, modelId, body ?? {})
      if (answer.status !== 200) return route.fulfill({ status: answer.status, contentType: 'application/json', body: JSON.stringify(answer.body) })
      const l = listings.get(name)
      const row = l?.models.find((r: Json) => r.id === modelId)
      if (row) Object.assign(row, body)
      recount(name)
      return ok(route, answer.body)
    }

    if ((m = path.match(/^\/api\/upstreams\/([^/]+)\/refresh$/))) {
      const name = decodeURIComponent(m[1])
      const l = listings.get(name)
      const u = upstreams.find((x) => x.name === name)
      if (l && u && !l.curated && !l.protected) {
        // Converting: everything it served stays on, the listing turns per-model.
        l.curated = true
        l.stale = false
        l.fetched_at = 1763341200
        u.curated = true
        recount(name)
        return ok(route, l)
      }
      if (ctl.refreshStale && l) return ok(route, { ...l, stale: true, warning: null })
      return ok(route, MOCK_MUTATION_FIXTURES.upstreamRefresh(name))
    }

    if ((m = path.match(/^\/api\/upstreams\/([^/]+)\/test$/))) {
      return ok(route, { ok: true, latency_ms: 42, models_count: 3, error: null })
    }

    if ((m = path.match(/^\/api\/upstreams\/([^/]+)$/))) {
      const name = decodeURIComponent(m[1])
      if (method === 'DELETE') {
        if (ctl.failDelete) return envelope(route, ctl.failDelete)
        const i = upstreams.findIndex((x) => x.name === name)
        if (i >= 0) upstreams.splice(i, 1)
        listings.delete(name)
        return ok(route, { ok: true })
      }
      if (method === 'PATCH') {
        if (ctl.failUpstreamPatch) {
          const f = ctl.failUpstreamPatch
          ctl.failUpstreamPatch = null
          return envelope(route, f)
        }
        const u = upstreams.find((x) => x.name === name)
        if (u) Object.assign(u, body ?? {})
        return ok(route, u ?? {})
      }
    }
    return route.fallback()
  })

  return ctl
}

/** Watch the page for console errors and uncaught exceptions. `allow` filters expected noise,
 * such as the browser's own "Failed to load resource" line for an HTTP failure a test forces. */
export function watchErrors(page: Page, allow: RegExp[] = []) {
  const seen: string[] = []
  page.on('console', (msg) => {
    if (msg.type() !== 'error') return
    const t = msg.text()
    if (allow.some((re) => re.test(t))) return
    seen.push(`console: ${t}`)
  })
  page.on('pageerror', (e) => seen.push(`pageerror: ${String(e)}`))
  return {
    seen,
    /** The browser logs one line per failed request; a spec that forces failures allows them. */
    expectClean: () => seen,
  }
}
