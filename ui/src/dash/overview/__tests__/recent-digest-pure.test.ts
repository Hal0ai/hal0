// Overview v2 Recent digest: folding slot state flaps (recent-digest-pure.ts).
import { beforeAll, describe, expect, it, vi } from 'vitest'
import { digest } from '../recent-digest-pure'
import { CT105_RECORDS, ERR_RECORD, MANY, NOW_MS, REC } from './recent-digest-fixtures'

beforeAll(() => {
  // HH:MM renders in local time; pin the zone so the hour is deterministic.
  vi.stubEnv('TZ', 'UTC')
})

const opts = { windowS: 600, rows: 5, now: NOW_MS }

describe('digest', () => {
  it('digest folds a serving flap into one served row', () => {
    const rows = digest(CT105_RECORDS, opts)
    expect(rows[0]).toMatchObject({ category: 'slot', tone: 'info', ts: '15:08' })
    expect(rows[0].html).toBe('<b>utility</b> served 3 requests · 15:07–15:08')
  })

  it('folds records outside the window into their own row, reading the last transition', () => {
    const rows = digest(CT105_RECORDS, opts)
    expect(rows).toHaveLength(2)
    expect(rows[1]).toMatchObject({ ts: '08:32', category: 'slot', tone: 'info' })
    expect(rows[1].html).toBe('<b>utility</b> ready → idle')
  })

  it('folds per target when two slots serve together', () => {
    const mk = (id: number, hhmmss: string, target: string, from: string, to: string) => ({
      ...CT105_RECORDS[0],
      id,
      ts: `2026-10-29T${hhmmss}+00:00`,
      target,
      message: `${target}: ${from} → ${to}`,
      after: { slot: target, from, to },
    })
    const rows = digest(
      [
        mk(9, '15:08:30', 'utility', 'serving', 'ready'),
        mk(8, '15:08:20', 'agent', 'serving', 'ready'),
        mk(7, '15:08:10', 'utility', 'ready', 'serving'),
        mk(6, '15:08:00', 'agent', 'ready', 'serving'),
        mk(5, '15:07:50', 'utility', 'serving', 'ready'),
        mk(4, '15:07:40', 'agent', 'serving', 'ready'),
        mk(3, '15:07:30', 'utility', 'ready', 'serving'),
        mk(2, '15:07:20', 'agent', 'ready', 'serving'),
      ],
      opts,
    )
    expect(rows).toHaveLength(2)
    expect(rows.map((r) => r.html)).toEqual([
      '<b>utility</b> served 2 requests · 15:07–15:08',
      '<b>agent</b> served 2 requests · 15:07–15:08',
    ])
  })

  it('digest never folds error records', () => {
    const rows = digest([ERR_RECORD, ...CT105_RECORDS], opts)
    expect(rows.filter((r) => r.tone === 'err')).toHaveLength(1)
    expect(rows[0].html).toBe('<b>code</b> failed to start · exit 137')
  })

  it('digest escapes message html', () => {
    const rows = digest([{ ...REC, target: null, message: '<img src=x onerror=1>' }], opts)
    expect(rows[0].html).not.toContain('<img')
    expect(rows[0].html).toBe('&lt;img src=x onerror=1&gt;')
  })

  it('escapes the target before bolding it', () => {
    const rows = digest([{ ...REC, target: '<b>x', message: 'up' }], opts)
    expect(rows[0].html).toBe('<b>&lt;b&gt;x</b> up')
  })

  it('does not repeat the target when the message already leads with it', () => {
    const rows = digest([{ ...REC, target: 'utility', message: 'utility: serving → ready', action: 'x.y' }], opts)
    expect(rows[0].html).toBe('<b>utility</b> serving → ready')
  })

  it('digest caps rows', () => {
    expect(digest(MANY, opts)).toHaveLength(5)
  })

  it('uses the default window and row cap when none are given', () => {
    expect(digest(MANY, { now: NOW_MS })).toHaveLength(5)
    expect(digest(CT105_RECORDS, { now: NOW_MS })[0].html).toContain('served 3 requests')
  })

  it('keeps passthrough records newest first and tones update and warn rows', () => {
    const rows = digest(
      [
        { ...REC, id: 3, category: 'update', action: 'update.available', target: 'hal0', message: '1.7.2 available' },
        { ...REC, id: 2, severity: 'warn' },
        REC,
      ],
      opts,
    )
    expect(rows.map((r) => r.tone)).toEqual(['update', 'warn', 'info'])
    expect(rows.map((r) => r.category)).toEqual(['update', 'service', 'service'])
  })

  it('labels a record older than 24 h with its weekday', () => {
    const old = { ...REC, ts: '2026-10-27T09:30:00+00:00' } // a Tuesday
    expect(digest([old], opts)[0].ts).toBe('Tue')
  })

  it('a single serving transition reads "1 request"', () => {
    expect(digest([CT105_RECORDS[1]], opts)[0].html).toBe('<b>utility</b> served 1 request · 15:07')
  })

  it('returns nothing for no records', () => {
    expect(digest([], opts)).toEqual([])
  })
})
