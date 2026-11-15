// The NPU Models sub-tab (Slots → NPU → Models): the GGUF models with their ggml-xdna NPU fit,
// with "New slot" opening the GGML create drawer. A per-op row carries one chip naming what keeps
// it off the fused path; when a requantize fixes it, a "copy fix" chip copies the command. Every
// readout is a real field or `—`. FLM models are chosen, downloaded and launched from the model
// dropdown on the FLM lane (Lanes tab), not listed here.
import { useMemo, useState } from 'react'
import { useModels } from '@/api/hooks/useModels'
import { useXdnaFit } from '@/api/hooks/useXdnaFit'
import { slotEditHash } from '../slots-route'
import { GgmlSlotDrawer } from './GgmlSlotDrawer.jsx'
import { GGML_IMAGE_MISSING } from './ggml-slot-pure'
import { XdnaFitChips } from './XdnaFitChips.jsx'
import { ggufNpuRows } from './npu-models-pure'

export function NpuModelsPanel({ slots = [], occ }) {
  const fitQ = useXdnaFit()
  const modelsQ = useModels()
  const [drawer, setDrawer] = useState({ open: false, mode: 'create', model: undefined, slot: undefined })
  const imageMissing = occ?.npu_engines?.xdna?.pulled === false

  const ggufRows = useMemo(() => ggufNpuRows(modelsQ.data, fitQ.data, slots), [modelsQ.data, fitQ.data, slots])

  return (
    <div className="npt-models" data-testid="npu-models">
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
