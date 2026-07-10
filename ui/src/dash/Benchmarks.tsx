/**
 * Benchmarks Dashboard Page — replica of the benchlab roster board.
 *
 * Data: /api/benchlab/roster          → model list with decode_ts, prefill_ts,
 *                                       accept, caps, spec, kv, size_gb, detail,
 *                                       name, hf_repo, runs, last_run
 *       /api/benchlab/cells?model=X    → per-lane × per-depth matrix
 *       /api/benchlab/history?model=X  → sparkline points
 *       /api/benchlab/runs?model=X     → run records for the detail drawer
 *       /api/benchlab/runs/RUN_ID      → single run detail (drawer)
 */

import React, { useState, useEffect, useCallback } from 'react';

/* ── types ── */

interface RosterModel {
  id: string;
  name: string;
  hf_repo?: string;
  decode_ts: number | null;
  prefill_ts: number | null;
  accept: number | null;
  caps: string[];
  spec: string | null;
  kv: string;
  size_gb: number | null;
  detail: {
    run_id: string; measured: string; lane: string;
    image: string; llamacpp_build: string;
    depth: number; sampler: string; reps: number;
    stddev: number; ttft_ms_p50: number;
    history?: { date: string; decode_ts: number }[];
  };
  runs: number;
  last_run: string;
  measured?: boolean;
}

interface RosterResponse {
  schema: number;
  host: { gpu: string; mem_gb: number; hal0: string };
  models: RosterModel[];
}

interface CellRecord {
  lane: string; depth: number; config: string;
  record?: { summary?: { decode_ts_med?: number | null; prefill_ts_med?: number | null } };
  decode_ts_med?: number | null;
}

interface HistoryPoint {
  ts: string; decode_ts_med?: number | null; prefill_ts_med?: number | null;
}

interface RunRecord {
  run_id: string; lane: string; depth: number; config?: string;
  kind: string; reps: number; outcome: string;
}

/* ── helpers ── */

const fmt = (v: number): string => (Math.abs(v) >= 100 ? v.toFixed(0) : v.toFixed(1));
const dash = (v: number | null | undefined, u: string = ''): string =>
  v != null ? fmt(v) + (u ? `<span class=\"u\">${u}</span>` : '') : '<span class=\"dim\">\u2014</span>';
const cleanName = (id: string): string =>
  id && id.includes('/') ? id.split('/').pop()!.replace(/\.gguf$/i, '') : (id || '?');
const laneLabel = (l: string): string => ({ rocm: 'ROCM', vulkan_radv: 'VULK' } as any)[l] || String(l || '?').toUpperCase();
const runDate = (rid: string): string => (rid || '').slice(0, 10);
const runTime = (rid: string): string => ((rid || '').slice(11, 16));

const CAP_ALIAS: Record<string, string> = { mtp: 'mtp', vision: 'vision', tools: 'tools', 'tool-calling': 'tools', agent: 'tools', coder: 'coding', coding: 'coding', chat: 'chat' };

function capsIcons(caps: string[]): string {
  const keys: string[] = [];
  for (const c of caps || []) { const k = CAP_ALIAS[String(c).toLowerCase()]; if (k && !keys.includes(k)) keys.push(k); }
  if (!keys.length) return '<span class=\"capnone\">\u2014</span>';
  const svgs: Record<string, string> = {
    mtp: `<svg viewBox="0 0 16 16" fill="currentColor"><path d="M8.7 1 3 9h3.6l-1 6 6.4-8H9.1z"/></svg>`,
    vision: `<svg viewBox="0 0 16 16" fill="none" stroke="currentColor" stroke-width="1.4"><path d="M1 8s2.6-4.6 7-4.6S15 8 15 8s-2.6 4.6-7 4.6S1 8 1 8z"/><circle cx="8" cy="8" r="1.9"/></svg>`,
    tools: `<svg viewBox="0 0 16 16" fill="none" stroke="currentColor" stroke-width="1.4" stroke-linejoin="round"><path d="M10.3 1.6a3.4 3.4 0 0 0-3.2 4.5L1.7 11.4 4 13.7l5.3-5.4a3.4 3.4 0 0 0 4.5-3.2l-2 2-2-2z"/></svg>`,
    coding: `<svg viewBox="0 0 16 16" fill="none" stroke="currentColor" stroke-width="1.4" stroke-linecap="round" stroke-linejoin="round"><polyline points="5 4 1.6 8 5 12"/><polyline points="11 4 14.4 8 11 12"/></svg>`,
    chat: `<svg viewBox="0 0 16 16" fill="none" stroke="currentColor" stroke-width="1.4" stroke-linejoin="round"><path d="M2 3.5h12v7H6.5L3.5 13V10.5H2z"/></svg>`,
  };
  const capColors: Record<string, string> = { mtp: 'var(--accent)', vision: 'var(--info)', tools: 'var(--ok)', coding: 'var(--ok)', chat: 'var(--fg-2)' };
  return keys.map(k => {
    const c = capColors[k] || 'var(--fg-3)';
    return `<span title="${k}" style="display:inline-flex;width:1.05rem;height:1.05rem;margin-right:0.15rem;color:${c};vertical-align:middle">${svgs[k] || ''}</span>`;
  }).join('');
}

/* ── inline SVG sparkline ── */

function sparklineSVG(points: HistoryPoint[]): string {
  const W = 260, H = 54, pad = 6;
  const hasDec = points.some(p => typeof p.decode_ts_med === 'number');
  if (!hasDec) return `<svg viewBox="0 0 ${W} ${H}" width="100%" height="${H}"><text x="${W/2}" y="${H/2+3}" text-anchor="middle" fill="var(--fg-4)" font-size="9">no series yet</text></svg>`;

  const pts = points.map((p, i) => ({ i, v: p.decode_ts_med! })).filter(p => p.v != null);
  if (pts.length < 2) return `<svg viewBox="0 0 ${W} ${H}" width="100%" height="${H}"><text x="${W/2}" y="${H/2+3}" text-anchor="middle" fill="var(--fg-4)" font-size="9">1 data point</text></svg>`;

  const ys = pts.map(p => p.v);
  const min = Math.min(...ys), max = Math.max(...ys), span = max - min || 1;
  const N = points.length;
  const x = (i: number) => pad + (N === 1 ? (W - 2 * pad) / 2 : (i * (W - 2 * pad)) / (N - 1));
  const y = (v: number) => H - pad - ((v - min) / span) * (H - 2 * pad);
  const path = pts.map((p, k) => `${k ? 'L' : 'M'}${x(p.i).toFixed(1)},${y(p.v).toFixed(1)}`).join(' ');
  const dots = pts.map(p => `<circle cx="${x(p.i).toFixed(1)}" cy="${y(p.v).toFixed(1)}" r="1.7" fill="var(--accent)"><title>${fmt(p.v)} t/s</title></circle>`).join('');

  return `<svg viewBox="0 0 ${W} ${H}" width="100%" height="${H}">
    <path d="${path}" fill="none" stroke="var(--accent)" stroke-width="1.5"/>
    ${dots}
    <text x="${pad}" y="10" fill="var(--fg-4)" font-size="8">${fmt(max)}</text>
    <text x="${pad}" y="${H-1}" fill="var(--fg-4)" font-size="8">${fmt(min)}</text>
  </svg>`;
}

/* ── components ── */

const Benchmarks: React.FC = () => {
  const [roster, setRoster] = useState<RosterModel[]>([]);
  const [host, setHost] = useState<{ gpu: string; mem_gb: number; hal0: string } | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [expanded, setExpanded] = useState<string | null>(null);
  // detail data cache
  const [detailCache, setDetailCache] = useState<Record<string, { cells: CellRecord[]; points: HistoryPoint[]; runs: RunRecord[] }>>({});

  useEffect(() => {
    fetch('/api/benchlab/roster')
      .then(r => r.json())
      .then((data: RosterResponse) => {
        setRoster(data.models || []);
        setHost(data.host || null);
        setLoading(false);
      })
      .catch(e => { setError(e.message); setLoading(false); });
  }, []);

  const toggleExpand = useCallback(async (id: string) => {
    if (expanded === id) { setExpanded(null); return; }
    setExpanded(id);
    if (detailCache[id]) return;
    try {
      const [cellsR, histR, runsR] = await Promise.all([
        fetch(`/api/benchlab/cells?model=${encodeURIComponent(id)}`).then(r => r.json()).catch(() => ({ cells: [] })),
        fetch(`/api/benchlab/history?model=${encodeURIComponent(id)}`).then(r => r.json()).catch(() => ({ points: [] })),
        fetch(`/api/benchlab/runs?model=${encodeURIComponent(id)}&limit=24`).then(r => r.json()).catch(() => ({ runs: [] })),
      ]);
      setDetailCache(prev => ({
        ...prev,
        [id]: { cells: cellsR.cells || [], points: histR.points || [], runs: runsR.runs || [] },
      }));
    } catch {}
  }, [expanded, detailCache]);

  if (loading) return <div className="p-4" style={{ color: 'var(--fg-3)' }}>Loading benchmarks\u2026</div>;
  if (error) return <div className="p-4" style={{ color: 'var(--warn)' }}>Error: {error}</div>;

  return (
    <div style={{ padding: '1rem 1.25rem 4rem', maxWidth: 1180, margin: '0 auto' }}>
      {/* Header */}
      <div className="section-head" style={{ display: 'flex', alignItems: 'baseline', gap: '0.6rem', margin: '0.2rem 0 0.6rem' }}>
        <h2 style={{ fontSize: '0.9rem', margin: 0, fontWeight: 600 }}>Roster board</h2>
        <span className="sub" style={{ color: 'var(--fg-4)', fontSize: 11 }}>click a model to expand</span>
        {host && (
          <span style={{ marginLeft: 'auto', fontSize: 11, color: 'var(--fg-4)' }}>
            {host.gpu} &middot; {host.mem_gb} GB &middot; hal0 v{host.hal0} &middot; {roster.length} models
          </span>
        )}
      </div>

      {/* Roster table */}
      <table style={{
        borderCollapse: 'separate', borderSpacing: 0, width: '100%',
        fontFamily: 'var(--jbm, monospace)', fontSize: 12,
      }}>
        <colgroup>
          <col />
          <col style={{ width: '5.5rem' }} />
          <col style={{ width: '5.5rem' }} />
          <col style={{ width: '3.4rem' }} />
          <col style={{ width: '5rem' }} />
          <col style={{ width: '5.5rem' }} />
          <col style={{ width: '4rem' }} />
          <col style={{ width: '6rem' }} />
          <col style={{ width: '4rem' }} />
          <col style={{ width: '3.4rem' }} />
        </colgroup>
        <thead>
          <tr>
            {['model', 'decode', 'prefill', 'acc', 'caps', 'spec / kv', 'size', 'version', 'last run', 'runs'].map(h => (
              <th key={h} style={{
                position: 'sticky', top: 0, zIndex: 2, background: 'var(--bg-1)',
                fontFamily: 'var(--jbm)', fontSize: 9, letterSpacing: '0.07em',
                textTransform: 'uppercase' as const, color: 'var(--fg-4)',
                fontWeight: 600, textAlign: h === 'model' || h === 'caps' || h === 'spec / kv' || h === 'version' ? 'left' : 'right',
                padding: '0.45rem 0.7rem', borderBottom: '1px solid var(--line)',
              }}>
                {h}
              </th>
            ))}
          </tr>
        </thead>
        <tbody>
          {roster.map(m => (
            <ModelRow
              key={m.id}
              model={m}
              expanded={expanded === m.id}
              onToggle={() => toggleExpand(m.id)}
              detail={detailCache[m.id]}
            />
          ))}
        </tbody>
      </table>
    </div>
  );
};

/* ── ModelRow ── */

function ModelRow({ model: m, expanded, onToggle, detail }: {
  model: RosterModel;
  expanded: boolean;
  onToggle: () => void;
  detail?: { cells: CellRecord[]; points: HistoryPoint[]; runs: RunRecord[] };
}) {
  const d = m.detail || {};
  const specKv = [m.spec, m.kv].filter(Boolean).join(' / ') || '\u2014';
  const ver = (d.llamacpp_build || '').split('-')[0] || '\u2014';

  return (
    <>
      <tr
        onClick={onToggle}
        className={m.measured === false ? 'dim' : ''}
        style={{ cursor: 'pointer', background: expanded ? 'var(--bg-2)' : undefined }}
      >
        <td style={{ padding: '0.5rem 0.7rem', borderBottom: '1px solid var(--line-soft)' }}>
          <span style={{ display: 'inline-block', width: '0.7rem', color: expanded ? 'var(--accent)' : 'var(--fg-4)', transition: 'transform 0.15s', transform: expanded ? 'rotate(90deg)' : undefined }}>{'\u25B8'}</span>
          {' '}
          <span style={{ fontFamily: 'var(--jbm)', fontSize: 12, color: 'var(--fg)' }}>
            {m.name || cleanName(m.id)}
          </span>
          {m.hf_repo && (
            <a
              href={`https://huggingface.co/${m.hf_repo}`}
              target="_blank" rel="noopener"
              onClick={e => e.stopPropagation()}
              style={{
                fontFamily: 'var(--jbm)', fontSize: 9.5, color: 'var(--fg-4)',
                textDecoration: 'none', marginLeft: 8,
              }}
            >
              {m.hf_repo} {'\u2197'}
            </a>
          )}
        </td>
        <td style={{ padding: '0.5rem 0.7rem', borderBottom: '1px solid var(--line-soft)', textAlign: 'right', fontFamily: 'var(--jbm)', fontVariantNumeric: 'tabular-nums' as any }}>
          <span dangerouslySetInnerHTML={{ __html: dash(m.decode_ts, ' t/s') }} />
        </td>
        <td style={{ padding: '0.5rem 0.7rem', borderBottom: '1px solid var(--line-soft)', textAlign: 'right', fontFamily: 'var(--jbm)', fontVariantNumeric: 'tabular-nums' as any }}>
          <span dangerouslySetInnerHTML={{ __html: dash(m.prefill_ts, ' t/s') }} />
        </td>
        <td style={{ padding: '0.5rem 0.7rem', borderBottom: '1px solid var(--line-soft)', textAlign: 'right' }}>
          {m.accept != null
            ? <span style={{ fontFamily: 'var(--jbm)', fontVariantNumeric: 'tabular-nums' as any }}>{Math.round(m.accept * 100)}<span style={{ fontSize: 9, color: 'var(--fg-4)', marginLeft: 2 }}>%</span></span>
            : <span style={{ color: 'var(--fg-4)' }}>{'\u2014'}</span>
          }
        </td>
        <td style={{ padding: '0.5rem 0.7rem', borderBottom: '1px solid var(--line-soft)' }}>
          <span dangerouslySetInnerHTML={{ __html: capsIcons(m.caps) }} />
        </td>
        <td style={{ padding: '0.5rem 0.7rem', borderBottom: '1px solid var(--line-soft)', fontSize: 11, color: 'var(--fg-3)' }}>
          {specKv}
        </td>
        <td style={{ padding: '0.5rem 0.7rem', borderBottom: '1px solid var(--line-soft)', textAlign: 'right', fontFamily: 'var(--jbm)', fontVariantNumeric: 'tabular-nums' as any }}>
          {m.size_gb != null ? fmt(m.size_gb) + ' GB' : <span style={{ color: 'var(--fg-4)' }}>{'\u2014'}</span>}
        </td>
        <td style={{ padding: '0.5rem 0.7rem', borderBottom: '1px solid var(--line-soft)', fontSize: 11, color: 'var(--fg-3)' }}>
          {ver}
        </td>
        <td style={{ padding: '0.5rem 0.7rem', borderBottom: '1px solid var(--line-soft)', textAlign: 'right', fontSize: 11, color: 'var(--fg-4)' }}>
          {m.last_run ? m.last_run : '\u2014'}
        </td>
        <td style={{ padding: '0.5rem 0.7rem', borderBottom: '1px solid var(--line-soft)', textAlign: 'right', fontFamily: 'var(--jbm)', fontVariantNumeric: 'tabular-nums' as any }}>
          {m.runs || 0}
        </td>
      </tr>
      <tr style={{ display: expanded ? undefined : 'none' }}>
        <td colSpan={10} style={{ padding: 0, borderBottom: '1px solid var(--line)', background: 'var(--bg-1)' }}>
          <div style={{ padding: '0.9rem 1.1rem 1.1rem' }}>
            {detail ? (
              <DetailInner model={m} detail={detail} />
            ) : (
              <div style={{ color: 'var(--fg-4)', fontStyle: 'italic' }}>loading\u2026</div>
            )}
          </div>
        </td>
      </tr>
    </>
  );
}

/* ── DetailInner ── */

function DetailInner({ model: m, detail }: { model: RosterModel; detail: { cells: CellRecord[]; points: HistoryPoint[]; runs: RunRecord[] } }) {
  const d = m.detail || {};
  const { cells, points, runs } = detail;

  // matrix: one cell per (lane, depth, config)
  const matrix = (() => {
    const groups = new Map<string, { lane: string; depth: number; config: string; decode: number | null; prefill: number | null }>();
    for (const c of cells) {
      const cfg = c.config || 'default';
      const key = `${c.lane}|${c.depth}|${cfg}`;
      let g = groups.get(key);
      if (!g) { g = { lane: c.lane, depth: c.depth, config: cfg, decode: null, prefill: null }; groups.set(key, g); }
      const sum = (c.record || {}).summary || {};
      if (g.decode == null) g.decode = c.decode_ts_med ?? sum.decode_ts_med ?? null;
      if (g.prefill == null) g.prefill = sum.prefill_ts_med ?? null;
    }
    return [...groups.values()].filter(g => g.decode != null || g.prefill != null)
      .sort((a, b) => String(a.lane).localeCompare(String(b.lane)) || (a.depth - b.depth));
  })();

  // group runs by (lane, depth, config) and cluster within 2 minutes
  const runGroups = (() => {
    const byLD = new Map<string, RunRecord[]>();
    for (const r of runs) {
      const k = `${r.lane}|${r.depth}|${r.config || 'default'}`;
      if (!byLD.has(k)) byLD.set(k, []);
      byLD.get(k)!.push(r);
    }
    const groups: { lane: string; depth: number; config: string; t: number; runs: RunRecord[] }[] = [];
    for (const list of byLD.values()) {
      list.sort((a, b) => String(a.run_id).localeCompare(String(b.run_id)));
      let cur: typeof groups[0] | null = null;
      for (const r of list) {
        const t = Date.parse((r.run_id || '').slice(0, 20)) || 0;
        if (cur && Math.abs(t - cur.t) <= 120000) { cur.runs.push(r); cur.t = t; }
        else { cur = { lane: r.lane, depth: r.depth, config: r.config || 'default', t, runs: [r] }; groups.push(cur); }
      }
    }
    groups.sort((a, b) => b.t - a.t);
    return groups;
  })();

  return (
    <div style={{ display: 'grid', gap: '1rem', gridTemplateColumns: '1.1fr 1fr' }}>
      {/* Left: stats + matrix */}
      <div>
        <h4 style={{ margin: '0 0 0.5rem', fontFamily: 'var(--jbm)', fontSize: 9, letterSpacing: '0.08em', textTransform: 'uppercase', color: 'var(--fg-4)', fontWeight: 600 }}>
          current summary
        </h4>
        <div style={{ display: 'flex', flexWrap: 'wrap', gap: '0.4rem' }}>
          {[
            ['decode', m.decode_ts, 't/s', true],
            ['prefill', m.prefill_ts, 't/s', false],
            ['accept', m.accept != null ? Math.round(m.accept * 100) : null, '%', false],
            ['ttft p50', d.ttft_ms_p50, 'ms', false],
            ['stddev', d.stddev, '', false],
            ['size', m.size_gb ?? null, 'GB', false],
            ['reps', d.reps ?? null, '', false],
          ].map(([label, val, unit, accent]) => (
            <div key={label as string} style={{
              border: '1px solid var(--line)', borderRadius: '0.4rem', padding: '0.4rem 0.6rem',
              background: 'var(--bg-2)', minWidth: 92,
            }}>
              <div style={{
                fontFamily: 'var(--jbm)', fontSize: 15,
                color: accent ? 'var(--accent)' : 'var(--fg)',
                fontVariantNumeric: 'tabular-nums' as any,
              }}>
                {val != null ? fmt(val as number) + (unit ? ` ${unit}` : '') : '\u2014'}
              </div>
              <div style={{ fontFamily: 'var(--jbm)', fontSize: 8.5, letterSpacing: '0.06em', textTransform: 'uppercase', color: 'var(--fg-4)', marginTop: 2 }}>
                {label as string}
              </div>
            </div>
          ))}
        </div>

        <h4 style={{ margin: '1rem 0 0.5rem', fontFamily: 'var(--jbm)', fontSize: 9, letterSpacing: '0.08em', textTransform: 'uppercase', color: 'var(--fg-4)', fontWeight: 600 }}>
          lane &times; depth &times; kind
        </h4>
        <div style={{ display: 'flex', flexWrap: 'wrap', gap: '0.35rem' }}>
          {matrix.length ? matrix.map((g, i) => (
            <div key={i} style={{ border: '1px solid var(--line)', borderRadius: '0.35rem', padding: '0.35rem 0.5rem', background: 'var(--bg-2)', minWidth: 96 }}>
              <div style={{ fontFamily: 'var(--jbm)', fontSize: 8.5, textTransform: 'uppercase', letterSpacing: '0.05em', color: 'var(--fg-4)', marginBottom: 2 }}>
                <span style={{
                  fontFamily: 'var(--jbm)', fontSize: 9, padding: '0.08rem 0.4rem', borderRadius: '0.3rem',
                  border: '1px solid var(--line)',
                  color: g.lane === 'rocm' ? 'var(--dev-rocm)' : 'var(--dev-vulkan)',
                }}>{laneLabel(g.lane)}</span>
                {' '}d{g.depth}
                {g.config !== 'default' && <span style={{ color: 'var(--accent)' }}>{` \u00b7 ${g.config}`}</span>}
              </div>
              <div style={{ fontFamily: 'var(--jbm)', fontSize: 13, color: 'var(--fg-2)' }}>
                {g.decode != null ? fmt(g.decode) + ' t/s' : <span style={{ color: 'var(--fg-4)' }}>\u2014</span>}
              </div>
              {g.prefill != null && (
                <div style={{ fontFamily: 'var(--jbm)', fontSize: 11, color: 'var(--fg-4)' }}>
                  {fmt(g.prefill)} t/s pf
                </div>
              )}
            </div>
          )) : <div style={{ color: 'var(--fg-4)', fontStyle: 'italic' }}>no measured cells for this model.</div>}
        </div>
      </div>

      {/* Right: sparkline + run history */}
      <div>
        <h4 style={{ margin: '0 0 0.5rem', fontFamily: 'var(--jbm)', fontSize: 9, letterSpacing: '0.08em', textTransform: 'uppercase', color: 'var(--fg-4)', fontWeight: 600 }}>
          throughput history
        </h4>
        <div style={{ border: '1px solid var(--line)', borderRadius: '0.4rem', padding: '0.5rem 0.6rem', background: 'var(--bg-2)' }}>
          <div dangerouslySetInnerHTML={{ __html: sparklineSVG(points) }} />
          <div style={{ fontSize: 10, color: 'var(--fg-4)', marginTop: '0.25rem' }}>
            <span style={{ color: 'var(--accent)' }}>{'\u25A0'}</span> decode &middot; {points.length} pt{points.length === 1 ? '' : 's'}
            {d.image && <span> &middot; {d.image.split('/').pop()}</span>}
          </div>
        </div>

        <h4 style={{ margin: '1rem 0 0.5rem', fontFamily: 'var(--jbm)', fontSize: 9, letterSpacing: '0.08em', textTransform: 'uppercase', color: 'var(--fg-4)', fontWeight: 600 }}>
          runs — {runGroups.length} sweep{runGroups.length === 1 ? '' : 's'}
        </h4>
        <div style={{ display: 'flex', flexWrap: 'wrap', gap: '0.3rem' }}>
          {runGroups.length ? runGroups.map((g, i) => {
            const order = ['prefill', 'decode', 'chat', 'cache-reuse', 'embed', 'rerank'];
            const kset = [...new Set(g.runs.map(r => r.kind))];
            const kinds = kset.sort((a, b) => (order.indexOf(a) + 1 || 99) - (order.indexOf(b) + 1 || 99)).join('+');
            const reps = Math.max(0, ...g.runs.map(r => r.reps || 0));
            const meas = reps > 0 ? `${reps} rep${reps === 1 ? '' : 's'}` : `${g.runs.length} rec`;
            const outcomes = g.runs.map(r => r.outcome);
            const ocCls = outcomes.every(o => o === 'ok') ? 'ok' : (outcomes.some(o => o !== 'ok' && o !== 'skipped-contended') ? 'bad' : 'warn');
            const ocColors: Record<string, string> = { ok: 'var(--ok)', warn: 'var(--warn)', bad: 'var(--err)' };
            return (
              <span key={i} style={{
                fontFamily: 'var(--jbm)', fontSize: 10, padding: '0.2rem 0.5rem',
                border: '1px solid var(--line)', borderRadius: '0.3rem',
                background: 'var(--bg-2)', cursor: 'default',
              }}>
                {runDate(g.runs[0].run_id)} {runTime(g.runs[0].run_id)}
                {' '}<span style={{
                  fontFamily: 'var(--jbm)', fontSize: 9, padding: '0.08rem 0.4rem', borderRadius: '0.3rem',
                  border: '1px solid var(--line)',
                  color: g.lane === 'rocm' ? 'var(--dev-rocm)' : 'var(--dev-vulkan)',
                }}>{laneLabel(g.lane)}</span>
                {' '}d{g.depth}
                {g.config !== 'default' && <span style={{ color: 'var(--accent)' }}>{` \u00b7 ${g.config}`}</span>}
                {' '}{kinds} &middot; {meas}
                <span style={{ color: ocColors[ocCls] || 'var(--fg-4)', marginLeft: 3 }}>{'\u25CF'}</span>
              </span>
            );
          }) : <span style={{ color: 'var(--fg-4)', fontStyle: 'italic' }}>no runs recorded.</span>}
        </div>
      </div>
    </div>
  );
}

export default Benchmarks;
