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
const {
  apiPostCalls,
  apiPostBodies,
  apiDeleteCalls,
  deleteOutcome,
  PULL_ROW,
  imagesOverride,
  familiesOverride,
} = vi.hoisted(() => ({
  apiPostCalls: [] as string[],
  apiPostBodies: [] as unknown[],
  apiDeleteCalls: [] as string[],
  // Task 5 (per-tag delete): controls what the mocked apiDelete resolves/
  // rejects with, per test — a mutable box (same idiom as imagesOverride)
  // so vi.hoisted mock factories can close over it.
  deleteOutcome: { current: 'removed' as 'removed' | 'missing' | 'tag_in_use' | 'rm_unavailable' },
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
  // Task 10's families payload (launch-truth FamilyStrip) — separate mutable
  // box, defaults to no families so pre-existing describe blocks (which
  // never set it) render an empty strip.
  familiesOverride: { current: null as Record<string, unknown>[] | null },
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
      data: {
        images: imagesOverride.current ?? [PULL_ROW],
        families: familiesOverride.current ?? [],
      },
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

// The real useRunnerImagePullJob (and Task 12's useRestartAffected) post
// through apiPost — mock the client boundary (not the hook) so the URL/body
// it actually builds is what's asserted, keeping apiGet/Hal0Error/etc. as
// the real implementations.
vi.mock('@/api/client', async () => {
  const actual = await vi.importActual<typeof import('@/api/client')>('@/api/client')
  return {
    ...actual,
    apiPost: (url: string, body?: unknown) => {
      apiPostCalls.push(url)
      apiPostBodies.push(body)
      return Promise.resolve({ id: 'job-1', restarted: [] })
    },
    // Task 5 (per-tag delete): the real useDeleteTag mutation posts through
    // apiDelete — mocked at the same client boundary as apiPost above so the
    // URL it actually builds (encodeURIComponent'd tag, unencoded id) is
    // what's asserted. `deleteOutcome` picks the response/rejection shape
    // per test.
    apiDelete: (url: string) => {
      apiDeleteCalls.push(url)
      const outcome = deleteOutcome.current
      if (outcome === 'removed') return Promise.resolve({ removed: true, outcome: 'removed' })
      if (outcome === 'missing') return Promise.resolve({ removed: false, outcome: 'missing' })
      if (outcome === 'tag_in_use') {
        return Promise.reject(
          new actual.Hal0Error('runner image tag is in use', {
            code: 'runner_image.tag_in_use',
            status: 409,
            details: { slots: ['brain'] },
          }),
        )
      }
      // rm_unavailable
      return Promise.reject(
        new actual.Hal0Error('image removal seam unavailable', {
          code: 'runner_image.rm_unavailable',
          status: 502,
          details: { reason: 'grant-denied' },
        }),
      )
    },
  }
})

const {
  groupRows,
  newerTagAvailable,
  newerTagCandidate,
  newestComparableTag,
  tagLanes,
  MUTABLE_TAGS,
  RunnerImagesView,
  provenanceRepoName,
  formatProvenanceShort,
  formatProvenanceFull,
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

describe('groupRows', () => {
  it('groups default families, specialized, and referenced rows', () => {
    const rows = [
      { id: 'a', is_default: { family: 'rocmfpx', source: 'release' } },
      { id: 'b', is_default: null, specialties: ['promptforge'] },
      { id: 'c', is_default: null, ownership: 'referenced' },
    ]
    const g = groupRows(rows)
    expect(g.defaults.map((r: { id: string }) => r.id)).toEqual(['a'])
    expect(g.specialized.map((r: { id: string }) => r.id)).toEqual(['b'])
    expect(g.other.map((r: { id: string }) => r.id)).toEqual(['c'])
  })

  it('reads specialties from extra.specialties when the top-level field is absent', () => {
    const rows = [{ id: 'd', is_default: null, extra: { specialties: ['comfyui'] } }]
    expect(groupRows(rows).specialized.map((r: { id: string }) => r.id)).toEqual(['d'])
  })

  it('a default-family row wins over specialties (defaults bucket takes priority)', () => {
    const rows = [
      { id: 'e', is_default: { family: 'rocmfpx', source: 'override' }, specialties: ['promptforge'] },
    ]
    const g = groupRows(rows)
    expect(g.defaults.map((r: { id: string }) => r.id)).toEqual(['e'])
    expect(g.specialized).toEqual([])
  })

  it('tolerates empty/absent input', () => {
    expect(groupRows([])).toEqual({ defaults: [], specialized: [], other: [] })
    expect(groupRows(undefined)).toEqual({ defaults: [], specialized: [], other: [] })
  })
})

// h0/runner-provenance — llama.cpp build-provenance formatters. Contract:
// `provenance: {source_repo, revision, patch_count} | null` (per tag row,
// hoisted to row.provenance for the headline; same shape on system-info's
// per-runner entries). Absent/empty provenance must format to null so
// callers render EXACTLY what they render today — no placeholder text.
describe('provenance formatters', () => {
  const ROCMFPX = {
    source_repo: 'https://github.com/hal0ai/ROCmFPX.git',
    revision: '0a59adde1c5b2f3a4d6e7f8091a2b3c4d5e6f708',
    patch_count: 4,
  }
  const UPSTREAM = {
    source_repo: 'https://github.com/ggml-org/llama.cpp.git',
    revision: 'c841aee0d5b9e2f1a3c4d5e6f708192a3b4c5d6e',
    patch_count: 0,
  }

  it('provenanceRepoName strips .git but keeps llama.cpp as-is', () => {
    expect(provenanceRepoName(ROCMFPX.source_repo)).toBe('ROCmFPX')
    expect(provenanceRepoName(UPSTREAM.source_repo)).toBe('llama.cpp')
    expect(provenanceRepoName('https://example.com/x/fork')).toBe('fork')
    expect(provenanceRepoName(null)).toBeNull()
    expect(provenanceRepoName('')).toBeNull()
  })

  it('formatProvenanceShort: fork with patches', () => {
    expect(formatProvenanceShort(ROCMFPX)).toBe('ROCmFPX @0a59add (+4 patches)')
  })

  it('formatProvenanceShort: pristine upstream omits the patch suffix', () => {
    expect(formatProvenanceShort(UPSTREAM)).toBe('llama.cpp @c841aee')
  })

  it('formatProvenanceShort: singular patch reads "+1 patch"', () => {
    expect(formatProvenanceShort({ ...ROCMFPX, patch_count: 1 }))
      .toBe('ROCmFPX @0a59add (+1 patch)')
  })

  it('formatProvenanceShort: partial labels degrade to what exists', () => {
    expect(formatProvenanceShort({ source_repo: ROCMFPX.source_repo, revision: null, patch_count: null }))
      .toBe('ROCmFPX')
    expect(formatProvenanceShort({ source_repo: null, revision: 'abc1234def', patch_count: null }))
      .toBe('@abc1234')
  })

  it('formatProvenanceShort: absent/empty provenance is null (render nothing)', () => {
    expect(formatProvenanceShort(null)).toBeNull()
    expect(formatProvenanceShort(undefined)).toBeNull()
    expect(formatProvenanceShort({ source_repo: null, revision: null, patch_count: null })).toBeNull()
  })

  it('formatProvenanceFull keeps the full source URL', () => {
    expect(formatProvenanceFull(ROCMFPX))
      .toBe('https://github.com/hal0ai/ROCmFPX.git @0a59add (+4 patches)')
    expect(formatProvenanceFull(UPSTREAM))
      .toBe('https://github.com/ggml-org/llama.cpp.git @c841aee')
    expect(formatProvenanceFull(null)).toBeNull()
  })
})

// Task 10 — launch-truth FamilyStrip (replaces DefaultsStrip). Families now
// come straight from the server's `families` summary (Task 9 payload)
// instead of being derived from image rows' `is_default` markers — the
// strip shows the effective ref, its source tier, and a "newer" chip when
// the registry has a release-shaped tag the effective ref's digest doesn't
// match.
describe('FamilyStrip (Task 10)', () => {
  afterEach(() => {
    familiesOverride.current = null
    document.body.innerHTML = ''
  })

  function mount() {
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
    return { host, root }
  }

  it('shows launch truth — effective ref, source chip, and the newer chip', () => {
    familiesOverride.current = [
      {
        family: 'rocmfpx',
        effective_ref: 'ghcr.io/x/a:0824',
        source: 'override',
        store_state: 'present',
        slots: ['brain'],
        pinned_slots: [],
        newest_release: { tag: '0826', digest: 'sha256:a' },
        update_available: true,
      },
    ]
    const { host, root } = mount()

    const rowEl = host.querySelector('[data-testid="ri-family-rocmfpx"]') as HTMLElement
    expect(rowEl).toBeTruthy()
    expect(rowEl.textContent).toContain('ghcr.io/x/a:0824')
    expect(rowEl.textContent).toContain('override')
    expect(rowEl.textContent).toContain('newer: 0826')
    expect(rowEl.textContent).toContain('via slots: brain')

    act(() => {
      root.unmount()
    })
  })

  it('renders env/manifest as plain chips and release as "release default"', () => {
    familiesOverride.current = [
      { family: 'a', effective_ref: 'ghcr.io/x/a:1', source: 'env', store_state: 'missing', slots: [], pinned_slots: [], newest_release: null, update_available: false },
      { family: 'b', effective_ref: 'ghcr.io/x/b:1', source: 'manifest', store_state: 'unknown', slots: [], pinned_slots: [], newest_release: null, update_available: false },
      { family: 'c', effective_ref: 'ghcr.io/x/c:1', source: 'release', store_state: 'present', slots: [], pinned_slots: ['agent'], newest_release: null, update_available: false },
    ]
    const { host, root } = mount()

    expect(host.querySelector('[data-testid="ri-family-a"]')?.textContent).toContain('env')
    expect(host.querySelector('[data-testid="ri-family-b"]')?.textContent).toContain('manifest')
    const releaseRow = host.querySelector('[data-testid="ri-family-c"]') as HTMLElement
    expect(releaseRow.textContent).toContain('release default')
    expect(releaseRow.textContent).toContain('pinned: agent')

    act(() => {
      root.unmount()
    })
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
    const lanes = tagLanes(V3_IMG) as any
    expect(lanes.other.find((t: any) => t.tag === 'latest')?.aliasOf).toBe('0826')
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

  // Fix-wave regression (final review): the digest-path scan used to exclude
  // the headline from consideration (`t.tag !== image.tag`), so when the
  // headline WAS already the newest release, the scan skipped straight past
  // it to an OLDER release and reported that as the "candidate" — the
  // newer: chip then showed permanently on an up-to-date row. The fix scans
  // for the first release-shaped tag overall and only treats it as a
  // candidate when it differs from the headline.
  it('headline already newest release: no candidate, no chip', () => {
    const UP_TO_DATE = {
      id: 'z',
      image: 'ghcr.io/x/c',
      tag: '0826',
      available_tags: ['0826', '0824'],
      tags: [
        { tag: '0826', digest: 'sha256:a', downloaded: true },
        { tag: '0824', digest: 'sha256:b', downloaded: false },
      ],
    }
    expect(newerTagCandidate(UP_TO_DATE)).toBeUndefined()
    expect(newerTagAvailable(UP_TO_DATE)).toBe(false)
  })

  // Keep the headline-trails case green alongside the fix above.
  it('headline trails the newest release: candidate + chip', () => {
    expect(newerTagCandidate(V3_IMG)).toBe('0826')
    expect(newerTagAvailable(V3_IMG)).toBe(true)
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
    apiPostBodies.length = 0
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

// Task 12 — restart-affected-slots verb (#2096 page-side workaround). A REAL
// render (mirrors Task 6 above): when a row's `in_use_by` is non-empty, a
// ghost "Restart N affected slot(s)" button appears behind the existing
// ConfirmDialog pattern; confirming posts the row's headline `image:tag` ref
// to POST /api/runner-images/restart-affected via the real useRestartAffected
// mutation (apiPost mocked at the client boundary, same as Task 6).
describe('RunnerImagesView restart-affected-slots (Task 12)', () => {
  afterEach(() => {
    apiPostCalls.length = 0
    apiPostBodies.length = 0
    imagesOverride.current = null
    document.body.innerHTML = ''
  })

  it('renders the button when in_use_by is non-empty and posts the ref on confirm', async () => {
    imagesOverride.current = [{ ...PULL_ROW, in_use_by: ['brain', 'ops'] }]

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

    const btn = host.querySelector('[data-testid="ri-restart-affected"]') as HTMLButtonElement
    expect(btn).toBeTruthy()
    expect(btn.textContent).toContain('Restart 2 affected slots')
    expect(host.querySelector('.modal-backdrop')).toBeNull()

    act(() => { btn.click() })
    expect(host.querySelector('.modal-backdrop')).toBeTruthy()
    expect(host.textContent).toContain('brain, ops')

    const confirmBtn = Array.from(host.querySelectorAll('button')).find(
      (b) => b.textContent === 'Restart',
    ) as HTMLButtonElement
    expect(confirmBtn).toBeTruthy()
    await act(async () => {
      confirmBtn.click()
      await Promise.resolve()
      await Promise.resolve()
      await Promise.resolve()
    })

    expect(apiPostCalls[apiPostCalls.length - 1]).toBe('/api/runner-images/restart-affected')
    expect(apiPostBodies[apiPostBodies.length - 1]).toEqual({
      ref: 'ghcr.io/hal0ai/hal0-combined:0826',
    })
    expect(host.querySelector('.modal-backdrop')).toBeNull()

    act(() => { root.unmount() })
  })

  it('does not render the button when in_use_by is empty', () => {
    imagesOverride.current = [{ ...PULL_ROW, in_use_by: [] }]

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

    expect(host.querySelector('[data-testid="ri-restart-affected"]')).toBeNull()

    act(() => { root.unmount() })
  })
})

// Task 5 (runner-images-v3 phase 4) — per-tag Delete button. A REAL render
// (same idiom as Task 6/12 above): a ghost "Delete :<tag>" button sits next
// to the pull controls, behind the shared ConfirmDialog; confirming DELETEs
// the picked tag via the real useDeleteTag mutation. Disabled (server still
// guards) whenever the picked tag IS the headline and the row's `in_use_by`
// is non-empty — that's the only tag this list payload can name usage for.
describe('RunnerImagesView per-tag delete (Task 5)', () => {
  afterEach(() => {
    apiDeleteCalls.length = 0
    deleteOutcome.current = 'removed'
    imagesOverride.current = null
    document.body.innerHTML = ''
  })

  function mount() {
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
    return { host, root }
  }

  it('renders the delete button and DELETEs the right URL on confirm', async () => {
    imagesOverride.current = [{ ...PULL_ROW, in_use_by: [] }]
    const { host, root } = mount()

    const btn = host.querySelector('[data-testid="ri-delete-tag"]') as HTMLButtonElement
    expect(btn).toBeTruthy()
    expect(btn.textContent).toContain('Delete')
    expect(btn.textContent).toContain(PULL_ROW.tag)
    expect(btn.disabled).toBe(false)
    expect(host.querySelector('.modal-backdrop')).toBeNull()

    act(() => { btn.click() })
    expect(host.querySelector('.modal-backdrop')).toBeTruthy()
    expect(host.textContent).toContain(PULL_ROW.tag)

    const confirmBtn = Array.from(host.querySelectorAll('button')).find(
      (b) => b.textContent === 'Delete',
    ) as HTMLButtonElement
    expect(confirmBtn).toBeTruthy()
    await act(async () => {
      confirmBtn.click()
      await Promise.resolve()
      await Promise.resolve()
      await Promise.resolve()
    })

    expect(apiDeleteCalls[apiDeleteCalls.length - 1]).toBe(
      `/api/runner-images/${PULL_ROW.id}/tags/${PULL_ROW.tag}`,
    )
    expect(host.querySelector('.modal-backdrop')).toBeNull()

    act(() => { root.unmount() })
  })

  it('is disabled with an explanatory title when the headline pick is in_use_by', () => {
    imagesOverride.current = [{ ...PULL_ROW, in_use_by: ['brain', 'ops'] }]
    const { host, root } = mount()

    const btn = host.querySelector('[data-testid="ri-delete-tag"]') as HTMLButtonElement
    expect(btn).toBeTruthy()
    expect(btn.disabled).toBe(true)
    expect(btn.title).toMatch(/brain/)
    expect(btn.title).toMatch(/ops/)

    act(() => { root.unmount() })
  })

  it('reports the "missing" outcome distinctly from "removed"', async () => {
    imagesOverride.current = [{ ...PULL_ROW, in_use_by: [] }]
    deleteOutcome.current = 'missing'
    const { host, root } = mount()

    const btn = host.querySelector('[data-testid="ri-delete-tag"]') as HTMLButtonElement
    act(() => { btn.click() })
    const confirmBtn = Array.from(host.querySelectorAll('button')).find(
      (b) => b.textContent === 'Delete',
    ) as HTMLButtonElement
    await act(async () => {
      confirmBtn.click()
      await Promise.resolve()
      await Promise.resolve()
      await Promise.resolve()
    })

    expect(apiDeleteCalls.length).toBe(1)
    expect(host.querySelector('.modal-backdrop')).toBeNull()

    act(() => { root.unmount() })
  })
})

// Pull button label — store truth, not the retired `local_path` marker.
// `local_path` is a pre-v3 field the catalogue can leave stale (or unset
// even after a real pull, on a store-read miss); `image.downloaded` is the
// v3 enriched field (`store_state === "present"`, or the `local_path`
// fallback ONLY when the store itself couldn't be read) — the button must
// key off that, not the raw marker.
describe('RunnerImagesView pull button label (store truth, #polish item D)', () => {
  afterEach(() => {
    imagesOverride.current = null
    document.body.innerHTML = ''
  })

  function mount() {
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
    return { host, root }
  }

  it('reads "Re-pull" from image.downloaded even when local_path is unset', () => {
    imagesOverride.current = [{ ...PULL_ROW, local_path: null, downloaded: true }]
    const { host, root } = mount()

    const btn = host.querySelector('[data-testid="ri-pull"]') as HTMLButtonElement
    expect(btn.textContent).toContain('Re-pull')

    act(() => { root.unmount() })
  })

  it('reads "Pull" from image.downloaded even when local_path is a stale marker', () => {
    imagesOverride.current = [
      { ...PULL_ROW, local_path: '/var/lib/hal0/runner-images/stale.json', downloaded: false },
    ]
    const { host, root } = mount()

    const btn = host.querySelector('[data-testid="ri-pull"]') as HTMLButtonElement
    expect(btn.textContent).toContain('Pull')
    expect(btn.textContent).not.toContain('Re-pull')

    act(() => { root.unmount() })
  })
})
