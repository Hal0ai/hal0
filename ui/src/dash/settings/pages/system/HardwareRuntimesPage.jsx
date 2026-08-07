// SYSTEM ▸ Hardware & Runtimes — merged evidence surface (settings-panel
// cleanup): the former INFERENCE ▸ Backend & GPU page (detected hardware,
// available backends, runner-registry reference) and DIAGNOSTICS ▸ Runtimes
// page (container image per runner) were both read-only introspection over
// the same axis, so they live together now.
//
// Honest surface note (unchanged from Backend & GPU): there is NO
// `/api/settings` (Hal0Config) key that selects the backend / engine / ROCm
// channel / runner image. That axis is resolved per-slot (`SlotConfig.device`
// via /api/slots) → per-model (`Model.preferred_runner` via /api/models) →
// the code-owned RUNNER_IMAGES registry (REWORK §D resolution precedence).
// Nothing here edits an image string — images ship with hal0 releases and the
// updater reconciles drift; to change what a slot runs on, set its BINARY in
// the slot editor. The old page's permanently-gated "Backend controls" rows
// (ROCm pin / gfx guard) and the disabled "Pre-pull" button are gone: neither
// had a backend route, and a dead control is worse than none. Both return
// with their lanes (spec §7.1b / §21.2, per-runner pull API).
import { useHardware } from '@/api/hooks/useHardware'
import { useBackends } from '@/api/hooks/useBackends'
import { useMetaEnums } from '@/api/hooks/useMeta'
import { useRuntimes } from '@/api/hooks/useRuntimes'
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

function DetectedHardwarePanel() {
  const hw = useHardware()
  const H = hw.data

  return (
    <div className="s-panel">
      <div className="s-row" style={{ paddingBottom: 4, borderBottom: '1px solid var(--line)' }}>
        <div className="k"><span>Detected hardware</span><FieldInfoIcon description="host accelerators · /api/hardware" /></div>
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
  )
}

function BackendsPanel() {
  const backendsQuery = useBackends()
  const backends = backendsQuery.data?.backends || []

  return (
    <div className="s-panel" style={{ marginTop: 12 }}>
      <div className="s-row" style={{ paddingBottom: 4, borderBottom: '1px solid var(--line)' }}>
        <div className="k"><span>Available backends</span><FieldInfoIcon description="installed inference backends · /api/backends" /></div>
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
  )
}

// ── Runner images (former Runtimes page) ────────────────────────────────────

// Runner hue by backend — the slot/model device hues, reused here.
function hueFor(backend, deviceClass) {
  const b = String(backend || '').toLowerCase()
  const d = String(deviceClass || '').toLowerCase()
  if (b.includes('rocm')) return 'var(--dev-rocm)'
  if (b.includes('vulkan')) return 'var(--dev-vulkan)'
  if (b.includes('flm') || d.includes('npu')) return 'var(--dev-npu)'
  return 'var(--dev-cpu)'
}

function StatusChip({ state }) {
  if (state === 'installed') {
    return <span className="chip" data-status="installed" style={{ color: 'var(--ok)', borderColor: 'var(--ok-line)', background: 'var(--ok-soft)' }}>● installed</span>
  }
  if (state === 'installable') {
    return <span className="chip" data-status="installable" style={{ color: 'var(--warn)', borderColor: 'var(--warn-line)', background: 'var(--warn-soft)' }}>○ not pulled</span>
  }
  return <span className="chip" data-status="unavailable" style={{ color: 'var(--fg-4)', borderColor: 'var(--line)', background: 'var(--bg-2)' }}>probe unavailable</span>
}

function RuntimeRow({ r }) {
  const hue = hueFor(r.backend, r.deviceClass)
  const models = r.models.length
  const slots = r.slots.length
  return (
    <div
      className="s-row"
      data-testid={`runtime-row-${r.key}`}
      style={{ display: 'grid', gridTemplateColumns: '150px 1fr 150px 150px', gap: 14, alignItems: 'center' }}
    >
      {/* runner */}
      <div style={{ display: 'flex', alignItems: 'center', gap: 8 }}>
        <span style={{ width: 7, height: 7, borderRadius: '50%', background: hue }} />
        <span className="mono" style={{ fontSize: 12.5, color: 'var(--fg)' }}>{r.key}</span>
      </div>
      {/* image · digest (read-only) */}
      <div className="mono" data-testid={`runtime-image-${r.key}`} style={{ fontSize: 11, color: 'var(--fg-3)', lineHeight: 1.5, wordBreak: 'break-all' }}>
        <span style={{ color: 'var(--fg-2)' }}>{r.imageRepo}</span>
        {r.tag && <span style={{ color: 'var(--fg-5)' }}>:{r.tag}</span>}
        <br />
        <span style={{ color: 'var(--fg-4)' }}>{r.digest ? r.digest : `${r.family} · digest pinned by release`}</span>
      </div>
      {/* resolves to */}
      <div className="mono" data-testid={`runtime-resolves-${r.key}`} style={{ fontSize: 11, color: 'var(--fg-3)' }}>
        {models === 0 && slots === 0 ? (
          <span style={{ color: 'var(--fg-5)' }}>not in use</span>
        ) : (
          <>
            <a href="#models" style={{ color: 'var(--fg-2)' }}>{models} model{models !== 1 ? 's' : ''}</a>
            {' · '}
            <a href="#slots" style={{ color: 'var(--fg-2)' }}>{slots} slot{slots !== 1 ? 's' : ''}</a>
          </>
        )}
      </div>
      {/* status */}
      <div style={{ display: 'flex', justifyContent: 'flex-end' }}>
        <span data-testid={`runtime-status-${r.key}`}><StatusChip state={r.state} /></span>
      </div>
    </div>
  )
}

function RunnerImagesPanel() {
  const rt = useRuntimes()

  return (
    <>
      {rt.probeUnavailable && (
        <div
          data-testid="runtimes-degraded"
          className="banner banner-warn"
          role="status"
          style={{ border: '1px solid var(--warn-line)', background: 'var(--warn-soft)', borderRadius: 8, padding: '11px 14px', margin: '12px 0 0', fontSize: 12.5, lineHeight: 1.5, color: 'var(--fg-2)' }}
        >
          Installed state unknown — the <span className="mono">system-info</span> probe is unavailable (no
          podman on this host). Showing the shipped registry.
        </div>
      )}

      <div className="s-panel" style={{ marginTop: 12 }}>
        <div className="s-row" style={{ paddingBottom: 4, borderBottom: '1px solid var(--line)' }}>
          <div className="k"><span>Runner images</span><FieldInfoIcon description="container image per runner · /api/system-info · reconciled by hal0 update" /></div>
        </div>
        <div className="s-row" style={{ borderBottom: '1px solid var(--line)', display: 'grid', gridTemplateColumns: '150px 1fr 150px 150px', gap: 14 }}>
          <span className="mono" style={{ fontSize: 9.5, letterSpacing: '.06em', textTransform: 'uppercase', color: 'var(--fg-4)' }}>runner</span>
          <span className="mono" style={{ fontSize: 9.5, letterSpacing: '.06em', textTransform: 'uppercase', color: 'var(--fg-4)' }}>image · digest</span>
          <span className="mono" style={{ fontSize: 9.5, letterSpacing: '.06em', textTransform: 'uppercase', color: 'var(--fg-4)' }}>resolves to</span>
          <span className="mono" style={{ fontSize: 9.5, letterSpacing: '.06em', textTransform: 'uppercase', color: 'var(--fg-4)', textAlign: 'right' }}>status</span>
        </div>

        {rt.isLoading && (
          <div className="s-row"><span className="mono" style={{ fontSize: 12, color: 'var(--fg-4)' }}>Probing runtimes…</span></div>
        )}
        {rt.isError && (
          <div className="s-row"><span className="err">{rt.error?.message || 'Failed to read /api/system-info'}</span></div>
        )}
        {!rt.isLoading && !rt.isError && rt.rows.length === 0 && (
          <div className="s-row"><span className="mono" style={{ fontSize: 12, color: 'var(--fg-4)' }}>No runners reported.</span></div>
        )}
        {rt.rows.map((r) => <RuntimeRow key={r.key} r={r} />)}
      </div>
    </>
  )
}

function RegistryReferencePanel() {
  const enums = useMetaEnums()

  return (
    <div className="s-panel" style={{ marginTop: 12 }}>
      <div className="s-row" style={{ paddingBottom: 4, borderBottom: '1px solid var(--line)' }}>
        <div className="k"><span>Runner registry</span><FieldInfoIcon description="shipped taxonomy (ML-4) · resolution reference" /></div>
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
  )
}

export function HardwareRuntimesPage() {
  return (
    <div className="s-section" data-testid="hardware-page">
      <h2>Hardware &amp; Runtimes</h2>
      <p className="desc">
        Detected accelerators, installed inference backends, and the container image each runner is
        pinned to — all read-only evidence. Backend, runner image, and ROCm channel are chosen{' '}
        <b>per slot</b> (device) and <b>per model</b> (preferred runner), then resolved against the
        shipped runner registry; images ship with hal0 releases and are reconciled by the updater.
        To change what a slot runs on, set its BINARY in the slot editor.
      </p>

      <DetectedHardwarePanel />
      <BackendsPanel />
      <RunnerImagesPanel />
      <RegistryReferencePanel />

      <div className="mono" style={{ marginTop: 12, fontSize: 10.5, color: 'var(--fg-5)' }}>
        Image &amp; digest are pinned by the release. <span style={{ color: 'var(--fg-4)' }}>hal0 update --channel stable</span> reconciles all runners in one pass.
      </div>
    </div>
  )
}
