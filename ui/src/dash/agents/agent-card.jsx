// hal0 dashboard — collectible agent cards (Agents Overview).
//
// Two presentational cards, productionised from Design/agent-card-kit:
//   LiveAgentCard   — the `serving` foil (Hermes). Full-bleed portrait,
//                     live health (status dot + throughput + context meter)
//                     on the front, abilities/skills on the flip side, and a
//                     quick-actions zone (Logs · Persona) above a primary
//                     Restart button pinned to the very bottom edge.
//   LockedAgentCard — a roadmap entry behind a grey "coming soon" mask. The
//                     dummy module tile + identity show through; the card is
//                     non-interactive (no flip) until the integration ships.
//
// Both are pure props-in components — agents-overview.jsx owns the data
// (window hooks) and the action handlers. Window-globals shim: register on
// `window`, read React / Icon / StatusDot from the same. No ES imports
// across dash/* (main.tsx load order is the contract).

const { useState, useRef, useLayoutEffect } = React;

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

const RESTART_LABELS = { idle: "Restart", busy: "Restarting…", ok: "Restarted", err: "Restart failed" };

// ── live foil card (Hermes) ─────────────────────────────────────────
function LiveAgentCard({ agent, health, statusCls, statusLabel, restart, onLogs, onPersona }) {
  const Icon = window.Icon;
  const StatusDot = window.StatusDot;
  const a = agent;
  const { innerRef, flipped, setFlipped, reduce } = useFlip();
  const { tiltRef, onMove, onLeave } = useTilt(reduce, { x: -10, y: 12 });
  const stop = (e) => e.stopPropagation();
  const rState = restart.state || "idle";
  const restartBusy = rState === "busy";

  return (
    <div
      className="fcard"
      data-testid="agent-card-hermes"
      onMouseMove={onMove}
      onMouseLeave={onLeave}
      onClick={() => setFlipped((f) => !f)}
    >
      <div className="fcard-tilt" ref={tiltRef}>
        <div className="fcard-inner" ref={innerRef} style={{ transform: "rotateY(" + (flipped ? 180 : 0) + "deg)" }}>

          {/* ── FRONT ── */}
          <div className="fc-face fc-front" style={{ opacity: flipped ? 0 : 1 }}>
            <div className="foilb-clip">
              <img className="foilb-img" src={a.art} alt={a.name} />
              <span className="foilb-vig" />
              <span className="foilb-scan" />
              <span className="foilb-holo" />

              <div className="foilb-top">
                <div className="fc-id">
                  <div className="fc-name-row">
                    <StatusDot cls={statusCls} />
                    <span className="fc-name">{a.name}</span>
                  </div>
                  <span className="fc-model">{a.model}</span>
                </div>
                <HealthBlock health={health} statusCls={statusCls} />
              </div>

              <div className="foilb-bottom">
                <div className="fc-role">{a.role}</div>
                <div className="fc-meta">
                  <span className={"fc-st" + (statusCls === "serving" ? "" : statusCls === "error" ? " error" : " idle")}>
                    <StatusDot cls={statusCls} />{statusLabel}
                  </span>
                  <span className="flip-hint"><Icon name="refresh" size={11} sw={1.5} />tap · abilities</span>
                  <span className="fc-rarity" title="holo rare">
                    {[0, 1, 2, 3, 4].map((i) => <span key={i} className={"fc-star" + (i < a.rarity ? "" : " off")} />)}
                  </span>
                </div>
              </div>
            </div>
          </div>

          {/* ── BACK ── */}
          <div className="fc-face fc-back" style={{ opacity: flipped ? 1 : 0 }}>
            <div className="fcb-h">
              <Icon name="agent" size={15} sw={1.4} />
              <span className="l"><b>{a.name}</b> · abilities</span>
              <span style={{ marginLeft: "auto" }}><StatusDot cls={statusCls} /></span>
            </div>
            <div className="fcb-body">
              {a.abilities.map((ab, i) => (
                <div className="atk" key={i}>
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
                </div>
              ))}
              <div className="fcb-skills">
                <div className="fcb-sec-l">Skills</div>
                <div className="skill-row">
                  {a.skills.map((s, i) => (
                    <span className={"skill" + (s.key ? " key" : "")} key={i}>{s.l}</span>
                  ))}
                </div>
              </div>
            </div>

            {/* quick actions — secondary row, then Restart pinned to the bottom */}
            <div className="fcb-actions" onClick={stop}>
              <div className="fcb-act-row">
                <button className="fc-act" data-testid="agent-action-logs" onClick={(e) => { stop(e); onLogs && onLogs(); }}>
                  <Icon name="logs" size={12} sw={1.5} />Logs
                </button>
                <button className="fc-act" data-testid="agent-action-persona" onClick={(e) => { stop(e); onPersona && onPersona(); }}>
                  <Icon name="agent" size={12} sw={1.5} />Persona
                </button>
              </div>
              <button
                className={"fc-restart" + (rState !== "idle" ? " " + rState : "")}
                data-testid="agent-action-restart"
                disabled={restartBusy}
                onClick={(e) => { stop(e); restart.onClick && restart.onClick(); }}
              >
                <Icon name="refresh" size={13} sw={1.7} className={restartBusy ? "spin" : undefined} />
                {RESTART_LABELS[rState] || RESTART_LABELS.idle}
              </button>
            </div>
          </div>

        </div>
      </div>
    </div>
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
                  <img
                    className="pi-mark"
                    src={a.logo}
                    alt={a.name + " logo"}
                    style={{ width: (a.logoScale || 0.86) * 100 + "%", height: (a.logoScale || 0.86) * 100 + "%" }}
                  />
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
