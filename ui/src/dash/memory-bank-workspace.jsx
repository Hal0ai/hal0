// hal0 memory v2 (Bank workspace UI, task C4) — the unified Bank workspace:
// BankBar on top, filter card / sources / fact list / (later) inspector +
// ego graph below.
//
// Ported from the design handoff's prototype/app-b.jsx (BankWorkspace,
// SourcesPanel, TopicChips) + prototype/explore.jsx (DensityStrip,
// FactList, AtlasPanel — EgoGraph/LocalGraph/Inspector land in later
// commits of this same task). Server-side filtering replaces the
// prototype's client-side `facts.filter(...)`: every filter (search, type,
// tag, when-brush, source focus, sort, paging) is driven through
// `useBankUnits`'s params instead of filtering a fully-loaded mock array.
//
// Window-globals contract: no ES imports across dash/*.jsx — reads
// window.MemV2 (C1), window.MemV2BankBar (C3) at render time; publishes
// window.MemV2Workspace the same way.

const { useState: useStateWorkspace, useEffect: useEffectWorkspace, useRef: useRefWorkspace } = React

const PAGE_SIZE = 10

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
  const { fmtN, Icon } = window.MemV2
  const useBankDocuments = window.__hal0UseBankDocuments
  const useDocumentReprocess = window.__hal0UseDocumentReprocess
  const docsQuery = useBankDocuments ? useBankDocuments(bank, { limit: 50 }) : { data: null }
  const reprocess = useDocumentReprocess ? useDocumentReprocess() : { mutate: () => {}, isPending: false }
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
function FactList({ units, sel, setSel, footer }) {
  const { FACT_COLORS, LINK_COLORS, LINK_LABEL } = window.MemV2
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
      {units.length === 0 && <div className="mv-empty">no facts match — clear a filter</div>}
      {units.length > 0 && footer && (
        <div className="mv-more num">slice end · refine with search, tags, or the time brush to go deeper</div>
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

  // The real/mocked `type` filter is single-valued — it can't express "any
  // 2 of 3" the prototype's independent per-type toggles allow. When
  // exactly one type is active, that's sent straight to the server
  // (the common case); for 0/2/3-active combinations (including the
  // all-on default), no `type` param is sent and the already
  // server-filtered page is narrowed client-side instead — `useBankUnits`
  // still does the heavy lifting (q/tags/state/from/to/documentId/sort/
  // paging), this is just the one filter dimension the API can't take a
  // set for.
  const activeTypes = Object.keys(types).filter((t) => types[t])
  const singleType = activeTypes.length === 1 ? activeTypes[0] : undefined

  const from = brush && buckets[brush[0]] ? buckets[brush[0]].time : undefined
  const to = brush && buckets[brush[1]] ? buckets[brush[1]].time : undefined

  const unitsParams = {
    q: q || undefined,
    tags: topic ? [topic] : undefined,
    type: singleType,
    from,
    to,
    documentId: src || undefined,
    sort: sort === 'salience' ? 'salience' : 'recency',
    limit: PAGE_SIZE,
    offset: page * PAGE_SIZE,
  }
  const unitsQuery = useBankUnits ? useBankUnits(bank, unitsParams) : { data: null }
  const allUnits = unitsQuery.data?.items || []
  // Client-side narrowing only for the 0/2/3-active-types case (see above).
  const pageUnits = singleType ? allUnits : allUnits.filter((u) => types[u.fact_type])
  const totalMatched = unitsQuery.data?.total_matched ?? pageUnits.length
  const pages = Math.max(1, Math.ceil(totalMatched / PAGE_SIZE))
  const pageSafe = Math.min(page, pages - 1)

  useEffectWorkspace(() => {
    setPage(0)
  }, [bank, q, topic, src, brush, sort, JSON.stringify(types)])

  const switchBank = (id) => {
    setBank(id)
    setSel(null)
    setTopic(null)
    setBrush(null)
    setQ('')
    setSrc(null)
  }

  const anyFilter = q || topic || src || brush || !types.world || !types.experience || !types.observation
  const clearAll = () => {
    setQ('')
    setTypes({ world: true, experience: true, observation: true })
    setTopic(null)
    setSrc(null)
    setBrush(null)
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
          {view === 'list' && <FactList units={pageUnits} sel={sel} setSel={setSel} footer={pageSafe === pages - 1} />}
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
          {view === 'graph' && (
            <div className="mv-card">
              <div className="mv-empty" style={{ padding: 60 }}>
                ego focus view — coming in the next commit of this task
              </div>
            </div>
          )}
          {view === 'web' &&
            (typeof window.MemV2WebGraph === 'function' ? (
              <window.MemV2WebGraph bank={bank} sel={sel} setSel={setSel} />
            ) : (
              <div className="mv-card">
                <div className="mv-empty" style={{ padding: 60 }}>
                  web view lands in task C5
                </div>
              </div>
            ))}
        </div>
        <div style={{ display: 'flex', flexDirection: 'column', gap: 14, minWidth: 0 }}>
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
            <div className="fline sec-div" style={{ alignItems: 'flex-start' }}>
              <DensityStrip ts={buckets} brush={brush} setBrush={setBrush} />
            </div>
            <AtlasPanel tags={tags} totalFacts={stats?.total_nodes ?? 0} onTag={setTopic} embed />
            <TopicChips tags={tags} topic={topic} setTopic={setTopic} />
          </div>
        </div>
      </div>
    </div>
  )
}

Object.assign(window, { MemV2Workspace: BankWorkspace, MemV2AtlasPanel: AtlasPanel, MemV2FactList: FactList, MemV2DensityStrip: DensityStrip })
