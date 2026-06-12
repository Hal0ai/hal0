// hal0 dashboard — Memory graph explorer (#memory/graph).
//
// Visualizes the Hindsight knowledge graph for one bank using a d3-force
// layout rendered as hand-rolled SVG (house style — no canvas widget).
// Two sources:
//   memories — GET /api/memory/banks/{bank}/graph (fact nodes, semantic/
//              temporal/causal links; filter by type/q/limit)
//   entities — GET /api/memory/banks/{bank}/entities/graph (entity
//              co-occurrence; min_count filter)
//
// Payloads are Cytoscape-style {data:{...}} wrappers (pinned from live
// 0.7.2). Self-loop edges are dropped before layout. d3-force primitives
// arrive via window.__hal0D3Force (memory-hook-bridge.ts).

const { useState: useStateMG, useMemo: useMemoMG } = React;

const MEM_LINK_COLORS = {
  semantic: 'var(--info)',
  temporal: 'var(--warn)',
  causal: 'var(--err)',
  cooccurrence: 'var(--hal0-accent)',
};

const MG_W = 800;
const MG_H = 520;

// Run the force simulation synchronously and return positioned copies.
// Hindsight graphs at dashboard scale (limit ≤ ~1000) settle fine in
// 250 ticks; layout is recomputed only when the payload changes.
function mgLayout(rawNodes, rawEdges) {
  const ids = new Set(rawNodes.map(n => n.id));
  const nodes = rawNodes.map(n => ({ ...n }));
  const links = rawEdges
    .filter(e => e.source !== e.target && ids.has(e.source) && ids.has(e.target))
    .map(e => ({ ...e }));
  const d3 = typeof window !== 'undefined' ? window.__hal0D3Force : null;
  if (d3 && nodes.length > 0) {
    const sim = d3
      .forceSimulation(nodes)
      .force('link', d3.forceLink(links).id(d => d.id).distance(70).strength(0.4))
      .force('charge', d3.forceManyBody().strength(-140))
      .force('center', d3.forceCenter(MG_W / 2, MG_H / 2))
      .force('collide', d3.forceCollide(16))
      .stop();
    const ticks = Math.min(300, 80 + nodes.length);
    for (let i = 0; i < ticks; i++) sim.tick();
  } else {
    // Fallback ring layout if the bridge is missing.
    nodes.forEach((n, i) => {
      const a = (2 * Math.PI * i) / Math.max(1, nodes.length);
      n.x = MG_W / 2 + Math.cos(a) * 180;
      n.y = MG_H / 2 + Math.sin(a) * 180;
    });
    links.forEach(l => {
      l.source = nodes.find(n => n.id === l.source) || l.source;
      l.target = nodes.find(n => n.id === l.target) || l.target;
    });
  }
  return { nodes, links };
}

function MemGraphDetail({ node, source, onClose }) {
  return (
    <div className="card mem-graph-detail" data-testid="mem-graph-detail">
      <div className="mem-detail-head">
        <span className="mono">{source === 'entities' ? 'entity' : 'fact'}</span>
        <button className="btn ghost sm pf-form-close" onClick={onClose} aria-label="Close">×</button>
      </div>
      <div className="mem-graph-detail-body mono">
        {source === 'entities' ? (
          <>
            <div className="mem-graph-detail-label">{node.label}</div>
            <div className="mem-graph-detail-row">mentions · <span className="num">{node.mentionCount ?? '—'}</span></div>
          </>
        ) : (
          <>
            <div className="mem-graph-detail-label">{node.text || node.label}</div>
            {node.date && <div className="mem-graph-detail-row">when · {node.date}</div>}
            {node.context && <div className="mem-graph-detail-row">context · {node.context}</div>}
            {node.entities && <div className="mem-graph-detail-row">entities · {node.entities}</div>}
          </>
        )}
      </div>
    </div>
  );
}

function MemGraphExplorer() {
  const useMemoryBanks = window.__hal0UseMemoryBanks;
  const useBankGraph = window.__hal0UseBankGraph;
  const useEntityGraph = window.__hal0UseEntityGraph;

  const banksQuery = useMemoryBanks ? useMemoryBanks() : { data: null };
  const banks = banksQuery.data?.banks || [];

  const [bankSel, setBankSel] = useStateMG(null);
  const [source, setSource] = useStateMG('memories'); // memories | entities
  const [typeFilter, setTypeFilter] = useStateMG('');
  const [minCount, setMinCount] = useStateMG(1);
  const [qDraft, setQDraft] = useStateMG('');
  const [q, setQ] = useStateMG('');
  const [zoom, setZoom] = useStateMG(1);
  const [selected, setSelected] = useStateMG(null);

  const bank = bankSel || banks[0]?.bank_id || null;

  const memQuery = useBankGraph
    ? useBankGraph(source === 'memories' ? bank : null, { type: typeFilter || undefined, q: q || undefined, limit: 300 })
    : { data: null, isLoading: false };
  const entQuery = useEntityGraph
    ? useEntityGraph(source === 'entities' ? bank : null, { min_count: minCount, limit: 500 })
    : { data: null, isLoading: false };

  const active = source === 'entities' ? entQuery : memQuery;
  const payload = active.data;

  const { nodes, links } = useMemoMG(() => {
    const rawNodes = (payload?.nodes || []).map(n => ({ ...(n.data || {}) }));
    const rawEdges = (payload?.edges || []).map(e => ({ ...(e.data || {}) }));
    return mgLayout(rawNodes, rawEdges);
  }, [payload]);

  const vbW = MG_W / zoom;
  const vbH = MG_H / zoom;
  const vbX = (MG_W - vbW) / 2;
  const vbY = (MG_H - vbH) / 2;

  const radius = (n) =>
    source === 'entities' ? Math.min(16, 6 + Math.sqrt(n.mentionCount || 1) * 2.5) : 7;

  return (
    <div className="mem-graph" data-testid="mem-graph-explorer">
      <div className="mem-graph-toolbar">
        <div className="mem-graph-sources">
          <button
            className={'btn ghost xs' + (source === 'memories' ? ' active' : '')}
            onClick={() => { setSource('memories'); setSelected(null); }}
            data-testid="mem-graph-source-memories"
          >
            Memories
          </button>
          <button
            className={'btn ghost xs' + (source === 'entities' ? ' active' : '')}
            onClick={() => { setSource('entities'); setSelected(null); }}
            data-testid="mem-graph-source-entities"
          >
            Entities
          </button>
        </div>
        <select
          className="input mono mem-graph-select"
          value={bank || ''}
          onChange={e => { setBankSel(e.target.value); setSelected(null); }}
          data-testid="mem-graph-bank"
          aria-label="Bank"
        >
          {banks.map(b => (
            <option key={b.bank_id} value={b.bank_id}>{b.bank_id}</option>
          ))}
        </select>
        {source === 'memories' ? (
          <>
            <select
              className="input mono mem-graph-select"
              value={typeFilter}
              onChange={e => setTypeFilter(e.target.value)}
              data-testid="mem-graph-type"
              aria-label="Fact type"
            >
              <option value="">all types</option>
              <option value="world">world</option>
              <option value="experience">experience</option>
              <option value="observation">observation</option>
            </select>
            <input
              className="input mono mem-graph-q"
              value={qDraft}
              placeholder="filter facts… (enter)"
              onChange={e => setQDraft(e.target.value)}
              onKeyDown={e => { if (e.key === 'Enter') setQ(qDraft.trim()); }}
              onBlur={() => setQ(qDraft.trim())}
              data-testid="mem-graph-q"
            />
          </>
        ) : (
          <label className="mono mem-graph-mincount">
            min mentions
            <input
              type="number"
              min="1"
              className="input mono"
              style={{ width: 56 }}
              value={minCount}
              onChange={e => setMinCount(Math.max(1, Number(e.target.value) || 1))}
              data-testid="mem-graph-mincount"
            />
          </label>
        )}
        <div className="mem-graph-zoom">
          <button className="btn ghost xs" onClick={() => setZoom(z => Math.min(4, z * 1.4))} aria-label="Zoom in">+</button>
          <button className="btn ghost xs" onClick={() => setZoom(z => Math.max(0.5, z / 1.4))} aria-label="Zoom out">−</button>
          <button className="btn ghost xs" onClick={() => setZoom(1)} aria-label="Reset zoom">fit</button>
        </div>
      </div>

      <div className="mem-graph-meta mono" data-testid="mem-graph-meta">
        {active.isLoading
          ? 'loading graph…'
          : `${nodes.length} nodes · ${links.length} edges` +
            (payload?.total_units != null ? ` · ${payload.total_units} units` : '') +
            (payload?.total_entities != null ? ` · ${payload.total_entities} entities` : '')}
      </div>

      <div className="mem-graph-stage">
        <svg
          viewBox={`${vbX} ${vbY} ${vbW} ${vbH}`}
          className="mem-graph-svg"
          data-testid="mem-graph-svg"
          role="img"
          aria-label="memory knowledge graph"
        >
          {links.map(l => (
            <line
              key={l.id}
              className="mem-gedge"
              x1={l.source.x} y1={l.source.y}
              x2={l.target.x} y2={l.target.y}
              stroke={MEM_LINK_COLORS[l.linkType] || 'var(--line-strong)'}
              strokeWidth={Math.max(0.6, Math.min(3, (l.weight || 1) * 1.2))}
              strokeOpacity="0.55"
            >
              <title>{l.linkType}</title>
            </line>
          ))}
          {nodes.map(n => (
            <g key={n.id} className="mem-gnode-g" onClick={() => setSelected(n)}>
              <circle
                className={'mem-gnode' + (selected?.id === n.id ? ' selected' : '')}
                cx={n.x} cy={n.y} r={radius(n)}
                fill={n.color || 'var(--info)'}
                fillOpacity="0.85"
                stroke={selected?.id === n.id ? 'var(--hal0-accent)' : 'var(--bg)'}
                strokeWidth={selected?.id === n.id ? 2 : 1}
              >
                <title>{n.label || n.text || n.id}</title>
              </circle>
              {(source === 'entities' || nodes.length <= 40) && (
                <text className="mem-gnode-lbl" x={n.x} y={n.y - radius(n) - 4} textAnchor="middle">
                  {(n.label || '').slice(0, 24)}
                </text>
              )}
            </g>
          ))}
        </svg>
        {!active.isLoading && nodes.length === 0 && (
          <div className="empty mono mem-graph-empty">No graph data for this bank/filter.</div>
        )}
      </div>

      <div className="mem-legend mono">
        {Object.entries(MEM_LINK_COLORS).map(([t, c]) => (
          <span key={t} className="mem-legend-item">
            <span className="mem-swatch" style={{ background: c }} />
            {t}
          </span>
        ))}
      </div>

      {selected && (
        <MemGraphDetail node={selected} source={source} onClose={() => setSelected(null)} />
      )}
    </div>
  );
}

Object.assign(window, { MemGraphExplorer });
