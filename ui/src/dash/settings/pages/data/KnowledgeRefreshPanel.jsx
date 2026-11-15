// DATA ▸ Memory ▸ Knowledge page refresh — [memory.refresh] (ADR-0060).
//
// When the memory engine refreshes knowledge pages, and when it consolidates. hal0 writes these
// onto every page whatever the page's writer asked for, so this one panel decides it for the box.
// The schedules are UTC cron expressions; the editor offers presets in the viewer's local time and
// always shows the UTC expression it will save and the next run. The pure half (cron, presets,
// the PUT body) is refreshSchedule.ts.
import { useEffect, useMemo, useState } from 'react'
import { useSettingsClient } from '../../data/settingsClient.js'
import { useMemoryRefreshPolicy } from '@/api/hooks/useMemory'
import { ApplyBadge } from '../../shared/ApplyBadge.jsx'
import { SRow } from '../../shared/SRow.jsx'
import { _advInputStyle } from '../../shared/SchemaRow.jsx'
import { Segmented } from '../../../memory-area/shared/Segmented.jsx'
import {
  CONSOLIDATION_MODES,
  HOUR_STEPS,
  PAGES_MODES,
  WEEKDAYS,
  cronError,
  cronFromPreset,
  formFromLive,
  formValid,
  formValidity,
  formatLocal,
  localOffsetMinutes,
  nextFire,
  offsetLabel,
  presetFromCron,
  presetWords,
  refreshPutBody,
  saveOutcome,
} from './refreshSchedule'

const KIND_OPTIONS = [
  { id: 'daily', label: 'Daily' },
  { id: 'hours', label: 'Every N hours' },
  { id: 'weekly', label: 'Weekly' },
  { id: 'custom', label: 'Custom cron' },
]

/**
 * One schedule: preset kind, its inputs in local time, the UTC expression and the next run.
 * `value` is the UTC cron the form holds; `onChange` gets the new one (or, for a custom cron
 * being typed, the text as it is, which the form reports invalid until it parses).
 */
export function ScheduleEditor({ value, onChange, disabled = false, testid, offsetMinutes, now }) {
  const offset = offsetMinutes ?? localOffsetMinutes()
  const [preset, setPreset] = useState(() => presetFromCron(value, offset))
  // A value that changes from outside (a save, a reset) re-reads the preset; one this editor
  // produced is already what the preset says.
  useEffect(() => {
    if (cronFromPreset(preset, offset) !== value && !(preset.kind === 'custom' && preset.cron === value)) {
      setPreset(presetFromCron(value, offset))
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [value, offset])

  const apply = (next) => {
    setPreset(next)
    const cron = cronFromPreset(next, offset)
    onChange(cron ?? (next.kind === 'custom' ? next.cron : value))
  }

  const pickKind = (kind) => {
    const time = preset.kind === 'daily' || preset.kind === 'weekly' ? preset.time : '03:00'
    if (kind === 'daily') apply({ kind, time })
    else if (kind === 'weekly') apply({ kind, day: preset.kind === 'weekly' ? preset.day : 0, time })
    else if (kind === 'hours')
      apply({ kind, every: preset.kind === 'hours' ? preset.every : 6, start: preset.kind === 'hours' ? preset.start : 0, minute: 0 })
    else apply({ kind: 'custom', cron: value })
  }

  const error = cronError(value)
  const next = error ? null : nextFire(value, now ?? new Date())
  const inputStyle = { ..._advInputStyle, width: 'auto' }

  return (
    <div data-testid={testid} style={{ display: 'flex', flexDirection: 'column', gap: 6, alignItems: 'flex-end' }}>
      <div style={{ display: 'flex', gap: 6, flexWrap: 'wrap', justifyContent: 'flex-end' }}>
        <select
          aria-label="Schedule"
          value={preset.kind}
          disabled={disabled}
          onChange={(e) => pickKind(e.target.value)}
          style={inputStyle}
          data-testid={`${testid}-kind`}
        >
          {KIND_OPTIONS.map((o) => (
            <option key={o.id} value={o.id}>{o.label}</option>
          ))}
        </select>
        {preset.kind === 'weekly' && (
          <select
            aria-label="Day"
            value={preset.day}
            disabled={disabled}
            onChange={(e) => apply({ ...preset, day: Number(e.target.value) })}
            style={inputStyle}
            data-testid={`${testid}-day`}
          >
            {WEEKDAYS.map((d, i) => (
              <option key={d} value={i}>{d}</option>
            ))}
          </select>
        )}
        {(preset.kind === 'daily' || preset.kind === 'weekly') && (
          <input
            type="time"
            aria-label="Time (your time)"
            value={preset.time}
            disabled={disabled}
            onChange={(e) => apply({ ...preset, time: e.target.value })}
            className="mono"
            style={inputStyle}
            data-testid={`${testid}-time`}
          />
        )}
        {preset.kind === 'hours' && (
          <>
            <select
              aria-label="Every"
              value={preset.every}
              disabled={disabled}
              onChange={(e) => {
                const every = Number(e.target.value)
                apply({ ...preset, every, start: preset.start % every })
              }}
              style={inputStyle}
              data-testid={`${testid}-every`}
            >
              {HOUR_STEPS.map((n) => (
                <option key={n} value={n}>{n === 1 ? 'every hour' : `every ${n} hours`}</option>
              ))}
            </select>
            <select
              aria-label="Starting at (your time)"
              value={preset.start}
              disabled={disabled}
              onChange={(e) => apply({ ...preset, start: Number(e.target.value) })}
              style={inputStyle}
              data-testid={`${testid}-start`}
            >
              {Array.from({ length: preset.every }, (_, h) => (
                <option key={h} value={h}>{`from ${String(h).padStart(2, '0')}:00`}</option>
              ))}
            </select>
            <input
              type="number"
              min={0}
              max={59}
              aria-label="Minute past the hour"
              value={preset.minute}
              disabled={disabled}
              onChange={(e) => apply({ ...preset, minute: Number(e.target.value) })}
              className="mono"
              style={{ ..._advInputStyle, width: 64 }}
              data-testid={`${testid}-minute`}
            />
          </>
        )}
        {preset.kind === 'custom' && (
          <input
            aria-label="Cron expression (UTC)"
            value={preset.cron}
            disabled={disabled}
            placeholder="0 3 * * *"
            onChange={(e) => apply({ kind: 'custom', cron: e.target.value })}
            className="mono"
            style={{ ..._advInputStyle, width: 160, borderColor: error ? 'var(--err)' : 'var(--line)' }}
            data-testid={`${testid}-cron`}
          />
        )}
      </div>
      <div className="mono" style={{ fontSize: 10.5, color: error ? 'var(--err)' : 'var(--fg-3)', textAlign: 'right' }}>
        {error ? (
          <span data-testid={`${testid}-error`}>{error}</span>
        ) : (
          <>
            <span data-testid={`${testid}-words`}>{presetWords(preset)}</span>
            {preset.kind !== 'custom' && <span> your time ({offsetLabel(offset)})</span>}
            <span> · UTC </span>
            <code data-testid={`${testid}-utc`}>{value}</code>
            <span> · next </span>
            <span data-testid={`${testid}-next`}>{formatLocal(next)}</span>
          </>
        )}
      </div>
    </div>
  )
}

export function KnowledgeRefreshPanel({ registry, offsetMinutes, now }) {
  const { settings, update } = useSettingsClient()
  const status = useMemoryRefreshPolicy()
  const notes = Array.isArray(status.data?.notes) ? status.data.notes : []
  const liveRefresh = settings.data?.memory?.refresh || null
  const liveKey = JSON.stringify(liveRefresh)
  const initial = useMemo(() => formFromLive(liveRefresh), [liveKey]) // eslint-disable-line react-hooks/exhaustive-deps
  const [form, setForm] = useState(initial)
  useEffect(() => setForm(initial), [initial])

  const set = (key, value) => setForm((f) => ({ ...f, [key]: value }))
  const validity = formValidity(form)
  const body = refreshPutBody(form, liveRefresh)
  const dirty = JSON.stringify(form) !== JSON.stringify(initial)
  const canSave = !!settings.data && dirty && body !== null && formValid(form) && !update.isPending

  const doSave = async () => {
    if (!body) return
    try {
      const resp = await update.mutateAsync(body)
      const { text, kind } = saveOutcome(resp)
      window.__hal0Toast && window.__hal0Toast(text, kind)
    } catch (e) {
      window.__hal0Toast && window.__hal0Toast(`Save failed — ${e?.message || 'see logs'}`, 'err')
    }
  }

  const badge = (key) => <ApplyBadge settingsKey={`memory.refresh.${key}`} registry={registry} />
  const scheduled = form.pages === 'scheduled'

  return (
    <div className="s-panel" style={{ marginBottom: 12 }} data-testid="mem-refresh-panel">
      <div className="s-row" style={{ paddingBottom: 4, borderBottom: '1px solid var(--line)' }}>
        <div className="k">
          <span>Knowledge page refresh</span>
          <FieldInfoIcon description="hal0.toml [memory.refresh] · when the memory engine rewrites knowledge pages, and when it consolidates. Applied to every page, whatever the page asked for." />
        </div>
      </div>
      {settings.isError && (
        <div className="s-row" style={{ padding: '8px 12px' }}>
          <span className="mono" style={{ fontSize: 11, color: 'var(--err)' }}>
            Could not load settings — {settings.error?.message || 'see logs'}
          </span>
        </div>
      )}
      <SRow
        k="Refresh pages"
        sub="A refresh rewrites a page from its facts: several minutes of the memory model, during which retains wait. Scheduled skips a page whose facts have not changed."
        v={
          <Segmented
            label="Refresh pages"
            options={PAGES_MODES.map((o) => ({ ...o, disabled: !settings.data }))}
            value={form.pages}
            onChange={(id) => set('pages', id)}
            data-testid="mem-refresh-pages"
          />
        }
        actions={badge('pages')}
      />
      {scheduled && (
        <SRow
          k="Schedule"
          sub="Shown in your local time. hal0 saves it as a UTC cron, so after a daylight-saving change the local time moves by an hour."
          v={
            <ScheduleEditor
              value={form.schedule}
              onChange={(v) => set('schedule', v)}
              disabled={!settings.data}
              testid="mem-refresh-schedule"
              offsetMinutes={offsetMinutes}
              now={now}
            />
          }
          actions={badge('schedule')}
        />
      )}
      {form.pages === 'after_consolidation' && (
        <div className="s-row" data-testid="mem-refresh-after-note" style={{ fontSize: 11, color: 'var(--warn)' }}>
          Every consolidation round refreshes each page it touched. On a busy box most of the memory model's time goes to refreshes; keep the minimum interval high.
        </div>
      )}
      {form.pages === 'manual' && (
        <div className="s-row" data-testid="mem-refresh-manual-note" style={{ fontSize: 11, color: 'var(--fg-3)' }}>
          Pages refresh only when you ask: Refresh in the Knowledge tab, or <span className="mono">hal0 memory kb refresh</span>.
        </div>
      )}
      <SRow
        k="Minimum interval"
        sub="Hours between two automatic refreshes of one page (0 to 720). A refresh started by hand is never held back."
        v={
          <span style={{ display: 'inline-flex', alignItems: 'center', gap: 6 }}>
            <input
              type="number"
              min={0}
              max={720}
              step="any"
              value={form.minIntervalHours}
              disabled={!settings.data || form.pages === 'manual'}
              onChange={(e) => set('minIntervalHours', e.target.value)}
              aria-label="Minimum interval in hours"
              className="mono"
              data-testid="mem-refresh-interval"
              style={{ ..._advInputStyle, width: 90, borderColor: validity.minInterval ? 'var(--err)' : 'var(--line)' }}
            />
            <span className="mono" style={{ fontSize: 11, color: 'var(--fg-3)' }}>h</span>
          </span>
        }
        actions={badge('min_interval_s')}
      />
      <SRow
        k="Consolidation"
        sub="Continuous consolidates new facts as they arrive. Scheduled runs it once on its own schedule, so the memory model is idle the rest of the day; recall still finds new facts at once."
        v={
          <Segmented
            label="Consolidation"
            options={CONSOLIDATION_MODES.map((o) => ({ ...o, disabled: !settings.data }))}
            value={form.consolidation}
            onChange={(id) => set('consolidation', id)}
            data-testid="mem-refresh-consolidation"
          />
        }
        actions={badge('consolidation')}
      />
      {form.consolidation === 'scheduled' && (
        <SRow
          k="Consolidation schedule"
          sub="When hal0 starts consolidation of this box's banks. Put it before the page refresh so pages see the new observations."
          v={
            <ScheduleEditor
              value={form.consolidationSchedule}
              onChange={(v) => set('consolidationSchedule', v)}
              disabled={!settings.data}
              testid="mem-refresh-consolidation-schedule"
              offsetMinutes={offsetMinutes}
              now={now}
            />
          }
          actions={badge('consolidation_schedule')}
        />
      )}
      {notes.map((note) => (
        <div key={note} className="s-row mono" role="status" data-testid="mem-refresh-note" style={{ display: 'block', fontSize: 11, color: 'var(--warn)' }}>
          ⚠ {note}
        </div>
      ))}
      <div className="s-row" style={{ display: 'block', fontSize: 11, color: 'var(--fg-3)', lineHeight: 1.5 }}>
        To run refreshes on another slot than retains, set{' '}
        <span className="mono">[memory.lanes.mental_model_refresh] virtual_model</span>.
      </div>
      <div style={{ display: 'flex', justifyContent: 'flex-end', gap: 8, padding: '8px 12px 4px' }}>
        {dirty && (
          <button className="btn ghost sm" onClick={() => setForm(initial)} data-testid="mem-refresh-reset">
            Reset
          </button>
        )}
        <button className="btn sm" disabled={!canSave} onClick={doSave} data-testid="mem-refresh-save">
          {update.isPending ? 'Saving…' : 'Save refresh settings'}
        </button>
      </div>
    </div>
  )
}

// Validation helper for the schedule rows' error state, re-exported for tests.
export { cronError }
