// hal0 operator board — BoardLane
//
// Window-global module. Exports:
//   window.BoardLane
//
// Dependencies (window globals, loaded before this file):
//   window.BoardIcon  — from board-view.jsx
//   window.BoardCard  — from kcard.jsx
//   window.React      — from globals-install

const { useMemo } = React;

// ─── BoardLane ────────────────────────────────────────────────────────────────
function BoardLane({ lane, tasks, byProfile, sel, onToggle, onOpen, openTask, onAdd, dnd }) {
  const BoardIcon = window.BoardIcon;
  const BoardCard  = window.BoardCard;

  const groups = useMemo(() => {
    if (!byProfile) return null;
    const m = new Map();
    tasks.forEach(t => {
      const k = t.profile || "unassigned";
      if (!m.has(k)) m.set(k, []);
      m.get(k).push(t);
    });
    return [...m.entries()];
  }, [tasks, byProfile]);

  return (
    <div
      className={"lane st-" + lane.id + (dnd.over === lane.id ? " dragover" : "")}
      data-testid={"board-lane-" + lane.id}
      onDragOver={(e) => { e.preventDefault(); dnd.setOver(lane.id); }}
      onDragLeave={() => dnd.setOver(o => (o === lane.id ? null : o))}
      onDrop={(e) => { e.preventDefault(); dnd.onDrop(lane.id); }}
    >
      <div className="lane-h">
        <span className="kdot glow" />
        <span className="lname">{lane.name}</span>
        <span className="lct">{tasks.length}</span>
        <span className="lspacer" />
        <button className="ladd" onClick={() => onAdd(lane.id)}>
          {BoardIcon && <BoardIcon name="plus" size={13} />}
        </button>
      </div>
      <div className="lane-desc">{lane.desc}</div>
      <div className="lane-rail" />
      <div className="lane-body">
        {tasks.length === 0 && (
          <div className={"lane-empty" + (dnd.over === lane.id ? " drop" : "")}>
            {dnd.over === lane.id ? "drop here" : "— no tasks —"}
          </div>
        )}
        {!byProfile && tasks.map(t => (
          BoardCard
            ? <BoardCard
                key={t.id}
                task={t}
                selected={sel.has(t.id)}
                onToggle={onToggle}
                onOpen={onOpen}
                isOpen={openTask === t.id}
                onDragStart={dnd.onDragStart}
                onDragEnd={dnd.onDragEnd}
              />
            : null
        ))}
        {byProfile && groups && groups.map(([p, ts]) => (
          <React.Fragment key={p}>
            <div className="sublane-h">{p}<span className="pc">{ts.length}</span></div>
            {ts.map(t => (
              BoardCard
                ? <BoardCard
                    key={t.id}
                    task={t}
                    selected={sel.has(t.id)}
                    onToggle={onToggle}
                    onOpen={onOpen}
                    isOpen={openTask === t.id}
                    onDragStart={dnd.onDragStart}
                    onDragEnd={dnd.onDragEnd}
                  />
                : null
            ))}
          </React.Fragment>
        ))}
      </div>
    </div>
  );
}

// ─── window globals ───────────────────────────────────────────────────────────
Object.assign(window, { BoardLane });
