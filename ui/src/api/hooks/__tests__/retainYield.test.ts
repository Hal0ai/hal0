// #2030 — zero-fact retain visibility. `watchRetainYield` polls the
// server-annotated single-operation record after a memory add and surfaces a
// "nothing was learned" completion as a warn toast. Exercised via injected
// deps (fetch/notify/sleep) per this suite's pure-logic convention.
import { describe, expect, it } from 'vitest'

import {
  NOTHING_LEARNED_FALLBACK,
  type RetainYieldOperation,
  watchRetainYield,
} from '../useMemory'

const noSleep = () => Promise.resolve()

function harness(ops: Array<RetainYieldOperation | Error>) {
  const toasts: Array<{ msg: string; kind: string }> = []
  let i = 0
  const fetchOp = async () => {
    const next = ops[Math.min(i++, ops.length - 1)]
    if (next instanceof Error) throw next
    return next
  }
  const notify = (msg: string, kind: 'warn') => {
    toasts.push({ msg, kind })
  }
  return { toasts, deps: { fetchOp, notify, sleep: noSleep, maxPolls: 5 } }
}

describe('watchRetainYield', () => {
  it('warns with the server notice on a zero-fact completion', async () => {
    const { toasts, deps } = harness([
      { status: 'processing' },
      { status: 'completed', facts_extracted: 0, nothing_learned: true, notice: 'zero facts' },
    ])
    expect(await watchRetainYield('shared', 'op-1', deps)).toBe('nothing_learned')
    expect(toasts).toEqual([{ msg: 'zero facts', kind: 'warn' }])
  })

  it('falls back to the built-in message when the record has no notice', async () => {
    const { toasts, deps } = harness([{ status: 'completed', nothing_learned: true }])
    expect(await watchRetainYield('shared', 'op-1', deps)).toBe('nothing_learned')
    expect(toasts[0].msg).toBe(NOTHING_LEARNED_FALLBACK)
  })

  it('stays silent when facts were extracted', async () => {
    const { toasts, deps } = harness([{ status: 'completed', facts_extracted: 4 }])
    expect(await watchRetainYield('shared', 'op-1', deps)).toBe('ok')
    expect(toasts).toEqual([])
  })

  it('stays silent on failed/cancelled ops (already loud elsewhere)', async () => {
    for (const status of ['failed', 'cancelled']) {
      const { toasts, deps } = harness([{ status }])
      expect(await watchRetainYield('shared', 'op-1', deps)).toBe('ok')
      expect(toasts).toEqual([])
    }
  })

  it('ends silently when the op fetch errors (engine hiccup, bank mismatch)', async () => {
    const { toasts, deps } = harness([new Error('404')])
    expect(await watchRetainYield('shared', 'op-1', deps)).toBe('gone')
    expect(toasts).toEqual([])
  })

  it('gives up quietly after the poll budget', async () => {
    const { toasts, deps } = harness([{ status: 'processing' }])
    expect(await watchRetainYield('shared', 'op-1', deps)).toBe('timeout')
    expect(toasts).toEqual([])
  })
})
