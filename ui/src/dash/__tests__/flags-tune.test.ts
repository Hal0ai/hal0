// Unit coverage for the profile drawer's launch-flags helpers (flags-tune.js)
// — pure, dependency-free logic that gates Save on every profile flags edit.
//
// Written as a real vitest spec (not the sibling `__tests__/*.test.mjs`
// files' plain-node-script style) so it's actually picked up by
// `vitest run` — vitest.config.ts's `include` is `src/**/*.test.ts` only.

import { describe, expect, it } from 'vitest'

import {
  MANAGED_FLAGS,
  MODEL_FACT_FLAGS,
  SLOT_HARDWARE_FLAGS,
  canonFlag,
  diffFlags,
  findManagedFlags,
  findModelFactFlags,
  findSlotHardwareFlags,
  flagsEquivalent,
  isFlagToken,
  tokenizeFlags,
  FLAG_ALIASES,
} from '../flags-tune.js'

describe("canonFlag", () => {
  it("folds a short alias to its long form", () => {
    // pick any real pair from FLAG_ALIASES at implementation time, e.g.:
    const [short, long] = Object.entries(FLAG_ALIASES)[0];
    expect(canonFlag(short)).toBe(long);
  });
  it("returns unknown flags unchanged", () => {
    expect(canonFlag("--totally-novel")).toBe("--totally-novel");
  });
});

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

describe('findModelFactFlags (ADR-0029: the model owns the chat template and projector)', () => {
  it('every entry in MODEL_FACT_FLAGS is independently detected', () => {
    for (const f of MODEL_FACT_FLAGS) {
      expect(findModelFactFlags(`${f} x`)).toContain(f)
    }
  })
  it('flags --jinja, --chat-template(-file) and the mmproj family', () => {
    expect(
      findModelFactFlags('-fa on --jinja --chat-template chatml --mmproj m.gguf --no-mmproj'),
    ).toEqual(['--jinja', '--chat-template', '--mmproj', '--no-mmproj'])
  })
  it('flags --spec-draft-device', () => {
    expect(findModelFactFlags('--spec-draft-device ROCm0')).toEqual(['--spec-draft-device'])
  })
  it('flags --reasoning-format none but not other formats', () => {
    expect(findModelFactFlags('--reasoning-format none')).toEqual(['--reasoning-format none'])
    expect(findModelFactFlags('--reasoning-format=none')).toEqual(['--reasoning-format=none'])
    expect(findModelFactFlags('--reasoning-format deepseek')).toEqual([])
  })
  it('matches the --flag=value spelling on the part before the =', () => {
    expect(findModelFactFlags('--chat-template-file=/x/y.jinja')).toEqual(['--chat-template-file'])
  })
  it('clean tuning text has no offenders', () => {
    expect(findModelFactFlags('-fa on -ctk q8_0 -ctv q8_0 --no-mmap')).toEqual([])
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
  it('folds the FULL alias table, not just the managed subset (#2211)', () => {
    // -b is an unmanaged llama-server alias for --batch-size. Same value spelled
    // two ways must be no-change, not added+removed.
    const d = diffFlags('-b 2048', '--batch-size 2048')
    expect(d.diverged).toBe(false)
    expect(d.added).toEqual([])
    expect(d.removed).toEqual([])
    expect(d.unchanged).toBe(1)
  })
  it('cross-spelling value divergence is ONE changed pair, in the operator spelling (#2211)', () => {
    const d = diffFlags('-b 2048', '--batch-size 1024')
    expect(d.diverged).toBe(true)
    expect(d.added).toEqual([])
    expect(d.removed).toEqual([])
    // `flag` carries the model side's own spelling; only the matching canonicalises.
    expect(d.changed).toEqual([{ flag: '-b', from: '1024', to: '2048' }])
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


