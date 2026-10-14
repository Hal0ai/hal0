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
//   * error AND transaction events are scrubbed (`beforeSend`,
//     `beforeSendTransaction`): every text field through `redactSecrets`, a
//     value under a secret-named key or a sensitive header masked whole, and a
//     hook that throws drops the event.
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

// ── Matching Python's `re` ───────────────────────────────────────────────────
//
// Python's `re.I` and `\s` are Unicode-aware and JS's are not, in ways that let
// a secret through (a `\x1c` in a separator, a Kelvin sign in a name). `rx`
// builds a RegExp that matches like the Python source:
//   * `\s` is Python's whitespace set: JS's, minus U+FEFF, plus U+001C..U+001F
//     and U+0085. `\S` is its complement.
//   * with `fold`, `i`, `s` and `k` also match what Python's `re.I` folds to
//     them: U+0130 and U+0131 (Turkish İ, ı) for `i`, U+017F (ſ) for `s`, U+212A
//     (Kelvin K) for `k`, and a class holding a letter range gets all four.
// The Python rules without `re.I` (JWTs, token prefixes) are built with
// `fold = false` or as plain literals. What JS cannot reproduce errs towards
// masking MORE. The one thing a regex cannot say is Python's Unicode `\b`
// (`SECRET_RE` has one, ASCII-only in JS), so `maskLogSecrets` drops a `_KEY=`
// match that follows a non-ASCII letter itself. A differential run of 120k
// generated lines, with these characters spliced in, found no other difference.
const WS_BODY = String.raw`\t-\r\x1c-\x20\x85\xa0\u1680\u2000-\u200a\u2028\u2029\u202f\u205f\u3000`
const FOLD_ALL = String.raw`\u0130\u0131\u017f\u212a`
const FOLD_LETTER: Record<string, string> = {
  i: String.raw`\u0130\u0131`,
  s: String.raw`\u017f`,
  k: String.raw`\u212a`,
}

function rx(src: string, flags: string, fold = true): RegExp {
  let out = ''
  let inClass = false
  let negated = false
  let classBody = ''
  for (let n = 0; n < src.length; n += 1) {
    const c = src[n]
    if (c === '\\') {
      const d = src[n + 1]
      n += 1
      let piece: string
      if (d === 's') piece = inClass ? WS_BODY : `[${WS_BODY}]`
      else if (d === 'S') {
        if (inClass) throw new Error('\\S inside a class')
        piece = `[^${WS_BODY}]`
      } else piece = `\\${d}`
      out += piece
    } else if (inClass) {
      if (c === ']') {
        inClass = false
        if (fold && !negated) {
          let extra = ''
          if (/[a-z]-[a-z]|[A-Z]-[A-Z]/.test(classBody)) extra = FOLD_ALL
          else for (const l of classBody.toLowerCase()) extra += FOLD_LETTER[l] ?? ''
          // A trailing `-` must stay last or it would turn into a range.
          out = classBody.endsWith('-') ? `${out.slice(0, -1)}${extra}-` : out + extra
        } else if (fold && /[A-Za-z]/.test(classBody)) {
          throw new Error('letters in a negated class')
        }
        out += ']'
      } else {
        classBody += c
        out += c
      }
    } else if (c === '[') {
      inClass = true
      classBody = ''
      negated = src[n + 1] === '^'
      out += negated ? '[^' : '['
      if (negated) n += 1
    } else if (fold && FOLD_LETTER[c.toLowerCase()] !== undefined) {
      out += `[${c.toLowerCase()}${c.toUpperCase()}${FOLD_LETTER[c.toLowerCase()]}]`
    } else {
      out += c
    }
  }
  return new RegExp(out, flags)
}

// The `Authorization: Bearer`, `HAL0_BEARER_TOKEN=` and `*_KEY=` alternatives
// end where their value starts: it runs to the next whitespace except where a
// second secret is packed after a `;` or `,` (`bareValueEnd`, #2529).
const SECRET_RE = rx(
  String.raw`(Authorization:\s*Bearer\s+)(?=\S)|(HAL0_BEARER_TOKEN=)(?=\S)|(Bearer\s+)([A-Za-z0-9_\-.]+)|(client_id=)([A-Za-z0-9_\-.]{16,})|(\b(?:[A-Za-z][A-Za-z0-9_]*_KEY|KEY)=)(?=\S)`,
  'giu',
)

// A Unicode word character, as Python's `\w`: a letter, a number or `_`.
const WORD_CHAR_END_RE = /[\p{L}\p{N}_]$/u

// `LOG_SECRET_RE` in `redact_log_line`. The `\b` in the pattern is ASCII-only,
// so a `_KEY=` match that follows a non-ASCII letter (`Éfoo_KEY=`) is one
// Python's `\b` does not allow: skip it and carry on one character later, as
// Python would.
function maskLogSecrets(value: string): string {
  let out = ''
  let last = 0
  SECRET_RE.lastIndex = 0
  for (let m = SECRET_RE.exec(value); m; m = SECRET_RE.exec(value)) {
    if (m[7] !== undefined && WORD_CHAR_END_RE.test(value.slice(Math.max(0, m.index - 2), m.index))) {
      SECRET_RE.lastIndex = m.index + 1
      continue
    }
    let start: number
    let end: number
    if (m[4] !== undefined || m[6] !== undefined) {
      // A Bearer token or a client_id holds no `;` or `,`: the match ends with
      // it, or past a secret it swallows (#2595).
      start = m.index + (m[3] ?? m[5]).length
      end = swallowingEnd(value, start, m.index + m[0].length, LOG_TOKEN, true)
    } else {
      start = m.index + m[0].length
      end = isQuote(value[start])
        ? quotedLogValueEnd(value, start)
        : bareValueEnd(value, start, LOG_VALUE, true)
    }
    if (end > start) {
      out += value.slice(last, start) + MASK
      last = end
    }
    SECRET_RE.lastIndex = end
  }
  return out + value.slice(last)
}

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
//   * Python's `re.I` and `\s` are matched through `rx`, above.

// Case-insensitive, with the `re.I` folds of `rx`, for the benign-name parts.
const ci = (s: string): string =>
  s.replace(/[a-z]/gi, (c) => {
    const l = c.toLowerCase()
    return `[${l}${c.toUpperCase()}${FOLD_LETTER[l] ?? ''}]`
  })
// Alphanumeric in Python's `re.I` sense: also what folds to a letter.
const NOT_LOWER_ALNUM = `(?![a-z0-9${FOLD_ALL}])`
const NOT_ALNUM = `(?![A-Za-z0-9${FOLD_ALL}])`

// `w` followed by a word end. A lowercase last letter ends a word before a
// capital (a camelCase hump: `tokensCount`); an uppercase one only before a
// non-alphanumeric (`tokensCOUNT`).
const ciEnd = (w: string): string => {
  const last = w.slice(-1)
  return (
    `${ci(w.slice(0, -1))}(?:${last.toLowerCase()}${NOT_LOWER_ALNUM}` +
    `|[${last.toUpperCase()}${FOLD_LETTER[last.toLowerCase()] ?? ''}]${NOT_ALNUM})`
  )
}

// `_SECRET_NAME`, as two tests on a whole name.
const SECRET_WORD_RE = rx(
  String.raw`SECRET|TOKEN|PASSWORD|PASS|API[_-]?KEY|ACCESS[_-]?KEY|PRIVATE[_-]?KEY|ENCRYPTION[_-]?KEY|SALT`,
  'iu',
)
const KEY_NAME_RE = rx(String.raw`(?:^|[_-])KEY$`, 'iu')
const SECRET_REF_SUFFIX_RE = rx(String.raw`_(?:env|file|path|dir)$`, 'iu')
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
const TOKENS_WORD_END = `(?:[Tt]okens${NOT_LOWER_ALNUM}|TOKENS${NOT_ALNUM})`
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
    `|${ci('pass')}(?=[a-zA-Z${FOLD_ALL}])(?!${ci('w')}(?:${ci('or')})?${ci('d')}|${ci('phrase')})`,
  'y',
)

// Length of the benign name part starting at `name[i]`, or 0. The Python tries
// its alternatives in this order at each position.
function benignPartAt(name: string, i: number): number {
  const prev = i > 0 ? name[i - 1] : ''
  // A word starts at the name's start, after a non-alphanumeric, or at a
  // camelCase hump (a lowercase letter or digit, then a capital).
  const wordStart =
    i === 0 || !/[A-Za-z0-9\u0130\u0131\u017f\u212a]/.test(prev) || (/[a-z0-9]/.test(prev) && /[A-Z]/.test(name[i]))
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
const NAME_RUN_RE = rx(String.raw`[A-Za-z0-9_-]+`, 'gu')
// Up to the value, which a lookahead checks for (`_NAME_SEP_RE`): quoted
// (closed or not, #2593) or bare.
const NAME_VALUE_TAIL_RE = rx(String.raw`(["']?\s*[=:]\s*)(?=[^\s,}&])`, 'yu')

function maskNameValues(line: string, log: boolean): string {
  let out = ''
  let last = 0
  NAME_RUN_RE.lastIndex = 0
  for (let run = NAME_RUN_RE.exec(line); run; run = NAME_RUN_RE.exec(line)) {
    const name = run[0]
    const nameEnd = run.index + name.length
    // The rest of a run that a benign value ended inside (at a keyword) is no
    // name, as the Python's lookbehind says.
    if (run.index > 0 && NAME_CHAR_RE.test(line[run.index - 1])) continue
    // No secret word: the Python has no match here and carries on right after
    // the name, so a `NAME=value` inside the value is still seen.
    if (!SECRET_WORD_RE.test(name) && !KEY_NAME_RE.test(name)) continue
    NAME_VALUE_TAIL_RE.lastIndex = nameEnd
    const tail = NAME_VALUE_TAIL_RE.exec(line)
    if (!tail) continue
    const start = nameEnd + tail[0].length
    const secret = isSecretName(name)
    // `_name_values`: a quoted value ends at its closing quote or the end of its
    // line, a bare one as `bareValueEnd` says, either past a secret it swallows.
    let end: number
    let closing = ''
    if (isQuote(line[start])) {
      const [quotedEnd, closed] = quotedValueEnd(line, start, log, secret)
      end = quotedEnd
      if (closed) closing = line[start]
    } else {
      end = bareValueEnd(line, start, NAME_VALUE, log, secret)
    }
    // A match is consumed whole, benign or not.
    NAME_RUN_RE.lastIndex = end
    if (end === start || !secret) continue
    const quote = isQuote(line[start]) ? line[start] : ''
    out += line.slice(last, start) + `${quote}${MASK}${closing}`
    last = end
  }
  return out + line.slice(last)
}

// HTTP auth schemes whose name is kept when an `Authorization` value is masked
// (`_AUTH_SCHEMES`).
const AUTH_SCHEMES =
  '(?:basic|bearer|token|apikey|api-key|key|bot|ssws|negotiate|digest|ntlm|oauth|hawk|dpop|aws4-hmac-sha256)'

// ── Packed secrets (#2529) ───────────────────────────────────────────────────
//
// Port of the "Packed secrets" block in `redaction.py`. A bare value used to
// run to the next whitespace, so `X_KEY=abc;Bearer tok` lost the second
// secret's name to the first mask and left `tok` visible. A bare value now ends
// before a `;` or `,` that starts another secret (`startsSecret`); a separator
// followed by anything else stays in the value and is masked with it.
//
// A value class is `[chunk, sep]`: `chunk` matches a run of the value's
// characters that never end it, `sep` one character that ends it only where a
// packed secret starts right after it. Both are sticky.
type ValueClass = [RegExp, RegExp]
const valueClass = (chunk: string, sep = '[;,]'): ValueClass => [rx(chunk, 'yu'), rx(sep, 'yu')]

const LOG_VALUE = valueClass(String.raw`[^\s;,]*`)
const NAME_VALUE = valueClass(String.raw`[^"'\s,}&;]*`, ';')
// `_LOG_TOKEN`: a `SECRET_RE` Bearer token or client_id.
const LOG_TOKEN = valueClass(String.raw`[A-Za-z0-9_\-.]*`, '(?!)')
// `_BEARER_TOKEN`.
const BEARER_TOKEN = valueClass(String.raw`[A-Za-z0-9._~+/=-]*`, '(?!)')
const AUTH_BASIC_TOKEN_VALUE = valueClass(String.raw`[^\s'";,]*`)
// A raw `Authorization` value, which may hold spaces (`_AUTH_RAW_VALUE`).
const AUTH_RAW_VALUE = valueClass(String.raw`[^\n"',;]*`, ';')
// `_SWALLOWED_VALUE["bearer"]`: a Bearer token, maybe already masked.
const SWALLOWED_BEARER = valueClass(String.raw`[A-Za-z0-9._~+/=*-]*`, '(?!)')

const OPENERS_RE = /["'(\[{<]*/y
// `_PACKED_HEAD_RE`, one alternative at a time, each up to the value.
const BEARER_HEAD_RE = rx(String.raw`bearer\s+(?=[A-Za-z0-9._~+/=-]|\*\*\*REDACTED\*\*\*)`, 'yiu')
const AUTH_HEAD_RE = rx(
  String.raw`authorization["']?\s*:\s*["']?${AUTH_SCHEMES}\s+(?=[^\s,])`,
  'yiu',
)
const AUTH_RAW_HEAD_RE = rx(
  String.raw`authorization["']?\s*:\s*["']?(?!${AUTH_SCHEMES}(?:\s|$))(?=[^\s"',]{8})`,
  'yiu',
)
// `_FLAG_PREFIX` up to the value. The flag's run is tested for a secret word
// in code, as `maskFlags` does, and its lookbehind in `head`.
const FLAG_HEAD_RE = rx(
  String.raw`(?:[A-Za-z0-9]|-(?!-))*(--[A-Za-z0-9-]*)(?:=|\s+)(?=[^-\s])`,
  'yu',
)
// `_LOG_HEAD_RE`; the `\b` of its `_KEY=` alternative is tested in `head`.
const LOG_HEAD_RE = rx(String.raw`Authorization:\s*Bearer\s+(?=\S)|HAL0_BEARER_TOKEN=(?=\S)`, 'yiu')
const LOG_KEY_HEAD_RE = rx(String.raw`(?:[A-Za-z][A-Za-z0-9_]*_KEY|KEY)=(?=\S)`, 'yiu')
const NAME_HEAD_RE = rx(String.raw`[A-Za-z0-9_-]+`, 'yu')
// What the Python's lookbehinds test for, in its `re.I` sense.
const NAME_CHAR_RE = rx(String.raw`[A-Za-z0-9_-]`, 'u')
const FLAG_RUN_CHAR_RE = rx(String.raw`[A-Za-z0-9-]`, 'u')
const ALNUM_RE = rx(String.raw`[A-Za-z0-9]`, 'u')
// A Unicode word character, for Python's `\b`.
const WORD_CHAR_RE = /[\p{L}\p{N}_]/u

function stickyMatch(re: RegExp, line: string, pos: number): RegExpExecArray | null {
  re.lastIndex = pos
  return re.exec(line)
}

// Where a secret's value starts, and the class it is read with.
type Head = [number, ValueClass]

// `_head`: where the value starts of a secret whose name or keyword starts at
// `line[pos]`, and the class its value is read with when another value has
// swallowed it, or null. With `log`, also a secret that `SECRET_RE` masks.
function head(line: string, pos: number, log: boolean): Head | null {
  let m = stickyMatch(BEARER_HEAD_RE, line, pos)
  if (m) return [pos + m[0].length, SWALLOWED_BEARER]
  m = stickyMatch(AUTH_HEAD_RE, line, pos)
  if (m) return [pos + m[0].length, AUTH_BASIC_TOKEN_VALUE]
  m = stickyMatch(AUTH_RAW_HEAD_RE, line, pos)
  if (m) return [pos + m[0].length, AUTH_RAW_VALUE]
  const prev = pos > 0 ? line[pos - 1] : ''
  if (!FLAG_RUN_CHAR_RE.test(prev)) {
    m = stickyMatch(FLAG_HEAD_RE, line, pos)
    if (m && FLAG_WORD_RE.test(m[1])) return [pos + m[0].length, LOG_VALUE]
  }
  if (log) {
    m = stickyMatch(LOG_HEAD_RE, line, pos)
    if (!m && !WORD_CHAR_RE.test(prev)) m = stickyMatch(LOG_KEY_HEAD_RE, line, pos)
    if (m) return [pos + m[0].length, LOG_VALUE]
  }
  if (NAME_CHAR_RE.test(prev)) return null
  const name = stickyMatch(NAME_HEAD_RE, line, pos)?.[0]
  if (name === undefined || (!SECRET_WORD_RE.test(name) && !KEY_NAME_RE.test(name))) return null
  const tail = stickyMatch(NAME_VALUE_TAIL_RE, line, pos + name.length)
  return tail && isSecretName(name) ? [pos + name.length + tail[0].length, NAME_VALUE] : null
}

// `_starts_secret`: true if a secret that the shape rules mask (or have masked)
// starts at `line[pos]`, after any opening quotes or brackets. With `log`, also
// one that `SECRET_RE` masks.
function startsSecret(line: string, pos: number, log: boolean): boolean {
  return head(line, pos + (stickyMatch(OPENERS_RE, line, pos)?.[0].length ?? 0), log) !== null
}

// `_packed_value_end`: where the bare value of class `value` starting at
// `line[start]` ends, before `bareValueEnd` looks for a swallowed secret. Every
// step moves forward and tests one separator, so this is linear in the value.
function packedValueEnd(
  line: string,
  start: number,
  [chunk, sep]: ValueClass,
  log: boolean,
): number {
  let end = start
  for (;;) {
    end += stickyMatch(chunk, line, end)?.[0].length ?? 0
    if (stickyMatch(sep, line, end) && !startsSecret(line, end + 1, log)) {
      end += 1
      continue
    }
    return end
  }
}

// Python's whitespace (`\s`) at the end of a string, for `rstrip`.
const TRAILING_WS_RE = rx(String.raw`\s+$`, 'u')

// ── Swallowed secrets (#2593, #2594, #2595) ──────────────────────────────────
//
// Port of the "Swallowed secrets" block in `redaction.py`. A value that takes
// in the start of another secret whose value lies past its end (after an empty
// value, glued on, or past a quote that does not close where it should) is
// extended over that secret's value, read as its own rule reads it. Only a
// benign value, which is not masked, ends before the secret instead.

// `_SWALLOW_AT_RE`'s keywords, which may start anywhere.
const SWALLOW_KEYWORD_RE = rx('bearer|authorization|hal0_bearer_token|client_id', 'yiu')
// `_CLIENT_ID_HEAD_RE`.
const CLIENT_ID_HEAD_RE = rx(String.raw`client_id=(?=[A-Za-z0-9_.-]{16})`, 'yiu')

const isQuote = (c: string | undefined): boolean => c === '"' || c === "'"

// `_quote_close`: where the quoted value opening at `line[start]` ends, and
// whether it closes there: at its closing quote on the same line, one that no
// backslash escapes, or, if it has none, at the end of its line. An unclosed
// quote never pairs with a quote on a later line. A backslash and the character
// after it are one unit in either kind of quote, and a backslash right before
// the end of its line is part of the value (`_QUOTED_RUN`).
const QUOTED_RUN_RE: Record<string, RegExp> = {
  '"': /[^"\\\n]*(?:\\[^\n]?[^"\\\n]*)*/y,
  "'": /[^'\\\n]*(?:\\[^\n]?[^'\\\n]*)*/y,
}
function quoteClose(line: string, start: number): [number, boolean] {
  const re = QUOTED_RUN_RE[line[start]]
  re.lastIndex = start + 1
  const end = start + 1 + (re.exec(line) as RegExpExecArray)[0].length
  return [end, end < line.length && line[end] === line[start]]
}

// `_value_stop`: `packedValueEnd`, less trailing whitespace.
function valueStop(line: string, start: number, value: ValueClass, log: boolean): number {
  const end = packedValueEnd(line, start, value, log)
  return start + line.slice(start, end).replace(TRAILING_WS_RE, '').length
}

// `_swallowed_head`: `head`, or a long `client_id=`.
function swallowedHead(line: string, pos: number, log: boolean): Head | null {
  const m = stickyMatch(CLIENT_ID_HEAD_RE, line, pos)
  if (m) return [pos + m[0].length, LOG_TOKEN]
  return head(line, pos, log)
}

// Where a swallowed secret starts, where its value starts, and its class.
type Swallowed = [number, number, ValueClass]

// `_swallowed`: a secret that starts inside the value `line[start:end]` and
// whose own value does not end inside it, or null; of those, the one whose
// value reaches furthest, then the first. In a value that is not `secret`, the
// first secret counts. A value read from inside one already read ends where
// that one does, so each class reads each character at most once.
function swallowed(
  line: string,
  start: number,
  end: number,
  log: boolean,
  secret = true,
): Swallowed | null {
  const read = new Map<ValueClass, [number, number]>()
  let best: Swallowed | null = null
  let reach = -1
  for (let pos = start; pos < end; pos += 1) {
    // A name, flag or word at the start of its run, or a keyword anywhere.
    if (pos > 0 && ALNUM_RE.test(line[pos - 1])) {
      if (!'bBaAhHcC'.includes(line[pos]) || !stickyMatch(SWALLOW_KEYWORD_RE, line, pos)) continue
    }
    const found = swallowedHead(line, pos, log)
    if (!found) continue
    const [valueStart, value] = found
    if (!secret) return [pos, valueStart, value]
    let stop: number
    if (isQuote(line[valueStart])) {
      stop = quoteClose(line, valueStart)[0]
      if (stop < end) continue
    } else {
      let last = read.get(value)
      if (!last || valueStart < last[0] || valueStart > last[1]) {
        last = [valueStart, valueStop(line, valueStart, value, log)]
        read.set(value, last)
      }
      stop = last[1]
      if (valueStart < end && stop <= end) continue
    }
    if (stop > reach) {
      best = [pos, valueStart, value]
      reach = stop
    }
  }
  return best
}

// `_past_swallowed`: where a value of class `outer` (null when quoted) that
// swallowed a secret ends. If it is not `secret` that is where the secret
// starts; otherwise past the secret's value, then on as each value it was
// inside would run, so redacting the masked text again keeps it.
function pastSwallowed(
  line: string,
  [pos, firstValueStart, firstValue]: Swallowed,
  outer: ValueClass | null,
  log: boolean,
  secret: boolean,
): number {
  if (!secret) return pos
  const enclosing: ValueClass[] = outer ? [outer] : []
  let valueStart = firstValueStart
  let value = firstValue
  for (;;) {
    let end: number
    let nested: Swallowed | null
    if (isQuote(line[valueStart])) {
      const [close, closed] = quoteClose(line, valueStart)
      end = close + (closed ? 1 : 0)
      nested = swallowed(line, valueStart + 1, close, log)
    } else {
      end = valueStop(line, valueStart, value, log)
      nested = swallowed(line, valueStart, end, log)
      if (nested) enclosing.push(value)
    }
    while (!nested && enclosing.length > 0) {
      valueStart = end
      value = enclosing.pop() as ValueClass
      end = valueStop(line, valueStart, value, log)
      nested = swallowed(line, valueStart, end, log)
      if (nested) enclosing.push(value)
    }
    if (!nested) return end
    ;[, valueStart, value] = nested
  }
}

// `_swallowing_end`: `end`, or past the value of a secret that the value
// `line[start:end]`, of class `value`, swallows.
function swallowingEnd(
  line: string,
  start: number,
  end: number,
  value: ValueClass,
  log: boolean,
  secret = true,
): number {
  const found = swallowed(line, start, end, log, secret)
  return found ? pastSwallowed(line, found, value, log, secret) : end
}

// `_bare_value_end`: where the bare value of class `value` starting at
// `line[start]` ends: before a packed secret, or past a secret it swallows.
function bareValueEnd(
  line: string,
  start: number,
  value: ValueClass,
  log: boolean,
  secret = true,
): number {
  return swallowingEnd(line, start, packedValueEnd(line, start, value, log), value, log, secret)
}

// `_quoted_value_end`: where the quoted value opening at `line[start]` ends,
// and whether its quote closes: past its closing quote or, if it has none, at
// the end of its line, and past the value of a secret it swallows. The quoted
// text is masked whole, spaces and all (#2598).
function quotedValueEnd(
  line: string,
  start: number,
  log: boolean,
  secret = true,
): [number, boolean] {
  const [close, closed] = quoteClose(line, start)
  let end = close + (closed ? 1 : 0)
  const inner = swallowed(line, start + 1, close, log, secret)
  if (inner) end = pastSwallowed(line, inner, null, log, secret)
  return [end, closed]
}

// `_quoted_log_value_end`: `quotedValueEnd` for a LOG_SECRET_RE value that opens
// with a quote, then on as a bare value runs on from there.
function quotedLogValueEnd(line: string, start: number): number {
  return bareValueEnd(line, quotedValueEnd(line, start, true)[0], LOG_VALUE, true)
}

// `_mask_bare_values`: `prefix` (global) matches up to a bare value; mask each
// such value and keep the match. With `spaces` the value may hold whitespace,
// but does not end with any.
function maskBareValues(
  line: string,
  prefix: RegExp,
  value: ValueClass,
  log: boolean,
  spaces = false,
): string {
  let out = ''
  let last = 0
  prefix.lastIndex = 0
  for (let m = prefix.exec(line); m; m = prefix.exec(line)) {
    const start = m.index + m[0].length
    let end = bareValueEnd(line, start, value, log)
    prefix.lastIndex = end
    if (spaces) end = start + line.slice(start, end).replace(TRAILING_WS_RE, '').length
    if (end > start) {
      out += line.slice(last, start) + MASK
      last = end
    }
  }
  return out + line.slice(last)
}

// The first four `_SHAPE_RULES`, in order.
const AUTH_BASIC_TOKEN_RE = rx(String.raw`authorization:\s*(?:basic|token)\s+(?=[^\s'"])`, 'giu')
const BEARER_RULE_RE = rx(String.raw`bearer\s+(?=[A-Za-z0-9._~+/=-])`, 'giu')
const AUTH_SCHEME_RE = rx(
  String.raw`authorization["']?\s*:\s*["']?${AUTH_SCHEMES}\s+(?=[^\s"',])`,
  'giu',
)
const AUTH_SCHEME_VALUE = valueClass(String.raw`[^\s"',;]*`, ';')
// Any other `Authorization` value of 8+ characters is a raw credential, masked
// to the end of the header value; a secret after a space in it is swallowed.
const AUTH_RAW_RE = rx(
  String.raw`authorization["']?\s*:\s*["']?(?!${AUTH_SCHEMES}(?:\s|$))(?=[^\s"',]{8,})`,
  'giu',
)

// `_AUTH_QUOTED_RE`: up to a quoted value after an `Authorization` scheme.
// The whitespace is horizontal, so a header that ends a line never takes the
// quoted text on the next one.
const HSPACE = String.raw`(?:(?!\n)\s)`
const AUTH_QUOTED_RE = rx(
  String.raw`authorization["']?${HSPACE}*:${HSPACE}*["']?${AUTH_SCHEMES}${HSPACE}+(?=["'])`,
  'giu',
)

// `_mask_quoted_values`: `prefix` (global) matches up to a value that opens with
// a quote; mask each such value, as `quotedValueEnd` bounds it, and keep the
// match and the value's quotes.
function maskQuotedValues(line: string, prefix: RegExp, log: boolean): string {
  let out = ''
  let last = 0
  prefix.lastIndex = 0
  for (let m = prefix.exec(line); m; m = prefix.exec(line)) {
    const start = m.index + m[0].length
    const [end, closed] = quotedValueEnd(line, start, log)
    out += line.slice(last, start) + line[start] + MASK + (closed ? line[start] : '')
    last = end
    prefix.lastIndex = end
  }
  return out + line.slice(last)
}

function maskAuthorization(line: string, log: boolean): string {
  let out = maskQuotedValues(line, AUTH_QUOTED_RE, log)
  out = maskBareValues(out, AUTH_BASIC_TOKEN_RE, AUTH_BASIC_TOKEN_VALUE, log)
  out = maskBareValues(out, BEARER_RULE_RE, BEARER_TOKEN, log)
  out = maskBareValues(out, AUTH_SCHEME_RE, AUTH_SCHEME_VALUE, log)
  return maskBareValues(out, AUTH_RAW_RE, AUTH_RAW_VALUE, log, true)
}

// True when `://` at `at` follows a URL scheme, `[a-z][a-z0-9+.-]*`. The Python
// puts the scheme in the pattern, which rescans the whole run from every
// letter in it; a run before `://` holds a scheme when it holds a letter.
function followsScheme(line: string, at: number): boolean {
  for (let j = at - 1; j >= 0; j -= 1) {
    const c = line.charCodeAt(j)
    // A letter, including what Python's `re.I` folds to one: İ ı ſ K.
    if (
      (c >= 65 && c <= 90) ||
      (c >= 97 && c <= 122) ||
      c === 0x130 ||
      c === 0x131 ||
      c === 0x17f ||
      c === 0x212a
    ) {
      return true
    }
    const inClass = (c >= 48 && c <= 57) || c === 43 || c === 46 || c === 45
    if (!inClass) return false
  }
  return false
}

// `scheme://user:password@` and `scheme://<16+ chars>@` userinfo.
const URL_PASSWORD_RE = rx(String.raw`(:\/\/[^/@\s:]*):[^/@\s]+@`, 'g')
const URL_LONG_USER_RE = rx(String.raw`:\/\/[^/@\s:]{16,}@`, 'g')

const FLAG_RUN_RE = rx(String.raw`--[A-Za-z0-9-]*`, 'gu')
const FLAG_WORD_RE = rx(String.raw`secret|token|password|pass|api-key|apikey|private-key`, 'iu')
const FLAG_VALUE_RE = rx(String.raw`(?:=|\s+)(?=[^-\s])`, 'yu')
const FLAG_VALUE = valueClass(String.raw`[^\s;,]*`)

// `--token VALUE`, `--api-key=VALUE`. The Python rescans from every `--` of a
// long dash run; every start in the run has the same outcome, so scan it once.
function maskFlags(line: string, log: boolean): string {
  let out = ''
  let last = 0
  FLAG_RUN_RE.lastIndex = 0
  for (let run = FLAG_RUN_RE.exec(line); run; run = FLAG_RUN_RE.exec(line)) {
    const runEnd = run.index + run[0].length
    if (!FLAG_WORD_RE.test(run[0])) continue
    FLAG_VALUE_RE.lastIndex = runEnd
    const tail = FLAG_VALUE_RE.exec(line)
    if (!tail) continue
    const start = runEnd + tail[0].length
    const end = bareValueEnd(line, start, FLAG_VALUE, log)
    FLAG_RUN_RE.lastIndex = end
    if (end > start) {
      out += line.slice(last, start) + MASK
      last = end
    }
  }
  return out + line.slice(last)
}

const CLIENT_ID_RE = rx(String.raw`(client_id=)[A-Za-z0-9_.-]{16,}`, 'giu')

// The JWT, prefix and AWS-key rules have no `re.I` in Python: ASCII only.
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
  return shapes(line, false)
}

// `_redact_shapes`: `log` when `SECRET_RE` has already run on `line`, so a value
// may also end before one of its secrets.
function shapes(line: string, log: boolean): string {
  let out = maskAuthorization(line, log)
  const beforePassword = out
  out = beforePassword.replace(URL_PASSWORD_RE, (m, userinfo: string, offset: number) =>
    followsScheme(beforePassword, offset) ? `${userinfo}:${MASK}@` : m,
  )
  const beforeLongUser = out
  out = beforeLongUser.replace(URL_LONG_USER_RE, (m, offset: number) =>
    followsScheme(beforeLongUser, offset) ? `://${MASK}@` : m,
  )
  out = maskNameValues(out, log)
  out = maskFlags(out, log)
  out = out.replace(CLIENT_ID_RE, `$1${MASK}`)
  out = maskJwts(out)
  // Case-sensitive, as in Python.
  out = out.replace(/(?:hf_|sk-|gh[pousr]_|github_pat_|xox[baprs]-)[A-Za-z0-9_-]{20,}/g, MASK)
  return out.replace(/AKIA[0-9A-Z]{16}/g, MASK)
}

// Exported for `sentry.test.ts` — the port is only trustworthy if it is
// pinned against the same cases as the Python original.
export function redactSecrets(value: unknown): unknown {
  if (typeof value !== 'string') return value
  return shapes(maskLogSecrets(value), true)
}

// ── Structured values ────────────────────────────────────────────────────────
//
// Port of `is_sensitive_key` (`_SENSITIVE_RE` in `src/hal0/api/_redact.py`):
// a value whose KEY names a secret is masked without being looked at, because a
// bare value (`{ password: 'hunter2' }`) has no shape for `redactSecrets` to
// find. It is a substring search, so it over-masks (`max_tokens`) on purpose,
// exactly as the backend's `_scrub` does. KEEP IN SYNC with `_SENSITIVE_RE`;
// the shared fixture's `sensitive_keys` and `plain_keys` pin both. `\n?$` is
// Python's `$`, which also matches before a final newline.
const SENSITIVE_KEY_RE = rx(
  String.raw`SECRET|TOKEN|PASSWORD|PASS|API[_-]?KEY|ACCESS[_-]?KEY|PRIVATE[_-]?KEY` +
    String.raw`|ENCRYPTION[_-]?KEY|SALT|_KEY\n?$|^KEY\n?$`,
  'iu',
)

export function isSensitiveKey(key: string): boolean {
  return SENSITIVE_KEY_RE.test(key)
}

// Port of `SENSITIVE_HEADERS` in `src/hal0/observability/sentry.py`: request
// headers masked wholesale, compared lower-cased. These carry a credential under
// a name `isSensitiveKey` does not catch, and `Authorization: Basic dXNl...` or a
// raw `Cookie` value has no text shape once its name is gone. KEEP IN SYNC; the
// shared fixture's `sensitive_headers` pins both.
const SENSITIVE_HEADERS: ReadonlySet<string> = new Set([
  'authorization',
  'proxy-authorization',
  'cookie',
  'set-cookie',
  'x-api-key',
  'api-key',
  'x-auth-token',
  'x-hal0-admin-key',
  'x-hal0-client-key',
])

// The TS twin of the backend's `_scrub`: mask the value under a sensitive key
// and redact every other string inside a value. Unlike the Python it returns a
// COPY, because Sentry hands us `console` arguments and the like by reference
// and rewriting the application's own objects would be a bug. It is bounded and
// cycle-safe: past `MAX_DEPTH`, past `MAX_NODES` values visited (leaves count
// too, so a flat array of a million strings is not scanned a million times), or
// at a reference to one of its own ancestors, a value is replaced by a marker
// rather than walked (fail closed, never ship what was not scrubbed).
//
// With `urls`, every string is also truncated at `?` before it is redacted:
// span and stack-frame fields hold full request URLs under many names
// (`url.full`, `http.query`, `abs_path`), and a query string is assumed to be
// credential-bearing.
const MAX_DEPTH = 8
const MAX_NODES = 5000

export function redactTree(value: unknown, opts: { urls?: boolean } = {}): unknown {
  let nodes = 0
  const ancestors = new Set<object>()
  const walk = (v: unknown, depth: number): unknown => {
    nodes += 1
    if (nodes > MAX_NODES || depth > MAX_DEPTH) return '[Truncated]'
    if (typeof v === 'string') return opts.urls ? scrubUrl(v) : redactSecrets(v)
    if (v === null || typeof v !== 'object') {
      return typeof v === 'function' || typeof v === 'symbol' ? '[Unserializable]' : v
    }
    if (ancestors.has(v)) return '[Circular]'
    ancestors.add(v)
    try {
      if (Array.isArray(v)) return v.map((item) => walk(item, depth + 1))
      const copy: Record<string, unknown> = {}
      for (const [key, item] of Object.entries(v)) {
        copy[key] = isSensitiveKey(key) ? MASK : walk(item, depth + 1)
      }
      return copy
    } finally {
      ancestors.delete(v)
    }
  }
  return walk(value, 0)
}

// Truncate a URL at `?` and mask what is left: userinfo and a token in the path
// survive `stripQuery`.
function scrubUrl(value: unknown): unknown {
  return typeof value === 'string' ? redactSecrets(stripQuery(value) as string) : value
}

type Bag = Record<string, unknown>

const isBag = (v: unknown): v is Bag => v !== null && typeof v === 'object'

// Scrub everything in `event` that can carry text, for error and transaction
// events alike. Mutates `event`, which is Sentry's own copy, and leaves
// `sdkProcessingMetadata` alone (the SDK reads it back after the hook). Throws
// on anything unexpected: the caller drops the event.
function scrubEvent(event: Bag): Bag {
  delete event.user
  const request = event.request as Bag | undefined
  if (request) {
    request.url = scrubUrl(request.url)
    delete request.query_string
    delete request.cookies
    delete request.data
    delete request.env
    if (isBag(request.headers)) {
      const headers: Bag = {}
      for (const [key, item] of Object.entries(request.headers)) {
        const name = key.toLowerCase()
        if (SENSITIVE_HEADERS.has(name) || isSensitiveKey(key)) headers[key] = MASK
        // `Referer` is a URL of the page that made the request.
        else if (name === 'referer' || name === 'referrer') headers[key] = scrubUrl(item)
        else headers[key] = redactTree(item)
      }
      request.headers = headers
    }
  }

  // A pageload or navigation transaction is named after the URL path.
  if (typeof event.transaction === 'string') event.transaction = scrubUrl(event.transaction)

  if (Array.isArray(event.breadcrumbs)) {
    event.breadcrumbs = event.breadcrumbs.map((crumb: unknown) => {
      if (!isBag(crumb)) return crumb
      const out: Bag = { ...crumb }
      if (typeof out.message === 'string') out.message = redactSecrets(out.message)
      if (isBag(out.data)) {
        // The default console breadcrumb keeps the raw call in `data.arguments`.
        const data = redactTree(out.data) as Bag
        for (const key of ['url', 'from', 'to']) {
          if (key in data) data[key] = scrubUrl(data[key])
        }
        out.data = data
      }
      return out
    })
  }

  // `extra` holds `__serialized__` for a captured plain object; `contexts` and
  // `tags` are free-form too. `contexts.trace.data` holds a transaction's root
  // span attributes, URLs included.
  for (const key of ['extra', 'contexts', 'tags']) {
    if (isBag(event[key])) event[key] = redactTree(event[key])
  }
  const contexts = event.contexts as Bag | undefined
  if (contexts && isBag(contexts.trace)) contexts.trace = redactTree(contexts.trace, { urls: true })

  // A transaction's child spans. A fetch/XHR span carries the full request URL
  // in its description and in several `data` attributes (`url`, `url.full`,
  // `http.query`), so every string in a span is URL-scrubbed.
  if (Array.isArray(event.spans)) {
    event.spans = event.spans.map((span: unknown) =>
      isBag(span) ? redactTree(span, { urls: true }) : span,
    )
  }

  // The text surfaces. `message` is the top-level string form, `logentry` its
  // structured twin, and `exception.values[].value` is what actually carries an
  // upstream 401's echoed header. The rest of an exception value (a frame's
  // `filename` and `abs_path` are URLs) is URL-scrubbed; `value` is not cut at
  // `?`, because it is prose.
  if (typeof event.message === 'string') event.message = redactSecrets(event.message)
  const logentry = event.logentry as Bag | undefined
  if (logentry && typeof logentry.message === 'string') {
    logentry.message = redactSecrets(logentry.message)
  }
  const exception = event.exception as Bag | undefined
  if (exception && Array.isArray(exception.values)) {
    exception.values = exception.values.map((value: unknown) => {
      if (!isBag(value)) return value
      const out = redactTree(value, { urls: true }) as Bag
      if (typeof value.value === 'string') out.value = redactSecrets(value.value)
      return out
    })
  }
  return event
}

// The `beforeSend` / `beforeSendTransaction` hook: error events reach only the
// first and transaction events only the second, so both are registered, as the
// backend registers `scrub_event` for both.
function scrubOrDrop<T>(event: T): T | null {
  try {
    return scrubEvent(event as unknown as Bag) as unknown as T
  } catch {
    // Fail closed, same as the backend: an event we could not scrub is an
    // event we do not send.
    return null
  }
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
      // Spans are sent inside their transaction event (no span streaming), so
      // `beforeSendTransaction` sees every span the tracing integration made.
      beforeSend: (event) => scrubOrDrop(event),
      beforeSendTransaction: (event) => scrubOrDrop(event),
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
