import { describe, expect, it } from 'vitest'
import {
  componentCell,
  componentForService,
  pendingCount,
  RETRYABLE_STATUSES,
} from '../components-pure'

const rows = [
  { id: 'openwebui', service_id: 'openwebui', installed: 'sha256:aaa', pinned: 'sha256:bbb', pin_label: '0.6.9', status: 'pending' },
  { id: 'hindsight', service_id: 'hindsight', installed: '0.9.2', pinned: '0.9.2', status: 'converged' },
  { id: 'hermes', service_id: 'hermes', installed: null, pinned: 'v2026.7.7.2', status: 'not-installed' },
  { id: 'runner-images', service_id: null, installed: '8 images', pinned: '8 images', status: 'rolled_back', error: 'x' },
]

describe('componentForService', () => {
  it('joins by service_id', () => {
    expect(componentForService(rows, 'hindsight')?.id).toBe('hindsight')
    expect(componentForService(rows, 'comfyui')).toBeUndefined()
  })
})

describe('componentCell', () => {
  it('converged renders version + ok tone', () => {
    expect(componentCell(rows[1])).toEqual({ label: '0.9.2 ✓', tone: 'ok' })
  })
  it('pending renders arrow with labels when present', () => {
    const cell = componentCell(rows[0])
    expect(cell.tone).toBe('pending')
    expect(cell.label).toContain('0.6.9')
  })
  it('failure statuses render failed tone', () => {
    expect(componentCell(rows[3]).tone).toBe('failed')
  })
  it('not-installed is muted', () => {
    expect(componentCell(rows[2]).tone).toBe('muted')
  })
})

describe('pendingCount', () => {
  it('counts pending + failed rows', () => {
    expect(pendingCount(rows)).toBe(2)
  })
})

describe('RETRYABLE_STATUSES', () => {
  it('retry only on failures, never on pending (spec §4)', () => {
    expect(RETRYABLE_STATUSES).toEqual(['build_failed', 'snapshot_failed', 'rolled_back'])
  })
})
