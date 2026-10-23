// #2919 — the memory engine model picker's pure helpers: one <select> whose
// value encodes either a local slot or an upstream model, the PUT body each
// choice turns into, and the "engine running" line that names what the
// engine really loads (#2918).
import { describe, expect, it } from 'vitest'
import {
  engineRunningLine,
  engineTargetOptions,
  engineTargetPutBody,
  engineTargetValue,
} from '../engineTarget.js'

const STATUS = {
  enabled: true,
  extraction_slot: 'utility',
  extraction_model: null,
  slot_resolves: true,
  available_slots: ['agent', 'utility'],
  available_models: [
    { id: 'MiniMax-M2.5', owned_by: 'minimax' },
    { id: 'minimax/minimax-m3', owned_by: 'openrouter' },
  ],
  target: { kind: 'slot', model: 'hal0/utility', alias: 'hal0/memory' },
  engine_llm: {
    provider: 'openai',
    base_url: 'http://127.0.0.1:8080/v1',
    model: 'hal0/memory',
    overridden: false,
    override_file: null,
    override_keys: [],
  },
}

describe('engineTargetValue', () => {
  it('encodes the local slot when no upstream model is set', () => {
    expect(engineTargetValue(STATUS)).toBe('slot:utility')
  })
  it('encodes the upstream model when one is set', () => {
    expect(engineTargetValue({ ...STATUS, extraction_model: 'MiniMax-M2.5' })).toBe(
      'model:MiniMax-M2.5',
    )
  })
  it('is empty without a status', () => {
    expect(engineTargetValue(null)).toBe('')
    expect(engineTargetValue(undefined)).toBe('')
  })
})

describe('engineTargetOptions', () => {
  it('groups local slots and upstream models', () => {
    expect(engineTargetOptions(STATUS)).toEqual({
      local: [
        { value: 'slot:agent', label: 'agent' },
        { value: 'slot:utility', label: 'utility' },
      ],
      upstream: [
        { value: 'model:MiniMax-M2.5', label: 'MiniMax-M2.5 · minimax', owner: 'minimax' },
        {
          value: 'model:minimax/minimax-m3',
          label: 'minimax/minimax-m3 · openrouter',
          owner: 'openrouter',
        },
      ],
    })
  })
  it('keeps a configured but missing slot or model pickable, marked', () => {
    const slotGone = engineTargetOptions({ ...STATUS, extraction_slot: 'coder', slot_resolves: false })
    expect(slotGone.local[0]).toEqual({ value: 'slot:coder', label: 'coder (not running)' })
    const modelGone = engineTargetOptions({ ...STATUS, extraction_model: 'gone/model' })
    expect(modelGone.upstream[0]).toEqual({
      value: 'model:gone/model',
      label: 'gone/model (not advertised by any upstream)',
      owner: null,
    })
  })
  it('is empty without a status', () => {
    expect(engineTargetOptions(null)).toEqual({ local: [], upstream: [] })
  })
})

describe('engineTargetPutBody', () => {
  it('a slot choice sets the slot and clears the upstream model', () => {
    expect(engineTargetPutBody('slot:agent')).toEqual({
      extraction_slot: 'agent',
      extraction_model: null,
    })
  })
  it('a model choice sets only the upstream model (the slot stays as the way back)', () => {
    expect(engineTargetPutBody('model:minimax/minimax-m3')).toEqual({
      extraction_model: 'minimax/minimax-m3',
    })
  })
  it('anything else sends nothing', () => {
    expect(engineTargetPutBody('')).toEqual({})
    expect(engineTargetPutBody('bogus')).toEqual({})
  })
})

describe('engineRunningLine', () => {
  it('names the model and provider the engine loads', () => {
    expect(engineRunningLine(STATUS)).toEqual({
      text: 'hal0/memory via openai @ http://127.0.0.1:8080/v1',
      warn: false,
    })
  })
  it('warns when hindsight-llm.env overrides the target', () => {
    const line = engineRunningLine({
      ...STATUS,
      engine_llm: {
        provider: 'minimax',
        base_url: 'https://api.minimax.io/v1',
        model: 'MiniMax-M2.5',
        overridden: true,
        override_file: '/etc/hal0/hindsight-llm.env',
        override_keys: ['HINDSIGHT_API_LLM_MODEL', 'HINDSIGHT_API_LLM_PROVIDER'],
      },
    })
    expect(line.warn).toBe(true)
    expect(line.text).toContain('MiniMax-M2.5 via minimax')
    expect(line.text).toContain('overridden by /etc/hal0/hindsight-llm.env')
    expect(line.text).toContain('HINDSIGHT_API_LLM_MODEL')
  })
  it('says so when the engine LLM is unknown', () => {
    expect(engineRunningLine({ ...STATUS, engine_llm: undefined })).toEqual({
      text: 'engine LLM unknown (hal0-api could not read the hindsight-api unit)',
      warn: false,
    })
    expect(engineRunningLine(null)).toEqual({
      text: 'engine LLM unknown (hal0-api could not read the hindsight-api unit)',
      warn: false,
    })
  })
})
