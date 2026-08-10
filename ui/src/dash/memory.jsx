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

// ── Engine-unreachable state (#1539) ────────────────────────────────────────
//
// These panels read `query.data?.<list> || []` and render an empty-state when
// the list is short. A failed query has `data === undefined`, so an engine
// outage — 503, dropped connection, hindsight-api restarting — renders as
// "No retain activity in this window." or, for operations, as NOTHING AT ALL
// (that panel returns null on an empty list, so the whole card vanishes).
// Neither is distinguishable from a healthy quiet bank.
//
// Same defect as #1471 in the graph explorer, repeated here — except these
// panels had no branch to get wrong: none consulted `isError`. They were also
// untestable until #1538 made a non-ok response representable under
// forced-mock, which is why they shipped unnoticed.
function MemError({ query, what, testid }) {
  if (!query?.isError) return null;
  return (
    <div className="empty mono mem-error" data-testid={testid}>
      <div>Memory engine unreachable — {query.error?.message || `could not load ${what}`}</div>
      {query.refetch && (
        <button
          className="btn ghost sm"
          style={{ marginTop: 8 }}
          data-testid={`${testid}-retry`}
          onClick={() => query.refetch()}
        >
          Retry
        </button>
      )}
    </div>
  );
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

// ── Provider card shell ─────────────────────────────────────────────────────
//
// Shared shell for the engine-identity card (Hindsight) so the
// Memory pane reads as "two providers, one system" instead of two bespoke
// layouts. Callers supply the identity bits (icon/name/chip/meta), a 3-up
// stats grid, an actions row, and an optional footer (graph-extraction /
// sync-timer line).
function MemProviderCard({ testId, icon, name, chip, meta, stats, actions, footer, dimmed, children }) {
  return (
    <div className={'card mo-engine mo-provider' + (dimmed ? ' dimmed' : '')} data-testid={testId}>
      <div className="mo-engine-head">
        <span className="mono mo-engine-name">{icon} {name}</span>
        {chip}
      </div>
      {meta && <div className="mo-engine-meta mono">{meta}</div>}
      {stats && stats.length > 0 && (
        <div className="mo-provider-stats">
          {stats.map((s, i) => (
            <div className="mo-provider-stat" key={i} title={s.title}>
              <div className={'mo-provider-stat-v mono num' + (s.warn ? ' warn' : '')}>{s.value}</div>
              <div className="mo-provider-stat-l mono">{s.label}</div>
            </div>
          ))}
        </div>
      )}
      {children}
      {actions && <div className="mo-provider-actions">{actions}</div>}
      {footer}
    </div>
  );
}

// Collapsed engine-feature badge wall → one compact "N/M capabilities" chip
// that pops a list open on click (same toggle idiom as the failed-ops popover
// in MemBankActivity below).
function MemCapabilitiesBadge({ features }) {
  const [open, setOpen] = useStateMem(false);
  const names = Object.keys(features || {});
  if (names.length === 0) {
    return <span className="mo-badge mono">no features</span>;
  }
  const onCount = names.filter((n) => features[n]).length;
  function toggle(ev) {
    ev.preventDefault();
    ev.stopPropagation();
    setOpen((v) => !v);
  }
  return (
    <span className="mo-cap-wrap">
      <button
        type="button"
        className={'mo-badge mono mo-cap-btn' + (open ? ' open' : '')}
        onClick={toggle}
        data-testid="mem-caps-toggle"
      >
        {onCount}/{names.length} capabilities
      </button>
      {open && (
        <span className="mo-cap-pop mono" onClick={(e) => e.stopPropagation()}>
          {names.map((n) => (
            <span key={n} className={'mo-cap-row' + (features[n] ? ' on' : '')}>
              <span className="dot" style={{ opacity: features[n] ? 1 : 0.3 }} />{n}
            </span>
          ))}
        </span>
      )}
    </span>
  );
}

// ── Hindsight provider card ─────────────────────────────────────────────────

function MemHindsightCard({
  engine, isLoading, banks, graphEnabled, graphErrors, onOpenGraph,
  selectedBank, onConsolidate, consolidating, onRetryFailed, retryingFailed,
}) {
  // GET /api/memory/banks is a verbatim Hindsight passthrough with no
  // reliable per-bank fact count, so "Facts" is sourced from the real
  // per-bank /stats (total_nodes), aggregated across every bank — NOT from
  // a `fact_count` field on the bank-list objects (that field isn't part of
  // the real API and only ever showed up in a forced-mock fixture). This
  // hook must be called unconditionally, before the isLoading early return,
  // to keep hook order stable across renders.
  const useAggregateBankStats = window.__hal0UseAggregateBankStats;
  const bankIds = (banks || []).map((b) => b.bank_id);
  const factsQuery = useAggregateBankStats
    ? useAggregateBankStats(bankIds)
    : { isLoading: false, totalFacts: 0 };

  if (isLoading) {
    return (
      <div className="card mo-engine mo-provider" data-testid="mem-provider-card-hindsight">
        <div className="mo-engine-head">
          <span className="mono mo-engine-name"><Icon name="brain" size={15} /> Hindsight</span>
        </div>
        <div className="empty mono">Probing engine…</div>
      </div>
    );
  }
  const e = engine || {};
  const enabled = e.enabled !== false;
  const reachable = !!e.reachable;
  const features = e.features || {};
  const totalFacts = factsQuery.totalFacts;
  // Graph-extraction on/off comes from /api/memory/graph/status (the real
  // state), NOT the Hindsight capability flags in /engine — those have no
  // `graph` key, so keying off them always read "off".
  const graphOn = !!graphEnabled;
  const errors = graphErrors || 0;

  const chip = (
    <span className={'chip ' + (reachable ? 'ok' : 'warn')}>{reachable ? 'reachable' : 'unreachable'}</span>
  );
  const meta = (
    <>
      <span>{e.version ? `v${e.version}` : 'version unknown'}</span>
      <span className="pf-sep">·</span>
      <span>{e.banks_total != null ? `${e.banks_total} bank${e.banks_total === 1 ? '' : 's'}` : '— banks'}</span>
    </>
  );
  const stats = [
    { label: 'Banks', value: e.banks_total ?? (banks || []).length },
    { label: 'Facts', value: factsQuery.isLoading && totalFacts === 0 ? '…' : totalFacts, title: "sum of total_nodes across every bank's /stats" },
    { label: 'Failed ops', value: errors, warn: errors > 0, title: 'aggregate failed extraction/consolidation ops' },
  ];
  const actions = (
    <>
      <button
        className="btn ghost xs"
        style={{ color: 'var(--yellow)', borderColor: 'var(--yellow-line, var(--line))' }}
        onClick={onConsolidate}
        disabled={consolidating || !selectedBank}
        title={selectedBank ? undefined : 'Select a bank below to consolidate'}
        data-testid="mem-btn-consolidate"
      >
        {consolidating ? 'Consolidating…' : 'Consolidate'}
      </button>
      {errors > 0 && (
        <button
          className="btn ghost xs"
          style={{ color: 'var(--warn)', borderColor: 'var(--warn)' }}
          onClick={onRetryFailed}
          disabled={retryingFailed}
          data-testid="mem-btn-retry-failed"
        >
          {retryingFailed ? 'Retrying…' : `Retry ${errors} failed`}
        </button>
      )}
      <button className="btn ghost xs" onClick={onOpenGraph} data-testid="mem-btn-open-graph">
        Open graph <Icon name="arrow" size={11} />
      </button>
    </>
  );
  const footer = (
    <div className="mo-graphline">
      <span className="mono">
        <span className={'dot' + (graphOn ? ' ready' : '')} /> graph extraction ·{' '}
        <b style={{ color: graphOn ? 'var(--ok)' : 'var(--fg-4)' }}>{graphOn ? 'on' : 'off'}</b>
      </span>
      <MemCapabilitiesBadge features={features} />
    </div>
  );

  return (
    <MemProviderCard
      testId="mem-provider-card-hindsight"
      icon={<Icon name="brain" size={15} />}
      name={enabled ? (e.engine || 'hindsight') : 'disabled'}
      chip={chip}
      meta={meta}
      stats={stats}
      actions={actions}
      footer={footer}
    />
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
      <div className="mo-ts-head mo-card-h">
        <span className="mo-eyebrow" style={{ overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>memories retained · {bank || '—'}</span>
      </div>
      <MemError query={query} what="the retain timeseries" testid="mem-timeseries-error" />
      {query.isError ? null : buckets.length === 0 ? (
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
      <div className="mo-ts-periods" style={{ marginTop: 8, display: 'flex', alignItems: 'center', gap: 4 }}>
        {['1d', '7d', '30d'].map(p => (
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
  );
}

// ── Live per-bank activity (ingest / extraction / consolidation) ──────────────
//
// Polls GET /api/memory/banks/{bank}/operations via the shared, adaptive
// useBankOperations query (fast while work is in flight, backing off when
// idle) so the operator can SEE what's happening and WHERE. Renders a spinner
// "N working" badge while pending+processing > 0, plus pending/processing/
// completed/failed counts. Failed count is a button that reveals the failed
// operation types (affordance for "extraction failed, roughly why").
function MemBankActivity({ bank, active = true, compact = false }) {
  const useBankOperations = window.__hal0UseBankOperations;
  const summarize = window.__hal0MemSummarizeOps;
  const q = useBankOperations ? useBankOperations(bank, { enabled: active }) : { data: null };
  const [showFailed, setShowFailed] = useStateMem(false);
  const a = summarize
    ? summarize(q.data)
    : { pending: 0, processing: 0, completed: 0, failed: 0, inFlight: 0, failedTypes: [], total: 0 };

  if (!q.data) return null;
  if (a.total === 0) {
    return compact ? null : <span className="mem-act-quiet mono">no activity yet</span>;
  }

  function toggleFailed(ev) {
    ev.preventDefault();
    ev.stopPropagation();
    setShowFailed(v => !v);
  }

  return (
    <span className="mem-activity mono" data-testid={`mem-activity-${bank}`}>
      {a.inFlight > 0 && (
        <span className="mem-act-badge working" title={`${a.inFlight} operation(s) in flight`}>
          <span className="mem-spin" aria-hidden="true" />
          {a.inFlight} working
        </span>
      )}
      {a.processing > 0 && <span className="mem-act-chip proc" title="processing">{a.processing} proc</span>}
      {a.pending > 0 && <span className="mem-act-chip pend" title="queued / pending">{a.pending} pending</span>}
      {!compact && a.completed > 0 && (
        <span className="mem-act-chip done" title="completed">{a.completed} done</span>
      )}
      {a.failed > 0 && (
        <button
          type="button"
          className={'mem-act-chip failed' + (showFailed ? ' open' : '')}
          onClick={toggleFailed}
          title="failed operations — click to see which"
          data-testid={`mem-failed-${bank}`}
        >
          {a.failed} failed
        </button>
      )}
      {showFailed && a.failed > 0 && (
        <span
          className="mem-failed-pop mono"
          data-testid={`mem-failed-pop-${bank}`}
          onClick={(e) => e.stopPropagation()}
        >
          <span className="mem-failed-h">failed operations</span>
          {a.failedTypes.map((t, i) => (
            <span key={i} className="mem-failed-row">{t}</span>
          ))}
        </span>
      )}
    </span>
  );
}

// ── Bank card ─────────────────────────────────────────────────────────────────

function MemBankCard({ bank, selected, onSelect }) {
  const useBankStats = window.__hal0UseBankStats;
  const statsQuery = useBankStats ? useBankStats(bank.bank_id) : { data: null };
  const stats = statsQuery.data;
  const byType = stats?.nodes_by_fact_type || {};
  // #1539: a failed /stats left every count reading 0, so an unreachable
  // engine looked like a bank with nothing in it. One card per bank here, so
  // a full banner would be noise — a chip carries the distinction instead.
  const statsFailed = !!statsQuery.isError;
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
        {statsFailed && (
          <span
            className="chip err"
            data-testid={`mem-bank-stats-error-${bank.bank_id}`}
            title={statsQuery.error?.message || 'bank stats unavailable — engine unreachable'}
          >
            stats unavailable
          </span>
        )}
        <div className="mem-bank-badges">
          <MemBankActivity bank={bank.bank_id} compact />
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
  const items = query.data?.operations || [];

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

  if (query.isError) {
    // Was `return null` for an empty list, which also swallowed this case —
    // the whole operations card silently disappeared on an outage.
    return <MemError query={query} what="operations" testid="mem-operations-error" />;
  }
  if (items.length === 0) {
    return null;
  }
  return (
    <div className="mem-ops">
      {items.map(op => (
        <div className="mem-op-row" key={op.id} data-testid={`mem-op-${op.id}`}>
          <span className={'chip ' + (op.status === 'failed' ? 'err' : op.status === 'completed' ? 'ok' : 'warn')}>
            {op.status}
          </span>
          <span className="mono mem-op-type">{op.task_type}</span>
          <span className="mono mem-op-when">{fmtWhen(op.created_at)}</span>
          {op.error_message && <span className="mem-op-err mono" title={op.error_message}>{op.error_message}</span>}
          <span className="mem-op-actions">
            {op.status === 'failed' && (
              <button className="btn ghost xs" data-testid="mem-op-retry" onClick={() => doRetry(op.id)}>
                Retry
              </button>
            )}
            {op.status === 'pending' && (
              <button className="btn ghost xs danger" data-testid="mem-op-cancel" onClick={() => doCancel(op.id)}>
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

function MemBankDetail({ bank, period, setPeriod, onClose, onDeleted }) {
  const useConsolidate = window.__hal0UseConsolidate;
  const useBankDelete = window.__hal0UseBankDelete;
  const consolidate = useConsolidate ? useConsolidate() : null;
  const del = useBankDelete ? useBankDelete() : null;
  const [confirming, setConfirming] = useStateMem(false);

  async function doConsolidate() {
    try {
      // Any 2xx is success — Hindsight may 202 with an empty body (res === null)
      // or return the queued op under operation_id / id / operation.
      const res = await consolidate.mutateAsync(bank.bank_id);
      const opId = res?.operation_id ?? res?.id ?? res?.operation;
      memToast(`Consolidation queued (${opId || 'ok'})`, 'ok');
    } catch (err) {
      // "already in progress" (409) / "nothing to consolidate" aren't failures
      // — the queue is simply busy or empty; report them informationally.
      const msg = err?.message || '';
      if (err?.status === 409 || /in progress|nothing to consolidate/i.test(msg)) {
        memToast(msg || 'Consolidation already in progress', 'info');
      } else {
        memToast(msg || 'Consolidate failed', 'err');
      }
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
    <div className="card mem-detail mo-main" data-testid="mem-bank-detail">
      <div className="mem-detail-head">
        <span className="mono">{bank.bank_id}</span>
        <div>
          <button className="btn ghost sm pf-form-close" onClick={onClose} aria-label="Close">×</button>
        </div>
      </div>
      {bank.mission && <div className="mo-main-mission">{bank.mission}</div>}
      {/* Live activity for this bank — spinner while ingest/extraction/
          consolidation is in flight, with failed-ops affordance. */}
      {/* Memories-retained spark moved to the left column beside engine + graph. */}
      <div className="mem-detail-activity">
        <MemBankActivity bank={bank.bank_id} />
      </div>
      {/* Full tools surface (recall · reflect · documents · models ·
          directives) for this bank, embedded so it lives in the primary
          bank display instead of a separate sub-tab. */}
      <div className="sec">
        <h2>Tools</h2>
        <div className="rule" />
      </div>
      {typeof MemToolsPanel === 'function' ? <MemToolsPanel bank={bank.bank_id} embedded /> : null}
      {/* .sec is a flex title-row — keep only the heading in it; content
          (ops list, danger controls) must be siblings or they shrink-wrap. */}
      <div className="sec">
        <h2>Operations</h2>
        <div className="rule" />
      </div>
      <MemOperations bank={bank.bank_id} />
      <div className="sec mem-danger">
        <h2>Danger zone</h2>
        <div className="rule" />
      </div>
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
          <FieldInfoIcon description="optional — identity/context used by reflect" />
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
  // Tools moved into the primary bank display, so #memory/tools falls back to
  // Overview; only Overview + Graph remain as sub-sections.
  const section = param === 'graph' ? 'graph' : 'overview';
  const useMemoryEngine = window.__hal0UseMemoryEngine;
  const useMemoryBanks = window.__hal0UseMemoryBanks;
  const useMemoryGraphStatus = window.__hal0UseMemoryGraphStatus;
  const engineQuery = useMemoryEngine ? useMemoryEngine() : { data: null, isLoading: false };
  const banksQuery = useMemoryBanks ? useMemoryBanks() : { data: null, isLoading: false };
  const graphQuery = useMemoryGraphStatus ? useMemoryGraphStatus() : { data: null };
  const graphEnabled = !!graphQuery.data?.enabled;
  const graphInFlight = graphQuery.data?.in_flight || 0;
  const useConsolidate = window.__hal0UseConsolidate;
  const consolidate = useConsolidate ? useConsolidate() : null;
  const useRetryFailedExtractions = window.__hal0UseRetryFailedExtractions;
  const retryFailed = useRetryFailedExtractions ? useRetryFailedExtractions() : null;

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

  function selectBank(bankId) {
    const next = bankId === selectedId ? null : bankId;
    setSelectedId(next);
    if (next) { try { localStorage.setItem(MEM_BANK_LS_KEY, next); } catch { /* ignore */ } }
  }

  const doConsolidate = async () => {
    if (!selected) return;
    // The consolidate hook is republished on `window` by memory-hook-bridge;
    // if the bridge hasn't loaded, fail loudly instead of silently swallowing
    // the click (the old `return` made the button look like it did nothing).
    if (!consolidate) {
      memToast('Consolidate unavailable — reload the page', 'err');
      return;
    }
    if (consolidate.isPending) return; // guard double-fire
    try {
      const res = await consolidate.mutateAsync(selected.bank_id);
      const opId = res?.operation_id ?? res?.id ?? res?.operation;
      memToast(`Consolidation queued (${opId || 'ok'})`, 'ok');
    } catch (err) {
      const msg = err?.message || '';
      if (err?.status === 409 || /in progress|nothing to consolidate/i.test(msg)) {
        memToast(msg || 'Consolidation already in progress', 'info');
      } else {
        memToast(msg || 'Consolidate failed', 'err');
      }
    }
  };

  const doRetryFailed = async () => {
    if (!retryFailed) return;
    try {
      const res = await retryFailed.mutateAsync();
      const q = res?.queued ?? 0;
      const s = res?.skipped ?? 0;
      memToast(
        q === 0 && s === 0
          ? 'No failed extractions to retry'
          : `Requeued ${q} failed extraction${q === 1 ? '' : 's'}${s ? ` · ${s} skipped` : ''}`,
        'ok',
      );
    } catch (err) {
      memToast(err?.message || 'Retry failed — see logs', 'err');
    }
  };

  return (
    <div className="view">
      {/* Heading intentionally omitted — MemoryView renders inside the Agent ▸
          Memory tab, which already supplies the page header. */}
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
      </div>

      {section === 'graph' ? (
        <MemGraphExplorer />
      ) : (
      <div className="mo">
      {/* Provider identity: the Hindsight engine card owns its own
          reachability/stats/actions. Full-width so the 3-up stat grid has
          room to breathe. */}
      <div className="mo-providers-row">
        <MemHindsightCard
          engine={engineQuery.data}
          isLoading={engineQuery.isLoading}
          banks={banks}
          graphEnabled={graphEnabled}
          graphErrors={graphQuery.data?.errors ?? 0}
          onOpenGraph={() => { window.location.hash = '#memory/graph'; }}
          selectedBank={selected}
          onConsolidate={doConsolidate}
          consolidating={!!consolidate?.isPending}
          onRetryFailed={doRetryFailed}
          retryingFailed={!!retryFailed?.isPending}
        />
      </div>
      {/* Aggregated graph-extraction stats + bank timeseries, beside the
          bank list. */}
      <div className="mo-top2">
        <div style={{display: "flex", flexDirection: "column", gap: 14}}>
          <MemoryGraphPanel />
          {selected && (
            <MemTimeseries bank={selected.bank_id} period={period} setPeriod={setPeriod} />
          )}
        </div>
        <div className="mo-banks-col">
          {/* .sec is a flex title-row (h2 + flex:1 rule); content must be a
              SIBLING, not a child, or it shrink-wraps into the heading row. */}
          <div className="sec">
            <h2>Banks {banks.length > 0 && <span className="ct">{banks.length}</span>}</h2>
            <div className="rule" />
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

          {creating && <MemNewBankForm onClose={() => setCreating(false)} />}

          <div className="sec" style={{marginTop: 22}}>
            <h2>{selected ? selected.bank_id : 'Detail'}</h2>
            <div className="rule" />
          </div>
          {selected ? (
            <MemBankDetail
              bank={selected}
              period={period}
              setPeriod={setPeriod}
              onClose={() => setSelectedId(null)}
              onDeleted={() => setSelectedId(null)}
            />
          ) : (
            <div className="card mo-main mo-main-empty" data-testid="mem-main-empty">
              <div className="empty mono">Select a bank above to see its memories, graph extraction, and operations.</div>
            </div>
          )}
        </div>
      </div>
      </div>
      )}
    </div>
  );
}

Object.assign(window, { MemoryView });
