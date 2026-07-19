// SERVER ▸ Security ▸ Route exposure (D4, post-R3 surface rework; live wiring
// landed UI-API-2).
//
// Read-only view of hal0's deny-by-default route-classification taxonomy
// (src/hal0/security/exposure.py — the AuthClass table the enforcement
// middleware, the exposure-CI ratchet, and this page all key off).
//
// The taxonomy itself (the four classes + what each means) is a stable,
// documented contract, so it's mirrored here as a static legend that teaches
// the model. The LIVE per-route classification — the actual (method, path) →
// class rows, plus the OPEN allowlist — now comes from GET /api/auth/exposure
// (routes/auth.py; walks the real RULES/OPEN_ALLOWLIST tuples in evaluation
// order, so it can't silently rot the way a hardcoded copy would). That route
// is ADMIN-gated, so a non-admin caller (or auth off with an anonymous
// session) sees an honest permission reason instead of a fabricated table.

import { useAuthExposure } from '@/api/hooks/useAuthExposure'

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

const CLASS_HUE = Object.fromEntries(CLASSES.map((c) => [c.key, c]))

// `authClass` comes off the wire lowercase (AuthClass.value, e.g. "admin") —
// the legend above keys on the uppercase taxonomy name, so normalise before
// both the lookup and the label.
function classChip(authClass) {
  const upper = String(authClass || '').toUpperCase()
  const c = CLASS_HUE[upper] || CLASS_HUE.ADMIN
  return (
    <span
      className="chip mono"
      style={{ color: c.hue, borderColor: c.line, background: c.soft, fontSize: 10, letterSpacing: '.04em' }}
    >
      {upper}
    </span>
  )
}

function permissionReason(error) {
  if (error?.status === 401 || error?.status === 403 || error?.code === 'auth.forbidden') {
    return 'Live per-route table requires an admin session — GET /api/auth/exposure is ADMIN-gated (log in as admin to view it).'
  }
  return error?.message
    ? `Live per-route table failed to load: ${error.message}`
    : 'Live per-route table failed to load.'
}

export function ExposureTable() {
  const exposure = useAuthExposure()
  const loading = exposure.isPending
  const errored = exposure.isError
  const rules = exposure.data?.rules ?? []
  const allowlist = exposure.data?.open_allowlist ?? []

  return (
    <div className="s-section" data-testid="exposure-table">
      <h3 style={{ margin: '0 0 4px', fontSize: 13 }}>Route exposure</h3>
      <p className="desc" style={{ marginTop: 0 }}>
        The backend&apos;s deny-by-default classification table is the source of truth for how much auth
        each route needs — read-only here. Below is the class taxonomy, then the live per-route table
        (<span className="mono">GET /api/auth/exposure</span>).
      </p>

      <div className="s-panel" style={{ marginBottom: 14 }}>
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

      <h4 className="mono" style={{ margin: '0 0 6px', fontSize: 11, color: 'var(--fg-4)' }}>
        Live per-route table
      </h4>

      {loading && (
        <div data-testid="exposure-live-loading" className="mono" style={{ fontSize: 11, color: 'var(--fg-5)', padding: '8px 2px' }}>
          loading route table…
        </div>
      )}

      {errored && (
        <div
          data-testid="exposure-live-error"
          className="mono"
          style={{ fontSize: 10.5, color: 'var(--warn)', padding: '8px 2px', lineHeight: 1.55 }}
        >
          ○ {permissionReason(exposure.error)}
        </div>
      )}

      {!loading && !errored && (
        <>
          <div className="s-panel" data-testid="exposure-live-rules" style={{ marginBottom: 10 }}>
            {rules.length === 0 && (
              <div className="mono" style={{ fontSize: 11, color: 'var(--fg-5)', padding: '10px 14px' }}>
                no rules reported
              </div>
            )}
            {rules.map((r, i) => (
              <div
                key={`${r.label}-${i}`}
                className="s-row"
                data-testid="exposure-rule-row"
                style={{ display: 'grid', gridTemplateColumns: '90px 90px 1fr', gap: 12, alignItems: 'center', padding: '9px 14px' }}
              >
                {classChip(r.auth_class)}
                <span className="mono" style={{ fontSize: 10.5, color: 'var(--fg-4)' }}>
                  {(r.methods && r.methods.join('/')) || 'ANY'}
                </span>
                <div>
                  <div style={{ fontSize: 12, color: 'var(--fg-2)' }}>{r.label}</div>
                  {r.pattern && (
                    <div className="mono" style={{ fontSize: 10.5, color: 'var(--fg-5)', marginTop: 2 }}>
                      {r.pattern}
                    </div>
                  )}
                </div>
              </div>
            ))}
          </div>

          <h4 className="mono" style={{ margin: '0 0 6px', fontSize: 11, color: 'var(--fg-4)' }}>
            OPEN allowlist ({allowlist.length})
          </h4>
          <div className="s-panel" data-testid="exposure-live-allowlist">
            {allowlist.length === 0 && (
              <div className="mono" style={{ fontSize: 11, color: 'var(--fg-5)', padding: '10px 14px' }}>
                no allowlist entries reported
              </div>
            )}
            {allowlist.map((a, i) => (
              <div
                key={`${a.method}-${a.path}-${i}`}
                className="s-row mono"
                data-testid="exposure-allowlist-row"
                style={{ display: 'grid', gridTemplateColumns: '70px 1fr', gap: 12, padding: '7px 14px', fontSize: 11, color: 'var(--fg-3)' }}
              >
                <span style={{ color: 'var(--fg-5)' }}>{a.method}</span>
                <span>{a.path}</span>
              </div>
            ))}
          </div>
        </>
      )}
    </div>
  )
}
