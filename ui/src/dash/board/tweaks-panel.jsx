// tweaks-panel.jsx — window-global overlay (NO ES imports)
// Exports: window.BoardTweaksPanel
// NOTE: shipped name TweaksPanel is taken; this exports as BoardTweaksPanel

(function () {
  const { } = React; // React in scope via window

  // Resolve BoardIcon at RENDER time (board-view.jsx registers it AFTER this
  // module loads; window.Icons is chrome's glyph-object, not a component).
  function Icon(props) {
    const BI = window.BoardIcon;
    return BI ? <BI {...props} /> : null;
  }

  function BoardTweaksPanel({ tw, set, onClose }) {
    const Seg = ({ k, opts, testId }) => (
      <div className="tw-seg" data-testid={testId}>
        {opts.map(([v, label]) => (
          <button
            key={v}
            className={tw[k] === v ? "on" : ""}
            onClick={() => set(k, v)}
          >
            {label}
          </button>
        ))}
      </div>
    );

    const Toggle = ({ k, label, testId }) => (
      <div
        className={"tw-toggle" + (tw[k] ? " on" : "")}
        onClick={() => set(k, !tw[k])}
        data-testid={testId}
      >
        <span className="tw-switch" />
        <span className="tt">{label}</span>
      </div>
    );

    return (
      <div className="tweaks" role="dialog" aria-label="Tweaks" data-testid="board-tweaks">
        <div className="tweaks-h">
          <span className="t">Tweaks</span>
          <span className="x" onClick={onClose}>
            <Icon name="close" size={14} />
          </span>
        </div>
        <div className="tweaks-b">
          <div className="tw">
            <span className="tl">card density</span>
            <Seg
              k="density"
              opts={[["comfortable", "comfortable"], ["compact", "compact"]]}
              testId="board-tweak-density"
            />
          </div>
          <div className="tw">
            <span className="tl">lane accent</span>
            <Seg
              k="accent"
              opts={[["dot", "dot"], ["left", "left-rail"], ["top", "top-rail"]]}
              testId="board-tweak-accent"
            />
          </div>
          <div className="tw">
            <span className="tl">card title</span>
            <Seg
              k="titlefont"
              opts={[["prose", "prose"], ["mono", "mono"]]}
              testId="board-tweak-titlefont"
            />
          </div>
          <div className="tw">
            <span className="tl">metadata</span>
            <Toggle k="meta" label="show ids + timestamps" testId="board-tweak-meta" />
          </div>
        </div>
      </div>
    );
  }

  window.BoardTweaksPanel = BoardTweaksPanel;
})();
