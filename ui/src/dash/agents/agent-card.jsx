// hal0 dashboard — collectible agent cards (Agents Overview).
//
// Two cards, productionised from Design/agent-card-kit:
//   LiveAgentCard   — the bundled agents (Hermes · Pi). The front keeps the
//                     collectible foil (full-bleed portrait for Hermes, the
//                     grid+mark treatment for Pi, tilt and holo) and paints
//                     the card model's phase: version pill and update badge
//                     on top, the live health block or the phase's own block
//                     on the right, and a bottom band per phase (chips and
//                     reach when healthy; journal tail, reasons and fixes,
//                     the install command or the running job otherwise).
//                     A click flips it to a tabbed console: Status,
//                     Controls, Lore, Lifecycle.
//   LockedAgentCard — a roadmap entry behind a grey "coming soon" mask. The
//                     dummy module tile + identity show through; the card is
//                     non-interactive (no flip) until the integration ships.
//
// agents-overview.jsx owns the data and the actions; this file paints
// `{identity, model, actions}` (model = window.__hal0DeriveCardModel's output
// plus `overview` and `health`). The reads made here belong to one face: the
// journal tail on the `down` face, and the persona list and approvals count
// on the Controls tab.
//
// Both faces stay mounted (the flip is an opacity cross-fade). The hidden one
// is `inert`, so it takes no focus or clicks, and action test ids are only set
// on the face that is showing (`live`): one id, one element, whichever side is
// up. While one action runs, every other action control is disabled.
//
// Window-globals shim: register on `window`, read React / Icon / StatusDot /
// ConfirmDialog / PersonaEditModal from the same. No ES imports across
// dash/* (main.tsx load order is the contract).

const { useState, useRef, useLayoutEffect, useEffect, useContext, createContext } = React;

// The key of the action this card is running, or null. Action controls read
// it to disable themselves, so a click can never be swallowed by `run`.
const CardBusy = createContext(null);

// Drive the 3D flip with the Web Animations API. A CSS transition on a
// rotateY 0→180 hits a matrix-interpolation singularity in some engines and
// freezes at t=0; a 3-keyframe WAAPI tween (through 90°) interpolates cleanly
// and holds the end state. Shared by any card that flips.
function useFlip() {
  const innerRef = useRef(null);
  const firstRef = useRef(true);
  const [flipped, setFlipped] = useState(false);
  const reduce =
    typeof window !== "undefined" &&
    window.matchMedia &&
    window.matchMedia("(prefers-reduced-motion: reduce)").matches;

  useLayoutEffect(() => {
    const el = innerRef.current;
    if (!el) return;
    if (firstRef.current) { firstRef.current = false; return; }
    const to = flipped ? 180 : 0;
    const from = flipped ? 0 : 180;
    if (reduce) { el.style.transform = "rotateY(" + to + "deg)"; return; }
    const anim = el.animate(
      [
        { transform: "rotateY(" + from + "deg)" },
        { transform: "rotateY(" + (from + to) / 2 + "deg)" },
        { transform: "rotateY(" + to + "deg)" },
      ],
      { duration: 600, easing: "cubic-bezier(.2,.74,.2,1)", fill: "forwards" },
    );
    anim.onfinish = () => {
      el.style.transform = "rotateY(" + to + "deg)";
      try { anim.cancel(); } catch (e) { /* no-op */ }
    };
  }, [flipped]);

  return { innerRef, flipped, setFlipped, reduce };
}

// Cursor-tracking tilt + holo position. Returns handlers + the tilt ref.
function useTilt(reduce, depth) {
  const tiltRef = useRef(null);
  const d = depth || { x: -10, y: 12 };
  const onMove = (e) => {
    if (reduce || !tiltRef.current) return;
    const r = tiltRef.current.getBoundingClientRect();
    const px = (e.clientX - r.left) / r.width;
    const py = (e.clientY - r.top) / r.height;
    const s = tiltRef.current.style;
    s.setProperty("--rx", ((py - 0.5) * d.x).toFixed(2) + "deg");
    s.setProperty("--ry", ((px - 0.5) * d.y).toFixed(2) + "deg");
    s.setProperty("--hx", (px * 100).toFixed(1) + "%");
    s.setProperty("--hy", (py * 100).toFixed(1) + "%");
  };
  const onLeave = () => {
    if (!tiltRef.current) return;
    const s = tiltRef.current.style;
    s.setProperty("--rx", "0deg");
    s.setProperty("--ry", "0deg");
  };
  return { tiltRef, onMove, onLeave };
}

// ── small helpers ───────────────────────────────────────────────────
const HERMES_UNIT = "hal0-agent@hermes.service";

const TABS = [
  ["status", "Status"],
  ["controls", "Controls"],
  ["lore", "Lore"],
  ["lifecycle", "Lifecycle"],
];

const PHASE_MOD = {
  down: " is-down",
  broken: " is-down",
  degraded: " is-degraded",
  working: " is-working",
  not_installed: " is-not-installed",
};

const REASON_LABELS = {
  gateway_down: "gateway down",
  api_unhealthy: "API server unhealthy",
  provision_failed: "provision failed",
  provision_warn: "provision warnings",
  doctor_drift: "doctor drift",
  approvals_drift: "approvals out of sync",
};

const PI_CHECK_LABELS = {
  binary: "binary on PATH",
  provider_ext: "hal0 provider ext",
  hindsight_ext: "hindsight ext",
  theme: "hal0 theme",
  mcp_entry: "memory MCP wired",
  adapter_gate: "adapter version",
  hindsight_config: "hindsight config",
};

const UPGRADE_COPY = [
  "Builds the pinned release beside the live one. Hermes keeps answering meanwhile.",
  "Migrates config, re-wires MCP and memory, verifies.",
  "Flips current and restarts the agent and the gateway. Telegram and the desktop drop for a few seconds.",
];

function stop(e) { e.stopPropagation(); }

function _dash(v) {
  if (v == null || v === "") return "—";
  return String(v);
}

function _yes(v) {
  return v === true ? "yes" : v === false ? "no" : "—";
}

function _list(v) {
  return Array.isArray(v) && v.length ? v.join(", ") : "—";
}

// Epoch seconds (the job registry) or an ISO string (the overview) → ms.
function _toMs(ts) {
  if (ts == null || ts === "") return null;
  if (typeof ts === "number" && Number.isFinite(ts)) return ts < 1e12 ? ts * 1000 : ts;
  const ms = Date.parse(String(ts));
  return Number.isNaN(ms) ? null : ms;
}

function _fmtTime(ts) {
  const ms = _toMs(ts);
  if (ms == null) return _dash(ts);
  return new Date(ms).toLocaleString([], { month: "short", day: "numeric", hour: "2-digit", minute: "2-digit" });
}

function _fmtSpan(sec) {
  const s = Math.max(0, Math.round(sec));
  if (s < 60) return s + "s";
  if (s < 3600) return Math.floor(s / 60) + "m " + (s % 60) + "s";
  return Math.floor(s / 3600) + "h " + Math.floor((s % 3600) / 60) + "m";
}

function _fmtAgo(sec) {
  const s = Math.max(0, Math.round(sec));
  if (s < 60) return s + "s ago";
  if (s < 3600) return Math.floor(s / 60) + "m ago";
  return Math.floor(s / 3600) + "h ago";
}

function _reasonLabel(reason) {
  return REASON_LABELS[reason] || String(reason || "").replace(/_/g, " ");
}

// "repair:hermes" / kind "repair" → repairing; converge → upgrading.
function _jobWord(job) {
  const p = String((job && (job.phase || job.kind)) || "");
  return p.indexOf("repair") === 0 ? "repairing" : "upgrading";
}

// Re-render every `ms` while mounted (elapsed timers, "refreshed Ns ago").
function useNow(ms) {
  const [now, setNow] = useState(() => Date.now());
  useEffect(() => {
    const t = setInterval(() => setNow(Date.now()), ms);
    return () => clearInterval(t);
  }, [ms]);
  return now;
}

// One action at a time per card; `busy` names it until its promise settles.
// The overview's actions never reject (they toast their own errors). Action
// controls are disabled while one runs; should a call still arrive, it is
// refused out loud, never dropped silently.
function useRunner() {
  const [busy, setBusy] = useState(null);
  const run = (key, fn) => {
    if (typeof fn !== "function") return;
    if (busy) {
      if (window.__hal0Toast) window.__hal0Toast("Another action is still running", "warning");
      return;
    }
    setBusy(key);
    let p;
    try { p = fn(); } catch (e) { p = null; }
    Promise.resolve(p)
      .catch(() => {})
      .then(() => setBusy(null));
  };
  return [busy, run];
}

function Ago({ ts }) {
  const now = useNow(1000);
  const ms = _toMs(ts);
  if (ms == null) return "—";
  return _fmtAgo((now - ms) / 1000);
}

// ── primitives local to the card ────────────────────────────────────
// `busy`: this button's own action is the one running (it shows busyLabel).
// `free`: the button only navigates or opens a dialog that does not act, so
// it stays usable while another action runs.
function ActBtn({ live, testid, icon, label, busyLabel, busy, onClick, primary, danger, disabled, title, wide, free }) {
  const Icon = window.Icon;
  const running = useContext(CardBusy);
  const cls =
    "fc-act" + (primary ? " primary" : "") + (danger ? " danger" : "") + (wide ? " wide" : "") + (busy ? " busy" : "");
  return (
    <button
      type="button"
      className={cls}
      data-testid={live ? testid : undefined}
      disabled={!!disabled || !!busy || !onClick || (!free && !!running)}
      title={title}
      onClick={(e) => { stop(e); if (onClick) onClick(); }}
    >
      {icon && <Icon name={icon} size={12} sw={1.5} />}
      {busy ? busyLabel || label + "…" : label}
    </button>
  );
}

// A command the operator runs on the box. The text stays selectable; the
// button copies it (the overview's `copy` toasts the command when the
// clipboard is unavailable).
function CopyCommand({ live, command, testid, actions, label }) {
  const Icon = window.Icon;
  if (!command) return null;
  const canCopy = !!(actions && actions.copy);
  return (
    <div className="fc-cmd">
      {label && <span className="fc-cmd-l">{label}</span>}
      <div className="fc-cmd-row">
        {/* selecting the command is an interaction: it must not flip the card */}
        <code className="fc-cmd-t" title={command} onClick={stop}>{command}</code>
        <button
          type="button"
          className="fc-cmd-b"
          data-testid={live ? testid : undefined}
          data-command={command}
          aria-label={"Copy " + command}
          title={"Copy " + command}
          disabled={!canCopy}
          onClick={(e) => { stop(e); if (canCopy) actions.copy(command); }}
        >
          <Icon name="doc" size={11} sw={1.5} />copy
        </button>
      </div>
    </div>
  );
}

function Rows({ rows }) {
  return (
    <div className="fcb-rows">
      {rows.map(([k, v], i) => (
        <div className="fcb-r" key={i}>
          <span className="k">{k}</span>
          <span className="v">{v}</span>
        </div>
      ))}
    </div>
  );
}

// ── live health block (front, top-right) ────────────────────────────
function HealthBlock({ health, statusCls }) {
  const StatusDot = window.StatusDot;
  const hasTput = health.tput != null && health.tput !== "";
  return (
    <div className="hv" title="endpoint health · context">
      <div className="hv-health">
        <StatusDot cls={statusCls} />
        <span className={"hv-tput" + (statusCls === "serving" ? "" : " idle")}>
          {hasTput ? health.tput : "—"}
        </span>
      </div>
      <div className="hv-ctx">
        <div className="hv-ctxrow">
          <span className="hv-l">ctx</span>
          <span className="hv-v">
            {health.ctxUsed != null ? health.ctxUsed : "—"}
            <i>/{health.ctxMax != null ? health.ctxMax : "—"}</i>
          </span>
        </div>
        <div className="hv-bar"><i style={{ width: Math.max(health.ctxPct || 0, 2) + "%" }} /></div>
      </div>
    </div>
  );
}

// The right-hand block when the health block does not apply.
function PhaseBlock({ model, isPi }) {
  const ov = model.overview || {};
  if (isPi && model.phase !== "not_installed") {
    return <div className="fc-pblock"><span className="fc-pb-l">no unit · no slot</span></div>;
  }
  switch (model.phase) {
    case "degraded":
      return (
        <div className="fc-pblock warn" title="degraded reasons">
          <span className="fc-pb-n">{model.degradedCount}</span>
          <span className="fc-pb-l">{model.degradedCount === 1 ? "issue" : "issues"}</span>
        </div>
      );
    case "down": {
      // The unit's state word as reported; "unknown" (or no report) is a dash.
      const unit = ov.units && ov.units.agent;
      return (
        <div className="fc-pblock err">
          <span className="fc-pb-l">agent unit</span>
          <span className="fc-pb-v">{_dash(unit && unit !== "unknown" ? unit : null)}</span>
        </div>
      );
    }
    case "broken":
      return <div className="fc-pblock err"><span className="fc-pb-l">install broken</span></div>;
    case "working":
      return (
        <div className="fc-pblock work">
          <span className="fc-spin" aria-hidden="true" />
          <span className="fc-pb-l">{_jobWord(model.job)}</span>
        </div>
      );
    case "not_installed":
      return <div className="fc-pblock"><span className="fc-pb-l">not installed</span></div>;
    default:
      return <div className="fc-pblock"><span className="fc-pb-l">liveness unknown</span></div>;
  }
}

// ── front: bottom band pieces ───────────────────────────────────────
function StatusRow({ identity, model }) {
  const Icon = window.Icon;
  const StatusDot = window.StatusDot;
  const cls = model.statusCls;
  const tone =
    cls === "serving" ? "" :
    cls === "error" ? " error" :
    cls === "working" ? " working" :
    model.phase === "degraded" ? " warn" : " idle";
  return (
    <div className="fc-meta">
      <span className={"fc-st" + tone}>
        <StatusDot cls={cls} />{model.statusLabel}
      </span>
      <span className="flip-hint"><Icon name="refresh" size={11} sw={1.5} />tap · console</span>
      <span className="fc-rarity" title="holo rare">
        {[0, 1, 2, 3, 4].map((i) => <span key={i} className={"fc-star" + (i < identity.rarity ? "" : " off")} />)}
      </span>
    </div>
  );
}

function Chips({ model }) {
  const c = model.chips;
  return (
    <div className="fc-chips">
      <span className="fc-chip">persona · <b>{_dash(c.persona)}</b></span>
      <span className="fc-chip">approvals · <b>{_dash(c.approvals)}</b></span>
      <span className={"fc-chip" + (c.terminal === "on" ? " on" : "")}>terminal · <b>{_dash(c.terminal)}</b></span>
    </div>
  );
}

function Reach({ model }) {
  if (!model.overview) {
    return <div className="fc-reach"><span className="fc-rchip">reach · —</span></div>;
  }
  const r = model.reach;
  const n = r.api.profiles.length;
  return (
    <div className="fc-reach">
      {model.gatewayOff ? (
        <span className="fc-rchip off">gateway off</span>
      ) : (
        r.platforms.map((p) => (
          <span key={p.id} className={"fc-rchip" + (p.lit ? " lit" : "")}>{p.id}</span>
        ))
      )}
      <span className={"fc-rchip" + (r.api.lit ? " lit" : "")}>api · {n} {n === 1 ? "profile" : "profiles"}</span>
      {r.desktop && (
        <a className="fc-rchip link" href={r.desktop} target="_blank" rel="noopener noreferrer" onClick={stop}>
          desktop ↗
        </a>
      )}
    </div>
  );
}

// The last journal lines of the agent unit; mounted only on the down face.
function JournalTail() {
  const useUnitLogTail = window.__hal0UseUnitLogTail;
  const q = useUnitLogTail ? useUnitLogTail(HERMES_UNIT, 5) : { data: null };
  const d = q.data;
  const lines = d && Array.isArray(d.lines) ? d.lines.slice(-5) : [];
  return (
    <div className="fc-journal" title={HERMES_UNIT}>
      <div className="fc-journal-l">journal · {HERMES_UNIT}</div>
      {lines.length ? (
        lines.map((l, i) => <div className="fc-journal-ln" key={i} title={l}>{l}</div>)
      ) : (
        <div className="fc-journal-ln dim">{d && d.hint ? d.hint : q.isLoading ? "reading the journal…" : "no journal lines"}</div>
      )}
    </div>
  );
}

// One fix control per degraded reason; a fix already offered by an earlier
// row is not offered twice (one Restart gateway covers both gateway rows).
function FixControl({ live, reason, model, actions, busy, run, ask }) {
  switch (reason.fix) {
    case "restart_gateway":
      return (
        <ActBtn live={live} testid="agent-action-restart-gateway" icon="refresh" label="Restart gateway"
          busy={busy === "restartGateway"} onClick={actions.restartGateway && (() => run("restartGateway", actions.restartGateway))} />
      );
    case "restart_agent":
      return (
        <ActBtn live={live} testid="agent-action-restart" icon="refresh" label="Restart agent"
          busy={busy === "restart"} onClick={actions.restart && (() => run("restart", actions.restart))} />
      );
    case "repair":
      return (
        <ActBtn live={live} testid="agent-action-repair" label="Repair"
          busy={busy === "repair"} onClick={actions.repair && (() => ask("repair"))} />
      );
    case "resync_approvals": {
      const mode = model.chips.approvals;
      return (
        <ActBtn live={live} testid="agent-action-resync" label="Re-sync" title={mode ? "Re-apply approvals mode " + mode : undefined}
          busy={busy === "resync"}
          onClick={actions.setApprovalsMode && mode ? () => run("resync", () => actions.setApprovalsMode(mode)) : null} />
      );
    }
    case "cli":
      return reason.command ? <CopyCommand live={live} command={reason.command} testid="agent-action-copy-fix" actions={actions} /> : null;
    default:
      return null;
  }
}

function Reasons({ live, identity, model, actions, busy, run, ask }) {
  const seen = new Set();
  const more = model.reasons.length - model.reasonsTop.length;
  return (
    <div className="fc-reasons" data-testid={"agent-reasons-" + identity.id}>
      {model.reasonsTop.map((r, i) => {
        const key = r.fix === "cli" ? "cli:" + (r.command || "") : r.fix;
        const repeat = seen.has(key);
        seen.add(key);
        return (
          <div className="fc-reason" key={i} data-reason={r.reason}>
            <div className="fc-reason-t">
              <span className="fc-reason-n">{_reasonLabel(r.reason)}</span>
              {r.detail && <span className="fc-reason-d" title={r.detail}>{r.detail}</span>}
            </div>
            {!repeat && (
              <FixControl live={live} reason={r} model={model} actions={actions} busy={busy} run={run} ask={ask} />
            )}
          </div>
        );
      })}
      {more > 0 && <div className="fc-more">+{more} more · flip for the console</div>}
    </div>
  );
}

// The running upgrade or repair. Repair jobs carry the provisioner's `[run ]`
// steps; a converge job has none, so it shows its state and elapsed time.
function JobFace({ id, job }) {
  const now = useNow(1000);
  if (!job) return null;
  const steps = Array.isArray(job.steps) ? job.steps.filter((s) => typeof s === "string") : [];
  const started = _toMs(job.created_at);
  const shown = steps.slice(-4);
  const earlier = steps.length - shown.length;
  return (
    <div className="fc-job" data-testid={"agent-job-" + id} data-state={job.state}>
      <div className="fc-job-h">
        <span className="fc-spin" aria-hidden="true" />
        <b>{_jobWord(job)}</b>
        <span className="fc-job-s">· {_dash(job.state)}</span>
        <span className="fc-job-t">{started != null ? _fmtSpan((now - started) / 1000) : "—"}</span>
      </div>
      {shown.length ? (
        <div className="fc-steps">
          {earlier > 0 && <div className="fc-step done">+{earlier} earlier</div>}
          {shown.map((s, i) => (
            <div key={i} className={"fc-step" + (i === shown.length - 1 ? " cur" : " done")}>{s}</div>
          ))}
        </div>
      ) : (
        <div className="fc-steps">
          <div className="fc-step cur">{job.state === "queued" ? "queued · waiting for the builder" : "building at the pin"}</div>
        </div>
      )}
    </div>
  );
}

function PiChecks({ model }) {
  const profile = (model.overview && model.overview.profile) || null;
  const checks = profile && Array.isArray(profile.checks) ? profile.checks : [];
  if (!checks.length) return <div className="fc-note">profile checks · —</div>;
  return (
    <div className="fc-checks">
      {checks.map((c) => {
        const st = c.ok === true ? "ok" : c.ok === false ? "bad" : "na";
        const mark = c.ok === true ? "✓" : c.ok === false ? "✗" : "–";
        return (
          <span key={c.id} className={"fc-check " + st} title={c.detail || undefined}>
            <i>{mark}</i>{PI_CHECK_LABELS[c.id] || c.id}
          </span>
        );
      })}
    </div>
  );
}

// ── front: bottom band by phase ─────────────────────────────────────
function PhaseBand(props) {
  const { live, identity, model, actions, busy, run, ask } = props;
  const isPi = identity.id === "pi";
  const status = <StatusRow identity={identity} model={model} />;

  if (model.phase === "not_installed") {
    return (
      <>
        <div className="fc-role">{identity.role}</div>
        <div className="fc-note">
          not installed on this box{model.version.pinLabel !== "—" && <> · installs <b>{model.version.pinLabel}</b></>}
        </div>
        <CopyCommand live={live} command={model.commands.install} testid="agent-action-copy-install" actions={actions} />
        {status}
      </>
    );
  }

  if (isPi) {
    return (
      <>
        <PiChecks model={model} />
        {model.phase === "broken" ? (
          <CopyCommand live={live} label="reinstall" command={model.commands.install} testid="agent-action-copy-install" actions={actions} />
        ) : model.updateAvailable ? (
          <CopyCommand live={live} label={"upgrade to " + model.version.pinLabel} command={model.commands.upgrade} testid="agent-action-copy-upgrade" actions={actions} />
        ) : null}
        {status}
      </>
    );
  }

  switch (model.phase) {
    case "down":
    case "broken": {
      const broken = model.phase === "broken";
      return (
        <>
          <JournalTail />
          <div className="fc-acts">
            <ActBtn live={live} testid="agent-action-restart" icon="refresh" label="Start" busyLabel="Starting…"
              primary={!broken} busy={busy === "restart"} onClick={actions.restart && (() => run("restart", actions.restart))} />
            <ActBtn live={live} testid="agent-action-repair" label="Repair" primary={broken}
              busy={busy === "repair"} onClick={actions.repair && (() => ask("repair"))} />
            <ActBtn live={live} testid="agent-action-logs" icon="logs" label="Logs" free onClick={actions.openLogs} />
          </div>
          {status}
        </>
      );
    }
    case "degraded":
      return (
        <>
          <Reasons {...props} />
          {status}
        </>
      );
    case "working":
      return (
        <>
          <JobFace id={identity.id} job={model.job} />
          {status}
        </>
      );
    case "ready":
    case "serving":
      return (
        <>
          <Chips model={model} />
          <Reach model={model} />
          {status}
        </>
      );
    default:
      return (
        <>
          <div className="fc-note">hal0 cannot read the agent unit's state on this box.</div>
          <div className="fc-acts">
            <ActBtn live={live} testid="agent-action-restart" icon="refresh" label="Restart" busyLabel="Restarting…"
              busy={busy === "restart"} onClick={actions.restart && (() => run("restart", actions.restart))} />
            <ActBtn live={live} testid="agent-action-logs" icon="logs" label="Logs" free onClick={actions.openLogs} />
          </div>
          {status}
        </>
      );
  }
}

// ── back: tabs ──────────────────────────────────────────────────────
function NotInstalledPane({ live, model, actions }) {
  return (
    <div className="fcb-pane-in">
      <div className="fc-note">
        Not installed on this box{model.version.pinLabel !== "—" && <> · installs <b>{model.version.pinLabel}</b></>}.
      </div>
      <CopyCommand live={live} command={model.commands.install} testid="agent-action-copy-install" actions={actions} />
    </div>
  );
}

function StatusTab({ identity, model }) {
  const ov = model.overview;
  const isPi = identity.id === "pi";
  if (!ov) {
    return (
      <div className="fcb-pane-in">
        <div className="fc-note">
          No overview from this daemon (GET /api/agents/{identity.id}/overview); the card shows liveness from the
          agents list only.
        </div>
      </div>
    );
  }
  const foot = <div className="fcb-foot">refreshed <Ago ts={model.generatedAt} /></div>;

  if (isPi) {
    const v = ov.version || {};
    const inst = v.installed || {};
    const mem = ov.memory || {};
    const profile = ov.profile || {};
    const checks = Array.isArray(profile.checks) ? profile.checks : [];
    const ext = (id) => {
      const c = checks.find((x) => x.id === id);
      return c ? _yes(c.ok) : "—";
    };
    return (
      <div className="fcb-pane-in">
        <Rows rows={[
          ["pi", inst.pi ? "v" + String(inst.pi).replace(/^v/, "") : "—"],
          ["adapter", _dash(inst.adapter)],
          ["pinned", model.version.pinLabel],
          ["home", _dash(profile.home)],
          ["profiled", _fmtTime(profile.profiled_at)],
        ]} />
        <div className="fcb-sec-l">profile checks</div>
        <PiChecks model={model} />
        <div className="fcb-sec-l">extensions hal0 manages</div>
        <Rows rows={[["hal0-provider", ext("provider_ext")], ["hindsight", ext("hindsight_ext")]]} />
        <div className="fcb-sec-l">memory wiring</div>
        <Rows rows={[
          ["MCP entry", _yes(mem.mcp_entry)],
          ["Hindsight config", _yes(mem.hindsight_config)],
          ["Hindsight API", mem.hindsight_api_reachable === true ? "reachable" : mem.hindsight_api_reachable === false ? "unreachable" : "—"],
        ]} />
        {foot}
      </div>
    );
  }

  const units = ov.units || {};
  const api = ov.api_server || {};
  const prov = ov.provision || {};
  const appr = ov.approvals || {};
  const mem = ov.memory || {};
  const doctor = Array.isArray(ov.doctor) ? ov.doctor : [];
  const profiles = Array.isArray(api.profiles) ? api.profiles : [];
  const provisionLine = model.provisionStale
    ? `provision report from ${_fmtTime(prov.reported_at)} predates the live release (built ${_fmtTime(prov.live_built_at)})`
    : prov.present === false
      ? "no provision report"
      : `ok ${_dash(prov.ok)} · warn ${_dash(prov.warn)} · fail ${_dash(prov.fail)}`;
  return (
    <div className="fcb-pane-in">
      <Rows rows={[
        ["agent unit", _dash(units.agent)],
        ["gateway", _dash(units.gateway) + (model.gatewayOff ? " · disabled" : "")],
        ["platforms", _list(ov.platforms)],
        ["api server", _dash(api.endpoint) + " · " + (api.healthy === true ? "healthy" : api.healthy === false ? "unhealthy" : "—")],
      ]} />
      {profiles.length > 0 && (
        <div className="fc-chips">
          {profiles.map((p) => <span key={p} className="fc-chip">{p}</span>)}
        </div>
      )}
      <Rows rows={[
        ["primary model", _dash(prov.primary_model)],
        ["delegation", _dash(prov.delegation_model)],
        ["chat slots", _dash(prov.chat_slot_count)],
        ["mcp servers", _dash(prov.mcp_server_count)],
        ["terminal tool", _dash(prov.terminal_tool)],
        ["memory", _dash(prov.memory_provider) + " · " + (mem.available === true ? "available" : mem.available === false ? "unavailable" : "—") + (mem.writes != null ? " · " + mem.writes + " writes" : "")],
        ["approvals", _dash(appr.mode) + (appr.effective && appr.effective !== appr.mode ? " (runs " + appr.effective + ")" : "") + " · " + (appr.in_sync === true ? "in sync" : appr.in_sync === false ? "out of sync" : "—") + " · " + _dash(appr.pending) + " pending"],
        ["provision", provisionLine],
        ["hal0 at provision", _dash(prov.hal0_version)],
        ["provisioned", _fmtTime(prov.completed_at)],
      ]} />
      {doctor.length > 0 && (
        <>
          <div className="fcb-sec-l">doctor</div>
          <Rows rows={doctor.map((d) => [d.id, d.status + (d.detail ? " · " + d.detail : "")])} />
        </>
      )}
      {model.reasons.length > 0 && (
        <>
          <div className="fcb-sec-l">degraded · {model.reasons.length}</div>
          <Rows rows={model.reasons.map((r) => [_reasonLabel(r.reason), _dash(r.detail) + (r.fix === "cli" && r.command ? " · " + r.command : "")])} />
        </>
      )}
      {foot}
    </div>
  );
}

function ControlsTab({ live, identity, model, actions, busy, run, editPersona }) {
  const isPi = identity.id === "pi";
  if (model.phase === "not_installed") return <NotInstalledPane live={live} model={model} actions={actions} />;

  if (isPi) {
    const c = model.commands;
    return (
      <div className="fcb-pane-in">
        <div className="fc-note">Pi runs as the user who installed it, so these run on the box, not from here.</div>
        <CopyCommand live={live} label="upgrade" command={c.upgrade} testid="agent-action-copy-upgrade" actions={actions} />
        <CopyCommand live={live} label="uninstall" command={c.uninstall} testid="agent-action-copy-uninstall" actions={actions} />
        <CopyCommand live={live} label="reinstall" command={c.install} testid="agent-action-copy-install" actions={actions} />
        <ActBtn live={live} testid="agent-action-refresh" icon="refresh" label="Re-check" busyLabel="Checking…" wide
          busy={busy === "refresh"} onClick={actions.refresh && (() => run("refresh", actions.refresh))} />
      </div>
    );
  }
  return <HermesControls live={live} model={model} actions={actions} busy={busy} run={run} editPersona={editPersona} />;
}

// Controls is the default tab and the back stays mounted, so the persona list
// and the approvals count poll from page load. Both share the sidebar block's
// query keys, so react-query serves the card and the sidebar from one poll.
function HermesControls({ live, model, actions, busy, run, editPersona }) {
  const Icon = window.Icon;
  const usePersonas = window.__hal0UseAgentPersonas;
  const useApprovalsCount = window.__hal0UseAgentApprovalsCount;
  const personasQ = usePersonas ? usePersonas("hermes") : { data: null };
  const countQ = useApprovalsCount ? useApprovalsCount() : { data: null };

  const list = (personasQ.data && personasQ.data.personas) || [];
  const active = (personasQ.data && personasQ.data.active) || "";
  const ov = model.overview || {};
  const pending =
    typeof countQ.data === "number" ? countQ.data : ov.approvals && ov.approvals.pending != null ? ov.approvals.pending : null;
  const mode = model.chips.approvals;
  const working = model.phase === "working";
  const running = useContext(CardBusy);

  return (
    <div className="fcb-pane-in fcb-ctl">
      {model.reach.desktop ? (
        <a className="fc-act primary wide" href={model.reach.desktop} target="_blank" rel="noopener noreferrer"
          data-testid={live ? "agent-action-desktop" : undefined} onClick={stop}>
          <Icon name="ext" size={12} sw={1.5} />Open Hermes desktop
        </a>
      ) : (
        <div className="fc-slot-dim">
          Set HAL0_HERMES_PUBLIC_URL to link the desktop ·{" "}
          <a href="https://hal0.dev/docs/reference/env-vars/" target="_blank" rel="noopener noreferrer" onClick={stop}>
            docs
          </a>
        </div>
      )}

      <div className="fcb-grid2">
        <ActBtn live={live} testid="agent-action-restart" icon="refresh" label="Restart agent" busyLabel="Restarting…"
          busy={busy === "restart"} disabled={working} onClick={actions.restart && (() => run("restart", actions.restart))} />
        <ActBtn live={live} testid="agent-action-restart-gateway" icon="refresh" label="Restart gateway" busyLabel="Restarting…"
          busy={busy === "restartGateway"} disabled={working}
          onClick={actions.restartGateway && (() => run("restartGateway", actions.restartGateway))} />
        <ActBtn live={live} testid="agent-action-logs" icon="logs" label="Unit logs" free onClick={actions.openLogs} />
        <ActBtn live={live} testid="agent-action-activity" icon="tools" label="Tool activity" free onClick={actions.openActivity} />
      </div>

      <div className="fcb-field">
        <span className="fcb-sec-l">persona</span>
        <div className="fcb-row">
          <select
            className="fc-select"
            aria-label="Active persona"
            data-testid={live ? "agent-action-persona" : undefined}
            value={active}
            disabled={!actions.activatePersona || !list.length || !!running}
            onClick={stop}
            onChange={(e) => {
              const pid = e.target.value;
              if (pid && pid !== active) run("persona", () => actions.activatePersona(pid));
            }}
          >
            {!list.length && <option value="">—</option>}
            {list.map((p) => <option key={p.id} value={p.id}>{p.display_name || p.id}</option>)}
          </select>
          <ActBtn live={live} testid="agent-action-persona-edit" label="Edit" free
            onClick={active ? () => editPersona(active) : null} title="Edit the active persona" />
        </div>
      </div>

      <div className="fcb-field">
        <span className="fcb-sec-l">command approvals</span>
        <div className="fcb-row">
          <div className="fc-seg" role="radiogroup" aria-label="approvals mode">
            {["smart", "manual"].map((m) => (
              <button
                key={m}
                type="button"
                role="radio"
                aria-checked={mode === m}
                className={"fc-seg-b" + (mode === m ? " on" : "")}
                data-testid={live ? "agent-approvals-" + m : undefined}
                disabled={!actions.setApprovalsMode || !mode || !!running}
                onClick={(e) => {
                  stop(e);
                  if (m !== mode) run("approvals", () => actions.setApprovalsMode(m));
                }}
              >{m}</button>
            ))}
          </div>
          <ActBtn live={live} testid="agent-action-approvals-inbox" label={"inbox · " + _dash(pending)} free
            onClick={actions.openApprovals} title="Pending approval queue" />
        </div>
      </div>

      <ActBtn live={live} testid="agent-action-reconcile" icon="refresh" label="Re-mirror profiles to Open WebUI"
        busyLabel="Re-mirroring…" wide busy={busy === "reconcile"}
        onClick={actions.reconcile && (() => run("reconcile", actions.reconcile))} />
    </div>
  );
}

function LoreTab({ identity, model }) {
  const lore = model.lore || { abilities: [], skills: [] };
  return (
    <div className="fcb-pane-in">
      {identity.abilities.map((ab, i) => {
        const l = lore.abilities.find((x) => x.name === ab.name);
        const unlocked = !!(l && l.unlocked);
        return (
          <div className={"atk" + (unlocked ? "" : " locked")} key={i}>
            <div className="atk-h">
              <span className="atk-cost">
                {Array.from({ length: 3 }).map((_, n) => (
                  <span key={n} className={"atk-pip" + (n >= ab.cost ? " spent" : "")} />
                ))}
              </span>
              <span className="atk-name">{ab.name}</span>
              <span className="atk-pow"><span className="v">{ab.pow}</span><span className="u">pwr</span></span>
            </div>
            <div className="atk-desc">{ab.desc}</div>
            <div className={"atk-unlock" + (unlocked ? " on" : "")}>
              {unlocked ? "unlocked" : "locked · " + ((l && l.note) || "no live signal")}
            </div>
          </div>
        );
      })}
      <div className="fcb-skills">
        <div className="fcb-sec-l">Skills</div>
        <div className="skill-row">
          {lore.skills.length ? (
            lore.skills.map((s, i) => (
              <span className={"skill" + (s.lit ? " key" : " dim")} key={i}>
                {s.label}{!s.lit && s.note ? <i>{" " + s.note}</i> : null}
              </span>
            ))
          ) : (
            <span className="skill dim">—</span>
          )}
        </div>
      </div>
    </div>
  );
}

function LifecycleTab({ live, identity, model, actions, busy, ask }) {
  const isPi = identity.id === "pi";
  if (model.phase === "not_installed") return <NotInstalledPane live={live} model={model} actions={actions} />;
  const ov = model.overview || {};
  const v = ov.version || {};

  if (isPi) {
    return (
      <div className="fcb-pane-in">
        <Rows rows={[
          ["installed", model.version.label + (model.version.sub !== "—" ? " · " + model.version.sub : "")],
          ["pinned", model.version.pinLabel],
          ["status", model.updateAvailable ? "behind the pin" : "matches the pin"],
        ]} />
        <div className="fc-note">
          Upgrade reinstalls both pins with npm, redeploys the hal0 provider and hindsight extensions and the theme,
          rewrites the managed package entry and the hal0-memory MCP entry, and re-stamps the marker. Run it as the
          user who installed pi; extensions you added yourself are left alone.
        </div>
        <CopyCommand live={live} command={model.commands.upgrade} testid="agent-action-copy-upgrade" actions={actions} />
      </div>
    );
  }

  const live_ = v.live || {};
  const working = model.phase === "working";
  const upgrade = model.lifecycle.primary === "upgrade";
  return (
    <div className="fcb-pane-in">
      <div className="fcb-card">
        <div className="fcb-sec-l">live release</div>
        <Rows rows={[
          ["version", model.version.label],
          ["ref", _dash(live_.ref)],
          ["built", _fmtTime(live_.built_at)],
          ["python", _dash(live_.python_minor)],
          ["extras", _list(live_.extras)],
          ["platforms", _list(live_.platform_extras)],
          ["previous", model.lifecycle.previous ? model.lifecycle.previous + " · kept for automatic flip-back" : "—"],
        ]} />
      </div>
      <div className="fcb-pin">
        pin · <b>{model.version.pinLabel}</b> {model.updateAvailable ? "· update available" : "· live matches the pin"}
      </div>
      {upgrade ? (
        <ActBtn live={live} testid="agent-action-upgrade" label={"Upgrade to " + model.version.pinLabel} primary wide
          busy={busy === "upgrade"} disabled={working} title={working ? "a build is already running" : undefined}
          onClick={actions.upgrade && (() => ask("upgrade"))} />
      ) : (
        <ActBtn live={live} testid="agent-action-repair" label="Repair · rebuild at the pin" wide
          busy={busy === "repair"} disabled={working} title={working ? "a build is already running" : undefined}
          onClick={actions.repair && (() => ask("repair"))} />
      )}
      <div className="fcb-danger">
        <span className="fcb-sec-l">danger zone</span>
        <ActBtn live={live} testid="agent-action-uninstall" icon="trash" label={"Uninstall " + identity.name} danger wide
          busy={busy === "uninstall"} disabled={working} onClick={actions.uninstall && (() => ask("uninstall"))} />
      </div>
    </div>
  );
}

function ConsoleTabs({ id, tab, setTab, live }) {
  return (
    <div className="fcb-tabs" role="tablist" aria-label="console">
      {TABS.map(([k, label]) => (
        <button
          key={k}
          id={"agent-" + id + "-tab-" + k}
          type="button"
          role="tab"
          aria-selected={tab === k}
          aria-controls={"agent-" + id + "-panel"}
          className={"fcb-tab" + (tab === k ? " on" : "")}
          data-testid={"agent-tab-" + k}
          tabIndex={live && tab === k ? 0 : -1}
          onClick={(e) => { stop(e); setTab(k); }}
          onKeyDown={(e) => {
            // Arrow keys move between tabs (WAI-ARIA tabs pattern).
            if (e.key !== "ArrowRight" && e.key !== "ArrowLeft") return;
            e.preventDefault();
            const i = TABS.findIndex(([t]) => t === k);
            const next = TABS[(i + (e.key === "ArrowRight" ? 1 : TABS.length - 1)) % TABS.length][0];
            setTab(next);
            const el = document.getElementById("agent-" + id + "-tab-" + next);
            if (el) el.focus();
          }}
        >{label}</button>
      ))}
    </div>
  );
}

// Upgrade, repair and uninstall confirms. Rendered beside the card, not in
// it: the card's perspective/transform would trap a fixed-position modal.
function LifecycleConfirms({ identity, model, confirm, busy, onCancel, onConfirm }) {
  const ConfirmDialog = window.ConfirmDialog;
  if (!ConfirmDialog) return null;
  const tag = (k, label) => <span data-testid={"agent-confirm-" + k}>{label}</span>;
  return (
    <>
      <ConfirmDialog
        open={confirm === "upgrade"}
        confirmDisabled={!!busy}
        onCancel={onCancel}
        onConfirm={onConfirm}
        title="Upgrade Hermes to the pinned release?"
        confirmLabel={tag("upgrade", "Upgrade")}
        footerNote=""
        message={
          <div className="ao-confirm">
            <div className="mono ao-confirm-v">{model.version.label} → {model.version.pinLabel}</div>
            {UPGRADE_COPY.map((l, i) => <p key={i}>{l}</p>)}
            <p className="ao-confirm-note">If any step fails, the old release is flipped back automatically.</p>
          </div>
        }
      />
      <ConfirmDialog
        open={confirm === "repair"}
        confirmDisabled={!!busy}
        onCancel={onCancel}
        onConfirm={onConfirm}
        title="Rebuild the Hermes release at the pinned version?"
        confirmLabel={tag("repair", "Repair")}
        footerNote=""
        message={
          <div className="ao-confirm">
            <p>Re-runs every provisioning phase and rebuilds the venv. Use it when MCP tools vanish or doctor reports drift.</p>
            <p className="ao-confirm-note">Restarting hal0-api while it runs stops the build; the live release is kept.</p>
          </div>
        }
      />
      <ConfirmDialog
        open={confirm === "uninstall"}
        confirmDisabled={!!busy}
        onCancel={onCancel}
        onConfirm={onConfirm}
        title={"Uninstall " + identity.name + "?"}
        confirmLabel={tag("uninstall", "Uninstall")}
        destructive
        typeToConfirm={identity.id}
        message={
          "Removes " + identity.name + "'s seed, data and state from this box and drops its Open WebUI upstreams. " +
          "Install it again with " + model.commands.install + "."
        }
      />
    </>
  );
}

// ── live card (Hermes · Pi) ─────────────────────────────────────────
function LiveAgentCard({ identity, model, actions }) {
  const Icon = window.Icon;
  const StatusDot = window.StatusDot;
  const PersonaEditModal = window.PersonaEditModal;
  const a = identity;
  const isPi = a.id === "pi";
  const { innerRef, flipped, setFlipped, reduce } = useFlip();
  const { tiltRef, onMove, onLeave } = useTilt(reduce, { x: -10, y: 12 });
  const [tab, setTab] = useState("controls");
  const [confirm, setConfirm] = useState(null);
  const [personaEdit, setPersonaEdit] = useState(null);
  const [busy, run] = useRunner();

  // The back is a control surface: no tilt there, or a target near the edge
  // moves out from under the pointer between hover and click.
  useEffect(() => {
    if (flipped) onLeave();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [flipped]);

  const healthy = !isPi && (model.phase === "ready" || model.phase === "serving");
  const front = { live: !flipped, identity: a, model, actions, busy, run, ask: setConfirm };
  const back = { live: flipped, identity: a, model, actions, busy, run, ask: setConfirm, editPersona: setPersonaEdit };
  const homeChip = isPi && model.overview && model.overview.profile ? model.overview.profile.home : null;
  const versionText =
    model.phase === "not_installed" && model.version.label === "—" ? "pin " + model.version.pinLabel : model.version.label;

  const onConfirm = () => {
    const k = confirm;
    setConfirm(null);
    const fn = k === "upgrade" ? actions.upgrade : k === "repair" ? actions.repair : k === "uninstall" ? actions.uninstall : null;
    run(k, fn);
  };

  let pane;
  if (tab === "status") pane = <StatusTab {...back} />;
  else if (tab === "lore") pane = <LoreTab {...back} />;
  else if (tab === "lifecycle") pane = <LifecycleTab {...back} />;
  else pane = <ControlsTab {...back} />;

  return (
    <CardBusy.Provider value={busy}>
      <div
        className={"fcard" + (PHASE_MOD[model.phase] || "")}
        data-testid={"agent-card-" + a.id}
        style={a.el ? { "--el": a.el, "--el-glow": a.elGlow } : undefined}
        onMouseMove={flipped ? undefined : onMove}
        onMouseLeave={onLeave}
        onClick={() => setFlipped((f) => !f)}
      >
        <div className="fcard-tilt" ref={tiltRef}>
          <div className="fcard-inner" ref={innerRef} style={{ transform: "rotateY(" + (flipped ? 180 : 0) + "deg)" }}>

            {/* ── FRONT ── */}
            <div
              className="fc-face fc-front"
              inert={flipped}
              style={{ opacity: flipped ? 0 : 1 }}
              data-testid={"agent-phase-" + a.id}
              data-phase={model.phase}
            >
              <div className="foilb-clip">
                {/* Agents with a full-bleed portrait (Hermes) render it; agents
                    with only a logo mark (Pi) reuse the abstract grid+mark
                    treatment authored for the roadmap cards. */}
                {a.art ? (
                  <img className="foilb-img" src={a.art} alt={a.name} />
                ) : (
                  <div className="pi-stage">
                    <span className="pi-grid" />
                    <div className="pi-mark-wrap">
                      {a.logo ? (
                        <img
                          className="pi-mark"
                          src={a.logo}
                          alt={a.name + " logo"}
                          style={{ width: (a.logoScale || 0.86) * 100 + "%", height: (a.logoScale || 0.86) * 100 + "%" }}
                        />
                      ) : null}
                    </div>
                  </div>
                )}
                <span className="foilb-vig" />
                <span className="foilb-scan" />
                <span className="foilb-holo" />

                <div className="foilb-top">
                  <div className="fc-id">
                    <div className="fc-name-row">
                      <StatusDot cls={model.statusCls} />
                      <span className="fc-name">{a.name}</span>
                    </div>
                    <div className="fc-version-row">
                      <span className="fc-version" data-testid={"agent-version-" + a.id} title={model.version.sub}>
                        {versionText}
                      </span>
                      {model.updateAvailable && model.phase !== "working" && model.phase !== "not_installed" && (
                        <span className="fc-badge" data-testid={"agent-update-badge-" + a.id} title="update available">
                          ↑ {model.version.pinLabel}
                        </span>
                      )}
                    </div>
                    {model.version.sub !== "—" && <span className="fc-sub">{model.version.sub}</span>}
                    <span className="fc-model">{a.model}</span>
                    {homeChip && <span className="fc-chip home">home · <b>{homeChip}</b></span>}
                  </div>
                  {healthy ? (
                    <HealthBlock health={model.health} statusCls={model.statusCls} />
                  ) : (
                    <PhaseBlock model={model} isPi={isPi} />
                  )}
                </div>

                <div className="foilb-bottom">
                  <PhaseBand {...front} />
                </div>
              </div>
            </div>

            {/* ── BACK ── */}
            <div className="fc-face fc-back" inert={!flipped} style={{ opacity: flipped ? 1 : 0 }}>
              <div className="fcb-h">
                <Icon name="agent" size={15} sw={1.4} />
                <span className="l"><b>{a.name}</b> · console</span>
                <span style={{ marginLeft: "auto" }}><StatusDot cls={model.statusCls} /></span>
              </div>
              <ConsoleTabs id={a.id} tab={tab} setTab={setTab} live={flipped} />
              <div
                className="fcb-body fcb-pane"
                role="tabpanel"
                id={"agent-" + a.id + "-panel"}
                aria-labelledby={"agent-" + a.id + "-tab-" + tab}
              >{pane}</div>
            </div>

          </div>
        </div>
      </div>

      {!isPi && (
        <LifecycleConfirms
          identity={a}
          model={model}
          confirm={confirm}
          busy={busy}
          onCancel={() => setConfirm(null)}
          onConfirm={onConfirm}
        />
      )}
      {!isPi && PersonaEditModal && (
        <PersonaEditModal open={!!personaEdit} personaId={personaEdit} onClose={() => setPersonaEdit(null)} />
      )}
    </CardBusy.Provider>
  );
}

// ── locked / coming-soon card ───────────────────────────────────────
function LockedAgentCard({ agent }) {
  const Icon = window.Icon;
  const a = agent;
  return (
    <div
      className="fcard locked"
      data-testid={"agent-card-locked-" + a.id}
      style={{ "--el": a.el, "--el-glow": a.elGlow }}
    >
      <div className="fcard-tilt">
        <div className="fcard-inner">
          <div className="fc-face fc-front">
            <div className="foilb-clip">
              <div className="pi-stage">
                <span className="pi-grid" />
                <div className="pi-mark-wrap">
                  {a.logo ? (
                    <img
                      className="pi-mark"
                      src={a.logo}
                      alt={a.name + " logo"}
                      style={{ width: (a.logoScale || 0.86) * 100 + "%", height: (a.logoScale || 0.86) * 100 + "%" }}
                    />
                  ) : null}
                </div>
              </div>

              <div className="foilb-top">
                <div className="fc-id">
                  <div className="fc-name-row">
                    <span className="dot-empty" />
                    <span className={"fc-name" + (a.caps ? " caps" : "")}>{a.name}</span>
                  </div>
                  <span className="fc-model">{a.model}</span>
                </div>
                <span className="soon-pill"><Icon name="clock" size={10} sw={1.6} />soon</span>
              </div>

              <div className="foilb-bottom">
                <div className="fc-role">{a.role}</div>
              </div>

              {/* grey mask + label across the whole card */}
              <div className="ao-mask">
                <span className="ao-mask-label">Coming soon</span>
                <span className="ao-mask-eta"><Icon name="clock" size={10} sw={1.6} />roadmap · <b>{a.eta}</b></span>
              </div>
            </div>
          </div>
        </div>
      </div>
    </div>
  );
}

Object.assign(window, { LiveAgentCard, LockedAgentCard });
