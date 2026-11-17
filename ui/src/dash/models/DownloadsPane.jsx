// The Models page's Downloads pane: one queue for every kind of model download, with progress,
// cancel, retry and dismiss. It sits under the detail card on every tab, and on Settings → Loaded
// models.
//
// Two job systems feed it. GGUF pulls and FLM downloads are the same pull job (GET
// /api/models/pulls). Image-model installs are ComfyUI's own jobs (GET /api/comfyui/jobs): those
// are listed while they run or after they fail, since a finished install has nothing to dismiss
// (the server keeps the latest job per model and has no clear call).
import { useState } from 'react'
import { isImageJobActive } from '@/api/hooks/imageJobState'
import { useImageJobCancel, useImageJobs, useImageModelInstall } from '@/api/hooks/useComfyui'
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
// One image-model install. The job names its model by id; the Image tab shows the rest.
export function ImageJobRow({ job }) {
  const cancel = useImageJobCancel()
  const install = useImageModelInstall()
  const failed = job.state === 'failed'
  const pct = job.bytes_total > 0 ? Math.round((job.bytes_downloaded / job.bytes_total) * 100) : 0
  const act = async (run, what) => {
    try {
      await run.mutateAsync(job.model_id)
    } catch (e) {
      window.__hal0Toast && window.__hal0Toast(`${what} failed — ${e?.message || 'see logs'}`, 'err')
    }
  }
  return (
    <div className="mdl-dl-row" data-testid={`mdl-dl-image-${job.model_id}`}>
      <div className="mdl-dl-line">
        <span className={'mdl-dl-name' + (failed ? ' err' : '')}>
          {job.model_id}
          <span className="mdl-dl-kind">image model</span>
        </span>
        <span className={failed ? 'err' : ''}>{failed ? 'failed' : job.state === 'queued' ? 'queued' : `${pct}%`}</span>
      </div>
      {!failed && (
        <div className="dl-bar" style={{ height: 4, marginBottom: 4 }}>
          <i style={{ width: `${pct}%`, background: 'var(--accent)' }} />
        </div>
      )}
      {!failed && job.bytes_total > 0 && (
        <div className="mdl-dl-sub">
          {fmtBytes(job.bytes_downloaded)} / {fmtBytes(job.bytes_total)}
        </div>
      )}
      {failed && job.error && <div className="mdl-dl-err">{job.error}</div>}
      <div style={{ display: 'flex', gap: 4 }}>
        {failed ? (
          <button type="button" className="btn ghost sm" disabled={install.isPending} onClick={() => act(install, 'Retry')}>
            ↻ Retry
          </button>
        ) : (
          <button type="button" className="btn ghost sm" disabled={cancel.isPending} onClick={() => act(cancel, 'Cancel')}>
            {cancel.isPending ? 'Cancelling…' : 'Cancel'}
          </button>
        )}
      </div>
    </div>
  )
}

export function DownloadsPane() {
  const { jobs } = usePullsList();
  const clearJob = useClearPullJob();
  const imageJobs = (useImageJobs().data ?? []).filter((j) => isImageJobActive(j) || j.state === 'failed');
  const total = jobs.length + imageJobs.length;
  return (
    <div className="mdl-dl" data-testid="mdl-downloads">
      <div className="mdl-dl-h">
        <span>Downloads</span>
        <span className="ct mono">{total}</span>
      </div>
      {total === 0 ? (
        <div style={{padding: "32px 16px", textAlign: "center", color: "var(--fg-4)", fontFamily: "var(--jbm)", fontSize: 12}}>
          <div style={{marginBottom: 6}}>No active downloads.</div>
          <div style={{fontSize: 11, color: "var(--fg-5)"}}>Model pulls, FLM downloads and image-model installs all show here.</div>
        </div>
      ) : (
        <>
          {jobs.map((j) => (
            <DownloadRow key={j.job_id || j.model_id} job={j} clearJob={clearJob} />
          ))}
          {imageJobs.map((j) => (
            <ImageJobRow key={`image:${j.model_id}`} job={j} />
          ))}
        </>
      )}
    </div>
  );
}
