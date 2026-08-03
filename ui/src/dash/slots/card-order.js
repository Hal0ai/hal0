// hal0 dashboard — operator-arranged slot-card order (drag to reorder).
//
// The Inference pane's slot cards can be rearranged by grabbing the grip at the
// top of a card and dropping it where you want it. This is a per-browser VIEW
// preference: it never touches slot config, so it needs no Save step and no
// backend round-trip — the new order is written to localStorage as it happens
// and applied on the next render.
//
// Ordering is stored as a list of slot NAMES, not indices, so adding, renaming,
// or deleting a slot can't scramble the arrangement:
//   - a name in the saved list keeps its saved position
//   - a name that isn't (a slot created since) falls to the end, in its natural
//     /api/slots order
//   - a saved name whose slot is gone is simply ignored
//
// The pure helpers below carry the logic (unit-tested in card-order.test.ts);
// useCardReorder is the hook the pane consumes. React is read off the global at
// call time (the dash modules' house pattern) so this file stays importable in
// the Node-environment unit suite.

const LS_PREFIX = 'hal0.slots.order.'

// Default key extractor — the pane's rows are `{ s, ind }` pairs.
const defaultKey = (r) => r?.s?.name

// ─── pure helpers ───────────────────────────────────────────────────────────

// Reorder `rows` to match `order` (a list of names). Rows named in `order` come
// first, in that order; everything else keeps its natural relative order and
// follows. Never mutates the input.
export function applyOrder(rows, order, keyOf = defaultKey) {
  const list = Array.isArray(rows) ? rows.slice() : []
  if (!Array.isArray(order) || order.length === 0) return list
  const rank = new Map()
  order.forEach((n, i) => { if (!rank.has(n)) rank.set(n, i) })
  const known = []
  const rest = []
  for (const r of list) (rank.has(keyOf(r)) ? known : rest).push(r)
  known.sort((a, b) => rank.get(keyOf(a)) - rank.get(keyOf(b)))
  return known.concat(rest)
}

// Move `name` to sit at `toIndex` in `names`. The index is read against the
// list BEFORE the removal, which is what makes a drag read naturally: dropping
// onto a card to your right lands you after it, onto one to your left, before
// it. Out-of-range indices clamp; an unknown name is a no-op copy.
export function moveName(names, name, toIndex) {
  const next = Array.isArray(names) ? names.slice() : []
  const from = next.indexOf(name)
  if (from < 0) return next
  next.splice(from, 1)
  const to = Math.max(0, Math.min(next.length, toIndex))
  next.splice(to, 0, name)
  return next
}

// ─── localStorage ───────────────────────────────────────────────────────────
// Fail-soft both ways: a corrupt/absent entry reads as "no saved order" (the
// natural /api/slots order), and a failed write (private mode, quota) leaves
// the in-memory order working for the session rather than breaking the drag.

export function readOrder(scope) {
  try {
    const raw = localStorage.getItem(LS_PREFIX + scope)
    if (!raw) return []
    const parsed = JSON.parse(raw)
    if (!Array.isArray(parsed)) return []
    return parsed.filter((n) => typeof n === 'string')
  } catch {
    return []
  }
}

export function writeOrder(scope, order) {
  try {
    localStorage.setItem(LS_PREFIX + scope, JSON.stringify(order))
  } catch {
    /* ignore — the session keeps the order in memory */
  }
}

// ─── useCardReorder ─────────────────────────────────────────────────────────
//
// Owns the saved order, the in-flight drag, and the DOM props the card + grip
// need. `scope` is the localStorage sub-key (one per card list, so the chat
// tier and the utility tier arrange independently).
//
// Returns:
//   rows      — `rows` in the operator's order
//   dragName  — the name currently being dragged (null when idle)
//   gripProps(name)  — spread onto the grip button
//   dropProps(name)  — spread onto the card element
//
// Reordering happens live on dragenter (not on drop), so the grid shows the
// result while you're still holding the card; the drop just ends the gesture.
// dragenter rather than dragover keeps that to one reorder per card crossed.
//
// Native HTML5 drag-and-drop has no touch support; the grip's arrow-key
// handling is the accessible (and touch-device) path.
export function useCardReorder(scope, rows, keyOf = defaultKey) {
  const [order, setOrder] = React.useState(() => readOrder(scope))
  // The dragged name lives in a REF as well as state. State drives the
  // `.dragging` styling; the ref is what the drop handlers read, because
  // dragstart and the first dragenter can land in the same task — a handler
  // closed over the pre-dragstart render would still see `dragName === null`
  // and swallow the reorder.
  const dragRef = React.useRef(null)
  const [dragName, setDragName] = React.useState(null)
  const beginDrag = (name) => {
    dragRef.current = name
    setDragName(name)
  }
  const endDrag = () => {
    dragRef.current = null
    setDragName(null)
  }

  // Mirrored in refs for the same reason as `dragRef`: a burst of drag events
  // can outrun React's re-render, and a reorder computed against a stale name
  // list would undo the one before it.
  const orderRef = React.useRef(order)
  const rowsRef = React.useRef(rows)
  orderRef.current = order
  rowsRef.current = rows

  const ordered = applyOrder(rows, order, keyOf)
  const currentNames = () => applyOrder(rowsRef.current, orderRef.current, keyOf).map(keyOf)

  const moveTo = (name, toIndex) => {
    const names = currentNames()
    const next = moveName(names, name, toIndex)
    if (next.length === names.length && next.every((n, i) => n === names[i])) return
    orderRef.current = next
    setOrder(next)
    writeOrder(scope, next)
  }

  const gripProps = (name) => ({
    draggable: true,
    onDragStart: (e) => {
      beginDrag(name)
      e.dataTransfer.effectAllowed = 'move'
      try {
        e.dataTransfer.setData('text/plain', name)
      } catch {
        /* Safari can refuse setData on a non-text drag — the name lives in state anyway */
      }
      // Drag the whole card, not the little grip button.
      const card = e.currentTarget.closest('.scard, .mcard')
      if (card && e.dataTransfer.setDragImage) {
        e.dataTransfer.setDragImage(card, card.offsetWidth / 2, 14)
      }
    },
    onDragEnd: endDrag,
    // The grip sits on a card whose header/body carry their own click targets.
    onClick: (e) => e.stopPropagation(),
    onKeyDown: (e) => {
      const step =
        e.key === 'ArrowLeft' || e.key === 'ArrowUp' ? -1
        : e.key === 'ArrowRight' || e.key === 'ArrowDown' ? 1
        : 0
      if (!step) return
      e.preventDefault()
      const i = currentNames().indexOf(name)
      if (i < 0) return
      // Keyed reorder moves the existing DOM node, so focus rides along with
      // the grip and the arrow keys keep working from the new position.
      moveTo(name, i + step)
    },
  })

  const dropProps = (name) => ({
    onDragOver: (e) => {
      if (!dragRef.current) return
      e.preventDefault()
      e.dataTransfer.dropEffect = 'move'
    },
    onDragEnter: (e) => {
      const dragged = dragRef.current
      if (!dragged || dragged === name) return
      e.preventDefault()
      moveTo(dragged, currentNames().indexOf(name))
    },
    onDrop: (e) => {
      if (!dragRef.current) return
      e.preventDefault()
      endDrag()
    },
  })

  return { rows: ordered, dragName, gripProps, dropProps }
}
