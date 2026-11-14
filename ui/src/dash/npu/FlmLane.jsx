// The FLM lane (Slots → NPU → Lanes): one anchor slot process serves chat + STT + embed.
// Toggles and the chat model are STAGED in `draft` (owned by NpuTabs) and applied together:
// one config write, one restart. Two guards:
//   · the anchor's [npu]/[model] changing elsewhere (another tab, the drawer) drops the draft
//     with a toast, so a stale draft never overwrites newer config;
//   · a staged chat model that is not installed is downloaded first; if that fails nothing is
//     written or restarted, the draft stays and the error shows on the lane.
import { useCallback, useEffect, useMemo, useRef, useState } from 'react'
import { useFlmCatalog } from '@/api/hooks/useFlmCatalog'
import { usePullJob } from '@/api/hooks/useModels'
import { useSlotConfig, useSlotCreate, useSlotEdit, useSlotLoad, useSlotRestart, useSlotUnload } from '@/api/hooks/useSlots'
import { SlotControls, slotCtrlPhase } from '../inference-pane.jsx'
import { slotIndicatorFromPhase } from '../slot-status.js'
import { npuAnchorSlot } from '../slots/slot-shared.js'
import { slotEditHash } from '../slots-route'
import { capEnabled, cfgFingerprint, draftBody, draftCount, pickChatModel, savedFlm, toggleCap } from './flm-draft-pure'

const toast = (msg, kind = 'info') =>
  typeof window !== 'undefined' && window.__hal0Toast && window.__hal0Toast(msg, kind)

const CAPS = [
  { cap: 'chat', label: 'Chat' },
  { cap: 'asr', label: 'Speech-to-text', shadow: 'flm-stt', catCaps: ['stt', 'asr', 'transcription'] },
  { cap: 'embed', label: 'Embeddings', shadow: 'flm-embed', catCaps: ['embed', 'embedding'] },
]

function bundledModel(catalog, catCaps) {
  const row = (catalog?.models ?? []).find((m) => (m.capabilities ?? []).some((c) => catCaps.includes(c)))
  return row?.model ?? null
}

function describeDraft(draft, saved) {
  const out = []
  for (const { cap, label } of CAPS) {
    if (cap in draft) out.push(`${label} ${draft[cap] ? 'on' : 'off'}`)
  }
  if (draft.chatModel) out.push(`chat model ${draft.chatModel}`)
  return out.join(' · ') || ''
}

function CreateFlm({ slots }) {
  const create = useSlotCreate()
  const taken = new Set((slots ?? []).map((s) => s?.name))
  const name = taken.has('flm') ? 'npu' : 'flm'
  return (
    <div className="npt-lane npt-lane-flm" data-testid="flm-lane">
      <div className="npt-lane-ttl"><span className="npt-eng flm">FLM</span><span className="npt-sub">FastFlowLM</span></div>
      <p className="npt-hint">No FLM slot yet. One FLM process serves chat, speech-to-text and embeddings.</p>
      <button
        type="button"
        className="btn primary sm"
        data-testid="flm-create"
        disabled={create.isPending}
        onClick={() =>
          create.mutate(
            { name, type: 'llm', runtime: 'container', device: 'npu', profile: 'flm' },
            { onError: (e) => toast(e?.message || 'Could not create the FLM slot', 'warn') },
          )
        }
      >
        Create FLM slot
      </button>
    </div>
  )
}

export function FlmLane({ slots, draft, setDraft }) {
  const anchor = npuAnchorSlot(slots)
  if (!anchor) return <CreateFlm slots={slots} />
  return <FlmLaneBody anchor={anchor} draft={draft} setDraft={setDraft} />
}

function FlmLaneBody({ anchor, draft, setDraft }) {
  const cfgQ = useSlotConfig(anchor.name)
  const catQ = useFlmCatalog()
  const editMut = useSlotEdit()
  const restartMut = useSlotRestart()
  const loadMut = useSlotLoad()
  const unloadMut = useSlotUnload()
  const pull = usePullJob()

  const cfg = cfgQ.data
  const saved = useMemo(() => savedFlm(cfg, anchor), [cfg, anchor])
  const catalog = catQ.data
  const count = draftCount(draft)
  const [busy, setBusy] = useState(null) // null | 'pulling' | 'writing'
  const [err, setErr] = useState(null)

  // Review Focus 1: drop a draft the moment the anchor's saved config moves under it.
  const fp = cfgFingerprint(cfg)
  const fpAtStage = useRef(null)
  const applying = useRef(false)
  useEffect(() => {
    if (count === 0) {
      fpAtStage.current = null
      return
    }
    if (fpAtStage.current == null) {
      fpAtStage.current = fp
      return
    }
    if (fpAtStage.current !== fp && !applying.current) {
      fpAtStage.current = null
      setErr(null)
      setDraft({})
      toast('FLM settings changed elsewhere, staged changes dropped', 'warn')
    }
  }, [fp, count, setDraft])

  // Review Focus 2: wait for the download of a staged model before writing anything.
  const waiter = useRef(null)
  useEffect(() => {
    const w = waiter.current
    if (!w || pull.modelId !== w.tag) return
    if (pull.state === 'completed') {
      waiter.current = null
      w.resolve()
    } else if (pull.state === 'failed' || pull.state === 'cancelled') {
      waiter.current = null
      w.reject(new Error(pull.error?.message || `Download ${pull.state}`))
    }
  }, [pull.state, pull.modelId, pull.error])

  const pullAndWait = useCallback(
    (tag) =>
      new Promise((resolve, reject) => {
        waiter.current = { tag, resolve, reject }
        Promise.resolve(pull.start(tag)).catch((e) => {
          if (waiter.current?.tag === tag) {
            waiter.current = null
            reject(e instanceof Error ? e : new Error(String(e)))
          }
        })
      }),
    [pull],
  )

  const installed = (tag) => {
    const row = (catalog?.models ?? []).find((m) => m.model === tag)
    return !row || row.installed !== false
  }

  const apply = async () => {
    if (busy || count === 0) return
    setErr(null)
    applying.current = true
    try {
      if (draft.chatModel && !installed(draft.chatModel)) {
        setBusy('pulling')
        await pullAndWait(draft.chatModel)
      }
      setBusy('writing')
      await editMut.mutateAsync({ name: anchor.name, body: draftBody(draft, saved) })
      fpAtStage.current = null
      setDraft({})
      toast(`${anchor.name} FLM settings updated — restarting`, 'info')
      restartMut.mutate(anchor.name, {
        onError: (e) => toast(`NPU restart failed — ${e?.message || 'see logs'}`, 'warn'),
      })
    } catch (e) {
      setErr(e?.message || 'Apply failed')
    } finally {
      applying.current = false
      setBusy(null)
    }
  }

  const ind = slotIndicatorFromPhase(anchor)
  const up = ['serving', 'ready', 'idle', 'loaded', 'warming', 'stale'].includes(ind.cls)
  const m = anchor.metrics || {}
  const tps = typeof m.toks === 'number' && m.toks > 0 ? Math.round(m.toks) : null
  const ttft = typeof m.ttft === 'number' && m.ttft > 0 ? Math.round(m.ttft) : null
  const mem = typeof m.mem === 'number' ? Math.round(m.mem * 10) / 10 : null
  const chatModel = draft.chatModel ?? saved.chatModel
  const capOn = (cap) => (cap in draft ? draft[cap] : saved[cap])
  const chatRows = (catalog?.models ?? []).filter((r) => !r.capabilities || r.capabilities.includes('chat'))
  const options = chatRows.some((r) => r.model === chatModel) || !chatModel ? chatRows : [{ model: chatModel, installed: true }, ...chatRows]
  const stagedKeys = (k) => k in draft

  return (
    <div className="npt-lane npt-lane-flm" data-testid="flm-lane">
      <div className="npt-lane-h">
        <div className="npt-lane-ttl">
          <span className="npt-eng flm">FLM</span>
          <span className="npt-sub">FastFlowLM · one process{anchor.port ? ` · :${anchor.port}` : ''}</span>
        </div>
        <span className={`ldot ${ind.cls}`} />
        <span className="npt-state" data-testid="flm-state">{ind.label ?? ind.cls}</span>
        <SlotControls
          phase={slotCtrlPhase(anchor)}
          onStart={() => loadMut.mutate(anchor.name)}
          onStop={() => unloadMut.mutate(anchor.name)}
          onRestart={() => restartMut.mutate(anchor.name)}
          onLogs={() => window.dispatchEvent(new CustomEvent('hal0:slot-logs', { detail: { name: anchor.name } }))}
          onEdit={() => {
            window.location.hash = slotEditHash(anchor.name)
          }}
        />
      </div>
      <div className="npt-hint">One FLM process serves chat, speech-to-text and embeddings together. Changes are staged and applied with one restart.</div>

      <div className="npt-caps">
        {CAPS.map(({ cap, label, shadow, catCaps }) => {
          const enabled = capEnabled(saved, draft, cap)
          const on = capOn(cap) && enabled
          return (
            <div className="npt-cap" key={cap} data-testid={`flm-row-${cap}`}>
              <button
                type="button"
                role="switch"
                aria-checked={on}
                aria-label={label}
                className={`npt-toggle${on ? ' on' : ''}${stagedKeys(cap) ? ' pending' : ''}`}
                data-testid={`flm-cap-${cap}`}
                disabled={!!busy || !enabled}
                title={enabled ? undefined : 'Pick a chat model first'}
                onClick={() => setDraft((d) => toggleCap(d, saved, cap))}
              />
              <span className="npt-cap-nm">
                {label}
                {shadow ? <small>slot {shadow}</small> : null}
              </span>
              {cap === 'chat' ? (
                <select
                  className="npt-sel"
                  data-testid="flm-chat-model"
                  aria-label="FLM chat model"
                  value={chatModel ?? ''}
                  disabled={!!busy || !capOn('chat')}
                  onChange={(e) => e.target.value && setDraft((d) => pickChatModel(d, saved, e.target.value))}
                >
                  {!chatModel && <option value="">No model</option>}
                  {options.map((r) => (
                    <option key={r.model} value={r.model}>
                      {r.model}
                      {r.installed === false ? ' — download' : ''}
                    </option>
                  ))}
                </select>
              ) : (
                <span className="npt-bundled">
                  {bundledModel(catalog, catCaps) ?? '—'} <em>· bundled with FLM</em>
                </span>
              )}
              <span className="npt-mx">
                {cap === 'chat' && up && on && tps != null ? (
                  <>
                    <b>{tps}</b> t/s{ttft != null ? ` · ${ttft} ms` : ''}
                  </>
                ) : (
                  '—'
                )}
              </span>
            </div>
          )
        })}
      </div>

      {count > 0 && (
        <div className="npt-applybar" data-testid="flm-applybar">
          <span className="t">
            <b>
              {count} change{count > 1 ? 's' : ''} staged.
            </b>{' '}
            {describeDraft(draft, saved)}. Applying restarts {anchor.name}
            {up ? ' and drops in-flight requests' : ''}.
            {busy === 'pulling' && (
              <span data-testid="flm-pulling"> Downloading {draft.chatModel}{pull.pct != null ? ` · ${Math.round(pull.pct)}%` : ''}…</span>
            )}
          </span>
          <button type="button" className="btn ghost sm" data-testid="flm-discard" disabled={!!busy} onClick={() => { setErr(null); setDraft({}) }}>
            Discard
          </button>
          <button type="button" className="btn primary sm" data-testid="flm-apply" disabled={!!busy} onClick={apply}>
            {busy ? 'Applying…' : 'Apply and restart'}
          </button>
        </div>
      )}
      {err && (
        <div className="npt-error" role="alert" data-testid="flm-error">
          {err}
        </div>
      )}

      <dl className="npt-kv">
        <dt>Profile</dt>
        <dd>{cfg?.profile ? String(cfg.profile) : '—'}</dd>
        <dt>RAM</dt>
        <dd>{up && mem != null ? `${mem} GB` : '—'}</dd>
      </dl>
    </div>
  )
}
