// hal0 dashboard — Memory graph Direction C: Ego explorer.
//
// Focus + context. One node sits at the center; its direct neighbours fan out
// on a ring, grouped + coloured by relationship type. Click a neighbour and it
// animates to the center (you "walk" the graph); a breadcrumb trail builds. A
// faded 2-hop outer ring gives context, and a timeline strip steps the centre
// forward/back through time. Because it only ever renders a local
// neighbourhood, it scales to any bank size.
//
// Adapted from the memory_overhaul prototype (GraphEgo) for the live app:
//   - consumes the pre-normalized FACT graph {nodes,links} (no buildFactGraph).
//   - default center = highest-degree node (prototype hardcoded "f9").
//   - topic chip from node.topicColor/topicLabel (normalized) not window.TOPICS.
//   - window.fmtMemDate replaces the prototype's window.fmtDate.
//   - empty-graph guard renders an .mg-empty state.
//
// Window-globals pattern (Vite-bundled): bare global React, siblings via
// window.* resolved through globalThis. Exported on window at file end.

const { useState, useRef, useEffect, useMemo } = React;

// Ring-1 is capped to keep high-degree nodes legible (Hindsight's semantic
// graph is far denser than curated banks — a single node can have dozens–
// hundreds of neighbours). "+K more" steps the cap up; nothing is hidden
// silently. Salience keeps the storyline edges (causal/temporal) on top.
const RING1_CAP_STEP = 24;

function GraphEgo({ graph, query, width, height, banner, onCenter }) {
  const W = width, H = height;
  const nodes = (graph && graph.nodes) || [];
  const links = (graph && graph.links) || [];

  const byId = useMemo(() => Object.fromEntries(nodes.map((n) => [n.id, n])), [nodes]);
  const sortedByTime = useMemo(() => [...nodes].sort((a, b) => (a.t || 0) - (b.t || 0)), [nodes]);

  // default center = highest-degree node (fallback: first node).
  const defaultCenter = useMemo(() => {
    if (!nodes.length) return null;
    const deg = {};
    for (const l of links) {
      const s = (l.source && l.source.id) || l.source;
      const t = (l.target && l.target.id) || l.target;
      deg[s] = (deg[s] || 0) + 1;
      deg[t] = (deg[t] || 0) + 1;
    }
    let best = nodes[0].id, bestN = -1;
    for (const n of nodes) {
      const d = deg[n.id] || 0;
      if (d > bestN) { bestN = d; best = n.id; }
    }
    return best;
  }, [nodes, links]);

  const [centerId, setCenterId] = useState(null);
  const [trail, setTrail] = useState([]);
  const [hover, setHover] = useState(null);
  const [ringCap, setRingCap] = useState(RING1_CAP_STEP);
  const center = (centerId && byId[centerId]) ? centerId : defaultCenter;

  // reset the ring cap whenever the centre changes (each node starts collapsed)
  useEffect(() => { setRingCap(RING1_CAP_STEP); }, [center]);

  // FU2: surface the active center to the parent so it can fetch a
  // server-side ego slice (Direction-C, big banks). Fires on center change.
  useEffect(() => {
    if (onCenter && center) onCenter(center);
  }, [center, onCenter]);

  // ── layout for current center ──────────────────────────────────────────────
  const layout = useMemo(() => {
    const cx = W * 0.57, cy = H * 0.48;
    if (!center) return { pos: {}, ring1: [], ring2: [], R1: 0, R2: 0, cx, cy };
    const all = window.neighborsOf(center, links);
    // group links per neighbour id (a neighbour can connect via several types)
    const map = {};
    all.forEach(({ id, link, dir }) => {
      (map[id] = map[id] || { id, links: [] }).links.push({ link, dir });
    });
    const fullRing1 = Object.values(map);
    // salience order: link-type priority (causal > temporal > cooccurrence >
    // semantic), then strongest edge weight, then most recent. Keeps the
    // storyline edges on top and sheds the noisy bulk-semantic fan first.
    const order = { causal: 0, temporal: 1, cooccurrence: 2, semantic: 3 };
    const rank = (lks) => Math.min(...lks.map((x) => order[x.link.linkType] ?? 9));
    const maxW = (lks) => Math.max(...lks.map((x) => x.link.weight || 1));
    fullRing1.sort((a, b) =>
      (rank(a.links) - rank(b.links)) ||
      (maxW(b.links) - maxW(a.links)) ||
      ((byId[b.id]?.t || 0) - (byId[a.id]?.t || 0)));
    const totalNbrs = fullRing1.length;
    const ring1 = fullRing1.slice(0, ringCap);
    const hidden = Math.max(0, totalNbrs - ring1.length);
    const R1 = Math.min(W * 0.26, H * 0.34, 200);
    const pos = { [center]: { x: cx, y: cy } };
    const slots = ring1.length + (hidden > 0 ? 1 : 0); // reserve one slot for "+K more"
    ring1.forEach((nb, i) => {
      const ang = -Math.PI / 2 + (i / Math.max(1, slots)) * Math.PI * 2;
      pos[nb.id] = { x: cx + Math.cos(ang) * R1, y: cy + Math.sin(ang) * R1, ang };
    });
    let more = null;
    if (hidden > 0) {
      const ang = -Math.PI / 2 + (ring1.length / Math.max(1, slots)) * Math.PI * 2;
      const mx = cx + Math.cos(ang) * R1, my = cy + Math.sin(ang) * R1;
      more = { count: hidden, x: mx, y: my, ang };
      pos.__more__ = { x: mx, y: my, ang };
    }
    // ring2 (2-hop context), capped + faded
    const seen = new Set([center, ...ring1.map((r) => r.id)]);
    const outer = [];
    ring1.forEach((nb) => {
      window.neighborsOf(nb.id, links).forEach(({ id }) => {
        if (!seen.has(id)) { seen.add(id); outer.push({ id, parent: nb.id }); }
      });
    });
    const cap = outer.slice(0, 30);
    const R2 = R1 * 1.95;
    cap.forEach((o, i) => {
      const parentAng = pos[o.parent]?.ang || 0;
      const spread = (i % 5 - 2) * 0.12;
      const ang = parentAng + spread;
      pos[o.id] = { x: cx + Math.cos(ang) * R2, y: cy + Math.sin(ang) * R2 };
    });
    return { pos, ring1, ring2: cap, R1, R2, cx, cy, more, totalNbrs };
  }, [center, links, byId, W, H, ringCap]);

  // ── tween between centres (rAF cubic-ease ~560ms; instant if reduced-motion) ─
  const [pos, setPos] = useState(layout.pos);
  const curRef = useRef(layout.pos), rafRef = useRef(0);
  useEffect(() => {
    const target = layout.pos, cx = layout.cx, cy = layout.cy;
    if (window.reducedMotion()) { curRef.current = target; setPos(target); return; }
    const from = {};
    for (const id in target) from[id] = curRef.current[id] || { x: cx, y: cy };
    const start = performance.now(), dur = 560, ease = (t) => 1 - Math.pow(1 - t, 3);
    const tick = (now) => {
      const p = Math.min(1, (now - start) / dur), e = ease(p), next = {};
      for (const id in target) {
        const a = from[id], b = target[id];
        next[id] = { x: a.x + (b.x - a.x) * e, y: a.y + (b.y - a.y) * e, ang: b.ang };
      }
      curRef.current = next; setPos(next);
      if (p < 1) rafRef.current = requestAnimationFrame(tick);
    };
    rafRef.current = requestAnimationFrame(tick);
    return () => cancelAnimationFrame(rafRef.current);
  }, [layout]);

  // ── navigation ──────────────────────────────────────────────────────────────
  function goTo(id, viaTrail) {
    if (id === center) return;
    if (!viaTrail) setTrail((t) => [...t, center]);
    setCenterId(id); setHover(null);
  }
  function back() {
    setTrail((t) => {
      if (!t.length) return t;
      const prev = t[t.length - 1];
      setCenterId(prev);
      return t.slice(0, -1);
    });
    setHover(null);
  }
  function stepTime(dir) {
    const i = sortedByTime.findIndex((n) => n.id === center);
    const j = Math.max(0, Math.min(sortedByTime.length - 1, i + dir));
    if (sortedByTime[j] && sortedByTime[j].id !== center) goTo(sortedByTime[j].id);
  }
  function expandRing() {
    setRingCap((c) => Math.min(layout.totalNbrs, c + RING1_CAP_STEP));
  }

  // ── empty state ─────────────────────────────────────────────────────────────
  if (!nodes.length || !center) {
    return (
      <div className="mg-wrap">
        <div className="mg-stage" style={{ height: H }}>
          <svg className="mg-svg" width={W} height={H}><window.GraphDefs /></svg>
          <div className="mg-empty">No memories to explore yet.</div>
        </div>
      </div>
    );
  }

  const centerNode = byId[center];
  const ring1ids = new Set(layout.ring1.map((r) => r.id));
  const screenFrac = (n) => { const p = pos[n.id] || { x: 0, y: 0 }; return { x: p.x / W, y: p.y / H }; };
  const deg = window.degreeByType(center, links);
  const tMin = sortedByTime.length ? sortedByTime[0].t : 0;
  const tMax = sortedByTime.length ? sortedByTime[sortedByTime.length - 1].t : 0;
  const tSpan = (tMax - tMin) || 1;

  return (
    <div className="mg-wrap">
      <div className="mg-stage" style={{ height: H }}>
        {/* breadcrumb trail */}
        <div className="mg-ego-trail mono">
          <button className="btn ghost xs" onClick={back} disabled={!trail.length}>
            <Icon name="arrow" size={11} style={{ transform: 'scaleX(-1)' }} /> back
          </button>
          <div className="mg-crumbs">
            {trail.slice(-4).map((id, i) => (
              <React.Fragment key={i}>
                <span
                  className="mg-crumb"
                  onClick={() => { const idx = trail.indexOf(id); setCenterId(id); setTrail(trail.slice(0, idx)); setHover(null); }}
                >{(byId[id]?.label || id).slice(0, 16)}</span>
                <span className="mg-crumb-sep">→</span>
              </React.Fragment>
            ))}
            <span className="mg-crumb cur">{(centerNode?.label || '').slice(0, 22)}</span>
          </div>
        </div>
        {banner}

        <svg className="mg-svg" width={W} height={H}>
          <window.GraphDefs />
          {/* faint ring guide */}
          <circle cx={layout.cx} cy={layout.cy} r={layout.R1} fill="none" stroke="var(--line-soft)" strokeDasharray="2 6" />

          {/* ring2 edges + nodes (2-hop context, faded) */}
          {layout.ring2.map((o) => {
            const a = pos[o.parent], b = pos[o.id]; if (!a || !b) return null;
            return <line key={'e2' + o.id} x1={a.x} y1={a.y} x2={b.x} y2={b.y} stroke="var(--line)" strokeOpacity="0.4" />;
          })}
          {layout.ring2.map((o) => {
            const p = pos[o.id], nd = byId[o.id]; if (!p || !nd) return null;
            return (
              <circle
                key={'n2' + o.id} cx={p.x} cy={p.y} r="4.5" fill={nd.color} fillOpacity="0.4"
                style={{ cursor: 'pointer' }} onClick={() => goTo(o.id)}
                onPointerEnter={() => setHover(nd)} onPointerLeave={() => setHover((h) => (h?.id === o.id ? null : h))}
              />
            );
          })}

          {/* ring1 edges (coloured by relationship; supports multiple typed arcs) */}
          {layout.ring1.map((nb) => {
            const a = pos[center], b = pos[nb.id]; if (!a || !b) return null;
            return nb.links.map((lk, k) => {
              const curve = (k - (nb.links.length - 1) / 2) * 0.16;
              const isCausal = lk.link.linkType === 'causal';
              return (
                <path
                  key={nb.id + k} d={window.edgeArc(a.x, a.y, b.x, b.y, curve)} fill="none"
                  stroke={window.MEM_LINK_COLORS[lk.link.linkType]} strokeOpacity="0.65" strokeWidth="1.6"
                  markerEnd={isCausal ? 'url(#arr-causal)' : undefined}
                />
              );
            });
          })}

          {/* ring1 nodes */}
          {layout.ring1.map((nb) => {
            const p = pos[nb.id], nd = byId[nb.id]; if (!p || !nd) return null;
            return (
              <g
                key={nb.id} data-node transform={`translate(${p.x},${p.y})`} style={{ cursor: 'pointer' }}
                onClick={() => goTo(nb.id)}
                onPointerEnter={() => setHover(nd)} onPointerLeave={() => setHover((h) => (h?.id === nb.id ? null : h))}
              >
                <circle r="9" fill={nd.color} fillOpacity="0.92" stroke="var(--bg)" strokeWidth="1.6" />
                <circle r="3.4" fill="var(--bg-1)" fillOpacity="0.5" />
                <text
                  className="mg-nodelabel" y={p.y < H / 2 ? -13 : 20} textAnchor="middle"
                  style={{ fontSize: 10.5, fill: 'var(--fg-2)' }}
                >{(nd.label || '').slice(0, 22)}</text>
              </g>
            );
          })}

          {/* "+K more" ring slot — expands the cap (nothing hidden silently) */}
          {layout.more && pos.__more__ && (
            <g
              data-testid="mem-ego-more" transform={`translate(${pos.__more__.x},${pos.__more__.y})`}
              style={{ cursor: 'pointer' }} onClick={expandRing}
              role="button" tabIndex={0}
              onKeyDown={(e) => { if (e.key === 'Enter' || e.key === ' ') { e.preventDefault(); expandRing(); } }}
            >
              <title>{`Show ${Math.min(RING1_CAP_STEP, layout.more.count)} more of ${layout.more.count} hidden neighbours`}</title>
              <circle r="11" fill="var(--bg-3)" stroke="var(--line-strong)" strokeWidth="1.2" />
              <text textAnchor="middle" dy="3.4" className="mono" style={{ fontSize: 9.5, fill: 'var(--fg-2)' }}>+{layout.more.count}</text>
            </g>
          )}

          {/* center node */}
          {(() => {
            const p = pos[center]; if (!p || !centerNode) return null;
            return (
              <g transform={`translate(${p.x},${p.y})`}>
                <circle r="26" fill={centerNode.color} fillOpacity="0.10" stroke={centerNode.color} strokeOpacity="0.5" />
                <circle r="15" fill={centerNode.color} fillOpacity="0.95" stroke="var(--bg)" strokeWidth="2" filter="url(#nodeglow)" />
                <circle r="6" fill="var(--bg-1)" fillOpacity="0.55" />
              </g>
            );
          })()}
        </svg>

        {/* center summary card */}
        {centerNode && (
          <div className="mg-ego-card">
            <div className="mg-ego-card-h mono">
              <span className="mg-ego-kind" style={{ color: window.MEM_FACT_COLORS[centerNode.type] || 'var(--info)' }}>{centerNode.type}</span>
              {centerNode.topicColor && (
                <span className="mg-ego-topic"><i style={{ background: centerNode.topicColor }} />{centerNode.topicLabel}</span>
              )}
              {centerNode.date && <span className="mg-ego-when">{window.fmtMemDate(centerNode.date, true)}</span>}
            </div>
            <div className="mg-ego-text">{centerNode.text || centerNode.label}</div>
            <div className="mono" data-testid="mem-ego-nbr-count" style={{ fontSize: 10, color: 'var(--fg-4)', marginBottom: 8 }}>
              neighbours · <b style={{ color: 'var(--fg-2)' }}>{layout.totalNbrs}</b>
              {layout.more ? ` · showing ${layout.ring1.length}` : ''}
            </div>
            <div className="mg-ego-deg">
              {Object.entries(deg).filter(([, n]) => n > 0).map(([k, n]) => (
                <span key={k} className="mono"><i style={{ background: window.MEM_LINK_COLORS[k] }} />{k} <b>{n}</b></span>
              ))}
            </div>
          </div>
        )}

        {hover && hover.id !== center && (
          <window.Hovercard node={hover} links={links} source="memories" {...screenFrac(hover)} />
        )}

        {/* timeline strip — step the centre through time */}
        <div className="mg-ego-time">
          <button className="btn ghost xs" onClick={() => stepTime(-1)} title="previous in time">‹ prev</button>
          <div className="mg-tl-track">
            {sortedByTime.map((n) => {
              const x = ((n.t || 0) - tMin) / tSpan;
              const isC = n.id === center, isN = ring1ids.has(n.id);
              return (
                <span
                  key={n.id} className={'mg-tl-tick' + (isC ? ' cur' : isN ? ' nbr' : '')}
                  style={{ left: `${x * 100}%`, background: isC ? 'var(--accent)' : (window.MEM_FACT_COLORS[n.type] || 'var(--info)') }}
                  title={window.fmtMemDate(n.date) + ' · ' + n.label} onClick={() => goTo(n.id)}
                />
              );
            })}
          </div>
          <button className="btn ghost xs" onClick={() => stepTime(1)} title="next in time">next ›</button>
        </div>
        <div className="mg-help mono" style={{ bottom: 52 }}>
          click any node to re-center · ‹ prev / next › walks the timeline · renders only the local neighbourhood, so it scales to any bank size
        </div>
      </div>
    </div>
  );
}

Object.assign(window, { GraphEgo });
