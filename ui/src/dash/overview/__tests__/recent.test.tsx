// @vitest-environment happy-dom
//
// Overview v2 Recent card: digest rows as .ov-act, Open events dispatch, empty state.
import React, { act } from 'react'
import { createRoot } from 'react-dom/client'
import { afterEach, beforeAll, beforeEach, describe, expect, it, vi } from 'vitest'
import type { ActivityRecord } from '@/api/hooks/useActivity'
import { CT105_RECORDS, ERR_RECORD, NOW_MS } from './recent-digest-fixtures'

;(globalThis as any).IS_REACT_ACT_ENVIRONMENT = true
;(globalThis as any).React = React

let records: ActivityRecord[] | undefined = []
let q: { isPending?: boolean; isError?: boolean } = {}
const useActivityRecent = vi.fn((_limit?: number) => ({ data: records, isPending: records === undefined && !q.isError, isError: false, ...q }))
vi.mock('@/api/hooks/useActivity', () => ({ useActivityRecent: (n?: number) => useActivityRecent(n) }))

await import('../shell.jsx')
await import('../recent.jsx')
const OvRecent = (window as any).OvRecent as React.ComponentType<{ onGo?: (id: string) => void }>

let host: HTMLDivElement
let root: ReturnType<typeof createRoot>

function mount() {
  host = document.createElement('div')
  document.body.appendChild(host)
  root = createRoot(host)
  act(() => root.render(<OvRecent onGo={() => {}} />))
}

beforeAll(() => {
  vi.stubEnv('TZ', 'UTC')
})
beforeEach(() => {
  vi.useFakeTimers({ toFake: ['Date'] })
  vi.setSystemTime(NOW_MS)
  records = CT105_RECORDS
  q = {}
  useActivityRecent.mockClear()
})
afterEach(() => {
  act(() => root.unmount())
  document.body.innerHTML = ''
  vi.useRealTimers()
})

describe('OvRecent', () => {
  it('renders the folded digest as .ov-act rows with timestamp, category and html', () => {
    mount()
    expect(useActivityRecent).toHaveBeenCalledWith(200)
    expect(host.querySelector('.ov-card-t')?.textContent).toBe('Recent')
    const rows = host.querySelectorAll('.ov-act')
    expect(rows).toHaveLength(2)
    expect(rows[0].querySelector('.ov-ts')?.textContent).toBe('15:08')
    expect(rows[0].querySelector('.ov-cat')?.textContent).toBe('slot')
    expect(rows[0].querySelector('.ov-msg')?.innerHTML).toBe('<b>utility</b> served 3 requests · 15:07–15:08')
    expect(rows[1].querySelector('.ov-msg')?.textContent).toBe('utility ready → idle')
  })

  it('tones the category by severity', () => {
    records = [ERR_RECORD]
    mount()
    expect(host.querySelector('.ov-act .ov-cat')?.className).toBe('ov-cat err')
  })

  it('Open events dispatches hal0:open-events', () => {
    mount()
    const seen = vi.fn()
    window.addEventListener('hal0:open-events', seen)
    const btn = [...host.querySelectorAll('footer button')].find((b) => /Open events/.test(b.textContent ?? ''))
    expect(btn).toBeTruthy()
    act(() => (btn as HTMLButtonElement).click())
    window.removeEventListener('hal0:open-events', seen)
    expect(seen).toHaveBeenCalledTimes(1)
  })

  it('shows "no recent activity" only for a successful empty list', () => {
    records = []
    mount()
    expect(host.querySelector('.ov-empty')?.textContent).toBe('no recent activity')
    expect(host.querySelectorAll('.ov-act')).toHaveLength(0)
  })

  it('shows a dash while the activity query is pending', () => {
    records = undefined
    mount()
    expect(host.querySelector('.ov-empty')?.textContent).toBe('—')
  })

  it('says activity is unavailable when the query errored', () => {
    records = undefined
    q = { isError: true, isPending: false }
    mount()
    expect(host.querySelector('.ov-empty')?.textContent).toBe('activity unavailable')
  })
})
