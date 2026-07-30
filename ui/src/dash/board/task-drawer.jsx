// hal0 operator board — TaskDrawer (window-global JSX)
// NO ES imports — React, hooks, and all deps via window globals.
// Exports: window.TaskDrawer, window.stName, window.liveDot
const { useState, useEffect, useRef } = React;

// Resolve BoardIcon at RENDER time, not module-eval: board-view.jsx (which
// registers window.BoardIcon) imports AFTER this module, so window.BoardIcon
// is undefined when this file evaluates. window.Icons is chrome's glyph-OBJECT
// map (not a component) — never fall through to it or React throws
// "Element type is invalid". Render nothing until BoardIcon is available.
function Icon(props) {
  const BI = window.BoardIcon;
  return BI ? <BI {...props} /> : null;
}

const stName = (s) => {
  const LANE = window.LANE || {};
  return LANE[s] ? LANE[s].name : s;
};

const liveDot = (s) => "kdot" + (s === "running" ? " live" : " glow");

// ─── Task detail drawer ────────────────────────────────────────────────
function TaskDrawer({ task, byId, onClose, onOpenTask, board }) {
  const toast = (msg, kind = "info") => { if (window.__hal0Toast) window.__hal0Toast(msg, kind); };

  // #1472: every mutation below used to toast SYNCHRONOUSLY with .mutate(), so
  // a 4xx/5xx — notably the 409 If-Match conflict board.py documents — still
  // read as "posted"/"linked"/"queued" while the change silently vanished on
  // the next refetch. That defeats the one job the toast has: telling the
  // operator whether it took. Settle first, then say which way it went.
  const settle = (mutation, vars, okMsg, what) => {
    if (!mutation) return;
    mutation.mutate(vars, {
      onSuccess: () => toast(okMsg),
      onError: (err) => toast(`${what} failed — ${err?.message || "see logs"}`, "err"),
    });
  };

  // prefer live hook; fall back to prop. useBoardTask returns a TanStack
  // QueryResult — the task is on `.data`, not the result object itself.
  const liveTaskQ = window.__hal0UseBoardTask ? window.__hal0UseBoardTask(task.id) : null;
  const t = (liveTaskQ && liveTaskQ.data) || task;

  const [draft, setDraft] = useState("");
  const [parentSelect, setParentSelect] = useState("");
  const [childSelect, setChildSelect] = useState("");

  // mutations (guarded; board threads through so ?board= + the cache
  // invalidation key match the view the operator is looking at)
  const updateTask = window.__hal0UseUpdateTask ? window.__hal0UseUpdateTask(board) : null;
  const addComment = window.__hal0UseAddComment ? window.__hal0UseAddComment(board) : null;
  const addLink    = window.__hal0UseAddLink    ? window.__hal0UseAddLink(board)    : null;
  const removeLink = window.__hal0UseRemoveLink ? window.__hal0UseRemoveLink(board) : null;
  const specifyTask   = window.__hal0UseSpecifyTask   ? window.__hal0UseSpecifyTask(board)   : null;
  const decomposeTask = window.__hal0UseDecomposeTask ? window.__hal0UseDecomposeTask(board) : null;

  // worker log (pull-only)
  const logHook = window.__hal0UseBoardTaskLog ? window.__hal0UseBoardTaskLog(t.id) : null;

  const doStatusChange = (status, extra = {}) => {
    if (!updateTask) return;
    settle(updateTask, { id: t.id, body: { status, ...extra } }, `${t.id} → ${status}`, "status change");
  };

  const doBlock = () => {
    const reason = prompt("Block reason?");
    if (!reason) return;
    if (!updateTask) return;
    updateTask.mutate({ id: t.id, body: { status: "blocked", block_reason: reason } });
    toast(`${t.id} → blocked`);
  };

  const sendComment = () => {
    const body = draft.trim();
    if (!body) return;
    if (!addComment) return;
    // useAddComment's mutationFn takes a bare string body (it wraps it as
    // {body} for the wire). Passing {body} here would double-nest to
    // {body:{body}}. Send the string.
    settle(addComment, { id: t.id, body }, "comment posted", "comment");
    setDraft("");
  };

  const doAddParent = () => {
    if (!parentSelect || !addLink) return;
    settle(addLink, { parent_id: parentSelect, child_id: t.id }, "parent linked", "link");
    setParentSelect("");
  };

  const doAddChild = () => {
    if (!childSelect || !addLink) return;
    settle(addLink, { parent_id: t.id, child_id: childSelect }, "child linked", "link");
    setChildSelect("");
  };

  const doRemoveLink = (parent_id, child_id, depId) => {
    if (!removeLink) return;
    settle(removeLink, { parent_id, child_id }, `dep ${depId} removed`, "dep removal");
  };

  const doSpecify = () => {
    if (!specifyTask) return;
    settle(specifyTask, { id: t.id, body: {} }, "specify queued", "specify");
  };

  const doDecompose = () => {
    if (!decomposeTask) return;
    settle(decomposeTask, { id: t.id, body: {} }, "decompose queued", "decompose");
  };

  const status   = t.status;
  const comments = t.comments || [];
  const events   = t.events   || [];
  const runs     = t.runs     || [];
  const parents  = (t.deps && t.deps.parents) || [];
  const children = (t.deps && t.deps.children) || [];

  const isInTriage = status === "triage";

  const DepChip = ({ id, kind }) => {
    const dep = byId[id];
    const depId = `board-dep-remove-${id}`;
    return (
      <span
        className={"dep-chip " + (dep ? "st-" + dep.status : "")}
        onClick={() => dep && onOpenTask(id)}
      >
        {dep && <span className={liveDot(dep.status)} />}
        {id}
        <span
          className="dx"
          data-testid={depId}
          onClick={(e) => {
            e.stopPropagation();
            if (kind === "parent") doRemoveLink(id, t.id, id);
            else doRemoveLink(t.id, id, id);
          }}
        >
          <Icon name="close" size={11} />
        </span>
      </span>
    );
  };

  return (
    <React.Fragment>
      <div className="b-drawer-scrim" onClick={onClose} />
      <aside
        className={"b-drawer task st-" + status}
        role="dialog"
        aria-label={t.title}
        data-testid="board-task-drawer"
      >
        <div className="b-drawer-h">
          <span className="dh-id">{t.id}</span>
          <span className="spacer" />
          <span className="dh-x" onClick={onClose}><Icon name="close" /></span>
        </div>

        <div className="b-drawer-body">
          {/* title */}
          <div className="dr-title">
            <span className={liveDot(status)} />
            <span>{t.title}</span>
          </div>

          {/* meta */}
          <div className="dr-meta">
            <div className="mr"><span className="k">status</span><span className="v"><span className={liveDot(status)} />{stName(status)}</span></div>
            <div className="mr"><span className="k">assignee</span><span className="v">{t.assignee ? "@" + t.assignee : <span style={{ color: "var(--fg-5)", fontStyle: "italic" }}>unassigned</span>}</span></div>
            <div className="mr"><span className="k">tenant</span><span className="v">{t.tenant}</span></div>
            <div className="mr"><span className="k">priority</span><span className="v">{t.priority > 0 ? <span style={{ color: "var(--accent)", display: "inline-flex", gap: 5, alignItems: "center" }}><Icon name="flag" size={12} />{t.priority}</span> : "0"}</span></div>
            <div className="mr"><span className="k">workspace</span><span className="v">{t.workspace}</span></div>
            <div className="mr"><span className="k">created by</span><span className="v">{t.createdBy} · {t.created}</span></div>
          </div>

          {/* status actions */}
          <div className="dr-actions">
            <button
              className={"dr-act" + (status === "triage" ? " on" : "")}
              data-testid="board-action-triage"
              onClick={() => doStatusChange("triage")}
            >→ triage</button>
            <button
              className={"dr-act" + (status === "ready" ? " on" : "")}
              data-testid="board-action-ready"
              onClick={() => doStatusChange("ready")}
            >→ ready</button>
            <button
              className="dr-act danger"
              data-testid="board-action-block"
              onClick={doBlock}
            >block</button>
            <button
              className="dr-act"
              data-testid="board-action-unblock"
              onClick={() => doStatusChange("todo")}
            >unblock</button>
            <button
              className={"dr-act ok" + (status === "done" ? " on" : "")}
              data-testid="board-action-complete"
              onClick={() => doStatusChange("done")}
            >complete</button>
            <button
              className="dr-act"
              data-testid="board-action-archive"
              onClick={() => doStatusChange("archived")}
            >archive</button>
            {isInTriage && (
              <React.Fragment>
                <button
                  className="dr-act"
                  data-testid="board-action-specify"
                  onClick={doSpecify}
                >specify</button>
                <button
                  className="dr-act"
                  data-testid="board-action-decompose"
                  onClick={doDecompose}
                >decompose</button>
              </React.Fragment>
            )}
          </div>

          {/* description + block reason */}
          <div className="dr-sec">
            <div className="dr-sec-h"><h4>description</h4></div>
            <div className="dr-desc">{t.body ?? t.desc}</div>
            {t.blockReason && (
              <div className="dr-block">
                <div className="bl">block reason</div>
                <div className="bb">{t.blockReason}</div>
              </div>
            )}
          </div>

          {/* dependencies */}
          <div className="dr-sec">
            <div className="dr-sec-h"><h4>dependencies</h4></div>
            <div className="dep-row">
              <span className="dl">parents</span>
              {parents.length
                ? <span className="dep-chips">{parents.map(id => <DepChip key={id} id={id} kind="parent" />)}</span>
                : <span className="dep-none">none</span>}
            </div>
            <div className="dep-add">
              <select
                className="input"
                value={parentSelect}
                onChange={e => setParentSelect(e.target.value)}
                data-testid="board-dep-add-parent"
              >
                <option value="">— add parent —</option>
                {Object.keys(byId).filter(id => id !== t.id).map(id => <option key={id} value={id}>{id}</option>)}
              </select>
              <button className="btn ghost" onClick={doAddParent}><Icon name="plus" size={13} />parent</button>
            </div>
            <div className="dep-row" style={{ marginTop: 12 }}>
              <span className="dl">children</span>
              {children.length
                ? <span className="dep-chips">{children.map(id => <DepChip key={id} id={id} kind="child" />)}</span>
                : <span className="dep-none">none</span>}
            </div>
            <div className="dep-add">
              <select
                className="input"
                value={childSelect}
                onChange={e => setChildSelect(e.target.value)}
                data-testid="board-dep-add-child"
              >
                <option value="">— add child —</option>
                {Object.keys(byId).filter(id => id !== t.id).map(id => <option key={id} value={id}>{id}</option>)}
              </select>
              <button className="btn ghost" onClick={doAddChild}><Icon name="plus" size={13} />child</button>
            </div>
          </div>

          {/* comments */}
          <div className="dr-sec">
            <div className="dr-sec-h"><h4>comments</h4><span className="ct">{comments.length}</span></div>
            {comments.length === 0 && <div className="empty-line">— no comments —</div>}
            {comments.map((c, i) => (
              <div className="cmt" key={i}>
                <div className="cmt-h"><span className="au">{c.author}</span><span className="at">{c.at}</span></div>
                <div className="cmt-b">{c.body}</div>
              </div>
            ))}
          </div>

          {/* events */}
          <div className="dr-sec" data-testid="board-events">
            <div className="dr-sec-h"><h4>events</h4><span className="ct">{events.length}</span></div>
            <div className="evlog">
              {events.map((e, i) => (
                <div className="evrow" key={i}>
                  <span className="ek">{e.kind}</span>
                  <span className="et">{e.at}</span>
                  <span className="ej">{e.json}</span>
                </div>
              ))}
            </div>
          </div>

          {/* worker log */}
          <div className="dr-sec" data-testid="board-worklog">
            <div className="dr-sec-h">
              <h4>worker log</h4>
              <span
                className="right"
                style={{ cursor: "pointer" }}
                data-testid="board-action-worklog-refresh"
                onClick={() => { if (logHook && logHook.refetch) logHook.refetch(); toast("log refreshed"); }}
              >refresh</span>
            </div>
            <div className="worklog">
              {logHook && Array.isArray(logHook.data) && logHook.data.length > 0
                ? logHook.data
                    .map(e => (typeof e === "string" ? e : (e.line ?? e.msg ?? JSON.stringify(e))))
                    .join("\n")
                : runs.some(r => r.state === "active")
                  ? "worker active · no log lines yet — refresh to pull the latest"
                  : "— no worker log yet (task hasn't spawned or log was rotated away) —"}
            </div>
          </div>

          {/* run history */}
          {runs.length > 0 && (
            <div className="dr-sec" data-testid="board-runs">
              <div className="dr-sec-h"><h4>run history</h4><span className="ct">{runs.length}</span></div>
              {runs.map((r, i) => (
                <div
                  className={"runrow st-" + (
                    r.state === "active" ? "running"
                    : r.state === "completed" ? "done"
                    : r.state === "review" ? "review"
                    : "blocked"
                  )}
                  key={i}
                >
                  <div className="rh">
                    <span className="rs">{r.state}</span>
                    <span className="rp">@{r.profile}</span>
                    <span className="rd">{r.dur}</span>
                    <span className="rt">{r.at}</span>
                  </div>
                  {r.msg && <div className="rm">{r.msg}</div>}
                </div>
              ))}
            </div>
          )}
        </div>

        {/* comment composer */}
        <div className="b-dr-composer">
          <textarea
            value={draft}
            placeholder="Add a comment…  (Enter to submit)"
            data-testid="board-comment-input"
            onChange={e => setDraft(e.target.value)}
            onKeyDown={e => { if (e.key === "Enter" && !e.shiftKey) { e.preventDefault(); sendComment(); } }}
          />
          <button className="btn" data-testid="board-action-comment" onClick={sendComment}>
            <Icon name="send" size={13} />Comment
          </button>
        </div>
      </aside>
    </React.Fragment>
  );
}

Object.assign(window, { TaskDrawer, stName, liveDot });
