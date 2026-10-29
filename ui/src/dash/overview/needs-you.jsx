// hal0 Overview v2 — Needs you: one inbox for notifications, doctor diagnoses, failing health
// checks and post-update slot drift. Replaces the old "Needs attention" card and SlotDriftBanner.
// Window-global module (OvNeedsYou); the merge rules live in needs-you-pure.ts.

import { useNotifications } from '../notifications.jsx'
import { useDiagnoses } from '@/api/hooks/useDiagnoses'
import { useNextStepRunner } from '../diagnostics/useNextStepRunner.js'
import { useHealthSystem, failingChecks } from '@/api/hooks/useRuntime'
import { useSlotDrift, useRestartDriftedSlots, useUpdateState } from '@/api/hooks/useUpdates'
import { useSlots } from '@/api/hooks/useSlots'
import { slotIndicatorFromPhase } from '../slot-status.js'
import { mergeAttention, allClearLine } from './needs-you-pure'

const { useMemo } = React

function InboxRow({ item }) {
  return (
    <div className={'ov-att ' + item.tone}>
      <span className="ov-bar" />
      <div>
        <div className="ov-eye">{item.eyebrow}</div>
        <div className="ov-body">{item.body}</div>
      </div>
      <div className="ov-acts">
        {item.actions.map((a) => (
          <button
            key={a.label}
            className={'btn sm' + (a.primary ? ' primary' : '') + (a.danger ? ' danger' : '')}
            disabled={a.disabled}
            onClick={a.run}
          >
            {a.label}
          </button>
        ))}
      </div>
    </div>
  )
}

function OvNeedsYou({ onGo }) {
  const go = onGo || ((id) => { window.location.hash = '#' + id })
  const notifications = useNotifications().actionableItems
  const { diagnoses, isLoading, isError, doctorFeedPending } = useDiagnoses()
  const { run } = useNextStepRunner()
  const health = useHealthSystem()
  const driftQ = useSlotDrift()
  const restart = useRestartDriftedSlots()
  const updates = useUpdateState()
  const slotsQ = useSlots()

  const failing = failingChecks(health.data)
  const driftCount = driftQ.data?.count ?? 0
  const drift = driftCount > 0
    ? { count: driftCount, slots: (driftQ.data?.slots ?? []).map((s) => s.slot).filter(Boolean) }
    : null

  const openDoctor = () => go('settings/doctor')
  const items = mergeAttention({
    notifications,
    diagnoses,
    failing,
    drift,
    handlers: { runStep: run, restartDrift: () => restart.mutate(undefined), driftPending: restart.isPending, openDoctor },
  })

  // The clock only moves when a source refreshes, not on every render.
  const checkedAt = useMemo(() => new Date(), [diagnoses, health.data, driftQ.data])

  const doctorLink = <button className="ov-lnk" onClick={openDoctor}>Run doctor →</button>

  if (items.length === 0) {
    const slots = slotsQ.data ?? []
    const healthy = slots.filter((s) => slotIndicatorFromPhase(s).cls !== 'error').length
    const hal0 = updates.data?.hal0
    const line = allClearLine({
      doctorKnown: !isLoading && !isError && !doctorFeedPending,
      slotsHealthy: [healthy, slots.length],
      diskFreeMb: health.data?.checks?.disk_state?.free_mb ?? null,
      channel: hal0?.channel ?? null,
      available: hal0?.available && hal0.available !== hal0.current ? hal0.available : null,
      checkedAt,
    })
    const parts = line.split(' · ')
    return (
      <section className="ov-card ov-inbox">
        <div className="ov-inbox-clear">
          <b>{parts[0]}</b>
          <span>{parts.slice(1, -1).join(' · ')}</span>
          <span className="sp" />
          <span>{parts[parts.length - 1]}</span>
          {doctorLink}
        </div>
      </section>
    )
  }

  return (
    <OvCard
      title="Needs you"
      note={`${items.length} item${items.length === 1 ? '' : 's'}`}
      className="ov-inbox has"
      flush
      right={doctorLink}
    >
      {items.map((it) => <InboxRow key={it.key} item={it} />)}
    </OvCard>
  )
}

Object.assign(window, { OvNeedsYou })
