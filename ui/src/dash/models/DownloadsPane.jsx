// The Models page's Downloads pane: every model pull the server knows about, with progress,
// cancel, retry and dismiss. It sits under the detail card on each tab that can start a pull
// (GGUF pulls and FLM downloads are the same pull job), and on Settings → Loaded models.
import { useState } from 'react'
import { apiPost } from '@/api/client'
import { ENDPOINTS } from '@/api/endpoints'
import { fmtBytes, fmtEta, fmtSpeed, pullFileLabel, useClearPullJob, usePullsList } from '@/api/hooks/useModels'

// ── DownloadRow ───────────────────────────────────────────────────────
// One row in the Downloads pane. Owns its own cancelling state via
// useState — must be its own component because it is rendered from a
// list (jobs.map). Calling a hook inside a .map() callback causes
// "Rendered more hooks than during the previous render" (React #310)
// the moment the job list changes length.
export function DownloadRow({ job, clearJob }) {
  const j = job;
  const pct = j.bytes_total ? Math.round((j.bytes_downloaded / (j.bytes_total || 1)) * 100) : 0;
  const isRunning = j.state === 'running';
  const isQueued = j.state === 'queued';
  const isCompleted = j.state === 'completed';
  const isFailed = j.state === 'failed';
  const isCancelled = j.state === 'cancelled';
  const [cancelling, setCancelling] = useState(false);
  const doCancel = async () => {
    setCancelling(true);
    try {
      await apiPost(ENDPOINTS.modelPullCancel(j.model_id));
    } catch (e) {
      window.__hal0Toast && window.__hal0Toast(
        `Cancel failed — ${e?.message || "see logs"}`, "err",
      );
    } finally {
      setCancelling(false);
    }
  };
  return (
    <div style={{padding: "12px 16px", borderBottom: "1px solid var(--line-soft)"}}>
      <div style={{display: "flex", justifyContent: "space-between", fontFamily: "var(--jbm)", fontSize: 11.5, marginBottom: 6, alignItems: "center", gap: 8}}>
        <span style={{
          color: isCompleted ? "var(--ok)" : isCancelled ? "var(--fg-4)" : isFailed ? "var(--err)" : "var(--fg)",
          overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap", flex: 1,
        }}>
          {j.hf_repo || j.model_id}
          {j.dest_path && <span style={{color: "var(--fg-4)", marginLeft: 6, fontSize: 10}}>→ {j.dest_path}</span>}
        </span>
        <span style={{color: isCompleted ? "var(--ok)" : isFailed ? "var(--err)" : "var(--fg-3)", fontSize: 11}}>
          {isRunning && `${pct}%`}
          {isQueued && "queued"}
          {isCompleted && "✓ done"}
          {isFailed && "failed"}
          {isCancelled && "cancelled"}
        </span>
      </div>
      {!isCompleted && !isFailed && !isCancelled && (
        <div className="dl-bar" style={{height: 4, marginBottom: 4}}>
          <i style={{width: `${pct}%`, background: "var(--accent)"}} />
        </div>
      )}
      {isRunning && (
        <div style={{display: "flex", justifyContent: "space-between", fontFamily: "var(--jbm)", fontSize: 10, color: "var(--fg-4)", marginBottom: 4}}>
          <span>
            {fmtBytes(j.bytes_downloaded)} / {fmtBytes(j.bytes_total)}
            {pullFileLabel(j.files || []) && ` · ${pullFileLabel(j.files || [])}`}
          </span>
          <span>{fmtSpeed(j.speed_bps)} · {fmtEta(j.eta_s)}</span>
        </div>
      )}
      {isFailed && j.error && (
        <div style={{marginBottom: 6, padding: "6px 10px", background: "var(--err-soft)", border: "1px solid var(--err-line)", borderRadius: "var(--rad-sm)", fontFamily: "var(--jbm)", fontSize: 11, color: "var(--err)"}}>
          {j.error.message || "Download failed"}
        </div>
      )}
      <div style={{display: "flex", gap: 4}}>
        {(isRunning || isQueued) && (
          <button className="btn ghost sm" onClick={doCancel} disabled={cancelling}>
            {cancelling ? "Cancelling…" : "Cancel"}
          </button>
        )}
        {isFailed && (
          <>
            <button className="btn ghost sm" onClick={() => apiPost(ENDPOINTS.modelPull(j.model_id))}>↻ Retry</button>
            <button className="btn ghost sm" onClick={() => clearJob.mutate(j.model_id)}>Clear</button>
          </>
        )}
        {isCompleted && (
          <button className="btn ghost sm" onClick={() => clearJob.mutate(j.model_id)}>Dismiss</button>
        )}
        {isCancelled && (
          <button className="btn ghost sm" onClick={() => clearJob.mutate(j.model_id)}>Clear</button>
        )}
      </div>
    </div>
  );
}

// ── DownloadsPane ─────────────────────────────────────────────────────
export function DownloadsPane() {
  const { jobs } = usePullsList();
  const clearJob = useClearPullJob();
  return (
    <div className="mdl-dl">
      <div className="mdl-dl-h">
        <span>Downloads</span>
        <span className="ct mono">{jobs.length}</span>
      </div>
      {jobs.length === 0 ? (
        <div style={{padding: "32px 16px", textAlign: "center", color: "var(--fg-4)", fontFamily: "var(--jbm)", fontSize: 12}}>
          <div style={{marginBottom: 6}}>No active downloads.</div>
          <div style={{fontSize: 11, color: "var(--fg-5)"}}>Add a model from the catalog or via "Add by HF coords".</div>
        </div>
      ) : (
        jobs.map((j) => (
          <DownloadRow key={j.job_id || j.model_id} job={j} clearJob={clearJob} />
        ))
      )}
    </div>
  );
}
