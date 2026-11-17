// @vitest-environment happy-dom
// Settings > Memory > Models (#3761): the three groups, the conditional remote rows, the minimal
// PUT body, the states around a save, and the rename of the old Reranker panel.
import React, { act } from 'react'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { QueryClient, QueryClientProvider, useMutation, useQuery } from '@tanstack/react-query'

import { cleanup, click, mount } from '../../../../memory-area/shared/test-harness'

;(globalThis as any).FieldInfoIcon = ({ description }: any) => <span data-sub={description} />

const SCHEMA = {
  properties: {
    memory: {
      properties: {
        models: {
          properties: {
            reranker_provider: { type: 'string' },
            reranker_model: { type: 'string' },
            reranker_max_candidates: { type: 'integer', minimum: 1, maximum: 10000, default: 60 },
            reranker_max_candidates_low: { anyOf: [{ type: 'integer', minimum: 1, maximum: 10000 }, { type: 'null' }], default: null },
            reranker_max_candidates_mid: { anyOf: [{ type: 'integer', minimum: 1, maximum: 10000 }, { type: 'null' }], default: null },
            reranker_max_candidates_high: { anyOf: [{ type: 'integer', minimum: 1, maximum: 10000 }, { type: 'null' }], default: null },
            reranker_local_batch_size: { anyOf: [{ type: 'integer', minimum: 1, maximum: 1024 }, { type: 'null' }], default: null },
            reranker_local_max_concurrent: { anyOf: [{ type: 'integer', minimum: 1, maximum: 64 }, { type: 'null' }], default: null },
            reranker_local_timeout_s: { anyOf: [{ type: 'integer', minimum: 0, maximum: 3600 }, { type: 'null' }], default: null },
            reranker_bucket_batching: { type: 'boolean', default: true },
            reranker_base_url: { anyOf: [{ type: 'string' }, { type: 'null' }], default: null },
            reranker_api_model: { anyOf: [{ type: 'string' }, { type: 'null' }], default: null },
            reranker_timeout_s: { anyOf: [{ type: 'integer', minimum: 1, maximum: 600 }, { type: 'null' }], default: null },
          },
        },
        decision: {
          properties: {
            provider: { type: 'string' },
            threads: { type: 'integer', minimum: 1, maximum: 64, default: 8 },
            max_concurrent: { type: 'integer', minimum: 1, maximum: 64, default: 4 },
            max_request_tokens: { type: 'integer', minimum: 256, maximum: 32768, default: 4096 },
            timeout_s: { type: 'integer', minimum: 5, maximum: 600, default: 60 },
          },
        },
      },
    },
  },
}

const state: any = {
  live: null,
  sent: [] as any[],
  respond: null as null | (() => Promise<any>),
  reads: 0,
  models: null,
  modelsError: false,
  settingsError: false,
}

// Real react-query state: the models read and the save are a real query and a real mutation, so
// "restarting" and "saving" come from state changes, not from a flag the test sets.
vi.mock('@/api/hooks/useMemory', () => ({
  useMemoryModels: () =>
    useQuery({
      queryKey: ['memory', 'models'],
      queryFn: async () => {
        state.reads += 1
        if (state.modelsError) throw new Error('engine down')
        return state.models
      },
      retry: false,
      staleTime: Infinity,
    }),
}))

vi.mock('../../../data/settingsClient.js', () => ({
  useSettingsClient: () => {
    const m = useMutation({
      mutationFn: async (body: unknown) => {
        state.sent.push(body)
        return state.respond ? state.respond() : {}
      },
    })
    return {
      settings: { data: state.live, isError: state.settingsError, error: null },
      schema: { data: SCHEMA },
      update: { isPending: m.isPending, mutateAsync: m.mutateAsync },
    }
  },
}))

const { MemoryModelsPanel } = await import('../MemoryModelsPanel.jsx')
const { modelsFocus } = await import('../memoryModels')

const q = (host: HTMLElement, id: string) => host.querySelector(`[data-testid="${id}"]`) as HTMLElement | null
const input = (host: HTMLElement, key: string) => q(host, `mem-models-${key.replace(/\./g, '-')}`) as HTMLInputElement

function setValue(el: Element, value: string) {
  const proto = el instanceof HTMLSelectElement ? HTMLSelectElement.prototype : HTMLInputElement.prototype
  const setter = Object.getOwnPropertyDescriptor(proto, 'value')!.set!
  act(() => {
    setter.call(el, value)
    el.dispatchEvent(new Event(el instanceof HTMLSelectElement ? 'change' : 'input', { bubbles: true }))
  })
}

async function save(host: HTMLElement, id: string) {
  await act(async () => {
    q(host, id)!.click()
  })
}

const render = (focus: any = null) =>
  mount(
    <QueryClientProvider client={new QueryClient()}>
      <MemoryModelsPanel registry={{}} focus={focus} />
    </QueryClientProvider>,
  )

// react-query notifies on a zero timer, so let one run (the fake clock, if it is on).
const flush = () =>
  act(async () => {
    // A few turns, not one: a loaded CI runner needs more than a single timer to settle.
    for (let i = 0; i < 5; i += 1) {
      if (vi.isFakeTimers()) await vi.advanceTimersByTimeAsync(0)
      else await new Promise((r) => setTimeout(r, 5))
    }
  })
const tick = (ms: number) => act(async () => { await vi.advanceTimersByTimeAsync(ms) })

// A save the test settles by hand.
function holdSave() {
  let release!: (v?: unknown) => void
  state.respond = () => new Promise((r) => { release = r })
  return () => act(async () => { release({}) })
}

beforeEach(() => {
  state.live = {
    memory: {
      models: { reranker_provider: 'local', reranker_model: 'hindsight-ai/hs-rerank-1', reranker_max_candidates: 60, reranker_bucket_batching: true },
      decision: { provider: 'local', threads: 8, max_concurrent: 4, max_request_tokens: 4096, timeout_s: 60 },
    },
  }
  state.models = {
    embeddings: { provider: 'local', model: 'hindsight-ai/hs-embed-1', recorded: 'hindsight-ai/hs-embed-1', recorded_provider: 'local' },
    reranker: { provider: 'local', model: 'hindsight-ai/hs-rerank-1' },
    decision: { enabled: true, provider: 'local', model: 'hs-decide-1-flash', reachable: true },
  }
  state.sent = []
  state.respond = null
  state.reads = 0
  state.modelsError = false
  state.settingsError = false
  ;(window as any).__hal0Toast = vi.fn()
})
afterEach(() => {
  vi.useRealTimers()
  cleanup()
})

describe('MemoryModelsPanel', () => {
  it('shows the embedder read-only with the switch command and no editable embeddings key', async () => {
    const { host } = render()
    await flush()
    const g = q(host, 'mem-models-embedder')!
    expect(g.textContent).toContain('hindsight-ai/hs-embed-1')
    expect(g.textContent).toContain('built with this model')
    expect(q(host, 'mem-models-embedder-cmd')!.textContent).toContain('sudo hal0 memory embedder switch <model>')
    expect(g.querySelectorAll('input, select')).toHaveLength(0)
    expect(host.innerHTML).not.toContain('embeddings_')
  })

  it('says when settings ask for a model the store was not built with', async () => {
    state.models.embeddings.model = 'other/embed'
    const { host } = render()
    await flush()
    expect(q(host, 'mem-models-embedder-note')!.textContent).toContain('other/embed')
  })

  it('offers the reranker providers the schema validator allows', () => {
    const { host } = render()
    const opts = Array.from(q(host, 'mem-models-reranker-provider')!.querySelectorAll('option')).map((o) => (o as HTMLOptionElement).value)
    expect(opts).toEqual(['local', 'litellm', 'tei', 'rrf'])
  })

  it('shows local knobs for local and no remote rows', () => {
    const { host } = render()
    expect(input(host, 'memory.models.reranker_local_batch_size')).not.toBeNull()
    expect(input(host, 'memory.models.reranker_bucket_batching')).not.toBeNull()
    expect(input(host, 'memory.models.reranker_base_url')).toBeNull()
    expect(input(host, 'memory.models.reranker_api_model')).toBeNull()
  })

  it('shows endpoint rows for litellm, only the url and timeout for tei, none for rrf', () => {
    const { host } = render()
    const provider = q(host, 'mem-models-reranker-provider')!
    setValue(provider, 'litellm')
    expect(input(host, 'memory.models.reranker_base_url')).not.toBeNull()
    expect(input(host, 'memory.models.reranker_api_model')).not.toBeNull()
    expect(input(host, 'memory.models.reranker_timeout_s')).not.toBeNull()
    expect(input(host, 'memory.models.reranker_local_batch_size')).toBeNull()
    setValue(provider, 'tei')
    expect(input(host, 'memory.models.reranker_base_url')).not.toBeNull()
    expect(input(host, 'memory.models.reranker_api_model')).toBeNull()
    setValue(provider, 'rrf')
    expect(input(host, 'memory.models.reranker_base_url')).toBeNull()
    expect(input(host, 'memory.models.reranker_timeout_s')).toBeNull()
  })

  it('keeps the save disabled until something changes, and says saving restarts the engine', () => {
    const { host } = render()
    expect((q(host, 'mem-models-reranker-save') as HTMLButtonElement).disabled).toBe(true)
    expect(q(host, 'mem-models-reranker')!.textContent).toContain('Saving restarts the memory engine.')
    expect(q(host, 'mem-models-decision')!.textContent).toContain('Saving restarts the memory engine.')
  })

  it('sends only the changed keys, nested', async () => {
    const { host } = render()
    setValue(input(host, 'memory.models.reranker_max_candidates'), '40')
    setValue(input(host, 'memory.models.reranker_local_batch_size'), '16')
    await save(host, 'mem-models-reranker-save')
    expect(state.sent).toEqual([{ memory: { models: { reranker_max_candidates: 40, reranker_local_batch_size: 16 } } }])
  })

  it('sends null to clear an optional number', async () => {
    state.live.memory.models.reranker_local_batch_size = 16
    const { host } = render()
    setValue(input(host, 'memory.models.reranker_local_batch_size'), '')
    await save(host, 'mem-models-reranker-save')
    expect(state.sent).toEqual([{ memory: { models: { reranker_local_batch_size: null } } }])
  })

  it('does not send a value put back to what is saved', () => {
    const { host } = render()
    setValue(input(host, 'memory.models.reranker_max_candidates'), '40')
    setValue(input(host, 'memory.models.reranker_max_candidates'), '60')
    expect((q(host, 'mem-models-reranker-save') as HTMLButtonElement).disabled).toBe(true)
  })

  it('refuses an out-of-range number and a remote provider with no endpoint', () => {
    const { host } = render()
    setValue(input(host, 'memory.models.reranker_max_candidates'), '0')
    expect((q(host, 'mem-models-reranker-save') as HTMLButtonElement).disabled).toBe(true)
    expect(host.textContent).toContain('At least 1.')
    setValue(input(host, 'memory.models.reranker_max_candidates'), '60')
    setValue(q(host, 'mem-models-reranker-provider')!, 'litellm')
    expect((q(host, 'mem-models-reranker-save') as HTMLButtonElement).disabled).toBe(true)
    expect(host.textContent).toContain('The endpoint is required')
    setValue(input(host, 'memory.models.reranker_base_url'), 'http://127.0.0.1:9000/v1')
    setValue(input(host, 'memory.models.reranker_api_model'), 'bge')
    expect((q(host, 'mem-models-reranker-save') as HTMLButtonElement).disabled).toBe(false)
  })

  it('shows validation errors from config.invalid on the field they name', async () => {
    state.respond = async () => {
      throw Object.assign(new Error('hal0 config failed schema validation'), {
        code: 'config.invalid',
        details: { 'memory.models.reranker_max_candidates': 'too many', 'memory.somewhere.else': 'odd' },
      })
    }
    const { host } = render()
    setValue(input(host, 'memory.models.reranker_max_candidates'), '40')
    await save(host, 'mem-models-reranker-save')
    const err = q(host, 'mem-models-memory-models-reranker_max_candidates-err') ?? host.querySelector('[role="alert"][id$="reranker_max_candidates-err"]')
    expect(err!.textContent).toBe('too many')
    expect(input(host, 'memory.models.reranker_max_candidates').getAttribute('aria-invalid')).toBe('true')
    expect(q(host, 'mem-models-reranker-outcome')!.textContent).toContain('Not saved')
    expect(q(host, 'mem-models-reranker-outcome')!.textContent).toContain('memory.somewhere.else')
  })

  it('reports a save that landed but whose engine restart failed', async () => {
    state.respond = async () => ({ _hal0: { showcase_dropin: { error: 'systemctl refused' } } })
    const { host } = render()
    setValue(input(host, 'memory.models.reranker_max_candidates'), '40')
    await save(host, 'mem-models-reranker-save')
    const out = q(host, 'mem-models-reranker-outcome')!
    expect(out.textContent).toContain('did not pick it up')
    expect(out.textContent).toContain('systemctl refused')
  })

  it('confirms a clean save', async () => {
    const { host } = render()
    setValue(input(host, 'memory.models.reranker_max_candidates'), '40')
    await save(host, 'mem-models-reranker-save')
    expect(q(host, 'mem-models-reranker-outcome')!.textContent).toContain('restarted')
  })

  it('offers the decision providers and warns plainly about hindsight-cloud', () => {
    const { host } = render()
    const select = q(host, 'mem-models-decision-provider')!
    const opts = Array.from(select.querySelectorAll('option')).map((o) => (o as HTMLOptionElement).value)
    expect(opts).toEqual(['off', 'local', 'hindsight-cloud'])
    expect(q(host, 'mem-models-decision-cloud')).toBeNull()
    setValue(select, 'hindsight-cloud')
    expect(q(host, 'mem-models-decision-cloud')!.textContent).toContain('sends memory content to a hosted service')
    expect(Array.from(select.querySelectorAll('option')).find((o) => (o as HTMLOptionElement).value === 'hindsight-cloud')!.textContent).toContain('sends memory content off this box')
  })

  it('sends only the changed decision keys', async () => {
    const { host } = render()
    setValue(input(host, 'memory.decision.threads'), '4')
    await save(host, 'mem-models-decision-save')
    expect(state.sent).toEqual([{ memory: { decision: { threads: 4 } } }])
  })

  it('shows the install command when the local server does not answer, worded for absent fields', async () => {
    state.models.decision.reachable = false
    const { host } = render()
    await flush()
    expect(q(host, 'mem-models-decision-note')!.textContent).toContain('not installed or not running')
    expect(q(host, 'mem-models-decision-cmd')!.textContent).toContain('sudo hal0 memory decision install')
  })

  it('uses installed and unit_active when the read carries them', async () => {
    state.models.decision = { ...state.models.decision, reachable: false, installed: true, unit_active: false }
    const { host } = render()
    await flush()
    expect(q(host, 'mem-models-decision-note')!.textContent).toContain('installed but not running')
  })

  it('shows no install command when the server answers or the provider is off', async () => {
    const a = render()
    await flush()
    expect(q(a.host, 'mem-models-decision-cmd')).toBeNull()
    cleanup()
    state.models.decision = { enabled: true, provider: 'off', reachable: false }
    state.live.memory.decision.provider = 'off'
    const b = render()
    await flush()
    expect(q(b.host, 'mem-models-decision-cmd')).toBeNull()
    expect(q(b.host, 'mem-models-decision-status')!.textContent).toBe('off')
  })

  it('says the models read failed and offers a retry, without blocking the reranker form', async () => {
    state.modelsError = true
    state.models = null
    const { host } = render()
    await flush()
    expect(q(host, 'mem-models-read-failed')!.textContent).toContain('Could not read the memory models')
    expect(input(host, 'memory.models.reranker_max_candidates')).not.toBeNull()
  })

  it('shows a loading line and disables the fields until settings arrive', () => {
    state.live = null
    const { host } = render()
    expect(q(host, 'mem-models-loading')).not.toBeNull()
    expect(input(host, 'memory.models.reranker_max_candidates').disabled).toBe(true)
  })

  it('scrolls to and focuses the group a deep link names', () => {
    const scrolled: string[] = []
    ;(Element.prototype as any).scrollIntoView = function () { scrolled.push(this.id) }
    const { host } = render('decision')
    expect(scrolled).toEqual(['memory-models-decision-title'])
    expect(document.activeElement).toBe(host.querySelector('#memory-models-decision-title'))
  })

  it('reads the group out of the settings sub-path', () => {
    expect(modelsFocus('models/reranker')).toBe('reranker')
    expect(modelsFocus('models/embedder')).toBe('embedder')
    expect(modelsFocus('models/nonsense')).toBeNull()
    expect(modelsFocus('other/reranker')).toBeNull()
    expect(modelsFocus(null)).toBeNull()
  })

  it('has a Reset that drops unsaved edits', () => {
    const { host } = render()
    setValue(input(host, 'memory.models.reranker_max_candidates'), '40')
    const reset = Array.from(q(host, 'mem-models-reranker')!.querySelectorAll('button')).find((b) => b.textContent === 'Reset')!
    click(reset)
    expect(input(host, 'memory.models.reranker_max_candidates').value).toBe('60')
  })

  it('shows the restart only in the group that is saving, and holds the other Save with a reason', async () => {
    const settle = holdSave()
    const { host } = render()
    await flush()
    setValue(input(host, 'memory.models.reranker_max_candidates'), '40')
    setValue(input(host, 'memory.decision.threads'), '4')
    act(() => { q(host, 'mem-models-reranker-save')!.click() })
    await flush()
    expect(q(host, 'mem-models-reranker-restarting')).not.toBeNull()
    expect(q(host, 'mem-models-decision-restarting')).toBeNull()
    const other = q(host, 'mem-models-decision-save') as HTMLButtonElement
    expect(other.disabled).toBe(true)
    expect(other.textContent).toBe('Save decision model')
    expect(q(host, 'mem-models-decision-blocked')!.textContent).toContain('One restart at a time')
    await settle()
    expect(q(host, 'mem-models-reranker-restarting')).toBeNull()
    expect(q(host, 'mem-models-decision-blocked')).toBeNull()
    expect((q(host, 'mem-models-decision-save') as HTMLButtonElement).disabled).toBe(false)
  })

  it('drops edits to decision numbers that are disabled when the provider is not local', async () => {
    const { host } = render()
    await flush()
    setValue(input(host, 'memory.decision.threads'), '4')
    setValue(q(host, 'mem-models-decision-provider')!, 'off')
    expect(input(host, 'memory.decision.threads').disabled).toBe(true)
    await save(host, 'mem-models-decision-save')
    expect(state.sent).toEqual([{ memory: { decision: { provider: 'off' } } }])
  })

  it('does not count a dropped edit as a change', async () => {
    const { host } = render()
    await flush()
    setValue(q(host, 'mem-models-decision-provider')!, 'off')
    setValue(q(host, 'mem-models-decision-provider')!, 'local')
    setValue(input(host, 'memory.decision.threads'), '4')
    setValue(q(host, 'mem-models-decision-provider')!, 'hindsight-cloud')
    setValue(q(host, 'mem-models-decision-provider')!, 'local')
    await save(host, 'mem-models-decision-save')
    expect(state.sent).toEqual([{ memory: { decision: { threads: 4 } } }])
  })

  describe('engine restart after a save', () => {
    it('retries a failed models read with backoff and shows no error while the engine comes back', async () => {
      vi.useFakeTimers()
      const { host } = render()
      await flush()
      setValue(input(host, 'memory.models.reranker_max_candidates'), '40')
      // The engine is down from the moment the save lands.
      state.respond = async () => { state.modelsError = true; return {} }
      await save(host, 'mem-models-reranker-save')
      await flush()
      expect(q(host, 'mem-models-restarting')).not.toBeNull()
      expect(q(host, 'mem-models-read-failed')).toBeNull()
      const afterSave = state.reads
      await tick(1100)
      expect(state.reads).toBeGreaterThan(afterSave)
      // Back off: the next retry is not immediate.
      const n = state.reads
      await tick(1500)
      expect(state.reads).toBe(n)
      // The engine answers: the note goes, the data is read, no error ever showed.
      state.modelsError = false
      await tick(10000)
      expect(q(host, 'mem-models-restarting')).toBeNull()
      expect(q(host, 'mem-models-read-failed')).toBeNull()
      expect(q(host, 'mem-models-embedder-model')!.textContent).toContain('hs-embed-1')
    })

    it('shows the error once the window has run out', async () => {
      vi.useFakeTimers()
      const { host } = render()
      await flush()
      setValue(input(host, 'memory.models.reranker_max_candidates'), '40')
      state.respond = async () => { state.modelsError = true; return {} }
      await save(host, 'mem-models-reranker-save')
      await flush()
      expect(q(host, 'mem-models-restarting')).not.toBeNull()
      await tick(70000)
      expect(q(host, 'mem-models-restarting')).toBeNull()
      expect(q(host, 'mem-models-read-failed')).not.toBeNull()
    })

    it('shows a failed read at once when no save has just landed', async () => {
      state.modelsError = true
      const { host } = render()
      await flush()
      expect(q(host, 'mem-models-read-failed')).not.toBeNull()
      expect(q(host, 'mem-models-restarting')).toBeNull()
    })
  })
})
