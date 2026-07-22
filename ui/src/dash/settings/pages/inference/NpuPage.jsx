// INFERENCE ▸ NPU — FastFlowLM (FLM) tuning on the AMD XDNA2 NPU.
// Extracted verbatim from settings.jsx NpuSection (P3-ui split phase 1).
//
// FastFlowLM (FLM) runs on the AMD XDNA2 NPU as a single process that can
// multiplex chat + embed + ASR. The three operator-relevant knobs already
// persist in the npu slot TOML and are consumed by providers/flm.py:
//   - [model].context_size → HAL0_FLM_CTX → --ctx-len
//   - [npu].embed          → HAL0_FLM_LOAD_EMBED → --embed 1
//   - [npu].asr            → HAL0_FLM_LOAD_ASR   → --asr 1
// All three take effect when the slot's container next (re)starts, so they're
// service-restart. Persisted via PUT /api/slots/{name}/config, mirroring how
// ImageGenSection writes the [image] table. A read-only occupancy strip below
// reflects the live AIE-column allocation (single-tenant: one FLM = 8 cols).
import { useState, useEffect } from 'react'
import { useSlots, useSlotEdit, useSlotConfig } from '@/api/hooks/useSlots'
import { useNpuOccupancy } from '@/api/hooks/useNpuOccupancy'
import { SRow } from '../../shared/SRow.jsx'

export function NpuPage() {
  const slotsQuery = useSlots();
  const editSlot = useSlotEdit();
  const occQuery = useNpuOccupancy();

  const npuSlots = (slotsQuery.data || []).filter(s => s.device === "npu");
  const npuName = npuSlots.length > 0 ? npuSlots[0].name : null;
  const cfgQuery = useSlotConfig(npuName);
  const cfg = cfgQuery.data || {};
  const liveCtx = cfg.model?.context_size;
  const liveNpu = cfg.npu || {};

  const DEF_CTX = "16384";
  const origCtx = liveCtx != null ? String(liveCtx) : DEF_CTX;
  const origAsr = !!liveNpu.asr;
  const origEmbed = !!liveNpu.embed;

  const [ctx, setCtx] = useState(DEF_CTX);
  const [asr, setAsr] = useState(false);
  const [embed, setEmbed] = useState(false);
  useEffect(() => {
    setCtx(liveCtx != null ? String(liveCtx) : DEF_CTX);
    setAsr(!!liveNpu.asr);
    setEmbed(!!liveNpu.embed);
  }, [cfgQuery.data]);

  const ctxNum = parseInt(ctx, 10);
  const ctxValid = /^\d+$/.test(ctx.trim()) && ctxNum >= 512;
  const dirty = !!npuName && (ctx !== origCtx || asr !== origAsr || embed !== origEmbed);

  const doSave = async () => {
    if (!npuName || !ctxValid) return;
    const body = { model: { context_size: ctxNum }, npu: { asr, embed } };
    try {
      await editSlot.mutateAsync({ name: npuName, body });
      window.__hal0Toast && window.__hal0Toast("NPU settings saved — restart the slot to apply", "warn");
    } catch (e) {
      window.__hal0Toast && window.__hal0Toast(`Save failed — ${e?.message || "see logs"}`, "err");
    }
  };

  const reset = () => { setCtx(origCtx); setAsr(origAsr); setEmbed(origEmbed); };

  const occ = occQuery.data;
  const inputStyle = {fontFamily: "var(--jbm)", fontSize: 11, background: "var(--bg-2)", color: "var(--fg)", border: "1px solid var(--line)", borderRadius: 4, padding: "3px 6px"};

  return (
    <div className="s-section">
      <h2>NPU</h2>
      <p className="desc">
        FastFlowLM on the AMD XDNA2 NPU. One FLM process serves chat and, when enabled,
        embeddings + speech-to-text on the same 8-column AIE array. These knobs write the
        npu slot TOML and take effect on the slot's next restart.
      </p>

      {slotsQuery.isPending && (
        <div style={{padding: 16, color: "var(--fg-4)", fontFamily: "var(--jbm)", fontSize: 12}}>Loading slots…</div>
      )}
      {!slotsQuery.isPending && npuSlots.length === 0 && (
        <p className="hint" style={{fontFamily: "var(--jbm)", fontSize: 12, color: "var(--fg-4)"}}>
          No NPU slot configured. Create a slot with device <span className="mono">npu</span> in the Slots view (or run <span className="mono">hal0 setup</span> with NPU opt-in) to tune FLM here.
        </p>
      )}

      {npuSlots.length > 0 && (
        <div className="s-panel">
          <div className="s-row" style={{paddingBottom: 4, borderBottom: "1px solid var(--line)"}}>
            <div className="k"><span>FLM slot</span><FieldInfoIcon description="{npuName} · device=npu · profile=flm" /></div>
            <div className="v">
              <span className="chip mono" style={{fontSize: 10, padding: "1px 6px", color: "var(--fg-3)"}}>{npuName}</span>
            </div>
          </div>
          <SRow
            k="Context size"
            sub="FLM --ctx-len (tokens) · larger = more KV cache on the NPU"
            v={
              <input type="number" min={512} step={512} value={ctx} disabled={!npuName}
                onChange={e => setCtx(e.target.value)} placeholder={DEF_CTX}
                className="mono" style={{...inputStyle, width: 120, borderColor: ctxValid || !ctx ? "var(--line)" : "var(--err)"}} />
            }
            // TODO(P3-ui): this chip is hardcoded amber instead of reading the
            // apply-plan registry (spec Risk #2, settings.jsx:1888 in the
            // pre-split file — the canonical anti-pattern example). Per-slot
            // TOML fields like npu.<slot>.model.context_size aren't
            // Hal0Config keys, so useApplyPlan()'s registry has no entry for
            // them — a real fix needs a per-slot apply-plan (or a registry
            // key) from the backend, not a frontend-only change. Left as
            // parity-preserving TODO rather than a broken "fix".
            actions={<span className="chip mono" style={{fontSize: 10, padding: "2px 8px", color: "var(--warn)", borderColor: "var(--warn)", whiteSpace: "nowrap"}}>⟳ restart {npuName}</span>}
          />
          <SRow
            k="Load embeddings"
            sub="Serve /v1/embeddings from the FLM trio (--embed 1)"
            v={
              <label className="mono" style={{display: "inline-flex", alignItems: "center", gap: 8, cursor: "pointer", color: "var(--fg-2)"}}>
                <input type="checkbox" checked={embed} disabled={!npuName} onChange={e => setEmbed(e.target.checked)} style={{accentColor: "var(--accent)"}} />
                <span>{embed ? "enabled" : "disabled"}</span>
              </label>
            }
          />
          <SRow
            k="Load ASR"
            sub="Serve /v1/audio/transcriptions from the FLM trio (--asr 1)"
            v={
              <label className="mono" style={{display: "inline-flex", alignItems: "center", gap: 8, cursor: "pointer", color: "var(--fg-2)"}}>
                <input type="checkbox" checked={asr} disabled={!npuName} onChange={e => setAsr(e.target.checked)} style={{accentColor: "var(--accent)"}} />
                <span>{asr ? "enabled" : "disabled"}</span>
              </label>
            }
          />
          <div style={{display: "flex", justifyContent: "flex-end", gap: 8, padding: "8px 12px 4px"}}>
            {dirty && <button className="btn ghost sm" onClick={reset}>Reset</button>}
            <button className="btn sm" disabled={!dirty || !ctxValid || editSlot.isPending} onClick={doSave}>
              {editSlot.isPending ? "Saving…" : "Save NPU settings"}
            </button>
          </div>
        </div>
      )}

      {/* ── Live occupancy (read-only) ── */}
      {occ?.present && (
        <div className="s-panel" style={{marginTop: 12}}>
          <div className="s-row" style={{paddingBottom: 4, borderBottom: "1px solid var(--line)"}}>
            <div className="k"><span>Occupancy</span><FieldInfoIcon description="live AIE column allocation · single-tenant" /></div>
            <div className="v">
              <span className="chip mono" style={{fontSize: 10, padding: "1px 6px", color: occ.cols_used > 0 ? "var(--ok)" : "var(--fg-4)", borderColor: occ.cols_used > 0 ? "var(--ok)" : "var(--line)"}}>
                {occ.cols_used}/{occ.cols_total} cols
              </span>
            </div>
          </div>
          <SRow k="Peak" mono v={`${occ.tops_peak} TOPS · ${occ.tiles} tiles (${occ.rows}×${occ.cols})`} />
          {(occ.slots || []).map(s => (
            <SRow key={s.name} k={s.name} sub={s.model || "—"} mono
              v={<>
                <span style={{color: s.state === "serving" || s.state === "ready" ? "var(--ok)" : "var(--fg-4)"}}>{s.state}</span>
                <span style={{color: "var(--fg-4)"}}> · {s.cols?.length || 0} cols{s.gb != null ? ` · ${s.gb} GB` : ""}</span>
              </>} />
          ))}
        </div>
      )}
      {occQuery.data && !occ?.present && (
        <div style={{marginTop: 12, fontFamily: "var(--jbm)", fontSize: 11, color: "var(--fg-4)"}}>
          No NPU detected on this host — occupancy unavailable.
        </div>
      )}
    </div>
  );
}
