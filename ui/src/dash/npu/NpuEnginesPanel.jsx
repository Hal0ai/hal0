// The NPU Engines & profiles sub-tab (Slots → NPU → Engines): what hal0 can and cannot read about
// the NPU host, the two engine images (FLM, ggml-xdna), and the NPU profiles with used-by and
// Clone (the real profile drawer from profiles.jsx). Every readout is a real field or `—`.
import { useMemo, useRef, useState } from 'react'
import { copyOrSelect } from '@/lib/clipboard'
import { useProfiles } from '@/api/hooks/useProfiles'
import { useFlmCatalog } from '@/api/hooks/useFlmCatalog'
import { ProfileDrawer } from '../profiles.jsx'
import { slotEditHash } from '../slots-route'
import { npuProfiles, readinessRows } from './npu-engines-pure'

const toast = (msg, kind = 'info') => typeof window !== 'undefined' && window.__hal0Toast && window.__hal0Toast(msg, kind)

const MARK = { ok: 'ok', warn: 'warn', unknown: 'unknown' }

function CopyCmd({ cmd, testid }) {
  const ref = useRef(null)
  return (
    <span className="npt-cmd">
      <code ref={ref} className="mono">{cmd}</code>
      <button type="button" className="btn ghost sm" data-testid={testid} onClick={() => copyOrSelect(cmd, ref.current)}>Copy</button>
    </span>
  )
}

export function NpuEnginesPanel({ occ, hw, slots = [] }) {
  const profQ = useProfiles()
  const catQ = useFlmCatalog()
  const [drawer, setDrawer] = useState(null)
  const rows = useMemo(() => readinessRows(occ, hw), [occ, hw])
  const profiles = useMemo(() => npuProfiles(profQ.data, slots), [profQ.data, slots])
  const eng = occ?.npu_engines
  const flm = eng?.flm
  const xdna = eng?.xdna

  const refreshCatalog = () =>
    Promise.resolve(catQ.refetch())
      .then(() => toast('FLM catalogue refreshed', 'info'))
      .catch((e) => toast(e?.message || 'Could not refresh the FLM catalogue', 'warn'))

  return (
    <div className="npt-engines" data-testid="npu-engines">
      <section className="npt-msec" data-testid="npu-readiness">
        <div className="npt-lane-ttl"><span className="npt-sub">Host readiness</span></div>
        <p className="npt-hint">hal0 runs in a container and can read only part of the NPU host. Rows marked unknown need checking on the host.</p>
        {rows.map((r) => (
          <div key={r.id} className="npt-mrow" data-testid={`ready-${r.id}`} data-state={r.state}>
            <span className={`chip mono npt-ready ${MARK[r.state]}`}>{r.state}</span>
            <span className="npt-mname">{r.label}</span>
            <span className="npt-hint">{r.detail}</span>
            {r.command && <CopyCmd cmd={r.command} testid="ready-pmode-copy" />}
          </div>
        ))}
      </section>

      <section className="npt-msec npt-msec-flm" data-testid="npu-engine-flm">
        <div className="npt-lane-ttl"><span className="npt-eng flm">FLM</span><span className="npt-sub">FastFlowLM engine</span></div>
        <div className="npt-mrow"><span className="npt-mname">Image</span><span className="mono" data-testid="engine-flm-image">{flm?.image ?? '—'}</span></div>
        <div className="npt-mrow"><span className="npt-mname">Version</span><span className="mono" data-testid="engine-flm-version">{flm?.version ?? '—'}</span></div>
        <div className="npt-mrow">
          <button type="button" className="btn sm" data-testid="engine-flm-refresh" disabled={catQ.isFetching} onClick={refreshCatalog}>Refresh catalogue</button>
        </div>
      </section>

      <section className="npt-msec npt-msec-ggml" data-testid="npu-engine-xdna">
        <div className="npt-lane-ttl"><span className="npt-eng ggml">GGML</span><span className="npt-sub">ggml-xdna engine</span></div>
        <div className="npt-mrow"><span className="npt-mname">Image</span><span className="mono" data-testid="engine-xdna-image">{xdna?.image ?? '—'}</span></div>
        <div className="npt-mrow"><span className="npt-mname">Tag</span><span className="mono" data-testid="engine-xdna-tag">{xdna?.tag ?? '—'}</span></div>
        <div className="npt-mrow">
          <span className="npt-mname">Pulled</span>
          <span className="mono" data-testid="engine-xdna-pulled">{xdna?.pulled == null ? 'unknown' : xdna.pulled ? 'yes' : 'no'}</span>
        </div>
        <div className="npt-mrow">
          <span className="npt-mname">Locked</span>
          <span className="chip mono" data-testid="engine-xdna-locked" title="Fixed by hal0; never editable or sent from the UI">GGML_XDNA_PGEMM_GLU_A=0</span>
        </div>
      </section>

      <section className="npt-msec" data-testid="npu-profiles">
        <div className="npt-lane-ttl"><span className="npt-sub">NPU profiles</span><a className="npt-hint" href="#profiles">All profiles</a></div>
        {profQ.isLoading && <p className="npt-hint">—</p>}
        {!profQ.isLoading && profiles.length === 0 && <p className="npt-hint">No NPU profiles.</p>}
        {profiles.map((p) => (
          <div key={p.name} className="npt-mrow" data-testid={`npu-profile-${p.name}`}>
            <span className={`npt-eng ${p.engine === 'flm' ? 'flm' : 'ggml'}`}>{p.engine === 'flm' ? 'FLM' : 'GGML'}</span>
            <span className="npt-mname mono">{p.name}{p.seed && <span className="chip mono" style={{ marginLeft: 6 }}>seed</span>}</span>
            <span className="npt-hint mono">{p.body}</span>
            <span className="npt-hint" data-testid={`npu-profile-${p.name}-usedby`}>
              {p.usedBy.length === 0 ? 'unused' : p.usedBy.map((n, i) => (
                <span key={n}>{i > 0 && ', '}<a href={slotEditHash(n)}>{n}</a></span>
              ))}
            </span>
            <button type="button" className="btn ghost sm" data-testid={`npu-profile-clone-${p.name}`} onClick={() => setDrawer({ mode: 'clone', source: (profQ.data || []).find((x) => x.name === p.name) })}>Clone</button>
          </div>
        ))}
      </section>

      {drawer?.source && (
        <ProfileDrawer mode={drawer.mode} source={drawer.source} existing={profQ.data || []} onClose={() => setDrawer(null)} onSaved={() => setDrawer(null)} />
      )}
    </div>
  )
}
