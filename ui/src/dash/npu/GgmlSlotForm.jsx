// The GGML slot form: create and edit for an xdna slot (ggml-xdna llama-server on the NPU, ADR-0057).
// One editor for both entry points — the NPU tab's GGML lane drawer and the Slots page's edit drawer.
// The xdna knobs are per slot (`[xdna]`); context is `[tune].ctx_size`; everything else is placement.
import { useMemo, useRef, useState } from 'react'
import { useModels } from '@/api/hooks/useModels'
import { useProfiles } from '@/api/hooks/useProfiles'
import { useSlotConfig, useSlotCreate, useSlotDelete, useSlotEdit, useSlotLoad, useSlotRestart, useSlots } from '@/api/hooks/useSlots'
import { useXdnaFit } from '@/api/hooks/useXdnaFit'
import { useXdnaKnobs } from '@/api/hooks/useXdnaKnobs'
import './npu-tab.css'
import { slotButtonPhase } from '../slot-status.js'
import { compatibleModels } from '../slots/slot-shared.js'
import {
  GGML_CTX_PRESETS,
  GGML_REQUANT_CMD,
  ggmlCreateBody,
  ggmlCtxError,
  ggmlEditBody,
  ggmlNameError,
  ggmlProfileOptions,
  ggmlRequantHint,
  nextGgmlName,
} from './ggml-slot-pure'

const XDNA_SLOT = { binary: 'xdna', device: 'npu', device_class: 'npu' }
const toast = (msg, kind = 'info') => typeof window !== 'undefined' && window.__hal0Toast && window.__hal0Toast(msg, kind)
const fmt = (n) => (n == null ? '—' : Number(n).toLocaleString('en-US'))

function FitTag({ row }) {
  if (!row) return <span className="chip mono npt-fit none" title="no fit data">—</span>
  const word = row.xdna_path || '—'
  return (
    <span className={`chip mono npt-fit ${row.xdna_path || 'none'}`} title={row.reason || word} data-fit={row.xdna_path || 'none'}>
      {word}
    </span>
  )
}

/** The saved draft: what the slot's config says now (edit) or the create defaults. */
function savedDraft(mode, slot, cfg, names, initialModel) {
  if (mode === 'create') {
    return { name: nextGgmlName(names), model: initialModel || null, profile: 'xdna', ctxSize: null, pmode: null, env: {}, host_xrt: null, autoload: false }
  }
  const x = cfg?.xdna || {}
  return {
    name: slot.name,
    model: cfg?.model?.default || slot.model_id || slot.model || null,
    profile: cfg?.profile || slot.profile || 'xdna',
    ctxSize: cfg?.tune?.ctx_size ?? null,
    pmode: x.pmode ?? null,
    env: { ...(x.env || {}) },
    host_xrt: x.host_xrt ?? null,
    autoload: !!(cfg?.autoload ?? slot.autoload),
  }
}

function copyText(text, el) {
  const select = () => {
    if (el && window.getSelection) {
      const r = document.createRange()
      r.selectNodeContents(el)
      const sel = window.getSelection()
      sel.removeAllRanges()
      sel.addRange(r)
    }
  }
  try {
    const p = navigator.clipboard?.writeText(text)
    if (p && p.catch) p.catch(select)
    else if (!p) select()
  } catch {
    select()
  }
}

/** `variant='full'`: the whole create/edit form. `variant='engine'`: only the engine section
 *  (power mode, knobs, host_xrt, per-op hint) with its own save — the Slots drawer owns the rest. */
export function GgmlSlotForm({ mode, slot, initialModel, onDone, variant = 'full' }) {
  const engineOnly = variant === 'engine'
  const slotsQ = useSlots()
  const cfgQ = useSlotConfig(mode === 'edit' ? slot?.name : undefined)
  const modelsQ = useModels()
  const fitQ = useXdnaFit()
  const knobsQ = useXdnaKnobs()
  const profilesQ = useProfiles()
  const createMut = useSlotCreate()
  const editMut = useSlotEdit()
  const loadMut = useSlotLoad()
  const restartMut = useSlotRestart()
  const deleteMut = useSlotDelete()

  const names = useMemo(() => (slotsQ.data ?? []).map((s) => s?.name).filter(Boolean), [slotsQ.data])
  const saved = savedDraft(mode, slot, cfgQ.data, names, initialModel)
  const [patch, setPatch] = useState({})
  const d = { ...saved, ...patch }
  const set = (p) => setPatch((cur) => ({ ...cur, ...p }))
  const [err, setErr] = useState(null)
  const [busy, setBusy] = useState(false)
  const [confirmDel, setConfirmDel] = useState(false)
  const [customCtx, setCustomCtx] = useState('')
  const cmdRef = useRef(null)

  const models = useMemo(() => compatibleModels(modelsQ.data, { type: 'llm', slot: XDNA_SLOT }), [modelsQ.data])
  const fit = fitQ.data ?? {}
  const profileOpts = ggmlProfileOptions(profilesQ.data)
  const profileRow = (profilesQ.data ?? []).find((p) => p.name === d.profile)
  const pmodes = knobsQ.data?.pmodes ?? []
  const envRows = knobsQ.data?.env ?? []

  const nameErr = mode === 'create' ? ggmlNameError(d.name, names) : null
  const ctxErr = d.ctxSize != null ? ggmlCtxError(d.ctxSize) : null
  const xrtErr = d.host_xrt && !d.host_xrt.startsWith('/') ? 'host_xrt must be an absolute path' : null
  const blocker = engineOnly ? xrtErr : nameErr || (mode === 'create' && !d.model ? 'pick a model' : null) || ctxErr || xrtErr
  const editBody = mode === 'edit' ? ggmlEditBody(d, saved) : {}
  const dirty = engineOnly ? 'xdna' in editBody : Object.keys(editBody).length > 0
  const working = busy || createMut.isPending || editMut.isPending

  const run = async (fn) => {
    setErr(null)
    setBusy(true)
    try {
      await fn()
    } catch (e) {
      setErr(e?.message || 'Request failed')
    } finally {
      setBusy(false)
    }
  }
  const create = (start) =>
    run(async () => {
      await createMut.mutateAsync(ggmlCreateBody(d))
      if (start) {
        try {
          await loadMut.mutateAsync(d.name)
        } catch (e) {
          toast(`${d.name} created, but starting it failed — ${e?.message || 'see logs'}`, 'warn')
        }
      }
      onDone?.()
    })
  const save = (restart) =>
    run(async () => {
      if (dirty) await editMut.mutateAsync({ name: slot.name, body: editBody })
      if (restart) await restartMut.mutateAsync(slot.name)
      setPatch({})
      onDone?.()
    })
  // Engine variant: `{xdna}` only; restart when the slot is running (what SlotXdnaBlock did).
  const saveEngine = () =>
    run(async () => {
      await editMut.mutateAsync({ name: slot.name, body: { xdna: editBody.xdna } })
      setPatch({})
      if (slotButtonPhase(slot) !== 'off') {
        toast(`${slot.name} xdna settings updated — restarting`, 'info')
        try {
          await restartMut.mutateAsync(slot.name)
        } catch (e) {
          toast(`${slot.name} restart failed — ${e?.message || 'see logs'}`, 'warn')
        }
      }
      onDone?.()
    })
  const remove = () =>
    run(async () => {
      await deleteMut.mutateAsync(slot.name)
      onDone?.()
    })

  const setEnv = (name, value) => {
    const next = { ...d.env }
    if (value === '') delete next[name]
    else next[name] = value
    set({ env: next })
  }
  const fitRow = fit[d.model]
  const profileCtx = profileRow?.ctx_size
  const ctxPlaceholder = `${fmt(profileCtx)} from ${d.profile || 'profile'}`

  return (
    <div className="ggml-form" data-testid="ggml-slot-form" data-mode={mode}>
      {!engineOnly && (
      <>
      {mode === 'create' && (
        <section className="ggml-sec">
          <label className="ggml-lbl" htmlFor="ggml-name">Name</label>
          <input id="ggml-name" className={'pf-input mono' + (nameErr ? ' err' : '')} data-testid="ggml-name" value={d.name} onChange={(e) => set({ name: e.target.value.trim() })} />
          {nameErr && <div className="pf-msg err mono" role="alert" data-testid="ggml-name-error">{nameErr}</div>}
        </section>
      )}

      <section className="ggml-sec" data-testid="ggml-model-list">
        <span className="ggml-lbl">Model</span>
        <div className="ggml-models" role="listbox" aria-label="GGUF model">
          {models.length === 0 && <div className="npt-hint">No GGUF chat models installed.</div>}
          {models.map((m) => (
            <button key={m.id} type="button" role="option" aria-selected={d.model === m.id} className={'ggml-model' + (d.model === m.id ? ' on' : '')} data-testid={`ggml-model-${m.id}`} onClick={() => set({ model: m.id })}>
              <span className="mono">{m.id}</span>
              <FitTag row={fit[m.id]} />
            </button>
          ))}
        </div>
      </section>

      <section className="ggml-sec">
        <label className="ggml-lbl" htmlFor="ggml-profile">Profile</label>
        <select id="ggml-profile" className="pf-input" data-testid="ggml-profile" value={d.profile} onChange={(e) => set({ profile: e.target.value })}>
          {profileOpts.map((o) => (
            <option key={o.name} value={o.name} disabled={o.disabled} title={o.reason}>
              {o.name}{o.disabled ? ` — ${o.reason}` : ''}
            </option>
          ))}
        </select>
      </section>

      <section className="ggml-sec" data-testid="ggml-ctx-row">
        <span className="ggml-lbl">Context</span>
        <div className="tune-segs" role="group" aria-label="Context">
          <button type="button" className={'tune-seg' + (d.ctxSize == null ? ' on' : '')} aria-pressed={d.ctxSize == null} data-testid="ggml-ctx-profile" onClick={() => { set({ ctxSize: null }); setCustomCtx('') }}>profile</button>
          {GGML_CTX_PRESETS.map((n) => (
            <button key={n} type="button" className={'tune-seg' + (d.ctxSize === n ? ' on' : '')} aria-pressed={d.ctxSize === n} data-testid={`ggml-ctx-${n}`} onClick={() => { set({ ctxSize: n }); setCustomCtx('') }}>{fmt(n)}</button>
          ))}
          <input className={'pf-input mono' + (ctxErr ? ' err' : '')} inputMode="numeric" aria-label="Custom context" data-testid="ggml-ctx-custom" placeholder={ctxPlaceholder}
            value={customCtx || (d.ctxSize != null && !GGML_CTX_PRESETS.includes(d.ctxSize) ? String(d.ctxSize) : '')}
            onChange={(e) => { const v = e.target.value.replace(/[^0-9]/g, ''); setCustomCtx(v); set({ ctxSize: v === '' ? null : Number(v) }) }} />
        </div>
        {ctxErr && <div className="pf-msg err mono" role="alert" data-testid="ggml-ctx-error">{ctxErr}</div>}
        <div className="npt-hint">Hermes needs at least 64K</div>
      </section>
      </>
      )}

      <section className="ggml-sec" data-testid="ggml-pmode-row">
        <span className="ggml-lbl">Power mode</span>
        <div className="tune-segs" role="group" aria-label="Power mode">
          {pmodes.map((p) => (
            <button key={p} type="button" className={'tune-seg' + (d.pmode === p ? ' on' : '')} aria-pressed={d.pmode === p} data-testid={`ggml-pmode-${p}`} onClick={() => set({ pmode: d.pmode === p ? null : p })}>{p}</button>
          ))}
        </div>
        <div className="npt-hint">applied host-side</div>
      </section>

      <section className="ggml-sec" data-testid="ggml-env">
        <span className="ggml-lbl">GGML_XDNA_* knobs</span>
        {envRows.map((k) =>
          k.fixed ? (
            <div key={k.name} className="ggml-knob fixed" data-testid={`ggml-fixed-${k.name}`}>
              <span className="mono">{k.name}</span>
              <span className="mono npt-hint">{k.default ?? '—'} · pinned by hal0</span>
            </div>
          ) : (
            <div key={k.name} className="ggml-knob" data-testid={`ggml-env-${k.name}`}>
              <label className="mono" htmlFor={`ggml-env-in-${k.name}`}>{k.name}</label>
              <input id={`ggml-env-in-${k.name}`} className="pf-input mono" placeholder={k.default ?? ''} value={d.env[k.name] ?? ''} onChange={(e) => setEnv(k.name, e.target.value)} />
              {k.doc && <span className="npt-hint">{k.doc}</span>}
            </div>
          ),
        )}
      </section>


      {ggmlRequantHint(fitRow) && (
        <div className="ggml-requant" data-testid="ggml-requant-hint" role="note">
          <div className="npt-hint">This model runs per-op (slower) on the NPU: {fitRow.reason}. Requantizing ssm_out to Q5_K lets it take the fused path:</div>
          <div className="ggml-requant-cmd">
            <code className="mono" ref={cmdRef} data-testid="ggml-requant-cmd">{GGML_REQUANT_CMD}</code>
            <button type="button" className="btn ghost sm" data-testid="ggml-requant-copy" onClick={() => copyText(GGML_REQUANT_CMD, cmdRef.current)}>Copy</button>
          </div>
        </div>
      )}
      {fitRow?.xdna_path === 'per-op' && !ggmlRequantHint(fitRow) && (
        <div className="npt-hint" data-testid="ggml-perop-reason">Runs per-op (slower) on the NPU: {fitRow.reason}</div>
      )}

      <section className="ggml-sec" data-testid="ggml-host-xrt-row">
        <span className="ggml-lbl">host_xrt</span>
        <div className="ggml-knob">
          <label className="mono" htmlFor="ggml-host-xrt">host_xrt</label>
          <input id="ggml-host-xrt" className={'pf-input mono' + (xrtErr ? ' err' : '')} data-testid="ggml-host-xrt" placeholder={knobsQ.data?.host_xrt?.doc || '/opt/xilinx/xrt'} value={d.host_xrt ?? ''} onChange={(e) => set({ host_xrt: e.target.value === '' ? null : e.target.value })} />
          {xrtErr && <div className="pf-msg err mono" role="alert" data-testid="ggml-host-xrt-error">{xrtErr}</div>}
        </div>
      </section>

      {!engineOnly && (
      <section className="ggml-sec" data-testid="ggml-advanced">
        <span className="ggml-lbl">Advanced</span>
        <div className="ggml-knob"><span className="mono">port</span><span className="mono" data-testid="ggml-port">{mode === 'edit' ? slot?.port || '—' : 'assigned on create'}</span></div>
        <label className="ggml-knob"><input type="checkbox" data-testid="ggml-autoload" checked={d.autoload} onChange={(e) => set({ autoload: e.target.checked })} /><span>Start at boot (autoload)</span></label>
      </section>
      )}

      {engineOnly ? (
        <div className="ggml-foot" data-testid="ggml-foot">
          {err && <div className="pf-msg err mono" role="alert" data-testid="ggml-error">{err}</div>}
          <span className="npt-hint" data-testid="slot-xdna-note">Saved to this slot's config, applied on restart.</span>
          <span className="pf-grow" />
          <button type="button" className="btn ghost sm" data-testid="ggml-save-engine" disabled={!dirty || !!blocker || working} onClick={saveEngine}>{working ? 'Saving…' : 'Save engine settings'}</button>
        </div>
      ) : (
        <>
      {err && <div className="pf-msg err mono" role="alert" data-testid="ggml-error">{err}</div>}
      {mode === 'create' && blocker && <div className="npt-hint" data-testid="ggml-blocker">{blocker}</div>}

      <footer className="ggml-foot" data-testid="ggml-foot">
        {mode === 'edit' &&
          (confirmDel ? (
            <span className="ggml-del">
              <span className="npt-hint">Delete {slot.name}?</span>
              <button type="button" className="btn danger sm" data-testid="ggml-delete-confirm" disabled={working} onClick={remove}>Delete slot</button>
              <button type="button" className="btn ghost sm" data-testid="ggml-delete-cancel" onClick={() => setConfirmDel(false)}>Keep</button>
            </span>
          ) : (
            <button type="button" className="btn danger sm" data-testid="ggml-delete" onClick={() => setConfirmDel(true)}>Delete</button>
          ))}
        <span className="pf-grow" />
        {mode === 'create' ? (
          <>
            <button type="button" className="btn ghost sm" data-testid="ggml-create" disabled={!!blocker || working} onClick={() => create(false)}>Create</button>
            <button type="button" className="btn primary sm" data-testid="ggml-create-start" disabled={!!blocker || working} onClick={() => create(true)}>Create and start</button>
          </>
        ) : (
          <>
            <button type="button" className="btn ghost sm" data-testid="ggml-save" disabled={!dirty || !!blocker || working} onClick={() => save(false)}>Save</button>
            <button type="button" className="btn primary sm" data-testid="ggml-save-restart" disabled={!dirty || !!blocker || working} onClick={() => save(true)}>Save and restart</button>
          </>
        )}
      </footer>
        </>
      )}
    </div>
  )
}
