import { describe, expect, it } from 'vitest'
import type { BankStats } from '@/api/hooks/useHindsight'
import { bankFigures, chosenBank } from '../memory-pure'

const stats = (over: Partial<BankStats> = {}): BankStats => ({
  bank_id: 'hal0-mono',
  total_nodes: 1284,
  total_links: 5120,
  total_documents: 40,
  nodes_by_fact_type: {},
  links_by_link_type: {},
  pending_operations: 3,
  failed_operations: 0,
  operations_by_status: {},
  last_consolidated_at: '2026-10-29T10:00:00Z',
  pending_consolidation: 0,
  failed_consolidation: 0,
  total_observations: 9876,
  ...over,
})

describe('chosenBank', () => {
  const banks = ['alpha', 'hal0-mono', 'zeta']
  it('stored bank wins when it exists', () => {
    expect(chosenBank(banks, 'zeta', '#memory/alpha')).toBe('zeta')
  })
  it('ignores a stored bank that is not in the list', () => {
    expect(chosenBank(banks, 'gone', '#memory/hal0-mono')).toBe('hal0-mono')
  })
  it('hash wins over the first bank', () => {
    expect(chosenBank(banks, null, '#memory/hal0-mono')).toBe('hal0-mono')
  })
  it('reads the ?bank= deep link the memory pane writes', () => {
    expect(chosenBank(banks, null, '#memory/bank?bank=zeta')).toBe('zeta')
  })
  it('falls back to the first bank', () => {
    expect(chosenBank(banks, null, '#overview')).toBe('alpha')
  })
  it('is null with no banks', () => {
    expect(chosenBank([], 'alpha', '#memory/alpha')).toBeNull()
  })
})

describe('bankFigures', () => {
  const NOW = Date.parse('2026-10-29T12:00:00Z') / 1000
  it('groups thousands', () => {
    expect(bankFigures(stats(), NOW)).toEqual({
      nodes: '1,284',
      links: '5,120',
      obs: '9,876',
      pending: 3,
      consolidated: '2 h ago',
    })
  })
  it('dashes figures it cannot read and never invents zeros', () => {
    expect(bankFigures(undefined, NOW)).toEqual({ nodes: '—', links: '—', obs: '—', pending: null, consolidated: null })
  })
  it('consolidated is null for a bank never consolidated or an unparsable stamp', () => {
    expect(bankFigures(stats({ last_consolidated_at: null }), NOW).consolidated).toBeNull()
    expect(bankFigures(stats({ last_consolidated_at: 'nope' }), NOW).consolidated).toBeNull()
  })
})
