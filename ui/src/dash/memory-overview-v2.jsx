// hal0 memory v2 (Bank workspace UI, task C2) — Overview: engine strip,
// growth chart, bank table.
//
// Ported from the design handoff's prototype/overview.jsx (TypeBar, Spark,
// GrowthChart, BankTable, EnginePanel, MemOverview → MemV2Overview here),
// wired to the real hook globals from useHindsight/useMemory (via
// memory-hook-bridge.ts) instead of the prototype's static mock arrays.
//
// Window-globals contract: no ES imports across dash/*.jsx — hooks and
// shared constants are read from window.__hal0Use*/window.MemV2 at render
// time; this file publishes window.MemV2Overview the same way.
//
// ADR-0023: the graph-extraction gate UI (enable/disable, slot picker,
// built/errors/live grid, active-tasks list) is NOT reimplemented here —
// EnginePanel embeds `window.MemoryGraphPanel` (from
// dash/agents/memory-tab.jsx) verbatim, per the brief.

const { useState: useStateMV2Overview } = React

function TypeBar({ b, h = 6 }) {
  const tot = b.world + b.experience + b.observation || 1
  const { FACT_COLORS } = window.MemV2
  return (
    <div className="mv-typebar" style={{ height: h }}>
      <i style={{ width: (b.world / tot) * 100 + '%', background: FACT_COLORS.world }} />
      <i style={{ width: (b.experience / tot) * 100 + '%', background: FACT_COLORS.experience }} />
      <i style={{ width: (b.observation / tot) * 100 + '%', background: FACT_COLORS.observation }} />
    </div>
  )
}

// series: TimeseriesBucket[] ({time, world, experience, observation}) — the
// prototype read a static TIMESERIES[bank] global; here it's passed in
// directly by the caller (already resolved from useBankTimeseries).
function Spark({ series, w = 96, h = 26 }) {
  const ts = series || []
  const vals = ts.map((d) => d.world + d.experience + d.observation)
  const max = Math.max(1, ...vals)
  const pts = vals
    .map((v, i) => `${(i / Math.max(1, vals.length - 1)) * w},${h - 2 - (v / max) * (h - 4)}`)
    .join(' ')
  return (
    <svg width={w} height={h}>
      <polyline points={pts} fill="none" stroke="var(--accent)" strokeWidth="1.25" opacity="0.85" />
    </svg>
  )
}

// stacked-bar growth chart with consolidation ticks.
//
// `consolidationDays` is a Set of "YYYY-MM-DD" strings — completed
// `consolidation`-type operations (exact upstream task_type string, per the
// Hindsight audit — NOT "consolidate") for `bank`, bucketed by day, so the
// accent triangle marker lands on the same bar the operation happened on.
function GrowthChart({ bank, banks, tsQuery, range, onRange, onBank, consolidationDays, wide }) {
  const { FACT_COLORS, fmtN, MvError } = window.MemV2
  const all = tsQuery?.data?.buckets || []
  const ts = range === '7d' ? all.slice(-7) : range === '1d' ? all.slice(-1) : all
  const W = wide ? 1240 : 640,
    H = wide ? 210 : 268,
    padL = 38,
    padB = 18,
    padT = 8
  const iw = ts.length ? (W - padL - 6) / ts.length : 0
  const max = Math.max(1, ...ts.map((d) => d.world + d.experience + d.observation))
  const y = (v) => H - padB - (v / max) * (H - padB - padT)
  const gridVals = [0, 0.5, 1].map((f) => Math.round(max * f))
  const total = ts.reduce((s, d) => s + d.world + d.experience + d.observation, 0)
  return (
    <div className="mv-card mv-growth" data-testid="mv-growth">
      <div className="hd" style={{ flexWrap: 'wrap' }}>
        <span className="mv-eyebrow">Retained</span>
        <span style={{ display: 'flex', gap: 4 }}>
          {(banks || []).map((b) => (
            <button
              key={b.bank_id}
              className={'mv-tf' + (bank === b.bank_id ? ' on' : '')}
              style={{ padding: '2px 9px', fontSize: 10.5 }}
              onClick={() => onBank(b.bank_id)}
            >
              {b.name || b.bank_id}
            </button>
          ))}
        </span>
        <span className="mono num" style={{ font: '600 12px var(--jbm)', color: 'var(--fg-2)' }}>
          +{fmtN(total)}{' '}
          <span style={{ color: 'var(--fg-4)', fontWeight: 400 }}>facts in {range}</span>
        </span>
        <span className="sp" />
        <div className="mv-rangetabs">
          {['1d', '7d', '30d'].map((r) => (
            <button
              key={r}
              data-testid={`mv-growth-range-${r}`}
              className={r === range ? 'on' : ''}
              onClick={() => onRange(r)}
            >
              {r}
            </button>
          ))}
        </div>
      </div>
      <div className="bd">
        {tsQuery?.isError ? (
          <MvError query={tsQuery} what="retain activity" testid="mv-overview-error" />
        ) : ts.length === 0 ? (
          <div className="empty mono" style={{ padding: '28px 0' }}>
            No retain activity in this window.
          </div>
        ) : (
          <>
            <svg viewBox={`0 0 ${W} ${H}`}>
              {gridVals.map((g, i) => (
                <g key={i}>
                  <line x1={padL} x2={W - 4} y1={y(g)} y2={y(g)} stroke="var(--line-soft,#1C1C1C)" />
                  <text className="mv-axis" x={padL - 6} y={y(g) + 3} textAnchor="end">
                    {fmtN(g)}
                  </text>
                </g>
              ))}
              {ts.map((d, i) => {
                const x = padL + i * iw + iw * 0.18,
                  bw = Math.max(2, iw * 0.64)
                const hW = y(0) - y(d.world),
                  hE = y(0) - y(d.experience),
                  hO = y(0) - y(d.observation)
                const cy = y(0)
                const dayKey = String(d.time).slice(0, 10)
                const ticked = consolidationDays && consolidationDays.has(dayKey)
                return (
                  <g key={i}>
                    <rect x={x} y={cy - hW} width={bw} height={hW} fill={FACT_COLORS.world} rx="1" />
                    <rect
                      x={x}
                      y={cy - hW - hE}
                      width={bw}
                      height={hE}
                      fill={FACT_COLORS.experience}
                      rx="1"
                    />
                    <rect
                      x={x}
                      y={cy - hW - hE - hO}
                      width={bw}
                      height={hO}
                      fill={FACT_COLORS.observation}
                      rx="1"
                    />
                    {ticked && (
                      <path
                        d={`M${x + bw / 2 - 3.5} ${H - 4} l3.5 -5 l3.5 5 z`}
                        fill="var(--accent)"
                      />
                    )}
                    {(ts.length <= 8 || i % (wide ? 3 : 6) === 0) && (
                      <text className="mv-axis" x={x + bw / 2} y={H - 6} textAnchor="middle">
                        {window.MemV2.dayKey(d.time)}
                      </text>
                    )}
                  </g>
                )
              })}
            </svg>
            <div className="mv-legend" style={{ marginTop: 8 }}>
              <span>
                <span className="sw" style={{ background: FACT_COLORS.world }} />
                world
              </span>
              <span>
                <span className="sw" style={{ background: FACT_COLORS.experience }} />
                experience
              </span>
              <span>
                <span className="sw" style={{ background: FACT_COLORS.observation }} />
                observation
              </span>
              <span style={{ marginLeft: 'auto' }}>▲ consolidation</span>
            </div>
          </>
        )}
      </div>
    </div>
  )
}

// One bank row — owns its own per-bank queries (stats, 30d timeseries for
// the spark, operations for the working/pending chips) so useMemoryBanks'
// dynamic-length bank list never calls hooks inside a .map callback body
// (each row is its own component instance instead).
function BankRow({ bank, onExplore, compact }) {
  const { FACT_COLORS, fmtN, Icon } = window.MemV2
  const useBankStats = window.__hal0UseBankStats
  const useBankTimeseries = window.__hal0UseBankTimeseries
  const useBankOperations = window.__hal0UseBankOperations
  const summarizeBankOperations = window.__hal0MemSummarizeOps

  const statsQuery = useBankStats ? useBankStats(bank.bank_id) : { data: null }
  // Only fetch the 30d spark series when the row actually renders it
  // (compact tables — Overview's usage — hide the spark column entirely).
  const tsQuery = useBankTimeseries ? useBankTimeseries(compact ? null : bank.bank_id, '30d') : { data: null }
  const opsQuery = useBankOperations ? useBankOperations(bank.bank_id) : { data: null }
  const activity = summarizeBankOperations ? summarizeBankOperations(opsQuery.data) : null

  const stats = statsQuery.data
  const world = stats?.nodes_by_fact_type?.world ?? 0
  const experience = stats?.nodes_by_fact_type?.experience ?? 0
  const observation = stats?.nodes_by_fact_type?.observation ?? 0
  const facts = stats?.total_nodes ?? bank.fact_count ?? 0
  const links = stats?.total_links ?? 0
  const docLine = bank.mission
    ? bank.mission.slice(0, 38) + '…'
    : stats?.total_documents
      ? `${stats.total_documents} docs`
      : 'empty'

  const working = activity?.processing ?? 0
  const pending = activity?.pending ?? 0

  return (
    <div
      className="mv-bankrow"
      data-testid={`mv-bank-row-${bank.bank_id}`}
      onClick={() => onExplore(bank.bank_id)}
    >
      <div>
        <div className="nm">
          {bank.name || bank.bank_id}
          <span className="doc">{docLine}</span>
        </div>
      </div>
      <div>
        <div className="mv-legend num">
          <span>
            <span className="sw" style={{ background: FACT_COLORS.world }} />
            <b>{fmtN(world)}</b>
          </span>
          <span>
            <span className="sw" style={{ background: FACT_COLORS.experience }} />
            <b>{fmtN(experience)}</b>
          </span>
          <span>
            <span className="sw" style={{ background: FACT_COLORS.observation }} />
            <b>{fmtN(observation)}</b>
          </span>
        </div>
        <TypeBar b={{ world, experience, observation }} />
      </div>
      <div>
        <span className="mv-cell-k">facts · links</span>
        {statsQuery.isError ? (
          // #1539-class fix, task C8: a failed stats query used to leave
          // `stats` undefined and every count fell back to 0 — an
          // unreachable engine read as an empty bank. Announce it instead.
          <span className="mv-cell-v num" data-testid="mv-overview-error" style={{ color: 'var(--err)' }}>
            stats unavailable
          </span>
        ) : (
          <span className="mv-cell-v num">
            {fmtN(facts)} <small>· {fmtN(links)}</small>
          </span>
        )}
      </div>
      {!compact && (
        <div>
          <span className="mv-cell-k">activity 30d</span>
          <Spark series={tsQuery.data?.buckets} />
        </div>
      )}
      <div style={{ display: 'flex', flexDirection: 'column', gap: 4, alignItems: 'flex-start' }}>
        {opsQuery.isError ? (
          // The old memory.jsx operations panel `return null`'d on an empty
          // list, so a failed query made the whole card vanish (#1539). This
          // chip is the per-row equivalent of that fix for the v2 table.
          <span className="mv-chip num" data-testid="mv-overview-error" style={{ color: 'var(--err)' }}>
            ops unavailable
          </span>
        ) : (
          <>
            {working > 0 && <span className="mv-chip on num">⟳ {working} working</span>}
            {pending > 0 && <span className="mv-chip num">{pending} pending</span>}
            {working === 0 && pending === 0 && (
              <span className="mv-chip num" style={{ opacity: 0.6 }}>
                idle
              </span>
            )}
          </>
        )}
      </div>
      <button
        className="mv-btn"
        style={{ border: 'none', background: 'transparent', padding: 4 }}
        data-testid={`mv-bank-explore-${bank.bank_id}`}
        onClick={(e) => {
          e.stopPropagation()
          onExplore(bank.bank_id)
        }}
      >
        <span className="arrow">
          <Icon name="arrow" size={14} />
        </span>
      </button>
    </div>
  )
}

function BankTable({ banks, onExplore, compact }) {
  return (
    <div className={'mv-card mv-banks' + (compact ? ' compact' : '')}>
      <div className="hd">
        <span className="mv-eyebrow">Banks · {(banks || []).length}</span>
        <span className="sp" />
        <button className="mv-btn">+ New bank</button>
      </div>
      {(banks || []).map((b) => (
        <BankRow key={b.bank_id} bank={b} onExplore={onExplore} compact={compact} />
      ))}
    </div>
  )
}

function EnginePanel({ banks, growthBank }) {
  const useMemoryEngine = window.__hal0UseMemoryEngine
  const useAggregateBankStats = window.__hal0UseAggregateBankStats
  const useMemoryGraphStatus = window.__hal0UseMemoryGraphStatus
  const { fmtN } = window.MemV2

  const engineQuery = useMemoryEngine ? useMemoryEngine() : { data: null }
  const bankIds = (banks || []).map((b) => b.bank_id)
  const aggregate = useAggregateBankStats ? useAggregateBankStats(bankIds) : { totalFacts: 0 }
  const graphStatus = useMemoryGraphStatus ? useMemoryGraphStatus() : { data: null }

  const engine = engineQuery.data
  const g = graphStatus.data
  const emptyCount = (banks || []).filter((b) => !b.fact_count).length

  return (
    <div className="mv-card" style={{ overflow: 'hidden' }} data-testid="mv-engine-panel">
      <div className="mv-engrid">
        <div>
          <span className="k">engine</span>
          <b style={{ fontSize: 15, color: engine?.reachable ? 'var(--ok)' : 'var(--err)' }}>
            ● {engine?.reachable ? 'reachable' : 'unreachable'}
          </b>
          <span className="s">{engine ? `${engine.engine || 'hindsight'} ${engine.version || ''}` : '—'}</span>
        </div>
        <div>
          <span className="k">banks</span>
          <b className="num">{(banks || []).length}</b>
          <span className="s">{emptyCount} empty</span>
        </div>
        <div>
          <span className="k">facts</span>
          <b className="num">{fmtN(aggregate.totalFacts || 0)}</b>
          <span className="s">all banks</span>
        </div>
        <div>
          <span className="k">graph built</span>
          <b className="num">{fmtN(g?.builds_ok ?? 0)}</b>
          <span className="s">lifetime</span>
        </div>
        <div>
          <span className="k">errors</span>
          <b className="num" style={{ color: g?.errors ? 'var(--err)' : undefined }}>
            {g?.errors ?? 0}
          </b>
          <span className="s">{g?.errors ? 'see logs' : '—'}</span>
        </div>
        <div>
          <span className="k">live</span>
          <b className="num" style={{ color: 'var(--accent)' }}>
            {fmtN(g?.in_flight ?? 0)}
          </b>
          <span className="s">pending</span>
        </div>
      </div>
      {/* ADR-0023 — reused verbatim, not reimplemented (enable/disable,
          slot picker, its own built/errors/live grid + active-tasks list). */}
      {window.MemoryGraphPanel && <window.MemoryGraphPanel />}
    </div>
  )
}

function OverviewError({ query }) {
  if (!query?.isError) return null
  return (
    <div className="empty mono" data-testid="mv-overview-error">
      <div>Memory engine unreachable — {query.error?.message || 'could not load the memory engine'}</div>
      {query.refetch && (
        <button className="mv-btn" style={{ marginTop: 8 }} onClick={() => query.refetch()}>
          Retry
        </button>
      )}
    </div>
  )
}

function MemV2Overview({ onExplore, growthBank, setGrowthBank }) {
  const [range, setRange] = useStateMV2Overview('30d')

  const useMemoryEngine = window.__hal0UseMemoryEngine
  const useMemoryBanks = window.__hal0UseMemoryBanks
  const useBankTimeseries = window.__hal0UseBankTimeseries
  const useBankOperations = window.__hal0UseBankOperations

  const engineQuery = useMemoryEngine ? useMemoryEngine() : { data: null, isError: false }
  const banksQuery = useMemoryBanks ? useMemoryBanks() : { data: null }
  const banks = banksQuery.data?.banks || []

  const growthTsQuery = useBankTimeseries ? useBankTimeseries(growthBank, '30d') : { data: null }
  const growthOpsQuery = useBankOperations ? useBankOperations(growthBank) : { data: null }

  // Completed `consolidation` (exact upstream task_type — NOT "consolidate")
  // operations for the selected growth bank, bucketed by day, for the
  // chart's accent-triangle ticks.
  const consolidationDays = new Set(
    (growthOpsQuery.data?.operations || [])
      .filter((o) => o.task_type === 'consolidation' && o.status === 'completed')
      .map((o) => String(o.created_at).slice(0, 10)),
  )

  if (engineQuery.isError) {
    return (
      <div className="mv-page">
        <OverviewError query={engineQuery} />
      </div>
    )
  }

  return (
    <div className="mv-page">
      <GrowthChart
        bank={growthBank}
        banks={banks}
        tsQuery={growthTsQuery}
        range={range}
        onRange={setRange}
        onBank={setGrowthBank}
        consolidationDays={consolidationDays}
        wide
      />
      <div className="mv-ovsplit">
        <EnginePanel banks={banks} growthBank={growthBank} />
        <BankTable banks={banks} onExplore={onExplore} compact />
      </div>
    </div>
  )
}

Object.assign(window, { MemV2Overview, TypeBar, Spark })
