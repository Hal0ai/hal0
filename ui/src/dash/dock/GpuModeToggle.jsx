// The top bar's GPU mode toggle: Chat | Image, a two-segment radio group beside the Agent Chat
// launcher. It reads the shared GPU state (GpuStatusProvider) and calls the same actions the footer
// and the GPU tab do; which segment is selected and what a click calls are gpu-toggle-pure.ts's.
//
//   Image, in chat mode           hold (asks first, in the popover below the toggle)
//   Image, while image mode is on keep (asks first as well)
//   Chat, in image mode           stop (the hook asks before dropping a running render)
//
// Hidden when there is no line to read (no GPU pool, no image engine) and for the blocked,
// refused and stale kinds, which the footer owns. Phone width: icons only (the labels are
// aria-labelled, so the names stay).
import { useEffect, useRef } from 'react'
import { renderPct } from '@/dash/image/status-line-parts-pure'
import { useGpuState } from './GpuStatusContext.jsx'
import {
  PIN_POPOVER_CONFIRM,
  PIN_POPOVER_LABEL,
  gpuToggleModel,
  pinPopoverQuestion,
  toggleClickAction,
  toggleTitle,
} from './gpu-toggle-pure'

const ICON = {
  width: 13,
  height: 13,
  viewBox: '0 0 16 16',
  fill: 'none',
  stroke: 'currentColor',
  strokeWidth: 1.4,
  'aria-hidden': true,
  focusable: false,
}

const ChatIcon = () => (
  <svg {...ICON} className="tb-gpu-ico">
    <path d="M2.5 3.5h11v7h-6l-3 2.5v-2.5h-2z" />
  </svg>
)
const ImageIcon = () => (
  <svg {...ICON} className="tb-gpu-ico">
    <rect x="2.5" y="3" width="11" height="10" rx="1.5" />
    <circle cx="6" cy="6.5" r="1.2" />
    <path d="M3 12l3.5-3.5 2.5 2.5 2-2 2.5 2.5" />
  </svg>
)
const LockIcon = () => (
  <svg {...ICON} className="tb-gpu-ico" fill="currentColor" stroke="none" data-testid="tb-gpu-lock">
    <path d="M5 7V5.5a3 3 0 016 0V7h.5a1 1 0 011 1v5a1 1 0 01-1 1h-7a1 1 0 01-1-1V8a1 1 0 011-1zm1.5 0h3V5.5a1.5 1.5 0 00-3 0z" />
  </svg>
)
const Spinner = () => <span className="tb-gpu-spin" aria-hidden="true" data-testid="tb-gpu-spinner" />

export function GpuModeToggle() {
  const state = useGpuState()
  const popRef = useRef(null)
  const imageRef = useRef(null)

  const view = state?.gpu.view ?? null
  const actions = state?.actions
  const model = state ? gpuToggleModel(view, state.gpu.facts) : null

  // The question the popover asks is the hook's open confirm, when this toggle drew it.
  const confirm = actions?.confirm
  const asking =
    confirm?.open && confirm.via === 'popover' && (confirm.kind === 'hold' || confirm.kind === 'keep')
      ? confirm.kind
      : null
  const decline = confirm?.decline

  // Escape and a press outside the popover answer "no"; focus goes to the popover's button on open
  // and back to the Image segment on close.
  useEffect(() => {
    if (!asking) return undefined
    const onKey = (e) => {
      if (e.key === 'Escape') decline()
    }
    const onDown = (e) => {
      if (popRef.current && !popRef.current.contains(e.target)) decline()
    }
    document.addEventListener('keydown', onKey)
    document.addEventListener('pointerdown', onDown, true)
    popRef.current?.querySelector('[data-testid="tb-gpu-pop-confirm"]')?.focus()
    const back = imageRef.current
    return () => {
      document.removeEventListener('keydown', onKey)
      document.removeEventListener('pointerdown', onDown, true)
      if (back?.isConnected) back.focus()
    }
  }, [asking, decline])

  if (!state || !model) return null
  const { gpu } = state
  const pct = model.rendering ? renderPct(gpu.progress) : null
  const arbiter = gpu.env?.arbiter ?? null
  const unloadSet = arbiter?.unload_set ?? null

  const choose = (segment) => {
    const action = toggleClickAction(segment, view, gpu.facts)
    if (!action) return
    void actions.run(action, action === 'hold' || action === 'keep' ? { ...gpu.ctx, confirmVia: 'popover' } : gpu.ctx)
  }

  // Roving focus, as a radio group does: the selected segment is the tab stop, the arrow keys move
  // between the two. Enter and Space still choose.
  const onKeyDown = (e) => {
    if (!['ArrowLeft', 'ArrowRight', 'ArrowUp', 'ArrowDown'].includes(e.key)) return
    e.preventDefault()
    const radios = Array.from(e.currentTarget.querySelectorAll('[role="radio"]'))
    const at = radios.indexOf(document.activeElement)
    const next = radios[(at + 1) % radios.length]
    next?.focus()
  }

  const busy = actions.pending != null && !asking
  const side = model.selected
  const segment = (id, label, icon) => (
    <button
      ref={id === 'image' ? imageRef : undefined}
      type="button"
      role="radio"
      aria-checked={side === id}
      aria-label={label}
      tabIndex={side === id ? 0 : -1}
      className="tb-gpu-seg"
      data-testid={`tb-gpu-${id}`}
      disabled={busy}
      onClick={() => choose(id)}
    >
      {icon}
      <span className="tb-gpu-lbl">{label}</span>
    </button>
  )

  return (
    <div className="tb-gpu-wrap">
      <div
        className="tb-gpu"
        role="radiogroup"
        aria-label="GPU mode"
        title={toggleTitle(view, unloadSet, gpu.memory, arbiter)}
        data-testid="tb-gpu-toggle"
        data-side={side}
        data-pinned={model.pinned ? '1' : '0'}
        data-kind={view.kind}
        onKeyDown={onKeyDown}
      >
        <span className="tb-gpu-thumb" aria-hidden="true" />
        {segment('chat', 'Chat', model.reloading ? <Spinner /> : <ChatIcon />)}
        {segment('image', 'Image', model.switching ? <Spinner /> : model.pinned ? <LockIcon /> : <ImageIcon />)}
        {model.rendering && (
          <i
            className={'tb-gpu-bar' + (pct == null ? ' indet' : '')}
            data-testid="tb-gpu-progress"
            aria-hidden="true"
            style={pct == null ? undefined : { transform: `scaleX(${pct / 100})` }}
          />
        )}
      </div>
      {asking && (
        <div className="tb-gpu-pop" role="dialog" aria-label={PIN_POPOVER_LABEL} data-testid="tb-gpu-pop" ref={popRef}>
          <p>{pinPopoverQuestion(asking, unloadSet, gpu.memory)}</p>
          <div className="tb-gpu-pop-row">
            <button type="button" className="tb-gpu-pop-btn" data-testid="tb-gpu-pop-cancel" onClick={decline}>
              Cancel
            </button>
            <button
              type="button"
              className="tb-gpu-pop-btn primary"
              data-testid="tb-gpu-pop-confirm"
              onClick={confirm.accept}
            >
              {PIN_POPOVER_CONFIRM}
            </button>
          </div>
        </div>
      )}
    </div>
  )
}
