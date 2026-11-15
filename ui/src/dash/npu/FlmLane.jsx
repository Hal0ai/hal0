// The FLM lane (Slots → NPU → Lanes): one FLM process serves chat, speech-to-text and embeddings,
// drawn as one card per role. The lane header starts, stops and restarts the process; each role
// card has its own on/off toggle, and the chat card picks the chat model.
//
// A toggle or a model pick applies at once (one config write, then one restart). While FLM runs,
// that restart pauses the other running roles, so a confirm names them first; turning off the last
// running role stops FLM. With FLM stopped nothing is interrupted: turning a role on starts FLM,
// anything else only saves. A model that is not installed downloads first; if the anchor's config
// changes while it downloads, nothing is written (another tab or the drawer got there first).
import { useCallback, useEffect, useMemo, useRef, useState } from 'react'
import { useFlmCatalog } from '@/api/hooks/useFlmCatalog'
import { usePullJob } from '@/api/hooks/useModels'
import { useSlotConfig, useSlotCreate, useSlotEdit, useSlotUnload } from '@/api/hooks/useSlots'
import { ConfirmDialog } from '../primitives.jsx'
import { SlotControls, slotCtrlPhase } from '../inference-pane.jsx'
import { slotIndicatorFromPhase } from '../slot-status.js'
import { npuAnchorSlot } from '../slots/slot-shared.js'
import { openSlotEditFromNpu } from '../slots-route'
import { FlmModelPicker } from './FlmModelPicker.jsx'
import { formatFlmSize } from './npu-models-pure'
import { useNpuConflictStart } from './useNpuConflictStart.jsx'
import {
  bundledFlmModel,
  capEnabled,
  cfgFingerprint,
  draftBody,
  flmRoleIndicator,
  planModelPick,
  planRoleToggle,
  savedFlm,
} from './flm-draft-pure'

const toast = (msg, kind = 'info') =>
  typeof window !== 'undefined' && window.__hal0Toast && window.__hal0Toast(msg, kind)

const DRIFT = 'flm-config-drift'
const AWAIT_CFG_MS = 10_000
// The non-chat roles, each served by the anchor's process and shown on its `-stt` / `-embed`
// shadow slot (src/hal0/slots/npu/trio.py).
const SHADOW_ROLES = [
  { cap: 'asr', suffix: 'stt', label: 'speech-to-text', route: '/v1/audio', catCaps: ['stt'], audio: true },
  { cap: 'embed', suffix: 'embed', label: 'embeddings', route: '/v1/embeddings', catCaps: ['embed'], audio: false },
]

function CreateFlm({ slots }) {
  const create = useSlotCreate()
  const taken = new Set((slots ?? []).map((s) => s?.name))
  // `npu` is a reserved Slots tab name, so the fallback is flm-npu (then flm-npu-2, ...).
  let name = 'flm'
  for (let i = 1; taken.has(name); i += 1) name = i === 1 ? 'flm-npu' : `flm-npu-${i}`
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

export function FlmLane({ slots }) {
  const anchor = npuAnchorSlot(slots)
  if (!anchor) return <CreateFlm slots={slots} />
  return <FlmLaneBody anchor={anchor} />
}

function RoleToggle({ cap, label, on, disabled, title, onToggle }) {
  return (
    <button
      type="button"
      role="switch"
      aria-checked={on}
      aria-label={`${label} role`}
      className={`npt-toggle${on ? ' on' : ''}`}
      data-testid={`flm-cap-${cap}`}
      disabled={disabled}
      title={title}
      onClick={onToggle}
    />
  )
}

function FlmLaneBody({ anchor }) {
  const cfgQ = useSlotConfig(anchor.name)
  const catQ = useFlmCatalog()
  const editMut = useSlotEdit()
  const start = useNpuConflictStart()
  const unloadMut = useSlotUnload()
  const pull = usePullJob()

  const cfg = cfgQ.data
  const cfgReady = cfg !== undefined
  const saved = useMemo(() => savedFlm(cfg, anchor), [cfg, anchor])
  const catalog = catQ.data
  const [busy, setBusy] = useState(null) // null | 'pulling' | 'writing'
  const [pullTag, setPullTag] = useState(null)
  const [err, setErr] = useState(null)
  const [ask, setAsk] = useState(null) // { plan, confirm } awaiting the operator
  // The config fingerprint as it was before our last write. The controls stay locked until the
  // refetched config differs from it, so the next change is planned against what was just saved.
  const [awaitFp, setAwaitFp] = useState(null)

  const fp = cfgFingerprint(cfg)
  const fpNow = useRef(fp)
  fpNow.current = fp
  const writing = useRef(false)
  const waiter = useRef(null)

  // A download in flight is abandoned when the anchor's config moves under it: writing the picked
  // model afterwards would overwrite the newer config.
  const fpAtPull = useRef(null)
  useEffect(() => {
    if (awaitFp == null) return undefined
    if (cfgReady && fp !== awaitFp) {
      setAwaitFp(null)
      return undefined
    }
    // A refetch that never lands (or lands unchanged) must not lock the lane for good.
    const t = setTimeout(() => setAwaitFp(null), AWAIT_CFG_MS)
    return () => clearTimeout(t)
  }, [awaitFp, fp, cfgReady])
  useEffect(() => {
    if (!cfgReady || !waiter.current || writing.current) return
    if (fpAtPull.current != null && fpAtPull.current !== fp) {
      const w = waiter.current
      waiter.current = null
      w.reject(new Error(DRIFT))
    }
  }, [fp, cfgReady])

  // Wait for the picked model's download before writing anything.
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

  const ind = slotIndicatorFromPhase(anchor)
  const up = ['serving', 'ready', 'idle', 'loaded', 'warming', 'stale'].includes(ind.cls)

  // One config write, then what the plan says to do with the process.
  const apply = async (plan) => {
    if (busy) return
    setErr(null)
    const tag = plan.next.chatModel
    try {
      if (tag && !installed(tag)) {
        setBusy('pulling')
        setPullTag(tag)
        fpAtPull.current = fpNow.current
        await pullAndWait(tag)
        if (fpAtPull.current !== fpNow.current) throw new Error(DRIFT)
      }
      setBusy('writing')
      writing.current = true
      const fpBefore = fpNow.current
      await editMut.mutateAsync({ name: anchor.name, body: draftBody(plan.next, saved) })
      setAwaitFp(fpBefore)
      if (plan.after === 'restart') {
        start.run(anchor.name, 'restart').catch((e) => toast(`FLM restart failed — ${e?.message || 'see logs'}`, 'warn'))
      } else if (plan.after === 'start') {
        start.run(anchor.name, 'load').catch((e) => toast(`FLM start failed — ${e?.message || 'see logs'}`, 'warn'))
      } else if (plan.after === 'stop') {
        unloadMut.mutate(anchor.name)
      } else if (!up) {
        toast(`${anchor.name}: saved — it takes effect when FLM next starts`, 'info')
      }
    } catch (e) {
      if (e?.message === DRIFT) toast('FLM settings changed elsewhere while the model downloaded, so nothing was applied', 'warn')
      else setErr(e?.message || 'Apply failed')
    } finally {
      writing.current = false
      fpAtPull.current = null
      setPullTag(null)
      setBusy(null)
    }
  }

  // A plan with a confirm waits for the operator; one without applies at once.
  const run = (plan) => {
    if (!plan) return
    if (plan.confirm) setAsk(plan)
    else apply(plan)
  }
  const confirmAsk = () => {
    const plan = ask
    setAsk(null)
    if (plan) apply(plan)
  }

  const startOrWarn = (kind) =>
    start.run(anchor.name, kind).catch((e) => toast(e?.message ? `${anchor.name}: ${e.message}` : `${anchor.name}: action failed`, 'warn'))
  const m = anchor.metrics || {}
  const tps = typeof m.toks === 'number' && m.toks > 0 ? Math.round(m.toks) : null
  const ttft = typeof m.ttft === 'number' && m.ttft > 0 ? Math.round(m.ttft) : null
  const mem = typeof m.mem === 'number' ? Math.round(m.mem * 10) / 10 : null
  const locked = !!busy || !cfgReady || awaitFp != null
  const toggle = (cap) => run(planRoleToggle(saved, cap, up))
  const pickChat = (id) => run(planModelPick(saved, id, installed(id), up))

  const chatInd = flmRoleIndicator(ind, saved.chat, 'chat')
  const profile = cfg?.profile ? String(cfg.profile) : 'flm'
  const roleNote = (on) => (!on ? 'off' : up ? 'served by the FLM process' : 'starts with FLM')

  return (
    <div className="npt-lane npt-lane-flm" data-testid="flm-lane">
      <div className="npt-lane-h">
        <div className="npt-lane-ttl">
          <span className="npt-eng flm">FLM</span>
          <span className="npt-sub">FastFlowLM · one process{anchor.port ? ` · :${anchor.port}` : ''}</span>
        </div>
        <span className={`sdot ${ind.cls}`} title={ind.tooltip} aria-label={ind.tooltip} />
        <span className="npt-state" data-testid="flm-state">{ind.label ?? ind.cls}</span>
        <SlotControls
          phase={slotCtrlPhase(anchor)}
          onStart={() => startOrWarn('load')}
          onStop={() => unloadMut.mutate(anchor.name)}
          onRestart={() => startOrWarn('restart')}
          onLogs={() => window.dispatchEvent(new CustomEvent('hal0:slot-logs', { detail: { name: anchor.name } }))}
          onEdit={() => openSlotEditFromNpu(anchor.name)}
        />
      </div>
      <div className="npt-hint">
        Each role switches on and off on its own card. FLM is one process, so a change restarts it and the other running roles pause for a few seconds.
      </div>

      <div className="npt-gcards">
        <div className={`npt-gcard${saved.chat ? '' : ' off'}`} data-testid="flm-row-chat">
          <div className="npt-ghead">
            <span className={`sdot ${chatInd.cls}`} title={chatInd.tooltip} aria-label={chatInd.tooltip} />
            <span className="npt-gname">{anchor.name}</span>
            <span className="npt-gport">chat</span>
            <span className="npt-gstate">{chatInd.label}</span>
            <span className="npt-gsp" />
            <RoleToggle cap="chat" label="chat" on={saved.chat} disabled={locked} onToggle={() => toggle('chat')} />
          </div>
          <div className="npt-gmodelrow">
            <FlmModelPicker
              models={catalog?.models}
              cap="chat"
              value={saved.chatModel ?? null}
              testid="flm-chat-model"
              ariaLabel="FLM chat model"
              // The write drops [model].default while chat is off, so a pick then would be lost.
              disabled={locked || !saved.chat}
              onChange={pickChat}
            />
          </div>
          {busy === 'pulling' && pullTag && (
            <div className="npt-flm-pull" data-testid="flm-pull-progress">
              <progress max={100} value={pull.pct != null ? Math.round(pull.pct) : undefined} aria-label={`Downloading ${pullTag}`} />
              <span data-testid="flm-pulling">
                Downloading {pullTag}
                {pull.pct != null ? ` · ${Math.round(pull.pct)}%` : '…'}
                {pull.total > 0 ? ` · ${formatFlmSize(pull.downloaded) ?? '0 MB'} of ${formatFlmSize(pull.total)}` : ''}
              </span>
            </div>
          )}
          <div className="npt-gchips">
            <span className="chip mono dev-npu">chat</span>
            <span className="chip mono">{profile}</span>
          </div>
          <div className="npt-gmx" data-testid="flm-mx-chat">
            {saved.chat && up ? (
              tps || ttft || mem ? (
                <>
                  {tps && <span><b>{tps}</b> t/s</span>}
                  {ttft && <span>{ttft} ms ttft</span>}
                  {mem && <span>{mem} GB</span>}
                </>
              ) : (
                '—'
              )
            ) : (
              roleNote(saved.chat)
            )}
          </div>
        </div>

        {SHADOW_ROLES.map((r) => {
          const enabled = capEnabled(saved, r.cap)
          // A role needs the chat model's process: with no model it reads off whatever [npu] says.
          const on = saved[r.cap] && enabled
          const ri = flmRoleIndicator(ind, on, r.cap)
          const model = bundledFlmModel(catalog, r.catCaps)
          return (
            <div className={`npt-gcard${on ? '' : ' off'}`} key={r.cap} data-testid={`flm-row-${r.cap}`}>
              <div className="npt-ghead">
                <span className={`sdot ${ri.cls}`} title={ri.tooltip} aria-label={ri.tooltip} />
                <span className="npt-gname">{`${anchor.name}-${r.suffix}`}</span>
                <span className="npt-gport">{r.label}</span>
                <span className="npt-gstate">{ri.label}</span>
                <span className="npt-gsp" />
                <RoleToggle
                  cap={r.cap}
                  label={r.label}
                  on={on}
                  disabled={locked || !enabled}
                  title={enabled ? undefined : 'Pick a chat model first'}
                  onToggle={() => toggle(r.cap)}
                />
              </div>
              <div className="npt-gmodel npt-bundled">
                {model ?? '—'} <em>· bundled with FLM</em>
              </div>
              <div className="npt-gchips">
                <span className="chip mono dev-npu">{r.label}</span>
                {r.audio && <span className="chip mono">audio</span>}
                <span className="chip mono">{r.route}</span>
              </div>
              <div className="npt-gmx">{roleNote(on)}</div>
            </div>
          )
        })}
      </div>

      {err && (
        <div className="npt-error" role="alert" data-testid="flm-error">
          {err}
        </div>
      )}
      {ask && (
        <ConfirmDialog
          open
          onCancel={() => setAsk(null)}
          onConfirm={confirmAsk}
          title={ask.confirm.title}
          message={<span data-testid="flm-confirm">{ask.confirm.body}</span>}
          confirmLabel={ask.confirm.confirmLabel}
          destructive={false}
          footerNote={ask.confirm.destructive ? 'In-flight requests on the paused roles fail.' : 'Nothing changes unless you confirm.'}
        />
      )}
      {start.dialog}
    </div>
  )
}
