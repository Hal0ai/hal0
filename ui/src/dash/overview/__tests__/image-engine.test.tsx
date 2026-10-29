// @vitest-environment happy-dom
//
// Overview v2 Image engine card: silent on a missing status, idle chat-mode body, Generate
// navigation, and the editor arrow under the on-box / off-box rule.
import React, { act } from 'react'
import { createRoot } from 'react-dom/client'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import type { ArbiterReading, ComfyuiStatus } from '@/api/hooks/useComfyui'
import { OFF_BOX_NOTE } from '../../image/editor-reach'

;(globalThis as any).IS_REACT_ACT_ENVIRONMENT = true
;(globalThis as any).React = React

let statusResult: { data: ComfyuiStatus | undefined; isError?: boolean } = { data: undefined, isError: true }
let arbiterResult: { data: ArbiterReading | undefined } = { data: undefined }
let slotsResult: { data: any[] | undefined } = { data: [{ name: 'img', type: 'image' }] }
let access = { onBox: true, lan: true, url: 'http://box:8188' as string | null, tunnel: 'ssh -L 8188:127.0.0.1:8188 <user>@box' }

vi.mock('@/api/hooks/useComfyui', () => ({
  useComfyui: () => statusResult,
  useArbiter: () => arbiterResult,
}))
vi.mock('@/api/hooks/useSlots', () => ({ useSlots: () => slotsResult }))
vi.mock('../../image/useEditorAccess', () => ({ useEditorAccess: () => access }))

await import('../shell.jsx')
await import('../image-engine.jsx')
const OvImageEngine = (window as any).OvImageEngine as React.ComponentType<{ onGo?: (id: string) => void }>

const IDLE: ComfyuiStatus = {
  mode: 'inference',
  reachable: true,
  engine: 'running',
  container: { name: 'hal0-img', state: 'running' },
  endpoint: null,
  queue: { running: 0, pending: 0 },
  util: null,
  temp: null,
  clock: null,
  progress: null,
  inference: { hermes: false },
  inventory: { checkpoints: 3, diffusion: 2, loras: 5, vae: 1 },
  switchover: { active: false, target: null, error: null },
  arbiter: null,
  gate: null,
  gate_refusal: null,
}
const CHAT: ArbiterReading = {
  mode: 'inference',
  arbiter: {
    mode: 'llm',
    pinned: false,
    saved_llm_slots: [],
    switching: false,
    image_jobs: 0,
    phase: null,
    restore_at: null,
    handoff_until: null,
    drain_in_flight: 0,
    unload_set: ['brain', 'utility', 'embed', 'stt'],
    llm_waiters: { count: 0, oldest_s: null },
  },
  switchover: { active: false, target: null, error: null },
  gate_refusal: null,
  skewMs: 0,
}

let host: HTMLDivElement
let root: ReturnType<typeof createRoot>
const onGo = vi.fn()

function mount() {
  host = document.createElement('div')
  document.body.appendChild(host)
  root = createRoot(host)
  act(() => root.render(<OvImageEngine onGo={onGo} />))
}

beforeEach(() => {
  statusResult = { data: IDLE }
  arbiterResult = { data: CHAT }
  slotsResult = { data: [{ name: 'img', type: 'image' }] }
  access = { onBox: true, lan: true, url: 'http://box:8188', tunnel: 'ssh -L 8188:127.0.0.1:8188 <user>@box' }
  onGo.mockClear()
})
afterEach(() => {
  act(() => root.unmount())
  document.body.innerHTML = ''
  vi.restoreAllMocks()
})

describe('OvImageEngine', () => {
  it('renders nothing and logs no console error when the status route fails (404 / no img slot)', () => {
    const err = vi.spyOn(console, 'error').mockImplementation(() => {})
    statusResult = { data: undefined, isError: true }
    mount()
    expect(host.innerHTML).toBe('')
    expect(err).not.toHaveBeenCalled()
  })

  // GET /api/comfyui/status always answers 200 (a status object with reachable:false on a box with
  // no engine), so a present status alone must not show the card: an img slot has to exist.
  it('renders nothing when the status is present but the box has no img slot', () => {
    slotsResult = { data: [{ name: 'agent', type: 'llm' }] }
    mount()
    expect(host.innerHTML).toBe('')
  })

  it('renders nothing while the slot list is still loading', () => {
    slotsResult = { data: undefined }
    mount()
    expect(host.innerHTML).toBe('')
  })

  it('an image-typed slot under another name still shows the card', () => {
    slotsResult = { data: [{ name: 'draw', type: 'image' }] }
    mount()
    expect(host.querySelector('.ov-card-t')?.textContent).toBe('Image engine')
  })

  it('idle: card title, chat mode pill, engine and queue line, four inventory tiles', () => {
    mount()
    expect(host.querySelector('.ov-card-t')?.textContent).toBe('Image engine')
    expect(host.querySelector('.ov-pill')?.textContent).toBe('chat mode')
    expect(host.querySelector('.ov-pill')?.className).toContain('ok')
    expect(host.querySelector('.ov-ie-row')?.textContent).toContain('queue 0 running · 0 pending')
    const tiles = [...host.querySelectorAll('.ov-inv > div')].map((d) => d.textContent)
    expect(tiles).toEqual(['3ckpt', '2diffusion', '5loras', '1vae'])
    expect(host.querySelector('.ov-prog')).toBeNull()
  })

  it('a queued render in chat mode shows the wait note', () => {
    statusResult = { data: { ...IDLE, queue: { running: 0, pending: 1 } } }
    mount()
    expect(host.querySelector('.ov-prog')?.textContent).toContain(
      '1 render queued · waits for image mode (switch pauses 4 slots)',
    )
  })

  it('a running render shows model, step, clock and a progress bar at the percentage', () => {
    arbiterResult = { data: { ...CHAT, arbiter: { ...CHAT.arbiter!, mode: 'img' } } }
    statusResult = {
      data: {
        ...IDLE,
        queue: { running: 1, pending: 0 },
        progress: { model: 'flux-dev', step: 14, total: 30, node: 'KSampler', elapsed_s: 83, prompt_id: 'p' },
      },
    }
    mount()
    const prog = host.querySelector('.ov-prog')!
    expect(prog.textContent).toContain('flux-dev')
    expect(prog.textContent).toContain('step 14/30')
    expect(prog.textContent).toContain('1:23')
    expect((prog.querySelector('.ov-track > i') as HTMLElement).style.width).toBe('47%')
    expect(host.querySelector('.ov-pill')?.textContent).toBe('image mode')
  })

  it('Generate goes to the image slot page', () => {
    mount()
    const btn = [...host.querySelectorAll('button')].find((b) => b.textContent?.startsWith('Generate'))!
    act(() => btn.click())
    expect(onGo).toHaveBeenCalledWith('slots/image')
  })

  it('on the box the editor arrow opens the editor link in a new tab', () => {
    const open = vi.spyOn(window, 'open').mockImplementation(() => null)
    mount()
    const btn = [...host.querySelectorAll('button')].find((b) => b.textContent?.startsWith('Open editor'))!
    expect((btn as HTMLButtonElement).disabled).toBe(false)
    act(() => btn.click())
    expect(open).toHaveBeenCalledWith('http://box:8188', '_blank', 'noopener')
  })

  it('off the box the editor arrow is disabled and titled with the off-box note', () => {
    const open = vi.spyOn(window, 'open').mockImplementation(() => null)
    access = { onBox: false, lan: false, url: null, tunnel: 'ssh -L 8188:127.0.0.1:8188 <user>@box' }
    mount()
    const btn = [...host.querySelectorAll('button')].find((b) => b.textContent?.startsWith('Open editor')) as HTMLButtonElement
    expect(btn.disabled).toBe(true)
    expect(btn.title).toBe(OFF_BOX_NOTE)
    act(() => btn.click())
    expect(open).not.toHaveBeenCalled()
  })
})
