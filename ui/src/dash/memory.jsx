// hal0 dashboard — Memory view (Hindsight engine surface).
//
// #memory route, gated on memory_enabled like #agent. Thin shell around the
// v2 surfaces: Overview (window.MemV2Overview) and the Bank workspace
// (window.MemV2Workspace), plus the engine loading/error/volatile states and
// the new-bank form. The pre-v2 components that used to live here
// (MemHindsightCard, MemBankCard, MemTimeseries, MemOperations,
// MemBankDetail + their MemProviderCard/MemCapabilitiesBadge/MemBankActivity
// helpers) were unreachable after the v2 rewrite and were removed in #2107 —
// bank delete, formerly buried in MemBankDetail, now lives in the Bank
// workspace's BankBar (memory-bank-bar.jsx).
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
// Announce a failed query loudly instead of letting it render as an empty
// state — an engine outage (503, dropped connection, hindsight-api
// restarting) must be distinguishable from a healthy quiet bank. Same
// defect class as #1471 in the graph explorer.
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

const MEM_BANK_RE = /^[a-z0-9][a-z0-9_-]{0,127}$/i;

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

// ── Root routing helpers (task C6) ──────────────────────────────────────────
//
// MemoryView keeps its own #memory/<section> sub-nav; main.jsx/agent-view.jsx
// resolve BOTH `#memory/<section>` and `#agent/memory/<section>` back to this
// component with `param` = the section (agent-view.jsx:39-52) — there is no
// rewrite anywhere, so navigation issued FROM HERE must preserve whichever
// prefix is currently active or a user sitting on `#agent/memory/bank` would
// get silently bounced to the bare `#memory/...` shape on their next click.
function memHashPrefix() {
  const hash = (typeof window !== 'undefined' && window.location.hash) || '';
  return hash.startsWith('#agent/memory') ? '#agent/memory' : '#memory';
}

// Deep-link query parsing: `?bank=<id>&fact=<id>` on the memory hash. Read
// directly off `window.location.hash` (NOT the `param` prop) — agent-view.jsx
// strips the query string before computing `param`/`section`, and the brief
// requires zero changes there, so this file owns its own query parsing.
function parseMemHashQuery() {
  const hash = (typeof window !== 'undefined' && window.location.hash) || '';
  const qIdx = hash.indexOf('?');
  if (qIdx === -1) return { bank: null, fact: null };
  const params = new URLSearchParams(hash.slice(qIdx + 1));
  return { bank: params.get('bank') || null, fact: params.get('fact') || null };
}

function writeMemHash(section, bank, fact) {
  const prefix = memHashPrefix();
  const path = section === 'bank' ? prefix + '/bank' : prefix;
  const params = new URLSearchParams();
  if (bank) params.set('bank', bank);
  if (fact) params.set('fact', fact);
  const qs = params.toString();
  window.location.hash = path + (qs ? '?' + qs : '');
}

// ── Main view ─────────────────────────────────────────────────────────────────

function MemoryView({ param } = {}) {
  // param contract: null → overview; 'bank' → the Bank workspace. Legacy
  // `#memory/graph` and `#memory/tools` (both folded into the new Bank
  // workspace's list/inspector/ego/web views) redirect to `#memory/bank`.
  const isLegacySection = param === 'graph' || param === 'tools';
  const section = param === 'bank' || isLegacySection ? 'bank' : 'overview';

  useEffectMem(() => {
    if (!isLegacySection) return;
    const hash = window.location.hash || '';
    const qIdx = hash.indexOf('?');
    const qs = qIdx >= 0 ? hash.slice(qIdx) : '';
    window.location.hash = memHashPrefix() + '/bank' + qs;
  }, [param]);

  const useMemoryEngine = window.__hal0UseMemoryEngine;
  const useMemoryBanks = window.__hal0UseMemoryBanks;
  const engineQuery = useMemoryEngine ? useMemoryEngine() : { data: null, isLoading: false, isError: false };
  const banksQuery = useMemoryBanks ? useMemoryBanks() : { data: null, isLoading: false };
  const banks = banksQuery.data?.banks || [];

  // Bank + fact selection: seeded from the deep-link query string first,
  // falling back to the persisted last-viewed bank (kept for the Graph/Tools
  // era's localStorage contract — still honoured for anyone landing on
  // `#memory/bank` with no `?bank=` of their own).
  const [bankId, setBankIdState] = useStateMem(() => {
    const fromHash = parseMemHashQuery().bank;
    if (fromHash) return fromHash;
    try { return localStorage.getItem(MEM_BANK_LS_KEY) || null; } catch { return null; }
  });
  const [sel, setSelState] = useStateMem(() => parseMemHashQuery().fact);
  const [growthBank, setGrowthBank] = useStateMem(bankId);
  const [creating, setCreating] = useStateMem(false);

  // Overview's growth chart needs a bank to fetch a timeseries for. On a
  // completely fresh session (no deep-link `?bank=`, nothing in
  // localStorage yet) `bankId`/`growthBank` both start null — default to
  // the first bank once the bank list loads, same as the Bank tab's own
  // `effectiveBank` fallback below.
  useEffectMem(() => {
    if (!growthBank && banks.length > 0) setGrowthBank(banks[0].bank_id);
  }, [growthBank, banks.length]);

  // Re-sync from the hash on navigation (back/forward, external hash edits,
  // or a deep link arriving after first mount).
  useEffectMem(() => {
    function onHashChange() {
      const q = parseMemHashQuery();
      if (q.bank) setBankIdState(q.bank);
      setSelState(q.fact);
    }
    window.addEventListener('hashchange', onHashChange);
    return () => window.removeEventListener('hashchange', onHashChange);
  }, []);

  // Persist bank selection — the shared key the old Graph/Tools tabs read.
  useEffectMem(() => {
    if (!bankId) return;
    try { localStorage.setItem(MEM_BANK_LS_KEY, bankId); } catch { /* ignore */ }
  }, [bankId]);

  function setBank(id) {
    setBankIdState(id);
    setSelState(null);
    if (section === 'bank') writeMemHash('bank', id, null);
  }
  function setSel(factId) {
    setSelState(factId);
    if (section === 'bank') writeMemHash('bank', bankId, factId);
  }
  // MemV2Overview's bank-table rows call this to jump into the workspace
  // pre-focused on that bank (its own "explore"/row-click affordance).
  function openBank(id) {
    setBankIdState(id);
    setSelState(null);
    writeMemHash('bank', id, null);
  }
  function goOverview() { writeMemHash('overview', null, null); }
  function goBank() { writeMemHash('bank', bankId, sel); }

  const engine = engineQuery.data;
  const engineLoading = engineQuery.isLoading;
  const engineDown = engineQuery.isError || engine?.reachable === false;
  // Fail-soft non-Hindsight fallback (routes/memory_admin.py's 501
  // memory.engine_unsupported path — e.g. a pgvector-only provider with no
  // Hindsight client): the engine answers, but graph/reflect/directives
  // aren't available and data isn't following the Hindsight persistence
  // path. Mocks always report `engine: 'hindsight'`, so this branch is
  // real-backend-only in practice.
  const engineVolatile = !engineDown && !!engine?.enabled && !!engine?.engine && engine.engine !== 'hindsight';
  const banksEmpty = !banksQuery.isLoading && banks.length === 0;
  const effectiveBank = bankId || banks[0]?.bank_id || null;

  return (
    <div className="view">
      {/* Heading intentionally omitted — MemoryView renders inside the Agent ▸
          Memory tab, which already supplies the page header. */}
      <div className="mem-tabs">
        <button
          className={'btn ghost xs' + (section === 'overview' ? ' active' : '')}
          onClick={goOverview}
          data-testid="mem-tab-overview"
        >
          Overview
        </button>
        <button
          className={'btn ghost xs' + (section === 'bank' ? ' active' : '')}
          onClick={goBank}
          data-testid="mem-tab-bank"
        >
          Bank
        </button>
      </div>

      {engineLoading ? (
        <div className="card mo-engine mo-provider" data-testid="mem-provider-card-hindsight">
          <div className="mo-engine-head">
            <span className="mono mo-engine-name"><Icon name="brain" size={15} /> Hindsight</span>
          </div>
          <div className="empty mono">Probing engine…</div>
        </div>
      ) : engineDown ? (
        <MemError query={engineQuery} what="the memory engine" testid="mem-engine-error" />
      ) : (
        <>
          {engineVolatile && (
            // task C8: the pgvector-only fallback (memory_admin.py's 501
            // memory.engine_unsupported path has no Hindsight client to talk
            // to) keeps facts in an in-process/ephemeral store, not
            // Hindsight's durable persistence — a restart of the fallback
            // provider loses them. Say so plainly rather than just "data is
            // stored".
            <div className="empty mono" data-testid="mem-engine-volatile" style={{ marginBottom: 14 }}>
              Memory is running on a non-Hindsight fallback engine ({engine.engine}) — volatile,
              writes don't survive restart. Graph extraction, reflect, and directives aren't
              available either.
            </div>
          )}
          {section === 'overview' ? (
            <>
              {typeof window.MemV2Overview === 'function' ? (
                <window.MemV2Overview onExplore={openBank} growthBank={growthBank} setGrowthBank={setGrowthBank} />
              ) : null}
              <div style={{ marginTop: 14 }}>
                {creating ? (
                  <MemNewBankForm onClose={() => setCreating(false)} />
                ) : (
                  <button className="btn ghost sm" onClick={() => setCreating(true)} data-testid="mem-btn-new-bank">
                    + New bank
                  </button>
                )}
              </div>
            </>
          ) : banksEmpty ? (
            <div className="card mo-main mo-main-empty" data-testid="mem-main-empty">
              <div className="empty mono">No memory banks yet. Create one to start recording.</div>
              {creating ? (
                <MemNewBankForm onClose={() => setCreating(false)} />
              ) : (
                <button className="btn sm" style={{ marginTop: 10 }} onClick={() => setCreating(true)} data-testid="mem-btn-new-bank">
                  + New bank
                </button>
              )}
            </div>
          ) : effectiveBank && typeof window.MemV2Workspace === 'function' ? (
            <window.MemV2Workspace bank={effectiveBank} setBank={setBank} sel={sel} setSel={setSel} />
          ) : null}
        </>
      )}
    </div>
  );
}

Object.assign(window, { MemoryView });
