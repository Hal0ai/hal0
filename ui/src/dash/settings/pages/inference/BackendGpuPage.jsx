// INFERENCE ▸ Backend & GPU — detected hardware + the runner/backend registry
// that ML-4 (RUNNER_IMAGES) established. Unblocked by the runner-image
// registry merge; slots into the INFERENCE group per the old
// SettingsNav TODO.
//
// spec (b) INFERENCE▸Backend&GPU: "engine/backend/ROCm/image = E(SEED_PROFILES)
// + G[§7.1b/§21.6]; gfx guard = G[§21.2] MISSING; detected hw = E(/api/hardware)".
//
// Honest surface note: there is NO `/api/settings` (Hal0Config) key that
// selects the backend / engine / ROCm channel / runner image. That axis is
// resolved per-slot (`SlotConfig.device` via /api/slots) → per-model
// (`Model.preferred_runner` via /api/models) → and finally the code-owned
// RUNNER_IMAGES registry (REWORK §D resolution precedence). So this page is a
// read-only introspection + reference surface over the endpoints that DO
// exist today (/api/hardware, /api/backends, /api/meta/enums); the writable
// gfx-guard / image-pin controls stay feature-gated until §7.1b/§21.2 land.
import { useHardware } from '@/api/hooks/useHardware'
import { useBackends } from '@/api/hooks/useBackends'
import { useMetaEnums } from '@/api/hooks/useMeta'
import { SRow } from '../../shared/SRow.jsx'

function _yn(v) {
  return v ? <span style={{ color: 'var(--ok)' }}>yes</span> : <span style={{ color: 'var(--fg-4)' }}>no</span>
}

function _mb(mb) {
  const n = Number(mb)
  if (!n || n <= 0) return '—'
  if (n < 1024) return `${n} MB`
  return `${(n / 1024).toFixed(1)} GB`
}

// Colour a backend row by install state.
function _stateChip(state) {
  const s = String(state || '').toLowerCase()
  const color = s === 'installed' ? 'var(--ok)' : s === 'updating' ? 'var(--warn)' : 'var(--fg-4)'
  return (
    <span className="chip" style={{ fontFamily: 'var(--jbm)', fontSize: 10, color, borderColor: color }}>
      {state || 'unknown'}
    </span>
  )
}

export function BackendGpuPage() {
  const hw = useHardware()
  const backendsQuery = useBackends()
  const enums = useMetaEnums()

  const H = hw.data
  const backends = backendsQuery.data?.backends || []

  return (
    <div className="s-section">
      <h2>Backend &amp; GPU</h2>
      <p className="desc">
        Detected accelerators and the inference backends available to slots. Backend, runner image, and
        ROCm channel are chosen <b>per slot</b> (device) and <b>per model</b> (preferred runner), then
        resolved against the shipped runner registry — not through global settings.
      </p>

      {/* ── Detected hardware (read-only, /api/hardware) ─────────────────── */}
      <div className="s-panel">
        <div className="s-row" style={{ paddingBottom: 4, borderBottom: '1px solid var(--line)' }}>
          <div className="k"><span>Detected hardware</span><span className="sub">host accelerators · /api/hardware</span></div>
        </div>
        {hw.isPending && <div style={{ padding: 12, color: 'var(--fg-4)', fontFamily: 'var(--jbm)', fontSize: 12 }}>Probing hardware…</div>}
        {hw.isError && <div className="err">{hw.error?.message || 'Failed to read hardware'}</div>}
        {H && (
          <>
            <SRow k="GPU" sub="Discrete / integrated graphics adapter" mono v={<span style={{ color: 'var(--fg-2)' }}>{H.gpu || '—'}{H.gpuVendor ? ` · ${H.gpuVendor}` : ''}</span>} />
            <SRow k="ROCm / compute capable" sub="HIP compute stack present (drives the rocm runner)" v={_yn(H.computeCapable)} />
            <SRow k="Vulkan capable" sub="Vulkan runtime present (drives the vulkan runner)" v={_yn(H.vulkanCapable)} />
            <SRow k="Unified / GTT memory" sub="Shared GPU memory pool (APU) · GTT ceiling" mono v={<span style={{ color: 'var(--fg-3)' }}>{_mb(H.unifiedMb)} unified · {_mb(H.gttTotalMb)} GTT{H.memoryKind ? ` · ${H.memoryKind}` : ''}</span>} />
            <SRow
              k="NPU"
              sub="AMD XDNA / FastFlowLM accelerator"
              mono
              v={
                H.npu?.present
                  ? <span style={{ color: 'var(--fg-2)' }}>{H.npu.name || H.npu.vendor || 'present'}{H.npu.columns ? ` · ${H.npu.columns} cols` : ''}{H.npu.driver ? ` · ${H.npu.driver}` : ''}</span>
                  : <span style={{ color: 'var(--fg-4)' }}>not detected</span>
              }
            />
            <SRow k="CPU" sub="Host processor" mono v={<span style={{ color: 'var(--fg-3)' }}>{H.cpu || '—'}{H.cores ? ` · ${H.cores}` : ''}</span>} />
          </>
        )}
      </div>

      {/* ── Available backends / runners (read-only, /api/backends) ──────── */}
      <div className="s-panel" style={{ marginTop: 12 }}>
        <div className="s-row" style={{ paddingBottom: 4, borderBottom: '1px solid var(--line)' }}>
          <div className="k"><span>Available backends</span><span className="sub">installed inference backends · /api/backends</span></div>
        </div>
        {backendsQuery.isPending && <div style={{ padding: 12, color: 'var(--fg-4)', fontFamily: 'var(--jbm)', fontSize: 12 }}>Loading backends…</div>}
        {backendsQuery.isError && <div className="err">{backendsQuery.error?.message || 'Failed to load backends'}</div>}
        {!backendsQuery.isPending && !backendsQuery.isError && backends.length === 0 && (
          <div style={{ padding: 12, color: 'var(--fg-4)', fontFamily: 'var(--jbm)', fontSize: 12 }}>No backends reported.</div>
        )}
        {backends.map(b => (
          <SRow
            key={b.id}
            k={<span className="mono">{b.id}{b.recommended ? <span className="chip" style={{ marginLeft: 6, fontSize: 10, color: 'var(--accent)', borderColor: 'var(--accent)' }}>recommended</span> : null}</span>}
            sub={b.note || (b.usedBy && b.usedBy.length ? `in use by ${b.usedBy.join(', ')}` : b.device ? `device: ${b.device}` : '—')}
            mono
            v={<span style={{ color: 'var(--fg-3)' }}>{b.version || ''}</span>}
            actions={_stateChip(b.state)}
          />
        ))}
      </div>

      {/* ── Runner registry reference (ML-4 taxonomy, read-only meta) ────── */}
      <div className="s-panel" style={{ marginTop: 12 }}>
        <div className="s-row" style={{ paddingBottom: 4, borderBottom: '1px solid var(--line)' }}>
          <div className="k"><span>Runner registry</span><span className="sub">shipped taxonomy (ML-4) · resolution reference</span></div>
        </div>
        <SRow k="Runtime families" sub="Runner families the registry can launch (RUNNER_IMAGES.runtime_family)" mono v={<span style={{ color: 'var(--fg-3)' }}>{(enums.runtime_families || []).join(' · ') || '—'}</span>} />
        <SRow k="Selectable backends" sub="Backends a GPU slot may pick (device → runner)" mono v={<span style={{ color: 'var(--fg-3)' }}>{(enums.selectable_backends || []).join(' · ') || '—'}</span>} />
        <SRow k="Device classes" sub="Where a runner runs" mono v={<span style={{ color: 'var(--fg-3)' }}>{(enums.device_classes || []).join(' · ') || '—'}</span>} />
        <SRow
          k="Resolution precedence"
          sub="How the launch image/flags are chosen (REWORK §D)"
          v={<span className="mono" style={{ color: 'var(--fg-3)', fontSize: 11 }}>runner defaults → profile tune → arch defaults → model metadata → slot overrides</span>}
        />
      </div>

      {/* ── Gated controls (feature-flagged, not yet built) ──────────────── */}
      <div className="s-panel" style={{ marginTop: 12, opacity: 0.6 }}>
        <div className="s-row" style={{ paddingBottom: 4, borderBottom: '1px solid var(--line)' }}>
          <div className="k"><span>Backend controls</span><span className="sub">coming with the model-config lane (spec §7.1b / §21.2)</span></div>
        </div>
        <SRow k="ROCm channel / image pin" sub="Pin a runner image + digest per backend" v={<span className="chip" style={{ fontSize: 10, color: 'var(--fg-4)', borderColor: 'var(--fg-4)' }}>⛔ gated</span>} />
        <SRow k="gfx guard (HSA_OVERRIDE)" sub="Required HIP arch check before slot launch" v={<span className="chip" style={{ fontSize: 10, color: 'var(--fg-4)', borderColor: 'var(--fg-4)' }}>⛔ gated</span>} />
      </div>
    </div>
  )
}
