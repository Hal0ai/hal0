import { describe, expect, it } from 'vitest'
import { typeGlyphKey } from './type-glyph.jsx'

describe('typeGlyphKey', () => {
  it('folds the spellings a model type arrives in onto one glyph', () => {
    expect(['llm', 'chat'].map(typeGlyphKey)).toEqual(['chat', 'chat'])
    expect(['embed', 'embedding', 'embeddings'].map(typeGlyphKey)).toEqual(['embed', 'embed', 'embed'])
    expect(['rerank', 'reranker'].map(typeGlyphKey)).toEqual(['rerank', 'rerank'])
    expect(['stt', 'asr', 'transcription'].map(typeGlyphKey)).toEqual(['stt', 'stt', 'stt'])
    expect(typeGlyphKey('TTS')).toBe('tts')
    expect(typeGlyphKey('image')).toBe('image')
  })
  it('has no glyph for an unknown type, which keeps its text chip', () => {
    expect(typeGlyphKey('video')).toBeNull()
    expect(typeGlyphKey('')).toBeNull()
    expect(typeGlyphKey(undefined)).toBeNull()
    expect(typeGlyphKey('constructor')).toBeNull()
  })
})
