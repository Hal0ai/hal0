// @vitest-environment happy-dom
//
// Runner Images page pure helpers (runner-catalogue-v2, Task D), plus a real
// render test for Task 6 (per-tag pull) — see that describe block below for
// why this file needs a DOM environment now.
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
import { createRoot } from 'react-dom/client'
import { act } from 'react-dom/test-utils'
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { afterEach, describe, expect, it, vi } from 'vitest'

;(globalThis as unknown as { window: typeof globalThis }).window = globalThis
;(globalThis as unknown as { React: typeof React }).React = React
// Icon glyphs come from chrome.jsx's window global — irrelevant here.
;(globalThis as unknown as { Icons: unknown }).Icons = new Proxy({}, { get: () => null })

// Task 6 (per-tag pull) render test below needs the REAL useRunnerImagePullJob
// (it's the thing under test — the hook must build `?tag=` into the pull
// POST) while the list/sync/default hooks stay stubbed, contract-shaped, and
// side-effect-free. vi.mock factories are hoisted above imports, so the spy +
// fixture rows they close over must be hoisted too (mirrors
// runner-images-confirm-flow.test.tsx's `vi.hoisted` idiom).
const { apiPostCalls, PULL_ROW, imagesOverride } = vi.hoisted(() => ({
  apiPostCalls: [] as string[],
  PULL_ROW: {
    id: 'rocmfpx-combined',
    image: 'ghcr.io/hal0ai/hal0-combined',
    tag: '0826',
    digest: null,
    size_bytes: 7_340_032_000,
    manifest_key: null,
    ownership: 'owned',
    publish: 'external',
    notes: null,
    build: null,
    local_path: null,
    downloaded_at: null,
    discovered_at: null,
    updated_at: null,
    extra: {},
    available_tags: ['0826', '0824'],
    is_default: null,
    in_use_by: [],
  },
  // Task 8 render test swaps in a v3-shaped row (tags[]/badges/store_state)
  // without disturbing PULL_ROW (Task 6's per-tag-pull test asserts against
  // it directly) — a mutable box the mock reads lazily each call.
  imagesOverride: { current: null as Record<string, unknown>[] | null },
}))

vi.mock('@/api/hooks/useRunnerImages', async () => {
  const actual =
    await vi.importActual<typeof import('@/api/hooks/useRunnerImages')>(
      '@/api/hooks/useRunnerImages',
    )
  return {
    ...actual,
    // useRunnerImagePullJob is left as the real implementation — it's what
    // Task 6 wires up.
    useRunnerImages: () => ({
      data: imagesOverride.current ?? [PULL_ROW],
      isPending: false,
      isError: false,
      error: null,
    }),
    useRunnerImageSync: () => ({ isPending: false, mutateAsync: async () => ({ images: [PULL_ROW] }) }),
    useRunnerImagePullsList: () => ({ data: [] }),
    useDownloadedRunnerImages: () => ({ data: [] }),
    useRunnerImage: () => ({ data: null }),
    useSetDefaultImage: () => ({ isPending: false, mutate: () => {} }),
  }
})

// The real useRunnerImagePullJob posts through apiPost — mock the client
// boundary (not the hook) so the URL it actually builds is what's asserted,
// keeping apiGet/Hal0Error/etc. as the real implementations.
vi.mock('@/api/client', async () => {
  const actual = await vi.importActual<typeof import('@/api/client')>('@/api/client')
  return {
    ...actual,
    apiPost: (url: string) => {
      apiPostCalls.push(url)
      return Promise.resolve({ id: 'job-1' })
    },
  }
})

const {
  defaultsStripRows,
  newerTagAvailable,
  newerTagCandidate,
  newestComparableTag,
  tagLanes,
  MUTABLE_TAGS,
  RunnerImagesView,
} = await import('../runner-images.jsx')

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

// Task 8 — three-lane tag picker + digest-aware "newer" over the v3 payload
// (tags[].{tag,digest,downloaded}, badges). tagLanes replaces tagChoices;
// newerTagAvailable becomes a digest fact when the row carries `tags`,
// falling back to the pre-v3 newestComparableTag() name-based heuristic
// (exercised by the `newerTagAvailable` describe block above) when it
// doesn't.
const V3_IMG = {
  id: 'x',
  image: 'ghcr.io/x/a',
  tag: '0824',
  available_tags: ['0826', '0824', 'latest', 'main'],
  tags: [
    { tag: '0826', digest: 'sha256:a', downloaded: false },
    { tag: '0824', digest: 'sha256:b', downloaded: true },
    { tag: 'latest', digest: 'sha256:a', downloaded: false },
    { tag: 'main', digest: 'sha256:c', downloaded: false },
  ],
  badges: { '0826': 'validated' },
}

describe('tagLanes', () => {
  it('buckets release / pin / other lanes', () => {
    const lanes = tagLanes(V3_IMG)
    expect(lanes.releases.map((t: { tag: string }) => t.tag)).toEqual(['0826', '0824'])
    expect(lanes.other.map((t: { tag: string }) => t.tag)).toEqual(['latest', 'main'])
  })

  it('collapses digest aliases', () => {
    const lanes = tagLanes(V3_IMG)
    expect(lanes.other.find((t: { tag: string }) => t.tag === 'latest').aliasOf).toBe('0826')
  })
})

describe('newerTagAvailable (digest-aware, v3 payload)', () => {
  it('is a digest fact', () => {
    expect(newerTagAvailable(V3_IMG)).toBe(true) // 0826 digest ≠ 0824's
    const same = { ...V3_IMG, tags: V3_IMG.tags.map(t => ({ ...t, digest: 'sha256:b' })) }
    expect(newerTagAvailable(same)).toBe(false) // all aliases of headline
  })

  // Fix round 1 (review): the verdict (newerTagAvailable) and the displayed
  // name (newerTagCandidate, used by the "newer: X" chip) must agree even
  // when `available_tags` and `tags[]` disagree — e.g. a per-tag digest
  // probe that hasn't caught up with a registry-order listing yet. Before
  // the fix, the chip's name came from a SEPARATE scan of `available_tags`
  // (newestComparableTag) under a different filter than the verdict's
  // `tags[]` scan, so it could name a tag that was never actually compared
  // — here, a tag ('0827') that isn't even present in `tags[]`.
  it('newerTagCandidate names the same tag the verdict actually compared, even when available_tags disagrees', () => {
    const DIVERGENT = {
      id: 'y',
      image: 'ghcr.io/x/b',
      tag: '0824',
      // Registry-order listing already has 0827; the per-tag digest
      // resolver hasn't caught up (tags[] below stops at 0826).
      available_tags: ['0827', '0826', '0824'],
      tags: [
        { tag: '0826', digest: 'sha256:a', downloaded: false },
        { tag: '0824', digest: 'sha256:b', downloaded: true },
      ],
    }
    // Sanity: the two lists really do disagree on the "newest" name.
    expect(newestComparableTag(DIVERGENT)).toBe('0827')
    expect(newerTagAvailable(DIVERGENT)).toBe(true) // 0826 digest ≠ 0824's
    expect(newerTagCandidate(DIVERGENT)).toBe('0826') // not '0827' — that tag was never compared
  })
})

// Real render — proves the <select> groups tags into <optgroup>s (other
// collapsed behind "show all tags"), and the header/row chips read
// store_state/downloaded truth instead of the retired
// local_path/downloaded_at markers.
describe('RunnerCard tag picker + store-truth chips (Task 8)', () => {
  afterEach(() => {
    imagesOverride.current = null
    document.body.innerHTML = ''
  })

  it('renders releases/pins optgroups, hides other until "show all tags", and shows the downloaded chip', async () => {
    imagesOverride.current = [
      {
        ...V3_IMG,
        digest: 'sha256:b',
        size_bytes: 1000,
        manifest_key: null,
        ownership: 'owned',
        publish: 'external',
        notes: null,
        build: null,
        local_path: null,
        downloaded_at: null,
        discovered_at: null,
        updated_at: null,
        extra: {},
        store_state: 'present',
        downloaded: true,
        store_context: 'rootful',
        is_default: null,
        in_use_by: [],
      },
    ]
    const host = document.createElement('div')
    document.body.appendChild(host)
    const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } })
    const root = createRoot(host)
    act(() => {
      root.render(
        React.createElement(
          QueryClientProvider,
          { client: qc },
          React.createElement(RunnerImagesView),
        ),
      )
    })

    const select = host.querySelector('[data-testid="ri-tag-pick"]') as HTMLSelectElement
    expect(select).toBeTruthy()
    let groups = Array.from(select.querySelectorAll('optgroup')).map(g => g.label)
    expect(groups).toEqual(['releases']) // pins empty (headline "0824" lands in releases); other collapsed

    const showAllBtn = host.querySelector('[data-testid="ri-show-all-tags"]') as HTMLButtonElement
    expect(showAllBtn).toBeTruthy()
    act(() => {
      showAllBtn.click()
    })
    groups = Array.from(select.querySelectorAll('optgroup')).map(g => g.label)
    expect(groups).toEqual(['releases', 'other'])

    const chips = Array.from(host.querySelectorAll('[data-testid="ri-store-state"]')).map(
      c => c.textContent,
    )
    expect(chips.length).toBeGreaterThan(0)
    expect(chips.every(t => t === '✓ downloaded')).toBe(true)

    act(() => {
      root.unmount()
    })
  })

  // Fix round 1 (review): the "other" optgroup used to be gated on
  // `showAllTags` alone, so picking a tag from it and then toggling the
  // "show all tags" button back OFF unmounted the very <option> the
  // controlled <select>'s value pointed at — the browser silently falls
  // back to the first rendered option while React's state still holds the
  // now-invisible pick, desyncing the UI from what would actually get
  // pulled. The "other" optgroup must stay rendered whenever the current
  // pick lives there, toggle state notwithstanding.
  it('keeps a picked other-lane tag selectable after toggling "show all tags" back off', async () => {
    imagesOverride.current = [
      {
        ...V3_IMG,
        digest: 'sha256:b',
        size_bytes: 1000,
        manifest_key: null,
        ownership: 'owned',
        publish: 'external',
        notes: null,
        build: null,
        local_path: null,
        downloaded_at: null,
        discovered_at: null,
        updated_at: null,
        extra: {},
        store_state: 'present',
        downloaded: true,
        store_context: 'rootful',
        is_default: null,
        in_use_by: [],
      },
    ]
    const host = document.createElement('div')
    document.body.appendChild(host)
    const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } })
    const root = createRoot(host)
    act(() => {
      root.render(
        React.createElement(
          QueryClientProvider,
          { client: qc },
          React.createElement(RunnerImagesView),
        ),
      )
    })

    const select = host.querySelector('[data-testid="ri-tag-pick"]') as HTMLSelectElement
    const showAllBtn = host.querySelector('[data-testid="ri-show-all-tags"]') as HTMLButtonElement

    act(() => {
      showAllBtn.click() // reveal "other" (latest, main)
    })
    act(() => {
      select.value = 'latest'
      select.dispatchEvent(new Event('change', { bubbles: true }))
    })
    act(() => {
      showAllBtn.click() // toggle back off — "latest" is still the pick
    })

    const groups = Array.from(select.querySelectorAll('optgroup')).map(g => g.label)
    expect(groups).toContain('other') // stays rendered because the pick lives there
    expect(select.value).toBe('latest') // controlled value survives the toggle

    act(() => {
      root.unmount()
    })
  })
})

// Task 6 — the backend's POST /api/runner-images/{id}/pull?tag= route already
// exists; the UI never sent it and disabled the Pull button on a non-headline
// pick instead. A REAL render (mirrors runner-images-confirm-flow.test.tsx),
// so the fix is proven end to end: pick a non-headline tag → button enables
// → click → the pull job hook's real POST URL carries `?tag=`.
describe('RunnerImagesView per-tag pull (Task 6)', () => {
  afterEach(() => {
    apiPostCalls.length = 0
    document.body.innerHTML = ''
  })

  it('pulls the picked non-headline tag', async () => {
    const host = document.createElement('div')
    document.body.appendChild(host)
    const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } })
    const root = createRoot(host)
    act(() => {
      root.render(
        React.createElement(
          QueryClientProvider,
          { client: qc },
          React.createElement(RunnerImagesView),
        ),
      )
    })

    const pick = host.querySelector('[data-testid="ri-tag-pick"]') as HTMLSelectElement
    expect(pick).toBeTruthy()
    act(() => {
      pick.value = '0824'
      pick.dispatchEvent(new Event('change', { bubbles: true }))
    })

    const btn = host.querySelector('[data-testid="ri-pull"]') as HTMLButtonElement
    expect(btn).toBeTruthy()
    expect(btn.disabled).toBe(false) // was disabled pre-v3 on any tag mismatch

    await act(async () => {
      btn.click()
      await Promise.resolve()
      await Promise.resolve()
      await Promise.resolve()
    })

    expect(apiPostCalls[apiPostCalls.length - 1]).toMatch(/\/pull\?tag=0824$/)

    act(() => {
      root.unmount()
    })
  })
})
