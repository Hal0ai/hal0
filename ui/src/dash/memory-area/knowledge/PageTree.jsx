// The knowledge pages of a bank, as the tree the engine keeps them in: folders that open and
// close, and pages that open in the reader.
//
// A tree, to the keyboard as to the eye: Tab reaches it once (one row holds the tab stop), Up
// and Down move through the rows, Right and Left open, close and step in and out of folders,
// Home and End jump to the ends, Enter or Space opens a page. Moving does not open anything: a
// page is fetched when it is opened, not when it is passed over.
//
// The open page is marked three ways: `aria-selected`, a fill, and the word "open".
//
// The card around it scrolls when the tree is long (memory-area.css), so a bank with a hundred
// pages does not push the reader off the page.
import { useMemo, useRef, useState } from 'react'
import '../memory-area.css'
import { treeKeyAction, treeRows } from '../knowledge-pure'
import { StatusChip } from '../shared/StatusChip.jsx'

function Chevron({ open }) {
  return (
    <svg
      className="ma-kn-node-icon"
      aria-hidden="true"
      width="11"
      height="11"
      viewBox="0 0 16 16"
      fill="none"
      stroke="currentColor"
      strokeWidth="1.5"
      strokeLinecap="round"
      strokeLinejoin="round"
    >
      <path d={open ? 'M4 6l4 4 4-4' : 'M6 4l4 4-4 4'} />
    </svg>
  )
}

function PageIcon() {
  return (
    <svg
      className="ma-kn-node-icon"
      aria-hidden="true"
      width="11"
      height="11"
      viewBox="0 0 16 16"
      fill="none"
      stroke="currentColor"
      strokeWidth="1.4"
      strokeLinecap="round"
      strokeLinejoin="round"
    >
      <path d="M4 1.8h5l3 3v9.4H4z" />
      <path d="M9 1.8v3h3" />
    </svg>
  )
}

/**
 * @param {{
 *   tree: object,
 *   pages: Array<{id: string, stale: boolean, failed: boolean}>,
 *   openId: string|null,
 *   onOpen: (id: string) => void,
 *   rowStale: boolean,
 *   rowFailed: boolean,
 *   labelledBy: string,
 * }} props `rowStale` and `rowFailed` say whether a row carries that chip: it does only when
 *   the rows differ, since a status every page shares is said once in the card's header.
 */
export function PageTree({ tree, pages, openId, onOpen, rowStale, rowFailed, labelledBy }) {
  const [collapsed, setCollapsed] = useState(() => new Set())
  const [focusId, setFocusId] = useState(null)
  const items = useRef(new Map())

  const rows = useMemo(() => treeRows(tree, collapsed), [tree, collapsed])
  const pageById = useMemo(() => new Map(pages.map((page) => [page.id, page])), [pages])

  // One row holds the tab stop: the one last focused, else the open page, else the first.
  const tabStop =
    rows.find((row) => row.id === focusId) ?? rows.find((row) => row.id === openId) ?? rows[0] ?? null

  const toggle = (id) =>
    setCollapsed((prev) => {
      const next = new Set(prev)
      if (next.has(id)) next.delete(id)
      else next.add(id)
      return next
    })

  const activate = (row) => {
    setFocusId(row.id)
    if (row.kind === 'folder') toggle(row.id)
    else onOpen(row.id)
  }

  const onKeyDown = (event, row) => {
    const action = treeKeyAction(rows, row.id, event.key)
    if (!action) return
    event.preventDefault()
    if (action.type === 'focus') {
      setFocusId(action.id)
      items.current.get(action.id)?.focus()
    } else if (action.type === 'toggle') toggle(action.id)
    else onOpen(action.id)
  }

  return (
    <div className="ma-kn-tree" role="tree" aria-labelledby={labelledBy} data-testid="ma-kn-tree">
      {rows.map((row) => {
        const page = row.kind === 'page' ? pageById.get(row.id) : null
        const open = row.kind === 'page' && row.id === openId
        return (
          <div
            key={row.id}
            ref={(el) => {
              if (el) items.current.set(row.id, el)
              else items.current.delete(row.id)
            }}
            className="ma-kn-node"
            role="treeitem"
            aria-level={row.level}
            aria-posinset={row.posInSet}
            aria-setsize={row.setSize}
            aria-expanded={row.kind === 'folder' ? row.expanded : undefined}
            aria-selected={row.kind === 'page' ? open : undefined}
            tabIndex={tabStop && tabStop.id === row.id ? 0 : -1}
            data-kind={row.kind}
            data-node-id={row.id}
            data-testid={`ma-kn-node-${row.id}`}
            // The indent is the row's depth; everything else about the row is in the stylesheet.
            style={{ paddingLeft: 14 + (row.level - 1) * 16 }}
            onClick={() => activate(row)}
            onFocus={() => setFocusId(row.id)}
            onKeyDown={(event) => onKeyDown(event, row)}
          >
            {row.kind === 'folder' ? <Chevron open={row.expanded} /> : <PageIcon />}
            <span className="ma-kn-node-name">{row.name}</span>
            {row.kind === 'folder' && (
              <span className="ma-kn-node-meta">
                {row.pageCount} {row.pageCount === 1 ? 'page' : 'pages'}
              </span>
            )}
            {page && rowFailed && page.failed && <StatusChip tone="err">failed</StatusChip>}
            {page && rowStale && page.stale && <StatusChip tone="warn">stale</StatusChip>}
            {open && <span className="ma-row-open">open</span>}
          </div>
        )
      })}
    </div>
  )
}
