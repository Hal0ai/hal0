// hal0 v3 dashboard — Runtimes hook (D3, post-R3 surface rework).
//
// The runner/image axis is EVIDENCE, not config: images belong to runners (a
// code registry, RUNNER_IMAGES, digest-pinned and shipped with hal0 releases).
// GET /api/system-info (CLIENT) reports, per runner, its resolved image ref +
// runtime family + device class + a local on-disk state (installed /
// installable / unavailable). The reverse index — which slots resolve to each
// runner — is computed CLIENT-SIDE from the existing slot list.
//
// spec-hw-slot-ownership §8: the join FLIPPED. Models no longer resolve to a
// runner (`model.preferred_runner` is sunset — a model is device-agnostic).
// Hardware/placement is owned by the SLOT: a slot picks its runner via
// `slot.binary` (a key into RUNNER_IMAGES). This hook now indexes SLOTS via
// `binary`; the models column is transitive (the models bound to those slots).
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
  /** Fit-check metadata (spec-hw-slot-ownership §4): the backends this runner
   *  image can serve. RUNNER_IMAGES carries `supported_backends`; the
   *  system-info endpoint is assumed to surface it (backend Lane C). Absent on
   *  an older backend — callers fall back to `[backend]`. */
  supported_backends?: string[]
  /** GGUF/format arch the runner accepts (lxc105: forks reject newer GGUFs).
   *  Assumed surfaced alongside supported_backends; optional. */
  format_arch?: string | null
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
  /** Fit-check backends this runner image serves (falls back to [backend]). */
  supportedBackends: string[]
  deviceClass: string
  image: string
  imageRepo: string
  tag: string | null
  digest: string | null
  state: RunnerState
  /** Models transitively bound (via a slot's `binary`) to this runner. */
  models: string[]
  /** Slots whose `binary` resolves to this runner. */
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
/** Normalize a slot `device` enum (gpu-rocm | gpu-vulkan | gpu-cuda | cpu | npu)
 *  to its bare backend token — the value fit-checked against a runner's
 *  supported_backends. Strips the `gpu-` prefix; passes cpu/npu through. */
export function deviceBackend(device?: string | null): string {
  const d = String(device || '').toLowerCase()
  if (!d) return ''
  return d.startsWith('gpu-') ? d.slice(4) : d
}

export function useRuntimes() {
  const sys = useSystemInfo()
  const models = useModels()
  const slots = useSlots()

  const backends = sys.data?.backends ?? {}
  const modelRows = (models.data ?? []) as any[]
  const slotRows = (slots.data ?? []) as any[]

  // model id → display label (models no longer carry a runner — §8).
  const modelLabel = new Map<string, string>()
  for (const m of modelRows) {
    if (m?.id) modelLabel.set(m.id, m.longName || m.name || m.id)
  }

  // Resolve a slot to its runner key. Primary: the slot's typed `binary` field
  // (a RUNNER_IMAGES key). Fallback for slots not yet migrated (empty binary):
  // the runner whose backend matches the slot's device backend — mirrors the
  // backend's `runner_for_backend` HW-gated default.
  const slotRunnerKey = (s: any): string | undefined => {
    const explicit = s?.binary ? String(s.binary) : ''
    if (explicit) return explicit
    const be = deviceBackend(s?.device)
    if (!be) return undefined
    const match = Object.entries(backends).find(([, b]) => b.backend === be)
    return match?.[0]
  }

  const rows: RuntimeRow[] = Object.entries(backends).map(([key, b]) => {
    const { repo, tag, digest } = parseImageRef(b.image)
    const supportedBackends =
      Array.isArray(b.supported_backends) && b.supported_backends.length > 0
        ? b.supported_backends
        : b.backend
          ? [b.backend]
          : []
    // Slots resolve to this runner via `binary` (or the device-backend default).
    const slotNames: string[] = []
    const modelSet = new Set<string>()
    for (const s of slotRows) {
      if (slotRunnerKey(s) !== key) continue
      slotNames.push(s.name)
      // Models column is transitive — the models bound to those slots.
      const mid = s?.model_id || s?.model
      if (mid) modelSet.add(modelLabel.get(mid) || String(mid))
    }
    return {
      key,
      family: b.runtime_family,
      backend: b.backend,
      supportedBackends,
      deviceClass: b.device_class,
      image: b.image,
      imageRepo: repo,
      tag,
      digest,
      state: b.state,
      models: [...modelSet],
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
