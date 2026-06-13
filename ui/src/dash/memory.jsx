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

const { useState: useStateMem, useEffect: useEffectMem } = React;

const MEM_BANK_LS_KEY = 'hal0.mem.bank';

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

function MemEngineCard({ engine, isLoading, onOpenGraph }) {
  if (isLoading) {
    return (
      <div className="card mo-engine" data-testid="mem-engine-card">
        <div className="mo-engine-head">
          <span className="mono mo-engine-name"><Icon name="brain" size={15} /> memory engine</span>
        </div>
        <div className="empty mono">Probing engine…</div>
      </div>
    );
  }
  const e = engine || {};
  const enabled = e.enabled !== false;
  const reachable = !!e.reachable;
  const features = e.features || {};
  const featureNames = Object.keys(features);
  const graphOn = !!features.graph;
  return (
    <div className="card mo-engine" data-testid="mem-engine-card">
      <div className="mo-engine-head">
        <span className="mono mo-engine-name">
          <Icon name="brain" size={15} /> {e.engine || (enabled ? 'no engine' : 'disabled')}
        </span>
        <span className={'chip ' + (reachable ? 'ok' : 'warn')}>
          {reachable ? 'reachable' : 'unreachable'}
        </span>
      </div>
      <div className="mo-engine-meta mono">
        <span>{e.version ? `v${e.version}` : 'version unknown'}</span>
        <span className="pf-sep">·</span>
        <span>{e.banks_total != null ? `${e.banks_total} banks` : '— banks'}</span>
      </div>
      <div className="mo-feature-row">
        {featureNames.length === 0 ? (
          <span className="mo-badge mono">no features</span>
        ) : (
          featureNames.map((name) => {
            const on = !!features[name];
            return (
              <span
                key={name}
                className={'mo-badge mono' + (on ? ' on' : '')}
                title={`engine feature: ${name} (${on ? 'on' : 'off'})`}
              >
                {on && <span className="dot ready" />}{name}
              </span>
            );
          })
        )}
      </div>
      <div className="mo-graphline">
        <span className="mono">
          <span className={'dot' + (graphOn ? ' ready' : '')} /> graph extraction ·{' '}
          <b style={{ color: graphOn ? 'var(--ok)' : 'var(--fg-4)' }}>{graphOn ? 'on' : 'off'}</b>
        </span>
        <button className="btn ghost xs" onClick={onOpenGraph} data-testid="mem-btn-open-graph">
          Open graph <Icon name="arrow" size={11} />
        </button>
      </div>
    </div>
  );
}

// ── Timeseries chart (stacked spark-bars, mo- house style) ────────────────────

function MemTimeseries({ bank, period, setPeriod }) {
  const useBankTimeseries = window.__hal0UseBankTimeseries;
  const query = useBankTimeseries ? useBankTimeseries(bank, period) : { data: null };
  const buckets = query.data?.buckets || [];

  // total per bucket drives bar height; segments stack the three fact types.
  const totals = buckets.map(b => MEM_FACT_TYPES.reduce((s, t) => s + (b[t] || 0), 0));
  const maxVal = Math.max(1, ...totals);

  return (
    <div className="card mo-ts" data-testid="mem-timeseries">
      <div className="mo-ts-head">
        <span className="mono">memories retained · {bank || '—'}</span>
        <div className="mo-ts-periods">
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
        <div className="mo-spark" role="img" aria-label="memories timeseries">
          {buckets.map((b, i) => {
            const total = totals[i];
            return (
              <i
                key={b.time || i}
                style={{
                  height: `${(total / maxVal) * 100}%`,
                  display: 'flex',
                  flexDirection: 'column-reverse',
                  background: 'transparent',
                }}
                title={`${b.time || ''} · ${total} facts`}
              >
                {MEM_FACT_TYPES.map(t => {
                  const v = b[t] || 0;
                  if (!v || !total) return null;
                  return (
                    <span
                      key={t}
                      style={{
                        display: 'block',
                        height: `${(v / total) * 100}%`,
                        background: MEM_FACT_COLORS[t],
                      }}
                    />
                  );
                })}
              </i>
            );
          })}
        </div>
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
  const pending = stats?.pending_operations || 0;
  const failed = stats?.failed_operations || 0;
  function onKey(ev) {
    if (ev.key === 'Enter' || ev.key === ' ') { ev.preventDefault(); onSelect(bank); }
  }
  return (
    <div
      className={'mo-bank' + (selected ? ' active' : '')}
      data-testid={`mem-bank-${bank.bank_id}`}
      onClick={() => onSelect(bank)}
      onKeyDown={onKey}
      role="button"
      tabIndex={0}
    >
      <div className="mo-bank-head">
        <span className="mono mo-bank-id">{bank.bank_id}</span>
        <div className="mem-bank-badges">
          {pending > 0 && (
            <span className="mo-badge warn mono" title="pending operations">{pending} pending</span>
          )}
          {failed > 0 && (
            <span className="mo-badge warn mono" style={{ color: 'var(--err)', borderColor: 'var(--err-line)' }} title="failed operations">{failed} failed</span>
          )}
        </div>
      </div>
      {bank.mission && <div className="mo-bank-mission">{bank.mission}</div>}
      <div className="mo-bank-counts mono">
        {MEM_FACT_TYPES.map(t => (
          <span key={t} className="mem-count" title={`${t} facts`}>
            <span className="mem-swatch" style={{ background: MEM_FACT_COLORS[t] }} />
            {t} <span className="num">{byType[t] || 0}</span>
          </span>
        ))}
      </div>
      <div className="mo-bank-meta mono">
        <span><span className="num">{stats?.total_documents ?? '—'}</span> docs</span>
        <span className="pf-sep">·</span>
        <span><span className="num">{stats?.total_links ?? '—'}</span> links</span>
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
  const [selectedId, setSelectedId] = useStateMem(() => {
    try { return localStorage.getItem(MEM_BANK_LS_KEY) || null; } catch { return null; }
  });
  const [creating, setCreating] = useStateMem(false);
  const [period, setPeriod] = useStateMem('7d');

  // Persist selection to the shared key the Graph/Tools tabs read.
  useEffectMem(() => {
    if (!selectedId) return;
    try { localStorage.setItem(MEM_BANK_LS_KEY, selectedId); } catch { /* ignore */ }
  }, [selectedId]);

  const selected = banks.find(b => b.bank_id === selectedId) || null;
  const chartBank = selected?.bank_id || banks[0]?.bank_id || null;

  function selectBank(bankId) {
    const next = bankId === selectedId ? null : bankId;
    setSelectedId(next);
    if (next) { try { localStorage.setItem(MEM_BANK_LS_KEY, next); } catch { /* ignore */ } }
  }

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
      <div className="mo">
      <div className="mo-top">
        <MemEngineCard
          engine={engineQuery.data}
          isLoading={engineQuery.isLoading}
          onOpenGraph={() => { window.location.hash = '#memory/graph'; }}
        />
        <MemTimeseries bank={chartBank} period={period} setPeriod={setPeriod} />
      </div>

      <div className="sec">
        <h2>Banks {banks.length > 0 && <span className="ct">{banks.length}</span>}</h2>
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
          <div className="mo-grid">
            {banks.map(b => (
              <MemBankCard
                key={b.bank_id}
                bank={b}
                selected={b.bank_id === selectedId}
                onSelect={(bank) => selectBank(bank.bank_id)}
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
      </div>
      )}
    </div>
  );
}

Object.assign(window, { MemoryView });
