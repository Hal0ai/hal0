/**
 * Benchmarks page — native /api/benchmarks/* over the hal0.bench store.
 *
 * Tabs (slot-tabs pattern, matches models.jsx):
 *   Roster — the model board: hal0 roster (measured + unmeasured) with
 *            decode/prefill/accept, expandable per-model detail (lane×depth
 *            matrix, history sparkline, run chips) and a queue button.
 *   Runs   — run records newest-first with the full-detail drawer (identity
 *            chips, per-rep table, telemetry, artifacts).
 *   Evals  — agentic-eval leaderboard (model × task scores).
 *   Run    — worker control (Start/Pause/Stop + exclusive), the queue, and
 *            the staleness plan.
 *
 * Data: GET  /api/benchmarks/roster|cells|history|runs|runs/{id}|evals|plan|queue
 *       POST /api/benchmarks/queue {model|suite, lanes?, configs?, kind?}
 *       POST /control {action,exclusive} · DELETE /api/benchmarks/queue/{id}
 */

import React, { useCallback, useEffect, useRef, useState } from 'react';
import { apiDelete, apiGet, apiPost } from '../api/client';

/* ── types ── */

interface RosterModel {
  id: string;
  gguf?: string;
  name?: string | null;
  hf_repo?: string | null;
  decode_ts: number | null;
  prefill_ts: number | null;
  accept: number | null;
  caps: string[];
  spec: string | null;
  kv: string | null;
  size_gb: number | null;
  detail: {
    run_id?: string; measured?: string; lane?: string;
    image?: string; llamacpp_build?: string;
    depth?: number; sampler?: string; reps?: number;
    stddev?: number; ttft_ms_p50?: number | null;
  } | null;
  runs: number;
  last_run: string | null;
  measured?: boolean;
}

interface RosterResponse {
  schema: number;
  host: { gpu: string; mem_gb: number; hal0: string };
  models: RosterModel[];
}

interface CellRow {
  lane: string; depth: number; kind: string;
  cell_key?: string; trigger?: string; config?: string; run_id?: string;
  decode_ts_med?: number | null;
  record?: { summary?: { decode_ts_med?: number | null; prefill_ts_med?: number | null }; config?: string };
}

interface HistoryPoint {
  ts: string; decode_ts_med?: number | null; prefill_ts_med?: number | null;
  config?: string; lane?: string; depth?: number; trigger?: string; cell_key?: string;
}

interface RegressionFlag {
  cell_key: string; model_id: string; delta_pct: number;
  newest_ts: string | null; trailing_median: number | null; run_ids: string[];
}

interface RunSummary {
  run_id: string; suite: string; trigger: string; model: string | null;
  lane: string; kind: string; depth: number | null; outcome: string;
  decode_ts_med: number | null; reps: number; config: string;
}

interface QueueState {
  control: { state: string; exclusive: boolean };
  active: any;
  updated: string | null;
  items: { id: string; label: string; suite?: string | null; model?: string | null; enqueued?: string }[];
}

/* ── helpers ── */

const toast = (msg: string, kind: string = 'info') => (window as any).__hal0Toast?.(msg, kind);

const fmt = (v: number): string => (Math.abs(v) >= 100 ? v.toFixed(0) : v.toFixed(1));
const cleanName = (id: string): string =>
  id && id.includes('/') ? id.split('/').pop()!.replace(/\.gguf$/i, '') : (id || '?');
const laneLabel = (l: string): string => ({ rocm: 'ROCM', vulkan_radv: 'VULK' } as any)[l] || String(l || '?').toUpperCase();
const runDate = (rid: string): string => (rid || '').slice(0, 10);
const runTime = (rid: string): string => (rid || '').slice(11, 16);

const OUTCOME_COLOR: Record<string, string> = {
  ok: 'var(--ok)',
  'skipped-contended': 'var(--warn)',
  failed: 'var(--err)',
  oom: 'var(--dev-vulkan, var(--info))',
};
const outcomeColor = (o: string): string => OUTCOME_COLOR[o] || 'var(--fg-4)';

const TRIGGER_COLOR: Record<string, string> = {
  manual: 'var(--fg-3)', scheduled: 'var(--info)', queue: 'var(--accent)',
};
const triggerColor = (t: string): string => TRIGGER_COLOR[t] || 'var(--fg-4)';

function TriggerChip({ t }: { t?: string | null }) {
  if (!t) return <Dash />;
  return (
    <span style={{ ...chipStyle, fontSize: 9, padding: '0.08rem 0.4rem', color: triggerColor(t), background: 'transparent' }}>
      {t}
    </span>
  );
}

const laneColor = (l: string): string =>
  l === 'rocm' ? 'var(--dev-rocm, var(--err))' : l === 'vulkan_radv' ? 'var(--dev-vulkan, var(--info))' : 'var(--fg-3)';

const mono = 'var(--jbm, monospace)';

const cellTd: React.CSSProperties = { padding: '0.5rem 0.7rem', borderBottom: '1px solid var(--line-soft)' };
const numTd: React.CSSProperties = { ...cellTd, textAlign: 'right', fontFamily: mono, fontVariantNumeric: 'tabular-nums' as any };
const thStyle = (align: 'left' | 'right'): React.CSSProperties => ({
  position: 'sticky', top: 0, zIndex: 2, background: 'var(--bg-1)',
  fontFamily: mono, fontSize: 9, letterSpacing: '0.07em',
  textTransform: 'uppercase' as const, color: 'var(--fg-4)',
  fontWeight: 600, textAlign: align,
  padding: '0.45rem 0.7rem', borderBottom: '1px solid var(--line)',
});
const h4Style: React.CSSProperties = {
  margin: '0 0 0.5rem', fontFamily: mono, fontSize: 9, letterSpacing: '0.08em',
  textTransform: 'uppercase', color: 'var(--fg-4)', fontWeight: 600,
};
const chipStyle: React.CSSProperties = {
  fontFamily: mono, fontSize: 10, padding: '0.15rem 0.5rem',
  border: '1px solid var(--line)', borderRadius: '0.3rem', background: 'var(--bg-2)',
  display: 'inline-flex', alignItems: 'center', gap: 4,
};

const Dash = () => <span style={{ color: 'var(--fg-4)' }}>{'—'}</span>;

/* ── bench menu (the queue dropdown) ── */

interface BenchOption {
  key: string;
  label: string;
  desc: string;
  body: Record<string, unknown>; // merged into POST /api/benchmarks/queue {model, ...body}
}

// Flag grid for Tune Bench — every flag must be in the planner's _TUNE_FLAGS
// whitelist or the plan step rejects the variant.
const TUNE_CONFIGS = [
  { label: 'default', flags: {} },
  { label: 'b512-ub256', flags: { '-b': '512', '-ub': '256' } },
  { label: 'b1024-ub512', flags: { '-b': '1024', '-ub': '512' } },
  { label: 'fa-on', flags: { '-fa': '1' } },
  { label: 'kv-q8', flags: { '-ctk': 'q8_0', '-ctv': 'q8_0' } },
];

const BENCH_OPTIONS: BenchOption[] = [
  { key: 'vulkan', label: 'Vulkan Bench', desc: 'pp + tg on the vulkan_radv lane', body: { lanes: ['vulkan_radv'] } },
  { key: 'rocm', label: 'ROCm Bench', desc: 'pp + tg on the rocm lane', body: { lanes: ['rocm'] } },
  { key: 'compare', label: 'Comparison of Both', desc: 'same cells on rocm + vulkan_radv', body: { lanes: ['rocm', 'vulkan_radv'] } },
  { key: 'tools', label: 'Tool Bench', desc: 'agentic tool-calling eval (live endpoint)', body: { kind: 'eval' } },
  { key: 'tune', label: 'Tune Bench', desc: `flag-tuning grid — ${TUNE_CONFIGS.length} configs`, body: { configs: TUNE_CONFIGS } },
];

function QueueMenu({ onPick, label = '+ queue', disabled = false, title }: {
  onPick: (opt: BenchOption) => void;
  label?: string;
  disabled?: boolean;
  title?: string;
}) {
  const [open, setOpen] = useState(false);
  const ref = useRef<HTMLDivElement>(null);

  useEffect(() => {
    if (!open) return;
    const onDown = (e: MouseEvent) => {
      if (ref.current && !ref.current.contains(e.target as Node)) setOpen(false);
    };
    const onKey = (e: KeyboardEvent) => { if (e.key === 'Escape') setOpen(false); };
    document.addEventListener('mousedown', onDown);
    document.addEventListener('keydown', onKey);
    return () => {
      document.removeEventListener('mousedown', onDown);
      document.removeEventListener('keydown', onKey);
    };
  }, [open]);

  return (
    <div ref={ref} style={{ position: 'relative', display: 'inline-block' }} onClick={e => e.stopPropagation()}>
      <button
        className="btn ghost sm"
        aria-haspopup="menu"
        aria-expanded={open}
        disabled={disabled}
        title={title || 'Queue a benchmark run for this model'}
        onClick={() => setOpen(o => !o)}
        style={{ fontSize: 10, padding: '2px 8px' }}
      >
        {label} <span style={{ fontSize: 8, opacity: 0.7 }}>{'▾'}</span>
      </button>
      {open && (
        <div role="menu" style={{
          position: 'absolute', top: 'calc(100% + 4px)', right: 0, zIndex: 30,
          minWidth: 220, padding: 4,
          background: 'var(--bg-1)', border: '1px solid var(--line)',
          borderRadius: 'var(--rad-lg, 8px)', boxShadow: '0 8px 24px rgba(0, 0, 0, 0.35)',
        }}>
          {BENCH_OPTIONS.map(opt => (
            <button
              key={opt.key}
              role="menuitem"
              onClick={() => { setOpen(false); onPick(opt); }}
              style={{
                display: 'block', width: '100%', textAlign: 'left', padding: '6px 9px',
                background: 'transparent', border: 'none', borderRadius: '0.3rem',
                cursor: 'pointer', color: 'var(--fg)',
              }}
              onMouseEnter={e => { e.currentTarget.style.background = 'var(--bg-2)'; }}
              onMouseLeave={e => { e.currentTarget.style.background = 'transparent'; }}
            >
              <div style={{ fontFamily: mono, fontSize: 11 }}>{opt.label}</div>
              <div style={{ fontFamily: mono, fontSize: 9, color: 'var(--fg-4)', marginTop: 1 }}>{opt.desc}</div>
            </button>
          ))}
        </div>
      )}
    </div>
  );
}

function Num({ v, unit }: { v: number | null | undefined; unit?: string }) {
  if (v == null) return <Dash />;
  return (
    <span style={{ fontFamily: mono, fontVariantNumeric: 'tabular-nums' as any }}>
      {fmt(v)}{unit && <span style={{ fontSize: 9, color: 'var(--fg-4)', marginLeft: 2 }}>{unit}</span>}
    </span>
  );
}

const CAP_ALIAS: Record<string, string> = { mtp: 'mtp', vision: 'vision', tools: 'tools', 'tool-calling': 'tools', agent: 'tools', coder: 'coding', coding: 'coding', chat: 'chat' };
const CAP_COLOR: Record<string, string> = { mtp: 'var(--accent)', vision: 'var(--info)', tools: 'var(--ok)', coding: 'var(--ok)', chat: 'var(--fg-2)' };
const CAP_SVG: Record<string, React.ReactNode> = {
  mtp: <svg viewBox="0 0 16 16" fill="currentColor"><path d="M8.7 1 3 9h3.6l-1 6 6.4-8H9.1z" /></svg>,
  vision: <svg viewBox="0 0 16 16" fill="none" stroke="currentColor" strokeWidth="1.4"><path d="M1 8s2.6-4.6 7-4.6S15 8 15 8s-2.6 4.6-7 4.6S1 8 1 8z" /><circle cx="8" cy="8" r="1.9" /></svg>,
  tools: <svg viewBox="0 0 16 16" fill="none" stroke="currentColor" strokeWidth="1.4" strokeLinejoin="round"><path d="M10.3 1.6a3.4 3.4 0 0 0-3.2 4.5L1.7 11.4 4 13.7l5.3-5.4a3.4 3.4 0 0 0 4.5-3.2l-2 2-2-2z" /></svg>,
  coding: <svg viewBox="0 0 16 16" fill="none" stroke="currentColor" strokeWidth="1.4" strokeLinecap="round" strokeLinejoin="round"><polyline points="5 4 1.6 8 5 12" /><polyline points="11 4 14.4 8 11 12" /></svg>,
  chat: <svg viewBox="0 0 16 16" fill="none" stroke="currentColor" strokeWidth="1.4" strokeLinejoin="round"><path d="M2 3.5h12v7H6.5L3.5 13V10.5H2z" /></svg>,
};

function CapIcons({ caps }: { caps: string[] }) {
  const keys: string[] = [];
  for (const c of caps || []) {
    const k = CAP_ALIAS[String(c).toLowerCase()];
    if (k && !keys.includes(k)) keys.push(k);
  }
  if (!keys.length) return <Dash />;
  return (
    <>
      {keys.map(k => (
        <span key={k} title={k} style={{
          display: 'inline-flex', width: '1.05rem', height: '1.05rem', marginRight: '0.15rem',
          color: CAP_COLOR[k] || 'var(--fg-3)', verticalAlign: 'middle',
        }}>{CAP_SVG[k]}</span>
      ))}
    </>
  );
}

/* ── sparkline ── */

function Sparkline({ points }: { points: HistoryPoint[] }) {
  const W = 260, H = 54, pad = 6;
  const pts = points.map((p, i) => ({ i, v: p.decode_ts_med })).filter(p => typeof p.v === 'number') as { i: number; v: number }[];
  if (pts.length < 2) {
    return (
      <svg viewBox={`0 0 ${W} ${H}`} width="100%" height={H}>
        <text x={W / 2} y={H / 2 + 3} textAnchor="middle" fill="var(--fg-4)" fontSize={9}>
          {pts.length === 1 ? '1 data point' : 'no series yet'}
        </text>
      </svg>
    );
  }
  const ys = pts.map(p => p.v);
  const min = Math.min(...ys), max = Math.max(...ys), span = max - min || 1;
  const N = points.length;
  const x = (i: number) => pad + (N === 1 ? (W - 2 * pad) / 2 : (i * (W - 2 * pad)) / (N - 1));
  const y = (v: number) => H - pad - ((v - min) / span) * (H - 2 * pad);
  const path = pts.map((p, k) => `${k ? 'L' : 'M'}${x(p.i).toFixed(1)},${y(p.v).toFixed(1)}`).join(' ');

  // Prefill is a separate signal on its own scale — a lighter, thinner path
  // just to show the trend shape alongside decode, not a directly comparable magnitude.
  const pfPts = points.map((p, i) => ({ i, v: p.prefill_ts_med })).filter(p => typeof p.v === 'number') as { i: number; v: number }[];
  let pfPath = '';
  if (pfPts.length >= 2) {
    const pfYs = pfPts.map(p => p.v);
    const pfMin = Math.min(...pfYs), pfMax = Math.max(...pfYs), pfSpan = pfMax - pfMin || 1;
    const pfY = (v: number) => H - pad - ((v - pfMin) / pfSpan) * (H - 2 * pad);
    pfPath = pfPts.map((p, k) => `${k ? 'L' : 'M'}${x(p.i).toFixed(1)},${pfY(p.v).toFixed(1)}`).join(' ');
  }

  return (
    <svg viewBox={`0 0 ${W} ${H}`} width="100%" height={H}>
      {pfPath && <path d={pfPath} fill="none" stroke="var(--fg-4)" strokeWidth={1} opacity={0.6} />}
      <path d={path} fill="none" stroke="var(--accent)" strokeWidth={1.5} />
      {pts.map(p => (
        <circle key={p.i} cx={x(p.i).toFixed(1)} cy={y(p.v).toFixed(1)} r={1.7} fill="var(--accent)">
          <title>{fmt(p.v)} t/s</title>
        </circle>
      ))}
      <text x={pad} y={10} fill="var(--fg-4)" fontSize={8}>{fmt(max)}</text>
      <text x={pad} y={H - 1} fill="var(--fg-4)" fontSize={8}>{fmt(min)}</text>
    </svg>
  );
}

/* ── root ── */

const Benchmarks: React.FC = () => {
  const [tab, setTab] = useState<'roster' | 'runs' | 'evals' | 'queue'>('roster');
  const [roster, setRoster] = useState<RosterModel[]>([]);
  const [host, setHost] = useState<RosterResponse['host'] | null>(null);
  const [rosterErr, setRosterErr] = useState<string | null>(null);
  const [rosterLoading, setRosterLoading] = useState(true);
  const [queue, setQueue] = useState<QueueState | null>(null);
  const [regressions, setRegressions] = useState<RegressionFlag[]>([]);

  const loadRoster = useCallback(() => {
    setRosterLoading(true);
    apiGet<RosterResponse>('/api/benchmarks/roster')
      .then(d => { setRoster(d.models || []); setHost(d.host || null); setRosterErr(null); })
      .catch(e => setRosterErr(e.message))
      .finally(() => setRosterLoading(false));
    apiGet<{ count: number; flags: RegressionFlag[] }>('/api/benchmarks/regressions')
      .then(d => setRegressions(d.flags || []))
      .catch(() => setRegressions([]));
  }, []);

  const loadQueue = useCallback(() => {
    apiGet<QueueState>('/api/benchmarks/queue').then(setQueue).catch(() => {});
  }, []);

  useEffect(() => { loadRoster(); }, [loadRoster]);
  useEffect(() => {
    loadQueue();
    const t = setInterval(loadQueue, 3000);
    return () => clearInterval(t);
  }, [loadQueue]);

  const enqueueBench = useCallback(async (id: string, opt: BenchOption) => {
    try {
      await apiPost('/api/benchmarks/queue', { model: id, ...opt.body });
      toast(`Queued ${opt.label} for ${cleanName(id)} — start the worker on the Run tab`, 'ok');
      loadQueue();
    } catch (e: any) { toast(`Queue failed: ${e.message}`, 'err'); }
  }, [loadQueue]);

  const measured = roster.filter(m => m.measured !== false).length;
  const workerState = queue?.control?.state || 'stopped';

  return (
    <div className="view">
      <div className="vh">
        <span className="vh-eye mono">Performance</span>
        <h1>Benchmarks</h1>
        <span className="vh-spacer" />
        {host && (
          <span style={{ fontFamily: mono, fontSize: 11, color: 'var(--fg-4)' }}>
            {host.gpu} &middot; {host.mem_gb} GB &middot; hal0 v{host.hal0}
          </span>
        )}
        <button className="btn ghost sm" onClick={loadRoster}>Refresh</button>
      </div>

      {/* ── Tab bar (matches slot-tabs pattern) ── */}
      <div className="slot-tabs" role="tablist" style={{ marginBottom: 0 }}>
        {([
          ['roster', 'Roster', `${measured}/${roster.length}`],
          ['runs', 'Runs', null],
          ['evals', 'Evals', null],
          ['queue', 'Run Queue', String(queue?.items?.length ?? 0)],
        ] as [typeof tab, string, string | null][]).map(([id, label, ct]) => (
          <button
            key={id}
            role="tab"
            aria-selected={tab === id}
            className={'slot-tab' + (tab === id ? ' on' : '')}
            onClick={() => setTab(id)}
          >
            <span>{label}</span>
            {ct != null && <span className="slot-tab-ct num">{ct}</span>}
            {id === 'queue' && (
              <span title={`worker: ${workerState}`} style={{
                width: 7, height: 7, borderRadius: '50%',
                background: workerState === 'running' ? 'var(--ok)' : workerState === 'paused' ? 'var(--warn)' : 'var(--fg-5)',
              }} />
            )}
          </button>
        ))}
      </div>

      <div style={{ marginTop: 18 }}>
        {tab === 'roster' && (
          <RosterTab roster={roster} loading={rosterLoading} error={rosterErr} onQueue={enqueueBench} regressions={regressions} />
        )}
        {tab === 'runs' && <RunsTab regressions={regressions} />}
        {tab === 'evals' && <EvalsTab />}
        {tab === 'queue' && (
          <QueueTab queue={queue} roster={roster} refresh={loadQueue} onQueueModel={enqueueBench} />
        )}
      </div>
    </div>
  );
};

/* ── Roster tab ── */

function RosterTab({ roster, loading, error, onQueue, regressions }: {
  roster: RosterModel[]; loading: boolean; error: string | null;
  onQueue: (id: string, opt: BenchOption) => void;
  regressions: RegressionFlag[];
}) {
  const [expanded, setExpanded] = useState<string | null>(null);
  const [detailCache, setDetailCache] = useState<Record<string, { cells: CellRow[]; points: HistoryPoint[]; runs: RunSummary[] }>>({});

  const toggleExpand = useCallback(async (id: string) => {
    if (expanded === id) { setExpanded(null); return; }
    setExpanded(id);
    if (detailCache[id]) return;
    const q = encodeURIComponent(id);
    const [cellsR, histR, runsR] = await Promise.all([
      apiGet<{ cells: CellRow[] }>(`/api/benchmarks/cells?model=${q}`).catch(() => ({ cells: [] })),
      apiGet<{ points: HistoryPoint[] }>(`/api/benchmarks/history?model=${q}`).catch(() => ({ points: [] })),
      apiGet<{ runs: RunSummary[] }>(`/api/benchmarks/runs?model=${q}&limit=24`).catch(() => ({ runs: [] })),
    ]);
    setDetailCache(prev => ({
      ...prev,
      [id]: { cells: cellsR.cells || [], points: histR.points || [], runs: runsR.runs || [] },
    }));
  }, [expanded, detailCache]);

  if (loading) return <div style={{ color: 'var(--fg-3)' }}>Loading benchmarks&hellip;</div>;
  if (error) return <div style={{ color: 'var(--warn)' }}>Error: {error}</div>;

  const regByModel = new Map<string, RegressionFlag[]>();
  for (const f of regressions) {
    if (!regByModel.has(f.model_id)) regByModel.set(f.model_id, []);
    regByModel.get(f.model_id)!.push(f);
  }

  return (
    <table style={{ borderCollapse: 'separate', borderSpacing: 0, width: '100%', fontFamily: mono, fontSize: 12 }}>
      <colgroup>
        <col />
        <col style={{ width: '5.5rem' }} />
        <col style={{ width: '5.5rem' }} />
        <col style={{ width: '3.4rem' }} />
        <col style={{ width: '5rem' }} />
        <col style={{ width: '5.5rem' }} />
        <col style={{ width: '4rem' }} />
        <col style={{ width: '5.6rem' }} />
        <col style={{ width: '3.2rem' }} />
        <col style={{ width: '4.2rem' }} />
      </colgroup>
      <thead>
        <tr>
          {(['model', 'decode', 'prefill', 'acc', 'caps', 'spec / kv', 'size', 'last run', 'runs', ''] as const).map((h, i) => (
            <th key={i} style={thStyle(h === 'model' || h === 'caps' || h === 'spec / kv' ? 'left' : 'right')}>{h}</th>
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
            onQueue={opt => onQueue(m.id, opt)}
            detail={detailCache[m.id]}
            flags={regByModel.get(m.id)}
          />
        ))}
      </tbody>
    </table>
  );
}

function ModelRow({ model: m, expanded, onToggle, onQueue, detail, flags }: {
  model: RosterModel;
  expanded: boolean;
  onToggle: () => void;
  onQueue: (opt: BenchOption) => void;
  detail?: { cells: CellRow[]; points: HistoryPoint[]; runs: RunSummary[] };
  flags?: RegressionFlag[];
}) {
  const specKv = [m.spec, m.kv].filter(Boolean).join(' / ') || '—';
  const worstFlag = flags && flags.length
    ? flags.reduce((worst, f) => f.delta_pct < worst.delta_pct ? f : worst, flags[0])
    : null;
  return (
    <>
      <tr
        onClick={onToggle}
        style={{ cursor: 'pointer', background: expanded ? 'var(--bg-2)' : undefined, opacity: m.measured === false ? 0.55 : 1 }}
      >
        <td style={cellTd}>
          <span style={{
            display: 'inline-block', width: '0.7rem', color: expanded ? 'var(--accent)' : 'var(--fg-4)',
            transition: 'transform 0.15s', transform: expanded ? 'rotate(90deg)' : undefined,
          }}>{'▸'}</span>
          {' '}
          <span style={{ fontFamily: mono, fontSize: 12, color: 'var(--fg)' }}>{m.name || cleanName(m.id)}</span>
          {m.hf_repo && (
            <a
              href={`https://huggingface.co/${m.hf_repo}`}
              target="_blank" rel="noopener noreferrer"
              onClick={e => e.stopPropagation()}
              style={{ fontFamily: mono, fontSize: 9.5, color: 'var(--fg-4)', textDecoration: 'none', marginLeft: 8 }}
            >
              {m.hf_repo} {'↗'}
            </a>
          )}
          {m.measured === false && (
            <span style={{ ...chipStyle, marginLeft: 8, fontSize: 9, color: 'var(--fg-4)' }}>unmeasured</span>
          )}
          {worstFlag && (
            <span
              title={`flagged runs: ${[...new Set(flags!.flatMap(f => f.run_ids))].join(', ')}`}
              style={{ ...chipStyle, marginLeft: 8, fontSize: 9, color: 'var(--err)', borderColor: 'var(--err)' }}
            >
              {'▼'} {Math.abs(worstFlag.delta_pct).toFixed(1)}%
            </span>
          )}
        </td>
        <td style={numTd}><Num v={m.decode_ts} unit=" t/s" /></td>
        <td style={numTd}><Num v={m.prefill_ts} unit=" t/s" /></td>
        <td style={numTd}>
          {m.accept != null
            ? <span style={{ fontFamily: mono, fontVariantNumeric: 'tabular-nums' as any }}>{Math.round(m.accept * 100)}<span style={{ fontSize: 9, color: 'var(--fg-4)', marginLeft: 2 }}>%</span></span>
            : <Dash />}
        </td>
        <td style={cellTd}><CapIcons caps={m.caps} /></td>
        <td style={{ ...cellTd, fontSize: 11, color: 'var(--fg-3)' }}>{specKv}</td>
        <td style={numTd}>{m.size_gb != null ? `${fmt(m.size_gb)} GB` : <Dash />}</td>
        <td style={{ ...numTd, fontSize: 11, color: 'var(--fg-4)' }}>{m.last_run || '—'}</td>
        <td style={numTd}>{m.runs || 0}</td>
        <td style={{ ...cellTd, textAlign: 'right' }}>
          <QueueMenu onPick={onQueue} />
        </td>
      </tr>
      {expanded && (
        <tr>
          <td colSpan={10} style={{ padding: 0, borderBottom: '1px solid var(--line)', background: 'var(--bg-1)' }}>
            <div style={{ padding: '0.9rem 1.1rem 1.1rem' }}>
              {detail
                ? <ModelDetail model={m} detail={detail} />
                : <div style={{ color: 'var(--fg-4)', fontStyle: 'italic' }}>loading&hellip;</div>}
            </div>
          </td>
        </tr>
      )}
    </>
  );
}

function ModelDetail({ model: m, detail }: {
  model: RosterModel;
  detail: { cells: CellRow[]; points: HistoryPoint[]; runs: RunSummary[] };
}) {
  const d = m.detail || {};
  const { cells, points, runs } = detail;

  // The winning record — the one that fed the headline decode/prefill tiles —
  // so lane/depth/config can be shown next to the number instead of leaving it
  // ambiguous which variant it came from.
  const winningCell = d.run_id ? cells.find(c => c.run_id === d.run_id) : undefined;
  const winningConfig = winningCell?.config || winningCell?.record?.config || 'default';
  const winningLane = winningCell?.lane ?? d.lane;
  const winningDepth = winningCell?.depth ?? d.depth;

  // Sparkline history mixes configs into one meaningless line unless filtered
  // down to a single variant — 'default' when present, else whatever the only
  // config in the series is.
  const sparkConfigs = [...new Set(points.map(p => p.config || 'default'))];
  const sparkConfig = sparkConfigs.includes('default') ? 'default' : sparkConfigs[0];
  const sparkPoints = points.filter(p => (p.config || 'default') === sparkConfig);

  // matrix: one card per (lane, depth, config)
  const matrix = (() => {
    const groups = new Map<string, { lane: string; depth: number; config: string; decode: number | null; prefill: number | null }>();
    for (const c of cells) {
      const cfg = c.record?.config || 'default';
      const key = `${c.lane}|${c.depth}|${cfg}`;
      let g = groups.get(key);
      if (!g) { g = { lane: c.lane, depth: c.depth, config: cfg, decode: null, prefill: null }; groups.set(key, g); }
      const sum = c.record?.summary || {};
      if (g.decode == null) g.decode = c.decode_ts_med ?? sum.decode_ts_med ?? null;
      if (g.prefill == null) g.prefill = sum.prefill_ts_med ?? null;
    }
    return [...groups.values()].filter(g => g.decode != null || g.prefill != null)
      .sort((a, b) => String(a.lane).localeCompare(String(b.lane)) || (a.depth - b.depth));
  })();

  // group runs by (lane, depth, config), clustered within 2 minutes
  const runGroups = (() => {
    const byLD = new Map<string, RunSummary[]>();
    for (const r of runs) {
      const k = `${r.lane}|${r.depth}|${r.config || 'default'}`;
      if (!byLD.has(k)) byLD.set(k, []);
      byLD.get(k)!.push(r);
    }
    const groups: { lane: string; depth: number | null; config: string; t: number; runs: RunSummary[] }[] = [];
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

  const tiles: [string, number | null | undefined, string][] = [
    ['decode', m.decode_ts, 't/s'],
    ['prefill', m.prefill_ts, 't/s'],
    ['accept', m.accept != null ? Math.round(m.accept * 100) : null, '%'],
    ['ttft p50', d.ttft_ms_p50, 'ms'],
    ['stddev', d.stddev, ''],
    ['size', m.size_gb, 'GB'],
    ['reps', d.reps, ''],
  ];

  return (
    <div style={{ display: 'grid', gap: '1rem', gridTemplateColumns: '1.1fr 1fr' }}>
      <div>
        <h4 style={h4Style}>current summary</h4>
        {(winningLane || winningDepth != null) && (
          <div style={{ display: 'flex', gap: '0.3rem', marginBottom: '0.4rem' }}>
            {winningLane && (
              <span style={{ ...chipStyle, fontSize: 9, padding: '0.08rem 0.4rem', color: laneColor(winningLane), background: 'transparent' }}>
                {laneLabel(winningLane)}
              </span>
            )}
            {winningDepth != null && <span style={{ ...chipStyle, fontSize: 9 }}>d{winningDepth}</span>}
            {winningConfig !== 'default' && <span style={{ ...chipStyle, fontSize: 9, color: 'var(--accent)' }}>{winningConfig}</span>}
          </div>
        )}
        <div style={{ display: 'flex', flexWrap: 'wrap', gap: '0.4rem' }}>
          {tiles.map(([label, val, unit], i) => (
            <div key={label} style={{
              border: '1px solid var(--line)', borderRadius: '0.4rem', padding: '0.4rem 0.6rem',
              background: 'var(--bg-2)', minWidth: 92,
            }}>
              <div style={{
                fontFamily: mono, fontSize: 15, color: i === 0 ? 'var(--accent)' : 'var(--fg)',
                fontVariantNumeric: 'tabular-nums' as any,
              }}>
                {val != null ? fmt(val) + (unit ? ` ${unit}` : '') : '—'}
              </div>
              <div style={{ fontFamily: mono, fontSize: 8.5, letterSpacing: '0.06em', textTransform: 'uppercase', color: 'var(--fg-4)', marginTop: 2 }}>
                {label}
              </div>
            </div>
          ))}
        </div>

        <h4 style={{ ...h4Style, margin: '1rem 0 0.5rem' }}>lane &times; depth &times; config</h4>
        <div style={{ display: 'flex', flexWrap: 'wrap', gap: '0.35rem' }}>
          {matrix.length ? matrix.map((g, i) => (
            <div key={i} style={{ border: '1px solid var(--line)', borderRadius: '0.35rem', padding: '0.35rem 0.5rem', background: 'var(--bg-2)', minWidth: 96 }}>
              <div style={{ fontFamily: mono, fontSize: 8.5, textTransform: 'uppercase', letterSpacing: '0.05em', color: 'var(--fg-4)', marginBottom: 2 }}>
                <span style={{ ...chipStyle, fontSize: 9, padding: '0.08rem 0.4rem', color: laneColor(g.lane), background: 'transparent' }}>
                  {laneLabel(g.lane)}
                </span>
                {' '}d{g.depth}
                {g.config !== 'default' && <span style={{ color: 'var(--accent)' }}>{` · ${g.config}`}</span>}
              </div>
              <div style={{ fontFamily: mono, fontSize: 13, color: 'var(--fg-2)' }}>
                {g.decode != null ? `${fmt(g.decode)} t/s` : <Dash />}
              </div>
              {g.prefill != null && (
                <div style={{ fontFamily: mono, fontSize: 11, color: 'var(--fg-4)' }}>{fmt(g.prefill)} t/s pf</div>
              )}
            </div>
          )) : <div style={{ color: 'var(--fg-4)', fontStyle: 'italic' }}>no measured cells for this model.</div>}
        </div>
      </div>

      <div>
        <h4 style={h4Style}>throughput history</h4>
        <div style={{ border: '1px solid var(--line)', borderRadius: '0.4rem', padding: '0.5rem 0.6rem', background: 'var(--bg-2)' }}>
          <Sparkline points={sparkPoints} />
          <div style={{ fontSize: 10, color: 'var(--fg-4)', marginTop: '0.25rem' }}>
            <span style={{ color: 'var(--accent)' }}>{'■'}</span> decode <span style={{ color: 'var(--fg-4)' }}>{'■'}</span> prefill
            &middot; {sparkConfig} &middot; {sparkPoints.length} pt{sparkPoints.length === 1 ? '' : 's'}
            {d.image && <span> &middot; {d.image.split('/').pop()}</span>}
          </div>
        </div>

        <h4 style={{ ...h4Style, margin: '1rem 0 0.5rem' }}>runs — {runGroups.length} sweep{runGroups.length === 1 ? '' : 's'}</h4>
        <div style={{ display: 'flex', flexWrap: 'wrap', gap: '0.3rem' }}>
          {runGroups.length ? runGroups.map((g, i) => {
            const kinds = [...new Set(g.runs.map(r => r.kind))].join('+');
            const reps = Math.max(0, ...g.runs.map(r => r.reps || 0));
            const outcomes = g.runs.map(r => r.outcome);
            const worst = outcomes.every(o => o === 'ok') ? 'ok'
              : outcomes.some(o => o !== 'ok' && o !== 'skipped-contended') ? 'failed' : 'skipped-contended';
            return (
              <span key={i} style={chipStyle}>
                {runDate(g.runs[0].run_id)} {runTime(g.runs[0].run_id)}
                <span style={{ ...chipStyle, fontSize: 9, padding: '0.05rem 0.35rem', color: laneColor(g.lane), background: 'transparent' }}>
                  {laneLabel(g.lane)}
                </span>
                d{g.depth} {g.config !== 'default' && <span style={{ color: 'var(--accent)' }}>{g.config}</span>}
                {kinds} &middot; {reps > 0 ? `${reps} rep${reps === 1 ? '' : 's'}` : `${g.runs.length} rec`}
                <span style={{ color: outcomeColor(worst) }}>{'●'}</span>
              </span>
            );
          }) : <span style={{ color: 'var(--fg-4)', fontStyle: 'italic' }}>no runs recorded.</span>}
        </div>
      </div>
    </div>
  );
}

/* ── Runs tab ── */

function RunsTab({ regressions }: { regressions: RegressionFlag[] }) {
  const [runs, setRuns] = useState<RunSummary[]>([]);
  const [loading, setLoading] = useState(true);
  const [filter, setFilter] = useState('');
  const [outcomeFilter, setOutcomeFilter] = useState<string | null>(null);
  const [expanded, setExpanded] = useState<string | null>(null);
  const [recordCache, setRecordCache] = useState<Record<string, any>>({});

  useEffect(() => {
    apiGet<{ runs: RunSummary[] }>('/api/benchmarks/runs?limit=200')
      .then(d => { setRuns(d.runs || []); })
      .catch(() => {})
      .finally(() => setLoading(false));
  }, []);

  const toggle = useCallback(async (rid: string) => {
    if (expanded === rid) { setExpanded(null); return; }
    setExpanded(rid);
    if (recordCache[rid]) return;
    try {
      const rec = await apiGet<any>(`/api/benchmarks/runs/${encodeURIComponent(rid)}`);
      setRecordCache(prev => ({ ...prev, [rid]: rec }));
    } catch { /* drawer shows loading state */ }
  }, [expanded, recordCache]);

  if (loading) return <div style={{ color: 'var(--fg-3)' }}>Loading runs&hellip;</div>;

  const filtered = filter
    ? runs.filter(r => `${r.model} ${r.suite} ${r.lane} ${r.kind} ${r.outcome} ${r.trigger}`.toLowerCase().includes(filter.toLowerCase()))
    : runs;
  const shown = outcomeFilter ? filtered.filter(r => r.outcome === outcomeFilter) : filtered;

  // Tally over the currently-shown (filtered, pre-outcome-filter) rows so the
  // chips reflect what the text filter narrowed to, not the raw /runs page.
  const tally: Record<string, number> = {};
  for (const r of filtered) {
    const o = r.outcome || '?';
    tally[o] = (tally[o] || 0) + 1;
  }

  return (
    <div>
      {regressions.length > 0 && (
        <div style={{
          display: 'flex', alignItems: 'center', gap: 8, marginBottom: 10, padding: '0.5rem 0.7rem',
          border: '1px solid var(--err)', borderRadius: 'var(--rad, 6px)', background: 'var(--bg-2)',
          fontFamily: mono, fontSize: 11, color: 'var(--err)',
        }}>
          {'▼'} {regressions.length} regression{regressions.length === 1 ? '' : 's'} flagged
          <span style={{ color: 'var(--fg-4)' }}>
            &middot; {regressions.map(f => cleanName(f.model_id)).join(', ')}
          </span>
        </div>
      )}
      <div style={{ display: 'flex', alignItems: 'center', gap: 10, marginBottom: 10 }}>
        <input
          placeholder="filter by model / suite / lane / outcome / trigger"
          value={filter}
          onChange={e => setFilter(e.target.value)}
          style={{
            fontFamily: mono, fontSize: 11, padding: '5px 9px', width: 320,
            background: 'var(--bg-2)', border: '1px solid var(--line)', borderRadius: 'var(--rad, 6px)', color: 'var(--fg)',
          }}
        />
        <span style={{ fontFamily: mono, fontSize: 10, color: 'var(--fg-4)' }}>
          {Object.entries(tally).map(([o, n]) => (
            <button
              key={o}
              onClick={() => setOutcomeFilter(cur => cur === o ? null : o)}
              title={`toggle filter: ${o}`}
              style={{
                marginRight: 10, background: 'transparent', border: 'none', cursor: 'pointer',
                fontFamily: mono, fontSize: 10, padding: 0,
                color: outcomeFilter === o ? 'var(--fg)' : 'var(--fg-4)',
                textDecoration: outcomeFilter === o ? 'underline' : 'none',
              }}
            >
              <span style={{ color: outcomeColor(o) }}>{'●'}</span> {o} {n}
            </button>
          ))}
        </span>
      </div>
      <table style={{ borderCollapse: 'separate', borderSpacing: 0, width: '100%', fontFamily: mono, fontSize: 12 }}>
        <thead>
          <tr>
            {(['run', 'model', 'suite', 'lane', 'kind', 'depth', 'cfg', 'trigger', 'reps', 'decode', ''] as const).map((h, i) => (
              <th key={i} style={thStyle(['depth', 'reps', 'decode'].includes(h) ? 'right' : 'left')}>{h}</th>
            ))}
          </tr>
        </thead>
        <tbody>
          {shown.map(r => (
            <React.Fragment key={r.run_id}>
              <tr onClick={() => toggle(r.run_id)} style={{ cursor: 'pointer', background: expanded === r.run_id ? 'var(--bg-2)' : undefined }}>
                <td style={{ ...cellTd, whiteSpace: 'nowrap', color: 'var(--fg-3)' }}>
                  <span style={{
                    display: 'inline-block', width: '0.7rem',
                    color: expanded === r.run_id ? 'var(--accent)' : 'var(--fg-4)',
                    transform: expanded === r.run_id ? 'rotate(90deg)' : undefined,
                  }}>{'▸'}</span>
                  {' '}{runDate(r.run_id)} {runTime(r.run_id)}
                </td>
                <td style={{ ...cellTd, color: 'var(--fg)' }}>{cleanName(r.model || '?')}</td>
                <td style={{ ...cellTd, fontSize: 11, color: 'var(--fg-3)' }}>{r.suite}</td>
                <td style={cellTd}>
                  <span style={{ ...chipStyle, fontSize: 9, padding: '0.08rem 0.4rem', color: laneColor(r.lane), background: 'transparent' }}>
                    {laneLabel(r.lane)}
                  </span>
                </td>
                <td style={{ ...cellTd, fontSize: 11 }}>{r.kind}</td>
                <td style={numTd}>{r.depth != null ? `d${r.depth}` : <Dash />}</td>
                <td style={{ ...cellTd, fontSize: 11, color: r.config !== 'default' ? 'var(--accent)' : 'var(--fg-4)' }}>{r.config}</td>
                <td style={cellTd}><TriggerChip t={r.trigger} /></td>
                <td style={numTd}>{r.reps}</td>
                <td style={numTd}><Num v={r.decode_ts_med} unit=" t/s" /></td>
                <td style={{ ...cellTd, textAlign: 'right' }}>
                  <span title={r.outcome} style={{ color: outcomeColor(r.outcome) }}>{'●'}</span>
                </td>
              </tr>
              {expanded === r.run_id && (
                <tr>
                  <td colSpan={11} style={{ padding: 0, borderBottom: '1px solid var(--line)', background: 'var(--bg-1)' }}>
                    <div style={{ padding: '0.9rem 1.1rem 1.1rem' }}>
                      {recordCache[r.run_id]
                        ? <RunDetail rec={recordCache[r.run_id]} />
                        : <div style={{ color: 'var(--fg-4)', fontStyle: 'italic' }}>loading&hellip;</div>}
                    </div>
                  </td>
                </tr>
              )}
            </React.Fragment>
          ))}
        </tbody>
      </table>
      {!shown.length && <div style={{ color: 'var(--fg-4)', fontStyle: 'italic', padding: '1rem 0' }}>no runs recorded yet.</div>}
    </div>
  );
}

function RunDetail({ rec }: { rec: any }) {
  const id = rec.identity || {};
  const model = id.model || {};
  const engine = id.engine || {};
  const config = id.config || {};
  const workload = id.workload || {};
  const host = rec.host || {};
  const summary = rec.summary || {};
  const telemetry = rec.telemetry || {};
  const reps: any[] = rec.reps || [];

  const idChips: [string, any][] = [
    ['engine', engine.kind],
    ['image', (engine.image || '').split('/').pop()],
    ['build', engine.llamacpp_build],
    ['lane', id.lane],
    ['kv', config.kv && (config.kv.main_k || config.kv.main_v) ? `${config.kv.main_k || '?'}/${config.kv.main_v || '?'}` : null],
    ['spec', config.spec ? (config.spec.type || JSON.stringify(config.spec)) : null],
    ['sampler', workload.sampler?.mode],
    ['depth', workload.depth != null ? `d${workload.depth}` : null],
    ['ctx', config.ctx || null],
    ['np', config.parallel],
    ['sha256', model.sha256 ? String(model.sha256).slice(0, 12) : null],
  ];
  const hostChips: [string, any][] = [
    ['host', host.name], ['platform', host.platform], ['gpu', host.gpu],
    ['kernel', host.kernel], ['hal0', host.hal0_version],
    ['exclusive', host.exclusive != null ? String(host.exclusive) : null],
  ];
  const sumTiles: [string, any, string][] = [
    ['decode med', summary.decode_ts_med, 't/s'],
    ['decode σ', summary.decode_ts_stddev, ''],
    ['prefill med', summary.prefill_ts_med, 't/s'],
    ['ttft p50', summary.ttft_ms_p50, 'ms'],
    ['ttft p95', summary.ttft_ms_p95, 'ms'],
    ['accept', summary.accept_med != null ? Math.round(summary.accept_med * 100) : null, '%'],
  ];
  const telTiles: [string, any, string][] = [
    ['vram peak', telemetry.vram_peak_mb, 'MB'],
    ['gtt peak', telemetry.gtt_peak_mb, 'MB'],
    ['edge max', telemetry.gpu_edge_temp_max_c, '°C'],
    ['power avg', telemetry.gpu_power_avg_w, 'W'],
  ];

  const cellKey: string | undefined = rec.cell_key;
  const cellKeyShort = cellKey ? (cellKey.length > 16 ? `${cellKey.slice(0, 16)}…` : cellKey) : null;

  return (
    <div style={{ display: 'grid', gap: '1rem' }}>
      <div>
        <h4 style={h4Style}>identity — {model.id || '?'}</h4>
        <div style={{ display: 'flex', flexWrap: 'wrap', gap: '0.3rem', marginBottom: '0.4rem' }}>
          {rec.config && (
            <span style={{ ...chipStyle, color: rec.config !== 'default' ? 'var(--accent)' : undefined }}>
              <span style={{ color: 'var(--fg-4)', textTransform: 'uppercase', fontSize: 8.5 }}>config</span> {rec.config}
            </span>
          )}
          {rec.trigger && (
            <span style={chipStyle}>
              <span style={{ color: 'var(--fg-4)', textTransform: 'uppercase', fontSize: 8.5 }}>trigger</span> {rec.trigger}
            </span>
          )}
          {rec.outcome && (
            <span style={{ ...chipStyle, color: outcomeColor(rec.outcome) }}>
              <span style={{ color: 'var(--fg-4)', textTransform: 'uppercase', fontSize: 8.5 }}>outcome</span> {rec.outcome}
            </span>
          )}
          {cellKeyShort && (
            <span style={chipStyle} title={cellKey}>
              <span style={{ color: 'var(--fg-4)', textTransform: 'uppercase', fontSize: 8.5 }}>cell</span> {cellKeyShort}
            </span>
          )}
        </div>
        <div style={{ display: 'flex', flexWrap: 'wrap', gap: '0.3rem' }}>
          {idChips.filter(([, v]) => v != null && v !== '').map(([k, v]) => (
            <span key={k} style={chipStyle}>
              <span style={{ color: 'var(--fg-4)', textTransform: 'uppercase', fontSize: 8.5 }}>{k}</span> {String(v)}
            </span>
          ))}
          {hostChips.filter(([, v]) => v != null && v !== '').map(([k, v]) => (
            <span key={k} style={{ ...chipStyle, opacity: 0.75 }}>
              <span style={{ color: 'var(--fg-4)', textTransform: 'uppercase', fontSize: 8.5 }}>{k}</span> {String(v)}
            </span>
          ))}
        </div>
        {Array.isArray(config.argv) && config.argv.length > 0 && (
          <pre style={{
            fontFamily: mono, fontSize: 10.5, color: 'var(--fg-3)', background: 'var(--bg-2)',
            border: '1px solid var(--line)', borderRadius: '0.35rem', padding: '0.45rem 0.6rem',
            margin: '0.5rem 0 0', whiteSpace: 'pre-wrap', wordBreak: 'break-all', userSelect: 'all',
          }}>{config.argv.join(' ')}</pre>
        )}
      </div>

      <div style={{ display: 'grid', gap: '1rem', gridTemplateColumns: '1fr 1fr' }}>
        <div>
          <h4 style={h4Style}>summary</h4>
          <div style={{ display: 'flex', flexWrap: 'wrap', gap: '0.4rem' }}>
            {sumTiles.filter(([, v]) => v != null).map(([label, val, unit]) => (
              <div key={label} style={{ border: '1px solid var(--line)', borderRadius: '0.4rem', padding: '0.35rem 0.55rem', background: 'var(--bg-2)', minWidth: 84 }}>
                <div style={{ fontFamily: mono, fontSize: 14, fontVariantNumeric: 'tabular-nums' as any }}>
                  {typeof val === 'number' ? fmt(val) : String(val)}{unit && <span style={{ fontSize: 9, color: 'var(--fg-4)', marginLeft: 2 }}>{unit}</span>}
                </div>
                <div style={{ fontFamily: mono, fontSize: 8.5, textTransform: 'uppercase', letterSpacing: '0.06em', color: 'var(--fg-4)', marginTop: 2 }}>{label}</div>
              </div>
            ))}
            {!sumTiles.some(([, v]) => v != null) && <span style={{ color: 'var(--fg-4)', fontStyle: 'italic' }}>no summary metrics.</span>}
          </div>

          {telTiles.some(([, v]) => v != null) && (
            <>
              <h4 style={{ ...h4Style, margin: '0.8rem 0 0.5rem' }}>
                telemetry{telemetry.throttled ? <span style={{ color: 'var(--warn)' }}> — THROTTLED</span> : ''}
              </h4>
              <div style={{ display: 'flex', flexWrap: 'wrap', gap: '0.4rem' }}>
                {telTiles.filter(([, v]) => v != null).map(([label, val, unit]) => (
                  <div key={label} style={{ border: '1px solid var(--line)', borderRadius: '0.4rem', padding: '0.35rem 0.55rem', background: 'var(--bg-2)', minWidth: 84 }}>
                    <div style={{ fontFamily: mono, fontSize: 14 }}>{val}<span style={{ fontSize: 9, color: 'var(--fg-4)', marginLeft: 2 }}>{unit}</span></div>
                    <div style={{ fontFamily: mono, fontSize: 8.5, textTransform: 'uppercase', color: 'var(--fg-4)', marginTop: 2 }}>{label}</div>
                  </div>
                ))}
              </div>
            </>
          )}

          {rec.note && (
            <div style={{ marginTop: '0.8rem', fontFamily: mono, fontSize: 10.5, color: 'var(--warn)', whiteSpace: 'pre-wrap' }}>{rec.note}</div>
          )}
        </div>

        <div>
          <h4 style={h4Style}>repetitions — {reps.length}</h4>
          {reps.length ? (
            <table style={{ borderCollapse: 'collapse', fontFamily: mono, fontSize: 11, width: '100%' }}>
              <thead>
                <tr>
                  {['#', 'decode', 'prefill', 'ttft', 'accept', 'wall'].map((h, i) => (
                    <th key={h} style={{ ...thStyle(i === 0 ? 'left' : 'right'), position: 'static' }}>{h}</th>
                  ))}
                </tr>
              </thead>
              <tbody>
                {reps.map((rep, i) => (
                  <tr key={i}>
                    <td style={{ ...cellTd, padding: '0.3rem 0.7rem', color: 'var(--fg-4)' }}>{i + 1}</td>
                    <td style={{ ...numTd, padding: '0.3rem 0.7rem' }}><Num v={rep.decode_ts} unit=" t/s" /></td>
                    <td style={{ ...numTd, padding: '0.3rem 0.7rem' }}><Num v={rep.prefill_ts} unit=" t/s" /></td>
                    <td style={{ ...numTd, padding: '0.3rem 0.7rem' }}>{rep.ttft_ms != null ? `${Math.round(rep.ttft_ms)} ms` : <Dash />}</td>
                    <td style={{ ...numTd, padding: '0.3rem 0.7rem' }}>{rep.accept_rate != null ? `${Math.round(rep.accept_rate * 100)}%` : <Dash />}</td>
                    <td style={{ ...numTd, padding: '0.3rem 0.7rem' }}>{rep.t_s != null ? `${fmt(rep.t_s)} s` : <Dash />}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          ) : <span style={{ color: 'var(--fg-4)', fontStyle: 'italic' }}>no per-rep samples.</span>}

          {Array.isArray(rec.artifacts_files) && rec.artifacts_files.length > 0 && (
            <div style={{ marginTop: '0.7rem', fontFamily: mono, fontSize: 10, color: 'var(--fg-4)' }}>
              artifacts: {rec.artifacts_files.join(' · ')}
            </div>
          )}
          <details style={{ marginTop: '0.6rem' }}>
            <summary style={{ fontFamily: mono, fontSize: 10, color: 'var(--fg-4)', cursor: 'pointer' }}>raw record JSON</summary>
            <pre style={{
              fontFamily: mono, fontSize: 9.5, color: 'var(--fg-3)', background: 'var(--bg-2)',
              border: '1px solid var(--line)', borderRadius: '0.35rem', padding: '0.5rem',
              maxHeight: 300, overflow: 'auto',
            }}>{JSON.stringify(rec, null, 2)}</pre>
          </details>
        </div>
      </div>
    </div>
  );
}

/* ── Evals tab ── */

function EvalsTab() {
  const [data, setData] = useState<any>(null);
  const [loading, setLoading] = useState(true);

  useEffect(() => {
    apiGet<any>('/api/benchmarks/evals').then(setData).catch(() => {}).finally(() => setLoading(false));
  }, []);

  if (loading) return <div style={{ color: 'var(--fg-3)' }}>Loading evals&hellip;</div>;
  if (!data || !data.models?.length) {
    return (
      <div style={{ color: 'var(--fg-4)', fontStyle: 'italic' }}>
        no eval records yet — run <span style={{ fontFamily: mono, color: 'var(--fg-3)' }}>hal0 bench eval --models &lt;id,...&gt;</span> on the box.
      </div>
    );
  }

  const taskOrder: string[] = data.task_order || [];
  const scoreColor = (s: number | null | undefined) =>
    s == null ? 'var(--fg-4)' : s >= 1 ? 'var(--ok)' : s > 0 ? 'var(--warn)' : 'var(--err)';

  return (
    <div style={{ overflowX: 'auto' }}>
      <table style={{ borderCollapse: 'separate', borderSpacing: 0, fontFamily: mono, fontSize: 12, minWidth: 720 }}>
        <thead>
          <tr>
            <th style={thStyle('left')}>model</th>
            <th style={thStyle('right')}>avg</th>
            {taskOrder.map(t => <th key={t} style={thStyle('right')}>{t.replace(/-/g, '‑')}</th>)}
          </tr>
        </thead>
        <tbody>
          {data.models.map((row: any) => (
            <tr key={row.model}>
              <td style={{ ...cellTd, color: 'var(--fg)' }}>{cleanName(row.model)}</td>
              <td style={{ ...numTd, color: 'var(--accent)', fontSize: 13 }}>{row.avg_score.toFixed(2)}</td>
              {taskOrder.map(t => {
                const cell = row.tasks?.[t];
                if (!cell) return <td key={t} style={{ ...numTd, color: 'var(--fg-5)' }}>{'—'}</td>;
                return (
                  <td key={t} style={numTd} title={`answer: ${cell.answer ?? '?'} · expected: ${cell.expected ?? '?'} · ${cell.checkpoints_hit}/${cell.checkpoints_total ?? '?'} checkpoints · ${cell.tool_calls ?? '?'} tools · ${cell.tokens_out ?? '?'} tok`}>
                    <span style={{ color: scoreColor(cell.score) }}>
                      {cell.correct ? 'ok' : cell.score != null ? cell.score.toFixed(2) : '—'}
                    </span>
                    {cell.wall_s != null && <span style={{ fontSize: 9, color: 'var(--fg-4)', marginLeft: 4 }}>{Math.round(cell.wall_s)}s</span>}
                  </td>
                );
              })}
            </tr>
          ))}
        </tbody>
      </table>
      <div style={{ fontFamily: mono, fontSize: 10, color: 'var(--fg-4)', marginTop: 8 }}>
        latest score per (model, task) &middot; {data.count} record{data.count === 1 ? '' : 's'} total &middot; hover a cell for answer/checkpoint detail
      </div>
    </div>
  );
}

/* ── Run Queue tab ── */

function QueueTab({ queue, roster, refresh, onQueueModel }: {
  queue: QueueState | null;
  roster: RosterModel[];
  refresh: () => void;
  onQueueModel: (id: string, opt: BenchOption) => void;
}) {
  const [plan, setPlan] = useState<any>(null);
  const [planLoading, setPlanLoading] = useState(true);
  const [suite, setSuite] = useState('roster');
  const [modelPick, setModelPick] = useState('');
  const busyRef = useRef(false);

  useEffect(() => {
    apiGet<any>('/api/benchmarks/plan').then(setPlan).catch(() => {}).finally(() => setPlanLoading(false));
  }, []);

  const control = async (body: Record<string, unknown>, msg: string) => {
    if (busyRef.current) return;
    busyRef.current = true;
    try { await apiPost('/api/benchmarks/control', body); toast(msg, 'ok'); refresh(); }
    catch (e: any) { toast(`control failed: ${e.message}`, 'err'); }
    finally { busyRef.current = false; }
  };

  const enqueueSuite = async () => {
    try {
      await apiPost('/api/benchmarks/queue', { suite });
      toast(`Queued suite ${suite}`, 'ok');
      refresh();
    } catch (e: any) { toast(`Queue failed: ${e.message}`, 'err'); }
  };

  const removeItem = async (id: string) => {
    try { await apiDelete(`/api/benchmarks/queue/${encodeURIComponent(id)}`); refresh(); }
    catch (e: any) { toast(`remove failed: ${e.message}`, 'err'); }
  };

  const state = queue?.control?.state || 'stopped';
  const exclusive = queue?.control?.exclusive ?? true;
  const items = queue?.items || [];
  const active = queue?.active;
  const suites: string[] = plan?.suites_considered?.length ? plan.suites_considered : ['roster', 'smoke', 'lane-matrix'];
  const staleBySuite: Record<string, number> = {};
  for (const c of plan?.cells || []) staleBySuite[c.suite] = (staleBySuite[c.suite] || 0) + 1;

  const stateColor = state === 'running' ? 'var(--ok)' : state === 'paused' ? 'var(--warn)' : 'var(--fg-4)';

  return (
    <div style={{ display: 'grid', gap: '1.2rem', gridTemplateColumns: 'minmax(340px, 1fr) 1.4fr' }}>
      <div>
        {/* worker control */}
        <h4 style={h4Style}>worker</h4>
        <div className="card" style={{ padding: '0.8rem 0.9rem' }}>
          <div style={{ display: 'flex', alignItems: 'center', gap: 10, flexWrap: 'wrap' }}>
            <span style={{ fontFamily: mono, fontSize: 12, color: stateColor }}>
              {'●'} {state === 'running' && !active ? 'armed — idle' : state}
            </span>
            {state === 'running' && !active && (
              <span style={{ fontFamily: mono, fontSize: 10, color: 'var(--fg-4)' }}>
                nothing running; queued items start immediately
              </span>
            )}
            <span className="vh-spacer" style={{ flex: 1 }} />
            <button className="btn sm" disabled={state === 'running'} onClick={() => control({ action: 'start' }, 'Worker started')}>Start</button>
            <button className="btn ghost sm" disabled={state !== 'running'} onClick={() => control({ action: 'pause' }, 'Worker pausing (between cells)')}>Pause</button>
            <button className="btn ghost sm" disabled={state === 'stopped'} onClick={() => control({ action: 'stop' }, 'Worker stopping (between cells)')}>Stop</button>
          </div>
          <label style={{ display: 'flex', alignItems: 'center', gap: 8, marginTop: 10, fontFamily: mono, fontSize: 11, color: 'var(--fg-3)', cursor: 'pointer' }}>
            <input
              type="checkbox"
              checked={exclusive}
              onChange={e => control({ exclusive: e.target.checked }, `Exclusive ${e.target.checked ? 'on' : 'off'}`)}
            />
            exclusive — stop competing GPU slots for clean numbers
          </label>
          <div style={{ fontFamily: mono, fontSize: 10, color: 'var(--fg-4)', marginTop: 8 }}>
            Pause/Stop take effect between cells (a sweep can't be suspended mid-run).
          </div>
          {active && (
            <div style={{ marginTop: 10, borderTop: '1px solid var(--line-soft)', paddingTop: 8, fontFamily: mono, fontSize: 11 }}>
              <span style={{ color: 'var(--ok)' }}>{'▶'}</span> running: {active.suite || active.item?.label}
              {active.cells != null && <span style={{ color: 'var(--fg-4)' }}> &middot; {active.cells} cell(s)</span>}
              {active.started && <span style={{ color: 'var(--fg-4)' }}> &middot; since {String(active.started).slice(11, 19)}</span>}
            </div>
          )}
        </div>

        {/* enqueue */}
        <h4 style={{ ...h4Style, margin: '1.1rem 0 0.5rem' }}>queue a run</h4>
        <div className="card" style={{ padding: '0.8rem 0.9rem', display: 'grid', gap: 10 }}>
          <div style={{ display: 'flex', gap: 8, alignItems: 'center', flexWrap: 'wrap' }}>
            <select
              value={suite}
              onChange={e => setSuite(e.target.value)}
              style={{ fontFamily: mono, fontSize: 11, padding: '5px 8px', background: 'var(--bg-2)', border: '1px solid var(--line)', borderRadius: 'var(--rad, 6px)', color: 'var(--fg)', flex: 1, minWidth: 180 }}
            >
              {suites.map(s => (
                <option key={s} value={s}>
                  {s}{staleBySuite[s] != null ? ` — ${staleBySuite[s]} stale` : ''}
                </option>
              ))}
            </select>
            <button className="btn sm" onClick={enqueueSuite}>Queue suite</button>
          </div>
          <div style={{ display: 'flex', gap: 8, alignItems: 'center', flexWrap: 'wrap' }}>
            <select
              value={modelPick}
              onChange={e => setModelPick(e.target.value)}
              style={{ fontFamily: mono, fontSize: 11, padding: '5px 8px', background: 'var(--bg-2)', border: '1px solid var(--line)', borderRadius: 'var(--rad, 6px)', color: 'var(--fg)', flex: 1, minWidth: 180 }}
            >
              <option value="">single model from the roster&hellip;</option>
              {roster.map(m => <option key={m.id} value={m.id}>{m.name || cleanName(m.id)}</option>)}
            </select>
            <QueueMenu
              label="Queue model"
              disabled={!modelPick}
              title={modelPick ? 'Pick a bench for this model' : 'Select a model first'}
              onPick={opt => modelPick && onQueueModel(modelPick, opt)}
            />
          </div>
        </div>

        {/* pending items */}
        <h4 style={{ ...h4Style, margin: '1.1rem 0 0.5rem' }}>pending — {items.length}</h4>
        <div style={{ display: 'grid', gap: 6 }}>
          {items.length ? items.map(it => (
            <div key={it.id} className="card" style={{ padding: '0.45rem 0.7rem', display: 'flex', alignItems: 'center', gap: 8 }}>
              <span style={{ fontFamily: mono, fontSize: 11, color: 'var(--fg-2)', flex: 1, overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>
                {it.suite ? <span style={{ color: 'var(--accent)' }}>suite </span> : null}{it.label}
              </span>
              <span style={{ fontFamily: mono, fontSize: 9.5, color: 'var(--fg-5)' }}>{(it.enqueued || '').slice(11, 16)}</span>
              <button
                className="btn ghost sm"
                title="Remove from queue"
                onClick={() => removeItem(it.id)}
                style={{ fontSize: 10, padding: '1px 7px' }}
              >&times;</button>
            </div>
          )) : <span style={{ color: 'var(--fg-4)', fontStyle: 'italic', fontFamily: mono, fontSize: 11 }}>queue is empty.</span>}
        </div>
      </div>

      {/* plan */}
      <div>
        <h4 style={h4Style}>
          plan — what's stale and why
          {plan?.registry_error && <span style={{ color: 'var(--warn)' }}> (registry unreachable)</span>}
        </h4>
        {planLoading ? (
          <div style={{ color: 'var(--fg-3)' }}>computing plan&hellip;</div>
        ) : plan?.cells?.length ? (
          <>
            <div style={{ fontFamily: mono, fontSize: 11, color: 'var(--fg-3)', marginBottom: 8 }}>
              {plan.stale_count} stale cell(s) across {plan.suites_considered?.join(', ')}
            </div>
            <div style={{ maxHeight: 520, overflowY: 'auto', border: '1px solid var(--line)', borderRadius: 'var(--rad-lg, 8px)' }}>
              <table style={{ borderCollapse: 'separate', borderSpacing: 0, width: '100%', fontFamily: mono, fontSize: 11 }}>
                <thead>
                  <tr>
                    {(['model', 'suite', 'lane', 'kind', 'depth', 'pri', 'excl', 'reason'] as const).map(h => (
                      <th key={h} style={thStyle(h === 'depth' || h === 'pri' ? 'right' : 'left')}>{h}</th>
                    ))}
                  </tr>
                </thead>
                <tbody>
                  {[...plan.cells].sort((a: any, b: any) => (b.priority ?? 0) - (a.priority ?? 0)).map((c: any, i: number) => (
                    <tr key={`${c.cell_key}-${i}`}>
                      <td style={{ ...cellTd, padding: '0.35rem 0.7rem' }}>{cleanName(c.model)}</td>
                      <td style={{ ...cellTd, padding: '0.35rem 0.7rem', color: 'var(--fg-4)' }}>{c.suite}</td>
                      <td style={{ ...cellTd, padding: '0.35rem 0.7rem' }}>
                        <span style={{ color: laneColor(c.lane), fontSize: 10 }}>{laneLabel(c.lane)}</span>
                      </td>
                      <td style={{ ...cellTd, padding: '0.35rem 0.7rem' }}>{c.kind}</td>
                      <td style={{ ...numTd, padding: '0.35rem 0.7rem' }}>d{c.depth}</td>
                      <td style={{ ...numTd, padding: '0.35rem 0.7rem' }}>{c.priority ?? <Dash />}</td>
                      <td style={{ ...cellTd, padding: '0.35rem 0.7rem' }}>{c.exclusive ? 'yes' : <Dash />}</td>
                      <td style={{ ...cellTd, padding: '0.35rem 0.7rem', color: c.reason === 'never-measured' ? 'var(--info)' : 'var(--warn)', fontSize: 10 }}>
                        {c.reason}
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          </>
        ) : (
          <div style={{ color: 'var(--ok)', fontFamily: mono, fontSize: 12 }}>
            nothing stale — every suite cell has a current ok record.
          </div>
        )}
      </div>
    </div>
  );
}

export default Benchmarks;
