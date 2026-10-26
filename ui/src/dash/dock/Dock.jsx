// hal0 dashboard — the dock: the global status footer (runtime / service
// pips, update chip, live journal + downloads panes). Moved verbatim from
// chrome.jsx's `Footer`; later tasks grow it into the status dock.

import { useState, useEffect, useMemo, useRef } from 'react'
import { useRuntimeRollup, useHealthSystem, failingChecks } from '@/api/hooks/useRuntime'
import { useLogsStream } from '@/api/hooks/useLogs'
import { useActivityStream } from '@/api/hooks/useActivity'
import { useSlots } from '@/api/hooks/useSlots'
import { usePullsList, useClearPullJob, fmtSpeed, fmtEta, pullFileLabel } from '@/api/hooks/useModels'
import { apiPost } from '@/api/client'
import { ENDPOINTS } from '@/api/endpoints'
import { useUpdateState } from '@/api/hooks/useUpdates'
import { useServicesHealth } from '@/api/hooks/useServicesHealth'
import { slotIndicatorFromPhase } from '../slot-status.js'
import { isImageGenRoute } from '../slots-route'
import { useImageRunStore } from '@/stores/useImageRunStore'
import { useStatusLineActions } from '../image/useStatusLineActions'
import { ForceStopConfirm, PinConfirm } from './gpu-parts.jsx'
import { useGpuStatus } from './useGpuStatus'
import { GpuSegment } from './GpuSegment.jsx'
import { GpuPane } from './GpuPane.jsx'
import { EventsPane, EventRow } from './EventsPane.jsx'
import { DOCK_FILTERS_DEFAULT, TICKER_ROWS, mergeFeeds, newRows, slotOfEvent } from './dock-pure'
import { _svcPip } from '../chrome.jsx'

// ─── Dock (the global status footer) ───
// The events pane and the collapsed ticker read the journal (`useLogsStream`, /api/journal/stream)
// and the activity trail (`useActivityStream`) merged into one timeline; the update chip reads
// `useUpdateState` directly (self-driven).
export function Dock({ route, param, expanded = false, onToggle }) {
  // Runtime rollup gives live status + slot readiness counts.
  const L = useRuntimeRollup();
  const health = useHealthSystem();
  const serviceHealth = useServicesHealth();
  const [filters, setFilters] = useState(DOCK_FILTERS_DEFAULT);
  const [footerTab, setFooterTab] = useState("journal");
  // The GPU state, computed once for the collapsed segment (and, later, the pane). /status polls
  // at its active cadence on the Image Gen route or while a run of this browser is in flight.
  const imageRunning = useImageRunStore((s) => s.run?.running === true);
  const gpu = useGpuStatus({ active: isImageGenRoute(route, param) || imageRunning });
  // A failed action's message goes when the line's state changes (the kind), not when its text
  // does: the headline counts seconds and steps.
  const gpuActions = useStatusLineActions({ resetKey: gpu.view?.kind ?? null });
  const openGpu = () => {
    setFooterTab("gpu");
    if (!expanded && onToggle) onToggle();
  };
  // Reset to journal when pane collapses
  useEffect(() => {
    if (!expanded) setFooterTab("journal");
  }, [expanded]);
  // The GPU tab is hidden while there is nothing to show; an open one falls back to the events.
  const gpuTabVisible = gpu.view != null;
  useEffect(() => {
    if (!gpuTabVisible && footerTab === "gpu") setFooterTab("journal");
  }, [gpuTabVisible, footerTab]);
  // Auto-expand and switch to downloads on pull-started
  useEffect(() => {
    const onPullStarted = () => {
      setFooterTab("downloads");
      if (!expanded && onToggle) onToggle();
    };
    window.addEventListener("hal0:pull-started", onPullStarted);
    // The notification bell's "Open downloads pane →" row reuses the same
    // expand-and-switch behaviour.
    window.addEventListener("hal0:open-downloads", onPullStarted);
    // The agent cards' "Activity" link opens the events tab.
    const onOpenEvents = () => {
      setFooterTab("journal");
      if (!expanded && onToggle) onToggle();
    };
    window.addEventListener("hal0:open-events", onOpenEvents);
    return () => {
      window.removeEventListener("hal0:pull-started", onPullStarted);
      window.removeEventListener("hal0:open-downloads", onPullStarted);
      window.removeEventListener("hal0:open-events", onOpenEvents);
    };
  }, [expanded, onToggle]);
  const { jobs, hasActive } = usePullsList({ enabled: expanded && footerTab === "downloads" });
  const clearJob = useClearPullJob();
  // Both streams stay open while the dock is mounted: the ticker and the unseen badge need them
  // collapsed. Filters apply client-side (instant, and every chip stays available).
  const live = useLogsStream({ follow: true });
  const activity = useActivityStream({ follow: true, kind: 'action', limit: 200 });
  const events = useMemo(
    () => mergeFeeds(live.ring || [], activity.records || []),
    [live.ring, activity.records],
  );

  // News is counted per stream. The journal and the activity trail are two connections: whichever
  // delivers its first batch second would otherwise read as new. A stream's first batch is its
  // backfill (`ready` flips in the effect below, after the render that first holds rows), and only
  // rows from a stream that was already ready on the previous render count.
  const journalLen = (live.ring || []).length;
  const activityLen = (activity.records || []).length;
  const readyRef = useRef({ j: false, a: false });
  // Unseen: rows appended while the dock is collapsed. The seen set is re-taken while expanded and
  // when the ring shrinks (a reset). Ids, not a count: a ring at its cap keeps its length while
  // new rows roll in.
  const seenRef = useRef({ ids: new Set(), size: 0 });
  const shrunk = events.length < seenRef.current.size;
  const unseen = expanded || shrunk ? 0 : newRows(events, seenRef.current.ids, readyRef.current).length;
  // hal0:slot-event goes out once per new row about a slot, with its own all-ids dedupe.
  const slotSeenRef = useRef(new Set());
  useEffect(() => {
    for (const e of newRows(events, slotSeenRef.current, readyRef.current)) {
      const slot = slotOfEvent(e);
      if (slot) window.dispatchEvent(new CustomEvent("hal0:slot-event", { detail: { slot } }));
    }
    slotSeenRef.current = new Set(events.map((e) => e.id));
    const prev = seenRef.current;
    if (expanded || events.length < prev.size) {
      seenRef.current = { ids: new Set(events.map((e) => e.id)), size: events.length };
    } else {
      // Backfill (rows of a stream not yet ready) is never news; fresh rows stay unseen.
      const ids = new Set(prev.ids);
      for (const e of events) if (!readyRef.current[e.stream]) ids.add(e.id);
      seenRef.current = { ids, size: events.length };
    }
    readyRef.current = { j: journalLen > 0, a: activityLen > 0 };
  }, [events, expanded, journalLen, activityLen]);

  // Update chip — self-driven from useUpdateState.
  const updates = useUpdateState();
  const updateState = updates.data;
  const hal0Channel = updateState && updateState.hal0;
  const hasUpdate = !!(
    hal0Channel &&
    hal0Channel.available &&
    hal0Channel.available !== hal0Channel.current
  );
  const degraded = health.data?.status === 'degraded';
  const failing = failingChecks(health.data);
  const runtimeTone = degraded ? 'warn' : L.status === 'up' ? 'up' : L.status === 'down' ? 'err' : 'warn';
  const runtimeTitle = degraded
    ? `degraded — ${failing.length ? failing.join("; ") : "see /api/health/system"}`
    : `${L.ready}/${L.total} slot container${L.total === 1 ? "" : "s"} ready`;
  // The `services` group is driven by whatever /api/services/health actually
  // reports (#1899) — NOT a hardcoded id list. The three-member array here
  // used to omit `comfyui` (and would silently omit any future service too),
  // leaving the always-visible footer reporting an unqualified "3 / 3 ready"
  // while a real backing service sat down. `hal0` itself is prepended since
  // it isn't in the services-health payload (it IS the API answering the
  // request) but every backend-reported id — including comfyui — renders a
  // pip and counts toward the ready total.
  //
  // When /api/services/health itself has not answered — first paint, 404
  // (older API), non-2xx, or network error all surface as `pending` with an
  // empty `services` list (useServicesHealth fails soft) — the group must NOT
  // collapse to a green "1 / 1 ready" hal0 self-check: the state of the real
  // backing services is unknown, which is exactly the unqualified-green defect
  // #1899 describes. Render a single warn-toned "services unknown" pip so the
  // count stays honest and the warn styling trips until real data arrives.
  const servicesUnknown =
    (serviceHealth.pending || serviceHealth.error) && !(serviceHealth.services || []).length;
  const footerIndicators = [
    {
      id: 'hal0',
      label: 'hal0',
      tone: degraded ? 'warn' : health.isError ? 'warn' : 'up',
      title: degraded ? `hal0 degraded — ${failing.join("; ") || "see /api/health/system"}` : 'hal0 api ok',
    },
    ...(servicesUnknown
      ? [{
          id: 'services-unknown',
          label: 'services',
          tone: 'warn',
          title: 'service health unknown — /api/services/health not answering',
        }]
      : (serviceHealth.services || []).map((s) => ({
          id: s.id,
          label: s.name || s.id,
          // 'stopped' is deliberate (grey pip, out of the ready count);
          // only a failed/unknown service reads red. Older backends omit
          // `state` — fall back to the boolean.
          tone: s.state === 'stopped' ? 'off' : (s.state ? (s.state === 'up' ? 'up' : 'err') : (s.up ? 'up' : 'err')),
          title: s.detail || `${s.name || s.id} down`,
        }))),
  ];
  // slots group — one LED pip per ACTIVE configured slot (a model bound is
  // what makes a slot real, #1369). Stopped slots render no pip — the ready
  // count already says how many of the total are up. Pip colours mirror the
  // slot-card indicator dot exactly (slotIndicatorFromPhase: serving green
  // pulse · ready yellow · warming amber pulse · error red).
  const allSlots = useSlots().data || [];
  const slotNames = allSlots.map((s) => s.name).filter(Boolean);
  const configuredSlots = allSlots.filter((s) => !!(s.model || s.model_default));
  const slotPips = configuredSlots
    .map((s) => ({ slot: s, ind: slotIndicatorFromPhase(s) }))
    .filter(({ ind }) => ind.cls !== 'offline');
  // Deliberately-stopped services keep a grey pip but leave the ready
  // fraction — "2 / 2 ready" with a grey comfyui pip, not "2 / 3".
  const servicesActive = footerIndicators.filter((s) => s.tone !== 'off');
  const servicesReady = servicesActive.filter((s) => s.tone === 'up').length;

  return (
    <div className={"footer" + (expanded ? " expanded" : "")} data-testid="dock">
      {expanded && (
        <div className="dock-x" data-testid="dock-expanded">
          <div className="foot-tabs">
            <button className={"foot-tab" + (footerTab === "journal" ? " active" : "")} data-testid="dock-tab-events" onClick={() => setFooterTab("journal")}>events</button>
            {gpu.view && (
              <button className={"foot-tab" + (footerTab === "gpu" ? " active" : "")} data-testid="dock-tab-gpu" onClick={() => setFooterTab("gpu")}>
                <span className={"dotc tone-" + gpu.view.tone} aria-hidden="true" />
                gpu
              </button>
            )}
            <button className={"foot-tab" + (footerTab === "downloads" ? " active" : "")} data-testid="dock-tab-downloads" onClick={() => setFooterTab("downloads")}>downloads</button>
          </div>
          {footerTab === "gpu" && gpu.view ? (
            <GpuPane gpu={gpu} actions={gpuActions} />
          ) : footerTab !== "downloads" ? (
            <EventsPane events={events} filters={filters} onFilters={setFilters} slotNames={slotNames} disconnected={live.disconnected} />
          ) : (
            <div className="foot-downloads">
              {jobs.length === 0 ? (
                <div className="foot-dl-empty">
                  No downloads yet — <a href="#models">browse models</a>
                </div>
              ) : (
                jobs.map((j) => {
                  const pct = j.bytes_total ? Math.round((j.bytes_downloaded / (j.bytes_total || 1)) * 100) : 0;
                  const isRunning = j.state === 'running';
                  const isQueued = j.state === 'queued';
                  const isCompleted = j.state === 'completed';
                  const isFailed = j.state === 'failed';
                  const isCancelled = j.state === 'cancelled';
                  const barClass = isCompleted ? ' completed' : isFailed ? ' failed' : '';
                  return (
                    <div key={j.job_id || j.model_id} className="foot-dl-row">
                      <span className={"foot-dl-state " + j.state}>
                        {isRunning ? '⟳' : isQueued ? '⏰' : isCompleted ? '✓' : isFailed ? '✗' : '—'}
                      </span>
                      <span className="foot-dl-repo">
                        {j.hf_repo || j.model_id}
                        {j.dest_path && (<><span className="foot-dl-arrow">→</span><span className="foot-dl-dest">{j.dest_path}</span></>)}
                      </span>
                      <span className="foot-dl-act">
                        {(isRunning || isQueued) && (
                          <button className="cancel" onClick={() => apiPost(ENDPOINTS.modelPullCancel(j.model_id))}>× cancel</button>
                        )}
                        {isFailed && (
                          <>
                            <button className="retry" onClick={() => apiPost(ENDPOINTS.modelPull(j.model_id))}>↻ retry</button>
                            <button onClick={() => clearJob.mutate(j.model_id)}>clear</button>
                          </>
                        )}
                        {isCompleted && (
                          <button onClick={() => clearJob.mutate(j.model_id)}>clear</button>
                        )}
                        {isCancelled && (
                          <button onClick={() => clearJob.mutate(j.model_id)}>clear</button>
                        )}
                      </span>
                      {!isCompleted && !isFailed && !isCancelled && (
                        <div className="foot-dl-bar">
                          <div className="foot-dl-bar-track">
                            <div className={"foot-dl-bar-fill" + barClass} style={{width: pct + '%'}} />
                          </div>
                          <span className="foot-dl-stats">
                            <span>{pct}%</span>
                            {isRunning && pullFileLabel(j.files || []) && <span>{pullFileLabel(j.files || [])}</span>}
                            {isRunning && (j.speed_bps > 0 ? <span>{fmtSpeed(j.speed_bps)}</span> : <span>calculating…</span>)}
                            {isRunning && j.eta_s > 0 && <span>{fmtEta(j.eta_s)} left</span>}
                          </span>
                        </div>
                      )}
                      {!isRunning && !isQueued && isCompleted && (
                        <div className="foot-dl-bar">
                          <div className="foot-dl-bar-track">
                            <div className="foot-dl-bar-fill completed" style={{width: '100%'}} />
                          </div>
                        </div>
                      )}
                      {isFailed && (
                        <div className="foot-dl-err">{j.error?.message || 'Download failed'}</div>
                      )}
                    </div>
                  );
                })
              )}
            </div>
          )}
        </div>
      )}
      <ForceStopConfirm confirm={gpuActions.confirm} />
      <PinConfirm confirm={gpuActions.confirm} />
      <div className="foot-chips dock-row">
        <GpuSegment gpu={gpu} actions={gpuActions} onOpen={openGpu} expanded={expanded} />
        {/* slots — one LED pip per ACTIVE slot (stopped slots hidden) + ready
            count; pip colours mirror the slot-card indicator dots */}
        <div className="foot-health" data-testid="foot-health-runtimes" title={runtimeTitle}>
          <span className="pips" aria-hidden="true">
            {slotPips.map(({ slot: s, ind }, i) => (
              <span
                key={s.name ?? i}
                className={"pip " + ind.cls}
                title={`${s.name ?? "slot"} — ${ind.label}`}
              />
            ))}
          </span>
          <span className="lbl">
            <span className="k">slots</span>
            <span className={"v" + (L.ready < L.total ? " warn" : "")}>
              <b>{L.ready} / {L.total}</b> ready
            </span>
          </span>
        </div>

        {/* services — one LED pip per backing service + ready count */}
        <div className="foot-health" data-testid="foot-health-services" title="service health">
          <span className="pips" aria-label="service health">
            {footerIndicators.map((svc) => (
              <span
                key={svc.id}
                data-service-id={svc.id}
                className={"pip " + _svcPip(svc.tone)}
                title={`${svc.label}: ${svc.title}`}
                aria-label={`${svc.label}: ${svc.tone}`}
              />
            ))}
          </span>
          <span className="lbl">
            <span className="k">services</span>
            <span className={"v" + (servicesReady < servicesActive.length ? " warn" : "")}>
              <b>{servicesReady} / {servicesActive.length}</b> ready
            </span>
          </span>
        </div>

        <span className="foot-spacer" />

        {hasUpdate && (
          <span className="foot-update" title={`hal0 ${hal0Channel.available} available`}>
            <span className="bullet">●</span> <b>hal0 {hal0Channel.available}</b> available
          </span>
        )}
        <button className="foot-toggle" data-testid="dock-toggle" onClick={onToggle} aria-expanded={expanded} aria-label="Toggle dock">
          <span style={{transform: expanded ? "rotate(180deg)" : "rotate(0)", transition: "transform 0.18s ease", display: "inline-block"}}>⌃</span>
          <span>dock</span>
          {unseen > 0 && !expanded && (
            <span className="badge" data-testid="dock-unseen" aria-label={`${unseen} new events`}>+{unseen}</span>
          )}
        </button>
      </div>
      {/* The ticker: the newest TICKER_ROWS merged rows, newest last. Hidden while expanded. */}
      {events.length > 0 && (
        <div className="foot-journal dock-ticker mono" data-testid="dock-ticker" aria-label="recent events">
          {events.slice(-TICKER_ROWS).map((e) => (
            <EventRow key={e.id} e={e} testid="dock-ticker-row" />
          ))}
        </div>
      )}
    </div>
  );
}
