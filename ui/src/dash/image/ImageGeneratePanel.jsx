// The Image Gen pane's Generate tab: prompt, model, size, options, the fit panel, the Generate
// button with its cost line, a run's phase strip with Cancel, the errors and the results.
//
// Data: `useComfyuiModels` (the installed models) and `useComfyuiFiles` (the user's own
// checkpoints), `useCapabilities` (the img selection, the default model), the img slot's
// `[image]` defaults, `useModelFit` (the verdict per size), `useArbiter` / `useComfyui` / the
// memory budget (the cost line, the phase strip, which cancel to make). Verdicts are the
// server's: nothing here judges a fit. The pure parts (form, request, number rules, strip) are
// generate-pure.ts; the request and the results are generate-store.ts, which outlive this panel
// (a render takes minutes, and leaving the tab does not cancel it).
//
// Mounted by ImageGenTabs, which keeps it mounted while another tab shows.
import { useCallback, useEffect, useId, useMemo, useReducer, useRef, useState } from 'react'
import './image.css'
import './image-generate.css'
import {
  useArbiter,
  useComfyui,
  useComfyuiFiles,
  useComfyuiModels,
  useModelFit,
} from '@/api/hooks/useComfyui'
import { useCapabilities } from '@/api/hooks/useCapabilities'
import { useSlotConfig, useSlots } from '@/api/hooks/useSlots'
import { useMemoryBudget } from '@/api/hooks/useStatsHardware'
import { RichSelect } from '@/dash/rich-select.jsx'
import { FitMeter } from './FitMeter'
import { GenerateOptions } from './GenerateOptions'
import { GenerateResults } from './GenerateResults'
import {
  clampCfg,
  clampN,
  clampSeed,
  clampSteps,
  generateChoices,
  initialForm,
  phaseStrip,
  pickChoice,
  rollSeed,
  snapDim,
  stripArbiter,
  toRequest,
} from './generate-pure'
import { cancelRun, dismissError, startRun, useGenerateState } from './generate-store'
import { parseSize } from './image-models-pure'
import { clockLabel, renderPct } from './status-line-parts-pure'
import { costLine } from './status-line-pure'

const note = (msg, kind) => window.__hal0Toast && window.__hal0Toast(msg, kind)

// The last row of the model select: what the select does not list, and where it runs.
const FOOTER_ID = '__editor-footer'
const FOOTER = 'Video, edit and upscale run in the ComfyUI editor'
const SEED_MISSING = 'Enter a seed or switch to Random.'

const EMPTY = Object.freeze({})
// Re-render once a second while a run is on screen, for its elapsed time.
function useTick(active) {
  const [, bump] = useReducer((n) => n + 1, 0)
  useEffect(() => {
    if (!active) return undefined
    const id = setInterval(bump, 1000)
    return () => clearInterval(id)
  }, [active])
}

function RunStrip({ steps, pct, elapsedS, onCancel }) {
  return (
    <div className="gen-run" data-testid="gen-run">
      {/* Only the steps are announced. The elapsed time below ticks every second and the bar
          moves with every poll: neither belongs in a live region. */}
      <ol className="gen-steps" aria-live="polite" data-testid="gen-steps">
        {steps.map((s, i) => (
          // A fixed list of six: the label changes with the step count, the place does not.
          <li key={i} className={`gen-step ${s.state}`} data-testid="gen-step" data-state={s.state}>
            {s.label}
          </li>
        ))}
      </ol>
      <div className="gen-run-bar">
        <div
          className="gen-bar"
          role="progressbar"
          aria-label="Render progress"
          aria-valuemin={0}
          aria-valuemax={100}
          aria-valuenow={pct ?? undefined}
          data-indeterminate={pct == null ? 'true' : 'false'}
          data-testid="gen-progress"
        >
          <i style={pct == null ? undefined : { width: `${pct}%` }} />
        </div>
        <span className="gen-elapsed mono" data-testid="gen-elapsed">
          {clockLabel(elapsedS)}
        </span>
        <button type="button" className="btn ghost sm" data-testid="gen-cancel" onClick={onCancel}>
          Cancel
        </button>
      </div>
    </div>
  )
}

/**
 * @param {object} props
 * @param {string} [props.initialModelId]   a model the Models tab's Generate action preselects
 * @param {number} [props.pickSeq]
 *   bumped on every such Generate click: the same model asked for again re-applies, even after the
 *   viewer has picked another one here
 * @param {(modelId?: string) => void} [props.onOpenModels]
 *   switch to the Models tab: from the empty state, and from the 409 "not installed" link
 */
export function ImageGeneratePanel({ initialModelId, pickSeq, onOpenModels }) {
  const id = useId()
  const modelsQuery = useComfyuiModels()
  const filesQuery = useComfyuiFiles()
  const caps = useCapabilities().data
  const slots = useSlots().data
  const memory = useMemoryBudget().data
  const gen = useGenerateState()
  const { run, results, error } = gen
  const running = run != null

  const arbiter = useArbiter().data?.arbiter ?? null
  // The pane is mounted, so /status polls at its active cadence, faster while a run is in flight.
  const status = useComfyui({ active: true, busy: running }).data
  useTick(running)

  // ── the model ──────────────────────────────────────────────────────────────

  const catalog = modelsQuery.data
  const engineAvailable = catalog?.engine?.available !== false
  const sel = caps?.selections?.img?.img
  const defaultId = sel?.enabled && sel?.model ? sel.model : null
  const choices = useMemo(
    () => generateChoices(catalog?.models, filesQuery.data?.files, defaultId),
    [catalog?.models, filesQuery.data?.files, defaultId],
  )
  const [picked, setPicked] = useState(null)
  // The Models tab's Generate action: a request to open on a model. It applies whenever the model
  // or the sequence number changes, so asking again for the model already asked for (after the
  // viewer picked another here) is a new request too.
  useEffect(() => {
    if (initialModelId) setPicked(initialModelId)
  }, [initialModelId, pickSeq])
  const modelId = pickChoice(choices, picked, initialModelId, defaultId)
  const choice = choices.find((c) => c.id === modelId) ?? null
  const isCheckpoint = choice?.kind === 'checkpoint'

  // The img slot's `[image]` defaults: the server applies them to a request that has none.
  const imgSlot = (slots ?? []).find((s) => s.name === 'img' || s.type === 'image' || s.group === 'img')?.name ?? null
  const slotDefaults = useSlotConfig(imgSlot).data?.image
  const base = useMemo(() => (choice ? initialForm(choice, slotDefaults) : null), [choice, slotDefaults])

  // ── what the viewer has typed ──────────────────────────────────────────────

  const [prompt, setPrompt] = useState('')
  const [negative, setNegative] = useState('')
  const [moreOpen, setMoreOpen] = useState(false)
  const [nText, setNText] = useState('1')
  const [seedLocked, setSeedLocked] = useState(false)
  const [seedText, setSeedText] = useState('')
  // What the viewer changed of what the model supplies (size, steps, cfg). It belongs to one
  // model: another model's own values replace it, so nothing is reset by hand.
  const [edits, setEdits] = useState(EMPTY)
  const mine = edits.model === modelId ? edits : EMPTY
  const edit = (patch) =>
    setEdits((prev) => ({ ...(prev.model === modelId ? prev : EMPTY), ...patch, model: modelId }))

  const size = mine.size ?? base?.size ?? ''
  const stepsText = mine.steps ?? (base?.steps != null ? String(base.steps) : '')
  const cfgText = mine.cfg ?? (base?.cfg != null ? String(base.cfg) : '')
  const sizes = choice?.sizes ?? []
  const customActive = mine.custom === true || (size !== '' && !sizes.includes(size))
  const parsedSize = parseSize(size)

  const seedValue = seedText === '' ? null : clampSeed(Number(seedText))
  const form = base && {
    ...base,
    prompt,
    negative_prompt: negative,
    size,
    steps: stepsText === '' ? null : Number(stepsText),
    cfg: cfgText === '' ? null : Number(cfgText),
    seed: seedLocked ? seedValue : null,
    n: Number(nText),
  }

  // ── fit, cost, and whether Generate can be pressed ─────────────────────────

  // Not asked for a user checkpoint: the route 404s for it (the hook skips it too).
  const fitQuery = useModelFit(modelId, size || null)
  const fit = isCheckpoint ? null : (fitQuery.data?.fit ?? null)
  // The verdict for this model and size is not in yet: nothing at all to show (the first look), or
  // the last model's or size's verdict is on screen as a placeholder (`keepPreviousData`). Either
  // way it is not this render's verdict: Generate waits for it, and the placeholder decides
  // nothing. A user checkpoint has no /fit and is never waited for: the /v1 preflight judges it.
  const fitFirstLook = !isCheckpoint && fitQuery.isLoading && !fitQuery.data
  const fitStale = !isCheckpoint && fitQuery.isPlaceholderData === true
  const fitPending = fitFirstLook || fitStale
  const cost = costLine(arbiter?.unload_set, memory, arbiter)
  const grace = arbiter?.phase === 'grace'

  // Why it cannot run: the server's words for the host and the fit, ours for a locked seed that
  // has been cleared (it would otherwise fall through to a random one, against what the viewer
  // asked for). An empty prompt only disables the button.
  const blocked = !engineAvailable
    ? catalog?.engine?.reason || 'Image generation is unavailable on this host.'
    : !fitStale && fit?.verdict === 'wont_fit'
      ? fit.reason || "Won't fit"
      : seedLocked && seedValue == null
        ? SEED_MISSING
        : null
  const canRun = !running && !blocked && !fitPending && form != null && prompt.trim() !== ''

  const submit = () => {
    if (!canRun) return
    // "Random" draws here, not on the server: this seed reaches the browser exactly, so the
    // result's Reuse seed can repeat the image (see `rollSeed`).
    void startRun(toRequest({ ...form, seed: form.seed ?? rollSeed() }), { graceAtStart: grace })
  }

  // ── a run: cancel ──────────────────────────────────────────────────────────

  // The store owns the run's Cancel and reports the run to the dock, so leaving this tab does not
  // end either. The panel's own button calls the same function.
  const cancel = cancelRun

  // ── loading, failing, nothing installed ────────────────────────────────────

  if (!catalog) {
    if (modelsQuery.isError) {
      return (
        <div className="gen">
          <div className="gen-error" role="alert" data-testid="gen-load-error">
            <span>{modelsQuery.error?.message || 'The API did not answer.'}</span>
            <button type="button" className="btn ghost sm" onClick={() => modelsQuery.refetch?.()}>
              Retry
            </button>
          </div>
        </div>
      )
    }
    return (
      <div className="gen-loading" data-testid="gen-loading">
        Loading models…
      </div>
    )
  }
  // The checkpoints come from a scan that can take a moment: do not call it empty before it ends.
  if (choices.length === 0 && !filesQuery.isLoading) {
    return (
      <div className="gen" data-testid="gen-panel">
        <div className="gen-empty" data-testid="gen-empty">
          <h2>No image model installed yet</h2>
          <p>
            {engineAvailable
              ? 'Install one on the Models tab, then come back here to generate with it.'
              : `Image generation is unavailable on this host${catalog.engine?.reason ? `: ${catalog.engine.reason}` : '.'}`}
          </p>
          <button type="button" className="btn sm" data-testid="gen-empty-models" onClick={() => onOpenModels?.()}>
            Open Models
          </button>
        </div>
      </div>
    )
  }
  if (!choice || !form) {
    return (
      <div className="gen-loading" data-testid="gen-loading">
        Loading models…
      </div>
    )
  }

  // ── the form ───────────────────────────────────────────────────────────────

  const options = [
    ...choices.map((c) => ({
      id: c.id,
      row: <span>{c.label}</span>,
      right: c.id === defaultId ? <span className="chip ok">Default</span> : null,
      desc: c.detail,
    })),
    { id: FOOTER_ID, disabled: true, row: <span className="gen-footer-row">{FOOTER}</span> },
  ]

  const pickSize = (s) => edit({ size: s, custom: false, customW: undefined, customH: undefined })
  // The custom fields hold the text typed until a field is left, then become the snapped size:
  // the fit is asked once for the size that will be rendered, not for every keystroke.
  const customW = mine.customW ?? String(parsedSize?.width ?? '')
  const customH = mine.customH ?? String(parsedSize?.height ?? '')
  const commitCustom = () => {
    if (customW === '' && customH === '') return
    const w = snapDim(Number(customW))
    const h = snapDim(Number(customH))
    edit({ size: `${w}x${h}`, custom: true, customW: undefined, customH: undefined })
  }

  const seedOn = {
    onSeed: setSeedText,
    onSeedBlur: () => {
      if (seedText === '') return
      const n = clampSeed(Number(seedText))
      setSeedText(n == null ? '' : String(n))
    },
    onSeedMode: (locked) => {
      setSeedLocked(locked)
      if (locked && seedText === '') setSeedText(String(rollSeed()))
    },
    onRoll: () => {
      setSeedLocked(true)
      setSeedText(String(rollSeed()))
    },
  }
  const reuseSeed = (seed) => {
    setSeedLocked(true)
    setSeedText(String(seed))
    setMoreOpen(true)
  }

  const strip = phaseStrip(
    stripArbiter(arbiter, { graceAtStart: run?.graceAtStart === true, elapsedMs: run ? Date.now() - run.startedAt : 0 }),
    status?.progress ?? null,
    running ? 'running' : 'idle',
  )
  const elapsedS = run ? Math.max(0, (Date.now() - run.startedAt) / 1000) : 0

  return (
    <div className="gen" data-testid="gen-panel">
      <div className="gen-form">
        <div className="gen-field">
          <label className="gen-label" htmlFor={`${id}-prompt`}>
            Prompt
          </label>
          <textarea
            id={`${id}-prompt`}
            className="input gen-prompt"
            data-testid="gen-prompt"
            rows={4}
            placeholder="Describe the image"
            value={prompt}
            onChange={(e) => setPrompt(e.target.value)}
            onKeyDown={(e) => {
              // Ctrl/Cmd+Enter generates; a plain Enter is a new line.
              if (e.key === 'Enter' && (e.ctrlKey || e.metaKey)) {
                e.preventDefault()
                submit()
              }
            }}
          />
        </div>

        <div className="gen-field">
          <span className="gen-label">Model</span>
          <RichSelect
            value={modelId}
            options={options}
            onChange={(next) => {
              if (next === FOOTER_ID) return
              setPicked(next)
              // An error about the last model says nothing of this one.
              dismissError()
            }}
            aria-label="Model"
            data-testid="gen-model"
          />
        </div>

        <div className="gen-field">
          <span className="gen-label">Size</span>
          <div className="gen-sizes" role="group" aria-label="Size">
            {sizes.map((s) => (
              <button
                key={s}
                type="button"
                className="gen-chip"
                data-testid={`gen-size-${s}`}
                aria-pressed={!customActive && size === s}
                onClick={() => pickSize(s)}
              >
                {s}
              </button>
            ))}
            <button
              type="button"
              className="gen-chip"
              data-testid="gen-size-custom"
              aria-pressed={customActive}
              onClick={() => edit({ custom: true })}
            >
              Custom
            </button>
            {customActive && (
              <span className="gen-custom">
                <input
                  className="input mono"
                  data-testid="gen-custom-w"
                  type="number"
                  aria-label="Width"
                  min={64}
                  max={2048}
                  step={8}
                  value={customW}
                  onChange={(e) => edit({ customW: e.target.value })}
                  onBlur={commitCustom}
                />
                ×
                <input
                  className="input mono"
                  data-testid="gen-custom-h"
                  type="number"
                  aria-label="Height"
                  min={64}
                  max={2048}
                  step={8}
                  value={customH}
                  onChange={(e) => edit({ customH: e.target.value })}
                  onBlur={commitCustom}
                />
              </span>
            )}
          </div>
        </div>

        <button
          type="button"
          className="gen-more"
          data-testid="gen-more"
          aria-expanded={moreOpen}
          onClick={() => setMoreOpen(!moreOpen)}
        >
          {moreOpen ? 'Fewer options' : 'More options'}
        </button>
        {moreOpen && (
          <GenerateOptions
            values={{
              negative,
              steps: stepsText,
              cfg: cfgText,
              n: nText,
              seed: seedLocked ? seedText : '',
              seedLocked,
            }}
            on={{
              onNegative: setNegative,
              onSteps: (v) => edit({ steps: v }),
              onStepsBlur: () => {
                const v = stepsText === '' ? null : clampSteps(Number(stepsText))
                edit({ steps: v == null ? '' : String(v) })
              },
              onCfg: (v) => edit({ cfg: v }),
              onCfgBlur: () => {
                const v = cfgText === '' ? null : clampCfg(Number(cfgText))
                edit({ cfg: v == null ? '' : String(v) })
              },
              onN: setNText,
              onNBlur: () => setNText(String(clampN(Number(nText)))),
              ...seedOn,
            }}
          />
        )}

        <div className="gen-fit" data-testid="gen-fit" data-stale={fitStale ? 'true' : undefined}>
          {isCheckpoint ? (
            <div className="gen-fit-note" data-testid="gen-fit-skipped">
              Fit is checked when you generate.
            </div>
          ) : fitFirstLook && engineAvailable ? (
            <div className="gen-fit-note" data-testid="gen-fit-loading">
              Checking fit…
            </div>
          ) : (
            <>
              {/* The last verdict stays up, dimmed, while the next is read: no flash. */}
              <FitMeter
                fit={fit}
                engineAvailable={engineAvailable}
                size={fitQuery.data?.size ?? undefined}
                requestedSize={size}
                perImage={form.n > 1}
                id="gen"
              />
              {fitStale && engineAvailable && (
                <div className="gen-fit-note" data-testid="gen-fit-loading">
                  Checking fit…
                </div>
              )}
            </>
          )}
        </div>

        <div className="gen-go">
          {running ? (
            <RunStrip steps={strip} pct={renderPct(status?.progress)} elapsedS={elapsedS} onCancel={cancel} />
          ) : (
            <div className="gen-go-row">
              <button
                type="button"
                className="btn lg"
                data-testid="gen-submit"
                disabled={!canRun}
                onClick={submit}
              >
                {grace ? 'Generate again' : 'Generate'}
              </button>
              {blocked && (
                <span className="gen-blocked" data-testid="gen-blocked" role="status">
                  {blocked}
                </span>
              )}
            </div>
          )}
          {/* What the next image costs is a question for before it starts: mid-run the box is
              already in image mode and the line would say so as if it were news. */}
          {!running && cost.length > 0 && (
            <div className="gen-cost" data-testid="gen-cost">
              {cost.join(' ')}
            </div>
          )}
        </div>

        {error && (
          <div className="gen-error" role="alert" data-testid="gen-error">
            <span>{error.message}</span>
            {error.code === 'image.model_not_installed' && (
              <button
                type="button"
                className="btn ghost sm"
                data-testid="gen-error-models"
                onClick={() =>
                  onOpenModels?.(typeof error.details.model === 'string' ? error.details.model : undefined)
                }
              >
                Open Models
              </button>
            )}
          </div>
        )}
      </div>

      <GenerateResults results={results} onReuseSeed={reuseSeed} />
    </div>
  )
}
