// hal0 v3 dashboard — Runtimes hook (D3, post-R3 surface rework).
//
// The runner/image axis is EVIDENCE, not config: images belong to runners (a
// code registry, RUNNER_IMAGES, digest-pinned and shipped with hal0 releases).
// GET /api/system-info (CLIENT) reports, per runner, its resolved image ref +
// runtime family + device class + a local on-disk state (installed /
// installable / unavailable). The reverse index — which models + slots resolve
// to each runner — is computed CLIENT-SIDE from the existing model/slot lists
// (a model selects a runner via `preferred_runner`; slots inherit through the
// model), so no dedicated /api/runtimes endpoint is needed for v1.
//
// NOT surfaced by system-info (deliberate backend scope trim, hardware.py):
// digest drift vs the shipped registry, and per-runner pull progress — so this
// page shows on-disk presence truthfully and flags the pull affordance as a
// pending API-lane request rather than fabricating "stale"/"pulling" states.

import { useQuery } from '@tanstack/react-query'
import { apiGet } from '../client'
import { ENDPOINTS } from '../endpoints'
import { useModels } from './useModels'
import { useSlots } from './useSlots'

export type RunnerState = 'installed' | 'installable' | 'unavailable'

export interface SystemInfoBackend {
  image: string
  runtime_family: string
  device_class: string
  backend: string
  state: RunnerState
}

export interface SystemInfo {
  hardware: Record<string, unknown>
  features: Record<string, unknown>
  backends: Record<string, SystemInfoBackend>
}

export interface RuntimeRow {
  key: string
  family: string
  backend: string
  deviceClass: string
  image: string
  imageRepo: string
  tag: string | null
  digest: string | null
  state: RunnerState
  models: string[]
  slots: string[]
}

const POLL_MS = 30_000

/** GET /api/system-info — never throws upstream; react-query surfaces errors. */
export function useSystemInfo() {
  return useQuery({
    queryKey: ['system-info'],
    queryFn: () => apiGet<SystemInfo>(ENDPOINTS.systemInfo),
    refetchInterval: POLL_MS,
    retry: false,
  })
}

// Split an image ref into { repo, tag, digest }. Mirrors the backend's
// _image_repo (strip @digest, then the tag after the last '/'-segment ':').
function parseImageRef(ref: string): { repo: string; tag: string | null; digest: string | null } {
  const s = String(ref || '')
  const atIdx = s.indexOf('@')
  const digest = atIdx >= 0 ? s.slice(atIdx + 1) : null
  const body = atIdx >= 0 ? s.slice(0, atIdx) : s
  const lastSlash = body.lastIndexOf('/')
  const tail = lastSlash >= 0 ? body.slice(lastSlash + 1) : body
  const colon = tail.indexOf(':')
  if (colon >= 0) {
    const tag = tail.slice(colon + 1)
    const repo = body.slice(0, lastSlash + 1) + tail.slice(0, colon)
    return { repo, tag, digest }
  }
  return { repo: body, tag: null, digest }
}

/**
 * Joined runtime rows: one per RUNNER_IMAGES entry, with its resolved image +
 * on-disk state and the reverse index of models + slots that resolve to it.
 * `probeUnavailable` is true when podman is unreachable and every runner
 * degrades to "unavailable" (the dev/no-podman box) — the page shows the
 * shipped registry and disables pull actions with that reason.
 */
export function useRuntimes() {
  const sys = useSystemInfo()
  const models = useModels()
  const slots = useSlots()

  const backends = sys.data?.backends ?? {}
  const modelRows = (models.data ?? []) as any[]
  const slotRows = (slots.data ?? []) as any[]

  // model id → runner key (preferred_runner is an untyped runtime field).
  const modelRunner = new Map<string, string>()
  const modelLabel = new Map<string, string>()
  for (const m of modelRows) {
    const runner = m?.preferred_runner
    if (m?.id) {
      modelLabel.set(m.id, m.longName || m.name || m.id)
      if (runner) modelRunner.set(m.id, String(runner))
    }
  }

  const rows: RuntimeRow[] = Object.entries(backends).map(([key, b]) => {
    const { repo, tag, digest } = parseImageRef(b.image)
    // Models that resolve to this runner: explicit preferred_runner match, else
    // fall back to the runner's backend appearing in the model's backends set.
    const modelNames: string[] = []
    for (const m of modelRows) {
      const explicit = m?.preferred_runner && String(m.preferred_runner) === key
      const byBackend =
        !m?.preferred_runner && b.backend && Array.isArray(m?.backends) && m.backends.includes(b.backend)
      if (explicit || byBackend) modelNames.push(m.longName || m.name || m.id)
    }
    // Slots resolve through their model.
    const slotNames: string[] = []
    for (const s of slotRows) {
      const mid = s?.model_id || s?.model
      const runnerForSlot = mid ? modelRunner.get(mid) : undefined
      if (runnerForSlot === key) slotNames.push(s.name)
    }
    return {
      key,
      family: b.runtime_family,
      backend: b.backend,
      deviceClass: b.device_class,
      image: b.image,
      imageRepo: repo,
      tag,
      digest,
      state: b.state,
      models: modelNames,
      slots: slotNames,
    }
  })

  const probeUnavailable = rows.length > 0 && rows.every((r) => r.state === 'unavailable')

  return {
    rows,
    probeUnavailable,
    isLoading: sys.isLoading,
    isError: sys.isError,
    error: sys.error as Error | null,
  }
}
