// @vitest-environment happy-dom
//
// OvNeedsYou: the merged attention inbox. The real merge runs; only the data hooks are mocked.
import React from 'react'
import { createRoot } from 'react-dom/client'
import { act } from 'react-dom/test-utils'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

;(globalThis as any).IS_REACT_ACT_ENVIRONMENT = true
;(globalThis as any).React = React

const state: { notifs: any[]; diagnoses: any[]; doctor: any; restartPending: boolean; failing: string[]; drift: any; updates: any } = {
  notifs: [],
  diagnoses: [],
  doctor: {},
  restartPending: false,
  failing: [],
  drift: { count: 0, slots: [] },
  updates: { hal0: { current: '1.7.1', available: null, channel: 'nightly' } },
}
const run = vi.fn()
const restart = vi.fn()

vi.mock('../../notifications.jsx', () => ({ useNotifications: () => ({ actionableItems: state.notifs }) }))
vi.mock('@/api/hooks/useDiagnoses', () => ({ useDiagnoses: () => ({ diagnoses: state.diagnoses, verdict: 'ok', isLoading: false, isError: false, doctorFeedPending: false, ...state.doctor }) }))
vi.mock('../../diagnostics/useNextStepRunner.js', () => ({ useNextStepRunner: () => ({ run }) }))
vi.mock('@/api/hooks/useRuntime', () => ({
  useHealthSystem: () => ({ data: { status: 'ok', checks: { disk_state: { ok: true, free_mb: 124429 } } } }),
  failingChecks: () => state.failing,
}))
vi.mock('@/api/hooks/useUpdates', () => ({
  useSlotDrift: () => ({ data: state.drift }),
  useRestartDriftedSlots: () => ({ mutate: restart, isPending: state.restartPending }),
  useUpdateState: () => ({ data: state.updates }),
}))
vi.mock('@/api/hooks/useSlots', () => ({
  useSlots: () => ({ data: [{ name: 'a', state: 'ready' }, { name: 'b', state: 'offline' }] }),
}))

await import('../shell.jsx')
await import('../needs-you.jsx')
const OvNeedsYou = (window as any).OvNeedsYou as React.ComponentType<{ onGo: (id: string) => void }>

let host: HTMLDivElement
let root: ReturnType<typeof createRoot>

function mount(onGo: (id: string) => void = () => {}) {
  host = document.createElement('div')
  document.body.appendChild(host)
  root = createRoot(host)
  act(() => {
    root.render(<OvNeedsYou onGo={onGo} />)
  })
}
const button = (label: string) =>
  [...host.querySelectorAll('button')].find((b) => b.textContent === label) as HTMLButtonElement | undefined

beforeEach(() => {
  state.notifs = []
  state.diagnoses = []
  state.doctor = {}
  state.restartPending = false
  state.failing = []
  state.drift = { count: 0, slots: [] }
  run.mockClear()
  restart.mockClear()
})
afterEach(() => {
  act(() => root.unmount())
  host.remove()
})

describe('OvNeedsYou', () => {
  it('shows the all-clear line and a Run doctor link when nothing needs attention', () => {
    const onGo = vi.fn()
    mount(onGo)
    expect(host.querySelector('.ov-inbox-clear')).not.toBeNull()
    expect(host.querySelector('.ov-inbox.has')).toBeNull()
    expect(host.textContent).toContain('All clear')
    expect(host.textContent).toContain('2/2 slots healthy')
    expect(host.textContent).toContain('disk 124 GB free')
    expect(host.textContent).toContain('up to date on nightly')
    act(() => button('Run doctor →')!.click())
    expect(onGo).toHaveBeenCalledWith('settings/doctor')
  })

  it('renders an approval as an accent row and fires the clicked action', () => {
    const approve = vi.fn()
    const deny = vi.fn()
    state.notifs = [
      {
        key: 'approval:1', kind: 'approval', tone: 'accent', eyebrow: 'approval · tool call', body: 'allow shell?',
        actions: [{ label: 'Approve', onClick: approve, primary: true }, { label: 'Deny', onClick: deny }],
      },
    ]
    mount()
    expect(host.querySelector('.ov-inbox.has')).not.toBeNull()
    const row = host.querySelector('.ov-att.accent')!
    expect(row.querySelector('.ov-eye')!.textContent).toBe('approval · tool call')
    expect(button('Approve')!.className).toContain('primary')
    expect(button('Deny')!.className).not.toContain('primary')
    act(() => button('Approve')!.click())
    expect(approve).toHaveBeenCalledOnce()
    expect(deny).not.toHaveBeenCalled()
  })

  it('runs a command next step through the runner', () => {
    const step = { kind: 'command', label: 'restart slot code', target: 'hal0 slot restart code' }
    state.diagnoses = [{ id: 'HAL0-SLOT-CODE-OOM', severity: 'fail', confidence: 'high', summary: 'code OOM', detail: 'exit 137', evidence: [], fixable: true, next_steps: [step] }]
    mount()
    expect(host.querySelector('.ov-att.err')).not.toBeNull()
    act(() => button('restart slot code')!.click())
    expect(run).toHaveBeenCalledWith(step)
  })

  it('restarts drifted slots from the drift row', () => {
    state.drift = { count: 2, slots: [{ slot: 'agent', diffs: [] }, { slot: 'brain', diffs: [] }] }
    mount()
    expect(host.querySelector('.ov-att.warn')!.textContent).toContain('agent, brain')
    act(() => button('restart drifted slots')!.click())
    expect(restart).toHaveBeenCalledWith(undefined)
  })

  it('counts items in the header note and routes open doctor to settings/doctor', () => {
    const onGo = vi.fn()
    state.failing = ['slot_manager: errored: code']
    mount(onGo)
    expect(host.textContent).toContain('1 item')
    act(() => button('open doctor')!.click())
    expect(onGo).toHaveBeenCalledWith('settings/doctor')
  })

  it('does not claim doctor ok while the doctor feed is loading', () => {
    state.doctor = { isLoading: true }
    mount()
    expect(host.textContent).toContain('doctor unavailable')
    expect(host.textContent).not.toContain('doctor ok')
  })

  it('says doctor ok when the feed answered', () => {
    mount()
    expect(host.textContent).toContain('doctor ok')
  })

  it('disables the drift action and shows restarting… while the restart runs', () => {
    state.drift = { count: 1, slots: [{ slot: 'agent', diffs: [] }] }
    state.restartPending = true
    mount()
    expect(button('restart drifted slots')).toBeUndefined()
    const b = button('restarting…')!
    expect(b.disabled).toBe(true)
    act(() => b.click())
    expect(restart).not.toHaveBeenCalled()
  })
})
