// The stylesheet's own rules, checked against its text: colours come from the dashboard tokens,
// every class is the area's, no outline is removed, and a state is never drawn by hue alone.
//
// Read from disk: vitest does not process CSS, so a `?raw` import of a stylesheet is empty here.
// @ts-expect-error The app's tsconfig carries no Node types on purpose; this test runs in Node.
import { readFileSync } from 'node:fs'
import { describe, expect, it } from 'vitest'

const read = (relative: string): string => readFileSync(new URL(relative, import.meta.url), 'utf8')
const css = read('./memory-area.css')
const dashboardCss = read('../../dashboard.css')

/** The stylesheet without its comments. */
const rules = css.replace(/\/\*[\s\S]*?\*\//g, '')

/** The declarations of the one rule whose selector is exactly `selector`. */
function block(selector: string): string {
  const at = rules.indexOf(`${selector} {`)
  expect(at, `rule ${selector}`).toBeGreaterThanOrEqual(0)
  return rules.slice(at, rules.indexOf('}', at))
}

describe('memory-area.css', () => {
  it('is read', () => {
    expect(rules.length).toBeGreaterThan(1000)
    expect(dashboardCss).toContain('--hal0-accent:')
  })

  it('has no colour literal: every colour is a dashboard token', () => {
    expect(rules).not.toMatch(/#[0-9a-fA-F]{3,8}\b/)
    expect(rules).not.toMatch(/\brgba?\(/)
    expect(rules).not.toMatch(/\bhsla?\(/)
  })

  it('uses only tokens that dashboard.css defines', () => {
    const used = new Set([...rules.matchAll(/var\((--[a-z0-9-]+)/g)].map((m) => m[1]))
    expect(used.size).toBeGreaterThan(5)
    for (const token of used) {
      expect(dashboardCss, token).toMatch(new RegExp(`${token}:`))
    }
  })

  it('names every class of its own with the .ma- prefix', () => {
    // Global classes it is allowed to restyle, always under an .ma scope or beside an .ma- class.
    const shared = new Set(['ma', 'btn', 'rsel-option-row-main'])
    const selectors = rules
      .split('}')
      .map((chunk) => chunk.split('{')[0])
      .filter((s) => s.trim() && !s.trim().startsWith('@'))
    const classes = new Set(selectors.flatMap((s) => [...s.matchAll(/\.([a-zA-Z][\w-]*)/g)].map((m) => m[1])))
    for (const name of classes) {
      if (!shared.has(name)) expect(name).toMatch(/^ma-/)
    }
    // A global class is never restyled on its own.
    for (const s of selectors) {
      if (/\.btn\b|\.rsel-/.test(s)) expect(s).toMatch(/\.ma\b|\.ma-/)
    }
  })

  it('never removes a focus outline', () => {
    expect(rules).not.toMatch(/outline\s*:\s*(0|none)\b/)
  })

  it('draws the chosen segment with fill, inner edge and weight, and no accent', () => {
    const on = block('.ma-seg-btn[aria-checked="true"]')
    expect(on).toMatch(/background:\s*var\(--bg-4\)/)
    expect(on).toMatch(/box-shadow:\s*inset/)
    expect(on).toMatch(/font-weight:\s*600/)
    expect(on).not.toMatch(/--accent|--hal0-accent|--ok|--warn|--err|--info/)
  })

  it('never tints a stat value', () => {
    expect(block('.ma-stat-value')).toMatch(/color:\s*var\(--fg\)/)
    expect(rules).not.toMatch(/\.ma-stat-value[^{]*\.(ok|warn|err|info)/)
  })

  it('gives a disabled button a flat, legible fill in place of the global dimming', () => {
    const disabled = block('.ma .btn[disabled]')
    expect(disabled).toMatch(/opacity:\s*1/)
    expect(disabled).toMatch(/background:\s*var\(--bg-3\)/)
    expect(disabled).toMatch(/color:\s*var\(--fg-3\)/)
  })

  it('keeps the notice bar neutral', () => {
    expect(block('.ma-notice')).not.toMatch(/--ok|--warn|--err|--info|--accent/)
  })
})
