// Unit coverage for the model drawer's launch-flags tune helpers
// (flags-tune.js) — previously zero direct unit tests despite being pure,
// dependency-free logic that gates Save on every model/profile flags edit.
//
// Written as a real vitest spec (not the sibling `__tests__/*.test.mjs`
// files' plain-node-script style) so it's actually picked up by
// `vitest run` — vitest.config.ts's `include` is `src/**/*.test.ts` only,
// which the `.mjs` files under this same directory do NOT match; they run
// (if at all) via `node <file>.mjs` directly per their own header comments.

import { describe, expect, it } from 'vitest'

import {
  MANAGED_FLAGS,
  SLOT_HARDWARE_FLAGS,
  diffFlags,
  findManagedFlags,
  findSlotHardwareFlags,
  flagsEquivalent,
  highlightSegments,
  isFlagToken,
  tokenizeFlags,
} from '../flags-tune.js'

describe('isFlagToken', () => {
  it('accepts long flags with real content', () => {
    expect(isFlagToken('--threads')).toBe(true)
    expect(isFlagToken('--ctx-size')).toBe(true)
  })
  it('rejects a bare "--" (no flag name)', () => {
    expect(isFlagToken('--')).toBe(false)
  })
  it('accepts short letter flags', () => {
    expect(isFlagToken('-t')).toBe(true)
    expect(isFlagToken('-ngl')).toBe(true)
  })
  it('rejects a negative number (leading "-" before a digit is a value)', () => {
    expect(isFlagToken('-1')).toBe(false)
    expect(isFlagToken('-999')).toBe(false)
  })
  it('rejects non-strings and empty input', () => {
    expect(isFlagToken('')).toBe(false)
    // Deliberately wrong type — mirrors defensive runtime callers.
    expect(isFlagToken(undefined as unknown as string)).toBe(false)
  })
})

describe('tokenizeFlags', () => {
  it('splits on whitespace', () => {
    expect(tokenizeFlags('-fa on -b 2048').tokens).toEqual(['-fa', 'on', '-b', '2048'])
  })
  it('collapses repeated whitespace and trims', () => {
    expect(tokenizeFlags('  -fa   on  ').tokens).toEqual(['-fa', 'on'])
  })
  it('keeps a single-quoted JSON value as one token, preserving inner double quotes (no backslash-escape support — this is the documented usage: wrap in the OTHER quote style)', () => {
    expect(
      tokenizeFlags(`--chat-template-kwargs '{"enable_thinking":false}'`).tokens,
    ).toEqual(['--chat-template-kwargs', '{"enable_thinking":false}'])
  })
  it('keeps a single-quoted value as one token', () => {
    expect(tokenizeFlags("--alias 'my model'").tokens).toEqual(['--alias', 'my model'])
  })
  it('reports an unbalanced quote as an error, without throwing', () => {
    const { tokens, error } = tokenizeFlags('-fa on "unterminated')
    expect(error).toMatch(/unbalanced quote/i)
    // Whatever was accumulated before the run-off is discarded, not half-emitted.
    expect(tokens).toEqual(['-fa', 'on'])
  })
  it('empty / whitespace-only input tokenizes to nothing, no error', () => {
    expect(tokenizeFlags('').tokens).toEqual([])
    expect(tokenizeFlags('   ').tokens).toEqual([])
    expect(tokenizeFlags(undefined as unknown as string).error).toBeNull()
  })
})

describe('findManagedFlags', () => {
  it('flags long managed spellings', () => {
    expect(findManagedFlags('-fa on --port 9000')).toEqual(['--port'])
  })
  it('canonicalises + flags short spellings (-ngl, -c)', () => {
    expect(findManagedFlags('-ngl 999 -c 8192')).toEqual(['-ngl', '-c'])
  })
  it('dedupes and preserves first-seen order', () => {
    expect(findManagedFlags('--port 1 -fa on --port 2')).toEqual(['--port'])
  })
  it('clean tuning text has no offenders', () => {
    expect(findManagedFlags('-fa on -b 2048 -ub 512 --parallel 1')).toEqual([])
  })
  it('every entry in MANAGED_FLAGS is independently detected', () => {
    for (const f of MANAGED_FLAGS) {
      expect(findManagedFlags(`${f} x`)).toContain(f)
    }
  })
})

describe('findSlotHardwareFlags', () => {
  it('flags --threads (the model-drawer placeholder regression this file guards)', () => {
    expect(findSlotHardwareFlags('-fa on -b 2048 --threads 8')).toEqual(['--threads'])
  })
  it('flags -ngl/-dev/-t short spellings too', () => {
    expect(findSlotHardwareFlags('-ngl 999 -dev cuda0 -t 4')).toEqual(['-ngl', '-dev', '-t'])
  })
  it('every entry in SLOT_HARDWARE_FLAGS is independently detected', () => {
    for (const f of SLOT_HARDWARE_FLAGS) {
      expect(findSlotHardwareFlags(`${f} x`)).toContain(f)
    }
  })
  it('clean tuning text has no offenders', () => {
    expect(findSlotHardwareFlags('-fa on -ctk q8_0 -ctv q8_0 --no-mmap')).toEqual([])
  })
})

describe('diffFlags', () => {
  it('identical text (modulo order) is unchanged, not diverged', () => {
    const d = diffFlags('-fa on -b 2048', '-b 2048 -fa on')
    expect(d.diverged).toBe(false)
    expect(d.added).toEqual([])
    expect(d.removed).toEqual([])
    expect(d.changed).toEqual([])
    expect(d.unchanged).toBe(2)
  })
  it('an added flag not in the profile is reported as added', () => {
    const d = diffFlags('-fa on -b 2048 --cache-type-k q8_0', '-fa on -b 2048')
    expect(d.diverged).toBe(true)
    expect(d.added).toEqual([{ flag: '--cache-type-k', value: 'q8_0' }])
  })
  it('a flag present in the profile but dropped from the model is removed', () => {
    const d = diffFlags('-fa on', '-fa on -b 2048')
    expect(d.diverged).toBe(true)
    expect(d.removed).toEqual([{ flag: '-b', value: '2048' }])
  })
  it('same flag, different value, is changed (not added+removed)', () => {
    const d = diffFlags('-b 4096', '-b 2048')
    expect(d.diverged).toBe(true)
    expect(d.changed).toEqual([{ flag: '-b', from: '2048', to: '4096' }])
    expect(d.added).toEqual([])
    expect(d.removed).toEqual([])
  })
  it('keys on the canonical short/long spelling so reordering never spuriously diverges', () => {
    // -ngl and --n-gpu-layers canonicalise to the same managed key.
    const d = diffFlags('--n-gpu-layers 999', '-ngl 999')
    expect(d.diverged).toBe(false)
    expect(d.unchanged).toBe(1)
  })
})

describe('flagsEquivalent', () => {
  it('true for whitespace/order-only differences', () => {
    expect(flagsEquivalent('-fa on   -b 2048', '-b 2048 -fa on')).toBe(true)
  })
  it('false when a value actually differs', () => {
    expect(flagsEquivalent('-b 2048', '-b 4096')).toBe(false)
  })
})

describe('highlightSegments', () => {
  it('classifies flag tokens vs value tokens vs whitespace', () => {
    const segs = highlightSegments('-fa on')
    expect(segs.map((s) => [s.text, s.kind])).toEqual([
      ['-fa', 'flag'],
      [' ', 'space'],
      ['on', 'value'],
    ])
  })
  it('a negative-number value is classified as a value, not a flag', () => {
    const segs = highlightSegments('-ngl -1')
    expect(segs.find((s) => s.text === '-1')?.kind).toBe('value')
  })
  it('round-trips the original text when segments are concatenated', () => {
    const text = '  -fa  on\t--cache-type-k q8_0  '
    const segs = highlightSegments(text)
    expect(segs.map((s) => s.text).join('')).toBe(text)
  })
})
