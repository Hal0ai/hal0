// hal0 dashboard — Agents Overview (the #agent landing tab).
//
// The library of agents as collectible cards. Hermes and Pi are the live
// bundled-agent foils (ADR-0004 §2 single-pick — exactly one of the two is
// actually installed on a given box, but both light up here so an operator
// can see the card either way, with status reflecting reality):
//   - agent liveness   ← window.__hal0UseAgents()  (GET /api/agents, 5s)
//   - throughput + ctx ← window.__hal0UseSlots()    (primary slot metrics,
//                          2.5s) — Hermes only; Pi is a CLI tool invoked
//                          against whichever hal0 slot the operator picks,
//                          not one dedicated backing slot, so its health
//                          block shows install status without throughput.
//   - Restart          → window.__hal0UseAgentRestart("hermes")
//                          (POST /api/agents/hermes/restart → systemctl).
//                          Pi has no systemd unit and no persona store, so
//                          its card has no Restart/Persona actions.
// The rest of the library (Qwen · OpenCode) are roadmap entries shown
// behind a grey "coming soon" mask with curated dummy content.
//
// Window-globals shim — register on window, read React + hooks + cards from
// the same. No ES imports across dash/* (main.tsx load order is the contract).

const { useState, useRef, useEffect } = React;

// ── static (curated) identity for the live Hermes card ──────────────
// Health + status + the backing model are live; the rest is the kit's
// authored content. `liveModel` is the model the orchestrated agent slot
// is actually serving (falls back to a dash when no slot is resolved).
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
    skills: [
      { l: "voice · tts", key: true },
      { l: "speech · stt", key: true },
      { l: "image-gen", key: true },
      { l: "vision", key: true },
      { l: "embeddings" },
    ],
  };
}

// ── static (curated) identity for the live Pi card ───────────────────
// Pi is a CLI tool, not a backing slot — `model` is the default model
// hal0's pi-coder driver wires it to (hal0-provider extension), not a
// live slot reading.
function _piCoderIdentity() {
  return {
    id: "pi-coder",
    name: "Pi",
    model: "hal0/agent",
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
    skills: [
      { l: "repo-aware", key: true },
      { l: "shared memory", key: true },
      { l: "delegation", key: true },
      { l: "cli-native" },
      { l: "theme" },
    ],
  };
}

// ── static (curated) identity for the live Turnstone card ────────────
// Turnstone is a self-hosted orchestration platform running its own server
// on loopback :9129; it routes every model through the hal0-api gateway and
// mounts hal0-memory + hal0-admin over MCP. Like Pi it has no dedicated
// backing slot — `model` is the default gateway virtual its config maps to.
function _turnstoneIdentity() {
  return {
    id: "turnstone",
    name: "Turnstone",
    model: "hal0/agent",
    role: "tool-using orchestration · judged · multi-workstream",
    rarity: 3,
    el: "#4fa9c9",
    elGlow: "rgba(79,169,201,0.18)",
    logo: (window.__hal0AgentArt && window.__hal0AgentArt.turnstone) || "",
    logoScale: 0.7,
    abilities: [
      { name: "Local Routing", cost: 1, desc: "Maps every live hal0 slot to a model alias — all inference stays on the box.", pow: "70" },
      { name: "Intent Judge", cost: 2, desc: "An LLM grades every tool call for risk before it runs; destructive acts gate for approval.", pow: "80" },
      { name: "Workstreams", cost: 3, desc: "Runs parallel tool-using sessions with its own memory bank and MCP tools.", pow: "85" },
    ],
    skills: [
      { l: "mcp tools", key: true },
      { l: "memory", key: true },
      { l: "judge · approvals", key: true },
      { l: "web · search" },
      { l: "server · sse" },
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

// Resolve the LLM slot Hermes orchestrates (its throughput/ctx source). The
// runtime names it `agent` (the GPU agent slot); fall back through the other
// chat-capable names, then any LLM slot. There is no `primary`/`isDefault`
// marker in the live topology, so name + type are the only honest signals.
function _primarySlot(slots) {
  if (!Array.isArray(slots)) return null;
  return (
    slots.find((s) => s.name === "primary") ||
    slots.find((s) => s.name === "agent") ||
    slots.find((s) => s.name === "chat") ||
    slots.find((s) => s.isDefault) ||
    slots.find((s) => s.type === "llm") ||
    null
  );
}

// Map agent liveness + slot activity → StatusDot cls + a short label.
// Mirrors useSidebarAgentRollup: an `installed` AgentRecord IS the running
// state (the agent runs as a systemd unit), `broken` is down. We then upgrade
// the dot to `serving` (green) when the backing slot is actively generating,
// and otherwise show `ready` (amber) — never a fake "serving" while idle.
function _derive(agentRec, slot) {
  if (!agentRec) return { cls: "offline", label: "not installed" };
  const status = String(agentRec.status || "").toLowerCase();
  if (status === "broken" || /error|fail|crash|down/.test(status)) {
    return { cls: "error", label: "down" };
  }
  const servingNow =
    !!slot && (slot.state === "serving" || (slot.metrics && slot.metrics.toks > 0));
  return servingNow
    ? { cls: "serving", label: "serving" }
    : { cls: "stale", label: "ready" };
}

function _health(slot) {
  const m = (slot && slot.metrics) || {};
  const toks = m.toks;
  const ctxUsed = m.ctx;
  const ctxMax = slot && slot.ctx_max != null ? slot.ctx_max : null;
  const ctxPct = ctxUsed != null && ctxMax ? Math.min(100, (ctxUsed / ctxMax) * 100) : 0;
  return {
    tput: toks != null && toks > 0 ? Math.round(toks) + " tok/s" : null,
    ctxUsed: _fmtK(ctxUsed),
    ctxMax: _fmtK(ctxMax),
    ctxPct,
  };
}

function AgentsOverview() {
  const LiveAgentCard = window.LiveAgentCard;
  const LockedAgentCard = window.LockedAgentCard;
  const PersonaEditModal = window.PersonaEditModal;

  const useAgents = window.__hal0UseAgents;
  const useSlots = window.__hal0UseSlots;
  const useAgentRestart = window.__hal0UseAgentRestart;

  const agentsQ = useAgents ? useAgents() : { data: null };
  const slotsQ = useSlots ? useSlots() : { data: null };
  const restart = useAgentRestart ? useAgentRestart("hermes") : null;

  const [restartState, setRestartState] = useState("idle"); // idle | busy | ok | err
  const [personaOpen, setPersonaOpen] = useState(false);
  const resetTimer = useRef(null);

  useEffect(() => () => { if (resetTimer.current) clearTimeout(resetTimer.current); }, []);

  const agents = (agentsQ.data && agentsQ.data.agents) || [];
  const hermesRec = agents.find((a) => a.name === "hermes" || a.id === "hermes") || null;
  const primary = _primarySlot(slotsQ.data);
  const { cls: statusCls, label: statusLabel } = _derive(hermesRec, primary);
  const health = _health(primary);

  // Pi is a CLI tool, not a backing service — no slot of its own to read
  // throughput/ctx from (_derive/_health both tolerate a null slot: status
  // falls back to "ready"/"not installed"/"down" off the AgentRecord alone,
  // health renders "—" placeholders).
  const piRec = agents.find((a) => a.name === "pi-coder" || a.id === "pi-coder") || null;
  const { cls: piStatusCls, label: piStatusLabel } = _derive(piRec, null);
  const piHealth = _health(null);

  // Turnstone runs its own server (loopback :9129), not a hal0 backing slot —
  // like Pi, status comes off the AgentRecord alone and health renders "—".
  const turnstoneRec =
    agents.find((a) => a.name === "turnstone" || a.id === "turnstone") || null;
  const { cls: tsStatusCls, label: tsStatusLabel } = _derive(turnstoneRec, null);
  const tsHealth = _health(null);

  const onRestart = () => {
    if (!restart || restartState === "busy") return;
    setRestartState("busy");
    if (resetTimer.current) clearTimeout(resetTimer.current);
    restart
      .mutateAsync()
      .then((res) => {
        // "restarting" (Type=notify handshake in flight) still resolves the
        // call — show success; the live polls converge the dot afterwards.
        setRestartState(res && res.status === "error" ? "err" : "ok");
      })
      .catch(() => setRestartState("err"))
      .finally(() => {
        resetTimer.current = setTimeout(() => setRestartState("idle"), 2600);
      });
  };

  const onLogs = () => { window.location.hash = "#logs"; };
  const onPersona = () => setPersonaOpen(true);

  return (
    <div className="agents-overview" data-testid="agents-overview">
      <div className="ao-head">
        <div className="ao-eye">hal0 · agent library</div>
        <p className="ao-sub">
          Every agent in the runtime as a collectible card. <b>Hermes</b>, <b>Pi</b>, and
          <b> Turnstone</b> are live — their cards stream real install/endpoint status and
          flip to abilities, skills, and quick actions. The rest are on the roadmap.
        </p>
        <div className="ao-legend">
          <span className="ao-lz"><span className="d serving" />Serving <span className="k">· live, wired</span></span>
          <span className="ao-lz"><span className="d soon" />Coming soon <span className="k">· on the roadmap</span></span>
        </div>
      </div>

      <div className="ao-grid">
        {LiveAgentCard && (
          <LiveAgentCard
            agent={_hermesIdentity(primary && primary.model)}
            health={health}
            statusCls={statusCls}
            statusLabel={statusLabel}
            restart={{ state: restartState, onClick: onRestart }}
            onLogs={onLogs}
            onPersona={onPersona}
          />
        )}
        {LiveAgentCard && (
          <LiveAgentCard
            agent={_piCoderIdentity()}
            health={piHealth}
            statusCls={piStatusCls}
            statusLabel={piStatusLabel}
          />
        )}
        {LiveAgentCard && (
          <LiveAgentCard
            agent={_turnstoneIdentity()}
            health={tsHealth}
            statusCls={tsStatusCls}
            statusLabel={tsStatusLabel}
          />
        )}
        {LockedAgentCard && _lockedRoster().map((a) => <LockedAgentCard key={a.id} agent={a} />)}
      </div>

      {PersonaEditModal && (
        <PersonaEditModal open={personaOpen} onClose={() => setPersonaOpen(false)} />
      )}
    </div>
  );
}

Object.assign(window, { AgentsOverview });
