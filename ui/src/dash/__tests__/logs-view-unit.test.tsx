// @vitest-environment happy-dom
// hal0#2931: LogsView honours the unit App parses from #logs?unit=<unit>.
import React from 'react'
import { createRoot } from 'react-dom/client'
import { act } from 'react-dom/test-utils'
import { beforeEach, describe, expect, it, vi } from 'vitest'

;(globalThis as any).IS_REACT_ACT_ENVIRONMENT = true
;(globalThis as any).React = React
;(globalThis as any).Icons = { download: 'dl' }

const unitTail = vi.fn()
const slotStream = vi.fn()

vi.mock('@/api/hooks/useLogs', () => ({
  useLogsHistorical: () => ({ data: { entries: [] }, isError: false }),
  useLogsStream: () => ({ ring: [], disconnected: false }),
  useSlotLogsStream: (...a: unknown[]) => {
    slotStream(...a)
    return { ring: [], disconnected: false, degraded: null }
  },
  parseRawLevel: () => 'info',
}))
vi.mock('@/api/hooks/useSlots', () => ({
  useSlots: () => ({ data: [{ name: 'primary' }, { name: 'other' }] }),
}))
vi.mock('@/api/hooks/useAgents', () => ({
  useUnitLogTail: (...a: unknown[]) => unitTail(...a),
}))

async function mount(unit: string | null) {
  await import('../extras.jsx')
  const LogsView = (window as any).LogsView
  const host = document.createElement('div')
  document.body.appendChild(host)
  await act(async () => {
    createRoot(host).render(<LogsView unit={unit} />)
  })
  return host
}

const buttons = (h: HTMLElement) => [...h.querySelectorAll('button')].map((b) => b.textContent)

describe('LogsView unit prop', () => {
  beforeEach(() => {
    unitTail.mockReset()
    slotStream.mockReset()
    unitTail.mockReturnValue({
      data: { unit: 'hal0-agent@hermes.service', lines: ['hermes: exited with status 1'], count: 1 },
      isError: false,
    })
    document.body.innerHTML = ''
  })

  it('a non-slot unit opens the unit channel and tails that unit', async () => {
    const h = await mount('hal0-agent@hermes.service')
    expect(buttons(h)).toContain('unit')
    expect(unitTail).toHaveBeenLastCalledWith('hal0-agent@hermes.service', 400, true)
    expect(h.textContent).toContain('hermes: exited with status 1')
    expect(h.querySelector('.log-source')?.textContent).toBe('hal0-agent@hermes.service')
  })

  it('a slot unit selects that slot in the slot picker', async () => {
    const h = await mount('hal0-slot@primary.service')
    expect(buttons(h)).not.toContain('unit')
    expect((h.querySelector('select') as HTMLSelectElement).value).toBe('primary')
    expect(slotStream).toHaveBeenLastCalledWith('primary', expect.anything())
  })

  it('no unit offers no unit channel and tails nothing', async () => {
    const h = await mount(null)
    expect(buttons(h)).not.toContain('unit')
    expect(unitTail).toHaveBeenLastCalledWith('', 400, false)
  })
})
