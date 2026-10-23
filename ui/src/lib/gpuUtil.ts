// Shared GPU-utilization reading. A forced performance level
// (gpu-compute.service) pins the AMD busy counter at 100, so every surface that
// shows `gpu_util` must caption it as pinned (the `util_is_forced_high`
// contract) instead of presenting a flat 100% as real load. Keep the wording
// here in step with the telemetry header's "util · pinned" caption.

export interface GpuUtilStats {
  gpu_util?: number | null
  util_is_forced_high?: boolean | null
}

export interface GpuUtilReading {
  /** Whole-number percentage, or null when there is no reading. */
  pct: number | null
  /** The counter is pinned high and says nothing about real load. */
  pinned: boolean
  /** "32%", or "—" when there is no reading. */
  text: string
}

/** Short pinned caption used next to the value. */
export const GPU_PINNED_SHORT = 'pinned'
/** Long pinned caption for rows with room to explain why. */
export const GPU_PINNED_LONG = 'pinned (forced performance level)'

export function gpuUtilReading(s: GpuUtilStats | null | undefined): GpuUtilReading {
  const pct = typeof s?.gpu_util === 'number' ? Math.round(s.gpu_util * 100) : null
  return {
    pct,
    pinned: pct != null && !!s?.util_is_forced_high,
    text: pct != null ? `${pct}%` : '—',
  }
}

/** Single-line form: "32%", or "100% · pinned (forced performance level)". */
export function formatGpuUtil(s: GpuUtilStats | null | undefined): string {
  const r = gpuUtilReading(s)
  return r.pinned ? `${r.text} · ${GPU_PINNED_LONG}` : r.text
}
