// FLM model dropdown for the NPU lane: the same RichSelect the inference slot cards use, with
// an installed / download status icon, a size chip and an audio chip per model. Installed models
// come first under an "Installed" header, the rest under "Download on pick": choosing one of those
// downloads it and applies (FlmLane does the staging). The headers are disabled, non-selectable
// options, so RichSelect itself is unchanged.
import React from 'react'
import { RichSelect } from '../rich-select.jsx'
import { flmPickerOptions, formatFlmSize } from './npu-models-pure'

const svgProps = {
  width: 12,
  height: 12,
  viewBox: '0 0 16 16',
  fill: 'none',
  stroke: 'currentColor',
  strokeWidth: 2,
  strokeLinecap: 'round',
  strokeLinejoin: 'round',
  'aria-hidden': 'true',
  focusable: 'false',
}

function StatusIcon({ installed }) {
  const title = installed ? 'installed' : 'not installed, downloads on pick'
  return (
    <span className={`npt-flm-st ${installed ? 'ok' : 'dl'}`} title={title} aria-label={title} role="img">
      <svg {...svgProps}>{installed ? <path d="M3 8l3 3 7-7" /> : <path d="M8 2v8M4 7l4 4 4-4M3 14h10" />}</svg>
    </span>
  )
}

const AudioChip = () => (
  <span className="chip info" title="also takes audio input">audio</span>
)

function chips(o) {
  const size = formatFlmSize(o.sizeBytes)
  return { size: size ? <span className="chip amber">{size}</span> : null }
}

/** RichSelect options (headers included) for one modality. Exported for tests. */
export function flmRichOptions(models, cap, current) {
  const groups = flmPickerOptions(models, cap, current)
  const out = []
  const header = (id, label) => out.push({ id, disabled: true, row: <span className="npt-flm-grp">{label}</span> })
  const add = (o) => {
    const { size } = chips(o)
    const name = <span className="npt-flm-name">{o.id}</span>
    out.push({
      id: o.id,
      row: (
        <span className="npt-flm-row">
          <StatusIcon installed={o.installed} />
          {name}
          {o.audio && <AudioChip />}
        </span>
      ),
      trigger: (
        <span className="npt-flm-row">
          <StatusIcon installed={o.installed} />
          {name}
          {o.audio && <AudioChip />}
          {size}
        </span>
      ),
      right: size,
      desc: o.family || !o.installed ? (
        <span className="rsel-desc-line">
          {o.family && <span>{o.family}</span>}
          {!o.installed && <span>downloads when applied</span>}
        </span>
      ) : null,
    })
  }
  if (groups.installed.length) {
    header('__hdr-installed', 'Installed')
    groups.installed.forEach(add)
  }
  if (groups.download.length) {
    header('__hdr-download', 'Download on pick')
    groups.download.forEach(add)
  }
  return out
}

export function FlmModelPicker({ models, cap = 'chat', value, onChange, disabled = false, testid, ariaLabel }) {
  const options = React.useMemo(() => flmRichOptions(models, cap, value), [models, cap, value])
  return (
    <span className="npt-flm-picker" onClick={(e) => e.stopPropagation()}>
      <RichSelect
        className="model-picker"
        data-testid={testid}
        aria-label={ariaLabel}
        value={value || null}
        options={options}
        disabled={disabled}
        onChange={(id) => id && onChange(id)}
      />
    </span>
  )
}
