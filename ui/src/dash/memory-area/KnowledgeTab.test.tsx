// @vitest-environment happy-dom
//
// Memory ▸ Knowledge: each state of the tab, with the hooks replaced (knowledge-test-fixtures)
// and the area object built as MemoryArea builds it.
import { act, createElement } from 'react'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { BANKS, ENGINE, makeArea } from './area-test-fixtures'
import {
  MODELS,
  TREE,
  knowledgeModel,
  knowledgeState,
  loadedState,
  pageNode,
  type KnowledgeHookState,
} from './knowledge-test-fixtures'
import { cleanup, click, mount, press, settle } from './shared/test-harness'

const m = vi.hoisted(() => ({ state: null as unknown }))

vi.mock('@/api/hooks/useHindsight', async () => {
  const { knowledgeHooks } = await import('./knowledge-test-fixtures')
  return knowledgeHooks(() => m.state as KnowledgeHookState)
})

const { KnowledgeTab } = await import('./KnowledgeTab.jsx')

let state: KnowledgeHookState
const use = (next: KnowledgeHookState) => {
  state = next
  m.state = next
}

beforeEach(() => {
  use(loadedState())
  vi.useFakeTimers({ toFake: ['Date'] })
  vi.setSystemTime(new Date('2026-11-09T12:00:00Z'))
})
afterEach(() => {
  cleanup()
  vi.useRealTimers()
})

type Area = ReturnType<typeof makeArea>
const HASH = '#memory/knowledge?bank=shared'
const render = (area: Area = makeArea({ hash: HASH })) => {
  const mounted = mount(createElement(KnowledgeTab, { area }))
  return { ...mounted, rerender: (next: Area = area) => mounted.render(createElement(KnowledgeTab, { area: next })) }
}
const byId = (host: Element, id: string) => host.querySelector<HTMLElement>(`[data-testid="${id}"]`)
const allById = (host: Element, id: string) => [...host.querySelectorAll<HTMLElement>(`[data-testid="${id}"]`)]
const textOf = (el: Element | null | undefined) => (el?.textContent ?? '').replace(/\s+/g, ' ').trim()
const stat = (host: Element, id: string) => host.querySelector<HTMLElement>(`[data-stat="${id}"]`)!
const chips = (el: Element | null) => [...(el?.querySelectorAll('.chip.ma-chip') ?? [])].map((c) => c.textContent)
const node = (host: Element, id: string) => byId(host, `ma-kn-node-${id}`)!
const calls = (name: string) => state.calls.filter((c) => c.name === name).map((c) => c.vars)

/** Let a chain of awaited writes run to its end. */
async function flush() {
  for (let i = 0; i < 12; i += 1) await settle()
}

function type(el: Element | null, value: string) {
  const input = el as HTMLInputElement | HTMLTextAreaElement
  const proto = input instanceof HTMLTextAreaElement ? HTMLTextAreaElement.prototype : HTMLInputElement.prototype
  const setter = Object.getOwnPropertyDescriptor(proto, 'value')!.set!
  act(() => {
    setter.call(input, value)
    input.dispatchEvent(new Event('input', { bubbles: true }))
  })
}

async function submit(form: Element | null) {
  act(() => {
    form!.dispatchEvent(new Event('submit', { bubbles: true, cancelable: true }))
  })
  await flush()
}

const field = (form: Element | null, label: string) => {
  const found = [...form!.querySelectorAll('label')].find((l) => textOf(l) === label)
  expect(found, `label "${label}"`).toBeTruthy()
  return form!.querySelector<HTMLInputElement>(`#${CSS.escape(found!.htmlFor)}`)!
}

describe('loading', () => {
  it('shows skeletons, and not one figure, while the engine check is out', () => {
    use(knowledgeState())
    const { host } = render(makeArea({ hash: HASH, engineQuery: {} }))
    expect(byId(host, 'ma-knowledge-loading')).not.toBeNull()
    expect(host.querySelectorAll('.ma-skel').length).toBeGreaterThan(4)
    expect(host.textContent).not.toMatch(/\d/)
  })

  it('shows skeletons, and not one figure, while the bank answers', () => {
    use(knowledgeState())
    const { host } = render()
    // The scope bar is there (the banks are known); everything under it is loading.
    expect(byId(host, 'ma-scope')).not.toBeNull()
    for (const id of ['ma-kn-pages-loading', 'ma-kn-models-loading', 'ma-kn-reader-loading', 'ma-kn-directives-loading']) {
      expect(byId(host, id), id).not.toBeNull()
    }
    expect([...host.querySelectorAll('.ma-stat-label')].map((el) => el.textContent)).toEqual([
      'Knowledge pages',
      'Mental models',
      'Directives',
      'Last successful refresh',
    ])
    for (const value of host.querySelectorAll('.ma-stat-value')) expect(value.querySelector('.ma-skel')).not.toBeNull()
    // No digit anywhere under the scope bar: a loading tab never shows a zero.
    const scope = byId(host, 'ma-scope')!
    const below = [...host.querySelector('[data-testid="ma-knowledge"]')!.children].filter((el) => el !== scope)
    for (const el of below) expect(el.textContent, el.getAttribute('data-testid') ?? el.className).not.toMatch(/\d/)
    // Nothing can be refreshed yet, and the button says why.
    const all = byId(host, 'ma-kn-refresh-all') as HTMLButtonElement
    expect(all.disabled).toBe(true)
    expect(textOf(document.getElementById(all.getAttribute('aria-describedby')!))).toBe('Still loading.')
  })

  it('does not ask for a page before the lists say what the bank holds', () => {
    use(knowledgeState())
    render(makeArea({ hash: `${HASH}&page=kb-thermal` }))
    expect(state.asked.some((a) => a.name === 'page')).toBe(false)
  })
})

describe('summary', () => {
  it('counts pages and mental models apart, with what is stale and what failed', () => {
    const { host } = render()
    const pages = stat(host, 'pages')
    expect(textOf(pages.querySelector('.ma-stat-value'))).toBe('3pagesfailing')
    expect(textOf(pages.querySelector('.ma-stat-helper'))).toBe('2 of 3 stale · 1 failed its last refresh')
    const models = stat(host, 'models')
    expect(textOf(models.querySelector('.ma-stat-value'))).toBe('2modelsstale')
    expect(textOf(models.querySelector('.ma-stat-helper'))).toBe('1 of 2 stale')
    const directives = stat(host, 'directives')
    expect(textOf(directives.querySelector('.ma-stat-value'))).toBe('3directives')
    expect(textOf(directives.querySelector('.ma-stat-helper'))).toBe('2 active')
  })

  it('gives the last successful refresh as a relative time, with the date under it', () => {
    const { host } = render()
    const last = stat(host, 'last-refresh')
    // The newest success is the thermal page's model: 9 Nov 2026 06:00 UTC, six hours before now.
    expect(textOf(last.querySelector('.ma-stat-value'))).toBe('6 h ago')
    expect(textOf(last.querySelector('.ma-stat-helper'))).toMatch(/^9 Nov 2026, \d\d:\d\d$/)
    expect(host.textContent).not.toMatch(/\d{4}-\d\d-\d\dT/)
  })

  it('puts the status in a chip and never tints the number', () => {
    const { host } = render()
    for (const value of host.querySelectorAll('.ma-stat-value')) {
      expect(value.className).not.toMatch(/\b(ok|warn|err|info)\b/)
    }
    expect(chips(stat(host, 'pages'))).toEqual(['failing'])
    expect(chips(stat(host, 'models'))).toEqual(['stale'])
    expect(chips(stat(host, 'directives'))).toEqual([])
  })

  it('says a figure was not reported when its list did not load, and never 0', () => {
    use(loadedState({ tree: { isError: true, error: { message: 'boom' } } }))
    const { host } = render()
    const pages = stat(host, 'pages')
    expect(pages.querySelector('[aria-label="not reported"]')).not.toBeNull()
    expect(textOf(pages.querySelector('.ma-stat-value'))).toBe('–')
    expect(byId(host, 'ma-kn-pages-error')).not.toBeNull()
    // The mental models still list, without the pages' models among them.
    expect(allById(host, 'ma-kn-models')[0].querySelectorAll('button')).toHaveLength(2)
  })
})

describe('pages and mental models', () => {
  it('lists every model once: pages in the tree, the rest under Mental models', () => {
    const { host } = render()
    const tree = byId(host, 'ma-kn-tree')!
    expect([...tree.querySelectorAll('[data-kind="page"] .ma-kn-node-name')].map((el) => el.textContent)).toEqual([
      'Power posture',
      'Thermal envelope',
      'Operator style',
    ])
    expect([...byId(host, 'ma-kn-models')!.querySelectorAll('button')].map((b) => b.dataset.testid)).toEqual([
      'ma-kn-model-mm-preferences',
      'ma-kn-model-mm-decisions',
    ])
    expect(textOf(byId(host, 'ma-kn-pages-meta'))).toBe('3 pages, 2 stale, 1 failed')
    expect(textOf(byId(host, 'ma-kn-models-meta'))).toBe('2 models, 1 stale')
  })

  it('puts a stale or failed chip only on the rows it applies to', () => {
    const { host } = render()
    expect(chips(node(host, 'kb-thermal'))).toEqual([])
    expect(chips(node(host, 'kb-power'))).toEqual(['stale'])
    expect(chips(node(host, 'kb-style'))).toEqual(['failed', 'stale'])
    expect(chips(node(host, 'f-box'))).toEqual([])
    expect(chips(byId(host, 'ma-kn-model-mm-decisions'))).toEqual([])
    expect(chips(byId(host, 'ma-kn-model-mm-preferences'))).toEqual(['stale'])
  })

  it('says a status every row shares once, in the header, and leaves the rows bare', () => {
    use(
      loadedState({
        models: { data: { items: MODELS.map((model) => ({ ...model, is_stale: true })) } },
        tree: {
          data: {
            roots: [
              pageNode({ id: 'kb-power', name: 'Power posture', mental_model_id: 'mm-power', is_stale: true }),
              pageNode({ id: 'kb-thermal', name: 'Thermal envelope', mental_model_id: 'mm-thermal', is_stale: true }),
            ],
          },
        },
      }),
    )
    const { host } = render()
    expect(textOf(byId(host, 'ma-kn-pages-meta'))).toBe('2 pages, all stale')
    expect(chips(byId(host, 'ma-kn-tree'))).toEqual([])
  })

  it('is a tree: levels, positions, one tab stop, and folders that say whether they are open', () => {
    const { host } = render()
    const tree = byId(host, 'ma-kn-tree')!
    expect(tree.getAttribute('role')).toBe('tree')
    expect(document.getElementById(tree.getAttribute('aria-labelledby')!)!.textContent).toBe('Knowledge pages')
    const items = [...tree.querySelectorAll('[role="treeitem"]')]
    expect(items.map((el) => el.getAttribute('aria-level'))).toEqual(['1', '2', '2', '1', '2'])
    expect(node(host, 'f-box').getAttribute('aria-expanded')).toBe('true')
    expect(node(host, 'kb-power').hasAttribute('aria-expanded')).toBe(false)
    expect(items.filter((el) => el.getAttribute('tabindex') === '0')).toHaveLength(1)
    expect(textOf(node(host, 'f-box'))).toContain('2 pages')
    expect(textOf(node(host, 'f-operator'))).toContain('1 page')
  })

  it('marks the open page by more than colour: selected, and the word "open"', () => {
    const { host } = render(makeArea({ hash: `${HASH}&page=kb-thermal` }))
    const open = node(host, 'kb-thermal')
    expect(open.getAttribute('aria-selected')).toBe('true')
    expect(open.querySelector('.ma-row-open')!.textContent).toBe('open')
    expect(node(host, 'kb-power').getAttribute('aria-selected')).toBe('false')
    expect(node(host, 'kb-power').querySelector('.ma-row-open')).toBeNull()
    // The tab stop is the open page.
    expect(open.getAttribute('tabindex')).toBe('0')
  })

  it('moves through the tree with the arrow keys without opening anything', () => {
    const replaceRoute = vi.fn()
    const { host } = render(makeArea({ hash: `${HASH}&page=kb-thermal`, replaceRoute }))
    act(() => node(host, 'kb-thermal').focus())
    expect(press(node(host, 'kb-thermal'), 'ArrowUp')).toBe(false)
    expect(document.activeElement).toBe(node(host, 'kb-power'))
    press(node(host, 'kb-power'), 'ArrowUp')
    expect(document.activeElement).toBe(node(host, 'f-box'))
    press(node(host, 'f-box'), 'End')
    expect(document.activeElement).toBe(node(host, 'kb-style'))
    expect(replaceRoute).not.toHaveBeenCalled()
    // A key that is not the tree's is left to the browser.
    expect(press(node(host, 'kb-style'), 'Tab')).toBe(true)
  })

  it('closes and opens a folder from the keyboard, and opens a page with Enter', () => {
    const replaceRoute = vi.fn()
    const { host } = render(makeArea({ hash: HASH, replaceRoute }))
    act(() => node(host, 'f-box').focus())
    press(node(host, 'f-box'), 'ArrowLeft')
    expect(node(host, 'f-box').getAttribute('aria-expanded')).toBe('false')
    expect(byId(host, 'ma-kn-node-kb-power')).toBeNull()
    press(node(host, 'f-box'), 'ArrowRight')
    expect(byId(host, 'ma-kn-node-kb-power')).not.toBeNull()
    press(node(host, 'f-box'), 'ArrowDown')
    press(node(host, 'kb-power'), 'ArrowDown')
    press(node(host, 'kb-thermal'), 'Enter')
    expect(replaceRoute).toHaveBeenCalledWith({ page: 'kb-thermal' })
  })

  it('writes the page that is clicked into the address', () => {
    const replaceRoute = vi.fn()
    const { host } = render(makeArea({ hash: HASH, replaceRoute }))
    click(node(host, 'kb-style'))
    expect(replaceRoute).toHaveBeenCalledWith({ page: 'kb-style' })
    click(byId(host, 'ma-kn-model-mm-decisions')!)
    expect(replaceRoute).toHaveBeenLastCalledWith({ page: 'mm-decisions' })
  })

  it('closes what is open when the bank changes', () => {
    const replaceRoute = vi.fn()
    const { host } = render(makeArea({ hash: `${HASH}&page=kb-thermal`, replaceRoute }))
    click(host.querySelectorAll('[role="radio"]')[1])
    expect(replaceRoute).toHaveBeenCalledWith({ bank: 'Hal0::Agents', page: null })
  })
})

describe('the reader, on a page', () => {
  it('shows the first page when the address names none', () => {
    const { host } = render()
    expect(textOf(byId(host, 'ma-kn-title'))).toBe('Power posture')
    expect(byId(host, 'ma-kn-reader')!.dataset.kind).toBe('knowledge page')
  })

  it('shows the page a link names: title, question, tags, last refresh, text', () => {
    const { host } = render(makeArea({ hash: `${HASH}&page=kb-thermal` }))
    const reader = byId(host, 'ma-kn-reader')!
    expect(document.getElementById(reader.getAttribute('aria-labelledby')!)!.textContent).toBe('Thermal envelope')
    expect(textOf(byId(host, 'ma-kn-question'))).toBe('what are the thermal limits?')
    expect(textOf(byId(host, 'ma-kn-tags'))).toBe('performance')
    expect(textOf(byId(host, 'ma-kn-refreshed'))).toMatch(/^6 h ago \(9 Nov 2026, \d\d:\d\d\)$/)
    expect(chips(reader.querySelector('.ma-card-head'))).toEqual(['fresh'])
    expect(byId(host, 'ma-kn-notice')).toBeNull()
  })

  it('draws the text from its markdown, as elements and never as HTML', () => {
    use(
      loadedState({
        pages: {
          'kb-thermal': {
            data: { id: 'kb-thermal', body: '## Limits\n\nPeak is **84 C**. <img src=x onerror=alert(1)>\n\n[bad](javascript:alert(1))' },
          },
        },
      }),
    )
    const { host } = render(makeArea({ hash: `${HASH}&page=kb-thermal` }))
    const content = byId(host, 'ma-kn-content')!
    expect(content.querySelector('h4')!.textContent).toBe('Limits')
    expect(content.querySelector('strong')!.textContent).toBe('84 C')
    // The tag in the text is text; nothing in the text became an element or a link.
    expect(content.querySelector('img')).toBeNull()
    expect(content.querySelector('a')).toBeNull()
    expect(content.textContent).toContain('<img src=x onerror=alert(1)>')
  })

  it('says a stale page is stale, and what a refresh does about it', () => {
    const { host } = render(makeArea({ hash: `${HASH}&page=kb-power` }))
    const notice = byId(host, 'ma-kn-notice')!
    expect(chips(notice)).toEqual(['stale'])
    expect(textOf(notice)).toContain('Facts in its scope were saved after its last refresh.')
    expect(chips(byId(host, 'ma-kn-reader')!.querySelector('.ma-card-head'))).toEqual([])
  })

  it('says when the last refresh failed, why, and what is shown meanwhile', () => {
    const { host } = render(makeArea({ hash: `${HASH}&page=kb-style` }))
    const notice = byId(host, 'ma-kn-notice')!
    expect(chips(notice)).toEqual(['failed'])
    expect(textOf(notice)).toMatch(/The last refresh failed on 8 Nov 2026, \d\d:\d\d\./)
    expect(textOf(notice)).toContain('Shown as of 1 Nov 2026 until a refresh succeeds.')
    expect(textOf(notice)).toContain('Automatic refreshes of this page are paused until one does.')
    expect(textOf(byId(host, 'ma-kn-failure-reason'))).toBe('The engine said: APIConnectionError: Connection error.')
    expect(host.textContent).not.toMatch(/refresh_failed|unexpected_error|[{}]/)
  })

  it('asks for the history of a page only when its last refresh failed', () => {
    render(makeArea({ hash: `${HASH}&page=kb-thermal` }))
    const asked = state.asked.filter((a) => a.name === 'history')
    expect(asked.length).toBeGreaterThan(0)
    for (const a of asked) expect(a.args[2]).toEqual({ enabled: false })
  })

  it('shows a skeleton while the page loads', () => {
    use(loadedState({ pages: {} }))
    const { host } = render(makeArea({ hash: `${HASH}&page=kb-thermal` }))
    expect(textOf(byId(host, 'ma-kn-title'))).toBe('Thermal envelope')
    expect(byId(host, 'ma-kn-content')).toBeNull()
    expect(byId(host, 'ma-kn-reader')!.querySelector('[aria-busy="true"] .ma-skel')).not.toBeNull()
  })

  it('says the page did not load, with the reason and a way to try again', () => {
    const refetch = vi.fn()
    use(
      loadedState({
        pages: { 'kb-thermal': { isError: true, error: { message: '{"detail":"Page not found"}' }, refetch } },
      }),
    )
    const { host } = render(makeArea({ hash: `${HASH}&page=kb-thermal` }))
    const error = byId(host, 'ma-kn-page-error')!
    expect(textOf(error)).toContain('This page did not load')
    expect(textOf(error)).toContain('Page not found')
    expect(textOf(error)).not.toMatch(/[{}]/)
    click(error.querySelector('button')!)
    expect(refetch).toHaveBeenCalledTimes(1)
  })

  it('says a page has no text yet when no refresh has written one', () => {
    use(loadedState({ pages: { 'kb-thermal': { data: { id: 'kb-thermal', body: '', markdown: '---\nid: "x"\n---\n\nNo content yet.' } } } }))
    const { host } = render(makeArea({ hash: `${HASH}&page=kb-thermal` }))
    expect(textOf(byId(host, 'ma-kn-no-text'))).toBe('No text yet. The first refresh that succeeds writes it.')
  })

  it('says an address that matches nothing matches nothing, and opens no other page', () => {
    const { host } = render(makeArea({ hash: `${HASH}&page=kb-gone` }))
    expect(textOf(byId(host, 'ma-kn-reader-missing'))).toContain('Nothing in this bank has that address')
    expect(byId(host, 'ma-kn-title')).toBeNull()
    expect(host.querySelector('[role="treeitem"][aria-selected="true"]')).toBeNull()
  })
})

describe('editing a page', () => {
  const openForm = (host: Element) => {
    click(byId(host, 'ma-kn-edit')!)
    return byId(host, 'ma-kn-edit-form')!
  }

  it('opens a form with the question and the tags, each under a real label', () => {
    const { host } = render(makeArea({ hash: `${HASH}&page=kb-thermal` }))
    const form = openForm(host)
    expect(field(form, 'Question').value).toBe('what are the thermal limits?')
    expect(field(form, 'Tags, separated by commas').value).toBe('performance')
    expect(byId(host, 'ma-kn-edit')).toBeNull()
  })

  it("saves only what changed, to the page's model", async () => {
    const { host } = render(makeArea({ hash: `${HASH}&page=kb-thermal` }))
    const form = openForm(host)
    type(field(form, 'Question'), 'what are the thermal limits now?')
    await submit(form)
    expect(calls('modelUpdate')).toEqual([
      { bank: 'shared', id: 'mm-thermal', body: { source_query: 'what are the thermal limits now?' } },
    ])
    expect(byId(host, 'ma-kn-edit-form')).toBeNull()
    expect(textOf(byId(host, 'ma-kn-edit-saved'))).toBe(
      'Saved. Refresh the page to rewrite it from the new question and tags.',
    )
  })

  it('saves tags as a list', async () => {
    const { host } = render(makeArea({ hash: `${HASH}&page=kb-thermal` }))
    const form = openForm(host)
    type(field(form, 'Tags, separated by commas'), 'performance, thermal ,')
    await submit(form)
    expect(calls('modelUpdate')).toEqual([
      { bank: 'shared', id: 'mm-thermal', body: { tags: ['performance', 'thermal'] } },
    ])
  })

  it('closes without a request when nothing changed, and on Cancel', async () => {
    const { host } = render(makeArea({ hash: `${HASH}&page=kb-thermal` }))
    await submit(openForm(host))
    expect(byId(host, 'ma-kn-edit-form')).toBeNull()
    openForm(host)
    click(byId(host, 'ma-kn-edit-cancel')!)
    expect(byId(host, 'ma-kn-edit-form')).toBeNull()
    expect(calls('modelUpdate')).toEqual([])
    expect(textOf(byId(host, 'ma-kn-edit-saved'))).toBe('')
  })

  it('keeps the form open and says why when the save fails or the question is empty', async () => {
    state.fail.modelUpdate = new Error('{"detail":"tags must be a list"}')
    const { host } = render(makeArea({ hash: `${HASH}&page=kb-thermal` }))
    const form = openForm(host)
    type(field(form, 'Question'), '  ')
    await submit(form)
    expect(textOf(byId(host, 'ma-kn-edit-error'))).toBe('Write the question this page answers.')
    expect(calls('modelUpdate')).toEqual([])
    type(field(form, 'Question'), 'another question?')
    await submit(form)
    expect(textOf(byId(host, 'ma-kn-edit-error'))).toBe('Not saved. tags must be a list')
    expect(byId(host, 'ma-kn-edit-form')).not.toBeNull()
  })
})

describe('the reader, on a mental model', () => {
  const open = (extra = '') => render(makeArea({ hash: `${HASH}&page=mm-decisions${extra}` }))

  it('shows its text, question, tags and refresh settings in plain words', () => {
    const { host } = open()
    expect(byId(host, 'ma-kn-reader')!.dataset.kind).toBe('mental model')
    expect(textOf(byId(host, 'ma-kn-title'))).toBe('Project decisions')
    expect(textOf(byId(host, 'ma-kn-question'))).toBe('what has been decided, and why?')
    expect(textOf(byId(host, 'ma-kn-tags'))).toBe('decisions')
    expect(textOf(byId(host, 'ma-kn-trigger'))).toBe(
      'Refreshes each time new facts are consolidated. A refresh edits the existing text and leaves unchanged sections as they are. At most one automatic refresh every 1 h.',
    )
    expect(byId(host, 'ma-kn-content')!.querySelector('h4')!.textContent).toBe('Decided')
    expect(byId(host, 'ma-kn-model-mm-decisions')!.getAttribute('aria-current')).toBe('true')
    // A model is not edited here; only a page has the form.
    expect(byId(host, 'ma-kn-edit')).toBeNull()
    expect(host.textContent).not.toMatch(/refresh_after_consolidation|min_refresh_interval|delta/)
  })

  it('lists its history with dates: what each refresh replaced, a failure and its reason, the first write', () => {
    const { host } = open()
    const list = byId(host, 'ma-kn-history-list')!
    expect([...list.children].map((li) => textOf(li).replace(/, \d\d:\d\d/g, ''))).toEqual([
      'Current textopen',
      'Text replaced on 5 Nov 2026',
      'Refresh failed on 3 Nov 2026APIConnectionError: Connection error.failed',
      'Text replaced on 2 Nov 2026',
      'First written on 1 Nov 2026',
    ])
    // Only an entry with earlier text opens anything.
    expect(list.querySelectorAll('button')).toHaveLength(3)
    expect(byId(host, 'ma-kn-history-failure')!.querySelector('button')).toBeNull()
  })

  it('shows the version that is chosen, says which it is, and goes back to the current text', () => {
    const { host } = open()
    const versions = allById(host, 'ma-kn-history-version')
    click(versions[1])
    expect(textOf(byId(host, 'ma-kn-content'))).toBe('First text.')
    expect(textOf(byId(host, 'ma-kn-version-note'))).toMatch(
      /^Showing the text that was replaced on 2 Nov 2026, \d\d:\d\d, not the current text\.$/,
    )
    expect(versions[1].getAttribute('aria-current')).toBe('true')
    expect(versions[1].querySelector('.ma-row-open')!.textContent).toBe('open')
    expect(byId(host, 'ma-kn-history-current')!.getAttribute('aria-current')).toBe('false')

    click(versions[0])
    expect(textOf(byId(host, 'ma-kn-content'))).toBe('Second text.')

    click(byId(host, 'ma-kn-history-current')!)
    expect(textOf(byId(host, 'ma-kn-content'))).toContain('Updates go in before noon.')
    expect(byId(host, 'ma-kn-version-note')).toBeNull()
  })

  it('says a model with no history has none', () => {
    const { host } = render(makeArea({ hash: `${HASH}&page=mm-preferences` }))
    expect(textOf(byId(host, 'ma-kn-history-empty'))).toContain('No earlier text')
    expect(byId(host, 'ma-kn-history-list')).toBeNull()
  })

  it('shows a failed model as failed in its row and in the reader', () => {
    use(
      loadedState({
        models: {
          data: {
            items: [
              ...MODELS.filter((x) => x.id !== 'mm-preferences'),
              knowledgeModel({
                id: 'mm-preferences',
                name: 'Operator preferences',
                last_refreshed_at: null,
                last_refresh_failed_at: '2026-11-09T09:00:00+00:00',
                content: '',
              }),
            ],
          },
        },
      }),
    )
    const { host } = render(makeArea({ hash: `${HASH}&page=mm-preferences` }))
    expect(chips(byId(host, 'ma-kn-model-mm-preferences'))).toEqual(['failed'])
    expect(textOf(byId(host, 'ma-kn-model-mm-preferences'))).toContain('never refreshed · last refresh failed 3 h ago')
    expect(textOf(byId(host, 'ma-kn-notice'))).toContain('No refresh has succeeded yet.')
    expect(textOf(byId(host, 'ma-kn-refreshed'))).toBe('Never: no refresh has succeeded yet.')
    expect(textOf(byId(host, 'ma-kn-no-text'))).toContain('No text yet.')
  })
})

describe('refreshing one', () => {
  const refreshButton = (host: Element) => byId(host, 'ma-kn-refresh') as HTMLButtonElement

  it("refreshes a page through its model, says it is running, and watches for the end", async () => {
    const { host } = render(makeArea({ hash: `${HASH}&page=kb-power` }))
    expect(refreshButton(host).className).toBe('btn ghost sm')
    click(refreshButton(host))
    await flush()
    expect(calls('refresh')).toEqual([{ bank: 'shared', id: 'mm-power' }])
    expect(refreshButton(host).disabled).toBe(true)
    expect(refreshButton(host).textContent).toBe('Refreshing…')
    expect(textOf(document.getElementById(refreshButton(host).getAttribute('aria-describedby')!))).toBe(
      'A refresh is running. This updates when it finishes.',
    )
    expect(chips(byId(host, 'ma-kn-reader')!.querySelector('.ma-card-head'))).toEqual(['running'])
    expect(state.polling).toBe(true)
  })

  it('shows the request in flight before the engine has answered it', async () => {
    let release: () => void = () => {}
    state.gate.refresh = new Promise<void>((resolve) => {
      release = resolve
    })
    const { host } = render(makeArea({ hash: `${HASH}&page=kb-power` }))
    click(refreshButton(host))
    await settle()
    expect(refreshButton(host).disabled).toBe(true)
    expect(refreshButton(host).textContent).toBe('Refreshing…')
    // Not watching yet: nothing has been queued.
    expect(state.polling).toBe(false)
    release()
    await flush()
    expect(state.polling).toBe(true)
  })

  it('says the refresh finished once the model has a newer refresh, and stops watching', async () => {
    const { host, rerender } = render(makeArea({ hash: `${HASH}&page=kb-power` }))
    click(refreshButton(host))
    await flush()
    use({
      ...state,
      tree: {
        data: {
          roots: TREE.roots.map((f) => ({
            ...f,
            children: f.children.map((p) => (p.id === 'kb-power' ? { ...p, is_stale: false } : p)),
          })),
        },
      },
      models: {
        data: {
          items: MODELS.map((x) =>
            x.id === 'mm-power' ? { ...x, is_stale: false, last_refreshed_at: '2026-11-09T12:00:30+00:00' } : x,
          ),
        },
      },
    })
    rerender()
    await flush()
    expect(textOf(byId(host, 'ma-kn-run-status'))).toBe('The refresh finished.')
    expect(refreshButton(host).disabled).toBe(false)
    expect(refreshButton(host).textContent).toBe('Refresh')
    expect(chips(byId(host, 'ma-kn-reader')!.querySelector('.ma-card-head'))).toEqual(['fresh'])
    expect(byId(host, 'ma-kn-notice')).toBeNull()
    expect(state.polling).toBe(false)
  })

  it('says the refresh failed once the model has a newer failure', async () => {
    const { host, rerender } = render(makeArea({ hash: `${HASH}&page=mm-decisions` }))
    click(refreshButton(host))
    await flush()
    use({
      ...state,
      models: {
        data: {
          items: MODELS.map((x) =>
            x.id === 'mm-decisions' ? { ...x, last_refresh_failed_at: '2026-11-09T12:00:30+00:00' } : x,
          ),
        },
      },
      history: {
        'mm-decisions': {
          data: {
            items: [
              { kind: 'refresh_failed', changed_at: '2026-11-09T12:00:30+00:00', error_message: 'The model timed out. Try later.' },
            ],
          },
        },
      },
    })
    rerender()
    await flush()
    expect(textOf(byId(host, 'ma-kn-run-status'))).toBe('The refresh failed.')
    expect(chips(byId(host, 'ma-kn-notice'))).toEqual(['failed'])
    expect(textOf(byId(host, 'ma-kn-failure-reason'))).toBe('The engine said: The model timed out.')
    expect(refreshButton(host).disabled).toBe(false)
  })

  it('says the refresh did not start, with the engine\u2019s reason shortened and never as JSON', async () => {
    state.fail.refresh = new Error(
      '{"error":{"code":"memory.engine_unreachable","message":"hindsight-api is not responding. It was last seen 4 min ago.","details":{}}}',
    )
    const { host } = render(makeArea({ hash: `${HASH}&page=kb-power` }))
    click(refreshButton(host))
    await flush()
    const error = byId(host, 'ma-kn-run-error')!
    expect(error.getAttribute('role')).toBe('alert')
    expect(textOf(error)).toBe('The refresh did not start. hindsight-api is not responding.')
    expect(host.textContent).not.toMatch(/[{}]|memory\.engine_unreachable/)
    expect(refreshButton(host).disabled).toBe(false)
    expect(state.polling).toBe(false)
  })

  it('stops waiting after ten minutes with nothing changed, and points at Operations', async () => {
    const { host, rerender } = render(makeArea({ hash: `${HASH}&page=kb-power` }))
    click(refreshButton(host))
    await flush()
    vi.setSystemTime(new Date('2026-11-09T12:10:01Z'))
    // The same answers, read again.
    use({ ...state, models: { data: { items: [...MODELS] } } })
    rerender()
    await flush()
    const status = byId(host, 'ma-kn-run-status')!
    expect(textOf(status)).toContain('The refresh has not finished after 10 min. It is listed under Operations.')
    expect(status.querySelector('a')!.getAttribute('href')).toBe('#memory/operations?bank=shared')
    expect(refreshButton(host).disabled).toBe(false)
    expect(state.polling).toBe(false)
  })

  it('keeps each refresh with the thing it was started on', async () => {
    const { host, rerender } = render(makeArea({ hash: `${HASH}&page=kb-power` }))
    click(refreshButton(host))
    await flush()
    rerender(makeArea({ hash: `${HASH}&page=kb-thermal` }))
    expect(refreshButton(host).disabled).toBe(false)
    expect(refreshButton(host).textContent).toBe('Refresh')
    rerender(makeArea({ hash: `${HASH}&page=kb-power` }))
    expect(refreshButton(host).disabled).toBe(true)
  })
})

describe('Refresh all stale', () => {
  const all = (host: Element) => byId(host, 'ma-kn-refresh-all') as HTMLButtonElement

  it('is a ghost button, and says how much is stale', () => {
    const { host } = render()
    expect(all(host).className).toBe('btn ghost')
    expect(all(host).disabled).toBe(false)
    expect(textOf(byId(host, 'ma-kn-refresh-sentence'))).toContain(
      '3 of 5 knowledge pages and mental models are stale or failed their last refresh.',
    )
    // There is no primary button anywhere on the tab.
    for (const button of host.querySelectorAll('button.btn')) {
      expect(button.className, button.textContent ?? '').toMatch(/\b(ghost|danger)\b/)
    }
  })

  it('starts one refresh at a time, in order, and says how many were started', async () => {
    const { host } = render()
    click(all(host))
    await flush()
    expect(calls('refresh')).toEqual([
      { bank: 'shared', id: 'mm-power' },
      { bank: 'shared', id: 'mm-style' },
      { bank: 'shared', id: 'mm-preferences' },
    ])
    expect(textOf(byId(host, 'ma-kn-bulk-status'))).toBe(
      '3 refreshes started. Each finishes on its own, and this tab updates as they do.',
    )
    expect(state.polling).toBe(true)
  })

  it('shows a count while it runs, and cannot be started twice', async () => {
    let release: () => void = () => {}
    state.gate.refresh = new Promise<void>((resolve) => {
      release = resolve
    })
    const { host } = render()
    click(all(host))
    await settle()
    expect(textOf(byId(host, 'ma-kn-bulk-status'))).toBe('Starting refresh 1 of 3: “Power posture”.')
    expect(all(host).disabled).toBe(true)
    expect(textOf(document.getElementById(all(host).getAttribute('aria-describedby')!))).toBe(
      'Refreshes are being started.',
    )
    release()
    await flush()
    expect(calls('refresh')).toHaveLength(3)
  })

  it('stops at the first that does not start, and names it and the reason', async () => {
    const { host } = render()
    const original = state.calls.push.bind(state.calls)
    // The second request is the one that fails.
    state.calls.push = (...items) => {
      const length = original(...items)
      state.fail.refresh = length === 2 ? new Error('The queue is full. Try again later.') : undefined
      return length
    }
    click(all(host))
    await flush()
    expect(calls('refresh')).toHaveLength(2)
    const stopped = byId(host, 'ma-kn-bulk-stopped')!
    expect(stopped.getAttribute('role')).toBe('alert')
    expect(textOf(stopped)).toBe('Stopped after 1 of 3. “Operator style” did not start: The queue is full.')
    expect(textOf(byId(host, 'ma-kn-bulk-status'))).toBe('')
    expect(all(host).disabled).toBe(false)
  })

  it('is disabled, with the reason, when nothing is stale', () => {
    use(
      loadedState({
        tree: { data: { roots: [pageNode({ id: 'kb-thermal', name: 'Thermal envelope', mental_model_id: 'mm-thermal' })] } },
        models: { data: { items: MODELS.filter((x) => x.id === 'mm-thermal' || x.id === 'mm-decisions') } },
      }),
    )
    const { host } = render()
    expect(all(host).disabled).toBe(true)
    expect(textOf(document.getElementById(all(host).getAttribute('aria-describedby')!))).toBe('Nothing is stale.')
    expect(textOf(byId(host, 'ma-kn-refresh-sentence'))).toBe(
      'Every knowledge page and mental model in this bank is fresh.',
    )
  })
})

describe('when the engine stops answering', () => {
  const down = (hash = HASH) =>
    makeArea({ hash, engineQuery: { data: { ...ENGINE, reachable: false } }, banksQuery: { data: { banks: BANKS } } })

  it('keeps what was read, says the engine is not answering, and links to Health', () => {
    const { host } = render(down())
    const notice = byId(host, 'ma-kn-engine-down')!
    expect(chips(notice)).toEqual(['unreachable'])
    expect(textOf(notice)).toContain('The memory engine is not answering.')
    expect(notice.querySelector('a')!.getAttribute('href')).toBe('#memory/health?bank=shared')
    expect(byId(host, 'ma-kn-tree')).not.toBeNull()
  })

  it('disables every refresh, each with the reason on screen and tied to its button', () => {
    const { host } = render(down(`${HASH}&page=kb-power`))
    for (const id of ['ma-kn-refresh-all', 'ma-kn-refresh']) {
      const button = byId(host, id) as HTMLButtonElement
      expect(button.disabled, id).toBe(true)
      const reason = document.getElementById(button.getAttribute('aria-describedby')!)!
      expect(reason.textContent, id).toBe('The memory engine is not answering.')
      expect(host.contains(reason)).toBe(true)
    }
    click(byId(host, 'ma-kn-refresh-all')!)
    click(byId(host, 'ma-kn-refresh')!)
    expect(calls('refresh')).toEqual([])
  })

  it('disables the refresh of a mental model too', () => {
    const { host } = render(down(`${HASH}&page=mm-decisions`))
    expect((byId(host, 'ma-kn-refresh') as HTMLButtonElement).disabled).toBe(true)
  })

  it('says so on its own when nothing was read before it stopped', () => {
    use(knowledgeState())
    const { host } = render(
      makeArea({ hash: HASH, engineQuery: { isError: true }, banksQuery: { isError: true } }),
    )
    const card = byId(host, 'ma-knowledge-unreachable')!
    expect(textOf(card)).toContain('The memory engine is not answering')
    expect(card.querySelector('a')!.getAttribute('href')).toBe('#memory/health?bank=shared')
    expect(byId(host, 'ma-scope')).toBeNull()
    expect(host.textContent).not.toMatch(/\d/)
  })
})

describe('an empty bank', () => {
  const emptyState = () =>
    loadedState({
      tree: { data: { roots: [] } },
      models: { data: { items: [], total: 0 } },
      directives: { data: { items: [], total: 0 } },
    })

  it('says what belongs here and how pages come to exist', () => {
    use(emptyState())
    const { host } = render()
    const empty = byId(host, 'ma-kn-empty')!
    expect(textOf(empty)).toContain('No knowledge pages or mental models in shared yet')
    expect(textOf(empty)).toContain("documents the engine writes from this bank's facts")
    expect(textOf(empty)).toContain('hal0 memory spec apply')
    expect(byId(host, 'ma-kn-tree')).toBeNull()
    expect(byId(host, 'ma-kn-reader')).toBeNull()
    // The counts are real zeros here: the lists loaded and are empty.
    expect(textOf(stat(host, 'pages').querySelector('.ma-stat-value'))).toBe('0pages')
    expect(stat(host, 'last-refresh').querySelector('[aria-label="not reported"]')).not.toBeNull()
    expect(textOf(byId(host, 'ma-kn-directives-empty'))).toContain('No directives for this bank')
  })

  it('offers one thing to do: add a mental model', async () => {
    use(emptyState())
    const { host } = render()
    const empty = byId(host, 'ma-kn-empty')!
    expect(empty.querySelectorAll('button')).toHaveLength(1)
    click(byId(empty, 'ma-kn-model-add')!)
    const form = byId(host, 'ma-kn-model-form')!
    type(field(form, 'Name'), ' Decisions ')
    type(field(form, 'Question'), 'what was decided?')
    await submit(form)
    expect(calls('modelCreate')).toEqual([
      { bank: 'shared', body: { name: 'Decisions', source_query: 'what was decided?' } },
    ])
    expect(byId(host, 'ma-kn-model-form')).toBeNull()
    expect(textOf(byId(empty, 'ma-kn-model-added'))).toContain('Added.')
  })

  it('keeps the lists when only one of them is empty', () => {
    use(loadedState({ models: { data: { items: MODELS.filter((x) => x.is_knowledge_page) } } }))
    const { host } = render()
    expect(byId(host, 'ma-kn-empty')).toBeNull()
    expect(textOf(byId(host, 'ma-kn-models-empty'))).toContain('No mental models besides the knowledge pages.')
    expect(byId(host, 'ma-kn-tree')).not.toBeNull()
  })
})

describe('adding a mental model', () => {
  it('needs a name and a question, and says which is missing', async () => {
    const { host } = render()
    click(byId(host, 'ma-kn-model-add')!)
    const form = byId(host, 'ma-kn-model-form')!
    await submit(form)
    expect(textOf(byId(host, 'ma-kn-model-form-error'))).toBe('Give the model a name.')
    type(field(form, 'Name'), 'Decisions')
    await submit(form)
    expect(textOf(byId(host, 'ma-kn-model-form-error'))).toBe('Write the question it answers.')
    expect(calls('modelCreate')).toEqual([])
  })
})

describe('directives', () => {
  const row = (host: Element, id: string) => byId(host, `ma-kn-directive-${id}`)!

  it('lists each with its name, text, priority and whether it is active, highest priority first', () => {
    const { host } = render()
    const rows = [...byId(host, 'ma-kn-directives-list')!.children]
    expect(rows.map((li) => textOf(byId(li, 'ma-kn-directive-name')))).toEqual([
      'Keep it terse',
      'Guard disk headroom',
      'Always cite sources',
    ])
    expect(textOf(byId(rows[0], 'ma-kn-directive-text'))).toBe('Default to terse responses.')
    expect(textOf(byId(rows[0], 'ma-kn-directive-priority'))).toBe('priority 3')
    expect(rows.map((li) => (byId(li, 'ma-kn-directive-active') as HTMLInputElement).checked)).toEqual([
      true,
      false,
      true,
    ])
    expect(textOf(byId(host, 'ma-kn-directives-meta'))).toBe('shared · 3 directives, 2 active')
    expect(host.textContent).not.toMatch(/managed/i)
  })

  it('has a real checkbox under a real label for the active switch, named for its directive', () => {
    const { host } = render()
    const box = byId(row(host, 'dir-cite'), 'ma-kn-directive-active') as HTMLInputElement
    expect(box.type).toBe('checkbox')
    expect(box.closest('label')).not.toBeNull()
    expect(box.getAttribute('aria-label')).toBe('Active: Always cite sources')
  })

  it('switches a directive off and on', async () => {
    const { host } = render()
    click(byId(row(host, 'dir-cite'), 'ma-kn-directive-active')!)
    click(byId(row(host, 'dir-disk'), 'ma-kn-directive-active')!)
    await flush()
    expect(calls('directiveUpdate')).toEqual([
      { bank: 'shared', id: 'dir-cite', body: { is_active: false } },
      { bank: 'shared', id: 'dir-disk', body: { is_active: true } },
    ])
  })

  it('adds one with the three fields the engine takes', async () => {
    const { host } = render()
    click(byId(host, 'ma-kn-directive-add')!)
    const form = byId(host, 'ma-kn-directive-add-form')!
    expect([...form.querySelectorAll('label')].map((l) => textOf(l))).toEqual(['Name', 'Text given to agents', 'Priority'])
    type(field(form, 'Name'), 'No secrets')
    type(field(form, 'Text given to agents'), 'Never repeat a password or a key.')
    type(field(form, 'Priority'), '5')
    expect(textOf(byId(form, 'ma-kn-directive-save'))).toBe('Add the directive')
    await submit(form)
    expect(calls('directiveCreate')).toEqual([
      { bank: 'shared', body: { name: 'No secrets', content: 'Never repeat a password or a key.', priority: 5 } },
    ])
    expect(byId(host, 'ma-kn-directive-add-form')).toBeNull()
  })

  it('does not send a directive that is missing something, and says what', async () => {
    const { host } = render()
    click(byId(host, 'ma-kn-directive-add')!)
    const form = byId(host, 'ma-kn-directive-add-form')!
    await submit(form)
    expect(textOf(byId(host, 'ma-kn-directive-form-error'))).toBe('Give the directive a name.')
    type(field(form, 'Name'), 'n')
    type(field(form, 'Text given to agents'), 't')
    type(field(form, 'Priority'), '1.5')
    await submit(form)
    expect(textOf(byId(host, 'ma-kn-directive-form-error'))).toBe('Priority is a whole number.')
    expect(calls('directiveCreate')).toEqual([])
  })

  it('edits one in place, starting from what it holds', async () => {
    const { host } = render()
    click(byId(row(host, 'dir-terse'), 'ma-kn-directive-edit')!)
    const form = byId(row(host, 'dir-terse'), 'ma-kn-directive-edit-form')!
    expect(field(form, 'Name').value).toBe('Keep it terse')
    expect(field(form, 'Text given to agents').value).toBe('Default to terse responses.')
    expect(field(form, 'Priority').value).toBe('3')
    type(field(form, 'Text given to agents'), 'Be brief.')
    type(field(form, 'Priority'), '4')
    await submit(form)
    expect(calls('directiveUpdate')).toEqual([
      { bank: 'shared', id: 'dir-terse', body: { name: 'Keep it terse', content: 'Be brief.', priority: 4 } },
    ])
    expect(byId(host, 'ma-kn-directive-edit-form')).toBeNull()
  })

  it('leaves an edit with Cancel, changing nothing', () => {
    const { host } = render()
    click(byId(row(host, 'dir-terse'), 'ma-kn-directive-edit')!)
    const form = byId(host, 'ma-kn-directive-edit-form')!
    const cancel = [...form.querySelectorAll('button')].find((b) => b.textContent === 'Cancel')!
    click(cancel)
    expect(byId(host, 'ma-kn-directive-edit-form')).toBeNull()
    expect(calls('directiveUpdate')).toEqual([])
  })

  it('asks before it deletes: the confirm button names the directive, the other keeps it', () => {
    const { host } = render()
    click(byId(row(host, 'dir-terse'), 'ma-kn-directive-delete')!)
    const confirm = byId(row(host, 'dir-terse'), 'ma-kn-directive-confirm')!
    expect(textOf(confirm.querySelector('h3'))).toBe('Delete the directive “Keep it terse”?')
    expect(textOf(confirm)).toContain('This cannot be undone.')
    const [danger, keep] = [...confirm.querySelectorAll('button')]
    expect(textOf(danger)).toBe('Delete the directive “Keep it terse”')
    expect(danger.className).toBe('btn danger')
    expect(textOf(keep)).toBe('Keep it')
    // Focus starts on the safe choice, and nothing was deleted by asking.
    expect(document.activeElement).toBe(keep)
    expect(calls('directiveDelete')).toEqual([])
    // The row's own buttons give way to the question.
    expect(byId(row(host, 'dir-terse'), 'ma-kn-directive-delete')).toBeNull()
  })

  it('keeps the directive on "Keep it" and on Escape', () => {
    const { host } = render()
    click(byId(row(host, 'dir-terse'), 'ma-kn-directive-delete')!)
    const keep = [...byId(host, 'ma-kn-directive-confirm')!.querySelectorAll('button')][1]
    click(keep)
    expect(byId(host, 'ma-kn-directive-confirm')).toBeNull()
    click(byId(row(host, 'dir-terse'), 'ma-kn-directive-delete')!)
    press(byId(host, 'ma-kn-directive-confirm')!, 'Escape')
    expect(byId(host, 'ma-kn-directive-confirm')).toBeNull()
    expect(calls('directiveDelete')).toEqual([])
  })

  it('deletes on the confirm button', async () => {
    const { host } = render()
    click(byId(row(host, 'dir-terse'), 'ma-kn-directive-delete')!)
    click(byId(host, 'ma-kn-directive-confirm')!.querySelector('button.danger')!)
    await flush()
    expect(calls('directiveDelete')).toEqual([{ bank: 'shared', id: 'dir-terse' }])
    // The row goes at once, before the list is read again: the question cannot be answered twice.
    expect(byId(host, 'ma-kn-directive-dir-terse')).toBeNull()
    expect(byId(host, 'ma-kn-directive-confirm')).toBeNull()
  })

  it('says a delete did not happen, and why', async () => {
    state.fail.directiveDelete = new Error('{"detail":"Directive is locked"}')
    const { host } = render()
    click(byId(row(host, 'dir-terse'), 'ma-kn-directive-delete')!)
    click(byId(host, 'ma-kn-directive-confirm')!.querySelector('button.danger')!)
    await flush()
    expect(textOf(byId(row(host, 'dir-terse'), 'ma-kn-directive-error'))).toBe('Not deleted. Directive is locked')
    expect(byId(host, 'ma-kn-directive-confirm')).toBeNull()
  })

  it('says the directives did not load, on their own card', () => {
    use(loadedState({ directives: { isError: true, error: { message: 'timeout' } } }))
    const { host } = render()
    expect(textOf(byId(host, 'ma-kn-directives-error'))).toContain('The directives did not load')
    expect(stat(host, 'directives').querySelector('[aria-label="not reported"]')).not.toBeNull()
    // The rest of the tab is unaffected.
    expect(byId(host, 'ma-kn-tree')).not.toBeNull()
  })
})

describe('the refresh interval', () => {
  const select = (host: Element) => byId(host, 'ma-kn-interval-select') as HTMLButtonElement
  const choose = async (host: Element, seconds: number) => {
    click(select(host))
    click(host.querySelector(`[data-option-id="${seconds}"]`)!)
    await flush()
  }

  it('is labelled in words, and shows the bank\u2019s value', () => {
    const { host } = render()
    const box = byId(host, 'ma-kn-interval')!
    expect(textOf(box.querySelector('.ma-eyebrow'))).toBe('Refresh at most every')
    expect(select(host).getAttribute('aria-label')).toBe('Refresh at most every')
    expect(textOf(select(host))).toContain('4 h')
    expect(host.textContent).not.toMatch(/mental_model_min_refresh_interval_seconds|14400/)
  })

  it('offers the five choices', () => {
    const { host } = render()
    click(select(host))
    expect([...host.querySelectorAll('[role="option"]')].map((o) => textOf(o))).toEqual([
      'time facts are consolidated',
      '1 h',
      '4 h',
      '12 h',
      '24 h',
    ])
  })

  it('saves the choice at once, to the bank\u2019s config, and says it saved', async () => {
    const { host } = render()
    await choose(host, 3600)
    expect(calls('configUpdate')).toEqual([
      { bank: 'shared', updates: { mental_model_min_refresh_interval_seconds: 3600 } },
    ])
    const status = byId(host, 'ma-kn-interval-status')!
    expect(status.getAttribute('role')).toBe('status')
    expect(textOf(status)).toBe('Saved.')
  })

  it('saves no limit as 0', async () => {
    const { host } = render()
    await choose(host, 0)
    expect(calls('configUpdate')).toEqual([
      { bank: 'shared', updates: { mental_model_min_refresh_interval_seconds: 0 } },
    ])
  })

  it('does not save the value it already has', async () => {
    const { host } = render()
    await choose(host, 14400)
    expect(calls('configUpdate')).toEqual([])
  })

  it('says a save failed, and why', async () => {
    state.fail.configUpdate = new Error('{"error":{"message":"The bank is read-only."}}')
    const { host } = render()
    await choose(host, 3600)
    expect(textOf(byId(host, 'ma-kn-interval-error'))).toBe('Not saved. The bank is read-only.')
    expect(textOf(byId(host, 'ma-kn-interval-status'))).toBe('')
  })

  it('shows a skeleton while the setting loads, and no value', () => {
    use(loadedState({ config: {} }))
    const { host } = render()
    expect(byId(host, 'ma-kn-interval')!.querySelector('.ma-skel')).not.toBeNull()
    expect(byId(host, 'ma-kn-interval-select')).toBeNull()
  })

  it('is disabled, with the reason, when the setting did not load', () => {
    use(loadedState({ config: { data: {} as never } }))
    const { host } = render()
    expect(select(host).disabled).toBe(true)
    expect(textOf(byId(host, 'ma-kn-interval-reason'))).toBe(
      "The bank's settings did not load, so this cannot be shown or changed.",
    )
  })

  it('is disabled, with the reason, when the engine is not answering', () => {
    const { host } = render(
      makeArea({ hash: HASH, engineQuery: { data: { ...ENGINE, reachable: false } }, banksQuery: { data: { banks: BANKS } } }),
    )
    expect(select(host).disabled).toBe(true)
    expect(textOf(byId(host, 'ma-kn-interval-reason'))).toBe('The memory engine is not answering.')
  })
})

describe('which bank', () => {
  it('reads everything for the bank in the address', () => {
    render(makeArea({ hash: '#memory/knowledge?bank=Hal0::Agents' }))
    for (const name of ['tree', 'models', 'directives', 'config']) {
      const asked = state.asked.filter((a) => a.name === name)
      expect(asked.length, name).toBeGreaterThan(0)
      for (const a of asked) expect(a.args[0], name).toBe('Hal0::Agents')
    }
  })

  it('shows the real banks in the scope bar, ids verbatim, and no project control', () => {
    const { host } = render()
    expect([...host.querySelectorAll('[role="radio"]')].map((r) => r.textContent)).toEqual(BANKS.map((b) => b.bank_id))
    expect(host.querySelector('[data-testid="ma-scope-project"]')).toBeNull()
  })

  it('writes the first bank into an address that names none, and shows nothing until it has', () => {
    const replaceRoute = vi.fn()
    const { host } = render(makeArea({ hash: '#memory/knowledge', replaceRoute }))
    expect(replaceRoute).toHaveBeenCalledTimes(1)
    expect(replaceRoute).toHaveBeenCalledWith({ bank: 'shared' })
    expect(byId(host, 'ma-kn-summary')).toBeNull()
    expect(state.asked).toEqual([])
  })

  it('says a bank the engine does not list is not listed, and shows no other bank\u2019s knowledge', () => {
    const { host } = render(makeArea({ hash: '#memory/knowledge?bank=gone' }))
    expect(textOf(byId(host, 'ma-knowledge-unknown-bank'))).toContain("This bank is not in the engine's list")
    expect(byId(host, 'ma-kn-tree')).toBeNull()
    expect(state.asked).toEqual([])
  })

  it('says there are no banks yet', () => {
    const { host } = render(makeArea({ hash: '#memory/knowledge', banksQuery: { data: { banks: [] } } }))
    expect(textOf(byId(host, 'ma-knowledge-no-banks'))).toContain('No banks yet')
  })

  it('no longer sends anyone to the legacy tools page', () => {
    const { host } = render()
    const hrefs = [...host.querySelectorAll('a')].map((a) => a.getAttribute('href'))
    expect(hrefs.filter((href) => href?.includes('memworkspace'))).toEqual([])
    expect(host.textContent).not.toMatch(/legacy tools/i)
  })
})
