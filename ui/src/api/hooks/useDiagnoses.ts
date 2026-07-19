// hal0 v3 dashboard — diagnoses hook (D6, post-R3 surface rework).
//
// `hal0 doctor` emits TYPED diagnoses — a stable HAL0-* id, severity,
// confidence, evidence[], next_steps[] (src/hal0/diagnostics.py) — but only
// over the CLI / --json path. There is no HTTP route yet (GET /api/doctor is an
// API-lane request), so the full verdict FEED can't be surfaced.
//
// What DOES exist today is GET /api/system-info (CLIENT) — hardware + features +
// per-runner backend state. So this hook wires the DiagnosisPanel to real data
// by synthesising ONE informational Diagnosis from the live hardware evidence,
// in exactly the same generic shape the doctor feed will use. When /api/doctor
// lands, its rows drop into the same `diagnoses` array and the panel renders
// them unchanged — the whole point of a generic Diagnosis renderer.

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

/** Roll a list of diagnoses up to "ok" | "warn" | "critical". Mirrors
 *  diagnostics.overall_verdict exactly so the client label matches the server. */
export function overallVerdict(diagnoses: Diagnosis[]): 'ok' | 'warn' | 'critical' {
  if (diagnoses.some((d) => d.severity === 'critical')) return 'critical'
  if (diagnoses.some((d) => d.severity === 'warn' || d.severity === 'fail')) return 'warn'
  return 'ok'
}

// The `hal0 doctor` HTTP feed does not exist — say so, once, in the panel.
export const DOCTOR_FEED_REASON =
  'Live doctor verdicts (typed HAL0-* diagnoses over HTTP) are not wired yet — `hal0 doctor` emits them on the CLI only. (API-lane request: GET /api/doctor)'

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

/**
 * The diagnoses feeding the panel. Today: one synthesised system-info evidence
 * card. `doctorFeedPending` stays true (with DOCTOR_FEED_REASON) until the HTTP
 * doctor route exists — the panel shows that as a stub-with-reason, never as a
 * fabricated pass.
 */
export function useDiagnoses() {
  const sys = useSystemInfo()
  const hw = (sys.data?.hardware ?? {}) as Record<string, unknown>
  const evidence = hardwareEvidence(hw)
  const probeUnavailable = !sys.isPending && evidence.length === 0

  const diagnoses: Diagnosis[] = []
  if (evidence.length > 0) {
    diagnoses.push({
      id: 'HAL0-SYS-INFO',
      severity: 'info',
      confidence: 'high',
      summary: (hw.platform_label as string) || 'System evidence collected',
      detail:
        'Derived live from GET /api/system-info (hardware probe). Not a `hal0 doctor` verdict — ' +
        'the typed pass/warn/fail feed lands with the doctor HTTP route.',
      evidence,
      next_steps: [
        { kind: 'command', label: 'run: hal0 doctor', target: 'hal0 doctor' },
        { kind: 'doc', label: 'Diagnostics reference', target: '#settings/about' },
      ],
      fixable: false,
    })
  }

  return {
    diagnoses,
    verdict: overallVerdict(diagnoses),
    isLoading: sys.isPending,
    isError: sys.isError,
    error: sys.error,
    probeUnavailable,
    doctorFeedPending: true,
    doctorFeedReason: DOCTOR_FEED_REASON,
    refetch: sys.refetch,
  }
}
