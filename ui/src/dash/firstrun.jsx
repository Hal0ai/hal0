// hal0 dashboard — FirstRun (bundle picker, confirmation, progress)
//
// Phase B1: bundles + per-model downloads read from real hooks where
// available. Hardware detection (RAM / NPU / disk) still uses
// HAL0_DATA.host because /api/hardware lands separately; flip when
// useHardware is universally cheap.

import { useCuratedBundles, useFirstRunInstall, useFirstRunComplete } from '@/api/hooks/useFirstRun'
import { useHardware } from '@/api/hooks/useHardware'

const { useState: useStateF } = React;

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
          <li><span className="mono" style={{color: "var(--fg-2)"}}>coder</span> slot is LRU-evictable — only one chat model is resident at a time when both exceed the per-type budget.</li>
          <li>HF_TOKEN is not required for this bundle. Configure later in Settings if you want gated repos.</li>
        </ul>
      </div>

      <div className="fr-actions">
        <button className="btn ghost lg" onClick={onBack}>Cancel</button>
        <button className="btn lg" onClick={() => {
          // Best-effort backend kick; UI advances regardless so the
          // mock build still shows the progress stage.
          installM.mutate({ bundle: bundleId, withNpu });
          onInstall();
        }}>{Icons.download} Install hal0-{bundle.name}</button>
      </div>
    </div>
  );
}

// ─── Install progress (state 3) ───
function FirstRunProgress({ onDone }) {
  // Phase B1: complete-mutation flips the backend's firstrun.completed
  // flag when the user clicks "Open dashboard". Downloads list is
  // intentionally still HAL0_DATA — per-row SSE wiring via
  // `usePullJob(id)` lands in B2 when DownloadRow swaps in the hook.
  const completeM = useFirstRunComplete();
  return (
    <div className="fr-inner">
      <div className="fr-prog-h">
        <h2>Installing hal0-Pro…</h2>
        <span className="meta">~38 GB total · est 12 min · downloads continue in background</span>
      </div>

      <div className="fr-prog-list">
        {HAL0_DATA.downloads.map((d, i) => (
          <div key={i} className="dl-row">
            <div className="dl-name mono">
              {d.name}
              <span className="sub">{d.repo}{d.rate && d.state === "pulling" ? ` · ${d.rate} · ${d.eta} remaining` : ""}</span>
            </div>
            <div className="dl-bar">
              <i className={d.state === "done" ? "ok" : d.state === "error" ? "err" : ""} style={{ width: `${d.pct}%` }} />
            </div>
            <div className={"dl-pct mono" + (d.state === "done" ? " ok" : d.state === "queued" ? " dim" : d.state === "error" ? " err" : "")}>
              {d.state === "done" ? "✓ 100%" : d.state === "queued" ? "queued" : d.state === "error" ? "✗ failed" : `${d.pct}%`}
            </div>
            <div className="dl-state mono">
              {d.state === "pulling" && `${d.done} / ${d.size}`}
              {d.state === "queued" && "waiting"}
              {d.state === "verifying" && "verifying"}
              {d.state === "done" && "complete"}
              {d.state === "paused" && "paused"}
              {d.state === "error" && "shard 2/2 sha256 mismatch"}
              {d.state === "cancelled" && "cancelled"}
            </div>
            {d.state === "error" && (
              <div className="dl-err">
                <span style={{color: "var(--err)", display: "inline-flex"}}>{Icons.warn}</span>
                <span style={{flex: 1}}>
                  <b>{d.name}</b> · corrupted shard 2 of 2 — sha256 mismatch. Retry to re-fetch the bad shard, or skip this model and pull it later from <span className="mono" style={{color: "var(--fg)"}}>/models</span>.
                </span>
                <button className="btn ghost sm" onClick={() => window.__hal0Toast && window.__hal0Toast(`Retrying ${d.name}`, "info")}>{Icons.restart} Retry</button>
                <button className="btn ghost sm" onClick={() => window.__hal0Toast && window.__hal0Toast(`Skipped ${d.name} — install later from /models`, "warn")}>Skip this model</button>
              </div>
            )}
          </div>
        ))}
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
  return (
    <div className="fr">
      {frStage === "pick" && (
        <FirstRunPicker
          onPick={b => { setFrBundle(b); setFrStage("confirm"); }}
          onSkip={() => setSkipOpen(true)}
          layout={layout}
        />
      )}
      {frStage === "confirm" && (
        <FirstRunConfirm
          bundleId={frBundle}
          onBack={() => setFrStage("pick")}
          onInstall={() => setFrStage("progress")}
        />
      )}
      {frStage === "progress" && (
        <FirstRunProgress onDone={() => onComplete()} />
      )}
      <SkipBundleDialog
        open={skipOpen}
        onCancel={() => setSkipOpen(false)}
        onConfirm={() => { setSkipOpen(false); onComplete(); }}
      />
    </div>
  );
}

Object.assign(window, { FirstRunView, FirstRunPicker, FirstRunConfirm, FirstRunProgress, BundleGrid, BundleTable });
