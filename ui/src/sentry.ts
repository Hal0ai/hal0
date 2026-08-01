// Optional browser-side Sentry for the hal0 dashboard.
//
// Mirrors the backend posture in `src/hal0/observability/sentry.py`: hal0 is a
// self-hosted appliance, so this is INERT unless a DSN is configured at build
// time. The SDK sits behind a dynamic `import()`, so it lands in its own
// rollup chunk that `index.html` does not reference: with no DSN the chunk is
// emitted to dist/ but never fetched, no listener is installed, and no request
// leaves the browser. (Verify after a build: the entry chunk named in
// dist/index.html must contain no `sentry-trace` string.)
//
// Configuration (Vite env, baked in at `npm run build`):
//
//   VITE_SENTRY_DSN                 the DSN. Unset/empty ⇒ hard off.
//   VITE_SENTRY_ENVIRONMENT         environment tag. Default 'development'.
//   VITE_SENTRY_TRACES_SAMPLE_RATE  float in [0,1]. Default 0 (errors only).
//
// Because these are build-time values, a dashboard already deployed to
// `/usr/lib/hal0/current/ui/dist` cannot be switched on without a rebuild —
// which is the point: an artifact built without a DSN has no endpoint to
// report to, and grepping it for the ingest host proves it.
//
// Privacy posture (strict scrub), matching the backend:
//   * `sendDefaultPii: false`, and the `user` block is dropped outright.
//   * every URL is truncated at `?` before it is sent. hal0 accepts
//     `?api_key=` on SSE/WS URLs (browsers cannot set headers on those), so a
//     query string is assumed to be credential-bearing.
//   * no Session Replay, no `attachStacktrace` of DOM content — a replay of a
//     chat pane would ship prompt text off the box.

// Injected by vite.config.ts `define`.
declare const __HAL0_UI_VERSION__: string

type SentryModule = typeof import('@sentry/react')

// Populated once the dynamic import resolves. Null while Sentry is off, or
// during the window between page load and the chunk arriving.
let sentry: SentryModule | null = null

function readSampleRate(raw: unknown, fallback: number): number {
  const value = Number.parseFloat(String(raw ?? ''))
  if (!Number.isFinite(value) || value < 0 || value > 1) return fallback
  return value
}

// Truncate at the first '?'. Applied to every URL-shaped field we know Sentry
// populates. Non-strings pass through untouched.
function stripQuery(value: unknown): unknown {
  if (typeof value !== 'string') return value
  const cut = value.indexOf('?')
  return cut === -1 ? value : value.slice(0, cut)
}

// Same sentinel the Python side uses, so a masked value looks identical
// whichever process produced the event.
const MASK = '***REDACTED***'

// Free-text secret scanner. This is a deliberate port of `LOG_SECRET_RE` in
// `src/hal0/api/_redact.py` — KEEP THE TWO IN SYNC. Dropping URL query strings
// and request bodies is not enough on its own: a credential also travels
// inside exception *text* (an upstream 401 that echoes the header it was sent),
// and that path is how a real token reached Sentry the first time this wiring
// was tested. Each alternative captures the prefix and the token separately so
// the prefix survives and only the token body is destroyed — an operator
// reading a redacted event can still see what kind of secret was present.
const SECRET_RE =
  /(Authorization:\s*Bearer\s+)(\S+)|(HAL0_BEARER_TOKEN=)(\S+)|(Bearer\s+)([A-Za-z0-9_\-.]+)|(client_id=)([A-Za-z0-9_\-.]{16,})|(\b(?:[A-Za-z][A-Za-z0-9_]*_KEY|KEY)=)(\S+)/gi

// Exported for `sentry.test.ts` — the port is only trustworthy if it is
// pinned against the same cases as the Python original.
export function redactSecrets(value: unknown): unknown {
  if (typeof value !== 'string') return value
  return value.replace(SECRET_RE, (match, ...groups: (string | undefined)[]) => {
    // Alternatives are (prefix, token) pairs; the first defined prefix wins.
    for (let i = 0; i < 10; i += 2) {
      if (groups[i] !== undefined) return `${groups[i]}${MASK}`
    }
    return match
  })
}

/**
 * Initialise browser Sentry. Safe to call unconditionally and more than once;
 * the second call is a no-op. Resolves to true when Sentry ends up live.
 *
 * Deliberately async + dynamically imported so that a build WITHOUT a DSN
 * never pulls the SDK into the bundle. The trade-off is a short window at
 * startup where an error is not yet captured; a synchronous static import
 * would close that window at the cost of shipping the SDK to every install.
 */
export async function initSentry(): Promise<boolean> {
  if (sentry) return true

  const dsn = String(import.meta.env.VITE_SENTRY_DSN ?? '').trim()
  if (!dsn) return false

  try {
    const mod = await import('@sentry/react')
    const tracesSampleRate = readSampleRate(import.meta.env.VITE_SENTRY_TRACES_SAMPLE_RATE, 0)

    mod.init({
      dsn,
      release: `hal0-ui@${__HAL0_UI_VERSION__}`,
      environment: String(import.meta.env.VITE_SENTRY_ENVIRONMENT ?? '').trim() || 'development',
      sendDefaultPii: false,
      tracesSampleRate,
      // Only add the tracing integration when it will actually sample —
      // otherwise it instruments every fetch/XHR on the dashboard's polling
      // loops for events that are immediately discarded.
      integrations: tracesSampleRate > 0 ? [mod.browserTracingIntegration()] : [],
      beforeSend(event) {
        try {
          delete event.user
          if (event.request) {
            event.request.url = stripQuery(event.request.url) as string | undefined
            delete event.request.query_string
            delete event.request.cookies
            delete event.request.data
          }
          if (Array.isArray(event.breadcrumbs)) {
            for (const crumb of event.breadcrumbs) {
              if (typeof crumb?.message === 'string') {
                crumb.message = redactSecrets(crumb.message) as string
              }
              if (crumb?.data && typeof crumb.data === 'object') {
                const data = crumb.data as Record<string, unknown>
                if ('url' in data) data.url = stripQuery(data.url)
                for (const [key, item] of Object.entries(data)) {
                  if (typeof item === 'string') data[key] = redactSecrets(item) as string
                }
              }
            }
          }

          // The text surfaces. `message` is the top-level string form,
          // `logentry` its structured twin, and `exception.values[].value` is
          // what actually carries an upstream 401's echoed header.
          if (typeof event.message === 'string') {
            event.message = redactSecrets(event.message) as string
          }
          if (event.logentry && typeof event.logentry.message === 'string') {
            event.logentry.message = redactSecrets(event.logentry.message) as string
          }
          for (const value of event.exception?.values ?? []) {
            if (typeof value.value === 'string') {
              value.value = redactSecrets(value.value) as string
            }
          }

          return event
        } catch {
          // Fail closed, same as the backend: an event we could not scrub is
          // an event we do not send.
          return null
        }
      },
    })
    mod.setTag('hal0.component', 'ui')
    sentry = mod
    return true
  } catch {
    // A blocked chunk fetch or a DSN typo must never break the dashboard.
    return false
  }
}

/**
 * Report a React render-time crash. No-op while Sentry is off.
 *
 * Called from `ViewErrorBoundary.componentDidCatch` in `dash/main.jsx`: that
 * boundary swallows the throw to keep the chrome alive, so without an explicit
 * capture here a black-screened view would be invisible in Sentry.
 */
export function captureUiException(err: unknown, componentStack?: string): void {
  if (!sentry) return
  try {
    sentry.withScope((scope) => {
      if (componentStack) scope.setContext('react', { componentStack })
      sentry!.captureException(err)
    })
  } catch {
    return
  }
}
