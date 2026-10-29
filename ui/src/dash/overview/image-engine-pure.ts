// hal0 Overview v2 — Image engine card: the model behind it. Pure, so the mode wording, the
// queue line, the held-render note and the inventory tiles are tested without React.

import type { ArbiterReading, ComfyuiStatus } from '@/api/hooks/useComfyui'
import { clockLabel, renderPct } from '../image/status-line-parts-pure'

export interface ImageEngineModel {
  modeTone: 'ok' | 'img' | 'warn'
  modeText: string
  engine: string
  queueLine: string
  progress: { model: string; step: string | null; node: string | null; elapsed: string; pct: number | null } | null
  pendingNote: string | null
  inventory: Array<{ k: string; v: number }>
}

// /status `inventory` key -> the tile's label, in tile order.
const TILES: Array<[key: string, label: string]> = [
  ['checkpoints', 'ckpt'],
  ['diffusion', 'diffusion'],
  ['loras', 'loras'],
  ['vae', 'vae'],
]

function mode(status: ComfyuiStatus, arbiter: ArbiterReading | undefined): Pick<ImageEngineModel, 'modeTone' | 'modeText'> {
  const a = arbiter?.arbiter ?? status.arbiter
  if (a?.switching) return { modeTone: 'warn', modeText: 'switching' }
  // The arbiter's own mode is the truth; the legacy top-level mode is only the fallback.
  if (a) return a.mode === 'img' ? { modeTone: 'img', modeText: 'image mode' } : { modeTone: 'ok', modeText: 'chat mode' }
  if (status.mode === 'generation') return { modeTone: 'img', modeText: 'image mode' }
  if (status.mode === 'inference') return { modeTone: 'ok', modeText: 'chat mode' }
  return { modeTone: 'warn', modeText: '—' }
}

/** The card's model, or null when there is no status (404 from an older backend, or no img slot). */
export function imageModel(status: ComfyuiStatus | undefined, arbiter: ArbiterReading | undefined): ImageEngineModel | null {
  if (!status) return null
  const running = status.queue?.running ?? 0
  const pending = status.queue?.pending ?? 0
  const a = arbiter?.arbiter ?? status.arbiter

  let pendingNote: string | null = null
  if (pending > 0 && a?.mode === 'llm') {
    const n = a.unload_set?.length
    const noun = pending === 1 ? 'render' : 'renders'
    pendingNote = `${pending} ${noun} queued · waits for image mode` + (n != null ? ` (switch pauses ${n} slots)` : '')
  }

  const p = status.progress
  const progress = p
    ? {
        model: p.model,
        step: p.step != null && p.total != null ? `step ${p.step}/${p.total}` : null,
        node: p.node || null,
        elapsed: clockLabel(p.elapsed_s),
        pct: renderPct(p),
      }
    : null

  const inv = status.inventory ?? {}
  const inventory = TILES.filter(([key]) => typeof inv[key] === 'number').map(([key, k]) => ({ k, v: inv[key] }))

  return { ...mode(status, arbiter), engine: status.engine, queueLine: `queue ${running} running · ${pending} pending`, progress, pendingNote, inventory }
}
