// hal0 dashboard — FirstRun (bundle picker, confirmation, progress)
//
// Phase B1: bundles + per-model downloads read from real hooks where
// available. Hardware detection (RAM / NPU / disk) still uses
// HAL0_DATA.host because /api/hardware lands separately; flip when
// useHardware is universally cheap.

import { useCuratedBundles, useFirstRunInstall, useFirstRunComplete } from '@/api/hooks/useFirstRun'
import { useHardware } from '@/api/hooks/useHardware'
import { useModelStore, useModelStoreSet, useModelStoreMigrate } from '@/api/hooks/useSettings'
import { usePullJob, fmtBytes, fmtSpeed, fmtEta } from '@/api/hooks/useModels'

const { useState: useStateF, useEffect: useEffectF } = React;

function _frFmtBytes(n) {
  if (!n || n < 0) return "—";
  if (n < 1024) return n + " B";
  if (n < 1024 ** 2) return (n / 1024).toFixed(1) + " KB";
  if (n < 1024 ** 3) return (n / 1024 ** 2).toFixed(1) + " MB";
  return (n / 1024 ** 3).toFixed(2) + " GB";
}

// ─── Storage step (state 1.5 — between picker and confirm) ───
//
// Lets the operator decide WHERE models live before any pulls start.
// Mirrors the Settings → Storage surface (same hooks, same dry-run
// migration plumbing) so the FirstRun choice and the Settings page
// stay in lockstep.
function FirstRunStorage({ onContinue, onBack }) {
  const storeQuery = useModelStore();
  const storeSet = useModelStoreSet();
  const storeMigrate = useModelStoreMigrate();
  const storeState = storeQuery.data;
  const [path, setPath] = useStateF("");
  const [pendingPlan, setPendingPlan] = useStateF(null);

  useEffectF(() => {
    if (storeState?.effective != null) setPath(storeState.effective);
  }, [storeState]);

  const apply = async (target, { migrate = false } = {}) => {
    try {
      const resp = await storeSet.mutateAsync({ path: target, migrate });
      if (resp.status === "needs_migration") {
        setPendingPlan({ ...resp.plan, path: target });
        return;
      }
      window.__hal0Toast && window.__hal0Toast(`Storage set → ${target}`, "ok");
      onContinue();
    } catch (e) {
      window.__hal0Toast && window.__hal0Toast(`Storage save failed — ${e?.message || "see logs"}`, "err");
    }
  };

  const confirmMigrate = async () => {
    if (!pendingPlan) return;
    const target = pendingPlan.path;
    setPendingPlan(null);
    try {
      await storeMigrate.mutateAsync({ path: target });
      window.__hal0Toast && window.__hal0Toast(`Moved + set → ${target}`, "ok");
      onContinue();
    } catch (e) {
      window.__hal0Toast && window.__hal0Toast(`Move failed — ${e?.message || "see logs"}`, "err");
    }
  };

  return (
    <div className="fr-inner">
      <div className="fr-head">
        <div className="fr-eyebrow"><span className="blip" />FirstRun · storage</div>
        <h1 className="fr-title">Where should models live?</h1>
        <p className="fr-lede">Hal0 reads + writes model files here. Every slot container points at the same path — pick once.</p>
      </div>

      {storeQuery.isPending && <div style={{padding: 20, color: "var(--fg-4)", fontFamily: "var(--jbm)", fontSize: 12}}>Probing storage candidates…</div>}

      {storeState && (
        <div className="card" style={{padding: 20, marginBottom: 24}}>
          <div className="mono" style={{fontSize: 10, color: "var(--fg-4)", textTransform: "uppercase", letterSpacing: "0.1em", marginBottom: 14}}>Storage path</div>
          <input
            className="input mono"
            value={path}
            onChange={e => setPath(e.target.value)}
            placeholder="/mnt/ai-models"
            style={{width: "100%", padding: "10px 12px", fontSize: 14, marginBottom: 14}}
          />
          <div style={{display: "flex", gap: 6, flexWrap: "wrap", marginBottom: 16}}>
            {storeState.suggestions.map(s => (
              <button
                key={s.path}
                className={"chip" + (s.path === path ? " amber" : "")}
                style={{cursor: "pointer", fontFamily: "var(--jbm)"}}
                onClick={() => setPath(s.path)}
                title={s.exists ? `${s.files_count} files · ${_frFmtBytes(s.size_bytes)} used · ${_frFmtBytes(s.free_bytes)} free` : "does not exist yet"}
              >
                {s.path}
                <span style={{marginLeft: 6, color: "var(--fg-4)", fontSize: 10}}>
                  {s.exists
                    ? (s.files_count > 0 ? `${s.files_count} files · ${_frFmtBytes(s.free_bytes)} free` : `empty · ${_frFmtBytes(s.free_bytes)} free`)
                    : "missing"}
                </span>
              </button>
            ))}
          </div>
          <div style={{fontFamily: "var(--jbm)", fontSize: 11.5, color: "var(--fg-3)"}}>
            {storeState.current_state.exists
              ? <>Current effective: <span style={{color: "var(--fg)"}}>{storeState.effective}</span> · {storeState.current_state.files_count} files · {_frFmtBytes(storeState.current_state.free_bytes)} free here</>
              : <>Current effective: <span style={{color: "var(--warn)"}}>{storeState.effective}</span> (missing)</>}
          </div>
        </div>
      )}

      <div className="fr-actions">
        <button className="btn ghost lg" onClick={onBack}>← back</button>
        <button
          className="btn lg"
          disabled={!path.trim() || storeSet.isPending}
          onClick={() => apply(path.trim(), { migrate: false })}
        >
          {storeSet.isPending ? "Saving…" : "Continue"}
        </button>
      </div>

      <ConfirmDialog
        open={!!pendingPlan}
        onCancel={() => setPendingPlan(null)}
        onConfirm={confirmMigrate}
        title="Move existing models?"
        message={
          pendingPlan ? (
            <span>
              We found <b>{pendingPlan.files_count} file(s)</b> ({_frFmtBytes(pendingPlan.size_bytes)}) at <span className="mono" style={{color: "var(--fg)"}}>{pendingPlan.source}</span>. Move them to <span className="mono" style={{color: "var(--accent)"}}>{pendingPlan.target}</span> and continue?
            </span>
          ) : null
        }
        confirmLabel={storeMigrate.isPending ? "Moving…" : "Move + continue"}
      />
    </div>
  );
}

// ─── Bundle picker (state 1) ───
function FirstRunPicker({ onPick, onSkip, layout }) {
  // Phase B1: live curated bundles + hardware detection. Fall through
  // to the static fixtures when either query hasn't returned yet, so
  // FirstRun renders fully on a cold boot.
  const bundlesQuery = useCuratedBundles();
  const hwQuery = useHardware();
  const bundles = bundlesQuery.data?.bundles ?? HAL0_DATA.bundles;
  const ramDetected = hwQuery.data?.ram?.total ?? HAL0_DATA.host.ram.total;
  // Recommended = highest tier whose minimum ≤ detected
  const fitTiers = bundles.filter(b => b.ram <= ramDetected);
  const recId = fitTiers.length ? fitTiers[fitTiers.length - 1].id : null;

  return (
    <div className="fr-inner">
      <div className="fr-head">
        <div className="fr-eyebrow"><span className="blip" />FirstRun · install</div>
        <h1 className="fr-title">Welcome to <span className="accent">hal0</span></h1>
        <p className="fr-lede">Pick a starting configuration. You can customise any slot later — or skip and configure manually.</p>
        <div className="fr-detect">
          <span className="seg"><span className="k">RAM</span><b>128 GB</b> unified</span>
          <span className="seg"><span className="k">GPU</span><b>Strix Halo</b> gfx1151</span>
          <span className="seg"><span className="k">NPU</span><b>XDNA2</b><span className="ok">●</span></span>
          <span className="seg"><span className="k">disk</span><b>412 GB</b> free</span>
        </div>
      </div>

      {layout === "table" ? (
        <BundleTable bundles={bundles} recId={recId} onPick={onPick} ram={ramDetected} />
      ) : (
        <BundleGrid bundles={bundles} recId={recId} onPick={onPick} ram={ramDetected} />
      )}

      <h3 className="fr-section-label" style={{marginTop: 28}}>Pre-built kits</h3>
      <div className="kit">
        <div className="kit-main">
          <div className="kit-eyebrow">AMD-curated · vendor-blessed</div>
          <div className="kit-name">LMX-Omni-52B-Halo</div>
          <div className="kit-spec">≥ 100 GB unified RAM Strix Halo · NPU trio · 4 slots ready out of the box</div>
          <div className="kit-models">
            <span className="chip">Qwen3.6-35B</span>
            <span className="chip">Whisper-Large</span>
            <span className="chip">kokoro</span>
            <span className="chip">Flux-2-Klein-9B</span>
          </div>
        </div>
        <div className="kit-side">
          <div className="sz mono">~75<span className="u">GB</span></div>
          <button className="btn lg" onClick={() => onPick("max")}>Install LMX kit</button>
        </div>
      </div>

      <div className="fr-skip-row">
        <button className="fr-skip" onClick={onSkip}>Skip — configure manually</button>
      </div>
    </div>
  );
}

function BundleGrid({ bundles, recId, onPick, ram }) {
  return (
    <div className="tiers">
      {bundles.map(b => {
        const fits = b.ram <= ram;
        const rec = b.id === recId;
        return (
          <div key={b.id} className={"tier-card" + (rec ? " recommended" : "") + (fits ? "" : " unfit")}>
            <div className="tier-card-h">
              <div className="tier-name mono">{b.name}</div>
              {rec ? <span className="tier-tag rec">★ recommended</span>
                   : fits ? <span className="tier-tag fit">fits</span>
                          : <span className="tier-tag unfit">needs ≥ {b.ram} GB</span>}
            </div>
            <div className="tier-spec">
              <b>{b.ram} GB+</b> unified · <b>~{b.sizeGB} GB</b> download
            </div>
            <div className="tier-stats">
              <div className="tier-stat">
                <div className="l">slots</div>
                <div className="v num">{b.includes.filter(i => i.active).length}<span className="u">/8</span></div>
              </div>
              <div className="tier-stat">
                <div className="l">size</div>
                <div className="v num">{b.sizeGB}<span className="u">GB</span></div>
              </div>
            </div>
            <div className="tier-includes">
              {b.includes.map((inc, i) => (
                <div key={i} className={"ln" + (inc.active ? "" : " faint")}>
                  <span className="ic">{inc.active ? "+" : "·"}</span>
                  <span>{inc.label}</span>
                </div>
              ))}
            </div>
            <div className="actions">
              <button className="btn" style={{flex: 1}} onClick={() => fits && onPick(b.id)} disabled={!fits}>
                Pick {b.name}
              </button>
            </div>
          </div>
        );
      })}
    </div>
  );
}

function BundleTable({ bundles, recId, onPick, ram }) {
  const rows = [
    { id: "chat",   label: "chat",   each: b => b.id === "lite" ? "1.2B" : b.id === "default" ? "9B" : b.id === "pro" ? "27B + 30B coder" : "35B + 30B coder + NPU 1B" },
    { id: "embed",  label: "embed + rerank", each: b => b.id === "lite" ? "—" : b.id === "default" ? "nomic-v1.5" : "nomic + bge-rerank" + (b.id === "max" ? " + embed-gemma" : "") },
    { id: "voice",  label: "voice (stt+tts)", each: b => b.id === "lite" ? "—" : b.id === "default" ? "whisper-base + kokoro" : "whisper-large + kokoro" + (b.id === "max" ? " + npu-stt" : "") },
    { id: "image",  label: "image",  each: b => b.id === "pro" ? "sd-turbo" : b.id === "max" ? "flux-2-klein-9b" : "—" },
    { id: "npu",    label: "NPU trio", each: b => b.id === "max" ? "agent + stt-npu + embed-npu" : "—" },
  ];
  return (
    <div className="card" style={{overflow: "hidden", marginBottom: 12}}>
      <div style={{display: "grid", gridTemplateColumns: "180px repeat(4, 1fr)", background: "var(--bg)", borderBottom: "1px solid var(--line)"}}>
        <div style={{padding: 14, fontFamily: "var(--jbm)", fontSize: 10, color: "var(--fg-4)", textTransform: "uppercase", letterSpacing: "0.1em"}}>capability</div>
        {bundles.map(b => {
          const fits = b.ram <= ram;
          const rec = b.id === recId;
          return (
            <div key={b.id} style={{padding: 14, borderLeft: "1px solid var(--line)", textAlign: "center", opacity: fits ? 1 : 0.5, position: "relative"}}>
              <div className="mono" style={{fontSize: 17, fontWeight: 500, letterSpacing: "-0.02em", color: rec ? "var(--accent)" : "var(--fg)"}}>{b.name}</div>
              <div className="mono" style={{fontSize: 10, color: "var(--fg-4)", marginTop: 2}}>{b.ram} GB+ · ~{b.sizeGB} GB</div>
              {rec && <div style={{position: "absolute", top: 0, left: 0, right: 0, height: 2, background: "var(--accent)"}} />}
            </div>
          );
        })}
      </div>
      {rows.map(r => (
        <div key={r.id} style={{display: "grid", gridTemplateColumns: "180px repeat(4, 1fr)", borderBottom: "1px solid var(--line-soft)"}}>
          <div style={{padding: 12, fontFamily: "var(--jbm)", fontSize: 12, color: "var(--fg-2)"}}>{r.label}</div>
          {bundles.map(b => {
            const v = r.each(b);
            const off = v === "—";
            return (
              <div key={b.id} style={{padding: 12, borderLeft: "1px solid var(--line-soft)", fontFamily: "var(--jbm)", fontSize: 11.5, textAlign: "center", color: off ? "var(--fg-5)" : "var(--fg-2)"}}>
                {v}
              </div>
            );
          })}
        </div>
      ))}
      <div style={{display: "grid", gridTemplateColumns: "180px repeat(4, 1fr)", borderTop: "1px solid var(--line)", background: "var(--bg)"}}>
        <div style={{padding: 12}} />
        {bundles.map(b => {
          const fits = b.ram <= ram;
          return (
            <div key={b.id} style={{padding: 12, borderLeft: "1px solid var(--line-soft)", textAlign: "center"}}>
              <button className="btn sm" style={{width: "92%", justifyContent: "center"}} disabled={!fits} onClick={() => fits && onPick(b.id)}>
                Pick {b.name}
              </button>
            </div>
          );
        })}
      </div>
    </div>
  );
}

// ─── Bundle confirmation (state 2) ───
function FirstRunConfirm({ bundleId, onBack, onInstall }) {
  const [withNpu, setWithNpu] = useStateF(false);
  // Phase B1: bundles + install mutation. Pull the install trigger
  // through the real hook; main.jsx's setFrStage('progress') still
  // drives the progress view.
  const bundlesQuery = useCuratedBundles();
  const installM = useFirstRunInstall();
  const bundles = bundlesQuery.data?.bundles ?? HAL0_DATA.bundles;
  const bundle = bundles.find(b => b.id === bundleId) || HAL0_DATA.bundles.find(b => b.id === bundleId);
  const det = HAL0_DATA.bundleDetails.pro; // detail-level data not yet over /api/firstrun
  return (
    <div className="fr-inner">
      <span className="fr-confirm-back mono" onClick={onBack}>← back to picker</span>
      <div className="fr-confirm-h">
        <h2>hal0-{bundle.name}</h2>
        <span className="sub">{bundle.ram} GB+ unified · ~{bundle.sizeGB} GB download · est 12 min</span>
      </div>
      <p className="fr-confirm-sub">{bundle.desc} You can change any slot after install.</p>

      <div className="fr-confirm-card">
        <div className="fr-confirm-card-h mono">
          <span>What gets installed</span>
          <b>{det.models.length} slots</b>
          <span style={{color: "var(--fg-4)"}}>· {det.models.reduce((a, m) => a + parseFloat(m.size), 0).toFixed(1)} GB total</span>
          <span className="right">capabilities.toml</span>
        </div>
        {det.models.map(m => (
          <div key={m.slot} className="fr-confirm-row">
            <span className="nm">{m.slot}</span>
            <span className="ml">{m.model}</span>
            <span className="sz">{m.size}</span>
            <span className="tag">
              {m.tag.split(" ").map((t, i) => <span key={i} className={"chip" + (t === "cpu" ? " dev-cpu" : t === "default" ? " amber outlined" : "")}>{t}</span>)}
            </span>
          </div>
        ))}
      </div>

      <div className="fr-confirm-card">
        <div className="fr-confirm-card-h mono" style={{justifyContent: "space-between"}}>
          <div style={{display: "flex", alignItems: "center", gap: 14}}>
            <span style={{display: "inline-flex", alignItems: "center", gap: 8}}>
              <span style={{width: 18, height: 18, borderRadius: 3, border: "1px solid rgba(200,150,255,0.40)", background: "rgba(200,150,255,0.08)", color: "var(--dev-npu)", display: "inline-flex", alignItems: "center", justifyContent: "center", fontSize: 9, letterSpacing: "0.05em", fontWeight: 600}}>NPU</span>
              FLM trio
            </span>
            <span style={{color: "var(--fg-4)"}}>· optional</span>
          </div>
          <label className="mono" style={{display: "inline-flex", alignItems: "center", gap: 8, cursor: "pointer", color: "var(--fg-2)"}}>
            <input type="checkbox" checked={withNpu} onChange={e => setWithNpu(e.target.checked)} style={{accentColor: "var(--accent)"}} />
            <span>Enable on install</span>
          </label>
        </div>
        {det.npu.map(m => (
          <div key={m.slot} className="fr-confirm-row" style={{opacity: withNpu ? 1 : 0.55}}>
            <span className="nm" style={{color: "var(--dev-npu)"}}>{m.slot}</span>
            <span className="ml">{m.model}</span>
            <span className="sz">{m.size}</span>
            <span className="tag">{m.tag.split(" ").map((t, i) => <span key={i} className="chip">{t}</span>)}</span>
          </div>
        ))}
        <div className="fr-confirm-foot">
          <span>~2 GB NPU memory · ~14s swap penalty on chat-model change · stt-npu + embed-npu are passengers</span>
        </div>
      </div>

      <div className="card" style={{padding: 16, fontSize: 12.5, color: "var(--fg-3)", marginBottom: 24, background: "var(--bg)"}}>
        <div className="mono" style={{fontSize: 10, color: "var(--fg-4)", textTransform: "uppercase", letterSpacing: "0.1em", marginBottom: 8}}>Notes</div>
        <ul style={{margin: 0, paddingLeft: 18, lineHeight: 1.7}}>
          <li>TTS runs on CPU only on Linux (kokoro:cpu). ~1s per sentence.</li>
          <li><span className="mono" style={{color: "var(--fg-2)"}}>coder</span> shares the iGPU with <span className="mono" style={{color: "var(--fg-2)"}}>primary</span> — the GPU arbiter stops one container to start the other when memory is tight.</li>
          <li>HF_TOKEN is not required for this bundle. Configure later in Settings if you want gated repos.</li>
        </ul>
      </div>

      <div className="fr-actions">
        <button className="btn ghost lg" onClick={onBack}>Cancel</button>
        <button className="btn lg" onClick={async () => {
          // Best-effort backend kick — advance the UI regardless.
          // Capture model_ids from the response so the progress pane
          // can reattach per-row SSE streams via usePullJob.reattach().
          let ids = [];
          try {
            const res = await installM.mutateAsync({ bundle: bundleId, withNpu });
            if (Array.isArray(res?.model_ids)) ids = res.model_ids;
          } catch (_e) { /* best-effort — progress stage renders graceful empty */ }
          onInstall(ids);
        }} disabled={installM.isPending}>{Icons.download} {installM.isPending ? "Starting…" : `Install hal0-${bundle.name}`}</button>
      </div>
    </div>
  );
}

// ─── Per-model download row backed by usePullJob (SSE reattach) ───
//
// Mirrors DownloadRow in model-modals.jsx but uses the existing
// dl-row/dl-bar/dl-pct/dl-state CSS layout from the firstrun pane.
// reattach() on mount reconnects to any in-flight pull; if the pull
// hasn't started yet the row shows "queued" as the initial state.
function FrDownloadRow({ modelId }) {
  const job = usePullJob();
  useEffectF(() => {
    if (modelId) job.reattach(modelId);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [modelId]);

  const state = job.state; // idle | queued | running | completed | failed | cancelled
  const pct   = job.pct ?? 0;

  const barClass = state === "completed" ? "ok" : state === "failed" ? "err" : "";
  const pctClass = "dl-pct mono" + (
    state === "completed" ? " ok" :
    state === "queued" || state === "idle" ? " dim" :
    state === "failed" || state === "cancelled" ? " err" : ""
  );
  const pctLabel =
    state === "completed" ? "✓ 100%" :
    state === "queued" || state === "idle" ? "queued" :
    state === "failed"    ? "✗ failed" :
    state === "cancelled" ? "cancelled" :
    `${pct}%`;
  const stateLabel =
    state === "running"   ? `${fmtBytes(job.downloaded)} / ${fmtBytes(job.total)}` :
    state === "queued" || state === "idle" ? "waiting" :
    state === "completed" ? "complete" :
    state === "failed"    ? (job.error?.message || "pull failed") :
    state === "cancelled" ? "cancelled" : "";

  return (
    <div className="dl-row">
      <div className="dl-name mono">
        {modelId}
        {state === "running" && job.speedBps > 0 && (
          <span className="sub">{fmtSpeed(job.speedBps)} · {fmtEta(job.etaS)} remaining</span>
        )}
      </div>
      <div className="dl-bar">
        <i className={barClass} style={{ width: `${pct}%` }} />
      </div>
      <div className={pctClass}>{pctLabel}</div>
      <div className="dl-state mono">{stateLabel}</div>
      {state === "failed" && (
        <div className="dl-err">
          <span style={{color: "var(--err)", display: "inline-flex"}}>{Icons.warn}</span>
          <span style={{flex: 1}}>
            <b>{modelId}</b> · {job.error?.message || "pull failed"}. Retry to re-fetch, or pull later from <span className="mono" style={{color: "var(--fg)"}}>/models</span>.
          </span>
          <button className="btn ghost sm" onClick={() => job.start(modelId)}>{Icons.restart} Retry</button>
        </div>
      )}
    </div>
  );
}

// ─── Install progress (state 3) ───
function FirstRunProgress({ onDone, bundleId, modelIds }) {
  // modelIds comes from the install mutation response (model_ids[]).
  // Each FrDownloadRow reattaches to the in-flight SSE stream for that
  // model via usePullJob.reattach(id). Empty array = graceful empty state.
  const completeM = useFirstRunComplete();
  const bundlesQuery = useCuratedBundles();
  const bundle = (bundlesQuery.data?.bundles ?? HAL0_DATA.bundles).find(b => b.id === bundleId)
              || HAL0_DATA.bundles.find(b => b.id === bundleId);
  const bundleName = bundle?.name ? `hal0-${bundle.name}` : 'hal0';
  const ids = Array.isArray(modelIds) && modelIds.length > 0 ? modelIds : [];
  return (
    <div className="fr-inner">
      <div className="fr-prog-h">
        <h2>Installing {bundleName}…</h2>
        <span className="meta">
          {bundle?.sizeGB ? `~${bundle.sizeGB} GB total · ` : ""}downloads continue in background
        </span>
      </div>

      <div className="fr-prog-list">
        {ids.length === 0 ? (
          <div className="dl-row" style={{color: "var(--fg-4)", fontFamily: "var(--jbm)", fontSize: 12, padding: "16px 0"}}>
            Install started — download rows will appear as pulls begin.
          </div>
        ) : (
          ids.map(id => <FrDownloadRow key={id} modelId={id} />)
        )}
      </div>

      <div className="fr-actions" style={{justifyContent: "space-between"}}>
        <button className="btn ghost lg">Pause all</button>
        <div style={{display: "flex", gap: 12}}>
          <button className="btn ghost lg">View logs</button>
          <button className="btn lg" onClick={() => {
            completeM.mutate();
            onDone();
          }}>Open dashboard</button>
        </div>
      </div>
    </div>
  );
}

// ─── FirstRun view shell ───
function FirstRunView({ frStage, setFrStage, frBundle, setFrBundle, onComplete, layout }) {
  const [skipOpen, setSkipOpen] = useStateF(false);
  // Capture model IDs returned by the install mutation so the progress
  // pane can reattach live SSE streams per model via FrDownloadRow.
  const [frModelIds, setFrModelIds] = useStateF([]);
  return (
    <div className="fr">
      {frStage === "pick" && (
        <FirstRunPicker
          // Route through the new "storage" stage before confirming the
          // bundle — the operator decides WHERE models live before any
          // pulls start so the bundle's downloads land in the right place.
          onPick={b => { setFrBundle(b); setFrStage("storage"); }}
          onSkip={() => setSkipOpen(true)}
          layout={layout}
        />
      )}
      {frStage === "storage" && (
        <FirstRunStorage
          onBack={() => setFrStage("pick")}
          onContinue={() => setFrStage("confirm")}
        />
      )}
      {frStage === "confirm" && (
        <FirstRunConfirm
          bundleId={frBundle}
          onBack={() => setFrStage("storage")}
          onInstall={ids => { setFrModelIds(ids || []); setFrStage("progress"); }}
        />
      )}
      {frStage === "progress" && (
        <FirstRunProgress bundleId={frBundle} modelIds={frModelIds} onDone={() => onComplete()} />
      )}
      <SkipBundleDialog
        open={skipOpen}
        onCancel={() => setSkipOpen(false)}
        onConfirm={() => { setSkipOpen(false); onComplete(); }}
      />
    </div>
  );
}

Object.assign(window, { FirstRunView, FirstRunPicker, FirstRunStorage, FirstRunConfirm, FirstRunProgress, BundleGrid, BundleTable });
