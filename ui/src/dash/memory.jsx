// hal0 dashboard — Memory view (Hindsight engine surface).
//
// #memory route, gated on memory_enabled like #agent. Renders the engine
// card (version/reachability/features from the fail-soft /api/memory/engine
// aggregator), per-bank cards with fact-type breakdowns and operation
// badges, a retained-memories timeseries chart, and a bank detail panel
// with the async-operations queue (retry/cancel), consolidate trigger,
// and bank create/delete.
//
// Hooks arrive via memory-hook-bridge.ts (window.__hal0Use*) — this file
// stays a no-ES-imports dash/*.jsx prototype module.

const { useState: useStateMem, useMemo: useMemoMem } = React;

function memToast(msg, kind = 'info') {
  if (typeof window !== 'undefined' && window.__hal0Toast) window.__hal0Toast(msg, kind);
}

// Fact-type palette — house tokens, not Hindsight's upstream colors.
const MEM_FACT_COLORS = {
  world: 'var(--info)',
  experience: 'var(--hal0-accent)',
  observation: 'var(--ok)',
};
const MEM_FACT_TYPES = ['world', 'experience', 'observation'];

const MEM_BANK_RE = /^[a-z0-9][a-z0-9_-]{0,127}$/i;

function fmtWhen(iso) {
  if (!iso) return 'never';
  try {
    const d = new Date(iso);
    return d.toLocaleString(undefined, { month: 'short', day: 'numeric', hour: '2-digit', minute: '2-digit' });
  } catch {
    return iso;
  }
}

// ── Engine card ───────────────────────────────────────────────────────────────

function MemEngineCard({ engine, isLoading }) {
  if (isLoading) {
    return (
      <div className="card mem-engine" data-testid="mem-engine-card">
        <div className="mem-engine-head mono">memory engine</div>
        <div className="empty mono">Probing engine…</div>
      </div>
    );
  }
  const e = engine || {};
  const reachable = !!e.reachable;
  const features = e.features || {};
  return (
    <div className="card mem-engine" data-testid="mem-engine-card">
      <div className="mem-engine-head">
        <span className="mono mem-engine-name">{e.engine || 'no engine'}</span>
        <span className={'chip ' + (reachable ? 'ok' : 'err')}>
          {reachable ? 'reachable' : 'unreachable'}
        </span>
      </div>
      <div className="mem-engine-meta mono">
        {e.version ? <span className="mem-engine-ver">v{e.version}</span> : <span>version unknown</span>}
        <span className="pf-sep">·</span>
        <span>{e.banks_total != null ? `${e.banks_total} banks` : '— banks'}</span>
      </div>
      <div className="mem-feature-row">
        {Object.entries(features)
          .filter(([, on]) => !!on)
          .map(([name]) => (
            <span key={name} className="pf-badge" title={`engine feature: ${name}`}>{name}</span>
          ))}
      </div>
    </div>
  );
}

// ── Timeseries chart (hand-rolled SVG, house style) ───────────────────────────

function MemTimeseries({ bank, period, setPeriod }) {
  const useBankTimeseries = window.__hal0UseBankTimeseries;
  const query = useBankTimeseries ? useBankTimeseries(bank, period) : { data: null };
  const buckets = query.data?.buckets || [];

  const W = 600, H = 150, PAD = 24;
  const maxVal = Math.max(1, ...buckets.flatMap(b => MEM_FACT_TYPES.map(t => b[t] || 0)));
  const x = (i) => buckets.length <= 1 ? W / 2 : PAD + (i * (W - PAD * 2)) / (buckets.length - 1);
  const y = (v) => H - PAD - (v / maxVal) * (H - PAD * 2);

  const pathFor = (type) =>
    buckets.map((b, i) => `${i === 0 ? 'M' : 'L'}${x(i).toFixed(1)},${y(b[type] || 0).toFixed(1)}`).join(' ');

  return (
    <div className="card mem-ts" data-testid="mem-timeseries">
      <div className="mem-ts-head">
        <span className="mono">memories retained · {bank || '—'}</span>
        <div className="mem-ts-periods">
          {['1d', '7d', '30d', '90d'].map(p => (
            <button
              key={p}
              className={'btn ghost xs' + (p === period ? ' active' : '')}
              onClick={() => setPeriod(p)}
            >
              {p}
            </button>
          ))}
        </div>
      </div>
      {buckets.length === 0 ? (
        <div className="empty mono">No retain activity in this window.</div>
      ) : (
        <svg viewBox={`0 0 ${W} ${H}`} className="mem-ts-svg" role="img" aria-label="memories timeseries">
          {MEM_FACT_TYPES.map(t => (
            <path key={t} className="mem-series" d={pathFor(t)} fill="none"
              stroke={MEM_FACT_COLORS[t]} strokeWidth="1.5" />
          ))}
          {MEM_FACT_TYPES.map(t =>
            buckets.map((b, i) => (
              <circle key={t + i} cx={x(i)} cy={y(b[t] || 0)} r="2" fill={MEM_FACT_COLORS[t]} />
            ))
          )}
        </svg>
      )}
      <div className="mem-legend mono">
        {MEM_FACT_TYPES.map(t => (
          <span key={t} className="mem-legend-item">
            <span className="mem-swatch" style={{ background: MEM_FACT_COLORS[t] }} />
            {t}
          </span>
        ))}
      </div>
    </div>
  );
}

// ── Bank card ─────────────────────────────────────────────────────────────────

function MemBankCard({ bank, selected, onSelect }) {
  const useBankStats = window.__hal0UseBankStats;
  const stats = (useBankStats ? useBankStats(bank.bank_id) : { data: null }).data;
  const byType = stats?.nodes_by_fact_type || {};
  return (
    <div
      className={'mem-bank-card' + (selected ? ' selected' : '')}
      data-testid={`mem-bank-${bank.bank_id}`}
      onClick={() => onSelect(bank)}
      role="button"
      tabIndex={0}
    >
      <div className="mem-bank-head">
        <span className="mono mem-bank-id">{bank.bank_id}</span>
        <div className="mem-bank-badges">
          {stats?.pending_operations > 0 && (
            <span className="mem-badge warn" title="pending operations">{stats.pending_operations}</span>
          )}
          {stats?.failed_operations > 0 && (
            <span className="mem-badge err" title="failed operations">{stats.failed_operations}</span>
          )}
        </div>
      </div>
      {bank.mission && <div className="mem-bank-mission">{bank.mission}</div>}
      <div className="mem-bank-counts mono">
        {MEM_FACT_TYPES.map(t => (
          <span key={t} className="mem-count" title={`${t} facts`}>
            <span className="mem-swatch" style={{ background: MEM_FACT_COLORS[t] }} />
            {t} <span className="num">{byType[t] || 0}</span>
          </span>
        ))}
      </div>
      <div className="mem-bank-meta mono">
        <span>{stats?.total_documents ?? '—'} docs</span>
        <span className="pf-sep">·</span>
        <span>{stats?.total_links ?? '—'} links</span>
        <span className="pf-sep">·</span>
        <span title="last consolidated">cons. {fmtWhen(stats?.last_consolidated_at)}</span>
      </div>
    </div>
  );
}

// ── Operations panel (inside bank detail) ─────────────────────────────────────

function MemOperations({ bank }) {
  const useBankOperations = window.__hal0UseBankOperations;
  const useOperationRetry = window.__hal0UseOperationRetry;
  const useOperationCancel = window.__hal0UseOperationCancel;
  const query = useBankOperations ? useBankOperations(bank) : { data: null };
  const retry = useOperationRetry ? useOperationRetry() : null;
  const cancel = useOperationCancel ? useOperationCancel() : null;
  const items = query.data?.items || [];

  async function doRetry(id) {
    try {
      await retry.mutateAsync({ bank, id });
      memToast(`Operation ${id} re-queued`, 'ok');
    } catch (err) {
      memToast(err?.message || 'Retry failed', 'err');
    }
  }
  async function doCancel(id) {
    try {
      await cancel.mutateAsync({ bank, id });
      memToast(`Operation ${id} cancelled`, 'ok');
    } catch (err) {
      memToast(err?.message || 'Cancel failed', 'err');
    }
  }

  if (items.length === 0) {
    return <div className="empty mono">No operations recorded.</div>;
  }
  return (
    <div className="mem-ops">
      {items.map(op => (
        <div className="mem-op-row" key={op.operation_id} data-testid={`mem-op-${op.operation_id}`}>
          <span className={'chip ' + (op.status === 'failed' ? 'err' : op.status === 'completed' ? 'ok' : 'warn')}>
            {op.status}
          </span>
          <span className="mono mem-op-type">{op.operation_type}</span>
          <span className="mono mem-op-when">{fmtWhen(op.created_at)}</span>
          {op.error_message && <span className="mem-op-err mono" title={op.error_message}>{op.error_message}</span>}
          <span className="mem-op-actions">
            {op.status === 'failed' && (
              <button className="btn ghost xs" data-testid="mem-op-retry" onClick={() => doRetry(op.operation_id)}>
                Retry
              </button>
            )}
            {op.status === 'pending' && (
              <button className="btn ghost xs danger" data-testid="mem-op-cancel" onClick={() => doCancel(op.operation_id)}>
                Cancel
              </button>
            )}
          </span>
        </div>
      ))}
    </div>
  );
}

// ── Bank detail panel ─────────────────────────────────────────────────────────

function MemBankDetail({ bank, onClose, onDeleted }) {
  const useConsolidate = window.__hal0UseConsolidate;
  const useBankDelete = window.__hal0UseBankDelete;
  const consolidate = useConsolidate ? useConsolidate() : null;
  const del = useBankDelete ? useBankDelete() : null;
  const [confirming, setConfirming] = useStateMem(false);

  async function doConsolidate() {
    try {
      const res = await consolidate.mutateAsync(bank.bank_id);
      memToast(`Consolidation queued (${res?.operation_id || 'ok'})`, 'ok');
    } catch (err) {
      memToast(err?.message || 'Consolidate failed', 'err');
    }
  }

  async function doDelete() {
    try {
      await del.mutateAsync(bank.bank_id);
      memToast(`Bank ${bank.bank_id} deleted`, 'ok');
      onDeleted();
    } catch (err) {
      memToast(err?.message || 'Delete failed', 'err');
      setConfirming(false);
    }
  }

  return (
    <div className="card mem-detail" data-testid="mem-bank-detail">
      <div className="mem-detail-head">
        <span className="mono">{bank.bank_id}</span>
        <div>
          <button className="btn ghost xs" onClick={doConsolidate} data-testid="mem-btn-consolidate">
            Consolidate now
          </button>
          <button className="btn ghost sm pf-form-close" onClick={onClose} aria-label="Close">×</button>
        </div>
      </div>
      <div className="sec">
        <h2>Operations</h2>
        <div className="rule" />
        <MemOperations bank={bank.bank_id} />
      </div>
      <div className="sec mem-danger">
        <h2>Danger zone</h2>
        <div className="rule" />
        {confirming ? (
          <div className="mem-confirm mono">
            Delete bank <b>{bank.bank_id}</b> and all its memories?
            <button className="btn danger xs" onClick={doDelete} data-testid="mem-btn-delete-confirm">Delete</button>
            <button className="btn ghost xs" onClick={() => setConfirming(false)}>Cancel</button>
          </div>
        ) : (
          <button className="btn ghost xs danger" onClick={() => setConfirming(true)} data-testid="mem-btn-delete-bank">
            Delete bank
          </button>
        )}
      </div>
    </div>
  );
}

// ── New bank form ─────────────────────────────────────────────────────────────

function MemNewBankForm({ onClose }) {
  const useBankUpsert = window.__hal0UseBankUpsert;
  const upsert = useBankUpsert ? useBankUpsert() : null;
  const [bankId, setBankId] = useStateMem('');
  const [mission, setMission] = useStateMem('');
  const [error, setError] = useStateMem(null);
  const [busy, setBusy] = useStateMem(false);

  async function submit(e) {
    e.preventDefault();
    const id = bankId.trim();
    if (!MEM_BANK_RE.test(id)) {
      setError('Lowercase letters, digits, hyphens, underscores');
      return;
    }
    setBusy(true);
    try {
      const body = mission.trim() ? { reflect_mission: mission.trim() } : {};
      await upsert.mutateAsync({ bank: id, body });
      memToast(`Bank ${id} created`, 'ok');
      onClose();
    } catch (err) {
      setError(err?.message || 'Create failed');
    } finally {
      setBusy(false);
    }
  }

  return (
    <form className="card mem-new-bank" onSubmit={submit} data-testid="mem-new-bank-form">
      <div className="mem-detail-head">
        <span className="mono">New bank</span>
        <button type="button" className="btn ghost sm pf-form-close" onClick={onClose} aria-label="Close">×</button>
      </div>
      <div className="form-row">
        <div className="form-lbl"><span>Bank id <span className="req">*</span></span></div>
        <div className="form-ctl">
          <input
            className={'input mono' + (error ? ' err' : '')}
            value={bankId}
            onChange={e => { setBankId(e.target.value); setError(null); }}
            placeholder="my-agent"
            maxLength={128}
            data-testid="mem-input-bank-id"
          />
          {error && <div className="hint err">{error}</div>}
        </div>
      </div>
      <div className="form-row">
        <div className="form-lbl">
          <span>Reflect mission</span>
          <span className="sub">optional — identity/context used by reflect</span>
        </div>
        <div className="form-ctl">
          <input
            className="input mono"
            value={mission}
            onChange={e => setMission(e.target.value)}
            placeholder="You are the memory of …"
            data-testid="mem-input-mission"
          />
        </div>
      </div>
      <div className="pf-form-foot">
        <button type="button" className="btn ghost sm" onClick={onClose}>Cancel</button>
        <button type="submit" className="btn sm" disabled={busy} data-testid="mem-btn-bank-submit">
          {busy ? 'Creating…' : 'Create'}
        </button>
      </div>
    </form>
  );
}

// ── Main view ─────────────────────────────────────────────────────────────────

function MemoryView({ param } = {}) {
  const section = param === 'graph' ? 'graph' : param === 'tools' ? 'tools' : 'overview';
  const useMemoryEngine = window.__hal0UseMemoryEngine;
  const useMemoryBanks = window.__hal0UseMemoryBanks;
  const engineQuery = useMemoryEngine ? useMemoryEngine() : { data: null, isLoading: false };
  const banksQuery = useMemoryBanks ? useMemoryBanks() : { data: null, isLoading: false };

  const banks = banksQuery.data?.banks || [];
  const [selectedId, setSelectedId] = useStateMem(null);
  const [creating, setCreating] = useStateMem(false);
  const [period, setPeriod] = useStateMem('7d');

  const selected = banks.find(b => b.bank_id === selectedId) || null;
  const chartBank = selected?.bank_id || banks[0]?.bank_id || null;

  return (
    <div className="view">
      <div className="view-head">
        <h2>Memory</h2>
        <div className="view-sub mono">
          Hindsight memory engine — banks, retained facts, consolidation and operations.
        </div>
      </div>

      <div className="mem-tabs">
        <button
          className={'btn ghost xs' + (section === 'overview' ? ' active' : '')}
          onClick={() => { window.location.hash = '#memory'; }}
          data-testid="mem-tab-overview"
        >
          Overview
        </button>
        <button
          className={'btn ghost xs' + (section === 'graph' ? ' active' : '')}
          onClick={() => { window.location.hash = '#memory/graph'; }}
          data-testid="mem-tab-graph"
        >
          Graph
        </button>
        <button
          className={'btn ghost xs' + (section === 'tools' ? ' active' : '')}
          onClick={() => { window.location.hash = '#memory/tools'; }}
          data-testid="mem-tab-tools"
        >
          Tools
        </button>
      </div>

      {section === 'graph' ? (
        <MemGraphExplorer />
      ) : section === 'tools' ? (
        <MemToolsPanel />
      ) : (
      <>
      <div className="mem-top">
        <MemEngineCard engine={engineQuery.data} isLoading={engineQuery.isLoading} />
        <MemTimeseries bank={chartBank} period={period} setPeriod={setPeriod} />
      </div>

      <div className="sec">
        <h2>Banks</h2>
        <div className="rule" />
        <div className="pf-toolbar">
          <button className="btn sm" onClick={() => setCreating(true)} data-testid="mem-btn-new-bank">
            + New bank
          </button>
        </div>
        {banksQuery.isLoading ? (
          <div className="empty mono">Loading banks…</div>
        ) : banks.length === 0 ? (
          <div className="empty mono">No memory banks yet.</div>
        ) : (
          <div className="mem-grid">
            {banks.map(b => (
              <MemBankCard
                key={b.bank_id}
                bank={b}
                selected={b.bank_id === selectedId}
                onSelect={(bank) => setSelectedId(bank.bank_id === selectedId ? null : bank.bank_id)}
              />
            ))}
          </div>
        )}
      </div>

      {creating && <MemNewBankForm onClose={() => setCreating(false)} />}

      {selected && (
        <MemBankDetail
          bank={selected}
          onClose={() => setSelectedId(null)}
          onDeleted={() => setSelectedId(null)}
        />
      )}
      </>
      )}
    </div>
  );
}

Object.assign(window, { MemoryView });
