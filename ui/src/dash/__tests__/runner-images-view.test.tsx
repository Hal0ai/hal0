// Runner Images page pure helpers (runner-catalogue-v2, Task D).
//
// Fixtures are shaped to the frozen Task-C contract: /api/runner-images rows
// carry `available_tags` (newest-first), `is_default` ({family, source} |
// null) and `in_use_by` (slot names). The helpers under test drive the
// Defaults strip and the per-row "newer tag" chip; they must stay defensive
// against rows from a pre-contract backend (fields missing) rather than
// assuming Task C has merged.
//
// runner-images.jsx is a window-globals dash module (`const {...} = React`
// at module top), so the globals are installed before the dynamic import —
// same pattern as memoryOverviewV2.smoke.test.tsx.
import React from 'react'
import { describe, expect, it } from 'vitest'

;(globalThis as unknown as { window: typeof globalThis }).window = globalThis
;(globalThis as unknown as { React: typeof React }).React = React

const { defaultsStripRows, newerTagAvailable, newestComparableTag, MUTABLE_TAGS } = await import(
  '../runner-images.jsx'
)

// Minimal contract-shaped row; overrides per case.
function row(overrides: Record<string, unknown> = {}) {
  return {
    id: 'rocmfpx-combined',
    image: 'ghcr.io/hal0ai/hal0-combined',
    tag: '0824',
    available_tags: ['0824', '0822'],
    is_default: { family: 'rocmfpx', source: 'release' },
    in_use_by: ['agent', 'utility'],
    ...overrides,
  }
}

describe('defaultsStripRows', () => {
  it('emits one {family, ref, source} row per family default, sorted by family', () => {
    const images = [
      row(),
      row({
        id: 'kokoro',
        image: 'ghcr.io/hal0ai/hal0-kokoro',
        tag: 'v1.2',
        available_tags: ['v1.2'],
        is_default: { family: 'kokoro', source: 'override' },
        in_use_by: [],
      }),
    ]
    expect(defaultsStripRows(images)).toEqual([
      { family: 'kokoro', ref: 'ghcr.io/hal0ai/hal0-kokoro:v1.2', source: 'override' },
      { family: 'rocmfpx', ref: 'ghcr.io/hal0ai/hal0-combined:0824', source: 'release' },
    ])
  })

  it('skips rows that are no family default (is_default null or missing)', () => {
    const images = [row({ is_default: null }), row({ id: 'x', is_default: undefined })]
    expect(defaultsStripRows(images)).toEqual([])
  })

  it('keeps the first row per family when several rows carry the same family', () => {
    const images = [
      row(),
      row({ id: 'rocmfpx-hy3', image: 'ghcr.io/hal0ai/hal0-rocmfpx-hy3', tag: 'hy3' }),
    ]
    const rows = defaultsStripRows(images)
    expect(rows).toHaveLength(1)
    expect(rows[0].ref).toBe('ghcr.io/hal0ai/hal0-combined:0824')
  })

  it('tolerates empty/absent input', () => {
    expect(defaultsStripRows([])).toEqual([])
    expect(defaultsStripRows(undefined)).toEqual([])
  })
})

describe('newerTagAvailable', () => {
  it('is true when the newest available tag differs from the headline tag', () => {
    expect(newerTagAvailable(row({ tag: '0822', available_tags: ['0824', '0822'] }))).toBe(true)
  })

  it('is false when the headline already is the newest tag', () => {
    expect(newerTagAvailable(row())).toBe(false)
  })

  it('is false on probe failure (available_tags empty) or missing fields', () => {
    expect(newerTagAvailable(row({ available_tags: [] }))).toBe(false)
    expect(newerTagAvailable(row({ available_tags: undefined }))).toBe(false)
    expect(newerTagAvailable(row({ tag: null }))).toBe(false)
    expect(newerTagAvailable(undefined)).toBe(false)
  })

  // Bug 2 — live dashboard observed the "newer" chip firing on mutable
  // branch/floating tags: comfyui headlined `latest` but showed
  // `newer: main` (a CI branch tag re-pushed every build); rocmfpx
  // headlined a pinned tag but showed `newer: server` (an old floating
  // dev tag). `main`/`server` sort ahead of the real headline only
  // because they're first in GHCR's registry-order "rest" bucket — never
  // because they're an actually newer build.
  it('ignores mutable-pointer tags (main/master/server/edge/nightly) as "newer" candidates', () => {
    for (const mutable of MUTABLE_TAGS) {
      expect(
        newerTagAvailable(row({ tag: '0824', available_tags: [mutable, '0824', '0822'] }))
      ).toBe(false)
    }
  })

  it('ignores `latest` as a "newer" candidate when latest is not the headline', () => {
    expect(newerTagAvailable(row({ tag: '0824', available_tags: ['latest', '0824'] }))).toBe(
      false
    )
  })

  it('does not exclude `latest` from comparison when latest IS the headline', () => {
    // comfyui: headline `latest`, registry lists CI branch tag `main`
    // ahead of it — `main` must be skipped, `latest` must stay eligible
    // (though here it's also the headline, so still no false positive).
    expect(newerTagAvailable(row({ tag: 'latest', available_tags: ['main', 'latest'] }))).toBe(
      false
    )
  })

  it('still reports a real newer tag once mutable pointers are skipped', () => {
    expect(
      newerTagAvailable(row({ tag: '0822', available_tags: ['main', '0824', '0822'] }))
    ).toBe(true)
  })

  it('newestComparableTag skips mutable pointers to find the display candidate', () => {
    expect(newestComparableTag(row({ tag: '0822', available_tags: ['server', '0824', '0822'] }))).toBe(
      '0824'
    )
  })
})
