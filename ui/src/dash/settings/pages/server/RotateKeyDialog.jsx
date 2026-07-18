// SERVER ▸ Security ▸ Rotate key dialog (D4, post-R3 surface rework).
//
// The rotate flow, built honestly against a backend that does NOT exist yet:
// there is no key-rotation route in src/hal0/api (only GET /api/auth/status +
// POST /api/auth/login). So the destructive confirm is disabled-with-reason —
// an API-lane request (POST /api/auth/keys/{tier}/rotate) — while the whole
// interaction (blunt breakage warning + type-to-confirm gate + one-time reveal
// contract) is fully wired for when it lands.
//
// Two non-negotiables the copy encodes:
//   1. The confirm is blunt about breakage — rotating the admin key logs out
//      every client using the current one, the moment you rotate.
//   2. The new value is shown ONCE for copy, then never again and never stored
//      in the dashboard (status-only surface — see SecurityPage.jsx). Until the
//      endpoint exists there is nothing to reveal; the reveal panel is the
//      contract for the value the future response returns, never a fabrication.

const { useState: useStateK, useEffect: useEffectK } = React

const ROTATE_DISABLED_REASON =
  'Key rotation is not wired yet — no rotation route exists (only /api/auth/status + /api/auth/login). (API-lane request: POST /api/auth/keys/{tier}/rotate)'

// The phrase the operator must type, per tier. Blunt + specific so a rotate is
// never a reflexive click.
function confirmPhrase(tier) {
  return `rotate ${tier}`
}

export function RotateKeyDialog({ open, tier = 'admin', onClose }) {
  const [typed, setTyped] = useStateK('')
  // Reveal state is the contract for a real rotation response; with no endpoint
  // it never populates. Kept so the one-time-reveal surface is exercised the
  // moment the route lands (revealed === the value returned once by rotate).
  const [revealed] = useStateK(null)

  useEffectK(() => {
    if (open) setTyped('')
  }, [open, tier])

  if (!open) return null

  const phrase = confirmPhrase(tier)
  const phraseOk = typed.trim() === phrase
  // Even a correctly-typed phrase can't rotate: the endpoint is absent.
  const canRotate = false

  return (
    <Modal
      open={open}
      onClose={onClose}
      eyebrow={<span style={{ color: 'var(--err)' }}>Destructive · breaks existing clients</span>}
      title={`Rotate ${tier} key?`}
      width={460}
      foot={
        <>
          <span />
          <span style={{ display: 'inline-flex', gap: 8, alignItems: 'center' }}>
            <button className="btn ghost sm" data-testid="rotate-cancel" onClick={onClose}>Cancel</button>
            <button
              className="btn sm"
              data-testid="rotate-confirm"
              disabled={!canRotate}
              title={!canRotate ? ROTATE_DISABLED_REASON : undefined}
              onClick={onClose}
              style={{ background: 'var(--err-soft)', borderColor: 'var(--err-line)', color: 'var(--err)' }}
            >
              Rotate key
            </button>
          </span>
        </>
      }
    >
      {revealed == null ? (
        <div style={{ display: 'flex', flexDirection: 'column', gap: 12 }}>
          <p style={{ fontSize: 12.5, lineHeight: 1.6, color: 'var(--fg-2)', margin: 0 }}>
            Every client using the current {tier} key stops working the moment you rotate. You&apos;ll get
            the new key <b>once</b> — copy it before closing; it is never shown again and never stored in
            the dashboard.
          </p>

          <label className="mono" style={{ fontSize: 11, color: 'var(--fg-4)' }}>
            Type <span style={{ color: 'var(--fg-2)' }}>{phrase}</span> to confirm
          </label>
          <input
            className="mono"
            data-testid="rotate-confirm-input"
            value={typed}
            onChange={(e) => setTyped(e.target.value)}
            placeholder={phrase}
            autoComplete="off"
            spellCheck={false}
            style={{
              padding: '8px 10px',
              fontSize: 12.5,
              background: 'var(--bg-2)',
              border: `1px solid ${phraseOk ? 'var(--ok-line)' : 'var(--line)'}`,
              borderRadius: 6,
              color: 'var(--fg)',
            }}
          />

          <div
            data-testid="rotate-blocked-reason"
            className="mono"
            style={{ fontSize: 10.5, color: 'var(--fg-5)', lineHeight: 1.55, borderTop: '1px solid var(--line)', paddingTop: 10 }}
          >
            ○ {ROTATE_DISABLED_REASON}
          </div>
        </div>
      ) : (
        // One-time reveal — reached only after a real rotation returns a value.
        <div style={{ display: 'flex', flexDirection: 'column', gap: 10 }}>
          <p style={{ fontSize: 12.5, color: 'var(--fg-2)', margin: 0 }}>
            New {tier} key — shown once. Copy it now; it is never shown again.
          </p>
          <code
            data-testid="rotate-revealed-once"
            className="mono"
            style={{ padding: '10px 12px', background: 'var(--bg-2)', border: '1px solid var(--line)', borderRadius: 6, fontSize: 12.5, wordBreak: 'break-all' }}
          >
            {revealed}
          </code>
        </div>
      )}
    </Modal>
  )
}
