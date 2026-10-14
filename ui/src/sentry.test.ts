// Pins the browser-side secret scrubber in src/sentry.ts.
//
// `redactSecrets` is a hand-port of `redact_log_line` in
// `src/hal0/redaction.py` (`LOG_SECRET_RE` and the shared shape pass
// `redact_secret_shapes`). A port with no test is a leak waiting for the
// next pattern change, and this one is not theoretical: the first live test of
// the Sentry wiring shipped a bearer token from the browser that the Python
// side had already masked, because the browser hook only scrubbed URLs and
// bodies. These cases mirror `tests/observability/test_sentry.py`, and the
// shared fixture below is also read by `tests/api/test_redact.py`.

import { beforeAll, describe, expect, it, vi } from 'vitest'

import sharedCases from '../../tests/fixtures/redaction_shapes.json'
import { initSentry, isSensitiveKey, redactSecrets, redactTree } from './sentry'

const MASK = '***REDACTED***'

// `initSentry` imports the SDK dynamically; stand in for it and keep the
// options it was initialised with, so the real `beforeSend` can be driven.
const sdk = vi.hoisted(() => ({
  init: vi.fn(),
  setTag: vi.fn(),
  browserTracingIntegration: vi.fn(),
}))
vi.mock('@sentry/react', () => sdk)

describe('redactSecrets', () => {
  it('masks an Authorization: Bearer header, keeping the prefix', () => {
    const out = redactSecrets(
      '401 from upstream (Authorization: Bearer sk-live-abcdef0123456789)',
    ) as string
    expect(out).not.toContain('sk-live-abcdef0123456789')
    expect(out).toContain(MASK)
    // The prefix survives so an operator can tell what kind of secret it was.
    expect(out).toContain('Authorization: Bearer')
  })

  it('masks a bare Bearer token', () => {
    const out = redactSecrets('sent Bearer sk-live-abcdef0123456789 upstream') as string
    expect(out).toBe(`sent Bearer ${MASK} upstream`)
  })

  it('masks HAL0_BEARER_TOKEN= and *_KEY= assignments', () => {
    expect(redactSecrets('HAL0_BEARER_TOKEN=abc123')).toBe(`HAL0_BEARER_TOKEN=${MASK}`)
    expect(redactSecrets('HAL0_ADMIN_KEY=hal0-admin-abc123')).toBe(`HAL0_ADMIN_KEY=${MASK}`)
    expect(redactSecrets('KEY=abc123')).toBe(`KEY=${MASK}`)
  })

  it('masks a long client_id but leaves short non-secret labels alone', () => {
    expect(redactSecrets('client_id=abcdefghijklmnopqrst')).toBe(`client_id=${MASK}`)
    // The Python original length-gates this at 16+ chars so the legitimate
    // short labels (`anonymous`, the 12-hex-char hash) are not masked.
    expect(redactSecrets('client_id=anonymous')).toBe('client_id=anonymous')
  })

  it('masks every occurrence in one string, not just the first', () => {
    const out = redactSecrets('Bearer aaaaaaaaaaaa and HAL0_ADMIN_KEY=bbbbbbbbbbbb') as string
    expect(out).toBe(`Bearer ${MASK} and HAL0_ADMIN_KEY=${MASK}`)
  })

  it('leaves ordinary text and non-strings untouched', () => {
    expect(redactSecrets('slot flm failed to start on port 8081')).toBe(
      'slot flm failed to start on port 8081',
    )
    expect(redactSecrets(undefined)).toBeUndefined()
    expect(redactSecrets(42)).toBe(42)
    expect(redactSecrets(null)).toBeNull()
  })
})

// ── Shared shape pass (#2413) ────────────────────────────────────────────────
//
// `tests/fixtures/redaction_shapes.json` is read by this file and by
// `tests/api/test_redact.py::TestSharedFixture`, so the Python original and
// this port are held to the same cases. Add a case there, not here.

interface SharedCases {
  value: string
  hyphen_value: string
  mask: [string, string][]
  keep: string[]
  exact: [string, string][]
  mask_names: string[]
  keep_names: string[]
  mask_hyphen_names: string[]
  sensitive_keys: string[]
  plain_keys: string[]
  sensitive_headers: string[]
}
const shared = sharedCases as unknown as SharedCases

describe('redactSecrets: shared fixture', () => {
  it.each(shared.mask)('masks %j', (line, secret) => {
    const out = redactSecrets(line) as string
    expect(out).not.toContain(secret)
    expect(out).toContain(MASK)
  })

  it.each(shared.keep)('leaves %j alone', (line) => {
    expect(redactSecrets(line)).toBe(line)
  })

  it.each(shared.exact)('renders %j as %j', (line, expected) => {
    expect(redactSecrets(line)).toBe(expected)
    // Idempotent: the same string can cross more than one seam.
    expect(redactSecrets(expected)).toBe(expected)
  })

  it.each(shared.mask_names)('masks the value of the name %s', (name) => {
    const out = redactSecrets(`${name}=${shared.value}`) as string
    expect(out).not.toContain(shared.value)
    expect(out).toContain(MASK)
  })

  it.each(shared.keep_names)('keeps the value of the count or location name %s', (name) => {
    const line = `${name}=${shared.value}`
    expect(redactSecrets(line)).toBe(line)
  })

  it.each(shared.mask_hyphen_names)('masks a hyphenated name %s in every form', (name) => {
    const v = shared.hyphen_value
    for (const line of [`${name}=${v}`, `${name}: ${v}`, `{"${name}": "${v}"}`, `--${name}=${v}`]) {
      const out = redactSecrets(line) as string
      expect(out).not.toContain(v)
      // The name is kept so a reader sees a secret was present.
      expect(out).toContain(name)
    }
  })
})

describe('redactSecrets: shape pass', () => {
  it('keeps the name, quoting and separator, and masks every occurrence', () => {
    expect(redactSecrets(`HF_TOKEN='abc def' and "apiKey" : "k_1234567890"`)).toBe(
      `HF_TOKEN='${MASK}' and "apiKey" : "${MASK}"`,
    )
  })

  it('rescans the value of a name with no secret word', () => {
    // `foo` is no secret name and consumes nothing: `token=` inside its value is seen.
    expect(redactSecrets('foo=token=abcdefgh1234')).toBe(`foo=token=${MASK}`)
  })

  it('ends the value of a benign secret-word name before a secret in it', () => {
    // The benign value is not masked, so a secret it took in would be left in
    // clear (#2594): it ends where the secret starts, as in the Python.
    expect(redactSecrets('max_tokens=token=abcdefgh1234')).toBe(`max_tokens=token=${MASK}`)
    expect(redactSecrets('max_tokens=5')).toBe('max_tokens=5')
  })

  it('judges a name longer than 128 characters as written', () => {
    const long = 'max_tokens_'.repeat(20)
    expect(redactSecrets(`${long}=Zq8vR2mW9xK4tL7pQ3`)).toBe(`${long}=${MASK}`)
    expect(redactSecrets('max_tokens=Zq8vR2mW9xK4tL7pQ3')).toBe('max_tokens=Zq8vR2mW9xK4tL7pQ3')
  })

  it('judges the count part at the match position', () => {
    // `max_tokensx` is not a count; only the later `max_tokens` is, leaving `tokensx`.
    expect(redactSecrets('a_max_tokensx_max_tokens=Zq8vR2mW9xK4tL7pQ3')).toBe(
      `a_max_tokensx_max_tokens=${MASK}`,
    )
  })

  it('takes a hyphen as an underscore and a camelCase hump as a word start', () => {
    expect(redactSecrets('max-tokens=Zq8vR2mW9xK4tL7pQ3')).toBe('max-tokens=Zq8vR2mW9xK4tL7pQ3')
    expect(redactSecrets('maxTokens=Zq8vR2mW9xK4tL7pQ3')).toBe('maxTokens=Zq8vR2mW9xK4tL7pQ3')
    expect(redactSecrets('apiTokenSCount=Zq8vR2mW9xK4tL7pQ3')).toBe(`apiTokenSCount=${MASK}`)
    expect(redactSecrets('API_TOKENS_PER_KEY=Zq8vR2mW9xK4tL7pQ3')).toBe(
      `API_TOKENS_PER_KEY=${MASK}`,
    )
  })

  it('finds a URL scheme anywhere in the run before ://', () => {
    expect(redactSecrets('1http://u:pw12345678@h/r')).toBe(`1http://u:${MASK}@h/r`)
    expect(redactSecrets('1234://u:pw12345678@h/r')).toBe('1234://u:pw12345678@h/r')
  })

  it('masks a JWT embedded after word characters, once per run', () => {
    const jwt = 'eyJhbGciOiJIUzI1NiJ9.eyJzdWIiOiJoYWwwIn0.c2lnbmF0dXJlMTIzNDU2'
    expect(redactSecrets(`tok${jwt}.`)).toBe(`tok${MASK}.`)
    expect(redactSecrets(`${jwt} ${jwt}`)).toBe(`${MASK} ${MASK}`)
  })
})

describe('redactSecrets: linear time', () => {
  // Each input is one shape a backtracking rule would rescan from every start
  // position: a quadratic rule takes seconds here, the linear scan ~10 ms.
  const N = 200_000
  const inputs: [string, string][] = [
    ['a long alphanumeric run', 'a'.repeat(N)],
    ['a long dash run', '-'.repeat(N)],
    ['--token repeated', '--token'.repeat(N / 7)],
    ['one long name that continues with no separator', 'token'.repeat(N / 5)],
    ['one long hyphenated name with no separator', 'api-token-'.repeat(N / 10)],
    ['eyJ repeated', 'eyJ'.repeat(N / 3)],
    // #2599: a `_KEY=` name may start at these, which the ASCII `\\b` let it, at every `a`.
    ['dotless i and a, repeated', '\u0131a'.repeat(N / 2)],
    ['a folded-letter name at every start', '\u0131api_key=,x '.repeat(N / 12)],
    ['eyJ then a long run', `eyJ${'a'.repeat(N)}`],
    ['hf_ repeated', 'hf_'.repeat(N / 3)],
    ['a long scheme-like run before ://', `${'a1.'.repeat(N / 3)}://`],
    ['://x:y repeated', '://x:y'.repeat(N / 6)],
    ['scheme and 16+ user chars, no @', `http://${'u'.repeat(N)}`],
    ['authorization: repeated', 'authorization: '.repeat(N / 15)],
    ['authorization then spaces', `authorization:${' '.repeat(N)}`],
    ['name then spaces', `token${' '.repeat(N)}`],
    ['name=" unterminated', `token="${'x'.repeat(N)}`],
    ['name=value repeated', 'token=x '.repeat(N / 8)],
    ['bearer then spaces', `bearer${' '.repeat(N)}`],
    ['client_id= repeated', 'client_id='.repeat(N / 10)],
    ['a_ repeated', 'a_'.repeat(N / 2)],
    ['KEY= repeated', 'KEY='.repeat(N / 4)],
    // #2529: a value tests each `;` or `,` in it for a packed secret.
    ['packed X_KEY=a; repeated', 'X_KEY=a;'.repeat(N / 8)],
    ['packed token=a; repeated', 'token=a;'.repeat(N / 8)],
    ['packed ;Bearer a repeated', ';Bearer a'.repeat(N / 9)],
    ['packed --token=a; repeated', '--token=a;'.repeat(N / 10)],
    ['one value of many ;a', `X_KEY=${';a'.repeat(N / 2)}`],
    ['a raw Authorization value of many words', `Authorization: abcdefghij${' a'.repeat(N / 2)}`],
    // #2594: each empty value tests the next word for a swallowed secret.
    ['empty x_key= values repeated', 'x_key= '.repeat(N / 7)],
    ['empty token= values repeated', 'token= '.repeat(N / 7)],
    // #2595: each value tests every run start in it for a swallowed secret.
    ['glued a.b_token= repeated', 'a.b_token='.repeat(N / 10)],
    ['glued aBearer repeated', 'aBearer '.repeat(N / 8)],
    ['glued X_KEY=a. repeated', 'X_KEY=a.'.repeat(N / 8)],
    ['glued Authorization repeated', 'a.Authorization: Basic '.repeat(N / 23)],
    ['glued a.b_token= then a value', `X_KEY=${'a.b_token='.repeat(N / 10)} v`],
    [
      'a raw Authorization value of many words, then a secret',
      `Authorization: abcdefghij${' a'.repeat(N / 2)} HF_TOKEN"=v`,
    ],
    // #2593: quoted values that close at a later name's quote, or never.
    ['unclosed quotes repeated', 'password="a '.repeat(N / 12)],
    ['quoted names repeated', 'x="a.b_token": '.repeat(N / 15)],
    ['a quote before each name', 'a"b_token="'.repeat(N / 11)],
  ]

  it.each(inputs)('%s', (_label, text) => {
    const started = performance.now()
    redactSecrets(text)
    expect(performance.now() - started).toBeLessThan(1500)
  })

  it('stays linear: 4x the input does not take 16x the time', () => {
    const time = (n: number): number => {
      const text = `${'--token x '.repeat(n)}${'api-token-'.repeat(n)}${'eyJ'.repeat(n)}`
      const started = performance.now()
      redactSecrets(text)
      return performance.now() - started
    }
    time(2000) // warm up
    const small = time(20_000)
    const big = time(80_000)
    // Linear is ~4x; a quadratic rule is ~16x. Allow a noisy 10x.
    expect(big).toBeLessThan(Math.max(small, 5) * 10)
  })
})

describe('redactTree', () => {
  it('masks strings at any depth and returns a copy', () => {
    const input = {
      a: ['HF_TOKEN=hf_abcdefghijklmnop', { b: 'Bearer abc123456789' }],
      n: 1,
      z: null,
    }
    const out = redactTree(input) as typeof input
    expect(JSON.stringify(out)).not.toContain('hf_abcdefghijklmnop')
    expect(JSON.stringify(out)).not.toContain('abc123456789')
    expect(out.n).toBe(1)
    expect(out.z).toBeNull()
    // The application's own object is not rewritten.
    expect(input.a[0]).toBe('HF_TOKEN=hf_abcdefghijklmnop')
  })

  it('survives a cycle', () => {
    const a: Record<string, unknown> = { s: 'HF_TOKEN=hf_abcdefghijklmnop' }
    a.self = a
    const out = redactTree(a) as Record<string, unknown>
    expect(out.self).toBe('[Circular]')
    expect(out.s).toBe(`HF_TOKEN=${MASK}`)
  })

  it('does not report a shared (non-cyclic) reference as a cycle', () => {
    const shared = { s: 'x' }
    expect(redactTree({ a: shared, b: shared })).toEqual({ a: { s: 'x' }, b: { s: 'x' } })
  })

  it('caps depth and size instead of walking them', () => {
    let deep: unknown = 'HF_TOKEN=hf_abcdefghijklmnop'
    for (let i = 0; i < 50; i += 1) deep = { next: deep }
    expect(JSON.stringify(redactTree(deep))).toContain('[Truncated]')
    expect(JSON.stringify(redactTree(deep))).not.toContain('hf_abcdefghijklmnop')

    const wide = Array.from({ length: 20_000 }, (_, i) => ({ i }))
    const started = performance.now()
    const out = redactTree(wide) as unknown[]
    expect(performance.now() - started).toBeLessThan(500)
    expect(out.length).toBe(20_000)
    expect(out.at(-1)).toBe('[Truncated]')
  })

  it('counts leaves toward the size cap, so a flat array of strings is not scanned whole', () => {
    const flat = Array.from({ length: 200_000 }, () => `HF_TOKEN=hf_abcdefghijklmnop ${'x'.repeat(40)}`)
    const started = performance.now()
    const out = redactTree(flat) as unknown[]
    expect(performance.now() - started).toBeLessThan(500)
    expect(out[0]).toBe(`HF_TOKEN=***REDACTED*** ${'x'.repeat(40)}`)
    expect(out.at(-1)).toBe('[Truncated]')
    expect(JSON.stringify(out)).not.toContain('hf_abcdefghijklmnop')
  })

  it('masks the value under a secret-named key whole, as the backend _scrub does', () => {
    const out = redactTree({
      password: 'hunter22222',
      auth: { apiKey: 'plainvalue123', nested: { client_secret: { any: 'shape' } } },
      list: [{ HAL0_ADMIN_KEY: 'abc' }],
      url: 'https://h/p',
      count: 3,
    })
    expect(out).toEqual({
      password: MASK,
      auth: { apiKey: MASK, nested: { client_secret: MASK } },
      list: [{ HAL0_ADMIN_KEY: MASK }],
      url: 'https://h/p',
      count: 3,
    })
  })

  it.each(shared.sensitive_keys)('treats the key %j as sensitive (shared fixture)', (key) => {
    expect(isSensitiveKey(key)).toBe(true)
    expect(redactTree({ [key]: 'plainvalue123' })).toEqual({ [key]: MASK })
  })

  it.each(shared.plain_keys)('treats the key %j as plain (shared fixture)', (key) => {
    expect(isSensitiveKey(key)).toBe(false)
    expect(redactTree({ [key]: 'plainvalue123' })).toEqual({ [key]: 'plainvalue123' })
  })

  it('cuts every string at ? in URL mode', () => {
    expect(redactTree({ 'url.full': 'https://h/p?api_key=q', 'http.query': '?api_key=q' }, { urls: true })).toEqual({
      'url.full': 'https://h/p',
      'http.query': '',
    })
  })
})

describe('initSentry beforeSend', () => {
  type Hook = (event: Record<string, unknown>) => Record<string, unknown> | null
  let beforeSend: Hook
  let beforeSendTransaction: Hook

  beforeAll(async () => {
    vi.stubEnv('VITE_SENTRY_DSN', 'https://public@example.invalid/1')
    vi.stubGlobal('__HAL0_UI_VERSION__', 'test')
    expect(await initSentry()).toBe(true)
    beforeSend = sdk.init.mock.calls[0][0].beforeSend as Hook
    beforeSendTransaction = sdk.init.mock.calls[0][0].beforeSendTransaction as Hook
  })

  const HF = 'hf_abcdefghijklmnopqrstuvwx'

  it('masks the raw arguments of a console breadcrumb', () => {
    // What the default Breadcrumbs integration records for console.error(...).
    const out = beforeSend({
      breadcrumbs: [
        {
          category: 'console',
          level: 'error',
          message: `HF_TOKEN=${HF}`,
          data: {
            arguments: [`HF_TOKEN=${HF}`, { headers: { Authorization: 'Bearer abc123456789' } }],
            logger: 'console',
          },
        },
      ],
    }) as { breadcrumbs: { message: string; data: { arguments: unknown[] } }[] }
    expect(JSON.stringify(out)).not.toContain(HF)
    expect(JSON.stringify(out)).not.toContain('abc123456789')
    expect(out.breadcrumbs[0].data.arguments[0]).toBe(`HF_TOKEN=${MASK}`)
  })

  it('masks a thrown plain object (extra.__serialized__), contexts and tags', () => {
    const out = beforeSend({
      extra: { __serialized__: { error: `HF_TOKEN=${HF}`, nested: [{ k: 'password: hunter22222' }] } },
      contexts: { app: { detail: 'Authorization: Bearer abc123456789' } },
      tags: { page: 'https://u:pw12345678@h/r' },
    })
    const text = JSON.stringify(out)
    expect(text).not.toContain(HF)
    expect(text).not.toContain('hunter22222')
    expect(text).not.toContain('abc123456789')
    expect(text).not.toContain('pw12345678')
  })

  it('masks userinfo and a token in request.url, and drops the query', () => {
    const out = beforeSend({
      request: {
        url: `https://user:pw12345678@host.invalid/${HF}/x?api_key=querysecret`,
        headers: { Referer: 'https://host.invalid/page?token=querysecret2', Accept: 'x' },
        query_string: 'api_key=querysecret',
        cookies: { s: 'c' },
      },
    }) as { request: { url: string; headers: Record<string, string> } }
    const text = JSON.stringify(out)
    expect(text).not.toContain('pw12345678')
    expect(text).not.toContain(HF)
    expect(text).not.toContain('querysecret')
    expect(out.request.headers.Referer).toBe('https://host.invalid/page')
    expect(out.request.headers.Accept).toBe('x')
    expect(out.request).not.toHaveProperty('query_string')
    expect(out.request).not.toHaveProperty('cookies')
  })

  it('masks navigation breadcrumb URLs and keeps the text surfaces masked', () => {
    const out = beforeSend({
      message: `HF_TOKEN=${HF}`,
      logentry: { message: `HF_TOKEN=${HF}` },
      exception: { values: [{ value: '401 Authorization: Bearer abc123456789' }] },
      breadcrumbs: [
        { category: 'navigation', data: { from: '/a?x=1', to: 'https://u:pw12345678@h/p?y=2' } },
      ],
      user: { id: 'u' },
    })
    const text = JSON.stringify(out)
    expect(text).not.toContain(HF)
    expect(text).not.toContain('abc123456789')
    expect(text).not.toContain('pw12345678')
    expect(text).not.toContain('x=1')
    expect(text).not.toContain('y=2')
    expect(out).not.toHaveProperty('user')
  })

  it('drops the event when scrubbing throws', () => {
    const hostile = {
      get breadcrumbs(): unknown {
        throw new Error('boom')
      },
    }
    expect(beforeSend(hostile as unknown as Record<string, unknown>)).toBeNull()
    expect(beforeSendTransaction(hostile as unknown as Record<string, unknown>)).toBeNull()
  })

  it('masks sensitive request headers by name, whatever the value looks like', () => {
    const headers: Record<string, string> = { Accept: 'text/html', 'X-Upstream-Token': 'plainvalue123' }
    for (const name of shared.sensitive_headers) headers[name] = `${name}-dXNlcjpwYXNz`
    headers.Authorization = 'Basic dXNlcjpwYXNz'
    headers.Cookie = 'session=abcdef0123456789'
    const out = beforeSend({ request: { url: 'https://h/p', headers } }) as {
      request: { headers: Record<string, string> }
    }
    const text = JSON.stringify(out)
    expect(text).not.toContain('dXNlcjpwYXNz')
    expect(text).not.toContain('abcdef0123456789')
    expect(text).not.toContain('plainvalue123')
    expect(out.request.headers.Authorization).toBe(MASK)
    expect(out.request.headers.Cookie).toBe(MASK)
    expect(out.request.headers.Accept).toBe('text/html')
  })

  it('masks a structured secret in a console breadcrumb by its key', () => {
    const out = beforeSend({
      breadcrumbs: [{ category: 'console', data: { arguments: ['login', { password: 'hunter22222' }] } }],
      extra: { config: { apiKey: 'plainvalue123' } },
    })
    const text = JSON.stringify(out)
    expect(text).not.toContain('hunter22222')
    expect(text).not.toContain('plainvalue123')
  })

  it('drops the query from stack frame URLs but keeps the exception text whole', () => {
    const out = beforeSend({
      exception: {
        values: [
          {
            type: 'Error',
            value: 'why? Authorization: Bearer abc123456789',
            stacktrace: {
              frames: [{ filename: 'https://h/?api_key=framesecret', abs_path: 'https://h/a.js?api_key=framesecret', lineno: 3 }],
            },
          },
        ],
      },
    }) as { exception: { values: { value: string; stacktrace: { frames: { lineno: number }[] } }[] } }
    const text = JSON.stringify(out)
    expect(text).not.toContain('framesecret')
    expect(text).not.toContain('abc123456789')
    expect(out.exception.values[0].value).toBe(`why? Authorization: Bearer ${MASK}`)
    expect(out.exception.values[0].stacktrace.frames[0].lineno).toBe(3)
  })

  it('scrubs a transaction event: name, request, trace context and every span', () => {
    const metadata = { spanCountBeforeProcessing: 1 }
    const out = beforeSendTransaction({
      type: 'transaction',
      transaction: `/p/${HF}`,
      user: { id: 'u' },
      request: { url: 'https://h/p?api_key=txsecret', headers: { Authorization: 'Basic dXNlcjpwYXNz' } },
      contexts: { trace: { data: { url: 'https://h/p?api_key=txsecret', 'sentry.op': 'pageload' } } },
      spans: [
        {
          op: 'http.client',
          description: 'GET https://u:pw12345678@h/api?api_key=txsecret',
          data: {
            url: 'https://h/api?api_key=txsecret',
            'url.full': 'https://h/api?api_key=txsecret',
            'http.query': '?api_key=txsecret',
            'http.method': 'GET',
            'http.response.status_code': 200,
            'x-auth-token': 'plainvalue123',
          },
        },
      ],
      sdkProcessingMetadata: metadata,
    }) as {
      spans: { op: string; description: string; data: Record<string, unknown> }[]
      sdkProcessingMetadata: unknown
    }
    const text = JSON.stringify(out)
    for (const secret of ['txsecret', 'pw12345678', HF, 'dXNlcjpwYXNz', 'plainvalue123']) {
      expect(text).not.toContain(secret)
    }
    expect(out).not.toHaveProperty('user')
    expect(out.spans[0].op).toBe('http.client')
    expect(out.spans[0].description).toBe(`GET https://u:${MASK}@h/api`)
    expect(out.spans[0].data['http.method']).toBe('GET')
    expect(out.spans[0].data['http.response.status_code']).toBe(200)
    // The SDK reads this back after the hook; it must survive untouched.
    expect(out.sdkProcessingMetadata).toBe(metadata)
  })
})
