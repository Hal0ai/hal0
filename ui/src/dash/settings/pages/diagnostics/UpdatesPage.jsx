// DIAGNOSTICS ▸ Updates — signed self-update (matches spec (b)
// DIAGNOSTICS▸Doctor/Bundle/Updates: updates = E(/api/updater/*)).
// Extracted verbatim from settings.jsx UpdatesSection (P3-ui split phase 1).
// The `id` stays "updates" (unchanged) so #settings/updates deep links keep
// working.
import { useState, useRef, useEffect } from 'react'
import { useUpdateState, useUpdateCheck, useUpdateApply, useUpdateJob, useSetUpdateChannel, useUpdateRollback } from '@/api/hooks/useUpdates'
import { ConfirmDialog } from '../../../primitives.jsx'
import { SRow } from '../../shared/SRow.jsx'

export function UpdatesPage() {
  // Phase B1: live state + check + apply mutations. While the query is
  // in flight or 5xx'd we render an empty envelope and let the SRow
  // fallbacks show '—' rather than fabricated versions.
  // Issue #546: channel switch (stable | preview | nightly) is wired to
  // useSetUpdateChannel → PUT /api/updates/channel; reads the current
  // value from useUpdateState().hal0.channel on load.
  const stateQuery = useUpdateState();
  const checkM = useUpdateCheck();
  const applyM = useUpdateApply();
  const setChannelM = useSetUpdateChannel();
  const rollbackM = useUpdateRollback();
  const [rollbackConfirm, setRollbackConfirm] = useState(false);
  // Optional version pin — parity with `hal0 update --target`. Empty
  // installs the channel's latest.
  const [pinVersion, setPinVersion] = useState("");
  const u = stateQuery.data || { hal0: {}, flm: {} };

  // The current channel lives on each per-component envelope (both
  // populated from telemetry.channel in hal0.toml); hal0.channel is
  // authoritative for the switch's initial value.
  const currentChannel = u.hal0?.channel || 'stable';

  // Track the most recent apply job so the user sees the backend's
  // verdict, not just the 202 ack. Toasts fire once on terminal state.
  const [jobId, setJobId] = useState(null);
  const lastTerminalJob = useRef(null);
  const { job, terminal } = useUpdateJob(jobId);
  useEffect(() => {
    if (!terminal || !job || lastTerminalJob.current === job.id) return;
    lastTerminalJob.current = job.id;
    if (job.state === 'applied') {
      window.__hal0Toast && window.__hal0Toast(`Updated to ${job.version || 'latest'} — services restarted`, "ok");
    } else {
      const detail = job.error || job.error_code || 'unknown';
      window.__hal0Toast && window.__hal0Toast(`Update failed: ${detail}`, "err");
    }
  }, [terminal, job]);

  const jobBusy = job && (job.state === 'queued' || job.state === 'running');
  const jobLabel = jobBusy
    ? (job.state === 'queued' ? 'queued…' : 'installing…')
    : null;

  return (
    <div className="s-section">
      <h2>Updates</h2>
      <p className="desc">Signed self-update. hal0 verifies a Sigstore signature before swapping binaries. Per-channel pins.</p>
      <div className="s-panel">
        <SRow
          k="hal0"
          sub="Dashboard + API + CLI"
          mono
          v={<>
            {u.hal0?.available
              ? <><span style={{color: "var(--accent)"}}>{u.hal0.available} available</span> <span style={{color: "var(--fg-4)"}}>· current {u.hal0.current}</span></>
              : <span>current {u.hal0?.current}</span>}
            {jobLabel && <span style={{marginLeft: 8, color: "var(--warn)", fontFamily: "var(--jbm)", fontSize: 11}}>· {jobLabel}</span>}
          </>}
          actions={<>
            <button
              className="btn sm"
              disabled={(!u.hal0?.available && !pinVersion.trim()) || applyM.isPending || !!jobBusy}
              onClick={() => {
                applyM.mutate(pinVersion.trim() || undefined, {
                  onSuccess: (snap) => {
                    setJobId(snap?.id || null);
                    window.__hal0Toast && window.__hal0Toast("Update started — brief outage during restart", "warn");
                  },
                  onError: (err) => {
                    const msg = (err && err.message) || "could not start update";
                    window.__hal0Toast && window.__hal0Toast(`Update failed: ${msg}`, "err");
                  },
                });
              }}
            >{applyM.isPending ? "Starting…" : (jobBusy ? "Installing…" : "Install update")}</button>
            <button
              className="btn ghost sm"
              disabled={checkM.isPending}
              onClick={() => checkM.mutate(undefined, {
                onError: (err) => {
                  const msg = (err && err.message) || "check failed";
                  window.__hal0Toast && window.__hal0Toast(`Check failed: ${msg}`, "err");
                },
              })}
            >{checkM.isPending ? "Checking…" : "Check"}</button>
            <button
              className="btn ghost sm"
              disabled={rollbackM.isPending || !!jobBusy}
              onClick={() => setRollbackConfirm(true)}
            >{rollbackM.isPending ? "Rolling back…" : "Roll back"}</button>
            <a className="btn ghost sm" href="https://hal0.dev/changelog" target="_blank" rel="noreferrer">Changelog →</a>
          </>}
        />
        {u.hal0?.revoked && (
          <SRow
            k="Release notice"
            sub="The latest release on this channel was withdrawn"
            v={<span style={{color: "var(--warn)"}}>
              {u.hal0.revoked_version ? `${u.hal0.revoked_version} was revoked` : "latest release revoked"}
              {u.hal0.revoked_reason ? ` — ${u.hal0.revoked_reason}` : ""}
            </span>}
          />
        )}
        <SRow
          k="flm"
          sub="Manual deb · vendor-supplied"
          mono
          v={u.flm?.current || '—'}
        />
        <SRow
          k="Pin version"
          sub="Install a specific release instead of the channel's latest · CLI parity: hal0 update --target"
          mono
          v={
            <input
              value={pinVersion}
              onChange={e => setPinVersion(e.target.value)}
              placeholder="empty = latest"
              className="mono"
              style={{fontFamily: "var(--jbm)", fontSize: 11, background: "var(--bg-2)", color: "var(--fg)", border: "1px solid var(--line)", borderRadius: 4, padding: "3px 6px", width: 160}}
            />
          }
        />
        <SRow
          k="Auto-check"
          sub="Background update checks by the daemon"
          mono
          v={stateQuery.data ? (u.autoCheck ? <span style={{color: "var(--ok)"}}>enabled</span> : <span style={{color: "var(--fg-4)"}}>disabled</span>) : "—"}
        />
        <SRow
          k="Channel"
          sub="Release track · persisted to hal0.toml"
          v={
            <select
              className="input mono"
              value={currentChannel}
              disabled={setChannelM.isPending}
              onChange={(e) => {
                const next = e.target.value;
                if (next === 'stable' || next === 'preview' || next === 'nightly') {
                  if (next === currentChannel) return;
                  setChannelM.mutate(next, {
                    onSuccess: () => {
                      window.__hal0Toast && window.__hal0Toast(`Channel set to ${next}`, "ok");
                    },
                    onError: (err) => {
                      const msg = (err && err.message) || "could not set channel";
                      window.__hal0Toast && window.__hal0Toast(`Channel change failed: ${msg}`, "err");
                    },
                  });
                }
              }}
              style={{maxWidth: 160}}
            >
              <option value="stable">stable</option>
              <option value="preview">preview</option>
              <option value="nightly">nightly</option>
            </select>
          }
        />
      </div>
      <ConfirmDialog
        open={rollbackConfirm}
        onCancel={() => setRollbackConfirm(false)}
        onConfirm={() => {
          setRollbackConfirm(false);
          rollbackM.mutate(undefined, {
            // The backend swaps the version symlink but does NOT restart
            // services — the running hal0-api keeps serving the current
            // build until the operator bounces it.
            onSuccess: () => window.__hal0Toast && window.__hal0Toast("Rolled back — restart hal0-api (Settings → Advanced) to run the previous version", "warn"),
            onError: (err) => window.__hal0Toast && window.__hal0Toast(`Rollback failed: ${err?.message || "no previous version retained"}`, "err"),
          });
        }}
        title="Roll back hal0?"
        message={
          <span>
            Reverts the installed tree to the previous retained version{u.hal0?.current ? <> (currently on <b className="mono">{u.hal0.current}</b>)</> : null}.
            The running service keeps serving until you restart hal0-api (Settings → Advanced).
            If no previous version is retained, nothing changes.
          </span>
        }
        confirmLabel="Roll back"
      />
    </div>
  );
}
