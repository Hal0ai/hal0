// Documentation links leave the box (#4109).
//
// hal0 does not serve its documentation: the API's single-page fallback answers any non-API path
// with the dashboard's index.html. A link to `/docs/...` therefore opens the dashboard again.
import { describe, expect, it } from 'vitest'

const sources = import.meta.glob('../**/*.{jsx,tsx}', {
  query: '?raw',
  import: 'default',
  eager: true,
}) as Record<string, string>

const production = Object.entries(sources).filter(
  ([file]) => !file.includes('__tests__') && !/\.test\.[jt]sx?$/.test(file),
)

describe('documentation links (#4109)', () => {
  it('no dashboard link points at a /docs path on the box', () => {
    const local: string[] = []
    for (const [file, text] of production) {
      for (const m of text.matchAll(/href=(?:"|\{`|\{")(\/docs[^"`]*)/g)) local.push(`${file}: ${m[1]}`)
    }
    expect(production.length).toBeGreaterThan(50)
    expect(local).toEqual([])
  })

  it('the NPU driver guide is linked on the public site', () => {
    const text = sources['../npu/NpuTabs.jsx']
    expect(text).toContain('href="https://hal0.dev/docs/getting-started/drivers/"')
  })
})
