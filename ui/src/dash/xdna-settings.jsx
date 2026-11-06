// xdna-settings.jsx — the slot drawer's engine block for an xdna
// (ggml-xdna llama-server) slot.
//
// Unlike halogen's HALOGEN_* flags (which live on the PROFILE, ADR-0029),
// the xdna knobs are per-SLOT: they write the `[xdna]` table on the slot's
// own config via the existing PUT /api/slots/{name}/config + restart flow
// (the same contract the NPU occupancy card's writeAnchor uses), because a
// pmode/env/host_xrt choice is a hardware-affinity fact of THIS resident
// slot, not a portable tune template shared across slots.
//
// The knob catalogue (GET /api/runners/xdna/knobs) is the single source of
// truth for which GGML_XDNA_* names are legal: an env row typed outside that
// list is rejected inline, the same UX as the halogen engine block's
// `eng-row` controls (engine-settings.jsx).
import React from 'react'
import { useXdnaKnobs } from '@/api/hooks/useXdnaKnobs'

const REQUANT_CMD = 'llama-quantize --tensor-type ssm_out=q5_K <in> <out> Q4_K_M'

function copyToClipboard(text) {
  if (typeof navigator !== 'undefined' && navigator.clipboard?.writeText) {
    navigator.clipboard.writeText(text).catch(() => {})
  }
}

/**
 * @param {{ xdna: {pmode?: string|null, env?: Record<string,string>, host_xrt?: string|null},
 *           xdnaPath?: string|null, readOnly?: boolean,
 *           onChange: (next: {pmode?: string|null, env?: Record<string,string>, host_xrt?: string|null}) => void }} props
 */
export function XdnaEngineSettings({ xdna, xdnaPath, readOnly = false, onChange }) {
  const knobsQuery = useXdnaKnobs()
  const knobs = knobsQuery.data
  const pmodes = knobs?.pmodes || []
  const envCatalog = knobs?.env || []
  const legalNames = new Set(envCatalog.map((k) => k.name))
  const env = xdna?.env || {}
  const [draftName, setDraftName] = React.useState('')
  const [nameError, setNameError] = React.useState(null)

  const set = (patch) => {
    if (!readOnly) onChange({ pmode: xdna?.pmode ?? null, env, host_xrt: xdna?.host_xrt ?? null, ...patch })
  }
  const setEnvValue = (name, value) => {
    const next = { ...env }
    if (value === '' || value == null) delete next[name]
    else next[name] = value
    set({ env: next })
  }
  const addRow = () => {
    const name = draftName.trim()
    if (!name) return
    if (!legalNames.has(name)) {
      setNameError(`"${name}" is not a known GGML_XDNA_* knob`)
      return
    }
    setNameError(null)
    setDraftName('')
    setEnvValue(name, '')
  }

  return (
    <div className="xdna-eng" data-testid="xdna-engine-settings">
      <div className="eng-row" data-testid="xdna-pmode-row">
        <div className="eng-head">
          <span className="eng-lbl">Power mode</span>
        </div>
        <div className="eng-ctl">
          <div className="tune-segs" role="group" aria-label="Power mode">
            {pmodes.map((p) => (
              <button
                key={p}
                type="button"
                className={'tune-seg' + ((xdna?.pmode ?? null) === p ? ' on' : '')}
                aria-pressed={(xdna?.pmode ?? null) === p}
                disabled={readOnly}
                data-testid={`xdna-pmode-opt-${p}`}
                onClick={() => set({ pmode: p })}
              >
                {p}
              </button>
            ))}
          </div>
        </div>
        <div className="eng-help">applied host-side</div>
      </div>

      <div className="eng-row" data-testid="xdna-env-table">
        <div className="eng-head">
          <span className="eng-lbl">GGML_XDNA_* knobs</span>
        </div>
        {Object.keys(env).length > 0 && (
          <table className="xdna-env-rows">
            <tbody>
              {Object.entries(env).map(([name, value]) => (
                <tr key={name} data-testid={`xdna-env-${name}`}>
                  <td className="mono">{name}</td>
                  <td>
                    <input
                      className="pf-input mono"
                      value={value}
                      disabled={readOnly}
                      placeholder={envCatalog.find((k) => k.name === name)?.default ?? ''}
                      aria-label={name}
                      onChange={(e) => setEnvValue(name, e.target.value)}
                    />
                  </td>
                  <td>
                    {!readOnly && (
                      <button
                        type="button"
                        className="eng-reset mono"
                        aria-label={`Remove ${name}`}
                        onClick={() => setEnvValue(name, null)}
                      >
                        reset
                      </button>
                    )}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        )}
        {!readOnly && (
          <div className="xdna-env-add">
            <input
              className="pf-input mono"
              list="xdna-env-names"
              placeholder="GGML_XDNA_…"
              value={draftName}
              data-testid="xdna-env-name-input"
              onChange={(e) => {
                setDraftName(e.target.value)
                setNameError(null)
              }}
              onKeyDown={(e) => e.key === 'Enter' && addRow()}
            />
            <datalist id="xdna-env-names">
              {envCatalog.map((k) => (
                <option key={k.name} value={k.name} />
              ))}
            </datalist>
            <button type="button" className="btn ghost sm" data-testid="xdna-env-add" onClick={addRow}>
              Add
            </button>
          </div>
        )}
        {nameError && (
          <div className="pf-msg err mono eng-err" role="alert" data-testid="xdna-env-name-error">
            {nameError}
          </div>
        )}
      </div>

      <div className="eng-row" data-testid="xdna-host-xrt-row">
        <div className="eng-head">
          <span className="eng-lbl">host_xrt path</span>
        </div>
        <div className="eng-ctl">
          <input
            className="pf-input mono"
            value={xdna?.host_xrt ?? ''}
            disabled={readOnly}
            placeholder={knobs?.host_xrt?.doc || '/opt/xilinx/xrt'}
            aria-label="host_xrt path"
            data-testid="xdna-host-xrt-input"
            onChange={(e) => {
              const v = e.target.value
              set({ host_xrt: v === '' ? null : v })
            }}
          />
        </div>
        {xdna?.host_xrt && !xdna.host_xrt.startsWith('/') && (
          <div className="pf-msg err mono eng-err" role="alert" data-testid="xdna-host-xrt-error">
            host_xrt must be an absolute path
          </div>
        )}
        {knobs?.host_xrt?.doc && <div className="eng-help">{knobs.host_xrt.doc}</div>}
      </div>

      {xdnaPath === 'per-op' && (
        <div className="xdna-requant-hint" data-testid="xdna-requant-hint" role="note">
          <div className="hint">
            This model runs per-op (slower) on the NPU. Requantizing the ssm_out tensor to
            Q5_K lets it take the fused path:
          </div>
          <div className="xdna-requant-cmd">
            <code className="mono">{REQUANT_CMD}</code>
            <button
              type="button"
              className="btn ghost sm"
              data-testid="xdna-requant-copy"
              onClick={() => copyToClipboard(REQUANT_CMD)}
            >
              Copy
            </button>
          </div>
        </div>
      )}
    </div>
  )
}
