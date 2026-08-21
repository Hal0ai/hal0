// hal0 memory v2 (Bank workspace UI, task C5) — the "web" view: full
// force-graph, salience-capped, with link-type lenses.
//
// Ported from the design handoff's prototype/web-graph.jsx — the animated
// d3-force sim (alphaTarget drag-reheat, gentle "jelly" settle-in) is kept
// as-is; d3-force comes ONLY from the existing window.__hal0D3Force bridge
// (memory-hook-bridge.ts) — this file never imports d3 itself, per the
// window-globals contract (no ES imports across dash/*.jsx).
//
// Data: window.__hal0UseBankGraph(bank, {type, q}) for the node/edge slab,
// capped client-side at 120 nodes by salience (degree×type_weight — same
// math as the backend's degree_by_node: sum of MemV2.LINK_WEIGHT over each
// node's incident edges). Real memory-graph link types verified live
// (Playwright + curl against a production bank, 2026-08-21):
// temporal/semantic/entity/caused_by — CORRECTION of the prior claim that
// causal-family links "never fire here": `caused_by` is the real wire name
// for that relationship and does appear in real payloads (see
// memory-v2-shared.jsx's LINK_COLORS/LINK_LABEL/LINK_WEIGHT). `causal` and
// `cooccurrence` (the prototype's own spellings/extra type) still haven't
// been seen in any live payload so far. The lens button row is derived from
// the types actually present in the loaded slab, not a fixed set, so no
// dead lens buttons render regardless of which of these actually show up.
//
// Window-globals contract: reads window.MemV2 (C1) at render time;
// publishes window.MemV2WebGraph the same way.

const { useState: useStateWeb, useEffect: useEffectWeb, useRef: useRefWeb } = React

const WEB_CAP = 120

function _nidWeb(n) {
  return n.data.id
}

// Salience = sum of MemV2.LINK_WEIGHT[linkType] over every edge incident to
// the node (both endpoints get credited) — the same "degree×type_weight"
// math the backend's degree_by_node computes. Pure + exported so it's
// vitest-testable without mounting anything.
export function computeSalience(nodes, edges, linkWeight) {
  const salience = new Map(nodes.map((n) => [_nidWeb(n), 0]))
  edges.forEach((e) => {
    // Real payloads emit `type`; the mock (and v1's normalizer) said `linkType`.
    const w = linkWeight[e.data.linkType ?? e.data.type] ?? 1
    const { source, target } = e.data
    if (salience.has(source)) salience.set(source, salience.get(source) + w)
    if (salience.has(target)) salience.set(target, salience.get(target) + w)
  })
  return salience
}

// Caps `nodes` to the top `cap` by salience (ties broken by original order,
// for determinism). Returns { shown, salience } — `salience` covers every
// input node (not just the shown slice), so callers can still look up a
// salience value for a node that got capped out if needed.
export function capNodesBySalience(nodes, edges, linkWeight, cap = WEB_CAP) {
  const salience = computeSalience(nodes, edges, linkWeight)
  if (nodes.length <= cap) return { shown: nodes, salience, capped: false }
  const ranked = [...nodes]
    .map((n, i) => ({ n, i, s: salience.get(_nidWeb(n)) ?? 0 }))
    .sort((a, b) => b.s - a.s || a.i - b.i)
    .slice(0, cap)
    .map((r) => r.n)
  return { shown: ranked, salience, capped: true }
}

function WebGraph({ bank, sel, setSel, filters }) {
  const { FACT_COLORS, LINK_COLORS, LINK_LABEL, LINK_WEIGHT, TOPIC_COLORS } = window.MemV2
  const d3 = window.__hal0D3Force
  const useBankGraph = window.__hal0UseBankGraph

  const W = 860,
    H = 620

  // `type` is deliberately NEVER forwarded to /graph (final-review I1):
  // `/graph` is a verbatim Hindsight passthrough — upstream `type` is
  // single-value exact-equality, so a comma-joined multi-type value (1 or 2
  // of the 3 toggles active) matches nothing and silently returns an empty
  // graph. Worse, even a *single* valid type filtered server-side drops any
  // edge whose other endpoint falls outside that type — the same
  // cross-type-edge understatement A3b fixed for `bank_units` — which would
  // also skew this view's salience cap. Filtering client-side via
  // `matchesFilters` below (dimming, not removing, so the true edge set and
  // salience ranking stay intact) avoids both problems in one fix rather
  // than conditionally forwarding only single values. The rest of `filters`
  // (tags/from/to/documentId) has no server-side equivalent on this route
  // either, so it was already applied the same way — this just adds `type`
  // to that existing client-side dim instead of a third, redundant path.
  const graphQuery = useBankGraph ? useBankGraph(bank, { q: filters?.q, limit: 500 }) : { data: null }
  const allNodes = graphQuery.data?.nodes || []
  const allEdges = graphQuery.data?.edges || []

  const { shown, capped } = capNodesBySalience(allNodes, allEdges, LINK_WEIGHT, WEB_CAP)
  const ids = new Set(shown.map(_nidWeb))
  const links = []
  const seen = new Set()
  allEdges.forEach((e) => {
    const { source, target } = e.data
    const linkType = e.data.linkType ?? e.data.type
    if (!ids.has(source) || !ids.has(target)) return
    const key = [source, target].sort().join('|') + '|' + linkType
    if (seen.has(key)) return
    seen.add(key)
    links.push({ s: source, d: target, t: linkType })
  })
  const degree = {}
  links.forEach((l) => {
    degree[l.s] = (degree[l.s] || 0) + 1
    degree[l.d] = (degree[l.d] || 0) + 1
  })

  // Lens set is derived from the link types actually present in the loaded
  // slab — never a fixed causal/cooccurrence/temporal/semantic/entity list,
  // so a bank with only temporal/semantic/entity links (the real shape)
  // never shows a dead causal/cooccurrence lens button.
  const presentTypes = [...new Set(links.map((l) => l.t))]
  const [lens, setLens] = useStateWeb(() => Object.fromEntries(presentTypes.map((t) => [t, true])))
  useEffectWeb(() => {
    setLens((prev) => {
      const next = { ...prev }
      let changed = false
      presentTypes.forEach((t) => {
        if (!(t in next)) {
          next[t] = true
          changed = true
        }
      })
      return changed ? next : prev
    })
  }, [presentTypes.join(',')])

  // filters.type is unitsParams' comma-joined type string (undefined when
  // all 3 fact-type toggles are active, i.e. "no filter") — split once per
  // render rather than per node.
  const typeSet = filters?.type ? new Set(filters.type.split(',')) : null

  // Client-side dim for every filter dimension the /graph endpoint either
  // can't take (tags — node.data.topic; from/to — node.data.date) or that
  // we deliberately stopped forwarding (type — see the graphQuery comment
  // above). documentId has no equivalent field on a graph node at all — a
  // fact's source document isn't part of this payload, so that one filter
  // dimension simply isn't enforceable here (documented, not silently
  // ignored).
  const matchesFilters = (n) => {
    const d = n.data
    if (typeSet && !typeSet.has(d.type)) return false
    if (filters?.tags && filters.tags.length && !filters.tags.includes(d.topic)) return false
    if (filters?.from && d.date && new Date(d.date).getTime() < new Date(filters.from).getTime()) return false
    if (filters?.to && d.date && new Date(d.date).getTime() > new Date(filters.to).getTime()) return false
    return true
  }

  const [tf, setTf] = useStateWeb({ k: 1, x: 0, y: 0 })
  const [hover, setHover] = useStateWeb(null)
  const [pos, setPos] = useStateWeb({})
  const dragRef = useRefWeb(null)
  const svgRef = useRefWeb(null)
  const simRef = useRefWeb(null)

  const topics = [...new Set(shown.map((n) => n.data.topic).filter(Boolean))]
  const key = shown.map(_nidWeb).join(',')

  useEffectWeb(() => {
    if (!shown.length || !d3 || !d3.forceSimulation) return
    const centers = {}
    topics.forEach((t, i) => {
      const a = (i / Math.max(1, topics.length)) * 2 * Math.PI - Math.PI / 2
      centers[t] = { x: W / 2 + 240 * Math.cos(a), y: H / 2 + 175 * Math.sin(a) }
    })
    const ns = shown.map((n) => ({ id: _nidWeb(n), topic: n.data.topic }))
    const ls = links.map((l) => ({ source: l.s, target: l.d, t: l.t }))
    // caused_by is the real wire spelling of the causal relationship
    // (post-smoke correction, see the file header) — same pull strength.
    const strengthOf = (t) =>
      t === 'causal' || t === 'caused_by' ? 0.9 : t === 'cooccurrence' ? 0.25 : t === 'temporal' ? 0.6 : 0.5
    const sim = d3
      .forceSimulation(ns)
      .force('link', d3.forceLink(ls).id((n) => n.id).distance(46).strength((l) => strengthOf(l.t)))
      .force('charge', d3.forceManyBody().strength(-120))
      .force('collide', d3.forceCollide().radius((n) => 10 + Math.sqrt(degree[n.id] || 0) * 3))
      .force('x', d3.forceX((n) => (centers[n.topic] || { x: W / 2 }).x).strength(0.18))
      .force('y', d3.forceY((n) => (centers[n.topic] || { y: H / 2 }).y).strength(0.18))
      .velocityDecay(0.42)
      .alphaDecay(0.035)
      .stop()
    for (let i = 0; i < 140; i++) sim.tick() // settle roughly off-screen first
    const byId = {}
    ns.forEach((n) => (byId[n.id] = n))
    const push = () => {
      const p = {}
      ns.forEach((n) => {
        n.x = Math.max(24, Math.min(W - 24, n.x))
        n.y = Math.max(24, Math.min(H - 24, n.y))
        p[n.id] = { x: n.x, y: n.y }
      })
      setPos(p)
    }
    push()
    sim.on('tick', push)
    sim.alpha(0.25).restart() // gentle visible settle-in
    simRef.current = { sim, byId }
    setTf({ k: 1, x: 0, y: 0 })
    return () => {
      sim.stop()
      simRef.current = null
    }
  }, [key, bank])

  const P = (id) => pos[id] || { x: W / 2, y: H / 2 }
  const nbr = (id) => links.some((l) => lens[l.t] && ((l.s === hover && l.d === id) || (l.d === hover && l.s === id)))
  const rOf = (id) => 4 + Math.sqrt(degree[id] || 0) * 2.4

  const toLocal = (e) => {
    const r = svgRef.current.getBoundingClientRect()
    return { x: ((e.clientX - r.left) / r.width) * W, y: ((e.clientY - r.top) / r.height) * H }
  }
  const onWheel = (e) => {
    e.preventDefault()
    const m = toLocal(e)
    const k2 = Math.max(0.5, Math.min(5, tf.k * (e.deltaY < 0 ? 1.18 : 1 / 1.18)))
    setTf({ k: k2, x: m.x - ((m.x - tf.x) / tf.k) * k2, y: m.y - ((m.y - tf.y) / tf.k) * k2 })
  }
  const onDown = (e, id) => {
    e.stopPropagation()
    const m = toLocal(e)
    dragRef.current = id ? { id, m } : { pan: true, m, tf0: { ...tf } }
    if (id && simRef.current) {
      const n = simRef.current.byId[id]
      if (n) {
        n.fx = n.x
        n.fy = n.y
        simRef.current.sim.alphaTarget(0.18).restart()
      }
    }
  }
  const onMove = (e) => {
    const d = dragRef.current
    if (!d) return
    const m = toLocal(e)
    if (d.pan) {
      setTf({ k: d.tf0.k, x: d.tf0.x + (m.x - d.m.x), y: d.tf0.y + (m.y - d.m.y) })
    } else {
      d.moved = true
      const n = simRef.current && simRef.current.byId[d.id]
      if (n) {
        n.fx = (m.x - tf.x) / tf.k
        n.fy = (m.y - tf.y) / tf.k
      } else {
        setPos((p) => ({ ...p, [d.id]: { x: (m.x - tf.x) / tf.k, y: (m.y - tf.y) / tf.k } }))
      }
    }
  }
  const onUp = (e, id) => {
    const d = dragRef.current
    dragRef.current = null
    if (simRef.current) {
      simRef.current.sim.alphaTarget(0)
      if (d && d.id && !d.moved) {
        const n = simRef.current.byId[d.id]
        if (n) {
          n.fx = null
          n.fy = null
        }
      }
    }
    if (d && d.id && !d.moved && id) setSel(id)
  }

  const cents = topics
    .map((t, i) => {
      const mine = shown.filter((n) => n.data.topic === t)
      if (mine.length < 2) return null
      const cx = mine.reduce((s, n) => s + P(_nidWeb(n)).x, 0) / mine.length
      const cy = mine.reduce((s, n) => s + P(_nidWeb(n)).y, 0) / mine.length
      return { label: t, x: cx, y: cy, c: TOPIC_COLORS[i % TOPIC_COLORS.length] }
    })
    .filter(Boolean)

  const visibleLinkCount = links.filter((l) => lens[l.t]).length

  return (
    <div className="mv-card mv-web" data-testid="mv-web">
      <div className="hd">
        <span className="mv-eyebrow">Web</span>
        <span className="num" style={{ font: '400 10.5px var(--jbm)', color: 'var(--fg-4)' }}>
          {shown.length} facts · {visibleLinkCount} links
          {capped && <span style={{ color: 'var(--warn)' }}> · top {WEB_CAP} by salience</span>}
        </span>
        <span className="sp" />
        {presentTypes.map((t) => (
          <button
            key={t}
            className={'mv-lens' + (lens[t] ? ' on' : '')}
            data-testid={`mv-web-lens-${t}`}
            style={{ '--lc': LINK_COLORS[t] || 'var(--fg-4)' }}
            onClick={() => setLens({ ...lens, [t]: !lens[t] })}
          >
            <i />
            {LINK_LABEL[t] || t}
          </button>
        ))}
      </div>
      <svg
        ref={svgRef}
        viewBox={`0 0 ${W} ${H}`}
        style={{ cursor: dragRef.current ? 'grabbing' : 'grab', touchAction: 'none' }}
        onWheel={onWheel}
        onPointerDown={(e) => onDown(e, null)}
        onPointerMove={onMove}
        onPointerUp={(e) => onUp(e, null)}
        onPointerLeave={() => (dragRef.current = null)}
      >
        <g transform={`translate(${tf.x},${tf.y}) scale(${tf.k})`}>
          {cents.map((c, i) => (
            <text key={i} x={c.x} y={c.y} textAnchor="middle" className="hull" style={{ fill: c.c }}>
              {String(c.label).toUpperCase()}
            </text>
          ))}
          {links.map((l, i) => {
            if (!lens[l.t]) return null
            const a = P(l.s),
              b = P(l.d)
            const lit = !hover || l.s === hover || l.d === hover
            return (
              <line
                key={i}
                x1={a.x}
                y1={a.y}
                x2={b.x}
                y2={b.y}
                stroke={LINK_COLORS[l.t] || 'var(--fg-4)'}
                strokeWidth={1.1 / tf.k}
                opacity={lit ? (l.t === 'cooccurrence' ? 0.3 : 0.55) : 0.06}
              />
            )
          })}
          {shown.map((n) => {
            const id = _nidWeb(n)
            const p = P(id),
              r = rOf(id)
            const filterOk = matchesFilters(n)
            const lit = !hover || hover === id || nbr(id)
            const opacity = !filterOk ? 0.12 : lit ? 1 : 0.22
            const showLbl = hover === id || sel === id || tf.k > 1.7
            const label = n.data.label || id
            return (
              <g
                key={id}
                data-testid={`mv-web-node-${id}`}
                opacity={opacity}
                onPointerDown={(e) => onDown(e, id)}
                onPointerUp={(e) => {
                  e.stopPropagation()
                  onUp(e, id)
                }}
                onPointerEnter={() => setHover(id)}
                onPointerLeave={() => setHover(null)}
                style={{ cursor: 'pointer' }}
              >
                <circle
                  cx={p.x}
                  cy={p.y}
                  r={r}
                  fill={FACT_COLORS[n.data.type] || 'var(--fg-4)'}
                  fillOpacity="0.85"
                  stroke={sel === id ? 'var(--accent)' : 'var(--bg)'}
                  strokeWidth={sel === id ? 2.5 / tf.k : 1 / tf.k}
                />
                {showLbl && (
                  <text
                    x={p.x}
                    y={p.y - r - 5 / tf.k}
                    textAnchor="middle"
                    className={sel === id ? 'sel' : ''}
                    style={{ fontSize: 10 / Math.max(1, tf.k * 0.85) }}
                  >
                    {String(label).length > 26 ? String(label).slice(0, 25) + '…' : label}
                  </text>
                )}
              </g>
            )
          })}
        </g>
      </svg>
      <div className="mv-lg-legend" style={{ padding: '8px 14px 10px', borderTop: '1px solid var(--line-soft,#1C1C1C)' }}>
        {Object.keys(FACT_COLORS).map((t) => (
          <span key={t}>
            <i style={{ background: FACT_COLORS[t], width: 7, height: 7, borderRadius: '50%' }} />
            {t}
          </span>
        ))}
        <span style={{ flex: 1 }} />
        <span>scroll to zoom · drag canvas to pan · drag nodes to pin · click to inspect</span>
        <button className="mv-btn" data-testid="mv-web-zoom" style={{ padding: '1px 8px', fontSize: 10 }} onClick={() => setTf({ k: 1, x: 0, y: 0 })}>
          fit
        </button>
      </div>
    </div>
  )
}

// Guarded so importing this module for its pure named exports
// (computeSalience/capNodesBySalience) under vitest's node test
// environment — no `window` global — doesn't throw.
if (typeof window !== 'undefined') {
  Object.assign(window, { MemV2WebGraph: WebGraph })
}
