// hal0 dashboard — generic Diagnosis panel (D6, post-R3 surface rework).
//
// ONE renderer for the typed Diagnosis shape (src/hal0/diagnostics.py: id,
// severity, confidence, summary, detail, evidence[], next_steps[], fixable).
// The UI NEVER hardcodes a specific check — every card is data-driven, so the
// backend taxonomy and this surface stay aligned: the live GET /api/doctor rows
// render here with zero panel changes (#1458).
//
// Depth-2 per the scoping answers: one panel component, wired to the server's
// doctor feed via useDiagnoses. A backend without that route degrades to
// synthesised GET /api/system-info evidence plus a stub-with-reason.

import { copyTextToClipboard } from '@/lib/clipboard'
import { useDiagnoses } from '@/api/hooks/useDiagnoses'
import { openDocTarget } from './next-step-actions'
import { useNextStepRunner } from './useNextStepRunner'
import { StepsDrawer } from './StepsDrawer.jsx'

const { useState } = React

// severity → hue. hal0's Severity is info|warn|fail|critical; the CSS palette
// has --info/--warn/--err/--ok, so fail+critical both map to --err (critical
// gets the filled treatment). Kept as data so a new severity is a one-line add.
const SEV = {
  info: { hue: 'var(--info)', line: 'var(--line)', soft: 'var(--bg-2)', glyph: 'ⓘ' },
  warn: { hue: 'var(--warn)', line: 'var(--warn-line)', soft: 'var(--warn-soft)', glyph: '⚠' },
  fail: { hue: 'var(--err)', line: 'var(--err-line)', soft: 'var(--err-soft)', glyph: '✕' },
  critical: { hue: 'var(--err)', line: 'var(--err-line)', soft: 'var(--err-soft)', glyph: '✕' },
}

const VERDICT = {
  ok: { label: 'all clear', hue: 'var(--ok)' },
  warn: { label: 'needs attention', hue: 'var(--warn)' },
  critical: { label: 'critical', hue: 'var(--err)' },
}

const CHIP_STYLE = {
  fontSize: 10.5,
  color: 'var(--fg-3)',
  borderColor: 'var(--line)',
  background: 'var(--bg-2)',
  display: 'inline-flex',
  alignItems: 'center',
  gap: 6,
}

// One acting chip per NextStep: `command` copies (and, where the command
// maps to an existing typed mutation, offers Run); `doc` opens the link;
// `manual` expands the full instruction inline. The command text itself
// renders in mono next to the label — no longer tooltip-only (D6 chips).
function NextStep({ step, runner }) {
  const [expanded, setExpanded] = useState(false)

  if (step.kind === 'doc') {
    return (
      <button type="button" className="chip mono" data-testid="diagnosis-next-step" style={CHIP_STYLE} onClick={() => openDocTarget(step.target)}>
        ↗ {step.label}
      </button>
    )
  }

  if (step.kind === 'manual') {
    return (
      <span className="chip mono" data-testid="diagnosis-next-step" style={{ ...CHIP_STYLE, flexDirection: 'column', alignItems: 'flex-start' }}>
        <button
          type="button"
          data-testid="diagnosis-next-step-manual-toggle"
          onClick={() => setExpanded((v) => !v)}
          aria-expanded={expanded}
          style={{ background: 'none', border: 'none', color: 'inherit', font: 'inherit', padding: 0, cursor: 'pointer' }}
        >
          • {step.label}
        </button>
        {expanded && <span style={{ color: 'var(--fg-4)' }}>{step.target}</span>}
      </span>
    )
  }

  // command
  const action = runner.actionFor(step)
  return (
    <span className="chip mono" data-testid="diagnosis-next-step" style={CHIP_STYLE}>
      $ {step.label}
      <code style={{ color: 'var(--fg-4)' }}>{step.target}</code>
      <button
        type="button"
        data-testid="diagnosis-next-step-copy"
        aria-label={`Copy ${step.target}`}
        onClick={async () => {
          const ok = await copyTextToClipboard(step.target)
          toast(ok ? 'Copied to clipboard' : 'Copy failed', ok ? 'ok' : 'err')
        }}
        style={{ background: 'none', border: '1px solid var(--line)', borderRadius: 4, color: 'inherit', font: 'inherit', padding: '1px 5px', cursor: 'pointer' }}
      >
        Copy
      </button>
      {action && (
        <button
          type="button"
          data-testid="diagnosis-next-step-run"
          disabled={runner.pending}
          onClick={() => runner.run(step)}
          style={{ background: 'none', border: '1px solid var(--line)', borderRadius: 4, color: 'inherit', font: 'inherit', padding: '1px 5px', cursor: 'pointer' }}
        >
          Run
        </button>
      )}
    </span>
  )
}

function DiagnosisCard({ d }) {
  const sev = SEV[d.severity] || SEV.info
  const runner = useNextStepRunner()
  const [stepsOpen, setStepsOpen] = useState(false)
  const steps = Array.isArray(d.next_steps) ? d.next_steps : []
  return (
    <div
      className="s-row"
      data-testid={`diagnosis-card-${d.id}`}
      data-severity={d.severity}
      style={{ display: 'flex', flexDirection: 'column', gap: 8, padding: '13px 14px', borderLeft: `2px solid ${sev.hue}` }}
    >
      {/* header: severity · id · summary */}
      <div style={{ display: 'flex', alignItems: 'center', gap: 10 }}>
        <span
          className="chip mono"
          data-testid="diagnosis-severity"
          style={{ fontSize: 10, color: sev.hue, borderColor: sev.line, background: sev.soft }}
        >
          {sev.glyph} {d.severity}
        </span>
        <span className="mono" data-testid="diagnosis-id" style={{ fontSize: 11, color: 'var(--fg-4)' }}>{d.id}</span>
        <span style={{ fontSize: 13, color: 'var(--fg)' }}>{d.summary}</span>
        <span style={{ flex: 1 }} />
        {steps.length >= 2 && (
          <button
            type="button"
            className="btn ghost sm"
            data-testid="diagnosis-open-steps"
            onClick={() => setStepsOpen(true)}
          >
            Steps ({steps.length})
          </button>
        )}
        {d.confidence && (
          <span className="mono" style={{ fontSize: 9.5, color: 'var(--fg-5)' }}>{d.confidence} confidence</span>
        )}
      </div>

      {d.detail && (
        <div style={{ fontSize: 12, color: 'var(--fg-3)', lineHeight: 1.55 }}>{d.detail}</div>
      )}

      {/* evidence */}
      {Array.isArray(d.evidence) && d.evidence.length > 0 && (
        <div data-testid="diagnosis-evidence" style={{ display: 'flex', flexDirection: 'column', gap: 3, borderTop: '1px solid var(--line)', paddingTop: 8 }}>
          {d.evidence.map((e, i) => (
            <div key={i} className="mono" style={{ fontSize: 11, color: 'var(--fg-3)' }}>
              <span style={{ color: 'var(--fg-5)' }}>{e.kind}</span> · {e.summary}
            </div>
          ))}
        </div>
      )}

      {/* next steps */}
      {steps.length > 0 && (
        <div style={{ display: 'flex', flexWrap: 'wrap', gap: 6, marginTop: 2 }}>
          {steps.map((s, i) => <NextStep key={i} step={s} runner={runner} />)}
        </div>
      )}

      {steps.length >= 2 && (
        <StepsDrawer
          open={stepsOpen}
          onClose={() => setStepsOpen(false)}
          eyebrow={d.id}
          title={d.summary}
          steps={steps}
        />
      )}
    </div>
  )
}

export function DiagnosisPanel() {
  const dx = useDiagnoses()
  const v = VERDICT[dx.verdict] || VERDICT.ok

  return (
    <div className="s-section" data-testid="diagnosis-panel">
      <div style={{ display: 'flex', alignItems: 'center', gap: 10 }}>
        <h2 style={{ margin: 0 }}>System health</h2>
        <span
          className="chip mono"
          data-testid="diagnosis-verdict"
          style={{ fontSize: 10.5, color: v.hue, borderColor: 'var(--line)', background: 'var(--bg-2)' }}
        >
          {v.label}
        </span>
        <span style={{ flex: 1 }} />
        <button className="btn ghost sm" data-testid="diagnosis-rerun" onClick={() => dx.refetch()} disabled={dx.isLoading}>
          Re-run
        </button>
      </div>
      <p className="desc" style={{ marginTop: 6 }}>
        A generic renderer for typed <span className="mono">hal0 doctor</span> diagnoses (HAL0-* id ·
        severity · evidence · next steps). The panel never hardcodes a check — cards are data-driven, so
        the backend taxonomy and this surface can&apos;t drift.
      </p>

      <div className="s-panel">
        {dx.isLoading && (
          <div className="s-row"><span className="mono" style={{ fontSize: 12, color: 'var(--fg-4)' }}>Collecting system evidence…</span></div>
        )}
        {dx.isError && (
          <div className="s-row"><span className="err">{dx.error?.message || 'Failed to read the doctor feed'}</span></div>
        )}
        {!dx.isLoading && !dx.isError && dx.diagnoses.length === 0 && (
          <div className="s-row" data-testid="diagnosis-empty">
            <span className="mono" style={{ fontSize: 12, color: 'var(--fg-4)' }}>
              {dx.probeUnavailable ? 'System-info probe unavailable — no hardware evidence to show.' : 'No diagnoses reported.'}
            </span>
          </div>
        )}
        {dx.diagnoses.map((d) => <DiagnosisCard key={d.id} d={d} />)}
      </div>

      {dx.doctorFeedPending && (
        <div
          data-testid="diagnosis-feed-stub"
          className="mono"
          style={{ marginTop: 10, fontSize: 10.5, color: 'var(--fg-5)', lineHeight: 1.55 }}
          title={dx.doctorFeedReason}
        >
          ○ {dx.doctorFeedReason}
        </div>
      )}
    </div>
  )
}
