// DATA ▸ Memory — memory engine, graph extraction, reranker.
// Extracted verbatim from settings.jsx MemorySection + MemoryEnginePanel +
// MemoryGraphPanel + MemoryRerankerPanel
// (P3-ui split phase 1). The `id` stays "memory" (unchanged) so
// #settings/memory deep links keep working.
import { useState, useEffect } from 'react'
import { useSettingsClient } from '../../data/settingsClient.js'
import { useMemoryGraphStatus, useRetryFailedExtractions, useUpdateMemoryGraph } from '@/api/hooks/useMemory'
import { ApplyBadge } from '../../shared/ApplyBadge.jsx'
import { SRow } from '../../shared/SRow.jsx'
import { AdvRow, _schemaField, _getIn, _deepMergePatch, _advCoerce, _advInputStyle } from '../../shared/SchemaRow.jsx'

export function MemoryPage() {
  // R5 data seam: one typed client supplies the merged reload-class registry.
  const { registry } = useSettingsClient();

  return (
    <div className="s-section">
      <h2>Memory</h2>
      <p className="desc">
        Memory engine, graph extraction, and second-pass reranking. Changes to the engine
        require a hal0-api restart; graph and reranker knobs apply live.
      </p>
      <MemoryEnginePanel registry={registry} />
      <MemoryGraphPanel />
      <MemoryRerankerPanel registry={registry} />
    </div>
  );
}

// Overrides the (stale) schema description for memory.engine — page-scoped
// (was part of settings.jsx-wide ADV_DESC_OVERRIDE table).
const MEMORY_ENGINE_DESC_OVERRIDE =
  "Active memory engine, applied on the next hal0-api restart. hindsight is the durable default. " +
  "pgvector is an in-memory, NON-DURABLE fallback — existing memories are not migrated, won't be visible while selected, " +
  "and the memory admin/graph tooling below is unavailable under it.";

function MemoryEnginePanel({ registry }) {
  const { settings, update, schema: schemaQuery } = useSettingsClient({ schema: true });
  const schema = schemaQuery.data || null;
  const live = settings.data || null;

  const engineField = _schemaField(schema, "memory.engine");
  const currentEngine = live?.memory?.engine || "hindsight";
  const [engine, setEngine] = useState(currentEngine);
  useEffect(() => {
    if (live?.memory?.engine) setEngine(live.memory.engine);
  }, [live?.memory?.engine]);

  const dirty = engine !== currentEngine;

  const doSave = async () => {
    try {
      await update.mutateAsync({ memory: { engine } });
      setEngine(engine);
      window.__hal0Toast && window.__hal0Toast("Memory engine saved — restart hal0-api to apply", "warn");
    } catch (e) {
      window.__hal0Toast && window.__hal0Toast(`Save failed — ${e?.message || "see logs"}`, "err");
    }
  };

  const engineDesc = MEMORY_ENGINE_DESC_OVERRIDE || engineField?.description || "";
  const options = ["hindsight", "pgvector"];

  return (
    <div className="s-panel" style={{marginBottom: 12}}>
      <div className="s-row" style={{paddingBottom: 4, borderBottom: "1px solid var(--line)"}}>
        <div className="k"><span>Engine</span><FieldInfoIcon description="hal0.toml [memory] · requires restart to switch" /></div>
      </div>
      <SRow
        k="Engine"
        sub={<span title={engineDesc}>{engineDesc.length > 150 ? engineDesc.slice(0, 147) + "…" : engineDesc}</span>}
        v={
          <select value={engine} onChange={e => setEngine(e.target.value)} style={_advInputStyle}>
            {options.map(o => <option key={o} value={o}>{o}</option>)}
          </select>
        }
        actions={<ApplyBadge settingsKey="memory.engine" registry={registry} />}
      />
      <div style={{display: "flex", justifyContent: "flex-end", gap: 8, padding: "8px 12px 4px"}}>
        {dirty && (
          <button className="btn ghost sm" onClick={() => setEngine(currentEngine)}>Reset</button>
        )}
        <button className="btn sm" disabled={!dirty || update.isPending} onClick={doSave}>
          {update.isPending ? "Saving…" : "Save engine"}
        </button>
      </div>
    </div>
  );
}

function MemoryRerankerPanel({ registry }) {
  const { settings, update, schema: schemaQuery } = useSettingsClient({ schema: true });
  const schema = schemaQuery.data || null;
  const live = settings.data || null;

  const RERANK_KEYS = [
    "memory.embedding.rerank_gateway_url",
    "memory.embedding.rerank_model",
    "memory.embedding.rerank_connect_timeout_s",
    "memory.embedding.rerank_read_timeout_s",
  ];
  const fields = {};
  for (const k of RERANK_KEYS) fields[k] = _schemaField(schema, k);

  const [buf, setBuf] = useState({});
  const onChange = (dotKey, value) => setBuf(b => ({ ...b, [dotKey]: value }));

  const dirtyKeys = Object.keys(buf).filter(k => {
    const { ok, value } = _advCoerce(fields[k], buf[k]);
    if (!ok) return true;
    const cur = _getIn(live, k);
    return value !== (cur === undefined ? (fields[k]?.default ?? null) : cur);
  });
  const invalidKeys = dirtyKeys.filter(k => !_advCoerce(fields[k], buf[k]).ok);
  const canSave = dirtyKeys.length > 0 && invalidKeys.length === 0 && !update.isPending;

  const doSave = async () => {
    let patch = {};
    for (const k of dirtyKeys) {
      const { value } = _advCoerce(fields[k], buf[k]);
      patch = _deepMergePatch(patch, k.split(".").reverse().reduce((acc, part) => ({ [part]: acc }), value));
    }
    try {
      await update.mutateAsync(patch);
      setBuf({});
      window.__hal0Toast && window.__hal0Toast("Reranker settings saved", "ok");
    } catch (e) {
      window.__hal0Toast && window.__hal0Toast(`Save failed — ${e?.message || "see logs"}`, "err");
    }
  };

  return (
    <div className="s-panel" style={{marginBottom: 12}}>
      <div className="s-row" style={{paddingBottom: 4, borderBottom: "1px solid var(--line)"}}>
        <div className="k"><span>Reranker</span><FieldInfoIcon description="hal0.toml [memory.embedding] · second-pass ranking after recall" /></div>
      </div>
      {RERANK_KEYS.map(k => (
        <AdvRow
          key={k}
          dotKey={k}
          field={fields[k]}
          live={_getIn(live, k)}
          buf={buf[k]}
          onChange={onChange}
          registry={registry}
        />
      ))}
      <div style={{display: "flex", justifyContent: "flex-end", gap: 8, padding: "8px 12px 4px"}}>
        {dirtyKeys.length > 0 && (
          <button className="btn ghost sm" onClick={() => setBuf({})}>Reset</button>
        )}
        <button className="btn sm" disabled={!canSave} onClick={doSave}>
          {update.isPending ? "Saving…" : "Save reranker"}
        </button>
      </div>
    </div>
  );
}

// ─── Memory graph extraction panel ──────────────────────────────────────────
//
// [memory.graph] gets a dedicated panel (not schema-driven rows) because the
// dedicated PUT /api/memory/graph endpoint does what the generic settings PUT
// can't: it validates the extraction slot against live enabled llm slots
// (available_slots / slot_resolves) and propagates the change into the
// hindsight-api drop-in + restart, reporting a propagation error if that
// restart fails (ADR-0023 §3).
function MemoryGraphPanel() {
  const statusQuery = useMemoryGraphStatus();
  const updateGraph = useUpdateMemoryGraph();
  const retryFailed = useRetryFailedExtractions();
  const st = statusQuery.data;

  const doRetryFailed = async () => {
    try {
      const res = await retryFailed.mutateAsync();
      const q = res?.queued ?? 0;
      const s = res?.skipped ?? 0;
      window.__hal0Toast && window.__hal0Toast(
        q === 0 && s === 0
          ? "No failed extractions to retry"
          : `Requeued ${q} failed extraction${q === 1 ? "" : "s"}${s ? ` · ${s} skipped` : ""}`,
        "ok",
      );
    } catch (e) {
      window.__hal0Toast && window.__hal0Toast(`Retry failed — ${e?.message || "see logs"}`, "err");
    }
  };

  const [enabled, setEnabled] = useState(false);
  const [slot, setSlot] = useState("");
  const [timeoutS, setTimeoutS] = useState("300");
  useEffect(() => {
    if (!st) return;
    setEnabled(!!st.enabled);
    setSlot(st.extraction_slot || "");
    if (st.llm_timeout_s != null) setTimeoutS(String(st.llm_timeout_s));
  }, [st?.enabled, st?.extraction_slot, st?.llm_timeout_s]);

  const timeoutNum = parseInt(timeoutS, 10);
  const timeoutValid = /^\d+$/.test(timeoutS.trim()) && timeoutNum >= 30 && timeoutNum <= 3600;
  const dirty = !!st && (
    enabled !== !!st.enabled
    || slot !== (st.extraction_slot || "")
    || (st.llm_timeout_s != null && timeoutS !== String(st.llm_timeout_s))
  );
  const slots = st?.available_slots || [];
  // Keep the currently-configured slot pickable even when it no longer
  // resolves, so the operator can see (and move off) a stale value.
  const slotOptions = slot && !slots.includes(slot) ? [slot, ...slots] : slots;

  const doSave = async () => {
    try {
      const body = { enabled };
      if (slot) body.extraction_slot = slot;
      if (timeoutValid) body.llm_timeout_s = timeoutNum;
      const resp = await updateGraph.mutateAsync(body);
      const perr = resp?.propagation?.error;
      window.__hal0Toast && window.__hal0Toast(
        perr
          ? `Saved, but hindsight-api restart failed — ${perr}`
          : "Memory graph settings saved",
        perr ? "err" : "ok",
      );
    } catch (e) {
      window.__hal0Toast && window.__hal0Toast(`Save failed — ${e?.message || "see logs"}`, "err");
    }
  };

  const stateChip = !st
    ? <span className="chip mono" style={{fontSize: 10, padding: "1px 6px", color: "var(--fg-4)"}}>—</span>
    : st.enabled
      ? (st.in_flight > 0
          ? <span className="chip mono" style={{fontSize: 10, padding: "1px 6px", color: "var(--warn)", borderColor: "var(--warn)"}}>extracting · {st.in_flight}</span>
          : <span className="chip mono" style={{fontSize: 10, padding: "1px 6px", color: "var(--ok)", borderColor: "var(--ok)"}}>on</span>)
      : <span className="chip mono" style={{fontSize: 10, padding: "1px 6px", color: "var(--fg-4)"}}>off</span>;

  return (
    <div className="s-panel" style={{marginBottom: 12}}>
      <div className="s-row" style={{paddingBottom: 4, borderBottom: "1px solid var(--line)"}}>
        <div className="k">
          <span>Memory graph extraction</span>
          <FieldInfoIcon description="hal0.toml [memory.graph] · builds a knowledge graph from stored memories via a local LLM slot" />
        </div>
        <div className="v">{stateChip}</div>
      </div>
      {statusQuery.isError && (
        <div className="s-row" style={{padding: "8px 12px"}}>
          <span className="mono" style={{fontSize: 11, color: "var(--err)"}}>
            Could not load graph status — {statusQuery.error?.message || "is memory enabled on this install?"}
          </span>
        </div>
      )}
      <SRow k="Enabled" sub="Reporting + extraction-slot routing. Hindsight builds its graph natively — this does not stop the daemon extracting" v={
        <input type="checkbox" checked={enabled} disabled={!st} onChange={e => setEnabled(e.target.checked)} style={{accentColor: "var(--accent)"}} />
      } />
      <SRow
        k="Extraction slot"
        sub={st && !st.slot_resolves
          ? "⚠ configured slot doesn't match an enabled LLM slot — extraction is stalled until this points at a live slot"
          : "Local LLM slot that runs the extraction prompts"}
        v={
          slotOptions.length > 0 ? (
            <select value={slot} disabled={!st} onChange={e => setSlot(e.target.value)} style={_advInputStyle}>
              {slotOptions.map(s => (
                <option key={s} value={s}>{s}{slots.includes(s) ? "" : " (not running)"}</option>
              ))}
            </select>
          ) : (
            <input value={slot} disabled={!st} onChange={e => setSlot(e.target.value)} placeholder="slot name (e.g. utility)"
              className="mono" style={{..._advInputStyle, width: 200}} />
          )
        }
      />
      <SRow
        k="LLM timeout"
        sub="Seconds the Hindsight daemon waits on extraction / consolidation / reflect calls (30–3600) · covers cold slot starts"
        v={
          <input
            type="number" min={30} max={3600} value={timeoutS} disabled={!st}
            onChange={e => setTimeoutS(e.target.value)}
            placeholder="300"
            className="mono"
            style={{..._advInputStyle, width: 100, borderColor: timeoutValid || !timeoutS ? "var(--line)" : "var(--err)"}}
          />
        }
      />
      {st && (
        <SRow
          k="Extraction health"
          sub="Lifetime counters for graph builds on this install"
          mono
          v={<>
            <span style={{color: "var(--ok)"}}>{st.builds_ok} built</span>
            <span style={{color: st.errors > 0 ? "var(--err)" : "var(--fg-4)"}}> · {st.errors} error{st.errors === 1 ? "" : "s"}</span>
            {st.last_built_at && <span style={{color: "var(--fg-4)"}}> · last {new Date(st.last_built_at).toLocaleString()}</span>}
            {st.last_error && <span style={{color: "var(--err)"}} title={st.last_error}> · {st.last_error.slice(0, 80)}{st.last_error.length > 80 ? "…" : ""}</span>}
          </>}
        />
      )}
      <div style={{display: "flex", justifyContent: "flex-end", gap: 8, padding: "8px 12px 4px"}}>
        {st && st.errors > 0 && (
          <button
            className="btn ghost sm"
            style={{marginRight: "auto", color: "var(--warn)", borderColor: "var(--warn)"}}
            disabled={retryFailed.isPending}
            onClick={doRetryFailed}
            data-testid="mem-btn-retry-failed"
            title="Requeue every failed extraction/consolidation op — safe to run once the slot resolves"
          >
            {retryFailed.isPending ? "Retrying…" : `Retry ${st.errors} failed`}
          </button>
        )}
        {dirty && (
          <button className="btn ghost sm" onClick={() => {
            setEnabled(!!st?.enabled);
            setSlot(st?.extraction_slot || "");
            setTimeoutS(st?.llm_timeout_s != null ? String(st.llm_timeout_s) : "300");
          }}>Reset</button>
        )}
        <button className="btn sm" disabled={!dirty || !timeoutValid || updateGraph.isPending} onClick={doSave}>
          {updateGraph.isPending ? "Saving…" : "Save graph settings"}
        </button>
      </div>
    </div>
  );
}
