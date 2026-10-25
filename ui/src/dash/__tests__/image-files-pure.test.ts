// Models → Image: the pure grouping behind the file inventory. The tab lists what
// GET /api/comfyui/files reports, one section per component kind, in the order an
// operator reads a ComfyUI install: the main weights first, helpers after, and any
// directory the catalog does not know (operator-filled ones) last, alphabetically.
import { describe, expect, it } from 'vitest'

import type { ComfyuiFile } from '@/api/hooks/useComfyui'

import { groupFilesByKind, kindLabel } from '../image/image-files-pure'

function file(kind: string, relpath: string, extra: Partial<ComfyuiFile> = {}): ComfyuiFile {
  return { kind, subdir: kind, relpath, size_bytes: 1, models: [], ...extra }
}

describe('groupFilesByKind', () => {
  it('orders kinds and puts extras last', () => {
    const files = [
      file('vae', 'ae.safetensors'),
      file('style_models', 'style.safetensors'),
      file('lora', 'a.safetensors'),
      file('checkpoint', 'sd.safetensors'),
      file('controlnet', 'canny.safetensors'),
      file('latent_upscaler', 'x2.safetensors'),
      file('clip_vision', 'siglip.safetensors'),
      file('upscaler', '4x.pth'),
      file('text_encoder', 'umt5.safetensors'),
      file('diffusion_model', 'wan.safetensors'),
      file('embeddings', 'neg.pt'),
    ]
    expect(groupFilesByKind(files).map((g) => g.kind)).toEqual([
      'checkpoint',
      'diffusion_model',
      'text_encoder',
      'vae',
      'lora',
      'upscaler',
      'clip_vision',
      'latent_upscaler',
      // extras, alphabetically
      'controlnet',
      'embeddings',
      'style_models',
    ])
  })

  it('keeps each group in the order the listing sent and labels it', () => {
    const files = [file('lora', 'b.safetensors'), file('lora', 'a.safetensors'), file('vae', 'ae.safetensors')]
    const groups = groupFilesByKind(files)
    expect(groups.map((g) => g.label)).toEqual(['VAE', 'LoRAs'])
    expect(groups[1]!.files.map((f) => f.relpath)).toEqual(['b.safetensors', 'a.safetensors'])
  })

  it('omits kinds with no files and handles an empty listing', () => {
    expect(groupFilesByKind([])).toEqual([])
    expect(groupFilesByKind([file('vae', 'ae.safetensors')]).map((g) => g.kind)).toEqual(['vae'])
  })
})

describe('kindLabel', () => {
  it('names every catalog kind', () => {
    expect(kindLabel('checkpoint')).toBe('Checkpoints')
    expect(kindLabel('diffusion_model')).toBe('Diffusion models')
    expect(kindLabel('text_encoder')).toBe('Text encoders')
    expect(kindLabel('vae')).toBe('VAE')
    expect(kindLabel('lora')).toBe('LoRAs')
    expect(kindLabel('upscaler')).toBe('Upscalers')
    expect(kindLabel('clip_vision')).toBe('CLIP vision')
    expect(kindLabel('latent_upscaler')).toBe('Latent upscalers')
  })

  it('falls back to a readable name for a directory the catalog does not know', () => {
    expect(kindLabel('style_models')).toBe('Style models')
    expect(kindLabel('controlnet')).toBe('Controlnet')
  })
})
