// hal0 dashboard — StepsDrawer (D6 next-steps modal shape).
//
// A reusable numbered-steps drawer for any diagnosis with >=2 NextSteps:
// eyebrow + title (drawer language per COMMON.md's UI section), one numbered
// row per step, each a command (mono text + Copy, and Run when the command
// maps to a typed action) or a doc (deep-link button) or a manual step
// (expands inline). Built for DiagnosisPanel's "Steps" opener but kept
// generic — reused as-is for extension enable steps (features/*.jsx).
//
// Uses the window-global `Drawer` primitive (ui/src/dash/primitives.jsx),
// same convention as every other dash/*.jsx surface — see
// ui/eslint.config.js's window-globals allowance.

import { copyTextToClipboard } from '@/lib/clipboard'
import { openDocTarget } from './next-step-actions'
import { useNextStepRunner } from './useNextStepRunner'

const { useState } = React

function StepRow({ index, step, runner }) {
  const [expanded, setExpanded] = useState(false)
  const action = step.kind === 'command' ? runner.actionFor(step) : null

  return (
    <div className="s-row" data-testid="steps-drawer-row" style={{ display: 'flex', gap: 10, alignItems: 'flex-start', padding: '10px 0' }}>
      <span className="mono" style={{ color: 'var(--fg-4)', fontSize: 12, minWidth: 18 }}>{index + 1}.</span>
      <div style={{ display: 'flex', flexDirection: 'column', gap: 6, flex: 1, minWidth: 0 }}>
        <div style={{ fontSize: 13, color: 'var(--fg)' }}>{step.label}</div>

        {step.kind === 'command' && (
          <div style={{ display: 'flex', gap: 8, alignItems: 'center', flexWrap: 'wrap' }}>
            <code className="mono" style={{ fontSize: 11.5, color: 'var(--fg-3)', background: 'var(--bg-2)', border: '1px solid var(--line)', borderRadius: 4, padding: '3px 7px' }}>
              {step.target}
            </code>
            <button
              type="button"
              className="btn ghost sm"
              data-testid="steps-drawer-copy"
              onClick={async () => {
                const ok = await copyTextToClipboard(step.target)
                toast(ok ? 'Copied to clipboard' : 'Copy failed', ok ? 'ok' : 'err')
              }}
            >
              Copy
            </button>
            {action && (
              <button
                type="button"
                className="btn sm"
                data-testid="steps-drawer-run"
                disabled={runner.pending}
                onClick={() => runner.run(step)}
              >
                Run
              </button>
            )}
          </div>
        )}

        {step.kind === 'doc' && (
          <button
            type="button"
            className="btn ghost sm"
            data-testid="steps-drawer-open-doc"
            onClick={() => openDocTarget(step.target)}
          >
            Open docs ↗
          </button>
        )}

        {step.kind === 'manual' && (
          <>
            <button
              type="button"
              className="btn ghost sm"
              data-testid="steps-drawer-manual-toggle"
              onClick={() => setExpanded((v) => !v)}
              aria-expanded={expanded}
            >
              {expanded ? 'Hide details' : 'Show details'}
            </button>
            {expanded && (
              <div className="mono" style={{ fontSize: 12, color: 'var(--fg-3)', lineHeight: 1.5 }}>
                {step.target}
              </div>
            )}
          </>
        )}
      </div>
    </div>
  )
}

export function StepsDrawer({ open, onClose, eyebrow, title, steps }) {
  const runner = useNextStepRunner()
  return (
    <Drawer open={open} onClose={onClose} eyebrow={eyebrow || 'Next steps'} title={title} width={480}>
      <div data-testid="steps-drawer" style={{ display: 'flex', flexDirection: 'column' }}>
        {(steps || []).map((step, i) => (
          <StepRow key={`${step.kind}-${step.target}-${i}`} index={i} step={step} runner={runner} />
        ))}
      </div>
    </Drawer>
  )
}

Object.assign(window, { StepsDrawer })
