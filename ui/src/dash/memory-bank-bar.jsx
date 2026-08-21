// hal0 memory v2 (Bank workspace UI, task C3) — BankBar (identity + reflect +
// rules) and the Add modal.
//
// Ported from the design handoff's prototype/bank-card.jsx (BankBar,
// AddModal), wired to real hooks (window.__hal0Use*, installed by
// memory-hook-bridge.ts) instead of the prototype's static mock arrays and
// setTimeout-simulated reflect.
//
// Window-globals contract: no ES imports across dash/*.jsx — reads
// window.MemV2 (task C1) and window.TypeBar/window.Spark (published by
// memory-overview-v2.jsx, task C2 — reused here rather than duplicated)
// at render time; publishes window.MemV2BankBar / window.MemV2AddModal the
// same way.
//
// Expert constraint (binding): observations are NOT curatable and are not
// a valid input fact_type — the Add-fact type picker only offers
// world/experience (the prototype's picker also offered "observation";
// that option is dropped here).

const { useState: useStateBankBar } = React

function memToast(msg, kind = 'info') {
  if (typeof window !== 'undefined' && window.__hal0Toast) window.__hal0Toast(msg, kind)
}

// fact_type ∈ {world, experience} only (expert constraint — observations
// are derived/detected, never operator-authored).
const ADD_FACT_TYPES = ['world', 'experience']

function AddModal({ bank, tab0 = 'fact', onClose }) {
  const { FACT_COLORS, Icon } = window.MemV2
  const [tab, setTab] = useStateBankBar(tab0)
  const TABS = [
    ['fact', 'Fact'],
    ['document', 'Document'],
    ['directive', 'Directive'],
    ['model', 'Mental model'],
  ]

  // fact tab
  const [ftype, setFtype] = useStateBankBar('world')
  const [factText, setFactText] = useStateBankBar('')
  const [factTags, setFactTags] = useStateBankBar('')
  const useMemoryAdd = window.__hal0UseMemoryAdd
  const memoryAdd = useMemoryAdd ? useMemoryAdd() : { mutate: () => {}, isPending: false }

  // directive tab
  const [dirText, setDirText] = useStateBankBar('')
  const [dirActive, setDirActive] = useStateBankBar(true)
  const useDirectiveCreate = window.__hal0UseDirectiveCreate
  const directiveCreate = useDirectiveCreate ? useDirectiveCreate() : { mutate: () => {}, isPending: false }

  // mental model tab
  const [modelName, setModelName] = useStateBankBar('')
  const [modelQuestion, setModelQuestion] = useStateBankBar('')
  const [refresh, setRefresh] = useStateBankBar('on consolidation')
  const useMentalModelCreate = window.__hal0UseMentalModelCreate
  const mentalModelCreate = useMentalModelCreate
    ? useMentalModelCreate()
    : { mutate: () => {}, isPending: false }

  // document tab — list + reprocess only; there is no ingest endpoint yet
  // (POST /api/memory/add has no file/URL upload path, and no
  // .../documents ingest route exists) — recorded as a concern in the C3
  // report rather than inventing one. The list still uses the real hooks.
  const useBankDocuments = window.__hal0UseBankDocuments
  const useDocumentReprocess = window.__hal0UseDocumentReprocess
  const docsQuery = useBankDocuments ? useBankDocuments(bank) : { data: null }
  const reprocess = useDocumentReprocess ? useDocumentReprocess() : { mutate: () => {}, isPending: false }

  const submitFact = () => {
    const text = factText.trim()
    if (!text) return
    const tags = factTags
      .split(',')
      .map((t) => t.trim())
      .filter(Boolean)
    memoryAdd.mutate(
      // dataset (NOT a top-level fact_type — POST /api/memory/add doesn't
      // accept one; the type picker's value rides in `metadata` instead,
      // and `source` is never sent — the server rejects/derives it from
      // the X-hal0-Agent header).
      { text, dataset: bank, tags, metadata: { fact_type: ftype } },
      {
        onSuccess: () => {
          memToast('Fact added', 'ok')
          onClose()
        },
        onError: (err) => memToast(`Add failed: ${err.message}`, 'err'),
      },
    )
  }

  const submitDirective = () => {
    const content = dirText.trim()
    if (!content) return
    directiveCreate.mutate(
      { bank, body: { name: content.slice(0, 40), content, is_active: dirActive, tags: [] } },
      {
        onSuccess: () => {
          memToast('Directive added', 'ok')
          onClose()
        },
        onError: (err) => memToast(`Add failed: ${err.message}`, 'err'),
      },
    )
  }

  const submitModel = () => {
    const name = modelName.trim()
    const q = modelQuestion.trim()
    if (!name || !q) return
    mentalModelCreate.mutate(
      { bank, body: { name, source_query: q } },
      {
        onSuccess: () => {
          memToast('Mental model created', 'ok')
          onClose()
        },
        onError: (err) => memToast(`Create failed: ${err.message}`, 'err'),
      },
    )
  }

  return (
    <>
      <div className="mv-scrim" onClick={onClose} />
      <div className="mv-modal" data-testid="mv-add-modal">
        <div className="mh">
          <span className="mv-eyebrow">{bank} · add</span>
          <span style={{ flex: 1 }} />
          <button className="mvi-x" onClick={onClose}>
            <Icon name="close" size={12} />
          </button>
        </div>
        <div className="mtabs">
          {TABS.map(([id, l]) => (
            <button
              key={id}
              className={'btab' + (tab === id ? ' on' : '')}
              data-testid={id === 'document' ? 'mv-add-doc-tab' : undefined}
              onClick={() => setTab(id)}
            >
              {l}
            </button>
          ))}
        </div>
        {tab === 'fact' && (
          <>
            <div className="mb">
              <div>
                <span className="mv-cell-k">fact text</span>
                <textarea
                  className="mv-input"
                  data-testid="mv-add-fact-text"
                  style={{ minHeight: 64, fontFamily: 'var(--geist,sans-serif)', resize: 'vertical' }}
                  placeholder="the thing to remember…"
                  value={factText}
                  onChange={(e) => setFactText(e.target.value)}
                />
              </div>
              <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr', gap: 12 }}>
                <div>
                  <span className="mv-cell-k">type</span>
                  <div style={{ display: 'flex', gap: 5 }}>
                    {ADD_FACT_TYPES.map((t) => (
                      <button
                        key={t}
                        className={'mv-tf ' + (ftype === t ? 'on' : '')}
                        onClick={() => setFtype(t)}
                      >
                        <span className="dot" style={{ background: FACT_COLORS[t] }} />
                        {t}
                      </button>
                    ))}
                  </div>
                </div>
                <div>
                  <span className="mv-cell-k">tags</span>
                  <input
                    className="mv-input"
                    data-testid="mv-add-fact-tags"
                    style={{ padding: '5px 9px' }}
                    placeholder="comma-separated · e.g. ci, thermal"
                    value={factTags}
                    onChange={(e) => setFactTags(e.target.value)}
                  />
                </div>
              </div>
            </div>
            <div className="mf">
              <span className="mini">extraction runs on utility · ~2s</span>
              <span style={{ flex: 1 }} />
              <button className="mv-btn" onClick={onClose}>
                Cancel
              </button>
              <button
                className="mv-btn primary"
                data-testid="mv-add-submit"
                onClick={submitFact}
                disabled={memoryAdd.isPending || !factText.trim()}
              >
                {memoryAdd.isPending ? 'Adding…' : 'Add fact'}
              </button>
            </div>
          </>
        )}
        {tab === 'document' && (
          <>
            <div className="mb">
              <div
                className="drop"
                title="Document ingest isn't wired to a backend endpoint yet — no upload/URL route exists. Reprocess an existing document below, or ingest via another existing flow."
              >
                drop a file — or paste a path / URL
              </div>
              <div className="mv-doclist">
                <span className="mv-cell-k">existing documents</span>
                {(docsQuery.data?.items || []).map((doc) => (
                  <div key={doc.id} className="mv-docrow">
                    <span className="nm">{doc.id}</span>
                    <span className="facts">{doc.memory_unit_count ?? 0} facts</span>
                    <button
                      className="mv-btn"
                      onClick={() => reprocess.mutate({ bank, id: doc.id })}
                      disabled={reprocess.isPending}
                    >
                      Reprocess
                    </button>
                  </div>
                ))}
              </div>
            </div>
            <div className="mf">
              <span className="mini">no ingest endpoint yet · reprocess only</span>
              <span style={{ flex: 1 }} />
              <button className="mv-btn" onClick={onClose}>
                Cancel
              </button>
              <button
                className="mv-btn primary"
                disabled
                title="Document ingest isn't wired to a backend endpoint yet"
              >
                Ingest
              </button>
            </div>
          </>
        )}
        {tab === 'directive' && (
          <>
            <div className="mb">
              <div>
                <span className="mv-cell-k">rule</span>
                <textarea
                  className="mv-input"
                  style={{ minHeight: 64, fontFamily: 'var(--geist,sans-serif)', resize: 'vertical' }}
                  placeholder="a standing instruction, injected into the agent's context every turn…"
                  value={dirText}
                  onChange={(e) => setDirText(e.target.value)}
                />
              </div>
              <div style={{ display: 'flex', alignItems: 'center', gap: 10 }}>
                <button
                  className={'mv-toggle' + (dirActive ? ' on' : '')}
                  onClick={() => setDirActive(!dirActive)}
                >
                  <i />
                </button>
                <span className="mini">active immediately</span>
              </div>
              <p className="mhelp">
                Directives shape behaviour, not memory — they're injected verbatim whenever an agent
                uses this bank. Keep them short and imperative.
              </p>
            </div>
            <div className="mf">
              <span style={{ flex: 1 }} />
              <button className="mv-btn" onClick={onClose}>
                Cancel
              </button>
              <button
                className="mv-btn primary"
                onClick={submitDirective}
                disabled={directiveCreate.isPending || !dirText.trim()}
              >
                {directiveCreate.isPending ? 'Adding…' : 'Add directive'}
              </button>
            </div>
          </>
        )}
        {tab === 'model' && (
          <>
            <div className="mb">
              <div>
                <span className="mv-cell-k">name</span>
                <input
                  className="mv-input"
                  placeholder="short handle, e.g. operator preferences"
                  value={modelName}
                  onChange={(e) => setModelName(e.target.value)}
                />
              </div>
              <div>
                <span className="mv-cell-k">standing question</span>
                <textarea
                  className="mv-input"
                  style={{ minHeight: 56, fontFamily: 'var(--geist,sans-serif)', resize: 'vertical' }}
                  placeholder="what should this bank keep an answer ready for?"
                  value={modelQuestion}
                  onChange={(e) => setModelQuestion(e.target.value)}
                />
              </div>
              <div>
                <span className="mv-cell-k">refresh</span>
                <div style={{ display: 'flex', gap: 5 }}>
                  {['on consolidation', 'daily', 'manual'].map((r) => (
                    <button
                      key={r}
                      className={'mv-tf ' + (refresh === r ? 'on' : '')}
                      onClick={() => setRefresh(r)}
                    >
                      {r}
                    </button>
                  ))}
                </div>
              </div>
              <p className="mhelp">
                The model's answer is rebuilt from memory on this schedule; agents read the cached
                answer instead of re-recalling.
                {/* refresh schedule is UI-only for now — useMentalModelCreate's body only
                    accepts {name, source_query}; there's no backend field for it yet. */}
              </p>
            </div>
            <div className="mf">
              <span className="mini">first answer builds now · async op</span>
              <span style={{ flex: 1 }} />
              <button className="mv-btn" onClick={onClose}>
                Cancel
              </button>
              <button
                className="mv-btn primary"
                onClick={submitModel}
                disabled={mentalModelCreate.isPending || !modelName.trim() || !modelQuestion.trim()}
              >
                {mentalModelCreate.isPending ? 'Creating…' : 'Create model'}
              </button>
            </div>
          </>
        )}
      </div>
    </>
  )
}

function BankBar({ bank, setBank }) {
  const { FACT_COLORS, fmtN, Icon, MvError } = window.MemV2
  const Spark = window.Spark

  const useMemoryBanks = window.__hal0UseMemoryBanks
  const useBankStats = window.__hal0UseBankStats
  const useBankTimeseries = window.__hal0UseBankTimeseries
  const useBankOperations = window.__hal0UseBankOperations
  const summarizeBankOperations = window.__hal0MemSummarizeOps
  const useReflect = window.__hal0UseReflect
  const useDirectives = window.__hal0UseDirectives
  const useDirectiveUpdate = window.__hal0UseDirectiveUpdate
  const useDirectiveDelete = window.__hal0UseDirectiveDelete
  const useMentalModels = window.__hal0UseMentalModels
  const useMentalModelRefresh = window.__hal0UseMentalModelRefresh

  const banksQuery = useMemoryBanks ? useMemoryBanks() : { data: null }
  const banks = banksQuery.data?.banks || []
  const b = banks.find((x) => x.bank_id === bank) || banks[0]

  const statsQuery = useBankStats ? useBankStats(bank) : { data: null }
  const stats = statsQuery.data
  const world = stats?.nodes_by_fact_type?.world ?? 0
  const experience = stats?.nodes_by_fact_type?.experience ?? 0
  const observation = stats?.nodes_by_fact_type?.observation ?? 0
  const facts = stats?.total_nodes ?? 0
  const links = stats?.total_links ?? 0

  const tsQuery = useBankTimeseries ? useBankTimeseries(bank, '30d') : { data: null }
  const opsQuery = useBankOperations ? useBankOperations(bank) : { data: null }
  const activity = summarizeBankOperations ? summarizeBankOperations(opsQuery.data) : null
  const working = activity?.processing ?? 0
  const pending = activity?.pending ?? 0

  const [tab, setTab] = useStateBankBar('reflect')
  const [modal, setModal] = useStateBankBar(null) // tab0 or null
  const [q, setQ] = useStateBankBar('')

  const reflect = useReflect ? useReflect() : { mutate: () => {}, isPending: false, data: null, reset: () => {} }
  const ask = (text) => {
    const question = (text ?? q).trim()
    if (!question) return
    setQ(question)
    // Verbatim Hindsight passthrough — the body field is `text`, not
    // `query` (memory_admin.py's passthrough allowlist requires `text`
    // for this route).
    reflect.mutate({ bank, body: { text: question } })
  }
  const ans = reflect.data

  const directivesQuery = useDirectives ? useDirectives(bank) : { data: null }
  const directives = directivesQuery.data?.items || []
  const directiveUpdate = useDirectiveUpdate ? useDirectiveUpdate() : { mutate: () => {} }
  const directiveDelete = useDirectiveDelete ? useDirectiveDelete() : { mutate: () => {} }
  const onCount = directives.filter((d) => d.is_active).length

  const mentalModelsQuery = useMentalModels ? useMentalModels(bank) : { data: null }
  const mentalModels = mentalModelsQuery.data?.items || []
  const mentalModelRefresh = useMentalModelRefresh ? useMentalModelRefresh() : { mutate: () => {} }
  const staleCount = mentalModels.filter((m) => m.is_stale).length

  if (!b) return null

  return (
    <div className="mv-card mv-bankbar" data-testid="mv-bankbar">
      <div className="row">
        <select
          className="mono pick"
          data-testid="mv-bank-select"
          value={bank}
          onChange={(e) => setBank(e.target.value)}
        >
          {banks.map((x) => (
            <option key={x.bank_id} value={x.bank_id}>
              {x.name || x.bank_id}
            </option>
          ))}
        </select>
        <div className="pers" title={b.mission || 'no description set'}>
          {b.mission || <span style={{ color: 'var(--fg-5)' }}>no description set</span>}
        </div>
        <div className="cell dist">
          <span className="mv-cell-k">composition</span>
          {window.TypeBar && <window.TypeBar b={{ world, experience, observation }} />}
          <div className="mv-legend num" style={{ marginTop: 5 }}>
            <span>
              <span className="sw" style={{ background: FACT_COLORS.world }} />
              {fmtN(world)}
            </span>
            <span>
              <span className="sw" style={{ background: FACT_COLORS.experience }} />
              {fmtN(experience)}
            </span>
            <span>
              <span className="sw" style={{ background: FACT_COLORS.observation }} />
              {fmtN(observation)}
            </span>
          </div>
        </div>
        <div className="cell">
          <span className="mv-cell-k">facts</span>
          <span className="mv-cell-v num">{fmtN(facts)}</span>
        </div>
        <div className="cell">
          <span className="mv-cell-k">links</span>
          <span className="mv-cell-v num">{fmtN(links)}</span>
        </div>
        <div className="cell">
          <span className="mv-cell-k">activity 30d</span>
          {Spark && <Spark series={tsQuery.data?.buckets} />}
        </div>
        <div className="ops">
          {working > 0 && <span className="mv-chip on num">⟳ {working} working</span>}
          {pending > 0 && <span className="mv-chip num">{pending} queued</span>}
          {working === 0 && pending === 0 && (
            <span className="mv-chip num" style={{ opacity: 0.6 }}>
              idle
            </span>
          )}
        </div>
        <div style={{ display: 'flex', gap: 6, flex: 'none' }}>
          <button className="mv-btn primary" data-testid="mv-add-open" onClick={() => setModal('fact')}>
            + Add
          </button>
          <button className="mv-btn" title="consolidate · export · wipe">
            <Icon name="more" size={13} />
          </button>
        </div>
      </div>
      <div className="btabs">
        <button
          className={'btab' + (tab === 'reflect' ? ' on' : '')}
          data-testid="mv-reflect-tab"
          onClick={() => setTab('reflect')}
        >
          Reflect
        </button>
        <button
          className={'btab' + (tab === 'rules' ? ' on' : '')}
          data-testid="mv-rules-tab"
          onClick={() => setTab('rules')}
        >
          Rules <span className="n num">{onCount} · {mentalModels.length}</span>
        </button>
      </div>
      {tab === 'reflect' && (
        <div>
          <div className="fline">
            <div className="mv-search">
              <Icon name="search" size={13} />
              <input
                data-testid="mv-reflect-q"
                placeholder="reason over this bank…"
                value={q}
                onChange={(e) => setQ(e.target.value)}
                onKeyDown={(e) => e.key === 'Enter' && ask()}
              />
            </div>
            {!ans && !reflect.isPending && (
              <>
                <span className="mv-tf" onClick={() => ask('why is extraction lagging?')}>
                  why is extraction lagging?
                </span>
                <span className="mv-tf" onClick={() => ask('what broke this week?')}>
                  what broke this week?
                </span>
              </>
            )}
            {ans && (
              <button
                className="mv-btn"
                onClick={() => {
                  reflect.reset()
                  setQ('')
                }}
              >
                clear
              </button>
            )}
            <button
              className="mv-btn primary"
              data-testid="mv-reflect-run"
              onClick={() => ask()}
              disabled={reflect.isPending}
            >
              {reflect.isPending ? '…' : 'Ask'}
            </button>
          </div>
          {reflect.isPending && (
            <div className="mv-empty" style={{ padding: 18 }}>
              recalling · reranking · composing…
            </div>
          )}
          {/* task C8: reflect is a mutation, not a query, so it has no
              `refetch` — the retry re-asks the same question via `ask()`
              instead of MvError's default query.refetch() call. */}
          {!reflect.isPending && reflect.isError && (
            <div className="empty mono" data-testid="mv-reflect-error" style={{ padding: 18 }}>
              <div>
                Memory engine unreachable —{' '}
                {reflect.error?.message || 'could not reflect over this bank'}
              </div>
              <button
                className="mv-btn"
                style={{ marginTop: 8 }}
                data-testid="mv-reflect-error-retry"
                onClick={() => ask(q)}
              >
                Retry
              </button>
            </div>
          )}
          {ans && (
            <div
              className="pad"
              data-testid="mv-reflect-out"
              style={{ borderTop: '1px solid var(--line-soft,#1C1C1C)', padding: '12px 14px' }}
            >
              <p
                style={{
                  font: '400 13px var(--geist,sans-serif)',
                  color: 'var(--fg-1,var(--fg))',
                  lineHeight: 1.55,
                  margin: '0 0 9px',
                }}
              >
                {ans.text}
              </p>
              {/* The real/mocked reflect payload is `{text, based_on: {facts,
                  documents, mental_models}}` — counts, not individual grounded
                  fact ids/budget/latency (unlike the prototype's richer mock
                  shape), so this shows counts rather than per-fact chips. */}
              {ans.based_on && (
                <div style={{ display: 'flex', gap: 10, flexWrap: 'wrap', alignItems: 'center' }}>
                  <span className="mv-cell-k" style={{ margin: 0 }}>
                    grounded in
                  </span>
                  <span className="mini num">{fmtN(ans.based_on.facts || 0)} facts</span>
                  <span className="mini num">{fmtN(ans.based_on.documents || 0)} documents</span>
                  <span className="mini num">{fmtN(ans.based_on.mental_models || 0)} mental models</span>
                </div>
              )}
            </div>
          )}
        </div>
      )}
      {tab === 'rules' && (directivesQuery.isError || mentalModelsQuery.isError) ? (
        <MvError
          query={directivesQuery.isError ? directivesQuery : mentalModelsQuery}
          what="directives and mental models"
          testid="mv-rules-error"
        />
      ) : tab === 'rules' && (
        <div style={{ display: 'grid', gridTemplateColumns: 'minmax(0,1fr) minmax(0,1fr)' }}>
          <div className="pad" style={{ borderRight: '1px solid var(--line-soft,#1C1C1C)', padding: '12px 14px' }}>
            <div style={{ display: 'flex', alignItems: 'center', marginBottom: 4 }}>
              <span className="mv-cell-k" style={{ margin: 0 }}>
                directives · {onCount} on
              </span>
              <span style={{ flex: 1 }} />
              <button
                className="mv-btn"
                style={{ padding: '0 7px', fontSize: 10 }}
                onClick={() => setModal('directive')}
              >
                + New
              </button>
            </div>
            {directives.map((d) => (
              <div key={d.id} className="mv-dir" data-testid={`mv-rule-row-${d.id}`} style={{ padding: '4px 0', border: 0 }}>
                <button
                  className={'mv-toggle' + (d.is_active ? ' on' : '')}
                  onClick={() =>
                    directiveUpdate.mutate({ bank, id: d.id, body: { is_active: !d.is_active } })
                  }
                >
                  <i />
                </button>
                <span className="txt" style={{ color: d.is_active ? 'var(--fg-2)' : 'var(--fg-4)', fontSize: 11.5 }}>
                  {d.content}
                </span>
                <button className="mvi-x" onClick={() => directiveDelete.mutate({ bank, id: d.id })}>
                  <Icon name="close" size={11} />
                </button>
              </div>
            ))}
          </div>
          <div className="pad" style={{ padding: '12px 14px' }}>
            <div style={{ display: 'flex', alignItems: 'center', marginBottom: 4 }}>
              <span className="mv-cell-k" style={{ margin: 0 }}>
                mental models · {staleCount} stale
              </span>
              <span style={{ flex: 1 }} />
              <button
                className="mv-btn"
                style={{ padding: '0 7px', fontSize: 10 }}
                onClick={() => setModal('model')}
              >
                + New
              </button>
            </div>
            {mentalModels.map((m) => (
              <div
                key={m.id}
                data-testid={`mv-rule-row-${m.id}`}
                style={{ display: 'flex', alignItems: 'center', gap: 8, padding: '4px 0', font: '500 11.5px var(--jbm)', color: 'var(--fg-2)' }}
                title={`${m.source_query} — ${m.content || ''}`}
              >
                <span
                  style={{ flex: 1, color: m.is_stale ? 'var(--warn)' : undefined, overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}
                >
                  {m.name}
                </span>
                {m.is_stale ? (
                  <span className="mv-chip warn">stale</span>
                ) : (
                  <span className="mini num">{(m.last_refreshed_at || '').slice(0, 10)}</span>
                )}
                <button
                  className="mv-btn"
                  style={{ padding: '0 7px', fontSize: 10 }}
                  onClick={() => mentalModelRefresh.mutate({ bank, id: m.id })}
                >
                  <Icon name="refresh" size={11} />
                </button>
              </div>
            ))}
            <p className="mhelp" style={{ marginTop: 6 }}>
              standing questions, refreshed from memory on consolidation — hover to read the current
              answer
            </p>
          </div>
        </div>
      )}
      {modal && <AddModal bank={bank} tab0={modal} onClose={() => setModal(null)} />}
    </div>
  )
}

Object.assign(window, { MemV2BankBar: BankBar, MemV2AddModal: AddModal })
