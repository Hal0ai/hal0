// Memory v2 web graph (task C5) — pure salience-cap unit tests.
//
// Salience = sum of MemV2.LINK_WEIGHT[linkType] over every edge incident
// to a node (both endpoints credited) — the same "degree×type_weight"
// math the backend's degree_by_node computes. capNodesBySalience caps a
// node list to the top-N by that score, deterministically (ties broken by
// original order).
import React from 'react'
import { describe, expect, it } from 'vitest'

// memory-web-graph.jsx destructures `useState`/`useEffect`/`useRef` off a
// module-level global `React` (the window-globals prototype convention —
// no ES import of react itself), so importing it for its pure named
// exports still needs that global set first. Static `import` specifiers
// are hoisted above ordinary statements (even ones textually placed
// earlier in the file), so the global has to be set via a dynamic
// `import()` instead — same reason the mount-smoke tests use it.
;(globalThis as unknown as { React: typeof React }).React = React
const { capNodesBySalience, computeSalience } = await import('../memory-web-graph.jsx')

const LINK_WEIGHT = { causal: 4, temporal: 3, cooccurrence: 2, semantic: 1, entity: 1 }

function node(id: string) {
  return { data: { id } }
}
function edge(source: string, target: string, linkType: string) {
  return { data: { source, target, linkType } }
}

describe('computeSalience', () => {
  it('sums incident edge weights for both endpoints', () => {
    const nodes = [node('a'), node('b'), node('c')]
    const edges = [edge('a', 'b', 'causal'), edge('b', 'c', 'semantic')]
    const s = computeSalience(nodes, edges, LINK_WEIGHT)
    expect(s.get('a')).toBe(4)
    expect(s.get('b')).toBe(4 + 1)
    expect(s.get('c')).toBe(1)
  })

  it('isolated nodes (no incident edges) get salience 0', () => {
    const nodes = [node('a'), node('lonely')]
    const edges = [edge('a', 'a', 'causal')] // self-loop, still credits 'a' twice
    const s = computeSalience(nodes, edges, LINK_WEIGHT)
    expect(s.get('lonely')).toBe(0)
  })

  it('falls back to weight 1 for an unknown link type', () => {
    const nodes = [node('a'), node('b')]
    const edges = [edge('a', 'b', 'some_future_type')]
    const s = computeSalience(nodes, edges, LINK_WEIGHT)
    expect(s.get('a')).toBe(1)
  })
})

describe('capNodesBySalience', () => {
  it('returns every node uncapped when the set is already <= cap', () => {
    const nodes = [node('a'), node('b')]
    const { shown, capped } = capNodesBySalience(nodes, [], LINK_WEIGHT, 5)
    expect(capped).toBe(false)
    expect(shown).toHaveLength(2)
  })

  it('caps to the top-N by salience, descending', () => {
    // a: causal(4) to b, temporal(3) to c → salience 7
    // b: causal(4) to a → salience 4
    // c: temporal(3) to a → salience 3
    // d: isolated → salience 0
    const nodes = [node('a'), node('b'), node('c'), node('d')]
    const edges = [edge('a', 'b', 'causal'), edge('a', 'c', 'temporal')]
    const { shown, capped } = capNodesBySalience(nodes, edges, LINK_WEIGHT, 2)
    expect(capped).toBe(true)
    expect(shown.map((n: { data: { id: string } }) => n.data.id)).toEqual(['a', 'b'])
  })

  it('breaks ties by original order (deterministic)', () => {
    const nodes = [node('x'), node('y'), node('z')]
    const { shown } = capNodesBySalience(nodes, [], LINK_WEIGHT, 2)
    // all salience 0 — stable order keeps the first two as-listed
    expect(shown.map((n: { data: { id: string } }) => n.data.id)).toEqual(['x', 'y'])
  })

  it('never returns more than the exact 120-node Global Constraints cap by default', () => {
    const nodes = Array.from({ length: 200 }, (_, i) => node(`n${i}`))
    const { shown, capped } = capNodesBySalience(nodes, [])
    expect(capped).toBe(true)
    expect(shown).toHaveLength(120)
  })
})
