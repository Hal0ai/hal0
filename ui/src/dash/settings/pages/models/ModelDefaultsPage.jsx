// MODELS ▸ Model Defaults — per-model launch defaults (ctx / GPU layers /
// chat template) + the arch/runner resolution reference. Unblocked by ML-4
// (the runner-image registry + model-config taxonomy); slots into the MODELS
// group per the old SettingsNav TODO.
//
// spec (b) MODELS▸Defaults: "ctx/quant/per-arch/load-on-start/per-model opts =
// E reshaped by G[§7.1a/d]". These launch defaults live on the model registry
// row (ModelDefaults, registry/model.py) — edited via PUT /api/models/{id},
// NOT /api/settings. A change is written to the registry immediately but is
// observed only when a slot serving the model is (re)started (the launch argv
// is resolved at slot start, §7.1a) — hence the "⟳ restart slots" badge, which
// resolves through the ONE reload-class source (data/reloadClass.js →
// `model.defaults.*` fallback), not a hand-rolled chip.
//
// Per-model editing is intentionally the SAME contract the Models-page recipe
// editor uses (useModelUpdate → PUT /api/models/{id} with a whole-`defaults`
// object): both surfaces converge on one hook + one backend writer, so there's
// one owner of the write. This page is the settings-side, defaults-focused
// view of that surface.
import { useState, useEffect } from 'react'
import { useModels, useModelUpdate } from '@/api/hooks/useModels'
import { useChatTemplates } from '@/api/hooks/useChatTemplates'
import { useMetaEnums } from '@/api/hooks/useMeta'
import { ApplyBadge } from '../../shared/ApplyBadge.jsx'
import { SRow } from '../../shared/SRow.jsx'
import { _advInputStyle } from '../../shared/SchemaRow.jsx'

// Build the next `defaults` object, preserving any ModelDefaults key this page
// doesn't surface (mtp/jinja/profile/extra_args/…). The registry PUT flat-
// merges `defaults` WHOLESALE, so we must start from the stored defaults and
// only override the keys we render — mirrors the recipe editor exactly.
// Emptying an input clears just that one key (delete → "launcher default").
function buildDefaultsPatch(init, { ctx, ngl, chatTemplate }) {
  const defaults = { ...init }
  if (ctx.trim()) {
    const n = parseInt(ctx, 10)
    if (Number.isFinite(n)) defaults.context_size = n
    else delete defaults.context_size
  } else delete defaults.context_size
  if (ngl.trim()) {
    const n = parseInt(ngl, 10)
    if (Number.isFinite(n)) defaults.n_gpu_layers = n
    else delete defaults.n_gpu_layers
  } else delete defaults.n_gpu_layers
  // 'auto' = GGUF-embedded template = absence of an override.
  if (chatTemplate && chatTemplate !== 'auto') defaults.chat_template = chatTemplate
  else delete defaults.chat_template
  return defaults
}

function ModelDefaultsRow({ model, templates }) {
  const update = useModelUpdate()
  const init = model?.defaults || {}

  const origCtx = init.context_size != null ? String(init.context_size) : ''
  const origNgl = init.n_gpu_layers != null ? String(init.n_gpu_layers) : ''
  const origTpl = init.chat_template ?? 'auto'

  const [ctx, setCtx] = useState(origCtx)
  const [ngl, setNgl] = useState(origNgl)
  const [chatTemplate, setChatTemplate] = useState(origTpl)
  useEffect(() => {
    setCtx(origCtx)
    setNgl(origNgl)
    setChatTemplate(origTpl)
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [model?.id])

  const ctxValid = ctx.trim() === '' || /^\d+$/.test(ctx.trim())
  const nglValid = ngl.trim() === '' || /^-?\d+$/.test(ngl.trim())
  const dirty = ctx !== origCtx || ngl !== origNgl || chatTemplate !== origTpl
  const canSave = dirty && ctxValid && nglValid && !update.isPending

  const onSave = async () => {
    const defaults = buildDefaultsPatch(init, { ctx, ngl, chatTemplate })
    try {
      await update.mutateAsync({ id: model.id, body: { defaults } })
      window.__hal0Toast && window.__hal0Toast(`Defaults saved for ${model.longName || model.id} — restart its slot to apply`, 'warn')
    } catch (e) {
      window.__hal0Toast && window.__hal0Toast(`Save failed — ${e?.message || 'see logs'}`, 'err')
    }
  }

  const numStyle = (ok) => ({ ..._advInputStyle, width: 96, borderColor: ok ? 'var(--line)' : 'var(--err)' })
  const tplOptions = ['auto', ...(templates || []).map(t => t.id)]

  return (
    <div className="s-panel" style={{ marginBottom: 10 }}>
      <div className="s-row" style={{ paddingBottom: 4, borderBottom: '1px solid var(--line)' }}>
        <div className="k">
          <span className="mono">{model.longName || model.id}</span>
          <FieldInfoIcon
            description={`${model.architecture ? `arch: ${model.architecture}` : model.type || 'model'}${model.quant ? ` · ${model.quant}` : ''}${model.preferred_runner ? ` · runner: ${model.preferred_runner}` : ''}`}
          />
        </div>
        <div className="ac" style={{ display: 'inline-flex', alignItems: 'center', gap: 6 }}>
          <ApplyBadge settingsKey="model.defaults.context_size" />
          {dirty && (
            <button className="btn ghost sm" disabled={!canSave} onClick={onSave}>
              {update.isPending ? 'Saving…' : 'Save'}
            </button>
          )}
        </div>
      </div>
      <SRow
        k="Context size"
        sub="ctx tokens · empty = model/launcher default"
        v={<input className="mono" value={ctx} onChange={e => setCtx(e.target.value)} placeholder="auto" style={numStyle(ctxValid)} />}
      />
      <SRow
        k="GPU layers"
        sub="n_gpu_layers · -1 = all on GPU, 0 = CPU only, empty = default"
        v={<input className="mono" value={ngl} onChange={e => setNgl(e.target.value)} placeholder="auto" style={numStyle(nglValid)} />}
      />
      <SRow
        k="Chat template"
        sub="Pinned template · auto = use the model's embedded template"
        v={
          <select value={chatTemplate} onChange={e => setChatTemplate(e.target.value)} style={_advInputStyle}>
            {tplOptions.map(o => <option key={o} value={o}>{o}</option>)}
          </select>
        }
      />
    </div>
  )
}

export function ModelDefaultsPage() {
  const modelsQuery = useModels()
  const templatesQuery = useChatTemplates(true)
  const enums = useMetaEnums()

  const models = Array.isArray(modelsQuery.data) ? modelsQuery.data.filter(m => m.installed !== false) : []
  // The catalogue endpoint can settle to a non-array (older backend / mock
  // envelope); coerce so the `.map()` in the template picker can't throw.
  const templates = Array.isArray(templatesQuery.data) ? templatesQuery.data : []

  return (
    <div className="s-section">
      <h2>Model Defaults</h2>
      <p className="desc">
        Per-model launch defaults — context length, GPU layers, and chat template. These are stored on the
        model registry row and resolved into a slot's launch command when it starts, so a change takes
        effect on the next slot restart.
      </p>

      {/* ── Resolution reference (read-only meta) ────────────────────────── */}
      <div className="s-panel">
        <div className="s-row" style={{ paddingBottom: 4, borderBottom: '1px solid var(--line)' }}>
          <div className="k"><span>Defaults resolution</span><FieldInfoIcon description="how launch flags are chosen · REWORK §D" /></div>
        </div>
        <SRow
          k="Precedence"
          sub="Later wins — model defaults sit near the end, before slot overrides"
          v={<span className="mono" style={{ color: 'var(--fg-3)', fontSize: 11 }}>runner → profile → arch/family → model defaults → slot overrides</span>}
        />
        <SRow k="Runtime families" sub="Arch/family default flags ship in the runner registry" mono v={<span style={{ color: 'var(--fg-3)' }}>{(enums.runtime_families || []).join(' · ') || '—'}</span>} />
        <SRow k="Chat templates" sub="Templates pinnable as a per-model default (/api/chat-templates)" mono v={<span style={{ color: 'var(--fg-3)' }}>{templates.length ? `${templates.length} available` : 'auto only'}</span>} />
      </div>

      {/* ── Per-model defaults ───────────────────────────────────────────── */}
      <div style={{ marginTop: 14, marginBottom: 8 }}>
        <span className="mono" style={{ fontSize: 11, color: 'var(--fg-4)' }}>Per-model defaults · stored on the registry row · <span style={{ color: 'var(--fg-3)' }}>PUT /api/models/&lt;id&gt;</span></span>
      </div>

      {modelsQuery.isPending && <div style={{ padding: 12, color: 'var(--fg-4)', fontFamily: 'var(--jbm)', fontSize: 12 }}>Loading models…</div>}
      {modelsQuery.isError && <div className="err">{modelsQuery.error?.message || 'Failed to load models'}</div>}
      {!modelsQuery.isPending && !modelsQuery.isError && models.length === 0 && (
        <div style={{ padding: 12, color: 'var(--fg-4)', fontFamily: 'var(--jbm)', fontSize: 12 }}>
          No installed models. Pull a model from Library &amp; Downloads first.
        </div>
      )}
      {models.map(m => (
        <ModelDefaultsRow key={m.id} model={m} templates={templates} />
      ))}
    </div>
  )
}
