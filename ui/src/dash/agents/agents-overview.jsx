// hal0 dashboard — Agents Overview (the #agent landing tab).
//
// The library of agents as collectible cards. Hermes is the live `serving`
// foil, wired to real data:
//   - agent liveness   ← window.__hal0UseAgents()  (GET /api/agents, 5s)
//   - throughput + ctx ← window.__hal0UseSlots()    (primary slot metrics, 2.5s)
//   - Restart          → window.__hal0UseAgentRestart("hermes")
//                          (POST /api/agents/hermes/restart → systemctl)
// The rest of the library (Pi · Qwen · OpenCode) are roadmap entries shown
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

// ── roadmap (coming-soon) cards — curated dummy content ─────────────
function _lockedRoster() {
  const art = window.__hal0AgentArt || {};
  return [
    {
      id: "pi", name: "Pi", caps: true, el: "#6f7785", elGlow: "rgba(143,160,179,0.16)",
      logo: art.pi, logoScale: 0.5, model: "qwen2.5-coder-32b",
      role: "autonomous coding · repo-aware engineering", eta: "soon",
    },
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
          Every agent in the runtime as a collectible card. <b>Hermes</b> is live — the card
          streams its real endpoint health and flips to its abilities, skills, and quick
          actions. The rest are on the roadmap.
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
        {LockedAgentCard && _lockedRoster().map((a) => <LockedAgentCard key={a.id} agent={a} />)}
      </div>

      {PersonaEditModal && (
        <PersonaEditModal open={personaOpen} onClose={() => setPersonaOpen(false)} />
      )}
    </div>
  );
}

Object.assign(window, { AgentsOverview });
