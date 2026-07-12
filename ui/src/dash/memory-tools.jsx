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
    <MtCard title="Recall" testid="mem-recall">
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
    <MtCard title="Reflect" testid="mem-reflect">
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

// Hindsight documents ship no title/filename — just an id + metadata/tags — so
// compose the most identifiable label we can and keep a short id for
// disambiguation, rather than showing a raw UUID.
function mtDocTitle(d) {
  const meta = d.document_metadata || {};
  const rp = d.retain_params || {};
  const rpMeta = rp.metadata || {};
  const firstLine = String(d.original_text || d.text || d.summary || '').split('\n')[0].trim();
  const named = meta.title || meta.filename || meta.name || meta.path || meta.uri || meta.url || d.title;
  const source = meta.source || rpMeta.source || rp.context;
  const label = named || firstLine || (source ? `${source} memory` : '') || 'document';
  return label.length > 80 ? label.slice(0, 80) + '…' : label;
}
function mtDocWhen(iso) {
  if (!iso) return '';
  try {
    return new Date(iso).toLocaleString(undefined, { month: 'short', day: 'numeric', hour: '2-digit', minute: '2-digit' });
  } catch { return ''; }
}

const MT_DOCS_PAGE = 10;

function MemDocuments({ bank }) {
  const useBankDocuments = window.__hal0UseBankDocuments;
  const useDocumentDelete = window.__hal0UseDocumentDelete;
  const useDocumentReprocess = window.__hal0UseDocumentReprocess;
  const [page, setPage] = useStateMTl(0);
  // Reset to the first page whenever the bank changes.
  React.useEffect(() => { setPage(0); }, [bank]);
  const docsQuery = useBankDocuments
    ? useBankDocuments(bank, { limit: MT_DOCS_PAGE, offset: page * MT_DOCS_PAGE })
    : { data: null };
  const del = useDocumentDelete ? useDocumentDelete() : null;
  const reprocess = useDocumentReprocess ? useDocumentReprocess() : null;
  const [confirmId, setConfirmId] = useStateMTl(null);
  const items = docsQuery.data?.items || [];
  const total = docsQuery.data?.total ?? 0;
  const pageCount = Math.max(1, Math.ceil(total / MT_DOCS_PAGE));
  // Clamp if a delete shrank the list past the current page.
  React.useEffect(() => {
    if (total && page > 0 && page >= pageCount) setPage(pageCount - 1);
  }, [total, page, pageCount]);

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
    <MtCard title={`documents · ${total || '—'}`} testid="mem-documents">
      {items.length === 0 ? (
        <div className="empty mono">No documents in this bank.</div>
      ) : (
        <>
          {items.map(d => (
            <div className="mt-doc" key={d.id} data-testid={`mem-doc-${d.id}`}>
              <span className="mt-doc-text" title={d.id}>
                {mtDocTitle(d)}
                <span className="mt-doc-id mono">#{String(d.id).slice(0, 8)}</span>
              </span>
              <span className="mt-doc-meta mono">
                {d.memory_unit_count ?? 0} facts
                {d.text_length != null ? ` · ${d.text_length} chars` : ''}
                {mtDocWhen(d.created_at) ? ` · ${mtDocWhen(d.created_at)}` : ''}
                {d.tags?.length ? ` · ${d.tags.join(', ')}` : ''}
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
          ))}
          {pageCount > 1 && (
            <div className="mt-pager mono">
              <button className="btn ghost xs" disabled={page <= 0} onClick={() => setPage(p => Math.max(0, p - 1))} data-testid="mem-docs-prev">
                Prev
              </button>
              <span className="mt-pager-lbl">page {page + 1} / {pageCount}</span>
              <button className="btn ghost xs" disabled={page >= pageCount - 1} onClick={() => setPage(p => p + 1)} data-testid="mem-docs-next">
                Next
              </button>
            </div>
          )}
        </>
      )}
    </MtCard>
  );
}

// ── Mental models ─────────────────────────────────────────────────────────────

function MemMentalModels({ bank }) {
  const useMentalModels = window.__hal0UseMentalModels;
  const useMentalModelRefresh = window.__hal0UseMentalModelRefresh;
  const useMentalModelCreate = window.__hal0UseMentalModelCreate;
  const useMentalModelDelete = window.__hal0UseMentalModelDelete;
  const query = useMentalModels ? useMentalModels(bank) : { data: null };
  const refresh = useMentalModelRefresh ? useMentalModelRefresh() : null;
  const create = useMentalModelCreate ? useMentalModelCreate() : null;
  const del = useMentalModelDelete ? useMentalModelDelete() : null;
  const items = query.data?.items || [];
  const [creating, setCreating] = useStateMTl(false);
  const [name, setName] = useStateMTl('');
  const [sourceQuery, setSourceQuery] = useStateMTl('');
  const [busy, setBusy] = useStateMTl(false);
  const [confirmId, setConfirmId] = useStateMTl(null);

  async function doRefresh(id) {
    try {
      await refresh.mutateAsync({ bank, id });
      mtToast('Mental model refresh queued', 'ok');
    } catch (err) {
      mtToast(err?.message || 'Refresh failed', 'err');
    }
  }

  async function doDelete(id) {
    if (!del) return;
    try {
      await del.mutateAsync({ bank, id });
      mtToast('Mental model deleted', 'ok');
    } catch (err) {
      mtToast(err?.message || 'Delete failed', 'err');
    } finally {
      setConfirmId(null);
    }
  }

  async function doCreate(e) {
    e.preventDefault();
    const nm = name.trim();
    const sq = sourceQuery.trim();
    if (!nm || !sq || !create) return;
    setBusy(true);
    try {
      await create.mutateAsync({ bank, body: { name: nm, source_query: sq } });
      mtToast(`Mental model "${nm}" created`, 'ok');
      setName('');
      setSourceQuery('');
      setCreating(false);
    } catch (err) {
      mtToast(err?.message || 'Create failed', 'err');
    } finally {
      setBusy(false);
    }
  }

  const newBtn = (
    <button
      className="btn ghost xs"
      onClick={() => setCreating(v => !v)}
      disabled={!bank}
      data-testid="mem-mm-new"
    >
      {creating ? 'Cancel' : '+ New'}
    </button>
  );

  return (
    <MtCard title="mental models" action={newBtn} testid="mem-mental-models">
      <p className="mt-mm-about">
        A mental model is a saved question the bank keeps answered — Hindsight synthesizes an
        answer from your memories and re-runs it on demand (refresh) as new facts land.
      </p>
      {creating && (
        <form className="mt-mm-form" onSubmit={doCreate} data-testid="mem-mm-form">
          <input
            className="input mono"
            value={name}
            onChange={e => setName(e.target.value)}
            placeholder="name — e.g. deploy-prefs"
            maxLength={80}
            data-testid="mem-mm-name-input"
          />
          <textarea
            className="input mono"
            value={sourceQuery}
            onChange={e => setSourceQuery(e.target.value)}
            placeholder="source query — the question this model should keep answered"
            rows={2}
            data-testid="mem-mm-query-input"
          />
          <div className="mt-mm-form-foot">
            <button
              type="submit"
              className="btn sm"
              disabled={busy || !name.trim() || !sourceQuery.trim()}
              data-testid="mem-mm-create"
            >
              {busy ? 'Creating…' : 'Create'}
            </button>
          </div>
        </form>
      )}
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
              {confirmId === m.id ? (
                <button className="btn danger xs" onClick={() => doDelete(m.id)} data-testid="mem-mm-delete-confirm" title="Confirm delete">
                  Delete?
                </button>
              ) : (
                <button className="btn ghost xs danger" onClick={() => setConfirmId(m.id)} data-testid="mem-mm-delete" title="Delete">
                  <Icon name="trash" size={12} />
                </button>
              )}
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
  const [busy, setBusy] = useStateMTl(false);
  const items = query.data?.items || [];

  async function submit(e) {
    e.preventDefault();
    if (!name.trim() || !content.trim() || !create) return;
    setBusy(true);
    try {
      await create.mutateAsync({ bank, body: { name: name.trim(), content: content.trim() } });
      mtToast(`Directive "${name.trim()}" created`, 'ok');
      setCreating(false);
      setName('');
      setContent('');
    } catch (err) {
      mtToast(err?.message || 'Create failed', 'err');
    } finally {
      setBusy(false);
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
    <button
      className="btn ghost xs"
      onClick={() => setCreating(c => !c)}
      disabled={!bank}
      data-testid="mem-dir-new"
    >
      {creating ? 'Cancel' : '+ New'}
    </button>
  );

  return (
    <MtCard title="directives" action={newBtn} testid="mem-directives">
      <p className="mt-mm-about">
        A directive is a standing rule you author for the bank — injected into an agent's context
        whenever it uses this bank, always on until you toggle it off.
      </p>
      {creating && (
        <form className="mt-mm-form" onSubmit={submit} data-testid="mem-dir-form">
          <input
            className="input mono"
            value={name}
            onChange={e => setName(e.target.value)}
            placeholder="name — e.g. tone"
            maxLength={80}
            data-testid="mem-dir-name"
          />
          <textarea
            className="input mono"
            value={content}
            onChange={e => setContent(e.target.value)}
            placeholder="the rule to always inject — e.g. Prefer podman over docker in examples."
            rows={2}
            data-testid="mem-dir-content"
          />
          <div className="mt-mm-form-foot">
            <button
              type="submit"
              className="btn sm"
              disabled={busy || !name.trim() || !content.trim()}
              data-testid="mem-dir-submit"
            >
              {busy ? 'Creating…' : 'Create'}
            </button>
          </div>
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

// `embedded` renders just the tool grid against a caller-supplied bank (used
// inside the Overview's primary bank display); standalone mode keeps its own
// bank picker bar for the #memory/tools route.
function MemToolsPanel({ bank: bankProp, embedded } = {}) {
  const useMemoryBanks = window.__hal0UseMemoryBanks;
  const banksQuery = useMemoryBanks ? useMemoryBanks() : { data: null };
  const banks = banksQuery.data?.banks || [];

  // bank selection persisted + shared with Overview/Graph via localStorage.
  const [bankSel, setBankSel] = useStateMTl(() => {
    try { return localStorage.getItem('hal0.mem.bank') || null; } catch { return null; }
  });
  const bankValid = bankSel && banks.some(b => b.bank_id === bankSel);
  const bank = embedded && bankProp ? bankProp : ((bankValid ? bankSel : banks[0]?.bank_id) || null);

  function chooseBank(id) {
    setBankSel(id);
    try { localStorage.setItem('hal0.mem.bank', id); } catch { /* ignore */ }
  }

  return (
    <div className={'mt' + (embedded ? ' mt-embedded' : '')} data-testid="mem-tools">
      {!embedded && (
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
      )}
      <div className="mt-cols">
        <div className="mt-col">
          <MemReflectPlayground bank={bank} />
          <MemRecallConsole bank={bank} />
        </div>
        <div className="mt-col">
          <MemDirectives bank={bank} />
          <MemMentalModels bank={bank} />
        </div>
      </div>
      <MemDocuments bank={bank} />
    </div>
  );
}

Object.assign(window, { MemToolsPanel });
