// OBSERVABILITY ▸ Health & Stats — WIRED (Phase-2 settings-seam lane).
//
// Was a bare "not yet wired — placeholder" stub (SWEEP §5). All the data
// this page needs already has a typed, polling hook shipped elsewhere in
// the dashboard (dashboard-redesign.jsx / services-card.jsx use the same
// ones) — there was no missing backend surface, just no settings-page UI:
//   - useHealthSystem()   → GET /api/health/system   (overall status + per-check map)
//   - useStatsHardware()  → GET /api/stats/hardware  (live RAM/GPU/NPU/CPU counters)
//   - useStatsPower()     → GET /api/stats/power     (GPU power/clock + CPU temp, fail-soft)
//   - useRequestsRollup() → GET /api/stats/requests  (dispatcher /v1 rollup, just landed)
//   - useServicesHealth() → GET /api/services/health (per-service up/down, fail-soft)
// Every hook here is already fail-soft (404/network error → pending, never
// throws) — this page just renders what they report. Prometheus scrape +
// per-slot metrics are read-only reference links, not re-implemented here.
import { useHealthSystem, failingChecks } from '@/api/hooks/useRuntime'
import { useStatsHardware } from '@/api/hooks/useStatsHardware'
import { useStatsPower } from '@/api/hooks/useStatsPower'
import { useRequestsRollup } from '@/api/hooks/useRequestsRollup'
import { useServicesHealth } from '@/api/hooks/useServicesHealth'
import { SRow } from '../../shared/SRow.jsx'

function _mb(mb) {
  const n = Number(mb)
  if (!n || n <= 0) return '—'
  if (n < 1024) return `${Math.round(n)} MB`
  return `${(n / 1024).toFixed(1)} GB`
}

function _pct(v) {
  return v == null ? '—' : `${Math.round(v)}%`
}

function _ms(v) {
  return v == null ? '—' : `${Math.round(v)} ms`
}

function _statusChip(ok, label) {
  const color = ok ? 'var(--ok)' : 'var(--warn)'
  return (
    <span className="chip" style={{ fontSize: 10, color, borderColor: color }}>
      {ok ? '● ' : '⚠ '}{label}
    </span>
  )
}

function HealthOverviewPanel() {
  const health = useHealthSystem()
  const failing = failingChecks(health.data)
  const degraded = health.data?.status === 'degraded'

  return (
    <div className="s-panel">
      <div className="s-row" style={{ paddingBottom: 4, borderBottom: '1px solid var(--line)' }}>
        <div className="k"><span>Health checks</span><FieldInfoIcon description="/api/health/system · per-dependency status" /></div>
      </div>
      <SRow
        k="Overall status"
        sub={degraded ? failing.join(', ') || 'one or more checks failing' : 'all checks passing'}
        v={_statusChip(!degraded, degraded ? 'degraded' : 'ok')}
      />
    </div>
  )
}

function HardwarePanel() {
  const stats = useStatsHardware()
  const H = stats.data

  return (
    <div className="s-panel" style={{ marginTop: 12 }}>
      <div className="s-row" style={{ paddingBottom: 4, borderBottom: '1px solid var(--line)' }}>
        <div className="k"><span>Live hardware</span><FieldInfoIcon description="/api/stats/hardware · polled every 2.5s" /></div>
      </div>
      {stats.isPending && <div style={{ padding: 12, color: 'var(--fg-4)', fontFamily: 'var(--jbm)', fontSize: 12 }}>Reading live counters…</div>}
      {stats.isError && <div className="err">{stats.error?.message || 'Failed to read /api/stats/hardware'}</div>}
      {H && (
        <>
          <SRow k="RAM" sub="Host memory in use" mono v={<span style={{ color: 'var(--fg-2)' }}>{_mb(H.ram_used_mb)} / {_mb(H.ram_total_mb)}</span>} />
          <SRow k="GPU utilization" sub="Compute load on the primary accelerator" mono v={_pct(H.gpu_util)} />
          <SRow k="GPU VRAM" sub="Dedicated/GTT memory in use" mono v={<span style={{ color: 'var(--fg-3)' }}>{_mb(H.gpu_vram_used_mb ?? H.gtt_used_mb)} / {_mb(H.gpu_vram_total_mb)}</span>} />
          <SRow k="CPU utilization" sub="Host CPU load" mono v={_pct(H.cpu_util)} />
          {H.npu_status && <SRow k="NPU" sub="XDNA/FastFlowLM model residency" v={_statusChip(H.npu_status.ok, `${H.npu_status.ok ? 'ok' : 'not ok'} · ${_mb(H.npu_status.model_mb)}`)} />}
        </>
      )}
    </div>
  )
}

function PowerPanel() {
  const power = useStatsPower()
  const P = power.data

  return (
    <div className="s-panel" style={{ marginTop: 12 }}>
      <div className="s-row" style={{ paddingBottom: 4, borderBottom: '1px solid var(--line)' }}>
        <div className="k"><span>Power &amp; thermal</span><FieldInfoIcon description="/api/stats/power · sysfs hwmon, sensor-dependent" /></div>
      </div>
      {power.isPending && <div style={{ padding: 12, color: 'var(--fg-4)', fontFamily: 'var(--jbm)', fontSize: 12 }}>source pending — no hwmon sensor exposed on this host</div>}
      {!power.isPending && P && (
        <>
          <SRow k="GPU power draw" mono v={P.gpu_power_w != null ? `${Math.round(P.gpu_power_w)} W` : '—'} />
          <SRow k="GPU clock" mono v={P.gpu_sclk_mhz != null ? `${P.gpu_sclk_mhz} MHz` : '—'} />
          <SRow k="GPU temperature" mono v={P.gpu_temp_c != null ? `${Math.round(P.gpu_temp_c)}°C` : '—'} />
          <SRow k="CPU temperature" mono v={P.cpu_temp_c != null ? `${Math.round(P.cpu_temp_c)}°C` : '—'} />
        </>
      )}
    </div>
  )
}

function RequestsPanel() {
  const { data, pending } = useRequestsRollup()
  const eps = data?.endpoints ?? []

  return (
    <div className="s-panel" style={{ marginTop: 12 }}>
      <div className="s-row" style={{ paddingBottom: 4, borderBottom: '1px solid var(--line)' }}>
        <div className="k"><span>Requests</span><FieldInfoIcon description="/api/stats/requests · dispatcher /v1 rollup, last 60s" /></div>
      </div>
      {pending && <div style={{ padding: 12, color: 'var(--fg-4)', fontFamily: 'var(--jbm)', fontSize: 12 }}>source pending</div>}
      {!pending && data && (
        <>
          <SRow k="Requests / min" mono v={data.req_per_min != null ? Math.round(data.req_per_min) : '—'} />
          <SRow k="Latency" sub="p50 · p95" mono v={`${_ms(data.p50_ms)} · ${_ms(data.p95_ms)}`} />
          <SRow k="Errors" sub="in window" mono v={data.errors ?? '—'} />
          {eps.length > 0 && (
            <SRow
              k="Top endpoints"
              mono
              v={<span style={{ color: 'var(--fg-3)' }}>{eps.slice(0, 5).map(e => `${e.path} (${e.count})`).join(' · ')}</span>}
            />
          )}
        </>
      )}
    </div>
  )
}

function ServicesPanel() {
  const { services, pending } = useServicesHealth()

  return (
    <div className="s-panel" style={{ marginTop: 12 }}>
      <div className="s-row" style={{ paddingBottom: 4, borderBottom: '1px solid var(--line)' }}>
        <div className="k"><span>Services</span><FieldInfoIcon description="/api/services/health · per-service up/down" /></div>
      </div>
      {pending && <div style={{ padding: 12, color: 'var(--fg-4)', fontFamily: 'var(--jbm)', fontSize: 12 }}>source pending</div>}
      {!pending && services.length === 0 && (
        <div style={{ padding: 12, color: 'var(--fg-4)', fontFamily: 'var(--jbm)', fontSize: 12 }}>No services reported.</div>
      )}
      {services.map(s => (
        <SRow
          key={s.id}
          k={s.name}
          sub={s.detail}
          mono
          v={s.stat ? <span style={{ color: 'var(--fg-3)' }}>{s.stat.label}: {s.stat.value}</span> : null}
          actions={_statusChip(s.up, s.up ? 'up' : 'down')}
        />
      ))}
    </div>
  )
}

export function HealthStatsPage() {
  return (
    <div className="s-section">
      <h2>Health &amp; Stats</h2>
      <p className="desc">
        Live hardware / power / request / service health, read straight off the same polling hooks
        the main dashboard uses. Raw scrape targets stay linkable below — per-slot detail lives on{' '}
        <a href="#slots">Loaded Models</a>.
      </p>

      <HealthOverviewPanel />
      <HardwarePanel />
      <PowerPanel />
      <RequestsPanel />
      <ServicesPanel />

      <div className="mono" style={{ marginTop: 12, fontSize: 10.5, color: 'var(--fg-5)' }}>
        Raw endpoints: <span style={{ color: 'var(--fg-4)' }}>/api/health</span> ·{' '}
        <span style={{ color: 'var(--fg-4)' }}>/api/metrics/prometheus</span> (scrape target) ·{' '}
        <span style={{ color: 'var(--fg-4)' }}>/api/slots/metrics</span> (per-slot detail).
      </div>
    </div>
  );
}
