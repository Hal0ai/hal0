// hal0 dashboard — unified notifications source.
//
// ONE hook, useNotifications(), is the single source of truth for everything
// that wants the operator's eye. Two surfaces render from it so their counts
// can never drift:
//   • the topbar bell (chrome.jsx) — shows every section, including transient
//     in-progress downloads (status the operator watches but can't act on);
//   • the dashboard "Needs attention" card (dashboard-redesign.jsx) — shows the
//     ACTIONABLE subset (`actionableItems`): approvals, error slots, failed
//     downloads, update-available, slot drift, and dev messages. In-progress
//     downloads are excluded there (they carry no action).
//
// The dev/system message store also lives here (moved from chrome.jsx) so a
// message published via window.hal0Notify({...}) or a `hal0:notify` event is
// captured even before either surface mounts.

import { useSlots } from '@/api/hooks/useSlots'
import { useModels, usePullsList, useClearPullJob, useModelUpdatesCheck, useModelUpdateAll } from '@/api/hooks/useModels'
import { useUpdateState, useSlotDrift, useRestartDriftedSlots } from '@/api/hooks/useUpdates'
import { useApprovalList } from '@/api/hooks/useAgents'
import { apiPost } from '@/api/client'
import { ENDPOINTS } from '@/api/endpoints'

const { useState: useSN, useEffect: useEN, useMemo: useMN } = React

// ─── dev/system message store (module-level; survives unmount) ───────────────
const NOTIF_LS_KEY = 'hal0:notif-dismissed'
function _loadDismissed() {
  try {
    const v = JSON.parse(localStorage.getItem(NOTIF_LS_KEY) || '[]')
    return Array.isArray(v) ? v : []
  } catch { return [] }
}
const _store = { msgs: [], dismissed: new Set(_loadDismissed()), listeners: new Set() }
function _emit() { _store.listeners.forEach((l) => l()) }

// Publish a message. A stable `id` makes it dismissible forever (localStorage),
// so "developer important messages" don't re-nag. Returns the id (or null when
// there's no title). Mirrors the pre-refactor chrome.jsx contract exactly.
export function hal0Notify(raw) {
  const d = raw || {}
  const title = String(d.title || d.msg || '').trim()
  if (!title) return null
  const id = String(d.id || ('msg-' + Date.now().toString(36) + Math.random().toString(36).slice(2, 6)))
  if (_store.dismissed.has(id)) return id
  if (_store.msgs.some((m) => m.id === id)) return id
  _store.msgs = [
    ..._store.msgs,
    {
      id,
      title,
      body: d.body ? String(d.body) : '',
      kind: ['info', 'warn', 'error', 'update'].includes(d.kind) ? d.kind : 'info',
      link: typeof d.link === 'string' ? d.link : '',
      ts: Date.now(),
    },
  ]
  _emit()
  return id
}

export function dismissNotifMessage(id) {
  _store.msgs = _store.msgs.filter((m) => m.id !== id)
  _store.dismissed.add(id)
  try {
    localStorage.setItem(NOTIF_LS_KEY, JSON.stringify([..._store.dismissed].slice(-100)))
  } catch { /* private mode — dismissal just won't persist */ }
  _emit()
}

if (typeof window !== 'undefined') {
  window.hal0Notify = hal0Notify
  window.addEventListener('hal0:notify', (e) => hal0Notify(e.detail))
}

export const NOTIF_KIND_HUE = {
  info: 'var(--info, var(--fg-3))',
  warn: 'var(--warn)',
  error: 'var(--err)',
  update: 'var(--accent)',
}

function useDevMessages() {
  const [, bump] = useSN(0)
  useEN(() => {
    const l = () => bump((t) => t + 1)
    _store.listeners.add(l)
    return () => _store.listeners.delete(l)
  }, [])
  return _store.msgs
}

function fmtWaiting(enqueuedAt) {
  if (typeof enqueuedAt !== 'number') return null
  const mins = Math.max(0, Math.round((Date.now() / 1000 - enqueuedAt) / 60))
  if (mins < 60) return `${mins} min`
  return `${Math.floor(mins / 60)} h ${mins % 60} min`
}

// ─── the shared hook ─────────────────────────────────────────────────────────
export function useNotifications() {
  const slots = useSlots().data ?? []
  const approvals = useApprovalList()?.data?.approvals ?? []
  const errorSlots = slots.filter((s) => s.state === 'error' || s.container_status === 'crashed')

  const pulls = usePullsList({ enabled: true })
  const jobs = Array.isArray(pulls.jobs) ? pulls.jobs : []
  const activePulls = jobs.filter((j) => j.state === 'queued' || j.state === 'running')
  const failedPulls = jobs.filter((j) => j.state === 'failed')
  const clearJob = useClearPullJob()

  const updates = useUpdateState()
  const hal0Ch = updates.data?.hal0
  const hasUpdate = !!(hal0Ch && hal0Ch.available && hal0Ch.available !== hal0Ch.current)
  const drift = useSlotDrift()
  const driftCount = drift.data?.count ?? 0
  const restartDrifted = useRestartDriftedSlots()

  const devMsgs = useDevMessages()

  // Model updates (HF) — probed app-level so the bell fires without the
  // Models page ever being opened. Derives LIVE from /api/models
  // update_available flags rather than a message store, so it self-clears
  // the moment updates land and never duplicates when the outdated set
  // changes (#1181 review follow-up).
  useModelUpdatesCheck()
  const models = useModels().data ?? []
  const updatableModels = models.filter((m) => m.installed && m.update_available)
  const updateAllModels = useModelUpdateAll()

  // Badge count — every actionable/transient signal (unchanged from the
  // pre-refactor bell so the badge number is stable).
  const count =
    approvals.length + errorSlots.length +
    activePulls.length + failedPulls.length +
    (hasUpdate ? 1 : 0) + (driftCount > 0 ? 1 : 0) +
    (updatableModels.length > 0 ? 1 : 0) +
    devMsgs.length

  const driftBusy = restartDrifted.isPending

  // Normalized ACTIONABLE items for the "Needs attention" card. Excludes
  // in-progress downloads (no action) — those live on the bell only. Shape
  // matches RDAttentionCard: { key, section, kind, tone, eyebrow, body, actions }.
  const actionableItems = useMN(() => {
    const items = []

    for (const a of approvals) {
      const agent = a.client_id || 'hermes'
      const waiting = fmtWaiting(a.enqueued_at)
      let arg = ''
      try {
        const vals = a.args ? Object.values(a.args).filter((v) => typeof v === 'string') : []
        arg = vals[0] || ''
      } catch { /* unparseable args → omit */ }
      items.push({
        key: `approval:${a.id}`,
        section: 'attention',
        kind: 'approval',
        tone: 'accent',
        eyebrow: 'agent · approval',
        body: (
          <>
            {agent} wants <span className="mono rd-attn-ident">{a.tool}</span>
            {arg && <> · <span className="mono rd-attn-ident">{arg}</span></>}.
            {waiting && <> Waiting {waiting}.</>}
          </>
        ),
        actions: [
          { label: 'Review', primary: true, onClick: () => window.dispatchEvent(new CustomEvent('hal0:open-approvals')) },
        ],
      })
    }

    for (const s of errorSlots) {
      const msg = s?.metadata?.message || s?.message || ''
      items.push({
        key: `slot:${s.name}`,
        section: 'attention',
        kind: 'error',
        tone: 'warn',
        eyebrow: 'slot · error',
        body: (
          <>
            <span className="mono rd-attn-ident">{s.name}</span> failed
            {msg ? <> — {msg}</> : ' — container crashed'}. Restart to recover.
          </>
        ),
        actions: [
          { label: 'Restart', onClick: () => window.dispatchEvent(new CustomEvent('hal0:slot-restart', { detail: { name: s.name } })) },
          { label: 'View slot', onClick: () => { window.location.hash = '#slots/' + s.name } },
        ],
      })
    }

    for (const j of failedPulls) {
      items.push({
        key: `dlf:${j.job_id || j.model_id}`,
        section: 'downloads',
        kind: 'download',
        tone: 'err',
        eyebrow: 'download · failed',
        body: (
          <>
            <span className="mono rd-attn-ident">{j.hf_repo || j.model_id}</span> download failed.
          </>
        ),
        actions: [
          { label: 'Retry', primary: true, onClick: () => apiPost(ENDPOINTS.modelPull(j.model_id)) },
          { label: 'Clear', onClick: () => clearJob.mutate(j.model_id) },
        ],
      })
    }

    if (hasUpdate) {
      items.push({
        key: 'update:hal0',
        section: 'updates',
        kind: 'update',
        tone: 'accent',
        eyebrow: 'update · available',
        body: (
          <>
            hal0 <span className="mono rd-attn-ident">{hal0Ch.available}</span> available
            {hal0Ch.current && <> (current {hal0Ch.current})</>}.
          </>
        ),
        actions: [
          { label: 'Update', primary: true, onClick: () => { window.location.hash = '#settings/updates' } },
        ],
      })
    }

    if (driftCount > 0) {
      items.push({
        key: 'update:drift',
        section: 'updates',
        kind: 'drift',
        tone: 'warn',
        eyebrow: 'update · slot drift',
        body: (
          <>
            {driftCount} slot{driftCount !== 1 ? 's' : ''} running a pre-update config — restart to apply.
          </>
        ),
        actions: [
          { label: driftBusy ? 'Restarting…' : 'Restart', disabled: driftBusy, onClick: () => restartDrifted.mutate(undefined) },
        ],
      })
    }

    for (const m of devMsgs) {
      const acts = []
      if (m.link) {
        acts.push({
          label: 'Open',
          primary: true,
          onClick: () => {
            if (/^#/.test(m.link)) { window.location.hash = m.link }
            else window.open(m.link, '_blank', 'noopener')
          },
        })
      }
      acts.push({ label: 'Dismiss', onClick: () => dismissNotifMessage(m.id) })
      items.push({
        key: `msg:${m.id}`,
        section: 'messages',
        kind: 'message',
        tone: m.kind === 'error' ? 'err' : m.kind === 'warn' ? 'warn' : 'accent',
        eyebrow: 'message',
        body: (
          <>
            <b>{m.title}</b>{m.body && <> — {m.body}</>}
          </>
        ),
        actions: acts,
      })
    }

    return items
  }, [approvals, errorSlots, failedPulls, hasUpdate, hal0Ch, driftCount, devMsgs, driftBusy, clearJob, restartDrifted])

  return {
    // raw derived values — the bell renders its existing per-section JSX from these
    approvals, errorSlots, activePulls, failedPulls,
    hasUpdate, hal0Ch, driftCount,
    updatableModels, updateAllModels,
    devMsgs, clearJob, restartDrifted,
    count,
    // normalized actionable subset — the dashboard card renders these
    actionableItems,
  }
}
