// hal0 Overview v2 — Needs you: the pure merge of every attention source into one ordered inbox,
// plus the all-clear line. The component (needs-you.jsx) supplies the handlers; nothing here
// touches a hook.

import type { Diagnosis, NextStep } from '@/api/hooks/useDiagnoses'
import { actionForNextStep } from '../diagnostics/next-step-actions'

export type InboxTone = 'accent' | 'warn' | 'err' | 'info'

/** One row of `useNotifications().actionableItems`. */
export interface NotificationItem {
  key: string
  kind?: string
  tone: 'accent' | 'warn' | 'err'
  eyebrow: string
  body: unknown
  actions: Array<{ label: string; onClick: () => void; primary?: boolean; disabled?: boolean }>
}

export interface InboxAction {
  label: string
  primary?: boolean
  danger?: boolean
  disabled?: boolean
  run: () => void
}

export interface InboxItem {
  key: string
  tone: InboxTone
  eyebrow: string
  body: unknown
  actions: InboxAction[]
}

/** The slice of a Diagnosis the inbox reads; confidence and evidence stay with the doctor page. */
export type DiagnosisRow = Pick<Diagnosis, 'id' | 'severity' | 'summary' | 'next_steps'> & Partial<Diagnosis>

export interface AttentionHandlers {
  runStep?: (step: NextStep) => void
  restartDrift?: () => void
  openDoctor?: () => void
}

export interface AttentionInput {
  notifications: NotificationItem[]
  diagnoses: DiagnosisRow[]
  failing: string[]
  drift: { count: number; slots: string[] } | null
  handlers?: AttentionHandlers
}

const TONE_RANK: Record<InboxTone, number> = { err: 0, warn: 1, accent: 2, info: 3 }
const noop = () => {}

function diagnosisItem(d: DiagnosisRow, h: AttentionHandlers): InboxItem | null {
  if (d.severity === 'info') return null
  const actions: InboxAction[] = d.next_steps
    .filter((s) => s.kind === 'command' && actionForNextStep(s) !== null)
    .map((s) => ({ label: s.label, run: () => (h.runStep ?? noop)(s) }))
  if (actions.length === 0) actions.push({ label: 'open doctor', run: h.openDoctor ?? noop })
  return {
    key: `doctor:${d.id}`,
    tone: d.severity === 'warn' ? 'warn' : 'err',
    eyebrow: `doctor · ${d.id}`,
    body: d.detail ? `${d.summary} — ${d.detail}` : d.summary,
    actions,
  }
}

function driftItem(drift: { count: number; slots: string[] }, h: AttentionHandlers): InboxItem {
  const names = drift.slots.filter(Boolean).join(', ')
  return {
    key: 'drift',
    tone: 'warn',
    eyebrow: 'update · slots need restart',
    body:
      `${drift.count} slot${drift.count === 1 ? ' needs' : 's need'} restart` +
      (names ? ` — ${names}` : '') +
      ', still running the pre-update launch command. Restarting briefly interrupts any in-flight request on those slots.',
    actions: [{ label: 'restart drifted slots', run: h.restartDrift ?? noop }],
  }
}

/** Merge notifications, doctor diagnoses, failing health checks and slot drift into one inbox. */
export function mergeAttention(input: AttentionInput): InboxItem[] {
  const h = input.handlers ?? {}
  const hasDrift = !!input.drift && input.drift.count > 0
  const items: InboxItem[] = []

  for (const n of input.notifications) {
    // The notification source carries its own drift row; the dedicated drift item replaces it.
    if (hasDrift && (n.key === 'update:drift' || n.kind === 'drift')) continue
    items.push({
      key: n.key,
      tone: n.tone,
      eyebrow: n.eyebrow,
      body: n.body,
      actions: n.actions.map((a) => ({ label: a.label, primary: a.primary, disabled: a.disabled, run: a.onClick })),
    })
  }
  for (const d of input.diagnoses) {
    const it = diagnosisItem(d, h)
    if (it) items.push(it)
  }
  for (const f of input.failing) {
    const name = f.split(':')[0].trim()
    items.push({
      key: `health:${name}`,
      tone: 'err',
      eyebrow: 'health check',
      body: f,
      actions: [{ label: 'open doctor', run: h.openDoctor ?? noop }],
    })
  }
  if (hasDrift && input.drift) items.push(driftItem(input.drift, h))

  // Array.prototype.sort is stable: sources keep their relative order inside a tone.
  return items.sort((a, b) => TONE_RANK[a.tone] - TONE_RANK[b.tone])
}

export interface AllClearInput {
  slotsHealthy: [number, number]
  diskFreeMb: number | null
  channel: string | null
  available: string | null
  checkedAt: Date
}

const pad2 = (n: number) => String(n).padStart(2, '0')

export function allClearLine(i: AllClearInput): string {
  const disk =
    typeof i.diskFreeMb === 'number' && Number.isFinite(i.diskFreeMb)
      ? `${Math.round(i.diskFreeMb / 1000)} GB`
      : '—'
  const parts = ['All clear', 'doctor ok']
  // No slot list yet (still loading, or none configured): say nothing rather than "0/0".
  if (i.slotsHealthy[1] > 0) parts.push(`${i.slotsHealthy[0]}/${i.slotsHealthy[1]} slots healthy`)
  parts.push(`disk ${disk} free`)
  if (i.channel) parts.push(i.available ? `${i.available} available on ${i.channel}` : `up to date on ${i.channel}`)
  parts.push(`checked ${pad2(i.checkedAt.getHours())}:${pad2(i.checkedAt.getMinutes())}`)
  return parts.join(' · ')
}
