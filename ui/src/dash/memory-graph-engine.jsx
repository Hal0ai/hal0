// hal0 dashboard — Memory graph engine (shared kernel for #memory/graph).
//
// Data-agnostic view layer for the three graph directions (A lensed force,
// B structured lenses, C ego explorer). House style: hand-rolled SVG, no
// canvas widget. Ported from the memory_overhaul prototype's graph-engine.jsx
// and adapted for the live app:
//   - d3-force arrives via window.__hal0D3Force (memory-hook-bridge.ts), not a
//     global `d3` CDN object.
//   - normalizeGraph() converts the live Cytoscape {data:{…}} payloads from
//     useBankGraph/useEntityGraph into the flat node/link shape the directions
//     consume, and DERIVES the `topic` field (Hindsight emits none) from
//     connected components over semantic edges.
//   - Color maps / fmt / TOPICS palette live here (no mock mem-data module).
//
// Registers on window so the no-ES-imports dash/*.jsx directions find it.

const {
  useState: useS,
  useRef: useR,
  useEffect: useE,
  useMemo: useM,
  useCallback: useCb,
  useReducer: useRd,
} = React;

// ── palettes / tokens (design-system vars; Okabe–Ito where categorical) ─────
const MEM_LINK_COLORS = {
  semantic: 'var(--info)',        // related meaning
  temporal: 'var(--warn)',        // happened near in time
  causal: 'var(--err)',           // led to (directed)
  cooccurrence: 'var(--accent)',  // mentioned together
};
const MEM_LINK_LABEL = {
  semantic: 'related meaning',
  temporal: 'near in time',
  causal: 'led to',
  cooccurrence: 'mentioned together',
};
const MEM_FACT_COLORS = { world: 'var(--info)', experience: 'var(--accent)', observation: '#6fcf97' };
const MEM_FACT_DESC = {
  world: 'stable fact / config',
  experience: 'episodic — something that happened',
  observation: 'a noticed pattern',
};
// Okabe–Ito colourblind-safe palette for derived topic clusters + entity kinds.
const MG_PALETTE = ['#5B9BD5', '#E69F00', '#009E73', '#CC79A7', '#F0E442', '#D55E00', '#56B4E9', '#B39DDB'];
// Static topic fallback (used only if a derived topic id collides with one of
// the prototype's narrative topic names; harmless otherwise).
const TOPICS = {};

function reducedMotion() {
  return typeof window !== 'undefined' && window.matchMedia
    ? window.matchMedia('(prefers-reduced-motion: reduce)').matches
    : false;
}

function fmtMemDate(d, withTime) {
  if (!d) return '';
  const dt = new Date(d);
  if (isNaN(+dt)) return String(d);
  const o = withTime
    ? { month: 'short', day: 'numeric', hour: '2-digit', minute: '2-digit' }
    : { month: 'short', day: 'numeric' };
  return dt.toLocaleString(undefined, o);
}

// ── live payload → normalized graph ─────────────────────────────────────────
// payload: Cytoscape-style { nodes:[{data}], edges:[{data}], total_* }.
// source: 'memories' (fact graph) | 'entities' (co-occurrence graph).
function normalizeGraph(payload, source) {
  const rawNodes = (payload?.nodes || []).map((n) => ({ ...(n && n.data ? n.data : n) }));
  const rawEdges = (payload?.edges || []).map((e) => ({ ...(e && e.data ? e.data : e) }));
  const ids = new Set(rawNodes.map((n) => n.id));
  const links = rawEdges
    .map((e) => {
      const s = e.source != null ? e.source : e.from;
      const t = e.target != null ? e.target : e.to;
      return {
        id: e.id || `${e.linkType || e.type || 'link'}:${s}>${t}`,
        source: s,
        target: t,
        linkType: e.linkType || e.type || 'semantic',
        weight: e.weight != null ? e.weight : 1,
      };
    })
    .filter((l) => l.source !== l.target && ids.has(l.source) && ids.has(l.target));

  if (source === 'entities') {
    const kinds = [];
    const nodes = rawNodes.map((n) => {
      const entKind = n.entKind || n.kind || n.type || 'entity';
      if (!kinds.includes(entKind)) kinds.push(entKind);
      return {
        ...n,
        kind: 'entity',
        entKind,
        label: n.label || n.id,
        mentionCount: n.mentionCount != null ? n.mentionCount : (n.mention_count || 0),
        color: n.color || MG_PALETTE[kinds.indexOf(entKind) % MG_PALETTE.length],
      };
    });
    return { nodes, links, topics: {} };
  }

  // facts: derive topic clusters from connected components over semantic edges.
  const semAdj = {};
  for (const l of links) {
    if (l.linkType !== 'semantic') continue;
    (semAdj[l.source] = semAdj[l.source] || []).push(l.target);
    (semAdj[l.target] = semAdj[l.target] || []).push(l.source);
  }
  const topicOf = {};
  let comp = 0;
  for (const n of rawNodes) {
    if (n.id in topicOf) continue;
    if (!semAdj[n.id]) continue; // isolated — handled below
    const id = 'topic-' + comp++;
    const q = [n.id];
    topicOf[n.id] = id;
    while (q.length) {
      const cur = q.shift();
      for (const nb of semAdj[cur] || []) {
        if (!(nb in topicOf)) {
          topicOf[nb] = id;
          q.push(nb);
        }
      }
    }
  }
  const topics = {};
  let ci = 0;
  const colorFor = (tid) => {
    if (!topics[tid]) {
      topics[tid] = { label: tid.replace('topic-', 'cluster '), color: MG_PALETTE[ci++ % MG_PALETTE.length] };
    }
    return topics[tid].color;
  };

  const coerceEnts = (n) => {
    if (Array.isArray(n.ents)) return n.ents;
    if (Array.isArray(n.entities)) return n.entities;
    if (typeof n.entities === 'string') return n.entities.split(/,\s*/).filter(Boolean);
    return [];
  };

  const nodes = rawNodes.map((n) => {
    const type = n.type || 'world';
    const date = n.date || n.occurred_start || n.mentioned_at || null;
    // node with no semantic edges → bucket by fact type so B still clusters it.
    const topic = topicOf[n.id] || 'type-' + type;
    const tColor = topicOf[n.id] ? colorFor(topic) : (MEM_FACT_COLORS[type] || 'var(--info)');
    if (!topics[topic]) topics[topic] = { label: topicOf[n.id] ? topic.replace('topic-', 'cluster ') : type, color: tColor };
    return {
      ...n,
      kind: 'fact',
      type,
      date,
      t: date ? +new Date(date) : 0,
      ents: coerceEnts(n),
      topic,
      topicLabel: topics[topic].label,
      topicColor: topics[topic].color,
      color: n.color || topics[topic].color,
      label: n.label || (n.text ? String(n.text).slice(0, 48) : n.id),
      text: n.text || n.label || '',
    };
  });
  return { nodes, links, topics };
}

// ── helpers ─────────────────────────────────────────────────────────────────
function neighborsOf(id, links) {
  const out = [];
  for (const l of links) {
    const s = l.source.id || l.source;
    const t = l.target.id || l.target;
    if (s === id) out.push({ id: t, link: l, dir: 'out' });
    else if (t === id) out.push({ id: s, link: l, dir: 'in' });
  }
  return out;
}
function degreeByType(id, links) {
  const d = { semantic: 0, temporal: 0, causal: 0, cooccurrence: 0 };
  for (const l of links) {
    const s = l.source.id || l.source;
    const t = l.target.id || l.target;
    if (s === id || t === id) d[l.linkType] = (d[l.linkType] || 0) + 1;
  }
  return d;
}
function shortestPath(a, b, links) {
  if (a === b) return [a];
  const adj = {};
  for (const l of links) {
    const s = l.source.id || l.source;
    const t = l.target.id || l.target;
    (adj[s] = adj[s] || []).push(t);
    (adj[t] = adj[t] || []).push(s);
  }
  const q = [a];
  const prev = { [a]: null };
  while (q.length) {
    const cur = q.shift();
    if (cur === b) break;
    for (const to of adj[cur] || []) if (!(to in prev)) { prev[to] = cur; q.push(to); }
  }
  if (!(b in prev)) return null;
  const path = [];
  let c = b;
  while (c != null) { path.unshift(c); c = prev[c]; }
  return path;
}
function pathEdges(path) {
  const set = new Set();
  if (!path) return set;
  for (let i = 1; i < path.length; i++) set.add([path[i - 1], path[i]].sort().join('|'));
  return set;
}
function edgeArc(x1, y1, x2, y2, curve = 0.12) {
  const mx = (x1 + x2) / 2;
  const my = (y1 + y2) / 2;
  const dx = x2 - x1;
  const dy = y2 - y1;
  const nx = -dy;
  const ny = dx;
  const cx = mx + nx * curve;
  const cy = my + ny * curve;
  return `M${x1},${y1} Q${cx},${cy} ${x2},${y2}`;
}

// ── live force layout (cools to rest, then sits still) ──────────────────────
function useForce(graph, opts) {
  const { width, height, distance = 64, charge = -180, collide = 16, center = true } = opts || {};
  const d3 = typeof window !== 'undefined' ? window.__hal0D3Force : null;
  const simRef = useR(null);
  const nodesRef = useR([]);
  const linksRef = useR([]);
  const [, bump] = useRd((x) => (x + 1) % 1e6, 0);

  useE(() => {
    const nodes = graph.nodes.map((n) => ({ ...n }));
    const idset = new Set(nodes.map((n) => n.id));
    const links = graph.links
      .filter((l) => idset.has(l.source.id || l.source) && idset.has(l.target.id || l.target))
      .map((l) => ({ ...l, source: l.source.id || l.source, target: l.target.id || l.target }));
    nodesRef.current = nodes;
    linksRef.current = links;
    if (!d3 || !nodes.length) { bump(); return; }
    const sim = d3
      .forceSimulation(nodes)
      .force('link', d3.forceLink(links).id((d) => d.id).distance(distance).strength(0.35))
      .force('charge', d3.forceManyBody().strength(charge).distanceMax(420))
      .force('collide', d3.forceCollide(collide))
      .force('x', d3.forceX(width / 2).strength(0.05))
      .force('y', d3.forceY(height / 2).strength(0.06));
    if (center) sim.force('center', d3.forceCenter(width / 2, height / 2));
    sim.alpha(1).alphaDecay(0.028).on('tick', bump);
    simRef.current = sim;
    return () => sim.stop();
    // eslint-disable-next-line
  }, [graph]);

  const reheat = useCb((a = 0.4) => { const s = simRef.current; if (s) s.alphaTarget(a).restart(); }, []);
  const cool = useCb(() => { const s = simRef.current; if (s) s.alphaTarget(0); }, []);

  useE(() => {
    const s = simRef.current;
    if (!s || !d3) return;
    if (center) s.force('center', d3.forceCenter(width / 2, height / 2));
    const fx = s.force('x');
    const fy = s.force('y');
    if (fx) fx.x(width / 2);
    if (fy) fy.y(height / 2);
    s.alpha(Math.max(s.alpha(), 0.4)).restart();
    // eslint-disable-next-line
  }, [width, height]);

  return { nodes: nodesRef.current, links: linksRef.current, sim: simRef, reheat, cool, bump };
}

// ── pan / zoom (cursor-anchored wheel zoom + drag-pan) ──────────────────────
function usePanZoom(initial) {
  const [t, setT] = useS(initial || { x: 0, y: 0, k: 1 });
  const dragRef = useR(null);
  const onWheel = useCb((e) => {
    e.preventDefault();
    const rect = e.currentTarget.getBoundingClientRect();
    const px = e.clientX - rect.left;
    const py = e.clientY - rect.top;
    setT((prev) => {
      const factor = Math.exp(-e.deltaY * 0.0014);
      const k = Math.min(4, Math.max(0.25, prev.k * factor));
      const kr = k / prev.k;
      return { k, x: px - (px - prev.x) * kr, y: py - (py - prev.y) * kr };
    });
  }, []);
  const onPointerDown = useCb((e) => {
    if (e.target.closest('[data-node]')) return; // node drag handles itself
    dragRef.current = { sx: e.clientX, sy: e.clientY, ox: 0, oy: 0, moved: false };
    setT((p) => { dragRef.current.ox = p.x; dragRef.current.oy = p.y; return p; });
    e.currentTarget.setPointerCapture(e.pointerId);
  }, []);
  const onPointerMove = useCb((e) => {
    const d = dragRef.current;
    if (!d) return;
    d.moved = true;
    setT((p) => ({ ...p, x: d.ox + (e.clientX - d.sx), y: d.oy + (e.clientY - d.sy) }));
  }, []);
  const onPointerUp = useCb(() => { dragRef.current = null; }, []);
  const reset = useCb(() => setT(initial || { x: 0, y: 0, k: 1 }), [initial]);
  const zoomBy = useCb((f) => setT((p) => ({ ...p, k: Math.min(4, Math.max(0.25, p.k * f)) })), []);
  return { t, setT, reset, zoomBy, bind: { onWheel, onPointerDown, onPointerMove, onPointerUp, onPointerLeave: onPointerUp } };
}

// ── node drag: screen → world via current transform, pins fx/fy ─────────────
function makeNodeDrag(sim, getT, svgRef, reheat, cool) {
  return (node) => (e) => {
    e.stopPropagation();
    const svg = svgRef.current;
    if (!svg) return;
    if (svg.setPointerCapture) svg.setPointerCapture(e.pointerId);
    const rect = svg.getBoundingClientRect();
    const toWorld = (cx, cy) => { const t = getT(); return { x: (cx - rect.left - t.x) / t.k, y: (cy - rect.top - t.y) / t.k }; };
    node.fx = node.x;
    node.fy = node.y;
    reheat(0.3);
    let moved = false;
    const move = (ev) => { moved = true; const w = toWorld(ev.clientX, ev.clientY); node.fx = w.x; node.fy = w.y; };
    const up = (ev) => {
      window.removeEventListener('pointermove', move);
      window.removeEventListener('pointerup', up);
      cool();
      if (!ev.shiftKey && !moved) { node.fx = null; node.fy = null; } // tap w/o shift = unpin
      node.__pinned = node.fx != null;
    };
    window.addEventListener('pointermove', move);
    window.addEventListener('pointerup', up);
  };
}

// ── rAF position tween (FLIP-style; used by B layout switch + C ego walk) ────
// targets: { [id]: {x,y} }. Returns current { [id]: {x,y} }, eased to targets.
function useTween(targets, ms = 450) {
  const [pos, setPos] = useS(targets);
  const fromRef = useR(targets);
  const rafRef = useR(null);
  const startRef = useR(0);
  useE(() => {
    if (reducedMotion()) { fromRef.current = targets; setPos(targets); return; }
    const from = fromRef.current || {};
    startRef.current = 0;
    const ease = (u) => 1 - Math.pow(1 - u, 3);
    const step = (ts) => {
      if (!startRef.current) startRef.current = ts;
      const u = Math.min(1, (ts - startRef.current) / ms);
      const e = ease(u);
      const next = {};
      for (const id in targets) {
        const a = from[id] || targets[id];
        const b = targets[id];
        next[id] = { x: a.x + (b.x - a.x) * e, y: a.y + (b.y - a.y) * e };
      }
      setPos(next);
      if (u < 1) rafRef.current = requestAnimationFrame(step);
      else fromRef.current = targets;
    };
    rafRef.current = requestAnimationFrame(step);
    return () => { if (rafRef.current) cancelAnimationFrame(rafRef.current); };
    // eslint-disable-next-line
  }, [targets, ms]);
  return pos;
}

// ── stage sizing (ResizeObserver) ───────────────────────────────────────────
function useSize(ref, reserve = 66) {
  const [size, setSize] = useS({ width: 800, height: 520 });
  useE(() => {
    const el = ref.current;
    if (!el) return;
    const measure = () => {
      const rect = el.getBoundingClientRect();
      const h = Math.max(360, window.innerHeight - rect.top - reserve);
      setSize({ width: Math.max(320, Math.floor(rect.width)), height: Math.floor(h) });
    };
    measure();
    const ro = new ResizeObserver(measure);
    ro.observe(el);
    window.addEventListener('resize', measure);
    return () => { ro.disconnect(); window.removeEventListener('resize', measure); };
    // eslint-disable-next-line
  }, [ref]);
  return size;
}

// ── SVG defs (per-link-type arrowheads + node glow) ─────────────────────────
function GraphDefs() {
  const items = Object.entries(MEM_LINK_COLORS);
  return (
    <defs>
      {items.map(([k, c]) => (
        <marker key={k} id={`arr-${k}`} viewBox="0 0 8 8" refX="7" refY="4" markerWidth="6" markerHeight="6" orient="auto-start-reverse">
          <path d="M0 0 L8 4 L0 8 z" fill={c} opacity="0.85" />
        </marker>
      ))}
      <filter id="nodeglow" x="-60%" y="-60%" width="220%" height="220%">
        <feGaussianBlur stdDeviation="3" result="b" />
        <feMerge><feMergeNode in="b" /><feMergeNode in="SourceGraphic" /></feMerge>
      </filter>
    </defs>
  );
}

// ── rich hovercard ──────────────────────────────────────────────────────────
function Hovercard({ node, links, x, y, source }) {
  if (!node) return null;
  const deg = degreeByType(node.id, links);
  const total = Object.values(deg).reduce((a, b) => a + b, 0);
  const isEnt = node.kind === 'entity';
  const C = isEnt ? node.color : (MEM_FACT_COLORS[node.type] || 'var(--info)');
  const flipX = x > 0.62;
  const flipY = y > 0.6;
  const style = {
    left: `calc(${x * 100}% ${flipX ? '- 16px' : '+ 16px'})`,
    top: `calc(${y * 100}% ${flipY ? '- 12px' : '+ 12px'})`,
    transform: `translate(${flipX ? '-100%' : '0'}, ${flipY ? '-100%' : '0'})`,
  };
  return (
    <div className="mg-hovercard" style={style} data-testid="mem-graph-hovercard">
      <div className="mg-hc-head">
        <span className="mg-hc-kind mono">{isEnt ? 'entity' : 'fact'}</span>
        {isEnt
          ? <span className="mg-hc-tag mono" style={{ color: node.color, borderColor: node.color }}>{node.entKind}</span>
          : <span className="mg-hc-tag mono" style={{ color: C, borderColor: C }}>{node.type}</span>}
        {!isEnt && node.topicColor && (
          <span className="mg-hc-topic mono"><i style={{ background: node.topicColor }} />{node.topicLabel}</span>
        )}
      </div>
      <div className="mg-hc-title">{isEnt ? node.label : (node.text || node.label)}</div>
      {!isEnt && node.date && <div className="mg-hc-when mono">{fmtMemDate(node.date, true)}</div>}
      {isEnt && <div className="mg-hc-when mono">{node.mentionCount} mentions</div>}
      <div className="mg-hc-edges">
        {Object.entries(deg).filter(([, n]) => n > 0).map(([k, n]) => (
          <span key={k} className="mg-hc-edge mono"><i style={{ background: MEM_LINK_COLORS[k] }} />{k}<b>{n}</b></span>
        ))}
        {total === 0 && <span className="mg-hc-edge mono" style={{ color: 'var(--fg-5)' }}>no links</span>}
      </div>
      <div className="mg-hc-hint mono">{source === 'entities' ? 'click → focus entity' : 'click → detail · drag to pin'}</div>
    </div>
  );
}

// ── node detail panel ───────────────────────────────────────────────────────
function NodeDetail({ node, graph, onClose, onFocus, onPathFrom, source }) {
  if (!node) return null;
  const isEnt = node.kind === 'entity';
  const nbrs = neighborsOf(node.id, graph.links);
  const byId = Object.fromEntries(graph.nodes.map((n) => [n.id, n]));
  const deg = degreeByType(node.id, graph.links);
  const C = isEnt ? node.color : (MEM_FACT_COLORS[node.type] || 'var(--info)');
  return (
    <div className="mg-detail" data-testid="mem-graph-detail">
      <div className="mg-detail-head">
        <span className="mono" style={{ color: 'var(--fg-3)' }}>{isEnt ? 'entity' : 'memory · ' + node.type}</span>
        <button className="mg-x" onClick={onClose} aria-label="Close"><Icon name="close" size={13} /></button>
      </div>
      <div className="mg-detail-title" style={{ borderColor: C }}>
        <span className="mg-detail-dot" style={{ background: C }} />
        <span>{isEnt ? node.label : (node.text || node.label)}</span>
      </div>
      <div className="mg-detail-meta mono">
        {!isEnt && node.topicColor && <span className="mg-meta-row"><span className="k">topic</span><span className="v"><i style={{ background: node.topicColor }} />{node.topicLabel}</span></span>}
        {!isEnt && node.date && <span className="mg-meta-row"><span className="k">when</span><span className="v">{fmtMemDate(node.date, true)}</span></span>}
        {isEnt && <span className="mg-meta-row"><span className="k">kind</span><span className="v">{node.entKind}</span></span>}
        {isEnt && <span className="mg-meta-row"><span className="k">mentions</span><span className="v num">{node.mentionCount}</span></span>}
      </div>
      <div className="mg-detail-edges">
        {Object.entries(deg).filter(([, n]) => n > 0).map(([k, n]) => (
          <span key={k} className="mg-chip-edge mono"><i style={{ background: MEM_LINK_COLORS[k] }} />{k} <b>{n}</b></span>
        ))}
      </div>
      <div className="mg-detail-actions">
        {onFocus && <button className="btn ghost xs" onClick={() => onFocus(node)}><Icon name="focus" size={12} /> Focus neighborhood</button>}
        {onPathFrom && <button className="btn ghost xs" onClick={() => onPathFrom(node)}><Icon name="path" size={12} /> Trace path…</button>}
      </div>
      <div className="mg-detail-sec mono">connections · {nbrs.length}</div>
      <div className="mg-detail-list">
        {nbrs.slice(0, 14).map(({ id, link, dir }, i) => {
          const nb = byId[id];
          if (!nb) return null;
          return (
            <div key={i} className="mg-nbr" onClick={() => onFocus && onFocus(nb)}>
              <span className="mg-nbr-line" style={{ background: MEM_LINK_COLORS[link.linkType] }} />
              <span className="mg-nbr-rel mono">{MEM_LINK_LABEL[link.linkType]}{link.linkType === 'causal' ? (dir === 'out' ? ' →' : ' ←') : ''}</span>
              <span className="mg-nbr-label">{nb.label}</span>
            </div>
          );
        })}
      </div>
    </div>
  );
}

Object.assign(window, {
  MEM_LINK_COLORS, MEM_LINK_LABEL, MEM_FACT_COLORS, MEM_FACT_DESC, TOPICS, MG_PALETTE,
  fmtMemDate, normalizeGraph, reducedMotion,
  neighborsOf, degreeByType, shortestPath, pathEdges, edgeArc,
  useForce, usePanZoom, makeNodeDrag, useTween, useSize,
  GraphDefs, Hovercard, NodeDetail,
});
