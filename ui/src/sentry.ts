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

// Free-text secret scanner. A deliberate port of `redact_log_line` in
// `src/hal0/redaction.py`: `LOG_SECRET_RE` plus the shared shape pass
// `redact_secret_shapes` (#2403, #2413). KEEP THE TWO IN SYNC: a rule changed
// in `redaction.py` changes here too, and `tests/fixtures/redaction_shapes.json`
// is the shared case list that both test suites read
// (`tests/api/test_redact.py`, `ui/src/sentry.test.ts`). Dropping URL query
// strings and request bodies is not enough on its own: a credential also
// travels inside exception *text* (an upstream 401 that echoes the header it
// was sent), and that path is how a real token reached Sentry the first time
// this wiring was tested. Each alternative captures the prefix and the token
// separately so the prefix survives and only the token body is destroyed — an
// operator reading a redacted event can still see what kind of secret was
// present.
const SECRET_RE =
  /(Authorization:\s*Bearer\s+)(\S+)|(HAL0_BEARER_TOKEN=)(\S+)|(Bearer\s+)([A-Za-z0-9_\-.]+)|(client_id=)([A-Za-z0-9_\-.]{16,})|(\b(?:[A-Za-z][A-Za-z0-9_]*_KEY|KEY)=)(\S+)/gi

// ── Shared shape pass (port of `redact_secret_shapes`) ───────────────────────
//
// Differences from the Python, all forced by the JS regex dialect:
//   * No lookbehind. `ui/src/main.tsx` imports this module statically, so a
//     regex an older browser cannot parse would break the whole dashboard, not
//     just Sentry. The Python's lookbehinds are done in code instead.
//   * No inline `(?-i:...)` groups. The benign-name rules need case-sensitive
//     `tokens`/`Tokens`/`TOKENS` and camelCase humps inside an otherwise
//     case-insensitive match, so they are written with explicit `[xX]`
//     classes (`ci`) instead.
//   * No possessive quantifiers or atomic groups. Four Python rules rescan a
//     long run from every start position (quadratic on `---…`, `eyJeyJ…`,
//     `aaa…` before `://`, or one long name with no `=`). Those are done as
//     one linear scan of the maximal run, which gives the same result because
//     every start inside a run sees the same suffix.
//   * `iu` rather than `i`, so `K` (Kelvin) and `ſ` fold to `k` and `s` as in
//     Python's Unicode ignore-case.
// Not ported: Python's `\s` also matches U+001C..U+001F and U+0085, and `\b`
// in `SECRET_RE` is ASCII-only here; neither can hide a secret body.

const ci = (s: string): string =>
  s.replace(/[a-z]/gi, (c) => `[${c.toLowerCase()}${c.toUpperCase()}]`)

// `w` followed by a word end. A lowercase last letter ends a word before a
// capital (a camelCase hump: `tokensCount`); an uppercase one only before a
// non-alphanumeric (`tokensCOUNT`).
const ciEnd = (w: string): string => {
  const last = w.slice(-1)
  return (
    `${ci(w.slice(0, -1))}(?:${last.toLowerCase()}(?![a-z0-9])` +
    `|${last.toUpperCase()}(?![A-Za-z0-9]))`
  )
}

// `_SECRET_NAME`, as two tests on a whole name.
const SECRET_WORD_RE =
  /SECRET|TOKEN|PASSWORD|PASS|API[_-]?KEY|ACCESS[_-]?KEY|PRIVATE[_-]?KEY|ENCRYPTION[_-]?KEY|SALT/iu
const KEY_NAME_RE = /(?:^|[_-])KEY$/iu
const SECRET_REF_SUFFIX_RE = /_(?:env|file|path|dir)$/iu
// A name longer than this is judged as written, with no count part removed
// (`_NAME_JUDGED_WHOLE_MAX`).
const NAME_JUDGED_WHOLE_MAX = 128

// `_BENIGN_NAME_PART_RE`: plural `tokens` is a count only with a qualifier.
const TOKENS_COUNT_WORD =
  'max|min|num|n|total|prompt|completion|context|ctx|input|output|text|image' +
  '|audio|video|budget|requested|expected|generated|reasoning|remaining' +
  '|floor|prediction|predicted'
const TOKENS_WEAK_COUNT_WORD =
  'new|cache|cached|tool_?call|tool_?response|tool|used|extra|draft|content|thinking|generation'
const TOKENS_RATE_UNIT = 's|sec|second|ms|min|minute|hour|request|req|iteration|iter|step|1k|k'
const TOKENS_WHOLE_NAME_SUFFIX = 'in|out|completed|predicted|evaluated|cached|used'
// `tokens`, `Tokens` or `TOKENS`; the first two may be followed by a capital.
const TOKENS_WORD = '(?:[Tt]okens|TOKENS)'
const TOKENS_WORD_END = '(?:[Tt]okens(?![a-z0-9])|TOKENS(?![A-Za-z0-9]))'
const COUNT_QUALIFIED_RE = new RegExp(
  `(?:${ci(TOKENS_COUNT_WORD)})_?(?:(?:${ci(TOKENS_WEAK_COUNT_WORD)})_?)?${TOKENS_WORD_END}`,
  'y',
)
const WEAK_QUALIFIED_RE = new RegExp(
  `(?:${ci(TOKENS_WEAK_COUNT_WORD)})_?${TOKENS_WORD_END}`,
  'y',
)
const COUNT_SUFFIX_RE = new RegExp(
  `${TOKENS_WORD}_?(?:${ciEnd('count')}|${ci('per')}_?(?:` +
    `${TOKENS_RATE_UNIT.split('|').map(ciEnd).join('|')}))`,
  'y',
)
const WHOLE_NAME_RE = new RegExp(`${TOKENS_WORD}_?(?:${ci(TOKENS_WHOLE_NAME_SUFFIX)})$`, 'y')
const NAME_PART_RE = new RegExp(
  `${ci('tokenizer')}|${ci('token')}_?${ci('count')}` +
    `|${ci('pass')}(?=[a-zA-Z])(?!${ci('w')}(?:${ci('or')})?${ci('d')}|${ci('phrase')})`,
  'y',
)

// Length of the benign name part starting at `name[i]`, or 0. The Python tries
// its alternatives in this order at each position.
function benignPartAt(name: string, i: number): number {
  const prev = i > 0 ? name[i - 1] : ''
  // A word starts at the name's start, after a non-alphanumeric, or at a
  // camelCase hump (a lowercase letter or digit, then a capital).
  const wordStart =
    i === 0 || !/[A-Za-z0-9]/.test(prev) || (/[a-z0-9]/.test(prev) && /[A-Z]/.test(name[i]))
  const rules: Array<[RegExp, 'word' | 'name' | 'any']> = [
    [COUNT_QUALIFIED_RE, 'word'],
    [WEAK_QUALIFIED_RE, 'name'],
    [COUNT_SUFFIX_RE, 'word'],
    [WHOLE_NAME_RE, 'name'],
    [NAME_PART_RE, 'any'],
  ]
  for (const [re, where] of rules) {
    if (where === 'word' && !wordStart) continue
    if (where === 'name' && i !== 0) continue
    re.lastIndex = i
    const m = re.exec(name)
    if (m) return m[0].length
  }
  return 0
}

function stripBenignNameParts(name: string): string {
  let out = ''
  for (let i = 0; i < name.length; ) {
    const len = benignPartAt(name, i)
    if (len > 0) {
      i += len
    } else {
      out += name[i]
      i += 1
    }
  }
  return out
}

// `_is_secret_name`: true if `name` names a secret value rather than a count, a
// tokenizer, or where a secret is stored. A `-` counts as `_`.
function isSecretName(rawName: string): boolean {
  let name = rawName.replace(/^-+/, '').replace(/-/g, '_')
  if (SECRET_REF_SUFFIX_RE.test(name)) return false
  if (name.length <= NAME_JUDGED_WHOLE_MAX) name = stripBenignNameParts(name)
  return SECRET_WORD_RE.test(name) || KEY_NAME_RE.test(name)
}

// `NAME` + separator + value, the value double-quoted, single-quoted or bare.
// The Python matches the name as `[A-Za-z0-9_-]*WORD[A-Za-z0-9_-]*` after a
// lookbehind, which is quadratic on a long name. Here a name is a maximal run
// (a separator cannot start with a name character, so the Python name always
// ends at the run's end too) and the separator is tried once after it.
const NAME_RUN_RE = /[A-Za-z0-9_-]+/g
const NAME_VALUE_TAIL_RE = /(["']?\s*[=:]\s*)("[^"]*"|'[^']*'|[^"'\s,}&]+)/y

function maskNameValues(line: string): string {
  let out = ''
  let last = 0
  NAME_RUN_RE.lastIndex = 0
  for (let run = NAME_RUN_RE.exec(line); run; run = NAME_RUN_RE.exec(line)) {
    const name = run[0]
    const nameEnd = run.index + name.length
    // No secret word: the Python has no match here and carries on right after
    // the name, so a `NAME=value` inside the value is still seen.
    if (!SECRET_WORD_RE.test(name) && !KEY_NAME_RE.test(name)) continue
    NAME_VALUE_TAIL_RE.lastIndex = nameEnd
    const tail = NAME_VALUE_TAIL_RE.exec(line)
    if (!tail) continue
    // A match is consumed whole, benign or not.
    NAME_RUN_RE.lastIndex = nameEnd + tail[0].length
    if (!isSecretName(name)) continue
    const value = tail[2]
    const quote = value[0] === '"' || value[0] === "'" ? value[0] : ''
    out += line.slice(last, run.index) + `${name}${tail[1]}${quote}${MASK}${quote}`
    last = NAME_RUN_RE.lastIndex
  }
  return out + line.slice(last)
}

// HTTP auth schemes whose name is kept when an `Authorization` value is masked
// (`_AUTH_SCHEMES`).
const AUTH_SCHEMES =
  '(?:basic|bearer|token|apikey|api-key|key|bot|ssws|negotiate|digest|ntlm|oauth|hawk|dpop|aws4-hmac-sha256)'

// The first four `_SHAPE_RULES`; each keeps group 1 and masks the rest.
const AUTH_RULES: RegExp[] = [
  /(authorization:\s*(?:basic|token)\s+)[^\s'"]+/giu,
  /(bearer\s+)[A-Za-z0-9._~+/=-]+/giu,
  new RegExp(`(authorization["']?\\s*:\\s*["']?${AUTH_SCHEMES}\\s+)[^\\s"',]+`, 'giu'),
  // Any other `Authorization` value of 8+ characters is a raw credential.
  new RegExp(
    `(authorization["']?\\s*:\\s*["']?)(?!${AUTH_SCHEMES}(?:\\s|$))` +
      `(?=[^\\s"',]{8,})[^"',\\n]*[^\\s"',]`,
    'giu',
  ),
]

// True when `://` at `at` follows a URL scheme, `[a-z][a-z0-9+.-]*`. The Python
// puts the scheme in the pattern, which rescans the whole run from every
// letter in it; a run before `://` holds a scheme when it holds a letter.
function followsScheme(line: string, at: number): boolean {
  for (let j = at - 1; j >= 0; j -= 1) {
    const c = line.charCodeAt(j)
    if ((c >= 65 && c <= 90) || (c >= 97 && c <= 122)) return true
    const inClass = (c >= 48 && c <= 57) || c === 43 || c === 46 || c === 45
    if (!inClass) return false
  }
  return false
}

// `scheme://user:password@` and `scheme://<16+ chars>@` userinfo.
const URL_PASSWORD_RE = /(:\/\/[^/@\s:]*):[^/@\s]+@/g
const URL_LONG_USER_RE = /:\/\/[^/@\s:]{16,}@/g

const FLAG_RUN_RE = /--[A-Za-z0-9-]*/g
const FLAG_WORD_RE = /secret|token|password|pass|api-key|apikey|private-key/iu
const FLAG_VALUE_RE = /(=|\s+)[^-\s]\S*/y

// `--token VALUE`, `--api-key=VALUE`. The Python rescans from every `--` of a
// long dash run; every start in the run has the same outcome, so scan it once.
function maskFlags(line: string): string {
  let out = ''
  let last = 0
  FLAG_RUN_RE.lastIndex = 0
  for (let run = FLAG_RUN_RE.exec(line); run; run = FLAG_RUN_RE.exec(line)) {
    const end = run.index + run[0].length
    if (!FLAG_WORD_RE.test(run[0])) continue
    FLAG_VALUE_RE.lastIndex = end
    const tail = FLAG_VALUE_RE.exec(line)
    if (!tail) continue
    FLAG_RUN_RE.lastIndex = end + tail[0].length
    out += line.slice(last, run.index) + `${run[0]}${tail[1]}${MASK}`
    last = FLAG_RUN_RE.lastIndex
  }
  return out + line.slice(last)
}

const SEGMENT_RE = /[A-Za-z0-9_-]+/g
const JWT_REST_RE = /\.[A-Za-z0-9_-]{10,}\.[A-Za-z0-9_-]{10,}/y

// `eyJ<10+>.<10+>.<10+>`. Only the first `eyJ` of a dot-free segment can start a
// match (a later one has fewer characters after it and the same dot after the
// segment), so each segment is tried once instead of once per `eyJ`.
function maskJwts(line: string): string {
  if (!line.includes('eyJ')) return line
  let out = ''
  let last = 0
  SEGMENT_RE.lastIndex = 0
  for (let seg = SEGMENT_RE.exec(line); seg; seg = SEGMENT_RE.exec(line)) {
    const at = seg[0].indexOf('eyJ')
    if (at === -1 || seg[0].length - (at + 3) < 10) continue
    const end = seg.index + seg[0].length
    JWT_REST_RE.lastIndex = end
    const rest = JWT_REST_RE.exec(line)
    if (!rest) continue
    SEGMENT_RE.lastIndex = end + rest[0].length
    out += line.slice(last, seg.index + at) + MASK
    last = SEGMENT_RE.lastIndex
  }
  return out + line.slice(last)
}

/**
 * Mask secret SHAPES in one line of free text (no literal known in advance).
 * Keeps each key's name, quoting and separator so a reader still sees a secret
 * WAS present. Idempotent. Port of `redact_secret_shapes`, same rule order.
 */
export function redactSecretShapes(line: string): string {
  let out = line
  for (const re of AUTH_RULES) out = out.replace(re, `$1${MASK}`)
  const beforePassword = out
  out = beforePassword.replace(URL_PASSWORD_RE, (m, userinfo: string, offset: number) =>
    followsScheme(beforePassword, offset) ? `${userinfo}:${MASK}@` : m,
  )
  const beforeLongUser = out
  out = beforeLongUser.replace(URL_LONG_USER_RE, (m, offset: number) =>
    followsScheme(beforeLongUser, offset) ? `://${MASK}@` : m,
  )
  out = maskNameValues(out)
  out = maskFlags(out)
  out = out.replace(/(client_id=)[A-Za-z0-9_.-]{16,}/giu, `$1${MASK}`)
  out = maskJwts(out)
  // Case-sensitive, as in Python.
  out = out.replace(/(?:hf_|sk-|gh[pousr]_|github_pat_|xox[baprs]-)[A-Za-z0-9_-]{20,}/g, MASK)
  return out.replace(/AKIA[0-9A-Z]{16}/g, MASK)
}

// Exported for `sentry.test.ts` — the port is only trustworthy if it is
// pinned against the same cases as the Python original.
export function redactSecrets(value: unknown): unknown {
  if (typeof value !== 'string') return value
  const masked = value.replace(SECRET_RE, (match, ...groups: (string | undefined)[]) => {
    // Alternatives are (prefix, token) pairs; the first defined prefix wins.
    for (let i = 0; i < 10; i += 2) {
      if (groups[i] !== undefined) return `${groups[i]}${MASK}`
    }
    return match
  })
  return redactSecretShapes(masked)
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
