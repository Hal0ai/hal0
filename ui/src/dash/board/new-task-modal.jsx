// new-task-modal.jsx — window-global overlay (NO ES imports)
// Exports: window.NewTaskModal
// Presentational: collects title/body/assignee/priority for a new card and
// calls onCreate({ title, body, assignee, priority }). The target lane (status)
// is owned by the caller (board-view passes it through on submit).
//
// Why this exists: clicking a lane's "+" used to immediately POST a blank
// `{ title: "New task" }` card, which the dispatcher then auto-advanced out of
// the lane. This modal makes creation explicit — nothing is created until the
// operator fills in at least a title and hits "Create task".

(function () {
  const { useState, useRef, useEffect } = React;

  // Resolve BoardIcon at RENDER time (board-view.jsx registers it AFTER this
  // module loads — grabbing it at module scope crashes with "Element type is
  // invalid"). Mirrors new-board-modal.jsx.
  function Icon(props) {
    const BI = window.BoardIcon;
    return BI ? <BI {...props} /> : null;
  }

  // Lane labels for the header chip — keep in sync with BOARD_LANES.
  const LANE_LABELS = {
    triage: "Triage",
    todo: "To-do",
    scheduled: "Scheduled",
    ready: "Ready",
    running: "Running",
    blocked: "Blocked",
    review: "Review",
    done: "Done",
    archived: "Archived",
  };

  function NewTaskModal({ lane, assignees, onClose, onCreate }) {
    const [title, setTitle] = useState("");
    const [body, setBody] = useState("");
    const [assignee, setAssignee] = useState("");
    const [priority, setPriority] = useState("");
    const titleRef = useRef(null);

    // Autofocus the title field when the modal opens.
    useEffect(() => {
      if (titleRef.current) titleRef.current.focus();
    }, []);

    const laneLabel = LANE_LABELS[lane] || lane || "Triage";
    const canCreate = title.trim().length > 0;

    const handleCreate = () => {
      if (!canCreate || !onCreate) return;
      const payload = { title: title.trim() };
      if (body.trim()) payload.body = body.trim();
      if (assignee) payload.assignee = assignee;
      const p = parseInt(priority, 10);
      if (!Number.isNaN(p)) payload.priority = p;
      onCreate(payload);
    };

    const assigneeList = Array.isArray(assignees) ? assignees : [];

    return (
      <div
        className="modal-scrim"
        onMouseDown={onClose}
        data-testid="board-new-task-modal"
      >
        <div className="modal" onMouseDown={(e) => e.stopPropagation()}>
          <div className="modal-h">
            <h3>New task · {laneLabel}</h3>
            <p>
              Give the card a title and any context the agent needs. It lands in
              the <b>{laneLabel}</b> lane — nothing is created until you hit
              Create task.
            </p>
          </div>

          <div className="modal-b">
            <div className="fld">
              <label>title — required</label>
              <input
                ref={titleRef}
                className="input"
                placeholder="e.g. Fix the chat SSE protocol mismatch"
                value={title}
                onChange={(e) => setTitle(e.target.value)}
                onKeyDown={(e) => {
                  if (e.key === "Enter" && (e.metaKey || e.ctrlKey)) handleCreate();
                }}
                data-testid="board-new-task-title"
              />
            </div>

            <div className="fld">
              <label>description (optional)</label>
              <textarea
                className="input"
                rows={4}
                placeholder="Spec, acceptance criteria, links…"
                value={body}
                onChange={(e) => setBody(e.target.value)}
                data-testid="board-new-task-body"
              />
            </div>

            <div className="fld">
              <label>assignee (optional)</label>
              <select
                className="input"
                value={assignee}
                onChange={(e) => setAssignee(e.target.value)}
                data-testid="board-new-task-assignee"
              >
                <option value="">— unassigned —</option>
                {assigneeList.map((a) => {
                  const id = a.id ?? a;
                  return (
                    <option key={id} value={id}>
                      @{id}
                    </option>
                  );
                })}
              </select>
            </div>

            <div className="fld">
              <label>priority (optional — integer, higher = sooner)</label>
              <input
                className="input"
                style={{ width: 96 }}
                type="number"
                placeholder="0"
                value={priority}
                onChange={(e) => setPriority(e.target.value)}
                data-testid="board-new-task-priority"
              />
            </div>
          </div>

          <div className="modal-f">
            <button
              className="btn ghost"
              onClick={onClose}
              data-testid="board-action-cancel-task"
            >
              Cancel
            </button>
            <button
              className="btn"
              onClick={handleCreate}
              disabled={!canCreate}
              data-testid="board-action-create-task"
            >
              <Icon name="plus" size={13} />
              Create task
            </button>
          </div>
        </div>
      </div>
    );
  }

  window.NewTaskModal = NewTaskModal;
})();
