// @vitest-environment happy-dom
//
// #3114: the slot rail and the dock each counted "ready" with a predicate of their own, so their
// `N / M ready` figures split while a slot warmed (a running container that has not reported healthy
// is not ready: slot-status.js, the one slot classifier, calls it `starting`). One owner now:
// isSlotReady / readyCounts in slot-status.js. This file pins the helper, and that the rail's
// counts and the dock's rollup agree on the same roster, the disputed states included.
import React, { act } from 'react'
import { createRoot } from 'react-dom/client'
import { describe, expect, it, vi } from 'vitest'
import type { Slot } from '@/api/hooks/useSlots'

;(globalThis as unknown as { React: typeof React }).React = React
;(globalThis as unknown as { IS_REACT_ACT_ENVIRONMENT: boolean }).IS_REACT_ACT_ENVIRONMENT = true

const { box } = vi.hoisted(() => ({ box: { slots: [] as unknown[] } }))

vi.mock('@/api/hooks/useSlots', async (importOriginal) => ({
  ...(await importOriginal<Record<string, unknown>>()),
  useSlots: () => ({ data: box.slots, isSuccess: true, isError: false }),
}))

const { isSlotConfigured, isSlotReady, readyCounts } = await import('../slot-status.js')
const { railCounts, railRows } = await import('../slot-rail-pure')
const { useRuntimeRollup } = await import('@/api/hooks/useRuntime')

const NOW = Date.UTC(2026, 10, 1, 12, 0, 0)
const FRESH = NOW / 1000 - 30 // last used 30 s ago, in epoch seconds

// A slot as /api/slots hands it over once the container is enriched.
const slot = (name: string, state: string, extra: Record<string, unknown> = {}): Slot =>
  ({ name, type: 'llm', device: 'gpu-vulkan', model: `${name}.gguf`, state, metrics: {}, ...extra }) as unknown as Slot

const serving = slot('serving', 'serving', {
  container_status: 'running',
  container_health: true,
  last_used_at: FRESH,
})
const loaded = slot('loaded', 'ready', { container_status: 'running', container_health: true })
// The disputed state: the container is up, /health has not answered yet. The old dock predicate
// counted it ready; the rail (and the slot card's own dot) call it `starting`.
const booting = slot('booting', 'warming', { container_status: 'running', container_health: false })
const bootingNoState = slot('booting-ready-state', 'ready', { container_status: 'running', container_health: false })
const pulling = slot('pulling', 'pulling', { container_status: 'pulling' })
const crashed = slot('crashed', 'error', { container_status: 'running', container_health: true })
const stopped = slot('stopped', 'offline', { container_status: 'stopped' })
// No container enrichment yet (a stale /api/status union entry): the bare state string decides.
const bareIdle = slot('bare-idle', 'idle')
const bareWarming = slot('bare-warming', 'warming')
const unconfigured = slot('unconfigured', 'offline', { model: '', container_status: 'stopped' })

const ROSTER = [serving, loaded, booting, bootingNoState, pulling, crashed, stopped, bareIdle, bareWarming, unconfigured]

describe('isSlotReady', () => {
  it('is loaded and healthy: serving or ready, never starting, pulling, error or stopped', () => {
    expect(isSlotReady(serving, NOW)).toBe(true)
    expect(isSlotReady(loaded, NOW)).toBe(true)
    expect(isSlotReady(booting, NOW)).toBe(false)
    expect(isSlotReady(bootingNoState, NOW)).toBe(false)
    expect(isSlotReady(pulling, NOW)).toBe(false)
    expect(isSlotReady(crashed, NOW)).toBe(false)
    expect(isSlotReady(stopped, NOW)).toBe(false)
  })

  it('reads a snapshot with no container enrichment off its state string', () => {
    expect(isSlotReady(bareIdle, NOW)).toBe(true)
    expect(isSlotReady(bareWarming, NOW)).toBe(false)
  })
})

describe('readyCounts', () => {
  it('counts the configured slots, and the ready ones among them', () => {
    expect(isSlotConfigured(unconfigured)).toBe(false)
    expect(isSlotConfigured({ ...unconfigured, modelDefault: 'fallback.gguf' })).toBe(true)
    expect(readyCounts(ROSTER, NOW)).toEqual({ ready: 3, total: 9 })
  })

  it('counts a ready slot with no model bound toward neither figure', () => {
    const odd = slot('odd', 'ready', { model: '', container_status: 'running', container_health: true })
    expect(readyCounts([odd], NOW)).toEqual({ ready: 0, total: 0 })
  })
})

describe('the rail and the dock count the same roster the same way (#3114)', () => {
  const dockCounts = async (slots: Slot[]) => {
    box.slots = slots
    let rollup: { ready: number; total: number; loaded: number } | undefined
    const Probe = () => {
      rollup = useRuntimeRollup()
      return null
    }
    const host = document.createElement('div')
    const root = createRoot(host)
    await act(async () => root.render(<Probe />))
    await act(async () => root.unmount())
    return rollup!
  }
  const railFigures = (slots: Slot[], unloadSet: string[] = [], imageMode = false) => {
    const { ready, total } = railCounts(railRows(slots, unloadSet, imageMode, NOW))
    return { ready, total }
  }

  it('agrees on a roster with a slot that is still warming', async () => {
    const dock = await dockCounts(ROSTER)
    expect({ ready: dock.ready, total: dock.total }).toEqual(railFigures(ROSTER))
    expect(dock.ready).toBe(3)
    expect(dock.loaded).toBe(dock.ready)
  })

  it('agrees while only warming slots are up: nothing is ready yet', async () => {
    const roster = [booting, bootingNoState, pulling, stopped]
    const dock = await dockCounts(roster)
    expect(railFigures(roster)).toEqual({ ready: 0, total: 4 })
    expect({ ready: dock.ready, total: dock.total }).toEqual({ ready: 0, total: 4 })
  })

  it('agrees on every one-slot roster', async () => {
    for (const s of ROSTER) {
      const dock = await dockCounts([s])
      expect({ slot: s.name, ready: dock.ready, total: dock.total }).toEqual({ slot: s.name, ...railFigures([s]) })
    }
  })
})
