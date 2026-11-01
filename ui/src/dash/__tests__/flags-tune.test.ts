// Unit coverage for the profile drawer's launch-flags helpers (flags-tune.js)
// — pure, dependency-free logic that gates Save on every profile flags edit.
//
// Written as a real vitest spec (not the sibling `__tests__/*.test.mjs`
// files' plain-node-script style) so it's actually picked up by
// `vitest run` — vitest.config.ts's `include` is `src/**/*.test.ts` only.

import { describe, expect, it } from 'vitest'

import {
  envPairs,
  findHalogenDenied,
  findHalogenMalformed,
  findHalogenOutOfRange,
  setEnvToken,
  MANAGED_FLAGS,
  MODEL_FACT_FLAGS,
  SPEC_TYPE_REFUSAL,
  SLOT_HARDWARE_FLAGS,
  canonFlag,
  diffFlags,
  findHostOwnedFlags,
  findManagedFlags,
  findModelFactFlags,
  findPositionalArgs,
  findSlotHardwareFlags,
  HOST_OWNED_FLAGS,
  VALUELESS_FLAGS,
  screenCanon,
  flagsEquivalent,
  isFlagToken,
  tokenizeFlags,
  FLAG_ALIASES,
} from '../flags-tune.js'
import { HALOGEN_DENIED, HALOGEN_KNOBS } from './fixtures/halogen-knobs'

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
  it('flags --spec-type, which the profile sets through Speculation (ADR-0031)', () => {
    expect(findModelFactFlags('--spec-type ngram-mod')).toEqual(['--spec-type'])
    expect(findModelFactFlags('--spec-type=draft-mtp')).toEqual(['--spec-type'])
    expect(SPEC_TYPE_REFUSAL).toContain("set the profile's Speculation instead")
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



// ── every llama.cpp spelling is screened, as on the server (#2567) ──────────

describe('screenCanon', () => {
  it('folds short, joined and underscore spellings onto the canonical flag', () => {
    expect(screenCanon('-m')).toBe('--model')
    expect(screenCanon('-m=/etc/shadow')).toBe('--model')
    expect(screenCanon('--model=/etc/shadow')).toBe('--model')
    expect(screenCanon('--api_key')).toBe('--api-key')
    expect(screenCanon('-hfr')).toBe('--hf-repo')
    expect(screenCanon('--gpu-layers')).toBe('--n-gpu-layers')
    expect(screenCanon('-b')).toBe('--batch-size')
  })
})

describe('findManagedFlags (every spelling)', () => {
  it.each([
    ['-m /etc/shadow', '-m'],
    ['-m=/etc/shadow', '-m'],
    ['--model=/etc/shadow', '--model'],
    ['-a evil', '-a'],
    ['--alias=evil', '--alias'],
    ['--ctx_size 4096', '--ctx_size'],
  ])('%s is flagged as %s', (text, typed) => {
    expect(findManagedFlags(`-fa auto ${text}`)).toEqual([typed])
  })
})

describe('findSlotHardwareFlags (every spelling)', () => {
  it('catches --gpu-layers, joined and underscore forms', () => {
    expect(findSlotHardwareFlags('--gpu-layers 99 -ngl=1 --n_gpu_layers 2')).toEqual([
      '--gpu-layers',
      '-ngl',
      '--n_gpu_layers',
    ])
  })
})

describe('findModelFactFlags (every spelling)', () => {
  it('catches -mm, --no-jinja and the projector device', () => {
    expect(findModelFactFlags('-mm /p.gguf --no-jinja -mmdev ROCm0 -devd CUDA0')).toEqual([
      '-mm',
      '--no-jinja',
      '-mmdev',
      '-devd',
    ])
  })
})

describe('findHostOwnedFlags', () => {
  it.each([
    '-hf org/model',
    '-hfr org/model',
    '--hf-repo=org/model',
    '-mu https://x/m.gguf',
    '-md /etc/shadow',
    '--api-key secret',
    '--api_key_file /etc/shadow',
    '--ssl-key-file /k',
    '--log-file /tmp/x',
    '--path /',
    '--rpc 10.0.0.1:50052',
    '--tools all',
    '-ag',
    '--agent',
  ])('%s is refused', (text) => {
    const offenders = findHostOwnedFlags(`-fa auto ${text}`)
    expect(offenders).toHaveLength(1)
    expect(HOST_OWNED_FLAGS).toContain(screenCanon(offenders[0]))
  })

  it('leaves tuning flags alone', () => {
    expect(
      findHostOwnedFlags('-fa auto -b 2048 -ub 512 -ctk q8_0 --temp 0.7 --no-agent --reasoning-budget -1'),
    ).toEqual([])
  })
})

describe('findPositionalArgs', () => {
  it('flags a bare argument, including one after a joined flag', () => {
    expect(findPositionalArgs('/etc/shadow -fa auto')).toEqual(['/etc/shadow'])
    expect(findPositionalArgs('--temp=0.7 /etc/shadow')).toEqual(['/etc/shadow'])
  })
  it('treats a value after a spaced flag (negative numbers too) as that flag\'s value', () => {
    expect(findPositionalArgs('-fa auto --reasoning-budget -1 --temp=0.7 -b 512')).toEqual([])
  })
})

describe('preset switches (#2567)', () => {
  it.each([
    '--embd-gemma-default',
    '--fim-qwen-7b-default',
    '--fim-qwen-14b-spec',
    '--gpt-oss-120b-default',
    '--vision-gemma-4b-default',
    '--spec-default',
  ])('%s is refused as host-owned', (preset) => {
    expect(findHostOwnedFlags(`-fa auto ${preset}`)).toEqual([preset])
  })
})

describe('findPositionalArgs (valueless switches)', () => {
  it('a token after a valueless switch is a bare argument', () => {
    expect(VALUELESS_FLAGS).toContain('--metrics')
    expect(findPositionalArgs('--metrics /etc/shadow')).toEqual(['/etc/shadow'])
    expect(findPositionalArgs('--embedding /m.gguf -fa auto')).toEqual(['/m.gguf'])
    expect(findPositionalArgs('--cont_batching x')).toEqual(['x'])
  })
  it('a valued flag keeps its value', () => {
    expect(findPositionalArgs('--cache-reuse 256 -fa auto --metrics')).toEqual([])
  })
})

describe('reasoning-format none (case and spacing)', () => {
  it.each([
    ['--reasoning_format=NONE', '--reasoning_format=NONE'],
    ['--reasoning-format None', '--reasoning-format None'],
    ['--reasoning-format=none', '--reasoning-format=none'],
  ])('%s is refused', (text, named) => {
    expect(findModelFactFlags(text)).toEqual([named])
  })
  it('other formats pass', () => {
    expect(findModelFactFlags('--reasoning-format DeepSeek')).toEqual([])
  })
})


// ── HALOGEN_* env tokens (the halogen profile's flags text) ─────────────────

describe('envPairs', () => {
  it('reads NAME=value tokens in order', () => {
    expect(envPairs('HALOGEN_KV_SLOTS=4 HALOGEN_KV8=1')).toEqual([
      { name: 'HALOGEN_KV_SLOTS', value: '4' },
      { name: 'HALOGEN_KV8', value: '1' },
    ])
  })

  it('keeps a quoted value whole, quotes stripped', () => {
    expect(envPairs('HALOGEN_THINKING_BUDGET_MESSAGE="Time is up." HALOGEN_KV8=1')).toEqual([
      { name: 'HALOGEN_THINKING_BUDGET_MESSAGE', value: 'Time is up.' },
      { name: 'HALOGEN_KV8', value: '1' },
    ])
    expect(envPairs("HALOGEN_X='a b'")).toEqual([{ name: 'HALOGEN_X', value: 'a b' }])
  })

  it('splits on the first = only, so a value may carry =', () => {
    expect(envPairs('HALOGEN_X=a=b')).toEqual([{ name: 'HALOGEN_X', value: 'a=b' }])
  })

  it('reads newline-separated tokens and skips tokens that are not NAME=value', () => {
    expect(envPairs('HALOGEN_A=1\nHALOGEN_B=2')).toEqual([
      { name: 'HALOGEN_A', value: '1' },
      { name: 'HALOGEN_B', value: '2' },
    ])
    expect(envPairs('-fa on HALOGEN_A=1 bare')).toEqual([{ name: 'HALOGEN_A', value: '1' }])
  })

  it('keeps duplicates, later last, and survives blank or unbalanced text', () => {
    expect(envPairs('HALOGEN_A=1 HALOGEN_A=2').map((p) => p.value)).toEqual(['1', '2'])
    expect(envPairs('')).toEqual([])
    expect(envPairs(undefined)).toEqual([])
    expect(envPairs('HALOGEN_A="open')).toEqual([])
  })
})

describe('setEnvToken', () => {
  it('appends a missing token, to blank text without a leading space', () => {
    expect(setEnvToken('', 'HALOGEN_KV_SLOTS', '4')).toBe('HALOGEN_KV_SLOTS=4')
    expect(setEnvToken('HALOGEN_KV8=1', 'HALOGEN_KV_SLOTS', '4')).toBe('HALOGEN_KV8=1 HALOGEN_KV_SLOTS=4')
    expect(setEnvToken('HALOGEN_KV8=1  \n', 'HALOGEN_KV_SLOTS', '4')).toBe('HALOGEN_KV8=1 HALOGEN_KV_SLOTS=4')
  })

  it('replaces a token in place and leaves the others, their quoting and their spacing alone', () => {
    const text = 'HALOGEN_A="Time is up."  HALOGEN_KV_SLOTS=2\nHALOGEN_B=\'x y\''
    expect(setEnvToken(text, 'HALOGEN_KV_SLOTS', '4')).toBe(
      'HALOGEN_A="Time is up."  HALOGEN_KV_SLOTS=4\nHALOGEN_B=\'x y\'',
    )
  })

  it('writes a value that needs quoting so it reads back whole', () => {
    for (const value of ['Time is up.', "it's", 'say "hi"', 'a$b']) {
      const out = setEnvToken('HALOGEN_KV8=1', 'HALOGEN_MSG', value)
      expect(envPairs(out)).toEqual([
        { name: 'HALOGEN_KV8', value: '1' },
        { name: 'HALOGEN_MSG', value },
      ])
    }
  })

  it('removes a token on null (and on an empty value, which the server calls malformed)', () => {
    expect(setEnvToken('A=1 HALOGEN_KV_SLOTS=4 B=2', 'HALOGEN_KV_SLOTS', null)).toBe('A=1 B=2')
    expect(setEnvToken('HALOGEN_KV_SLOTS=4 B=2', 'HALOGEN_KV_SLOTS', null)).toBe('B=2')
    expect(setEnvToken('A=1 HALOGEN_KV_SLOTS=4', 'HALOGEN_KV_SLOTS', null)).toBe('A=1')
    expect(setEnvToken('HALOGEN_KV_SLOTS=4', 'HALOGEN_KV_SLOTS', null)).toBe('')
    expect(setEnvToken('A=1 HALOGEN_KV_SLOTS=4', 'HALOGEN_KV_SLOTS', '')).toBe('A=1')
    expect(setEnvToken('A=1', 'HALOGEN_KV_SLOTS', null)).toBe('A=1')
  })

  it('collapses duplicates of the token onto one (the server reads the last)', () => {
    expect(setEnvToken('HALOGEN_A=1 B=2 HALOGEN_A=3', 'HALOGEN_A', '9')).toBe('B=2 HALOGEN_A=9')
    expect(setEnvToken('HALOGEN_A=1 B=2 HALOGEN_A=3', 'HALOGEN_A', null)).toBe('B=2')
  })

  it('does not match a name that merely starts or ends with the target', () => {
    expect(setEnvToken('HALOGEN_KV_SLOTS_MAX=7', 'HALOGEN_KV_SLOTS', '4')).toBe(
      'HALOGEN_KV_SLOTS_MAX=7 HALOGEN_KV_SLOTS=4',
    )
  })

  it('leaves text with an unbalanced quote untouched rather than gluing onto it', () => {
    expect(setEnvToken('HALOGEN_A="open', 'HALOGEN_B', '1')).toBe('HALOGEN_A="open')
  })

  it('round-trips with envPairs', () => {
    let text = ''
    text = setEnvToken(text, 'HALOGEN_KV_SLOTS', '4')
    text = setEnvToken(text, 'HALOGEN_MAX_TOK', '32768')
    text = setEnvToken(text, 'HALOGEN_KV_SLOTS', '8')
    expect(envPairs(text)).toEqual([
      { name: 'HALOGEN_KV_SLOTS', value: '8' },
      { name: 'HALOGEN_MAX_TOK', value: '32768' },
    ])
    text = setEnvToken(text, 'HALOGEN_KV_SLOTS', null)
    expect(text).toBe('HALOGEN_MAX_TOK=32768')
  })
})

describe('findHalogenDenied', () => {
  it('names an exact denied variable as typed', () => {
    expect(findHalogenDenied('HALOGEN_API_PORT=1', HALOGEN_KNOBS, HALOGEN_DENIED)).toEqual(['HALOGEN_API_PORT'])
  })

  it('treats a trailing * as a prefix', () => {
    expect(findHalogenDenied('HALOGEN_NPU_DEVICE=0 HALOGEN_RECORD_PATH=/x', HALOGEN_KNOBS, HALOGEN_DENIED)).toEqual([
      'HALOGEN_NPU_DEVICE',
      'HALOGEN_RECORD_PATH',
    ])
  })

  it('dedupes, keeps first-seen order, and passes the curated knobs and unlisted names', () => {
    const text = 'HALOGEN_KV_SLOTS=4 HALOGEN_CTX=1 HALOGEN_FUTURE_KNOB=2 HALOGEN_PORT=9 HALOGEN_CTX=2'
    expect(findHalogenDenied(text, HALOGEN_KNOBS, HALOGEN_DENIED)).toEqual(['HALOGEN_CTX', 'HALOGEN_PORT'])
  })

  it('does not treat a longer name as an exact match, or a * entry as a literal name', () => {
    expect(findHalogenDenied('HALOGEN_API_PORT_X=1 HALOGEN_NPU=1', HALOGEN_KNOBS, HALOGEN_DENIED)).toEqual([])
    // a typed `*` is no name at all (the shape screen refuses it as malformed)
    expect(findHalogenDenied('HALOGEN_NPU_*=1', HALOGEN_KNOBS, HALOGEN_DENIED)).toEqual([])
  })

  it('is empty for clean, blank, malformed or unbalanced text and a missing list', () => {
    expect(findHalogenDenied('HALOGEN_KV_SLOTS=4', HALOGEN_KNOBS, HALOGEN_DENIED)).toEqual([])
    expect(findHalogenDenied('', HALOGEN_KNOBS, HALOGEN_DENIED)).toEqual([])
    expect(findHalogenDenied('-fa on', HALOGEN_KNOBS, HALOGEN_DENIED)).toEqual([])
    expect(findHalogenDenied('HALOGEN_A="open', HALOGEN_KNOBS, HALOGEN_DENIED)).toEqual([])
    expect(findHalogenDenied('HALOGEN_API_PORT=1', HALOGEN_KNOBS, undefined)).toEqual([])
  })

  it('names a denied variable whatever its value; the empty value is findHalogenMalformed\'s too', () => {
    expect(findHalogenDenied('HALOGEN_API_PORT=', HALOGEN_KNOBS, HALOGEN_DENIED)).toEqual(['HALOGEN_API_PORT'])
    expect(findHalogenMalformed('HALOGEN_API_PORT=')).toEqual(['HALOGEN_API_PORT='])
  })
})

describe('findHalogenMalformed', () => {
  it('flags tokens that are not HALOGEN_NAME=value, by the spelling typed', () => {
    expect(findHalogenMalformed('HALOGEN_KV8=1 -fa on')).toEqual(['-fa', 'on'])
    expect(findHalogenMalformed('HALOGEN_KV8')).toEqual(['HALOGEN_KV8'])
    expect(findHalogenMalformed('halogen_kv8=1')).toEqual(['halogen_kv8=1'])
    expect(findHalogenMalformed('OTHER_KV8=1')).toEqual(['OTHER_KV8=1'])
  })

  it('flags an empty value and a value with a line break', () => {
    expect(findHalogenMalformed('HALOGEN_KV8=')).toEqual(['HALOGEN_KV8='])
    expect(findHalogenMalformed('HALOGEN_MSG="a\nb"')).toHaveLength(1)
  })

  it('accepts quoted values and is empty for clean, blank or unbalanced text', () => {
    expect(findHalogenMalformed('HALOGEN_MSG="Time is up." HALOGEN_KV8=1')).toEqual([])
    expect(findHalogenMalformed('')).toEqual([])
    expect(findHalogenMalformed('HALOGEN_A="open')).toEqual([])
  })
})

describe('findHalogenOutOfRange', () => {
  const bad = (text: string) => findHalogenOutOfRange(text, HALOGEN_KNOBS)

  it('accepts every in-range value, the bounds included', () => {
    expect(
      bad(
        'HALOGEN_KV_SLOTS=1 HALOGEN_KV_SLOTS=64 HALOGEN_KV_POOL_POSITIONS=1024 HALOGEN_MAX_TOK=32768 ' +
          'HALOGEN_KV8=0 HALOGEN_WEIGHTS_LOCK=1 HALOGEN_PROMPT_CACHE=0 HALOGEN_MAX_TOKENS_DEFAULT=65536 ' +
          'HALOGEN_HOST_RESERVE_GIB=10',
      ),
    ).toEqual([])
  })

  it('refuses an int below its minimum or above its maximum', () => {
    expect(bad('HALOGEN_KV_SLOTS=0')[0]).toMatchObject({ name: 'HALOGEN_KV_SLOTS', value: '0' })
    expect(bad('HALOGEN_KV_SLOTS=0')[0].message).toContain('at least 1')
    expect(bad('HALOGEN_KV_SLOTS=65')[0].message).toContain('at most 64')
    expect(bad('HALOGEN_HOST_RESERVE_GIB=9')[0].message).toContain('at least 10')
    expect(bad('HALOGEN_MAX_TOKENS_DEFAULT=65537')[0].message).toContain('at most 65536')
  })

  it('refuses an int that is not a whole number, or has more than 9 digits', () => {
    expect(bad('HALOGEN_KV_SLOTS=2.5')[0].message).toContain('whole number')
    expect(bad('HALOGEN_KV_SLOTS=-1')[0].message).toContain('whole number')
    expect(bad('HALOGEN_KV_SLOTS=abc')[0].message).toContain('whole number')
    expect(bad('HALOGEN_KV_POOL_POSITIONS=1234567890')[0].message).toContain('9 digits')
  })

  it('refuses a bool other than 0 or 1 and an enum outside its choices', () => {
    expect(bad('HALOGEN_KV8=2')[0].message).toContain('0 or 1')
    expect(bad('HALOGEN_KV8=true')[0].message).toContain('0 or 1')
    expect(bad('HALOGEN_MAX_TOK=8192')[0].message).toContain('16384, 32768')
    expect(bad('HALOGEN_PROMPT_CACHE=3')[0].message).toContain('0, 1, 2')
  })

  it('judges the last value of a repeated name, as the server does', () => {
    expect(bad('HALOGEN_KV_SLOTS=999 HALOGEN_KV_SLOTS=4')).toEqual([])
    expect(bad('HALOGEN_KV_SLOTS=4 HALOGEN_KV_SLOTS=999')).toHaveLength(1)
  })

  it('ignores names no knob curates, and blank, malformed or empty-knob input', () => {
    expect(bad('HALOGEN_FUTURE_KNOB=anything')).toEqual([])
    expect(bad('')).toEqual([])
    expect(bad('-fa on')).toEqual([])
    expect(findHalogenOutOfRange('HALOGEN_KV_SLOTS=0', [])).toEqual([])
    expect(findHalogenOutOfRange('HALOGEN_KV_SLOTS=0', undefined)).toEqual([])
  })
})
