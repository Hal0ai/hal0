// @vitest-environment happy-dom
//
// Stacks — Load dialog teardown disclosure (#1511) and honest copy (#1524 ST5).
//
// Applying a stack is a declarative replace: converge unloads every running
// slot the stack does not name (src/hal0/stacks/apply.py, pass 3). The Load
// dialog must say so before the operator commits, from the dry-run's
// `unloads`, and the result toast must report what was unloaded instead of a
// bare "loaded". Server-side render; happy-dom only because stacks.jsx
// registers itself on `window` at import time.
import React from 'react'
import { renderToStaticMarkup } from 'react-dom/server'
import { describe, expect, it } from 'vitest'

;(globalThis as unknown as { React: typeof React }).React = React
// Icon glyphs come from chrome.jsx's window global — irrelevant here.
;(globalThis as unknown as { Icons: unknown }).Icons = new Proxy({}, { get: () => null })

import {
  previewFromDryRun,
  LoadDialog,
  formatSlotNames,
  loadResultToast,
  createsNotice,
  warningLines,
  missingModelsNote,
  unloadNotice,
} from '../stacks.jsx'

const VM = {
  slug: 'coding',
  name: 'Coding',
  intent: 'two-slot coding stack',
  slots: [
    { name: 'agent', model: 'qwen3-coder', available: true },
    { name: 'utility', model: 'gemma-3', available: false },
  ],
}

const noop = () => {}

function render(preview: unknown, vm = VM) {
  return renderToStaticMarkup(
    <LoadDialog vm={vm} preview={preview} busy={false} onLoad={noop} onPull={noop} onClose={noop} />,
  )
}

describe('unloadNotice', () => {
  it('names the running slots the apply will unload', () => {
    expect(unloadNotice({ status: 'ok', unloads: ['coder', 'img'] })).toEqual({
      tone: 'warn',
      text: '2 running slots not in this stack will be unloaded: coder, img.',
    })
  })

  it('uses the singular for one slot', () => {
    expect(unloadNotice({ status: 'ok', unloads: ['img'] }).text).toBe(
      '1 running slot not in this stack will be unloaded: img.',
    )
  })

  it('says nothing else is unloaded when the preview list is empty', () => {
    expect(unloadNotice({ status: 'ok', unloads: [] })).toEqual({
      tone: 'muted',
      text: 'No other running slots will be unloaded.',
    })
  })

  it('still discloses the replace semantics when the preview failed', () => {
    const n = unloadNotice({ status: 'error' })
    expect(n.tone).toBe('warn')
    expect(n.text).toContain('unloads every running slot it does not include')
  })

  it('reports a pending preview while loading', () => {
    expect(unloadNotice({ status: 'loading' }).text).toMatch(/^Checking which running slots/)
    expect(unloadNotice(null).tone).toBe('muted')
  })
})

describe('formatSlotNames', () => {
  it('truncates long lists with a +N more tail', () => {
    expect(formatSlotNames(['a', 'b', 'c', 'd', 'e', 'f'])).toBe('a, b, c, d +2 more')
    expect(formatSlotNames(['a', 'b'])).toBe('a, b')
  })
})

describe('loadResultToast', () => {
  it('reports the unloaded slots instead of dropping them', () => {
    expect(
      loadResultToast('Coding', { converged: { errors: [], unloaded: ['coder', 'img', 'tts'] } }),
    ).toEqual(['Stack “Coding” loaded — unloaded 3 slots: coder, img, tts', 'ok'])
  })

  it('keeps the plain message when nothing was unloaded', () => {
    expect(loadResultToast('Coding', { converged: { errors: [], unloaded: [] } })).toEqual([
      'Stack “Coding” loaded',
      'ok',
    ])
  })

  it('warns on slot errors and still reports unloads', () => {
    expect(
      loadResultToast('Coding', {
        converged: { errors: [{ target: 'agent', error: 'boom' }], unloaded: ['img'] },
      }),
    ).toEqual(['Loaded Coding with 1 slot error — unloaded 1 slot: img', 'warn'])
  })
})

describe('LoadDialog', () => {
  it('discloses the teardown before the operator commits', () => {
    const html = render({ status: 'ok', unloads: ['coder', 'img'] })
    expect(html).toContain('data-testid="st-load-unloads"')
    expect(html).toContain('2 running slots not in this stack will be unloaded: coder, img.')
    expect(html).toContain('Load anyway')
  })

  it('holds the confirm button until the preview resolves', () => {
    const html = render({ status: 'loading' })
    expect(html).toMatch(/<button[^>]*disabled=""[^>]*data-testid="st-load-confirm"[^>]*>Checking…<\/button>/)
  })

  it('enables confirm once the preview is in', () => {
    const html = render({ status: 'ok', unloads: [] }, { ...VM, slots: [VM.slots[0]] })
    expect(html).toMatch(/<button class="btn sm" data-testid="st-load-confirm">Load stack<\/button>/)
    expect(html).toContain('No other running slots will be unloaded.')
  })

  it('describes missing models honestly (#1524 ST5)', () => {
    const html = render({ status: 'ok', unloads: [] })
    expect(html).toContain(missingModelsNote(1))
    expect(missingModelsNote(1)).toBe(
      '1 model not found locally — those slots will fail to load until the model is pulled.',
    )
    expect(html).not.toContain('skipped unless pulled')
  })
})

describe('previewFromDryRun (#1511)', () => {
  it('passes an unloads array through', () => {
    expect(previewFromDryRun({ unloads: ['coder'] })).toEqual({
      status: 'ok', unloads: ['coder'], creates: [], warnings: [],
    })
  })

  it('treats an answer without unloads as a failed preview, not an all-clear', () => {
    expect(previewFromDryRun({ stack: 'coding', dry_run: true })).toEqual({ status: 'error' })
    expect(previewFromDryRun(null)).toEqual({ status: 'error' })
  })
})

describe('dry-run creates and warnings (#2348)', () => {
  const ok = (extra: object) => previewFromDryRun({ unloads: [], ...extra })

  it('carries creates and warnings through the preview', () => {
    expect(ok({ creates: ['vision'], warnings: ['agent: model \'x\' not in registry'] })).toEqual({
      status: 'ok', unloads: [], creates: ['vision'], warnings: ['agent: model \'x\' not in registry'],
    })
  })

  it('defaults absent or malformed extras to empty, not a failed preview', () => {
    expect(ok({ creates: 'nope', warnings: null })).toMatchObject({ status: 'ok', creates: [], warnings: [] })
  })

  it('renders warnings and creates next to the unload notice', () => {
    const html = render(ok({ creates: ['vision', 'tts'], warnings: ["agent: profile 'p' not found"] }))
    expect(html).toContain('data-testid="st-load-creates"')
    expect(html).toContain('2 new slots will be created: vision, tts.')
    expect(html).toContain('data-testid="st-load-warnings"')
    expect(html).toContain('agent: profile &#x27;p&#x27; not found')
  })

  it('renders nothing extra when both lists are empty', () => {
    const html = render(ok({ creates: [], warnings: [] }))
    expect(html).not.toContain('st-load-creates')
    expect(html).not.toContain('st-load-warnings')
  })

  it('renders nothing extra while loading or when the preview failed', () => {
    for (const p of [{ status: 'loading' }, { status: 'error' }, null]) {
      const html = render(p)
      expect(html).not.toContain('st-load-creates')
      expect(html).not.toContain('st-load-warnings')
    }
  })

  it('caps a long warning list with a +N more tail', () => {
    const warnings = Array.from({ length: 8 }, (_, i) => `w${i}`)
    const { shown, more } = warningLines(ok({ warnings }))
    expect(shown).toEqual(['w0', 'w1', 'w2', 'w3', 'w4'])
    expect(more).toBe(3)
    expect(render(ok({ warnings }))).toContain('+3 more')
    expect(createsNotice(ok({ creates: ['a', 'b', 'c', 'd', 'e'] }))).toBe(
      '5 new slots will be created: a, b, c, d +1 more.',
    )
  })

  it('surfaces real-apply warnings in the result toast', () => {
    expect(
      loadResultToast('Coding', { converged: { errors: [], unloaded: [] }, warnings: ['a: model \'m\' not in registry'] }),
    ).toEqual(['Stack “Coding” loaded — 1 warning: a: model \'m\' not in registry', 'warn'])
    expect(
      loadResultToast('Coding', { converged: { errors: [] }, warnings: ['a', 'b', 'c'] })[0],
    ).toBe('Stack “Coding” loaded — 3 warnings: a, b +1 more')
  })
})
