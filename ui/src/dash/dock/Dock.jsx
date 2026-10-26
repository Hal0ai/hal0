// hal0 dashboard — the dock: the global status footer (runtime / service
// pips, update chip, live journal + downloads panes). Moved verbatim from
// chrome.jsx's `Footer`; later tasks grow it into the status dock.

import { useState, useEffect } from 'react'
import { useRuntimeRollup, useHealthSystem, failingChecks } from '@/api/hooks/useRuntime'
import { useLogsStream } from '@/api/hooks/useLogs'
import { useSlots } from '@/api/hooks/useSlots'
import { usePullsList, useClearPullJob, fmtSpeed, fmtEta, pullFileLabel } from '@/api/hooks/useModels'
import { apiPost } from '@/api/client'
import { ENDPOINTS } from '@/api/endpoints'
import { useUpdateState } from '@/api/hooks/useUpdates'
import { useServicesHealth } from '@/api/hooks/useServicesHealth'
import { slotIndicatorFromPhase } from '../slot-status.js'
import { _srcGroup, _srcHue, _shortTs, _svcPip, highlightFoot } from '../chrome.jsx'

// ─── Dock (the global status footer) ───
// Phase 3 of #322: the journal pane reads `useLogsStream` against
// /api/journal/stream (no more HAL0_DATA.journal fallback) and the
// update chip reads `useUpdateState` directly (no more `updateAvailable`
// prop threaded from main.jsx — the chip is self-driven).
export function Dock({ route, param, expanded = false, onToggle }) {
  // Runtime rollup gives live status + slot readiness counts.
  // useLogsStream subscribes to /api/journal/stream when the pane is
  // expanded (saves opening an SSE we don't render).
  const L = useRuntimeRollup();
  const health = useHealthSystem();
  const serviceHealth = useServicesHealth();
  const [paneSrc, setPaneSrc] = useState("merged");
  const [paneQ, setPaneQ] = useState("");
  const [footerTab, setFooterTab] = useState("journal");
  // Reset to journal when pane collapses
  useEffect(() => {
    if (!expanded) setFooterTab("journal");
  }, [expanded]);
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
    return () => {
      window.removeEventListener("hal0:pull-started", onPullStarted);
      window.removeEventListener("hal0:open-downloads", onPullStarted);
    };
  }, [expanded, onToggle]);
  const { jobs, hasActive } = usePullsList({ enabled: expanded && footerTab === "downloads" });
  const clearJob = useClearPullJob();
  // Stream the full (merged) event feed and filter by source group
  // CLIENT-SIDE. This keeps every source chip available regardless of the
  // active filter (deriving chips from a server-narrowed ring would drop
  // the other groups) and makes switching instant — no SSE reconnect.
  const live = useLogsStream({ follow: expanded });

  // The ring is the SOLE source of truth — no HAL0_DATA fallback. When
  // empty (cold load before SSE replays or a genuinely quiet system)
  // the body renders an empty-state placeholder below.
  const ringSorted = [...(live.ring || [])].sort((a, b) =>
    (a.ts || '').localeCompare(b.ts || ''),
  );

  // Distinct source groups present in the ring → the chip set. `merged` is
  // always first (no filter); the rest come from real event sources.
  const srcGroups = ["merged", ...[...new Set(ringSorted.map((e) => _srcGroup(e.source)))].sort()];

  // Source chip filters by group (`slot` matches every `slot:*`); search is
  // client-only against `entry.msg` for instant feedback while typing.
  const filtered = ringSorted.filter((e) => {
    if (paneSrc !== 'merged' && _srcGroup(e.source) !== paneSrc) return false;
    return !(paneQ && (!e.msg || !e.msg.toLowerCase().includes(paneQ.toLowerCase())));
  });

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
  const configuredSlots = (useSlots().data || []).filter((s) => !!(s.model || s.model_default));
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
        <>
          <div className="foot-tabs">
            <button className={"foot-tab" + (footerTab === "journal" ? " active" : "")} onClick={() => setFooterTab("journal")}>journal</button>
            <button className={"foot-tab" + (footerTab === "downloads" ? " active" : "")} onClick={() => setFooterTab("downloads")}>downloads</button>
          </div>
          {footerTab === "journal" ? (
        <div className="foot-pane">
          <div className="foot-pane-h mono">
            <span>Live journal</span>
            <span className="ct">· {filtered.length} / {ringSorted.length}</span>
            <div className="foot-pane-filter mono">
              {srcGroups.map((k) => (
                <button
                  key={k}
                  onClick={() => setPaneSrc(k)}
                  className={"foot-pane-chip" + (paneSrc === k ? " on" : "")}
                >{k}</button>
              ))}
            </div>
            <input
              className="foot-pane-search mono"
              value={paneQ}
              onChange={e => setPaneQ(e.target.value)}
              placeholder="search…"
            />
            <span style={{display: "inline-flex", gap: 10, marginLeft: "auto"}}>
              <span style={{color: live.disconnected ? "var(--warn)" : "var(--ok)", display: "inline-flex", alignItems: "center", gap: 5}}>
                <span className={"dot" + (live.disconnected ? "" : " ready")} />
                {live.disconnected ? "reconnecting…" : "follow tail"}
              </span>
              <a href="#logs" className="foot-pane-link">Open full logs →</a>
            </span>
          </div>
          <div className="foot-pane-body">
            {ringSorted.length === 0 ? (
              <div className="foot-pane-empty" style={{padding: 24, textAlign: "center", color: "var(--fg-4)", fontFamily: "var(--jbm)", fontSize: 11.5}}>
                No events yet
              </div>
            ) : filtered.length === 0 ? (
              <div style={{padding: 24, textAlign: "center", color: "var(--fg-4)", fontFamily: "var(--jbm)", fontSize: 11.5}}>
                No journal entries match. <span style={{color: "var(--accent)", cursor: "pointer"}} onClick={() => { setPaneSrc("merged"); setPaneQ(""); }}>Clear filters</span>
              </div>
            ) : filtered.map((e, i) => (
              <div key={e.id ?? i} className={"foot-line " + e.level} style={{"--src": _srcHue(e.source)}}>
                <span className="stripe" />
                <span className="ts">{_shortTs(e.ts)}</span>
                <span className={"src " + e.source}><span className="d" />{e.source}</span>
                <span className="lvl">{e.level}</span>
                <span className="msg">{paneQ ? highlightFoot(e.msg, paneQ) : e.msg}</span>
              </div>
            ))}
          </div>
        </div>
      
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
        </>
      )}
      <div className="foot-chips">
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
        </button>
      </div>
      {/*
        Always-visible footer journal ribbon: a thin tail of the most
        recent entries. Pulls from the same SSE ring; renders nothing
        before the first frame arrives so we never hardcode mock copy.
      */}
      {ringSorted.length > 0 && (
        <div className="foot-journal mono" aria-label="recent journal">
          {ringSorted.slice(-6).map((e, i) => (
            <span key={e.id ?? i} className={"ent " + e.level} style={{"--src": _srcHue(e.source)}}>
              <span className="d" />
              <span className="src">{e.source}</span>
              <span className="ts">{_shortTs(e.ts)}</span>
              <span className="msg">{e.msg}</span>
            </span>
          ))}
        </div>
      )}
    </div>
  );
}
