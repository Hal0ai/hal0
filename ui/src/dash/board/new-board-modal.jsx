// new-board-modal.jsx — window-global overlay (NO ES imports)
// Exports: window.NewBoardModal
// Presentational: collects slug/name/desc/icon/switchTo, calls onCreate({...})

(function () {
  const { useState } = React;

  // Resolve BoardIcon at RENDER time (board-view.jsx registers it AFTER this
  // module loads; window.Icons is chrome's glyph-object, not a component —
  // grabbing it crashes the modal with "Element type is invalid").
  function Icon(props) {
    const BI = window.BoardIcon;
    return BI ? <BI {...props} /> : null;
  }

  function NewBoardModal({ onClose, onCreate }) {
    const [slug, setSlug] = useState("");
    const [name, setName] = useState("");
    const [desc, setDesc] = useState("");
    const [icon, setIcon] = useState("▣");
    const [sw, setSw] = useState(true);

    const handleCreate = () => {
      if (onCreate) {
        onCreate({
          slug: slug.trim() || "untitled",
          name: name.trim(),
          desc: desc.trim(),
          icon: icon || "▣",
          switchTo: sw,
        });
      }
    };

    return (
      <div
        className="modal-scrim"
        onMouseDown={onClose}
        data-testid="board-new-modal"
      >
        <div className="modal" onMouseDown={(e) => e.stopPropagation()}>
          <div className="modal-h">
            <h3>New board</h3>
            <p>
              Boards isolate unrelated streams of work — one per project, repo, or
              domain. Each gets its own kanban.db, workspaces, and logs. Workers on
              one board never see another's tasks.
            </p>
          </div>

          <div className="modal-b">
            <div className="fld">
              <label>slug — lowercase, hyphens</label>
              <input
                className="input"
                placeholder="strix-halo-02"
                value={slug}
                onChange={(e) => setSlug(e.target.value)}
                data-testid="board-new-slug"
              />
            </div>

            <div className="fld">
              <label>display name (optional)</label>
              <input
                className="input"
                placeholder="Display name"
                value={name}
                onChange={(e) => setName(e.target.value)}
                data-testid="board-new-name"
              />
            </div>

            <div className="fld">
              <label>description (optional)</label>
              <input
                className="input"
                placeholder="What runs on this board?"
                value={desc}
                onChange={(e) => setDesc(e.target.value)}
                data-testid="board-new-desc"
              />
            </div>

            <div className="fld">
              <label>icon (single glyph)</label>
              <input
                className="input"
                style={{ width: 64 }}
                value={icon}
                onChange={(e) => setIcon(e.target.value.slice(0, 2))}
                data-testid="board-new-icon"
              />
            </div>

            <label
              className="modal-check"
              onClick={() => setSw((s) => !s)}
              data-testid="board-new-switch"
            >
              <span className={"kcheck" + (sw ? " on" : "")}>
                <Icon name="check" size={11} />
              </span>
              Switch to this board after creating it
            </label>
          </div>

          <div className="modal-f">
            <button
              className="btn ghost"
              onClick={onClose}
              data-testid="board-action-cancel-board"
            >
              Cancel
            </button>
            <button
              className="btn"
              onClick={handleCreate}
              data-testid="board-action-create-board"
            >
              Create board
            </button>
          </div>
        </div>
      </div>
    );
  }

  window.NewBoardModal = NewBoardModal;
})();
