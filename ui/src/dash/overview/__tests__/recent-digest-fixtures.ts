// Real CT105 `slot.state` records (2026-10-29), newest first, shared by the digest and card tests.
import type { ActivityRecord } from '@/api/hooks/useActivity'

let nextId = 100

function slotState(iso: string, from: string, to: string, extra: Record<string, unknown> = {}): ActivityRecord {
  const id = nextId--
  return {
    id,
    ts: `2026-10-29T${iso}+00:00`,
    kind: 'event',
    category: 'slot',
    action: 'slot.state',
    target: 'utility',
    actor: 'system',
    severity: 'info',
    outcome: null,
    message: `utility: ${from} → ${to}`,
    before: null,
    after: { slot: 'utility', from, to, ...extra },
    error: null,
    duration_ms: null,
    request_id: null,
  }
}

export const CT105_RECORDS: ActivityRecord[] = [
  slotState('15:08:25', 'serving', 'ready'),
  slotState('15:07:39', 'ready', 'serving'),
  slotState('15:07:38', 'serving', 'ready'),
  slotState('15:07:30', 'ready', 'serving'),
  slotState('15:07:29', 'serving', 'ready'),
  slotState('15:07:23', 'idle', 'serving'),
  slotState('08:32:48', 'ready', 'idle', { message: 'idle for 318s' }),
  slotState('08:27:30', 'serving', 'ready'),
]

/** 15:10 UTC on the fixture day — the records are minutes old, so times render as HH:MM. */
export const NOW_MS = Date.parse('2026-10-29T15:10:00+00:00')

export const REC: ActivityRecord = {
  id: 500,
  ts: '2026-10-29T15:09:00+00:00',
  kind: 'event',
  category: 'service',
  action: 'service.restart',
  target: 'OpenWebUI',
  actor: 'system',
  severity: 'info',
  outcome: null,
  message: 'restarted · unit active',
  before: null,
  after: null,
  error: null,
  duration_ms: null,
  request_id: null,
}

export const ERR_RECORD: ActivityRecord = {
  ...REC,
  id: 501,
  category: 'slot',
  action: 'slot.state',
  target: 'code',
  severity: 'error',
  message: 'failed to start · exit 137',
  after: { slot: 'code', from: 'loading', to: 'failed' },
}

/** Twelve unrelated, unfoldable records (distinct actions), newest first. */
export const MANY: ActivityRecord[] = Array.from({ length: 12 }, (_, i) => ({
  ...REC,
  id: 700 - i,
  action: `service.restart.${i}`,
  ts: new Date(NOW_MS - i * 60_000).toISOString(),
}))
