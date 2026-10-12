// MODELS ▸ Model Defaults — per-model FILE FACTS (chat template) plus the
// arch/runner resolution reference. ADR-0029: a model carries facts about its
// file only. Every tune — context window, flags, thinking, vision, MTP on/off,
// GPU layers — lives on a profile (Profiles page) and is picked by the slot,
// so this page no longer edits a context size or GPU layers.
//
// The chat template is stored on the model registry row (ModelDefaults,
// registry/model.py) — edited via PUT /api/models/{id}, NOT /api/settings. A
// change is written to the registry immediately but is observed only when a
// slot serving the model is (re)started (the launch argv is resolved at slot
// start) — hence the "⟳ restart slots" badge, which resolves through the ONE
// reload-class source (data/reloadClass.js → `model.defaults.*` fallback),
// not a hand-rolled chip.
//
// Per-model editing is the SAME contract the model drawer uses
// (useModelUpdate → PUT /api/models/{id} with a `defaults` object): both
// surfaces converge on one hook + one backend writer.
import { useState, useEffect } from 'react'
import { useModels, useModelUpdate } from '@/api/hooks/useModels'
import { useChatTemplates } from '@/api/hooks/useChatTemplates'
import { useMetaEnums } from '@/api/hooks/useMeta'
import { ApplyBadge } from '../../shared/ApplyBadge.jsx'
import { SRow } from '../../shared/SRow.jsx'
import { _advInputStyle } from '../../shared/SchemaRow.jsx'

// The next `defaults` object. Facts only (ADR-0029): the server rejects any
// tuning key under `defaults` with a 422, so the body starts from the stored
// FACT keys alone — a stale tuning key on a row can never ride along. Since
// #1413 the registry merges `defaults` one level deep, so absent means "keep
// the stored value" and only `null` deletes: 'auto' sends `null`, never an
// omitted key.
const FACT_KEYS = ['chat_template', 'mtp', 'jinja', 'tokenizer_repo']
function buildDefaultsPatch(init, { chatTemplate }) {
  const defaults = Object.fromEntries(
    FACT_KEYS.filter(k => init[k] !== undefined).map(k => [k, init[k]]),
  )
  // 'auto' = GGUF-embedded template = absence of an override.
  defaults.chat_template = chatTemplate && chatTemplate !== 'auto' ? chatTemplate : null
  return defaults
}

function ModelDefaultsRow({ model, templates }) {
  const update = useModelUpdate()
  const init = model?.defaults || {}

  const origTpl = init.chat_template ?? 'auto'

  const [chatTemplate, setChatTemplate] = useState(origTpl)
  useEffect(() => {
    setChatTemplate(origTpl)
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [model?.id])

  const dirty = chatTemplate !== origTpl
  const canSave = dirty && !update.isPending

  const onSave = async () => {
    const defaults = buildDefaultsPatch(init, { chatTemplate })
    try {
      await update.mutateAsync({ id: model.id, body: { defaults } })
      window.__hal0Toast && window.__hal0Toast(`Defaults saved for ${model.longName || model.id} — restart its slot to apply`, 'warn')
    } catch (e) {
      window.__hal0Toast && window.__hal0Toast(`Save failed — ${e?.message || 'see logs'}`, 'err')
    }
  }

  const nativeCtx = Number(model?.metadata?.context_length) || null
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
          <ApplyBadge settingsKey="model.defaults.chat_template" />
          {dirty && (
            <button className="btn ghost sm" disabled={!canSave} onClick={onSave}>
              {update.isPending ? 'Saving…' : 'Save'}
            </button>
          )}
        </div>
      </div>
      <SRow
        k="Native context"
        sub="The window this file was trained for · read-only · a slot launches with its profile's ctx_size, falling back to this"
        mono
        v={<span style={{ color: 'var(--fg-3)' }} data-testid="model-defaults-native-ctx">{nativeCtx ? `${nativeCtx.toLocaleString('en-US')} tokens` : 'not recorded'}</span>}
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
        Per-model file facts — the chat template. These are stored on the model registry row and
        resolved into a slot's launch command when it starts, so a change takes effect on the next
        slot restart. The context window, flags, thinking, vision and MTP are not model settings:
        they belong to a profile (Profiles page), and the slot picks the profile. GPU layers are
        slot-owned hardware, set on the slot's hardware grid.
      </p>

      {/* ── Resolution reference (read-only meta) ────────────────────────── */}
      <div className="s-panel">
        <div className="s-row" style={{ paddingBottom: 4, borderBottom: '1px solid var(--line)' }}>
          <div className="k"><span>Defaults resolution</span><FieldInfoIcon description="how launch flags are chosen · ADR-0029" /></div>
        </div>
        <SRow
          k="Precedence"
          sub="Later wins — the profile's tune is the main layer"
          v={<span className="mono" style={{ color: 'var(--fg-3)', fontSize: 11 }}>base → model facts → profile → slot placement</span>}
        />
        <SRow k="Runtime families" sub="Arch/family default flags ship in the runner registry" mono v={<span style={{ color: 'var(--fg-3)' }}>{(enums.runtime_families || []).join(' · ') || '—'}</span>} />
        <SRow k="Chat templates" sub="Templates pinnable as a per-model default (/api/chat-templates)" mono v={<span style={{ color: 'var(--fg-3)' }}>{templates.length ? `${templates.length} available` : 'auto only'}</span>} />
      </div>

      {/* ── Per-model defaults ───────────────────────────────────────────── */}
      <div style={{ marginTop: 14, marginBottom: 8 }}>
        <span className="mono" style={{ fontSize: 11, color: 'var(--fg-4)' }}>Per-model file facts · stored on the registry row · <span style={{ color: 'var(--fg-3)' }}>PUT /api/models/&lt;id&gt;</span></span>
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
