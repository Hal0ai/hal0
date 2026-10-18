import { describe, expect, it } from 'vitest'
import { pullFileLabel, type PullFileSnapshot } from '../useModels'

const f = (kind: string, bytes_done: number, bytes_total: number, name = `${kind}.gguf`) =>
  ({ hf_filename: name, kind, bytes_done, bytes_total }) as PullFileSnapshot

describe('pullFileLabel', () => {
  it('is null for a single-file pull', () => {
    expect(pullFileLabel([f('model', 10, 100)])).toBeNull()
  })

  it('names the part a split pull is on', () => {
    const files = [f('shard', 100, 100), f('shard', 5, 100), f('shard', 0, 100), f('mmproj', 0, 10)]
    expect(pullFileLabel(files)).toBe('part 2 of 3')
  })

  it('names the projector once every part is in', () => {
    const files = [f('shard', 100, 100), f('shard', 100, 100), f('mmproj', 3, 10)]
    expect(pullFileLabel(files)).toBe('vision projector')
  })

  it('names a sidecar by its basename and is null when everything landed', () => {
    expect(pullFileLabel([f('model', 9, 9), f('sidecar', 0, 0, 'dir/voices-v1.0.bin')])).toBe(
      'voices-v1.0.bin',
    )
    expect(pullFileLabel([f('shard', 9, 9), f('shard', 9, 9)])).toBeNull()
  })
})
