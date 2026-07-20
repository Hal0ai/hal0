// hal0 dashboard — flag-migration resolution view (D5, post-R3 surface rework).
//
// The single moment the new mental model has to be taught correctly: several
// slots shared ONE model with divergent launch overrides, so the migrator
// refused to fold it. This modal shows, per refused model, the divergent slots
// and ONLY the flags that actually conflict — side by side — and offers the two
// resolutions the brief ratifies:
//
//   · pick one slot's column as CANONICAL (that set of flags becomes the
//     model's single launch-flags text), or
//   · SPLIT into separate model rows (weights are refcounted — cheap).
//
// Nothing is applied until the operator chooses — AND, today, not even then:
// there is no apply endpoint (POST /api/migrations/flag-report/resolve is an
// API-lane request), so the destructive "Apply" is disabled-with-reason. The
// selection interaction is fully wired for when it lands.

const { useState: useStateM, useEffect: useEffectM } = React

const APPLY_DISABLED_REASON =
  'Applying a resolution has no route yet — the migration lane owns POST /api/migrations/flag-report/resolve. (API-lane request)'

function SeverityChip({ severity }) {
  const warn = severity !== 'critical'
  return (
    <span
      className="chip mono"
      data-testid="migration-severity"
      style={{
        fontSize: 10,
        color: warn ? 'var(--warn)' : 'var(--err)',
        borderColor: warn ? 'var(--warn-line)' : 'var(--err-line)',
        background: warn ? 'var(--warn-soft)' : 'var(--err-soft)',
      }}
    >
      {severity}
    </span>
  )
}

export function MigrationResolveView({ open, report, onClose }) {
  const models = report?.refused_models ?? []
  const [idx, setIdx] = useStateM(0)
  const [mode, setMode] = useStateM('canonical') // 'canonical' | 'split'
  const [canonicalSlot, setCanonicalSlot] = useStateM(null) // slot name

  // Reset the pager + choices whenever the modal (re)opens or the report identity
  // changes, so a second open never inherits a stale selection.
  useEffectM(() => {
    if (open) { setIdx(0); setMode('canonical'); setCanonicalSlot(null) }
  }, [open, report?.id])

  // Clear the canonical pick when moving between models — a pick is per-model.
  useEffectM(() => { setCanonicalSlot(null); setMode('canonical') }, [idx])

  if (!open || models.length === 0) return null

  const clamped = Math.min(idx, models.length - 1)
  const m = models[clamped]
  const total = models.length
  const slotCols = m.slots
  // A resolution is "chosen" once canonical has a column, or split is selected.
  const chosen = mode === 'split' || (mode === 'canonical' && canonicalSlot != null)

  return (
    <Modal
      open={open}
      onClose={onClose}
      eyebrow={
        <span className="mono" style={{ fontSize: 11 }}>
          {report.id} · {clamped + 1} of {total}
        </span>
      }
      title="Resolve flag migration"
      width={640}
      foot={
        <>
          <span style={{ display: 'inline-flex', gap: 8, alignItems: 'center' }}>
            <button
              className="btn ghost sm"
              data-testid="migration-pager-prev"
              disabled={clamped === 0}
              onClick={() => setIdx((i) => Math.max(0, i - 1))}
            >‹ Prev</button>
            <button
              className="btn ghost sm"
              data-testid="migration-pager-next"
              disabled={clamped >= total - 1}
              onClick={() => setIdx((i) => Math.min(total - 1, i + 1))}
            >Next ›</button>
          </span>
          <span style={{ display: 'inline-flex', gap: 8, alignItems: 'center' }}>
            <button className="btn ghost sm" data-testid="migration-cancel" onClick={onClose}>Cancel</button>
            <button
              className="btn sm"
              data-testid="migration-apply"
              disabled
              title={APPLY_DISABLED_REASON}
            >
              Apply resolution
            </button>
          </span>
        </>
      }
    >
      <div data-testid="migration-resolve-view" style={{ display: 'flex', flexDirection: 'column', gap: 14 }}>
        {/* model header */}
        <div style={{ display: 'flex', alignItems: 'center', gap: 10 }}>
          <span data-testid="migration-resolve-model" style={{ fontSize: 14, color: 'var(--fg)' }}>{m.model_label}</span>
          <SeverityChip severity={m.severity} />
        </div>
        <p style={{ fontSize: 12.5, lineHeight: 1.6, color: 'var(--fg-2)', margin: 0 }}>
          <b>{slotCols.length} slots</b> shared this model with different launch overrides. R3 gives each
          model one launch-flags text, so hal0 couldn&apos;t fold them automatically. Pick one set as
          canonical, or split into separate models.
        </p>

        {/* side-by-side divergent table — only the conflicting flags */}
        <div className="s-panel" style={{ overflowX: 'auto' }}>
          <div
            className="s-row"
            style={{ display: 'grid', gridTemplateColumns: `120px repeat(${slotCols.length}, 1fr)`, gap: 10, borderBottom: '1px solid var(--line)', padding: '9px 12px' }}
          >
            <span className="mono" style={{ fontSize: 9.5, letterSpacing: '.06em', textTransform: 'uppercase', color: 'var(--fg-4)' }}>flag</span>
            {slotCols.map((sl) => {
              const picked = mode === 'canonical' && canonicalSlot === sl.name
              return (
                <button
                  key={sl.name}
                  className="mono"
                  data-testid={`migration-canonical-${sl.name}`}
                  onClick={() => { setMode('canonical'); setCanonicalSlot(sl.name) }}
                  title="Make this slot's flags the canonical set"
                  style={{
                    textAlign: 'left', background: picked ? 'var(--ok-soft)' : 'transparent',
                    border: `1px solid ${picked ? 'var(--ok-line)' : 'var(--line)'}`, borderRadius: 6,
                    padding: '4px 7px', cursor: 'pointer', color: picked ? 'var(--ok)' : 'var(--fg-2)', fontSize: 11,
                  }}
                >
                  {picked ? '● ' : '○ '}{sl.name} <span style={{ color: 'var(--fg-5)' }}>#{sl.slot_id}</span>
                </button>
              )
            })}
          </div>
          {m.conflicts.map((c) => (
            <div
              key={c.flag}
              className="s-row"
              data-testid={`migration-conflict-${c.flag.replace(/^-+/, '')}`}
              style={{ display: 'grid', gridTemplateColumns: `120px repeat(${slotCols.length}, 1fr)`, gap: 10, padding: '9px 12px', alignItems: 'center' }}
            >
              <span className="mono" style={{ fontSize: 11.5, color: 'var(--fg-2)' }}>{c.flag}</span>
              {slotCols.map((sl) => {
                const v = c.values[sl.name]
                const picked = mode === 'canonical' && canonicalSlot === sl.name
                return (
                  <span
                    key={sl.name}
                    className="mono"
                    style={{ fontSize: 11.5, color: picked ? 'var(--ok)' : 'var(--fg-3)', fontWeight: picked ? 600 : 400 }}
                  >
                    {v ?? <span style={{ color: 'var(--fg-5)' }}>—</span>}
                  </span>
                )
              })}
            </div>
          ))}
        </div>

        {/* resolution mode */}
        <div style={{ display: 'flex', gap: 8 }}>
          <button
            className="btn ghost sm"
            data-testid="migration-mode-canonical"
            onClick={() => setMode('canonical')}
            style={mode === 'canonical' ? { borderColor: 'var(--ok-line)', color: 'var(--ok)' } : undefined}
          >
            Pick canonical{canonicalSlot ? ` · ${canonicalSlot}` : ''}
          </button>
          <button
            className="btn ghost sm"
            data-testid="migration-mode-split"
            onClick={() => { setMode('split'); setCanonicalSlot(null) }}
            style={mode === 'split' ? { borderColor: 'var(--accent)', color: 'var(--accent)' } : undefined}
          >
            Split into {slotCols.length} models
          </button>
        </div>

        <div
          data-testid="migration-apply-reason"
          className="mono"
          style={{ fontSize: 10.5, color: chosen ? 'var(--fg-4)' : 'var(--fg-5)', lineHeight: 1.55, borderTop: '1px solid var(--line)', paddingTop: 10 }}
        >
          {chosen
            ? <>Ready — {mode === 'split' ? `split into ${slotCols.length} separate models` : `${canonicalSlot} becomes canonical`}. ○ {APPLY_DISABLED_REASON}</>
            : <>Nothing is applied until you choose a resolution. ○ {APPLY_DISABLED_REASON}</>}
        </div>
      </div>
    </Modal>
  )
}
