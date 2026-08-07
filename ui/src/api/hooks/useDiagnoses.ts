// hal0 v3 dashboard — diagnoses hook (D6, wired to the live doctor feed).
//
// `hal0 doctor` emits TYPED diagnoses — a stable HAL0-* id, severity,
// confidence, evidence[], next_steps[] (src/hal0/diagnostics.py) — and
// GET /api/doctor (src/hal0/api/routes/doctor.py) serves exactly that shape
// over HTTP, composed from the same seams `hal0 doctor verify --json` reads.
// This hook reads that route; the DiagnosisPanel renders its rows unchanged,
// which is the whole point of a generic Diagnosis renderer.
//
// FALLBACK: a backend that predates the route (404) — or one answering with a
// payload that carries no `diagnoses` array — degrades to ONE synthesised
// informational card built from GET /api/system-info hardware evidence, and
// reports `doctorFeedPending` so the panel can say WHY there are no verdicts
// rather than implying a clean bill of health. Every other failure (5xx,
// network) surfaces as an error; it is never silently downgraded to "ok".

import { useQuery } from '@tanstack/react-query'
import { apiGet, Hal0Error } from '../client'
import { ENDPOINTS } from '../endpoints'
import { useSystemInfo } from './useRuntimes'

export type Severity = 'info' | 'warn' | 'fail' | 'critical'
export type Confidence = 'low' | 'medium' | 'high'

export interface Evidence {
  kind: string // "file" | "command" | "endpoint" | "table_row" | "config"
  summary: string
  data?: Record<string, unknown>
}

export interface NextStep {
  kind: 'command' | 'manual' | 'doc'
  label: string
  target: string
}

export interface Diagnosis {
  id: string
  severity: Severity
  confidence: Confidence
  summary: string
  detail?: string
  evidence: Evidence[]
  next_steps: NextStep[]
  fixable?: boolean
}

export type Verdict = 'ok' | 'warn' | 'critical'

/** The GET /api/doctor body (routes/doctor.py DoctorResponse). */
export interface DoctorFeed {
  verdict: Verdict
  diagnoses: Diagnosis[]
}

/** Roll a list of diagnoses up to "ok" | "warn" | "critical". Mirrors
 *  diagnostics.overall_verdict exactly so the client label matches the server. */
export function overallVerdict(diagnoses: Diagnosis[]): Verdict {
  if (diagnoses.some((d) => d.severity === 'critical')) return 'critical'
  if (diagnoses.some((d) => d.severity === 'warn' || d.severity === 'fail')) return 'warn'
  return 'ok'
}

/** Shown in-panel when the server feed isn't answering — never a fake pass. */
export const DOCTOR_FEED_UNAVAILABLE_REASON =
  'Live doctor feed unavailable (GET /api/doctor) — showing synthesised system-info ' +
  'evidence instead of typed `hal0 doctor` verdicts.'

// ─── server payload normalisation ────────────────────────────────────────────
// The route is response_model-validated server-side, so this is defensive
// rather than corrective: it keeps one malformed row from blanking the panel.

const SEVERITIES: ReadonlySet<string> = new Set<Severity>(['info', 'warn', 'fail', 'critical'])
const CONFIDENCES: ReadonlySet<string> = new Set<Confidence>(['low', 'medium', 'high'])
const STEP_KINDS: ReadonlySet<string> = new Set<NextStep['kind']>(['command', 'manual', 'doc'])

function normalizeEvidence(raw: unknown): Evidence[] {
  if (!Array.isArray(raw)) return []
  const out: Evidence[] = []
  for (const row of raw) {
    if (!row || typeof row !== 'object') continue
    const e = row as Record<string, unknown>
    if (typeof e.summary !== 'string') continue
    out.push({
      kind: typeof e.kind === 'string' ? e.kind : 'config',
      summary: e.summary,
      data: (e.data as Record<string, unknown> | undefined) ?? {},
    })
  }
  return out
}

function normalizeNextSteps(raw: unknown): NextStep[] {
  if (!Array.isArray(raw)) return []
  const out: NextStep[] = []
  for (const row of raw) {
    if (!row || typeof row !== 'object') continue
    const s = row as Record<string, unknown>
    if (typeof s.label !== 'string' || typeof s.target !== 'string') continue
    out.push({
      kind: STEP_KINDS.has(String(s.kind)) ? (s.kind as NextStep['kind']) : 'manual',
      label: s.label,
      target: s.target,
    })
  }
  return out
}

function normalizeDiagnosis(raw: unknown): Diagnosis | null {
  if (!raw || typeof raw !== 'object') return null
  const d = raw as Record<string, unknown>
  if (typeof d.id !== 'string' || !d.id) return null
  return {
    id: d.id,
    severity: (SEVERITIES.has(String(d.severity)) ? d.severity : 'info') as Severity,
    confidence: (CONFIDENCES.has(String(d.confidence)) ? d.confidence : 'medium') as Confidence,
    summary: typeof d.summary === 'string' && d.summary ? d.summary : d.id,
    detail: typeof d.detail === 'string' && d.detail ? d.detail : undefined,
    evidence: normalizeEvidence(d.evidence),
    next_steps: normalizeNextSteps(d.next_steps),
    fixable: d.fixable === true,
  }
}

/** `null` means "this backend has no doctor feed" — the caller falls back. */
export function normalizeDoctorFeed(raw: unknown): DoctorFeed | null {
  if (!raw || typeof raw !== 'object') return null
  const body = raw as Record<string, unknown>
  if (!Array.isArray(body.diagnoses)) return null
  const diagnoses = body.diagnoses
    .map(normalizeDiagnosis)
    .filter((d): d is Diagnosis => d !== null)
  const verdict =
    body.verdict === 'critical' || body.verdict === 'warn' || body.verdict === 'ok'
      ? (body.verdict as Verdict)
      : overallVerdict(diagnoses)
  return { verdict, diagnoses }
}

const DOCTOR_POLL_MS = 30_000

/**
 * Polls GET /api/doctor. Resolves to `null` — NOT an error — only when the
 * route is missing (404) or answers without a diagnosis feed, which is the
 * one case the system-info synthesis is allowed to stand in for.
 */
export function useDoctorFeed() {
  return useQuery<DoctorFeed | null>({
    queryKey: ['doctor', 'feed'],
    queryFn: async () => {
      try {
        return normalizeDoctorFeed(await apiGet<unknown>(ENDPOINTS.doctor))
      } catch (err) {
        if (err instanceof Hal0Error && err.status === 404) return null
        throw err
      }
    },
    refetchInterval: DOCTOR_POLL_MS,
    retry: false,
  })
}

// ─── system-info fallback ────────────────────────────────────────────────────

function mb(v: unknown): string | null {
  const n = typeof v === 'number' ? v : Number(v)
  if (!Number.isFinite(n) || n <= 0) return null
  if (n >= 1024) return `${(n / 1024).toFixed(1)} GB`
  return `${Math.round(n)} MB`
}

// Turn the flat /api/hardware payload into evidence rows — tolerant of absent
// keys (a degraded probe simply yields fewer rows).
function hardwareEvidence(hw: Record<string, unknown>): Evidence[] {
  const rows: Evidence[] = []
  const push = (label: string, value: string | null | undefined, data: Record<string, unknown> = {}) => {
    if (value == null || value === '') return
    rows.push({ kind: 'config', summary: `${label} · ${value}`, data })
  }
  push('platform', (hw.platform_label as string) || (hw.platform as string))
  push('cpu', (hw.cpu_name as string) || null, { cores: hw.cpu_cores, threads: hw.cpu_threads })
  push('gpu', (hw.gpu_name as string) || null, { vendor: hw.gpu_vendor })
  push('unified memory', mb(hw.unified_memory_mb) ?? mb(hw.ram_total_mb))
  push('vram', mb(hw.vram_total_mb))
  push('disk free', mb(hw.disk_free_mb))
  if (hw.npu_present) push('npu', (hw.npu_name as string) || 'present')
  return rows
}

function synthesiseSystemInfo(hw: Record<string, unknown>, evidence: Evidence[]): Diagnosis[] {
  if (evidence.length === 0) return []
  return [
    {
      id: 'HAL0-SYS-INFO',
      severity: 'info',
      confidence: 'high',
      summary: (hw.platform_label as string) || 'System evidence collected',
      detail:
        'Derived live from GET /api/system-info (hardware probe). Not a `hal0 doctor` verdict — ' +
        'this backend is not serving the doctor feed.',
      evidence,
      next_steps: [
        { kind: 'command', label: 'run: hal0 doctor', target: 'hal0 doctor' },
        { kind: 'doc', label: 'Diagnostics reference', target: '#settings/updates' },
      ],
      fixable: false,
    },
  ]
}

/**
 * The diagnoses feeding the panel: the server's typed rows when
 * GET /api/doctor answers, otherwise the synthesised system-info card with
 * `doctorFeedPending` set so the panel can explain the gap.
 */
export function useDiagnoses() {
  const doctor = useDoctorFeed()
  const sys = useSystemInfo()

  const feed = doctor.data ?? null
  const hw = (sys.data?.hardware ?? {}) as Record<string, unknown>
  const evidence = feed ? [] : hardwareEvidence(hw)
  const synthesised = feed ? [] : synthesiseSystemInfo(hw, evidence)

  const diagnoses = feed ? feed.diagnoses : synthesised
  const verdict = feed ? feed.verdict : overallVerdict(synthesised)

  return {
    diagnoses,
    verdict,
    // The doctor feed is the primary source, so it owns loading/error; the
    // fallback's own state only matters once the feed is known to be absent.
    isLoading: doctor.isPending || (!feed && sys.isPending),
    isError: doctor.isError || (!feed && !doctor.isPending && sys.isError),
    error: doctor.error ?? (feed ? null : sys.error),
    probeUnavailable: !feed && !sys.isPending && evidence.length === 0,
    doctorFeedPending: !feed && !doctor.isPending,
    doctorFeedReason: DOCTOR_FEED_UNAVAILABLE_REASON,
    refetch: () => {
      void doctor.refetch()
      void sys.refetch()
    },
  }
}
