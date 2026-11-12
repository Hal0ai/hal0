// @vitest-environment happy-dom
import { createElement } from 'react'
import { afterEach, describe, expect, it, vi } from 'vitest'
import { SEGMENT_LIMIT, ScopeBar } from './ScopeBar.jsx'
import { cleanup, click, mount } from './test-harness'

afterEach(cleanup)

const banksOf = (n: number) => Array.from({ length: n }, (_, i) => ({ id: `Hal0::Bank.${i + 1}` }))
const combo = (host: HTMLElement, name: string) =>
  host.querySelector<HTMLButtonElement>(`[role="combobox"][aria-label="${name}"]`)

describe('ScopeBar', () => {
  it('is a group named Scope', () => {
    const { host } = mount(createElement(ScopeBar, { banks: banksOf(2), bank: 'Hal0::Bank.1', onBankChange: () => {} }))
    const group = host.querySelector('[role="group"]')!
    expect(group.getAttribute('aria-label')).toBe('Scope')
  })

  it(`draws the banks as segments while there are ${SEGMENT_LIMIT} or fewer`, () => {
    const onBankChange = vi.fn()
    const { host } = mount(
      createElement(ScopeBar, { banks: banksOf(SEGMENT_LIMIT), bank: 'Hal0::Bank.2', onBankChange }),
    )
    const group = host.querySelector('[role="radiogroup"]')!
    // Named by the visible "Bank" label.
    const label = host.ownerDocument.getElementById(group.getAttribute('aria-labelledby')!)!
    expect(label.textContent).toBe('Bank')
    const radios = [...group.querySelectorAll<HTMLButtonElement>('[role="radio"]')]
    expect(radios).toHaveLength(SEGMENT_LIMIT)
    // The id is shown verbatim, case and punctuation kept.
    expect(radios[1].textContent).toBe('Hal0::Bank.2')
    expect(radios[1].getAttribute('aria-checked')).toBe('true')
    expect(combo(host, 'Bank')).toBeNull()

    click(radios[4])
    expect(onBankChange).toHaveBeenCalledWith('Hal0::Bank.5')
  })

  it('switches to a select past six banks', () => {
    const onBankChange = vi.fn()
    const { host } = mount(
      createElement(ScopeBar, { banks: banksOf(SEGMENT_LIMIT + 1), bank: 'Hal0::Bank.3', onBankChange }),
    )
    expect(host.querySelector('[role="radiogroup"]')).toBeNull()
    const trigger = combo(host, 'Bank')!
    expect(trigger.textContent).toContain('Hal0::Bank.3')

    click(trigger)
    const options = [...host.querySelectorAll<HTMLElement>('[role="option"]')]
    expect(options).toHaveLength(SEGMENT_LIMIT + 1)
    click(options[6])
    expect(onBankChange).toHaveBeenCalledWith('Hal0::Bank.7')
  })

  it('shows a bank label and count when given', () => {
    const { host } = mount(
      createElement(ScopeBar, {
        banks: [{ id: 'a', label: 'hal0:shared', count: 52955 }, { id: 'b' }],
        bank: 'a',
        onBankChange: () => {},
      }),
    )
    const radios = [...host.querySelectorAll('[role="radio"]')]
    expect(radios[0].textContent).toBe('hal0:shared52,955')
    expect(radios[1].textContent).toBe('b')
  })

  it.each([[undefined], [null], [[]]])('renders no project control when projects is %j', (projects) => {
    const { host } = mount(
      createElement(ScopeBar, {
        banks: banksOf(2),
        bank: 'Hal0::Bank.1',
        onBankChange: () => {},
        projects: projects as never,
      }),
    )
    expect(combo(host, 'Project')).toBeNull()
    expect(host.textContent).not.toContain('Project')
    expect(host.querySelectorAll('.ma-scope-field')).toHaveLength(1)
  })

  it('offers All projects first, and reports it as null', () => {
    const onProjectChange = vi.fn()
    const { host } = mount(
      createElement(ScopeBar, {
        banks: banksOf(2),
        bank: 'Hal0::Bank.1',
        onBankChange: () => {},
        projects: [{ id: 'hal0' }, { id: 'hal0-web' }],
        project: 'hal0-web',
        onProjectChange,
      }),
    )
    const trigger = combo(host, 'Project')!
    expect(trigger.textContent).toContain('hal0-web')

    click(trigger)
    const options = [...host.querySelectorAll<HTMLElement>('[role="option"]')]
    expect(options.map((o) => o.textContent)).toEqual(['All projects', 'hal0', 'hal0-web'])
    click(options[1])
    expect(onProjectChange).toHaveBeenLastCalledWith('hal0')

    click(combo(host, 'Project')!)
    click(host.querySelectorAll<HTMLElement>('[role="option"]')[0])
    expect(onProjectChange).toHaveBeenLastCalledWith(null)
  })

  it('shows All projects when no project is chosen', () => {
    const { host } = mount(
      createElement(ScopeBar, {
        banks: banksOf(2),
        bank: 'Hal0::Bank.1',
        onBankChange: () => {},
        projects: [{ id: 'hal0' }],
        project: null,
      }),
    )
    expect(combo(host, 'Project')!.textContent).toContain('All projects')
  })
})
