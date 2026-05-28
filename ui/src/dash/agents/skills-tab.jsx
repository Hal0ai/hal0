// hal0 v0.3 PR-8 — SkillsTab.
//
// Lifted verbatim from the old AgentView monolith (extras.jsx). The
// skill catalog stays static for v0.3 — wiring to /api/agents/skills
// is queued in master plan §4 PR-11 (docs sweep + endpoint catalog).

function SkillsTab() {
  const skills = [
    { name: "read_file",       cap: "fs-read",        policy: "remember", calls: 247, src: "builtin" },
    { name: "write_file",      cap: "fs-write",       policy: "always",   calls: 38,  src: "builtin" },
    { name: "edit_file",       cap: "fs-write",       policy: "always",   calls: 14,  src: "builtin" },
    { name: "list_dir",        cap: "fs-read",        policy: "remember", calls: 41,  src: "builtin" },
    { name: "shell_exec",      cap: "shell-exec",     policy: "always",   calls: 9,   src: "builtin" },
    { name: "model_pull",      cap: "registry-write", policy: "always",   calls: 3,   src: "hal0-router" },
    { name: "restart_slot",    cap: "slot-control",   policy: "always",   calls: 1,   src: "hal0-router" },
    { name: "generate_image",  cap: "tool-call",      policy: "auto",     calls: 18,  src: "omnirouter" },
    { name: "transcribe_audio",cap: "tool-call",      policy: "auto",     calls: 7,   src: "omnirouter" },
    { name: "text_to_speech",  cap: "tool-call",      policy: "auto",     calls: 22,  src: "omnirouter" },
    { name: "embed_text",      cap: "tool-call",      policy: "auto",     calls: 184, src: "omnirouter" },
    { name: "rerank_documents",cap: "tool-call",      policy: "auto",     calls: 41,  src: "omnirouter" },
  ];
  return (
    <div data-testid="skills-tab">
      <div className="card" style={{overflow: "hidden"}}>
        <div style={{padding: "10px 18px", background: "var(--bg)", borderBottom: "1px solid var(--line)", display: "grid", gridTemplateColumns: "200px 160px 1fr 120px 80px auto", gap: 16, fontFamily: "var(--jbm)", fontSize: 10, color: "var(--fg-4)", textTransform: "uppercase", letterSpacing: "0.08em"}}>
          <span>skill</span>
          <span>capability</span>
          <span>source</span>
          <span>policy</span>
          <span style={{textAlign: "right"}}>calls</span>
          <span></span>
        </div>
        {skills.map(s => (
          <div key={s.name} style={{padding: "11px 18px", borderBottom: "1px solid var(--line-soft)", display: "grid", gridTemplateColumns: "200px 160px 1fr 120px 80px auto", gap: 16, alignItems: "center", fontFamily: "var(--jbm)", fontSize: 12}}>
            <span style={{color: "var(--fg)", fontWeight: 500}}>{s.name}</span>
            <span style={{color: "var(--fg-3)"}}>{s.cap}</span>
            <span style={{color: "var(--fg-4)"}}>{s.src}</span>
            <span>
              {s.policy === "always"   && <span className="chip warn">always</span>}
              {s.policy === "remember" && <span className="chip ok">remember</span>}
              {s.policy === "auto"     && <span className="chip">auto</span>}
              {s.policy === "deny"     && <span className="chip err">deny</span>}
            </span>
            <span style={{textAlign: "right", color: "var(--fg-2)"}} className="num">{s.calls}</span>
            <button className="btn ghost sm">{Icons.edit}</button>
          </div>
        ))}
      </div>
      <div style={{marginTop: 14, fontFamily: "var(--jbm)", fontSize: 11, color: "var(--fg-4)"}}>
        12 skills wired · 8 require approval · 4 auto via OmniRouter · skill source includes builtin, hal0-router, omnirouter, and any user-added MCP servers (none configured).
      </div>
    </div>
  );
}

Object.assign(window, { SkillsTab });
