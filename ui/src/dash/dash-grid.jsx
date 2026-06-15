// hal0 dashboard — DashGrid + DashboardOverhaulView (W3)
//
// 12-column row-aligned CSS grid with edit mode: drag-reorder, resize,
// card library, pin/unpin. Layout state from useDashLayout + reconcile.
// Rows size to their tallest card and cells stretch to fill, so card
// bottoms line up across a band (not masonry — see overhaul.css .dash-grid).
//
// Window-global module — exports DashGrid + DashboardOverhaulView onto
// window so other dash modules can reference them without ES imports.
//
// Dependencies (window globals, loaded before this file):
//   window.DCard         — from cards-shell.jsx (W1)
//   window.SlotList      — from slot-list.jsx (W2), optional until landed
//   window.MemoryMap     — from memory-map.jsx, optional
//   window.ThroughputCard2    — from W4, optional
//   window.UtilizationCard    — from W4, optional
//   window.QuickChatCard      — from W5, optional
//   window.ServicesCard       — from W5, optional
//
// TypeScript hooks used via window bridge:
//   window.__hal0UseDashLayout     — useDashLayout()
//   window.__hal0UseSaveDashLayout — useSaveDashLayout()
//   window.__hal0UseSlots          — useSlots() (for reconcile)
//   window.__hal0UseStatsHardware  — useStatsHardware()

const { React: _R } = window;
const {
  useState,
  useRef,
  useCallback,
  useMemo,
} = React;

// ─── constants ────────────────────────────────────────────────────────────────
const GRID_COLS = 12;
const GRID_GAP  = 16;

// ─── CARD_REGISTRY ────────────────────────────────────────────────────────────
// Mirrors useDashLayout.ts CARD_REGISTRY. JSX-side copy so dash-grid.jsx
// works standalone (no ES import from the TS hook file).
const CARD_REGISTRY = [
  { id: 'slots',       name: 'Slots',               icon: 'slots',  span: 12, min: 8,  locked: true,  defaultOn: true  },
  { id: 'memory',      name: 'Memory',              icon: 'gauge',  span: 8,  min: 4,  locked: false, defaultOn: true  },
  { id: 'throughput',  name: 'Throughput',          icon: 'bolt',   span: 4,  min: 3,  locked: false, defaultOn: true  },
  { id: 'quickchat',   name: 'Quick Chat',          icon: 'chat',   span: 6,  min: 4,  locked: false, defaultOn: true  },
  { id: 'services',    name: 'Services',            icon: 'route',  span: 6,  min: 4,  locked: false, defaultOn: true  },
  { id: 'utilization', name: 'Utilization',         icon: 'gauge',  span: 4,  min: 3,  locked: false, defaultOn: true  },
  { id: 'attention',   name: 'Needs Attention',     icon: 'shield', span: 4,  min: 3,  locked: false, defaultOn: true  },
  { id: 'slottrack',   name: 'Per-Slot Throughput', icon: 'flow',   span: 4,  min: 3,  locked: false, defaultOn: false },
  { id: 'approvals',   name: 'Agent Approvals',     icon: 'shield', span: 4,  min: 3,  locked: false, defaultOn: false },
  { id: 'power',       name: 'Power & Thermal',     icon: 'thermo', span: 4,  min: 3,  locked: false, defaultOn: false },
  { id: 'scheduler',   name: 'Scheduler',           icon: 'clock',  span: 4,  min: 3,  locked: false, defaultOn: false },
];
const CARD_MAP = Object.fromEntries(CARD_REGISTRY.map(c => [c.id, c]));

function buildDefaultLayout() {
  const on = CARD_REGISTRY.filter(c => c.defaultOn);
  return {
    v: 2,
    order: on.map(c => c.id),
    enabled: Object.fromEntries(CARD_REGISTRY.map(c => [c.id, c.defaultOn])),
    spans: Object.fromEntries(CARD_REGISTRY.map(c => [c.id, c.span])),
    pinned: [],
  };
}

function reconcileLayout(layout, slots) {
  const slotNames = new Set((slots ?? []).map(s => s.name));
  const pinned = (layout.pinned ?? []).filter(n => slotNames.has(n));

  const existingNonPin = (layout.order ?? []).filter(k => !k.startsWith('pin:'));
  const newOrder = [];
  let insertedPins = false;

  for (const key of existingNonPin) {
    newOrder.push(key);
    if (key === 'slots' && !insertedPins) {
      insertedPins = true;
      for (const name of pinned) {
        newOrder.push(`pin:${name}`);
      }
    }
  }

  if (!newOrder.includes('slots')) {
    newOrder.unshift('slots');
    const idx = 0;
    for (let i = pinned.length - 1; i >= 0; i--) {
      newOrder.splice(idx + 1, 0, `pin:${pinned[i]}`);
    }
  }

  const spans = {};
  for (const key of newOrder) {
    const raw = layout.spans?.[key] ?? 0;
    if (key.startsWith('pin:')) {
      spans[key] = Math.max(3, Math.min(12, raw || 3));
    } else {
      const def = CARD_MAP[key];
      spans[key] = Math.max(def?.min ?? 3, Math.min(12, raw || (def?.span ?? 4)));
    }
  }

  const enabled = { ...(layout.enabled ?? {}) };
  for (const card of CARD_REGISTRY) {
    if (card.locked) enabled[card.id] = true;
    if (!(card.id in enabled)) enabled[card.id] = card.defaultOn;
  }

  return { v: 2, order: newOrder, enabled, spans, pinned };
}

// ─── GridCell ─────────────────────────────────────────────────────────────────
// Wraps one card with: column span, edit overlay (grip/span/remove/resize).
// Height is row-aligned by the grid (no per-card measurement).

function GridCell({ layoutKey, colSpan, editing, isLocked, onRemove, onDragStart, onDragEnter, onDragEnd, onResizeStart, children }) {
  // Row-aligned layout: the cell only declares its column span; height comes
  // from the implicit grid row (sized to the tallest card in the band) via
  // `align-items: stretch`. No JS height measurement needed.
  const style = {
    gridColumn: `span ${colSpan}`,
  };

  const handleDragStart = (e) => {
    e.dataTransfer.effectAllowed = 'move';
    e.dataTransfer.setData('text/plain', layoutKey);
    onDragStart(layoutKey);
  };

  const handleDragEnter = (e) => {
    e.preventDefault();
    onDragEnter(layoutKey);
  };

  const handleDragOver = (e) => {
    e.preventDefault();
    e.dataTransfer.dropEffect = 'move';
  };

  return (
    <div
      className={'dash-cell' + (editing ? ' editing' : '')}
      style={style}
      draggable={editing && !isLocked}
      onDragStart={editing ? handleDragStart : undefined}
      onDragEnter={editing ? handleDragEnter : undefined}
      onDragOver={editing ? handleDragOver : undefined}
      onDragEnd={editing ? onDragEnd : undefined}
    >
      {children}
      {editing && (
        <div className="cell-edit-overlay">
          {!isLocked && (
            <span
              className="cell-grip"
              title="Drag to reorder"
              draggable
              onDragStart={handleDragStart}
            >
              <svg width="14" height="14" viewBox="0 0 14 14" fill="none">
                <circle cx="5" cy="4" r="1.2" fill="currentColor"/>
                <circle cx="9" cy="4" r="1.2" fill="currentColor"/>
                <circle cx="5" cy="7" r="1.2" fill="currentColor"/>
                <circle cx="9" cy="7" r="1.2" fill="currentColor"/>
                <circle cx="5" cy="10" r="1.2" fill="currentColor"/>
                <circle cx="9" cy="10" r="1.2" fill="currentColor"/>
              </svg>
            </span>
          )}
          <span className="cell-span-badge">{colSpan}/12</span>
          {!isLocked && (
            <button
              className="cell-remove"
              onClick={onRemove}
              title="Remove from home"
              aria-label="Remove card"
            >×</button>
          )}
          {!isLocked && (
            <div
              className="cell-resize-handle"
              title="Drag to resize"
              onPointerDown={onResizeStart}
            />
          )}
        </div>
      )}
    </div>
  );
}

// ─── CardLibrary ──────────────────────────────────────────────────────────────
// Edit-mode panel: grid of all cards, click to toggle on/off; Reset + Done.

function CardLibrary({ enabled, onToggle, onReset, onDone }) {
  return (
    <div className="card-library">
      <div className="card-library-h">
        <span className="card-library-title">Card Library</span>
        <span className="card-library-spacer" />
        <button className="lib-btn lib-btn-reset" onClick={onReset}>Reset</button>
        <button className="lib-btn lib-btn-done" onClick={onDone}>Done</button>
      </div>
      <div className="card-library-grid">
        {CARD_REGISTRY.map(card => {
          const on = enabled[card.id] !== false;
          return (
            <button
              key={card.id}
              className={'lib-card' + (on ? ' lib-card-on' : '') + (card.locked ? ' lib-card-locked' : '')}
              onClick={() => !card.locked && onToggle(card.id)}
              disabled={card.locked}
              title={card.locked ? 'Always on (locked)' : (on ? 'Click to remove' : 'Click to add')}
            >
              <span className="lib-card-name">{card.name}</span>
              <span className="lib-card-badge">{on ? 'on' : 'off'}</span>
            </button>
          );
        })}
      </div>
    </div>
  );
}

// ─── Card renderers ───────────────────────────────────────────────────────────
// Maps registry id → rendered card body. Unknown = placeholder.
// All window.X checks are gated so grid renders before siblings land.

function Placeholder({ id }) {
  const DCard = window.DCard;
  if (!DCard) return <div className="dash-placeholder">source pending ({id})</div>;
  return (
    <DCard title={id} note="source pending">
      <div className="dash-placeholder-body">widget loading</div>
    </DCard>
  );
}

function PinnedSlotPlaceholder({ slotName }) {
  const DCard = window.DCard;
  if (!DCard) return <div className="dash-placeholder">pin:{slotName}</div>;
  return (
    <DCard title={slotName} note="pinned slot · source pending">
      <div className="dash-placeholder-body">widget loading</div>
    </DCard>
  );
}

function renderCard(key, { pinnedSet, onTogglePin }) {
  // Pin cards
  if (key.startsWith('pin:')) {
    const slotName = key.slice(4);
    return <PinnedSlotPlaceholder key={key} slotName={slotName} />;
  }

  // Named registry cards — use window globals when present
  switch (key) {
    case 'slots': {
      const SlotList = window.SlotList;
      if (typeof SlotList === 'function') {
        return <SlotList key="slots" pinnedSet={pinnedSet} onTogglePin={onTogglePin} />;
      }
      return <Placeholder key="slots" id="slots" />;
    }
    case 'memory': {
      const MemoryMap = window.MemoryMap;
      if (typeof MemoryMap === 'function') {
        return <MemoryMap key="memory" />;
      }
      return <Placeholder key="memory" id="memory" />;
    }
    case 'throughput': {
      const ThroughputCard2 = window.ThroughputCard2;
      if (typeof ThroughputCard2 === 'function') {
        return <ThroughputCard2 key="throughput" />;
      }
      return <Placeholder key="throughput" id="throughput" />;
    }
    case 'quickchat': {
      const QuickChatCard = window.QuickChatCard;
      if (typeof QuickChatCard === 'function') {
        return <QuickChatCard key="quickchat" />;
      }
      return <Placeholder key="quickchat" id="quickchat" />;
    }
    case 'services': {
      const ServicesCard = window.ServicesCard;
      if (typeof ServicesCard === 'function') {
        return <ServicesCard key="services" />;
      }
      return <Placeholder key="services" id="services" />;
    }
    case 'utilization': {
      const UtilizationCard = window.UtilizationCard;
      if (typeof UtilizationCard === 'function') {
        return <UtilizationCard key="utilization" />;
      }
      return <Placeholder key="utilization" id="utilization" />;
    }
    case 'power': {
      const C = window.PowerCard;
      return typeof C === 'function' ? <C key="power" /> : <Placeholder key="power" id="power" />;
    }
    case 'slottrack': {
      const C = window.SlotTrackCard;
      return typeof C === 'function' ? <C key="slottrack" /> : <Placeholder key="slottrack" id="slottrack" />;
    }
    case 'approvals': {
      const C = window.ApprovalsCard;
      return typeof C === 'function' ? <C key="approvals" /> : <Placeholder key="approvals" id="approvals" />;
    }
    case 'attention': {
      const C = window.AttentionCard;
      return typeof C === 'function' ? <C key="attention" /> : <Placeholder key="attention" id="attention" />;
    }
    case 'scheduler': {
      const C = window.SchedulerCard;
      return typeof C === 'function' ? <C key="scheduler" /> : <Placeholder key="scheduler" id="scheduler" />;
    }
    default:
      return <Placeholder key={key} id={key} />;
  }
}

// ─── DashGrid ─────────────────────────────────────────────────────────────────
// Props:
//   editing          — boolean (controlled from outside; topbar toggle)
//   onToggleEdit     — () => void
//   layout           — DashLayout (from useDashLayout; already reconciled)
//   slots            — Slot[] from useSlots
//   onLayoutChange   — (newLayout) => void — called on every structural change
//
// Also exposes a local Customize/Done button for standalone testing.

function DashGrid({ editing: editingProp, onToggleEdit, layout, slots, onLayoutChange }) {
  // Local editing state when not controlled from outside
  const [localEditing, setLocalEditing] = useState(false);
  const controlled = editingProp !== undefined;
  const editing = controlled ? editingProp : localEditing;
  const toggleEdit = onToggleEdit ?? (() => setLocalEditing(e => !e));

  // Drag state
  const dragKeyRef = useRef(null);

  // Resize state
  const resizeRef = useRef({ key: null, startX: 0, startSpan: 0, colWidth: 0 });

  // Visible items: order filtered to enabled cards + existing pin: keys
  const pinnedSet = useMemo(() => new Set(layout.pinned ?? []), [layout.pinned]);

  const visibleItems = useMemo(() => {
    return (layout.order ?? []).filter(key => {
      if (key.startsWith('pin:')) return true; // all pin: keys in order are visible
      return layout.enabled?.[key] !== false;
    });
  }, [layout.order, layout.enabled]);

  const emitChange = useCallback((next) => {
    onLayoutChange?.(next);
  }, [onLayoutChange]);

  // ── drag-reorder ──────────────────────────────────────────────────
  const handleDragStart = useCallback((key) => {
    dragKeyRef.current = key;
  }, []);

  const handleDragEnter = useCallback((targetKey) => {
    const dragKey = dragKeyRef.current;
    if (!dragKey || dragKey === targetKey) return;

    const order = [...(layout.order ?? [])];
    const fromIdx = order.indexOf(dragKey);
    const toIdx = order.indexOf(targetKey);
    if (fromIdx === -1 || toIdx === -1) return;

    order.splice(fromIdx, 1);
    order.splice(toIdx, 0, dragKey);
    emitChange({ ...layout, order });
  }, [layout, emitChange]);

  const handleDragEnd = useCallback(() => {
    dragKeyRef.current = null;
  }, []);

  // ── resize ────────────────────────────────────────────────────────
  const handleResizeStart = useCallback((key, e) => {
    e.preventDefault();
    e.stopPropagation();

    // Measure column width from the grid container
    const grid = document.querySelector('.dash-grid');
    if (!grid) return;
    const gridWidth = grid.offsetWidth;
    const colWidth = (gridWidth - (GRID_COLS - 1) * GRID_GAP) / GRID_COLS;

    resizeRef.current = {
      key,
      startX: e.clientX,
      startSpan: layout.spans?.[key] ?? CARD_MAP[key]?.span ?? 4,
      colWidth,
    };

    const onMove = (mv) => {
      const { key: rKey, startX, startSpan, colWidth: cw } = resizeRef.current;
      if (!rKey || cw === 0) return;
      const delta = mv.clientX - startX;
      const colDelta = Math.round(delta / (cw + GRID_GAP));
      const rawSpan = startSpan + colDelta;
      const def = CARD_MAP[rKey];
      const minSpan = rKey.startsWith('pin:') ? 3 : (def?.min ?? 3);
      const newSpan = Math.max(minSpan, Math.min(12, rawSpan));

      emitChange({
        ...layout,
        spans: { ...layout.spans, [rKey]: newSpan },
      });
    };

    const onUp = () => {
      resizeRef.current = { key: null, startX: 0, startSpan: 0, colWidth: 0 };
      window.removeEventListener('pointermove', onMove);
      window.removeEventListener('pointerup', onUp);
    };

    window.addEventListener('pointermove', onMove);
    window.addEventListener('pointerup', onUp);
  }, [layout, emitChange]);

  // ── toggle card ───────────────────────────────────────────────────
  const handleToggleCard = useCallback((id) => {
    const card = CARD_MAP[id];
    if (card?.locked) return;
    const wasOn = layout.enabled?.[id] !== false;
    const nowOn = !wasOn;
    let order = [...(layout.order ?? [])];
    if (nowOn && !order.includes(id)) {
      // Re-add: insert at end
      order.push(id);
    } else if (!nowOn) {
      order = order.filter(k => k !== id);
    }
    emitChange({
      ...layout,
      order,
      enabled: { ...layout.enabled, [id]: nowOn },
    });
  }, [layout, emitChange]);

  // ── remove card ───────────────────────────────────────────────────
  const handleRemove = useCallback((key) => {
    if (CARD_MAP[key]?.locked) return;
    let order = (layout.order ?? []).filter(k => k !== key);
    const enabled = { ...layout.enabled };
    if (!key.startsWith('pin:')) {
      enabled[key] = false;
    }
    emitChange({ ...layout, order, enabled });
  }, [layout, emitChange]);

  // ── reset layout ──────────────────────────────────────────────────
  const handleReset = useCallback(() => {
    const def = buildDefaultLayout();
    emitChange(def);
  }, [emitChange]);

  // ── pin/unpin ─────────────────────────────────────────────────────
  const handleTogglePin = useCallback((slotName) => {
    const isPinned = (layout.pinned ?? []).includes(slotName);
    let pinned, order;

    if (isPinned) {
      // Unpin: remove from pinned + remove pin:X from order
      pinned = (layout.pinned ?? []).filter(n => n !== slotName);
      order = (layout.order ?? []).filter(k => k !== `pin:${slotName}`);
    } else {
      // Pin: add to pinned + insert pin:X right after slots (after existing pins)
      pinned = [...(layout.pinned ?? []), slotName];
      order = [...(layout.order ?? [])];
      const slotsIdx = order.indexOf('slots');
      // Find last existing pin: key after slots
      let insertAt = slotsIdx + 1;
      while (insertAt < order.length && order[insertAt].startsWith('pin:')) {
        insertAt++;
      }
      order.splice(insertAt, 0, `pin:${slotName}`);
    }

    const spans = { ...layout.spans };
    if (!isPinned && !(layout.spans?.[`pin:${slotName}`])) {
      spans[`pin:${slotName}`] = 3;
    }

    emitChange({ ...layout, pinned, order, spans });
  }, [layout, emitChange]);

  const pinnedCount = layout.pinned?.length ?? 0;
  const cardCount = visibleItems.filter(k => !k.startsWith('pin:')).length;

  return (
    <div className="dash-grid-root">
      {/* Local Customize/Done button for standalone testing */}
      {!controlled && (
        <div className="dash-grid-local-controls">
          <button
            className={'dash-customize-btn' + (editing ? ' active' : '')}
            onClick={toggleEdit}
          >
            {editing ? 'Done' : 'Customize'}
          </button>
        </div>
      )}

      {/* Card library — edit mode only */}
      {editing && (
        <CardLibrary
          enabled={layout.enabled ?? {}}
          onToggle={handleToggleCard}
          onReset={handleReset}
          onDone={() => toggleEdit()}
        />
      )}

      {/* Row-aligned grid */}
      <div className={'dash-grid' + (editing ? ' dash-grid-editing' : '')}>
        {visibleItems.map(key => {
          const span = layout.spans?.[key] ?? (
            key.startsWith('pin:') ? 3 : (CARD_MAP[key]?.span ?? 4)
          );
          const isLocked = !key.startsWith('pin:') && !!CARD_MAP[key]?.locked;

          return (
            <GridCell
              key={key}
              layoutKey={key}
              colSpan={span}
              editing={editing}
              isLocked={isLocked}
              onRemove={() => handleRemove(key)}
              onDragStart={handleDragStart}
              onDragEnter={handleDragEnter}
              onDragEnd={handleDragEnd}
              onResizeStart={(e) => handleResizeStart(key, e)}
            >
              {renderCard(key, { pinnedSet, onTogglePin: handleTogglePin })}
            </GridCell>
          );
        })}
      </div>

      {/* Grid footer — edit mode only */}
      {editing && (
        <div className="dash-grid-footer">
          <span className="mono">
            {cardCount} cards on home
            {' · '}
            {pinnedCount} pinned slots
            {' · '}
            layout saved
          </span>
        </div>
      )}
    </div>
  );
}

// ─── DashboardOverhaulView ────────────────────────────────────────────────────
// Page shell: hero strip → card library (edit) → grid → grid footer (edit).
// Does NOT wire into routing — lead will wire after review.
// Props:
//   editing     — optional bool (controlled from topbar; falls back to local state)
//   onToggleEdit — optional () => void

function DashboardOverhaulView({ editing: editingProp, onToggleEdit }) {
  // Hook bridges — all optional/gated
  const useDashLayout   = window.__hal0UseDashLayout   || null;
  const useSaveLayout   = window.__hal0UseSaveDashLayout || null;
  const useSlotsHook    = window.__hal0UseSlots        || null;
  const useHardwareHook = window.__hal0UseStatsHardware || null;

  const layoutQuery  = useDashLayout ? useDashLayout()  : { data: null };
  const saveLayout   = useSaveLayout ? useSaveLayout()  : null;
  const slotsQuery   = useSlotsHook  ? useSlotsHook()   : { data: [] };
  const hwQuery      = useHardwareHook ? useHardwareHook() : { data: null };

  const slots = slotsQuery.data ?? [];
  const hw    = hwQuery.data ?? null;

  // Live host identity for the hero greeting (handoff: "Welcome back, halo.
  // system steady on <host>"). Prefer the live stats node, fall back to the
  // HAL0_DATA seed host name so the hero never shows a bare "on ".
  const hostName =
    hw?.host?.node ||
    (typeof window !== 'undefined' && window.HAL0_DATA?.host?.name) ||
    null;

  // Reconcile raw layout with live slots
  const rawLayout = layoutQuery.data;
  const layout = useMemo(() => {
    const base = rawLayout && rawLayout.v === 2 ? rawLayout : buildDefaultLayout();
    return reconcileLayout(base, slots);
  }, [rawLayout, slots]);

  // Local editing state (can be controlled by parent)
  const [localEditing, setLocalEditing] = useState(false);
  const controlled = editingProp !== undefined;
  const editing = controlled ? editingProp : localEditing;
  const toggleEdit = onToggleEdit ?? (() => setLocalEditing(e => !e));

  // On layout change: update local state + persist via PUT
  const handleLayoutChange = useCallback((next) => {
    // Persist; backend may 404 — useSaveDashLayout swallows the error
    saveLayout?.mutate(next);
  }, [saveLayout]);

  // Hero strip meta
  const serving = slots.filter(s => s.state === 'serving');
  const totalTps = serving.reduce((acc, s) => acc + (s.metrics?.toks ?? 0), 0);
  const ramFreeMb = hw ? ((hw.ram_total_mb ?? 0) - (hw.ram_used_mb ?? 0)) : null;
  const ramFreeGb = ramFreeMb != null ? (ramFreeMb / 1024).toFixed(1) : null;

  const heroMeta = [
    serving.length > 0 ? `${serving.length} serving` : null,
    totalTps > 0 ? `${totalTps.toFixed(0)} tok/s` : null,
    ramFreeGb != null ? `${ramFreeGb} GB free` : null,
  ].filter(Boolean).join(' · ') || '—';

  return (
    // `view` is the shared route-view wrapper (scroll container + max-width +
    // padding from the app shell); every route view uses it. Keep it so the
    // overhaul board inherits the same chrome as the old DashboardView.
    <div className="view dash-overhaul-view">
      {/* Hero strip — handoff copy: "Welcome back, halo. system steady on
          <host>". `hero-strip` class kept alongside `dash-hero` so shell +
          existing hero specs still target it. */}
      <div className="dash-hero hero-strip">
        <span className="dash-hero-greeting">
          Welcome back, halo. system steady{hostName ? ` on ` : ''}
          {hostName ? <span className="mono">{hostName}</span> : ''}
        </span>
        <span className="dash-hero-spacer" />
        <span className="dash-hero-meta mono">{heroMeta}</span>
        <button
          className={'dash-customize-btn' + (editing ? ' active' : '')}
          onClick={toggleEdit}
        >
          {editing ? 'Done' : 'Customize'}
        </button>
      </div>

      {/* Grid — passes editing + change handler down */}
      <DashGrid
        editing={editing}
        onToggleEdit={toggleEdit}
        layout={layout}
        slots={slots}
        onLayoutChange={handleLayoutChange}
      />
    </div>
  );
}

// ─── window globals ───────────────────────────────────────────────────────────
Object.assign(window, { DashGrid, DashboardOverhaulView });
