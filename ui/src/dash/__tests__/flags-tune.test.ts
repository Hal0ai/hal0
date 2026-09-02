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
import {
  canonFlag, groupFlagPairs, spliceFlagValue,
  removeFlagFromText, addFlagToText, FLAG_ALIASES, CATEGORY_ORDER,
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

describe("groupFlagPairs", () => {
  it("groups known flags and routes unknown to template-misc", () => {
    const { groups, error } = groupFlagPairs("--temp 0.4 --cache-type-k q8_0 --wat 1");
    expect(error).toBeNull();
    const ids = groups.map((g) => g.id);
    expect(ids).toContain("sampling");
    expect(ids).toContain("cache-kv");
    const misc = groups.find((g) => g.id === "template-misc");
    expect(misc!.pairs).toEqual([{ flag: "--wat", canon: "--wat", value: "1" }]);
  });
  it("omits empty groups and keeps CATEGORY_ORDER order", () => {
    const { groups } = groupFlagPairs("--temp 0.4");
    expect(groups).toHaveLength(1);
    expect(groups[0].id).toBe("sampling");
    const order = CATEGORY_ORDER.map((c) => c.id);
    expect(order).toEqual(["sampling", "cache-kv", "memory-batch", "template-misc"]);
  });
  it("surfaces tokenizer errors", () => {
    expect(groupFlagPairs('--temp "0.4').error).toMatch(/unbalanced quote/);
  });
});

describe("splice helpers preserve spelling, order, untouched text", () => {
  const t = "-fa auto --temp 0.4 -b 2048";
  it("spliceFlagValue replaces one value", () => {
    expect(spliceFlagValue(t, "--temp", "0.2")).toBe("-fa auto --temp 0.2 -b 2048");
  });
  it("removeFlagFromText drops flag+value", () => {
    expect(removeFlagFromText(t, "--temp")).toBe("-fa auto -b 2048");
  });
  it("removeFlagFromText drops a boolean flag alone", () => {
    expect(removeFlagFromText("--jinja --temp 0.4", "--jinja")).toBe("--temp 0.4");
  });
  it("addFlagToText appends, quoting whitespace values", () => {
    expect(addFlagToText(t, "--override-kv", "a b")).toBe(t + ' --override-kv "a b"');
    expect(addFlagToText("", "--jinja", null)).toBe("--jinja");
  });
  it("splice via a short alias canon hits the long-typed flag and vice versa", () => {
    expect(spliceFlagValue("--batch-size 2048", canonFlag("-b"), "512"))
      .toBe("--batch-size 512");
  });
  it("spliceFlagValue replaces a double-quoted multi-word value without corrupting the remainder", () => {
    expect(spliceFlagValue('--foo "a b" --temp 0.4', "--foo", "x"))
      .toBe("--foo x --temp 0.4");
  });
  it("spliceFlagValue replaces a single-quoted multi-word value without corrupting the remainder", () => {
    expect(spliceFlagValue("--foo 'a b' --temp 0.4", "--foo", "x"))
      .toBe("--foo x --temp 0.4");
  });
  it("removeFlagFromText drops a double-quoted multi-word value without corrupting the remainder", () => {
    expect(removeFlagFromText('--foo "a b" --temp 0.4', "--foo")).toBe("--temp 0.4");
  });
  it("removeFlagFromText drops a single-quoted multi-word value without corrupting the remainder", () => {
    expect(removeFlagFromText("--foo 'a b' --temp 0.4", "--foo")).toBe("--temp 0.4");
  });
});

// Repeating a flag is legitimate llama-server usage (-ot / --override-kv /
// --lora), so "the pair for this canon" is not unique and the helpers have to
// be told WHICH one. Without the occurrence index every affordance on the
// second pill silently acted on the first.
describe("splice helpers address a specific occurrence of a repeated flag", () => {
  const rep = "-ot ffn=CPU -ot attn=GPU --temp 0.4";

  it("defaults to the first occurrence (unchanged contract)", () => {
    expect(removeFlagFromText(rep, "-ot")).toBe("-ot attn=GPU --temp 0.4");
    expect(spliceFlagValue(rep, "-ot", "x")).toBe("-ot x -ot attn=GPU --temp 0.4");
  });

  it("removes the second occurrence and leaves the first intact", () => {
    expect(removeFlagFromText(rep, "-ot", 1)).toBe("-ot ffn=CPU --temp 0.4");
  });

  it("edits the second occurrence's value only", () => {
    expect(spliceFlagValue(rep, "-ot", "attn=CPU", 1))
      .toBe("-ot ffn=CPU -ot attn=CPU --temp 0.4");
  });

  it("counts occurrences across mixed spellings via canonFlag", () => {
    const mixed = "-b 512 --batch-size 2048";
    expect(spliceFlagValue(mixed, "--batch-size", "1024", 1)).toBe("-b 512 --batch-size 1024");
    expect(removeFlagFromText(mixed, "--batch-size", 1)).toBe("-b 512");
  });

  it("counts a bare repetition as its own occurrence", () => {
    // `--temp --temp 0.5`: the first carries no value (the next token is a
    // flag), so occurrence 0 is the bare one and occurrence 1 is the valued one.
    const bare = "--temp --temp 0.5";
    expect(removeFlagFromText(bare, "--temp", 0)).toBe("--temp 0.5");
    expect(spliceFlagValue(bare, "--temp", "0.9", 1)).toBe("--temp --temp 0.9");
    // Occurrence 0 has no value token to replace — still a documented no-op.
    expect(spliceFlagValue(bare, "--temp", "0.9", 0)).toBe(bare);
  });

  it("an out-of-range occurrence is a no-op, never a wrong-pair edit", () => {
    expect(removeFlagFromText(rep, "-ot", 5)).toBe(rep);
    expect(spliceFlagValue(rep, "-ot", "x", 5)).toBe(rep);
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
