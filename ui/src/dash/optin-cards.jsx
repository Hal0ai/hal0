// hal0 dashboard overhaul — W6: opt-in library cards
//
// Window-global module. Exports five cards onto window:
//   PowerCard       — Power & Thermal  (opt-in, source-pending gate on 404)
//   SlotTrackCard   — Per-Slot Throughput  (opt-in, per_slot from throughput history)
//   ApprovalsCard   — Agent Approvals   (opt-in, window bridge mutations)
//   AttentionCard   — Needs Attention   (DEFAULT-ON, derived from slots + approvals)
//   SchedulerCard   — Scheduler         (honest "no source" — lemond is stateless)
//
// Import order: after cards-shell.jsx (DCard/StatusDot on window), after
// services-card.jsx. Listed in main.tsx right after services-card.jsx import.
//
// NO stub data. All gated/honest-empty. Risk chip omitted (ApprovalEntry has
// no risk field). SSE wiring for approvals skipped — polled list hook is live.
// agentApprovalsStream constant added to endpoints.ts for completeness.

import { useStatsPower } from '@/api/hooks/useStatsPower'
import { useThroughputHistory } from '@/api/hooks/useThroughputHistory'
import { useSlots } from '@/api/hooks/useSlots'

// React globals installed by globals-install before this module evaluates.
const { useState, useCallback } = React;

// ─── helpers ─────────────────────────────────────────────────────────────────

function fmt1(n) {
  return typeof n === 'number' ? n.toFixed(1) : '—'
}

function fmtInt(n) {
  return typeof n === 'number' ? Math.round(n).toString() : '—'
}

// ─── PowerCard ───────────────────────────────────────────────────────────────
// Source: useStatsPower → GET /api/stats/power (new endpoint).
// Fail-soft: 404 → isPending → gated "source pending" body.
// NO fan row (fanless box). NO vs-cap bar (no cap field exposed).

function PowerCard() {
  const { data, isPending } = useStatsPower()

  if (isPending) {
    return (
      <DCard title="POWER &amp; THERMAL">
        <div className="mc-pending">
          <span className="mc-pending-label">source pending</span>
          <span className="mc-pending-sub">waiting for /api/stats/power</span>
        </div>
      </DCard>
    )
  }

  const gpuW   = data?.gpu_power_w  ?? null
  const gpuT   = data?.gpu_temp_c   ?? null
  const cpuT   = data?.cpu_temp_c   ?? null
  const sclk   = data?.gpu_sclk_mhz ?? null

  return (
    <DCard title="POWER &amp; THERMAL">
      <div className="pw-rows">
        {/* GPU power — big-ish number */}
        <div className="pw-hero-row">
          <span className="pw-hero-num mono">{fmt1(gpuW)}</span>
          <span className="pw-hero-unit">W</span>
          <span className="pw-hero-label mono">GPU power</span>
        </div>

        {/* Temps */}
        <div className="pw-metric-row">
          <span className="pw-label mono">GPU temp</span>
          <span className="pw-value mono">{fmt1(gpuT)}<span className="pw-unit"> °C</span></span>
        </div>
        <div className="pw-metric-row">
          <span className="pw-label mono">CPU temp</span>
          <span className="pw-value mono">{fmt1(cpuT)}<span className="pw-unit"> °C</span></span>
        </div>

        {/* Clock */}
        <div className="pw-metric-row">
          <span className="pw-label mono">GPU sclk</span>
          <span className="pw-value mono">{fmtInt(sclk)}<span className="pw-unit"> MHz</span></span>
        </div>
      </div>
    </DCard>
  )
}

// ─── SlotTrackCard ───────────────────────────────────────────────────────────
// Source: useThroughputHistory().per_slot (Record<slotName, number[]>).
// StatusDot: looks up the slot from useSlots() by name for real state.
// TTFT: from slot.metrics.ttft (em-dash if absent).
// If per_slot empty/pending → gated "source pending".

function SlotTrackCard() {
  const { data: history, isPending: histPending } = useThroughputHistory()
  const slotsQuery = useSlots()
  const slots = slotsQuery.data ?? []

  const perSlot = history?.per_slot ?? null

  // isPending when history itself is pending OR per_slot absent/empty
  const isPending =
    histPending ||
    perSlot == null ||
    Object.keys(perSlot).length === 0

  if (isPending) {
    return (
      <DCard title="PER-SLOT THROUGHPUT">
        <div className="mc-pending">
          <span className="mc-pending-label">source pending</span>
          <span className="mc-pending-sub">waiting for per_slot throughput data</span>
        </div>
      </DCard>
    )
  }

  // Build slot lookup by name
  const slotByName = {}
  for (const s of slots) slotByName[s.name] = s

  const entries = Object.entries(perSlot)

  return (
    <DCard title="PER-SLOT THROUGHPUT">
      <div className="st-rows">
        {entries.map(([name, samples]) => {
          const slot = slotByName[name] ?? null
          const latest = samples.length > 0 ? samples[samples.length - 1] : null
          const toks = typeof latest === 'number' ? latest : null
          const ttft = slot?.metrics?.ttft ?? null

          // Mini spark: last 10 samples
          const spark = Array.isArray(samples) ? samples.slice(-10) : []
          const maxV = spark.length > 0 ? Math.max(...spark, 1) : 1

          return (
            <div key={name} className="st-row">
              {/* StatusDot: real slot obj if found, else offline */}
              <StatusDot slot={slot ?? undefined} phase={slot ? undefined : 'offline'} size={7} />

              <span className="st-name mono">{name}</span>

              {/* Mini spark */}
              <span className="mc-spark st-spark">
                {spark.map((v, i) => (
                  <i
                    key={i}
                    className="mc-spark-bar"
                    style={{ height: `${Math.max((v / maxV) * 100, 2)}%` }}
                  />
                ))}
                {spark.length < 10 && Array.from({ length: 10 - spark.length }).map((_, i) => (
                  <i key={`pad-${i}`} className="mc-spark-bar pad" style={{ height: '2%' }} />
                ))}
              </span>

              {/* tok/s — sodium-yellow accent per tokens */}
              <span className="st-toks mono" style={{ color: toks != null ? 'var(--yellow, var(--comfy, #E5B84F))' : 'var(--fg-5)' }}>
                {toks != null ? fmt1(toks) : '—'}
              </span>
              <span className="st-toks-unit mono">t/s</span>

              {/* TTFT */}
              <span className="st-ttft mono">
                {typeof ttft === 'number' ? `${Math.round(ttft * 1000)}ms` : '—'}
              </span>
            </div>
          )
        })}
      </div>
    </DCard>
  )
}

// ─── ApprovalsCard ───────────────────────────────────────────────────────────
// Source: window.__hal0UseApprovalList() — polled live list.
// Mutations: window.__hal0UseApproveApproval(), window.__hal0UseDenyApproval().
// NO risk chip (ApprovalEntry has no risk field — omitted, not faked).
// Empty list → honest "no pending approvals" (source IS live, just empty).
// SSE (agentApprovalsStream) not wired here — polled hook is sufficient.

function ApprovalsCard() {
  // Window bridges installed by chrome.jsx (lines 749-751).
  const useApprovalList   = window.__hal0UseApprovalList
  const useApproveApproval = window.__hal0UseApproveApproval
  const useDenyApproval    = window.__hal0UseDenyApproval

  // Guards — hooks must be functions before calling them (hooks rule: always call).
  // These are always set by chrome.jsx which loads before this card renders,
  // but we gate the render rather than the hook call to respect rules-of-hooks.
  if (typeof useApprovalList !== 'function') {
    return (
      <DCard title="AGENT APPROVALS">
        <div className="mc-pending">
          <span className="mc-pending-label">source pending</span>
          <span className="mc-pending-sub">approval bridge not ready</span>
        </div>
      </DCard>
    )
  }

  return <ApprovalsCardInner
    useApprovalList={useApprovalList}
    useApproveApproval={useApproveApproval}
    useDenyApproval={useDenyApproval}
  />
}

function ApprovalsCardInner({ useApprovalList, useApproveApproval, useDenyApproval }) {
  const listQuery   = useApprovalList()
  const approveMut  = useApproveApproval ? useApproveApproval() : null
  const denyMut     = useDenyApproval    ? useDenyApproval()    : null

  const approvals = listQuery?.data?.approvals ?? []

  return (
    <DCard title="AGENT APPROVALS">
      {approvals.length === 0 ? (
        <div className="ap-empty mono">no pending approvals</div>
      ) : (
        <div className="ap-rows">
          {approvals.map((entry) => {
            const agentName = entry.client_id || 'hermes'
            // args: JSON.stringify, capped at 120 chars
            let argsStr = ''
            try {
              argsStr = JSON.stringify(entry.args ?? {})
              if (argsStr.length > 120) argsStr = argsStr.slice(0, 117) + '...'
            } catch {
              argsStr = '[unparseable]'
            }

            const onApprove = () => approveMut?.mutate(entry.id)
            const onDeny    = () => denyMut?.mutate(entry.id)

            return (
              <div key={entry.id} className="ap-row">
                <div className="ap-row-top">
                  <span className="ap-agent mono">{agentName}</span>
                  {/* NO risk chip — ApprovalEntry has no risk field */}
                  <span className="ap-tool mono">{entry.tool}</span>
                </div>
                <div className="ap-args mono">{argsStr}</div>
                <div className="ap-actions">
                  <button
                    className="ap-btn ap-btn-deny"
                    onClick={onDeny}
                    disabled={!denyMut}
                    title="Deny this approval request"
                  >Deny</button>
                  <button
                    className="ap-btn ap-btn-approve"
                    onClick={onApprove}
                    disabled={!approveMut}
                    title="Approve this approval request"
                  >Approve</button>
                </div>
              </div>
            )
          })}
        </div>
      )}
    </DCard>
  )
}

// ─── AttentionCard ───────────────────────────────────────────────────────────
// DEFAULT-ON card. Real-derived from useSlots() + approvals: slots in
// error/warming/starting/pulling + pending approvals count.
// Honest calm state "all systems steady" when nothing wrong + no approvals.

function AttentionCard() {
  const slotsQuery = useSlots()
  const slots = slotsQuery.data ?? []

  // Window bridge for approvals count (same bridge used by ApprovalsCard).
  const useApprovalList = window.__hal0UseApprovalList
  return <AttentionCardInner slots={slots} useApprovalList={useApprovalList} />
}

// Split to avoid conditional hook call — inner always calls the hook.
function AttentionCardInner({ slots, useApprovalList }) {
  // Always call hooks — we handle the missing-bridge case via null data.
  const listQuery = typeof useApprovalList === 'function' ? useApprovalList() : null
  const approvalCount = listQuery?.data?.approvals?.length ?? 0

  // Slots needing attention
  const errorSlots   = slots.filter(s => s.state === 'error')
  const warmingSlots = slots.filter(s =>
    s.state === 'warming' || s.state === 'starting' || s.state === 'pulling'
  )
  const attnSlots = [...errorSlots, ...warmingSlots]

  const hasAnything = attnSlots.length > 0 || approvalCount > 0

  return (
    <DCard title="NEEDS ATTENTION">
      {!hasAnything ? (
        <div className="attn-calm mono">all systems steady</div>
      ) : (
        <div className="attn-rows">
          {attnSlots.map(slot => (
            <div key={slot.name} className="attn-row">
              <StatusDot slot={slot} size={7} />
              <span className="attn-name mono">{slot.name}</span>
              <span className="attn-state mono">{slot.state}</span>
            </div>
          ))}
          {approvalCount > 0 && (
            <div className="attn-row attn-approvals">
              <span className="attn-dot-placeholder" />
              <span className="attn-name mono">approvals</span>
              <span className="attn-state mono" style={{ color: 'var(--warn, #E8B94E)' }}>
                {approvalCount} pending
              </span>
            </div>
          )}
        </div>
      )}
    </DCard>
  )
}

// ─── SchedulerCard ───────────────────────────────────────────────────────────
// STAYS GATED — lemond dispatcher is stateless; no real source.
// Honest "no source" state — distinct from "source pending" (coming-soon).
// NO fake stat tiles or dispatches ever.

function SchedulerCard() {
  return (
    <DCard title="SCHEDULER">
      <div className="sched-no-source">
        <span className="sched-no-source-label mono">no scheduler telemetry</span>
        <span className="sched-no-source-sub mono">
          lemond dispatcher is stateless — no in-flight/queue history exposed
        </span>
      </div>
    </DCard>
  )
}

// ─── window globals ───────────────────────────────────────────────────────────
Object.assign(window, {
  PowerCard,
  SlotTrackCard,
  ApprovalsCard,
  AttentionCard,
  SchedulerCard,
})
