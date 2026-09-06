// INTEGRATIONS ▸ Agent Chat — the dashboard steward chat ([brain_chat]).
//
// Schema-driven (R5 data seam): enabled / read_only / max_rounds /
// completion_timeout_s render through the same AdvRow + useSettingsForm
// machinery AdvancedPage uses, so their copy and effect badges come from the
// server schema + apply-plan registry instead of hand-authored strings that
// can drift. `model` and `tool_model` stay custom RichSelect widgets — they
// need slot-aware option lists a generic AdvRow can't build — but both still
// read the SAME useSettingsForm buffer/dirty/patch/confirm-gate loop as the
// generic rows, so Save/Reset/manual-restart-confirm behave identically.
//
// tool_model (#2108): previously had no dashboard path at all — this page
// only ever saved enabled/read_only/model/max_rounds/completion_timeout_s.
// The picker below offers the shipped default (hal0/agent), every configured
// chat-capable slot, an explicit "disabled" choice, and — when `model` names
// a specific slot — a "same as chat model" preset. Each option chips whether
// its target slot currently has a model loaded, and GET /api/settings/fields'
// server-computed `live_target` drives a plain-language banner when the
// SAVED value has nowhere live to route a tool call (the fresh-install gap
// #2108 documents: the `agent` slot ships with no bound model).
import { useMemo, useState } from 'react'
import { useSettingsClient } from '../../data/settingsClient.js'
import { useSettingsForm } from '../../data/useSettingsForm.js'
import { useSettingsFields, useSettingsPreview } from '@/api/hooks/useSettings'
import { useSlots } from '@/api/hooks/useSlots'
import { Banner } from '../../../primitives.jsx'
import { RichSelect } from '@/dash/rich-select.jsx'
import { AdvRow, _getIn } from '../../shared/SchemaRow.jsx'
import { ApplyBadge } from '../../shared/ApplyBadge.jsx'
import { SRow } from '../../shared/SRow.jsx'
import { SettingsPreviewDrawer } from '../../shared/SettingsPreviewDrawer.jsx'

// Mirrors the canonical chains in hal0.normalize.resolver.DEFAULT_CHAINS —
// advisory only, for the picker's per-option "loaded" chip before a save.
// The authoritative answer for the SAVED value is the server's
// GET /api/settings/fields `live_target` (real resolver, real slot state).
const _CANONICAL_CHAINS = {
  'hal0/agent': ['agent'],
  'hal0/utility': ['utility', 'agent'],
  'hal0/npu': ['npu', 'utility', 'agent'],
  'hal0/brain': ['brain', 'agent'],
};

function _chainFor(value) {
  if (!value || !value.startsWith('hal0/')) return null;
  if (_CANONICAL_CHAINS[value]) return _CANONICAL_CHAINS[value];
  return [value.slice(5), 'agent'];
}

// First slot in the alias's fallback chain that has a model bound, or null.
function _resolvedSlotName(value, slots) {
  const chain = _chainFor(value);
  if (!chain) return null;
  const byName = new Map((slots || []).map(s => [s.name, s]));
  for (const name of chain) {
    const s = byName.get(name);
    if (s && s.model) return name;
  }
  return null;
}

function toolModelOptions(llmSlots, model) {
  const byName = new Map(llmSlots.map(s => [s.name, s]));
  const chip = (name) => {
    const s = byName.get(name);
    if (!s) return <span className="chip warn">not configured</span>;
    return s.model
      ? <span className="chip ok">{s.model}</span>
      : <span className="chip warn">no model loaded</span>;
  };
  const opts = [
    {
      id: 'hal0/agent',
      row: 'hal0/agent (default)',
      right: chip('agent'),
      desc: "The always-on anchor every fallback chain ends in. Ships with no model bound — pull one into the agent slot before relying on this default.",
    },
  ];
  if (model && model.trim() && model !== 'hal0/agent') {
    opts.push({
      id: model,
      row: `Same as chat model (${model})`,
      right: chip(_resolvedSlotName(model, llmSlots) || model.replace(/^hal0\//, '')),
      desc: "Route tool-calling rounds to whatever slot the chat model itself uses — no separate reroute target.",
    });
  }
  for (const s of llmSlots) {
    const virtual = `hal0/${s.name}`;
    if (virtual === 'hal0/agent' || virtual === model) continue;
    opts.push({
      id: virtual,
      row: virtual,
      right: s.model ? <span className="chip ok">{s.model}</span> : <span className="chip warn">no model loaded</span>,
      desc: s.model ? `Loaded: ${s.model}` : "Configured, but no model is currently loaded on this slot.",
    });
  }
  opts.push({
    id: 'off',
    row: 'Disabled',
    desc: "No reroute — a chat model that can't emit tool calls this runtime parses will say so instead of silently failing.",
  });
  return opts;
}

function modelOptions(llmSlots, liveModel) {
  const opts = [
    { id: '', row: 'Persona default (hal0/brain)', desc: "Falls back through hal0/brain → the agent slot." },
  ];
  for (const s of llmSlots) {
    opts.push({
      id: `hal0/${s.name}`,
      row: `hal0/${s.name}`,
      right: s.model ? <span className="chip ok">{s.model}</span> : <span className="chip warn">no model loaded</span>,
    });
  }
  // Keep a currently-set custom value selectable even if its slot isn't listed.
  if (liveModel && !opts.some(o => o.id === liveModel)) {
    opts.push({ id: liveModel, row: liveModel, desc: "Currently set; slot not in the live list." });
  }
  return opts;
}

export function AgentsBrainPage() {
  const client = useSettingsClient({ schema: true });
  const { settings, update, registry } = client;
  const slotsQuery = useSlots();
  const fieldsQuery = useSettingsFields();

  const live = settings.data?.brain_chat || {};
  const llmSlots = useMemo(
    () => (slotsQuery.data || []).filter(s => s.type === 'llm'),
    [slotsQuery.data],
  );

  const KEYS = [
    'brain_chat.enabled', 'brain_chat.read_only', 'brain_chat.model',
    'brain_chat.tool_model', 'brain_chat.max_rounds', 'brain_chat.completion_timeout_s',
  ];
  const form = useSettingsForm(client, KEYS);
  const { buf, set, fields, dirtyKeys, canSave } = form;
  const preview = useSettingsPreview();
  const [previewOpen, setPreviewOpen] = useState(false);
  const [previewData, setPreviewData] = useState(null);

  const modelValue = buf['brain_chat.model'] !== undefined ? buf['brain_chat.model'] : (live.model || '');
  const toolModelValue = buf['brain_chat.tool_model'] !== undefined
    ? buf['brain_chat.tool_model']
    : (live.tool_model || 'hal0/agent');

  const fieldsByPath = useMemo(() => {
    const out = {};
    for (const f of fieldsQuery.data?.fields || []) out[f.path] = f;
    return out;
  }, [fieldsQuery.data]);
  // #2108: only meaningful for the SAVED value (server resolves against real
  // slot state) — a dirty, unsaved edit shows the picker's own per-option
  // chip instead, so this only ever warns about what's live right now.
  const savedToolModelHasNoLiveTarget = fieldsByPath['brain_chat.tool_model']?.live_target === false
    && !dirtyKeys.includes('brain_chat.tool_model');

  // Preview before apply (#1967, #2195, #2203, #1511): the drawer renders
  // the exact ChangeSet POST /api/settings/preview computes for the pending
  // patch; Apply below re-sends that same patch through PUT /api/settings,
  // which computes the identical ChangeSet server-side — preview can't show
  // something apply wouldn't do, because it's the same function.
  const onSaveClick = async () => {
    try {
      const resp = await preview.mutateAsync(form.buildPatch());
      setPreviewData(resp.changeset);
      setPreviewOpen(true);
    } catch (e) {
      window.__hal0Toast && window.__hal0Toast(`Preview failed — ${e?.message || "see logs"}`, "err");
    }
  };

  const confirmApply = async () => {
    try {
      const { needsRestart } = await form.commit();
      setPreviewOpen(false);
      window.__hal0Toast && window.__hal0Toast(
        needsRestart ? "Saved — restart to apply the marked changes" : "Brain chat settings saved",
        needsRestart ? "warn" : "ok",
      );
    } catch (e) {
      window.__hal0Toast && window.__hal0Toast(`Save failed — ${e?.message || "see logs"}`, "err");
    }
  };

  const loading = settings.isPending || client.schema.isPending;

  return (
    <div className="s-section">
      <h2>Agent Chat</h2>
      <p className="desc">
        The dashboard's agent-chat steward (<span className="mono">hal0-brain</span>): the
        slide-out chat that administers this instance via tools. These guardrails hold
        server-side, independent of the persona's tool allowlist, and apply live.
      </p>

      {loading && <div style={{padding: 16, color: "var(--fg-4)", fontFamily: "var(--jbm)", fontSize: 12}}>Loading config schema…</div>}

      {!loading && (
        <div className="s-panel" style={{marginBottom: 12}}>
          <AdvRow dotKey="brain_chat.enabled" field={fields['brain_chat.enabled']}
            live={_getIn(live, 'enabled')} buf={buf['brain_chat.enabled']} onChange={set} registry={registry} label="Enabled" />
          <AdvRow dotKey="brain_chat.read_only" field={fields['brain_chat.read_only']}
            live={_getIn(live, 'read_only')} buf={buf['brain_chat.read_only']} onChange={set} registry={registry} label="Read-only" />

          <SRow
            k="Model / slot override"
            sub={fields['brain_chat.model']?.description}
            v={
              <RichSelect
                value={modelValue}
                options={modelOptions(llmSlots, live.model)}
                onChange={(id) => set('brain_chat.model', id)}
                aria-label="Chat model override"
                data-testid="brain-chat-model-select"
              />
            }
            actions={<ApplyBadge settingsKey="brain_chat.model" registry={registry} />}
          />

          <SRow
            k="Tool model"
            sub={fields['brain_chat.tool_model']?.description}
            v={
              <RichSelect
                value={toolModelValue}
                options={toolModelOptions(llmSlots, modelValue)}
                onChange={(id) => set('brain_chat.tool_model', id)}
                aria-label="Tool-routing model"
                data-testid="brain-chat-tool-model-select"
              />
            }
            actions={<ApplyBadge settingsKey="brain_chat.tool_model" registry={registry} />}
          />
          {savedToolModelHasNoLiveTarget && (
            <div style={{padding: "0 12px 8px"}}>
              <Banner
                kind="warn"
                body={
                  <span>
                    <span className="mono">{toolModelValue}</span> has no live target — tool-calling
                    turns will fail until a model is loaded on that slot, or you pick a loaded target above.
                  </span>
                }
              />
            </div>
          )}

          <AdvRow dotKey="brain_chat.max_rounds" field={fields['brain_chat.max_rounds']}
            live={_getIn(live, 'max_rounds')} buf={buf['brain_chat.max_rounds']} onChange={set} registry={registry} label="Max rounds" />
          <AdvRow dotKey="brain_chat.completion_timeout_s" field={fields['brain_chat.completion_timeout_s']}
            live={_getIn(live, 'completion_timeout_s')} buf={buf['brain_chat.completion_timeout_s']} onChange={set} registry={registry} label="Completion timeout (s)" />

          <div style={{display: "flex", justifyContent: "flex-end", gap: 8, padding: "8px 12px 4px"}}>
            {dirtyKeys.length > 0 && <button className="btn ghost sm" onClick={form.reset}>Reset</button>}
            <button className="btn sm" disabled={!canSave || preview.isPending} onClick={onSaveClick}>
              {preview.isPending ? "Checking…" : "Review & save"}
            </button>
          </div>
        </div>
      )}

      <SettingsPreviewDrawer
        open={previewOpen}
        onClose={() => setPreviewOpen(false)}
        changes={previewData?.changes}
        unknown={previewData?.unknown}
        onConfirm={confirmApply}
        confirming={update.isPending}
      />
    </div>
  );
}
