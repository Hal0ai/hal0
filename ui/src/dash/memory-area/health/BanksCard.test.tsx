// @vitest-environment happy-dom
//
// The banks table of Memory ▸ Health: every column present and labelled for the stacked layout a
// phone gets, and a long bank id kept in full.
import { createElement } from 'react'
import { afterEach, describe, expect, it } from 'vitest'
import { BanksCard } from './BanksCard.jsx'
import { cleanup, mount } from '../shared/test-harness'

const LONG = 'coding-agent::ultrareview-1.4-very-long-bank-identifier'
const rows = [
  { id: LONG, facts: 1200, documents: 40, waiting: 3, failed: 1, reported: true },
  { id: 'hal0:shared', facts: 10, documents: null, waiting: null, failed: null, reported: false },
]
const totals = { banks: 2, facts: 1210 }

afterEach(cleanup)

describe('BanksCard', () => {
  it('marks the table for the stacked layout and labels every figure cell', () => {
    const { host } = mount(createElement(BanksCard, { rows, totals }))
    const table = host.querySelector('[data-testid="ma-health-banks"]')!
    expect(table.classList.contains('ma-table-stack')).toBe(true)
    const first = table.querySelector('tbody tr')!
    expect([...first.querySelectorAll('td')].map((td) => td.getAttribute('data-label'))).toEqual([
      'Facts',
      'Documents',
      'Waiting',
      'Failed',
    ])
  })

  it('keeps table semantics when stacked: explicit roles and a label element in every figure cell', () => {
    const { host } = mount(createElement(BanksCard, { rows, totals }))
    const table = host.querySelector('[data-testid="ma-health-banks"]')!
    expect(table.getAttribute('role')).toBe('table')
    expect([...table.querySelectorAll('thead, tbody')].map((el) => el.getAttribute('role'))).toEqual(['rowgroup', 'rowgroup'])
    expect([...table.querySelectorAll('tr')].map((el) => el.getAttribute('role'))).toEqual(['row', 'row', 'row'])
    expect([...table.querySelectorAll('thead th')].map((el) => el.getAttribute('role'))).toEqual(Array(5).fill('columnheader'))
    const first = table.querySelector('tbody tr')!
    expect(first.querySelector('th')!.getAttribute('role')).toBe('rowheader')
    expect([...first.querySelectorAll('td')].map((el) => el.getAttribute('role'))).toEqual(Array(4).fill('cell'))
    expect([...first.querySelectorAll('td .ma-cell-label')].map((el) => el.textContent)).toEqual([
      'Facts: ',
      'Documents: ',
      'Waiting: ',
      'Failed: ',
    ])
  })

  it('keeps a long bank id whole, as the link text and its title', () => {
    const { host } = mount(createElement(BanksCard, { rows, totals }))
    const link = host.querySelector<HTMLAnchorElement>('tbody tr th a')!
    expect(link.textContent).toBe(LONG)
    expect(link.title).toBe(LONG)
  })
})
