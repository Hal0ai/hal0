// hal0 memory overhaul — Direction B: Structured lenses.
//
// One bank, four LENSES — each a layout that fits the relationship's meaning:
//   semantic     → topic clusters (phyllotaxis-packed neighborhoods)
//   temporal     → time axis + lanes, with a draggable/playable scrubber
//   causal       → left→right layered DAG (reasoning chains)
//   cooccurrence → entity adjacency matrix (kills the hairball at any density)
//
// Switching lenses TWEENS node positions (window.useTween, FLIP/rAF cubic-ease,
// respects reduced motion) so edges stay attached.
//
// Window-globals pattern (no ES imports across dash modules). The engine
// (memory-graph-engine.jsx) loads first and registers the helpers we use:
//   window.useTween, window.edgeArc, window.GraphDefs, window.Hovercard,
//   window.NodeDetail, window.fmtMemDate, window.reducedMotion,
//   window.MEM_FACT_COLORS, window.MEM_LINK_COLORS, window.MG_PALETTE.
//
// ADAPTATION vs the prototype: the prototype's GraphStructured took raw `facts`
// and called buildFactGraph/buildEntityGraph internally. Here both graphs are
// ALREADY normalized by the engine's normalizeGraph(), so we consume:
//   graph        — fact graph   { nodes, links, topics }
//   entityGraph  — entity graph { nodes, links }
// directly. We also handle the live-data realities: zero causal edges → empty
// state, derived topics via node.topic/topicColor/topicLabel, and missing
// entityGraph → friendly matrix empty state.

const { useState, useRef, useMemo, useEffect } = React;

const B_LENSES = [
  { id: 'semantic', label: 'Semantic', sub: 'clusters', icon: 'layers' },
  { id: 'temporal', label: 'Temporal', sub: 'timeline', icon: 'clock' },
  { id: 'causal', label: 'Causal', sub: 'chains', icon: 'arrow' },
  { id: 'cooccurrence', label: 'Co-occur', sub: 'matrix', icon: 'graph' },
];

const SCRUB_KEY = 'hal0.mem.scrubT';

function readScrub() {
  try {
    const v = parseFloat(localStorage.getItem(SCRUB_KEY));
    return isFinite(v) ? Math.min(1, Math.max(0, v)) : 1;
  } catch {
    return 1;
  }
}

function GraphStructured({ graph, entityGraph, query, width, height, banner }) {
  const W = width || 800;
  const H = height || 520;

  const factGraph = graph || { nodes: [], links: [], topics: {} };
  const entGraph = entityGraph || { nodes: [], links: [] };
  const fnodes = factGraph.nodes || [];
  const flinks = factGraph.links || [];
  const topics = factGraph.topics || {};

  const [lens, setLens] = useState('semantic');
  const [hover, setHover] = useState(null);
  const [selected, setSelected] = useState(null);
  const [cursor, setCursor] = useState(readScrub); // temporal scrubber 0..1
  const [playing, setPlaying] = useState(false);

  // ── layout geometry (virtual W×H) ──────────────────────────────────────────
  const padX = 56;
  const padTop = 64;
  const padBot = 52;
  const innerW = Math.max(80, W - padX * 2);
  const innerH = Math.max(80, H - padTop - padBot);

  // topic metadata lookup: prefer per-node topicColor/topicLabel (engine-derived),
  // fall back to the topics map, then to the palette.
  const topicMeta = (tid, sampleNode) => {
    if (sampleNode && sampleNode.topicColor) {
      return { color: sampleNode.topicColor, label: sampleNode.topicLabel || tid };
    }
    const t = topics[tid];
    if (t) return { color: t.color, label: t.label };
    return { color: 'var(--info)', label: String(tid || 'cluster') };
  };

  // ── SEMANTIC: topic clusters (phyllotaxis pack per cluster) ────────────────
  const semantic = useMemo(() => {
    const buckets = {};
    fnodes.forEach((n) => {
      const k = n.topic || ('type-' + (n.type || 'world'));
      (buckets[k] = buckets[k] || []).push(n);
    });
    const keys = Object.keys(buckets);
    const cols = Math.max(1, Math.min(3, keys.length));
    const rows = Math.max(1, Math.ceil(keys.length / cols));
    const cw = innerW / cols;
    const ch = innerH / rows;
    const pos = {};
    const clusters = [];
    keys.forEach((k, i) => {
      const cx = padX + (i % cols) * cw + cw / 2;
      const cy = padTop + Math.floor(i / cols) * ch + ch / 2;
      const arr = buckets[k];
      const R = Math.min(cw, ch) * 0.34;
      arr.forEach((n, j) => {
        const a = 2.399963 * j; // golden angle
        const r = R * Math.sqrt(j / Math.max(1, arr.length - 1));
        pos[n.id] = { x: cx + Math.cos(a) * r, y: cy + Math.sin(a) * r };
      });
      const meta = topicMeta(k, arr[0]);
      clusters.push({ key: k, cx, cy, r: R + 26, color: meta.color, label: meta.label, n: arr.length });
    });
    return { pos, clusters };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [fnodes, innerW, innerH, W, H]);

  // ── TEMPORAL: time-axis lanes per fact type, with de-collision ─────────────
  const temporal = useMemo(() => {
    const pos = {};
    const laneTypes = ['world', 'experience', 'observation'];
    const laneY = {};
    laneTypes.forEach((t, i) => {
      laneY[t] = padTop + 26 + (i + 0.5) * ((innerH - 26) / laneTypes.length);
    });
    const times = fnodes.map((n) => n.t || 0).filter((t) => t > 0);
    const min = times.length ? Math.min(...times) : 0;
    const max = times.length ? Math.max(...times) : 1;
    const span = max - min || 1;
    const sorted = [...fnodes].sort((a, b) => (a.t || 0) - (b.t || 0));
    const lastX = { world: -99, experience: -99, observation: -99 };
    sorted.forEach((n) => {
      const lane = laneTypes.includes(n.type) ? n.type : 'world';
      const x = padX + (((n.t || min) - min) / span) * innerW;
      let y = laneY[lane];
      if (x - (lastX[lane] ?? -99) < 16) y += (Math.round(x) % 2 ? 14 : -14); // de-collide
      lastX[lane] = x;
      pos[n.id] = { x, y };
    });
    return { pos, laneTypes, laneY, min, max, span };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [fnodes, innerW, innerH, W, H]);

  // ── CAUSAL: longest-path layered DAG (left→right) + tray for un-chained ────
  const causal = useMemo(() => {
    const links = flinks.filter((l) => l.linkType === 'causal');
    const idOf = (x) => (x && x.id) || x;
    const out = {};
    const inc = {};
    const inCausal = new Set();
    links.forEach((l) => {
      const s = idOf(l.source);
      const t = idOf(l.target);
      (out[s] = out[s] || []).push(t);
      inc[t] = (inc[t] || 0) + 1;
      inCausal.add(s);
      inCausal.add(t);
    });
    const pos = {};
    if (links.length === 0) {
      return { pos, maxLayer: 0, trayCount: 0, empty: true };
    }
    // longest-path layering from roots (no incoming causal edge), cycle-guarded.
    const layer = {};
    const visit = (id, d, seen) => {
      if (seen.has(id)) return;
      seen.add(id);
      layer[id] = Math.max(layer[id] || 0, d);
      (out[id] || []).forEach((t) => visit(t, d + 1, seen));
      seen.delete(id);
    };
    fnodes.forEach((n) => {
      if (inCausal.has(n.id) && !inc[n.id]) visit(n.id, 0, new Set());
    });
    // any chained node not reached (pure cycle) → layer 0
    inCausal.forEach((id) => {
      if (!(id in layer)) layer[id] = 0;
    });
    const maxLayer = Math.max(0, ...Object.values(layer));
    const byLayer = {};
    for (let i = 0; i <= maxLayer; i++) byLayer[i] = [];
    Object.entries(layer).forEach(([id, l]) => byLayer[l].push(id));
    const colW = innerW / (maxLayer + 1);
    for (let l = 0; l <= maxLayer; l++) {
      const arr = byLayer[l];
      const gap = (innerH - 20) / (arr.length + 1);
      arr.forEach((id, j) => {
        pos[id] = { x: padX + l * colW + colW / 2, y: padTop + 10 + (j + 1) * gap };
      });
    }
    // un-chained facts → bottom tray
    const tray = fnodes.filter((n) => !inCausal.has(n.id));
    tray.forEach((n, j) => {
      pos[n.id] = { x: padX + 20 + (j + 0.5) * (innerW / Math.max(1, tray.length)), y: H - 22 };
    });
    return { pos, maxLayer, trayCount: tray.length, empty: false };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [fnodes, flinks, innerW, innerH, W, H]);

  // target positions for the active lens (cooccurrence keeps semantic layout
  // underneath so the tween back is sensible, though the SVG isn't shown).
  const targetPos =
    lens === 'semantic' ? semantic.pos
    : lens === 'temporal' ? temporal.pos
    : lens === 'causal' ? causal.pos
    : semantic.pos;

  const pos = window.useTween(targetPos);

  // ── temporal scrubber autoplay ─────────────────────────────────────────────
  useEffect(() => {
    if (!playing || lens !== 'temporal') return;
    let raf;
    let last = performance.now();
    const step = (now) => {
      const dt = (now - last) / 1000;
      last = now;
      setCursor((c) => {
        const n = c + dt * 0.22;
        if (n >= 1) {
          setPlaying(false);
          return 1;
        }
        return n;
      });
      raf = requestAnimationFrame(step);
    };
    raf = requestAnimationFrame(step);
    return () => cancelAnimationFrame(raf);
  }, [playing, lens]);

  // persist scrubber position
  useEffect(() => {
    try {
      localStorage.setItem(SCRUB_KEY, String(cursor));
    } catch {
      /* ignore */
    }
  }, [cursor]);

  const cursorT = temporal.min + (temporal.max - temporal.min) * cursor;

  // ── query highlight + temporal reveal ──────────────────────────────────────
  const q = (query || '').toLowerCase();
  const matchesQ = (n) => q && (n.label || n.text || '').toLowerCase().includes(q);
  function nodeOpacity(n) {
    if (q) return matchesQ(n) ? 1 : 0.16;
    if (lens === 'temporal') return (n.t || 0) <= cursorT + 6e5 ? 1 : 0.12;
    return 1;
  }
  const showLabel = (n) =>
    hover?.id === n.id || selected?.id === n.id || matchesQ(n) || lens === 'causal';
  const screenFrac = (n) => {
    const p = pos[n.id] || { x: 0, y: 0 };
    return { x: p.x / W, y: p.y / H };
  };

  // edges visible for the current lens
  const lensEdges =
    lens === 'semantic' ? flinks.filter((l) => l.linkType === 'semantic')
    : lens === 'temporal' ? flinks.filter((l) => l.linkType === 'temporal')
    : lens === 'causal' ? flinks.filter((l) => l.linkType === 'causal')
    : [];

  const idOf = (x) => (x && x.id) || x;
  const fcolor = window.MEM_FACT_COLORS;
  const lcolor = window.MEM_LINK_COLORS;

  const emptyFacts = fnodes.length === 0;

  return (
    <div className="mg-wrap">
      <div className="mg-stage" style={{ height: H }}>
        {/* lens selector */}
        <div className="mg-controls mg-float mg-bfloat">
          <div className="mg-lensseg">
            {B_LENSES.map((l) => (
              <button
                key={l.id}
                className={'mg-lensbtn' + (lens === l.id ? ' on' : '')}
                onClick={() => {
                  setLens(l.id);
                  setSelected(null);
                  setHover(null);
                }}
                aria-pressed={lens === l.id}
              >
                <Icon name={l.icon} size={13} />
                <span>{l.label}</span>
                <em>{l.sub}</em>
              </button>
            ))}
          </div>
        </div>
        {banner}

        {lens !== 'cooccurrence' ? (
          <svg
            className="mg-svg"
            width={W}
            height={H}
            onClick={(e) => {
              if (e.target.tagName === 'svg') setSelected(null);
            }}
          >
            <window.GraphDefs />

            {/* empty bank */}
            {emptyFacts && (
              <text x={W / 2} y={H / 2} textAnchor="middle" className="mg-axis-lbl" style={{ fill: 'var(--fg-4)' }}>
                No memories in this bank yet
              </text>
            )}

            {/* lens scaffolding */}
            {!emptyFacts && lens === 'semantic' &&
              semantic.clusters.map((c) => (
                <g key={c.key} style={{ transition: 'opacity .4s' }}>
                  <circle
                    cx={c.cx}
                    cy={c.cy}
                    r={c.r}
                    fill={c.color}
                    fillOpacity="0.05"
                    stroke={c.color}
                    strokeOpacity="0.28"
                    strokeDasharray="3 4"
                  />
                  <text
                    x={c.cx}
                    y={c.cy - c.r - 7}
                    textAnchor="middle"
                    className="mg-cluster-lbl"
                    style={{ fill: c.color }}
                  >
                    {c.label} · {c.n}
                  </text>
                </g>
              ))}

            {!emptyFacts && lens === 'temporal' && (
              <g>
                {temporal.laneTypes.map((t) => (
                  <g key={t}>
                    <line
                      x1={padX}
                      y1={temporal.laneY[t]}
                      x2={W - padX}
                      y2={temporal.laneY[t]}
                      stroke="var(--line-soft)"
                      strokeDasharray="2 5"
                    />
                    <text
                      x={12}
                      y={temporal.laneY[t] - 8}
                      textAnchor="start"
                      className="mg-lane-lbl"
                      style={{ fill: fcolor[t] }}
                    >
                      {t}
                    </text>
                  </g>
                ))}
                <line x1={padX} y1={H - padBot + 18} x2={W - padX} y2={H - padBot + 18} stroke="var(--line)" />
                <text x={padX} y={H - 12} className="mg-axis-lbl">
                  {window.fmtMemDate(temporal.min)}
                </text>
                <text x={W - padX} y={H - 12} textAnchor="end" className="mg-axis-lbl">
                  {window.fmtMemDate(temporal.max)}
                </text>
                {/* scrubber cursor */}
                {(() => {
                  const x = padX + cursor * innerW;
                  return (
                    <g>
                      <line
                        x1={x}
                        y1={padTop}
                        x2={x}
                        y2={H - padBot + 18}
                        stroke="var(--accent)"
                        strokeOpacity="0.8"
                        strokeWidth="1.5"
                      />
                      <circle cx={x} cy={H - padBot + 18} r="5" fill="var(--accent)" />
                      <text
                        x={x}
                        y={padTop - 6}
                        textAnchor="middle"
                        className="mg-axis-lbl"
                        style={{ fill: 'var(--accent)' }}
                      >
                        {window.fmtMemDate(cursorT, true)}
                      </text>
                    </g>
                  );
                })()}
              </g>
            )}

            {!emptyFacts && lens === 'causal' && !causal.empty &&
              Array.from({ length: causal.maxLayer + 1 }).map((_, i) => {
                const colW = innerW / (causal.maxLayer + 1);
                return (
                  <text
                    key={i}
                    x={padX + i * colW + colW / 2}
                    y={padTop - 8}
                    textAnchor="middle"
                    className="mg-axis-lbl"
                  >
                    {i === 0 ? 'cause' : i === causal.maxLayer ? 'effect' : '→'}
                  </text>
                );
              })}

            {/* causal empty state */}
            {!emptyFacts && lens === 'causal' && causal.empty && (
              <text
                x={W / 2}
                y={H / 2}
                textAnchor="middle"
                className="mg-axis-lbl"
                style={{ fill: 'var(--fg-4)' }}
              >
                No causal links in this bank yet
              </text>
            )}

            {/* edges */}
            {!emptyFacts &&
              lensEdges.map((l) => {
                const s = pos[idOf(l.source)];
                const t = pos[idOf(l.target)];
                if (!s || !t) return null;
                const isCausal = l.linkType === 'causal';
                return (
                  <path
                    key={l.id}
                    d={window.edgeArc(s.x, s.y, t.x, t.y, lens === 'temporal' ? 0 : isCausal ? 0.04 : 0.14)}
                    fill="none"
                    stroke={lcolor[l.linkType]}
                    strokeOpacity={lens === 'semantic' ? 0.35 : 0.6}
                    strokeWidth={isCausal ? 1.6 : 1}
                    markerEnd={isCausal ? 'url(#arr-causal)' : undefined}
                  />
                );
              })}

            {/* fact nodes — in causal-empty mode we still render them (semantic
                positions persist under the tween) so the view isn't blank */}
            {!emptyFacts &&
              fnodes.map((n) => {
                const p = pos[n.id];
                if (!p) return null;
                const sel = selected?.id === n.id;
                const stroke = lens === 'temporal' ? fcolor[n.type] || 'var(--bg)' : 'var(--bg)';
                return (
                  <g
                    key={n.id}
                    data-node
                    transform={`translate(${p.x},${p.y})`}
                    style={{ cursor: 'pointer', opacity: nodeOpacity(n) }}
                    onPointerEnter={() => setHover(n)}
                    onPointerLeave={() => setHover((h) => (h?.id === n.id ? null : h))}
                    onClick={(e) => {
                      e.stopPropagation();
                      setSelected(n);
                    }}
                  >
                    {sel && <circle r="11" fill="none" stroke="var(--fg)" strokeWidth="1.5" strokeOpacity="0.8" />}
                    <circle
                      r="6.5"
                      fill={n.color || fcolor[n.type] || 'var(--info)'}
                      fillOpacity="0.92"
                      stroke={stroke}
                      strokeWidth="1.4"
                    />
                    <circle r="2.6" fill="var(--bg-1)" fillOpacity="0.5" />
                    {showLabel(n) && (
                      <text
                        className="mg-nodelabel"
                        y="-11"
                        textAnchor="middle"
                        style={{ fontSize: 10.5, fill: sel ? 'var(--fg)' : 'var(--fg-3)' }}
                      >
                        {(n.label || '').slice(0, 22)}
                      </text>
                    )}
                  </g>
                );
              })}
          </svg>
        ) : (
          <CooccurMatrix graph={entGraph} W={W} H={H} onHover={setHover} setSelected={setSelected} />
        )}

        {hover && lens !== 'cooccurrence' && (
          <window.Hovercard node={hover} links={flinks} source="memories" {...screenFrac(hover)} />
        )}

        {lens === 'temporal' && !emptyFacts && (
          <div className="mg-scrubber">
            <button className="btn ghost xs" onClick={() => setPlaying((p) => !p)} aria-label={playing ? 'Pause' : 'Play'}>
              {playing ? '❚❚' : '▶'}
            </button>
            <input
              type="range"
              min="0"
              max="1"
              step="0.001"
              value={cursor}
              onChange={(e) => {
                setCursor(+e.target.value);
                setPlaying(false);
              }}
              className="mg-range"
              aria-label="Timeline scrubber"
            />
            <span className="mono mg-scrub-date">{window.fmtMemDate(cursorT, true)}</span>
            <button
              className="btn ghost xs"
              onClick={() => {
                setCursor(1);
                setPlaying(false);
              }}
            >
              all
            </button>
          </div>
        )}

        <div className="mg-help mono">
          {lens === 'cooccurrence'
            ? 'hover a cell · darker = more co-mentions'
            : lens === 'temporal'
            ? 'drag the scrubber or press play'
            : 'click a node for detail · switch lenses above'}
        </div>
      </div>

      {selected && (
        <window.NodeDetail
          node={selected}
          graph={{ nodes: fnodes, links: flinks }}
          source="memories"
          onClose={() => setSelected(null)}
          onFocus={(n) => setSelected(n)}
        />
      )}
    </div>
  );
}

// ── entity co-occurrence matrix ───────────────────────────────────────────────
// Consumes the already-normalized ENTITY graph { nodes, links }. Rows/cols are
// sorted by entKind then mentionCount (degree proxy); cell intensity scales with
// the link weight relative to the bank max. Diagonal shows self mention count.
function CooccurMatrix({ graph, W, H }) {
  const [cell, setCell] = useState(null);
  const nodes = useMemo(
    () =>
      [...(graph.nodes || [])].sort(
        (a, b) => (a.entKind || '').localeCompare(b.entKind || '') || (b.mentionCount || 0) - (a.mentionCount || 0)
      ),
    [graph]
  );
  const idx = Object.fromEntries(nodes.map((n, i) => [n.id, i]));
  const co = {};
  let maxCo = 1;
  (graph.links || []).forEach((l) => {
    const s = (l.source && l.source.id) || l.source;
    const t = (l.target && l.target.id) || l.target;
    co[s + '|' + t] = l.weight;
    co[t + '|' + s] = l.weight;
    maxCo = Math.max(maxCo, l.weight);
  });
  const N = nodes.length;

  if (N === 0) {
    return (
      <div className="mg-matrix-wrap">
        <svg className="mg-svg" width={W} height={H}>
          <text x={W / 2} y={H / 2} textAnchor="middle" className="mg-axis-lbl" style={{ fill: 'var(--fg-4)' }}>
            No entities to correlate in this bank yet
          </text>
        </svg>
      </div>
    );
  }

  const lblW = 120;
  const top = 70;
  const size = Math.max(6, Math.min((W - lblW - 40) / N, (H - top - 30) / N, 30));
  const get = (a, b) => (a === b ? nodes[idx[a]].mentionCount || 0 : co[a + '|' + b] || 0);

  return (
    <div className="mg-matrix-wrap">
      <svg className="mg-svg" width={W} height={H}>
        {/* column labels */}
        {nodes.map((n, j) => {
          const cx = lblW + j * size + size / 2;
          return (
            <text
              key={'c' + n.id}
              x={cx}
              y={top - 8}
              transform={`rotate(-45 ${cx} ${top - 8})`}
              className="mg-mx-lbl"
              style={{ fill: cell && cell.j === j ? n.color : 'var(--fg-4)' }}
            >
              {(n.label || '').slice(0, 14)}
            </text>
          );
        })}
        {nodes.map((row, i) => (
          <g key={row.id}>
            <text
              x={lblW - 8}
              y={top + i * size + size / 2 + 3}
              textAnchor="end"
              className="mg-mx-lbl"
              style={{ fill: cell && cell.i === i ? row.color : 'var(--fg-3)' }}
            >
              {(row.label || '').slice(0, 16)}
            </text>
            {nodes.map((col, j) => {
              const v = get(row.id, col.id);
              const diag = i === j;
              const intensity = diag ? 0 : v / maxCo;
              return (
                <rect
                  key={col.id}
                  x={lblW + j * size}
                  y={top + i * size}
                  width={size - 1.5}
                  height={size - 1.5}
                  rx="2"
                  fill={diag ? 'var(--bg-3)' : v ? row.color : 'var(--bg-1)'}
                  fillOpacity={diag ? 1 : 0.12 + intensity * 0.8}
                  stroke={cell && cell.i === i && cell.j === j ? 'var(--accent)' : 'transparent'}
                  strokeWidth="1.5"
                  onPointerEnter={() => setCell({ i, j, v, a: row.label, b: col.label, diag })}
                  onPointerLeave={() => setCell((c) => (c && c.i === i && c.j === j ? null : c))}
                  style={{ cursor: 'pointer' }}
                />
              );
            })}
          </g>
        ))}
      </svg>
      {cell && (
        <div className="mg-mx-tip mono">
          {cell.diag ? (
            <>
              <b>{cell.a}</b> · {cell.v} mentions
            </>
          ) : cell.v ? (
            <>
              <b>{cell.a}</b> + <b>{cell.b}</b> · co-mentioned <b>{cell.v}×</b>
            </>
          ) : (
            <span style={{ color: 'var(--fg-5)' }}>
              {cell.a} + {cell.b} · never together
            </span>
          )}
        </div>
      )}
    </div>
  );
}

Object.assign(window, { GraphStructured });
