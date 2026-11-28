// Every internal link in the dashboard opens a real page (#4107).
//
// parseRoute sends an unknown address to the Overview without a word, so a link to a page that
// was renamed or never existed looks like a broken button. This scans the dashboard's sources.
import { describe, expect, it } from 'vitest'
import { ROUTES, isKnownRoute } from '../routes'

const sources = import.meta.glob('../**/*.{jsx,tsx}', {
  query: '?raw',
  import: 'default',
  eager: true,
}) as Record<string, string>

const production = Object.entries(sources).filter(
  ([file]) => !file.includes('__tests__') && !/\.test\.[jt]sx?$/.test(file),
)

/** `href="#page/..."` literals, and template literals whose address starts with a fixed head. */
function internalLinks(text: string): string[] {
  const found: string[] = []
  for (const m of text.matchAll(/href=(?:"|\{`)#([A-Za-z][^"`$\s]*)/g)) found.push(m[1])
  return found
}

/** `route: "page/..."` entries, the command palette's jump targets. */
function paletteRoutes(text: string): string[] {
  return [...text.matchAll(/\broute:\s*"([^"]+)"/g)].map(m => m[1])
}

describe('dashboard routes (#4107)', () => {
  it('reads a useful number of sources', () => {
    expect(production.length).toBeGreaterThan(50)
  })

  it('every internal href opens a real page', () => {
    const dead: string[] = []
    let seen = 0
    for (const [file, text] of production) {
      for (const link of internalLinks(text)) {
        seen += 1
        if (!isKnownRoute(link)) dead.push(`${file}: #${link}`)
      }
    }
    expect(seen).toBeGreaterThan(15)
    expect(dead).toEqual([])
  })

  it('every command palette route opens a real page', () => {
    const text = sources['../command-palette.jsx']
    expect(text).toBeTruthy()
    const routes = paletteRoutes(text)
    expect(routes.length).toBeGreaterThan(8)
    expect(routes.filter(r => !isKnownRoute(r))).toEqual([])
  })
})

describe('isKnownRoute', () => {
  it('accepts each page, with a sub-path or a query', () => {
    for (const r of ROUTES) {
      expect(isKnownRoute(r)).toBe(true)
      expect(isKnownRoute(`#${r}/sub/path?x=1`)).toBe(true)
    }
  })

  it('refuses the three addresses that used to be linked', () => {
    expect(isKnownRoute('#agents')).toBe(false)
    expect(isKnownRoute('hardware')).toBe(false)
    expect(isKnownRoute('#profiles')).toBe(false)
  })

  it('knows the one alias the router understands', () => {
    expect(isKnownRoute('#agents/mcp')).toBe(true)
    expect(isKnownRoute('#agents/other')).toBe(false)
  })
})
