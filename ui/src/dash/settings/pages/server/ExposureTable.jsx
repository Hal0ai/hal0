// SERVER ▸ Security ▸ Route exposure (D4, post-R3 surface rework).
//
// Read-only view of hal0's deny-by-default route-classification taxonomy
// (src/hal0/security/exposure.py — the AuthClass table the enforcement
// middleware, the exposure-CI ratchet, and this page all key off).
//
// The taxonomy itself (the four classes + what each means) is a stable,
// documented contract, so it's mirrored here as a static legend that teaches
// the model. But the LIVE per-route classification — the actual (method, path)
// → class rows and per-class counts — is static SERVER data with NO route
// serving it today: nothing under src/hal0/api/routes exposes RULES /
// OPEN_ALLOWLIST over HTTP. So the concrete table is a stub-with-reason (an
// API-lane request: GET /api/auth/exposure) rather than a hardcoded copy of
// the backend table that would silently rot the moment a rule changes.

const CLASSES = [
  {
    key: 'OPEN',
    hue: 'var(--warn)',
    line: 'var(--warn-line)',
    soft: 'var(--warn-soft)',
    blurb: 'No auth, ever. A tiny, enumerated allowlist — liveness, the /v1/models SDK probe, the Prometheus scrape, login/status, the static SPA shell.',
    example: '/api/health · /v1/models',
  },
  {
    key: 'CLIENT',
    hue: 'var(--info)',
    line: 'var(--line)',
    soft: 'var(--bg-2)',
    blurb: 'The /v1/* inference surface plus a short list of read-only introspection GETs (models/slots/stats/hardware/system-info). Reachable with a client key.',
    example: '/v1/chat/completions · /api/system-info',
  },
  {
    key: 'ADMIN',
    hue: 'var(--err)',
    line: 'var(--err-line)',
    soft: 'var(--err-soft)',
    blurb: 'Everything mutating, config-bearing, or secret-returning — and every unclassified path (deny-by-default). A new router is locked out until a rule is added.',
    example: '/api/settings · /api/secrets',
  },
  {
    key: 'BOOTSTRAP',
    hue: 'var(--fg-3)',
    line: 'var(--line)',
    soft: 'var(--bg-2)',
    blurb: 'Open only until an admin key is configured (the installer surface). Once HAL0_ADMIN_KEY is set, the gate treats BOOTSTRAP exactly like ADMIN.',
    example: '/api/install/*',
  },
]

const LIVE_TABLE_REASON =
  'Live per-route classification is not served over HTTP yet — the deny-by-default table (security/exposure.py) is static server data with no read endpoint. (API-lane request: GET /api/auth/exposure)'

export function ExposureTable() {
  return (
    <div className="s-section" data-testid="exposure-table">
      <h3 style={{ margin: '0 0 4px', fontSize: 13 }}>Route exposure</h3>
      <p className="desc" style={{ marginTop: 0 }}>
        The backend&apos;s deny-by-default classification table is the source of truth for how much auth
        each route needs — read-only here. Below is the class taxonomy; the concrete per-route table
        needs a read endpoint that does not exist yet.
      </p>

      <div className="s-panel">
        {CLASSES.map((c) => (
          <div
            key={c.key}
            className="s-row"
            data-testid={`exposure-class-${c.key.toLowerCase()}`}
            style={{ display: 'grid', gridTemplateColumns: '110px 1fr', gap: 14, alignItems: 'start', padding: '12px 14px' }}
          >
            <span
              className="chip mono"
              style={{ color: c.hue, borderColor: c.line, background: c.soft, fontSize: 10.5, letterSpacing: '.04em', justifySelf: 'start' }}
            >
              {c.key}
            </span>
            <div>
              <div style={{ fontSize: 12.5, color: 'var(--fg-2)', lineHeight: 1.5 }}>{c.blurb}</div>
              <div className="mono" style={{ fontSize: 10.5, color: 'var(--fg-5)', marginTop: 4 }}>
                e.g. {c.example}
              </div>
            </div>
          </div>
        ))}
      </div>

      <div
        data-testid="exposure-live-stub"
        className="mono"
        style={{ marginTop: 10, fontSize: 10.5, color: 'var(--fg-5)', lineHeight: 1.55 }}
        title={LIVE_TABLE_REASON}
      >
        ○ Live per-route table unavailable — {LIVE_TABLE_REASON}
      </div>
    </div>
  )
}
