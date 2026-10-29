// Pure helper for the Overview version strip's host summary.

const DASH = '—'

/** Leading integer of the normalised cores string ("12c · 24t" → 12); null when absent. */
export function coreCount(cores: string | number | null | undefined): number | null {
  const n = parseInt(String(cores ?? ''), 10)
  return Number.isFinite(n) && n > 0 ? n : null
}

/**
 * `host <name> · <cores> cores · <pool> GB[ uma]`. A figure the probes cannot source renders `—`,
 * never `0`; the uma word shows only on unified-memory hosts.
 */
export function hostSummary(
  hw: { name?: string; cores?: string | number; memoryKind?: string },
  poolGb: number,
): string {
  const cores = coreCount(hw.cores)
  const pool = poolGb > 0 ? String(Math.round(poolGb * 10) / 10) : DASH
  const uma = hw.memoryKind === 'unified' ? ' uma' : ''
  return `host ${hw.name || DASH} · ${cores ?? DASH} cores · ${pool} GB${uma}`
}
