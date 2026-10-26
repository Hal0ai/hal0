// The Engine tab's facts: which workflows the editor card lists, which notice the engine card
// shows, the gate row and the queue line. Pure.
import { describe, expect, it } from 'vitest'
import type { ComfyuiCatalogModel, ComfyuiStatus } from '@/api/hooks/useComfyui'
import {
  engineNotice,
  gateRow,
  queueSummary,
  workflowChips,
} from '../image/engine-pure'

const model = (id: string, state: ComfyuiCatalogModel['state'], label = id): ComfyuiCatalogModel =>
  ({ id, label, state }) as ComfyuiCatalogModel

describe('workflowChips', () => {
  const models = [
    model('sdxl-lightning', 'complete', 'SDXL Lightning'),
    model('qwen-image', 'partial'),
    model('wan22-t2v', 'complete', 'Wan 2.2 Text to Video'),
    model('sdxl-turbo', 'none'),
  ]

  it('lists the installed models, then the custom files no catalog model owns', () => {
    const chips = workflowChips(models, [
      { name: 'sdxl-lightning', source: 'primary' },
      { name: 'qwen-image', source: 'primary' },
      { name: 'wan22-t2v', source: 'primary' },
      { name: 'my-upscale', source: 'user' },
    ])
    expect(chips).toEqual([
      { id: 'sdxl-lightning', label: 'SDXL Lightning', custom: false },
      { id: 'wan22-t2v', label: 'Wan 2.2 Text to Video', custom: false },
      { id: 'my-upscale', label: 'my-upscale', custom: true },
    ])
  })

  it('does not list a catalog model that is not installed, even when its file is on disk', () => {
    // A partial install can have its workflow file already; the file is the catalog's, not custom.
    const chips = workflowChips(models, [
      { name: 'qwen-image', source: 'primary' },
      { name: 'sdxl-turbo', source: 'primary' },
    ])
    expect(chips.map((c) => c.id)).toEqual(['sdxl-lightning', 'wan22-t2v'])
  })

  it('shows an installed model whose file is missing: the editor can still be opened for it', () => {
    expect(workflowChips(models, []).map((c) => c.id)).toEqual(['sdxl-lightning', 'wan22-t2v'])
  })

  it('lists nothing while the catalog is unknown: every file would look custom', () => {
    expect(workflowChips(undefined, [{ name: 'sdxl-lightning', source: 'primary' }])).toEqual([])
  })

  it('copes with no workflow listing', () => {
    expect(workflowChips(models, undefined).map((c) => c.id)).toEqual(['sdxl-lightning', 'wan22-t2v'])
  })
})

describe('engineNotice', () => {
  const status = (over: Partial<ComfyuiStatus>) =>
    ({ engine: 'running', reachable: true, ...over }) as ComfyuiStatus

  it('names a stopped engine', () => {
    expect(engineNotice(status({ engine: 'stopped', reachable: false }))).toBe('stopped')
  })

  it('names a container that is up while ComfyUI does not answer', () => {
    expect(engineNotice(status({ engine: 'starting', reachable: false }))).toBe('unreachable')
  })

  it('says nothing for an engine that answers', () => {
    expect(engineNotice(status({ engine: 'running' }))).toBeNull()
    expect(engineNotice(status({ engine: 'generating' }))).toBeNull()
    // `starting` while ComfyUI answers is not a state the backend reports; not a fault if it did.
    expect(engineNotice(status({ engine: 'starting', reachable: true }))).toBeNull()
  })

  it('says nothing before the first answer', () => {
    expect(engineNotice(undefined)).toBeNull()
  })
})

describe('gateRow', () => {
  it('covers all four states', () => {
    expect(gateRow({ state: 'current', version_seen: '3' })).toMatchObject({ label: 'current', tone: 'ok' })
    expect(gateRow({ state: 'stale', version_seen: '2' })).toMatchObject({ label: 'stale', tone: 'warn' })
    expect(gateRow({ state: 'unknown', version_seen: null })).toMatchObject({ label: 'unknown', tone: 'muted' })
    expect(gateRow({ state: 'no_credential', version_seen: null })).toMatchObject({
      label: 'no credential',
      tone: 'err',
    })
  })

  it('has a row only when the backend reported a gate', () => {
    expect(gateRow(null)).toBeNull()
    expect(gateRow(undefined)).toBeNull()
  })
})

describe('queueSummary', () => {
  it('counts what runs and what waits', () => {
    expect(queueSummary({ running: 1, pending: 2 })).toEqual({ line: '1 running · 2 waiting', empty: false })
    expect(queueSummary({ running: 0, pending: 0 })).toEqual({ line: '0 running · 0 waiting', empty: true })
  })

  it('reads a count the body left out as none, never as "undefined"', () => {
    const partial = (q: object) => queueSummary(q as { running: number; pending: number })
    expect(partial({})).toEqual({ line: '0 running · 0 waiting', empty: true })
    expect(partial({ running: 1 })).toEqual({ line: '1 running · 0 waiting', empty: false })
    expect(partial({ pending: 2 })).toEqual({ line: '0 running · 2 waiting', empty: false })
  })
})
