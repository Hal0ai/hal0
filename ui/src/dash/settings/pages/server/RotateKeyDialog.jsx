// SERVER ▸ Security ▸ Rotate key dialog (D4 → key-rotation lane).
//
// Wired to the real POST /api/auth/rotate (routes/auth.py). The endpoint mints
// a fresh box key, writes it into /etc/hal0/api.env (0640, never world-
// readable), and applies it live in-process — no restart. It returns STATUS
// ONLY: { tier, rotated_at, key_len, fingerprint, applies_live,
// restart_required, session_preserved, note }.
//
// Two non-negotiables the copy + code encode:
//   1. The confirm is blunt about breakage — rotating a key logs out every
//      client using the old one the moment you rotate. Type-to-confirm gate so
//      a rotate is never a reflexive click.
//   2. The new value is NEVER shown. Unlike a classic "reveal once" flow, hal0
//      never sends the key over the wire: the operator retrieves it out-of-band
//      from /etc/hal0/api.env on the box. We surface the fingerprint (a one-way
//      hash prefix) so they can VERIFY which key is live — never the value.

import { useRotateKey } from '@/api/hooks/useAuthActions'

const { useState: useStateK, useEffect: useEffectK } = React

// The phrase the operator must type, per tier. Blunt + specific so a rotate is
// never a reflexive click.
function confirmPhrase(tier) {
  return `rotate ${tier}`
}

export function RotateKeyDialog({ open, tier = 'admin', onClose, onRotated }) {
  const [typed, setTyped] = useStateK('')
  const [result, setResult] = useStateK(null)
  const rotate = useRotateKey()

  useEffectK(() => {
    if (open) {
      setTyped('')
      setResult(null)
      rotate.reset()
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [open, tier])

  if (!open) return null

  const phrase = confirmPhrase(tier)
  const phraseOk = typed.trim() === phrase
  const pending = rotate.isPending
  const canRotate = phraseOk && !pending && result == null

  const doRotate = () => {
    if (!canRotate) return
    rotate.mutate(tier, {
      onSuccess: (data) => {
        setResult(data)
        onRotated?.(data)
      },
    })
  }

  const errText = rotate.isError
    ? rotate.error?.code === 'auth.rate_limited'
      ? 'Too many rotate attempts — slow down and retry shortly.'
      : rotate.error?.message || 'Rotation failed. Check the server logs.'
    : null

  return (
    <Modal
      open={open}
      onClose={onClose}
      eyebrow={
        result ? (
          <span style={{ color: 'var(--ok)' }}>Rotated · old {tier} key is now dead</span>
        ) : (
          <span style={{ color: 'var(--err)' }}>Destructive · breaks existing clients</span>
        )
      }
      title={result ? `${tier} key rotated` : `Rotate ${tier} key?`}
      width={460}
      foot={
        result ? (
          <>
            <span />
            <button className="btn sm" data-testid="rotate-done" onClick={onClose}>
              Done
            </button>
          </>
        ) : (
          <>
            <span />
            <span style={{ display: 'inline-flex', gap: 8, alignItems: 'center' }}>
              <button className="btn ghost sm" data-testid="rotate-cancel" onClick={onClose}>Cancel</button>
              <button
                className="btn sm"
                data-testid="rotate-confirm"
                disabled={!canRotate}
                onClick={doRotate}
                style={{ background: 'var(--err-soft)', borderColor: 'var(--err-line)', color: 'var(--err)' }}
              >
                {pending ? 'Rotating…' : 'Rotate key'}
              </button>
            </span>
          </>
        )
      }
    >
      {result == null ? (
        <div style={{ display: 'flex', flexDirection: 'column', gap: 12 }}>
          <p style={{ fontSize: 12.5, lineHeight: 1.6, color: 'var(--fg-2)', margin: 0 }}>
            Every client using the current {tier} key stops working the moment you rotate. The new key
            is written to <span className="mono">/etc/hal0/api.env</span> on the box and applied live —
            it is <b>never shown in the dashboard</b>. Retrieve it there; a browser session stays signed
            in (cookie-based).
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

          {errText && (
            <div data-testid="rotate-error" className="mono err" style={{ fontSize: 10.5, lineHeight: 1.55 }}>
              {errText}
            </div>
          )}
        </div>
      ) : (
        // Status-only result — fingerprint + rotated_at + the re-auth notice.
        // There is NO value here by design; the key never traverses the wire.
        <div data-testid="rotate-result" style={{ display: 'flex', flexDirection: 'column', gap: 10 }}>
          <div style={{ display: 'flex', gap: 14, flexWrap: 'wrap' }}>
            <div>
              <div className="mono" style={{ fontSize: 10, color: 'var(--fg-5)' }}>fingerprint</div>
              <div data-testid="rotate-fingerprint" className="mono" style={{ fontSize: 13, color: 'var(--ok)' }}>
                {result.fingerprint}
              </div>
            </div>
            <div>
              <div className="mono" style={{ fontSize: 10, color: 'var(--fg-5)' }}>rotated</div>
              <div data-testid="rotate-rotated-at" className="mono" style={{ fontSize: 11.5, color: 'var(--fg-2)' }}>
                {result.rotated_at}
              </div>
            </div>
            <div>
              <div className="mono" style={{ fontSize: 10, color: 'var(--fg-5)' }}>applied</div>
              <div className="mono" style={{ fontSize: 11.5, color: result.restart_required ? 'var(--warn)' : 'var(--ok)' }}>
                {result.restart_required ? 'restart required' : 'live · no restart'}
              </div>
            </div>
          </div>
          <p data-testid="rotate-note" style={{ fontSize: 12, lineHeight: 1.6, color: 'var(--fg-2)', margin: 0 }}>
            {result.note}
          </p>
        </div>
      )}
    </Modal>
  )
}
