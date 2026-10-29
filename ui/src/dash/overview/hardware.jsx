// hal0 Overview v2 — Hardware card: gpu, cpu, npu, ram and disk rows.
//
// Replaces the old Utilization card. GPU and CPU keep a 30-sample ring each,
// fed on every /api/stats/hardware data change, so the row sparklines draw
// the last minute or so of the live counters. A metric a probe cannot source
// renders "—", never 0.
import { useHardware, hasGpuAccess, hasNpuAccess } from '@/api/hooks/useHardware'
import { useStatsHardware } from '@/api/hooks/useStatsHardware'
import { useStatsPower } from '@/api/hooks/useStatsPower'
import { useHealthSystem } from '@/api/hooks/useRuntime'
import { useSlots } from '@/api/hooks/useSlots'
import { useMemoryMapModel } from '../memory-map'
import { hardwareRows, pushSample } from './hardware-pure'

const { useRef, useEffect, useState } = React

function OvHardware({ onGo: _onGo }) {
  const hw = useHardware()
  const stats = useStatsHardware()
  const power = useStatsPower()
  const health = useHealthSystem()
  const slotsQuery = useSlots()
  const { ram } = useMemoryMapModel()

  const st = stats.data
  const hasGpu = hasGpuAccess(hw.data)
  const hasNpu = hasNpuAccess(hw.data)

  const gpuRing = useRef([])
  const cpuRing = useRef([])
  const [, bump] = useState(0)
  useEffect(() => {
    if (!st) return
    gpuRing.current = pushSample(gpuRing.current, hasGpu ? (st.gpu_util ?? null) : null)
    cpuRing.current = pushSample(cpuRing.current, st.cpu_util ?? null)
    bump((n) => n + 1)
    // The ring follows the data object, not the gating flag.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [st])

  const npuSlots = (slotsQuery.data ?? [])
    .filter((s) => (s.device || '') === 'npu' && s.coresident)
    .map((s) => s.name)

  const rows = hardwareRows({
    hasGpu,
    hasNpu,
    gpuName: hw.data?.gpu || null,
    cores: hw.data?.cores || null,
    stats: st,
    power: power.data,
    ram,
    disk: health.data?.checks?.disk_state ?? null,
    npuSlots,
  })

  const rings = { gpu: gpuRing.current, cpu: cpuRing.current }

  return (
    <OvCard title="Hardware" note="live">
      {rows.map((r) => (
        <div className="ov-hw" key={r.key}>
          <span className="ov-k">
            <span className="ov-d" style={{ background: r.color }} />
            {r.label}
          </span>
          <div className="ov-mid">
            <span className="ov-cap" title={r.caption}>{r.caption}</span>
            <span className="ov-track">
              {r.pct != null && <i style={{ width: `${r.pct}%`, background: r.color }} />}
            </span>
          </div>
          <div className="ov-r">
            {rings[r.key] && <OvSpark values={rings[r.key]} width={70} height={18} color={r.color} />}
            {r.pill ? (
              <OvPill tone={r.pill === 'active' ? 'ok' : 'dim'}>
                <span className="ov-d" />
                {r.pill}
              </OvPill>
            ) : (
              <span className="ov-v">{r.value}</span>
            )}
          </div>
        </div>
      ))}
    </OvCard>
  )
}

Object.assign(window, { OvHardware })
