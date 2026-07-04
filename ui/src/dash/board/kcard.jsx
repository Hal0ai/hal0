// hal0 operator board — KCard (task card)
//
// Window-global module. Exports:
//   window.BoardCard
//
// Dependencies (window globals, loaded before this file):
//   window.BoardIcon  — from board-view.jsx (loaded first in main.tsx)
//   window.React      — from globals-install

const { useState, useCallback } = React;

// ─── BoardCard ────────────────────────────────────────────────────────────────
function BoardCard({ task, selected, onToggle, onOpen, isOpen, onDragStart, onDragEnd }) {
  const BoardIcon = window.BoardIcon;

  // depCount is "done/total" (e.g. "2/3", "10/12") — split on the slash;
  // char-indexing broke for counts of 10+.
  const dep = task.depCount ? String(task.depCount).split("/") : null;

  return (
    <div
      className={
        "kcard st-" + task.status +
        (selected ? " sel" : "") +
        (isOpen ? " open" : "")
      }
      draggable
      data-testid={"board-task-" + task.id}
      onDragStart={(e) => onDragStart(e, task)}
      onDragEnd={onDragEnd}
      onClick={() => onOpen(task.id)}
    >
      <div className="kc-top">
        <span
          className={"kc-check" + (selected ? " on" : "")}
          onClick={(e) => { e.stopPropagation(); onToggle(task.id); }}
        >
          <span className={"kcheck" + (selected ? " on" : "")}>
            {BoardIcon && <BoardIcon name="check" size={11} />}
          </span>
        </span>
        <span className="kc-dot" />
        <span className="kc-id">{task.id}</span>
        <span className="kc-badges">
          {task.priority > 0 && (
            <span className="kc-prio" title={"priority " + task.priority}>
              {BoardIcon && <BoardIcon name="flag" size={12} />}
            </span>
          )}
          {dep && (
            <span className={"kc-dep" + (dep[0] !== dep[1] ? " warn" : "")}>
              {BoardIcon && <BoardIcon name="dep" size={11} />}
              {task.depCount}
            </span>
          )}
        </span>
      </div>
      <div className="kc-title">{task.title}</div>
      <div className="kc-foot">
        <span className={"kc-assignee" + (task.assignee ? "" : " unassigned")}>
          {task.assignee ? "@" + task.assignee : "unassigned"}
        </span>
        {task.commentCount > 0 && (
          <span className="kc-meta">
            {BoardIcon && <BoardIcon name="comment" size={11} />}
            {task.commentCount}
          </span>
        )}
        {task.schedule && (
          <span className="kc-sched">
            {BoardIcon && <BoardIcon name="clock" size={11} />}
            {task.schedule}
          </span>
        )}
        <span className="kc-when">{task.created}</span>
      </div>
    </div>
  );
}

// ─── window globals ───────────────────────────────────────────────────────────
Object.assign(window, { BoardCard });
