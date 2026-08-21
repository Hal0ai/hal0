// hal0 memory v2 (Bank workspace UI, task C4) — the unified Bank workspace:
// BankBar on top, filter card / sources / fact list / (later) inspector +
// ego graph below.
//
// Ported from the design handoff's prototype/app-b.jsx (BankWorkspace,
// SourcesPanel, TopicChips) + prototype/explore.jsx (DensityStrip,
// FactList, AtlasPanel, Inspector — EgoGraph/LocalGraph land in the last
// commit of this same task). Server-side filtering replaces the
// prototype's client-side `facts.filter(...)`: every filter (search, type,
// tag, when-brush, source focus, sort, paging) is driven through
// `useBankUnits`'s params instead of filtering a fully-loaded mock array.
//
// Window-globals contract: no ES imports across dash/*.jsx — reads
// window.MemV2 (C1), window.MemV2BankBar (C3) at render time; publishes
// window.MemV2Workspace the same way.
//
// Curation constraints (expert-verified, hindsight-api 0.8.4 — binding):
//   - observations are NOT curatable — Inspector hides Edit/Invalidate for
//     fact_type === 'observation' (Delete still applies to any fact_type).
//   - no per-fact tag editing exists upstream — no tag editor anywhere here.
//   - never send an empty PATCH body (422) — Curate's Save button requires
//     the draft to actually differ from the current text.
//   - the revert flow lists archived facts via `state=invalidated` on
//     useBankUnits (they vanish from the default listing) — Inspector
//     doesn't need a fallback fetch for this: useUnitCurate's PATCH
//     response IS the updated unit, so invalidate/revert just replace the
//     locally-held fact with that response instead of waiting on a refetch.
//   - the history endpoint 404s for non-observation facts (already
//     normalized to an empty history by B1's useUnitHistory) — the History
//     toggle button only renders for observation facts, so it's never
//     offered where it can't answer.

const { useState: useStateWorkspace, useEffect: useEffectWorkspace, useRef: useRefWorkspace } = React

const PAGE_SIZE = 10

function memToastWs(msg, kind = 'info') {
  if (typeof window !== 'undefined' && window.__hal0Toast) window.__hal0Toast(msg, kind)
}

// ── when-filter: readable histogram + presets + drag handles ──────────────
// Ported near-verbatim from the prototype; `ts` is the real
// useBankTimeseries(bank, '30d') bucket array instead of a static
// TIMESERIES[bank] mock. `brush` is an index range into `ts`
// ([startIdx, endIdx]); the caller (BankWorkspace) converts that to
// `from`/`to` ISO strings for useBankUnits.
function DensityStrip({ ts, brush, setBrush }) {
  const { fmtN, dayKey } = window.MemV2
  const n = ts.length
  const [drag, setDrag] = useStateWorkspace(null)
  const barsRef = useRefWorkspace(null)
  const idxOf = (e, el) => {
    const r = el.getBoundingClientRect()
    return Math.max(0, Math.min(n - 1, Math.floor(((e.clientX - r.left) / r.width) * n)))
  }
  const sel = drag ? [Math.min(drag[0], drag[1]), Math.max(drag[0], drag[1])] : brush
  const selCount = sel
    ? ts.slice(sel[0], sel[1] + 1).reduce((s, d) => s + d.world + d.experience + d.observation, 0)
    : 0
  const max = Math.max(1, ...ts.map((d) => d.world + d.experience + d.observation))
  const preset = (days) => (days == null ? setBrush(null) : setBrush([Math.max(0, n - days), n - 1]))
  const presetOn = (days) =>
    days == null ? !sel : !!sel && sel[1] === n - 1 && sel[0] === Math.max(0, n - days)
  const onDown = (e) => {
    if (!barsRef.current || n === 0) return
    const i = idxOf(e, barsRef.current)
    setDrag([i, i])
  }
  const onMove = (e) => drag && barsRef.current && setDrag([drag[0], idxOf(e, barsRef.current)])
  const onUp = () => {
    if (drag) {
      const s = [Math.min(drag[0], drag[1]), Math.max(drag[0], drag[1])]
      setBrush(s[0] === 0 && s[1] === n - 1 ? null : s)
      setDrag(null)
    }
  }
  const ticks = n ? [0, Math.floor(n * 0.25), Math.floor(n * 0.5), Math.floor(n * 0.75), n - 1] : []
  return (
    <div className="mv-when" data-testid="mv-when-brush">
      <span className="rowlbl" style={{ marginTop: 14 }}>
        when
      </span>
      <div className="body">
        <div className="bars" ref={barsRef} onMouseDown={onDown} onMouseMove={onMove} onMouseUp={onUp} onMouseLeave={onUp}>
          {ts.map((d, i) => {
            const v = d.world + d.experience + d.observation
            const inR = !sel || (i >= sel[0] && i <= sel[1])
            return (
              <i
                key={i}
                className={inR ? 'in' : ''}
                style={{ height: Math.max(v > 0 ? 5 : 2, (v / max) * 100) + '%' }}
              />
            )
          })}
          {sel && (
            <span
              className="selbox"
              style={{ left: (sel[0] / n) * 100 + '%', width: ((sel[1] - sel[0] + 1) / n) * 100 + '%' }}
            >
              <span className="h" style={{ left: -3 }} />
              <span className="h" style={{ right: -3 }} />
            </span>
          )}
        </div>
        <div className="ticks num">
          {ticks.map((i) => (
            <span key={i}>{ts[i] && dayKey(ts[i].time)}</span>
          ))}
        </div>
      </div>
      <div className="side">
        <div style={{ display: 'flex', gap: 5 }}>
          {[
            ['24h', 1],
            ['7d', 7],
            ['30d', null],
          ].map(([l, d]) => (
            <button key={l} className={'mv-tf' + (presetOn(d) ? ' on' : '')} onClick={() => preset(d)}>
              {l}
            </button>
          ))}
        </div>
        {sel ? (
          <span className="mini num" style={{ display: 'inline-flex', alignItems: 'center', gap: 6 }}>
            {dayKey(ts[sel[0]].time)} – {dayKey(ts[sel[1]].time)} ·{' '}
            <b style={{ color: 'var(--accent)' }}>{fmtN(selCount)}</b> written
            <button
              className="mvi-x"
              style={{ width: 18, height: 18 }}
              onClick={() => setBrush(null)}
              aria-label="clear date filter"
              title="clear date filter"
            >
              <window.MemV2.Icon name="close" size={10} />
            </button>
          </span>
        ) : (
          <span className="mini">drag to filter by date</span>
        )}
      </div>
    </div>
  )
}

// ── atlas: tag bubbles, fed by useBankTags (not a static TOPICS mock) ──────
function AtlasPanel({ tags, totalFacts, onTag, embed }) {
  const { fmtN } = window.MemV2
  const W = 372,
    H = embed ? 240 : 300
  const placed = []
  const maxN = Math.max(1, ...tags.map((t) => t.count))
  const rBase = embed ? 11 : 16,
    rSpan = embed ? 31 : 42
  ;[...tags]
    .map((t, i) => ({ ...t, i, r: rBase + Math.sqrt(t.count / maxN) * rSpan }))
    .sort((a, z) => z.r - a.r)
    .forEach((t) => {
      let x = W / 2,
        y = H / 2
      if (placed.length) {
        for (let a = 0, d = 30; ; a += 0.7, d += 2.2) {
          x = W / 2 + d * Math.cos(a)
          y = H / 2 + d * Math.sin(a) * 0.72
          const ok = placed.every((p) => Math.hypot(p.x - x, p.y - y) > p.r + t.r + 4)
          if (ok && x - t.r > 4 && x + t.r < W - 4 && y - t.r > 4 && y + t.r < H - 4) break
          if (d > W) break
        }
      }
      placed.push({ ...t, x, y })
    })
  const { TOPIC_COLORS } = window.MemV2
  const svg = tags.length > 0 && (
    <svg viewBox={`0 0 ${W} ${H}`}>
      {placed.map((t, i) => {
        const c = TOPIC_COLORS[i % TOPIC_COLORS.length]
        return (
          <g key={t.tag} className="bubble" onClick={() => onTag(t.tag)}>
            <circle cx={t.x} cy={t.y} r={t.r} fill={`color-mix(in srgb, ${c} 14%, transparent)`} stroke={c} strokeWidth="1.25" />
            <text x={t.x} y={t.y - 2} textAnchor="middle" style={{ font: `600 ${t.r > 40 ? 11.5 : 10}px var(--jbm)`, fill: 'var(--fg-1, var(--fg))' }}>
              {t.tag}
            </text>
            <text x={t.x} y={t.y + 12} textAnchor="middle" className="num" style={{ font: '400 9.5px var(--jbm)', fill: 'var(--fg-3)' }}>
              {fmtN(t.count)}
            </text>
          </g>
        )
      })}
    </svg>
  )
  return (
    <div className="mv-atlas" style={{ borderTop: '1px solid var(--line-soft,#1C1C1C)' }}>
      <div style={{ display: 'flex', alignItems: 'center', padding: '9px 14px 0' }}>
        <span className="mv-eyebrow">Tag map</span>
        <span style={{ flex: 1 }} />
        <span className="mini num">{fmtN(totalFacts)} facts</span>
      </div>
      {svg || (
        <div className="mv-empty" style={{ padding: 24 }}>
          bank is empty
        </div>
      )}
    </div>
  )
}

function TopicChips({ tags, topic, setTopic }) {
  const { TOPIC_COLORS, fmtN } = window.MemV2
  return (
    <div className="fline sec-div">
      <span className="rowlbl">tags</span>
      <div style={{ display: 'flex', gap: 5, flexWrap: 'wrap', alignItems: 'center' }}>
        {tags.map((t, i) => {
          const c = TOPIC_COLORS[i % TOPIC_COLORS.length]
          const on = topic === t.tag
          return (
            <button
              key={t.tag}
              className={'mv-tf' + (on ? ' amb' : '')}
              data-testid={`mv-tag-chip-${t.tag}`}
              onClick={() => setTopic(on ? null : t.tag)}
            >
              <span className="dot" style={{ background: c }} />
              {t.tag} <span className="num" style={{ color: 'var(--fg-5)' }}>{fmtN(t.count)}</span>
              {on && <window.MemV2.Icon name="close" size={10} />}
            </button>
          )
        })}
      </div>
    </div>
  )
}

function SourcesPanel({ bank, src, setSrc, limit, setLimit, matchCount }) {
  const { fmtN, Icon, MvError } = window.MemV2
  const useBankDocuments = window.__hal0UseBankDocuments
  const useDocumentReprocess = window.__hal0UseDocumentReprocess
  const docsQuery = useBankDocuments ? useBankDocuments(bank, { limit: 50 }) : { data: null }
  const reprocess = useDocumentReprocess ? useDocumentReprocess() : { mutate: () => {}, isPending: false }

  if (docsQuery.isError) {
    return (
      <div className="mv-card" data-testid="mv-sources">
        <MvError query={docsQuery} what="sources" testid="mv-sources-error" />
      </div>
    )
  }
  const docs = docsQuery.data?.items || []
  const focused = src && docs.find((d) => d.id === src)

  const Row = ({ d, on }) => (
    <div
      className={'mv-docrow' + (on ? ' focused' : '')}
      data-testid={`mv-source-row-${d.id}`}
      onClick={() => setSrc(on ? null : d.id)}
    >
      <Icon name="doc" size={14} />
      <span className="nm mono">{d.id}</span>
      <span className="num facts">{d.memory_unit_count ?? 0} facts</span>
      <span className="num when">{(d.created_at || '').slice(0, 10)}</span>
      {on ? (
        <button
          className="mvi-x"
          onClick={(e) => {
            e.stopPropagation()
            setSrc(null)
          }}
          aria-label="clear"
        >
          <Icon name="close" size={11} />
        </button>
      ) : (
        <button
          className="mv-btn"
          style={{ padding: '1px 8px', fontSize: 10.5 }}
          disabled={reprocess.isPending}
          onClick={(e) => {
            e.stopPropagation()
            reprocess.mutate({ bank, id: d.id })
          }}
        >
          Reprocess
        </button>
      )}
    </div>
  )

  const totalFacts = docs.reduce((s, d) => s + (d.memory_unit_count ?? 0), 0)

  return (
    <div className="mv-card" data-testid="mv-sources">
      <div className="hd">
        <span className="mv-eyebrow">Sources</span>
        <span className="num" style={{ font: '400 10.5px var(--jbm)', color: 'var(--fg-4)' }}>
          {focused ? '1 focused' : `${docs.length} · ${fmtN(totalFacts)} facts extracted`}
        </span>
        <span className="sp" />
        {!focused && (
          <div className="mv-rangetabs">
            {[5, 15, 25, 50].map((n) => (
              <button key={n} className={n === limit ? 'on' : ''} onClick={() => setLimit(n)}>
                {n}
              </button>
            ))}
          </div>
        )}
      </div>
      {focused ? (
        <>
          <Row d={focused} on />
          <div className="mv-more num" style={{ textAlign: 'left', display: 'flex', gap: 8 }}>
            <span>
              ↓ <b style={{ color: 'var(--fg-2)' }}>{matchCount}</b> memories extracted from this source
            </span>
            <span style={{ flex: 1 }} />
            <a style={{ cursor: 'pointer' }} onClick={() => setSrc(null)}>
              show all sources
            </a>
          </div>
        </>
      ) : (
        <>
          {docs.slice(0, limit).map((d) => (
            <Row key={d.id} d={d} on={false} />
          ))}
          {docs.length > limit && <div className="mv-more num">{docs.length - limit} more · raise the limit</div>}
        </>
      )}
    </div>
  )
}

// ── fact list ───────────────────────────────────────────────────────────
function FactList({ units, sel, setSel, footer, query, anyFilter }) {
  const { FACT_COLORS, LINK_COLORS, LINK_LABEL, MvError } = window.MemV2
  // #1539-class fix, task C8: a failed units query used to fall back to an
  // empty array indistinguishable from "no facts match — clear a filter".
  // Check it first so an engine outage is announced, not read as a healthy
  // filtered-to-nothing bank.
  if (query?.isError) {
    return (
      <div className="mv-card mv-list">
        <MvError query={query} what="memory units" testid="mv-units-error" />
      </div>
    )
  }
  return (
    <div className="mv-card mv-list">
      {units.map((f) => {
        const counts = f.link_counts_by_type || {}
        const tag = (f.tags || [])[0]
        return (
          <div
            key={f.id}
            className={'mv-fact' + (sel === f.id ? ' on' : '')}
            data-testid={`mv-fact-row-${f.id}`}
            onClick={() => setSel(f.id)}
          >
            <div className="row1">
              <span className="tdot" style={{ background: FACT_COLORS[f.fact_type] }} title={f.fact_type} />
              <span
                className="lbl"
                style={
                  f.state === 'invalidated'
                    ? { color: 'var(--fg-4)', textDecoration: 'line-through', textDecorationColor: 'var(--fg-5)' }
                    : undefined
                }
              >
                {f.context || f.text}
              </span>
              {f.state === 'invalidated' && <span className="mv-chip warn">invalidated</span>}
              <span className="when num">{String(f.occurred_start).slice(5, 16).replace('T', ' · ')}</span>
            </div>
            <p className="txt" style={f.state === 'invalidated' ? { opacity: 0.55 } : undefined}>
              {f.text}
            </p>
            <div className="meta">
              {tag && (
                <span className="mv-tag" style={{ color: 'var(--fg-3)', border: '1px solid var(--line)' }}>
                  {tag}
                </span>
              )}
              <span className="mv-tag" style={{ color: 'var(--fg-3)', border: '1px solid var(--line)' }}>
                {f.fact_type}
              </span>
              <span style={{ flex: 1 }} />
              {Object.entries(counts).map(([t, n]) => (
                <span key={t} className="mv-linkct num" title={LINK_LABEL[t] || t}>
                  <span className="sw" style={{ background: LINK_COLORS[t] || 'var(--fg-4)' }} />
                  {n}
                </span>
              ))}
            </div>
          </div>
        )
      })}
      {units.length === 0 &&
        // task C8: a genuinely fresh bank (zero facts, no filters active) was
        // showing the same "clear a filter" copy as a filtered-to-nothing
        // search — telling the operator to clear a filter that doesn't
        // exist. Distinguish the two.
        (anyFilter ? (
          <div className="mv-empty">no facts match — clear a filter</div>
        ) : (
          <div className="mv-empty" data-testid="mv-units-empty-bank">
            No memories in this bank yet — use “+ Add” above to record the first fact.
          </div>
        ))}
      {units.length > 0 && footer && (
        <div className="mv-more num">slice end · refine with search, tags, or the time brush to go deeper</div>
      )}
    </div>
  )
}

// ── ego / local graph — server-bounded neighbourhood (never the whole bank) ─
// The server already does the BFS + depth/limit bounding
// (useBankSubgraph(mode:'ego', node, depth, limit)) — these helpers only
// figure out radial ring placement for the already-small returned
// nodes/edges list, replacing the prototype's client-side BFS over mock
// ENTITIES/FACTS.
const trunc = (s, n = 20) => (String(s).length > n ? String(s).slice(0, n - 1) + '…' : String(s))
const _nid = (n) => n.data.id

function _egoAdjacency(edges) {
  const adj = new Map()
  edges.forEach((e) => {
    const { source, target } = e.data
    if (!adj.has(source)) adj.set(source, [])
    if (!adj.has(target)) adj.set(target, [])
    adj.get(source).push(target)
    adj.get(target).push(source)
  })
  return adj
}

// BFS ring distance from `centerId` over the (already server-bounded) node
// set — layout only, not a second data-limiting pass.
function _egoRings(nodes, edges, centerId) {
  const ids = new Set(nodes.map(_nid))
  const adj = _egoAdjacency(edges)
  const ring = new Map([[centerId, 0]])
  let frontier = [centerId]
  let r = 0
  while (frontier.length) {
    r += 1
    const next = []
    frontier.forEach((id) => {
      ;(adj.get(id) || []).forEach((to) => {
        if (ids.has(to) && !ring.has(to)) {
          ring.set(to, r)
          next.push(to)
        }
      })
    })
    frontier = next
  }
  return ring
}

// ego graph (main "focus" view pane) — depth-zoomable radial layout.
function EgoGraph({ bank, centerId, onGo }) {
  const { FACT_COLORS, LINK_COLORS, LINK_LABEL } = window.MemV2
  const [depth, setDepth] = useStateWorkspace(2)
  const useBankSubgraph = window.__hal0UseBankSubgraph
  const subQuery = useBankSubgraph
    ? useBankSubgraph(bank, { mode: 'ego', node: centerId, depth, limit: 60, enabled: !!centerId })
    : { data: null }
  const nodes = subQuery.data?.nodes || []
  const edges = subQuery.data?.edges || []

  const depthSlider = (
    <div className="mv-depthslider">
      <span className="k">depth</span>
      <input
        type="range"
        min="1"
        max="10"
        step="1"
        data-testid="mv-ego-depth"
        value={depth}
        onChange={(e) => setDepth(+e.target.value)}
      />
      <b className="num">{depth}</b>
    </div>
  )

  if (!centerId || nodes.length === 0) {
    return (
      <div className="mv-card mv-ego" data-testid="mv-ego">
        <div className="hd">
          <span className="mv-eyebrow">Neighbourhood</span>
        </div>
        <div style={{ position: 'relative' }}>
          <div className="mv-empty" style={{ padding: 60 }}>
            {centerId ? 'no facts match' : 'select a fact to see its neighbourhood'}
          </div>
          {centerId && depthSlider}
        </div>
      </div>
    )
  }

  const ring = _egoRings(nodes, edges, centerId)
  const R = (r) => 90 + (r - 1) * 70
  const byRing = new Map()
  nodes.forEach((n) => {
    const r = ring.get(_nid(n)) ?? 0
    if (!byRing.has(r)) byRing.set(r, [])
    byRing.get(r).push(n)
  })
  const positions = new Map([[centerId, { x: 0, y: 0 }]])
  ;[...byRing.entries()]
    .filter(([r]) => r > 0)
    .forEach(([r, group]) => {
      group.forEach((n, i) => {
        const a = (i / Math.max(1, group.length)) * 2 * Math.PI - Math.PI / 2
        positions.set(_nid(n), { x: R(r) * Math.cos(a), y: R(r) * Math.sin(a) })
      })
    })
  const xs = [...positions.values()].map((p) => p.x)
  const ys = [...positions.values()].map((p) => p.y)
  const minX = Math.min(...xs) - 90,
    maxX = Math.max(...xs) + 90
  const minY = Math.min(...ys) - 40,
    maxY = Math.max(...ys) + 70
  const VW = Math.max(400, maxX - minX),
    VH = Math.max(300, maxY - minY)
  const maxReach = Math.max(0, ...[...ring.values()])

  return (
    <div className="mv-card mv-ego" data-testid="mv-ego">
      <div className="hd">
        <span className="mv-eyebrow">Neighbourhood</span>
        <span className="num" style={{ font: '400 10.5px var(--jbm)', color: 'var(--fg-4)' }}>
          {nodes.length} facts · {edges.length} links
          {maxReach < depth && <span> · exhausted at depth {maxReach}</span>}
        </span>
        <span className="sp" />
        <span style={{ font: '400 10px var(--jbm)', color: 'var(--fg-5)' }}>click a node to re-centre</span>
      </div>
      <div style={{ position: 'relative' }}>
        <svg viewBox={`${minX} ${minY} ${VW} ${VH}`} style={{ maxHeight: 640, margin: '0 auto', display: 'block', width: '100%' }}>
          {Array.from({ length: Math.min(depth, maxReach) }, (_, i) => (
            <circle key={i} cx={0} cy={0} r={R(i + 1)} fill="none" stroke="var(--line-soft,#1C1C1C)" strokeDasharray="2 5" />
          ))}
          {edges.map((e, i) => {
            const a = positions.get(e.data.source)
            const b = positions.get(e.data.target)
            if (!a || !b) return null
            const w = Number(e.data.weight) || 1
            return (
              <line
                key={i}
                x1={a.x}
                y1={a.y}
                x2={b.x}
                y2={b.y}
                stroke={LINK_COLORS[e.data.linkType ?? e.data.type] || 'var(--fg-4)'}
                strokeWidth={w >= 2 ? 1.5 : 1}
                opacity={w >= 2 ? 0.65 : 0.35}
              />
            )
          })}
          {nodes.map((n) => {
            const id = _nid(n)
            const p = positions.get(id) || { x: 0, y: 0 }
            const r0 = ring.get(id) ?? 0
            const r = r0 === 0 ? 10 : r0 === 1 ? 6.5 : 5
            const above = p.y < 0 || r0 === 0
            return (
              <g key={id} style={{ cursor: r0 === 0 ? 'default' : 'pointer' }} onClick={() => r0 !== 0 && onGo(id)}>
                <circle cx={p.x} cy={p.y} r={r} fill={r0 === 0 ? FACT_COLORS[n.data.type] : 'var(--bg-2)'} stroke={FACT_COLORS[n.data.type]} strokeWidth="1.5" />
                <text
                  x={p.x}
                  y={r0 === 0 ? p.y + 26 : above ? p.y - r - 8 : p.y + r + 14}
                  textAnchor="middle"
                  style={{ font: '500 10px var(--jbm)', fill: 'var(--fg-3)' }}
                >
                  {trunc(n.data.label || id, r0 >= 2 ? 18 : 24)}
                </text>
              </g>
            )
          })}
        </svg>
        {depthSlider}
      </div>
      <div className="mv-lg-legend" style={{ padding: '8px 14px 10px', borderTop: '1px solid var(--line-soft,#1C1C1C)' }}>
        {Object.keys(LINK_COLORS).map((t) => (
          <span key={t}>
            <i style={{ background: LINK_COLORS[t] }} />
            {LINK_LABEL[t]}
          </span>
        ))}
        <span style={{ flex: 1 }} />
        {Object.keys(FACT_COLORS).map((t) => (
          <span key={t}>
            <i style={{ background: FACT_COLORS[t], width: 7, height: 7, borderRadius: '50%' }} />
            {t}
          </span>
        ))}
      </div>
    </div>
  )
}

// inspector's compact depth-1 neighbourhood (no depth slider, no re-centre
// zoom — a quick "what's directly connected" glance).
function LocalGraph({ bank, centerId, onGo }) {
  const { FACT_COLORS, LINK_COLORS } = window.MemV2
  const useBankSubgraph = window.__hal0UseBankSubgraph
  const subQuery = useBankSubgraph
    ? useBankSubgraph(bank, { mode: 'ego', node: centerId, depth: 1, limit: 13, enabled: !!centerId })
    : { data: null }
  const nodes = subQuery.data?.nodes || []
  const edges = subQuery.data?.edges || []
  const center = nodes.find((n) => _nid(n) === centerId)
  const nbrs = nodes.filter((n) => _nid(n) !== centerId)
  const W = 372,
    H = 236,
    cx = W / 2,
    cy = H / 2,
    R = 78

  if (!center) {
    return (
      <div className="mv-empty" style={{ padding: 18 }}>
        {centerId ? 'loading neighbourhood…' : 'no fact selected'}
      </div>
    )
  }

  return (
    <div className="mv-lg">
      <svg viewBox={`0 0 ${W} ${H}`}>
        <circle cx={cx} cy={cy} r={R} fill="none" stroke="var(--line-soft,#1C1C1C)" strokeDasharray="2 5" />
        {nbrs.map((n, i) => {
          const a = (i / Math.max(1, nbrs.length)) * 2 * Math.PI - Math.PI / 2
          const x = cx + R * Math.cos(a),
            y = cy + R * Math.sin(a)
          const above = y < cy
          const id = _nid(n)
          const edge = edges.find(
            (e) => (e.data.source === centerId && e.data.target === id) || (e.data.target === centerId && e.data.source === id),
          )
          return (
            <g key={id} style={{ cursor: 'pointer' }} onClick={() => onGo(id)}>
              <line x1={cx} y1={cy} x2={x} y2={y} stroke={edge ? LINK_COLORS[edge.data.linkType ?? edge.data.type] || 'var(--fg-4)' : 'var(--fg-4)'} strokeWidth="1.5" opacity="0.75" />
              <circle cx={x} cy={y} r="6" fill="var(--bg-2)" stroke={FACT_COLORS[n.data.type]} strokeWidth="1.5" />
              <text x={x} y={above ? y - 12 : y + 19} textAnchor="middle" style={{ font: '500 10px var(--jbm)', fill: 'var(--fg-3)' }}>
                {trunc(n.data.label || id)}
              </text>
            </g>
          )
        })}
        <circle cx={cx} cy={cy} r="9" fill={FACT_COLORS[center.data.type]} stroke="var(--fg)" strokeWidth="1.25" />
        <text x={cx} y={cy + 26} textAnchor="middle" style={{ font: '600 10.5px var(--jbm)', fill: 'var(--fg)' }}>
          {trunc(center.data.label || centerId, 26)}
        </text>
      </svg>
    </div>
  )
}

// ── inspector ─────────────────────────────────────────────────────────────
// `unitsPage` is the currently-displayed page of units (from BankWorkspace's
// own useBankUnits fetch) — used to resolve the selected fact's data without
// a second "get unit by id" endpoint (none exists). After any curate
// mutation, `useUnitCurate`'s PATCH response IS the updated unit, so it
// replaces the resolved fact locally instead of waiting on a refetch — this
// is also how a just-invalidated fact (which vanishes from the default
// valid-only listing) stays viewable/revertable without a fallback fetch.
function Inspector({ bank, sel, setSel, unitsPage }) {
  const { FACT_COLORS, Icon } = window.MemV2
  const useUnitCurate = window.__hal0UseUnitCurate
  const useUnitHistory = window.__hal0UseUnitHistory
  const useMemoryDelete = window.__hal0UseMemoryDelete

  const [override, setOverride] = useStateWorkspace(null)
  const [mode, setMode] = useStateWorkspace(null) // 'curate' | 'invalidate' | 'delete' | null
  const [draft, setDraft] = useStateWorkspace('')
  const [reason, setReason] = useStateWorkspace('')
  const [showHistory, setShowHistory] = useStateWorkspace(false)
  useEffectWorkspace(() => {
    setOverride(null)
    setMode(null)
    setReason('')
    setShowHistory(false)
  }, [sel])

  const fromPage = (unitsPage || []).find((u) => u.id === sel)
  const f = override || fromPage

  const curate = useUnitCurate ? useUnitCurate(bank) : { mutate: () => {}, isPending: false }
  const del = useMemoryDelete ? useMemoryDelete() : { mutate: () => {}, isPending: false }
  const isObservation = f?.fact_type === 'observation'
  // History 404s for non-observation facts (normalized to an empty history
  // by B1's useUnitHistory) — only fetch/offer it where it can answer.
  const history = useUnitHistory
    ? useUnitHistory(bank, sel, { enabled: isObservation && showHistory })
    : { data: null, isLoading: false }

  if (!f) {
    return (
      <div className="mv-card mv-insp" data-testid="mv-inspector">
        <div className="mv-empty" style={{ padding: 48 }}>
          fact not found — it may have left the current page
        </div>
      </div>
    )
  }

  const isInvalidated = f.state === 'invalidated'

  const submitCurate = () => {
    const text = draft.trim()
    // Never send an empty PATCH body — require ≥1 changed field.
    if (!text || text === f.text) {
      memToastWs('Nothing changed', 'warn')
      return
    }
    curate.mutate(
      { id: f.id, body: { text } },
      {
        onSuccess: (updated) => {
          setOverride(updated)
          setMode(null)
          memToastWs('Fact updated', 'ok')
        },
        onError: (err) => memToastWs(`Update failed: ${err.message}`, 'err'),
      },
    )
  }
  const submitInvalidate = () => {
    curate.mutate(
      { id: f.id, body: { state: 'invalidated', reason: reason || undefined } },
      {
        onSuccess: (updated) => {
          setOverride(updated)
          setMode(null)
          memToastWs('Fact invalidated — excluded from recall', 'warn')
        },
        onError: (err) => memToastWs(`Invalidate failed: ${err.message}`, 'err'),
      },
    )
  }
  const submitRevert = () => {
    curate.mutate(
      { id: f.id, body: { state: 'valid' } },
      {
        onSuccess: (updated) => {
          setOverride(updated)
          memToastWs('Fact restored to recall', 'ok')
        },
        onError: (err) => memToastWs(`Revert failed: ${err.message}`, 'err'),
      },
    )
  }
  const submitDelete = () => {
    del.mutate(
      { ids: [f.id], dataset: bank },
      {
        onSuccess: () => {
          memToastWs('Fact deleted — audited', 'ok')
          setSel(null)
        },
        onError: (err) => memToastWs(`Delete failed: ${err.message}`, 'err'),
      },
    )
  }

  return (
    <div className="mv-card mv-insp" data-testid="mv-inspector">
      {isInvalidated && (
        <div className="mvi-banner warn">
          <span>invalidated — excluded from recall · reversible</span>
          <span style={{ flex: 1 }} />
          <button
            className="mv-btn"
            data-testid="mv-insp-revert"
            style={{ padding: '1px 9px', fontSize: 10.5 }}
            onClick={submitRevert}
            disabled={curate.isPending}
          >
            Revert
          </button>
        </div>
      )}
      <div className="mvi-sec">
        <div className="mvi-top">
          <span
            className="mv-tag"
            style={{ color: FACT_COLORS[f.fact_type], border: `1px solid color-mix(in srgb, ${FACT_COLORS[f.fact_type]} 35%, transparent)` }}
          >
            ● {f.fact_type}
          </span>
          {(f.tags || []).map((t) => (
            <span key={t} className="mv-tag" style={{ color: 'var(--fg-3)', border: '1px solid var(--line)' }}>
              {t}
            </span>
          ))}
          {isObservation && (
            <button
              className="mv-btn"
              data-testid="mv-insp-history"
              style={{ padding: '0 7px', fontSize: 10 }}
              onClick={() => setShowHistory((v) => !v)}
            >
              <Icon name="clock" size={11} /> History
            </button>
          )}
          <span className="sp" />
          <button className="mvi-x" onClick={() => setSel(null)} aria-label="close">
            <Icon name="close" size={13} />
          </button>
        </div>
        {mode === 'curate' ? (
          <div className="mvi-form">
            <textarea
              className="mv-input"
              style={{ minHeight: 84, fontFamily: 'var(--geist,sans-serif)', resize: 'vertical', lineHeight: 1.5 }}
              value={draft}
              onChange={(e) => setDraft(e.target.value)}
            />
            <input className="mv-input" placeholder="why? — recorded in history" value={reason} onChange={(e) => setReason(e.target.value)} />
            <div style={{ display: 'flex', gap: 8, alignItems: 'center' }}>
              <span className="mini">creates a new revision · original stays in history</span>
              <span style={{ flex: 1 }} />
              <button className="mv-btn" onClick={() => setMode(null)}>
                Cancel
              </button>
              <button className="mv-btn primary" onClick={submitCurate} disabled={curate.isPending}>
                {curate.isPending ? 'Saving…' : 'Save revision'}
              </button>
            </div>
          </div>
        ) : (
          <p
            className="fulltxt"
            style={isInvalidated ? { color: 'var(--fg-4)', textDecoration: 'line-through', textDecorationColor: 'var(--fg-5)' } : undefined}
          >
            {f.text}
          </p>
        )}
      </div>
      <div className="mvi-sec">
        <div className="mv-kv num">
          <span className="k">when</span>
          <span className="v">{String(f.occurred_start || '').replace('T', ' · ')}</span>
          <span className="k">unit id</span>
          <span className="v" style={{ color: 'var(--fg-4)' }}>
            {f.id}
          </span>
          <span className="k">entities</span>
          <span className="v">{(f.entities || []).length ? f.entities.join(' · ') : '—'}</span>
          <span className="k">salience</span>
          <span className="v">
            {/* task C7 (backend A3b): null for a unit outside the
                salience-scored slab — render "—", not "null". final-review
                M1: the real server value is an unbounded full-precision
                float (sum of type_weight*weight over incident edges, e.g.
                7.399999999999999) — the mock rounds to 2dp, which is why no
                test caught the raw render. .toFixed(2), null-safe. */}
            {f.salience == null ? '—' : f.salience.toFixed(2)}{' '}
            <span style={{ color: 'var(--fg-4)' }}>{f.salience == null ? 'unknown' : 'weighted degree'}</span>
          </span>
        </div>
      </div>
      <div className="mvi-sec">
        <div className="mvi-h">
          Neighbourhood
          <span className="ct num"> · {Object.values(f.link_counts_by_type || {}).reduce((a, b) => a + b, 0)} links</span>
        </div>
        <LocalGraph bank={bank} centerId={f.id} onGo={setSel} />
        <div className="mv-lg-legend">
          {Object.keys(window.MemV2.LINK_COLORS).map((t) => (
            <span key={t}>
              <i style={{ background: window.MemV2.LINK_COLORS[t] }} />
              {window.MemV2.LINK_LABEL[t]}
            </span>
          ))}
        </div>
      </div>
      {isObservation && showHistory && (
        <div className="mvi-sec">
          <div className="mvi-hist">
            {history.isLoading ? (
              <div className="mv-empty" style={{ padding: 14 }}>
                loading…
              </div>
            ) : (
              <>
                {(history.data?.events || []).map((h, i) => (
                  <div key={i} className="mvi-ev">
                    <span className={'nd ' + (h.state || '')} />
                    <div className="body">
                      <div className="top">
                        <b>{h.state || 'event'}</b>
                        <span className="at num">{String(h.at || '').replace('T', ' · ')}</span>
                      </div>
                      {h.reason && <div className="note">{h.reason}</div>}
                    </div>
                  </div>
                ))}
                {(history.data?.events || []).length === 0 && (
                  <div className="mv-empty" style={{ padding: 14 }}>
                    no history yet
                  </div>
                )}
              </>
            )}
          </div>
        </div>
      )}
      {mode === 'invalidate' && (
        <div className="mvi-sec mvi-confirm">
          <p>
            Soft-invalidate — the fact stops being recalled or injected but stays in the bank. Reversible from here; no
            approval gate.
          </p>
          <input className="mv-input" placeholder="why? — recorded in history" value={reason} onChange={(e) => setReason(e.target.value)} />
          <div style={{ display: 'flex', gap: 8, justifyContent: 'flex-end' }}>
            <button className="mv-btn" onClick={() => setMode(null)}>
              Cancel
            </button>
            <button
              className="mv-btn"
              style={{ color: 'var(--warn)', borderColor: 'color-mix(in srgb, var(--warn) 35%, transparent)' }}
              onClick={submitInvalidate}
              disabled={curate.isPending}
            >
              Invalidate
            </button>
          </div>
        </div>
      )}
      {mode === 'delete' && (
        <div className="mvi-sec mvi-confirm danger">
          <p>
            Permanent — removed from recall and the graph, with a durable audit row (actor, target, outcome). Prefer
            Invalidate if the fact is merely wrong.
          </p>
          <div style={{ display: 'flex', gap: 8, justifyContent: 'flex-end' }}>
            <button className="mv-btn" onClick={() => setMode(null)}>
              Cancel
            </button>
            <button className="mv-btn danger" onClick={submitDelete} disabled={del.isPending}>
              Delete fact
            </button>
          </div>
        </div>
      )}
      {!mode && (
        <div className="mvi-sec mvi-actions">
          {!isObservation && (
            <button
              className="mv-btn"
              data-testid="mv-insp-edit"
              onClick={() => {
                setDraft(f.text)
                setMode('curate')
              }}
            >
              <Icon name="edit" size={12} /> Curate
            </button>
          )}
          {!isObservation && !isInvalidated && (
            <button className="mv-btn" data-testid="mv-insp-invalidate" style={{ color: 'var(--warn)' }} onClick={() => setMode('invalidate')}>
              Invalidate
            </button>
          )}
          {isObservation && (
            <span className="mini" style={{ color: 'var(--fg-4)' }}>
              observations aren't curatable — derived patterns, not operator-editable
            </span>
          )}
          <span className="sp" />
          <button className="mv-btn danger" data-testid="mv-insp-delete" onClick={() => setMode('delete')}>
            Delete
          </button>
        </div>
      )}
    </div>
  )
}

function BankWorkspace({ bank, setBank, sel, setSel }) {
  const { FACT_COLORS } = window.MemV2

  const [q, setQ] = useStateWorkspace('')
  const [types, setTypes] = useStateWorkspace({ world: true, experience: true, observation: true })
  const [topic, setTopic] = useStateWorkspace(null)
  const [brush, setBrush] = useStateWorkspace(null) // [startIdx, endIdx] into the 30d ts buckets
  const [sort, setSort] = useStateWorkspace('recent')
  const [view, setView] = useStateWorkspace('list')
  const [src, setSrc] = useStateWorkspace(null)
  const [srcLimit, setSrcLimit] = useStateWorkspace(5)
  const [page, setPage] = useStateWorkspace(0)
  // "Show invalidated" — the only way to browse/recover a fact once
  // Inspector's local `override` state (set right after an invalidate
  // mutation) is gone (navigated away, page reload, …). Flips the units
  // query to `state=invalidated`; Inspector already renders the
  // invalidated banner + Revert action for any fact with
  // `state === 'invalidated'` regardless of how it got there.
  const [showInvalidated, setShowInvalidated] = useStateWorkspace(false)

  const useBankStats = window.__hal0UseBankStats
  const useBankTags = window.__hal0UseBankTags
  const useBankTimeseries = window.__hal0UseBankTimeseries
  const useBankUnits = window.__hal0UseBankUnits

  const statsQuery = useBankStats ? useBankStats(bank) : { data: null }
  const stats = statsQuery.data
  const typeCounts = stats?.nodes_by_fact_type || {}

  const tagsQuery = useBankTags ? useBankTags(bank) : { data: null }
  const tags = tagsQuery.data?.items || []

  const tsQuery = useBankTimeseries ? useBankTimeseries(bank, '30d') : { data: null }
  const buckets = tsQuery.data?.buckets || []

  // Truthful server-side type filtering (fix round, post C4-review): the
  // real/mocked `type` filter now accepts a comma-joined multi-value OR
  // (hal0-side task A3b — upstream Hindsight itself is single-value-only,
  // but the units route layers OR semantics on top: `type=world,experience`
  // → truthful filtered total_matched/next_offset; the mock's
  // `buildBankUnits` mirrors the same comma-list OR contract). ALL active
  // types are sent — there is NO client-side narrowing of the returned
  // page anymore, so `total_matched`/`pages`/"showing X–Y of N" always
  // match exactly what's rendered, for every toggle combination (1, 2, or
  // 3 of 3 active). All-3-active (the default) and 0-active both send no
  // `type` param — the former because "every type" is equivalent to "no
  // filter" on both mock and server; the latter is handled entirely
  // client-side below (zero rows, no network round-trip needed) since
  // "every type off" isn't a filter value any endpoint needs to express.
  const activeTypes = Object.keys(types).filter((t) => types[t])
  const zeroTypesActive = activeTypes.length === 0
  const typeParam = !zeroTypesActive && activeTypes.length < 3 ? activeTypes.join(',') : undefined

  const from = brush && buckets[brush[0]] ? buckets[brush[0]].time : undefined
  const to = brush && buckets[brush[1]] ? buckets[brush[1]].time : undefined

  const unitsParams = {
    q: q || undefined,
    tags: topic ? [topic] : undefined,
    type: typeParam,
    state: showInvalidated ? 'invalidated' : undefined,
    from,
    to,
    documentId: src || undefined,
    sort: sort === 'salience' ? 'salience' : 'recency',
    limit: PAGE_SIZE,
    offset: page * PAGE_SIZE,
  }
  // final-review M6: the hook must still be called unconditionally (React
  // hooks rule — it's a real hook call either way, `enabled` just tells
  // react-query to skip the network round-trip), but with 0 type toggles
  // active the result is always discarded by the `zeroTypesActive ? [] : …`
  // branches below — so there's no reason to fire it.
  const unitsQuery = useBankUnits ? useBankUnits(bank, unitsParams, { enabled: !zeroTypesActive }) : { data: null }
  const pageUnits = zeroTypesActive ? [] : unitsQuery.data?.items || []
  const totalMatched = zeroTypesActive ? 0 : unitsQuery.data?.total_matched ?? pageUnits.length
  const pages = Math.max(1, Math.ceil(totalMatched / PAGE_SIZE))
  const pageSafe = Math.min(page, pages - 1)

  useEffectWorkspace(() => {
    setPage(0)
  }, [bank, q, topic, src, brush, sort, showInvalidated, JSON.stringify(types)])

  // The ego/focus view needs a centre — default to the first matching fact
  // when switching into it with nothing selected yet.
  useEffectWorkspace(() => {
    if (view === 'graph' && !sel && pageUnits.length) setSel(pageUnits[0].id)
  }, [view, sel, pageUnits.length])

  const switchBank = (id) => {
    setBank(id)
    setSel(null)
    setTopic(null)
    setBrush(null)
    setQ('')
    setSrc(null)
    setShowInvalidated(false)
  }

  // Keyboard (spec'd, not in the prototype): ↑/↓ moves list selection
  // (only meaningful in the list view — a no-op elsewhere since there's no
  // ordered row set to move through), Esc closes the inspector.
  useEffectWorkspace(() => {
    const onKeyDown = (e) => {
      if (e.key === 'Escape') {
        if (sel) setSel(null)
        return
      }
      if (view !== 'list' || pageUnits.length === 0) return
      if (e.key === 'ArrowDown' || e.key === 'ArrowUp') {
        e.preventDefault()
        const idx = pageUnits.findIndex((u) => u.id === sel)
        const delta = e.key === 'ArrowDown' ? 1 : -1
        const next = idx === -1 ? 0 : Math.max(0, Math.min(pageUnits.length - 1, idx + delta))
        setSel(pageUnits[next].id)
      }
    }
    window.addEventListener('keydown', onKeyDown)
    return () => window.removeEventListener('keydown', onKeyDown)
  }, [sel, view, pageUnits.map((u) => u.id).join(',')])

  const anyFilter =
    q || topic || src || brush || showInvalidated || !types.world || !types.experience || !types.observation
  const clearAll = () => {
    setQ('')
    setTypes({ world: true, experience: true, observation: true })
    setTopic(null)
    setSrc(null)
    setBrush(null)
    setShowInvalidated(false)
  }

  return (
    <div className="mv-page" data-testid="mv-workspace">
      {window.MemV2BankBar && <window.MemV2BankBar bank={bank} setBank={switchBank} />}
      <div className="mv-workgrid">
        <div style={{ display: 'flex', flexDirection: 'column', gap: 14, minWidth: 0 }}>
          <div className="mv-card">
            <div className="fline">
              <span className="mini num">
                showing{' '}
                <b style={{ color: 'var(--fg-2)' }}>
                  {totalMatched ? pageSafe * PAGE_SIZE + 1 : 0}–{Math.min(totalMatched, (pageSafe + 1) * PAGE_SIZE)}
                </b>{' '}
                of {totalMatched} matched
                {/* final-review I2: the server's 2000-row upstream slab can
                    clip the match set — total_matched/paging (and, under
                    sort=salience, the ranking) are only accurate within that
                    slab. Say so, mirroring the web view's own "top 120 by
                    salience" cap notice, instead of stating a number the
                    server itself flagged as a floor, not a total. */}
                {unitsQuery.data?.truncated && (
                  <span data-testid="mv-units-truncated" style={{ color: 'var(--warn)' }}>
                    {' '}
                    (first 2000 matched — refine filters)
                  </span>
                )}
              </span>
              {topic && (
                <span className="mv-tf amb" onClick={() => setTopic(null)}>
                  tag: {topic} <window.MemV2.Icon name="close" size={10} />
                </span>
              )}
              {src && (
                <span className="mv-tf amb" onClick={() => setSrc(null)}>
                  source: {src} <window.MemV2.Icon name="close" size={10} />
                </span>
              )}
              {brush && buckets[brush[0]] && (
                <span className="mv-tf amb" onClick={() => setBrush(null)}>
                  when: {window.MemV2.dayKey(buckets[brush[0]].time)} – {window.MemV2.dayKey(buckets[brush[1]].time)}{' '}
                  <window.MemV2.Icon name="close" size={10} />
                </span>
              )}
              <span style={{ flex: 1 }} />
              <span className="mini">sort</span>
              <select
                className="mono"
                data-testid="mv-sort"
                style={{ font: '500 11px var(--jbm)', background: 'transparent', color: 'var(--fg-2)', border: '1px solid var(--line)', borderRadius: 4, padding: '1px 4px' }}
                value={sort}
                onChange={(e) => setSort(e.target.value)}
              >
                <option value="recent">newest</option>
                <option value="salience">most connected</option>
              </select>
              <div className="mv-seg">
                <button className={view === 'list' ? 'on' : ''} data-testid="mv-view-list" onClick={() => setView('list')}>
                  <window.MemV2.Icon name="logs" size={12} /> list
                </button>
                <button className={view === 'graph' ? 'on' : ''} data-testid="mv-view-graph" onClick={() => setView('graph')}>
                  <window.MemV2.Icon name="focus" size={12} /> focus
                </button>
                <button className={view === 'web' ? 'on' : ''} data-testid="mv-view-web" onClick={() => setView('web')}>
                  <window.MemV2.Icon name="graph" size={12} /> web
                </button>
              </div>
            </div>
          </div>
          {view === 'list' && (
            <SourcesPanel bank={bank} src={src} setSrc={setSrc} limit={srcLimit} setLimit={setSrcLimit} matchCount={totalMatched} />
          )}
          {view === 'list' && (
            <FactList
              units={pageUnits}
              sel={sel}
              setSel={setSel}
              footer={pageSafe === pages - 1}
              query={unitsQuery}
              anyFilter={anyFilter}
            />
          )}
          {view === 'list' && pages > 1 && (
            <div className="mv-card">
              <div className="fline" style={{ justifyContent: 'center', gap: 14 }}>
                <button className="mv-btn" disabled={pageSafe === 0} style={{ opacity: pageSafe === 0 ? 0.4 : 1 }} onClick={() => setPage(pageSafe - 1)}>
                  ‹ prev
                </button>
                <span className="mini num">
                  page <b style={{ color: 'var(--fg-2)' }}>{pageSafe + 1}</b> of {pages}
                </span>
                <button
                  className="mv-btn"
                  data-testid="mv-fact-page-next"
                  disabled={pageSafe === pages - 1}
                  style={{ opacity: pageSafe === pages - 1 ? 0.4 : 1 }}
                  onClick={() => setPage(pageSafe + 1)}
                >
                  next ›
                </button>
              </div>
            </div>
          )}
          {view === 'graph' && <EgoGraph bank={bank} centerId={sel} onGo={setSel} />}
          {view === 'web' &&
            (typeof window.MemV2WebGraph === 'function' ? (
              <window.MemV2WebGraph bank={bank} sel={sel} setSel={setSel} filters={unitsParams} />
            ) : (
              <div className="mv-card">
                <div className="mv-empty" style={{ padding: 60 }}>
                  web view lands in task C5
                </div>
              </div>
            ))}
        </div>
        <div style={{ display: 'flex', flexDirection: 'column', gap: 14, minWidth: 0 }}>
          {sel ? (
            <Inspector bank={bank} sel={sel} setSel={setSel} unitsPage={pageUnits} />
          ) : (
            <div className="mv-card mv-fside" data-testid="mv-filter-card">
              <div className="hd">
                <span className="mv-eyebrow">Filters</span>
                <span className="sp" />
                {anyFilter && (
                  <button className="mv-btn" style={{ padding: '1px 8px', fontSize: 10.5 }} onClick={clearAll}>
                    clear all
                  </button>
                )}
              </div>
              <div className="fline">
                <div className="mv-search">
                  <window.MemV2.Icon name="search" size={13} />
                  <input data-testid="mv-search" placeholder="search facts…" value={q} onChange={(e) => setQ(e.target.value)} />
                </div>
              </div>
              <div className="fline sec-div" style={{ flexWrap: 'wrap' }}>
                {['world', 'experience', 'observation'].map((t) => (
                  <button
                    key={t}
                    className={'mv-tf ' + (types[t] ? 'on' : 'off')}
                    data-testid={`mv-type-${t}`}
                    onClick={() => setTypes({ ...types, [t]: !types[t] })}
                  >
                    <span className="dot" style={{ background: FACT_COLORS[t] }} />
                    {t} <span className="num" style={{ color: 'var(--fg-4)' }}>{typeCounts[t] ?? 0}</span>
                  </button>
                ))}
              </div>
              <div className="fline sec-div">
                {/* Only way to browse/recover an invalidated fact once
                    Inspector's local `override` state is gone (nav away,
                    reload). Flips the units query to state=invalidated;
                    Inspector already renders the invalidated banner +
                    Revert action for any fact in that state. */}
                <button
                  className={'mv-tf ' + (showInvalidated ? 'on' : 'off')}
                  data-testid="mv-state-invalidated"
                  onClick={() => setShowInvalidated((v) => !v)}
                >
                  <span className="dot" style={{ background: 'var(--warn)' }} />
                  {showInvalidated ? 'showing invalidated' : 'show invalidated'}
                </button>
              </div>
              <div className="fline sec-div" style={{ alignItems: 'flex-start' }}>
                <DensityStrip ts={buckets} brush={brush} setBrush={setBrush} />
              </div>
              <AtlasPanel tags={tags} totalFacts={stats?.total_nodes ?? 0} onTag={setTopic} embed />
              <TopicChips tags={tags} topic={topic} setTopic={setTopic} />
            </div>
          )}
        </div>
      </div>
    </div>
  )
}

Object.assign(window, {
  MemV2Workspace: BankWorkspace,
  MemV2AtlasPanel: AtlasPanel,
  MemV2FactList: FactList,
  MemV2DensityStrip: DensityStrip,
  MemV2Inspector: Inspector,
  MemV2EgoGraph: EgoGraph,
  MemV2LocalGraph: LocalGraph,
})
