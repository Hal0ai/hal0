// hal0 operator board — BoardView shell + BoardIcon
//
// Window-global module. NO ES imports from other dash/* files.
// Exports:
//   window.BoardView   — main board surface (mounts into .view)
//   window.BoardIcon   — svg icon component (KIT_GLYPHS)
//
// Dependencies (window globals):
//   window.React              — globals-install
//   window.BoardLane          — lane.jsx (loaded before this in main.tsx)
//   window.BoardCard          — kcard.jsx
//   window.TaskDrawer         — drawers agent
//   window.AgentChat          — drawers agent
//   window.NewBoardModal      — drawers agent
//   window.OrchPopover        — drawers agent
//
// Hook bridge (board-hook-bridge.ts publishes onto window):
//   window.__hal0UseBoardView
//   window.__hal0UseBoards
//   window.__hal0UseBoardProfiles
//   window.__hal0UseBoardAssignees
//   window.__hal0UseBoardOrchestration
//   window.__hal0UseBoardConfig
//   window.__hal0UseUpdateTask
//   window.__hal0UseBulkTasks
//   window.__hal0UseDeleteTask
//   window.__hal0UseReassignTask
//   window.__hal0UseNudgeDispatch
//   window.__hal0UseSwitchBoard
//   window.__hal0UseCreateBoard
//   window.__hal0UseCreateTask
//   window.__hal0UseBoardEventsStream

const { useState, useEffect, useMemo, useRef } = React;

// ─── Icon glyph set (exact replica of prototype board-chrome.jsx KIT_GLYPHS) ─
const KIT_GLYPHS = {
  dashboard: <g><rect x="2" y="2" width="5" height="5" rx="1"/><rect x="9" y="2" width="5" height="9" rx="1"/><rect x="2" y="9" width="5" height="5" rx="1"/></g>,
  slots:     <g><rect x="2" y="3" width="12" height="3" rx="0.5"/><rect x="2" y="7" width="12" height="3" rx="0.5"/><rect x="2" y="11" width="12" height="3" rx="0.5"/><circle cx="4" cy="4.5" r="0.6" fill="currentColor" stroke="none"/><circle cx="4" cy="8.5" r="0.6" fill="currentColor" stroke="none"/><circle cx="4" cy="12.5" r="0.6" fill="currentColor" stroke="none"/></g>,
  models:    <g><path d="M2 4l6-2 6 2-6 2-6-2z"/><path d="M2 8l6 2 6-2"/><path d="M2 12l6 2 6-2"/></g>,
  board:     <g><rect x="2" y="2.5" width="3.2" height="11" rx="0.8"/><rect x="6.4" y="2.5" width="3.2" height="7" rx="0.8"/><rect x="10.8" y="2.5" width="3.2" height="9" rx="0.8"/></g>,
  logs:      <path d="M3 3h10M3 6h10M3 9h7M3 12h5"/>,
  connections: <g><circle cx="6" cy="8" r="2.5"/><circle cx="11" cy="11" r="1.5" fill="currentColor" stroke="none"/><path d="M8 9.5l2 1M3.5 4.5h4M3.5 6.5h3"/></g>,
  settings:  <g><circle cx="8" cy="8" r="2"/><path d="M8 1v2M8 13v2M1 8h2M13 8h2M3 3l1.5 1.5M11.5 11.5L13 13M3 13l1.5-1.5M11.5 4.5L13 3"/></g>,
  bell:      <path d="M4 11h8c-1 0-1.5-0.5-1.5-2V6.5a2.5 2.5 0 0 0-5 0V9c0 1.5-0.5 2-1.5 2zM6.5 13a1.5 1.5 0 0 0 3 0"/>,
  search:    <g><circle cx="7" cy="7" r="4"/><path d="M10 10l3 3"/></g>,
  close:     <path d="M4 4l8 8M12 4l-8 8"/>,
  plus:      <path d="M8 3v10M3 8h10"/>,
  chev:      <path d="M4 6l4 4 4-4"/>,
  chevR:     <path d="M6 4l4 4-4 4"/>,
  check:     <path d="M3 8l3 3 7-7"/>,
  send:      <g><path d="M14 2L7 9"/><path d="M14 2l-4.5 12-2.5-5-5-2.5L14 2z"/></g>,
  refresh:   <g><path d="M14 8a6 6 0 1 1-2-4.5"/><path d="M14 1v3.5h-3.5"/></g>,
  dispatch:  <g><path d="M2 8h7"/><path d="M6 5l3 3-3 3"/><path d="M11 3v10"/><path d="M14 3v10"/></g>,
  link:      <g><path d="M6.5 9.5l3-3M6.2 5.2l1-1a2.4 2.4 0 0 1 3.4 3.4l-1 1M9.8 10.8l-1 1a2.4 2.4 0 0 1-3.4-3.4l1-1"/></g>,
  dep:       <g><circle cx="4" cy="4" r="1.6"/><circle cx="12" cy="12" r="1.6"/><path d="M4 5.6V9a2 2 0 0 0 2 2h4.4"/></g>,
  comment:   <path d="M2.5 3.5h11v7h-6l-3 2.5V10.5h-2z"/>,
  flag:      <g><path d="M4 14V2.5"/><path d="M4 3h8l-1.5 2.5L12 8H4"/></g>,
  archive:   <g><rect x="2" y="3" width="12" height="3" rx="0.8"/><path d="M3 6v7h10V6M6.5 9h3"/></g>,
  clock:     <g><circle cx="8" cy="8" r="6"/><path d="M8 4.5V8l2.5 1.5"/></g>,
  sliders:   <g><path d="M3 4h6M11 4h2M3 8h2M7 8h6M3 12h8M13 12h0.5"/><circle cx="10" cy="4" r="1.4" fill="currentColor" stroke="none"/><circle cx="6" cy="8" r="1.4" fill="currentColor" stroke="none"/><circle cx="12" cy="12" r="1.4" fill="currentColor" stroke="none"/></g>,
  chat:      <g><path d="M2.5 3h11v8h-7l-3 2.5V11h-1z"/><path d="M5 6h6M5 8h4"/></g>,
  trash:     <g><path d="M3 4h10M6 4V2.5h4V4M5 4l.7 9h4.6L11 4"/></g>,
  more:      <g><circle cx="3" cy="8" r="1" fill="currentColor" stroke="none"/><circle cx="8" cy="8" r="1" fill="currentColor" stroke="none"/><circle cx="13" cy="8" r="1" fill="currentColor" stroke="none"/></g>,
  edit:      <g><path d="M3 11.5V13h1.5L12 5.5 10.5 4z"/><path d="M9.5 5l1.5 1.5"/></g>,
  spark:     <path d="M8 2l1.4 4.2L14 8l-4.6 1.8L8 14l-1.4-4.2L2 8l4.6-1.8z"/>,
};

function BoardIcon({ name, size = 16, sw = 1.5 }) {
  return (
    <svg
      width={size}
      height={size}
      viewBox="0 0 16 16"
      fill="none"
      stroke="currentColor"
      strokeWidth={sw}
      strokeLinecap="round"
      strokeLinejoin="round"
      aria-hidden="true"
    >
      {KIT_GLYPHS[name]}
    </svg>
  );
}

// ─── Lanes definition (mirrors prototype LANES) ───────────────────────────────
const BOARD_LANES = [
  { id: "triage",    name: "Triage",    desc: "New tasks, awaiting spec" },
  { id: "todo",      name: "To-do",     desc: "Specified + queued" },
  { id: "scheduled", name: "Scheduled", desc: "Time-triggered tasks" },
  { id: "ready",     name: "Ready",     desc: "Claimed, agent dispatching" },
  { id: "running",   name: "Running",   desc: "Worker active" },
  { id: "blocked",   name: "Blocked",   desc: "Waiting for resource or input" },
  { id: "review",    name: "Review",    desc: "Needs operator sign-off" },
  { id: "done",      name: "Done",      desc: "Completed" },
];

// ─── Lightweight dropdown (filter selects) ────────────────────────────────────
function BoardSelect({ label, value, options, onChange, width }) {
  const [open, setOpen] = useState(false);
  const cur = options.find(o => o.id === value) || options[0];
  return (
    <div className="flt">
      <label>{label}</label>
      <div className="sel-wrap">
        <button
          className="sel-btn"
          style={width ? { minWidth: width } : null}
          onClick={() => setOpen(o => !o)}
        >
          <span className="v">{cur ? cur.label : value}</span>
          <span className="cv"><BoardIcon name="chev" size={13} /></span>
        </button>
        {open && (
          <React.Fragment>
            <div style={{ position: "fixed", inset: 0, zIndex: 55 }} onClick={() => setOpen(false)} />
            <div className="sel-menu" style={{ zIndex: 60 }}>
              {options.map(o => (
                <div
                  key={o.id}
                  className={"so" + (o.id === value ? " sel" : "")}
                  onClick={() => { onChange(o.id); setOpen(false); }}
                >
                  <span>{o.label}</span>
                  {o.id === value
                    ? <span className="ck"><BoardIcon name="check" size={13} /></span>
                    : (o.ct !== undefined && <span className="ct">{o.ct}</span>)
                  }
                </div>
              ))}
            </div>
          </React.Fragment>
        )}
      </div>
    </div>
  );
}

function BoardCheckFlt({ on, onClick, children }) {
  return (
    <label className="flt-check" onClick={onClick}>
      <span className={"kcheck" + (on ? " on" : "")}>
        <BoardIcon name="check" size={11} />
      </span>
      {children}
    </label>
  );
}

// ─── BoardView ────────────────────────────────────────────────────────────────
function BoardView() {
  // ── hook bridge reads (read-guarded — bridge may not be loaded yet) ──
  const useBoardView        = window.__hal0UseBoardView;
  const useBoards           = window.__hal0UseBoards;
  const useBoardProfiles    = window.__hal0UseBoardProfiles;
  const useBoardAssignees   = window.__hal0UseBoardAssignees;
  const useBoardOrchestration = window.__hal0UseBoardOrchestration;
  const useBoardConfig      = window.__hal0UseBoardConfig;
  const useUpdateTask       = window.__hal0UseUpdateTask;
  const useDeleteTask       = window.__hal0UseDeleteTask;
  const useReassignTask     = window.__hal0UseReassignTask;
  const useNudgeDispatch    = window.__hal0UseNudgeDispatch;
  const useSwitchBoard      = window.__hal0UseSwitchBoard;
  const useCreateBoard      = window.__hal0UseCreateBoard;
  const useCreateTask       = window.__hal0UseCreateTask;
  const useBoardChat        = window.__hal0UseBoardChat;
  const useBoardEventsStream = window.__hal0UseBoardEventsStream;

  // ── keep WS stream alive ──
  useBoardEventsStream && useBoardEventsStream();

  // ── data queries ──
  const boardViewQ    = useBoardView ? useBoardView({}) : { data: null };
  const boardsQ       = useBoards    ? useBoards()       : { data: null };
  const profilesQ     = useBoardProfiles ? useBoardProfiles() : { data: null };
  const assigneesQ    = useBoardAssignees ? useBoardAssignees() : { data: null };
  const orchQ         = useBoardOrchestration ? useBoardOrchestration() : { data: null };
  const configQ       = useBoardConfig ? useBoardConfig() : { data: null };

  // ── mutations ──
  const updateTask  = useUpdateTask  ? useUpdateTask()  : null;
  const deleteTask  = useDeleteTask  ? useDeleteTask()  : null;
  const reassignTask = useReassignTask ? useReassignTask() : null;
  const nudge       = useNudgeDispatch ? useNudgeDispatch() : null;
  const switchBoard = useSwitchBoard ? useSwitchBoard()  : null;
  const createBoard = useCreateBoard ? useCreateBoard()  : null;
  const createTask  = useCreateTask  ? useCreateTask()   : null;
  // Chat state lives HERE (BoardView stays mounted) rather than inside
  // AgentChat, so the conversation survives closing/reopening the drawer.
  const chat        = useBoardChat   ? useBoardChat()    : null;

  // ── local state ──
  const [board, setBoard]           = useState("default");
  const [boardMenu, setBoardMenu]   = useState(false);
  const [newBoard, setNewBoard]     = useState(false);

  const [orchOpen, setOrchOpen]     = useState(false);
  const [orch, setOrch]             = useState({ mode: "auto", tickInterval: 30, failureLimit: 3, maxInflight: 4, claimTtl: 120 });

  const [search, setSearch]         = useState("");
  const [tenant, setTenant]         = useState("all");
  const [profile, setProfile]       = useState("all");
  const [showArchived, setShowArchived] = useState(false);
  const [byProfile, setByProfile]   = useState(true);

  const [sel, setSel]               = useState(() => new Set());
  const [openTask, setOpenTask]     = useState(null);
  const [chatOpen, setChatOpen]     = useState(false);
  const [newTaskLane, setNewTaskLane] = useState(null);

  // Board display is fixed: compact spacing, left-rail accent, mono titles.
  // (The runtime tweaks panel was removed — these are the permanent defaults.)
  const tw = { density: "compact", accent: "left", titlefont: "mono", meta: true };

  const [attn, setAttn]             = useState(true);

  const [dragId, setDragId]         = useState(null);
  const [dragOver, setDragOver]     = useState(null);
  const [delArmed, setDelArmed]     = useState(false);

  // ── reassign local state (for bulk reassign select) ──
  const [reassignTarget, setReassignTarget] = useState("");

  const setOrchK = (k, v) => setOrch(s => ({ ...s, [k]: v }));

  // ── Esc closes overlays ──
  useEffect(() => {
    const onKey = (e) => {
      if (e.key === "Escape") {
        setOpenTask(null);
        setChatOpen(false);
        setOrchOpen(false);
        setBoardMenu(false);
        setNewTaskLane(null);
      }
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, []);

  // ── toast helper ──
  const toast = (msg, kind = "info") => {
    if (window.__hal0Toast) window.__hal0Toast(msg, kind);
  };

  // ── data extraction ──
  const tasks = boardViewQ.data?.tasks ?? [];
  const boards = Array.isArray(boardsQ.data)
    ? boardsQ.data
    : (boardsQ.data?.boards ?? []);

  const profilesList = profilesQ.data?.profiles ?? profilesQ.data ?? [];
  const assigneesList = assigneesQ.data?.assignees ?? assigneesQ.data ?? [];

  const orchData = orchQ.data ?? orch;
  const configData = configQ.data ?? {};

  // ── derived ──
  const curBoard = boards.find(b => b.slug === board) || boards[0] || { slug: "default", name: "Default", icon: "▣", count: tasks.length, desc: "" };

  const tenantOpts = useMemo(() => {
    const tenants = [...new Set(tasks.map(t => t.tenant).filter(Boolean))];
    return [{ id: "all", label: "all tenants" }, ...tenants.map(t => ({ id: t, label: t }))];
  }, [tasks]);

  const profileOpts = useMemo(() => {
    const ps = profilesList.length > 0
      ? profilesList
      : [...new Set(tasks.map(t => t.assignee).filter(Boolean))].map(p => ({ id: p, label: p }));
    return [{ id: "all", label: "all profiles" }, ...ps.map(p => ({ id: p.id ?? p, label: p.label ?? p.id ?? p, ct: p.count }))];
  }, [profilesList, tasks]);

  const matches = (t) => {
    if (search && !(
      (t.title || "").toLowerCase().includes(search.toLowerCase()) ||
      (t.id || "").toLowerCase().includes(search.toLowerCase())
    )) return false;
    if (tenant !== "all" && t.tenant !== tenant) return false;
    if (profile !== "all" && t.assignee !== profile) return false;
    return true;
  };

  const lanes = useMemo(() =>
    BOARD_LANES.filter(l => l.id !== "archived" || showArchived),
    [showArchived]
  );

  const laneTasks = (id) => tasks.filter(t => t.status === id && matches(t));

  const visibleIds = useMemo(() =>
    tasks.filter(t => (t.status !== "archived" || showArchived) && matches(t)).map(t => t.id),
    [tasks, search, tenant, profile, showArchived]
  );

  const attnTasks = tasks.filter(t => t.status === "blocked" || t.status === "review");

  // ── selections ──
  const toggleSel = (id) => setSel(s => { const n = new Set(s); n.has(id) ? n.delete(id) : n.add(id); return n; });
  const clearSel  = () => setSel(new Set());
  const selectAllVisible = () => setSel(new Set(visibleIds));

  // ── mutations: bulk move via N updateTask PATCHes ──
  const moveTo = (ids, status) => {
    if (updateTask) {
      ids.forEach(id => updateTask.mutate({ id, body: { status } }));
    }
  };

  const delTasks = (ids) => {
    if (deleteTask) {
      ids.forEach(id => deleteTask.mutate(id));
    }
    setSel(new Set());
  };

  const doReassign = () => {
    if (!reassignTarget || !reassignTask) return;
    [...sel].forEach(id => reassignTask.mutate({ id, body: { assignee: reassignTarget } }));
    clearSel();
    setReassignTarget("");
    toast("reassigned");
  };

  const doSwitchBoard = (slug) => {
    setBoard(slug);
    setBoardMenu(false);
    clearSel();
    if (switchBoard) switchBoard.mutate(slug);
  };

  const doNudge = () => {
    if (nudge) nudge.mutate({});
    toast("dispatcher nudged");
  };

  const doRefresh = () => {
    toast("board refreshed");
  };

  // ── drag-and-drop ──
  const dnd = {
    over: dragOver,
    setOver: setDragOver,
    onDragStart: (e, task) => {
      setDragId(task.id);
      e.dataTransfer.effectAllowed = "move";
      try { e.dataTransfer.setData("text/plain", task.id); } catch (_) {}
    },
    onDragEnd: () => { setDragId(null); setDragOver(null); setDelArmed(false); },
    onDrop: (laneId) => {
      if (dragId) {
        moveTo([dragId], laneId);
        toast(dragId + " → " + laneId);
      }
      setDragId(null);
      setDragOver(null);
    },
  };

  const clearFilters = () => { setSearch(""); setTenant("all"); setProfile("all"); setShowArchived(false); };
  const anyFilter = search || tenant !== "all" || profile !== "all" || showArchived;

  // ── window component lookups ──
  const TaskDrawer     = window.TaskDrawer;
  const AgentChat      = window.AgentChat;
  const NewBoardModal  = window.NewBoardModal;
  const NewTaskModal   = window.NewTaskModal;
  const OrchPopover    = window.OrchPopover;
  const BoardLane      = window.BoardLane;

  const opened = openTask ? tasks.find(t => t.id === openTask) : null;
  const byId   = useMemo(() => Object.fromEntries(tasks.map(t => [t.id, t])), [tasks]);

  return (
    <React.Fragment>
      <div
        className="board"
        data-density={tw.density}
        data-accent={tw.accent}
        data-titlefont={tw.titlefont}
        data-meta={tw.meta ? "on" : "off"}
        data-testid="board-view"
      >
        <div className="board-view">
          <div className="board-top">

            {/* board selector */}
            <div className="board-bar">
              <span className="bb-label">Board</span>
              <div className="board-select">
                <button
                  className="bs-btn"
                  data-testid="board-selector"
                  aria-expanded={boardMenu}
                  onClick={() => setBoardMenu(o => !o)}
                >
                  <span className="ic">{curBoard.icon}</span>
                  <span className="nm">{curBoard.name} · {curBoard.count ?? tasks.length}</span>
                  <span className="cv"><BoardIcon name="chev" size={13} /></span>
                </button>
                {boardMenu && (
                  <React.Fragment>
                    <div style={{ position: "fixed", inset: 0, zIndex: 55 }} onClick={() => setBoardMenu(false)} />
                    <div className="board-menu">
                      {boards.map(b => (
                        <div
                          key={b.slug}
                          className={"bm-row" + (b.slug === board ? " sel" : "")}
                          onClick={() => doSwitchBoard(b.slug)}
                        >
                          <span className="ic">{b.icon}</span>
                          <div style={{ minWidth: 0 }}>
                            <div className="nm">{b.name}</div>
                            <div className="ds">{b.desc}</div>
                          </div>
                          {b.slug === board
                            ? <span className="ck"><BoardIcon name="check" size={14} /></span>
                            : <span className="bct">{b.count}</span>
                          }
                        </div>
                      ))}
                    </div>
                  </React.Fragment>
                )}
              </div>
              <span className="bb-count">{tasks.length} tasks</span>
              <span className="bb-spacer" />
              <span className="bb-help" title="About boards">?</span>
              <button className="btn" data-testid="board-action-new-board" onClick={() => setNewBoard(true)}>
                <BoardIcon name="plus" size={13} />New board
              </button>
            </div>

            {/* orchestration row */}
            <div className="orch-row">
              <div className="board-select" style={{ position: "relative" }}>
                <div
                  className={"orch-pill" + ((orchData.mode === "manual") ? " manual" : "")}
                  onClick={() => setOrchOpen(o => !o)}
                >
                  <span className="od" />
                  <span className="k">Orchestration</span>
                  <span className="v">{orchData.mode ?? "auto"}</span>
                </div>
                {orchOpen && (
                  <React.Fragment>
                    <div style={{ position: "fixed", inset: 0, zIndex: 65 }} onClick={() => setOrchOpen(false)} />
                    {OrchPopover
                      ? <OrchPopover orch={orchData} set={setOrchK} onClose={() => setOrchOpen(false)} />
                      : null
                    }
                  </React.Fragment>
                )}
              </div>
              <span className="orch-link" onClick={() => setOrchOpen(true)}>
                <span className="tri">▸</span>Orchestration settings
              </span>
            </div>

            {/* attention banner */}
            {attn && attnTasks.length > 0 && (
              <div className="attn" data-testid="board-attn">
                <span className="am">!!</span>
                <span className="ax">
                  <b>{attnTasks.length}</b> tasks need attention —{" "}
                  {attnTasks.filter(t => t.status === "blocked").length} blocked ·{" "}
                  {attnTasks.filter(t => t.status === "review").length} in review
                </span>
                <span className="spacer" />
                <button
                  className="ab"
                  onClick={() => { setTenant("all"); setProfile("all"); setSearch(""); toast("filtered to attention"); }}
                >Show</button>
                <span className="axc" onClick={() => setAttn(false)}>
                  <BoardIcon name="close" size={14} />
                </span>
              </div>
            )}

            {/* filter bar */}
            <div className="filterbar">
              <div className="flt flt-search">
                <label>Search</label>
                <span className="sic"><BoardIcon name="search" size={14} /></span>
                <input
                  className="input"
                  placeholder="Filter cards…"
                  value={search}
                  data-testid="board-search"
                  onChange={e => setSearch(e.target.value)}
                />
              </div>
              <BoardSelect label="Tenant" value={tenant} options={tenantOpts} onChange={setTenant} />
              <BoardSelect label="Assignee" value={profile} options={profileOpts} onChange={setProfile} />
              <BoardCheckFlt on={showArchived} onClick={() => setShowArchived(s => !s)}>
                <span data-testid="board-toggle-archived">Show archived</span>
              </BoardCheckFlt>
              <BoardCheckFlt on={byProfile} onClick={() => setByProfile(s => !s)}>
                <span data-testid="board-toggle-byprofile">Lanes by profile</span>
              </BoardCheckFlt>
              <span className="flt-spacer" />
              <div className="flt-actions">
                {/* agent chat toggle — FIRST in flt-actions */}
                <button
                  className={"tb-chat" + (chatOpen ? " on" : "")}
                  data-testid="board-action-chat"
                  onClick={() => setChatOpen(o => !o)}
                >
                  <BoardIcon name="chat" size={14} />
                  <span>agent</span>
                  <span className="agent-dot" />
                </button>
                <button
                  className="btn ghost sm"
                  data-testid="board-action-nudge"
                  onClick={doNudge}
                >
                  <BoardIcon name="dispatch" size={13} />Nudge dispatcher
                </button>
                <button
                  className="btn ghost sm"
                  data-testid="board-action-refresh"
                  onClick={doRefresh}
                >
                  <BoardIcon name="refresh" size={13} />Refresh
                </button>
                {anyFilter && (
                  <button className="flt-clear" onClick={clearFilters}>Clear filters</button>
                )}
              </div>
            </div>

            {/* bulk toolbar */}
            {sel.size > 0 && (
              <div className="bulkbar">
                <span className="bsel">{sel.size} selected</span>
                <span className="bdiv" />
                <button className="bb-act" data-testid="board-action-todo"
                  onClick={() => { moveTo([...sel], "todo"); toast(sel.size + " → todo"); clearSel(); }}>→ todo</button>
                <button className="bb-act" data-testid="board-action-ready"
                  onClick={() => { moveTo([...sel], "ready"); toast(sel.size + " → ready"); clearSel(); }}>→ ready</button>
                <button className="bb-act" data-testid="board-action-block"
                  onClick={() => { moveTo([...sel], "blocked"); clearSel(); }}>block</button>
                <button className="bb-act" data-testid="board-action-unblock"
                  onClick={() => { moveTo([...sel], "todo"); clearSel(); }}>unblock</button>
                <button className="bb-act" data-testid="board-action-complete"
                  onClick={() => { moveTo([...sel], "done"); clearSel(); }}>complete</button>
                <button className="bb-act" data-testid="board-action-archive"
                  onClick={() => { moveTo([...sel], "archived"); clearSel(); }}>archive</button>
                <button className="bb-act danger" data-testid="board-action-delete"
                  onClick={() => delTasks([...sel])}>
                  <BoardIcon name="trash" size={12} />delete
                </button>
                <span className="bdiv" />
                <select
                  className="input bmini"
                  value={reassignTarget}
                  onChange={e => setReassignTarget(e.target.value)}
                >
                  <option value="">— reassign —</option>
                  {(assigneesList.length > 0 ? assigneesList : profilesList).map(p => {
                    const pid = p.id ?? p;
                    return <option key={pid} value={pid}>@{pid}</option>;
                  })}
                </select>
                <button className="bb-act" data-testid="board-action-reassign" onClick={doReassign}>apply</button>
                <span className="bspacer" />
                <button className="bb-ghost" data-testid="board-action-select-all" onClick={selectAllVisible}>Select all visible</button>
                <button className="bb-ghost" data-testid="board-action-clear" onClick={clearSel}>Clear</button>
              </div>
            )}
          </div>

          {/* lanes — dropping a card ANYWHERE outside a column deletes it.
              While a drag is over the board background (not a lane) the
              danger veil below arms; releasing there removes the card. */}
          <div
            className="lanes-scroll"
            data-testid="board-drop-delete"
            onDragOver={(e) => {
              if (!dragId) return;
              const overLane = e.target.closest && e.target.closest(".lane");
              e.preventDefault();
              setDelArmed(!overLane);
            }}
            onDrop={(e) => {
              if (!dragId) return;
              const overLane = e.target.closest && e.target.closest(".lane");
              if (!overLane) {
                e.preventDefault();
                delTasks([dragId]);
                toast(dragId + " deleted");
              }
              setDragId(null);
              setDragOver(null);
              setDelArmed(false);
            }}
          >
            <div className="lanes">
              {lanes.map(l => (
                BoardLane
                  ? <BoardLane
                      key={l.id}
                      lane={l}
                      tasks={laneTasks(l.id)}
                      byProfile={byProfile}
                      sel={sel}
                      onToggle={toggleSel}
                      onOpen={setOpenTask}
                      openTask={openTask}
                      onAdd={(laneId) => setNewTaskLane(laneId)}
                      dnd={dnd}
                    />
                  : null
              ))}
            </div>
          </div>
        </div>

        {/* danger veil — pointer-events:none so the drop still lands on the
            lanes-scroll background underneath; purely a visual cue. */}
        {dragId && delArmed && (
          <div className="danger-veil" data-testid="board-danger-veil" aria-hidden="true">
            <div className="danger-badge">
              <BoardIcon name="trash" size={34} />
              <span>Release to delete</span>
            </div>
          </div>
        )}
      </div>

      {/* task drawer */}
      {opened && TaskDrawer && (
        <TaskDrawer
          key={opened.id}
          task={opened}
          byId={byId}
          onClose={() => setOpenTask(null)}
          onOpenTask={(id) => setOpenTask(id)}
          onToast={toast}
        />
      )}

      {/* agent chat */}
      {chatOpen && AgentChat && (
        <AgentChat
          chat={chat}
          byId={byId}
          onClose={() => setChatOpen(false)}
          onOpenTask={(id) => { setChatOpen(false); setOpenTask(id); }}
        />
      )}

      {/* new task modal — explicit creation; nothing is POSTed until submit.
          Wrapped in `.board` so the `.board .modal-*` scoped styles apply
          (BoardView mounts under `.main`, not under a `.board` ancestor). */}
      {newTaskLane && NewTaskModal && (
        <div className="board">
          <NewTaskModal
            lane={newTaskLane}
            assignees={assigneesList.length > 0 ? assigneesList : profilesList}
            onClose={() => setNewTaskLane(null)}
            onCreate={(fields) => {
              const laneId = newTaskLane;
              setNewTaskLane(null);
              if (createTask) {
                createTask.mutate(
                  { ...fields, status: laneId },
                  { onSuccess: () => toast('task "' + fields.title + '" created in ' + laneId) }
                );
              }
            }}
          />
        </div>
      )}

      {/* new board modal — wrapped in `.board` so the `.board .modal-*`
          scoped styles apply (BoardView's overlays render outside the main
          `.board` container, as siblings in this fragment). */}
      {newBoard && NewBoardModal && (
        <div className="board">
        <NewBoardModal
          onClose={() => setNewBoard(false)}
          onCreate={(b) => {
            setNewBoard(false);
            // Actually POST the new board (createBoard was previously wired
            // but never called — the modal only switched). After it lands,
            // switch to it if the operator opted in.
            const body = { name: b.name || b.slug, slug: b.slug, desc: b.desc, icon: b.icon };
            if (createBoard) {
              createBoard.mutate(body, {
                onSuccess: () => { if (b.switchTo) doSwitchBoard(b.slug); },
              });
            } else if (b.switchTo) {
              doSwitchBoard(b.slug);
            }
            toast('board "' + b.slug + '" created');
          }}
        />
        </div>
      )}
    </React.Fragment>
  );
}

// ─── window globals ───────────────────────────────────────────────────────────
Object.assign(window, { BoardView, BoardIcon });
