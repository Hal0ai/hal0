// hal0 dashboard — Memory graph explorer (#memory/graph).
//
// Direction A "Lensed" force graph + the MemGraphExplorer wrapper the
// Memory page mounts. Visualizes the Hindsight knowledge graph for one bank
// using the shared engine (memory-graph-engine.jsx) — hand-rolled SVG, d3-force
// via window.__hal0D3Force, house style (no canvas widget).
//
// Two sources:
//   memories — GET /api/memory/banks/{bank}/graph (fact nodes; semantic/
//              temporal/causal links; filter by type/q/limit)
//   entities — GET /api/memory/banks/{bank}/entities/graph (entity
//              co-occurrence; min_count filter)
//
// Live payloads are Cytoscape-style {data:{…}}; window.normalizeGraph converts
// them to the flat {nodes,links,topics} contract the directions consume.
// Live data may carry no causal edges and derived topics — every overlay
// handles empty / zero-edge graphs without crashing.

const { useState, useRef, useMemo, useCallback, useEffect } = React;

const ALL_LENSES = ['semantic', 'temporal', 'causal', 'cooccurrence'];
const FACT_TYPES = ['world', 'experience', 'observation'];

const DIRECTIONS = [
  { id: 'a', label: 'Lensed', sub: 'force graph', icon: 'graph' },
  { id: 'b', label: 'Structured', sub: 'per-type layouts', icon: 'layers' },
  { id: 'c', label: 'Ego', sub: 'focus + context', icon: 'focus' },
];

// ── Direction A: lensed force graph ─────────────────────────────────────────
function GraphLensed({ graph, source, query, width, height, banner }) {
  const svgRef = useRef(null);
  const { t, setT, zoomBy, bind } = window.usePanZoom({ x: 0, y: 0, k: 1 });
  const tRef = useRef(t);
  tRef.current = t;

  const forceOpts = source === 'entities'
    ? { width, height, distance: 96, charge: -300, collide: 22 }
    : { width, height, distance: 82, charge: -260, collide: 19 };
  const { nodes, links, reheat, cool } = window.useForce(graph, forceOpts);

  const [lenses, setLenses] = useState(() => new Set(ALL_LENSES));
  const [typeFilter, setTypeFilter] = useState(null); // world|experience|observation|null
  const [hover, setHover] = useState(null);
  const [selected, setSelected] = useState(null);
  const [ego, setEgo] = useState(null);               // focused node id (2-hop)
  const [pathMode, setPathMode] = useState(false);
  const [pathFrom, setPathFrom] = useState(null);
  const [pathTo, setPathTo] = useState(null);
  const [focusId, setFocusId] = useState(null);        // keyboard-focused node id

  const drag = useMemo(
    () => window.makeNodeDrag(null, () => tRef.current, svgRef, reheat, cool),
    [reheat, cool],
  );

  // Reset overlays when the underlying graph identity changes (bank/source swap).
  useEffect(() => {
    setSelected(null);
    setEgo(null);
    setPathMode(false);
    setPathFrom(null);
    setPathTo(null);
    setHover(null);
    setTypeFilter(null);
  }, [graph]);

  // auto-fit: frame the node bounding box in the stage (latest-fn in a ref).
  const fitRef = useRef(null);
  fitRef.current = () => {
    const ns = nodes.filter((n) => isFinite(n.x) && isFinite(n.y));
    if (!ns.length) return;
    let a = Infinity, b = Infinity, c = -Infinity, d = -Infinity;
    ns.forEach((n) => { a = Math.min(a, n.x); b = Math.min(b, n.y); c = Math.max(c, n.x); d = Math.max(d, n.y); });
    const padX = 70, padTop = 64, padBot = 40, gw = (c - a) || 1, gh = (d - b) || 1;
    const k = Math.min(1.45, Math.max(0.28, Math.min((width - padX * 2) / gw, (height - padTop - padBot) / gh)));
    const cyView = (padTop + (height - padBot)) / 2;
    setT({ k, x: width / 2 - ((a + c) / 2) * k, y: cyView - ((b + d) / 2) * k });
  };
  useEffect(() => {
    if (window.reducedMotion()) {
      const t0 = setTimeout(() => fitRef.current && fitRef.current(), 350);
      return () => clearTimeout(t0);
    }
    const t1 = setTimeout(() => fitRef.current && fitRef.current(), 650);
    const t2 = setTimeout(() => fitRef.current && fitRef.current(), 1500);
    return () => { clearTimeout(t1); clearTimeout(t2); };
  }, [graph, width, height]);
  const fitView = () => fitRef.current && fitRef.current();

  // visible links by lens + dimmed nodes by fact-type filter
  const linkVisible = useCallback((l) => lenses.has(l.linkType), [lenses]);
  const nodeDimmedByType = useCallback(
    (n) => !!typeFilter && n.kind === 'fact' && n.type !== typeFilter,
    [typeFilter],
  );

  // ego neighborhood (2 hops over currently-visible lenses)
  const egoSet = useMemo(() => {
    if (!ego) return null;
    const keep = new Set([ego]);
    let frontier = [ego];
    for (let hop = 0; hop < 2; hop++) {
      const next = [];
      for (const id of frontier) {
        for (const nb of window.neighborsOf(id, links)) {
          if (linkVisible(nb.link) && !keep.has(nb.id)) { keep.add(nb.id); next.push(nb.id); }
        }
      }
      frontier = next;
    }
    return keep;
  }, [ego, links, linkVisible]);

  // path highlight (shortest path over visible lenses)
  const path = useMemo(
    () => (pathFrom && pathTo) ? window.shortestPath(pathFrom, pathTo, links.filter(linkVisible)) : null,
    [pathFrom, pathTo, links, linkVisible],
  );
  const pathNodes = useMemo(() => new Set(path || []), [path]);
  const pathEdgeSet = useMemo(() => window.pathEdges(path), [path]);

  const q = (query || '').toLowerCase();
  const matchesQ = useCallback(
    (n) => !!q && (n.label || n.text || '').toLowerCase().includes(q),
    [q],
  );

  function nodeOpacity(n) {
    if (pathFrom && pathTo) return pathNodes.has(n.id) ? 1 : 0.12;
    if (egoSet) return egoSet.has(n.id) ? 1 : 0.1;
    if (nodeDimmedByType(n)) return 0.14;
    if (q) return matchesQ(n) ? 1 : 0.18;
    return 1;
  }
  function edgeOpacity(l) {
    const s = l.source.id || l.source;
    const tg = l.target.id || l.target;
    if (!linkVisible(l)) return 0;
    if (pathFrom && pathTo) return pathEdgeSet.has([s, tg].sort().join('|')) ? 0.95 : 0.04;
    if (egoSet) return (egoSet.has(s) && egoSet.has(tg)) ? 0.7 : 0.03;
    return 0.5;
  }

  function onNodeClick(n) {
    if (pathMode) {
      if (!pathFrom) setPathFrom(n.id);
      else if (!pathTo && n.id !== pathFrom) setPathTo(n.id);
      else { setPathFrom(n.id); setPathTo(null); }
      return;
    }
    setSelected(n);
    setEgo(null);
  }
  function focusNode(n) {
    setEgo(n.id); setSelected(n); setFocusId(n.id);
    setPathFrom(null); setPathTo(null); setPathMode(false);
    reheat(0.15);
  }
  function startPath(n) {
    setPathMode(true); setPathFrom(n.id); setPathTo(null); setEgo(null);
  }
  function clearOverlays() {
    setEgo(null); setPathMode(false); setPathFrom(null); setPathTo(null);
  }

  // hovercard position as a fraction of the stage (0..1)
  function screenFrac(n) {
    return { x: (n.x * t.k + t.x) / width, y: (n.y * t.k + t.y) / height };
  }

  // keyboard: Enter select · f focus · p path · arrows walk to a neighbor
  function onNodeKeyDown(e, n) {
    if (e.key === 'Enter' || e.key === ' ') { e.preventDefault(); onNodeClick(n); }
    else if (e.key === 'f' || e.key === 'F') { e.preventDefault(); focusNode(n); }
    else if (e.key === 'p' || e.key === 'P') { e.preventDefault(); startPath(n); }
    else if (e.key.startsWith('Arrow')) {
      const nbrs = window.neighborsOf(n.id, links).filter((nb) => linkVisible(nb.link));
      if (!nbrs.length) return;
      e.preventDefault();
      // pick neighbor in the rough direction of the arrow
      const byId = Object.fromEntries(nodes.map((x) => [x.id, x]));
      const want = e.key === 'ArrowRight' ? [1, 0]
        : e.key === 'ArrowLeft' ? [-1, 0]
          : e.key === 'ArrowDown' ? [0, 1] : [0, -1];
      let best = null, bestScore = -Infinity;
      for (const nb of nbrs) {
        const tgt = byId[nb.id];
        if (!tgt) continue;
        const dx = (tgt.x || 0) - (n.x || 0);
        const dy = (tgt.y || 0) - (n.y || 0);
        const len = Math.hypot(dx, dy) || 1;
        const score = (dx / len) * want[0] + (dy / len) * want[1];
        if (score > bestScore) { bestScore = score; best = tgt; }
      }
      if (best) {
        setFocusId(best.id);
        const el = svgRef.current && svgRef.current.querySelector(`[data-node-id="${cssEsc(best.id)}"]`);
        if (el && el.focus) el.focus();
      }
    }
  }

  const showLabel = (n) =>
    hover?.id === n.id || selected?.id === n.id || focusId === n.id || ego === n.id ||
    (egoSet && egoSet.has(n.id)) || pathNodes.has(n.id) || matchesQ(n) ||
    n.kind === 'entity' || nodes.length <= 40;
  const nodeR = (n) => n.kind === 'entity' ? Math.min(18, 7 + Math.sqrt(n.mentionCount || 1) * 2.6) : 7;
  const fillOf = (n) => n.kind === 'entity' ? n.color : (n.color || window.MEM_FACT_COLORS[n.type] || 'var(--info)');

  const counts = useMemo(() => {
    const c = { semantic: 0, temporal: 0, causal: 0, cooccurrence: 0 };
    links.forEach((l) => { c[l.linkType] = (c[l.linkType] || 0) + 1; });
    return c;
  }, [links]);
  const activeLenses = ALL_LENSES.filter((k) => counts[k] > 0);

  return (
    <div className="mg-wrap">
      <div className="mg-stage" style={{ height }}>
        <div className="mg-controls mg-float">
          <div className="mg-lenses">
            {activeLenses.map((k) => {
              const on = lenses.has(k);
              return (
                <button
                  key={k}
                  className={'mg-lens' + (on ? ' on' : '')}
                  style={on ? { '--lc': window.MEM_LINK_COLORS[k] } : {}}
                  onClick={() => setLenses((prev) => { const n = new Set(prev); n.has(k) ? n.delete(k) : n.add(k); return n; })}
                  aria-pressed={on}
                >
                  <i style={{ background: window.MEM_LINK_COLORS[k] }} />{k}<b className="num">{counts[k]}</b>
                </button>
              );
            })}
          </div>
          {source !== 'entities' && (
            <div className="mg-typefilter">
              {FACT_TYPES.map((ty) => (
                <button
                  key={ty}
                  className={'mg-tf' + (typeFilter === ty ? ' on' : '')}
                  onClick={() => setTypeFilter((p) => (p === ty ? null : ty))}
                  aria-pressed={typeFilter === ty}
                >
                  <i style={{ background: window.MEM_FACT_COLORS[ty] }} />{ty}
                </button>
              ))}
            </div>
          )}
          {(ego || (pathFrom && pathTo) || pathMode) && (
            <button className="btn ghost xs" onClick={clearOverlays}><Icon name="close" size={11} /> clear</button>
          )}
          <button
            className={'btn ghost xs' + (pathMode ? ' active' : '')}
            onClick={() => { setPathMode((p) => !p); setPathFrom(null); setPathTo(null); setEgo(null); }}
          >
            <Icon name="path" size={12} /> path
          </button>
        </div>

        <div className="mg-zoom mg-zoomfloat">
          <button className="btn ghost xs" onClick={() => zoomBy(1.3)} aria-label="Zoom in"><Icon name="plus" size={12} /></button>
          <button className="btn ghost xs" onClick={() => zoomBy(1 / 1.3)} aria-label="Zoom out"><Icon name="minus" size={12} /></button>
          <button className="btn ghost xs" onClick={fitView} aria-label="Fit"><Icon name="fit" size={12} /></button>
        </div>

        {banner}

        {pathMode && (
          <div className="mg-pathbar mono">
            <Icon name="path" size={12} />
            {!pathFrom ? 'pick a start node' : !pathTo ? 'pick an end node' : `path · ${path ? path.length - 1 + ' hops' : 'no route'}`}
          </div>
        )}

        <svg
          ref={svgRef}
          className="mg-svg"
          data-testid="mem-graph-svg"
          width={width}
          height={height}
          role="img"
          aria-label="memory knowledge graph"
          {...bind}
          style={{ cursor: 'grab' }}
          onClick={(e) => { if (e.target.tagName === 'svg') setSelected(null); }}
        >
          <window.GraphDefs />
          <g transform={`translate(${t.x},${t.y}) scale(${t.k})`}>
            {links.map((l) => {
              const s = l.source;
              const tg = l.target;
              if (s == null || tg == null || s.x == null || tg.x == null) return null;
              const op = edgeOpacity(l);
              if (op === 0) return null;
              const ci = ALL_LENSES.indexOf(l.linkType);
              const curve = (ci - 1.5) * 0.06;
              const isCausal = l.linkType === 'causal';
              return (
                <path
                  key={l.id}
                  d={window.edgeArc(s.x, s.y, tg.x, tg.y, curve)}
                  fill="none"
                  stroke={window.MEM_LINK_COLORS[l.linkType]}
                  strokeOpacity={op}
                  strokeWidth={Math.max(0.7, Math.min(3, (l.weight || 1) * 1.1)) / Math.sqrt(t.k)}
                  markerEnd={isCausal ? `url(#arr-${l.linkType})` : undefined}
                  style={window.reducedMotion() ? undefined : { transition: 'stroke-opacity .25s var(--ease)' }}
                />
              );
            })}
            {nodes.map((n) => {
              const r = nodeR(n);
              const op = nodeOpacity(n);
              const sel = selected?.id === n.id || ego === n.id;
              const inPath = n.id === pathFrom || n.id === pathTo;
              return (
                <g
                  key={n.id}
                  data-node
                  data-node-id={n.id}
                  tabIndex={0}
                  transform={`translate(${n.x || 0},${n.y || 0})`}
                  style={{ cursor: 'pointer', opacity: op, transition: window.reducedMotion() ? undefined : 'opacity .25s var(--ease)' }}
                  onPointerDown={drag(n)}
                  onPointerEnter={() => setHover(n)}
                  onPointerLeave={() => setHover((h) => (h?.id === n.id ? null : h))}
                  onFocus={() => setFocusId(n.id)}
                  onKeyDown={(e) => onNodeKeyDown(e, n)}
                  onClick={(e) => { e.stopPropagation(); onNodeClick(n); }}
                  onDoubleClick={(e) => { e.stopPropagation(); focusNode(n); }}
                >
                  {(sel || inPath || focusId === n.id) && (
                    <circle r={r + 5} fill="none" stroke={inPath ? 'var(--accent)' : 'var(--fg)'} strokeWidth={1.5 / t.k} strokeOpacity="0.8" />
                  )}
                  <circle
                    r={r}
                    fill={fillOf(n)}
                    fillOpacity={n.kind === 'entity' ? 0.9 : 0.92}
                    stroke={n.__pinned ? 'var(--accent)' : 'var(--bg)'}
                    strokeWidth={(n.__pinned ? 2 : 1.4) / t.k}
                    filter={hover?.id === n.id ? 'url(#nodeglow)' : undefined}
                  />
                  {n.kind === 'fact' && <circle r={r * 0.42} fill="var(--bg-1)" fillOpacity="0.5" />}
                  {n.__pinned && <circle r={1.7 / t.k + 1.2} cy={-r - 3 / t.k} fill="var(--accent)" />}
                  {showLabel(n) && (
                    <text
                      className="mg-nodelabel"
                      y={-r - 5 / t.k}
                      textAnchor="middle"
                      style={{ fontSize: 11 / t.k, fill: sel ? 'var(--fg)' : 'var(--fg-3)' }}
                    >
                      {(n.label || '').slice(0, 26)}
                    </text>
                  )}
                </g>
              );
            })}
          </g>
        </svg>

        {hover && !pathMode && <window.Hovercard node={hover} links={links} source={source} {...screenFrac(hover)} />}

        <div className="mg-legend-float mono">
          {activeLenses.map((k) => (
            <span key={k} className={'mg-lg' + (lenses.has(k) ? '' : ' off')}>
              <i style={{ background: window.MEM_LINK_COLORS[k] }} />{k}
            </span>
          ))}
        </div>
        <div className="mg-help mono">drag node to pin · double-click to focus · scroll to zoom</div>
      </div>

      {selected && !pathMode && (
        <window.NodeDetail
          node={selected}
          graph={{ nodes, links }}
          source={source}
          onClose={() => { setSelected(null); setEgo(null); }}
          onFocus={focusNode}
          onPathFrom={startPath}
        />
      )}
    </div>
  );
}

// tiny CSS.escape fallback for attribute selectors (node ids may contain odd chars)
function cssEsc(s) {
  if (typeof CSS !== 'undefined' && CSS.escape) return CSS.escape(String(s));
  return String(s).replace(/["\\\]\[#.:>+~*^$|=()]/g, '\\$&');
}

// ── Wrapper the Memory page mounts ──────────────────────────────────────────
function MemGraphExplorer() {
  const banksQuery = window.__hal0UseMemoryBanks ? window.__hal0UseMemoryBanks() : { data: null, isLoading: false };
  const banks = banksQuery.data?.banks || [];

  const [bankSel, setBankSel] = useState(() => {
    try { return localStorage.getItem('hal0.mem.bank') || null; } catch { return null; }
  });
  const [source, setSource] = useState('memories'); // memories | entities
  const [direction, setDirection] = useState(() => {
    try { return localStorage.getItem('hal0.mem.dir') || 'a'; } catch { return 'a'; }
  });
  const [typeFilter, setTypeFilter] = useState('');
  const [qDraft, setQDraft] = useState('');
  const [q, setQ] = useState('');

  const stageRef = useRef(null);
  const { width, height } = window.useSize(stageRef);

  // resolve active bank (persisted selection → first bank)
  const bankValid = bankSel && banks.some((b) => b.bank_id === bankSel);
  const bank = (bankValid ? bankSel : banks[0]?.bank_id) || null;

  function chooseBank(id) {
    setBankSel(id);
    try { localStorage.setItem('hal0.mem.bank', id); } catch { /* ignore */ }
  }
  function chooseDir(id) {
    setDirection(id);
    try { localStorage.setItem('hal0.mem.dir', id); } catch { /* ignore */ }
  }
  function commitQ() { setQ(qDraft.trim()); }

  // fetch both sources; B/C want the fact + entity graphs together.
  const factQuery = window.__hal0UseBankGraph
    ? window.__hal0UseBankGraph(bank, { type: typeFilter || undefined, q: q || undefined, limit: 300 })
    : { data: null, isLoading: false };
  const entQuery = window.__hal0UseEntityGraph
    ? window.__hal0UseEntityGraph(bank, { min_count: 1, limit: 500 })
    : { data: null, isLoading: false };

  const factGraph = useMemo(() => window.normalizeGraph(factQuery.data, 'memories'), [factQuery.data]);
  const entityGraph = useMemo(() => window.normalizeGraph(entQuery.data, 'entities'), [entQuery.data]);

  // active graph drives the meta line + scale banner
  const activeSource = direction === 'a' ? source : 'memories';
  const activeGraph = activeSource === 'entities' ? entityGraph : factGraph;
  const activeQuery = activeSource === 'entities' ? entQuery : factQuery;
  const loading = activeQuery.isLoading;
  const payload = activeQuery.data;

  const nodeCount = activeGraph.nodes.length;
  const edgeCount = activeGraph.links.length;
  const big = nodeCount > 240;

  const banner = big ? (
    <div className="mg-scalewarn mono">
      <Icon name="layers" size={12} /> {nodeCount} nodes · large bank — use <b>Structured</b> or <b>Ego</b> for clarity at this scale
    </div>
  ) : null;

  // ── empty / loading shells ────────────────────────────────────────────────
  if (!bank) {
    return (
      <div className="mem-graph" data-testid="mem-graph-explorer">
        <div className="empty mono mem-graph-empty">
          {banksQuery.isLoading ? 'loading banks…' : 'No memory banks available.'}
        </div>
      </div>
    );
  }

  const totals =
    (payload?.total_units != null ? ` · ${payload.total_units} units` : '') +
    (payload?.total_entities != null ? ` · ${payload.total_entities} entities` : '');

  return (
    <div className="mem-graph" data-testid="mem-graph-explorer">
      <div className="mg-toolbar">
        {direction === 'a' && (
          <div className="mg-source">
            <button
              className={'mg-seg' + (source === 'memories' ? ' on' : '')}
              onClick={() => setSource('memories')}
              data-testid="mem-graph-source-memories"
            >
              Memories
            </button>
            <button
              className={'mg-seg' + (source === 'entities' ? ' on' : '')}
              onClick={() => setSource('entities')}
              data-testid="mem-graph-source-entities"
            >
              Entities
            </button>
          </div>
        )}

        <label className="mg-field">
          <span className="mono">bank</span>
          <select
            className="input mono"
            value={bank}
            onChange={(e) => chooseBank(e.target.value)}
            data-testid="mem-graph-bank"
            aria-label="Bank"
          >
            {banks.map((b) => (
              <option key={b.bank_id} value={b.bank_id}>
                {b.bank_id}{b.fact_count != null ? ` · ${b.fact_count} facts` : ''}
              </option>
            ))}
          </select>
        </label>

        {direction === 'a' && source === 'memories' && (
          <label className="mg-field">
            <span className="mono">type</span>
            <select
              className="input mono"
              value={typeFilter}
              onChange={(e) => setTypeFilter(e.target.value)}
              data-testid="mem-graph-type"
              aria-label="Fact type"
            >
              <option value="">all types</option>
              {FACT_TYPES.map((ty) => <option key={ty} value={ty}>{ty}</option>)}
            </select>
          </label>
        )}

        {(direction !== 'a' || source === 'memories') && (
          <label className="mg-field mg-search">
            <Icon name="search" size={13} />
            <input
              className="input mono"
              value={qDraft}
              placeholder="search facts…"
              onChange={(e) => { setQDraft(e.target.value); if (e.target.value === '') setQ(''); }}
              onKeyDown={(e) => { if (e.key === 'Enter') commitQ(); }}
              onBlur={commitQ}
              data-testid="mem-graph-q"
            />
          </label>
        )}

        <div className="mg-spring" />

        <div className="mg-dirswitch">
          {DIRECTIONS.map((d) => (
            <button
              key={d.id}
              className={'mg-dir' + (direction === d.id ? ' on' : '')}
              onClick={() => chooseDir(d.id)}
              title={d.label + ' — ' + d.sub}
              aria-pressed={direction === d.id}
            >
              <Icon name={d.icon} size={13} /><span>{d.label}</span>
            </button>
          ))}
        </div>
      </div>

      <div className="mg-meta mono" data-testid="mem-graph-meta">
        {loading
          ? 'loading graph…'
          : `${nodeCount} nodes · ${edgeCount} edges${totals}`}
        <span className="mg-meta-dir">direction <b>{DIRECTIONS.find((d) => d.id === direction).label.toLowerCase()}</b></span>
      </div>

      <div ref={stageRef} className="mg-host">
        {!loading && nodeCount === 0 ? (
          <div className="empty mono mem-graph-empty">No graph data for this bank/filter.</div>
        ) : direction === 'a' ? (
          <GraphLensed
            graph={source === 'entities' ? entityGraph : factGraph}
            source={source}
            query={q}
            width={width}
            height={height}
            banner={banner}
          />
        ) : direction === 'b' ? (
          window.GraphStructured ? (
            <window.GraphStructured graph={factGraph} entityGraph={entityGraph} query={q} width={width} height={height} banner={banner} />
          ) : (
            <div className="empty mono mem-graph-empty">loading…</div>
          )
        ) : (
          window.GraphEgo ? (
            <window.GraphEgo graph={factGraph} query={q} width={width} height={height} banner={banner} />
          ) : (
            <div className="empty mono mem-graph-empty">loading…</div>
          )
        )}
      </div>
    </div>
  );
}

Object.assign(window, { MemGraphExplorer, GraphLensed });
