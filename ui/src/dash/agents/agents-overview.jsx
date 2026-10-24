// hal0 dashboard — Agents Overview (the #agent landing tab).
//
// The library of agents as collectible cards. Hermes and Pi are the live
// bundled agents; each card is drawn from one render-ready model:
//   - install, version, units, provision, doctor, degraded reasons
//                      ← window.__hal0UseAgentOverview(name)
//                        (GET /api/agents/{name}/overview, 10s)
//   - agent liveness   ← window.__hal0UseAgents()  (GET /api/agents, 5s), the
//                        fallback when a daemon predates the overview route
//   - throughput + ctx ← window.__hal0UseSlots()    (primary slot metrics) —
//                        Hermes only; Pi is a CLI tool with no backing slot
//   - upgrade / repair jobs ← window.__hal0UseUpdateJob(jobId) (1.5s poll)
//   - the active persona ← window.__hal0UseAgentPersonas("hermes") (5s), for
//                        the persona chip (provision.json goes stale on an
//                        activation)
// After every action the card awaits window.__hal0RefreshAgentOverview, which
// reads the overview with ?refresh=1: a plain refetch would answer from the
// route's 30 s cache with the state from before the action.
// window.__hal0DeriveCardModel (agent-phase.ts) folds those into the model the
// card paints; this module owns the data and the action handlers, the card
// (agent-card.jsx) only renders them.
// The rest of the library (Qwen · OpenCode) are roadmap entries shown behind
// a grey "coming soon" mask with curated dummy content.
//
// Window-globals shim — register on window, read React + hooks + cards from
// the same. No ES imports across dash/* (main.tsx load order is the contract).

import { useQueryClient } from "@tanstack/react-query";

const { useState, useEffect, useRef } = React;

// ── curated identity for the live Hermes card ───────────────────────
// Names, art and descriptions only: whether an ability is unlocked or a skill
// lit comes from the card model. `liveModel` is the model the orchestrated
// agent slot is actually serving (a dash when no slot is resolved).
function _hermesIdentity(liveModel) {
  return {
    id: "hermes",
    name: "Hermes",
    model: liveModel || "—",
    role: "remote control · self-improving · orchestration",
    rarity: 3,
    art: (window.__hal0AgentArt && window.__hal0AgentArt.hermes) || "",
    abilities: [
      { name: "Ghost Relay", cost: 2, desc: "Summon her from any Telegram or Discord thread.", pow: "40" },
      { name: "Engram", cost: 2, desc: "Folds every run back into memory — never relearns.", pow: "60" },
      { name: "Deep Run", cost: 3, desc: "Chains tools for hours, fully AFK.", pow: "90" },
    ],
  };
}

// ── curated identity for the live Pi card ────────────────────────────
// Pi is a CLI tool, not a backing slot — `model` is the provider hal0's pi
// driver wires it to (hal0-provider extension), not a live slot reading.
// As for Hermes, skill chips and their lit state come from the card model.
function _piIdentity() {
  return {
    id: "pi",
    name: "Pi",
    model: "hal0/agent · cli tool",
    role: "autonomous coding · repo-aware engineering",
    rarity: 2,
    el: "#6f7785",
    elGlow: "rgba(143,160,179,0.16)",
    logo: (window.__hal0AgentArt && window.__hal0AgentArt.pi) || "",
    logoScale: 0.5,
    abilities: [
      { name: "Slot Sync", cost: 1, desc: "Auto-discovers every hal0 slot as a model provider — no config, no restart.", pow: "70" },
      { name: "Dual Memory", cost: 2, desc: "Reads and writes hal0's shared memory bank alongside every other agent.", pow: "65" },
      { name: "Delegate", cost: 2, desc: "Spins up scout, planner, worker, and reviewer subagents for parallel work.", pow: "75" },
    ],
  };
}

// ── roadmap (coming-soon) cards — curated dummy content ─────────────
function _lockedRoster() {
  const art = window.__hal0AgentArt || {};
  return [
    {
      id: "qwen", name: "Qwen", caps: true, el: "#8b86f9", elGlow: "rgba(123,116,247,0.22)",
      logo: art.qwen, logoScale: 0.9, model: "qwen-agent runtime",
      role: "multimodal · tool-calling agent", eta: "Q3 2026",
    },
    {
      id: "opencode", name: "opencode", caps: false, el: "#cdc7c0", elGlow: "rgba(214,211,206,0.16)",
      logo: art.opencode, logoScale: 0.82, model: "open-source TUI agent",
      role: "terminal-native · open coding agent", eta: "soon",
    },
  ];
}

// ── helpers ─────────────────────────────────────────────────────────
function _fmtK(n) {
  if (n == null || Number.isNaN(n)) return null;
  if (n >= 1000) return Math.round(n / 1000) + "K";
  return String(Math.round(n));
}

// Resolve the LLM slot Hermes orchestrates (its throughput/ctx source).
// Prefer the real role discriminators — `group`/`type` (backend-classified,
// see useSlots.ts inferSlotShape) plus `isDefault` — over the slot's
// display NAME, which is operator-renamable (useSlotRename) and therefore
// not a stable marker of "this is the chat slot". Name literals are kept
// only as a last-resort fallback for responses so sparse even group/type
// inference couldn't run.
function _primarySlot(slots) {
  if (!Array.isArray(slots)) return null;
  return (
    slots.find((s) => s.isDefault && (s.group === "chat" || s.type === "llm")) ||
    slots.find((s) => s.group === "chat" && s.type === "llm") ||
    slots.find((s) => s.type === "llm") ||
    slots.find((s) => s.name === "primary") ||
    slots.find((s) => s.name === "agent") ||
    slots.find((s) => s.name === "chat") ||
    null
  );
}

function _health(slot) {
  const m = (slot && slot.metrics) || {};
  const toks = m.toks;
  const ctxUsed = m.ctx;
  const ctxMax = slot && slot.ctx_effective != null ? slot.ctx_effective : null;
  const ctxPct = ctxUsed != null && ctxMax ? Math.min(100, (ctxUsed / ctxMax) * 100) : 0;
  return {
    tput: toks != null && toks > 0 ? Math.round(toks) + " tok/s" : null,
    ctxUsed: _fmtK(ctxUsed),
    ctxMax: _fmtK(ctxMax),
    ctxPct,
  };
}

// Toast kinds are the ones dashboard.css styles: info, ok, warn, err.
function _toast(msg, kind) {
  if (window.__hal0Toast) window.__hal0Toast(msg, kind || "info");
}

function _errText(err) {
  return (err && err.message) || "request failed";
}

// A 409 from the converge or repair route: another job (or the builder lock)
// is already in flight.
function _isBusy(err) {
  return !!err && err.status === 409;
}

// What a finished converge job did, from the Hermes arm's result
// (src/hal0/components/hermes_arm.py: upgraded, converged, skipped, busy; the
// failed kinds end the job as `failed`). Returns [message, toast kind].
function _upgradeOutcome(result, pinLabel) {
  const status = result && result.status;
  if (status === "upgraded") return ["Hermes upgraded to " + pinLabel, "ok"];
  if (status === "converged") return ["Hermes already matches the pin", "ok"];
  if (status === "skipped") return ["Hermes upgrade skipped: " + (result.reason || "no reason given"), "warn"];
  if (status === "busy") return ["Hermes builder is busy", "warn"];
  return ["Hermes upgrade finished", "ok"];
}

// "converge:hermes" → upgrade, "repair:hermes" → repair.
function _jobKind(job, fallback) {
  const phase = String((job && (job.phase || job.kind)) || "");
  if (phase.startsWith("repair")) return "repair";
  if (phase.startsWith("converge")) return "upgrade";
  return fallback || "upgrade";
}

// The overview route keys its cache by job and recomputes `job` and `phase` on
// every request, so a live overview stops reporting a job once it ends. This
// only guards a read that still names a job our poller has already seen
// finish (a static mock, or a response that was in flight as the job ended):
// that job and its `working` phase are dropped, and the phase falls back to
// the record rule until the next read.
function _withoutFinishedJob(overview, doneIds) {
  if (!overview || !overview.job || !doneIds.includes(overview.job.id)) return overview;
  return { ...overview, job: null, phase: overview.phase === "working" ? undefined : overview.phase };
}

// How long a queued or running build may go without an update before the card
// stops waiting for it. The server bounds a repair child at 45 minutes and its
// `install` step legitimately sits 15 to 20 without writing one, so this sits
// above that bound.
const JOB_STALE_MS = 50 * 60 * 1000;

function AgentsOverview() {
  const LiveAgentCard = window.LiveAgentCard;
  const LockedAgentCard = window.LockedAgentCard;
  const derive = window.__hal0DeriveCardModel;

  const qc = useQueryClient();

  const useAgents = window.__hal0UseAgents;
  const useSlots = window.__hal0UseSlots;
  const useAgentOverview = window.__hal0UseAgentOverview;
  const useUpdateJob = window.__hal0UseUpdateJob;
  const useComponentConverge = window.__hal0UseComponentConverge;
  const useAgentRepair = window.__hal0UseAgentRepair;
  const useAgentRestart = window.__hal0UseAgentRestart;
  const useAgentGatewayRestart = window.__hal0UseAgentGatewayRestart;
  const useAgentUninstall = window.__hal0UseAgentUninstall;
  const useCommandApprovalsMode = window.__hal0UseCommandApprovalsMode;
  const usePersonaActivate = window.__hal0UsePersonaActivate;
  const useHermesReconcileUpstreams = window.__hal0UseHermesReconcileUpstreams;
  const useAgentPersonas = window.__hal0UseAgentPersonas;
  const useConfigUrls = window.__hal0UseConfigUrls;
  const useCapabilities = window.__hal0UseCapabilities;

  const agentsQ = useAgents ? useAgents() : { data: null };
  const slotsQ = useSlots ? useSlots() : { data: null };
  const hermesOvQ = useAgentOverview ? useAgentOverview("hermes") : { data: null };
  const piOvQ = useAgentOverview ? useAgentOverview("pi") : { data: null };
  const urlsQ = useConfigUrls ? useConfigUrls() : { data: null };
  const capsQ = useCapabilities ? useCapabilities() : { data: null };
  // The Controls tab and the sidebar poll the same key, so this adds no request.
  const personasQ = useAgentPersonas ? useAgentPersonas("hermes") : { data: null };

  const converge = useComponentConverge ? useComponentConverge() : null;
  const repair = useAgentRepair ? useAgentRepair("hermes") : null;
  const restart = useAgentRestart ? useAgentRestart("hermes") : null;
  const gatewayRestart = useAgentGatewayRestart ? useAgentGatewayRestart("hermes") : null;
  const uninstall = useAgentUninstall ? useAgentUninstall("hermes") : null;
  const approvalsMode = useCommandApprovalsMode ? useCommandApprovalsMode("hermes") : null;
  const personaActivate = usePersonaActivate ? usePersonaActivate("hermes") : null;
  const reconcile = useHermesReconcileUpstreams ? useHermesReconcileUpstreams() : null;

  // Read `name`'s overview past the route's 30 s cache and cache it. Never
  // rejects: on an error the card keeps what it has and the next poll retries.
  const refreshOverview = (name) => {
    const refresh = window.__hal0RefreshAgentOverview;
    return refresh ? refresh(qc, name).catch(() => {}) : Promise.resolve();
  };
  const refreshHermes = () => refreshOverview("hermes");

  // ── the Hermes build job (upgrade = converge, repair) ──────────────
  // Seeded from the overview's in-flight job (a build started from the CLI or
  // the Services page) and from our own mutation results.
  const [jobId, setJobId] = useState(null);
  const [doneIds, setDoneIds] = useState([]);
  const startedKind = useRef(null);
  // The pin as the card shows it, for the upgrade toast (set each render).
  const pinLabel = useRef("the pin");
  const jobPoll = useUpdateJob
    ? useUpdateJob(jobId, { staleAfterMs: JOB_STALE_MS })
    : { job: null, terminal: false, lost: false };

  const hermesOv = hermesOvQ.data || null;
  const ovJobId = hermesOv && hermesOv.job ? hermesOv.job.id : null;
  useEffect(() => {
    if (ovJobId && ovJobId !== jobId && !doneIds.includes(ovJobId)) setJobId(ovJobId);
  }, [ovJobId, jobId, doneIds]);

  // Read terminal off the snapshot itself: useUpdateJob's `terminal` flag is
  // only reset when the id goes to null, so it can still be true from the
  // previous job while a new one is being polled.
  const polled = jobPoll.job && jobPoll.job.id === jobId ? jobPoll.job : null;
  const finished = !!polled && (polled.state === "applied" || polled.state === "failed");
  useEffect(() => {
    if (!finished || doneIds.includes(polled.id)) return;
    setDoneIds((ids) => [...ids, polled.id]);
    qc.invalidateQueries({ queryKey: ["agents"] });
    const kind = _jobKind(polled, startedKind.current);
    const result = polled.component_result || {};
    refreshOverview("hermes").then(() => {
      if (polled.state === "failed") {
        const why = polled.error || polled.error_code || "see the unit logs";
        _toast(`Hermes ${kind} failed: ${why}`, "err");
      } else if (kind === "repair") {
        _toast("Hermes repaired", "ok");
      } else {
        const [msg, tone] = _upgradeOutcome(result, pinLabel.current);
        _toast(msg, tone);
      }
    });
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [finished, polled && polled.id]);

  // The server lost the job (its record is gone) or it stopped updating, so no
  // finish is coming. Treat it as over: the card leaves `working`, the overview
  // is read fresh, and the operator is told to look at the unit logs. Marking
  // it done also keeps a stale overview from adopting it again. The card's
  // busy lock is not held for a build (an action's busy state ends when the
  // request returns), so there is nothing else to release.
  const lost = !!jobPoll.lost && !!jobId;
  useEffect(() => {
    if (!lost || doneIds.includes(jobId)) return;
    const kind = _jobKind(polled || (hermesOv && hermesOv.job), startedKind.current);
    setDoneIds((ids) => [...ids, jobId]);
    refreshOverview("hermes").then(() => {
      _toast(`Lost track of the Hermes ${kind} job; check the unit logs`, "warn");
    });
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [lost, jobId]);

  // Until the first status read lands, show the job as the overview reported
  // it, or as queued when we just started it, so the card turns to `working`
  // the moment the mutation returns.
  const seeded = hermesOv && hermesOv.job && hermesOv.job.id === jobId ? hermesOv.job : null;
  // A lost job's last snapshot still reads queued or running, so it is left out
  // here and in the overview filter below, in the same render that learns of
  // the loss rather than the one after the effect marks it done.
  const jobForModel = lost
    ? null
    : polled ||
      (jobId && !doneIds.includes(jobId)
        ? seeded || { id: jobId, state: "queued", phase: (startedKind.current === "repair" ? "repair" : "converge") + ":hermes" }
        : null);
  const settledIds = lost ? [...doneIds, jobId] : doneIds;

  // ── models ─────────────────────────────────────────────────────────
  const agents = (agentsQ.data && agentsQ.data.agents) || [];
  const hermesRec = agents.find((a) => a.name === "hermes" || a.id === "hermes") || null;
  const piRec = agents.find((a) => a.name === "pi" || a.id === "pi") || null;
  const primary = _primarySlot(slotsQ.data);
  const caps = capsQ.data || null;
  const urls = urlsQ.data || null;
  const piOv = piOvQ.data || null;
  const personas = personasQ.data || null;
  const activeId = (personas && personas.active) || null;
  const activeRow =
    activeId && personas && Array.isArray(personas.personas)
      ? personas.personas.find((p) => p.id === activeId) || null
      : null;
  const livePersona = activeRow ? activeRow.display_name || activeRow.id : activeId;

  // `lastTool` stays null this release: the only source, GET
  // /api/agents/{name}/activity, shells out to journalctl on every call. The
  // model keeps the input so a later change can wire a cheap source.
  const hermesModel = derive
    ? {
        ...derive({
          agent: "hermes",
          record: hermesRec,
          overview: _withoutFinishedJob(hermesOv, settledIds),
          slot: primary,
          job: jobForModel,
          caps,
          urls,
          lastTool: null,
          persona: livePersona,
        }),
        overview: hermesOv,
        // A 404 (an older daemon) is `null` data; anything else is an error.
        overviewError: hermesOvQ.isError ? _errText(hermesOvQ.error) : null,
        health: _health(primary),
      }
    : null;
  if (hermesModel) pinLabel.current = hermesModel.version.pinLabel;
  const piModel = derive
    ? {
        ...derive({ agent: "pi", record: piRec, overview: piOv, slot: null, job: null, caps, urls, lastTool: null }),
        overview: piOv,
        overviewError: piOvQ.isError ? _errText(piOvQ.error) : null,
        health: _health(null),
      }
    : null;

  // ── actions ────────────────────────────────────────────────────────
  // Each resolves (never rejects) once the card shows the box after it and
  // its toast is out, so a card button holds its busy state until then.
  const startJob = (kind, run) => {
    startedKind.current = kind;
    return run()
      .then((res) => {
        if (res && res.id) setJobId(res.id);
        // The job overlay is current on every read; no refresh needed to
        // pick up the new job.
        if (hermesOvQ.refetch) hermesOvQ.refetch();
      })
      .catch((err) => {
        if (_isBusy(err)) _toast("Hermes builder is busy", "warn");
        else _toast(`Hermes ${kind} failed to start: ${_errText(err)}`, "err");
      });
  };

  const copy = (text) => {
    const fail = () => _toast(text, "info");
    try {
      if (!navigator.clipboard || !navigator.clipboard.writeText) {
        fail();
        return Promise.resolve();
      }
      return navigator.clipboard.writeText(text).then(() => _toast("Copied", "ok"), fail);
    } catch (e) {
      fail();
      return Promise.resolve();
    }
  };

  const hermesActions = {
    restart: restart
      ? () =>
          restart
            .mutateAsync()
            .then((res) =>
              refreshHermes().then(() => {
                if (res && res.status === "error") _toast(`Hermes restart failed: ${res.detail || "see the unit logs"}`, "err");
                else _toast(res && res.status === "restarting" ? "Hermes is restarting" : "Hermes restarted", "ok");
              }),
            )
            .catch((err) => _toast(`Hermes restart failed: ${_errText(err)}`, "err"))
      : null,
    restartGateway: gatewayRestart
      ? () =>
          gatewayRestart
            .mutateAsync()
            .then((res) =>
              refreshHermes().then(() => {
                if (res && res.status === "error") _toast(`Gateway restart failed: ${res.detail || "see the unit logs"}`, "err");
                else _toast("Hermes gateway restarted", "ok");
              }),
            )
            .catch((err) => _toast(`Gateway restart failed: ${_errText(err)}`, "err"))
      : null,
    repair: repair ? () => startJob("repair", () => repair.mutateAsync()) : null,
    upgrade: converge ? () => startJob("upgrade", () => converge.mutateAsync("hermes")) : null,
    uninstall: uninstall
      ? () =>
          uninstall
            .mutateAsync()
            .then((res) =>
              refreshHermes().then(() => {
                if (res && res.status === "partially_uninstalled") {
                  const n = (res.residual || []).length;
                  _toast(`Hermes partly uninstalled: ${n} path${n === 1 ? "" : "s"} left behind`, "warn");
                } else if (res && res.status === "not_installed") {
                  _toast("Hermes was not installed", "info");
                } else {
                  _toast("Hermes uninstalled", "ok");
                }
              }),
            )
            .catch((err) => _toast(`Uninstall failed: ${_errText(err)}`, "err"))
      : null,
    setApprovalsMode: approvalsMode
      ? (mode) =>
          approvalsMode
            .mutateAsync(mode)
            .then((res) =>
              refreshHermes().then(() => {
                const unsure = res && (res.restart === "unknown" || res.restart === "timeout");
                _toast((res && res.message) || `Approvals set to ${mode}`, unsure ? "warn" : "ok");
              }),
            )
            .catch((err) => _toast(`Approvals change failed: ${_errText(err)}`, "err"))
      : null,
    activatePersona: personaActivate
      ? (pid) =>
          personaActivate
            .mutateAsync(pid)
            // Waits for the refetch, so the chip and the select name the new
            // persona before the busy state clears.
            .then((res) => qc.invalidateQueries({ queryKey: ["agents"] }).then(() => res))
            .then((res) => {
              // The API answers `refresh` with whether the prompt reached
              // Hermes; a failed write must not read as a plain success.
              const refresh = res && res.refresh;
              const live = !refresh || refresh.status === "applied" || refresh.status === "unchanged";
              _toast(refresh ? `Persona activated — ${refresh.message}` : "Persona activated", live ? "ok" : "err");
            })
            .catch((err) => _toast(`Persona activation failed: ${_errText(err)}`, "err"))
      : null,
    reconcile: reconcile
      ? () =>
          reconcile
            .mutateAsync()
            .then((res) =>
              refreshHermes().then(() => {
                const n = res && Array.isArray(res.profiles_wired) ? res.profiles_wired.length : null;
                _toast(n ? `Re-mirrored ${n} profile${n === 1 ? "" : "s"} to Open WebUI` : "Profiles re-mirrored to Open WebUI", "ok");
              }),
            )
            .catch((err) => _toast(`Re-mirror failed: ${_errText(err)}`, "err"))
      : null,
    // LogsView (extras.jsx) reads no hash parameters, so there is no unit
    // filter to pass; the journal pane there tails hal0's own stream.
    openLogs: () => { window.location.hash = "#logs"; },
    // The Activity surface is the ActivityLog pane on the Slots page.
    openActivity: () => { window.location.hash = "#slots"; },
    // The approval queue modal is owned by App (main.jsx) and opened by event.
    openApprovals: () => { window.dispatchEvent(new CustomEvent("hal0:open-approvals")); },
    copy,
    refresh: () => Promise.all([refreshHermes(), agentsQ.refetch ? agentsQ.refetch() : null]).then(() => {}),
  };

  // Pi is a CLI tool: no unit, no gateway, no build job. Its lifecycle verbs
  // run as the user who installed it, so the card only hands out commands.
  const piActions = {
    restart: null,
    restartGateway: null,
    repair: null,
    upgrade: null,
    uninstall: null,
    setApprovalsMode: null,
    activatePersona: null,
    reconcile: null,
    openLogs: null,
    openActivity: null,
    openApprovals: null,
    copy,
    // Re-check: the card shows "Checking…" until the fresh read lands.
    refresh: () =>
      Promise.all([refreshOverview("pi"), agentsQ.refetch ? agentsQ.refetch() : null]).then(() => {}),
  };

  return (
    <div className="agents-overview" data-testid="agents-overview">
      <div className="ao-head">
        <div className="ao-eye">hal0 · agent library</div>
        <p className="ao-sub">
          Every agent in the runtime as a card. Hermes and Pi stream real install, version and
          health state; flip a card for its console.
        </p>
        <div className="ao-legend">
          <span className="ao-lz"><span className="d serving" />Serving <span className="k">· live, wired</span></span>
          <span className="ao-lz"><span className="d ready" />Ready</span>
          <span className="ao-lz"><span className="d down" />Down</span>
          <span className="ao-lz"><span className="d soon" />Coming soon <span className="k">· on the roadmap</span></span>
        </div>
      </div>

      <div className="ao-grid">
        {LiveAgentCard && hermesModel && (
          <LiveAgentCard
            identity={_hermesIdentity(primary && primary.model)}
            model={hermesModel}
            actions={hermesActions}
          />
        )}
        {LiveAgentCard && piModel && (
          <LiveAgentCard identity={_piIdentity()} model={piModel} actions={piActions} />
        )}
        {LockedAgentCard && _lockedRoster().map((a) => <LockedAgentCard key={a.id} agent={a} />)}
      </div>
    </div>
  );
}

Object.assign(window, { AgentsOverview });
