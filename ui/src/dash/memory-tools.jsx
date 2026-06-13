// hal0 dashboard — Memory tools (#memory/tools).
//
// Management surface for one Hindsight bank:
//   - Recall console (query/budget/types → ranked facts)
//   - Reflect playground (disposition-aware answer + based_on counts)
//   - Documents browser (delete / reprocess — both async-op producers)
//   - Mental models (stale badge, refresh)
//   - Directives (create / toggle / delete)
//
// Hooks via window.__hal0Use* (memory-hook-bridge.ts).
// Visual skin: memory-overhaul.css mt-* classes (bankbar/grid/card/head/…).

const { useState: useStateMTl } = React;

function mtToast(msg, kind = 'info') {
  if (typeof window !== 'undefined' && window.__hal0Toast) window.__hal0Toast(msg, kind);
}

// fact-type → dot/swatch color (shared with the graph engine).
function mtFactColor(t) {
  const c = window.MEM_FACT_COLORS;
  return (c && c[t]) || 'var(--info)';
}

// ── Tool card shell (matches prototype ToolCard) ────────────────────────────────

function MtCard({ title, action, wide, testid, children }) {
  return (
    <div className={'card mt-card' + (wide ? ' wide' : '')} data-testid={testid}>
      <div className="mt-head mono">
        {title}
        {action}
      </div>
      {children}
    </div>
  );
}

// ── Recall console ────────────────────────────────────────────────────────────

function MemRecallConsole({ bank }) {
  const useRecall = window.__hal0UseRecall;
  const recall = useRecall ? useRecall() : null;
  const [q, setQ] = useStateMTl('');
  const [budget, setBudget] = useStateMTl('mid');
  const [types, setTypes] = useStateMTl(['world', 'experience', 'observation']);
  const [results, setResults] = useStateMTl(null);
  const [busy, setBusy] = useStateMTl(false);

  function toggleType(t) {
    setTypes(ts => (ts.includes(t) ? ts.filter(x => x !== t) : [...ts, t]));
  }

  async function run() {
    if (!q.trim() || !bank) return;
    setBusy(true);
    try {
      const body = { query: q.trim(), budget, types };
      const out = await recall.mutateAsync({ bank, body });
      setResults(out?.results || []);
    } catch (err) {
      mtToast(err?.message || 'Recall failed', 'err');
    } finally {
      setBusy(false);
    }
  }

  return (
    <MtCard title="recall console" testid="mem-recall">
      <div className="mt-row">
        <input
          className="input mono"
          value={q}
          onChange={e => setQ(e.target.value)}
          onKeyDown={e => { if (e.key === 'Enter') run(); }}
          placeholder="ask the bank…"
          data-testid="mem-recall-q"
        />
        <select
          className="input mono"
          style={{ width: 78 }}
          value={budget}
          onChange={e => setBudget(e.target.value)}
          data-testid="mem-recall-budget"
          aria-label="Budget"
        >
          <option value="low">low</option>
          <option value="mid">mid</option>
          <option value="high">high</option>
        </select>
        <button className="btn sm" onClick={run} disabled={busy} data-testid="mem-recall-run">
          {busy ? 'Recalling…' : 'Recall'}
        </button>
      </div>
      <div className="mt-types mono">
        {['world', 'experience', 'observation'].map(t => (
          <label key={t} className="mt-check">
            <input type="checkbox" checked={types.includes(t)} onChange={() => toggleType(t)} />
            <i style={{ background: mtFactColor(t) }} />
            {t}
          </label>
        ))}
      </div>
      {results && (
        <div className="mt-results" data-testid="mem-recall-results">
          {results.length === 0 && <div className="empty mono">No matches.</div>}
          {results.map(r => (
            <div className="mt-result" key={r.id}>
              <span className="mt-fact-dot" style={{ background: mtFactColor(r.type) }} title={r.type} />
              <span className="mt-result-text">{r.text}</span>
              <span className="mt-result-meta mono">
                {r.type}
                {r.tags?.length ? ` · ${r.tags.join(', ')}` : ''}
              </span>
            </div>
          ))}
        </div>
      )}
    </MtCard>
  );
}

// ── Reflect playground ────────────────────────────────────────────────────────

function MemReflectPlayground({ bank }) {
  const useReflect = window.__hal0UseReflect;
  const reflect = useReflect ? useReflect() : null;
  const [q, setQ] = useStateMTl('');
  const [out, setOut] = useStateMTl(null);
  const [busy, setBusy] = useStateMTl(false);

  async function run() {
    if (!q.trim() || !bank) return;
    setBusy(true);
    try {
      const res = await reflect.mutateAsync({ bank, body: { query: q.trim() } });
      setOut(res || null);
    } catch (err) {
      mtToast(err?.message || 'Reflect failed', 'err');
    } finally {
      setBusy(false);
    }
  }

  const basedOn = out?.based_on || null;
  return (
    <MtCard title="reflect playground" testid="mem-reflect">
      <div className="mt-row">
        <input
          className="input mono"
          value={q}
          onChange={e => setQ(e.target.value)}
          onKeyDown={e => { if (e.key === 'Enter') run(); }}
          placeholder="reason over this bank…"
          data-testid="mem-reflect-q"
        />
        <button className="btn sm" onClick={run} disabled={busy} data-testid="mem-reflect-run">
          {busy ? 'Reflecting…' : 'Reflect'}
        </button>
      </div>
      {out && (
        <div className="mt-reflect" data-testid="mem-reflect-out">
          <div className="mt-reflect-text">{out.text}</div>
          {basedOn && (
            <div className="mt-reflect-based mono">
              based on {basedOn.memories ?? 0} memories · {basedOn.mental_models ?? 0} mental
              models · {basedOn.directives ?? 0} directives
            </div>
          )}
        </div>
      )}
    </MtCard>
  );
}

// ── Documents browser ─────────────────────────────────────────────────────────

function MemDocuments({ bank }) {
  const useBankDocuments = window.__hal0UseBankDocuments;
  const useDocumentDelete = window.__hal0UseDocumentDelete;
  const useDocumentReprocess = window.__hal0UseDocumentReprocess;
  const docsQuery = useBankDocuments ? useBankDocuments(bank, { limit: 25 }) : { data: null };
  const del = useDocumentDelete ? useDocumentDelete() : null;
  const reprocess = useDocumentReprocess ? useDocumentReprocess() : null;
  const [confirmId, setConfirmId] = useStateMTl(null);
  const items = docsQuery.data?.items || [];

  async function doDelete(id) {
    try {
      await del.mutateAsync({ bank, id });
      mtToast(`Document ${id} deleted`, 'ok');
    } catch (err) {
      mtToast(err?.message || 'Delete failed', 'err');
    } finally {
      setConfirmId(null);
    }
  }

  async function doReprocess(id) {
    try {
      await reprocess.mutateAsync({ bank, id });
      mtToast('Reprocess queued', 'ok');
    } catch (err) {
      mtToast(err?.message || 'Reprocess failed', 'err');
    }
  }

  return (
    <MtCard title={`documents · ${docsQuery.data?.total ?? '—'}`} testid="mem-documents">
      {items.length === 0 ? (
        <div className="empty mono">No documents in this bank.</div>
      ) : (
        items.map(d => (
          <div className="mt-doc" key={d.id} data-testid={`mem-doc-${d.id}`}>
            <span className="mt-doc-text">{(d.original_text || d.id).slice(0, 90)}</span>
            <span className="mt-doc-meta mono">
              {d.memory_unit_count ?? 0} facts{d.tags?.length ? ` · ${d.tags.join(', ')}` : ''}
            </span>
            <span className="mt-doc-actions">
              <button className="btn ghost xs" onClick={() => doReprocess(d.id)} data-testid="mem-doc-reprocess" title="Reprocess">
                <Icon name="refresh" size={12} />
              </button>
              {confirmId === d.id ? (
                <button className="btn danger xs" onClick={() => doDelete(d.id)} data-testid="mem-doc-delete-confirm">
                  Confirm
                </button>
              ) : (
                <button className="btn ghost xs danger" onClick={() => setConfirmId(d.id)} data-testid="mem-doc-delete" title="Delete">
                  <Icon name="trash" size={12} />
                </button>
              )}
            </span>
          </div>
        ))
      )}
    </MtCard>
  );
}

// ── Mental models ─────────────────────────────────────────────────────────────

function MemMentalModels({ bank }) {
  const useMentalModels = window.__hal0UseMentalModels;
  const useMentalModelRefresh = window.__hal0UseMentalModelRefresh;
  const query = useMentalModels ? useMentalModels(bank) : { data: null };
  const refresh = useMentalModelRefresh ? useMentalModelRefresh() : null;
  const items = query.data?.items || [];

  async function doRefresh(id) {
    try {
      await refresh.mutateAsync({ bank, id });
      mtToast('Mental model refresh queued', 'ok');
    } catch (err) {
      mtToast(err?.message || 'Refresh failed', 'err');
    }
  }

  return (
    <MtCard title="mental models" testid="mem-mental-models">
      {items.length === 0 ? (
        <div className="empty mono">No mental models defined.</div>
      ) : (
        items.map(m => (
          <div className="mt-mm" key={m.id} data-testid={`mem-mm-${m.id}`}>
            <div className="mt-mm-main">
              <span className="mono mt-mm-name">{m.name}</span>
              {m.is_stale && <span className="mo-badge warn mono">stale</span>}
              <button className="btn ghost xs" onClick={() => doRefresh(m.id)} data-testid="mem-mm-refresh" title="Refresh">
                <Icon name="refresh" size={12} />
              </button>
            </div>
            <div className="mt-mm-q mono">{m.source_query}</div>
            {m.content && <div className="mt-mm-content">{m.content.slice(0, 200)}</div>}
          </div>
        ))
      )}
    </MtCard>
  );
}

// ── Directives ────────────────────────────────────────────────────────────────

function MemDirectives({ bank }) {
  const useDirectives = window.__hal0UseDirectives;
  const useDirectiveCreate = window.__hal0UseDirectiveCreate;
  const useDirectiveUpdate = window.__hal0UseDirectiveUpdate;
  const useDirectiveDelete = window.__hal0UseDirectiveDelete;
  const query = useDirectives ? useDirectives(bank) : { data: null };
  const create = useDirectiveCreate ? useDirectiveCreate() : null;
  const update = useDirectiveUpdate ? useDirectiveUpdate() : null;
  const del = useDirectiveDelete ? useDirectiveDelete() : null;

  const [creating, setCreating] = useStateMTl(false);
  const [name, setName] = useStateMTl('');
  const [content, setContent] = useStateMTl('');
  const items = query.data?.items || [];

  async function submit(e) {
    e.preventDefault();
    if (!name.trim() || !content.trim()) return;
    try {
      await create.mutateAsync({ bank, body: { name: name.trim(), content: content.trim() } });
      mtToast(`Directive ${name} created`, 'ok');
      setCreating(false);
      setName('');
      setContent('');
    } catch (err) {
      mtToast(err?.message || 'Create failed', 'err');
    }
  }

  async function toggleActive(d) {
    try {
      await update.mutateAsync({ bank, id: d.id, body: { is_active: !d.is_active } });
    } catch (err) {
      mtToast(err?.message || 'Update failed', 'err');
    }
  }

  async function doDelete(id) {
    try {
      await del.mutateAsync({ bank, id });
      mtToast('Directive deleted', 'ok');
    } catch (err) {
      mtToast(err?.message || 'Delete failed', 'err');
    }
  }

  const newBtn = (
    <button className="btn ghost xs" onClick={() => setCreating(c => !c)} data-testid="mem-dir-new">
      + New
    </button>
  );

  return (
    <MtCard title="directives" action={newBtn} wide testid="mem-directives">
      {creating && (
        <form className="mt-row" onSubmit={submit} style={{ marginBottom: 8 }}>
          <input
            className="input mono"
            value={name}
            onChange={e => setName(e.target.value)}
            placeholder="name"
            data-testid="mem-dir-name"
          />
          <input
            className="input mono"
            value={content}
            onChange={e => setContent(e.target.value)}
            placeholder="always-injected rule…"
            data-testid="mem-dir-content"
          />
          <button type="submit" className="btn sm" data-testid="mem-dir-submit">Create</button>
        </form>
      )}
      {items.length === 0 ? (
        <div className="empty mono">No directives.</div>
      ) : (
        items.map(d => (
          <div className="mt-dir" key={d.id} data-testid={`mem-dir-${d.id}`}>
            <label className="mt-check mono" title="active">
              <input type="checkbox" checked={!!d.is_active} onChange={() => toggleActive(d)} />
              {d.name}
            </label>
            <span className="mt-dir-content">{d.content}</span>
            <button className="btn ghost xs danger" onClick={() => doDelete(d.id)} data-testid="mem-dir-delete" title="Delete">
              <Icon name="trash" size={12} />
            </button>
          </div>
        ))
      )}
    </MtCard>
  );
}

// ── Tools panel ───────────────────────────────────────────────────────────────

function MemToolsPanel() {
  const useMemoryBanks = window.__hal0UseMemoryBanks;
  const banksQuery = useMemoryBanks ? useMemoryBanks() : { data: null };
  const banks = banksQuery.data?.banks || [];

  // bank selection persisted + shared with Overview/Graph via localStorage.
  const [bankSel, setBankSel] = useStateMTl(() => {
    try { return localStorage.getItem('hal0.mem.bank') || null; } catch { return null; }
  });
  const bankValid = bankSel && banks.some(b => b.bank_id === bankSel);
  const bank = (bankValid ? bankSel : banks[0]?.bank_id) || null;

  function chooseBank(id) {
    setBankSel(id);
    try { localStorage.setItem('hal0.mem.bank', id); } catch { /* ignore */ }
  }

  return (
    <div className="mt" data-testid="mem-tools">
      <div className="mt-bankbar mono">
        <span style={{ color: 'var(--fg-4)' }}>bank</span>
        <select
          className="input mono"
          value={bank || ''}
          onChange={e => chooseBank(e.target.value)}
          data-testid="mem-tools-bank"
          aria-label="Bank"
        >
          {banks.map(b => (
            <option key={b.bank_id} value={b.bank_id}>{b.bank_id}</option>
          ))}
        </select>
        <span className="mt-hint">recall &amp; reflect run against the live Hindsight bank</span>
      </div>
      <div className="mt-grid">
        <MemRecallConsole bank={bank} />
        <MemReflectPlayground bank={bank} />
        <MemDocuments bank={bank} />
        <MemMentalModels bank={bank} />
        <MemDirectives bank={bank} />
      </div>
    </div>
  );
}

Object.assign(window, { MemToolsPanel });
