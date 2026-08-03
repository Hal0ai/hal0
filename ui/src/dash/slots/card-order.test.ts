// card-order — pure-logic coverage for slot-card drag reordering.
//
// Named `.test.ts` deliberately (see slot-shared.test.ts): vitest.config.ts
// only collects src/**/*.test.ts. The environment is `node`, so this file
// exercises the pure helpers only — readOrder/writeOrder/useCardReorder need
// localStorage and React and are covered by the e2e spec instead.
import { describe, expect, it } from 'vitest'

// card-order.js is untyped JS; tsconfig has allowJs with checkJs:false.
import { applyOrder, moveName } from './card-order.js'

const rows = (...names: string[]) => names.map((n) => ({ s: { name: n } }))
const names = (list: { s: { name: string } }[]) => list.map((r) => r.s.name)

describe('applyOrder', () => {
  it('leaves the natural order alone when nothing is saved', () => {
    const input = rows('a', 'b', 'c')
    expect(names(applyOrder(input, []))).toEqual(['a', 'b', 'c'])
    expect(names(applyOrder(input, null))).toEqual(['a', 'b', 'c'])
  })

  it('sorts saved names into their saved positions', () => {
    expect(names(applyOrder(rows('a', 'b', 'c'), ['c', 'a', 'b']))).toEqual(['c', 'a', 'b'])
  })

  it('sends slots created since the save to the end, in natural order', () => {
    // `d` and `e` are new; `c` was saved first.
    expect(names(applyOrder(rows('a', 'd', 'b', 'e', 'c'), ['c', 'b', 'a'])))
      .toEqual(['c', 'b', 'a', 'd', 'e'])
  })

  it('ignores saved names whose slot is gone', () => {
    expect(names(applyOrder(rows('a', 'b'), ['deleted', 'b', 'a']))).toEqual(['b', 'a'])
  })

  it('never mutates the input list', () => {
    const input = rows('a', 'b', 'c')
    applyOrder(input, ['c', 'b', 'a'])
    expect(names(input)).toEqual(['a', 'b', 'c'])
  })
})

describe('moveName', () => {
  it('drops a card after the one it was dragged rightwards onto', () => {
    // dragging `a` onto `c` (index 2) lands it past `c`
    expect(moveName(['a', 'b', 'c'], 'a', 2)).toEqual(['b', 'c', 'a'])
  })

  it('drops a card before the one it was dragged leftwards onto', () => {
    expect(moveName(['a', 'b', 'c'], 'c', 0)).toEqual(['c', 'a', 'b'])
  })

  it('clamps an out-of-range index instead of losing the card', () => {
    expect(moveName(['a', 'b', 'c'], 'b', -5)).toEqual(['b', 'a', 'c'])
    expect(moveName(['a', 'b', 'c'], 'b', 99)).toEqual(['a', 'c', 'b'])
  })

  it('is a no-op copy for an unknown name', () => {
    const input = ['a', 'b']
    const out = moveName(input, 'zzz', 0)
    expect(out).toEqual(['a', 'b'])
    expect(out).not.toBe(input)
  })
})
