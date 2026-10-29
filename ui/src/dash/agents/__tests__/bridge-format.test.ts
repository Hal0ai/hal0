// Pure helpers behind the bridge surfaces (bridge-format.ts).
import { describe, expect, it } from 'vitest'

import {
  BRIDGE_GROUP_CHIP,
  bridgeNameError,
  bridgeRunGroup,
  bridgeRunSeconds,
  bridgeShortRunId,
  bridgeStatusLabel,
  fmtAgo,
  fmtSpan,
} from '../bridge-format'

describe('bridgeNameError', () => {
  it.each(['a', 'laptop', 'ci-bot', 'dev_box_2', '0day', 'a'.repeat(32)])('accepts %s', (n) => {
    expect(bridgeNameError(n)).toBeNull()
  })

  it.each(['', 'Laptop', '-lead', '_lead', 'has space', 'dot.name', 'a'.repeat(33)])('refuses %j', (n) => {
    expect(bridgeNameError(n)).toBe(
      `invalid bridge client name '${n}': use 1-32 characters from a-z, 0-9, '_' and '-', starting with a letter or digit`,
    )
  })
})

describe('bridgeRunGroup', () => {
  it('groups every status the API names', () => {
    const groups: Record<string, string[]> = {
      live: ['queued', 'running', 'stopping'],
      waiting: ['waiting_for_approval', 'waiting_for_clarification'],
      ok: ['completed'],
      // `unknown` is terminal in the backend (store.py TERMINAL_STATUSES), published as run_failed
      bad: ['failed', 'cancelled', 'interrupted', 'unknown'],
    }
    for (const [group, statuses] of Object.entries(groups)) {
      for (const s of statuses) expect(bridgeRunGroup(s)).toBe(group)
    }
  })

  it('reads a status it has never heard of as waiting, since that wants a look', () => {
    expect(bridgeRunGroup('teleporting')).toBe('waiting')
  })

  it('has a chip colour for each group', () => {
    expect(BRIDGE_GROUP_CHIP).toEqual({ live: 'info', waiting: 'warn', ok: 'ok', bad: 'err' })
  })
})

describe('labels', () => {
  it('spaces a status and shortens a run id', () => {
    expect(bridgeStatusLabel('waiting_for_clarification')).toBe('waiting for clarification')
    expect(bridgeShortRunId('run_0123456789abcdef')).toBe('run_01234567')
    expect(bridgeShortRunId('short')).toBe('short')
  })
})

describe('time', () => {
  const now = 1_800_000_000_000
  const at = (agoSec: number) => now / 1000 - agoSec

  it('fmtAgo', () => {
    expect(fmtAgo(null, now)).toBe('')
    expect(fmtAgo(at(1), now)).toBe('just now')
    expect(fmtAgo(at(42), now)).toBe('42s ago')
    expect(fmtAgo(at(300), now)).toBe('5m ago')
    expect(fmtAgo(at(3 * 3600 + 5), now)).toBe('3h ago')
    expect(fmtAgo(at(2 * 86400), now)).toBe('2d ago')
    // a clock a little behind the server's never reads as the future
    expect(fmtAgo(at(-30), now)).toBe('just now')
  })

  it('fmtSpan', () => {
    expect(fmtSpan(0)).toBe('0s')
    expect(fmtSpan(42)).toBe('42s')
    expect(fmtSpan(125)).toBe('2m 5s')
    expect(fmtSpan(3600 + 5 * 60 + 9)).toBe('1h 5m')
  })

  it('bridgeRunSeconds stops at updated_at once a run is terminal', () => {
    const base = { created_at: now / 1000 - 600, updated_at: now / 1000 - 500 }
    expect(bridgeRunSeconds({ ...base, status: 'completed' }, now)).toBe(100)
    expect(bridgeRunSeconds({ ...base, status: 'running' }, now)).toBe(600)
    // unknown is terminal in the backend, so its clock stops too
    expect(bridgeRunSeconds({ ...base, status: 'unknown' }, now)).toBe(100)
  })
})
