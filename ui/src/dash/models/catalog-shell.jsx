// The Models page's shared catalog shell: the toolbar, the sectioned list, the row, the detail
// card and the header actions that every tab (Inference, Upstream, Image, FLM) renders through.
// A tab brings its own data and says what each row and detail holds; the layout, the status
// vocabulary and the class names are here, so the tabs cannot drift apart again.
//
// The class names are the long-standing `.mdl-*` ones (dashboard.css), which the e2e specs select
// on. The pure parts (status, State filter, sections, paging) are catalog-pure.ts.
import { useEffect, useRef, useState } from 'react'
import { catalogDot, catalogStatus } from './catalog-pure'
import { LaneCode } from '../lane-code.jsx'

export const PER_PAGE_OPTS = [10, 25, 50, 'all']

// ── header ──────────────────────────────────────────────────────────────────────

/**
 * The two actions every tab's header carries: Check updates and Add model.
 * `check` is `{ label, title, pending, onClick, testid }`; `addItems` is
 * `[{ label, hint, onClick, testid }]`. One add item is a plain button; several open a menu.
 */
export function CatalogHeaderActions({ check, addItems = [], children }) {
  const [open, setOpen] = useState(false)
  const wrap = useRef(null)
  useEffect(() => {
    if (!open) return undefined
    const away = (e) => {
      if (!wrap.current?.contains(e.target)) setOpen(false)
    }
    const esc = (e) => {
      if (e.key === 'Escape') setOpen(false)
    }
    document.addEventListener('mousedown', away)
    document.addEventListener('keydown', esc)
    return () => {
      document.removeEventListener('mousedown', away)
      document.removeEventListener('keydown', esc)
    }
  }, [open])
  const single = addItems.length === 1 ? addItems[0] : null
  return (
    <>
      {children}
      {check && (
        <button
          type="button"
          className="btn ghost"
          data-testid={check.testid ?? 'mdl-check-updates'}
          disabled={check.pending}
          title={check.title}
          onClick={check.onClick}
        >
          {check.pending ? 'Checking…' : (check.label ?? 'Check updates')}
        </button>
      )}
      {single && (
        <button type="button" className="btn" data-testid="mdl-add" title={single.hint} onClick={single.onClick}>
          + {single.label}
        </button>
      )}
      {addItems.length > 1 && (
        <span className="mdl-add-wrap" ref={wrap}>
          <button
            type="button"
            className="btn"
            data-testid="mdl-add"
            aria-haspopup="menu"
            aria-expanded={open}
            onClick={() => setOpen((v) => !v)}
          >
            + Add model ▾
          </button>
          {open && (
            <div className="hal0-menu right mdl-add-menu" role="menu" data-testid="mdl-add-menu">
              {addItems.map((it) => (
                <button
                  key={it.label}
                  type="button"
                  role="menuitem"
                  className="hal0-menu-item mdl-add-item"
                  data-testid={it.testid}
                  onClick={() => {
                    setOpen(false)
                    it.onClick()
                  }}
                >
                  <span className="hal0-menu-lbl">{it.label}</span>
                  {it.hint && <span className="mdl-add-hint">{it.hint}</span>}
                </button>
              ))}
            </div>
          )}
        </span>
      )}
    </>
  )
}

// ── toolbar ─────────────────────────────────────────────────────────────────────

/**
 * Search, chip groups, sort. `groups` is `[{ id, label, chips: [{ id, label, title }], selected,
 * onToggle }]`; every group is multi-select OR. `sort` is `{ fields, field, dir, onField, onDir }`
 * or absent. Chips carry `data-testid="mdl-filter-<chip id>"`.
 */
export function CatalogToolbar({ q, onQ, placeholder = 'search name, repo, id…', groups = [], sort, onClear }) {
  const filtered = !!q.trim() || groups.some((g) => g.selected.length > 0)
  return (
    <div className="mdl-toolbar">
      <input
        className="input mono mdl-search"
        placeholder={placeholder}
        aria-label="Search models"
        value={q}
        onChange={(e) => onQ(e.target.value)}
      />
      {groups.map((g) => (
        <div key={g.id} className="mdl-toolbar-grp" role="group" aria-label={g.label}>
          <span className="lbl">{g.label}</span>
          {g.chips.map((c) => (
            <button
              key={c.id}
              type="button"
              className={'mdl-chip' + (g.selected.includes(c.id) ? ' on' : '')}
              data-testid={`mdl-filter-${c.id}`}
              aria-pressed={g.selected.includes(c.id)}
              title={c.title}
              onClick={() => g.onToggle(c.id)}
            >
              {c.label}
            </button>
          ))}
        </div>
      ))}
      {filtered && onClear && (
        <button type="button" className="mdl-chip mdl-clear" onClick={onClear}>
          clear ✕
        </button>
      )}
      {sort && (
        <div className="mdl-toolbar-grp mdl-sort-grp">
          <select
            className="input mono mdl-sort"
            data-testid="mdl-sort-field"
            aria-label="Sort by"
            value={sort.field}
            onChange={(e) => sort.onField(e.target.value)}
          >
            {sort.fields.map((f) => (
              <option key={f.id} value={f.id}>
                {f.label}
              </option>
            ))}
          </select>
          <button
            type="button"
            className="mdl-chip mdl-sort-dir"
            data-testid="mdl-sort-dir"
            title={sort.dir === 'asc' ? 'ascending' : 'descending'}
            aria-label={sort.dir === 'asc' ? 'Sorted ascending' : 'Sorted descending'}
            onClick={sort.onDir}
          >
            {sort.dir === 'asc' ? '↑' : '↓'}
          </button>
        </div>
      )}
    </div>
  )
}

/** Toggle one id in a multi-select list: the setter every chip group uses. */
export const toggleIn = (list, id) => (list.includes(id) ? list.filter((x) => x !== id) : [...list, id])

// ── list ────────────────────────────────────────────────────────────────────────

/**
 * The list card: toolbar, header line, sections of rows, pager.
 * `paged` is `pageSections(...)`'s result; `renderRow(row)` returns a `<CatalogRow>`.
 * `loading` / `error` / `empty` are strings shown in place of the rows.
 */
export function CatalogList({
  toolbar,
  title,
  headerExtra,
  right,
  paged,
  renderRow,
  loading,
  error,
  empty,
  note,
  pager,
  testid,
  sectionTestid = (key) => `mdl-section-${key}`,
}) {
  return (
    <div className="mdl-list" data-testid={testid}>
      {toolbar}
      <div className="mdl-list-h">
        <span>{title}</span>
        {headerExtra}
        <span className="ct">· {paged.total} shown</span>
        {right && <span className="right mono">{right}</span>}
      </div>
      {note}
      {loading && <div className="mdl-list-msg">{loading}</div>}
      {error && (
        <div className="mdl-list-msg err" role="alert">
          {error}
        </div>
      )}
      {!loading && !error && paged.total === 0 && <div className="mdl-list-msg empty">{empty}</div>}
      {paged.sections.map((s) => (
        <div key={s.key} data-testid={sectionTestid(s.key)}>
          <div className="mdl-section-label">{`${s.label} · ${s.total}`}</div>
          {s.rows.map(renderRow)}
        </div>
      ))}
      {pager && (
        <div className="mdl-pager">
          <div className="mdl-pager-pages">
            <button
              type="button"
              className="mdl-chip"
              disabled={paged.page <= 1}
              onClick={() => pager.onPage(paged.page - 1)}
            >
              ← Prev
            </button>
            <span className="mono" style={{ fontSize: 11, color: 'var(--fg-4)' }}>
              {`${paged.page}/${paged.totalPages}`}
            </span>
            <button
              type="button"
              className="mdl-chip"
              disabled={paged.page >= paged.totalPages}
              onClick={() => pager.onPage(paged.page + 1)}
            >
              Next →
            </button>
          </div>
          <div className="mdl-pager-size">
            <span className="lbl">per page</span>
            {PER_PAGE_OPTS.map((opt) => (
              <button
                key={String(opt)}
                type="button"
                className={'mdl-chip' + (pager.perPage === opt ? ' on' : '')}
                onClick={() => pager.onPerPage(opt)}
              >
                {opt === 'all' ? 'All' : opt}
              </button>
            ))}
          </div>
        </div>
      )}
    </div>
  )
}

const LINK_ICON = (
  <svg viewBox="0 0 16 16" width="11" height="11" fill="none" stroke="currentColor" strokeWidth="1.5" strokeLinecap="round" aria-hidden="true">
    <path d="M6.8 9.2a2.6 2.6 0 0 0 3.7 0l2-2a2.6 2.6 0 0 0-3.7-3.7l-.7.7M9.2 6.8a2.6 2.6 0 0 0-3.7 0l-2 2a2.6 2.6 0 0 0 3.7 3.7l.7-.7" />
  </svg>
)

/**
 * The status chip for a row or a detail header, from the shared vocabulary.
 * With `row`, the three everyday states are drawn without a chip box, since the row's leading dot
 * already carries them: installed keeps only its words for a screen reader, in use becomes a link
 * mark and the slot's name, and an upstream row shows the EXT lane code. The words stay in the DOM.
 */
export function StatusChip({ state, testid, row = false }) {
  const s = catalogStatus(state)
  if (row && s.kind === 'installed') {
    return <span className="mdl-status sr-only" data-testid={testid} data-status={s.kind}>{s.label}</span>
  }
  if (row && s.kind === 'inuse') {
    const [lead, ...rest] = s.label.split(' · ')
    return (
      <span className="mdl-status mdl-status-inuse" data-testid={testid} data-status={s.kind} title={s.title}>
        {LINK_ICON}
        <span className="sr-only">{lead} · </span>
        {rest.join(' · ')}
      </span>
    )
  }
  if (row && s.kind === 'remote') {
    return (
      <span className="mdl-status mdl-status-ext" data-testid={testid} data-status={s.kind} title={s.title}>
        <LaneCode lane="ext" title={s.title} />
        <span className="sr-only">{s.label}</span>
      </span>
    )
  }
  return (
    <span
      className={'chip mdl-status' + (s.tone ? ' ' + s.tone : '')}
      data-testid={testid}
      data-status={s.kind}
      title={s.title}
      {...(s.kind === 'update' ? { role: 'status', 'aria-label': 'Update available' } : {})}
    >
      {s.label}
    </span>
  )
}

/**
 * One row, the same grid on every tab: state dot, name over source, tags, size, status, menu.
 * `state` is a `CatalogState`; `tags` and `menu` are nodes; `badge` sits beside the name.
 * The row is a click target for selection and answers Enter and Space.
 */
export function CatalogRow({ state, selected, onSelect, name, sub, badge, tags, size, menu, testid, dotTestid, statusTestid, sizeTestid, children }) {
  const pct = state.download ? state.download.pct : null
  return (
    <div
      className={'mdl-row' + (selected ? ' sel' : '')}
      data-testid={testid}
      tabIndex={0}
      aria-current={selected ? 'true' : undefined}
      onClick={onSelect}
      onKeyDown={(e) => {
        if (e.target !== e.currentTarget) return
        if (e.key === 'Enter' || e.key === ' ') {
          e.preventDefault()
          onSelect()
        }
      }}
    >
      <span className="mdl-row-icon" data-testid={dotTestid}>
        <span className={'mdl-dot ' + catalogDot(state)} />
      </span>
      <span className="nm">
        {name}
        {badge}
        <span className="sub">{sub || ''}</span>
      </span>
      <span className="mdl-row-tags">{tags}</span>
      <span className="sz num" data-testid={sizeTestid}>{size || ''}</span>
      <span className="tg">
        <StatusChip state={state} testid={statusTestid} row />
      </span>
      <span className="mdl-row-kebab">{menu}</span>
      {state.download && <span className="mdl-row-bar" style={{ width: `${Math.max(0, Math.min(100, pct ?? 0))}%` }} />}
      {children}
    </div>
  )
}

// ── detail ──────────────────────────────────────────────────────────────────────

/** The detail card. Children are the header, facts, sections and actions below, in that order. */
export function DetailCard({ children, testid }) {
  return (
    <div className="mdl-detail" data-testid={testid}>
      {children}
    </div>
  )
}

export function DetailEmpty({ children = 'Select a model to see its details.' }) {
  return (
    <div className="mdl-detail">
      <div className="mdl-detail-h" style={{ padding: 24, color: 'var(--fg-4)' }}>
        {children}
      </div>
    </div>
  )
}

/** Name, where it comes from, and its status chips on the right. */
export function DetailHeader({ state, name, source, chips }) {
  return (
    <div className="mdl-detail-h">
      <div className="mdl-detail-top">
        <span className="mdl-row-icon">
          <span className={'mdl-dot ' + catalogDot(state)} />
        </span>
        <div className="nm mono">{name}</div>
        <span className="mdl-detail-chips">{chips}</span>
      </div>
      <div className="repo">{source}</div>
    </div>
  )
}

/** The facts grid: `[{ k, v, testid, wrap, wide }]`. A missing value reads as a dash; `wide`
 *  spans the row and `wrap` breaks a long path. */
export function DetailFacts({ facts }) {
  return (
    <div className="mdl-detail-meta">
      {facts.map((f) => (
        <div key={f.k} style={f.wide ? { gridColumn: '1 / -1' } : undefined}>
          <div className="k">{f.k}</div>
          <div className="v" data-testid={f.testid} style={f.wrap ? { wordBreak: 'break-all' } : undefined}>
            {f.v === null || f.v === undefined || f.v === '' ? '—' : f.v}
          </div>
        </div>
      ))}
    </div>
  )
}

/** A titled block of the detail card. */
export function DetailSection({ title, children, testid }) {
  return (
    <div className="mdl-detail-recipe" data-testid={testid}>
      <div className="lbl">{title}</div>
      {children}
    </div>
  )
}

/** One label / value line inside a `DetailSection`. */
export function DetailLine({ k, children, testid }) {
  return (
    <div className="ro-row" data-testid={testid}>
      <span className="k">{k}</span>
      <span className="v">{children}</span>
    </div>
  )
}

export function DetailNone({ children }) {
  return (
    <div className="mono mdl-detail-none">
      {children}
    </div>
  )
}

/** The action row at the foot of the card; `note` explains a disabled action. */
export function DetailActions({ children, note }) {
  return (
    <div className="mdl-detail-actions">
      {children}
      {note && <div className="mdl-detail-why">{note}</div>}
    </div>
  )
}

/**
 * "Used by": who references the model. `users` is `[{ name, detail, href }]`; a user with an
 * `href` is a link to where that reference is changed.
 */
export function DetailUsedBy({ users, none = 'Nothing is using this model.' }) {
  return (
    <DetailSection title="Used by" testid="mdl-used-by">
      {users.length === 0 ? (
        <DetailNone>{none}</DetailNone>
      ) : (
        users.map((u) => (
          <div key={`${u.name}:${u.detail ?? ''}`} className="ro-row">
            <span className="k">{u.href ? <a href={u.href}>{u.name}</a> : u.name}</span>
            <span className="v">{u.detail}</span>
          </div>
        ))
      )}
    </DetailSection>
  )
}
