// hal0 memory v2 (Bank workspace UI) — shared constants + Icon primitive.
//
// Task C1: self-contained shared module for Phase C (C2/C4/…). Ported
// verbatim from the design handoff prototype (data.jsx:5-12 for the
// color/label/weight maps + the fmtN helper, explore.jsx:3 for dayKey,
// shell.jsx:5-47 for the Icon glyph component) — deliberately NOT reusing
// `./chrome.jsx`'s GLYPHS map, so this module has zero dependency on the
// rest of dash/*.jsx and can be imported standalone by every Phase C view.
//
// Window-globals contract: no ES imports across dash/*.jsx — this file
// takes none, and publishes everything it defines via
// `Object.assign(window, { MemV2 })` so C2/C4/… read `window.MemV2.*`.

const FACT_COLORS = { world: '#7FB8FF', experience: '#FFB000', observation: '#6FCF97' }
const FACT_DESC = {
  world: 'stable fact / config',
  experience: 'something that happened',
  observation: 'a noticed pattern',
}

// Link palette. CORRECTION (post-smoke live check, 2026-08-21 — supersedes
// the prior "causal never appears in real payloads" claim below): a live
// production bank's memory graph was verified via Playwright + curl to emit
// {temporal, semantic, entity, caused_by} — `caused_by` is the real wire
// name for the causal relationship, not `causal`, and it DOES appear in real
// payloads (this bank's Web view rendered a working "caused_by" lens with
// real edges). `causal` itself and `cooccurrence` still only ever appear in
// the design prototype's mock graph data, not in any live payload seen so
// far, and are kept for backward compat with anything still rendering the
// prototype's mocks. `caused_by` shares `causal`'s color/label/weight family
// (same relationship, real wire spelling) rather than getting its own
// entry. `entity` uses Okabe–Ito magenta (#CC79A7, colourblind-safe, same
// accessible set as TOPIC_COLORS below) — distinct from causal's red,
// temporal/cooccurrence's amber, and semantic's blue.
const LINK_COLORS = {
  causal: '#EF6B6B',
  caused_by: '#EF6B6B',
  temporal: '#E8B94E',
  cooccurrence: '#FFB000',
  semantic: '#7FB8FF',
  entity: '#CC79A7',
}
const LINK_LABEL = {
  causal: 'led to',
  caused_by: 'led to',
  temporal: 'near in time',
  cooccurrence: 'mentioned together',
  semantic: 'related meaning',
  entity: 'shared entity',
}
const LINK_WEIGHT = { causal: 4, caused_by: 4, temporal: 3, cooccurrence: 2, semantic: 1, entity: 1 }

// Okabe–Ito, colourblind-safe (same set as the memory map)
const TOPIC_COLORS = [
  '#E69F00',
  '#56B4E9',
  '#009E73',
  '#F0E442',
  '#0072B2',
  '#D55E00',
  '#CC79A7',
  '#999999',
]

const fmtN = (n) => n.toLocaleString('en-US')

// t is a local-ISO-ish timestamp string ("YYYY-MM-DDTHH:MM…") — slices out
// "MM-DD" and reformats as "MM/DD" for compact axis/day-bucket labels.
const dayKey = (t) => t.slice(5, 10).replace('-', '/')

// ─── Icon — house 16×16 thin-line glyph set (self-contained SVG map) ───────
const GLYPHS = {
  dashboard: (
    <g>
      <rect x="2" y="2" width="5" height="5" rx="1" />
      <rect x="9" y="2" width="5" height="9" rx="1" />
      <rect x="2" y="9" width="5" height="5" rx="1" />
    </g>
  ),
  slots: (
    <g>
      <rect x="2" y="3" width="12" height="3" rx="0.5" />
      <rect x="2" y="7" width="12" height="3" rx="0.5" />
      <rect x="2" y="11" width="12" height="3" rx="0.5" />
      <circle cx="4" cy="4.5" r="0.6" fill="currentColor" stroke="none" />
      <circle cx="4" cy="8.5" r="0.6" fill="currentColor" stroke="none" />
      <circle cx="4" cy="12.5" r="0.6" fill="currentColor" stroke="none" />
    </g>
  ),
  models: (
    <g>
      <path d="M2 4l6-2 6 2-6 2-6-2z" />
      <path d="M2 8l6 2 6-2" />
      <path d="M2 12l6 2 6-2" />
    </g>
  ),
  // memory — a knowledge graph mark (the section identity)
  memory: (
    <g>
      <circle cx="4" cy="4" r="1.8" />
      <circle cx="12" cy="5" r="1.8" />
      <circle cx="7.5" cy="12" r="1.8" />
      <path d="M5.4 4.7l5.2 0.4M5.2 5.6l1.6 4.7M10.6 6.5l-2.4 4.2" />
    </g>
  ),
  agent: (
    <g>
      <circle cx="8" cy="6" r="2.5" />
      <path d="M3 14c0-2.5 2.2-4.5 5-4.5s5 2 5 4.5" />
      <circle cx="13" cy="3" r="1.5" />
    </g>
  ),
  connections: (
    <g>
      <circle cx="6" cy="8" r="2.5" />
      <circle cx="11" cy="11" r="1.5" fill="currentColor" stroke="none" />
      <path d="M8 9.5l2 1M3.5 4.5h4M3.5 6.5h3" />
    </g>
  ),
  logs: <path d="M3 3h10M3 6h10M3 9h7M3 12h5" />,
  settings: (
    <g>
      <circle cx="8" cy="8" r="2" />
      <path d="M8 1v2M8 13v2M1 8h2M13 8h2M3 3l1.5 1.5M11.5 11.5L13 13M3 13l1.5-1.5M11.5 4.5L13 3" />
    </g>
  ),
  bell: (
    <path d="M4 11h8c-1 0-1.5-0.5-1.5-2V6.5a2.5 2.5 0 0 0-5 0V9c0 1.5-0.5 2-1.5 2zM6.5 13a1.5 1.5 0 0 0 3 0" />
  ),
  search: (
    <g>
      <circle cx="7" cy="7" r="4" />
      <path d="M10 10l3 3" />
    </g>
  ),
  close: <path d="M4 4l8 8M12 4l-8 8" />,
  plus: <path d="M8 3v10M3 8h10" />,
  minus: <path d="M3 8h10" />,
  fit: (
    <path d="M2 5V3a1 1 0 0 1 1-1h2M11 2h2a1 1 0 0 1 1 1v2M14 11v2a1 1 0 0 1-1 1h-2M5 14H3a1 1 0 0 1-1-1v-2" />
  ),
  // section sub-nav glyphs
  overview: (
    <g>
      <rect x="2" y="2.5" width="12" height="4" rx="1" />
      <rect x="2" y="9" width="5.5" height="4.5" rx="1" />
      <rect x="8.5" y="9" width="5.5" height="4.5" rx="1" />
    </g>
  ),
  graph: (
    <g>
      <circle cx="4" cy="11" r="1.7" />
      <circle cx="11.5" cy="4" r="1.7" />
      <circle cx="12" cy="12" r="1.7" />
      <path d="M5.4 9.9l4.8-4.2M5.7 11.3l4.6 0.6" />
    </g>
  ),
  tools: (
    <g>
      <path d="M9.5 2.5a3 3 0 0 0 3.9 3.9l-2 2L6 13.8a1.6 1.6 0 0 1-2.3-2.3l5.4-5.4z" />
    </g>
  ),
  clock: (
    <g>
      <circle cx="8" cy="8" r="6" />
      <path d="M8 4.5V8l2.5 1.5" />
    </g>
  ),
  focus: (
    <g>
      <circle cx="8" cy="8" r="2.2" />
      <path d="M8 1.5v2.2M8 12.3v2.2M1.5 8h2.2M12.3 8h2.2" />
    </g>
  ),
  path: (
    <g>
      <circle cx="3.5" cy="12.5" r="1.5" />
      <circle cx="12.5" cy="3.5" r="1.5" />
      <path d="M4.8 11.4C7 9 6 6 9 5.2" />
    </g>
  ),
  pin: <path d="M8 1.5l1.8 4.2 4.2 0.4-3.2 2.8 1 4.1L8 10.9 4.2 13l1-4.1L2 6.1l4.2-0.4z" />,
  layers: (
    <g>
      <path d="M8 2l6 3-6 3-6-3 6-3z" />
      <path d="M2 9l6 3 6-3" />
    </g>
  ),
  scrub: (
    <g>
      <path d="M2 8h12" />
      <circle cx="6" cy="8" r="2.2" fill="currentColor" stroke="none" />
    </g>
  ),
  refresh: (
    <g>
      <path d="M14 8a6 6 0 1 1-2-4.5" />
      <path d="M14 1v3.5h-3.5" />
    </g>
  ),
  hide: (
    <g>
      <path d="M2 8s2.4-4.2 6-4.2S14 8 14 8s-2.4 4.2-6 4.2S2 8 2 8z" />
      <circle cx="8" cy="8" r="1.6" />
    </g>
  ),
  arrow: <path d="M3 8h9M8.5 4.5L12 8l-3.5 3.5" />,
  ext: (
    <g>
      <path d="M6 3H3v10h10v-3" />
      <path d="M9 3h4v4M13 3l-6 6" />
    </g>
  ),
  dot: <circle cx="8" cy="8" r="3" fill="currentColor" stroke="none" />,
  spark: <path d="M8 1.5l1.4 4.6L14 7.5l-4.6 1.4L8 13.5l-1.4-4.6L2 7.5l4.6-1.4z" />,
  doc: (
    <g>
      <path d="M4 2h5l3 3v9H4z" />
      <path d="M9 2v3h3" />
    </g>
  ),
  brain: (
    <g>
      <path d="M6 2.5A2.5 2.5 0 0 0 3.5 5v.2A2.3 2.3 0 0 0 3 9.5 2.4 2.4 0 0 0 6 13.5V2.5z" />
      <path d="M10 2.5A2.5 2.5 0 0 1 12.5 5v.2A2.3 2.3 0 0 1 13 9.5a2.4 2.4 0 0 1-3 4V2.5z" />
    </g>
  ),
}

const Icon = ({ name, size = 16, sw = 1.5 }) => (
  <svg
    width={size}
    height={size}
    viewBox="0 0 16 16"
    fill="none"
    stroke="currentColor"
    strokeWidth={sw}
    strokeLinecap="round"
    strokeLinejoin="round"
    aria-hidden="true"
  >
    {GLYPHS[name] || GLYPHS.dot}
  </svg>
)

// task C8: shared engine-outage error branch for the Bank workspace surfaces
// (Overview's growth chart + bank rows, the workspace's units/sources lists,
// the BankBar's reflect/rules tabs). Ported from memory.jsx's pre-v2
// `MemError` component (#1539) so every v2 card gets the same "announce the
// outage, don't render an empty-state" treatment instead of re-implementing
// it per file. `query` is any TanStack Query result exposing `isError` /
// `error` / (optionally) `refetch`.
// Post-smoke fix: the live 404 hit against CT105 (units route missing from
// the currently-deployed backend — a deploy-skew case, not an outage) was
// rendering "Memory engine unreachable", which is wrong — the engine
// answered fine, this install's API just doesn't have the route yet.
// Branch the headline by the error's actual HTTP status (Hal0Error.status,
// client.ts) rather than one blanket message for every failure:
//   - 404                 → deploy-skew ("doesn't serve this view yet")
//   - other 4xx (incl 422)→ a rejected request, not an outage
//   - everything else     → the original "unreachable" copy (503/network/
//                            501/no status at all)
function mvErrorHeadline(error, what) {
  const status = error?.status
  const detail = error?.message || `could not load ${what}`
  if (status === 404) return "This install's API doesn't serve this view yet"
  if (typeof status === 'number' && status >= 400 && status < 500) return `Request rejected — ${detail}`
  return `Memory engine unreachable — ${detail}`
}

const MvError = ({ query, what, testid }) => {
  if (!query?.isError) return null
  return (
    <div className="empty mono" data-testid={testid}>
      <div>{mvErrorHeadline(query.error, what)}</div>
      {query.refetch && (
        <button
          className="mv-btn"
          style={{ marginTop: 8 }}
          data-testid={`${testid}-retry`}
          onClick={() => query.refetch()}
        >
          Retry
        </button>
      )}
    </div>
  )
}

const MemV2 = {
  FACT_COLORS,
  FACT_DESC,
  LINK_COLORS,
  LINK_LABEL,
  LINK_WEIGHT,
  TOPIC_COLORS,
  fmtN,
  dayKey,
  Icon,
  MvError,
}

// Named exports alongside the window-globals publish, purely so the pure
// helpers are vitest-importable without mounting anything (this module has
// no other exports consumers should reach for — the window-globals
// contract, not a module import, is how Phase C views are meant to consume
// MemV2). Guard the window write so importing this module under vitest's
// node test environment (no `window` global) doesn't throw.
export { fmtN, dayKey, mvErrorHeadline }

if (typeof window !== 'undefined') {
  Object.assign(window, { MemV2 })
}
