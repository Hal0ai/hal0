// The NPU Models sub-tab (Slots → NPU → Models): the closed FLM catalogue (download, "Use for
// chat" stages into the Lanes draft) and the GGUF models with their ggml-xdna NPU fit, with
// "New slot" opening the GGML create drawer. A per-op row carries one chip naming what keeps it
// off the fused path; when a requantize fixes it, a "copy fix" chip copies the command. Every
// readout is a real field or `—`.
import { useMemo, useState } from 'react'
import { useFlmCatalog } from '@/api/hooks/useFlmCatalog'
import { useModels, usePullJob } from '@/api/hooks/useModels'
import { useSlotConfig } from '@/api/hooks/useSlots'
import { useXdnaFit } from '@/api/hooks/useXdnaFit'
import { npuAnchorSlot } from '../slots/slot-shared.js'
import { slotEditHash } from '../slots-route'
import { GgmlSlotDrawer } from './GgmlSlotDrawer.jsx'
import { GGML_IMAGE_MISSING } from './ggml-slot-pure'
import { XdnaFitChips } from './XdnaFitChips.jsx'
import { bundledFlmModel, pickChatModel, savedFlm } from './flm-draft-pure'
import { flmCatalogRows, ggufNpuRows } from './npu-models-pure'

const toast = (msg, kind = 'info') => typeof window !== 'undefined' && window.__hal0Toast && window.__hal0Toast(msg, kind)

const CAP_FILTERS = ['all', 'chat', 'stt', 'embed']

export function NpuModelsPanel({ slots = [], occ, setFlmDraft, onSwitchTab }) {
  const anchor = npuAnchorSlot(slots)
  const cfgQ = useSlotConfig(anchor?.name)
  const catQ = useFlmCatalog()
  const fitQ = useXdnaFit()
  const modelsQ = useModels()
  const pull = usePullJob()
  const [filter, setFilter] = useState({ installed: 'all', cap: 'all' })
  const [drawer, setDrawer] = useState({ open: false, mode: 'create', model: undefined, slot: undefined })
  const imageMissing = occ?.npu_engines?.xdna?.pulled === false

  const catalog = catQ.data
  const saved = useMemo(() => savedFlm(cfgQ.data, anchor), [cfgQ.data, anchor])

  const usage = useMemo(() => {
    const u = {}
    const stt = bundledFlmModel(catalog, ['stt'])
    const embed = bundledFlmModel(catalog, ['embed'])
    if (stt) u[stt] = 'flm stt'
    if (embed) u[embed] = 'flm embed'
    if (saved.chatModel) u[saved.chatModel] = 'flm chat'
    return u
  }, [catalog, saved.chatModel])

  const flmRows = useMemo(() => flmCatalogRows(catalog?.models, filter, usage), [catalog, filter, usage])
  const ggufRows = useMemo(() => ggufNpuRows(modelsQ.data, fitQ.data, slots), [modelsQ.data, fitQ.data, slots])

  const useForChat = (id) => {
    setFlmDraft((d) => pickChatModel(d, saved, id))
    onSwitchTab('lanes')
    toast(`${id} staged as the FLM chat model — apply it on Lanes`, 'info')
  }
  const download = (id) => Promise.resolve(pull.start(id)).catch((e) => toast(e?.message || `Could not start the download of ${id}`, 'warn'))

  return (
    <div className="npt-models" data-testid="npu-models">
      <section className="npt-msec npt-msec-flm" data-testid="flm-models">
        <div className="npt-lane-ttl"><span className="npt-eng flm">FLM</span><span className="npt-sub">FastFlowLM catalogue</span></div>
        <div className="npt-mbar">
          <button type="button" className={`btn sm${filter.installed === 'installed' ? ' on' : ''}`} data-testid="flm-filter-installed" aria-pressed={filter.installed === 'installed'} onClick={() => setFilter((f) => ({ ...f, installed: f.installed === 'installed' ? 'all' : 'installed' }))}>
            Installed only
          </button>
          {CAP_FILTERS.map((c) => (
            <button key={c} type="button" className={`btn sm${filter.cap === c ? ' on' : ''}`} data-testid={`flm-filter-${c}`} aria-pressed={filter.cap === c} onClick={() => setFilter((f) => ({ ...f, cap: c }))}>
              {c === 'all' ? 'All' : c}
            </button>
          ))}
        </div>
        {flmRows.length === 0 && <p className="npt-hint">{catalog ? 'No FLM models match.' : '—'}</p>}
        {flmRows.map((r) => {
          const pulling = pull.inFlight || pull.state === 'running' || pull.state === 'queued'
          const downloading = pull.modelId === r.model && pulling
          const isChat = r.capabilities.includes('chat')
          const current = (saved.chatModel ?? null) === r.model
          return (
            <div key={r.model} className="npt-mrow" data-testid={`flm-row-${r.model}`}>
              <span className="npt-mname mono">{r.model}</span>
              {r.family && <span className="chip mono">{r.family}</span>}
              {r.capabilities.map((c) => <span key={c} className="chip mono">{c}</span>)}
              <span className="chip mono">{r.installed ? 'installed' : 'not installed'}</span>
              {r.usedBy && <span className="chip mono npt-used">{r.usedBy}</span>}
              {!r.installed && (
                <button type="button" className="btn sm" data-testid={`flm-download-${r.model}`} disabled={pulling} onClick={() => download(r.model)}>
                  {downloading ? `Downloading${pull.pct != null ? ` ${Math.round(pull.pct)}%` : '…'}` : 'Download'}
                </button>
              )}
              {isChat && !current && (
                <button type="button" className="btn primary sm" data-testid={`flm-use-${r.model}`} disabled={!anchor} title={anchor ? undefined : 'No FLM slot yet'} onClick={() => useForChat(r.model)}>
                  Use for chat
                </button>
              )}
            </div>
          )
        })}
        {pull.error && <div className="npt-error-inline" role="alert">{pull.error.message}</div>}
      </section>

      <section className="npt-msec npt-msec-ggml" data-testid="gguf-models">
        <div className="npt-lane-ttl"><span className="npt-eng ggml">GGML</span><span className="npt-sub" title="fused: the whole SSM block runs as one NPU kernel (fast). per-op: each operator dispatches separately (slower).">GGUF models on the NPU</span></div>
        {ggufRows.length === 0 && <p className="npt-hint">{fitQ.data ? 'No GGUF chat models in the registry.' : '—'}</p>}
        {ggufRows.map((r) => (
          <div key={r.id} className="npt-mrow" data-testid={`gguf-row-${r.id}`}>
            <span className="npt-mname mono">{r.id}</span>
            <span className="chip mono" data-testid={`gguf-arch-${r.id}`}>{r.arch ?? '—'}</span>
            <span className="chip mono">{r.size ?? '—'}</span>
            <XdnaFitChips row={{ xdna_path: r.fit, reason: r.reason }} testid={`gguf-${r.id}`} />
            {r.usedBy.map((n) => (
              <a
                key={n}
                className="chip mono npt-used"
                data-testid={`gguf-used-${r.id}-${n}`}
                href={slotEditHash(n)}
                onClick={(e) => {
                  // An xdna slot edits in the page, like the GGML lane; leaving the tab would drop
                  // the staged FLM changes.
                  const slot = (slots ?? []).find((x) => x.name === n)
                  if (!slot) return
                  e.preventDefault()
                  setDrawer({ open: true, mode: 'edit', model: undefined, slot })
                }}
              >
                {n}
              </a>
            ))}
            <button type="button" className="btn sm" data-testid={`gguf-new-${r.id}`} disabled={imageMissing} title={imageMissing ? GGML_IMAGE_MISSING : undefined} onClick={() => setDrawer({ open: true, mode: 'create', model: r.id, slot: undefined })}>
              New slot
            </button>
          </div>
        ))}
      </section>

      <GgmlSlotDrawer open={drawer.open} mode={drawer.mode} slot={drawer.slot} initialModel={drawer.model} onClose={() => setDrawer((d) => ({ ...d, open: false }))} />
    </div>
  )
}
