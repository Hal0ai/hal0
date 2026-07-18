// ─── Restart hal0-api panel ──────────────────────────────────────────────────
//
// Uses the whitelisted one-click repair endpoint (hal0-api.service is in
// _REPAIRABLE_UNITS). The POST's connection is EXPECTED to drop — the API
// restarts underneath the request — so both success and network error enter
// a health-poll loop against /api/health until the service answers again.
//
// Extracted verbatim from settings.jsx (~2492-2573, P3-ui split phase 1).
// Risk #5 (spec): ConfirmDialog and window.__hal0Toast were implicit
// window-globals in the original. ConfirmDialog is threaded here as a real
// import; window.__hal0Toast stays a global — it's a runtime-installed
// toast-host singleton (see ui/src/dash/main.jsx), not a static export, so
// there's nothing to import it from.
import { useState, useRef, useEffect } from 'react'
import { useQueryClient } from '@tanstack/react-query'
import { useServiceRepair } from '@/api/hooks/useServicesHealth'
import { ConfirmDialog } from '../../primitives.jsx'
import { SRow } from './SRow.jsx'

export function RestartApiPanel() {
  const repair = useServiceRepair();
  const qc = useQueryClient();
  const [confirmOpen, setConfirmOpen] = useState(false);
  const [waiting, setWaiting] = useState(false);
  const pollRef = useRef(null);
  // Unmount guard: the tick may be awaiting fetch() when the component
  // unmounts — clearing the timer alone wouldn't stop it from rescheduling
  // or firing toasts/invalidation after navigation.
  const cancelledRef = useRef(false);

  useEffect(() => () => {
    cancelledRef.current = true;
    if (pollRef.current) clearTimeout(pollRef.current);
  }, []);

  const pollHealth = (deadline) => {
    pollRef.current = setTimeout(async () => {
      try {
        const res = await fetch("/api/health", { headers: { Accept: "application/json" } });
        if (cancelledRef.current) return;
        if (res.ok) {
          setWaiting(false);
          window.__hal0Toast && window.__hal0Toast("hal0-api is back online", "ok");
          qc.invalidateQueries();
          return;
        }
      } catch { /* still restarting */ }
      if (cancelledRef.current) return;
      if (Date.now() < deadline) pollHealth(deadline);
      else {
        setWaiting(false);
        window.__hal0Toast && window.__hal0Toast("hal0-api didn't come back within 90s — check journalctl -u hal0-api", "err");
      }
    }, 1500);
  };

  const doRestart = () => {
    setWaiting(true);
    window.__hal0Toast && window.__hal0Toast("Restarting hal0-api — brief outage expected", "warn");
    // The request racing the restart means BOTH outcomes are normal here.
    repair.mutate("hal0-api.service", {
      onSettled: () => pollHealth(Date.now() + 90_000),
    });
  };

  return (
    <div className="s-panel">
      <SRow
        k="hal0-api service"
        sub="Control plane · dashboard, slot manager, /v1 proxy. Restart to apply settings marked ⟳ restart hal0-api or ⚠ manual restart."
        v={waiting
          ? <span className="mono" style={{color: "var(--warn)", fontSize: 11}}>restarting — waiting for /api/health…</span>
          : <span className="mono" style={{color: "var(--fg-3)", fontSize: 11}}>running</span>}
        actions={
          <button className="btn danger sm" disabled={waiting || repair.isPending} onClick={() => setConfirmOpen(true)}>
            {waiting ? "Restarting…" : "Restart hal0-api"}
          </button>
        }
      />
      <ConfirmDialog
        open={confirmOpen}
        onCancel={() => setConfirmOpen(false)}
        onConfirm={() => { setConfirmOpen(false); doRestart(); }}
        title="Restart hal0-api?"
        message={
          <span>
            In-flight requests (including chat completions) will drop while the service restarts —
            typically a few seconds. Slot containers keep running; the dashboard reconnects automatically.
          </span>
        }
        confirmLabel="Restart"
      />
    </div>
  );
}
